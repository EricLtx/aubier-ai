//! The `openai-compat` arch against a stand-in server on loopback: what goes
//! on the wire, what comes back off it, what is refused before anything is
//! sent, which claims a mount must make — and what never appears anywhere:
//! the key.
mod fake;

use fake::{Fake, Reply};
use serde_json::{json, Value};
use vk_arch_openai_compat::{
    KeySource, OpenAiCompatAdapter, OpenAiCompatConfig, SecretString, CHAT_PATH, MAX_OUTPUT_TOKENS,
};
use vk_contracts::arch::{Determinism, Locality};
use vk_contracts::labels::Scope;
use vk_kernel::arch::{AdapterError, ArchAdapter, MountSpec};

/// Shaped like a real key, so that "it never appears" is a claim about a
/// string that would be noticed.
const KEY: &str = "sk-compat-TESTKEYTESTKEYTESTKEYTESTKEY";

fn answer(content: Value, prompt_tokens: u32, completion_tokens: u32) -> Value {
    json!({
        "id": "chatcmpl-FAKE",
        "object": "chat.completion",
        "model": "served-model",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    })
}

fn ok_server() -> Fake {
    Fake::start(&[(
        CHAT_PATH,
        Reply::json(200, &answer(json!("the answer"), 1234, 56)),
    )])
}

fn config(fake: &Fake) -> OpenAiCompatConfig {
    OpenAiCompatConfig::new(&fake.base_url, "llama-3.3-70b", 32_768, "local", 0)
}

fn adapter(fake: &Fake) -> OpenAiCompatAdapter {
    OpenAiCompatAdapter::with_config(Some(SecretString::new(KEY)), config(fake)).expect("mounts")
}

fn other(err: AdapterError) -> String {
    match err {
        AdapterError::Other(e) => format!("{e:#}"),
        i4 => panic!("expected a failed call, got an invariant refusal: {i4:?}"),
    }
}

// --------------------------------------------------------------- the call

#[test]
fn complete_sends_one_user_message_with_the_key_as_a_bearer_and_reads_the_answer() {
    let fake = ok_server();
    let done = adapter(&fake)
        .complete("ROLE: draft\nGOAL: say something", 0)
        .expect("answered");
    assert_eq!(done.text, "the answer");

    let sent = fake.only(CHAT_PATH);
    assert_eq!(sent.method, "POST");
    assert_eq!(
        sent.headers.get("authorization").map(String::as_str),
        Some(format!("Bearer {KEY}").as_str()),
        "the key goes on the header the API reads it from, and nowhere else"
    );
    assert!(sent.query.is_none(), "never in a query string");
    assert!(!sent.body.to_string().contains(KEY), "never in the body");
    assert_eq!(sent.body["model"], "llama-3.3-70b");
    assert_eq!(
        sent.body["messages"],
        json!([{"role": "user", "content": "ROLE: draft\nGOAL: say something"}])
    );
    assert_eq!(sent.body["max_tokens"], json!(MAX_OUTPUT_TOKENS));
    assert!(sent.body.get("stream").is_none(), "nothing streams");
}

#[test]
fn an_answer_reports_the_server_s_own_counts_and_claims_no_price() {
    let fake = ok_server();
    let done = adapter(&fake).complete("hello", 0).expect("answered");
    assert_eq!(done.tokens_in_measured, Some(1234));
    assert_eq!(done.tokens_out, Some(56));
    // No price list for a server this node cannot name: no price, never 0.
    assert_eq!(done.cost_list_usd, None);
    let details = done.details.expect("measurements");
    assert_eq!(details["finish_reason"], "stop");
    assert_eq!(details["id"], "chatcmpl-FAKE");
    assert_eq!(details["prompt_counted_by_api"], false);
}

#[test]
fn a_server_that_reports_no_usage_is_recorded_as_unmeasured_not_as_zero() {
    let mut body = answer(json!("fine"), 0, 0);
    body.as_object_mut().unwrap().remove("usage");
    let fake = Fake::start(&[(CHAT_PATH, Reply::json(200, &body))]);
    let done = adapter(&fake).complete("hello", 0).expect("answered");
    assert_eq!(done.tokens_in_measured, None);
    assert_eq!(done.tokens_out, None);
}

#[test]
fn a_reasoning_model_s_thinking_chunks_are_not_the_answer() {
    let fake = Fake::start(&[(
        CHAT_PATH,
        Reply::json(
            200,
            &answer(
                json!([
                    {"type": "thinking", "thinking": [{"type": "text", "text": "hmm"}]},
                    {"type": "text", "text": "first"},
                    {"type": "text", "text": " second"},
                ]),
                10,
                10,
            ),
        ),
    )]);
    assert_eq!(
        adapter(&fake).complete("hi", 0).expect("answered").text,
        "first second"
    );
}

