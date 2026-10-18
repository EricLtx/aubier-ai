//! vk driver learn / qualify / plan show over the pipe (plan Task 9): a real kernel, this checkout's
//! vkforge spawned under the governor, the recorded catalogues and the fake application
//! (`ForgeConfig.fixtures`). Skipped, saying why on stderr, where `forge_available` says no (L-P18).
mod common;

use common::*;
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};
use vk_contracts::driver::{DriverLearned, DriverManifest, DriverQualified};
use vk_contracts::plan::{plan_sha256, Ambiguity, DriverPlan};
use vk_contracts::{hash_bytes, hash_canonical};
use vk_ipc::forge::ForgeConfig;

fn learn_params(corpus: &Path, out: &Path) -> Value {
    json!({"family": "office-document", "app": "word", "corpus": corpus, "out": out})
}

fn read_plan(out: &Path) -> (DriverPlan, Vec<u8>) {
    let bytes = std::fs::read(out.join("plan.json")).unwrap();
    (serde_json::from_slice(&bytes).unwrap(), bytes)
}

fn manifest_of(driver: &Path) -> DriverManifest {
    serde_json::from_slice(&std::fs::read(driver.join("manifest.json")).unwrap()).unwrap()
}

async fn learn_refusal(node: &Node, params: Value) -> String {
    let e = node.call_human("driver.learn", params).await.unwrap_err();
    call_error(&e).message.clone()
}

#[tokio::test]
async fn learn_runs_the_five_stages_and_writes_driver_learned() {
    if !forge_available() {
        return;
    }
    let node = Node::start().await;
    let corpus = ooxml_corpus(&node.dir().join("corpus"), 10, 0);
    let out = node.dir().join("word-maison");
    let r = node
        .call_human("driver.learn", learn_params(&corpus, &out))
        .await
        .unwrap();
    for f in [
        "catalogue.json",
        "binding-word.json",
        "invariants.json",
        "plan.json",
        "plan-excluded.json",
        "driver/driver.py",
        "driver/manifest.json",
    ] {
        assert!(out.join(f).is_file(), "{f}");
    }
    assert!(
        !out.join(".vault.json").exists(),
        "the vault is deleted after generation"
    );
    let (plan, bytes) = read_plan(&out);
    plan.validate().unwrap();
    let inv_bytes = std::fs::read(out.join("invariants.json")).unwrap();
    let inv: Value = serde_json::from_slice(&inv_bytes).unwrap();
    assert_eq!(
        plan.invariants_sha256,
        hash_bytes(&inv_bytes),
        "a file hash, as Python wrote it (L-P13)"
    );
    let manifest = manifest_of(&out.join("driver"));
    assert_eq!(r["driver_id"], json!(manifest.driver_id()));
    assert_eq!(r["plan_sha256"], json!(plan_sha256(&bytes)));
    assert_eq!(r["ambiguities"], json!([]));
    let last = node.last_event();
    assert_eq!(last.kind, "driver.learned");
    assert_eq!(
        last.payload_hash,
        hash_canonical(&DriverLearned {
            family: "office-document".into(),
            app: "word".into(),
            catalogue_sha256: plan.catalogue_sha256.clone(),
            ontology_sha256: plan.ontology_sha256.clone(),
            corpus_schema_sha256: inv["corpus"]["schema_sha256"].as_str().unwrap().into(),
            invariants_sha256: plan.invariants_sha256.clone(),
            plan_sha256: plan_sha256(&bytes),
            driver_id: manifest.driver_id(),
            by: "laptop".into(),
        })
    );
    assert!(
        node.events("infer").is_empty() && node.events("task.submitted").is_empty(),
        "zero tokens"
    );
    node.shutdown().await;
}

#[tokio::test]
async fn learn_is_a_human_act_and_refuses_an_unknown_family_or_a_used_out() {
    if !forge_available() {
        return;
    }
    let node = Node::start().await;
    let corpus = ooxml_corpus(&node.dir().join("corpus"), 10, 0);
    let out = node.dir().join("word-maison");
    let e = node
        .call("driver.learn", learn_params(&corpus, &out))
        .await
        .unwrap_err();
    assert!(
        call_error(&e).message.starts_with("I1: learning a driver"),
        "{e}"
    );
    let m = learn_refusal(
        &node,
        json!({"family": "spreadsheet", "app": "word", "corpus": corpus, "out": out}),
    )
    .await;
    assert!(
        m.contains("family spreadsheet is not known; remedy: --family office-document"),
        "{m}"
    );
    let m = learn_refusal(
        &node,
        json!({"family": "office-document", "app": "excel", "corpus": corpus, "out": out}),
    )
    .await;
    assert!(
        m.contains("app excel is not known; remedy: --app word or --app powerpoint"),
        "{m}"
    );
    let m = learn_refusal(
        &node,
        json!({"family": "office-document", "app": "word", "corpus": "corpus", "out": out}),
    )
    .await;
    assert!(m.contains("is not absolute"), "{m}");
    let m = learn_refusal(
        &node,
        learn_params(&corpus, &node.dir().join("Word_Maison")),
    )
    .await;
    assert!(m.contains("driver name"), "{m}");
    std::fs::create_dir_all(&out).unwrap();
    std::fs::write(out.join("x"), "x").unwrap();
    let m = learn_refusal(&node, learn_params(&corpus, &out)).await;
    assert!(m.contains("is not empty; remedy: a new directory"), "{m}");
    assert!(node.events("driver.learned").is_empty());
    assert!(
        !node.dir().join("forge").exists(),
        "every refusal came before a stage was spawned"
    );
    node.shutdown().await;
}

