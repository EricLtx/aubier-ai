//! The HTTP plumbing the API arches of this family share — this crate's own
//! chat-completions arch, `vk-arch-mistral` and `vk-arch-gemini` — so that the
//! three of them refuse, scrub and classify the same way.
//!
//! Nothing here knows a provider. What it knows is the four rules every one
//! of them keeps: the client never follows a redirect (a custom key header
//! would be carried across it), every string that came off the far end is
//! scrubbed of the key before this node repeats it, a failed call says
//! `http status <code>:` first so it can be told apart from a transport
//! failure, and the estimate a prompt is projected by runs high.
use anyhow::{anyhow, bail, Context, Result};
use reqwest::blocking::{Client, Request, Response};
use serde_json::Value;
use std::io::Read;
use std::time::Duration;
use vk_arch_anthropic::SecretString;

/// The longest answer one call may produce, on every arch in this family —
/// the same cap the Anthropic arch keeps, and for the same reason: an arch
/// driven as a completion engine has no business generating a book because
/// nobody bounded it.
pub const MAX_OUTPUT_TOKENS: u32 = 4096;

/// How much of the ceiling a prompt may take before the call is refused (I4′).
/// The tenth left over is the margin the count is allowed to be wrong by.
pub const CONTEXT_HEADROOM: f64 = 0.9;

/// The most one response may weigh, on every arch in this family. A real
/// answer is a few kilobytes of JSON; a far end that sends more is broken or
/// hostile, and the kernel does not buffer it to find out which.
pub const MAX_BODY_BYTES: u64 = 4 * 1024 * 1024;

/// How long a call that is only metadata — a token count — may take.
pub const COUNT_TIMEOUT: Duration = Duration::from_secs(30);

// ------------------------------------------------------------ the failures

/// What a failed HTTP call was, as far as the kernel will want to know.
///
/// Not yet an [`vk_kernel::arch::AdapterError`] arm: every failure still
/// reaches the kernel as `AdapterError::Other`, carrying a message that
/// starts `http status <code>:` and names this classification right after.
/// The enum is the classification itself, kept in one place so that the day
/// the kernel has arms for it the mapping is one `match` and not a re-reading
/// of three providers' error pages.
// map to AdapterError::{RateLimited, Exhausted, Retired} once arch-quota-health merges
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HttpFailure {
    /// 429: the provider is pacing this key. The same call later may succeed.
    RateLimited,
    /// 402, or a 429 whose code says the account has nothing left: no call
    /// will succeed until a human pays or raises a limit.
    Exhausted,
    /// 404: the model is not — or no longer — served at this origin. The
    /// path is fixed by the adapter, so what is missing is the model.
    Retired,
    /// Anything else: a bad request, a bad key, a server that failed.
    Other,
}

impl HttpFailure {
    /// The words the error message carries right after its status.
    pub fn as_str(self) -> &'static str {
        match self {
            HttpFailure::RateLimited => "rate limited",
            HttpFailure::Exhausted => "credit or quota exhausted",
            HttpFailure::Retired => "model not served",
            HttpFailure::Other => "failed",
        }
    }
}

/// Provider codes that mean "the account is out", sent on a 429 rather than
/// a 402. `insufficient_quota` is OpenAI's documented one, which the servers
/// that copy its API copy too.
const EXHAUSTED_CODES: &[&str] = &["insufficient_quota"];

/// Classify a failed call by its status and, where the provider sent one,
/// its machine-readable error code.
// map to AdapterError::{RateLimited, Exhausted, Retired} once arch-quota-health merges
pub fn classify_status(status: u16, code: Option<&str>) -> HttpFailure {
    match status {
        402 => HttpFailure::Exhausted,
        429 if code.is_some_and(|c| EXHAUSTED_CODES.contains(&c)) => HttpFailure::Exhausted,
        429 => HttpFailure::RateLimited,
        404 => HttpFailure::Retired,
        _ => HttpFailure::Other,
    }
}

/// The parts of a provider's error body worth repeating, whichever of the
/// shapes in use it came in:
///
/// * `{"error": {"message", "type" | "status", "code"}}` — OpenAI,
///   llama-server, and Gemini (whose `status` is `RESOURCE_EXHAUSTED` and the
///   like, and whose numeric `code` is the HTTP status again);
/// * `{"object": "error", "message", "type", "code"}` — Mistral and vLLM;
/// * `{"message": …}` or `{"detail": …}` — a gateway in front of either.
#[derive(Debug, Default, PartialEq)]
pub struct ErrorBody {
    pub kind: Option<String>,
    pub message: Option<String>,
    /// A machine-readable code, when the provider sent one as a string.
    pub code: Option<String>,
}

