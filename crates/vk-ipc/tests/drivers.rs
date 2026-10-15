//! The driver syscalls end to end (pilot spec §4.2–4.4): a real kernel behind a
//! real local endpoint, the daemon's [`DriverPool`] spawning a real driver
//! process — Task 1's scripted fake driver, run with `python -I` — and the
//! client calling `driver.*` exactly as `vk` and `vk-mcp` do.
//!
//! What is pinned: mounting is a human act refused to a machine principal
//! before anything is spawned, and a human mount spawns the driver and lists it
//! under `/drivers`; a manifest whose files do not hash to its `files_sha256`
//! is refused and nothing is mounted; `driver.act` runs outside the kernel
//! lock against a token the kernel minted, echoes the driver's result, puts
//! `driver.acted` on the ledger, and re-spawns a host the pool no longer has
//! (a daemon restart) from the row; an evidence refusal keeps the host, a
//! driver that dies is dropped and degraded and a remount brings it back; an
//! act without a live token is refused and spawns nothing; unmounting drops
//! the host, and kills the process of an act still in flight (Ruling R13); a
//! mount racing a lazy re-spawn starts one process, not two; the script the
//! manifest names is held to its root. Task 7: a process killed between two
//! acts is re-spawned at the next, one killed under an act degrades the
//! driver until umount + mount; two acts on two drivers overlap; and an idle
//! process is adopted only for the manifest identity it was spawned for. The
//! final fix wave: a malformed response is recorded by its closed word and
//! detailed in `driver.log` (I1); the memory cap is the manifest's, or 6 GiB
//! for a browser (I2).
//!
//! When no `python` is on PATH a test says so on stderr and returns; it never
//! fails silently and never passes vacuously without saying why.
use serde_json::{json, Value};
use std::path::Path;
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use vk_contracts::labels::{Clearance, Scope};
use vk_contracts::principal::{HumanKey, Principal, SoftwareHumanKey};
use vk_ipc::client::{CallError, Client};
use vk_ipc::drivers::DriverPool;
use vk_ipc::PresenceProof;

type Shared = Arc<Mutex<vk_kernel::RealKernel>>;

/// The scripted driver of Task 1's brief, verbatim (`crates/vk-driver/tests/
/// fake_driver.rs`): copied, because one test binary does not import another.
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

/// The driver's own directory, apart from the state directory: the script and
/// nothing else, so `hash_dir` of it is the hash of exactly that script.
fn driver_dir() -> tempfile::TempDir {
    driver_dir_of(FAKE_DRIVER)
}

fn driver_dir_of(script: &str) -> tempfile::TempDir {
    let dir = tempfile::tempdir().expect("a temporary directory");
    std::fs::write(dir.path().join("fake_driver.py"), script).expect("write the driver");
    dir
}

/// Put in front of the fake driver: each start appends a line to
/// `<work dir>/starts.txt`, then waits a second before the hello — long
/// enough for a second request to arrive while the first spawn is under way.
const COUNTING: &str = "import os, time\n\
open(os.path.join(os.environ[\"VK_DRIVER_WORK_DIR\"], \"starts.txt\"), \"a\").write(\"start\\n\")\n\
time.sleep(1)\n";

