//! `arch.mount` configs over the pipe (final review I-1 and I-4, Ruling 41;
//! Ruling 42): what a request may say about the binary, the container and the
//! server a local arch runs on, and which of those take a human.
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
    /// An interactive daemon, as every Ollama test here wants.
    async fn start() -> Node {
        Node::start_as(false).await
    }

    /// A daemon in either mode: `as_service` is the `ServerConfig` field
    /// `vkd --as-service` sets (Ruling 43), not a request field.
    async fn start_as(as_service: bool) -> Node {
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
            as_service,
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

/// A stand-in `claude` in `dir` that answers `--version` and nothing else:
/// enough for a mount to probe it. `.cmd` on Windows, a script elsewhere.
fn stand_in(dir: &std::path::Path) -> std::path::PathBuf {
    let bin = dir.join(if cfg!(windows) {
        "fake-claude.cmd"
    } else {
        "fake-claude.sh"
    });
    let script = if cfg!(windows) {
        "@echo off\r\nif \"%~1\"==\"--version\" echo 0.0.0-test\r\nexit /b 0\r\n"
    } else {
        "#!/bin/sh\nif [ \"$1\" = \"--version\" ]; then echo 0.0.0-test; fi\n"
    };
    std::fs::write(&bin, script).unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&bin, std::fs::Permissions::from_mode(0o700)).unwrap();
    }
    bin
}

/// The path the daemon stores for `p`: canonical, with Windows' verbatim
/// prefix removed — computed here independently of the daemon's own helper.
fn resolved(p: &std::path::Path) -> String {
    let c = std::fs::canonicalize(p).unwrap();
    let s = c.to_string_lossy().into_owned();
    match s.strip_prefix(r"\\?\") {
        Some(rest) => rest.to_string(),
        None => s,
    }
}

/// A directory link at `link` to `target`: a symlink on Unix, a junction on
/// Windows (no privilege needed, unlike a symlink there).
fn dir_link(target: &std::path::Path, link: &std::path::Path) {
    #[cfg(unix)]
    std::os::unix::fs::symlink(target, link).unwrap();
    #[cfg(windows)]
    {
        let made = std::process::Command::new("cmd")
            .args(["/C", "mklink", "/J"])
            .arg(link)
            .arg(target)
            .output()
            .unwrap();
        assert!(made.status.success(), "mklink /J: {made:?}");
    }
}

/// Remove a directory link without following it (before the tempdir goes).
fn remove_dir_link(link: &std::path::Path) {
    #[cfg(unix)]
    std::fs::remove_file(link).unwrap();
    #[cfg(windows)]
    std::fs::remove_dir(link).unwrap();
}

impl Node {
    /// Mount a Claude Code arch on `binary` with a proof, and say what the
    /// spec recorded as the binary.
    async fn mounted_binary(&self, binary: &std::path::Path) -> Value {
        let proof = self.proof().await;
        // A model per directory: the arch id hashes the model and the
        // version, so two stand-ins under one model would be one arch.
        let model = binary
            .parent()
            .and_then(|d| d.file_name())
            .map_or("m".into(), |n| n.to_string_lossy().into_owned());
        let mounted = self
            .c
            .call(
                "arch.mount",
                json!({"kind": "claude-code", "config": {"binary": binary, "model": model}}),
                Some(proof),
            )
            .await
            .unwrap_or_else(|e| panic!("{} must be accepted: {e:#}", binary.display()));
        let shown = self
            .c
            .call("arch.show", json!({"arch_id": mounted["arch_id"]}), None)
            .await
            .unwrap();
        shown["config"]["binary"].clone()
    }
}

/// Final review I-1, Ruling 43: the refusals every daemon makes — a relative
/// path, a path with nothing there, a `..` component, a field the closed
/// shape does not have — over the pipe, in both daemon modes.
#[tokio::test]
async fn claude_code_refuses_relative_missing_and_dot_dot_in_every_mode() {
    for as_service in [false, true] {
        let n = Node::start_as(as_service).await;
        let scratch = tempfile::tempdir().unwrap();
        vk_contracts::testing::guard_state_dir(scratch.path());
        let bin = stand_in(scratch.path());
        let mode = if as_service { "service" } else { "interactive" };

        let err = n
            .refused("claude-code", json!({"binary": "bin/claude"}), true)
            .await;
        bad_request(&err, "relative", &format!("{mode}: a relative path"));

        let missing = scratch.path().join("nowhere").join("claude");
        let err = n
            .refused("claude-code", json!({"binary": missing}), true)
            .await;
        bad_request(&err, "does not exist", &format!("{mode}: nothing there"));

        let dotted = scratch
            .path()
            .join("x")
            .join("..")
            .join(bin.file_name().unwrap());
        let err = n
            .refused("claude-code", json!({"binary": dotted}), true)
            .await;
        bad_request(&err, "`..` component", &format!("{mode}: a .. path"));

        // A link loop resolves to nowhere (Unix: a Windows symlink needs a
        // privilege the tests do not have).
        #[cfg(unix)]
        {
            let (a, b) = (scratch.path().join("loop-a"), scratch.path().join("loop-b"));
            std::os::unix::fs::symlink(&b, &a).unwrap();
            std::os::unix::fs::symlink(&a, &b).unwrap();
            let err = n
                .refused("claude-code", json!({"binary": a.join("claude")}), true)
                .await;
            assert_eq!(err.code, vk_ipc::E_BAD_PARAMS, "{mode}: a loop: {err:?}");
        }

        // Ruling 42: the claude-code config is a closed shape.
        let err = n
            .refused(
                "claude-code",
                json!({"binary": "bin/claude", "endpoint": "x"}),
                true,
            )
            .await;
        bad_request(&err, "unknown field", &format!("{mode}: an unknown field"));
        n.stop().await;
    }
}

