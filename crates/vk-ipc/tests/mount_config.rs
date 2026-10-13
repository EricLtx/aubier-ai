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
    async fn start() -> Node {
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
        let server = tokio::spawn(vk_ipc::server::serve(k.clone(), endpoint.clone()));
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

/// Final review I-1 (Ruling 41, minimum form): the Claude Code binary a
/// request names is held to the rule `vkd-service install` applies to its own
/// `ImagePath` — absolute, there, not in a synced folder, not under a user
/// profile — and refused at mount, over the pipe, before it is ever run.
#[tokio::test]
async fn claude_code_refuses_a_binary_the_daemon_should_not_run() {
    let n = Node::start().await;
    let scratch_dir = tempfile::tempdir().unwrap();
    vk_contracts::testing::guard_state_dir(scratch_dir.path());
    let scratch = Real::of(scratch_dir.path());

    let relative = n
        .refused("claude-code", json!({"binary": "bin/claude"}), true)
        .await;
    bad_request(&relative, "relative", "a relative path");

    let missing = scratch.path().join("nowhere").join("claude");
    let missing = n
        .refused("claude-code", json!({"binary": missing}), true)
        .await;
    bad_request(&missing, "does not exist", "a path with nothing there");

    // A file that is there, in a synced folder. Not executable, so even a
    // daemon that ran it would only fail.
    let synced = scratch.path().join("OneDrive");
    std::fs::create_dir(&synced).unwrap();
    let synced = synced.join("claude.txt");
    std::fs::write(&synced, "not a program").unwrap();
    let synced = n
        .refused("claude-code", json!({"binary": synced}), true)
        .await;
    bad_request(&synced, "synced folder", "a OneDrive path");

    // On Windows the scratch directory is under `%TEMP%`, which is under the
    // profile: the case the service would run as `NT SERVICE\vkd`.
    if cfg!(windows) && vk_store::paths::under_a_user_profile(scratch.path()) {
        let mine = scratch.path().join("claude.txt");
        std::fs::write(&mine, "not a program").unwrap();
        let mine = n
            .refused("claude-code", json!({"binary": mine}), true)
            .await;
        bad_request(&mine, "user profile", "a file under the profile");
    }

    // Ruling 42: the claude-code config is a closed shape. `binary` stays a
    // field this wave, through the refusals above.
    let unknown = n
        .refused(
            "claude-code",
            json!({"binary": "bin/claude", "endpoint": "x"}),
            true,
        )
        .await;
    bad_request(&unknown, "unknown field", "a field the shape does not have");
    n.stop().await;
}

/// A directory spelled with no link in it: on macOS the temp directory is
/// under `/var`, itself a symlink to `/private/var`, which the binary rule
/// refuses on its own; the tests about other refusals start from the real
/// path. On Windows the temp path is used as it is.
struct Real(std::path::PathBuf);

impl Real {
    fn of(p: &std::path::Path) -> Real {
        Real(if cfg!(unix) {
            std::fs::canonicalize(p).unwrap()
        } else {
            p.to_path_buf()
        })
    }
    fn path(&self) -> &std::path::Path {
        &self.0
    }
}

/// I-1 addendum: the refusals run on the *resolved* path. A `..` component,
/// and a symlink or junction anywhere on the way, are refused outright — a
/// link inside an innocent directory that points under a user profile would
/// otherwise pass a check made on the spelling.
#[tokio::test]
async fn claude_code_refuses_a_binary_reached_through_a_link_or_dot_dot() {
    let n = Node::start().await;
    let scratch_dir = tempfile::tempdir().unwrap();
    vk_contracts::testing::guard_state_dir(scratch_dir.path());
    let scratch = Real::of(scratch_dir.path());
    let target = scratch.path().join("target");
    std::fs::create_dir(&target).unwrap();
    std::fs::write(target.join("claude.txt"), "not a program").unwrap();

    let dotted = scratch
        .path()
        .join("target")
        .join("..")
        .join("target")
        .join("claude.txt");
    let err = n
        .refused("claude-code", json!({"binary": dotted}), true)
        .await;
    bad_request(&err, "`..` component", "a path with a .. component");

    let link = scratch.path().join("link");
    #[cfg(unix)]
    std::os::unix::fs::symlink(&target, &link).unwrap();
    #[cfg(windows)]
    {
        // A directory junction: no privilege needed, unlike a symlink.
        let made = std::process::Command::new("cmd")
            .args(["/C", "mklink", "/J"])
            .arg(&link)
            .arg(&target)
            .output()
            .unwrap();
        assert!(made.status.success(), "mklink /J: {made:?}");
    }
    let err = n
        .refused(
            "claude-code",
            json!({"binary": link.join("claude.txt")}),
            true,
        )
        .await;
    bad_request(
        &err,
        "symlink or junction",
        "a binary reached through a link",
    );
    // The junction goes before the directory is removed, so the cleanup
    // cannot follow it.
    #[cfg(windows)]
    std::fs::remove_dir(&link).unwrap();
    n.stop().await;
}

/// I-1 addendum: what is persisted and run is the resolved path, not the
/// request's spelling. Unix only: an accepted path on Windows must be outside
/// every user profile, and the test's scratch directory is inside one.
#[cfg(unix)]
#[tokio::test]
async fn claude_code_persists_the_resolved_binary_path() {
    use std::os::unix::fs::PermissionsExt;
    let n = Node::start().await;
    let scratch_dir = tempfile::tempdir().unwrap();
    vk_contracts::testing::guard_state_dir(scratch_dir.path());
    let scratch = Real::of(scratch_dir.path());
    let fake = scratch.path().join("fake-claude.sh");
    std::fs::write(
        &fake,
        "#!/bin/sh\nif [ \"$1\" = \"--version\" ]; then echo 0.0.0-test; fi\n",
    )
    .unwrap();
    std::fs::set_permissions(&fake, std::fs::Permissions::from_mode(0o700)).unwrap();
    // The same file, spelled with a doubled separator.
    let spelled = format!("{}//fake-claude.sh", scratch.path().display());
    let proof = n.proof().await;
    let mounted =
        n.c.call(
            "arch.mount",
            json!({"kind": "claude-code", "config": {"binary": spelled, "model": "m"}}),
            Some(proof),
        )
        .await
        .unwrap();
    let shown =
        n.c.call("arch.show", json!({"arch_id": mounted["arch_id"]}), None)
            .await
            .unwrap();
    assert_eq!(
        shown["config"]["binary"],
        json!(std::fs::canonicalize(&fake).unwrap()),
        "{shown}"
    );
    n.stop().await;
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
