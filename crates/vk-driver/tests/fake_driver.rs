//! The host against a scripted driver: a Python script that speaks protocol 1
//! and misbehaves on request — sleeps past its deadline, names evidence outside
//! its directory, writes more evidence than the cap allows, prints noise on
//! stdout, exits mid-request — so every refusal the host owes the kernel is
//! exercised against a real child process, not a mock.
//!
//! Every test writes the script into its own temporary directory and runs it
//! with `python -I`. When no `python` is on PATH the test says so on stderr
//! and returns; it never fails silently and never passes vacuously without
//! saying why.
//!
//! The kill tests bound their wait by [`kill_bound`], far below the 30 s (or
//! 60 s) the driver would otherwise sleep; what they assert is the outcome —
//! the deadline or the STOP cut the sleep short and the tree is dead — never
//! how fast the kill ran (Ruling 44). On Windows `kill_tree` is a `taskkill
//! /T /F`, measured at 0.8–1.2 s on an idle machine and more when a dozen
//! Python interpreters start at once; a bound at the brief's 2–3 s held in
//! most full-suite runs here and failed in one.
use serde_json::{json, Value};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};
use vk_contracts::driver::{DriverAction, DriverManifest};
use vk_driver::{DriverHost, EvidenceCaps, HostError, PROTOCOL};
use vk_harness::confine::NoopGovernor;

/// The scripted driver of the task brief, verbatim.
const FAKE_DRIVER: &str = r#"import json, os, sys, time
def say(o): sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
say({"hello": {"protocol": 1, "driver": "fake", "version": "0", "actions": ["echo", "slow", "escape", "big", "noise", "die"]}})
for line in sys.stdin:
    r = json.loads(line); a = r["action"]; ev = r["evidence_dir"]
    if a == "echo":
        p = os.path.join(ev, "note.txt"); open(p, "w").write("hi")
        say({"id": r["id"], "ok": True, "result": {"echo": r["args"]}, "evidence": [{"name": "note.txt", "mime": "text/plain", "path": p}]})
    elif a == "slow": time.sleep(30)
    elif a == "escape":
        say({"id": r["id"], "ok": True, "result": {}, "evidence": [{"name": "note.txt", "mime": "text/plain", "path": os.path.join(ev, "..", "..", "secret.txt")}]})
    elif a == "big":
        p = os.path.join(ev, "big.bin"); open(p, "wb").write(b"x" * (3 * 1024 * 1024))
        say({"id": r["id"], "ok": True, "result": {}, "evidence": [{"name": "big.bin", "mime": "application/octet-stream", "path": p}]})
    elif a == "noise":
        sys.stdout.write("Playwright warning: something\n"); sys.stdout.flush()
        say({"id": r["id"], "ok": True, "result": {"after": "noise"}})
    elif a == "die": sys.exit(3)
"#;

/// A driver that answers one action, `env`, with the environment the host gave
/// it and its working directory — the contract the Python driver of Task 4
/// reads its configuration from (the brief's four variables, and ruling R1's
/// `VK_DRIVER_HOSTS`).
const ENV_DRIVER: &str = r#"import json, os, sys
def say(o): sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
say({"hello": {"protocol": 1, "driver": "env", "version": "0", "actions": ["env"]}})
names = ["VK_DRIVER_PROFILE_DIR", "VK_DRIVER_WORK_DIR", "VK_DRIVER_HEADLESS", "VK_DRIVER_CHANNEL", "VK_DRIVER_HOSTS"]
for line in sys.stdin:
    r = json.loads(line)
    env = {k: os.environ.get(k) for k in names}
    env["cwd"] = os.getcwd()
    say({"id": r["id"], "ok": True, "result": env})
"#;

/// Caps wide enough that nothing in these tests trips them unless a test
/// lowers one on purpose.
const WIDE: EvidenceCaps = EvidenceCaps {
    per_file: 8 << 20,
    per_action: 32 << 20,
};

