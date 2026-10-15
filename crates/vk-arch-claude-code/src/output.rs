//! The one JSON object `claude -p --output-format json` prints, and the only
//! reading of it the adapter is allowed to do.
//!
//! Field names and their meaning are spike 2a's, measured against Claude Code
//! 2.1.281. Two of them are easy to misread and are the reason this module
//! exists rather than a `serde_json::Value` walk at the call site:
//!
//! * `usage.input_tokens` counts the **uncached** prompt tokens alone. The
//!   prompt is that plus `cache_creation_input_tokens` and
//!   `cache_read_input_tokens` — `tokens_in` below.
//! * an API error keeps `subtype: "success"` and may not set `is_error`;
//!   `api_error_status` is what says the model never answered.

use anyhow::{anyhow, bail, Context, Result};
use serde_json::Value;
use std::time::Duration;
use vk_kernel::arch::AdapterError;

/// One `claude -p --output-format json` result object, as printed.
#[derive(Debug, Clone, PartialEq)]
pub struct ClaudeJson {
    /// The completion — or, on a failed run, what went wrong in prose.
    pub result: String,
    pub is_error: bool,
    /// Why the run ended, when the CLI says (`error_max_turns`, `api_error`…).
    pub terminal_reason: Option<String>,
    /// The provider's error status, rendered; `None` when the call went through.
    pub api_error_status: Option<String>,
    /// Prompt tokens that were **not** served from or written to the cache.
    pub input_tokens: u64,
    pub cache_creation_input_tokens: u64,
    pub cache_read_input_tokens: u64,
    pub output_tokens: u64,
    /// List-price equivalent. Under a subscription nothing is billed per call,
    /// and `modelUsage.<model>.costBasis` is `"list"` to say so.
    pub total_cost_usd: f64,
    /// Wall time for the whole process, and for the API leg of it.
    pub duration_ms: u64,
    pub duration_api_ms: u64,
    pub session_id: String,
}

impl ClaudeJson {
    /// Every prompt token this call consumed: uncached, newly cached and read
    /// back from cache. `input_tokens` on its own is the uncached remainder,
    /// which on a cached call is a small fraction of the prompt.
    pub fn tokens_in(&self) -> u64 {
        self.input_tokens
            .saturating_add(self.cache_creation_input_tokens)
            .saturating_add(self.cache_read_input_tokens)
    }

    /// Did the run fail? `is_error` is the CLI's own flag; `api_error_status`
    /// catches the case where the process ran fine and the provider did not.
    pub fn failed(&self) -> bool {
        self.is_error || self.api_error_status.is_some()
    }

    /// The refusal a failed run deserves: what Claude said, under the reason
    /// it gave for stopping. Both halves matter to whoever reads the ledger —
    /// `error_max_turns` and `529 overloaded_error` want different answers.
    fn refusal(&self) -> anyhow::Error {
        let mut why = String::from("claude refused the call");
        if let Some(status) = &self.api_error_status {
            why.push_str(&format!(" (api_error_status {status})"));
        }
        if let Some(reason) = &self.terminal_reason {
            why.push_str(&format!(" [{reason}]"));
        }
        if !self.result.is_empty() {
            why.push_str(&format!(": {}", self.result));
        }
        anyhow!(why)
    }
}

/// Parse the object exactly as printed, failed or not.
///
/// Missing numbers are read as zero — a run that reports no cache figures
/// used no cache — but `result` and `session_id` are required: an object
/// without them is not the one this adapter knows how to read, and guessing a
/// completion out of it is the one thing an arch must never do.
pub fn parse_fields(s: &str) -> Result<ClaudeJson> {
    let v: Value = serde_json::from_str(s)
        .with_context(|| format!("claude did not print a JSON object: {}", head(s)))?;
    if !v.is_object() {
        bail!("claude printed {}, not a result object", head(s));
    }
    let str_field = |name: &str| -> Result<String> {
        v.get(name)
            .and_then(Value::as_str)
            .map(str::to_owned)
            .ok_or_else(|| anyhow!("no string `{name}` in what claude printed: {}", head(s)))
    };
    let num = |owner: &Value, name: &str| owner.get(name).and_then(Value::as_u64).unwrap_or(0);
    let usage = v.get("usage").cloned().unwrap_or(Value::Null);
    Ok(ClaudeJson {
        result: str_field("result")?,
        is_error: v.get("is_error").and_then(Value::as_bool).unwrap_or(false),
        terminal_reason: rendered(v.get("terminal_reason")),
        api_error_status: rendered(v.get("api_error_status")),
        input_tokens: num(&usage, "input_tokens"),
        cache_creation_input_tokens: num(&usage, "cache_creation_input_tokens"),
        cache_read_input_tokens: num(&usage, "cache_read_input_tokens"),
        output_tokens: num(&usage, "output_tokens"),
        // Never invented: a call whose cost the CLI did not report is reported
        // as costing nothing, which is what a subscription call is.
        total_cost_usd: v
            .get("total_cost_usd")
            .and_then(Value::as_f64)
            .unwrap_or(0.0),
        duration_ms: num(&v, "duration_ms"),
        duration_api_ms: num(&v, "duration_api_ms"),
        session_id: str_field("session_id")?,
    })
}

