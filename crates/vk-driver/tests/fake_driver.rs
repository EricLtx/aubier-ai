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
use std::io::{Read, Write};
use std::net::TcpListener;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};
use vk_contracts::driver::{DriverAction, DriverManifest};
use vk_driver::{DriverHost, EvidenceCaps, HostError, ProtocolFault, PROTOCOL};
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
names = ["VK_DRIVER_PROFILE_DIR", "VK_DRIVER_WORK_DIR", "VK_DRIVER_HEADLESS", "VK_DRIVER_CHANNEL", "VK_DRIVER_HOSTS", "VK_DRIVER_APPLICATIONS"]
for line in sys.stdin:
    r = json.loads(line)
    env = {k: os.environ.get(k) for k in names}
    env["cwd"] = os.getcwd()
    say({"id": r["id"], "ok": True, "result": env})
"#;

/// A driver whose one action, `link`, plants a link at
/// `<evidence_dir>/note.txt` — a junction to the directory `args.target` on
/// Windows (`mklink /J`, which needs no privilege), a symlink to it elsewhere
/// — and names it as evidence. `made` says whether the link could be created.
const LINK_DRIVER: &str = r#"import json, os, subprocess, sys
def say(o): sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
say({"hello": {"protocol": 1, "driver": "link", "version": "0", "actions": ["link"]}})
for line in sys.stdin:
    r = json.loads(line); p = os.path.join(r["evidence_dir"], "note.txt"); target = r["args"]["target"]
    if os.name == "nt":
        res = subprocess.run(["cmd", "/c", "mklink", "/J", p, target], capture_output=True, text=True)
        made, err = res.returncode == 0, res.stdout + res.stderr
    else:
        os.symlink(target, p); made, err = True, ""
    ev = [{"name": "note.txt", "mime": "text/plain", "path": p}] if made else []
    say({"id": r["id"], "ok": made, "result": {"made": made, "err": err[-300:]}, "evidence": ev})
"#;

/// A driver whose one action, `fetch`, has a child Python of its own fetch
/// `args.url` — so the socket belongs to a grandchild of the host, as a
/// Playwright driver's sockets belong to its browser.
const NET_DRIVER: &str = r#"import json, subprocess, sys
def say(o): sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
say({"hello": {"protocol": 1, "driver": "net", "version": "0", "actions": ["fetch"]}})
for line in sys.stdin:
    r = json.loads(line)
    code = "import urllib.request; urllib.request.urlopen(%r).read()" % r["args"]["url"]
    res = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)
    say({"id": r["id"], "ok": res.returncode == 0, "result": {"rc": res.returncode, "err": res.stderr[-300:]}})
"#;

/// A driver that breaks protocol on request: `babble` answers under the
/// request's id but without `ok`, its `result` carrying `args.answer` — the
/// site's words, as a real driver's answer would; `stranger` answers under
/// another request's id.
const BABBLE_DRIVER: &str = r#"import json, sys
def say(o): sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
say({"hello": {"protocol": 1, "driver": "babble", "version": "0", "actions": ["babble", "stranger"]}})
for line in sys.stdin:
    r = json.loads(line)
    if r["action"] == "babble": say({"id": r["id"], "result": {"answer": r["args"]["answer"]}})
    else: say({"id": r["id"] + 100, "ok": True})
"#;

