//! Google's **native Gemini API** as an arch — `generateContent` for the
//! answer and `countTokens` for the I4′ count, on
//! `https://generativelanguage.googleapis.com`.
//!
//! ## What is honest about it
//!
//! It is not **governed**: the inference runs on Google's machines, so
//! `locality: Cloud`, `governed: false`, clearance stops at `Business` and
//! third-party data is refused.
//!
//! * **`jurisdiction: "US"`.** The Gemini Developer API makes no regional
//!   promise; Google is a US provider, and an EU-hosted Gemini is Vertex AI —
//!   a different API, a different contract, and not this arch.
//! * **`retention_days: Some(55)`.** The Gemini API's usage policies
//!   (ai.google.dev/gemini-api/docs/usage-policies, 2026-06-09): "Google
//!   retains the following data for fifty-five (55) days for the purposes of
//!   detecting and preventing violations … Prompts … Contextual Information …
//!   Output." That is the **paid** tier's treatment. On the unpaid tier
//!   "Google uses the content you submit … to provide, improve, and develop
//!   Google products … human reviewers may read, annotate, and process your
//!   API input and output" (Gemini API Additional Terms, 2026-04-28) — which
//!   no manifest field can say. Two clauses of the same terms settle it for
//!   this node: in the EEA, Switzerland and the UK "the terms under 'How
//!   Google uses Your Data' in 'Paid Services' apply to all Services,
//!   including … unpaid quota", and "you may use only Paid Services when
//!   making API Clients available to users in the EEA, Switzerland, or the
//!   UK". A customer node mounts this arch on a billing-enabled key.
//! * **The price is the standard paid tier's list**, and the *regular* one:
//!   where Google runs an introductory price with an end date, [`MODELS`]
//!   records the price that follows it, so a recorded cost errs high for a
//!   few weeks rather than low for ever after.
//!
//! `generateContent` is the API the design names. Since June 2026 Google
//! calls it "legacy" beside the newer Interactions API, while stating that it
//! "remains fully supported"; the version this arch is addressed by
//! ([`API_VERSION`]) is in its identity, so a move to either is a new arch.
//!
//! ## The key
//!
//! Service `vk`, user `gemini`, put there by `vk secret set gemini`, handled
//! by the same [`SecretString`] and scrub as every API arch. It goes on the
//! `x-goog-api-key` header — **never** the `?key=` query parameter Google
//! also accepts: a query string is part of the URL, and a URL is what a
//! transport error, a proxy log and a redirect all repeat.
//!
//! ## I4′
//!
//! The ceiling is the model's documented input window ([`MODELS`]), a prompt
//! past nine tenths of it is refused before anything is sent, and the
//! answer's own `usageMetadata.promptTokenCount` is checked against the whole
//! window afterwards. The pre-check count is the API's own `countTokens`
//! ("there's no charge for calling countTokens", Firebase AI Logic docs),
//! falling back to the `bytes/3 + 64` estimate when that call fails, as on the
//! Anthropic arch: a rate-limited counter should cost accuracy and not the
//! inference.
//!
//! ## Thinking
//!
//! Every current Gemini model thinks, and `maxOutputTokens` bounds the
//! thinking **and** the answer together ("the maximum number of tokens a
//! response can generate, including thought tokens"); thinking is billed at
//! the output rate. The kernel calls every arch with an answer bound of at
//! most 1 024 tokens, so a model left at its default — dynamic, and high on
//! Gemini 3 — or given a fixed budget the size of that bound spends the whole
//! of it thinking and answers nothing (review I-1: a fixed 1 024-token
//! budget made every 2.5 Pro and 2.5 Flash call end `MAX_TOKENS` with no
//! text).
//!
//! So the thinking budget is **derived from each call's bound**: a quarter of
//! it, never past [`THINKING_MAX`], never under the model's floor — the
//! least budget a model that cannot stop thinking accepts. Three quarters of
//! the bound are always left for the answer, and a bound so small the floor
//! would take all of it is refused before anything is sent. It goes as
//! `thinkingBudget`, in tokens, on every listed model that thinks by default
//! — Gemini 3 and later included, where Google keeps `thinkingBudget` "for
//! backward compatibility" beside `thinkingLevel`, because a level bounds
//! nothing in tokens and a budget does. Nothing is sent to 2.5 Flash-Lite,
//! which does not think unless asked, nor to an unlisted model, because a
//! field a model may 400 on is not something to guess. The rule is in the
//! arch identity and fixes the budget for every bound; the budget a call was
//! actually sent is in its `details`.