#[test]
fn a_filtered_or_empty_answer_is_a_failed_call_and_not_an_empty_draft() {
    let mut filtered = answer(json!(""), 10, 0);
    filtered["choices"][0]["finish_reason"] = json!("content_filter");
    let fake = Fake::start(&[(CHAT_PATH, Reply::json(200, &filtered))]);
    let text = other(adapter(&fake).complete("hi", 0).expect_err("filtered"));
    assert!(text.contains("content_filter"), "{text}");

    let fake = Fake::start(&[(CHAT_PATH, Reply::json(200, &answer(json!("  "), 10, 0)))]);
    let text = other(adapter(&fake).complete("hi", 0).expect_err("empty"));
    assert!(text.contains("no text at all"), "{text}");
}

#[test]
fn one_answer_is_bounded_and_never_past_the_family_s_cap() {
    let fake = ok_server();
    let a = adapter(&fake);
    a.complete("hi", 1_000_000).expect("answered");
    a.complete("hi", 128).expect("answered");
    let sent = fake.seen(CHAT_PATH);
    assert_eq!(sent[0].body["max_tokens"], json!(MAX_OUTPUT_TOKENS));
    assert_eq!(sent[1].body["max_tokens"], 128);
}

// ------------------------------------------------------------------- I4′

#[test]
fn a_prompt_past_nine_tenths_of_the_stated_window_is_refused_before_anything_is_sent() {
    let fake = ok_server();
    let a = OpenAiCompatAdapter::with_config(
        Some(SecretString::new(KEY)),
        OpenAiCompatConfig::new(&fake.base_url, "m", 1_000, "local", 0),
    )
    .expect("mounts");
    assert_eq!(a.context_budget(), 900);
    // 3 000 bytes estimate at 1 064 tokens, past the 900 budget.
    match a.complete(&"x".repeat(3_000), 0) {
        Err(AdapterError::I4Prime { needed, ceiling }) => {
            assert_eq!(needed, 1_064);
            assert_eq!(ceiling, 900);
        }
        other => panic!("expected an I4′ refusal, got {other:?}"),
    }
    assert!(fake.all().is_empty(), "nothing was sent");
}

#[test]
fn an_answer_whose_measured_prompt_reached_the_window_is_refused_after_the_call() {
    let fake = Fake::start(&[(
        CHAT_PATH,
        Reply::json(200, &answer(json!("half an answer"), 4_096, 10)),
    )]);
    let a = OpenAiCompatAdapter::with_config(
        Some(SecretString::new(KEY)),
        OpenAiCompatConfig::new(&fake.base_url, "m", 4_096, "local", 0),
    )
    .expect("mounts");
    match a.complete("short", 0) {
        Err(AdapterError::I4Prime { needed, ceiling }) => {
            assert_eq!(needed, 4_096);
            assert_eq!(ceiling, 4_096, "the full window, the number compared");
        }
        other => panic!("expected an I4′ refusal after the call, got {other:?}"),
    }
}

// ------------------------------------------------------------ the failures

#[test]
fn a_failed_call_says_its_http_status_first_and_how_it_failed() {
    for (status, body, class) in [
        (
            429,
            json!({"error": {"message": "slow down", "type": "rate_limit_error"}}),
            "rate limited",
        ),
        (
            429,
            json!({"error": {"message": "no money", "type": "insufficient_quota", "code": "insufficient_quota"}}),
            "credit or quota exhausted",
        ),
        (
            402,
            json!({"error": {"message": "add credits"}}),
            "credit or quota exhausted",
        ),
        (
            404,
            json!({"error": {"message": "model gone", "type": "not_found_error"}}),
            "model not served",
        ),
        (400, json!({"detail": "bad"}), "failed"),
    ] {
        let fake = Fake::start(&[(CHAT_PATH, Reply::json(status, &body))]);
        let text = other(adapter(&fake).complete("hi", 0).expect_err("failed"));
        assert!(
            text.starts_with(&format!("http status {status}: {class}:")),
            "{status}: {text}"
        );
        // And the provider's own words, which are what the reader needs.
        let message = body["error"]["message"]
            .as_str()
            .or(body["detail"].as_str())
            .unwrap();
        assert!(text.contains(message), "{text}");
    }
}

