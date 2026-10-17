//! Driver packaging over the pipe (packaging spec §4.3–4.5, plan Task 8): a
//! real kernel behind a real local endpoint, its install root and publisher
//! key file named through the kernel's own seams, and the client calling
//! `capital.*` and `driver.*` exactly as `vk` does.
//!
//! What is pinned: the publisher key is made by a human and self-pinned;
//! `driver.pack` refuses what it can before anything is written — a version
//! that is not `MAJOR.MINOR.PATCH`, an `--out` that is not empty, a node
//! with no publisher key, a machine principal — and, with the pinned CPython
//! release (`VK_TEST_PYTHON_RELEASE`), writes a signed layout whose tree
//! materialises again elsewhere to the same fingerprint and runs, its
//! `vkdriver` found, before `driver.packed` reaches the ledger; a driver
//! mounts by `name@version` from a tree under the install root whose
//! manifest its package signs, with a publisher this node pinned and a tree
//! that hashes to the manifest's fingerprint, and is refused otherwise
//! before anything runs; an installed tree changed under a mounted driver
//! degrades it with `tampered` at the next spawn; `driver.ls` lists what is
//! installed beside what is mounted; a `{manifest, root}` mount is held to
//! the context rules before its process starts.
//!
//! The installed trees here are laid out by hand, as `vk driver install`
//! will: the driver's files, `manifest.json` (the package's manifest layer),
//! and `.package/{manifest.json,signature.json}` (the image manifest and the
//! publisher's signature over its digest). Their command is the test
//! runner's own `python`, so a mount runs without the pinned release; when
//! there is none on PATH a test says so on stderr and returns.
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
use vk_capital::layout::{self, Annotations, Layers};
use vk_capital::signature::{KeySource, Pins, PublisherKey};
use vk_contracts::driver::{DriverManifest, DriverUnmounted};
use vk_contracts::hash_canonical;
use vk_contracts::labels::{Clearance, Scope};
use vk_contracts::package::DriverPacked;
use vk_contracts::principal::{HumanKey, Principal, SoftwareHumanKey};
use vk_ipc::client::{CallError, Client};
use vk_ipc::drivers::DriverPool;
use vk_ipc::PresenceProof;

type Shared = Arc<Mutex<vk_kernel::RealKernel>>;

/// The interpreter the hand-installed drivers run under (as in
/// `tests/drivers.rs`): `python` on Windows, `python3` elsewhere.
const PYTHON: &str = if cfg!(windows) { "python" } else { "python3" };

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

/// A driver with one action, `echo`; each start appends a line to
/// `<work dir>/starts.txt`, so a test can tell whether a process ran.
const FAKE_DRIVER: &str = r#"import json, os, sys
open(os.path.join(os.environ["VK_DRIVER_WORK_DIR"], "starts.txt"), "a").write("start\n")
def say(o): sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
say({"hello": {"protocol": 1, "driver": "fake", "version": "0", "actions": ["echo"]}})
for line in sys.stdin:
    r = json.loads(line)
    say({"id": r["id"], "ok": True, "result": {"echo": r["args"]}})
"#;

/// The manifest of [`FAKE_DRIVER`], as `name`, run as `command`.
fn manifest(name: &str, command: &[&str], files_sha256: &str, source: &str) -> Value {
    json!({
        "name": name,
        "kind": "browser",
        "program": {
            "runtime": "python",
            "command": command,
            "files_sha256": files_sha256,
            "browser_channel": null,
            "headless": true
        },
        "hosts": ["example.invalid"],
        "actions": [{
            "name": "echo",
            "reversible": true,
            "args": {"type": "object"},
            "evidence": [],
            "to_register": false
        }],
        "min_interval_ms": 0,
        "max_action_ms": 60_000,
        "clearance": {"max_scope": "business", "third_party_allowed": true},
        "source": source,
        "governed": false
    })
}

