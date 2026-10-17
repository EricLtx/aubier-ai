//! `vk driver install` and `vk driver uninstall` (packaging spec §4.7, D7,
//! D9): the elevated steps. They are OS acts by an administrator, off the
//! ledger, so they need no daemon and carry no presence proof — like `vk
//! boot`, `vk man` and `vk secret`, they run in this process. The mount
//! that follows is the human act the kernel records, with the package
//! digest this install verified.
//!
//! The install root is the kernel's own (`VK_DRIVER_INSTALL_ROOT`, else
//! `%ProgramFiles%\Aubier\drivers`), and a publisher is pinned where the
//! daemon looks for it: its state directory (`--state-dir`, else this
//! user's, as `vk boot` resolves it).
use crate::{endpoint, plain_path, render, show, Cli};
use anyhow::{anyhow, bail, Context, Result};
use clap::Args;
use serde_json::json;
use std::io::BufRead;
use std::path::{Path, PathBuf};
use vk_capital::install::Tofu;
use vk_capital::signature::{PinnedKey, Pins};
use vk_capital::CapitalError;
use vk_ipc::client::Client;

/// `vk driver install`'s arguments.
#[derive(Args)]
pub struct InstallArgs {
    /// The package: the directory `vk driver pack` wrote.
    #[arg(value_name = "PKG")]
    package: PathBuf,
    /// Pin a publisher this node has not pinned without asking — for CI. Its
    /// key is printed all the same.
    #[arg(long)]
    yes: bool,
    /// The daemon's state directory, where the publisher is pinned (default:
    /// this user's, as `vk boot` resolves it).
    #[arg(long, value_name = "DIR")]
    state_dir: Option<PathBuf>,
}

/// `vk driver uninstall`'s arguments.
#[derive(Args)]
pub struct UninstallArgs {
    /// The installed version, as `vk driver ls` lists it: `NAME@VERSION`.
    #[arg(value_name = "NAME@VERSION")]
    spec: String,
}

/// `vk driver install PKG`: refused unless this shell may write under the
/// install root; otherwise the package is verified, materialised, checked
/// against its fingerprint and renamed into place by
/// [`vk_capital::install::install`], and the publisher pinned in the
/// daemon's state directory when the administrator confirms it.
///
/// Elevated, the root must be the administrators' alone
/// ([`administrators_only`]), and the tree is read-only to everyone but the
/// administrators and SYSTEM from its first byte: `<name>` and `.part` are
/// created with that DACL ([`make_tree_dir`]) before the interpreter is
/// extracted and run in them. A non-elevated install (a root this account
/// may write, through `VK_DRIVER_INSTALL_ROOT`: the test seam) makes plain
/// directories under the root's own ACL: a tree its owner may rewrite is
/// not protected by any DACL it sets, and nothing in it runs elevated.
pub fn install(cli: &Cli, args: &InstallArgs) -> Result<()> {
    let root = vk_kernel::default_install_root();
    writable(&root, "install")?;
    let elevated = elevated()?;
    if elevated {
        administrators_only(&root)?;
    }
    let state_dir = args.state_dir.as_deref().map(absolute).transpose()?;
    let state_dir = vk_store::paths::resolve_state_dir(state_dir)?;
    let pins = Pins::at(&state_dir);
    let package = absolute(&args.package)?;
    let make_dir = |dir: &Path| make_tree_dir(dir, elevated);
    let yes = args.yes;
    let mut confirm = |key: &PinnedKey| ask(key, yes);
    let report = vk_capital::install::install(
        &package,
        &root,
        &pins,
        Tofu::Confirm(&mut confirm),
        &pinned_by()?,
        &make_dir,
    )
    .map_err(refusal)?;
    let publishers = state_dir.join("capital").join("publishers");
    show(
        cli,
        json!({
            "name": report.name,
            "version": report.version,
            "target": plain_path(&report.target),
            "package_digest": report.package_digest,
            "fingerprint": report.fingerprint,
            "publisher_key_id": report.publisher_key_id,
            "pinned_now": report.pinned_now,
            "pins": plain_path(&publishers),
            "read_only": elevated && cfg!(windows),
        }),
        render::installed,
    )
}

