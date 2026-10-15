//! The wire between the host and a driver: JSON lines over stdio, protocol 1
//! (pilot spec §4.2).
//!
//! The driver's first line is a [`Hello`] in its envelope, `{"hello": {...}}`.
//! Each action after that is one [`Request`] line from the host and one
//! [`Response`] line from the driver carrying the same `id`; one request is in
//! flight at a time. Evidence travels by path ([`EvidenceRef`]), never inline:
//! the driver writes files under the `evidence_dir` the host created for the
//! request, and the host reads them back under its own caps.
//!
//! The response types take unknown fields in their stride — a newer driver may
//! say more than this host understands — but a field this host relies on
//! (`id`, `ok`) must be there and well-typed, or the line is a protocol error.
use serde::{Deserialize, Serialize};
use serde_json::Value;

/// What the driver says first: its protocol, its identity, and the actions it
/// implements — a superset of the manifest's, or the host refuses it.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Hello {
    pub protocol: u32,
    pub driver: String,
    pub version: String,
    pub actions: Vec<String>,
}

/// The envelope the hello travels in: `{"hello": {...}}`.
#[derive(Debug, Deserialize)]
pub(crate) struct HelloLine {
    pub hello: Hello,
}

/// One action asked of the driver. `evidence_dir` is the absolute path of the
/// directory the host made for this request's evidence files.
#[derive(Debug, Serialize)]
pub struct Request<'a> {
    pub id: u64,
    pub action: &'a str,
    pub args: &'a Value,
    pub deadline_ms: u64,
    pub evidence_dir: String,
}

/// One evidence file the driver wrote: the name the manifest declares for the
/// action, its media type, and where under `evidence_dir` it is.
#[derive(Debug, Deserialize)]
pub struct EvidenceRef {
    pub name: String,
    pub mime: String,
    pub path: String,
}

/// The five words a driver's `ok: false` may give as its `reason` (pilot spec
/// §4.2). The host pins every response to this set before the kernel sees it
/// ([`Response::closed_reason`]), so the driver's own `reason` on the ledger
/// is one of these words and never the driver's, or the site's, text
/// (Ruling 28).
pub const RESPONSE_REASONS: &[&str] = &["dialog", "timeout", "host", "precondition", "internal"];

/// The driver's answer to the request with the same `id`. `ok: false` carries
/// a `reason` (`dialog|timeout|host|precondition|internal`) and a `detail`;
/// either way `result` and `evidence` may be present.
#[derive(Debug, Deserialize)]
pub struct Response {
    pub id: u64,
    pub ok: bool,
    #[serde(default)]
    pub reason: Option<String>,
    #[serde(default)]
    pub detail: Option<String>,
    #[serde(default)]
    pub result: Value,
    #[serde(default)]
    pub evidence: Vec<EvidenceRef>,
}

impl Response {
    /// The response's `reason` and `detail` with the reason pinned to
    /// [`RESPONSE_REASONS`]: a word outside the set becomes `internal`, and
    /// the word the driver gave moves into the detail, for the log. A
    /// missing reason stays missing.
    pub fn closed_reason(
        reason: Option<String>,
        detail: Option<String>,
    ) -> (Option<String>, Option<String>) {
        match reason {
            Some(word) if RESPONSE_REASONS.contains(&word.as_str()) => (Some(word), detail),
            Some(word) => {
                let note = format!("reason outside the protocol's set: {word:?}");
                let detail = match detail {
                    Some(d) if !d.is_empty() => format!("{note}; {d}"),
                    _ => note,
                };
                (Some("internal".into()), Some(detail))
            }
            None => (None, detail),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn the_request_line_has_the_spec_shape() {
        let args = json!({"case": "N3-02"});
        let r = Request {
            id: 17,
            action: "poser_question",
            args: &args,
            deadline_ms: 120_000,
            evidence_dir: "/tmp/ev/17".into(),
        };
        let v: Value = serde_json::from_str(&serde_json::to_string(&r).unwrap()).unwrap();
        assert_eq!(
            v,
            json!({
                "id": 17,
                "action": "poser_question",
                "args": {"case": "N3-02"},
                "deadline_ms": 120_000,
                "evidence_dir": "/tmp/ev/17"
            })
        );
    }

    #[test]
    fn a_response_needs_only_id_and_ok_and_takes_unknown_fields() {
        let r: Response = serde_json::from_str(r#"{"id": 3, "ok": false, "later": 1}"#).unwrap();
        assert_eq!(r.id, 3);
        assert!(!r.ok);
        assert_eq!(r.reason, None);
        assert_eq!(r.detail, None);
        assert_eq!(r.result, Value::Null);
        assert!(r.evidence.is_empty());
        assert!(serde_json::from_str::<Response>(r#"{"id": 3}"#).is_err());
        assert!(serde_json::from_str::<Response>(r#"{"ok": true}"#).is_err());
    }

    #[test]
    fn the_hello_rides_its_envelope() {
        let h: HelloLine = serde_json::from_str(
            r#"{"hello": {"protocol": 1, "driver": "fake", "version": "0", "actions": ["a"]}}"#,
        )
        .unwrap();
        assert_eq!(h.hello.protocol, 1);
        assert_eq!(h.hello.actions, ["a"]);
        assert!(serde_json::from_str::<HelloLine>(r#"{"protocol": 1}"#).is_err());
    }
}
