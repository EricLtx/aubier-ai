//! The driver registry and `driver.act` in three phases (pilot spec §4.3–4.4,
//! §5), against a real store and without a driver process: the daemon owns the
//! process and hands the kernel the outcome, so these tests hand it one too.
//! What is pinned: mounting is a human act and idempotent; `/drivers` lists and
//! shows; the seven refusals of §4.3 in order, each by its quoted text; a
//! settle attaches evidence under its own name at the register's label plus
//! the third-party origin, feeds the register when the action says so, and
//! logs hashes only; a host failure degrades the driver; one act per driver at
//! a time; a relaunched step acts again on a new token; recipe-shaped tasks
//! (constraints and evidence into the register, the step's instruction as the
//! prompt, a judge that attaches `verdict.md`, a draft that returned nothing
//! fails rather than waits); the table survives a restart; and `RecipeStep`
//! is serde-identical to `StepKind`.
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;
use vk_contracts::arch::ArchManifest;
use vk_contracts::driver::{
    DriverActed, DriverAction, DriverKind, DriverManifest, DriverMounted, DriverProgram,
    DriverUnmounted,
};
use vk_contracts::hash_canonical;
use vk_contracts::labels::{Clearance, DataClass, Label, Origin, Scope};
use vk_contracts::ledger::LedgerEvent;
use vk_contracts::principal::{Approval, ApprovalKind, HumanKey, Principal, SoftwareHumanKey};
use vk_contracts::recipe::TaskRecipe;
use vk_contracts::syscalls::{Ctx, Kernel, KernelError};
use vk_contracts::testing::KernelTestHooks;
use vk_driver::{ActOutcome, EvidenceFile};
use vk_harness::launch::{ExitReason, HarnessRun};
use vk_kernel::arch::{AdapterError, ArchAdapter, Completion, MountSpec};
use vk_kernel::drivers::{DriverActPlan, DriverRow, DriverState};
use vk_kernel::ns::{resolve, Entry};
use vk_kernel::tasks::{
    HarnessLaunch, StepKind, StepStatus, Task, TaskStatus, HARNESS_MAX_FILE_BYTES,
    HARNESS_MAX_RUN_BYTES, HARNESS_PROMPT,
};
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

/// A manifest shaped like the pilot's: two actions, `echo {case}` returning
/// one evidence file and feeding the register, `look` returning nothing.
fn maskai_like() -> DriverManifest {
    DriverManifest {
        name: "maskai-1".into(),
        kind: DriverKind::Browser,
        program: DriverProgram {
            runtime: "python-playwright".into(),
            command: vec!["python".into(), "-I".into(), "driver.py".into()],
            files_sha256: format!("sha256:{}", "0".repeat(64)),
            browser_channel: None,
            headless: true,
        },
        hosts: vec!["app.maskai.fr".into()],
        actions: vec![
            DriverAction {
                name: "echo".into(),
                reversible: true,
                args: serde_json::json!({
                    "type": "object",
                    "properties": { "case": { "type": "string" } },
                    "required": ["case"],
                    "additionalProperties": false
                }),
                evidence: vec!["note.txt".into()],
                to_register: true,
            },
            DriverAction {
                name: "look".into(),
                reversible: true,
                args: serde_json::json!({ "type": "object", "additionalProperties": false }),
                evidence: vec![],
                to_register: false,
            },
        ],
        min_interval_ms: 0,
        max_action_ms: 60_000,
        clearance: Clearance {
            max_scope: Scope::Business,
            third_party_allowed: true,
        },
        governed: false,
    }
}

/// Where the manifest's relative `program.command` resolves (Ruling R6): the
/// kernel stores it for the daemon and resolves nothing itself.
fn root() -> PathBuf {
    PathBuf::from("drivers/maskai-recette")
}

fn mount(k: &mut RealKernel) -> DriverRow {
    k.mount_driver(&human(1), maskai_like(), root(), true)
        .unwrap()
}

fn harness_step(drivers: &[&str], max_actions: Option<u32>) -> StepKind {
    StepKind::Harness {
        name: "claude-code".into(),
        drivers: drivers.iter().map(|d| d.to_string()).collect(),
        instruction: Some("do".into()),
        max_actions,
        timeout_s: None,
    }
}

/// The recipe-shaped task the brief names: a goal, a kind, one constraint and
/// one line of evidence, and the caller's steps.
fn recette_task(k: &mut RealKernel, steps: Vec<StepKind>) -> Task {
    k.create_task(
        &machine(1),
        "recette",
        "report",
        Label::bottom(),
        &["fictional data only".into()],
        &["cases: N0-1".into()],
        steps,
    )
    .unwrap()
}

/// A task with one harness step over `maskai-1` and the usual tail.
fn one_level(k: &mut RealKernel) -> Task {
    recette_task(
        k,
        vec![
            harness_step(&["maskai-1"], Some(2)),
            StepKind::Approve,
            StepKind::Release {
                to_dir: "runs".into(),
            },
        ],
    )
}

fn launch(k: &mut RealKernel, task_id: &str) -> HarnessLaunch {
    k.harness_launch(&machine(2), task_id, "claude-code", 60_000)
        .unwrap()
}

fn echo_args() -> serde_json::Value {
    serde_json::json!({ "case": "N0-1" })
}

/// What a successful `echo` comes back as from the host: one evidence file
/// and a result whose `text` is a journal line.
fn echo_outcome(text: &str) -> ActOutcome {
    ActOutcome {
        ok: true,
        reason: None,
        detail: None,
        result: serde_json::json!({ "text": text }),
        evidence: vec![EvidenceFile {
            name: "note.txt".into(),
            mime: "text/plain".into(),
            bytes: b"hello from the site".to_vec(),
        }],
        duration_ms: 7,
        hosts_seen: vec!["1.2.3.4:443".into()],
    }
}

