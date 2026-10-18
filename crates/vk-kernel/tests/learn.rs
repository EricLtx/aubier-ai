//! The kernel's side of driver learning (plan Task 9): the answer rule, the one model call on a
//! content-free register, the two ledger rows, the qualification gate.
use serde_json::{json, Value};
use std::path::Path;
use std::time::Duration;
use vk_contracts::arch::ArchManifest;
use vk_contracts::driver::{DriverLearned, DriverManifest, DriverQualified};
use vk_contracts::labels::{Clearance, Scope};
use vk_contracts::ledger::LedgerEvent;
use vk_contracts::plan::Qualification;
use vk_contracts::principal::Principal;
use vk_contracts::syscalls::{Ctx, Kernel, KernelError};
use vk_contracts::testing::KernelTestHooks;
use vk_contracts::{hash_bytes, hash_canonical};
use vk_kernel::learn::{answer_choice, ANSWER_RULE};
use vk_kernel::RealKernel;

/// A kernel over `dir`, retrying the store's single-writer lock for a moment
/// (see `tests/harness.rs` for why a closed store can stay locked briefly
/// when another test's child sits between `fork` and `exec`).
fn open(dir: &Path) -> RealKernel {
    vk_contracts::testing::guard_state_dir(dir);
    let source = || vk_store::keys::KeySource::File(dir.join("m.key"));
    let deadline = std::time::Instant::now() + Duration::from_secs(5);
    loop {
        match RealKernel::open(dir, source(), "n1") {
            Ok(k) => return k,
            Err(e) if std::time::Instant::now() < deadline => {
                assert!(
                    e.to_string().contains("already open"),
                    "opening {}: {e:#}",
                    dir.display()
                );
                std::thread::sleep(Duration::from_millis(20));
            }
            Err(e) => panic!("opening {}: {e:#}", dir.display()),
        }
    }
}

fn machine(now: u64) -> Ctx {
    Ctx {
        principal: Principal::Machine {
            node_id: "n1".into(),
            lease_id: "cli".into(),
        },
        clearance: Clearance {
            max_scope: Scope::Personal,
            third_party_allowed: true,
        },
        partition: "local".into(),
        now_ms: now,
    }
}

fn human(now: u64) -> Ctx {
    Ctx {
        principal: Principal::Human {
            device_id: "phone-1".into(),
        },
        ..machine(now)
    }
}

fn events_of(k: &RealKernel, kind: &str) -> Vec<LedgerEvent> {
    k.ledger()
        .events()
        .iter()
        .filter(|e| e.kind == kind)
        .cloned()
        .collect()
}

fn mock_manifest(name: &str) -> ArchManifest {
    use vk_contracts::arch::*;
    ArchManifest {
        name: name.into(),
        capabilities: [Capability::Generate, Capability::Plan].into(),
        locality: Locality::Local,
        jurisdiction: "FR".into(),
        retention_days: None,
        cost_per_1k_tokens_eur: Some(0.0),
        latency_ms_p50: 1,
        context_ceiling: 4096,
        determinism: Determinism::SeededDeterministic,
        identity: ArchIdentity {
            weights_sha256: format!("sha256:mock-{name}"),
            engine: "mock".into(),
            engine_version: "1".into(),
            backend: "cpu".into(),
            quant: "-".into(),
            kv_cache: "-".into(),
            threads: 1,
            batch: 1,
            sampling: Default::default(),
            seed: Some(1),
        },
        clearance: Clearance {
            max_scope: Scope::Personal,
            third_party_allowed: true,
        },
        governed: true,
    }
}

fn word_com() -> DriverManifest {
    serde_json::from_str(include_str!(
        "../../../contracts/examples/driver_manifest/valid/word_com.json"
    ))
    .unwrap()
}

fn browser() -> DriverManifest {
    serde_json::from_str(include_str!(
        "../../../contracts/examples/driver_manifest/valid/identity-frozen-2026-10-17.json"
    ))
    .unwrap()
}

fn learned(driver_id: &str) -> DriverLearned {
    serde_json::from_value(json!({
        "family": "office-document", "app": "word",
        "catalogue_sha256": format!("sha256:{}", "2".repeat(64)), "ontology_sha256": format!("sha256:{}", "1".repeat(64)),
        "corpus_schema_sha256": format!("sha256:{}", "4".repeat(64)), "invariants_sha256": format!("sha256:{}", "3".repeat(64)),
        "plan_sha256": format!("sha256:{}", "5".repeat(64)), "driver_id": driver_id, "by": "whoever"
    }))
    .unwrap()
}

const TEN: Qualification = Qualification {
    min_passes: 10,
    min_rate: 1.0,
};

fn tie() -> Value {
    json!({"invariant": "body_font", "kind": "font_run", "candidates": [
        {"value": {"name": "Garamond", "size": 11}, "support": 0.7},
        {"value": {"name": "Calibri", "size": 12}, "support": 0.3}]})
}

