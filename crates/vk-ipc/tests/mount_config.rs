//! `arch.mount` configs over the pipe (final review I-1 and I-4, Ruling 41;
//! Ruling 42): what a request may say about the container and the server a
//! local arch runs on, which of those take a human — and, since the founder's
//! decision of 2026-10-13, that it says nothing about the Claude Code binary,
//! which is the daemon's (`vkd --claude-bin`; its rule is in `claude_bin.rs`).
//!
//! Every test here points `VK_DOCKER` at a path that does not exist, so no
//! request can reach this machine's Docker — whatever the daemon decides, the
//! worst it can do is fail to run a binary that is not there. A process of
//! its own (this file is its own test binary), so the variable is set for
//! nothing else.
use serde_json::{json, Value};
use std::sync::{Arc, Mutex};
use vk_contracts::principal::{HumanKey, SoftwareHumanKey};
use vk_ipc::client::{CallError, Client};
use vk_ipc::PresenceProof;

fn no_docker() {
    // The same value from every test, so concurrent writes cannot disagree.
    std::env::set_var(
        "VK_DOCKER",
        std::env::temp_dir()
            .join("vk-no-docker-here")
            .join("docker"),
    );
}

fn kernel(dir: &std::path::Path) -> Arc<Mutex<vk_kernel::RealKernel>> {
    vk_contracts::testing::guard_state_dir(dir);
    Arc::new(Mutex::new(
        vk_kernel::RealKernel::open(
            dir,
            vk_store::keys::KeySource::File(dir.join("m.key")),
            "n1",
        )
        .unwrap(),
    ))
}

async fn shutdown(
    server: tokio::task::JoinHandle<anyhow::Result<()>>,
    k: Arc<Mutex<vk_kernel::RealKernel>>,
    d: tempfile::TempDir,
) {
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
    d.close().expect("the state directory is removed");
}

/// A daemon with one enrolled human key, and a client on it.
struct Node {
    d: tempfile::TempDir,
    k: Arc<Mutex<vk_kernel::RealKernel>>,
    server: tokio::task::JoinHandle<anyhow::Result<()>>,
    c: Client,
    key: SoftwareHumanKey,
}

impl Node {
    /// A daemon whose Claude Code binary is the default, as every Ollama test
    /// here wants.
    async fn start() -> Node {
        Node::start_with(std::path::PathBuf::from("claude")).await
    }

    /// A daemon whose Claude Code binary is `claude_bin`: the `ServerConfig`
    /// field `vkd --claude-bin` sets, never a request field.
    async fn start_with(claude_bin: std::path::PathBuf) -> Node {
        no_docker();
        let d = tempfile::tempdir().unwrap();
        let k = kernel(d.path());
        let key = SoftwareHumanKey::generate("laptop");
        {
            use vk_contracts::testing::KernelTestHooks;
            k.lock()
                .unwrap()
                .enroll_device("laptop", key.verifying_key_bytes());
        }
        let endpoint = vk_ipc::transport::test_endpoint();
        let listener = vk_ipc::transport::os::bind(&endpoint).await.unwrap();
        let config = vk_ipc::server::ServerConfig {
            claude_bin,
            ..vk_ipc::server::ServerConfig::new(endpoint.0.clone())
        };
        let server = tokio::spawn(vk_ipc::server::serve_on(k.clone(), listener, config));
        tokio::time::sleep(std::time::Duration::from_millis(100)).await;
        let c = Client::connect(&endpoint).await.unwrap();
        Node {
            d,
            k,
            server,
            c,
            key,
        }
    }

    async fn proof(&self) -> PresenceProof {
        let ch = self
            .c
            .call("presence.challenge", json!({}), None)
            .await
            .unwrap();
        PresenceProof::sign(&self.key, ch["nonce"].as_str().unwrap())
    }

    /// `arch.mount` with a presence proof; the error it must come back with.
    async fn refused(&self, kind: &str, config: Value, human: bool) -> CallError {
        let proof = if human {
            Some(self.proof().await)
        } else {
            None
        };
        let err = self
            .c
            .call("arch.mount", json!({"kind": kind, "config": config}), proof)
            .await
            .expect_err("this mount must be refused");
        let e = err
            .downcast_ref::<CallError>()
            .unwrap_or_else(|| panic!("not a server error: {err}"));
        CallError {
            code: e.code,
            message: e.message.clone(),
        }
    }

    async fn stop(self) {
        drop(self.c);
        shutdown(self.server, self.k, self.d).await;
    }
}

