//! Driver packaging, the kernel's half (packaging spec §4.2–4.5): the
//! publisher key is made once, by a human, and pinned on the node that made
//! it; `driver.packed` is a human act on the ledger naming the package by its
//! digest; a mount says where its tree came from, and the kernel checks that
//! by path in both directions — an installed manifest outside the install
//! root and a development manifest inside it are both refused, as is the
//! service context this chantier does not ship; an installed mount records
//! its package digest and where its fingerprint cache lives; `tampered` is a
//! word of the closed vocabulary that degrades the driver.
use std::path::{Path, PathBuf};
use vk_contracts::driver::{DriverMounted, DriverSource, ExecutionContext};
use vk_contracts::hash_canonical;
use vk_contracts::syscalls::{Kernel, KernelError};
use vk_kernel::drivers::{DriverFailure, DriverState};
use vk_kernel::RealKernel;

/// The helpers of `tests/drivers.rs`, copied: a kernel over a temp dir, the
/// two principals, and the pilot-shaped manifest.
mod common {
    use std::path::{Path, PathBuf};
    use std::time::Duration;
    use vk_contracts::driver::{
        DriverAction, DriverKind, DriverManifest, DriverProgram, DriverSource, ExecutionContext,
    };
    use vk_contracts::labels::{Clearance, Scope};
    use vk_contracts::principal::Principal;
    use vk_contracts::syscalls::Ctx;
    use vk_kernel::RealKernel;