use anyhow::{anyhow, ensure, Context, Result};
use reqwest::blocking::Client;
use serde_json::{json, Value};
use std::time::Duration;
use vk_arch_openai_compat::http::{
    self, answer_tokens, estimate_tokens, usable, COUNT_TIMEOUT, MAX_OUTPUT_TOKENS,
};
use vk_arch_openai_compat::Price;
use vk_contracts::arch::{ArchIdentity, ArchManifest, Capability, Determinism, Locality};
use vk_contracts::labels::{Clearance, Scope};
use vk_kernel::arch::{AdapterError, ArchAdapter, Completion};

pub use vk_arch_openai_compat::{load_key, KeySource, SecretString};

/// Google's origin for the Gemini Developer API, and the only one an
/// ordinary node uses. Not a request field; daemon configuration
/// (`vkd --gemini-base-url`, for tests), bounded where it is configured and
/// again here.
pub const DEFAULT_BASE_URL: &str = "https://generativelanguage.googleapis.com";

/// The API version every path is under. `v1beta` is what Google's own
/// documentation and SDKs address ("the GenAI SDKs use `v1beta` by
/// default"); it is in the arch identity.
pub const API_VERSION: &str = "v1beta";

/// The model a `gemini` arch runs on unless told otherwise: Gemini 3.8
/// Flash, stable, and one of the two Google names "for any new projects".
pub const DEFAULT_MODEL: &str = "gemini-3.8-flash";

/// The keyring user this arch's key lives under: `vk`/`gemini`.
pub const KEYRING_USER: &str = "gemini";

/// The header the key goes on.
pub const KEY_HEADER: &str = "x-goog-api-key";

/// The ceiling for a model with no row in [`MODELS`]. Lower than any listed
/// window on purpose — an unlisted id on this API may be a smaller open model
/// — and guessing low costs only a prompt projected further than it had to be.
pub const FALLBACK_CONTEXT: u32 = 128_000;

/// The share of a call's answer bound its thinking may take: one part in
/// this many.
pub const THINKING_SHARE: u32 = 4;

/// The most thinking one call is ever given, in tokens, whatever its bound.
pub const THINKING_MAX: u32 = 1_024;

/// What a model is sent about thinking (see the crate documentation).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Thinking {
    /// `thinkingConfig.thinkingBudget`, derived per call from the answer
    /// bound: `bound / THINKING_SHARE`, at least `floor`, at most
    /// [`THINKING_MAX`]. `floor` is the least budget the model accepts —
    /// `0` only where thinking may be switched off.
    Budget { floor: u32 },
    /// No `thinkingConfig` at all: the model's own default.
    Default,
}

impl Thinking {
    /// The thinking budget a call bounded at `bound` is sent, if any.
    pub fn budget(self, bound: u32) -> Option<u32> {
        match self {
            Thinking::Budget { floor } => {
                Some((bound / THINKING_SHARE).max(floor).min(THINKING_MAX))
            }
            Thinking::Default => None,
        }
    }

    /// The `thinkingConfig` a call bounded at `bound` sends, if any.
    pub fn config(self, bound: u32) -> Option<Value> {
        self.budget(bound).map(|n| json!({ "thinkingBudget": n }))
    }

    /// How the arch identity names it: the rule, which fixes the budget for
    /// every bound a call can have.
    pub fn label(self) -> String {
        match self {
            Thinking::Budget { floor } => {
                format!("budget:bound/{THINKING_SHARE},min:{floor},max:{THINKING_MAX}")
            }
            Thinking::Default => "model-default".into(),
        }
    }
}

/// A higher rate for long prompts: every token of a call whose prompt is
/// past `above` is priced at these rates, input and output alike.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct LongContext {
    pub above: u32,
    pub input_usd_per_mtok: f64,
    pub output_usd_per_mtok: f64,
}

/// One model, as Google documents it.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Model {
    /// The model id, exactly as it goes in the path.
    pub id: &'static str,
    /// The documented input window, in tokens — the I4′ ceiling.
    pub context_window: u32,
    /// Standard paid-tier list price for input tokens, USD per million.
    pub input_usd_per_mtok: f64,
    /// The same for output tokens, thinking included.
    pub output_usd_per_mtok: f64,
    /// The long-prompt tier, on the models that have one.
    pub long: Option<LongContext>,
    /// Order-of-magnitude p50 latency for a ~500-token answer, in ms. Not
    /// measured; outside the identity.
    pub latency_ms_p50: u32,
    /// What this model is sent about thinking.
    pub thinking: Thinking,
}

