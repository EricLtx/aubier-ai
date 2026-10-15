//! The Mistral arch against a stand-in API on loopback: the request it puts
//! on the wire, the answer it reads back with Mistral's own counts and the EU
//! endpoint's price, what it refuses before anything is sent, how a failed
//! call is classified — and that the key appears nowhere but its header.
mod fake;

use fake::{Fake, Reply};
use serde_json::{json, Value};
use vk_arch_mistral::{
    MistralAdapter, MistralConfig, SecretString, DEFAULT_BASE_URL, DEFAULT_MODEL, MODELS,
    REGIONAL_UPCHARGE,
};
use vk_arch_openai_compat::{CHAT_PATH, MAX_OUTPUT_TOKENS};
use vk_contracts::arch::{Determinism, Locality};
use vk_contracts::labels::Scope;
use vk_kernel::arch::{AdapterError, ArchAdapter, MountSpec};

const KEY: &str = "mistral-TESTKEYTESTKEYTESTKEYTESTKEY";

fn answer(content: Value, prompt_tokens: u32, completion_tokens: u32) -> Value {
    json!({
        "id": "cmpl-FAKE",
        "object": "chat.completion",
        "model": DEFAULT_MODEL,
        "created": 0,
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

fn server(reply: Reply) -> Fake {
    Fake::start(&[(CHAT_PATH, reply)])
}

fn adapter_with(fake: &Fake, cfg: MistralConfig) -> MistralAdapter {
    MistralAdapter::with_config(
        SecretString::new(KEY),
        MistralConfig {
            base_url: fake.base_url.clone(),
            ..cfg
        },
    )
    .expect("mounts")
}

fn adapter(fake: &Fake) -> MistralAdapter {
    adapter_with(fake, MistralConfig::default())
}

fn other(err: AdapterError) -> String {
    match err {
        AdapterError::Other(e) => format!("{e:#}"),
        i4 => panic!("expected a failed call, got an invariant refusal: {i4:?}"),
    }
}

#[test]
fn complete_posts_one_user_message_on_a_bearer_key_and_reads_the_answer() {
    let fake = server(Reply::json(200, &answer(json!("bonjour"), 12, 3)));
    let done = adapter(&fake)
        .complete("ROLE: draft\nGOAL: greet", 0)
        .expect("answered");
    assert_eq!(done.text, "bonjour");

    let sent = fake.only(CHAT_PATH);
    assert_eq!(sent.method, "POST");
    assert_eq!(
        sent.headers.get("authorization").map(String::as_str),
        Some(format!("Bearer {KEY}").as_str())
    );
    assert!(sent.query.is_none());
    assert!(!sent.body.to_string().contains(KEY));
    assert_eq!(sent.body["model"], DEFAULT_MODEL);
    assert_eq!(
        sent.body["messages"],
        json!([{"role": "user", "content": "ROLE: draft\nGOAL: greet"}])
    );
    assert_eq!(sent.body["max_tokens"], json!(MAX_OUTPUT_TOKENS));
    // Nothing the arch identity does not name: no stream, no reasoning knob.
    assert!(sent.body.get("stream").is_none());
    assert!(sent.body.get("reasoning_effort").is_none());
}

#[test]
fn an_answer_reports_mistral_s_counts_and_the_eu_endpoint_s_price() {
    let fake = server(Reply::json(200, &answer(json!("ok"), 100_000, 100_000)));
    let done = adapter(&fake).complete("hello", 0).expect("answered");
    assert_eq!(done.tokens_in_measured, Some(100_000));
    assert_eq!(done.tokens_out, Some(100_000));
    // Large 3 at $0.50 in and $1.50 out per million, times 1.1 for the EU
    // regional endpoint: $0.055 + $0.165 for a hundred thousand of each.
    let cost = done.cost_list_usd.expect("a listed model is priced");
    assert!((cost - 0.22).abs() < 1e-9, "{cost}");
}

#[test]
fn a_reasoning_answer_s_thinking_chunks_stay_out_of_the_register() {
    let fake = server(Reply::json(
        200,
        &answer(
            json!([
                {"type": "thinking", "thinking": [{"type": "text", "text": "let me see"}], "signature": "s"},
                {"type": "text", "text": "the answer"},
            ]),
            10,
            20,
        ),
    ));
    assert_eq!(
        adapter(&fake).complete("hi", 0).expect("answered").text,
        "the answer"
    );
}

#[test]
fn a_prompt_past_nine_tenths_of_the_window_is_refused_before_anything_is_sent() {
    let fake = server(Reply::json(200, &answer(json!("x"), 1, 1)));
    let a = adapter_with(
        &fake,
        MistralConfig {
            context_ceiling: 1_000,
            ..Default::default()
        },
    );
    assert_eq!(a.context_budget(), 900);
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
fn the_post_check_refuses_an_answer_whose_measured_prompt_reached_the_window() {
    let fake = server(Reply::json(200, &answer(json!("x"), 4_096, 1)));
    let a = adapter_with(
        &fake,
        MistralConfig {
            context_ceiling: 4_096,
            ..Default::default()
        },
    );
    match a.complete("short", 0) {
        Err(AdapterError::I4Prime { needed, ceiling }) => {
            assert_eq!((needed, ceiling), (4_096, 4_096));
        }
        other => panic!("expected an I4′ refusal after the call, got {other:?}"),
    }
}

/// The three failures the kernel will want to tell apart, in the shapes
/// Mistral documents them — and its real 401, whose body is a bare
/// `{"detail": …}` rather than the documented error object.
#[test]
fn rate_limit_exhausted_credit_and_a_retired_model_are_classified() {
    for (status, body, class, says) in [
        (
            429,
            json!({"object": "error", "message": "Requests rate limit exceeded", "type": "rate_limit_error", "code": "1300"}),
            "rate limited",
            "Requests rate limit exceeded",
        ),
        (
            402,
            json!({"object": "error", "message": "The prepaid credit balance has reached zero", "type": "invalid_request_error"}),
            "credit or quota exhausted",
            "prepaid credit",
        ),
        (
            404,
            json!({"object": "error", "message": "Invalid model: magistral-medium-2509", "type": "invalid_request_error", "param": "model", "code": "unknown_model"}),
            "model not served",
            "Invalid model",
        ),
        (
            401,
            json!({"detail": "Invalid API Key"}),
            "failed",
            "Invalid API Key",
        ),
    ] {
        let fake = server(Reply::json(status, &body));
        let text = other(adapter(&fake).complete("hi", 0).expect_err("failed"));
        assert!(
            text.starts_with(&format!("http status {status}: {class}:")),
            "{status}: {text}"
        );
        assert!(text.contains(says), "{text}");
        assert!(text.contains("the Mistral API"), "{text}");
    }
}

#[test]
fn a_key_echoed_back_by_the_far_end_never_reaches_an_error() {
    let fake = server(Reply::json(
        401,
        &json!({"object": "error", "message": format!("bad key {KEY}"), "type": format!("t{KEY}"), "code": KEY}),
    ));
    let text = other(adapter(&fake).complete("hi", 0).expect_err("401"));
    assert!(!text.contains(KEY), "{text}");
    assert!(text.contains("bad key [redacted]"), "{text}");
}

#[test]
fn a_redirect_is_refused_and_the_key_never_reaches_the_second_host() {
    let collector = server(Reply::json(200, &answer(json!("x"), 1, 1)));
    let api = server(Reply::redirect(
        307,
        &format!("{}{CHAT_PATH}", collector.base_url),
    ));
    let text = other(adapter(&api).complete("hi", 0).expect_err("redirect"));
    assert!(text.starts_with("http status 307:"), "{text}");
    assert!(collector.all().is_empty(), "the key followed the redirect");
}

#[test]
fn an_origin_off_this_machine_in_plaintext_is_refused_at_mount() {
    let err = MistralAdapter::with_config(
        SecretString::new(KEY),
        MistralConfig {
            base_url: "http://api.eu.mistral.ai".into(),
            ..Default::default()
        },
    )
    .err()
    .expect("plaintext");
    assert!(format!("{err:#}").contains("in the clear"), "{err:#}");
}

#[test]
fn the_manifest_is_an_eu_cloud_arch_kept_thirty_days_at_business_and_ungoverned() {
    let m = MistralAdapter::manifest_for(&MistralConfig::default());
    assert_eq!(m.name, format!("mistral/{DEFAULT_MODEL}"));
    assert_eq!(m.locality, Locality::Cloud);
    assert_eq!(m.jurisdiction, "EU");
    assert_eq!(m.retention_days, Some(30));
    assert_eq!(m.clearance.max_scope, Scope::Business);
    assert!(!m.clearance.third_party_allowed);
    assert!(!m.governed);
    assert_eq!(m.determinism, Determinism::NonDeterministic);
    assert_eq!(m.context_ceiling, 256_000);
    assert_eq!(
        m.identity.sampling.get("base_url").map(String::as_str),
        Some(DEFAULT_BASE_URL)
    );
    // $0.50 per million in, times 1.1, at the node's pinned rate.
    let price = m.cost_per_1k_tokens_eur.expect("priced");
    let expected = 0.50 * REGIONAL_UPCHARGE / 1_000.0 * vk_arch_openai_compat::EUR_PER_USD;
    assert!((price - expected).abs() < 1e-12, "{price}");
    assert!(m.validate().is_ok());
}

#[test]
fn the_fixed_origin_is_the_eu_regional_endpoint() {
    assert_eq!(DEFAULT_BASE_URL, "https://api.eu.mistral.ai");
    assert!(vk_arch_openai_compat::check_base_url(DEFAULT_BASE_URL).is_ok());
}

#[test]
fn an_unlisted_model_or_an_alias_mounts_with_no_price_and_the_fallback_window() {
    for id in ["mistral-large-latest", "mistral-imaginary-9"] {
        let m = MistralAdapter::manifest_for(&MistralConfig {
            model: id.into(),
            ..Default::default()
        });
        assert_eq!(m.cost_per_1k_tokens_eur, None, "{id}");
        assert_eq!(m.context_ceiling, vk_arch_mistral::FALLBACK_CONTEXT, "{id}");
    }
}

#[test]
fn every_model_in_the_table_is_pinned_priced_and_has_a_window() {
    for m in MODELS {
        assert!(!m.id.contains("latest"), "{} is an alias", m.id);
        assert!(
            m.context_window >= vk_arch_mistral::FALLBACK_CONTEXT,
            "{}",
            m.id
        );
        assert!(m.input_usd_per_mtok > 0.0, "{}", m.id);
        assert!(m.output_usd_per_mtok >= m.input_usd_per_mtok, "{}", m.id);
    }
    assert!(vk_arch_mistral::model(DEFAULT_MODEL).is_some());
}

#[test]
fn the_model_and_the_origin_are_part_of_the_arch_id() {
    let base = MistralAdapter::manifest_for(&MistralConfig::default());
    let small = MistralAdapter::manifest_for(&MistralConfig {
        model: "mistral-small-2603".into(),
        ..Default::default()
    });
    let elsewhere = MistralAdapter::manifest_for(&MistralConfig {
        base_url: "https://api.mistral.ai".into(),
        ..Default::default()
    });
    assert_ne!(base.arch_id(), small.arch_id());
    assert_ne!(base.arch_id(), elsewhere.arch_id());
}

#[test]
fn a_mount_spec_for_this_arch_carries_no_key() {
    assert!(MountSpec::new(
        "mistral",
        json!({"model": DEFAULT_MODEL, "max_tokens": 4096, "context_ceiling": 0})
    )
    .is_ok());
    assert!(MountSpec::new("mistral", json!({"api_key": KEY})).is_err());
}

#[test]
fn a_missing_key_file_names_the_remedy() {
    let dir = tempfile::tempdir().expect("tempdir");
    let err = vk_arch_mistral::load_key(
        &vk_arch_mistral::KeySource::File(dir.path().join("nope")),
        vk_arch_mistral::KEYRING_USER,
    )
    .expect_err("missing");
    assert!(
        format!("{err:#}").contains("vk secret set mistral"),
        "{err:#}"
    );
}
