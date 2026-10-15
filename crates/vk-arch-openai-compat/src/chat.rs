//! The OpenAI-style chat-completions wire, as one arch: `POST
//! /v1/chat/completions`, a bearer key, one user message, one bounded answer.
//!
//! Two kinds are built on it: this crate's own `openai-compat` (Mammouth,
//! llama-server, vLLM — any server that copies the API) and `mistral`, whose
//! native API is the same wire. What differs between them is the manifest —
//! where the far end is, how long it keeps what it is sent, what it charges —
//! and that is the caller's to build; this module keeps the part they share
//! identical, so a fix to one is a fix to both.
//!
//! **I4′ without a tokenizer.** No server in this family offers a standard
//! count endpoint, so the pre-check is the `bytes/3 + 64` estimate against
//! nine tenths of the ceiling, and the post-check is the answer's own
//! `usage.prompt_tokens` against the full ceiling. The servers named above
//! refuse an oversize prompt rather than truncate it; one that truncated
//! silently would be caught by the post-check only if the ceiling it was
//! mounted with is the window it really has, which for `openai-compat` is the
//! operator's claim (see the crate documentation).
use crate::http::{self, answer_tokens, estimate_tokens, usable};
use anyhow::{anyhow, Context, Result};
use reqwest::blocking::Client;
use serde_json::Value;
use std::time::Duration;
use vk_arch_anthropic::SecretString;
use vk_contracts::arch::ArchManifest;
use vk_kernel::arch::{AdapterError, ArchAdapter, Completion};

/// The one path this wire posts to, under the base URL.
pub const CHAT_PATH: &str = "/v1/chat/completions";

/// A list price, in USD per million tokens.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Price {
    pub input_usd_per_mtok: f64,
    pub output_usd_per_mtok: f64,
}

impl Price {
    /// What `tokens_in` and `tokens_out` cost at this price, in USD.
    pub fn cost(&self, tokens_in: u32, tokens_out: u32) -> f64 {
        f64::from(tokens_in) * self.input_usd_per_mtok / 1e6
            + f64::from(tokens_out) * self.output_usd_per_mtok / 1e6
    }
}

/// Everything about one chat-completions arch that is not its manifest.
#[derive(Debug, Clone)]
pub struct ChatSettings {
    /// How the far end is named in a message: `the Mistral API`.
    pub provider: &'static str,
    /// The origin, and any path prefix in front of [`CHAT_PATH`]. Daemon
    /// configuration, never a request's; checked again here.
    pub base_url: String,
    /// The model id, exactly as it is sent.
    pub model: String,
    /// The mount's own bound on one answer, capped at
    /// [`MAX_OUTPUT_TOKENS`](crate::MAX_OUTPUT_TOKENS).
    pub max_tokens: u32,
    /// How long one call may take.
    pub timeout: Duration,
    /// The list price per call, or `None` where this node has no price list:
    /// then every call is recorded with no price rather than as free.
    pub price: Option<Price>,
    /// The `user-agent` the client sends.
    pub user_agent: &'static str,
}

/// One arch on the chat-completions wire.
pub struct ChatArch {
    manifest: ArchManifest,
    settings: ChatSettings,
    /// Whether this arch has already said that its server reports no
    /// `usage` — once per arch, not once per call (review M-6).
    said_no_usage: std::sync::atomic::AtomicBool,
    /// `None` only for a server that needs none — the caller decides when
    /// that is allowed (`openai-compat` on loopback, and nowhere else).
    key: Option<SecretString>,
    client: Client,
}

impl ChatArch {
    /// The arch, or the reason there is none: an origin this node will not
    /// put a key on the wire to, or a TLS stack that will not start.
    ///
    /// The origin is checked here as well as where it is configured: this is
    /// the last place before the key goes on a wire, and a refusal that holds
    /// for every caller is better than one that depended on which door the
    /// configuration came in by.
    pub fn new(
        manifest: ArchManifest,
        settings: ChatSettings,
        key: Option<SecretString>,
    ) -> Result<ChatArch> {
        vk_arch_anthropic::check_base_url(&settings.base_url)?;
        crate::check_max_tokens(settings.max_tokens)?;
        let client = http::build_client(settings.user_agent)?;
        Ok(ChatArch {
            manifest,
            settings,
            key,
            client,
            said_no_usage: std::sync::atomic::AtomicBool::new(false),
        })
    }