impl Model {
    /// The price of a call whose prompt was `prompt_tokens` long.
    pub fn price(&self, prompt_tokens: u32) -> Price {
        match self.long {
            Some(l) if prompt_tokens > l.above => Price {
                input_usd_per_mtok: l.input_usd_per_mtok,
                output_usd_per_mtok: l.output_usd_per_mtok,
            },
            _ => Price {
                input_usd_per_mtok: self.input_usd_per_mtok,
                output_usd_per_mtok: self.output_usd_per_mtok,
            },
        }
    }
}

const ONE_MEBI_TOKENS: u32 = 1_048_576;
/// A model that cannot stop thinking: its budget never goes under 128
/// tokens, the least 2.5 Pro accepts, assumed of the generations after it.
const THINKS: Thinking = Thinking::Budget { floor: 128 };

/// Every model this node will price.
///
/// **Source:** ai.google.dev — the models page, the pricing page and the
/// deprecations page, updated 2026-10-12 to 2026-10-15 and **read
/// 2026-10-16**. Standard paid tier, USD per million tokens; output prices
/// include thinking tokens. Every window is the documented 1 048 576.
///
/// **Regular prices, not introductory ones.** Gemini 4 Argon is at $2 / $10
/// until 2026-11-30 and $4 / $20 from 2026-12-01; Gemini 3.8 and 3.6 Flash
/// are at $0.75 / $3.75 until 2026-12-31 and $1.50 / $7.50 after. The table
/// carries the later figures. Gemini 4 Argon's window is shared between input
/// and output; the tenth of headroom I4′ keeps is far wider than the
/// [`MAX_OUTPUT_TOKENS`] this arch ever asks for.
///
/// Left out: `gemini-3-pro-preview` (shut down 2026-03-09) and
/// `gemini-2.0-flash` (2026-06-01). In, with a caveat: the 2.5 models, which
/// Google now limits "to users who have actively used them in the past";
/// `gemini-3.1-flash-lite`, which shuts down 2027-05-07; and two previews.
pub const MODELS: &[Model] = &[
    Model {
        id: "gemini-4-argon",
        context_window: ONE_MEBI_TOKENS,
        input_usd_per_mtok: 4.00,
        output_usd_per_mtok: 20.00,
        long: None,
        latency_ms_p50: 12_000,
        thinking: THINKS,
    },
    Model {
        id: "gemini-3.8-flash",
        context_window: ONE_MEBI_TOKENS,
        input_usd_per_mtok: 1.50,
        output_usd_per_mtok: 7.50,
        long: None,
        latency_ms_p50: 5_000,
        thinking: THINKS,
    },
    Model {
        id: "gemini-3.6-flash",
        context_window: ONE_MEBI_TOKENS,
        input_usd_per_mtok: 1.50,
        output_usd_per_mtok: 7.50,
        long: None,
        latency_ms_p50: 5_000,
        thinking: THINKS,
    },
    Model {
        id: "gemini-3.5-flash-lite",
        context_window: ONE_MEBI_TOKENS,
        input_usd_per_mtok: 0.30,
        output_usd_per_mtok: 2.50,
        long: None,
        latency_ms_p50: 3_000,
        thinking: THINKS,
    },
    Model {
        id: "gemini-3.1-pro-preview",
        context_window: ONE_MEBI_TOKENS,
        input_usd_per_mtok: 2.00,
        output_usd_per_mtok: 12.00,
        long: Some(LongContext {
            above: 200_000,
            input_usd_per_mtok: 4.00,
            output_usd_per_mtok: 18.00,
        }),
        latency_ms_p50: 15_000,
        thinking: THINKS,
    },
    Model {
        id: "gemini-3.1-flash-lite",
        context_window: ONE_MEBI_TOKENS,
        input_usd_per_mtok: 0.25,
        output_usd_per_mtok: 1.50,
        long: None,
        latency_ms_p50: 3_000,
        thinking: THINKS,
    },
    Model {
        id: "gemini-3-flash-preview",
        context_window: ONE_MEBI_TOKENS,
        input_usd_per_mtok: 0.50,
        output_usd_per_mtok: 3.00,
        long: None,
        latency_ms_p50: 5_000,
        thinking: THINKS,
    },
    Model {
        id: "gemini-2.5-pro",
        context_window: ONE_MEBI_TOKENS,
        input_usd_per_mtok: 1.25,
        output_usd_per_mtok: 10.00,
        long: Some(LongContext {
            above: 200_000,
            input_usd_per_mtok: 2.50,
            output_usd_per_mtok: 15.00,
        }),
        latency_ms_p50: 15_000,
        thinking: THINKS,
    },
    Model {
        id: "gemini-2.5-flash",
        context_window: ONE_MEBI_TOKENS,
        input_usd_per_mtok: 0.30,
        output_usd_per_mtok: 2.50,
        long: None,
        latency_ms_p50: 5_000,
        // 2.5 Flash alone may be sent 0: its thinking can be switched off.
        thinking: Thinking::Budget { floor: 0 },
    },
    Model {
        id: "gemini-2.5-flash-lite",
        context_window: ONE_MEBI_TOKENS,
        input_usd_per_mtok: 0.10,
        output_usd_per_mtok: 0.40,
        long: None,
        latency_ms_p50: 3_000,
        thinking: Thinking::Default,
    },
];

