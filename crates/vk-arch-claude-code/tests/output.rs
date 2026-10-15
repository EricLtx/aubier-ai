//! What the adapter is allowed to read out of `claude -p --output-format json`.
//!
//! The canned objects here are the shape spike 2a recorded from Claude Code
//! 2.1.281 under a claude.ai subscription: `usage.input_tokens` counts the
//! *uncached* prompt tokens alone, the whole prompt is that plus the two cache
//! figures, and `total_cost_usd` is the list-price equivalent of a call the
//! subscription did not bill.
use vk_arch_claude_code::output;

/// One successful call, as the CLI prints it.
fn success() -> String {
    r#"{
      "type": "result",
      "subtype": "success",
      "is_error": false,
      "duration_ms": 9861,
      "duration_api_ms": 6702,
      "ttft_ms": 1204,
      "num_turns": 1,
      "result": "ok",
      "session_id": "0f1b2c3d-4e5f-6071-8293-a4b5c6d7e8f9",
      "total_cost_usd": 0.0148193,
      "usage": {
        "input_tokens": 12,
        "cache_creation_input_tokens": 3421,
        "cache_read_input_tokens": 11002,
        "output_tokens": 517,
        "service_tier": "standard"
      },
      "modelUsage": {
        "claude-sonnet-5": {
          "inputTokens": 12,
          "outputTokens": 517,
          "cacheReadInputTokens": 11002,
          "cacheCreationInputTokens": 3421,
          "costUSD": 0.0148193,
          "contextWindow": 200000,
          "costBasis": "list"
        }
      },
      "permission_denials": []
    }"#
    .to_string()
}

#[test]
fn parse_reads_every_pinned_field() {
    let j = output::parse(&success()).expect("a successful result parses");
    assert_eq!(j.result, "ok");
    assert!(!j.is_error);
    assert_eq!(j.session_id, "0f1b2c3d-4e5f-6071-8293-a4b5c6d7e8f9");
    assert_eq!(j.input_tokens, 12);
    assert_eq!(j.cache_creation_input_tokens, 3421);
    assert_eq!(j.cache_read_input_tokens, 11002);
    assert_eq!(j.output_tokens, 517);
    assert_eq!(j.duration_ms, 9861);
    assert_eq!(j.duration_api_ms, 6702);
    assert!((j.total_cost_usd - 0.0148193).abs() < 1e-9, "{j:?}");
}

/// `usage.input_tokens` is the uncached remainder, never the prompt. Reading it
/// as the prompt would under-report every cached call by an order of magnitude.
#[test]
fn tokens_in_is_the_whole_prompt_not_the_uncached_remainder() {
    let j = output::parse(&success()).expect("parse");
    assert_eq!(j.tokens_in(), 12 + 3421 + 11002);
    assert_ne!(j.tokens_in(), j.input_tokens);
}

#[test]
fn a_failed_run_is_an_error_carrying_what_claude_said() {
    let failed = success()
        .replace("\"is_error\": false", "\"is_error\": true")
        .replace(
            "\"result\": \"ok\"",
            "\"result\": \"Credit balance is too low\"",
        );
    let err = output::parse(&failed).expect_err("is_error must not be handed back as a completion");
    let msg = err.to_string();
    assert!(
        msg.contains("Credit balance is too low"),
        "the refusal must carry what claude said: {msg}"
    );

    // ...and the fields are still readable, for a caller that wants to log them.
    let raw = output::parse_fields(&failed).expect("a failed object still parses");
    assert!(raw.is_error);
    assert!(raw.failed());
    assert_eq!(raw.result, "Credit balance is too low");
}

/// Spike 2a: on an API error `subtype` stays `"success"` and `is_error` can be
/// absent — `api_error_status` is the field that says the call did not happen.
#[test]
fn an_api_error_status_is_a_failure_even_when_is_error_is_absent() {
    let api_error = r#"{
      "type": "result", "subtype": "success",
      "duration_ms": 812, "duration_api_ms": 640, "num_turns": 1,
      "result": "API Error: 529 overloaded_error",
      "api_error_status": 529,
      "terminal_reason": "api_error",
      "session_id": "s-1", "total_cost_usd": 0.0,
      "usage": {"input_tokens": 0, "output_tokens": 0}
    }"#;
    let err = output::parse(api_error).expect_err("an API error is not a completion");
    let msg = err.to_string();
    assert!(msg.contains("529"), "{msg}");
    assert!(msg.contains("overloaded_error"), "{msg}");
}

