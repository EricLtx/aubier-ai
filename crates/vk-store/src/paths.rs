//! State directory resolution (plan Global Constraints): local app data, never a sync folder.
use anyhow::{bail, Context, Result};
use std::ffi::OsStr;
use std::path::{Path, PathBuf};

const SYNC_MARKERS: &[&str] = &[
    "onedrive",
    "dropbox",
    "icloud",
    "com~apple~clouddocs",
    "google drive",
    "googledrive",
];

pub fn state_dir(override_dir: Option<PathBuf>) -> Result<PathBuf> {
    let dir = resolve_state_dir(override_dir)?;
    private_dir(&dir).with_context(|| format!("create {}", dir.display()))?;
    Ok(dir)
}

/// Create `dir` if it is missing and, on Unix, make it — new or not —
/// reachable by its owner alone (`0700`). Registers, the ledger and released
/// plaintext all live under the state directory, and on a shared machine
/// every other account could otherwise read them. Windows leaves it to the
/// ACL of the parent, which is per user for `%LOCALAPPDATA%` — and, under
/// the service, to `win_acl`'s own audit, which this does not weaken.
///
/// A directory that is already there must be **this user's** (SP1a review
/// M6), and that is checked before its mode is touched: a `0700` directory
/// another account made looks right and is not, and `chmod` on it would fail
/// with a message about permissions when the fact is ownership. And it must
/// be a directory, not a link to one (Task 10 review, Minor 3): a link owned
/// by another account that points at a `0700` directory of this user's would
/// pass both checks through `metadata`, and its owner can re-point it later —
/// so the path is read with `symlink_metadata` and a link is refused as one.
pub fn private_dir(dir: &Path) -> std::io::Result<()> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::{DirBuilderExt, MetadataExt, PermissionsExt};
        if let Ok(meta) = std::fs::symlink_metadata(dir) {
            if meta.file_type().is_symlink() {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::PermissionDenied,
                    format!("{} is a symbolic link; refusing to use it", dir.display()),
                ));
            }
            // SAFETY: `geteuid` reads the process's effective uid and nothing else.
            let me = unsafe { libc::geteuid() };
            if meta.uid() != me {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::PermissionDenied,
                    format!(
                        "{} is owned by uid {}, not this user (uid {me}); refusing to use it",
                        dir.display(),
                        meta.uid()
                    ),
                ));
            }
        }
        std::fs::DirBuilder::new()
            .recursive(true)
            .mode(0o700)
            .create(dir)?;
        std::fs::set_permissions(dir, std::fs::Permissions::from_mode(0o700))?;
    }
    #[cfg(not(unix))]
    std::fs::create_dir_all(dir)?;
    Ok(())
}

/// `OpenOptions` for a file the store creates: born `0600` on Unix rather
/// than briefly world-readable between creation and a later `chmod`. The
/// caller still chooses the access mode and the disposition.
#[cfg(unix)]
pub fn private_file_options() -> std::fs::OpenOptions {
    use std::os::unix::fs::OpenOptionsExt;
    let mut opts = std::fs::OpenOptions::new();
    opts.mode(0o600);
    opts
}

/// Windows: the file takes the ACL of its directory, which is per user for
/// `%LOCALAPPDATA%`; there is no mode to set.
#[cfg(not(unix))]
pub fn private_file_options() -> std::fs::OpenOptions {
    std::fs::OpenOptions::new()
}

/// Owner-only (`0600`) on Unix for a file that already exists — one a
/// previous version created with the umask, or one SQLite made beside its
/// database. A no-op on Windows.
pub fn restrict_file(path: &Path) -> std::io::Result<()> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600))?;
    }
    #[cfg(not(unix))]
    let _ = path;
    Ok(())
}

/// Where the state directory *would* be: resolved and refused for the same
/// reasons as `state_dir`, but nothing is created. A caller that may still
/// decline to use it (`vk boot`, which first asks what the daemon already on
/// this endpoint is serving) must not leave an empty directory behind when it
/// refuses.
pub fn resolve_state_dir(override_dir: Option<PathBuf>) -> Result<PathBuf> {
    let dir = match override_dir {
        Some(d) => resolve_override(&d, &std::env::current_dir()?),
        None => directories::ProjectDirs::from("ai", "VerticalAI", "vk")
            .context("no home directory")?
            .data_local_dir()
            .to_path_buf(),
    };
    refuse_sync_folder(&dir)?;
    Ok(dir)
}

/// Absolutise a `--state-dir` override against `cwd` without touching the filesystem
/// (an override can be a path that doesn't exist yet, so `Path::canonicalize` won't do).
/// An empty `raw` therefore resolves to `cwd` itself.
pub fn resolve_override(raw: &Path, cwd: &Path) -> PathBuf {
    if raw.is_absolute() {
        raw.to_path_buf()
    } else {
        cwd.join(raw)
    }
}