/// The table's row for `id`, or `None` — which mounts, with no price claimed,
/// the fallback ceiling and no thinking setting sent.
pub fn model(id: &str) -> Option<&'static Model> {
    MODELS.iter().find(|m| m.id == id)
}

/// The documented window for `id`, or [`FALLBACK_CONTEXT`].
pub fn context_window(id: &str) -> u32 {
    model(id).map_or(FALLBACK_CONTEXT, |m| m.context_window)
}

/// What `id` is sent about thinking.
pub fn thinking(id: &str) -> Thinking {
    model(id).map_or(Thinking::Default, |m| m.thinking)
}

/// The manifest's price tag — input tokens per thousand, in euros, at the
/// short-prompt rate and the node's one pinned rate — or `None`, never
/// `0.0`, for an unlisted model (Ruling 30).
pub fn cost_per_1k_tokens_eur(id: &str) -> Option<f64> {
    model(id).map(|m| m.input_usd_per_mtok / 1_000.0 * vk_arch_openai_compat::EUR_PER_USD)
}

/// What a call cost at list price, or `None` for an unlisted model.
pub fn cost_list_usd(id: &str, prompt_tokens: u32, output_tokens: u32) -> Option<f64> {
    model(id).map(|m| m.price(prompt_tokens).cost(prompt_tokens, output_tokens))
}

/// Is `id` something that can go in a path? The model id is spliced into
/// `/v1beta/models/{id}:generateContent`, so it is held to the characters
/// Google's ids use — lowercase letters, digits, `.` and `-` — and a `/`,
/// `?`, `#` or `%` that would move the request somewhere else is refused.
pub fn check_model_id(id: &str) -> Result<()> {
    ensure!(
        !id.is_empty()
            && id.len() <= 100
            && id
                .bytes()
                .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'.' || b == b'-'),
        "gemini: {id:?} is not a model id (lowercase letters, digits, `.` and `-`)"
    );
    Ok(())
}

// --------------------------------------------------------------- the config

/// Everything `arch.mount { kind: "gemini" }` may say. Not the key.
#[derive(Debug, Clone, PartialEq)]
pub struct GeminiConfig {
    pub model: String,
    /// The daemon's origin: [`DEFAULT_BASE_URL`] on every real node.
    pub base_url: String,
    /// The mount's own bound on one answer — thinking included — capped at
    /// [`MAX_OUTPUT_TOKENS`].
    pub max_tokens: u32,
    /// The I4′ ceiling; `0` is the model's documented window.
    pub context_ceiling: u32,
    /// How long one `generateContent` call may take.
    pub timeout: Duration,
}

impl Default for GeminiConfig {
    fn default() -> GeminiConfig {
        GeminiConfig {
            model: DEFAULT_MODEL.into(),
            base_url: DEFAULT_BASE_URL.into(),
            max_tokens: MAX_OUTPUT_TOKENS,
            context_ceiling: 0,
            timeout: Duration::from_secs(600),
        }
    }
}

impl GeminiConfig {
    fn ceiling(&self) -> u32 {
        if self.context_ceiling > 0 {
            self.context_ceiling
        } else {
            context_window(&self.model)
        }
    }

