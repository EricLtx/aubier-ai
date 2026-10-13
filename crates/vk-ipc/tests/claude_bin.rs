//! `vkd --claude-bin` (founder decision 2026-10-13, after final review I-1;
//! Rulings 41 and 43): the Claude Code executable the `claude-code` arch runs
//! is daemon configuration, settled once at start by
//! `vk_ipc::server::claude_binary_on` — which `vkd` and `vkd-service install`
//! both call — and never a request field.
//!
//! These are its refusals and its acceptances. Every one is driven with an
//! explicit `PATH`, so no test reads or writes the process environment, and
//! nothing here is run: the rule is about where the file is, and a stand-in is
//! an empty file.
use std::ffi::OsString;
use std::path::{Path, PathBuf};
use vk_ipc::server::claude_binary_on;

/// A `PATH` with nothing on it.
fn empty_path() -> OsString {
    OsString::new()
}

/// A file at `dir/name`, made executable on Unix. Nothing runs it.
fn file_at(dir: &Path, name: &str) -> PathBuf {
    let p = dir.join(name);
    std::fs::write(&p, b"").unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o700)).unwrap();
    }
    p
}

/// A stand-in `claude` by its full path.
fn stand_in(dir: &Path) -> PathBuf {
    file_at(dir, "fake-claude")
}

/// What a bare `claude` is looked up as on this OS: `claude.exe` on Windows
/// (never the `.cmd` shim), `claude` elsewhere.
fn on_path_name() -> &'static str {
    if cfg!(windows) {
        "claude.exe"
    } else {
        "claude"
    }
}