/// `vk driver uninstall NAME@VERSION`: refused while the daemon on this
/// endpoint reports that version mounted (no daemon answering: nothing is
/// mounted), and unless this shell may write where the tree is.
pub async fn uninstall(cli: &Cli, args: &UninstallArgs) -> Result<()> {
    let Some((name, version)) = args
        .spec
        .split_once('@')
        .filter(|(n, v)| vk_contracts::driver::is_driver_name(n) && vk_capital::is_version(v))
    else {
        bail!(
            "{:?} is not name@version; remedy: vk driver uninstall <name>@<MAJOR.MINOR.PATCH>",
            args.spec
        );
    };
    let root = vk_kernel::default_install_root();
    let mounted = mounted(cli, name, version).await?;
    let target = root.join(name).join(version);
    if !mounted && target.is_dir() {
        writable(&root.join(name), "uninstall")?;
    }
    vk_capital::install::uninstall(&root, name, version, mounted).map_err(refusal)?;
    show(
        cli,
        json!({ "name": name, "version": version, "target": plain_path(&target) }),
        render::uninstalled,
    )
}

/// Whether the daemon answering on this shell's endpoint reports
/// `name@version` mounted: one of its `driver.ls` rows names it, in any
/// state but `installed`. No daemon there — the endpoint does not exist —
/// means nothing is mounted; a daemon this shell cannot ask is a refusal.
async fn mounted(cli: &Cli, name: &str, version: &str) -> Result<bool> {
    let ep = endpoint(cli);
    let c = match Client::connect(&ep).await {
        Ok(c) => c,
        Err(e) if no_daemon(&e) => return Ok(false),
        Err(e) => bail!(
            "the daemon on {} cannot be asked what is mounted ({e:#}); remedy: start or stop the \
             daemon, then retry",
            ep.0
        ),
    };
    let rows = c
        .call("driver.ls", json!({}), None)
        .await
        .with_context(|| format!("ask the daemon on {} what is mounted", ep.0))?;
    Ok(rows.as_array().is_some_and(|rows| {
        rows.iter().any(|r| {
            r["name"] == name && r["version"] == version && r["state"]["state"] != "installed"
        })
    }))
}

/// Whether a failed connect means there is no daemon on the endpoint: the
/// pipe (or the socket) is not there. Anything else — a busy pipe, a
/// refused connection, a pipe served by another account — is a daemon this
/// shell cannot ask, and an uninstall is not decided without it.
fn no_daemon(e: &anyhow::Error) -> bool {
    e.downcast_ref::<std::io::Error>()
        .is_some_and(|io| io.kind() == std::io::ErrorKind::NotFound)
}

/// Refused when non-administrators may write the install root: an elevated
/// install extracts the interpreter there and runs it as an administrator,
/// so whoever can write the root could put their code between the two
/// (review fix 1). Windows: no allow entry may give Everyone,
/// Authenticated Users, the built-in Users or INTERACTIVE a write right.
fn administrators_only(root: &Path) -> Result<()> {
    #[cfg(windows)]
    if let Some((sid, group)) = vk_store::win_acl::non_admin_writer(root)? {
        bail!(
            "the install root {} can be written by {group} ({sid}), not only by administrators: \
             an elevated install would run what they put there; remedy: install under the \
             default root, %ProgramFiles%\\Aubier\\drivers (VK_DRIVER_INSTALL_ROOT unset), or \
             give this root the administrators-only ACL of %ProgramFiles%",
            plain_path(root)
        );
    }
    #[cfg(not(windows))]
    let _ = root;
    Ok(())
}

/// The directory maker of an install: `<name>` (when it is new) and
/// `.part`, each of which must not exist yet. Elevated, on Windows, the
/// read-only tree DACL from creation on (`create_readonly_tree_dir`, which
/// never adopts an existing directory), so that the interpreter and pip run
/// as an administrator never sit in a directory another account can write;
/// otherwise a plain directory.
fn make_tree_dir(dir: &Path, elevated: bool) -> vk_capital::error::Result<()> {
    #[cfg(windows)]
    if elevated {
        return vk_store::win_acl::create_readonly_tree_dir(dir).map_err(|e| {
            match e.downcast_ref::<std::io::Error>().map(std::io::Error::kind) {
                Some(std::io::ErrorKind::AlreadyExists) => {
                    CapitalError::Io(std::io::ErrorKind::AlreadyExists.into())
                }
                _ => CapitalError::Refused(format!(
                    "{} could not be created read-only ({e:#}); remedy: run vk driver install \
                     from an elevated shell",
                    plain_path(dir)
                )),
            }
        });
    }
    let _ = elevated;
    std::fs::create_dir(dir)?;
    Ok(())
}