#[test]
fn a_key_the_far_end_echoes_back_is_scrubbed_out_of_every_error() {
    let fake = Fake::start(&[(
        CHAT_PATH,
        Reply::json(
            401,
            &json!({"error": {
                "message": format!("Incorrect API key provided: {KEY}"),
                "type": format!("leak_{KEY}"),
                "code": KEY,
            }}),
        ),
    )]);
    let text = other(adapter(&fake).complete("hi", 0).expect_err("401"));
    assert!(!text.contains(KEY), "the key is in the error: {text}");
    assert!(
        text.contains("Incorrect API key provided: [redacted]"),
        "{text}"
    );
    assert!(text.contains("leak_[redacted]"), "{text}");
}

#[test]
fn a_redirect_is_refused_and_the_key_never_reaches_the_second_host() {
    for status in [302u16, 307] {
        let collector = ok_server();
        let api = Fake::start(&[(
            CHAT_PATH,
            Reply::redirect(status, &format!("{}{CHAT_PATH}", collector.base_url)),
        )]);
        let text = other(adapter(&api).complete("hi", 0).expect_err("a redirect"));
        assert!(
            text.starts_with(&format!("http status {status}:")),
            "{text}"
        );
        assert!(!text.contains(KEY), "{text}");
        assert!(
            collector.all().is_empty(),
            "{status}: the key was sent to the redirect target"
        );
    }
}

// --------------------------------------------------------------- the origin

#[test]
fn a_server_off_this_machine_must_be_https() {
    let cfg = OpenAiCompatConfig::new("http://collector.example", "m", 8_192, "FR", 30);
    let err = OpenAiCompatAdapter::with_config(Some(SecretString::new(KEY)), cfg)
        .err()
        .expect("plaintext off loopback");
    assert!(format!("{err:#}").contains("in the clear"), "{err:#}");
}

#[test]
fn a_loopback_server_needs_no_key_and_is_sent_none() {
    let fake = ok_server();
    let a = OpenAiCompatAdapter::with_config(None, config(&fake)).expect("mounts keyless");
    a.complete("hi", 0).expect("answered");
    assert!(
        !fake.only(CHAT_PATH).headers.contains_key("authorization"),
        "no key, no header"
    );
}

#[test]
fn a_server_off_this_machine_without_a_key_is_refused_with_the_remedy() {
    let cfg = OpenAiCompatConfig::new("https://api.mammouth.ai", "m", 8_192, "FR", 30);
    let err = OpenAiCompatAdapter::with_config(None, cfg)
        .err()
        .expect("no key");
    assert!(
        format!("{err:#}").contains("vk secret set openai-compat"),
        "{err:#}"
    );
}

#[test]
fn a_key_named_by_file_is_read_even_for_a_loopback_server() {
    let dir = tempfile::tempdir().expect("tempdir");
    let path = dir.path().join("key");
    std::fs::write(&path, format!("{KEY}\n")).expect("write");
    let key = vk_arch_openai_compat::load_key_for(&KeySource::File(path), "http://127.0.0.1:1")
        .expect("read")
        .expect("a key");
    assert_eq!(key.scrub(KEY), "[redacted]");
    // A file the operator named and that is not there is an error, even on
    // loopback: naming one says a key is wanted.
    let err = vk_arch_openai_compat::load_key_for(
        &KeySource::File(dir.path().join("nope")),
        "http://127.0.0.1:1",
    )
    .expect_err("missing");
    assert!(
        format!("{err:#}").contains("vk secret set openai-compat"),
        "{err:#}"
    );
}

// ------------------------------------------------------------- the claims

#[test]
fn the_manifest_carries_the_operator_s_claims_and_is_never_governed() {
    let cloud = OpenAiCompatAdapter::manifest_for(&OpenAiCompatConfig::new(
        "https://api.mammouth.ai",
        "mistral-medium-3",
        131_072,
        "FR",
        30,
    ));
    assert_eq!(cloud.name, "openai-compat/mistral-medium-3");
    assert_eq!(cloud.locality, Locality::Cloud);
    assert_eq!(cloud.jurisdiction, "FR");
    assert_eq!(cloud.retention_days, Some(30));
    assert_eq!(cloud.context_ceiling, 131_072);
    assert_eq!(cloud.cost_per_1k_tokens_eur, None);
    assert_eq!(cloud.clearance.max_scope, Scope::Business);
    assert!(!cloud.clearance.third_party_allowed);
    assert!(!cloud.governed);
    assert_eq!(cloud.determinism, Determinism::NonDeterministic);
    assert!(cloud.validate().is_ok());

    // On a loopback address the socket goes nowhere: Local, still ungoverned,
    // still capped at Business.
    let local = OpenAiCompatAdapter::manifest_for(&OpenAiCompatConfig::new(
        "http://127.0.0.1:8080",
        "qwen3",
        32_768,
        "local",
        0,
    ));
    assert_eq!(local.locality, Locality::Local);
    assert_eq!(local.retention_days, Some(0));
    assert!(!local.governed);
    assert_eq!(local.clearance.max_scope, Scope::Business);
}