/// Parse, and refuse a failed run. What this hands back is always a completion:
/// an error object wearing the same shape comes back as `Err`, carrying what
/// Claude said, so a refusal can never be raised into a register as an answer.
pub fn parse(s: &str) -> Result<ClaudeJson> {
    let j = parse_fields(s)?;
    if j.failed() {
        return Err(j.refusal());
    }
    Ok(j)
}

/// Parse, and refuse a failed run **typed** where the run says something
/// about the provider's plan (2026-10-15): what `complete` hands the kernel.
/// [`provider_refusal`] decides which; anything it does not recognise is the
/// same refusal [`parse`] gives.
pub fn parse_for_kernel(s: &str, now_unix_s: u64) -> std::result::Result<ClaudeJson, AdapterError> {
    let j = parse_fields(s)?;
    if j.failed() {
        return Err(
            provider_refusal(&j, now_unix_s).unwrap_or_else(|| AdapterError::Other(j.refusal()))
        );
    }
    Ok(j)
}

/// What a failed run says about the provider's plan, when it says something
/// the kernel keeps as the arch's health — `None` for a run that succeeded
/// and for every failure it does not recognise, which stays the plain
/// refusal it always was.
///
/// Recognised, and only these, because a guess here would hold an arch back
/// or fail a step for a reason the provider never gave:
/// - `api_error_status` 429, or a result saying the subscription's usage
///   limit is reached (`Claude AI usage limit reached|<unix seconds>`, `You've
///   hit your limit`) → rate-limited, until the reset time when the CLI
///   printed one after a `|`;
/// - `api_error_status` 402, or a result saying the credit balance is too
///   low (Claude Code on an API key) → exhausted;
/// - `api_error_status` 404 → retired: the model is not served.
///
/// `overloaded_error` (529) and `error_max_turns` are not here: neither is
/// about the plan.
pub fn provider_refusal(j: &ClaudeJson, now_unix_s: u64) -> Option<AdapterError> {
    if !j.failed() {
        return None;
    }
    let status = j.api_error_status.as_deref().unwrap_or("").trim();
    let said = j.result.to_ascii_lowercase();
    if status.starts_with("429")
        || said.contains("usage limit reached")
        || said.contains("hit your limit")
    {
        return Some(AdapterError::RateLimited {
            retry_after: reset_after(&j.result, now_unix_s),
        });
    }
    if status.starts_with("402") || said.contains("credit balance is too low") {
        return Some(AdapterError::Exhausted {
            detail: j.refusal().to_string(),
        });
    }
    if status.starts_with("404") {
        return Some(AdapterError::Retired {
            detail: j.refusal().to_string(),
        });
    }
    None
}

/// The wait until the reset a usage-limit message names as `…|<unix
/// seconds>`; `None` when it names none, or one already past.
fn reset_after(result: &str, now_unix_s: u64) -> Option<Duration> {
    let (_, at) = result.rsplit_once('|')?;
    let at: u64 = at.trim().parse().ok()?;
    at.checked_sub(now_unix_s)
        .filter(|wait| *wait > 0)
        .map(Duration::from_secs)
}

/// A JSON scalar as text, with `null` and absence both meaning "not said".
fn rendered(v: Option<&Value>) -> Option<String> {
    match v {
        None | Some(Value::Null) => None,
        Some(Value::String(s)) => Some(s.clone()),
        Some(other) => Some(other.to_string()),
    }
}

/// Enough of what was printed to recognise it in an error message, and no
/// more: the output can be the whole completion, and an error is not a place
/// to reprint one.
fn head(s: &str) -> String {
    let trimmed = s.trim();
    if trimmed.is_empty() {
        return "nothing".into();
    }
    let cut = trimmed
        .char_indices()
        .map(|(i, _)| i)
        .nth(120)
        .unwrap_or(trimmed.len());
    if cut < trimmed.len() {
        format!("{}…", &trimmed[..cut])
    } else {
        trimmed.to_string()
    }
}