    pub fn settings(&self) -> &ChatSettings {
        &self.settings
    }

    /// The request this arch puts on the wire, and the only one it does: the
    /// model, one user message, and the bound on the answer — the smaller of
    /// the caller's and the mount's, never past [`crate::MAX_OUTPUT_TOKENS`]. No
    /// `stream`, no `temperature`, no tools: one request, one answer, at the
    /// provider's defaults. `max_tokens` rather than OpenAI's newer
    /// `max_completion_tokens`, because it is the spelling every server in
    /// this family accepts.
    pub fn request(model: &str, mount_cap: u32, prompt: &str, max_tokens: u32) -> Value {
        serde_json::json!({
            "model": model,
            "messages": [{ "role": "user", "content": prompt }],
            "max_tokens": answer_tokens(mount_cap, max_tokens),
        })
    }

    fn post(&self, body: &Value) -> Result<Value> {
        let url = format!(
            "{}{CHAT_PATH}",
            self.settings.base_url.trim_end_matches('/')
        );
        let builder = self
            .client
            .post(url)
            .header("content-type", "application/json")
            .timeout(self.settings.timeout)
            .json(body);
        // The key goes on only through `authorize`, which refuses a request
        // that is not under the checked origin (review M-4).
        let request = match &self.key {
            Some(key) => {
                key.authorize(builder, &self.settings.base_url, "authorization", "Bearer ")?
            }
            None => builder.build().context("build the request")?,
        };
        let key = self.key.as_ref();
        let r = http::send(&self.client, key, request)?;
        http::read_json(self.settings.provider, CHAT_PATH, key, r)
    }
}

/// The answer's text: `content` when it is a string, and every `text` chunk
/// in order when it is a list — which is how a reasoning model (Mistral's
/// Magistral) returns its answer, with `thinking` chunks in front of the text
/// that must not be concatenated into the register.
pub fn text_of(content: &Value) -> String {
    match content {
        Value::String(s) => s.clone(),
        Value::Array(chunks) => chunks
            .iter()
            .filter(|c| c["type"] == "text")
            .filter_map(|c| c["text"].as_str())
            .collect(),
        _ => String::new(),
    }
}

impl ArchAdapter for ChatArch {
    fn manifest(&self) -> &ArchManifest {
        &self.manifest
    }

    /// What `complete` will accept, not the manifest's ceiling: the kernel
    /// projects a long prompt *into* this number.
    fn context_budget(&self) -> u32 {
        usable(self.manifest.context_ceiling)
    }

    fn count_tokens(&self, text: &str) -> u32 {
        estimate_tokens(text)
    }

