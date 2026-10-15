//! The Gemini arch against a stand-in API on loopback: the `countTokens` and
//! `generateContent` it sends, the key on its header and nowhere else, the
//! answer read back without the thinking, Google's own counts and the price
//! they come to, I4′ before and after, how a failed call is classified, and
//! the finishes that are not answers.
mod fake;

use fake::{Fake, Reply};
use serde_json::{json, Value};
use vk_arch_gemini::{
    GeminiAdapter, GeminiConfig, SecretString, Thinking, API_VERSION, DEFAULT_BASE_URL,
    DEFAULT_MODEL, KEY_HEADER, MODELS,
};
use vk_arch_openai_compat::MAX_OUTPUT_TOKENS;
use vk_contracts::arch::{Determinism, Locality};
use vk_contracts::labels::Scope;
use vk_kernel::arch::{AdapterError, ArchAdapter, MountSpec};

const KEY: &str = "AIzaSyTESTKEYTESTKEYTESTKEYTESTKEY12";

fn generate_path(model: &str) -> String {
    format!("/{API_VERSION}/models/{model}:generateContent")
}

fn count_path(model: &str) -> String {
    format!("/{API_VERSION}/models/{model}:countTokens")
}

fn answer(parts: Value, finish: &str, prompt: u32, candidates: u32, thoughts: u32) -> Value {
    json!({
        "candidates": [{
            "content": {"role": "model", "parts": parts},
            "finishReason": finish,
            "index": 0,
        }],
        "usageMetadata": {
            "promptTokenCount": prompt,
            "candidatesTokenCount": candidates,
            "thoughtsTokenCount": thoughts,
            "totalTokenCount": prompt + candidates + thoughts,
        },
        "modelVersion": DEFAULT_MODEL,
        "responseId": "resp-FAKE",
    })
}

fn ok_answer() -> Value {
    answer(
        json!([{"text": "thinking out loud", "thought": true}, {"text": "the answer"}]),
        "STOP",
        1_200,
        40,
        300,
    )
}

/// A stand-in answering both paths of the default model.
fn server(count: Option<u32>, generate: Reply) -> Fake {
    let count_reply = match count {
        Some(n) => Reply::json(200, &json!({"totalTokens": n})),
        None => Reply::json(
            429,
            &json!({"error": {"code": 429, "message": "slow down", "status": "RESOURCE_EXHAUSTED"}}),
        ),
    };
    Fake::start(&[
        (&count_path(DEFAULT_MODEL), count_reply),
        (&generate_path(DEFAULT_MODEL), generate),
    ])
}

fn adapter_with(fake: &Fake, cfg: GeminiConfig) -> GeminiAdapter {
    GeminiAdapter::with_config(
        SecretString::new(KEY),
        GeminiConfig {
            base_url: fake.base_url.clone(),
            ..cfg
        },
    )
    .expect("mounts")
}

fn adapter(fake: &Fake) -> GeminiAdapter {
    adapter_with(fake, GeminiConfig::default())
}

fn other(err: AdapterError) -> String {
    match err {
        AdapterError::Other(e) => format!("{e:#}"),
        i4 => panic!("expected a failed call, got an invariant refusal: {i4:?}"),
    }
}

#[test]
fn complete_counts_then_generates_with_the_key_on_its_header_only() {
    let fake = server(Some(1_200), Reply::json(200, &ok_answer()));
    let done = adapter(&fake)
        .complete("ROLE: draft\nGOAL: hello", 0)
        .expect("answered");
    // The thought part is the model's reasoning, not the register.
    assert_eq!(done.text, "the answer");

    let counted = fake.only(&count_path(DEFAULT_MODEL));
    let sent = fake.only(&generate_path(DEFAULT_MODEL));
    for request in [&counted, &sent] {
        assert_eq!(request.method, "POST");
        assert_eq!(
            request.headers.get(KEY_HEADER).map(String::as_str),
            Some(KEY)
        );
        // Never `?key=`: a query string is part of the URL every error repeats.
        assert!(request.query.is_none(), "{:?}", request.query);
        assert!(!request.headers.contains_key("authorization"));
        assert!(!request.body.to_string().contains(KEY));
    }
    let contents = json!([{"role": "user", "parts": [{"text": "ROLE: draft\nGOAL: hello"}]}]);
    assert_eq!(counted.body["contents"], contents);
    assert_eq!(sent.body["contents"], contents);
    assert_eq!(
        sent.body["generationConfig"]["maxOutputTokens"],
        json!(MAX_OUTPUT_TOKENS)
    );
    // The thinking is given a quarter of the bound — which counts the
    // thinking — so three quarters of it are left for the answer.
    assert_eq!(
        sent.body["generationConfig"]["thinkingConfig"],
        json!({"thinkingBudget": 1_024})
    );
    // Nothing Google has announced will become an error.
    for knob in ["temperature", "topP", "topK"] {
        assert!(sent.body["generationConfig"].get(knob).is_none(), "{knob}");
    }
}