/// A refusal as a bad request whose message carries `needle`.
fn bad_request(err: &CallError, needle: &str, what: &str) {
    assert_eq!(err.code, vk_ipc::E_BAD_PARAMS, "{what}: {err:?}");
    assert!(
        err.message.contains(needle),
        "{what}: wanted {needle:?} in {err:?}"
    );
}

/// A stand-in `claude` in `dir` that answers `--version` with `version` and
/// nothing else: enough for a mount to probe it. `.cmd` on Windows, a script
/// elsewhere.
fn stand_in(dir: &std::path::Path, version: &str) -> std::path::PathBuf {
    let bin = dir.join(if cfg!(windows) {
        "fake-claude.cmd"
    } else {
        "fake-claude.sh"
    });
    let script = if cfg!(windows) {
        format!("@echo off\r\nif \"%~1\"==\"--version\" echo {version}\r\nexit /b 0\r\n")
    } else {
        format!("#!/bin/sh\nif [ \"$1\" = \"--version\" ]; then echo {version}; fi\n")
    };
    std::fs::write(&bin, script).unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&bin, std::fs::Permissions::from_mode(0o700)).unwrap();
    }
    bin
}

/// The founder's decision of 2026-10-13: a `claude-code` mount that names a
/// binary is a bad request that says where the binary is set — whatever the
/// value, a clean one included — and nothing is mounted on the way. The
/// config is still a closed shape (Ruling 42): any other field it does not
/// have is named and refused too.
#[tokio::test]
async fn a_claude_code_mount_that_names_a_binary_is_refused() {
    let scratch = tempfile::tempdir().unwrap();
    vk_contracts::testing::guard_state_dir(scratch.path());
    let bin = stand_in(scratch.path(), "0.0.0-daemon");
    let n = Node::start_with(bin.clone()).await;
    for named in [json!(bin), json!("claude"), json!("bin/claude")] {
        let err = n
            .refused(
                "claude-code",
                json!({ "binary": named, "model": "m" }),
                true,
            )
            .await;
        bad_request(&err, "binary", &format!("binary = {named}"));
        assert!(
            err.message.contains("--claude-bin"),
            "the refusal names the daemon flag: {err:?}"
        );
    }
    let err = n
        .refused("claude-code", json!({ "endpoint": "x" }), true)
        .await;
    bad_request(&err, "unknown field", "an unknown field");
    let info = n.c.call("boot.info", json!({}), None).await.unwrap();
    assert_eq!(info["arches"], 0, "nothing was mounted: {info}");
    n.stop().await;
}

/// Without a binary, the mount runs the daemon's: the version in the arch's
/// identity is the one the daemon's stand-in answers, and the spec that boot
/// will replay records no binary at all.
#[tokio::test]
async fn a_claude_code_mount_runs_the_daemons_binary() {
    let scratch = tempfile::tempdir().unwrap();
    vk_contracts::testing::guard_state_dir(scratch.path());
    let n = Node::start_with(stand_in(scratch.path(), "0.0.0-daemon")).await;
    let proof = n.proof().await;
    let mounted =
        n.c.call(
            "arch.mount",
            json!({"kind": "claude-code", "config": {"model": "m"}}),
            Some(proof),
        )
        .await
        .expect("a mount without a binary is accepted");
    let shown =
        n.c.call("arch.show", json!({"arch_id": mounted["arch_id"]}), None)
            .await
            .unwrap();
    assert_eq!(
        shown["manifest"]["identity"]["engine_version"], "0.0.0-daemon",
        "the daemon's binary was asked its version: {shown}"
    );
    assert_eq!(shown["kind"], "claude-code", "{shown}");
    assert!(
        shown["config"].get("binary").is_none(),
        "the spec records no binary: {shown}"
    );
    // The provider's health beside the manifest (2026-10-15), healthy from
    // the mount on; and no quota claim, which this adapter does not make.
    assert_eq!(shown["health"]["state"], "healthy", "{shown}");
    assert!(shown["manifest"].get("quota").is_none(), "{shown}");
    let top = n.c.call("top", json!({}), None).await.unwrap();
    let id = mounted["arch_id"].as_str().unwrap();
    assert_eq!(top["health"][id]["state"], "healthy", "{top}");
    n.stop().await;
}