/// Ruling 43: an interactive daemon is the user's own process and may run the
/// user's own binary — in a synced folder, and on Windows under the profile
/// (`%TEMP%` is there) — and what it stores is the resolved path.
#[tokio::test]
async fn an_interactive_daemon_accepts_the_users_own_binary() {
    let n = Node::start_as(false).await;
    let scratch = tempfile::tempdir().unwrap();
    vk_contracts::testing::guard_state_dir(scratch.path());

    let mine = stand_in(scratch.path());
    if cfg!(windows) {
        assert!(
            vk_store::paths::under_a_user_profile(&mine),
            "the Windows case this test is for: an absolute path under the profile"
        );
    }
    assert_eq!(n.mounted_binary(&mine).await, json!(resolved(&mine)));

    let synced = scratch.path().join("OneDrive");
    std::fs::create_dir(&synced).unwrap();
    let synced = stand_in(&synced);
    assert_eq!(n.mounted_binary(&synced).await, json!(resolved(&synced)));
    n.stop().await;
}

/// Ruling 43: under the service, a binary that resolves into a synced folder
/// or a user profile is refused — checked on the resolved path, so a link
/// whose own spelling is clean does not get one through.
#[tokio::test]
async fn the_service_refuses_a_binary_that_resolves_where_the_user_can_replace_it() {
    let n = Node::start_as(true).await;
    let scratch = tempfile::tempdir().unwrap();
    vk_contracts::testing::guard_state_dir(scratch.path());

    let synced = scratch.path().join("OneDrive");
    std::fs::create_dir(&synced).unwrap();
    let synced_bin = stand_in(&synced);
    let err = n
        .refused("claude-code", json!({"binary": synced_bin}), true)
        .await;
    bad_request(&err, "synced folder", "a OneDrive path");

    // A link spelled without any sync marker, whose target is in OneDrive.
    let link = scratch.path().join("tools");
    dir_link(&synced, &link);
    let through = link.join(synced_bin.file_name().unwrap());
    let err = n
        .refused("claude-code", json!({"binary": through}), true)
        .await;
    bad_request(&err, "synced folder", "a link whose target is in OneDrive");
    remove_dir_link(&link);

    // On Windows the scratch directory is under `%TEMP%`, inside the profile:
    // what the service would run as `NT SERVICE\vkd`.
    if cfg!(windows) {
        let mine = stand_in(scratch.path());
        let err = n
            .refused("claude-code", json!({"binary": mine}), true)
            .await;
        bad_request(&err, "user profile", "a file under the profile");
    }
    n.stop().await;
}

/// Ruling 43: a link is followed, not refused. One whose target is clean is
/// accepted, and the spec records the target. In the service mode on Unix
/// (no Windows profile there) as well as interactively; on Windows the
/// service has no clean target to point at in a test, since every scratch
/// directory is under the profile.
#[tokio::test]
async fn a_link_to_a_clean_target_is_accepted_and_the_target_is_recorded() {
    let modes: &[bool] = if cfg!(windows) {
        &[false]
    } else {
        &[false, true]
    };
    for &as_service in modes {
        let n = Node::start_as(as_service).await;
        let scratch = tempfile::tempdir().unwrap();
        vk_contracts::testing::guard_state_dir(scratch.path());
        let target = scratch.path().join("real");
        std::fs::create_dir(&target).unwrap();
        let bin = stand_in(&target);
        let link = scratch.path().join("link");
        dir_link(&target, &link);
        let through = link.join(bin.file_name().unwrap());
        assert_eq!(
            n.mounted_binary(&through).await,
            json!(resolved(&bin)),
            "as_service = {as_service}"
        );
        remove_dir_link(&link);
        n.stop().await;
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