/// An installed tree laid out by hand under `install_root`, as
/// `vk driver install` lays one out: the driver's script, `manifest.json`
/// (`source: installed`, its `files_sha256` the tree's fingerprint), and
/// `.package/` holding the image manifest whose manifest layer is that very
/// file and `key`'s signature over the image manifest's digest. Returns the
/// tree and the package digest.
fn install_by_hand(
    install_root: &Path,
    name: &str,
    version: &str,
    key: &PublisherKey,
) -> (PathBuf, String) {
    let root = install_root.join(name).join(version);
    std::fs::create_dir_all(&root).unwrap();
    std::fs::write(root.join("fake_driver.py"), FAKE_DRIVER).unwrap();
    let fingerprint = vk_capital::fingerprint::hash_tree(&root).unwrap();
    let m: DriverManifest = serde_json::from_value(manifest(
        name,
        &[PYTHON, "-I", "fake_driver.py"],
        &fingerprint,
        "installed",
    ))
    .unwrap();
    let manifest_bytes = serde_json::to_vec(&m).unwrap();
    std::fs::write(root.join("manifest.json"), &manifest_bytes).unwrap();
    let scratch = tempfile::tempdir().unwrap();
    let out = scratch.path().join("pkg");
    let (_, digest) = layout::write(
        &out,
        &Layers {
            manifest: manifest_bytes,
            files: b"FILES".to_vec(),
            lock: b"[]".to_vec(),
            wheels: b"WHEELS".to_vec(),
            python: b"PY".to_vec(),
        },
        &Annotations {
            name: name.into(),
            version: version.into(),
            driver_id: m.driver_id(),
            created: "2026-10-17T00:00:00Z".into(),
            publisher_key_id: key.key_id(),
            python_release: "test".into(),
            python_upstream_sha256: "0".repeat(64),
        },
    )
    .unwrap();
    let package = root.join(".package");
    std::fs::create_dir_all(&package).unwrap();
    std::fs::copy(
        layout::blob_path(&out, &digest),
        package.join("manifest.json"),
    )
    .unwrap();
    std::fs::write(
        package.join("signature.json"),
        serde_json::to_vec_pretty(&key.sign(&digest)).unwrap(),
    )
    .unwrap();
    (root, digest)
}

/// One daemon over its own state directory: a device enrolled, the install
/// root `<state>/installed` and the publisher key file `<state>/publisher.key`
/// set through the kernel's seams, and the pinned CPython release, when
/// `VK_TEST_PYTHON_RELEASE` names it, as the archive `driver.pack` uses.
struct Node {
    d: tempfile::TempDir,
    k: Shared,
    pool: Arc<DriverPool>,
    device: SoftwareHumanKey,
    server: tokio::task::JoinHandle<anyhow::Result<()>>,
    c: Client,
    install_root: PathBuf,
}

impl Node {
    async fn start() -> Node {
        let d = tempfile::tempdir().unwrap();
        vk_contracts::testing::guard_state_dir(d.path());
        let install_root = d.path().join("installed");
        std::fs::create_dir_all(&install_root).unwrap();
        let k: Shared = Arc::new(Mutex::new(
            vk_kernel::RealKernel::open(
                d.path(),
                vk_store::keys::KeySource::File(d.path().join("m.key")),
                "n1",
            )
            .unwrap()
            .with_install_root(&install_root)
            .with_publisher_key_file(d.path().join("publisher.key")),
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
            python_archive: std::env::var_os("VK_TEST_PYTHON_RELEASE").map(PathBuf::from),
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
            install_root,
        }
    }

    async fn proof(&self) -> PresenceProof {
        let ch = self
            .c
            .call("presence.challenge", json!({}), None)
            .await
            .unwrap();
        PresenceProof::sign(&self.device, ch["nonce"].as_str().unwrap())
    }

    /// A call carrying a fresh presence proof: a human act.
    async fn call_human(&self, method: &str, params: Value) -> anyhow::Result<Value> {
        let proof = self.proof().await;
        self.c.call(method, params, Some(proof)).await
    }

    /// `vk capital key init`, and the key it made, loaded from the file the
    /// kernel keeps it in.
    async fn publisher_key(&self) -> PublisherKey {
        let info = self
            .call_human("capital.key_init", json!({}))
            .await
            .unwrap();
        let key = PublisherKey::load(&KeySource::File(self.d.path().join("publisher.key")))
            .unwrap()
            .expect("key_init made the key");
        assert_eq!(info["key_id"], key.key_id());
        key
    }

    fn events(&self, kind: &str) -> Vec<vk_contracts::ledger::LedgerEvent> {
        let k = self.k.lock().unwrap();
        vk_contracts::syscalls::Kernel::ledger(&*k)
            .events()
            .iter()
            .filter(|e| e.kind == kind)
            .cloned()
            .collect()
    }

    fn last_event(&self) -> vk_contracts::ledger::LedgerEvent {
        let k = self.k.lock().unwrap();
        vk_contracts::syscalls::Kernel::ledger(&*k)
            .events()
            .last()
            .cloned()
            .expect("an event")
    }

    /// The `driver.ls` row named `name`.
    async fn listed(&self, name: &str) -> Value {
        let listed = self.c.call("driver.ls", json!({}), None).await.unwrap();
        listed
            .as_array()
            .unwrap()
            .iter()
            .find(|r| r["name"] == name)
            .cloned()
            .unwrap_or_else(|| panic!("no row {name} in {listed}"))
    }

    /// A lease token for a harness step that declared `fake`.
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
        let machine = vk_contracts::syscalls::Ctx {
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
        };
        self.k
            .lock()
            .unwrap()
            .harness_launch(
                &machine,
                task["id"].as_str().unwrap(),
                "claude-code",
                60_000,
            )
            .unwrap()
            .token
    }