/// Fix round 1, I-1: a daemon left at start with a bare name it did not find
/// re-resolves it at the mount, with the start rule, and refuses the mount
/// when it is still not there — nothing is spawned through a PATH lookup.
#[tokio::test]
async fn a_bare_name_the_daemon_did_not_find_is_resolved_again_at_mount() {
    let n = Node::start_with(std::path::PathBuf::from("vk-no-such-claude-anywhere")).await;
    let err = n
        .refused("claude-code", json!({ "model": "m" }), true)
        .await;
    bad_request(&err, "PATH", "a bare name still not on the PATH");
    assert!(err.message.contains("--claude-bin"), "{err:?}");
    n.stop().await;
}

/// Re-review: boot re-creation on an interactive daemon that found no
/// `claude` at start goes through the same re-resolution as a mount — the
/// factory refuses, with the default's own message, instead of handing the
/// adapter a bare name to look up at call time.
#[test]
fn boot_re_creation_resolves_a_bare_name_again_and_refuses_it_when_absent() {
    use vk_kernel::arch::MountSpec;
    let d = tempfile::tempdir().unwrap();
    vk_contracts::testing::guard_state_dir(d.path());
    let factory = vk_ipc::server::adapter_factory(
        d.path().to_path_buf(),
        vk_arch_anthropic::KeySource::Keyring,
        vk_arch_anthropic::DEFAULT_BASE_URL.to_string(),
        std::path::PathBuf::from("vk-no-such-claude-anywhere"),
    );
    let spec = MountSpec::new("claude-code", json!({"model": "m"})).unwrap();
    let err = match factory(&spec) {
        Ok(_) => panic!("a bare name that is not on the PATH must not build an arch"),
        Err(e) => format!("{e:#}"),
    };
    assert!(
        err.contains("no `vk-no-such-claude-anywhere` on the daemon's PATH")
            && err.contains("--claude-bin <absolute path>"),
        "{err}"
    );
}

/// A spec persisted by SP1b still carries the binary its request named. Boot
/// re-creation accepts it, ignores the field — the daemon's binary is what
/// runs — and says so in the log, naming what it ignored. No migration: the
/// row is left as it is.
#[test]
fn a_legacy_spec_naming_a_binary_is_re_created_on_the_daemons_and_the_field_is_logged() {
    use vk_kernel::arch::{ArchAdapter, MountSpec};
    let d = tempfile::tempdir().unwrap();
    let dir = d.path().to_path_buf();
    vk_contracts::testing::guard_state_dir(&dir);
    let daemons = stand_in(&dir, "0.0.0-daemon");
    let legacy = dir.join("old").join("claude");
    let open = || {
        vk_kernel::RealKernel::open_with_factory(
            &dir,
            vk_store::keys::KeySource::File(dir.join("m.key")),
            "n1",
            vk_ipc::server::adapter_factory(
                dir.clone(),
                vk_arch_anthropic::KeySource::Keyring,
                vk_arch_anthropic::DEFAULT_BASE_URL.to_string(),
                daemons.clone(),
            ),
        )
        .unwrap()
    };
    // Mounted the SP1b way: the spec carries a `binary`.
    let arch_id = {
        let mut k = open();
        let fresh = MountSpec::new("claude-code", json!({"model": "m"})).unwrap();
        let built = (k.adapter_factory())(&fresh).expect("the daemon's binary builds the arch");
        let adapter: Arc<dyn ArchAdapter> = Arc::from(built);
        let spec = MountSpec::new(
            "claude-code",
            json!({"binary": legacy.to_string_lossy(), "model": "m"}),
        )
        .unwrap();
        k.mount_with(adapter, spec, vk_kernel::Remount::Keep)
            .unwrap()
            .arch_id
    };
    let k = open();
    let pending = k.pending_mounts();
    assert_eq!(pending.len(), 1, "one arch to re-create");
    let (id, spec) = &pending[0];
    assert_eq!(id, &arch_id);
    assert!(
        spec.config.get("binary").is_some(),
        "the row is not migrated"
    );

    let log = Captured::default();
    let subscriber = tracing_subscriber::fmt()
        .with_writer(log.clone())
        .with_ansi(false)
        .finish();
    let made = tracing::subscriber::with_default(subscriber, || (k.adapter_factory())(spec));
    let adapter = made.expect("a legacy spec is re-created, not refused for its field");
    assert_eq!(
        adapter.manifest().arch_id(),
        arch_id,
        "the same arch comes back"
    );
    assert_eq!(adapter.manifest().identity.engine_version, "0.0.0-daemon");
    let said = log.text();
    let legacy = legacy.to_string_lossy().into_owned();
    assert!(
        said.contains("binary") && said.contains("ignored") && said.contains(&legacy),
        "the log says which binary was ignored: {said:?}"
    );
    assert!(said.contains("--claude-bin"), "{said:?}");
}