impl ErrorBody {
    pub fn of(v: &Value) -> ErrorBody {
        let inner = if v["error"].is_object() {
            &v["error"]
        } else {
            v
        };
        let text = |x: &Value| x.as_str().map(str::to_string);
        ErrorBody {
            kind: text(&inner["type"]).or_else(|| text(&inner["status"])),
            message: text(&inner["message"])
                .or_else(|| text(&v["message"]))
                .or_else(|| text(&v["detail"]))
                .or_else(|| text(&v["error"])),
            code: text(&inner["code"]),
        }
    }
}

// --------------------------------------------------------------- the wire

/// `text` with the key, if there is one, taken out of it.
pub fn scrub(key: Option<&SecretString>, text: &str) -> String {
    match key {
        Some(k) => k.scrub(text),
        None => text.to_string(),
    }
}

/// The blocking client every call in this family goes through.
///
/// **`Policy::none()` is load-bearing**, as it is on the Anthropic arch:
/// reqwest follows up to ten redirects by default and strips only
/// `authorization` and four other headers on a host change, so a custom key
/// header (`x-goog-api-key`) would be re-sent verbatim to whatever a `307`
/// named, POST body and all. None of these APIs redirects; a redirect is an
/// interception or a misconfiguration, and either is a failed call.
///
/// The machine's proxy configuration **is** honoured, for the Anthropic
/// arch's reason: these talk to the public internet from whatever network the
/// node is on, and a proxy is how many networks reach it at all. A connect
/// timeout makes a black-holed route fail in ten seconds rather than at the
/// call's own timeout.
pub fn build_client(user_agent: &str) -> Result<Client> {
    Client::builder()
        .user_agent(user_agent)
        .connect_timeout(Duration::from_secs(10))
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .context("build the HTTPS client")
}

/// Send `request` — built, and when it carries a key, built by
/// [`SecretString::authorize`], which checked where it goes. A transport
/// error can name the URL; it cannot be allowed to name anything else this
/// node put on the request.
pub fn send(client: &Client, key: Option<&SecretString>, request: Request) -> Result<Response> {
    client
        .execute(request)
        .map_err(|e| anyhow!(scrub(key, &format!("{e:#}"))))
}

/// A response's body as JSON, or the failure: `http status <code>:` first,
/// then the classification, then the provider's own `type` and `message` —
/// which are what the person reading it needs — and never the key, wherever
/// in the body the far end put it.
pub fn read_json(
    provider: &str,
    what: &str,
    key: Option<&SecretString>,
    r: Response,
) -> Result<Value> {
    let status = r.status();
    // A far end that declares more than the cap is refused before a byte of
    // it is read; one that does not declare, or lies, is cut at the cap.
    if r.content_length().is_some_and(|n| n > MAX_BODY_BYTES) {
        bail!("{what}: answer larger than {MAX_BODY_BYTES} bytes");
    }
    let text = read_bounded(r)?;
    let parsed: Value = serde_json::from_str(&text).unwrap_or(Value::Null);
    if !status.is_success() {
        let body = ErrorBody::of(&parsed);
        let class = classify_status(status.as_u16(), body.code.as_deref());
        // Both cut, like an unparsed body: a 400 or a 422 from vLLM or
        // llama-server can echo the whole prompt back in its message, and
        // this message reaches the client (review M-1).
        let kind = head(body.kind.as_deref().unwrap_or("error"), 100);
        let message = head(body.message.as_deref().unwrap_or(&text), 300);
        bail!(
            "http status {}: {}: {what}: {provider} answered {status} ({}): {}",
            status.as_u16(),
            class.as_str(),
            scrub(key, &kind),
            scrub(key, &message)
        );
    }
    if parsed.is_null() {
        bail!(
            "{what}: cannot read the answer of {provider}: {}",
            scrub(key, &head(&text, 300))
        );
    }
    Ok(parsed)
}

/// `r` read to its end, but never past [`MAX_BODY_BYTES`]: one byte more than
/// the cap is asked for, so that "exactly the cap" passes and "past it" is
/// told apart. The refusal repeats none of the body.
fn read_bounded<R: Read>(r: R) -> Result<String> {
    let mut bytes = Vec::new();
    // A read that fails midway leaves what was read, as `Response::text` did.
    let _ = r.take(MAX_BODY_BYTES + 1).read_to_end(&mut bytes);
    if bytes.len() as u64 > MAX_BODY_BYTES {
        bail!("answer larger than {MAX_BODY_BYTES} bytes");
    }
    Ok(String::from_utf8_lossy(&bytes).into_owned())
}

/// The first `n` characters of `s`, marked when something was cut — a far end
/// that answers with a whole HTML error page must not put it in a log line.
pub fn head(s: &str, n: usize) -> String {
    if s.chars().count() <= n {
        return s.trim().to_string();
    }
    format!("{}…", s.chars().take(n).collect::<String>())
}