/// The longest a kill test waits for the host to come back from a deadline, a
/// STOP or a silent driver: 20 s on CI (the `CI` variable is set; GitHub's
/// Windows runners are slow to start and to kill a process, Ruling 44), 10 s
/// otherwise. Either is a third of the 30 s the fake driver sleeps and a
/// sixth of the 60 s the silent one does, so a bound met is a sleep cut
/// short; nominal time is about 1 s of `taskkill` past the trigger.
fn kill_bound() -> Duration {
    if std::env::var_os("CI").is_some() {
        Duration::from_secs(20)
    } else {
        Duration::from_secs(10)
    }
}

/// Is a `python` on PATH? When not, say so — the test is then skipped out
/// loud, never failed and never silently green.
fn python_available() -> bool {
    match Command::new("python")
        .arg("--version")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
    {
        Ok(status) if status.success() => true,
        other => {
            eprintln!("no python on PATH; skipping ({other:?})");
            false
        }
    }
}

/// One test's directories: the script, the host's work and profile
/// directories and the evidence root, all under one temporary directory that
/// goes when the test does.
struct Rig {
    _tmp: tempfile::TempDir,
    root: PathBuf,
    script: PathBuf,
    work: PathBuf,
    profile: PathBuf,
    evidence: PathBuf,
}

impl Rig {
    fn new(script: &str) -> Rig {
        let tmp = tempfile::tempdir().expect("a temporary directory");
        let root = tmp.path().to_path_buf();
        let path = root.join("fake_driver.py");
        std::fs::write(&path, script).expect("write the driver script");
        let profile = root.join("profile");
        std::fs::create_dir_all(&profile).expect("create the profile dir");
        Rig {
            _tmp: tmp,
            script: path,
            work: root.join("work"),
            profile,
            evidence: root.join("evidence"),
            root,
        }
    }

    /// The argv that runs this rig's script, isolated.
    fn command(&self) -> Vec<String> {
        vec![
            "python".into(),
            "-I".into(),
            self.script.display().to_string(),
        ]
    }

    /// A manifest over `command` declaring `actions`: headless, no channel,
    /// one host. Validated, so the host is exercised over a manifest the
    /// kernel would have accepted.
    fn manifest(&self, command: Vec<String>, actions: Value) -> DriverManifest {
        let m: DriverManifest = serde_json::from_value(json!({
            "name": "fake",
            "kind": "browser",
            "program": {
                "runtime": "python",
                "command": command,
                "files_sha256": format!("sha256:{}", "0".repeat(64)),
                "browser_channel": null,
                "headless": true
            },
            "hosts": ["example.invalid"],
            "actions": actions,
            "min_interval_ms": 0,
            "max_action_ms": 60_000,
            "clearance": {"max_scope": "business", "third_party_allowed": true},
            "governed": false
        }))
        .expect("a well-formed manifest");
        m.validate().expect("a valid manifest");
        m
    }

    /// The six-action manifest of the fake driver.
    fn fake_manifest(&self) -> DriverManifest {
        let action = |name: &str, evidence: &[&str]| {
            json!({
                "name": name,
                "reversible": true,
                "args": {"type": "object"},
                "evidence": evidence,
                "to_register": false
            })
        };
        self.manifest(
            self.command(),
            json!([
                action("echo", &["note.txt"]),
                action("slow", &[]),
                action("escape", &["note.txt"]),
                action("big", &["big.bin"]),
                action("noise", &[]),
                action("die", &[]),
            ]),
        )
    }

    fn spawn(&self, manifest: &DriverManifest) -> DriverHost {
        DriverHost::spawn(
            manifest,
            &self.work,
            &self.profile,
            &self.evidence,
            Box::new(NoopGovernor::new()),
        )
        .expect("the fake driver says hello")
    }

    fn log(&self) -> String {
        std::fs::read_to_string(self.work.join("driver.log")).unwrap_or_default()
    }

    /// Nothing left under the evidence root: every per-action directory went.
    fn evidence_root_is_empty(&self) -> bool {
        match std::fs::read_dir(&self.evidence) {
            Ok(entries) => entries.count() == 0,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => true,
            Err(e) => panic!("read {}: {e}", self.evidence.display()),
        }
    }
}