    pub fn open(dir: &Path) -> RealKernel {
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

    pub fn machine(now: u64) -> Ctx {
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

    pub fn human(now: u64) -> Ctx {
        Ctx {
            principal: Principal::Human {
                device_id: "phone-1".into(),
            },
            ..machine(now)
        }
    }

    pub fn maskai_like() -> DriverManifest {
        DriverManifest {
            name: "maskai-1".into(),
            kind: DriverKind::Browser,
            program: DriverProgram {
                runtime: "python-playwright".into(),
                command: vec!["python".into(), "-I".into(), "driver.py".into()],
                files_sha256: format!("sha256:{}", "0".repeat(64)),
                browser_channel: None,
                headless: true,
                max_memory_bytes: None,
            },
            hosts: vec!["app.maskai.fr".into()],
            actions: vec![DriverAction {
                name: "look".into(),
                reversible: true,
                args: serde_json::json!({ "type": "object", "additionalProperties": false }),
                evidence: vec![],
                to_register: false,
            }],
            min_interval_ms: 0,
            max_action_ms: 60_000,
            clearance: Clearance {
                max_scope: Scope::Business,
                third_party_allowed: true,
            },
            governed: false,
            execution_context: ExecutionContext::Interactive,
            needs_input_desktop: false,
            source: DriverSource::Development,
        }
    }

    pub fn root() -> PathBuf {
        PathBuf::from("drivers/maskai-recette")
    }
}

/// A kernel whose install root is `install_root`, through the builder rather
/// than the process-wide `VK_DRIVER_INSTALL_ROOT`.
fn open_installed(dir: &Path, install_root: &Path) -> RealKernel {
    common::open(dir).with_install_root(install_root)
}

/// `<state_dir>/drivers/<name>/profile`, hashed as the mounted event does.
fn profile_dir_hash(k: &RealKernel, name: &str) -> String {
    vk_contracts::hash_bytes(k.driver_dirs(name).profile.to_string_lossy().as_bytes())
}

#[test]
fn the_publisher_key_is_created_once_self_pinned_and_shown() {
    let t = tempfile::tempdir().unwrap();
    let mut k = common::open(t.path()).with_publisher_key_file(t.path().join("publisher.key"));
    assert!(k.publisher_key_show().unwrap().is_none());
    let info = k.publisher_key_init(&common::human(1)).unwrap();
    assert!(info.pinned);
    assert!(info.key_id.starts_with("sha256:"), "{}", info.key_id);
    assert!(info.public_pem.contains("AUBIER PUBLISHER KEY"));
    assert!(t
        .path()
        .join("capital/publishers")
        .join(format!("{}.pub", info.key_id.trim_start_matches("sha256:")))
        .exists());
    assert!(t.path().join("publisher.key").exists());
    let again = k.publisher_key_init(&common::human(2)).unwrap();
    assert_eq!(again, info, "a second init reports the existing key");
    assert_eq!(k.publisher_key_show().unwrap(), Some(info));
    let e = k.publisher_key_init(&common::machine(3)).unwrap_err();
    assert!(matches!(e, KernelError::I1(_)), "{e}");
    assert!(e.to_string().contains("human act"), "{e}");
}

#[test]
fn driver_packed_is_a_human_act_on_the_ledger_with_the_six_hashes() {
    let t = tempfile::tempdir().unwrap();
    let mut k = common::open(t.path());
    let m = common::maskai_like();
    let row = k
        .driver_packed(&common::human(1), &m, "1.0.0", "sha256:aaaa", "sha256:bbbb")
        .unwrap();
    assert_eq!(row.driver_id, m.driver_id());
    assert_eq!(row.name, "maskai-1");
    assert_eq!(row.version, "1.0.0");
    assert_eq!(row.package_digest, "sha256:aaaa");
    assert_eq!(row.files_sha256, m.program.files_sha256);
    assert_eq!(row.publisher_key_id, "sha256:bbbb");
    assert_eq!(row.by, "phone-1");
    let last = k.ledger().events().last().unwrap().clone();
    assert_eq!(last.kind, "driver.packed");
    assert_eq!(last.payload_hash, hash_canonical(&row));

    let before = k.ledger().events().len();
    let e = k
        .driver_packed(&common::machine(2), &m, "1.0.0", "sha256:a", "sha256:b")
        .unwrap_err();
    assert!(matches!(e, KernelError::I1(_)), "{e}");
    assert!(
        e.to_string()
            .contains("packing a driver is a human act: run it from an enrolled device"),
        "{e}"
    );
    // The context rules hold at pack too, and a version that is not
    // MAJOR.MINOR.PATCH never becomes a path or a ledger field.
    let mut service = common::maskai_like();
    service.execution_context = ExecutionContext::Service;
    let e = k
        .driver_packed(&common::human(3), &service, "1.0.0", "sha256:a", "sha256:b")
        .unwrap_err();
    assert!(e.to_string().contains("ships with vk-agent"), "{e}");
    for bad in ["", "1.0", "1.0.0-rc1", "../../x", "1..0"] {
        let e = k
            .driver_packed(&common::human(4), &m, bad, "sha256:a", "sha256:b")
            .unwrap_err();
        assert!(matches!(e, KernelError::Gate(_)), "{bad:?}: {e}");
        assert!(e.to_string().contains("remedy:"), "{bad:?}: {e}");
    }
    assert_eq!(k.ledger().events().len(), before, "a refusal logs nothing");
}

#[test]
fn a_service_context_is_refused_at_mount_with_the_remedy() {
    let t = tempfile::tempdir().unwrap();
    let mut k = common::open(t.path());
    let mut m = common::maskai_like();
    m.execution_context = ExecutionContext::Service;
    m.program.headless = true;
    let e = k
        .mount_driver(&common::human(1), m, common::root(), true, None)
        .unwrap_err();
    assert!(matches!(e, KernelError::I1(_)), "{e}");
    assert!(
        e.to_string().contains(
            "the service mode ships with vk-agent; remedy: execution_context interactive"
        ),
        "{e}"
    );
    assert!(k.drivers().unwrap().is_empty());
}

#[test]
fn source_is_verified_by_path_in_both_directions() {
    let t = tempfile::tempdir().unwrap();
    let install_root = t.path().join("pf").join("Aubier").join("drivers");
    std::fs::create_dir_all(&install_root).unwrap();
    let mut k = open_installed(t.path(), &install_root);
    assert_eq!(k.install_root(), install_root.as_path());

    // A manifest that says installed, from a development directory.
    let mut m = common::maskai_like();
    m.source = DriverSource::Installed;
    let e = k
        .mount_driver(
            &common::human(1),
            m.clone(),
            common::root(),
            true,
            Some("sha256:dd".into()),
        )
        .unwrap_err();
    assert!(matches!(e, KernelError::I1(_)), "{e}");
    assert!(e.to_string().contains("must live under"), "{e}");
    assert!(
        e.to_string()
            .contains("remedy: install the package with vk driver install"),
        "{e}"
    );
    // Nor can a `..` walk out of the install root and still claim it.
    let escaped = install_root
        .join("maskai-1")
        .join("..")
        .join("..")
        .join("..");
    let e = k
        .mount_driver(
            &common::human(2),
            m.clone(),
            escaped,
            true,
            Some("sha256:dd".into()),
        )
        .unwrap_err();
    assert!(e.to_string().contains("must live under"), "{e}");

    // A development manifest from inside the install root.
    let installed = install_root.join("maskai-1").join("1.0.0");
    std::fs::create_dir_all(&installed).unwrap();
    let mut dev = common::maskai_like();
    dev.source = DriverSource::Development;
    let e = k
        .mount_driver(&common::human(3), dev, installed, true, None)
        .unwrap_err();
    assert!(matches!(e, KernelError::I1(_)), "{e}");
    assert!(e.to_string().contains("mounts by name@version"), "{e}");
    assert!(
        e.to_string()
            .contains("remedy: vk mount driver maskai-1@1.0.0"),
        "{e}"
    );
    assert!(k.drivers().unwrap().is_empty());
}

#[test]
fn an_installed_mount_records_the_package_digest_and_source() {
    let t = tempfile::tempdir().unwrap();
    let install_root = t.path().join("drivers-installed");
    let mut k = open_installed(t.path(), &install_root);
    let installed = install_root.join("maskai-1").join("1.0.0");
    std::fs::create_dir_all(&installed).unwrap();
    let mut m = common::maskai_like();
    m.source = DriverSource::Installed;
    let row = k
        .mount_driver(
            &common::human(1),
            m,
            installed.clone(),
            true,
            Some("sha256:dd".into()),
        )
        .unwrap();
    assert_eq!(row.package_digest.as_deref(), Some("sha256:dd"));
    assert_eq!(
        row.fingerprint_cache,
        Some(
            t.path()
                .join("drivers")
                .join("maskai-1")
                .join("fingerprint.json")
        )
    );
    assert_eq!(k.driver("maskai-1").unwrap(), Some(row.clone()));
    let last = k.ledger().events().last().unwrap().clone();
    assert_eq!(last.kind, "driver.mounted");
    assert_eq!(
        last.payload_hash,
        hash_canonical(&DriverMounted {
            driver_id: row.driver_id.clone(),
            name: "maskai-1".into(),
            manifest_hash: hash_canonical(&row.manifest),
            profile_dir_hash: profile_dir_hash(&k, "maskai-1"),
            governed: true,
            by: "phone-1".into(),
            package_digest: Some("sha256:dd".into()),
            source: DriverSource::Installed,
        }),
        "the mounted event carries the package digest and the source"
    );

    // The same package again is the same mount (Ruling 13); another package
    // of the same driver is refused while this one is ready.
    let mut m = common::maskai_like();
    m.source = DriverSource::Installed;
    let again = k
        .mount_driver(
            &common::human(2),
            m.clone(),
            installed.clone(),
            true,
            Some("sha256:dd".into()),
        )
        .unwrap();
    assert_eq!(again, row);
    let events = k.ledger().events().len();
    let e = k
        .mount_driver(
            &common::human(3),
            m.clone(),
            installed.clone(),
            true,
            Some("sha256:ee".into()),
        )
        .unwrap_err();
    assert!(matches!(e, KernelError::Gate(_)), "{e}");
    assert!(e.to_string().contains("umount"), "{e}");
    // An installed mount names its package, and only an installed one does.
    let e = k
        .mount_driver(&common::human(4), m, installed, true, None)
        .unwrap_err();
    assert!(matches!(e, KernelError::Gate(_)), "{e}");
    assert!(e.to_string().contains("package digest"), "{e}");
    assert_eq!(k.ledger().events().len(), events);
}

#[test]
fn a_development_mount_names_no_package_and_keeps_no_fingerprint_cache() {
    let t = tempfile::tempdir().unwrap();
    let mut k = common::open(t.path());
    let e = k
        .mount_driver(
            &common::human(1),
            common::maskai_like(),
            common::root(),
            true,
            Some("sha256:dd".into()),
        )
        .unwrap_err();
    assert!(matches!(e, KernelError::Gate(_)), "{e}");
    assert!(e.to_string().contains("package digest"), "{e}");
    let row = k
        .mount_driver(
            &common::human(2),
            common::maskai_like(),
            common::root(),
            true,
            None,
        )
        .unwrap();
    assert_eq!(row.package_digest, None);
    assert_eq!(row.fingerprint_cache, None);
    let last = k.ledger().events().last().unwrap().clone();
    assert_eq!(
        last.payload_hash,
        hash_canonical(&DriverMounted {
            driver_id: row.driver_id.clone(),
            name: "maskai-1".into(),
            manifest_hash: hash_canonical(&row.manifest),
            profile_dir_hash: profile_dir_hash(&k, "maskai-1"),
            governed: true,
            by: "phone-1".into(),
            package_digest: None,
            source: DriverSource::Development,
        })
    );
}

/// A row written before packaging — no `package_digest`, no
/// `fingerprint_cache` — still reads, as a development mount.
#[test]
fn an_old_driver_row_still_reads() {
    let t = tempfile::tempdir().unwrap();
    let mut k = common::open(t.path());
    let row = k
        .mount_driver(
            &common::human(1),
            common::maskai_like(),
            common::root(),
            true,
            None,
        )
        .unwrap();
    let mut old = serde_json::to_value(&row).unwrap();
    let fields = old.as_object_mut().unwrap();
    assert!(fields.remove("package_digest").is_some());
    assert!(fields.remove("fingerprint_cache").is_some());
    let read: vk_kernel::drivers::DriverRow = serde_json::from_value(old).unwrap();
    assert_eq!(read, row);
}

#[test]
fn tampered_degrades_the_driver_and_is_a_closed_word() {
    let t = tempfile::tempdir().unwrap();
    let mut k = common::open(t.path());
    k.mount_driver(
        &common::human(1),
        common::maskai_like(),
        common::root(),
        true,
        None,
    )
    .unwrap();
    assert_eq!(DriverFailure::Tampered.reason(), "tampered");
    assert!(vk_kernel::drivers::DRIVER_FAILURE_REASONS.contains(&"tampered"));
    k.degrade_driver("maskai-1", &DriverFailure::Tampered.reason())
        .unwrap();
    let row = k
        .drivers()
        .unwrap()
        .into_iter()
        .find(|r| r.manifest.name == "maskai-1")
        .unwrap();
    assert!(matches!(row.state, DriverState::Degraded { ref reason } if reason == "tampered"));
    let last = k.ledger().events().last().unwrap().clone();
    assert_eq!(last.kind, "driver.unmounted");
}

/// The default install root, when nothing overrides it: under Program Files
/// on Windows, `/opt/aubier/drivers` elsewhere.
#[test]
fn the_install_root_defaults_under_program_files() {
    if std::env::var_os("VK_DRIVER_INSTALL_ROOT").is_some() {
        eprintln!("skipped: VK_DRIVER_INSTALL_ROOT is set for this run");
        return;
    }
    let t = tempfile::tempdir().unwrap();
    let k = common::open(t.path());
    let expected: PathBuf = if cfg!(windows) {
        PathBuf::from(std::env::var_os("ProgramFiles").unwrap())
            .join("Aubier")
            .join("drivers")
    } else {
        PathBuf::from("/opt/aubier/drivers")
    };
    assert_eq!(k.install_root(), expected.as_path());
}