/// `C:\Users\…`, by either name the machine knows it under.
///
/// The rule `vkd-service install` applies to its own `ImagePath` and
/// `arch.mount claude-code` applies to a `binary` it is asked to run (final
/// review I-1, Ruling 41): a file under a user profile is one that user's
/// processes can replace, and the daemon — under the service, `NT SERVICE\vkd`
/// — would run the replacement at the next start or call. One predicate for
/// both, here beside [`refuse_sync_folder`], the other half of the same rule.
/// A pure function of the spelling and the environment: on Unix nothing is
/// under a Windows profile, so it is `false` there.
pub fn under_a_user_profile(p: &Path) -> bool {
    let lower = p.to_string_lossy().to_lowercase().replace('/', "\\");
    let roots = [
        std::env::var("USERPROFILE").ok(),
        std::env::var("PUBLIC").ok(),
        std::env::var("SystemDrive")
            .ok()
            .map(|d| format!("{d}\\Users")),
        Some(r"c:\users".into()),
    ];
    roots.into_iter().flatten().any(|root| {
        let root = root.to_lowercase().replace('/', "\\");
        !root.is_empty() && lower.starts_with(&format!("{}\\", root.trim_end_matches('\\')))
    })
}

/// Public because it is not only the state directory that must not live in a
/// synced folder: `vkd-service install` refuses to register a binary from one
/// too, since OneDrive may replace or dehydrate the file a service starts from.
pub fn refuse_sync_folder(dir: &Path) -> Result<()> {
    if let Some(m) = sync_marker_in(dir) {
        bail!("state directory {} is inside a synced folder ({m}); use --state-dir to choose a local path", dir.display());
    }
    // And through any junction or symlink on the way (SP1a review M9): the
    // deepest ancestor that exists is canonicalised, the part that does not
    // exist yet is put back on top, and the markers are matched again.
    if let Some(real) = canonical_form(dir) {
        if real != dir {
            if let Some(m) = sync_marker_in(&real) {
                bail!(
                    "state directory {} resolves to {}, which is inside a synced folder ({m}); \
                     use --state-dir to choose a local path",
                    dir.display(),
                    real.display()
                );
            }
        }
    }
    Ok(())
}

/// The first sync marker in `dir`'s spelling, if any.
fn sync_marker_in(dir: &Path) -> Option<&'static str> {
    let lower = dir.to_string_lossy().to_lowercase().replace('\\', "/");
    SYNC_MARKERS.iter().copied().find(|m| lower.contains(m))
}