#[test]
fn an_answer_reports_google_s_counts_with_thinking_billed_as_output() {
    let fake = server(Some(1_200), Reply::json(200, &ok_answer()));
    let done = adapter(&fake).complete("hello", 0).expect("answered");
    assert_eq!(done.tokens_in_measured, Some(1_200));
    // 40 answer tokens and 300 thinking tokens: both output, as billed.
    assert_eq!(done.tokens_out, Some(340));
    // 3.8 Flash at $1.50 in and $7.50 out per million.
    let cost = done.cost_list_usd.expect("a listed model is priced");
    let expected = 1_200.0 * 1.50 / 1e6 + 340.0 * 7.50 / 1e6;
    assert!((cost - expected).abs() < 1e-12, "{cost} vs {expected}");
    let details = done.details.expect("measurements");
    assert_eq!(details["thoughts_tokens"], 300);
    assert_eq!(details["finish_reason"], "STOP");
    assert_eq!(details["response_id"], "resp-FAKE");
    assert_eq!(details["prompt_counted_by_api"], true);
}

#[test]
fn a_long_prompt_on_a_tiered_model_is_priced_at_the_long_rate() {
    let model = "gemini-2.5-pro";
    let fake = Fake::start(&[
        (
            &count_path(model),
            Reply::json(200, &json!({"totalTokens": 250_000})),
        ),
        (
            &generate_path(model),
            Reply::json(
                200,
                &answer(json!([{"text": "long"}]), "STOP", 250_000, 1_000, 0),
            ),
        ),
    ]);
    let done = adapter_with(
        &fake,
        GeminiConfig {
            model: model.into(),
            ..Default::default()
        },
    )
    .complete("a prompt Google counts long", 0)
    .expect("answered");
    let expected = 250_000.0 * 2.50 / 1e6 + 1_000.0 * 15.00 / 1e6;
    let cost = done.cost_list_usd.expect("priced");
    assert!((cost - expected).abs() < 1e-12, "{cost} vs {expected}");
    // And the 2.5 generation is sent a budget, not a level, which it 400s on.
    assert_eq!(
        fake.only(&generate_path(model)).body["generationConfig"]["thinkingConfig"],
        json!({"thinkingBudget": 1_024})
    );
}

#[test]
fn the_pre_check_asks_google_to_count_and_refuses_before_generating() {
    let fake = server(Some(5_000), Reply::json(200, &ok_answer()));
    let a = adapter_with(
        &fake,
        GeminiConfig {
            context_ceiling: 1_000,
            ..Default::default()
        },
    );
    match a.complete("a short prompt Google says is long", 0) {
        Err(AdapterError::I4Prime { needed, ceiling }) => {
            assert_eq!(needed, 5_000);
            assert_eq!(ceiling, 900);
            assert_eq!(ceiling, a.context_budget());
        }
        other => panic!("expected an I4′ refusal, got {other:?}"),
    }
    assert!(fake.seen(&generate_path(DEFAULT_MODEL)).is_empty());
    assert_eq!(fake.seen(&count_path(DEFAULT_MODEL)).len(), 1);
}

#[test]
fn a_counter_that_fails_falls_back_to_the_estimate_rather_than_failing_the_call() {
    let fake = server(None, Reply::json(200, &ok_answer()));
    let done = adapter(&fake).complete("hello", 0).expect("answered");
    assert_eq!(done.text, "the answer");
    assert_eq!(
        done.details.expect("details")["prompt_counted_by_api"],
        false
    );
}