    /// Kill every driver, stop the server, let go of the kernel and remove
    /// the state directory, in that order (see `tests/drivers.rs`).
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
            "a connection still holds the kernel"
        );
        drop(k);
        d.close()
            .expect("the state directory is removed once the store is closed");
    }
}

fn call_error(err: &anyhow::Error) -> &CallError {
    err.downcast_ref::<CallError>()
        .unwrap_or_else(|| panic!("not a server error: {err}"))
}

/// A development driver as `vk driver pack` takes one, under
/// `<dir>/drivers/maskai-fixture/` in a repository (`<dir>/Cargo.toml`),
/// beside the shared `vkdriver/` library it imports. Its `pack.json`
/// vendors that library (ruling R-T8a), and the driver looks for it under
/// `vendor/` first and in its parent directory otherwise — the way
/// `drivers/maskai-recette/driver.py` will. Its one requirement is a small
/// pure wheel with a console script (the one `vk-capital`'s materialise test
/// uses). Debris a pack must leave out sits in both: a `.venv`, `tests`,
/// bytecode, the library's own test.
fn fixture_driver_dir(dir: &Path) -> PathBuf {
    std::fs::write(dir.join("Cargo.toml"), "[workspace]\n").unwrap();
    let drivers = dir.join("drivers");
    let src = drivers.join("maskai-fixture");
    for sub in [".venv/Scripts", "tests", "__pycache__"] {
        std::fs::create_dir_all(src.join(sub)).unwrap();
    }
    std::fs::write(
        src.join("driver.py"),
        "import sys\nfrom pathlib import Path\n\
         DRIVER_DIR = Path(__file__).resolve().parent\n\
         VENDOR = DRIVER_DIR / \"vendor\"\n\
         sys.path.insert(0, str(VENDOR if VENDOR.is_dir() else DRIVER_DIR.parent))\n\
         from vkdriver.protocol import MARK  # noqa: E402\n\
         import pycodestyle  # noqa: E402\n\
         print(MARK, pycodestyle.__version__)\n",
    )
    .unwrap();
    std::fs::write(src.join("requirements.txt"), "pycodestyle==2.12.1\n").unwrap();
    std::fs::write(src.join("pack.json"), r#"{"vendor": ["drivers/vkdriver"]}"#).unwrap();
    std::fs::write(src.join(".venv/Scripts/python.exe"), b"MZ").unwrap();
    std::fs::write(src.join("tests/test_driver.py"), "").unwrap();
    std::fs::write(src.join("__pycache__/driver.cpython-313.pyc"), b"pyc").unwrap();
    let lib = drivers.join("vkdriver");
    std::fs::create_dir_all(lib.join("__pycache__")).unwrap();
    std::fs::write(lib.join("__init__.py"), "").unwrap();
    std::fs::write(lib.join("protocol.py"), "MARK = \"vkdriver found\"\n").unwrap();
    std::fs::write(lib.join("test_protocol.py"), "").unwrap();
    std::fs::write(lib.join("__pycache__/protocol.cpython-313.pyc"), b"pyc").unwrap();
    let hash = vk_contracts::driver::hash_dir(&src).unwrap();
    std::fs::write(
        src.join("manifest.json"),
        serde_json::to_vec_pretty(&manifest(
            "maskai-fixture",
            &[".venv/Scripts/python.exe", "-I", "driver.py"],
            &hash,
            "development",
        ))
        .unwrap(),
    )
    .unwrap();
    src
}

/// The files a tar holds, `/`-joined and sorted, read back the way an
/// install reads the layer.
fn tar_paths(bytes: &[u8]) -> Vec<String> {
    fn walk(root: &Path, dir: &Path, out: &mut Vec<String>) {
        for entry in std::fs::read_dir(dir).unwrap() {
            let path = entry.unwrap().path();
            if path.is_dir() {
                walk(root, &path, out);
            } else {
                let rel = path.strip_prefix(root).unwrap();
                out.push(rel.to_string_lossy().replace('\\', "/"));
            }
        }
    }
    let t = tempfile::tempdir().unwrap();
    vk_capital::wheels::untar_into(bytes, t.path()).unwrap();
    let mut out = vec![];
    walk(t.path(), t.path(), &mut out);
    out.sort();
    out
}

/// The directories `driver.pack` stages in: `%LOCALAPPDATA%\vk\stage\*`
/// on Windows, `$TMPDIR/vk-stage-*` elsewhere.
fn stages() -> Vec<PathBuf> {
    let (dir, prefix) = if cfg!(windows) {
        (
            PathBuf::from(std::env::var_os("LOCALAPPDATA").unwrap())
                .join("vk")
                .join("stage"),
            "",
        )
    } else {
        (std::env::temp_dir(), "vk-stage-")
    };
    let Ok(entries) = std::fs::read_dir(dir) else {
        return vec![];
    };
    let mut out: Vec<PathBuf> = entries
        .map(|e| e.unwrap().path())
        .filter(|p| {
            p.file_name()
                .and_then(|n| n.to_str())
                .is_some_and(|n| n.starts_with(prefix))
        })
        .collect();
    out.sort();
    out
}

/// The publisher key is a human's to make, self-pinned on the node that
/// made it, and shown without being made.
#[tokio::test]
async fn the_publisher_key_is_made_by_a_human_and_shown() {
    let node = Node::start().await;
    let err = node
        .c
        .call("capital.key_show", json!({}), None)
        .await
        .unwrap_err();
    assert_eq!(call_error(&err).code, vk_ipc::E_NOT_FOUND, "{err}");
    assert_eq!(
        call_error(&err).message,
        "no publisher key; remedy: vk capital key init"
    );
    assert!(
        !node.d.path().join("publisher.key").exists(),
        "showing makes no key"
    );
    let err = node
        .c
        .call("capital.key_init", json!({}), None)
        .await
        .unwrap_err();
    assert_eq!(call_error(&err).code, vk_ipc::E_INVARIANT, "{err}");
    assert!(call_error(&err).message.contains("human act"), "{err}");
    let made = node
        .call_human("capital.key_init", json!({}))
        .await
        .unwrap();
    assert_eq!(made["pinned"], true, "{made}");
    assert!(made["public_pem"]
        .as_str()
        .unwrap()
        .contains("AUBIER PUBLISHER KEY"));
    let shown = node
        .c
        .call("capital.key_show", json!({}), None)
        .await
        .unwrap();
    assert_eq!(shown, made);
    let pinned = Pins::at(node.d.path())
        .get(made["key_id"].as_str().unwrap())
        .unwrap();
    assert!(pinned.is_some(), "self-pinned on this node");
    node.shutdown().await;
}

/// The whole pack, with the pinned CPython release and PyPI: a signed
/// layout, the manifest layer `source: installed` with the packed command
/// and the tree's fingerprint, the files layer the driver's files plus the
/// `vkdriver` its `pack.json` vendors and none of the debris, the lock the one wheel; the
/// tree materialises again in another directory to the same fingerprint
/// and runs, `vkdriver` and the wheel found; `driver.packed` is the last
/// event, naming what was packed; and nothing is left in the stage.
#[tokio::test]
async fn pack_writes_a_signed_layout_and_a_driver_packed_row() {
    let Some(archive) = std::env::var_os("VK_TEST_PYTHON_RELEASE") else {
        eprintln!("VK_TEST_PYTHON_RELEASE is not set; skipping the pack with the real release");
        return;
    };
    let node = Node::start().await;
    let key = node.publisher_key().await;
    let src = fixture_driver_dir(node.d.path());
    let out = node.d.path().join("dist");
    let staged = stages();
    let r = node
        .call_human(
            "driver.pack",
            json!({
                "manifest": src.join("manifest.json"),
                "version": "0.1.0",
                "out": out,
                "python_cache": node.d.path().join("pycache"),
            }),
        )
        .await
        .unwrap();
    assert_eq!(r["name"], "maskai-fixture", "{r}");
    assert_eq!(r["version"], "0.1.0", "{r}");
    assert_eq!(stages(), staged, "the stage directory went with the pack");

    let opened = layout::open(&out).unwrap();
    assert_eq!(opened.package_digest, r["package_digest"]);
    let pinned = Pins::at(node.d.path()).get(&key.key_id()).unwrap().unwrap();
    vk_capital::signature::verify(&pinned, &opened.package_digest, &opened.signature).unwrap();

    let packed: DriverManifest =
        serde_json::from_slice(&layout::read_layer(&opened, "manifest").unwrap()).unwrap();
    assert_eq!(
        packed.program.command,
        ["python/python.exe", "-E", "-s", "-B", "driver.py"]
    );
    assert_eq!(packed.source, vk_contracts::driver::DriverSource::Installed);
    assert_eq!(packed.program.files_sha256, r["files_sha256"]);
    assert_eq!(packed.driver_id(), r["driver_id"]);
    assert_eq!(opened.package.name(), "maskai-fixture");
    assert_eq!(opened.package.version(), "0.1.0");

    let files = layout::read_layer(&opened, "files").unwrap();
    assert_eq!(
        tar_paths(&files),
        [
            "driver.py",
            "pack.json",
            "requirements.txt",
            "vendor/vkdriver/__init__.py",
            "vendor/vkdriver/protocol.py"
        ],
        "the driver's files and what pack.json vendors, no debris, no manifest"
    );
    let lock: Vec<vk_capital::wheels::Locked> =
        serde_json::from_slice(&layout::read_layer(&opened, "lock").unwrap()).unwrap();
    assert_eq!(lock.len(), 1, "{lock:?}");
    assert_eq!(
        (lock[0].name.as_str(), lock[0].version.as_str()),
        ("pycodestyle", "2.12.1")
    );

    // The tree an install will build, built here in another directory.
    let tree = node.d.path().join("m").join("0.1.0");
    let again = vk_capital::materialise::materialise(
        &tree,
        &files,
        Path::new(&archive),
        vk_capital::python::PINNED.sha256,
        &layout::read_layer(&opened, "wheels").unwrap(),
        &lock,
    )
    .unwrap();
    assert_eq!(again.fingerprint, r["files_sha256"], "reproducible");
    let ran = Command::new(tree.join("python").join("python.exe"))
        .args(["-E", "-s", "-B"])
        .arg(tree.join("driver.py"))
        .output()
        .unwrap();
    assert!(
        ran.status.success(),
        "{}",
        String::from_utf8_lossy(&ran.stderr)
    );
    assert_eq!(
        String::from_utf8_lossy(&ran.stdout).trim(),
        "vkdriver found 2.12.1"
    );

    let last = node.last_event();
    assert_eq!(last.kind, "driver.packed");
    assert_eq!(
        last.payload_hash,
        hash_canonical(&DriverPacked {
            driver_id: packed.driver_id(),
            name: "maskai-fixture".into(),
            version: "0.1.0".into(),
            package_digest: opened.package_digest.clone(),
            files_sha256: packed.program.files_sha256.clone(),
            publisher_key_id: key.key_id(),
            by: "laptop".into(),
        })
    );
    node.shutdown().await;
}

/// What `driver.pack` can refuse, it refuses before it writes anything or
/// downloads anything: a machine principal, a version that is not
/// `MAJOR.MINOR.PATCH`, an `--out` that holds something, a node with no
/// publisher key. None of these needs the CPython release.
#[tokio::test]
async fn a_second_pack_into_the_same_out_is_refused_before_anything_is_written() {
    let node = Node::start().await;
    let src = fixture_driver_dir(node.d.path());
    let out = node.d.path().join("dist");
    let params = |version: &str| {
        json!({
            "manifest": src.join("manifest.json"),
            "version": version,
            "out": out,
            "python_cache": node.d.path().join("pycache"),
        })
    };

    let err = node
        .c
        .call("driver.pack", params("0.1.0"), None)
        .await
        .unwrap_err();
    assert_eq!(call_error(&err).code, vk_ipc::E_INVARIANT, "{err}");
    assert!(call_error(&err).message.contains("human act"), "{err}");

    let err = node
        .call_human("driver.pack", params("0.1"))
        .await
        .unwrap_err();
    assert!(
        call_error(&err)
            .message
            .contains("is not MAJOR.MINOR.PATCH in digits; remedy: pass a version such as 1.0.0"),
        "{err}"
    );

    let err = node
        .call_human("driver.pack", params("0.1.0"))
        .await
        .unwrap_err();
    assert!(
        call_error(&err)
            .message
            .contains("no publisher key; remedy: vk capital key init"),
        "{err}"
    );
    assert!(!out.exists(), "nothing was written");

    node.publisher_key().await;
    std::fs::create_dir_all(&out).unwrap();
    std::fs::write(out.join("index.json"), b"{}").unwrap();
    let err = node
        .call_human("driver.pack", params("0.1.0"))
        .await
        .unwrap_err();
    assert!(
        call_error(&err)
            .message
            .contains("exists and is not an empty directory; remedy: choose an empty --out"),
        "{err}"
    );
    let left: Vec<_> = std::fs::read_dir(&out).unwrap().collect();
    assert_eq!(left.len(), 1, "the out directory is as it was");
    assert!(
        !node.d.path().join("pycache").exists(),
        "nothing downloaded"
    );
    assert!(node.events("driver.packed").is_empty());
    node.shutdown().await;
}

/// `driver.mount {installed: "name@version"}`: the tree under the install
/// root, its signature verified against a pinned publisher, its fingerprint
/// held to the manifest's; the answer and the record say where it came
/// from, and `driver.ls` lists the mount beside the versions installed. An
/// absent version, a malformed spec and both forms at once are refused.
#[tokio::test]
async fn mount_by_name_and_version_resolves_the_installed_tree_and_refuses_an_absent_version() {
    if !python_available() {
        return;
    }
    let node = Node::start().await;
    let key = node.publisher_key().await;
    let (root, digest) = install_by_hand(&node.install_root, "fake", "1.0.0", &key);

    let info = node.c.call("boot.info", json!({}), None).await.unwrap();
    assert_eq!(
        info["install_root"],
        node.install_root.display().to_string(),
        "{info}"
    );

    let mounted = node
        .call_human("driver.mount", json!({"installed": "fake@1.0.0"}))
        .await
        .unwrap();
    assert_eq!(mounted["name"], "fake", "{mounted}");
    assert_eq!(mounted["source"], "installed", "{mounted}");
    assert_eq!(mounted["version"], "1.0.0", "{mounted}");
    assert!(mounted["governed"].is_boolean(), "{mounted}");
    assert!(node.pool.has_host("fake"));
    let shown = node
        .c
        .call("driver.show", json!({"name": "fake"}), None)
        .await
        .unwrap();
    assert_eq!(shown["package_digest"], digest, "{shown}");
    assert_eq!(
        shown["root"],
        vk_kernel::drivers::resolved(&root).display().to_string(),
        "{shown}"
    );
    let cache = node.d.path().join("drivers/fake/fingerprint.json");
    assert!(cache.is_file(), "the fingerprint cache is kept");
    let listed = node.listed("fake").await;
    assert_eq!(listed["state"]["state"], "ready", "{listed}");
    assert_eq!(listed["source"], "installed", "{listed}");
    assert_eq!(listed["version"], "1.0.0", "{listed}");
    assert_eq!(listed["installed_versions"], json!(["1.0.0"]), "{listed}");

    let again = node
        .call_human("driver.mount", json!({"installed": "fake@1.0.0"}))
        .await
        .unwrap();
    assert_eq!(again["already_mounted"], true, "{again}");
    assert_eq!(node.events("driver.mounted").len(), 1);

    let err = node
        .call_human("driver.mount", json!({"installed": "fake@9.9.9"}))
        .await
        .unwrap_err();
    assert_eq!(call_error(&err).code, vk_ipc::E_NOT_FOUND, "{err}");
    assert!(
        call_error(&err)
            .message
            .contains("fake@9.9.9 is not installed; remedy: vk driver install"),
        "{err}"
    );
    for spec in [
        "fake",
        "Fake@1.0.0",
        "fake@1.0",
        "../x@1.0.0",
        "fake@1.0.0@2",
    ] {
        let err = node
            .call_human("driver.mount", json!({"installed": spec}))
            .await
            .unwrap_err();
        assert_eq!(call_error(&err).code, vk_ipc::E_BAD_PARAMS, "{spec}: {err}");
        assert!(
            call_error(&err)
                .message
                .contains("remedy: vk mount driver <name>@<MAJOR.MINOR.PATCH>"),
            "{spec}: {err}"
        );
    }
    let err = node
        .call_human(
            "driver.mount",
            json!({"installed": "fake@1.0.0", "root": root.display().to_string()}),
        )
        .await
        .unwrap_err();
    assert_eq!(call_error(&err).code, vk_ipc::E_BAD_PARAMS, "{err}");
    assert_eq!(node.events("driver.mounted").len(), 1);
    node.shutdown().await;
}

/// Each way an installed tree can fail to be what its publisher signed is
/// refused at mount, before anything runs: a publisher this node never
/// pinned, a `manifest.json` that is not the package's manifest layer, a
/// file of the tree changed after the install.
#[tokio::test]
async fn an_installed_tree_that_is_not_what_was_signed_is_refused_before_anything_runs() {
    if !python_available() {
        return;
    }
    let node = Node::start().await;
    node.publisher_key().await;
    let starts = node.d.path().join("drivers/fake/work/starts.txt");
    let mount = || node.call_human("driver.mount", json!({"installed": "fake@1.0.0"}));

    let stranger = tempfile::tempdir().unwrap();
    let unpinned =
        PublisherKey::load_or_create(&KeySource::File(stranger.path().join("k"))).unwrap();
    let (root, _) = install_by_hand(&node.install_root, "fake", "1.0.0", &unpinned);
    let err = mount().await.unwrap_err();
    let why = &call_error(&err).message;
    assert!(
        why.contains(&format!(
            "publisher {} is not pinned on this node; remedy: vk driver install pins it",
            unpinned.key_id()
        )),
        "{err}"
    );

    std::fs::remove_dir_all(&root).unwrap();
    let key = PublisherKey::load(&KeySource::File(node.d.path().join("publisher.key")))
        .unwrap()
        .unwrap();
    let (root, _) = install_by_hand(&node.install_root, "fake", "1.0.0", &key);
    let text = std::fs::read_to_string(root.join("manifest.json")).unwrap();
    std::fs::write(
        root.join("manifest.json"),
        text.replace("example.invalid", "elsewhere.invalid"),
    )
    .unwrap();
    let err = mount().await.unwrap_err();
    assert!(
        call_error(&err).message.contains(
            "the installed manifest of fake@1.0.0 is not the one its package signs; remedy: \
             reinstall the package, then remount"
        ),
        "{err}"
    );

    std::fs::remove_dir_all(&root).unwrap();
    let (root, _) = install_by_hand(&node.install_root, "fake", "1.0.0", &key);
    std::fs::write(
        root.join("fake_driver.py"),
        format!("{FAKE_DRIVER}# changed\n"),
    )
    .unwrap();
    let err = mount().await.unwrap_err();
    let why = &call_error(&err).message;
    assert!(
        why.contains("installed tree fingerprint")
            && why.contains("is not the manifest's")
            && why.contains("remedy: reinstall, then remount"),
        "{err}"
    );

    assert!(!node.pool.has_host("fake"), "nothing runs");
    assert!(!starts.exists(), "nothing ever ran");
    assert!(node.events("driver.mounted").is_empty());
    node.shutdown().await;
}

/// The fingerprint before every spawn (packaging spec §4.3): a byte changed
/// under the installed tree of a mounted driver is found when its process
/// is next started, and the act is refused with `tampered` — the driver
/// degraded, the record's `driver.unmounted` naming why, nothing run.
#[tokio::test]
async fn an_act_after_a_byte_change_under_the_installed_tree_degrades_with_tampered() {
    if !python_available() {
        return;
    }
    let node = Node::start().await;
    let key = node.publisher_key().await;
    let (root, _) = install_by_hand(&node.install_root, "fake", "1.0.0", &key);
    let mounted = node
        .call_human("driver.mount", json!({"installed": "fake@1.0.0"}))
        .await
        .unwrap();
    let token = node.token().await;
    let act = |case: &str| json!({"token": token, "driver": "fake", "action": "echo", "args": {"case": case}});
    let answer = node.c.call("driver.act", act("1"), None).await.unwrap();
    assert_eq!(answer["result"]["echo"]["case"], "1", "{answer}");

    // The process goes (a restart, a crash); the tree changes meanwhile.
    node.pool.drop_host("fake");
    let script = root.join("fake_driver.py");
    let mut bytes = std::fs::read(&script).unwrap();
    bytes[0] ^= 0x01;
    std::fs::write(&script, bytes).unwrap();
    let starts = node.d.path().join("drivers/fake/work/starts.txt");
    let before = std::fs::read_to_string(&starts).unwrap();

    let err = node.c.call("driver.act", act("2"), None).await.unwrap_err();
    let e = call_error(&err);
    assert_eq!(e.code, vk_ipc::E_INVARIANT, "{err}");
    assert!(e.message.ends_with("tampered"), "{err}");
    assert!(!node.pool.has_host("fake"), "nothing was spawned");
    assert_eq!(std::fs::read_to_string(&starts).unwrap(), before);
    let listed = node.listed("fake").await;
    assert_eq!(listed["state"]["state"], "degraded", "{listed}");
    assert_eq!(listed["state"]["reason"], "tampered", "{listed}");
    let unmounted = node.events("driver.unmounted");
    assert_eq!(
        unmounted.last().unwrap().payload_hash,
        hash_canonical(&DriverUnmounted {
            driver_id: mounted["driver_id"].as_str().unwrap().into(),
            reason: "tampered".into(),
        })
    );
    node.shutdown().await;
}

/// `driver.ls` lists every `<name>/<version>` under the install root that
/// holds a manifest, mounted or not; anything else there is not a version.
#[tokio::test]
async fn driver_ls_lists_installed_versions_before_any_mount() {
    let node = Node::start().await;
    for dir in [
        "fake/1.0.0",
        "fake/0.10.0",
        "fake/0.9.0",
        "fake/1.1.0.part",
        "other/2.0.0",
        "other/latest",
        "Bad_Name/1.0.0",
        "empty/1.0.0",
    ] {
        let dir = node.install_root.join(dir);
        std::fs::create_dir_all(&dir).unwrap();
        if !dir.starts_with(node.install_root.join("empty")) {
            std::fs::write(dir.join("manifest.json"), b"{}").unwrap();
        }
    }
    std::fs::write(node.install_root.join("stray.txt"), b"x").unwrap();
    let listed = node.c.call("driver.ls", json!({}), None).await.unwrap();
    assert_eq!(
        listed,
        json!([
            {"name": "fake", "state": {"state": "installed"}, "source": "installed",
             "version": null, "installed_versions": ["0.9.0", "0.10.0", "1.0.0"]},
            {"name": "other", "state": {"state": "installed"}, "source": "installed",
             "version": null, "installed_versions": ["2.0.0"]},
        ]),
        "{listed}"
    );
    node.shutdown().await;
}

/// A `{manifest, root}` mount is held to the context rules before its
/// process is started: a manifest that says installed from a development
/// directory, and one that asks for the service context, are refused with
/// nothing run.
#[tokio::test]
async fn a_manifest_mount_is_held_to_the_context_rules_before_anything_runs() {
    if !python_available() {
        return;
    }
    let node = Node::start().await;
    let drv = tempfile::tempdir().unwrap();
    std::fs::write(drv.path().join("fake_driver.py"), FAKE_DRIVER).unwrap();
    let hash = vk_contracts::driver::hash_dir(drv.path()).unwrap();
    let starts = node.d.path().join("drivers/fake/work/starts.txt");
    let command = [PYTHON, "-I", "fake_driver.py"];

    let installed = manifest("fake", &command, &hash, "installed");
    let mut service = manifest("fake", &command, &hash, "development");
    service["execution_context"] = json!("service");
    for (m, why) in [
        (installed, "must live under"),
        (service, "the service mode ships with vk-agent"),
    ] {
        let err = node
            .call_human(
                "driver.mount",
                json!({"manifest": m, "root": drv.path().display().to_string()}),
            )
            .await
            .unwrap_err();
        assert_eq!(call_error(&err).code, vk_ipc::E_INVARIANT, "{err}");
        assert!(call_error(&err).message.contains(why), "{err}");
    }
    assert!(!starts.exists(), "no process was started");
    assert!(node.events("driver.mounted").is_empty());

    // The same driver as a development mount runs.
    node.call_human(
        "driver.mount",
        json!({"manifest": manifest("fake", &command, &hash, "development"),
               "root": drv.path().display().to_string()}),
    )
    .await
    .unwrap();
    assert!(starts.exists());
    node.shutdown().await;
}