    /// The path of `method` (`generateContent`, `countTokens`) on this
    /// config's model.
    pub fn path(&self, method: &str) -> String {
        format!("/{API_VERSION}/models/{}:{method}", self.model)
    }

    /// Everything this config says that can be refused, checked without a
    /// key: the origin, the model id that goes in the path, and the answer
    /// bound. The daemon runs it **before** it reads the keyring (review
    /// M-2) — a mount refused for what it says must not have read a
    /// credential first — and the adapter runs it again.
    pub fn check(&self) -> Result<()> {
        vk_arch_openai_compat::check_base_url(&self.base_url)?;
        check_model_id(&self.model)?;
        vk_arch_openai_compat::check_max_tokens(self.max_tokens)
    }
}

// -------------------------------------------------------------- the adapter

pub struct GeminiAdapter {
    cfg: GeminiConfig,
    manifest: ArchManifest,
    key: SecretString,
    client: Client,
}

/// How the far end is named in a message.
const PROVIDER: &str = "the Gemini API";

/// The finish reasons that mean an answer, complete or bounded. Anything else
/// — `SAFETY`, `RECITATION`, `PROHIBITED_CONTENT`, `BLOCKLIST`, `SPII`,
/// `LANGUAGE`, `OTHER` and the rest — is a failed call, whatever text came
/// with it. `CONTINUATION` is a bounded answer this arch does not continue.
const ANSWERED: &[&str] = &["STOP", "MAX_TOKENS", "CONTINUATION"];

impl GeminiAdapter {
    /// The arch, or the reason there is none: a config
    /// [`GeminiConfig::check`] refuses, or a TLS stack that will not start.
    pub fn with_config(key: SecretString, cfg: GeminiConfig) -> Result<GeminiAdapter> {
        cfg.check()?;
        let client = http::build_client("verticalai-vk-arch-gemini")?;
        Ok(GeminiAdapter {
            manifest: Self::manifest_for(&cfg),
            cfg,
            key,
            client,
        })
    }

    /// The manifest for this configuration. In the identity: the model, the
    /// API version, the origin and the thinking setting — everything that
    /// changes what answers.
    pub fn manifest_for(cfg: &GeminiConfig) -> ArchManifest {
        ArchManifest {
            name: format!("gemini/{}", cfg.model),
            capabilities: [Capability::Generate, Capability::Plan, Capability::Judge].into(),
            locality: Locality::Cloud,
            jurisdiction: "US".into(),
            // The paid tier's fifty-five days (see the crate documentation).
            retention_days: Some(55),
            cost_per_1k_tokens_eur: cost_per_1k_tokens_eur(&cfg.model),
            latency_ms_p50: model(&cfg.model).map_or(10_000, |m| m.latency_ms_p50),
            context_ceiling: cfg.ceiling(),
            determinism: Determinism::NonDeterministic,
            identity: ArchIdentity {
                weights_sha256: format!("model:{}", cfg.model),
                engine: "gemini-generate-content".into(),
                engine_version: API_VERSION.into(),
                backend: "gemini-developer-api".into(),
                quant: "-".into(),
                kv_cache: "-".into(),
                threads: 1,
                batch: 1,
                sampling: [
                    ("thinking".to_string(), thinking(&cfg.model).label()),
                    ("base_url".to_string(), cfg.base_url.clone()),
                ]
                .into_iter()
                .collect(),
                seed: None,
            },
            clearance: Clearance {
                max_scope: Scope::Business,
                third_party_allowed: false,
            },
            governed: false,
        }
    }

    /// The `generateContent` request, and the only one this arch sends: one
    /// user turn, the bound on the answer — the smaller of the caller's and
    /// the mount's, thinking included — and the thinking budget derived from
    /// that bound ([`Thinking::budget`]). No `temperature`, `topP` or `topK`:
    /// Google ignores them on the newest models and has announced they will
    /// become errors.
    pub fn generate_request(cfg: &GeminiConfig, prompt: &str, max_tokens: u32) -> Value {
        let bound = answer_tokens(cfg.max_tokens, max_tokens);
        let mut generation = json!({ "maxOutputTokens": bound });
        if let Some(t) = thinking(&cfg.model).config(bound) {
            generation["thinkingConfig"] = t;
        }
        json!({
            "contents": [{ "role": "user", "parts": [{ "text": prompt }] }],
            "generationConfig": generation,
        })
    }