fn never() -> bool {
    false
}

fn action<'a>(m: &'a DriverManifest, name: &str) -> &'a DriverAction {
    m.action(name).expect("the manifest declares the action")
}

#[test]
fn hello_then_echo_returns_result_and_evidence() {
    if !python_available() {
        return;
    }
    let rig = Rig::new(FAKE_DRIVER);
    let m = rig.fake_manifest();
    let mut host = rig.spawn(&m);
    assert_eq!(host.hello().protocol, PROTOCOL);
    assert_eq!(host.hello().driver, "fake");
    assert!(!host.governed(), "a Noop governor governs nothing");

    let args = json!({"case": "N3-02"});
    let out = host
        .act(
            action(&m, "echo"),
            &args,
            Duration::from_secs(10),
            WIDE,
            &never,
        )
        .expect("echo answers");
    assert!(out.ok, "{out:?}");
    assert_eq!(out.result["echo"], args);
    assert_eq!(out.evidence.len(), 1, "{:?}", out.evidence);
    assert_eq!(out.evidence[0].name, "note.txt");
    assert_eq!(out.evidence[0].mime, "text/plain");
    assert_eq!(out.evidence[0].bytes, b"hi");
    assert!(
        rig.evidence_root_is_empty(),
        "the per-action evidence directory is removed after the read"
    );

    // A second act gets a fresh id and works the same.
    let out = host
        .act(
            action(&m, "echo"),
            &json!({}),
            Duration::from_secs(10),
            WIDE,
            &never,
        )
        .expect("echo answers again");
    assert!(out.ok);
    host.shutdown();
}

#[test]
fn a_slow_action_is_killed_at_the_deadline() {
    if !python_available() {
        return;
    }
    let rig = Rig::new(FAKE_DRIVER);
    let m = rig.fake_manifest();
    let mut host = rig.spawn(&m);
    let started = Instant::now();
    let err = host
        .act(
            action(&m, "slow"),
            &json!({}),
            Duration::from_secs(1),
            WIDE,
            &never,
        )
        .expect_err("a 30 s sleep does not answer in 1 s");
    let elapsed = started.elapsed();
    assert!(matches!(err, HostError::Timeout), "{err}");
    assert!(
        elapsed < kill_bound(),
        "the kill came {elapsed:?} after the request (bound {:?})",
        kill_bound()
    );
    assert!(
        host.try_wait().expect("try_wait").is_some(),
        "the driver tree is dead after a deadline kill"
    );
    assert!(rig.evidence_root_is_empty());
}

#[test]
fn an_evidence_path_outside_the_dir_is_refused() {
    if !python_available() {
        return;
    }
    let rig = Rig::new(FAKE_DRIVER);
    // `<evidence_root>/<id>/../../secret.txt` is this file.
    std::fs::write(rig.root.join("secret.txt"), b"not for the driver").unwrap();
    let m = rig.fake_manifest();
    let mut host = rig.spawn(&m);
    let err = host
        .act(
            action(&m, "escape"),
            &json!({}),
            Duration::from_secs(10),
            WIDE,
            &never,
        )
        .expect_err("a path outside the evidence directory is refused");
    assert!(
        matches!(&err, HostError::EvidenceEscape(name) if name == "note.txt"),
        "{err}"
    );
    assert!(rig.evidence_root_is_empty());
}

#[test]
fn evidence_above_the_per_file_cap_is_refused() {
    if !python_available() {
        return;
    }
    let rig = Rig::new(FAKE_DRIVER);
    let m = rig.fake_manifest();
    let mut host = rig.spawn(&m);
    let caps = EvidenceCaps {
        per_file: 2 << 20,
        per_action: 32 << 20,
    };
    let err = host
        .act(
            action(&m, "big"),
            &json!({}),
            Duration::from_secs(10),
            caps,
            &never,
        )
        .expect_err("3 MiB is above a 2 MiB cap");
    match &err {
        HostError::EvidenceTooLarge { name, len, cap } => {
            assert_eq!(name, "big.bin");
            assert_eq!(*len, 3 << 20);
            assert_eq!(*cap, 2 << 20);
        }
        other => panic!("expected EvidenceTooLarge, got {other}"),
    }
    assert!(rig.evidence_root_is_empty());
}