#[test]
fn a_corrected_claim_is_a_different_arch() {
    let base = OpenAiCompatConfig::new("https://api.mammouth.ai", "m", 131_072, "FR", 30);
    let id = OpenAiCompatAdapter::manifest_for(&base).arch_id();
    for changed in [
        OpenAiCompatConfig {
            jurisdiction: "US".into(),
            ..base.clone()
        },
        OpenAiCompatConfig {
            retention_days: 0,
            ..base.clone()
        },
        OpenAiCompatConfig {
            context_ceiling: 32_768,
            ..base.clone()
        },
        OpenAiCompatConfig {
            base_url: "https://openrouter.ai/api".into(),
            ..base.clone()
        },
        OpenAiCompatConfig {
            model: "other".into(),
            ..base.clone()
        },
    ] {
        assert_ne!(
            OpenAiCompatAdapter::manifest_for(&changed).arch_id(),
            id,
            "{changed:?}"
        );
    }
}

#[test]
fn a_mount_that_will_not_state_its_claims_is_refused() {
    let key = || Some(SecretString::new(KEY));
    let base = OpenAiCompatConfig::new("https://api.mammouth.ai", "m", 8_192, "FR", 30);
    for (cfg, says) in [
        (
            OpenAiCompatConfig {
                jurisdiction: "unknown".into(),
                ..base.clone()
            },
            "not a claim",
        ),
        (
            OpenAiCompatConfig {
                jurisdiction: "ZZ".into(),
                ..base.clone()
            },
            "user-assigned",
        ),
        (
            OpenAiCompatConfig {
                jurisdiction: "local".into(),
                ..base.clone()
            },
            "name the country",
        ),
        (
            OpenAiCompatConfig {
                context_ceiling: 0,
                ..base.clone()
            },
            "--ctx",
        ),
        (
            OpenAiCompatConfig {
                context_ceiling: u32::MAX,
                ..base.clone()
            },
            "past every published one",
        ),
        (
            OpenAiCompatConfig {
                model: " ".into(),
                ..base.clone()
            },
            "--model",
        ),
    ] {
        let err = OpenAiCompatAdapter::with_config(key(), cfg)
            .err()
            .expect("refused");
        assert!(format!("{err:#}").contains(says), "{says}: {err:#}");
    }
}

#[test]
fn a_mount_spec_for_this_arch_carries_no_key() {
    let spec = MountSpec::new(
        "openai-compat",
        json!({
            "model": "m",
            "context_ceiling": 8_192,
            "jurisdiction": "FR",
            "retention_days": 30,
            "max_tokens": 4096,
        }),
    );
    assert!(spec.is_ok(), "{spec:?}");
    assert!(MountSpec::new("openai-compat", json!({"api_key": KEY})).is_err());
}

#[test]
fn a_secret_never_prints_itself() {
    let key = SecretString::new(KEY);
    assert_eq!(format!("{key:?}"), "SecretString(redacted)");
}

// ------------------------------------------- what comes back off the wire

/// Review M-1: a 400 or a 422 from vLLM or llama-server can echo the whole
/// prompt back in its message, and the message reaches the client. It is cut
/// to a few hundred characters, as an unparsed body already was.
#[test]
fn a_far_end_message_is_cut_before_it_is_repeated() {
    let echoed = "the prompt, echoed back ".repeat(200);
    let fake = Fake::start(&[(
        CHAT_PATH,
        Reply::json(
            400,
            &json!({"object": "error", "message": echoed, "type": "x".repeat(500)}),
        ),
    )]);
    let text = other(adapter(&fake).complete("hi", 0).expect_err("400"));
    assert!(text.starts_with("http status 400:"), "{text}");
    assert!(text.contains('…'), "say that something was cut: {text}");
    assert!(
        text.chars().count() < 800,
        "{} characters reached the client",
        text.chars().count()
    );
}

/// Review M-5: the provider's own ids are strings off the far end like any
/// other, and go into the record scrubbed.
#[test]
fn the_ids_in_the_record_are_scrubbed_of_the_key() {
    let mut body = answer(json!("fine"), 10, 2);
    body["id"] = json!(format!("id-{KEY}"));
    body["model"] = json!(format!("model-{KEY}"));
    let fake = Fake::start(&[(CHAT_PATH, Reply::json(200, &body))]);
    let done = adapter(&fake).complete("hi", 0).expect("answered");
    let details = done.details.expect("details").to_string();
    assert!(
        !details.contains(KEY),
        "the key is in the record: {details}"
    );
    assert!(details.contains("id-[redacted]"), "{details}");
}