/// `--max-turns` is enforced by the CLI. The shape is spike 2a §5.3's, measured:
/// exit 1, `subtype: "error_max_turns"`, `terminal_reason: "max_turns"`,
/// `stop_reason: "tool_use"`, `is_error: true`, `num_turns: 2` — note that the
/// reason is `max_turns`, *not* the subtype, which is the easy thing to assume.
#[test]
fn a_turn_limit_refusal_names_its_reason() {
    let hit_limit = r#"{
      "type": "result", "subtype": "error_max_turns", "is_error": true,
      "duration_ms": 4100, "duration_api_ms": 3900, "num_turns": 2,
      "result": "", "terminal_reason": "max_turns", "stop_reason": "tool_use",
      "api_error_status": null,
      "session_id": "s-2", "total_cost_usd": 0.01,
      "usage": {"input_tokens": 9, "output_tokens": 40}
    }"#;
    let msg = output::parse(hit_limit)
        .expect_err("a turn limit is a refusal")
        .to_string();
    assert!(msg.contains("max_turns"), "{msg}");

    // `is_error` is what gates it, and the reason is readable beside it.
    let raw = output::parse_fields(hit_limit).expect("the object still parses");
    assert!(raw.is_error);
    assert_eq!(raw.terminal_reason.as_deref(), Some("max_turns"));
    assert_eq!(
        raw.api_error_status, None,
        "a turn limit is not an API error"
    );
}

#[test]
fn malformed_output_is_an_error_and_not_an_empty_completion() {
    for bad in [
        "",
        "not json at all",
        "{\"type\":\"result\"",
        // Valid JSON, but not the object this adapter knows how to read.
        "{\"type\":\"result\",\"subtype\":\"success\"}",
        "[]",
    ] {
        match output::parse(bad) {
            Ok(read) => panic!("{bad:?} must be refused, not read as {read:?}"),
            Err(why) => assert!(
                !why.to_string().is_empty(),
                "a refusal has to say something: {bad:?}"
            ),
        }
    }
}

// ------------------------------------------------- the plan's refusals (2026-10-15)

fn failed(result: &str, api_error_status: Option<&str>) -> output::ClaudeJson {
    let status = api_error_status.map_or("null".to_string(), |s| format!("{s:?}"));
    output::parse_fields(&format!(
        r#"{{"type": "result", "subtype": "success", "is_error": true,
             "result": {result:?}, "session_id": "s", "api_error_status": {status}}}"#
    ))
    .expect("a failed run still parses")
}

#[test]
fn a_usage_limit_is_a_rate_limit_until_the_reset_the_cli_printed() {
    use vk_kernel::arch::AdapterError;
    let now = 1_760_000_000;
    let limited = failed("Claude AI usage limit reached|1760003600", None);
    assert!(
        matches!(
            output::provider_refusal(&limited, now),
            Some(AdapterError::RateLimited { retry_after: Some(d) }) if d.as_secs() == 3_600
        ),
        "the reset time is the wait"
    );
    // No reset printed, or one already past: the wait is unknown, not zero.
    let unknown = failed("You've hit your limit", None);
    assert!(matches!(
        output::provider_refusal(&unknown, now),
        Some(AdapterError::RateLimited { retry_after: None })
    ));
    let past = failed("Claude AI usage limit reached|1700000000", None);
    assert!(matches!(
        output::provider_refusal(&past, now),
        Some(AdapterError::RateLimited { retry_after: None })
    ));
    let status = failed("API Error: 429", Some("429"));
    assert!(matches!(
        output::provider_refusal(&status, now),
        Some(AdapterError::RateLimited { .. })
    ));
}

#[test]
fn a_spent_balance_is_exhausted_a_missing_model_retired_and_the_rest_unchanged() {
    use vk_kernel::arch::AdapterError;
    let spent = failed("Credit balance is too low", None);
    assert!(matches!(
        output::provider_refusal(&spent, 0),
        Some(AdapterError::Exhausted { .. })
    ));
    let gone = failed("model: claude-2.1", Some("404"));
    assert!(matches!(
        output::provider_refusal(&gone, 0),
        Some(AdapterError::Retired { detail }) if detail.contains("claude-2.1")
    ));
    // Not about the plan: an overloaded provider, a run out of turns.
    assert!(output::provider_refusal(&failed("Overloaded", Some("529")), 0).is_none());
    assert!(output::provider_refusal(&failed("max turns", None), 0).is_none());
    // And a run that succeeded is not a refusal of any kind.
    let ok = output::parse(&success()).unwrap();
    assert!(output::provider_refusal(&ok, 0).is_none());
}