/// A harness run record as if the process had exited with `reason`.
fn run_ending(reason: ExitReason) -> HarnessRun {
    HarnessRun {
        exit_code: match &reason {
            ExitReason::Exited(c) => Some(*c),
            _ => None,
        },
        exit_reason: reason,
        stdout_json: String::new(),
        outcome: None,
        connections: vec![],
        samples: 0,
        duration_ms: 1,
        governed: false,
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

/// An arch that answers every prompt with nothing at all.
struct Silent(ArchManifest);

impl ArchAdapter for Silent {
    fn manifest(&self) -> &ArchManifest {
        &self.0
    }
    fn context_budget(&self) -> u32 {
        self.0.context_ceiling
    }
    fn count_tokens(&self, text: &str) -> u32 {
        (text.len() / 4) as u32 + 1
    }
    fn complete(&self, _prompt: &str, _max_tokens: u32) -> Result<Completion, AdapterError> {
        Ok(Completion::text(""))
    }
}

/// The human approval the task waits for, answered with `key` through the
/// kernel's own ceremony: a minted challenge, signed, spent.
fn approve_waiting(k: &mut RealKernel, key: &SoftwareHumanKey, task_id: &str, now: u64) {
    let ch = k.mint_approval_challenge(&machine(now), task_id).unwrap();
    k.approve(
        &human(now),
        Approval {
            subject_hash: ch.action_digest.clone(),
            kind: ApprovalKind::Human,
            approver: Principal::Human {
                device_id: key.device_id(),
            },
            challenge: Some(ch.clone()),
            signature_hex: Some(hex::encode(key.sign(&ch.digest()))),
        },
    )
    .unwrap();
}

#[test]
fn mount_is_a_human_act_and_is_idempotent() {
    let d = tempfile::tempdir().unwrap();
    let mut k = open(d.path());

    // I1: a machine principal cannot mount.
    let err = k
        .mount_driver(&machine(1), maskai_like(), root(), true)
        .unwrap_err();
    assert!(matches!(err, KernelError::I1(_)), "{err}");
    assert!(k.drivers().unwrap().is_empty());
    assert!(events_of(&k, "driver.mounted").is_empty());

    // A human mounts: the row, the three directories, one event.
    let row = mount(&mut k);
    assert_eq!(row.driver_id, maskai_like().driver_id());
    assert_eq!(row.state, DriverState::Ready);
    assert_eq!(row.root, root());
    assert_eq!(row.mounted_ms, 1);
    assert_eq!(row.last_act_ms, 0);
    assert!(
        row.manifest.governed,
        "governed is what the host observed, not what the file said"
    );
    let dirs = k.driver_dirs("maskai-1");
    for dir in [&dirs.profile, &dirs.work, &dirs.evidence] {
        assert!(dir.is_dir(), "{}", dir.display());
        assert!(dir.starts_with(d.path().join("drivers").join("maskai-1")));
    }
    let mounted = events_of(&k, "driver.mounted");
    assert_eq!(mounted.len(), 1);
    assert_eq!(
        mounted[0].payload_hash,
        hash_canonical(&DriverMounted {
            driver_id: row.driver_id.clone(),
            name: "maskai-1".into(),
            manifest_hash: hash_canonical(&row.manifest),
            profile_dir_hash: vk_contracts::hash_bytes(dirs.profile.to_string_lossy().as_bytes()),
            governed: true,
            by: "phone-1".into(),
        }),
        "the mounted event names the identity and who mounted it"
    );

    // The same driver again: the same row, no second event (Ruling 13).
    let again = k
        .mount_driver(&human(2), maskai_like(), root(), true)
        .unwrap();
    assert_eq!(again, row);
    assert_eq!(events_of(&k, "driver.mounted").len(), 1);

    // A different manifest under the same name is refused with the remedy.
    let mut other = maskai_like();
    other.hosts.push("other.example".into());
    let err = k.mount_driver(&human(3), other, root(), true).unwrap_err();
    assert!(matches!(err, KernelError::Gate(_)), "{err}");
    assert!(err.to_string().contains("umount"), "{err}");
    assert_eq!(k.drivers().unwrap().len(), 1);
    assert_eq!(k.driver("maskai-1").unwrap().unwrap(), row);
    assert!(k.ledger().verify_chain());
}

#[test]
fn ls_drivers_and_show_one() {
    let d = tempfile::tempdir().unwrap();
    let mut k = open(d.path());
    let row = mount(&mut k);

    match resolve(&k, &machine(0), "/").unwrap() {
        Entry::Dir { entries } => assert!(entries.contains(&"drivers".to_string()), "{entries:?}"),
        other => panic!("expected the root listing, got {other:?}"),
    }
    match resolve(&k, &machine(0), "/drivers").unwrap() {
        Entry::Dir { entries } => assert_eq!(entries, vec!["maskai-1".to_string()]),
        other => panic!("expected a listing, got {other:?}"),
    }
    match resolve(&k, &machine(0), "/drivers/maskai-1").unwrap() {
        Entry::Driver(shown) => {
            assert_eq!(shown.state, DriverState::Ready);
            assert_eq!(*shown, row);
        }
        other => panic!("expected a driver, got {other:?}"),
    }
    assert!(matches!(
        resolve(&k, &machine(0), "/drivers/ghost"),
        Err(KernelError::NotFound(_))
    ));
    // The human devices are untouched by the new listing.
    assert!(matches!(
        resolve(&k, &machine(0), "/devices/ghost"),
        Err(KernelError::NotFound(_))
    ));
}

#[test]
fn refusal_1_unknown_token_and_step_not_running() {
    let d = tempfile::tempdir().unwrap();
    let mut k = open(d.path());
    mount(&mut k);
    let t = one_level(&mut k);

    let err = k
        .driver_act_begin("not-a-token", 5, "maskai-1", "echo", &echo_args())
        .unwrap_err();
    assert!(matches!(err, KernelError::I1(_)), "{err}");

    let l = launch(&mut k, &t.id);
    // The row says the step is not running (as after a settle the daemon
    // raced): the token still resolves, and the act is still refused.
    let mut row: Task = k.store().db.get_json("tasks", &t.id).unwrap().unwrap();
    row.steps[0].status = StepStatus::Pending;
    k.store().db.put_json("tasks", &t.id, &row).unwrap();
    let err = k
        .driver_act_begin(&l.token, 6, "maskai-1", "echo", &echo_args())
        .unwrap_err();
    assert!(matches!(err, KernelError::Gate(_)), "{err}");
    assert!(
        err.to_string().contains("the harness step is not running"),
        "{err}"
    );
    // Nothing was claimed by the refusal.
    row.steps[0].status = StepStatus::Running;
    k.store().db.put_json("tasks", &t.id, &row).unwrap();
    k.driver_act_begin(&l.token, 7, "maskai-1", "echo", &echo_args())
        .unwrap();
}

#[test]
fn refusal_2_undeclared_driver_missing_driver_degraded_driver() {
    let d = tempfile::tempdir().unwrap();
    let mut k = open(d.path());
    mount(&mut k);

    // A step that declared no driver.
    let silent = recette_task(&mut k, vec![harness_step(&[], None)]);
    let l = launch(&mut k, &silent.id);
    let err = k
        .driver_act_begin(&l.token, 5, "maskai-1", "echo", &echo_args())
        .unwrap_err();
    assert!(matches!(err, KernelError::Gate(_)), "{err}");
    assert_eq!(
        err.to_string(),
        format!(
            "gate verdict missing or failed: step 0 of task {} did not declare driver maskai-1",
            silent.id
        )
    );

    // A declared driver that is not mounted, then one that is degraded.
    let t = recette_task(&mut k, vec![harness_step(&["ghost", "maskai-1"], None)]);
    let l = launch(&mut k, &t.id);
    let err = k
        .driver_act_begin(&l.token, 6, "ghost", "echo", &echo_args())
        .unwrap_err();
    assert!(matches!(err, KernelError::NotFound(_)), "{err}");
    assert!(err.to_string().contains("driver ghost"), "{err}");

    k.degrade_driver("maskai-1", "timeout").unwrap();
    let err = k
        .driver_act_begin(&l.token, 7, "maskai-1", "echo", &echo_args())
        .unwrap_err();
    assert!(matches!(err, KernelError::Gate(_)), "{err}");
    assert!(
        err.to_string()
            .contains("driver maskai-1 is degraded: timeout; remount it"),
        "{err}"
    );
    // The row stays, so `vk driver show` can explain.
    assert_eq!(
        k.driver("maskai-1").unwrap().unwrap().state,
        DriverState::Degraded {
            reason: "timeout".into()
        }
    );
}

#[test]
fn refusal_3_unknown_action_and_bad_args() {
    let d = tempfile::tempdir().unwrap();
    let mut k = open(d.path());
    mount(&mut k);
    let t = one_level(&mut k);
    let l = launch(&mut k, &t.id);

    let err = k
        .driver_act_begin(&l.token, 5, "maskai-1", "fly", &echo_args())
        .unwrap_err();
    assert!(matches!(err, KernelError::Gate(_)), "{err}");
    assert!(
        err.to_string()
            .contains("driver maskai-1 has no action fly"),
        "{err}"
    );

    let err = k
        .driver_act_begin(
            &l.token,
            6,
            "maskai-1",
            "echo",
            &serde_json::json!({ "case": 3 }),
        )
        .unwrap_err();
    assert!(matches!(err, KernelError::Gate(_)), "{err}");
    assert!(err.to_string().contains("args for echo"), "{err}");

    // Neither refusal counted as an action or left the driver busy.
    k.driver_act_begin(&l.token, 7, "maskai-1", "echo", &echo_args())
        .unwrap();
}

#[test]
fn refusal_4_cadence() {
    let d = tempfile::tempdir().unwrap();
    let mut k = open(d.path());
    let mut m = maskai_like();
    m.min_interval_ms = 2000;
    k.mount_driver(&human(1), m, root(), true).unwrap();
    let t = one_level(&mut k);
    let l = launch(&mut k, &t.id);

    // Never acted: the first act is not held back.
    let plan = k
        .driver_act_begin(&l.token, 1000, "maskai-1", "echo", &echo_args())
        .unwrap();
    k.driver_act_settle(&l.token, 1000, &plan, Ok(echo_outcome("one")))
        .unwrap();
    assert_eq!(k.driver("maskai-1").unwrap().unwrap().last_act_ms, 1000);

    let err = k
        .driver_act_begin(&l.token, 1500, "maskai-1", "look", &serde_json::json!({}))
        .unwrap_err();
    assert!(matches!(err, KernelError::Gate(_)), "{err}");
    assert!(
        err.to_string()
            .contains("driver maskai-1: 1500 ms before the next action"),
        "{err}"
    );
    k.driver_act_begin(&l.token, 3000, "maskai-1", "look", &serde_json::json!({}))
        .unwrap();
}

#[test]
fn refusal_5_max_actions() {
    let d = tempfile::tempdir().unwrap();
    let mut k = open(d.path());
    mount(&mut k);
    let t = one_level(&mut k);
    let l = launch(&mut k, &t.id);

    for now in [10u64, 20] {
        let plan = k
            .driver_act_begin(&l.token, now, "maskai-1", "echo", &echo_args())
            .unwrap();
        k.driver_act_settle(&l.token, now + 1, &plan, Ok(echo_outcome("x")))
            .unwrap();
    }
    let err = k
        .driver_act_begin(&l.token, 30, "maskai-1", "echo", &echo_args())
        .unwrap_err();
    assert!(matches!(err, KernelError::Gate(_)), "{err}");
    assert!(
        err.to_string().contains("step 0 reached its 2 actions"),
        "{err}"
    );
}

#[test]
fn refusal_6_stop() {
    let d = tempfile::tempdir().unwrap();
    let mut k = open(d.path());
    mount(&mut k);
    let t = one_level(&mut k);
    let l = launch(&mut k, &t.id);
    k.stop(&human(3), "node").unwrap();
    let err = k
        .driver_act_begin(&l.token, 4, "maskai-1", "echo", &echo_args())
        .unwrap_err();
    assert!(
        matches!(err, KernelError::Stopped(ref s) if s == "node"),
        "{err}"
    );
}

#[test]
fn refusal_7_label_above_clearance() {
    let d = tempfile::tempdir().unwrap();
    let mut k = open(d.path());
    mount(&mut k);
    let t = one_level(&mut k);
    let l = launch(&mut k, &t.id);

    // The register rises to Personal after the launch; the driver is cleared
    // to Business.
    let mut reg = k.read_register(&machine(3), &t.register).unwrap();
    reg.label = Label {
        scope: Scope::Personal,
        data_class: DataClass::Own,
        origins: Default::default(),
    };
    k.write_register(&machine(3), reg).unwrap();

    let err = k
        .driver_act_begin(&l.token, 4, "maskai-1", "echo", &echo_args())
        .unwrap_err();
    assert!(matches!(err, KernelError::I2(_)), "{err}");
    assert!(
        err.to_string().contains(&format!(
            "register of task {} exceeds driver maskai-1's clearance",
            t.id
        )),
        "{err}"
    );
}

#[test]
fn settle_attaches_evidence_logs_hashes_only_and_feeds_the_register() {
    let d = tempfile::tempdir().unwrap();
    let mut k = open(d.path());
    let row = mount(&mut k);
    let t = one_level(&mut k);
    let l = launch(&mut k, &t.id);
    let before = k.read_register(&machine(9), &t.register).unwrap();

    let plan: DriverActPlan = k
        .driver_act_begin(&l.token, 10, "maskai-1", "echo", &echo_args())
        .unwrap();
    assert_eq!(plan.task_id, t.id);
    assert_eq!(plan.step_index, 0);
    assert_eq!(plan.name, "maskai-1");
    assert_eq!(plan.driver_id, row.driver_id);
    assert_eq!(plan.action.name, "echo");
    assert_eq!(plan.args, echo_args());
    assert_eq!(plan.args_hash, hash_canonical(&echo_args()));
    assert_eq!(plan.deadline_ms, 10 + 60_000);
    assert_eq!(plan.begun_ms, 10);
    assert_eq!(plan.caps.per_file, HARNESS_MAX_FILE_BYTES);
    assert_eq!(plan.caps.per_action, HARNESS_MAX_RUN_BYTES / 4);
    assert_eq!(plan.dirs.evidence, k.driver_dirs("maskai-1").evidence);

    let result = k
        .driver_act_settle(&l.token, 20, &plan, Ok(echo_outcome("journal line")))
        .unwrap();
    assert_eq!(result, serde_json::json!({ "text": "journal line" }));

    // The evidence is an artefact under its own name, at the register's label
    // plus the third-party origin; the bytes are what the site produced.
    let reg = k.read_register(&machine(21), &t.register).unwrap();
    assert_eq!(reg.artefacts.len(), before.artefacts.len() + 1);
    let art = reg.artefacts.last().unwrap();
    assert_eq!(art.kind, "note.txt");
    let env = k.store().blobs.envelope(&art.hash).unwrap();
    assert!(
        env.label.origins.contains(&Origin::ThirdPartyInbound),
        "{:?}",
        env.label
    );
    assert_eq!(env.label.scope, before.label.scope);
    assert_eq!(env.label.data_class, before.label.data_class);
    assert_eq!(
        k.read_artefact(&machine(21), &art.hash).unwrap(),
        b"hello from the site".to_vec()
    );
    // `to_register`: the result's text is the register's newest evidence.
    let fed = reg.evidence.last().unwrap();
    assert_eq!(fed.content, "journal line");
    assert_eq!(fed.origin, Origin::ThirdPartyInbound);
    assert_eq!(reg.evidence.len(), before.evidence.len() + 1);

    // The ledger: one `driver.acted`, last, hashes only.
    let last = k.ledger().events().last().unwrap().clone();
    assert_eq!(last.kind, "driver.acted");
    let expected = DriverActed {
        task_id: t.id.clone(),
        step_index: 0,
        driver_id: row.driver_id.clone(),
        action: "echo".into(),
        args_hash: hash_canonical(&echo_args()),
        result_hash: hash_canonical(&serde_json::json!({ "text": "journal line" })),
        ok: true,
        reason: None,
        duration_ms: 7,
        evidence: vec![art.hash.clone()],
        evidence_bytes: b"hello from the site".len() as u64,
        hosts_seen: vec!["1.2.3.4:443".into()],
    };
    assert_eq!(
        last.payload_hash,
        hash_canonical(&expected),
        "the acted event is exactly the hashes-only record"
    );
    let payload = serde_json::to_string(&expected).unwrap();
    assert!(!payload.contains("hello from the site"), "{payload}");
    assert!(!payload.contains("journal line"), "{payload}");
    assert_eq!(events_of(&k, "driver.acted").len(), 1);

    // The row moved on, and the driver is free again.
    let after = k.driver("maskai-1").unwrap().unwrap();
    assert_eq!(after.last_act_ms, 20);
    assert_eq!(after.state, DriverState::Ready);
    k.driver_act_begin(&l.token, 21, "maskai-1", "look", &serde_json::json!({}))
        .unwrap();
    assert!(k.ledger().verify_chain());
}

#[test]
fn settle_on_host_timeout_degrades_the_driver() {
    let d = tempfile::tempdir().unwrap();
    let mut k = open(d.path());
    let row = mount(&mut k);
    let t = one_level(&mut k);
    let l = launch(&mut k, &t.id);

    let plan = k
        .driver_act_begin(&l.token, 10, "maskai-1", "echo", &echo_args())
        .unwrap();
    let err = k
        .driver_act_settle(&l.token, 30, &plan, Err("timeout".into()))
        .unwrap_err();
    assert!(matches!(err, KernelError::Gate(_)), "{err}");
    assert!(err.to_string().contains("timeout"), "{err}");

    let acted = events_of(&k, "driver.acted");
    assert_eq!(acted.len(), 1);
    assert_eq!(
        acted[0].payload_hash,
        hash_canonical(&DriverActed {
            task_id: t.id.clone(),
            step_index: 0,
            driver_id: row.driver_id.clone(),
            action: "echo".into(),
            args_hash: hash_canonical(&echo_args()),
            result_hash: hash_canonical(&serde_json::Value::Null),
            ok: false,
            reason: Some("timeout".into()),
            duration_ms: 20,
            evidence: vec![],
            evidence_bytes: 0,
            hosts_seen: vec![],
        })
    );
    let after = k.driver("maskai-1").unwrap().unwrap();
    assert_eq!(
        after.state,
        DriverState::Degraded {
            reason: "timeout".into()
        }
    );
    let unmounted = events_of(&k, "driver.unmounted");
    assert_eq!(unmounted.len(), 1);
    assert_eq!(
        unmounted[0].payload_hash,
        hash_canonical(&DriverUnmounted {
            driver_id: row.driver_id.clone(),
            reason: "timeout".into(),
        })
    );
    // Nothing reached the register.
    let reg = k.read_register(&machine(31), &t.register).unwrap();
    assert!(reg.artefacts.is_empty());

    let err = k
        .driver_act_begin(&l.token, 40, "maskai-1", "echo", &echo_args())
        .unwrap_err();
    assert!(err.to_string().contains("degraded"), "{err}");

    // The remedy works: a remount of the same driver brings it back.
    let back = k
        .mount_driver(&human(50), maskai_like(), root(), true)
        .unwrap();
    assert_eq!(back.state, DriverState::Ready);
    assert_eq!(events_of(&k, "driver.mounted").len(), 2);
    k.driver_act_begin(&l.token, 51, "maskai-1", "echo", &echo_args())
        .unwrap();
}

#[test]
fn in_flight_act_blocks_a_second_begin_on_the_same_driver_until_settled() {
    let d = tempfile::tempdir().unwrap();
    let mut k = open(d.path());
    mount(&mut k);
    let t = one_level(&mut k);
    let l = launch(&mut k, &t.id);

    let plan = k
        .driver_act_begin(&l.token, 10, "maskai-1", "echo", &echo_args())
        .unwrap();
    let err = k
        .driver_act_begin(&l.token, 11, "maskai-1", "look", &serde_json::json!({}))
        .unwrap_err();
    assert!(matches!(err, KernelError::Gate(_)), "{err}");
    assert!(err.to_string().contains("in flight"), "{err}");

    k.driver_act_settle(&l.token, 12, &plan, Ok(echo_outcome("x")))
        .unwrap();
    // The refused begin did not count: this is the second of two actions.
    k.driver_act_begin(&l.token, 13, "maskai-1", "look", &serde_json::json!({}))
        .unwrap();
}

/// Review Focus 2: two harness steps of one task declare the same driver.
/// After the first run settles and the human approves, the second step's run
/// holds a new token that begins fine; the spent token does not; and while
/// that act is in flight a token of another task is refused on the driver,
/// then accepted once it settles.
#[test]
fn a_relaunched_step_after_waiting_human_acts_again() {
    let d = tempfile::tempdir().unwrap();
    let mut k = open(d.path());
    mount(&mut k);
    let key = SoftwareHumanKey::generate("phone-1");
    k.enroll_device("phone-1", key.verifying_key_bytes());
    let t = recette_task(
        &mut k,
        vec![
            harness_step(&["maskai-1"], Some(2)),
            StepKind::Approve,
            harness_step(&["maskai-1"], Some(2)),
            StepKind::Approve,
        ],
    );

    // Level one: one act, a clean exit; the evidence is the step's artefact.
    let first = launch(&mut k, &t.id);
    let plan = k
        .driver_act_begin(&first.token, 3, "maskai-1", "echo", &echo_args())
        .unwrap();
    k.driver_act_settle(&first.token, 4, &plan, Ok(echo_outcome("N0-1 ok")))
        .unwrap();
    let settled = k
        .harness_settle(
            &machine(5),
            &t.id,
            &first,
            &run_ending(ExitReason::Exited(0)),
            false,
        )
        .unwrap();
    assert_eq!(settled.steps[0].status, StepStatus::Done, "{settled:?}");
    assert!(matches!(
        k.driver_act_begin(&first.token, 6, "maskai-1", "look", &serde_json::json!({})),
        Err(KernelError::I1(_))
    ));

    // The human approves; the scheduler moves on.
    let waiting = k.run_task_step(&machine(7), &t.id).unwrap();
    assert_eq!(waiting.status, TaskStatus::WaitingHuman);
    approve_waiting(&mut k, &key, &t.id, 8);
    let moved = k.run_task_step(&machine(9), &t.id).unwrap();
    assert_eq!(moved.steps[1].status, StepStatus::Done);
    assert_eq!(moved.status, TaskStatus::Running);

    // Level two: a new token on the same driver, counting from zero.
    let second = k
        .harness_launch(&machine(10), &t.id, "claude-code", 60_000)
        .unwrap();
    assert_ne!(second.token, first.token);
    assert_eq!(second.step_index, 2);
    let plan2 = k
        .driver_act_begin(&second.token, 11, "maskai-1", "echo", &echo_args())
        .unwrap();
    assert_eq!(plan2.step_index, 2);

    // Another task on the same driver, while that act is in flight.
    let other = recette_task(&mut k, vec![harness_step(&["maskai-1"], None)]);
    let third = k
        .harness_launch(&machine(12), &other.id, "claude-code", 60_000)
        .unwrap();
    let err = k
        .driver_act_begin(&third.token, 13, "maskai-1", "look", &serde_json::json!({}))
        .unwrap_err();
    assert!(err.to_string().contains("in flight"), "{err}");
    k.driver_act_settle(&second.token, 14, &plan2, Ok(echo_outcome("N1-1 ok")))
        .unwrap();
    k.driver_act_begin(&third.token, 15, "maskai-1", "look", &serde_json::json!({}))
        .unwrap();
}

#[test]
fn judge_attaches_verdict_md() {
    let d = tempfile::tempdir().unwrap();
    let mut k = open(d.path());
    let arch = k.register_arch(mock_manifest("mock"));
    let t = recette_task(
        &mut k,
        vec![StepKind::Judge { arch_id: arch }, StepKind::Approve],
    );
    let t = k.run_task_step(&machine(2), &t.id).unwrap();
    assert_eq!(t.steps[0].status, StepStatus::Done);
    let reg = k.read_register(&machine(3), &t.register).unwrap();
    let verdict = reg
        .artefacts
        .last()
        .expect("the judge attached its verdict");
    assert_eq!(verdict.kind, "verdict.md");
    assert_eq!(
        k.read_artefact(&machine(3), &verdict.hash).unwrap(),
        reg.open_questions.last().unwrap().as_bytes()
    );
    let t = k.run_task_step(&machine(4), &t.id).unwrap();
    assert_eq!(t.status, TaskStatus::WaitingHuman);
    assert_eq!(
        k.approval_subject(&machine(5), &t.id).unwrap(),
        verdict.hash
    );
}

/// Review Focus 5: two approves, the second after a draft whose arch returned
/// an empty string. The draft fails and the task fails; nothing waits on the
/// judge's verdict as if it were the report.
#[test]
fn a_recipe_with_two_approves_and_a_draft_that_returns_nothing_fails_not_waits() {
    let d = tempfile::tempdir().unwrap();
    let mut k = open(d.path());
    let key = SoftwareHumanKey::generate("phone-1");
    k.enroll_device("phone-1", key.verifying_key_bytes());
    let judge = k.register_arch(mock_manifest("mock"));
    let silent_manifest = mock_manifest("silent");
    let silent = k
        .mount(
            Arc::new(Silent(silent_manifest.clone())),
            MountSpec::mock(&silent_manifest, silent_manifest.context_ceiling),
        )
        .unwrap()
        .arch_id;
    let t = recette_task(
        &mut k,
        vec![
            StepKind::Judge { arch_id: judge },
            StepKind::Approve,
            StepKind::Draft { arch_id: silent },
            StepKind::Approve,
            StepKind::Release {
                to_dir: "runs".into(),
            },
        ],
    );
    k.run_task_step(&machine(2), &t.id).unwrap();
    let waiting = k.run_task_step(&machine(3), &t.id).unwrap();
    assert_eq!(waiting.status, TaskStatus::WaitingHuman);
    approve_waiting(&mut k, &key, &t.id, 4);
    let moved = k.run_task_step(&machine(5), &t.id).unwrap();
    assert_eq!(moved.steps[1].status, StepStatus::Done);

    let err = k.run_task_step(&machine(6), &t.id).unwrap_err();
    assert!(matches!(err, KernelError::Gate(_)), "{err}");
    assert!(
        err.to_string().contains("returned nothing to attach"),
        "{err}"
    );
    let row = k.task(&machine(7), &t.id).unwrap().unwrap();
    assert_eq!(row.status, TaskStatus::Failed);
    assert!(matches!(row.steps[2].status, StepStatus::Failed(_)));
    assert_eq!(row.steps[3].status, StepStatus::Pending);
    let err = k.approval_subject(&machine(8), &t.id).unwrap_err();
    assert!(err.to_string().contains("not waiting"), "{err}");
}

#[test]
fn harness_launch_uses_the_step_instruction_and_default_prompt_otherwise() {
    let d = tempfile::tempdir().unwrap();
    let mut k = open(d.path());
    let told = recette_task(
        &mut k,
        vec![StepKind::Harness {
            name: "claude-code".into(),
            drivers: vec![],
            instruction: Some("Niveau N0: mesurer_chargement trois fois.".into()),
            max_actions: Some(60),
            timeout_s: Some(1800),
        }],
    );
    let l = launch(&mut k, &told.id);
    assert_eq!(l.prompt, "Niveau N0: mesurer_chargement trois fois.");
    assert_eq!(l.timeout_s, Some(1800));
    assert!(
        !format!("{l:?}").contains(&l.token),
        "the token is redacted"
    );

    let plain = recette_task(
        &mut k,
        vec![StepKind::Harness {
            name: "claude-code".into(),
            drivers: vec![],
            instruction: None,
            max_actions: None,
            timeout_s: None,
        }],
    );
    let l = launch(&mut k, &plain.id);
    assert_eq!(l.prompt, HARNESS_PROMPT);
    assert_eq!(l.timeout_s, None);
}

#[test]
fn create_task_writes_constraints_and_evidence_into_the_register() {
    let d = tempfile::tempdir().unwrap();
    let mut k = open(d.path());
    let t = k
        .create_task(
            &machine(1),
            "recette",
            "report",
            Label::bottom(),
            &["fictional data only".into(), "nothing deleted".into()],
            &["cases: N0-1".into()],
            vec![StepKind::Approve],
        )
        .unwrap();
    let reg = k.read_register(&machine(2), &t.register).unwrap();
    assert_eq!(
        reg.constraints,
        vec!["fictional data only".to_string(), "nothing deleted".into()]
    );
    assert_eq!(reg.evidence.len(), 1);
    assert_eq!(reg.evidence[0].content, "cases: N0-1");
    assert_eq!(reg.evidence[0].origin, Origin::OwnerAuthored);
    assert_eq!(
        reg.evidence[0].source_hash,
        hash_canonical(&"cases: N0-1".to_string())
    );
    assert_eq!(reg.goal, "recette");
    assert_eq!(t.id, reg.task_id);
}

#[test]
fn boot_rereads_the_drivers_table() {
    let d = tempfile::tempdir().unwrap();
    let row = {
        let mut k = open(d.path());
        mount(&mut k)
    };
    let mut k = open(d.path());
    let rows = k.drivers().unwrap();
    assert_eq!(rows, vec![row.clone()]);

    // A stored manifest that no longer validates is degraded at boot, with
    // the reason on the row and on the ledger; the sound one is untouched.
    let mut broken = row.clone();
    broken.manifest.name = "broken".into();
    broken.manifest.hosts.clear();
    broken.driver_id = broken.manifest.driver_id();
    k.store().db.put_json("drivers", "broken", &broken).unwrap();
    k.boot().unwrap();
    assert_eq!(
        k.driver("broken").unwrap().unwrap().state,
        DriverState::Degraded {
            reason: "manifest invalid at boot".into()
        }
    );
    assert_eq!(k.driver("maskai-1").unwrap().unwrap(), row);
    let unmounted = events_of(&k, "driver.unmounted");
    assert_eq!(unmounted.len(), 1);
    assert_eq!(
        unmounted[0].payload_hash,
        hash_canonical(&DriverUnmounted {
            driver_id: broken.driver_id.clone(),
            reason: "manifest invalid at boot".into(),
        })
    );
    // A second boot has nothing left to degrade.
    k.boot().unwrap();
    assert_eq!(events_of(&k, "driver.unmounted").len(), 1);
}

#[test]
fn recipe_steps_deserialize_as_step_kinds() {
    let recipe: TaskRecipe = serde_json::from_str(include_str!(
        "../../../contracts/examples/task_recipe/valid/recette.json"
    ))
    .unwrap();
    recipe.validate().unwrap();
    assert!(!recipe.steps.is_empty());
    for step in &recipe.steps {
        let wire = serde_json::to_value(step).unwrap();
        let kind: StepKind =
            serde_json::from_value(wire.clone()).unwrap_or_else(|e| panic!("{wire}: {e}"));
        assert_eq!(serde_json::to_value(&kind).unwrap(), wire);
    }
    // And a recipe step with every harness option set survives the trip.
    let full = serde_json::json!({
        "kind": "harness", "name": "claude-code", "drivers": ["maskai-1"],
        "instruction": "do", "max_actions": 60, "timeout_s": 1800
    });
    let kind: StepKind = serde_json::from_value(full.clone()).unwrap();
    assert_eq!(serde_json::to_value(&kind).unwrap(), full);
}

/// Ruling 21.4 on the driver side: the harness run times out and settles
/// while the browser is still working on an act. When the act comes back its
/// token is spent; the settle still records `driver.acted{ok:false}`, frees
/// the driver, moves its clock, and refuses the dead token.
#[test]
fn settle_after_the_run_ended_still_records_the_action() {
    let d = tempfile::tempdir().unwrap();
    let mut k = open(d.path());
    let row = mount(&mut k);
    let t = one_level(&mut k);
    let l = launch(&mut k, &t.id);
    let plan = k
        .driver_act_begin(&l.token, 10, "maskai-1", "echo", &echo_args())
        .unwrap();
    k.harness_settle(
        &machine(11),
        &t.id,
        &l,
        &run_ending(ExitReason::Timeout),
        false,
    )
    .unwrap();

    let err = k
        .driver_act_settle(&l.token, 12, &plan, Ok(echo_outcome("late")))
        .unwrap_err();
    assert!(matches!(err, KernelError::I1(_)), "{err}");
    let acted = events_of(&k, "driver.acted");
    assert_eq!(acted.len(), 1);
    assert_eq!(
        acted[0].payload_hash,
        hash_canonical(&DriverActed {
            task_id: t.id.clone(),
            step_index: 0,
            driver_id: row.driver_id.clone(),
            action: "echo".into(),
            args_hash: hash_canonical(&echo_args()),
            result_hash: hash_canonical(&serde_json::json!({ "text": "late" })),
            ok: false,
            reason: Some(
                "harness run ended before the action settled: I1 violated: unknown harness token"
                    .into()
            ),
            duration_ms: 7,
            evidence: vec![],
            evidence_bytes: 0,
            hosts_seen: vec!["1.2.3.4:443".into()],
        })
    );
    // Nothing reached the register; the driver is free and its clock moved.
    let reg = k.read_register(&machine(13), &t.register).unwrap();
    assert!(reg.artefacts.is_empty());
    let after = k.driver("maskai-1").unwrap().unwrap();
    assert_eq!(after.last_act_ms, 12);
    assert_eq!(after.state, DriverState::Ready);
    let other = recette_task(&mut k, vec![harness_step(&["maskai-1"], None)]);
    let l2 = k
        .harness_launch(&machine(14), &other.id, "claude-code", 60_000)
        .unwrap();
    k.driver_act_begin(&l2.token, 15, "maskai-1", "look", &serde_json::json!({}))
        .unwrap();
}

/// A successful action whose evidence is `files`.
fn outcome_with(files: Vec<EvidenceFile>) -> ActOutcome {
    ActOutcome {
        evidence: files,
        ..echo_outcome("x")
    }
}

fn note(bytes: Vec<u8>) -> EvidenceFile {
    EvidenceFile {
        name: "note.txt".into(),
        mime: "text/plain".into(),
        bytes,
    }
}

/// The `driver.acted{ok:false}` record a settle-door refusal leaves: the
/// outcome's own result hash, duration and hosts; nothing kept as evidence.
fn refused_acted(t: &Task, row: &DriverRow, outcome: &ActOutcome, why: &str) -> DriverActed {
    DriverActed {
        task_id: t.id.clone(),
        step_index: 0,
        driver_id: row.driver_id.clone(),
        action: "echo".into(),
        args_hash: hash_canonical(&echo_args()),
        result_hash: hash_canonical(&outcome.result),
        ok: false,
        reason: Some(why.into()),
        duration_ms: outcome.duration_ms,
        evidence: vec![],
        evidence_bytes: 0,
        hosts_seen: outcome.hosts_seen.clone(),
    }
}

/// Review round 1, finding 1: an action schema the library will not compile is
/// refused at mount with its remedy; a row that reached the table behind the
/// mount's back still gets a refusal from `driver_act_begin`, never a panic
/// under the kernel lock.
#[test]
fn a_schema_that_does_not_compile_is_refused_at_mount_and_is_a_gate_at_begin() {
    let d = tempfile::tempdir().unwrap();
    let mut k = open(d.path());
    let mut m = maskai_like();
    m.actions[0].args = serde_json::json!({
        "type": "object",
        "properties": { "case": { "type": "strnig" } }
    });
    let err = k
        .mount_driver(&human(1), m.clone(), root(), true)
        .unwrap_err();
    assert!(matches!(err, KernelError::Gate(_)), "{err}");
    assert!(err.to_string().contains("does not compile"), "{err}");
    assert!(k.drivers().unwrap().is_empty());

    let row = DriverRow {
        driver_id: m.driver_id(),
        manifest: m,
        root: root(),
        mounted_ms: 1,
        state: DriverState::Ready,
        last_act_ms: 0,
    };
    k.store().db.put_json("drivers", "maskai-1", &row).unwrap();
    let t = one_level(&mut k);
    let l = launch(&mut k, &t.id);
    let err = k
        .driver_act_begin(&l.token, 5, "maskai-1", "echo", &echo_args())
        .unwrap_err();
    assert!(matches!(err, KernelError::Gate(_)), "{err}");
    assert!(err.to_string().contains("args for echo"), "{err}");
    assert!(err.to_string().contains("does not compile"), "{err}");
    // Nothing was claimed: the sound action goes through.
    k.driver_act_begin(&l.token, 6, "maskai-1", "look", &serde_json::json!({}))
        .unwrap();
}

/// Review round 1, finding 3 (i): an evidence name the action did not declare
/// is refused at the kernel's door — `driver.acted{ok:false, reason}`, no
/// artefact, the clock moved, the driver free again.
#[test]
fn settle_refuses_an_evidence_name_the_action_did_not_declare() {
    let d = tempfile::tempdir().unwrap();
    let mut k = open(d.path());
    let row = mount(&mut k);
    let t = one_level(&mut k);
    let l = launch(&mut k, &t.id);
    let plan = k
        .driver_act_begin(&l.token, 10, "maskai-1", "echo", &echo_args())
        .unwrap();
    let outcome = outcome_with(vec![EvidenceFile {
        name: "other.txt".into(),
        mime: "text/plain".into(),
        bytes: b"x".to_vec(),
    }]);
    let err = k
        .driver_act_settle(&l.token, 20, &plan, Ok(outcome.clone()))
        .unwrap_err();
    let why = "evidence other.txt is not a name the manifest allows for echo";
    assert!(matches!(err, KernelError::Gate(_)), "{err}");
    assert!(err.to_string().contains(why), "{err}");
    let acted = events_of(&k, "driver.acted");
    assert_eq!(acted.len(), 1);
    assert_eq!(
        acted[0].payload_hash,
        hash_canonical(&refused_acted(&t, &row, &outcome, why))
    );
    assert!(k
        .read_register(&machine(21), &t.register)
        .unwrap()
        .artefacts
        .is_empty());
    assert_eq!(k.driver("maskai-1").unwrap().unwrap().last_act_ms, 20);
    k.driver_act_begin(&l.token, 22, "maskai-1", "look", &serde_json::json!({}))
        .unwrap();
}

/// Review round 1, finding 3 (ii): a file over the per-file cap is refused,
/// recorded, and remembered on the token so the run fails naming it.
#[test]
fn settle_refuses_a_file_over_the_per_file_cap_and_the_run_fails_naming_it() {
    let d = tempfile::tempdir().unwrap();
    let mut k = open(d.path());
    let row = mount(&mut k);
    let t = one_level(&mut k);
    let l = launch(&mut k, &t.id);
    let plan = k
        .driver_act_begin(&l.token, 10, "maskai-1", "echo", &echo_args())
        .unwrap();
    let len = HARNESS_MAX_FILE_BYTES + 1;
    let outcome = outcome_with(vec![note(vec![b'x'; len as usize])]);
    let err = k
        .driver_act_settle(&l.token, 20, &plan, Ok(outcome.clone()))
        .unwrap_err();
    let why = format!(
        "note.txt is {len} bytes, above the {HARNESS_MAX_FILE_BYTES}-byte limit for one file"
    );
    assert!(matches!(err, KernelError::Gate(_)), "{err}");
    assert!(err.to_string().contains(&why), "{err}");
    let acted = events_of(&k, "driver.acted");
    assert_eq!(acted.len(), 1);
    assert_eq!(
        acted[0].payload_hash,
        hash_canonical(&refused_acted(&t, &row, &outcome, &why))
    );
    assert!(k
        .read_register(&machine(21), &t.register)
        .unwrap()
        .artefacts
        .is_empty());
    assert_eq!(k.driver("maskai-1").unwrap().unwrap().last_act_ms, 20);
    k.driver_act_begin(&l.token, 22, "maskai-1", "look", &serde_json::json!({}))
        .unwrap();

    // Remembered: the run fails at settle, naming the file.
    let settled = k
        .harness_settle(
            &machine(30),
            &t.id,
            &l,
            &run_ending(ExitReason::Exited(0)),
            false,
        )
        .unwrap();
    assert_eq!(settled.status, TaskStatus::Failed);
    match &settled.steps[0].status {
        StepStatus::Failed(w) => assert!(w.contains("note.txt"), "{w}"),
        other => panic!("{other:?}"),
    }
}

/// Review round 1, finding 3 (iii): evidence that would take the run past its
/// 16 MiB total is refused whole, recorded, and remembered so the run fails
/// naming the file; what was attached before the breach stays.
#[test]
fn settle_refuses_evidence_past_the_run_total_and_the_run_fails_naming_it() {
    let d = tempfile::tempdir().unwrap();
    let mut k = open(d.path());
    let row = mount(&mut k);
    let t = recette_task(
        &mut k,
        vec![harness_step(&["maskai-1"], None), StepKind::Approve],
    );
    let l = launch(&mut k, &t.id);
    // Exactly at the cap: eight files of exactly the per-file maximum.
    let acts = HARNESS_MAX_RUN_BYTES / HARNESS_MAX_FILE_BYTES;
    for i in 0..acts {
        let now = 10 + i;
        let plan = k
            .driver_act_begin(&l.token, now, "maskai-1", "echo", &echo_args())
            .unwrap();
        k.driver_act_settle(
            &l.token,
            now,
            &plan,
            Ok(outcome_with(vec![note(vec![
                i as u8;
                HARNESS_MAX_FILE_BYTES
                    as usize
            ])])),
        )
        .unwrap();
    }
    let before = k
        .read_register(&machine(50), &t.register)
        .unwrap()
        .artefacts
        .len();
    assert_eq!(before, acts as usize);

    let plan = k
        .driver_act_begin(&l.token, 100, "maskai-1", "echo", &echo_args())
        .unwrap();
    let outcome = outcome_with(vec![note(b"1".to_vec())]);
    let err = k
        .driver_act_settle(&l.token, 101, &plan, Ok(outcome.clone()))
        .unwrap_err();
    let why = format!(
        "evidence of echo (1 bytes) would take the run past the {HARNESS_MAX_RUN_BYTES}-byte limit"
    );
    assert!(matches!(err, KernelError::Gate(_)), "{err}");
    assert!(err.to_string().contains(&why), "{err}");
    let acted = events_of(&k, "driver.acted");
    assert_eq!(acted.len(), acts as usize + 1);
    assert_eq!(
        acted.last().unwrap().payload_hash,
        hash_canonical(&refused_acted(&t, &row, &outcome, &why))
    );
    assert_eq!(
        k.read_register(&machine(102), &t.register)
            .unwrap()
            .artefacts
            .len(),
        before,
        "nothing of the refused action is kept"
    );
    assert_eq!(k.driver("maskai-1").unwrap().unwrap().last_act_ms, 101);

    let settled = k
        .harness_settle(
            &machine(110),
            &t.id,
            &l,
            &run_ending(ExitReason::Exited(0)),
            false,
        )
        .unwrap();
    assert_eq!(settled.status, TaskStatus::Failed);
    match &settled.steps[0].status {
        StepStatus::Failed(w) => assert!(w.contains("note.txt"), "{w}"),
        other => panic!("{other:?}"),
    }
}

/// Review round 1, finding 4: an act the daemon dropped without settling must
/// not hold the driver busy for ever. Unmounting clears it with the row, and
/// the remount acts again.
#[test]
fn unmount_clears_an_act_left_in_flight_so_a_remount_can_act() {
    let d = tempfile::tempdir().unwrap();
    let mut k = open(d.path());
    let row = mount(&mut k);
    let t = one_level(&mut k);
    let l = launch(&mut k, &t.id);
    k.driver_act_begin(&l.token, 10, "maskai-1", "echo", &echo_args())
        .unwrap();
    let err = k
        .driver_act_begin(&l.token, 11, "maskai-1", "look", &serde_json::json!({}))
        .unwrap_err();
    assert!(err.to_string().contains("in flight"), "{err}");

    let gone = k
        .unmount_driver("maskai-1", "operator")
        .unwrap()
        .expect("the row was there");
    assert_eq!(gone.driver_id, row.driver_id);
    assert!(k.driver("maskai-1").unwrap().is_none());
    let unmounted = events_of(&k, "driver.unmounted");
    assert_eq!(unmounted.len(), 1);
    assert_eq!(
        unmounted[0].payload_hash,
        hash_canonical(&DriverUnmounted {
            driver_id: row.driver_id.clone(),
            reason: "operator".into(),
        })
    );
    assert!(
        k.unmount_driver("maskai-1", "operator").unwrap().is_none(),
        "a second unmount finds nothing and logs nothing"
    );
    assert_eq!(events_of(&k, "driver.unmounted").len(), 1);

    let back = mount(&mut k);
    assert_eq!(back.state, DriverState::Ready);
    k.driver_act_begin(&l.token, 12, "maskai-1", "look", &serde_json::json!({}))
        .unwrap();
}

/// Ruling R12: every host-side failure degrades the driver — a process that
/// would not start, printed no hello, timed out, died or broke protocol is
/// not one to send the next request to. Only the kernel's own evidence
/// refusals (the settle door) leave it `Ready`.
#[test]
fn settle_on_any_host_failure_degrades_the_driver() {
    let d = tempfile::tempdir().unwrap();
    let mut k = open(d.path());
    let row = mount(&mut k);
    let t = one_level(&mut k);
    let l = launch(&mut k, &t.id);
    let plan = k
        .driver_act_begin(&l.token, 10, "maskai-1", "echo", &echo_args())
        .unwrap();
    let reason = "driver would not start: spawn failed";
    let err = k
        .driver_act_settle(&l.token, 30, &plan, Err(reason.into()))
        .unwrap_err();
    assert!(matches!(err, KernelError::Gate(_)), "{err}");
    assert!(err.to_string().contains(reason), "{err}");
    assert_eq!(
        k.driver("maskai-1").unwrap().unwrap().state,
        DriverState::Degraded {
            reason: reason.into()
        }
    );
    let unmounted = events_of(&k, "driver.unmounted");
    assert_eq!(unmounted.len(), 1);
    assert_eq!(
        unmounted[0].payload_hash,
        hash_canonical(&DriverUnmounted {
            driver_id: row.driver_id.clone(),
            reason: reason.into(),
        })
    );
    let err = k
        .driver_act_begin(&l.token, 31, "maskai-1", "look", &serde_json::json!({}))
        .unwrap_err();
    assert!(err.to_string().contains("degraded"), "{err}");
}

/// Reviewer minor 2: a judge whose completion is empty after `arch::raise`'s
/// `judge: ` prefix has no verdict to attach, and the step fails by name
/// rather than leaving the approve after it waiting on a blank.
#[test]
fn a_judge_that_returns_nothing_fails_the_step() {
    let d = tempfile::tempdir().unwrap();
    let mut k = open(d.path());
    let silent_manifest = mock_manifest("silent");
    let silent = k
        .mount(
            Arc::new(Silent(silent_manifest.clone())),
            MountSpec::mock(&silent_manifest, silent_manifest.context_ceiling),
        )
        .unwrap()
        .arch_id;
    let t = recette_task(
        &mut k,
        vec![StepKind::Judge { arch_id: silent }, StepKind::Approve],
    );
    let err = k.run_task_step(&machine(2), &t.id).unwrap_err();
    assert!(matches!(err, KernelError::Gate(_)), "{err}");
    assert!(
        err.to_string().contains("returned no verdict to attach"),
        "{err}"
    );
    let row = k.task(&machine(3), &t.id).unwrap().unwrap();
    assert_eq!(row.status, TaskStatus::Failed);
    assert!(matches!(row.steps[0].status, StepStatus::Failed(_)));
    assert!(k
        .read_register(&machine(4), &t.register)
        .unwrap()
        .artefacts
        .is_empty());
}