/// `dir` with every link in its existing part resolved: the deepest existing
/// ancestor canonicalised, and the components below it — the ones that do
/// not exist yet — joined back on. `None` when nothing of it exists, or when
/// a component is not a plain name (`..`), in which case the raw check above
/// is all there is.
fn canonical_form(dir: &Path) -> Option<PathBuf> {
    let mut existing = dir;
    let mut rest: Vec<&OsStr> = Vec::new();
    while !existing.exists() {
        rest.push(existing.file_name()?);
        existing = existing.parent()?;
    }
    let mut real = std::fs::canonicalize(existing).ok()?;
    for part in rest.iter().rev() {
        real.push(part);
    }
    Some(real)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Moved from `vk-service` with the predicate (final review I-1).
    #[test]
    fn a_path_under_a_user_profile_is_recognised_by_either_spelling() {
        let mut inside: Vec<String> = vec![
            r"C:\Users\someone\.cargo-target\release\vkd-service.exe".into(),
            r"c:\users\someone\vkd-service.exe".into(),
            r"C:/Users/someone/vkd-service.exe".into(),
        ];
        if let Ok(home) = std::env::var("USERPROFILE") {
            inside.push(format!(r"{home}\build\release\vkd-service.exe"));
        }
        for p in &inside {
            assert!(under_a_user_profile(Path::new(p)), "{p}");
        }
        for outside in [
            r"C:\Program Files\VerticalAI\vkd-service.exe",
            r"C:\vk\vkd-service.exe",
            r"D:\opt\vk\vkd-service.exe",
            // Not a profile: a directory that merely starts the same way.
            r"C:\UsersBackup\vkd-service.exe",
            "/usr/local/bin/claude",
        ] {
            assert!(!under_a_user_profile(Path::new(outside)), "{outside}");
        }
    }

    #[test]
    fn refuses_sync_folders() {
        for bad in [
            "C:/Users/x/OneDrive/vk",
            "/home/x/Dropbox/vk",
            "/Users/x/Library/Mobile Documents/com~apple~CloudDocs/vk",
        ] {
            assert!(
                state_dir(Some(std::path::PathBuf::from(bad))).is_err(),
                "{bad} must be refused"
            );
        }
    }
    #[test]
    fn accepts_explicit_local_dir() {
        let d = tempfile::tempdir().unwrap();
        assert_eq!(state_dir(Some(d.path().to_path_buf())).unwrap(), d.path());
    }
    #[test]
    fn relative_override_inside_synced_cwd_is_refused() {
        let resolved =
            resolve_override(Path::new("vk-data"), Path::new("C:/Users/x/OneDrive/repo"));
        assert!(
            refuse_sync_folder(&resolved).is_err(),
            "relative override under a synced cwd must be refused"
        );
    }
    #[test]
    fn absolute_override_is_kept() {
        let d = tempfile::tempdir().unwrap();
        let resolved = resolve_override(d.path(), Path::new("C:/some/other/cwd"));
        assert_eq!(resolved, d.path());
    }
    #[test]
    fn resolving_does_not_create_the_directory() {
        let d = tempfile::tempdir().unwrap();
        let wanted = d.path().join("not-yet");
        assert_eq!(resolve_state_dir(Some(wanted.clone())).unwrap(), wanted);
        assert!(!wanted.exists(), "resolution must not create anything");
        assert_eq!(state_dir(Some(wanted.clone())).unwrap(), wanted);
        assert!(wanted.exists(), "but opening it does");
    }

    /// A state directory another account made is refused by name (review
    /// M6), before its mode is touched: `/tmp` is root's, so it is "not
    /// yours", not "permission denied on chmod". Skipped as root, where
    /// `/tmp` *is* the caller's and this test would then set its mode. The
    /// path is `/tmp` canonicalised: on macOS `/tmp` is a link to
    /// `/private/tmp`, and a link is refused as a link before its owner is
    /// asked (Minor 3) — this test is about the owner.
    #[cfg(unix)]
    #[test]
    fn a_directory_owned_by_another_user_is_refused_by_name() {
        // SAFETY: `geteuid` reads the process's effective uid and nothing else.
        if unsafe { libc::geteuid() } == 0 {
            eprintln!("running as root: skipped");
            return;
        }
        let tmp = std::fs::canonicalize("/tmp").unwrap();
        let err = private_dir(&tmp).expect_err("/tmp is not this user's");
        let msg = err.to_string();
        assert!(msg.contains("owned by uid 0"), "{msg}");
        assert!(msg.contains("not this user"), "{msg}");
        assert_eq!(err.kind(), std::io::ErrorKind::PermissionDenied);
        // And one of our own passes, and is tightened.
        let d = tempfile::tempdir().unwrap();
        private_dir(d.path()).unwrap();
    }

    /// A symbolic link is refused as a link, whatever it points at (Task 10
    /// review, Minor 3): a link somebody else owns to a `0700` directory of
    /// this user's would otherwise pass the owner and mode checks through
    /// `metadata`, and its owner could re-point it later. The link here is
    /// this user's own — a second account cannot be had in a test — so what
    /// is pinned is that a link is refused *as a link*, by name, before any
    /// ownership question is asked.
    #[cfg(unix)]
    #[test]
    fn a_symbolic_link_is_refused_as_a_link() {
        let d = tempfile::tempdir().unwrap();
        let real = d.path().join("real");
        std::fs::create_dir(&real).unwrap();
        let link = d.path().join("link");
        std::os::unix::fs::symlink(&real, &link).unwrap();
        let err = private_dir(&link).expect_err("a link is not a directory of ours");
        let msg = err.to_string();
        assert!(msg.contains("is a symbolic link"), "{msg}");
        assert!(msg.contains(&link.display().to_string()), "{msg}");
        assert_eq!(err.kind(), std::io::ErrorKind::PermissionDenied);
        // The directory it points at is fine, reached directly.
        private_dir(&real).unwrap();
    }

    /// A junction or symlink into a synced folder is caught through the link
    /// (review M9): the deepest existing ancestor is canonicalised before the
    /// markers are matched, with the part of the path that does not exist
    /// yet put back on top of it.
    #[test]
    fn a_link_into_a_synced_folder_is_refused_through_the_link() {
        let d = tempfile::tempdir().unwrap();
        let synced = d.path().join("OneDrive");
        std::fs::create_dir_all(synced.join("docs")).unwrap();
        let link = d.path().join("plain");
        #[cfg(unix)]
        std::os::unix::fs::symlink(&synced, &link).unwrap();
        #[cfg(windows)]
        {
            // A junction needs no privilege; a symlink would.
            let made = std::process::Command::new("cmd")
                .args(["/c", "mklink", "/J"])
                .arg(&link)
                .arg(&synced)
                .output()
                .unwrap();
            assert!(
                made.status.success(),
                "mklink /J: {}",
                String::from_utf8_lossy(&made.stderr)
            );
        }
        for p in [
            link.clone(),
            link.join("docs"),
            link.join("docs").join("not-yet").join("vk"),
        ] {
            let err = refuse_sync_folder(&p).expect_err("a path through the link is synced");
            let msg = err.to_string();
            assert!(msg.contains("onedrive"), "{}: {msg}", p.display());
            assert!(
                msg.contains("resolves to"),
                "the refusal names the resolved path: {msg}"
            );
        }
        // The same shape with no link in it is fine, whether or not it exists.
        let plain = d.path().join("elsewhere");
        refuse_sync_folder(&plain).unwrap();
        refuse_sync_folder(&plain.join("not-yet").join("vk")).unwrap();
    }

    #[test]
    fn empty_override_resolves_to_cwd() {
        let cwd = Path::new("C:/Users/x/project");
        let resolved = resolve_override(Path::new(""), cwd);
        assert_eq!(resolved, cwd);
    }
}
