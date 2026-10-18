//! What the pipe tests share (ruling L-P17): one daemon over its own state directory
//! ([`Node`]), the interpreter the hand-made drivers run under, and — for the vkforge tests —
//! the checkout's root, the vkforge interpreter and a fixture corpus.
//!
//! Each test binary uses a part of it, hence the `allow` below: CI's clippy runs with
//! `-D warnings`, and a helper one binary leaves unused is not a defect of that binary.
#![allow(dead_code)]

use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
use vk_capital::signature::{KeySource, PublisherKey};
use vk_contracts::labels::{Clearance, Scope};
use vk_contracts::principal::{HumanKey, Principal, SoftwareHumanKey};
use vk_ipc::client::{CallError, Client};
use vk_ipc::drivers::DriverPool;
use vk_ipc::forge::ForgeConfig;
use vk_ipc::PresenceProof;

pub type Shared = Arc<Mutex<vk_kernel::RealKernel>>;

/// The interpreter the hand-installed drivers run under (as in
/// `tests/drivers.rs`): `python` on Windows, `python3` elsewhere.
pub const PYTHON: &str = if cfg!(windows) { "python" } else { "python3" };

pub fn python_available() -> bool {
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

/// The checkout's root, for the vkforge package and its fixture scripts.
pub fn repo_root() -> PathBuf {
    std::path::absolute(Path::new(env!("CARGO_MANIFEST_DIR")).join("../..")).unwrap()
}

/// `%LOCALAPPDATA%\vk\venvs\vkforge`, the virtualenv drivers/vkforge/setup.ps1 makes (L-P12).
pub fn vkforge_venv() -> PathBuf {
    PathBuf::from(std::env::var_os("LOCALAPPDATA").unwrap_or_default())
        .join("vk")
        .join("venvs")
        .join("vkforge")
}

/// The interpreter the daemon runs vkforge with: `VK_FORGE_PYTHON`, else the virtualenv's.
pub fn forge_python() -> PathBuf {
    std::env::var_os("VK_FORGE_PYTHON")
        .map(PathBuf::from)
        .unwrap_or_else(|| vkforge_venv().join("Scripts").join("python.exe"))
}

/// Whether the vkforge tests can run here (L-P18): the virtualenv a generated driver's `.venv` links
/// to exists, and the interpreter imports lxml and comtypes. Says why on stderr when not.
pub fn forge_available() -> bool {
    let python = forge_python();
    let venv = vkforge_venv().join("Scripts").join("python.exe");
    let imports = Command::new(&python)
        .args(["-I", "-c", "import lxml, comtypes"])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok_and(|s| s.success());
    if !(imports && venv.is_file()) {
        eprintln!(
            "vkforge: {} cannot import lxml and comtypes, or {} is absent; skipping \
             (drivers\\vkforge\\setup.ps1 makes the virtualenv)",
            python.display(),
            venv.display()
        );
        return false;
    }
    true
}

/// A corpus of fixture contracts (drivers/vkforge/tests/ooxml_fixtures.py): `garamond` bodies in
/// Garamond 11 then `calibri` in Calibri 12, each with the house title, footer and a sibling PDF.
pub fn ooxml_corpus(dir: &Path, garamond: u32, calibri: u32) -> PathBuf {
    let script = repo_root().join("drivers/vkforge/tests/ooxml_fixtures.py");
    let ok = Command::new(forge_python())
        .arg("-I")
        .arg(&script)
        .arg(dir)
        .arg(garamond.to_string())
        .arg(calibri.to_string())
        .status()
        .expect("run ooxml_fixtures.py")
        .success();
    assert!(ok, "ooxml_fixtures.py {}", dir.display());
    dir.to_path_buf()
}

/// One daemon over its own state directory: a device enrolled, the install
/// root `<state>/installed` and the publisher key file `<state>/publisher.key`
/// set through the kernel's seams, the pinned CPython release, when
/// `VK_TEST_PYTHON_RELEASE` names it, as the archive `driver.pack` uses, and
/// a vkforge (the in-process form of `vkd --vkforge-root`).
pub struct Node {
    pub d: tempfile::TempDir,
    pub k: Shared,
    pub pool: Arc<DriverPool>,
    pub device: SoftwareHumanKey,
    pub server: tokio::task::JoinHandle<anyhow::Result<()>>,
    pub c: Client,
    pub install_root: PathBuf,
    /// Where the daemon serves: a test that needs a second connection dials it.
    pub endpoint: vk_ipc::transport::Endpoint,
}

impl Node {
    /// A node whose vkforge is this checkout's `drivers/`, run by [`forge_python`], with the
    /// recorded catalogues and the fake application (`fixtures`).
    pub async fn start() -> Node {
        Node::start_with_forge(ForgeConfig {
            python: forge_python(),
            fixtures: true,
            ..ForgeConfig::at(repo_root().join("drivers"))
        })
        .await
    }

    /// A node whose daemon was given `forge`, as `vkd --vkforge-root` gives one (L-P26, L-P30).
    pub async fn start_with_forge(forge: ForgeConfig) -> Node {
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
            forge: Some(forge),
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
            endpoint,
        }
    }

    /// The state directory.
    pub fn dir(&self) -> &Path {
        self.d.path()
    }

    pub async fn proof(&self) -> PresenceProof {
        let ch = self
            .c
            .call("presence.challenge", json!({}), None)
            .await
            .unwrap();
        PresenceProof::sign(&self.device, ch["nonce"].as_str().unwrap())
    }

    /// A call carrying a fresh presence proof: a human act.
    pub async fn call_human(&self, method: &str, params: Value) -> anyhow::Result<Value> {
        let proof = self.proof().await;
        self.c.call(method, params, Some(proof)).await
    }

    /// A call with no presence proof: a machine act.
    pub async fn call(&self, method: &str, params: Value) -> anyhow::Result<Value> {
        self.c.call(method, params, None).await
    }

    /// `vk capital key init`, and the key it made, loaded from the file the
    /// kernel keeps it in.
    pub async fn publisher_key(&self) -> PublisherKey {
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

    pub fn events(&self, kind: &str) -> Vec<vk_contracts::ledger::LedgerEvent> {
        let k = self.k.lock().unwrap();
        vk_contracts::syscalls::Kernel::ledger(&*k)
            .events()
            .iter()
            .filter(|e| e.kind == kind)
            .cloned()
            .collect()
    }

    pub fn last_event(&self) -> vk_contracts::ledger::LedgerEvent {
        let k = self.k.lock().unwrap();
        vk_contracts::syscalls::Kernel::ledger(&*k)
            .events()
            .last()
            .cloned()
            .expect("an event")
    }

    /// The `driver.ls` row named `name`.
    pub async fn listed(&self, name: &str) -> Value {
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
    pub async fn token(&self) -> String {
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
    pub async fn shutdown(self) {
        let Node {
            d,
            k,
            pool,
            server,
            c,
            ..
        } = self;
        drop(c);
        let mounted: Vec<String> = k
            .lock()
            .unwrap()
            .drivers()
            .unwrap()
            .into_iter()
            .map(|row| row.manifest.name)
            .collect();
        for name in mounted.iter().map(String::as_str).chain(["fake"]) {
            pool.drop_host(name);
        }
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

pub fn call_error(err: &anyhow::Error) -> &CallError {
    err.downcast_ref::<CallError>()
        .unwrap_or_else(|| panic!("not a server error: {err}"))
}