#[test]
fn non_json_lines_on_stdout_are_logged_not_fatal() {
    if !python_available() {
        return;
    }
    let rig = Rig::new(FAKE_DRIVER);
    let m = rig.fake_manifest();
    let mut host = rig.spawn(&m);
    let out = host
        .act(
            action(&m, "noise"),
            &json!({}),
            Duration::from_secs(10),
            WIDE,
            &never,
        )
        .expect("the noise is not the answer; the answer follows it");
    assert!(out.ok);
    assert_eq!(out.result["after"], "noise");
    assert!(out.evidence.is_empty());
    let log = rig.log();
    assert!(
        log.contains("Playwright warning"),
        "driver.log should carry the stray line; it has: {log:?}"
    );
}

#[test]
fn a_driver_that_exits_is_reported_dead() {
    if !python_available() {
        return;
    }
    let rig = Rig::new(FAKE_DRIVER);
    let m = rig.fake_manifest();
    let mut host = rig.spawn(&m);
    let err = host
        .act(
            action(&m, "die"),
            &json!({}),
            Duration::from_secs(10),
            WIDE,
            &never,
        )
        .expect_err("a driver that exits answers nothing");
    match &err {
        HostError::Dead(why) => assert!(why.contains("exit code 3"), "{why}"),
        other => panic!("expected Dead, got {other}"),
    }
    assert!(host.try_wait().expect("try_wait").is_some());
    // The host stays usable as a value, but every further act is Dead too.
    let again = host
        .act(
            action(&m, "echo"),
            &json!({}),
            Duration::from_secs(10),
            WIDE,
            &never,
        )
        .expect_err("nothing answers a dead driver");
    assert!(matches!(again, HostError::Dead(_)), "{again}");
}

#[test]
fn an_undeclared_evidence_name_is_refused() {
    if !python_available() {
        return;
    }
    let rig = Rig::new(FAKE_DRIVER);
    let m = rig.fake_manifest();
    let mut host = rig.spawn(&m);
    // The same `echo`, but asked for under a declaration with no evidence.
    let mut bare = action(&m, "echo").clone();
    bare.evidence.clear();
    let err = host
        .act(&bare, &json!({}), Duration::from_secs(10), WIDE, &never)
        .expect_err("note.txt is not declared for this action");
    assert!(
        matches!(&err, HostError::EvidenceNotDeclared(name) if name == "note.txt"),
        "{err}"
    );
    assert!(rig.evidence_root_is_empty());
}

#[test]
fn no_hello_within_the_timeout_is_refused() {
    if !python_available() {
        return;
    }
    let rig = Rig::new(FAKE_DRIVER);
    let silent = vec![
        "python".to_string(),
        "-I".to_string(),
        "-c".to_string(),
        "import time; time.sleep(60)".to_string(),
    ];
    let m = rig.manifest(
        silent,
        json!([{"name": "echo", "reversible": true, "args": {"type": "object"}, "evidence": []}]),
    );
    let started = Instant::now();
    let err = DriverHost::spawn_with_hello_timeout(
        &m,
        &rig.work,
        &rig.profile,
        &rig.evidence,
        Box::new(NoopGovernor::new()),
        Duration::from_secs(1),
    )
    .err()
    .expect("a driver that never says hello is refused");
    let elapsed = started.elapsed();
    assert!(
        matches!(err, HostError::HelloTimeout(d) if d == Duration::from_secs(1)),
        "{err}"
    );
    assert!(
        elapsed < kill_bound(),
        "the refusal came {elapsed:?} after the spawn (bound {:?})",
        kill_bound()
    );
}