    fn complete(&self, prompt: &str, max_tokens: u32) -> Result<Completion, AdapterError> {
        // I4′, before anything is sent. The estimate runs high, so a prompt
        // it passes is one the server's own count also passes.
        let ceiling = self.context_budget();
        let needed = estimate_tokens(prompt);
        if needed > ceiling {
            return Err(AdapterError::I4Prime { needed, ceiling });
        }

        let body = Self::request(
            &self.settings.model,
            self.settings.max_tokens,
            prompt,
            max_tokens,
        );
        let answer = self.post(&body).map_err(AdapterError::Other)?;

        let usage = &answer["usage"];
        let tokens_in = usage
            .get("prompt_tokens")
            .filter(|v| v.is_u64())
            .map(http::count);
        let tokens_out = usage
            .get("completion_tokens")
            .filter(|v| v.is_u64())
            .map(http::count);

        // I4′ again, from the server's own count of what it read, against the
        // **full** ceiling — the number the refusal then names, so an
        // operator can reconcile the two.
        match tokens_in {
            Some(read) if read >= self.manifest.context_ceiling => {
                return Err(AdapterError::I4Prime {
                    needed: read,
                    ceiling: self.manifest.context_ceiling,
                });
            }
            Some(_) => {}
            // Nothing to check against: a server that reports no `usage`
            // leaves only the pre-check standing. Said once per arch, and the
            // record says it for every call (review M-6).
            None => {
                if !self
                    .said_no_usage
                    .swap(true, std::sync::atomic::Ordering::Relaxed)
                {
                    tracing::warn!(
                        arch = %self.manifest.name,
                        "{} reports no usage: the I4' post-check on the prompt it read is                          skipped for this arch, and only the estimate before the call stands",
                        self.settings.provider
                    );
                }
            }
        }

        let choice = &answer["choices"][0];
        let finish = choice["finish_reason"].as_str().unwrap_or("").to_string();
        // The provider's moderation withheld the answer: HTTP 200 and nothing
        // usable. A failed call, not an empty draft to raise into a register.
        if finish == "content_filter" {
            return Err(AdapterError::Other(anyhow!(
                "{} withheld the answer (finish_reason content_filter)",
                self.settings.provider
            )));
        }
        // Mistral documents an `error` finish: generation failed part-way
        // and what text there is, is not an answer.
        if finish == "error" {
            return Err(AdapterError::Other(anyhow!(
                "{} stopped with an error part-way (finish_reason error)",
                self.settings.provider
            )));
        }
        let text = text_of(&choice["message"]["content"]);
        if text.trim().is_empty() {
            return Err(AdapterError::Other(anyhow!(
                "{} answered with no text at all (finish_reason {}, {} output tokens)",
                self.settings.provider,
                if finish.is_empty() { "none" } else { &finish },
                tokens_out.map_or("unreported".to_string(), |n| n.to_string()),
            )));
        }

        Ok(Completion {
            text,
            // Measured when the server reported it, and absent — not zero —
            // when it did not: a zero would be a measurement of nothing.
            tokens_in_measured: tokens_in,
            tokens_out,
            cost_list_usd: match (self.settings.price, tokens_in, tokens_out) {
                (Some(p), Some(i), Some(o)) => Some(p.cost(i, o)),
                _ => None,
            },
            details: Some(serde_json::json!({
                "prompt_tokens": tokens_in,
                "completion_tokens": tokens_out,
                "finish_reason": finish,
                // Strings off the far end, scrubbed like every other one
                // (review M-5).
                "id": http::scrub(self.key.as_ref(), answer["id"].as_str().unwrap_or("-")),
                "model": http::scrub(
                    self.key.as_ref(),
                    answer["model"].as_str().unwrap_or(&self.settings.model),
                ),
                "usage_reported": tokens_in.is_some(),
                // There is no count endpoint on this wire: the pre-check was
                // the estimate, and an audit of an I4′ refusal should know it.
                "prompt_counted_by_api": false,
            })),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::http::MAX_OUTPUT_TOKENS;
    use serde_json::json;

    #[test]
    fn the_answer_is_the_string_or_the_text_chunks_and_never_the_thinking() {
        assert_eq!(text_of(&json!("plain")), "plain");
        let chunks = json!([
            {"type": "thinking", "thinking": [{"type": "text", "text": "hmm"}]},
            {"type": "text", "text": "a"},
            {"type": "text", "text": "b"},
        ]);
        assert_eq!(text_of(&chunks), "ab");
        assert_eq!(text_of(&Value::Null), "");
    }

    #[test]
    fn the_request_is_one_user_message_and_a_bounded_answer() {
        let r = ChatArch::request("m", 100_000, "hi", 0);
        assert_eq!(r["model"], "m");
        assert_eq!(r["max_tokens"], MAX_OUTPUT_TOKENS);
        assert_eq!(r["messages"], json!([{"role": "user", "content": "hi"}]));
        assert!(r.get("stream").is_none());
        assert_eq!(ChatArch::request("m", 128, "hi", 1024)["max_tokens"], 128);
    }
}