/// A driver with two actions: `echo`, and `spawn_child`, which starts
/// `cmd /c ping -n 30 127.0.0.1 >nul` — a child of its own that lives half a
/// minute, its stdio not the driver's — and answers its pid.
const CHILD_DRIVER: &str = r#"import json, subprocess, sys
def say(o): sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
say({"hello": {"protocol": 1, "driver": "child", "version": "0", "actions": ["echo", "spawn_child"]}})
for line in sys.stdin:
    r = json.loads(line)
    if r["action"] == "echo":
        say({"id": r["id"], "ok": True, "result": {"echo": r["args"]}})
    else:
        child = subprocess.Popen("cmd /c ping -n 30 127.0.0.1 >nul", stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        say({"id": r["id"], "ok": True, "result": {"pid": child.pid}})
"#;

/// A driver that refuses every action, `wordy` with the site's words as its
/// `reason` and `terse` with a word of the protocol's set — the two ways an
/// `ok: false` can arrive at the host.
const WORDY_DRIVER: &str = r#"import json, sys
def say(o): sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
say({"hello": {"protocol": 1, "driver": "wordy", "version": "0", "actions": ["wordy", "terse"]}})
for line in sys.stdin:
    r = json.loads(line)
    if r["action"] == "wordy": say({"id": r["id"], "ok": False, "reason": "the site said: " + r["args"]["marker"], "detail": "page text"})
    else: say({"id": r["id"], "ok": False, "reason": "precondition", "detail": "no session"})
"#;

/// A driver whose first line is JSON but not a hello, and which then waits.
const NO_HELLO_DRIVER: &str = r#"import sys, time
sys.stdout.write('{"greeting": "hi"}\n'); sys.stdout.flush()
time.sleep(30)
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

/// The interpreter the fake drivers run under: `python` on Windows (where
/// `python3` may be the Store's stub), `python3` elsewhere (PEP 394: a Unix
/// need not have a `python`, and WSL's Ubuntu has none).
const PYTHON: &str = if cfg!(windows) { "python" } else { "python3" };

/// Is a `python` on PATH? When not, say so — the test is then skipped out
/// loud, never failed and never silently green.
fn python_available() -> bool {
    match Command::new(PYTHON)
        .arg("--version")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
    {
        Ok(status) if status.success() => true,
        other => {
            eprintln!("no {PYTHON} on PATH; skipping ({other:?})");
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
            PYTHON.into(),
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
    // The refusal names the path the driver gave, not the declared name: the
    // name is fine, the path is what points elsewhere (ruling R9).
    assert!(
        matches!(&err, HostError::EvidenceEscape(path) if path.ends_with("secret.txt")),
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
        PYTHON.to_string(),
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

/// Task 1 re-review (and its review round 1): the tree is signalled once, by
/// whatever saw the driver die first — a deadline kill, the reap after a
/// driver exited, a `try_wait` that saw it gone — and never again. Once the
/// leader is reaped its id is free for reuse, so a later signal (the drop's,
/// or a second act's on a dead host) could reach some other group (the
/// harness's M18 rule). The host's tree-signal count, read after the drop,
/// is the proof: one for the kill or the sweep, nothing more.
#[test]
fn a_tree_is_swept_once_and_never_signalled_again() {
    if !python_available() {
        return;
    }
    let rig = Rig::new(FAKE_DRIVER);
    let m = rig.fake_manifest();

    // A deadline kill: one signal, and none from a second act or the drop.
    let mut host = rig.spawn(&m);
    let signals = host.tree_signals();
    host.act(
        action(&m, "echo"),
        &json!({}),
        Duration::from_secs(10),
        WIDE,
        &never,
    )
    .expect("a live driver answers");
    assert!(!host.swept(), "a driver still running has not been swept");
    assert_eq!(signals.count(), 0, "nothing has signalled a live driver");
    let err = host
        .act(
            action(&m, "slow"),
            &json!({}),
            Duration::from_secs(1),
            WIDE,
            &never,
        )
        .expect_err("a 30 s sleep does not answer in 1 s");
    assert!(matches!(err, HostError::Timeout), "{err}");
    assert!(host.swept(), "the deadline kill swept the tree");
    assert_eq!(
        signals.count(),
        1,
        "the deadline kill signalled the tree once"
    );
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
    assert_eq!(
        signals.count(),
        1,
        "a second act on the dead host signals nothing"
    );
    drop(host);
    assert_eq!(signals.count(), 1, "dropping a swept host signals nothing");

    // A driver that exits on its own under an act: the reap sweeps, once. The
    // sweep signals on Unix, and on Windows only an ungoverned tree (the
    // rig's Noop governor); a governed one is the Job Object's.
    let mut host = rig.spawn(&m);
    let signals = host.tree_signals();
    let sweeps = u32::from(cfg!(unix) || !host.governed());
    let err = host
        .act(
            action(&m, "die"),
            &json!({}),
            Duration::from_secs(10),
            WIDE,
            &never,
        )
        .expect_err("a driver that exits answers nothing");
    assert!(matches!(err, HostError::Dead(_)), "{err}");
    assert!(host.swept(), "the reap swept the tree");
    assert_eq!(signals.count(), sweeps, "the reap swept once");
    drop(host);
    assert_eq!(
        signals.count(),
        sweeps,
        "dropping a swept host signals nothing"
    );

    // A driver killed while idle: the `try_wait` that sees it gone sweeps, once.
    let mut host = rig.spawn(&m);
    let signals = host.tree_signals();
    vk_driver::kill_pid_tree(host.pid());
    let until = Instant::now() + kill_bound();
    while host.try_wait().expect("try_wait").is_none() {
        assert!(Instant::now() < until, "the killed driver never exited");
        std::thread::sleep(Duration::from_millis(20));
    }
    assert!(
        host.swept(),
        "the try_wait that saw the exit swept the tree"
    );
    assert_eq!(signals.count(), sweeps, "the try_wait swept once");
    assert!(host.try_wait().expect("try_wait").is_some());
    drop(host);
    assert_eq!(
        signals.count(),
        sweeps,
        "dropping a swept host signals nothing"
    );
}

/// Ruling R13: the daemon kills the tree of a host an act holds, by the pid
/// the host reports, from another thread — what an unmount does. The act
/// comes back `Dead`, well within the thirty seconds `slow` sleeps, and the
/// driver is gone.
#[test]
fn kill_pid_tree_ends_an_act_from_outside() {
    if !python_available() {
        return;
    }
    let rig = Rig::new(FAKE_DRIVER);
    let m = rig.fake_manifest();
    let mut host = rig.spawn(&m);
    let pid = host.pid();
    let killer = std::thread::spawn(move || {
        std::thread::sleep(Duration::from_millis(500));
        vk_driver::kill_pid_tree(pid);
    });
    let started = Instant::now();
    let err = host
        .act(
            action(&m, "slow"),
            &json!({}),
            Duration::from_secs(30),
            WIDE,
            &never,
        )
        .expect_err("the kill ends the action");
    let elapsed = started.elapsed();
    killer.join().expect("the killer thread");
    assert!(matches!(err, HostError::Dead(_)), "{err}");
    assert!(
        elapsed < kill_bound(),
        "the act came back {elapsed:?} after the request (bound {:?})",
        kill_bound()
    );
    assert!(
        host.try_wait().expect("try_wait").is_some(),
        "the driver is dead after the kill"
    );
}

/// A driver that answers `echo` and, once its stdin closes — the protocol's
/// end — writes `eof.txt` into its work directory and exits; with `stubborn`
/// on its command line it sleeps a minute instead of exiting.
const EOF_DRIVER: &str = r#"import json, os, sys, time
def say(o): sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
say({"hello": {"protocol": 1, "driver": "eof", "version": "0", "actions": ["echo"]}})
for line in sys.stdin:
    r = json.loads(line)
    say({"id": r["id"], "ok": True, "result": {"echo": r["args"]}})
open(os.path.join(os.environ["VK_DRIVER_WORK_DIR"], "eof.txt"), "w").write("eof")
if "stubborn" in sys.argv: time.sleep(60)
"#;

/// Ruling L-P49: the host stops a running driver the way the protocol ends —
/// its stdin closed — and gives it five seconds to exit on its own (a learned
/// driver quits the Office instance it started on its way out, L-P46) before
/// the tree is killed. The driver that exits at EOF wrote its marker, which a
/// process killed first could not have; the one that stays on is killed once
/// the grace is over, and not before.
#[test]
fn a_driver_is_stopped_by_closing_its_stdin_before_any_kill() {
    if !python_available() {
        return;
    }
    let rig = Rig::new(EOF_DRIVER);
    let echo =
        json!([{"name": "echo", "reversible": true, "args": {"type": "object"}, "evidence": []}]);
    let marker = rig.work.join("eof.txt");

    let m = rig.manifest(rig.command(), echo.clone());
    let mut host = rig.spawn(&m);
    host.act(
        action(&m, "echo"),
        &json!({}),
        Duration::from_secs(10),
        WIDE,
        &never,
    )
    .expect("a live driver answers");
    assert!(
        !marker.exists(),
        "a running driver has not seen its input end"
    );
    drop(host);
    assert!(
        marker.is_file(),
        "the driver read EOF on its stdin and ran its own end before any kill"
    );

    std::fs::remove_file(&marker).expect("remove the marker");
    let mut command = rig.command();
    command.push("stubborn".into());
    let m = rig.manifest(command, echo);
    let mut host = rig.spawn(&m);
    host.act(
        action(&m, "echo"),
        &json!({}),
        Duration::from_secs(10),
        WIDE,
        &never,
    )
    .expect("a live driver answers");
    let pid = host.pid();
    let started = Instant::now();
    drop(host);
    let waited = started.elapsed();
    assert!(
        marker.is_file(),
        "the stubborn driver saw its input end too"
    );
    assert!(
        waited >= Duration::from_millis(4500),
        "a driver still running after EOF is given its grace before the kill: {waited:?}"
    );
    assert!(
        waited < Duration::from_secs(5) + kill_bound(),
        "and then killed: the drop came back after {waited:?}"
    );
    if cfg!(windows) {
        assert!(
            !windows_process_alive(pid),
            "the stubborn driver {pid} is gone"
        );
    }
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
    let m = rig.manifest(rig.command(), env_action.clone());
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
    assert_eq!(out.result["VK_DRIVER_APPLICATIONS"], "");

    // A com driver: no host, its applications comma-joined.
    let rig = Rig::new(ENV_DRIVER);
    let mut m = rig.manifest(rig.command(), env_action.clone());
    m.kind = vk_contracts::driver::DriverKind::Com;
    m.hosts.clear();
    m.applications = vec!["WINWORD.EXE".into(), "POWERPNT.EXE".into()];
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
    assert_eq!(
        out.result["VK_DRIVER_APPLICATIONS"],
        "WINWORD.EXE,POWERPNT.EXE"
    );
    assert_eq!(out.result["VK_DRIVER_HOSTS"], "");
    host.shutdown();
}

/// Re-review of the final fix wave: the driver's own `ok: false` reason is
/// pinned to the protocol's five words before the kernel sees it. A reason
/// carrying the site's words becomes `internal`, with the driver's word moved
/// into the detail for the log; a word of the set passes as given.
#[test]
fn a_drivers_reason_outside_the_protocols_set_becomes_internal() {
    if !python_available() {
        return;
    }
    let rig = Rig::new(WORDY_DRIVER);
    let actions = json!([
        {"name": "wordy", "reversible": true, "args": {"type": "object"}, "evidence": []},
        {"name": "terse", "reversible": true, "args": {"type": "object"}, "evidence": []},
    ]);
    let m = rig.manifest(rig.command(), actions);
    let marker = "words-from-the-site ".repeat(15);
    let mut host = rig.spawn(&m);

    let out = host
        .act(
            action(&m, "wordy"),
            &json!({"marker": marker}),
            Duration::from_secs(10),
            WIDE,
            &never,
        )
        .expect("a refusal is an outcome, not a host error");
    assert!(!out.ok);
    assert_eq!(out.reason.as_deref(), Some("internal"));
    let detail = out.detail.expect("the driver's word is kept, for the log");
    assert!(detail.contains("words-from-the-site"), "{detail}");
    assert!(detail.contains("page text"), "{detail}");
    assert!(vk_driver::RESPONSE_REASONS.contains(&"internal"));

    let out = host
        .act(
            action(&m, "terse"),
            &json!({}),
            Duration::from_secs(10),
            WIDE,
            &never,
        )
        .expect("a refusal is an outcome, not a host error");
    assert_eq!(out.reason.as_deref(), Some("precondition"));
    assert_eq!(out.detail.as_deref(), Some("no session"));
}

/// Final review I1: every protocol failure says which kind it is, so the
/// kernel can word the ledger's `reason` from the kind alone; the detail —
/// which may quote the driver's output, and the site's words with it — stays
/// in the message, for the log. A response without `ok` is a malformed
/// response, one under another id a foreign id, a program that is not there
/// was not started, a first line that is not a hello is a bad hello.
#[test]
fn protocol_faults_are_typed_and_the_detail_stays_in_the_message() {
    if !python_available() {
        return;
    }
    let rig = Rig::new(BABBLE_DRIVER);
    let actions = json!([
        {"name": "babble", "reversible": true, "args": {"type": "object"}, "evidence": []},
        {"name": "stranger", "reversible": true, "args": {"type": "object"}, "evidence": []},
    ]);
    let m = rig.manifest(rig.command(), actions.clone());
    let marker = "words-from-the-site ".repeat(15);

    let mut host = rig.spawn(&m);
    let err = host
        .act(
            action(&m, "babble"),
            &json!({"answer": marker}),
            Duration::from_secs(10),
            WIDE,
            &never,
        )
        .expect_err("a response without `ok` is refused");
    match &err {
        HostError::Protocol {
            fault: ProtocolFault::MalformedResponse,
            detail,
        } => assert!(detail.contains("words-from-the-site"), "{detail}"),
        other => panic!("not a malformed response: {other}"),
    }

    let mut host = rig.spawn(&m);
    let err = host
        .act(
            action(&m, "stranger"),
            &json!({}),
            Duration::from_secs(10),
            WIDE,
            &never,
        )
        .expect_err("a response under another id is refused");
    assert!(
        matches!(
            err,
            HostError::Protocol {
                fault: ProtocolFault::ForeignId,
                ..
            }
        ),
        "{err}"
    );

    let refused = |rig: &Rig, m: &DriverManifest| match DriverHost::spawn(
        m,
        &rig.work,
        &rig.profile,
        &rig.evidence,
        Box::new(NoopGovernor::new()),
    ) {
        Ok(_) => panic!("the spawn was accepted"),
        Err(e) => e,
    };
    let missing = rig.manifest(vec!["vk-no-such-program".into()], actions.clone());
    let err = refused(&rig, &missing);
    assert!(
        matches!(
            err,
            HostError::Protocol {
                fault: ProtocolFault::NotStarted,
                ..
            }
        ),
        "{err}"
    );

    let silent = Rig::new(NO_HELLO_DRIVER);
    let m = silent.manifest(silent.command(), actions);
    let err = refused(&silent, &m);
    assert!(
        matches!(
            err,
            HostError::Protocol {
                fault: ProtocolFault::BadHello,
                ..
            }
        ),
        "{err}"
    );
}

/// Is the interpreter the fake drivers run under a packaged app — the
/// Microsoft Store build of Python, whose `sys.executable` lives under
/// `WindowsApps`? Windows starts such a process's non-packaged children
/// outside its job, and outside any Job the host made.
fn interpreter_is_packaged() -> bool {
    Command::new(PYTHON)
        .args(["-I", "-c", "import sys; print(sys.executable)"])
        .stdin(Stdio::null())
        .output()
        .map(|o| String::from_utf8_lossy(&o.stdout).contains("\\WindowsApps\\"))
        .unwrap_or(false)
}

/// Is the Windows process `pid` running? `tasklist` filtered on the id, in
/// CSV: a row names the id in quotes, and nothing else does.
fn windows_process_alive(pid: u32) -> bool {
    Command::new("tasklist")
        .args(["/FI", &format!("PID eq {pid}"), "/NH", "/FO", "CSV"])
        .stdin(Stdio::null())
        .output()
        .map(|o| String::from_utf8_lossy(&o.stdout).contains(&format!("\"{pid}\"")))
        .unwrap_or(false)
}

/// On Windows the platform governor is a Job Object: the host contains the
/// driver tree in it and reads back that it did, so `governed()` is true, and
/// the contained driver works as before. Final review I5: the claim is the
/// *tree*, so the driver starts a child of its own (`spawn_child`: `cmd /c
/// ping -n 30 127.0.0.1 >nul`, half a minute of life) and the test holds the
/// TCB to it — the child's id is in the Job's own process list while it
/// runs, and the child is gone within ten seconds of the host's shutdown,
/// long before its thirty seconds are up. Elsewhere there is no Job Object,
/// and the test says so and returns. Under a Microsoft Store Python the
/// child is *not* in the Job — Windows starts a packaged app's non-packaged
/// children outside its job — so the membership is not asserted there: the
/// test prints the gap, which the TCB states, and checks the kill alone.
#[test]
fn tree_is_contained_on_windows() {
    if !cfg!(windows) {
        eprintln!("no Job Object on this platform, so no tree to contain; skipping");
        return;
    }
    if !python_available() {
        return;
    }
    use vk_harness::confine::for_this_platform;
    let rig = Rig::new(CHILD_DRIVER);
    let m = rig.manifest(
        rig.command(),
        json!([
            {"name": "echo", "reversible": true, "args": {"type": "object"}, "evidence": []},
            {"name": "spawn_child", "reversible": true, "args": {"type": "object"}, "evidence": []},
        ]),
    );
    let governor = for_this_platform(1 << 30, None).expect("a Job Object governor");
    let mut host = DriverHost::spawn(&m, &rig.work, &rig.profile, &rig.evidence, governor)
        .expect("the child driver says hello under the job");
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
    // `hosts_seen` is the list of `ip:port` endpoints seen; a driver that
    // talks to nothing leaves it empty, and nothing in it is anything else.
    assert!(
        out.hosts_seen.iter().all(|h| h
            .rsplit_once(':')
            .is_some_and(|(_, port)| port.parse::<u16>().is_ok())),
        "hosts_seen carries `ip:port` entries: {:?}",
        out.hosts_seen
    );

    // A grandchild of the host: in the job while it runs.
    let out = host
        .act(
            action(&m, "spawn_child"),
            &json!({}),
            Duration::from_secs(10),
            WIDE,
            &never,
        )
        .expect("spawn_child answers under the job");
    let child = out.result["pid"]
        .as_u64()
        .and_then(|p| u32::try_from(p).ok())
        .unwrap_or_else(|| panic!("the child's pid: {:?}", out.result));
    assert!(windows_process_alive(child), "the child {child} runs");
    let pids = host.tree_pids();
    assert!(
        pids.contains(&host.pid()),
        "the driver is in the job: {pids:?}"
    );
    if interpreter_is_packaged() {
        // Measured on the founder's node (final fix wave): a Microsoft Store
        // Python is a packaged app, and Windows starts a packaged process's
        // *non-packaged* children (cmd, ping — and Playwright's node and
        // Chrome) outside its job, so outside this Job too. The containment
        // claim does not hold there, the TCB says so, and only the
        // parent-chain kill below is checked.
        eprintln!(
            "KNOWN GAP: {PYTHON} is the Microsoft Store build; its non-packaged child {child} \
             is outside the Job ({pids:?}). The Job holds the tree only under a python.org \
             interpreter (CI). Checking the parent-chain kill alone."
        );
    } else {
        assert!(
            pids.contains(&child),
            "the child {child} is in the job's process list: {pids:?}"
        );
    }

    // And gone with the tree, well inside its thirty seconds.
    let packaged = interpreter_is_packaged();
    host.shutdown();
    if packaged {
        // Ruling L-P49: the host now closes the driver's stdin first, and this
        // driver exits on its own at EOF, so no parent-chain kill can walk to
        // its child any more — and the Job, under a packaged interpreter, does
        // not hold it. The gap above, which R27 keeps out of every driver
        // virtualenv; the child is ended here so the test leaves nothing.
        std::thread::sleep(Duration::from_millis(500));
        if windows_process_alive(child) {
            eprintln!(
                "KNOWN GAP: under a Microsoft Store {PYTHON}, the child {child} of a driver that \
                 exits on its own at EOF outlives it (no Job holds it, no parent to walk from); \
                 ending it by hand"
            );
            vk_driver::kill_pid_tree(child);
        }
        return;
    }
    let deadline = Instant::now() + Duration::from_secs(10);
    while windows_process_alive(child) {
        assert!(
            Instant::now() < deadline,
            "the child {child} outlived the host's shutdown by ten seconds"
        );
        std::thread::sleep(Duration::from_millis(100));
    }
}

/// A link planted at the evidence name — a junction on Windows, a symlink
/// elsewhere — is refused as an escape although its path is under the
/// directory, and removing the directory afterwards does not reach through it.
#[test]
fn a_link_at_the_evidence_name_is_refused() {
    if !python_available() {
        return;
    }
    let rig = Rig::new(LINK_DRIVER);
    // What the link points at: a directory with a file in it. A junction needs
    // a directory target; the symlink takes the same one.
    let outside = rig.root.join("outside");
    std::fs::create_dir_all(&outside).unwrap();
    std::fs::write(outside.join("keep.txt"), b"must survive").unwrap();
    let m = rig.manifest(
        rig.command(),
        json!([{"name": "link", "reversible": true, "args": {"type": "object"}, "evidence": ["note.txt"]}]),
    );
    let mut host = rig.spawn(&m);
    let outcome = host.act(
        action(&m, "link"),
        &json!({"target": outside.display().to_string()}),
        Duration::from_secs(10),
        WIDE,
        &never,
    );
    match outcome {
        Ok(out) if out.result["made"] == false => {
            eprintln!(
                "the driver could not create the link; skipping ({})",
                out.result["err"]
            );
            return;
        }
        Ok(out) => panic!("a link as evidence was accepted: {out:?}"),
        Err(err) => assert!(
            matches!(&err, HostError::EvidenceEscape(name) if name == "note.txt"),
            "{err}"
        ),
    }
    assert!(
        outside.join("keep.txt").exists(),
        "removing the evidence directory must not reach through the link"
    );
    assert!(rig.evidence_root_is_empty());
}

/// The egress the host records is the whole tree's, not the leader's: a fake
/// driver whose child Python fetches from a local server the test runs shows
/// that server's `127.0.0.1:<port>` in `hosts_seen`. The pids come from the
/// Job Object (ruling R8), so this proves itself on Windows only; elsewhere
/// the host sees the leader alone and the test says so and returns. The
/// server holds the connection for 1.5 s: the sampler looks every 500 ms, and
/// a server that answered at once would be gone between two looks.
#[test]
fn egress_of_the_whole_tree_is_seen_under_the_job() {
    if !cfg!(windows) {
        eprintln!("no process governor on this platform, so the tree's pids are unknown; skipping");
        return;
    }
    if !python_available() {
        return;
    }
    let listener = TcpListener::bind(("127.0.0.1", 0)).expect("bind a loopback port");
    let port = listener.local_addr().unwrap().port();
    let server = std::thread::spawn(move || {
        let Ok((mut socket, _)) = listener.accept() else {
            return;
        };
        let mut head = [0u8; 1024];
        let _ = socket.read(&mut head);
        std::thread::sleep(Duration::from_millis(1500));
        let _ = socket
            .write_all(b"HTTP/1.0 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok");
    });
    let rig = Rig::new(NET_DRIVER);
    let m = rig.manifest(
        rig.command(),
        json!([{"name": "fetch", "reversible": true, "args": {"type": "object"}, "evidence": []}]),
    );
    let governor = vk_harness::confine::for_this_platform(1 << 30, None).expect("a governor");
    let mut host = DriverHost::spawn(&m, &rig.work, &rig.profile, &rig.evidence, governor)
        .expect("the net driver says hello under the job");
    assert!(host.governed());
    let out = host
        .act(
            action(&m, "fetch"),
            &json!({"url": format!("http://127.0.0.1:{port}/")}),
            Duration::from_secs(20),
            WIDE,
            &never,
        )
        .expect("fetch answers");
    assert!(out.ok, "the grandchild's fetch failed: {:?}", out.result);
    server.join().expect("the server thread");
    assert!(
        out.hosts_seen.iter().any(|h| h.contains("127.0.0.1")),
        "the grandchild's connection is in hosts_seen: {:?}",
        out.hosts_seen
    );
    host.shutdown();
}