#[test]
fn the_answer_rule_takes_a_candidate_and_nothing_else() {
    let (garamond, calibri) = (
        json!({"name": "Garamond", "size": 11}),
        json!({"name": "Calibri", "size": 12}),
    );
    let amb = tie();
    assert_eq!(
        answer_choice(
            r#"{"invariant": "body_font", "value": {"name": "Calibri", "size": 12}}"#,
            &amb
        ),
        Some(calibri)
    );
    assert_eq!(
        answer_choice(r#"Je choisis {"size": 11, "name": "Garamond"}."#, &amb),
        Some(garamond),
        "key order is not meaning"
    );
    assert_eq!(
        answer_choice(
            r#"{"invariant": "body_font", "value": {"name": "Comic Sans MS", "size": 40}}"#,
            &amb
        ),
        None
    );
    assert_eq!(
        answer_choice(
            r#"{"invariant": "title_style", "value": {"name": "Calibri", "size": 12}}"#,
            &amb
        ),
        None
    );
    assert_eq!(answer_choice("Garamond", &amb), None);
    assert_eq!(answer_choice("", &amb), None);
    let tokens = json!({"invariant": "footer_text", "kind": "footer", "candidates": [
        {"value": "<redacted:sha256:aa>", "support": 0.5}, {"value": "<redacted:sha256:bb>", "support": 0.5}]});
    assert_eq!(
        answer_choice(&format!("PLAN: ROLE: plan\nGOAL: {tokens}"), &tokens),
        None,
        "an echo names no string"
    );
    assert_eq!(
        answer_choice(
            r#"{"invariant": "footer_text", "value": "<redacted:sha256:bb>"}"#,
            &tokens
        ),
        Some(json!("<redacted:sha256:bb>"))
    );
}

#[test]
fn learn_ambiguity_calls_infer_once_on_a_content_free_register() {
    let d = tempfile::tempdir().unwrap();
    let mut k = open(d.path());
    let arch = k.register_arch(mock_manifest("mock"));
    let chosen = k.learn_ambiguity(&human(1), &arch, &tie()).unwrap();
    assert_eq!(
        chosen,
        Some(json!({"name": "Garamond", "size": 11})),
        "the mock's echo carries the first candidate (L-P20)"
    );
    assert_eq!(events_of(&k, "task.submitted").len(), 1);
    assert!(!events_of(&k, "infer").is_empty());
    let tasks = k.tasks(&human(2)).unwrap();
    assert_eq!(tasks.len(), 1);
    let reg = k.read_register(&human(3), &tasks[0].register).unwrap();
    assert_eq!(
        reg.goal,
        serde_json::to_string(&tie()).unwrap(),
        "the ambiguity and nothing else"
    );
    assert_eq!(
        (reg.constraints.clone(), reg.evidence.len()),
        (vec![ANSWER_RULE.to_string()], 0)
    );
    assert_eq!(reg.label.scope, Scope::Business);
}

#[test]
fn driver_learned_is_a_human_act_and_qualified_needs_a_learned_driver() {
    let d = tempfile::tempdir().unwrap();
    let mut k = open(d.path());
    let id = word_com().driver_id();
    let e = k
        .driver_learned(&machine(1), learned(&id), "word-maison", d.path(), TEN)
        .unwrap_err();
    assert!(matches!(e, KernelError::I1(_)), "{e}");
    let record = d.path().join("qualification.json");
    std::fs::write(&record, b"{}\n").unwrap();
    let q = DriverQualified {
        driver_id: id.clone(),
        passes: 10,
        failures: 0,
        qualification_sha256: hash_bytes(b"{}\n"),
    };
    let e = k
        .driver_qualified(&machine(2), q, &record)
        .unwrap_err()
        .to_string();
    assert!(
        e.contains("was not learned on this node; remedy: vk driver learn"),
        "{e}"
    );
    k.driver_learned(&human(3), learned(&id), "word-maison", d.path(), TEN)
        .unwrap();
    let rows = events_of(&k, "driver.learned");
    assert_eq!(rows.len(), 1);
    let by_the_device = DriverLearned {
        by: "phone-1".into(),
        ..learned(&id)
    };
    assert_eq!(
        rows[0].payload_hash,
        hash_canonical(&by_the_device),
        "by is the principal's, not the caller's"
    );
    let row = k.learned(&id).unwrap().expect("the learned row");
    assert_eq!(
        (
            row.name.as_str(),
            row.qualification.min_passes,
            row.qualified.is_none()
        ),
        ("word-maison", 10, true)
    );
}

#[test]
fn driver_qualified_then_the_com_gate_reads_the_record() {
    let d = tempfile::tempdir().unwrap();
    let mut k = open(d.path());
    let m = word_com();
    let id = m.driver_id();
    let e = k.check_qualified(&m).unwrap_err().to_string();
    assert!(
        e.contains("is not qualified; remedy: vk driver qualify"),
        "{e}"
    );
    k.driver_learned(&human(1), learned(&id), "word-maison", d.path(), TEN)
        .unwrap();
    assert!(k
        .check_qualified(&m)
        .unwrap_err()
        .to_string()
        .contains("is not qualified"));
    let record = d.path().join("qualification.json");
    let write = |text: &str| {
        std::fs::write(&record, text).unwrap();
        hash_bytes(text.as_bytes())
    };
    let q = DriverQualified {
        driver_id: id.clone(),
        passes: 10,
        failures: 0,
        qualification_sha256: write("{\"passes\": 10}\n"),
    };
    k.driver_qualified(&machine(2), q.clone(), &record).unwrap();
    assert_eq!(events_of(&k, "driver.qualified").len(), 1);
    k.check_qualified(&m).unwrap();
    write("{\"passes\": 3}\n");
    let e = k.check_qualified(&m).unwrap_err().to_string();
    assert!(
        e.contains("qualification.json changed since vk driver qualify"),
        "{e}"
    );
    // Under the thresholds: recorded (spec §6), and refused at mount.
    let nine = DriverQualified {
        passes: 9,
        failures: 1,
        qualification_sha256: write("{\"passes\": 9}\n"),
        ..q
    };
    k.driver_qualified(&machine(3), nine, &record).unwrap();
    let e = k.check_qualified(&m).unwrap_err().to_string();
    assert!(e.contains("9 passes of 10"), "{e}");
    k.check_qualified(&browser()).unwrap();
}