#[tokio::test]
async fn qualify_writes_driver_qualified_and_the_mount_gate_opens() {
    if !forge_available() {
        return;
    }
    let node = Node::start().await;
    let corpus = ooxml_corpus(&node.dir().join("corpus"), 10, 0);
    let out = node.dir().join("word-maison");
    node.call_human("driver.learn", learn_params(&corpus, &out))
        .await
        .unwrap();
    let driver = out.join("driver");
    let manifest = manifest_of(&driver);
    let mount = json!({"manifest": manifest, "root": driver});
    // Learned, not qualified: refused before anything is spawned (L-P4; no override, L-P16).
    let e = node
        .call_human("driver.mount", mount.clone())
        .await
        .unwrap_err();
    assert!(
        call_error(&e)
            .message
            .contains("is not qualified; remedy: vk driver qualify"),
        "{e}"
    );
    let q = node
        .call(
            "driver.qualify",
            json!({"driver": driver, "corpus": corpus}),
        )
        .await
        .unwrap();
    assert_eq!(
        (
            q["passes"].as_u64(),
            q["failures"].as_u64(),
            q["fake"].as_bool()
        ),
        (Some(10), Some(0), Some(true))
    );
    let record = PathBuf::from(q["record"].as_str().unwrap());
    let bytes = std::fs::read(&record).unwrap();
    let rec: Value = serde_json::from_slice(&bytes).unwrap();
    assert_eq!(
        rec["plan_sha256"],
        json!(plan_sha256(&std::fs::read(out.join("plan.json")).unwrap())),
        "one plan_sha256, in Python and in Rust (L-P13)"
    );
    let last = node.last_event();
    assert_eq!(last.kind, "driver.qualified");
    assert_eq!(
        last.payload_hash,
        hash_canonical(&DriverQualified {
            driver_id: manifest.driver_id(),
            passes: 10,
            failures: 0,
            qualification_sha256: hash_bytes(&bytes),
        })
    );
    // The gate opens; the kind is read from the row's manifest: driver.mounted carries none (L-P11).
    node.call_human("driver.mount", mount.clone())
        .await
        .unwrap();
    assert_eq!(node.last_event().kind, "driver.mounted");
    let row = node
        .call("driver.show", json!({"name": "word-maison"}))
        .await
        .unwrap();
    assert_eq!(row["manifest"]["kind"], json!("com"));
    assert_eq!(row["manifest"]["applications"], json!(["WINWORD.EXE"]));
    node.call_human("driver.unmount", json!({"name": "word-maison"}))
        .await
        .unwrap();
    // A record edited after qualify is not the one the kernel hashed.
    let edited = String::from_utf8(bytes)
        .unwrap()
        .replacen("\"passes\": 10", "\"passes\": 3", 1);
    std::fs::write(&record, edited).unwrap();
    let e = node.call_human("driver.mount", mount).await.unwrap_err();
    assert!(
        call_error(&e)
            .message
            .contains("qualification.json changed since vk driver qualify"),
        "{e}"
    );
    node.shutdown().await;
}

#[tokio::test]
async fn a_stage_that_fails_is_one_refusal_line_in_the_kernel_voice() {
    if !forge_available() {
        return;
    }
    let node = Node::start().await;
    let corpus = ooxml_corpus(&node.dir().join("corpus"), 2, 0);
    let out = node.dir().join("word-maison");
    let e = node
        .call_human("driver.learn", learn_params(&corpus, &out))
        .await
        .unwrap_err();
    assert_eq!(
        call_error(&e).message,
        "vkforge mine: corpus has 2 files; remedy: more documents of one kind",
        "the stage's own line, verbatim (L-P15)"
    );
    let mut left: Vec<String> = std::fs::read_dir(&out)
        .unwrap()
        .map(|e| e.unwrap().file_name().to_string_lossy().into_owned())
        .collect();
    left.sort();
    assert_eq!(left, ["binding-word.json", "catalogue.json"]);
    assert!(node.events("driver.learned").is_empty());
    node.shutdown().await;
}