/// The path the daemon pins for `p`: canonical, with Windows' verbatim prefix
/// removed — computed here independently of the daemon's own helper.
fn resolved(p: &Path) -> PathBuf {
    let c = std::fs::canonicalize(p).unwrap();
    let s = c.to_string_lossy().into_owned();
    PathBuf::from(s.strip_prefix(r"\\?\").map_or(s.clone(), str::to_string))
}

/// A directory link at `link` to `target`: a symlink on Unix, a junction on
/// Windows (no privilege needed, unlike a symlink there).
fn dir_link(target: &Path, link: &Path) {
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

/// Remove a directory link without following it.
fn remove_dir_link(link: &Path) {
    #[cfg(unix)]
    std::fs::remove_file(link).unwrap();
    #[cfg(windows)]
    std::fs::remove_dir(link).unwrap();
}

/// A scratch directory the leak guard knows about.
fn scratch() -> tempfile::TempDir {
    let d = tempfile::tempdir().unwrap();
    vk_contracts::testing::guard_state_dir(d.path());
    d
}

/// `--claude-bin asked` must be refused, for a reason that names the flag
/// and carries `needle`.
fn refused(asked: &Path, as_service: bool, path_var: &OsString, needle: &str) {
    let err = claude_binary_on(Some(asked), as_service, path_var).expect_err(&format!(
        "--claude-bin {} must be refused (as_service = {as_service})",
        asked.display()
    ));
    let msg = format!("{err:#}");
    assert!(
        msg.contains("--claude-bin") && msg.contains(needle),
        "{} (as_service = {as_service}): wanted --claude-bin and {needle:?} in {msg:?}",
        asked.display()
    );
}

/// The refusals every daemon makes, in both modes: a relative path that is
/// not a bare name, a path with nothing there, a `..` component, a link loop
/// (Unix: a Windows symlink needs a privilege the tests do not have), and a
/// bare name the daemon's own `PATH` does not have.
#[test]
fn every_daemon_refuses_a_relative_a_missing_a_dot_dot_and_an_unfound_binary() {
    let d = scratch();
    let bin = stand_in(d.path());
    #[cfg(unix)]
    let looped = {
        let (a, b) = (d.path().join("loop-a"), d.path().join("loop-b"));
        std::os::unix::fs::symlink(&b, &a).unwrap();
        std::os::unix::fs::symlink(&a, &b).unwrap();
        a.join("claude")
    };
    for as_service in [false, true] {
        refused(
            Path::new("bin/claude"),
            as_service,
            &empty_path(),
            "relative",
        );
        refused(
            &d.path().join("nowhere").join("claude"),
            as_service,
            &empty_path(),
            "does not exist",
        );
        refused(
            &d.path().join("x").join("..").join(bin.file_name().unwrap()),
            as_service,
            &empty_path(),
            "`..` component",
        );
        refused(
            Path::new("no-such-claude"),
            as_service,
            &empty_path(),
            "PATH",
        );
        #[cfg(unix)]
        refused(&looped, as_service, &empty_path(), "resolved");
    }
}

/// A bare name is looked up on the daemon's own `PATH` and pinned to the file
/// found there — explicitly or as the default — and an explicit one that is
/// not there is refused. On Windows the lookup is `claude.exe` only: a
/// `claude.cmd` shim alone on the `PATH` is not a Claude Code this daemon
/// runs. The default that is not on the `PATH` at all is a warning, not a
/// refusal: a node starts without Claude Code installed, and a `claude-code`
/// mount then fails saying why.
#[test]
fn a_bare_name_is_found_on_the_daemons_path_and_pinned() {
    let d = scratch();
    let found = file_at(d.path(), on_path_name());
    let path_var = OsString::from(d.path());
    for as_service in [false, true] {
        // Under the service only where the scratch directory is not in a
        // profile — on Windows it is (`%TEMP%`), and that case is below.
        if as_service && cfg!(windows) {
            continue;
        }
        let pinned = claude_binary_on(None, as_service, &path_var).expect("the default is found");
        assert_eq!(pinned, resolved(&found), "as_service = {as_service}");
        let named = claude_binary_on(Some(Path::new("claude")), as_service, &path_var)
            .expect("an explicit bare name is found");
        assert_eq!(named, resolved(&found), "as_service = {as_service}");
    }
    // Not there: the default is kept as it is, an explicit name is refused.
    assert_eq!(
        claude_binary_on(None, false, &empty_path()).unwrap(),
        PathBuf::from("claude")
    );
    refused(Path::new("claude"), false, &empty_path(), "PATH");

    #[cfg(windows)]
    {
        let shims = scratch();
        file_at(shims.path(), "claude.cmd");
        refused(
            Path::new("claude"),
            false,
            &OsString::from(shims.path()),
            "PATH",
        );
    }
}

/// Ruling 43: an interactive daemon is the user's own process and may run the
/// user's own binary — in a synced folder, and on Windows under the profile
/// (`%TEMP%` is there) — and what it pins is the resolved path.
#[test]
fn an_interactive_daemon_accepts_the_users_own_binary() {
    let d = scratch();
    let mine = stand_in(d.path());
    if cfg!(windows) {
        assert!(
            vk_store::paths::under_a_user_profile(&mine),
            "the Windows case this test is for: an absolute path under the profile"
        );
    }
    assert_eq!(
        claude_binary_on(Some(&mine), false, &empty_path()).unwrap(),
        resolved(&mine)
    );
    let synced = d.path().join("OneDrive");
    std::fs::create_dir(&synced).unwrap();
    let synced = stand_in(&synced);
    assert_eq!(
        claude_binary_on(Some(&synced), false, &empty_path()).unwrap(),
        resolved(&synced)
    );
}

/// Ruling 43: under the service, a binary that resolves into a synced folder
/// or a user profile is refused — checked on the resolved path, so a link
/// whose own spelling is clean does not get one through, and a bare name found
/// on the `PATH` is held to the same rule as a path.
#[test]
fn the_service_refuses_a_binary_that_resolves_where_the_user_can_replace_it() {
    let d = scratch();
    let synced = d.path().join("OneDrive");
    std::fs::create_dir(&synced).unwrap();
    let synced_bin = stand_in(&synced);
    refused(&synced_bin, true, &empty_path(), "synced folder");

    // A link spelled without any sync marker, whose target is in OneDrive.
    let link = d.path().join("tools");
    dir_link(&synced, &link);
    refused(
        &link.join(synced_bin.file_name().unwrap()),
        true,
        &empty_path(),
        "synced folder",
    );
    remove_dir_link(&link);

    // Found on the PATH, in OneDrive.
    file_at(&synced, on_path_name());
    refused(
        Path::new("claude"),
        true,
        &OsString::from(&synced),
        "synced folder",
    );

    // On Windows the scratch directory is under `%TEMP%`, inside the profile:
    // what the service would run as `NT SERVICE\vkd` — by its own spelling,
    // and through a link outside every profile whose target is inside one.
    #[cfg(windows)]
    {
        let mine = stand_in(d.path());
        refused(&mine, true, &empty_path(), "user profile");

        let outside = OutsideProfileLink::to(d.path());
        assert!(
            !vk_store::paths::under_a_user_profile(&outside.link),
            "the link's own spelling must be clean: {}",
            outside.link.display()
        );
        refused(
            &outside.link.join(mine.file_name().unwrap()),
            true,
            &empty_path(),
            "user profile",
        );
    }
}

/// A junction under `%ProgramData%` — outside every user profile — whose
/// target is a directory inside one. Removed (the link only, never its
/// target) when it goes out of scope.
#[cfg(windows)]
struct OutsideProfileLink {
    link: PathBuf,
}

#[cfg(windows)]
impl OutsideProfileLink {
    fn to(target: &Path) -> OutsideProfileLink {
        let root = PathBuf::from(
            std::env::var("ProgramData").unwrap_or_else(|_| r"C:\ProgramData".into()),
        );
        let link = root.join(format!(
            "vk-test-claude-bin-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        dir_link(target, &link);
        OutsideProfileLink { link }
    }
}

#[cfg(windows)]
impl Drop for OutsideProfileLink {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir(&self.link);
    }
}

/// Final re-review N-2: under the service, a binary that is not on a local
/// fixed drive — a UNC share, in either spelling — is refused before the
/// daemon reaches for it. Windows only: there is no UNC elsewhere.
#[cfg(windows)]
#[test]
fn the_service_refuses_a_binary_that_is_not_on_a_local_fixed_drive() {
    for unc in [
        r"\\127.0.0.1\vk-no-such-share\claude.exe",
        r"\\?\UNC\127.0.0.1\vk-no-such-share\claude.exe",
    ] {
        refused(Path::new(unc), true, &empty_path(), "local fixed drive");
    }
}

/// Ruling 43: a link is followed, not refused. One whose target is clean is
/// accepted and the target is what is pinned — interactively everywhere, and
/// under the service on Unix (on Windows every scratch directory is under the
/// profile, so the service's clean case is a system binary, below).
#[test]
fn a_link_to_a_clean_target_is_accepted_and_the_target_is_pinned() {
    let modes: &[bool] = if cfg!(windows) {
        &[false]
    } else {
        &[false, true]
    };
    for &as_service in modes {
        let d = scratch();
        let target = d.path().join("real");
        std::fs::create_dir(&target).unwrap();
        let bin = stand_in(&target);
        let link = d.path().join("link");
        dir_link(&target, &link);
        let through = link.join(bin.file_name().unwrap());
        assert_eq!(
            claude_binary_on(Some(&through), as_service, &empty_path()).unwrap(),
            resolved(&bin),
            "as_service = {as_service}"
        );
        remove_dir_link(&link);
    }
}

/// The service's clean case on Windows: an absolute path on a local fixed
/// drive, outside every profile and synced folder, is accepted as it is.
#[cfg(windows)]
#[test]
fn the_service_accepts_a_clean_absolute_path() {
    let system =
        PathBuf::from(std::env::var("SystemRoot").unwrap_or_else(|_| r"C:\Windows".into()))
            .join("System32")
            .join("cmd.exe");
    assert_eq!(
        claude_binary_on(Some(&system), true, &empty_path()).unwrap(),
        resolved(&system)
    );
}