// ----------------------------------------------------------- the numbers

/// The same deliberately crude estimate every arch on this node projects by:
/// three bytes to the token plus a fixed margin. It **over**-counts — prose
/// runs nearer four bytes to the token (spike 1a measured 1.25× on prose and
/// 1.9× on a short prompt) — which is the direction a refusal should err in.
pub fn estimate_tokens(text: &str) -> u32 {
    u32::try_from(text.len() / 3 + 64).unwrap_or(u32::MAX)
}

/// The largest prompt an arch with this ceiling accepts: nine tenths of it.
/// One number for the budget the kernel is told and the limit `complete`
/// enforces, so the kernel can never fit a prompt to a size the adapter then
/// refuses.
pub fn usable(ceiling: u32) -> u32 {
    (f64::from(ceiling) * CONTEXT_HEADROOM) as u32
}

/// The bound on one answer: the smaller of what the caller asked for and the
/// mount's cap — itself never past [`MAX_OUTPUT_TOKENS`] — and the cap when
/// the caller says `0`, which means "no opinion". A mount cap of `0` is
/// refused where the mount is checked ([`crate::check_max_tokens`]), not
/// quietly raised to 1 here (review M-7).
pub fn answer_tokens(mount_cap: u32, asked: u32) -> u32 {
    let cap = mount_cap.min(MAX_OUTPUT_TOKENS);
    if asked == 0 {
        cap
    } else {
        asked.min(cap)
    }
}

/// A `u64` count off the wire, saturated into the `u32` the kernel keeps.
pub fn count(v: &Value) -> u32 {
    u32::try_from(v.as_u64().unwrap_or(0)).unwrap_or(u32::MAX)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn a_body_is_read_up_to_the_cap_and_refused_one_byte_past_it() {
        let at_cap = vec![b'a'; MAX_BODY_BYTES as usize];
        assert_eq!(
            read_bounded(&at_cap[..]).expect("at the cap").len() as u64,
            MAX_BODY_BYTES
        );
        let past = vec![b'a'; MAX_BODY_BYTES as usize + 1];
        let err = format!("{:#}", read_bounded(&past[..]).expect_err("past the cap"));
        assert_eq!(err, format!("answer larger than {MAX_BODY_BYTES} bytes"));
    }

    #[test]
    fn a_429_is_a_rate_limit_unless_its_code_says_the_account_is_out() {
        assert_eq!(classify_status(429, None), HttpFailure::RateLimited);
        assert_eq!(
            classify_status(429, Some("rate_limit_exceeded")),
            HttpFailure::RateLimited
        );
        assert_eq!(
            classify_status(429, Some("insufficient_quota")),
            HttpFailure::Exhausted
        );
        assert_eq!(classify_status(402, None), HttpFailure::Exhausted);
        assert_eq!(classify_status(404, None), HttpFailure::Retired);
        for other in [400, 401, 403, 422, 500, 503] {
            assert_eq!(classify_status(other, None), HttpFailure::Other, "{other}");
        }
    }

    #[test]
    fn every_error_shape_in_use_is_read() {
        let openai = json!({"error": {"message": "m", "type": "t", "code": "insufficient_quota"}});
        assert_eq!(
            ErrorBody::of(&openai),
            ErrorBody {
                kind: Some("t".into()),
                message: Some("m".into()),
                code: Some("insufficient_quota".into()),
            }
        );
        // Gemini: a numeric code (the HTTP status again) is not a code.
        let gemini =
            json!({"error": {"code": 429, "message": "m", "status": "RESOURCE_EXHAUSTED"}});
        let g = ErrorBody::of(&gemini);
        assert_eq!(g.kind.as_deref(), Some("RESOURCE_EXHAUSTED"));
        assert_eq!(g.code, None);
        let mistral = json!({"object": "error", "message": "m", "type": "t", "code": "3001"});
        assert_eq!(ErrorBody::of(&mistral).code.as_deref(), Some("3001"));
        assert_eq!(
            ErrorBody::of(&json!({"detail": "Unauthorized"}))
                .message
                .as_deref(),
            Some("Unauthorized")
        );
        assert_eq!(ErrorBody::of(&Value::Null), ErrorBody::default());
    }

    #[test]
    fn the_answer_cap_is_never_past_the_family_s_own() {
        assert_eq!(answer_tokens(100_000, 0), MAX_OUTPUT_TOKENS);
        assert_eq!(answer_tokens(100_000, 10), 10);
        assert_eq!(answer_tokens(128, 1024), 128);
        assert_eq!(usable(1_000), 900);
        assert_eq!(estimate_tokens(""), 64);
        assert_eq!(estimate_tokens(&"x".repeat(300)), 164);
    }
}