/// The fake driver under a tag: each start appends the tag to
/// `<work dir>/starts.txt`, and `echo` answers it beside the args, so a test
/// can tell which manifest's process ran an act.
fn tagged(tag: &str) -> String {
    let echo = r#""result": {"echo": r["args"]}"#;
    assert!(
        FAKE_DRIVER.contains(echo),
        "the fake driver's echo line moved"
    );
    format!(
        "TAG = {tag:?}\n\
         import os\n\
         open(os.path.join(os.environ[\"VK_DRIVER_WORK_DIR\"], \"starts.txt\"), \"a\").write(TAG + \"\\n\")\n\
         {}",
        FAKE_DRIVER.replace(echo, r#""result": {"echo": r["args"], "tag": TAG}"#)
    )
}

/// A driver with one action, `meet`: it leaves a mark named `args.me` in
/// `args.dir`, then waits up to ten seconds for the mark named `args.other`
/// and answers whether it came. Two of them meet only if both act at once.
const MEET_DRIVER: &str = r#"import json, os, sys, time
def say(o): sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
say({"hello": {"protocol": 1, "driver": "meet", "version": "0", "actions": ["meet"]}})
for line in sys.stdin:
    r = json.loads(line); a = r["args"]
    open(os.path.join(a["dir"], a["me"]), "w").close()
    other, until = os.path.join(a["dir"], a["other"]), time.time() + 10
    while not os.path.exists(other) and time.time() < until: time.sleep(0.05)
    say({"id": r["id"], "ok": True, "result": {"met": os.path.exists(other)}})
"#;

/// A driver whose one action, `babble`, answers under the request's id but
/// without `ok`, its `result.answer` carrying `args.answer`: the site's words
/// in a response the host cannot read.
const BABBLE_DRIVER: &str = r#"import json, sys
def say(o): sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
say({"hello": {"protocol": 1, "driver": "babble", "version": "0", "actions": ["babble"]}})
for line in sys.stdin:
    r = json.loads(line)
    say({"id": r["id"], "result": {"answer": r["args"]["answer"]}})
"#;

/// Is the process with this id still running? A zombie is not: it has
/// exited and only waits for its parent to reap it ([`zombie`]).
fn process_alive(pid: u32) -> bool {
    #[cfg(windows)]
    {
        let out = Command::new("tasklist")
            .args(["/FI", &format!("PID eq {pid}"), "/NH"])
            .output();
        match out {
            Ok(o) => String::from_utf8_lossy(&o.stdout).contains(&pid.to_string()),
            Err(_) => false,
        }
    }
    #[cfg(unix)]
    {
        // SAFETY: `kill(pid, 0)` sends no signal; it asks whether the process
        // exists.
        let exists = unsafe { libc::kill(pid as libc::pid_t, 0) == 0 };
        exists && !zombie(pid)
    }
}

/// Has `pid` exited, and only waits to be reaped? On Unix a killed driver
/// whose host is still in the pool stays a zombie until the host reaps it —
/// at the next act's `ensure` — and `kill(pid, 0)` still finds it; to these
/// tests it is gone. Linux reads the state from `/proc/<pid>/stat`; where
/// there is no `/proc` (macOS), `ps` prints it.
#[cfg(unix)]
fn zombie(pid: u32) -> bool {
    if let Ok(stat) = std::fs::read_to_string(format!("/proc/{pid}/stat")) {
        // `pid (comm) state …`: the state follows the last `)`, because the
        // command name may hold anything, parentheses included.
        return stat
            .rsplit_once(')')
            .and_then(|(_, rest)| rest.trim_start().chars().next())
            .is_some_and(|state| matches!(state, 'Z' | 'X'));
    }
    Command::new("ps")
        .args(["-o", "stat=", "-p", &pid.to_string()])
        .output()
        .map(|o| {
            String::from_utf8_lossy(&o.stdout)
                .trim_start()
                .starts_with('Z')
        })
        .unwrap_or(false)
}

/// Poll `cond` every 50 ms for up to ten seconds.
async fn eventually(what: &str, mut cond: impl FnMut() -> bool) {
    let deadline = Instant::now() + Duration::from_secs(10);
    while !cond() {
        assert!(Instant::now() < deadline, "{what}");
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
}

/// A manifest over the fake driver with four of its actions: `echo`, which
/// returns `note.txt`; `big`, whose 3 MiB `big.bin` is over the per-file cap;
/// `die`, which exits mid-request; and `slow`, which sleeps thirty seconds.
/// The command is relative, as a manifest's is (Ruling R6): the daemon
/// resolves it against the `root` the mount names.
fn manifest(files_sha256: &str) -> Value {
    let action = |name: &str, evidence: &[&str]| {
        json!({
            "name": name,
            "reversible": true,
            "args": {"type": "object"},
            "evidence": evidence,
            "to_register": false
        })
    };
    json!({
        "name": "fake",
        "kind": "browser",
        "program": {
            "runtime": "python",
            "command": [PYTHON, "-I", "fake_driver.py"],
            "files_sha256": files_sha256,
            "browser_channel": null,
            "headless": true
        },
        "hosts": ["example.invalid"],
        "actions": [
            action("echo", &["note.txt"]),
            action("big", &["big.bin"]),
            action("die", &[]),
            action("slow", &[]),
        ],
        "min_interval_ms": 0,
        "max_action_ms": 60_000,
        "clearance": {"max_scope": "business", "third_party_allowed": true},
        "governed": false
    })
}

/// `driver.mount`'s params for the driver in `dir`, with its true hash.
fn mount_params(dir: &Path) -> Value {
    let hash = vk_contracts::driver::hash_dir(dir).expect("hash the driver directory");
    json!({ "manifest": manifest(&hash), "root": dir.display().to_string() })
}

/// `driver.mount`'s params for the [`MEET_DRIVER`] in `dir`, mounted as `name`.
fn meet_params(dir: &Path, name: &str) -> Value {
    let mut params = mount_params(dir);
    params["manifest"]["name"] = json!(name);
    params["manifest"]["actions"] = json!([{
        "name": "meet",
        "reversible": true,
        "args": {"type": "object"},
        "evidence": [],
        "to_register": false
    }]);
    params
}

/// One daemon: a kernel over its own state directory with a device enrolled,
/// serving on a fresh endpoint with a pool this test can look into.
struct Node {
    d: tempfile::TempDir,
    k: Shared,
    pool: Arc<DriverPool>,
    device: SoftwareHumanKey,
    server: tokio::task::JoinHandle<anyhow::Result<()>>,
    c: Client,
    /// For a second connection: one client has one call in flight at a time.
    endpoint: vk_ipc::transport::Endpoint,
}

impl Node {
    async fn start() -> Node {
        let d = tempfile::tempdir().unwrap();
        vk_contracts::testing::guard_state_dir(d.path());
        let k: Shared = Arc::new(Mutex::new(
            vk_kernel::RealKernel::open(
                d.path(),
                vk_store::keys::KeySource::File(d.path().join("m.key")),
                "n1",
            )
            .unwrap(),
        ));
        let device = SoftwareHumanKey::generate("laptop");
        {
            use vk_contracts::testing::KernelTestHooks;
            k.lock()
                .unwrap()
                .enroll_device("laptop", device.verifying_key_bytes());
        }
        let pool = DriverPool::new();
        let endpoint = vk_ipc::transport::test_endpoint();
        let listener = vk_ipc::transport::os::bind(&endpoint).await.unwrap();
        let config = vk_ipc::server::ServerConfig {
            drivers: pool.clone(),
            ..vk_ipc::server::ServerConfig::new(endpoint.0.clone())
        };
        let server = tokio::spawn(vk_ipc::server::serve_on(k.clone(), listener, config));
        let c = Client::connect(&endpoint).await.unwrap();
        Node {
            d,
            k,
            pool,
            device,
            server,
            c,
            endpoint,
        }
    }

    /// One fresh challenge, signed by the enrolled device: a proof good for
    /// exactly one request.
    async fn proof(&self) -> PresenceProof {
        let ch = self
            .c
            .call("presence.challenge", json!({}), None)
            .await
            .unwrap();
        PresenceProof::sign(&self.device, ch["nonce"].as_str().unwrap())
    }

    async fn mount(&self, dir: &Path) -> Value {
        self.mount_with(mount_params(dir)).await
    }

    async fn mount_with(&self, params: Value) -> Value {
        let proof = self.proof().await;
        self.c
            .call("driver.mount", params, Some(proof))
            .await
            .expect("a human mounts the driver")
    }

    /// A recipe-shaped task whose first step is a harness step declaring the
    /// driver, created over the pipe, and the lease token the kernel mints
    /// when that step is launched — what `harness.run` hands `vk-mcp`.
    async fn token(&self) -> String {
        self.token_for(&["fake"]).await
    }

    /// [`token`](Self::token) for a step declaring `drivers`.
    async fn token_for(&self, drivers: &[&str]) -> String {
        let task = self
            .c
            .call(
                "task.create",
                json!({
                    "goal": "recette",
                    "artefact_type": "report",
                    "constraints": ["fictional data only"],
                    "evidence": ["cases: N0-1"],
                    "steps": [
                        {"kind": "harness", "name": "claude-code", "drivers": drivers},
                        {"kind": "approve"}
                    ]
                }),
                None,
            )
            .await
            .unwrap();
        self.k
            .lock()
            .unwrap()
            .harness_launch(
                &machine(),
                task["id"].as_str().unwrap(),
                "claude-code",
                60_000,
            )
            .unwrap()
            .token
    }

    async fn state_of(&self, name: &str) -> Value {
        let row = self
            .c
            .call("driver.show", json!({"name": name}), None)
            .await
            .unwrap();
        row["state"].clone()
    }

    fn events(&self, kind: &str) -> usize {
        let k = self.k.lock().unwrap();
        vk_contracts::syscalls::Kernel::ledger(&*k)
            .events()
            .iter()
            .filter(|e| e.kind == kind)
            .count()
    }

    fn last_event(&self) -> String {
        let k = self.k.lock().unwrap();
        vk_contracts::syscalls::Kernel::ledger(&*k)
            .events()
            .last()
            .map(|e| e.kind.clone())
            .unwrap_or_default()
    }

    /// Kill every driver this test started, stop the server, let go of the
    /// kernel and remove the state directory — in that order, because a
    /// running driver holds its work directory (and its log) open, and on
    /// Windows a directory with an open file in it cannot be removed.
    async fn shutdown(self) {
        let Node {
            d,
            k,
            pool,
            server,
            c,
            ..
        } = self;
        drop(c);
        pool.drop_host("fake");
        server.abort();
        let _ = server.await;
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
        while Arc::strong_count(&k) > 1 && std::time::Instant::now() < deadline {
            tokio::time::sleep(std::time::Duration::from_millis(10)).await;
        }
        assert_eq!(
            Arc::strong_count(&k),
            1,
            "a connection task still holds the kernel"
        );
        drop(k);
        d.close()
            .expect("the state directory is removed once the store is closed");
    }
}

/// The JSON-RPC error the server answered with.
fn call_error(err: &anyhow::Error) -> &CallError {
    err.downcast_ref::<CallError>()
        .unwrap_or_else(|| panic!("not a server error: {err}"))
}

/// A machine principal, as `harness_launch` is called by the daemon.
fn machine() -> vk_contracts::syscalls::Ctx {
    vk_contracts::syscalls::Ctx {
        principal: Principal::Machine {
            node_id: "n1".into(),
            lease_id: "test".into(),
        },
        clearance: Clearance {
            max_scope: Scope::Personal,
            third_party_allowed: true,
        },
        partition: "local".into(),
        now_ms: vk_kernel::now_ms(),
    }
}

/// Invariant I1 over the pipe: a machine principal may not put a browser
/// under this node's control, and is refused before any process is spawned;
/// a human may, and the driver is then running, listed and shown.
#[tokio::test]
async fn mount_requires_presence_then_mounts_and_spawns() {
    if !python_available() {
        return;
    }
    let drv = driver_dir();
    let node = Node::start().await;

    let err = node
        .c
        .call("driver.mount", mount_params(drv.path()), None)
        .await
        .unwrap_err();
    let e = call_error(&err);
    assert_eq!(e.code, vk_ipc::E_INVARIANT, "{err}");
    assert!(
        e.message.contains(
            "I1: mounting a driver puts a browser under this node's control, which is a \
             human act; re-run it with a presence proof"
        ),
        "{err}"
    );
    assert!(
        !node.pool.has_host("fake"),
        "a refused mount spawns nothing"
    );
    assert_eq!(node.events("driver.mounted"), 0, "and records nothing");

    let mounted = node.mount(drv.path()).await;
    assert_eq!(mounted["name"], "fake", "{mounted}");
    assert!(mounted["governed"].is_boolean(), "{mounted}");
    assert!(
        mounted["driver_id"]
            .as_str()
            .is_some_and(|id| id.starts_with("sha256:")),
        "{mounted}"
    );
    assert_eq!(mounted["already_mounted"], false, "{mounted}");
    assert!(
        node.pool.has_host("fake"),
        "a human mount spawns the driver"
    );
    assert_eq!(node.events("driver.mounted"), 1);

    // `vk ls /drivers` and `vk ls /drivers/fake`.
    let ls = node
        .c
        .call("ns.ls", json!({"path": "/drivers"}), None)
        .await
        .unwrap();
    assert_eq!(ls["entries"], json!(["fake"]), "{ls}");
    let shown = node
        .c
        .call("ns.ls", json!({"path": "/drivers/fake"}), None)
        .await
        .unwrap();
    assert_eq!(shown["type"], "driver", "{shown}");
    assert_eq!(shown["driver_id"], mounted["driver_id"], "{shown}");
    assert_eq!(
        shown["manifest"]["governed"], mounted["governed"],
        "{shown}"
    );

    // A repeat mount is idempotent and says so (Ruling 13): same row, no
    // second event, the same host.
    let again = node.mount(drv.path()).await;
    assert_eq!(again["already_mounted"], true, "{again}");
    assert_eq!(again["driver_id"], mounted["driver_id"], "{again}");
    assert_eq!(node.events("driver.mounted"), 1);

    node.shutdown().await;
}

/// The three phases over the pipe: a token the kernel minted for a harness
/// step that declared the driver, an act the daemon runs with the kernel lock
/// released, the driver's result back and `driver.acted` on the ledger. A
/// pool that has lost the host — what a daemon restart leaves — re-spawns it
/// from the row at the next act.
#[tokio::test]
async fn act_round_trip_through_the_daemon() {
    if !python_available() {
        return;
    }
    let drv = driver_dir();
    let node = Node::start().await;
    node.mount(drv.path()).await;
    let token = node.token().await;

    let act = |case: &str| {
        json!({
            "token": token,
            "driver": "fake",
            "action": "echo",
            "args": {"case": case}
        })
    };
    let answer = node.c.call("driver.act", act("N0-1"), None).await.unwrap();
    assert_eq!(answer["ok"], true, "{answer}");
    assert_eq!(answer["result"]["echo"]["case"], "N0-1", "{answer}");
    assert_eq!(node.last_event(), "driver.acted");
    assert_eq!(node.events("driver.acted"), 1);
    assert!(node.pool.has_host("fake"), "a good act keeps the host");

    // A restart's empty pool: the next act spawns the driver again.
    node.pool.drop_host("fake");
    assert!(!node.pool.has_host("fake"));
    let answer = node.c.call("driver.act", act("N0-2"), None).await.unwrap();
    assert_eq!(answer["result"]["echo"]["case"], "N0-2", "{answer}");
    assert_eq!(node.events("driver.acted"), 2);
    assert!(node.pool.has_host("fake"), "the act re-spawned the host");

    // But not a driver whose files changed since the mount: that is a new
    // driver, and the old identity is not re-spawned over it.
    node.pool.drop_host("fake");
    let script = drv.path().join("fake_driver.py");
    std::fs::write(&script, format!("{FAKE_DRIVER}# edited\n")).unwrap();
    let err = node
        .c
        .call("driver.act", act("N0-3"), None)
        .await
        .unwrap_err();
    let e = call_error(&err);
    assert_eq!(e.code, vk_ipc::E_INVARIANT, "{err}");
    // The closed word (final review I1); the daemon's account — a hash and
    // a path — is in the driver's log, not in the refusal or the record.
    assert!(e.message.ends_with("would not start"), "{err}");
    assert!(!e.message.contains("driver files hash"), "{err}");
    let log = std::fs::read_to_string(node.d.path().join("drivers/fake/work/driver.log"))
        .expect("the driver's log");
    assert!(
        log.contains("failed: would not start: driver files hash"),
        "{log}"
    );
    assert!(!node.pool.has_host("fake"), "nothing was spawned");
    assert_eq!(node.state_of("fake").await["state"], "degraded");
    assert_eq!(node.state_of("fake").await["reason"], "would not start");

    node.shutdown().await;
}

/// The pool's half of a failed act (Ruling 4). An evidence refusal is read
/// back after the action, with the driver alive and in step: the host stays
/// and the driver stays ready. A driver that dies under an act is dropped
/// from the pool and the driver degraded; a later act is refused without
/// spawning anything, and remounting — the remedy the refusal names — starts
/// a fresh process that acts again.
#[tokio::test]
async fn a_dead_driver_is_dropped_and_degraded_and_a_remount_brings_it_back() {
    if !python_available() {
        return;
    }
    let drv = driver_dir();
    let node = Node::start().await;
    node.mount(drv.path()).await;
    let token = node.token().await;
    let act = |action: &str| json!({"token": token, "driver": "fake", "action": action});

    let err = node
        .c
        .call("driver.act", act("big"), None)
        .await
        .unwrap_err();
    let e = call_error(&err);
    assert_eq!(e.code, vk_ipc::E_INVARIANT, "{err}");
    assert!(e.message.contains("evidence too large: big.bin"), "{err}");
    assert!(
        node.pool.has_host("fake"),
        "an evidence refusal keeps the host"
    );
    assert_eq!(node.state_of("fake").await["state"], "ready");

    let err = node
        .c
        .call("driver.act", act("die"), None)
        .await
        .unwrap_err();
    let e = call_error(&err);
    assert_eq!(e.code, vk_ipc::E_INVARIANT, "{err}");
    assert!(e.message.contains("dead"), "{err}");
    assert!(!node.pool.has_host("fake"), "a dead driver leaves the pool");
    assert_eq!(node.state_of("fake").await["state"], "degraded");

    let err = node
        .c
        .call("driver.act", act("echo"), None)
        .await
        .unwrap_err();
    assert!(call_error(&err).message.contains("remount it"), "{err}");
    assert!(!node.pool.has_host("fake"), "a refused act spawns nothing");

    let remounted = node.mount(drv.path()).await;
    assert_eq!(remounted["already_mounted"], false, "{remounted}");
    assert!(node.pool.has_host("fake"), "the remount started the driver");
    assert_eq!(node.state_of("fake").await["state"], "ready");
    let answer = node.c.call("driver.act", act("echo"), None).await.unwrap();
    assert_eq!(answer["ok"], true, "{answer}");

    node.shutdown().await;
}

/// Final review I1 (Ruling 28), end to end: a driver whose response drops
/// `ok` but carries the site's answer breaks protocol. The harness's refusal,
/// the degraded row and the record carry `protocol: malformed response`
/// alone; the line the host could not read — the answer with it — is in the
/// driver's log, where an operator looks, and nowhere the kernel keeps.
#[tokio::test]
async fn a_malformed_response_is_recorded_by_its_word_and_detailed_in_the_log() {
    if !python_available() {
        return;
    }
    let drv = driver_dir_of(BABBLE_DRIVER);
    let node = Node::start().await;
    let mut params = mount_params(drv.path());
    params["manifest"]["actions"] = json!([{
        "name": "babble",
        "reversible": true,
        "args": {"type": "object"},
        "evidence": [],
        "to_register": false
    }]);
    node.mount_with(params).await;
    let token = node.token().await;
    let marker = "the-site-answered-this ".repeat(13);

    let err = node
        .c
        .call(
            "driver.act",
            json!({"token": token, "driver": "fake", "action": "babble", "args": {"answer": marker}}),
            None,
        )
        .await
        .unwrap_err();
    let e = call_error(&err);
    assert_eq!(e.code, vk_ipc::E_INVARIANT, "{err}");
    assert!(e.message.ends_with("protocol: malformed response"), "{err}");
    assert!(!e.message.contains("the-site-answered-this"), "{err}");

    let state = node.state_of("fake").await;
    assert_eq!(state["state"], "degraded", "{state}");
    assert_eq!(state["reason"], "protocol: malformed response", "{state}");
    assert_eq!(node.events("driver.acted"), 1);
    assert_eq!(node.events("driver.unmounted"), 1);

    let log = std::fs::read_to_string(node.d.path().join("drivers/fake/work/driver.log"))
        .expect("the driver's log");
    assert!(
        log.contains(
            "failed: protocol: malformed response: driver protocol: response 1 is unreadable"
        ),
        "{log}"
    );
    assert!(
        log.contains("the-site-answered-this"),
        "the detail is kept: {log}"
    );
    node.shutdown().await;
}

/// Final review I2: the memory cap on a driver's tree is the manifest's
/// `program.max_memory_bytes`, and 6 GiB for a browser whose manifest names
/// none — not the harness's 2 GiB, under which a headed Chrome with two tabs
/// fails its allocations. The cap is the pool's choice at every spawn: at the
/// mount, and at the lazy re-spawn of an act after a restart.
#[tokio::test]
async fn the_memory_cap_is_the_manifests_or_six_gib_for_a_browser() {
    if !python_available() {
        return;
    }
    let drv = driver_dir();
    let node = Node::start().await;
    node.mount(drv.path()).await;
    assert_eq!(
        node.pool.memory_cap("fake"),
        Some(6 << 30),
        "the browser default"
    );

    let mut params = mount_params(drv.path());
    params["manifest"]["name"] = json!("twin");
    params["manifest"]["program"]["max_memory_bytes"] = json!(512u64 << 20);
    node.mount_with(params).await;
    assert_eq!(
        node.pool.memory_cap("twin"),
        Some(512 << 20),
        "the manifest's"
    );

    // A restart's empty pool: the act re-spawns under the same cap.
    node.pool.drop_host("twin");
    assert_eq!(node.pool.memory_cap("twin"), None, "no process, no cap");
    let token = node.token_for(&["twin"]).await;
    let answer = node
        .c
        .call(
            "driver.act",
            json!({"token": token, "driver": "twin", "action": "echo", "args": {"case": "x"}}),
            None,
        )
        .await
        .unwrap();
    assert_eq!(answer["ok"], true, "{answer}");
    assert_eq!(node.pool.memory_cap("twin"), Some(512 << 20));
    node.pool.drop_host("twin");
    node.shutdown().await;
}

/// No token, no act: a request without one is malformed, and one with a token
/// the kernel never minted is an I1 refusal. Neither spawns the driver or
/// records an action.
#[tokio::test]
async fn act_without_token_is_refused() {
    if !python_available() {
        return;
    }
    let drv = driver_dir();
    let node = Node::start().await;
    node.mount(drv.path()).await;
    node.pool.drop_host("fake");

    let err = node
        .c
        .call(
            "driver.act",
            json!({"driver": "fake", "action": "echo", "args": {}}),
            None,
        )
        .await
        .unwrap_err();
    assert_eq!(call_error(&err).code, vk_ipc::E_BAD_PARAMS, "{err}");

    let err = node
        .c
        .call(
            "driver.act",
            json!({"token": "not-a-token", "driver": "fake", "action": "echo", "args": {}}),
            None,
        )
        .await
        .unwrap_err();
    let e = call_error(&err);
    assert_eq!(e.code, vk_ipc::E_INVARIANT, "{err}");
    assert!(e.message.contains("I1"), "{err}");

    assert!(!node.pool.has_host("fake"), "a refused act spawns nothing");
    assert_eq!(node.events("driver.acted"), 0, "and records nothing");
    node.shutdown().await;
}

/// The manifest names the driver's files by hash: a directory that does not
/// hash to `files_sha256` is refused with the remedy, and so is a root that
/// is not absolute. Nothing is mounted and nothing runs.
#[tokio::test]
async fn files_hash_mismatch_is_refused() {
    if !python_available() {
        return;
    }
    let drv = driver_dir();
    let node = Node::start().await;

    let wrong = json!({
        "manifest": manifest(&format!("sha256:{}", "0".repeat(64))),
        "root": drv.path().display().to_string(),
    });
    let proof = node.proof().await;
    let err = node
        .c
        .call("driver.mount", wrong, Some(proof))
        .await
        .unwrap_err();
    let e = call_error(&err);
    assert_eq!(e.code, vk_ipc::E_BAD_PARAMS, "{err}");
    assert!(e.message.contains("driver files hash"), "{err}");
    assert!(
        e.message
            .contains("regenerate the manifest with drivers/vkdriver/hash_dir.py"),
        "{err}"
    );

    let mut relative = mount_params(drv.path());
    relative["root"] = json!("drivers/fake");
    let proof = node.proof().await;
    let err = node
        .c
        .call("driver.mount", relative, Some(proof))
        .await
        .unwrap_err();
    let e = call_error(&err);
    assert_eq!(e.code, vk_ipc::E_BAD_PARAMS, "{err}");
    assert!(e.message.contains("absolute"), "{err}");

    let listed = node.c.call("driver.ls", json!({}), None).await.unwrap();
    assert_eq!(listed, json!([]), "nothing is mounted");
    assert!(!node.pool.has_host("fake"), "nothing runs");
    assert_eq!(node.events("driver.mounted"), 0);
    node.shutdown().await;
}

/// `driver.unmount` deletes the row, records why, and kills the driver: the
/// pool holds no host for the name afterwards.
#[tokio::test]
async fn unmount_drops_the_host() {
    if !python_available() {
        return;
    }
    let drv = driver_dir();
    let node = Node::start().await;
    node.mount(drv.path()).await;
    assert!(node.pool.has_host("fake"));
    let listed = node.c.call("driver.ls", json!({}), None).await.unwrap();
    assert_eq!(listed.as_array().map(Vec::len), Some(1), "{listed}");
    let shown = node
        .c
        .call("driver.show", json!({"name": "fake"}), None)
        .await
        .unwrap();
    assert_eq!(shown["manifest"]["name"], "fake", "{shown}");

    let gone = node
        .c
        .call("driver.unmount", json!({"name": "fake"}), None)
        .await
        .unwrap();
    assert_eq!(gone["name"], "fake", "{gone}");
    assert!(!node.pool.has_host("fake"), "the host went with the row");
    let listed = node.c.call("driver.ls", json!({}), None).await.unwrap();
    assert_eq!(listed, json!([]), "{listed}");
    assert_eq!(node.events("driver.unmounted"), 1);

    let err = node
        .c
        .call("driver.show", json!({"name": "fake"}), None)
        .await
        .unwrap_err();
    assert_eq!(call_error(&err).code, vk_ipc::E_NOT_FOUND, "{err}");
    node.shutdown().await;
}

/// Ruling R13: unmounting a driver whose act is still running kills that
/// act's process — a browser profile is never open in two processes, and
/// nothing keeps acting on the site for minutes after `vk driver umount`.
/// The act comes back long before the thirty seconds `slow` sleeps, with the
/// host's failure, and its settle is the kernel's stale path.
#[tokio::test]
async fn unmount_kills_an_act_in_flight() {
    if !python_available() {
        return;
    }
    let drv = driver_dir();
    let node = Node::start().await;
    node.mount(drv.path()).await;
    let token = node.token().await;
    let pid = node.pool.pid("fake").expect("the mounted driver's process");
    assert!(process_alive(pid));

    let second = Client::connect(&node.endpoint).await.unwrap();
    let started = Instant::now();
    let slow = tokio::spawn(async move {
        second
            .call(
                "driver.act",
                json!({"token": token, "driver": "fake", "action": "slow"}),
                None,
            )
            .await
    });
    // The host makes the act's evidence directory just before it writes the
    // request: once it is there, the act is in flight.
    let evidence = node.d.path().join("drivers").join("fake").join("evidence");
    eventually("the slow act never started", || {
        std::fs::read_dir(&evidence).is_ok_and(|mut d| d.next().is_some())
    })
    .await;

    let gone = node
        .c
        .call("driver.unmount", json!({"name": "fake"}), None)
        .await
        .unwrap();
    assert_eq!(gone["name"], "fake", "{gone}");
    let answer = tokio::time::timeout(Duration::from_secs(15), slow)
        .await
        .expect("the act came back once its process was killed")
        .unwrap();
    assert!(
        started.elapsed() < Duration::from_secs(20),
        "well before the thirty seconds `slow` sleeps: {:?}",
        started.elapsed()
    );
    let err = answer.unwrap_err();
    let e = call_error(&err);
    assert_eq!(e.code, vk_ipc::E_INVARIANT, "{err}");
    assert!(e.message.contains("stale act"), "{err}");
    eventually("the driver's process is gone", || !process_alive(pid)).await;
    assert!(!node.pool.has_host("fake"));
    assert_eq!(node.events("driver.acted"), 1, "the stale act is recorded");
    node.shutdown().await;
}

/// Reviewer minor 3: a mount and an act that both find no process — after a
/// restart — start one between them. The driver here counts its starts and
/// takes a second to say hello, so the two requests overlap the spawn.
#[tokio::test]
async fn a_mount_racing_a_lazy_respawn_spawns_one_process() {
    if !python_available() {
        return;
    }
    let drv = driver_dir_of(&format!("{COUNTING}{FAKE_DRIVER}"));
    let node = Node::start().await;
    node.mount(drv.path()).await;
    let token = node.token().await;
    let starts = node
        .d
        .path()
        .join("drivers")
        .join("fake")
        .join("work")
        .join("starts.txt");
    let count = || {
        std::fs::read_to_string(&starts)
            .map(|s| s.lines().count())
            .unwrap_or(0)
    };
    assert_eq!(count(), 1);
    // A restart's empty pool.
    node.pool.drop_host("fake");

    let other = Client::connect(&node.endpoint).await.unwrap();
    let proof = node.proof().await;
    let act = json!({"token": token, "driver": "fake", "action": "echo", "args": {"case": "race"}});
    let (mounted, acted) = tokio::join!(
        node.c
            .call("driver.mount", mount_params(drv.path()), Some(proof)),
        other.call("driver.act", act, None),
    );
    let mounted = mounted.unwrap();
    assert_eq!(mounted["already_mounted"], true, "{mounted}");
    let acted = acted.unwrap();
    assert_eq!(acted["result"]["echo"]["case"], "race", "{acted}");
    assert_eq!(count(), 2, "two requests, one process");
    drop(other);
    node.shutdown().await;
}

/// A driver process that dies between two acts — killed from outside, as a
/// crash or the Task Manager would — is re-spawned lazily at the next act
/// from the row (`ensure` runs before every act, reviewer minor 2): the act
/// answers, on a new process, and the driver stays ready. One that dies
/// *under* an act is another matter (Ruling R12): the act fails `dead`, the
/// driver is degraded, a further act is refused with the remedy, and
/// `vk driver umount` + `vk mount driver` bring it back.
#[tokio::test]
async fn a_dead_host_is_respawned_lazily_on_the_next_act() {
    if !python_available() {
        return;
    }
    let drv = driver_dir();
    let node = Node::start().await;
    node.mount(drv.path()).await;
    let token = node.token().await;
    let echo = |case: &str| json!({"token": token, "driver": "fake", "action": "echo", "args": {"case": case}});
    node.c.call("driver.act", echo("1"), None).await.unwrap();
    let first = node.pool.pid("fake").expect("the driver's process");

    // Dies idle: the next act re-spawns it.
    vk_driver::kill_pid_tree(first);
    eventually("the killed driver is gone", || !process_alive(first)).await;
    let answer = node.c.call("driver.act", echo("2"), None).await.unwrap();
    assert_eq!(answer["result"]["echo"]["case"], "2", "{answer}");
    let second = node.pool.pid("fake").expect("a re-spawned process");
    assert_ne!(second, first, "the act ran on a new process");
    assert_eq!(node.state_of("fake").await["state"], "ready");
    assert_eq!(node.events("driver.unmounted"), 0);
    assert_eq!(node.events("driver.acted"), 2);

    // Dies under an act: degraded (R12); umount + mount; acts again.
    let other = Client::connect(&node.endpoint).await.unwrap();
    let slow_act = json!({"token": token, "driver": "fake", "action": "slow"});
    let slow = tokio::spawn(async move { other.call("driver.act", slow_act, None).await });
    let evidence = node.d.path().join("drivers").join("fake").join("evidence");
    eventually("the slow act never started", || {
        std::fs::read_dir(&evidence).is_ok_and(|mut d| d.next().is_some())
    })
    .await;
    vk_driver::kill_pid_tree(second);
    let err = tokio::time::timeout(Duration::from_secs(15), slow)
        .await
        .expect("the act came back once its process was killed")
        .unwrap()
        .unwrap_err();
    let e = call_error(&err);
    assert_eq!(e.code, vk_ipc::E_INVARIANT, "{err}");
    assert!(e.message.contains("dead"), "{err}");
    assert_eq!(node.state_of("fake").await["state"], "degraded");
    assert!(!node.pool.has_host("fake"), "a dead driver leaves the pool");
    let err = node
        .c
        .call("driver.act", echo("3"), None)
        .await
        .unwrap_err();
    assert!(call_error(&err).message.contains("remount it"), "{err}");
    assert!(!node.pool.has_host("fake"), "a refused act spawns nothing");

    node.c
        .call("driver.unmount", json!({"name": "fake"}), None)
        .await
        .unwrap();
    let mounted = node.mount(drv.path()).await;
    assert_eq!(mounted["already_mounted"], false, "{mounted}");
    let answer = node.c.call("driver.act", echo("4"), None).await.unwrap();
    assert_eq!(answer["result"]["echo"]["case"], "4", "{answer}");
    assert_eq!(node.state_of("fake").await["state"], "ready");
    node.shutdown().await;
}

/// Neither the kernel lock nor the pool's map is held across an act, so two
/// acts on two drivers overlap. Each `meet` leaves its mark and waits up to
/// ten seconds for the other's: both meet only if both ran at once — run one
/// after the other, the first would give up alone.
#[tokio::test]
async fn two_acts_on_two_drivers_run_concurrently() {
    if !python_available() {
        return;
    }
    let drv = driver_dir_of(MEET_DRIVER);
    let node = Node::start().await;
    for name in ["fake", "twin"] {
        node.mount_with(meet_params(drv.path(), name)).await;
    }
    let token = node.token_for(&["fake", "twin"]).await;
    let spot = tempfile::tempdir().unwrap();
    let meet = |me: &str, other: &str| {
        json!({
            "token": token,
            "driver": me,
            "action": "meet",
            "args": {"dir": spot.path().display().to_string(), "me": me, "other": other}
        })
    };
    let other = Client::connect(&node.endpoint).await.unwrap();
    let started = Instant::now();
    let (a, b) = tokio::join!(
        node.c.call("driver.act", meet("fake", "twin"), None),
        other.call("driver.act", meet("twin", "fake"), None),
    );
    let (a, b) = (a.unwrap(), b.unwrap());
    assert_eq!(a["result"]["met"], true, "{a}");
    assert_eq!(b["result"]["met"], true, "{b}");
    assert!(
        started.elapsed() < Duration::from_secs(10),
        "neither waited out the other: {:?}",
        started.elapsed()
    );
    assert_eq!(node.events("driver.acted"), 2);
    drop(other);
    node.pool.drop_host("twin");
    node.shutdown().await;
}

/// Task 3 re-review: the pool adopts an idle process only for the identity it
/// was spawned for. An unmount that lands between an act's row read and its
/// `ensure` leaves a process for a manifest no longer mounted (simulated here
/// by calling `ensure` with the old row after the unmount, as that act
/// would); a different manifest mounted later under the same name gets a
/// fresh process of its own, never that orphan, and its acts run there.
#[tokio::test]
async fn a_different_manifest_under_the_same_name_never_adopts_the_old_process() {
    if !python_available() {
        return;
    }
    let a = driver_dir_of(&tagged("A"));
    let b = driver_dir_of(&tagged("B"));
    let node = Node::start().await;
    node.mount(a.path()).await;
    let token = node.token().await;
    let act = json!({"token": token, "driver": "fake", "action": "echo", "args": {"case": "x"}});
    let answer = node.c.call("driver.act", act.clone(), None).await.unwrap();
    assert_eq!(answer["result"]["tag"], "A", "{answer}");

    let (row_a, dirs) = {
        let k = node.k.lock().unwrap();
        (k.driver("fake").unwrap().unwrap(), k.driver_dirs("fake"))
    };
    node.c
        .call("driver.unmount", json!({"name": "fake"}), None)
        .await
        .unwrap();
    assert!(!node.pool.has_host("fake"));
    // The late `ensure` of an act that read A's row before the unmount.
    node.pool.ensure(&row_a, &dirs).unwrap();
    let orphan = node.pool.pid("fake").expect("A's orphaned process");

    let mounted = node.mount(b.path()).await;
    assert_eq!(mounted["already_mounted"], false, "{mounted}");
    assert_ne!(mounted["driver_id"], json!(row_a.driver_id), "{mounted}");
    let answer = node.c.call("driver.act", act, None).await.unwrap();
    assert_eq!(
        answer["result"]["tag"], "B",
        "the act ran on B's process: {answer}"
    );
    assert_ne!(node.pool.pid("fake"), Some(orphan));
    eventually("A's orphan is gone", || !process_alive(orphan)).await;
    let starts = std::fs::read_to_string(dirs.work.join("starts.txt")).unwrap();
    assert_eq!(
        starts.lines().collect::<Vec<_>>(),
        ["A", "A", "B"],
        "A at its mount, A's orphan, then B"
    );
    node.shutdown().await;
}

/// Task 7 review, minor 1: an orphan of an unmounted manifest that is still
/// acting — a stale act whose late `ensure` spawned it — is never taken for
/// another manifest's process. A mount of a different manifest under the
/// same name is refused as busy, naming the old identity and the remedy;
/// `vk driver umount` kills the orphan's act (Ruling R13) even with no row
/// left to remove; the new manifest then mounts on a fresh process and acts
/// there.
#[tokio::test]
async fn an_orphan_still_acting_is_busy_by_its_identity_until_unmounted() {
    if !python_available() {
        return;
    }
    let a = driver_dir_of(&tagged("A"));
    let b = driver_dir_of(&tagged("B"));
    let node = Node::start().await;
    node.mount(a.path()).await;
    let (row_a, dirs) = {
        let k = node.k.lock().unwrap();
        (k.driver("fake").unwrap().unwrap(), k.driver_dirs("fake"))
    };
    node.c
        .call("driver.unmount", json!({"name": "fake"}), None)
        .await
        .unwrap();
    assert!(!node.pool.has_host("fake"));

    // The stale act of a run that read A's row before the unmount: its
    // `ensure` spawns A again, and its thirty-second action runs there.
    let stale = {
        let (pool, row, dirs) = (node.pool.clone(), row_a.clone(), dirs.clone());
        std::thread::spawn(move || {
            let slow = row.manifest.action("slow").expect("slow").clone();
            let caps = vk_driver::EvidenceCaps {
                per_file: 1 << 20,
                per_action: 4 << 20,
            };
            let never = || false;
            pool.act(
                &row,
                &dirs,
                &slow,
                &json!({}),
                Duration::from_secs(60),
                caps,
                &never,
            )
        })
    };
    eventually("the stale act never started", || {
        std::fs::read_dir(&dirs.evidence).is_ok_and(|mut d| d.next().is_some())
    })
    .await;
    let orphan = node.pool.pid("fake").expect("A's orphan, acting");

    let proof = node.proof().await;
    let err = node
        .c
        .call("driver.mount", mount_params(b.path()), Some(proof))
        .await
        .unwrap_err();
    let e = call_error(&err);
    assert_eq!(e.code, vk_ipc::E_BAD_PARAMS, "{err}");
    assert!(
        e.message.contains(&format!(
            "busy with an act of another manifest ({})",
            row_a.driver_id
        )),
        "{err}"
    );
    assert!(e.message.contains("vk driver umount fake"), "{err}");
    assert_eq!(node.events("driver.mounted"), 1, "B is not mounted");

    // The remedy: no row is left, so the unmount answers not-found, and it
    // kills the orphan's act all the same.
    let err = node
        .c
        .call("driver.unmount", json!({"name": "fake"}), None)
        .await
        .unwrap_err();
    assert_eq!(call_error(&err).code, vk_ipc::E_NOT_FOUND, "{err}");
    let outcome = stale.join().expect("the stale act's thread");
    assert!(
        outcome.as_ref().is_err_and(|why| why.reason() == "dead"),
        "{outcome:?}"
    );
    eventually("A's orphan is gone", || !process_alive(orphan)).await;
    assert!(!node.pool.has_host("fake"));

    node.mount(b.path()).await;
    let token = node.token().await;
    let answer = node
        .c
        .call(
            "driver.act",
            json!({"token": token, "driver": "fake", "action": "echo", "args": {"case": "y"}}),
            None,
        )
        .await
        .unwrap();
    assert_eq!(answer["result"]["tag"], "B", "{answer}");
    let starts = std::fs::read_to_string(dirs.work.join("starts.txt")).unwrap();
    assert_eq!(
        starts.lines().collect::<Vec<_>>(),
        ["A", "A", "B"],
        "A at its mount, A's orphan, then B"
    );
    node.shutdown().await;
}

/// Reviewer Important 1: the script the manifest names lives under the root
/// it is mounted from. A `..` path would hash one directory and run another,
/// and an absolute one would ignore the root and bind the manifest to one
/// machine; both are refused at mount, before anything runs.
#[tokio::test]
async fn the_script_is_held_to_root() {
    if !python_available() {
        return;
    }
    let drv = driver_dir();
    let node = Node::start().await;
    let absolute = drv.path().join("fake_driver.py").display().to_string();
    for script in ["../fake_driver.py", absolute.as_str()] {
        let mut params = mount_params(drv.path());
        params["manifest"]["program"]["command"] = json!([PYTHON, "-I", script]);
        let proof = node.proof().await;
        let err = node
            .c
            .call("driver.mount", params, Some(proof))
            .await
            .unwrap_err();
        let e = call_error(&err);
        assert_eq!(e.code, vk_ipc::E_BAD_PARAMS, "{script}: {err}");
        assert!(
            e.message.contains(
                "program.command paths must be relative to the manifest's directory, without `..`"
            ),
            "{script}: {err}"
        );
    }
    assert!(!node.pool.has_host("fake"), "nothing ran");
    assert_eq!(node.events("driver.mounted"), 0);

    // The relative script still mounts.
    node.mount(drv.path()).await;
    assert!(node.pool.has_host("fake"));
    node.shutdown().await;
}
