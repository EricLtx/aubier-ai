//! Review M-6, alone in its own test binary on purpose.
//!
//! The warning is read back through a scoped `tracing` subscriber, and
//! `tracing` caches whether a call site is of interest process-wide: a test
//! that reached the same `warn!` first, on another thread with no subscriber,
//! could leave it marked uninteresting and this test reading an empty log. No
//! other test in this process reaches that call site, so the reading is
//! deterministic.
mod fake;

use fake::{Fake, Reply};
use serde_json::json;
use std::sync::{Arc, Mutex};
use vk_arch_openai_compat::{OpenAiCompatAdapter, OpenAiCompatConfig, SecretString, CHAT_PATH};
use vk_kernel::arch::ArchAdapter;

#[derive(Clone, Default)]
struct Captured(Arc<Mutex<Vec<u8>>>);

impl std::io::Write for Captured {
    fn write(&mut self, b: &[u8]) -> std::io::Result<usize> {
        self.0.lock().unwrap().extend_from_slice(b);
        Ok(b.len())
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

/// A server that reports no `usage` leaves the I4′ post-check with nothing
/// to check. That is said — once per arch, not once per call — and the
/// record says it too.
#[test]
fn a_skipped_post_check_is_said_once_and_recorded() {
    let body = json!({
        "id": "chatcmpl-FAKE",
        "object": "chat.completion",
        "model": "served-model",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": "fine"},
            "finish_reason": "stop",
        }],
    });
    let fake = Fake::start(&[(CHAT_PATH, Reply::json(200, &body))]);
    let a = OpenAiCompatAdapter::with_config(
        Some(SecretString::new("sk-compat-WARNWARNWARNWARN")),
        OpenAiCompatConfig::new(&fake.base_url, "m", 32_768, "local", 0),
    )
    .expect("mounts");

    let captured = Captured::default();
    let writer = captured.clone();
    let subscriber = tracing_subscriber::fmt()
        .with_writer(move || writer.clone())
        .with_max_level(tracing::Level::WARN)
        .with_ansi(false)
        .finish();
    let details = tracing::subscriber::with_default(subscriber, || {
        let first = a.complete("hi", 0).expect("answered");
        a.complete("hi again", 0).expect("answered");
        first.details.expect("details")
    });
    let log = String::from_utf8(captured.0.lock().unwrap().clone()).unwrap();
    assert_eq!(
        log.matches("post-check").count(),
        1,
        "once per arch, not per call: {log}"
    );
    assert!(log.contains("WARN"), "{log}");
    assert_eq!(details["usage_reported"], false);
}
