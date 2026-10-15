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
//! the host.
//!
//! When no `python` is on PATH a test says so on stderr and returns; it never
//! fails silently and never passes vacuously without saying why.
use serde_json::{json, Value};
use std::path::Path;
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
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

/// The driver's own directory, apart from the state directory: the script and
/// nothing else, so `hash_dir` of it is the hash of exactly that script.
fn driver_dir() -> tempfile::TempDir {
    let dir = tempfile::tempdir().expect("a temporary directory");
    std::fs::write(dir.path().join("fake_driver.py"), FAKE_DRIVER).expect("write the driver");
    dir
}

/// A manifest over the fake driver with three of its actions: `echo`, which
/// returns `note.txt`; `big`, whose 3 MiB `big.bin` is over the per-file cap;
/// and `die`, which exits mid-request. The command is relative, as a
/// manifest's is (Ruling R6): the daemon resolves it against the `root` the
/// mount names.
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
            "command": ["python", "-I", "fake_driver.py"],
            "files_sha256": files_sha256,
            "browser_channel": null,
            "headless": true
        },
        "hosts": ["example.invalid"],
        "actions": [
            action("echo", &["note.txt"]),
            action("big", &["big.bin"]),
            action("die", &[]),
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

/// One daemon: a kernel over its own state directory with a device enrolled,
/// serving on a fresh endpoint with a pool this test can look into.
struct Node {
    d: tempfile::TempDir,
    k: Shared,
    pool: Arc<DriverPool>,
    device: SoftwareHumanKey,
    server: tokio::task::JoinHandle<anyhow::Result<()>>,
    c: Client,
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
        let proof = self.proof().await;
        self.c
            .call("driver.mount", mount_params(dir), Some(proof))
            .await
            .expect("a human mounts the driver")
    }

    /// A recipe-shaped task whose first step is a harness step declaring the
    /// driver, created over the pipe, and the lease token the kernel mints
    /// when that step is launched — what `harness.run` hands `vk-mcp`.
    async fn token(&self) -> String {
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
                        {"kind": "harness", "name": "claude-code", "drivers": ["fake"]},
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
    assert!(e.message.contains("driver would not start"), "{err}");
    assert!(e.message.contains("driver files hash"), "{err}");
    assert!(!node.pool.has_host("fake"), "nothing was spawned");
    assert_eq!(node.state_of("fake").await["state"], "degraded");

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