#[test]
fn the_post_check_refuses_an_answer_whose_measured_prompt_reached_the_window() {
    let fake = server(
        Some(10),
        Reply::json(200, &answer(json!([{"text": "x"}]), "STOP", 4_096, 1, 0)),
    );
    let a = adapter_with(
        &fake,
        GeminiConfig {
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

#[test]
fn a_blocked_prompt_or_a_withheld_answer_is_a_failed_call() {
    let blocked = json!({"promptFeedback": {"blockReason": "PROHIBITED_CONTENT"}, "usageMetadata": {"promptTokenCount": 9}});
    let fake = server(Some(9), Reply::json(200, &blocked));
    let text = other(adapter(&fake).complete("hi", 0).expect_err("blocked"));
    assert!(text.contains("blockReason PROHIBITED_CONTENT"), "{text}");

    for reason in ["SAFETY", "RECITATION", "SPII", "OTHER"] {
        let fake = server(
            Some(9),
            Reply::json(200, &answer(json!([{"text": "partial"}]), reason, 9, 1, 0)),
        );
        let text = other(adapter(&fake).complete("hi", 0).expect_err(reason));
        assert!(text.contains(&format!("finishReason {reason}")), "{text}");
    }
}

#[test]
fn a_bound_spent_on_thinking_says_so_rather_than_returning_nothing() {
    let fake = server(
        Some(9),
        Reply::json(
            200,
            &answer(
                json!([{"text": "still thinking", "thought": true}]),
                "MAX_TOKENS",
                9,
                0,
                4_096,
            ),
        ),
    );
    let text = other(
        adapter(&fake)
            .complete("hi", 0)
            .expect_err("nothing written"),
    );
    assert!(text.contains("spent the whole answer bound"), "{text}");
    assert!(text.contains("4096 thinking tokens"), "{text}");
}

/// The classification, in the error shape Google's API returns — numeric
/// `code`, string `status` — and its real answer to a bad key, which is a
/// 400 and not a 401.
#[test]
fn rate_limit_exhausted_credit_and_a_retired_model_are_classified() {
    for (status, body, class) in [
        (
            429,
            json!({"error": {"code": 429, "message": "Resource has been exhausted (e.g. check quota).", "status": "RESOURCE_EXHAUSTED"}}),
            "rate limited",
        ),
        (
            402,
            json!({"error": {"code": 402, "message": "Prepay credit balance is depleted.", "status": "FAILED_PRECONDITION"}}),
            "credit or quota exhausted",
        ),
        (
            404,
            json!({"error": {"code": 404, "message": "models/gemini-3-pro-preview is not found for API version v1beta", "status": "NOT_FOUND"}}),
            "model not served",
        ),
        (
            400,
            json!({"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.", "status": "INVALID_ARGUMENT"}}),
            "failed",
        ),
    ] {
        let fake = server(Some(9), Reply::json(status, &body));
        let text = other(adapter(&fake).complete("hi", 0).expect_err("failed"));
        assert!(
            text.starts_with(&format!("http status {status}: {class}:")),
            "{status}: {text}"
        );
        assert!(
            text.contains(body["error"]["message"].as_str().unwrap()),
            "{text}"
        );
        assert!(
            text.contains(body["error"]["status"].as_str().unwrap()),
            "{text}"
        );
    }
}

#[test]
fn a_key_echoed_back_by_the_far_end_never_reaches_an_error() {
    let fake = server(
        Some(9),
        Reply::json(
            400,
            &json!({"error": {"code": 400, "message": format!("API key {KEY} not valid"), "status": format!("S{KEY}")}}),
        ),
    );
    let text = other(adapter(&fake).complete("hi", 0).expect_err("400"));
    assert!(!text.contains(KEY), "{text}");
    assert!(text.contains("API key [redacted] not valid"), "{text}");
}

#[test]
fn a_redirect_is_refused_and_the_key_never_reaches_the_second_host() {
    let collector = server(Some(9), Reply::json(200, &ok_answer()));
    let api = server(
        Some(9),
        Reply::redirect(
            307,
            &format!("{}{}", collector.base_url, generate_path(DEFAULT_MODEL)),
        ),
    );
    let text = other(adapter(&api).complete("hi", 0).expect_err("redirect"));
    assert!(text.starts_with("http status 307:"), "{text}");
    assert!(collector.all().is_empty(), "the key followed the redirect");
}

#[test]
fn an_origin_off_this_machine_in_plaintext_or_a_path_shaped_model_is_refused() {
    let plaintext = GeminiAdapter::with_config(
        SecretString::new(KEY),
        GeminiConfig {
            base_url: "http://generativelanguage.googleapis.com".into(),
            ..Default::default()
        },
    )
    .err()
    .expect("plaintext");
    assert!(
        format!("{plaintext:#}").contains("in the clear"),
        "{plaintext:#}"
    );

    let smuggled = GeminiAdapter::with_config(
        SecretString::new(KEY),
        GeminiConfig {
            model: "gemini-3.8-flash:generateContent?alt=sse#".into(),
            ..Default::default()
        },
    )
    .err()
    .expect("not a model id");
    assert!(
        format!("{smuggled:#}").contains("not a model id"),
        "{smuggled:#}"
    );
}

#[test]
fn the_manifest_is_a_us_cloud_arch_kept_fifty_five_days_at_business_and_ungoverned() {
    let m = GeminiAdapter::manifest_for(&GeminiConfig::default());
    assert_eq!(m.name, format!("gemini/{DEFAULT_MODEL}"));
    assert_eq!(m.locality, Locality::Cloud);
    assert_eq!(m.jurisdiction, "US");
    assert_eq!(m.retention_days, Some(55));
    assert_eq!(m.clearance.max_scope, Scope::Business);
    assert!(!m.clearance.third_party_allowed);
    assert!(!m.governed);
    assert_eq!(m.determinism, Determinism::NonDeterministic);
    assert_eq!(m.context_ceiling, 1_048_576);
    assert_eq!(m.identity.engine_version, API_VERSION);
    // The rule, which fixes the budget for every bound a call can have.
    assert_eq!(
        m.identity.sampling.get("thinking").map(String::as_str),
        Some("budget:bound/4,min:128,max:1024")
    );
    assert_eq!(
        m.identity.sampling.get("base_url").map(String::as_str),
        Some(DEFAULT_BASE_URL)
    );
    assert!(m.cost_per_1k_tokens_eur.is_some());
    assert!(m.validate().is_ok());
}

#[test]
fn an_unlisted_model_mounts_with_no_price_no_thinking_setting_and_the_fallback_window() {
    let cfg = GeminiConfig {
        model: "gemini-imaginary-9".into(),
        ..Default::default()
    };
    let m = GeminiAdapter::manifest_for(&cfg);
    assert_eq!(m.cost_per_1k_tokens_eur, None);
    assert_eq!(m.context_ceiling, vk_arch_gemini::FALLBACK_CONTEXT);
    assert!(
        GeminiAdapter::generate_request(&cfg, "hi", 0)["generationConfig"]
            .get("thinkingConfig")
            .is_none()
    );
    assert_eq!(
        vk_arch_gemini::cost_list_usd("gemini-imaginary-9", 1, 1),
        None
    );
}

#[test]
fn every_model_in_the_table_is_priced_has_a_window_and_a_thinking_rule() {
    for m in MODELS {
        assert!(vk_arch_gemini::check_model_id(m.id).is_ok(), "{}", m.id);
        assert!(
            m.context_window >= vk_arch_gemini::FALLBACK_CONTEXT,
            "{}",
            m.id
        );
        assert!(m.input_usd_per_mtok > 0.0, "{}", m.id);
        assert!(m.output_usd_per_mtok > m.input_usd_per_mtok, "{}", m.id);
        // A budget in tokens everywhere but 2.5 Flash-Lite, which does not
        // think unless asked; 2.5 Flash alone may be sent a budget of 0.
        let expected = match m.id {
            "gemini-2.5-flash-lite" => Thinking::Default,
            "gemini-2.5-flash" => Thinking::Budget { floor: 0 },
            _ => Thinking::Budget { floor: 128 },
        };
        assert_eq!(m.thinking, expected, "{}", m.id);
    }
    assert!(vk_arch_gemini::model(DEFAULT_MODEL).is_some());
}

/// Review I-1: the kernel calls every arch with `max_tokens` at most 1 024
/// (`budget.min(1024)`), and on Gemini that bound counts the thinking. A
/// fixed 1 024-token thinking budget spent the whole of it, every call ended
/// `MAX_TOKENS` with no text, and the arch mounted but never answered. At the
/// kernel's real bound every listed model must leave most of it for the
/// answer.
#[test]
fn at_the_kernel_s_real_bound_every_model_leaves_three_quarters_for_the_answer() {
    for m in MODELS {
        for (bound, expected) in [(1_024u32, 256u32), (4_096, 1_024), (512, 128)] {
            let cfg = GeminiConfig {
                model: m.id.into(),
                ..Default::default()
            };
            let generation =
                &GeminiAdapter::generate_request(&cfg, "hi", bound)["generationConfig"];
            assert_eq!(generation["maxOutputTokens"], bound, "{} at {bound}", m.id);
            match m.thinking {
                Thinking::Default => assert!(
                    generation.get("thinkingConfig").is_none(),
                    "{} at {bound}",
                    m.id
                ),
                Thinking::Budget { .. } => {
                    let budget = generation["thinkingConfig"]["thinkingBudget"]
                        .as_u64()
                        .unwrap_or_else(|| panic!("{} at {bound}: no budget", m.id));
                    assert_eq!(budget, u64::from(expected), "{} at {bound}", m.id);
                    assert!(
                        budget * 4 <= u64::from(bound),
                        "{} at {bound}: thinking takes more than a quarter",
                        m.id
                    );
                }
            }
            assert!(
                generation.get("thinkingLevel").is_none()
                    && generation["thinkingConfig"].get("thinkingLevel").is_none(),
                "a level bounds nothing in tokens: {generation}"
            );
        }
    }
}

/// The same at the wire, out of a real `complete` at the kernel's bound: the
/// budget sent and the budget recorded are the quarter, and the call answers.
#[test]
fn a_call_at_the_kernel_s_bound_sends_and_records_a_quarter_for_thinking() {
    let model = "gemini-2.5-pro";
    let fake = Fake::start(&[
        (
            &count_path(model),
            Reply::json(200, &json!({"totalTokens": 12})),
        ),
        (
            &generate_path(model),
            Reply::json(
                200,
                &answer(json!([{"text": "an answer"}]), "STOP", 12, 300, 256),
            ),
        ),
    ]);
    let done = adapter_with(
        &fake,
        GeminiConfig {
            model: model.into(),
            ..Default::default()
        },
    )
    .complete("hello", 1_024)
    .expect("answered");
    assert_eq!(done.text, "an answer");
    let generation = &fake.only(&generate_path(model)).body["generationConfig"];
    assert_eq!(generation["maxOutputTokens"], 1_024);
    assert_eq!(generation["thinkingConfig"], json!({"thinkingBudget": 256}));
    assert_eq!(done.details.expect("details")["thinking_budget"], 256);
}

/// A bound no larger than the model's least thinking budget would leave the
/// answer nothing: refused before anything — the count included — is sent.
#[test]
fn a_bound_the_least_thinking_budget_would_consume_is_refused_before_sending() {
    let model = "gemini-2.5-pro";
    let fake = Fake::start(&[]);
    let err = adapter_with(
        &fake,
        GeminiConfig {
            model: model.into(),
            ..Default::default()
        },
    )
    .complete("hello", 128)
    .expect_err("nothing would be left for the answer");
    let text = other(err);
    assert!(text.contains("128") && text.contains("thinking"), "{text}");
    assert!(fake.all().is_empty(), "something was sent");
}

#[test]
fn the_model_the_origin_and_the_thinking_are_part_of_the_arch_id() {
    let base = GeminiAdapter::manifest_for(&GeminiConfig::default());
    for other in [
        GeminiConfig {
            model: "gemini-2.5-flash".into(),
            ..Default::default()
        },
        GeminiConfig {
            base_url: "https://proxy.example".into(),
            ..Default::default()
        },
    ] {
        assert_ne!(
            GeminiAdapter::manifest_for(&other).arch_id(),
            base.arch_id()
        );
    }
}

#[test]
fn a_mount_spec_for_this_arch_carries_no_key() {
    assert!(MountSpec::new(
        "gemini",
        json!({"model": DEFAULT_MODEL, "max_tokens": 4096, "context_ceiling": 0})
    )
    .is_ok());
    assert!(MountSpec::new("gemini", json!({"apiKey": KEY})).is_err());
}

#[test]
fn a_missing_key_file_names_the_remedy() {
    let dir = tempfile::tempdir().expect("tempdir");
    let err = vk_arch_gemini::load_key(
        &vk_arch_gemini::KeySource::File(dir.path().join("nope")),
        vk_arch_gemini::KEYRING_USER,
    )
    .expect_err("missing");
    assert!(
        format!("{err:#}").contains("vk secret set gemini"),
        "{err:#}"
    );
}