    fn post(&self, path: &str, body: &Value, timeout: Duration) -> Result<Value> {
        let url = format!("{}{path}", self.cfg.base_url.trim_end_matches('/'));
        let request = self
            .client
            .post(url)
            .header("content-type", "application/json")
            .timeout(timeout)
            .json(body);
        // Only through `authorize`, which refuses a request that is not under
        // the checked origin (review M-4).
        let request = self
            .key
            .authorize(request, &self.cfg.base_url, KEY_HEADER, "")?;
        let r = http::send(&self.client, Some(&self.key), request)?;
        http::read_json(PROVIDER, path, Some(&self.key), r)
    }

    /// The API's own count of `prompt`, or `Err` when it could not be had.
    fn api_count_tokens(&self, prompt: &str) -> Result<u32> {
        let path = self.cfg.path("countTokens");
        let body = json!({ "contents": [{ "role": "user", "parts": [{ "text": prompt }] }] });
        let answer = self
            .post(&path, &body, COUNT_TIMEOUT)
            .with_context(|| format!("POST {path}"))?;
        answer["totalTokens"]
            .as_u64()
            .and_then(|n| u32::try_from(n).ok())
            .ok_or_else(|| anyhow!("{path}: no totalTokens in the answer"))
    }
}

/// The answer: every text part of the first candidate, in order, except the
/// ones marked `thought` — which carry the model's reasoning, when it is
/// asked to include it, and must not be concatenated into the register.
pub fn text_of(candidate: &Value) -> String {
    candidate["content"]["parts"]
        .as_array()
        .map(|parts| {
            parts
                .iter()
                .filter(|p| p["thought"] != Value::Bool(true))
                .filter_map(|p| p["text"].as_str())
                .collect()
        })
        .unwrap_or_default()
}

impl ArchAdapter for GeminiAdapter {
    fn manifest(&self) -> &ArchManifest {
        &self.manifest
    }

    fn context_budget(&self) -> u32 {
        usable(self.manifest.context_ceiling)
    }

    /// The estimate, not the API: the kernel calls this once per candidate
    /// line while it fits a prompt, and each would be a round trip. The one
    /// measured count is taken in `complete`. The estimate runs high, so a
    /// prompt the kernel fitted by it is one the API's count also fits.
    fn count_tokens(&self, text: &str) -> u32 {
        estimate_tokens(text)
    }