#[tokio::test]
async fn an_ambiguity_with_a_mock_arch_is_resolved_and_traced() {
    if !forge_available() {
        return;
    }
    let node = Node::start().await;
    let mounted = node
        .call("arch.mount_mock", json!({"name": "mock"}))
        .await
        .unwrap();
    let arch = mounted["arch_id"].as_str().unwrap().to_string();
    let corpus = ooxml_corpus(&node.dir().join("corpus"), 7, 3); // body fonts at 0.7 and 0.3: one ambiguity
    let out = node.dir().join("word-maison");
    let mut params = learn_params(&corpus, &out);
    params["arch"] = json!(arch);
    let r = node.call_human("driver.learn", params).await.unwrap();
    let garamond = json!({"name": "Garamond", "size": 11});
    let traced = Ambiguity {
        invariant: "body_font".into(),
        resolved_by: format!("arch:{arch}"),
        value: Some(garamond.clone()),
    };
    let (plan, _) = read_plan(&out);
    assert_eq!(plan.ambiguities, vec![traced.clone()]);
    let fonts: Vec<_> = plan.actions[0]
        .steps
        .iter()
        .filter(|s| s.verb == "set_font_run")
        .collect();
    assert_eq!(fonts.len(), 1);
    assert_eq!(
        fonts[0].args["font"], garamond,
        "the first candidate, which the mock's echo carries (L-P20)"
    );
    assert_eq!(
        r["ambiguities"],
        serde_json::to_value(vec![traced]).unwrap()
    );
    let resolver: Value =
        serde_json::from_slice(&std::fs::read(out.join("resolver.json")).unwrap()).unwrap();
    assert_eq!(
        resolver,
        json!({"body_font": {"value": garamond, "resolved_by": format!("arch:{arch}")}})
    );
    assert_eq!(
        node.events("task.submitted").len(),
        1,
        "one model call, for the one ambiguity"
    );
    node.shutdown().await;
}

/// The stand-in vkforge of the deadline test (L-P30): it writes its working directory, its own id and
/// the id of a child it starts, then sleeps a minute — longer than any deadline the test gives it.
const SLEEPER: &str = r#"import os, subprocess, sys, time
from pathlib import Path
child = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(60)"])
Path(__file__).resolve().parents[1].joinpath("stage.txt").write_text(
    f"{os.getcwd()}\n{os.getpid()}\n{child.pid}\n", encoding="utf-8")
time.sleep(60)
"#;

/// Is the Windows process `pid` running? `tasklist` filtered on the id, in CSV, as
/// `crates/vk-driver/tests/fake_driver.rs` asks it (the forge tests run where comtypes imports: Windows).
fn process_alive(pid: u32) -> bool {
    std::process::Command::new("tasklist")
        .args(["/FI", &format!("PID eq {pid}"), "/NH", "/FO", "CSV"])
        .stdin(std::process::Stdio::null())
        .output()
        .map(|o| String::from_utf8_lossy(&o.stdout).contains(&format!("\"{pid}\"")))
        .unwrap_or(false)
}

/// L-P2, L-P30: a stage is a governed one-shot process — started in the vkforge root, refused by the
/// kernel within its deadline plus five seconds, and killed with its whole tree (the Job governor).
/// The root is a stand-in, given the way `vkd --vkforge-root` gives one (`ForgeConfig::at`).
#[tokio::test]
async fn vk_forge_a_stage_past_its_deadline_is_killed_in_its_root() {
    if !forge_available() {
        return;
    }
    let roots = tempfile::tempdir().unwrap();
    let root = roots.path().join("drivers");
    std::fs::create_dir_all(root.join("vkforge")).unwrap();
    std::fs::write(root.join("vkforge").join("__main__.py"), SLEEPER).unwrap();
    let deadline = Duration::from_secs(5);
    let node = Node::start_with_forge(ForgeConfig {
        python: forge_python(),
        stage_deadline: deadline,
        ..ForgeConfig::at(root.clone())
    })
    .await;
    let corpus = node.dir().join("corpus"); // never read: the first stage, inventory, is the sleeper
    std::fs::create_dir_all(&corpus).unwrap();
    let started = Instant::now();
    let e = node
        .call_human(
            "driver.learn",
            learn_params(&corpus, &node.dir().join("word-maison")),
        )
        .await
        .unwrap_err();
    let waited = started.elapsed();
    assert!(
        waited < deadline + Duration::from_secs(5),
        "the kernel answered after {waited:?}"
    );
    let message = call_error(&e).message.clone();
    assert!(
        message.contains("vkforge inventory ran past its deadline of 5 s"),
        "{message}"
    );
    let stage =
        std::fs::read_to_string(root.join("stage.txt")).expect("the stage ran and wrote stage.txt");
    let lines: Vec<&str> = stage.lines().collect();
    assert_eq!(
        std::fs::canonicalize(lines[0]).unwrap(),
        std::fs::canonicalize(&root).unwrap(),
        "current_dir is the vkforge root (L-P2)"
    );
    for pid in &lines[1..3] {
        let pid: u32 = pid.parse().unwrap();
        let gone_by = Instant::now() + Duration::from_secs(5);
        while process_alive(pid) {
            assert!(
                Instant::now() < gone_by,
                "python {pid} outlived its stage: the Job holds the tree"
            );
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    }
    assert!(node.events("driver.learned").is_empty());
    node.shutdown().await;
}