#[test]
fn stop_kills_the_tree() {
    if !python_available() {
        return;
    }
    let rig = Rig::new(FAKE_DRIVER);
    let m = rig.fake_manifest();
    let mut host = rig.spawn(&m);
    let since = Instant::now();
    let stop = move || since.elapsed() > Duration::from_millis(200);
    let started = Instant::now();
    let err = host
        .act(
            action(&m, "slow"),
            &json!({}),
            Duration::from_secs(30),
            WIDE,
            &stop,
        )
        .expect_err("a STOP interrupts the action");
    let elapsed = started.elapsed();
    assert!(matches!(err, HostError::Stopped), "{err}");
    assert!(
        elapsed < kill_bound(),
        "the kill came {elapsed:?} after the request (bound {:?})",
        kill_bound()
    );
    assert!(
        host.try_wait().expect("try_wait").is_some(),
        "the driver tree is dead after a STOP"
    );
}

#[test]
fn the_environment_names_the_dirs_the_mode_and_the_hosts() {
    if !python_available() {
        return;
    }
    let env_action =
        json!([{"name": "env", "reversible": true, "args": {"type": "object"}, "evidence": []}]);

    // Headed, a channel, two hosts.
    let rig = Rig::new(ENV_DRIVER);
    let mut m = rig.manifest(rig.command(), env_action.clone());
    m.program.headless = false;
    m.program.browser_channel = Some("chrome".into());
    m.hosts = vec!["app.example.invalid".into(), "cdn.example.invalid".into()];
    m.validate().unwrap();
    let mut host = rig.spawn(&m);
    let out = host
        .act(
            action(&m, "env"),
            &json!({}),
            Duration::from_secs(10),
            WIDE,
            &never,
        )
        .expect("env answers");
    assert!(out.ok);
    let env = &out.result;
    assert_eq!(
        env["VK_DRIVER_PROFILE_DIR"],
        rig.profile.display().to_string()
    );
    assert_eq!(env["VK_DRIVER_WORK_DIR"], rig.work.display().to_string());
    assert_eq!(env["VK_DRIVER_HEADLESS"], "0");
    assert_eq!(env["VK_DRIVER_CHANNEL"], "chrome");
    assert_eq!(
        env["VK_DRIVER_HOSTS"],
        "app.example.invalid,cdn.example.invalid"
    );
    let cwd = env["cwd"].as_str().expect("cwd is a string");
    assert_eq!(
        std::fs::canonicalize(cwd).unwrap(),
        std::fs::canonicalize(&rig.work).unwrap(),
        "the driver runs in the work directory"
    );
    host.shutdown();

    // Headless and no channel: `1` and the empty string.
    let rig = Rig::new(ENV_DRIVER);
    let m = rig.manifest(rig.command(), env_action);
    let mut host = rig.spawn(&m);
    let out = host
        .act(
            action(&m, "env"),
            &json!({}),
            Duration::from_secs(10),
            WIDE,
            &never,
        )
        .expect("env answers");
    assert_eq!(out.result["VK_DRIVER_HEADLESS"], "1");
    assert_eq!(out.result["VK_DRIVER_CHANNEL"], "");
    assert_eq!(out.result["VK_DRIVER_HOSTS"], "example.invalid");
}

/// On Windows the platform governor is a Job Object: the host contains the
/// driver tree in it and reads back that it did, so `governed()` is true, and
/// the contained driver works as before.
#[cfg(windows)]
#[test]
fn tree_is_contained_on_windows() {
    if !python_available() {
        return;
    }
    use vk_harness::confine::for_this_platform;
    let rig = Rig::new(FAKE_DRIVER);
    let m = rig.fake_manifest();
    let governor = for_this_platform(1 << 30, None).expect("a Job Object governor");
    let mut host = DriverHost::spawn(&m, &rig.work, &rig.profile, &rig.evidence, governor)
        .expect("the fake driver says hello under the job");
    assert!(
        host.governed(),
        "the OS read back that the driver is in the job"
    );
    let out = host
        .act(
            action(&m, "echo"),
            &json!({"x": 1}),
            Duration::from_secs(10),
            WIDE,
            &never,
        )
        .expect("echo answers under the job");
    assert!(out.ok);
    assert_eq!(out.result["echo"]["x"], 1);
    host.shutdown();
}