/// Refused, with nothing written, unless this shell may create `dir` and
/// write in it: the one line an administrator who forgot to elevate reads.
fn writable(dir: &Path, verb: &str) -> Result<()> {
    let probe = || -> std::io::Result<()> {
        std::fs::create_dir_all(dir)?;
        let file = dir.join(format!(".vk-{verb}-probe-{}", std::process::id()));
        std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&file)?;
        std::fs::remove_file(&file)
    };
    match probe() {
        Ok(()) => Ok(()),
        Err(e) if e.kind() == std::io::ErrorKind::PermissionDenied => bail!(
            "vk driver {verb} writes under {}: run it from an elevated shell",
            plain_path(dir)
        ),
        Err(e) => Err(e).with_context(|| format!("prepare {}", plain_path(dir))),
    }
}

/// The TOFU prompt (packaging spec D8): the key's id and its PEM, then a
/// question answered by one line of stdin — `y` or `yes` pins it, anything
/// else, an empty line or no input at all refuses it. `--yes` answers for
/// CI. On stderr, so that `--json` keeps stdout to the report.
fn ask(key: &PinnedKey, yes: bool) -> bool {
    eprintln!("publisher key {}", key.key_id);
    eprintln!("{}", key.pem().trim_end());
    eprint!("pin this publisher on this node? [y/N] ");
    if yes {
        eprintln!("y (--yes)");
        return true;
    }
    let mut line = String::new();
    if std::io::stdin().lock().read_line(&mut line).is_err() {
        return false;
    }
    matches!(line.trim().to_ascii_lowercase().as_str(), "y" | "yes")
}

/// Whether this shell runs elevated (Windows). Off Windows a package cannot
/// run (its interpreter is a Windows build), and the question does not
/// arise.
fn elevated() -> Result<bool> {
    #[cfg(windows)]
    return vk_store::win_acl::process_is_elevated();
    #[cfg(not(windows))]
    Ok(false)
}

/// Who pins a publisher, for the pin's sidecar: this process's user SID on
/// Windows (an elevated shell has the desktop user's SID), the user name
/// elsewhere.
fn pinned_by() -> Result<String> {
    #[cfg(windows)]
    return vk_store::win_acl::current_process_sid();
    #[cfg(not(windows))]
    Ok(std::env::var("USER").unwrap_or_else(|_| "unknown".into()))
}

fn absolute(p: &Path) -> Result<PathBuf> {
    std::path::absolute(p).with_context(|| format!("make {} absolute", p.display()))
}

/// A `vk-capital` failure as this shell prints it: a refusal is already one
/// line in the kernel's voice ending in its remedy; anything else is the
/// disk.
fn refusal(e: CapitalError) -> anyhow::Error {
    match e {
        CapitalError::Refused(why) => anyhow!(why),
        CapitalError::Io(e) => {
            anyhow!(e).context("the install root or the package could not be written or read")
        }
        CapitalError::Json(e) => anyhow!(e).context("a package component did not encode"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Only an endpoint that is not there means no daemon: a refused or
    /// busy connection, or a pipe this shell may not open, is a daemon it
    /// cannot ask (review fix 4).
    #[test]
    fn only_a_missing_endpoint_means_no_daemon() {
        let io = |kind: std::io::ErrorKind| anyhow::Error::new(std::io::Error::from(kind));
        assert!(no_daemon(&io(std::io::ErrorKind::NotFound)));
        for kind in [
            std::io::ErrorKind::ConnectionRefused,
            std::io::ErrorKind::PermissionDenied,
            std::io::ErrorKind::TimedOut,
        ] {
            assert!(!no_daemon(&io(kind)), "{kind:?}");
        }
        assert!(!no_daemon(&anyhow!("pipe busy")));
    }

    /// An elevated install refuses a root a broad group may write, and
    /// takes one that only this account, SYSTEM and the administrators may
    /// (review fix 1).
    #[cfg(windows)]
    #[test]
    fn an_elevated_install_refuses_a_root_a_broad_group_may_write() {
        let t = tempfile::tempdir().unwrap();
        administrators_only(t.path()).unwrap();
        let open = t.path().join("open");
        std::fs::create_dir(&open).unwrap();
        let granted = std::process::Command::new("icacls")
            .arg(&open)
            .args(["/grant", "*S-1-5-11:(OI)(CI)M"])
            .stdout(std::process::Stdio::null())
            .status()
            .unwrap()
            .success();
        assert!(granted, "icacls");
        let e = administrators_only(&open).unwrap_err().to_string();
        assert!(e.contains("Authenticated Users (S-1-5-11)"), "{e}");
        assert!(e.contains("; remedy: "), "{e}");
        assert!(e.contains(r"%ProgramFiles%\Aubier\drivers"), "{e}");
        assert_eq!(e.lines().count(), 1, "{e}");
    }
}