/// What a `tracing` subscriber wrote, for a test to read back.
#[derive(Clone, Default)]
struct Captured(Arc<Mutex<Vec<u8>>>);

impl Captured {
    fn text(&self) -> String {
        String::from_utf8_lossy(&self.0.lock().unwrap()).into_owned()
    }
}

impl std::io::Write for Captured {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        self.0.lock().unwrap().extend_from_slice(buf);
        Ok(buf.len())
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

impl<'a> tracing_subscriber::fmt::MakeWriter<'a> for Captured {
    type Writer = Captured;
    fn make_writer(&'a self) -> Captured {
        self.clone()
    }
}

/// Ruling 42 (1): the Ollama config is a closed shape too — a field this
/// daemon does not take is named and refused, never quietly ignored.
#[tokio::test]
async fn ollama_refuses_a_field_it_does_not_take() {
    let n = Node::start().await;
    let err = n
        .refused(
            "ollama",
            json!({"base_url": "http://127.0.0.1:9", "volume": "/"}),
            true,
        )
        .await;
    bad_request(&err, "unknown field", "an unknown Ollama field");
    n.stop().await;
}

/// Ruling 42 (2): `memory` and `cpus` reach `docker run`'s argv, so they are
/// held to Docker's own plain formats before they get there.
#[tokio::test]
async fn ollama_refuses_caps_that_are_not_plain_numbers() {
    let n = Node::start().await;
    for (field, value) in [
        ("memory", "12g --privileged"),
        ("memory", "12G"),
        ("memory", "-1"),
        ("memory", ""),
        ("memory", "12gb"),
        ("cpus", "6; rm"),
        ("cpus", "1e9"),
        ("cpus", "1."),
        ("cpus", "-2"),
    ] {
        let err = n.refused("ollama", json!({ field: value }), false).await;
        bad_request(&err, field, &format!("{field} = {value:?}"));
        assert!(
            err.message.contains("format"),
            "{field} = {value:?} is refused for its format: {err:?}"
        );
    }
    n.stop().await;
}

/// Ruling 42 (3): the image is the plan's, pinned. A request may repeat it;
/// anything else is refused — whatever proof comes with it.
#[tokio::test]
async fn ollama_refuses_an_image_that_is_not_the_pinned_one() {
    let n = Node::start().await;
    for image in ["ollama/ollama:latest", "evil.example/ollama:0.33.3"] {
        let err = n.refused("ollama", json!({ "image": image }), true).await;
        bad_request(&err, "pinned", image);
    }
    n.stop().await;
}

/// Final re-review N-1: the image check runs before the proof gate. The
/// pinned image by digest (or tag and digest) is the pinned image and needs no
/// proof — the CLI sends none for a container mount — and a foreign image is
/// refused as a bad request, proof or not, never asked for a proof first.
#[tokio::test]
async fn the_pinned_image_by_digest_needs_no_proof_and_a_foreign_one_is_refused_outright() {
    let n = Node::start().await;
    for pinned in [
        format!("ollama/ollama@{}", vk_arch_ollama::PINNED_IMAGE_DIGEST),
        format!(
            "{}@{}",
            vk_arch_ollama::DEFAULT_IMAGE,
            vk_arch_ollama::PINNED_IMAGE_DIGEST
        ),
    ] {
        // Past the gate and the image check, it reaches Docker — which this
        // test process has pointed at nothing.
        let err = n.refused("ollama", json!({ "image": pinned }), false).await;
        bad_request(&err, "docker", &pinned);
    }
    let err = n
        .refused("ollama", json!({ "image": "ollama/ollama:latest" }), false)
        .await;
    bad_request(&err, "pinned", "a foreign image without a proof");
    n.stop().await;
}

/// Ruling 42 (4): an `--external` server is a human act, like a cloud kind
/// (Ruling 31) — and the loopback rule of I-4 still holds with the proof.
#[tokio::test]
async fn ollama_external_needs_a_human_and_still_only_loopback() {
    let n = Node::start().await;
    let err = n
        .refused("ollama", json!({"base_url": "http://127.0.0.1:9"}), false)
        .await;
    assert_eq!(err.code, vk_ipc::E_INVARIANT, "{err:?}");
    assert!(err.message.contains("presence proof"), "{err:?}");

    let err = n
        .refused(
            "ollama",
            json!({"base_url": r"http://evil.example\@127.0.0.1:11434"}),
            true,
        )
        .await;
    bad_request(&err, "not this machine", "the backslash URL");
    n.stop().await;
}