    fn complete(&self, prompt: &str, max_tokens: u32) -> Result<Completion, AdapterError> {
        // A bound the model's least thinking budget would take all of leaves
        // the answer nothing: refused before anything — the count included —
        // goes on the wire, rather than paid for and answered with no text.
        let bound = answer_tokens(self.cfg.max_tokens, max_tokens);
        let thinking_budget = thinking(&self.cfg.model).budget(bound);
        if let Some(t) = thinking_budget.filter(|t| *t >= bound) {
            return Err(AdapterError::Other(anyhow!(
                "an answer bound of {bound} tokens leaves nothing after {}'s least thinking \
                 budget of {t} tokens; ask for more than {t}",
                self.cfg.model
            )));
        }

        let ceiling = self.context_budget();
        let (needed, counted) = match self.api_count_tokens(prompt) {
            Ok(n) => (n, true),
            Err(e) => {
                tracing::debug!(
                    error = %e,
                    "the Gemini token counter did not answer; falling back to the estimate"
                );
                (estimate_tokens(prompt), false)
            }
        };
        if needed > ceiling {
            return Err(AdapterError::I4Prime { needed, ceiling });
        }

        let path = self.cfg.path("generateContent");
        let body = Self::generate_request(&self.cfg, prompt, max_tokens);
        let answer = self
            .post(&path, &body, self.cfg.timeout)
            .map_err(AdapterError::Other)?;

        // The prompt itself was refused: no candidates at all.
        if let Some(reason) = answer["promptFeedback"]["blockReason"].as_str() {
            return Err(AdapterError::Other(anyhow!(
                "{PROVIDER} blocked the prompt (blockReason {reason})"
            )));
        }

        let usage = &answer["usageMetadata"];
        let measured = usage.is_object();
        // A count is an integer or it is not a count: a string or a float is
        // unmeasured, never a zero that would price the call at nothing
        // (review M-3). A count Google leaves out is a zero it means.
        let integer = |key: &str| usage.get(key).filter(|v| v.is_u64()).map(http::count);
        let left_out = |key: &str| usage.get(key).is_none_or(Value::is_null);
        let part = |key: &str| integer(key).or_else(|| left_out(key).then_some(0));
        let prompt_tokens = integer("promptTokenCount");
        let candidates_tokens = part("candidatesTokenCount");
        let thoughts_tokens = part("thoughtsTokenCount");
        // Thinking is billed at the output rate and bounded by the same
        // `maxOutputTokens`: it is output, as far as cost and the bound go.
        let tokens_out = match (measured, candidates_tokens, thoughts_tokens) {
            (true, Some(c), Some(t)) => Some(c.saturating_add(t)),
            _ => None,
        };

        // I4′ again, from Google's own count of what it read, against the
        // whole window — the number the refusal names.
        if let Some(read) = prompt_tokens {
            if read >= self.manifest.context_ceiling {
                return Err(AdapterError::I4Prime {
                    needed: read,
                    ceiling: self.manifest.context_ceiling,
                });
            }
        }

        let candidate = &answer["candidates"][0];
        let finish = candidate["finishReason"].as_str().unwrap_or("").to_string();
        if !finish.is_empty() && !ANSWERED.contains(&finish.as_str()) {
            return Err(AdapterError::Other(anyhow!(
                "{PROVIDER} withheld the answer (finishReason {finish})"
            )));
        }
        let text = text_of(candidate);
        if text.trim().is_empty() {
            let thoughts_tokens = thoughts_tokens.unwrap_or(0);
            return Err(AdapterError::Other(if finish == "MAX_TOKENS" {
                anyhow!(
                    "{PROVIDER} spent the whole answer bound before writing any text \
                     ({thoughts_tokens} thinking tokens, finishReason MAX_TOKENS); raise \
                     --max-tokens or choose a model that thinks less"
                )
            } else {
                anyhow!(
                    "{PROVIDER} answered with no text at all (finishReason {})",
                    if finish.is_empty() { "none" } else { &finish }
                )
            }));
        }

        Ok(Completion {
            text,
            tokens_in_measured: prompt_tokens,
            tokens_out,
            cost_list_usd: match (prompt_tokens, tokens_out) {
                (Some(i), Some(o)) => cost_list_usd(&self.cfg.model, i, o),
                _ => None,
            },
            details: Some(json!({
                "prompt_tokens": prompt_tokens,
                "candidates_tokens": candidates_tokens,
                "thoughts_tokens": thoughts_tokens,
                "cached_tokens": part("cachedContentTokenCount"),
                "finish_reason": finish,
                // Strings off the far end, scrubbed like every other one
                // (review M-5).
                "response_id": self
                    .key
                    .scrub(answer["responseId"].as_str().unwrap_or("-")),
                "model_version": self
                    .key
                    .scrub(answer["modelVersion"].as_str().unwrap_or(&self.cfg.model)),
                "thinking": thinking(&self.cfg.model).label(),
                // What this call was actually sent, out of its bound.
                "thinking_budget": thinking_budget,
                "answer_bound": bound,
                // Whether the pre-check was Google's count or the estimate.
                "prompt_counted_by_api": counted,
            })),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_model_id_is_held_to_the_characters_of_one() {
        for good in ["gemini-3.8-flash", "gemini-2.5-pro", "gemma-3-27b-it"] {
            assert!(check_model_id(good).is_ok(), "{good}");
        }
        for bad in [
            "",
            "models/gemini-3.8-flash",
            "gemini?key=x",
            "../v1/files",
            "gemini#x",
            "Gemini-3.8-Flash",
            "gemini%2F",
        ] {
            assert!(check_model_id(bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn a_long_prompt_is_priced_at_the_long_rate_on_the_models_that_have_one() {
        let pro = model("gemini-2.5-pro").expect("listed");
        assert_eq!(pro.price(200_000).input_usd_per_mtok, 1.25);
        assert_eq!(pro.price(200_001).input_usd_per_mtok, 2.50);
        assert_eq!(pro.price(200_001).output_usd_per_mtok, 15.00);
        let flash = model(DEFAULT_MODEL).expect("listed");
        assert_eq!(flash.price(900_000).input_usd_per_mtok, 1.50);
    }

    #[test]
    fn thought_parts_are_not_the_answer() {
        let candidate = json!({"content": {"parts": [
            {"text": "reasoning", "thought": true},
            {"text": "a"},
            {"text": "b", "thought": false},
        ]}});
        assert_eq!(text_of(&candidate), "ab");
        assert_eq!(text_of(&Value::Null), "");
    }
}
