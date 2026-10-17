//! The driver's Python libraries as wheels and their lock, and the two tar
//! layers (`files`, `wheels`) written deterministically and read back with
//! every entry checked.
//!
//! OS analogy: the lock is the bill of materials of a firmware build — part
//! number, revision and checksum of every component — and `untar_into` is the
//! loader that writes only inside its partition and refuses a section that
//! points elsewhere.

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::io::Read;
use std::path::{Component, Path, PathBuf};
use std::process::{Command, Stdio};

use serde::{Deserialize, Serialize};

use crate::error::{refuse, Result};
use crate::python::sha256_file;

/// One Python distribution of the lock: `name` and `version` as the wheel's
/// file name spells them, `sha256` 64 lowercase hex digits of the wheel.
#[derive(Serialize, Deserialize, PartialEq, Eq, Debug, Clone)]
pub struct Locked {
    pub name: String,
    pub version: String,
    pub filename: String,
    pub sha256: String,
}

const NO_WHEEL: &str = "every requirement needs a wheel for this interpreter";
const ALTERED: &str = "the archive was altered; fetch it again from its source";

/// Resolves `requirements` with dependencies against `python_exe`'s own tags:
/// `<python> -I -m pip download --only-binary=:all: --dest <wheels_dir> -r <requirements>`
/// (network), then builds the lock from the `.whl` files in `wheels_dir`,
/// sorted by file name (name and version parsed from the file name, SHA-256
/// computed). `wheels_dir` should be new: every wheel in it enters the lock.
/// Refuses if pip fails or leaves anything that is not a wheel (an sdist means
/// a wheel was missing). A `requirements` with no requirement line resolves to
/// an empty lock without running pip.
pub fn resolve(python_exe: &Path, requirements: &Path, wheels_dir: &Path) -> Result<Vec<Locked>> {
    fs::create_dir_all(wheels_dir)?;
    let text = fs::read(requirements)?;
    let has_requirement = String::from_utf8_lossy(&text).lines().any(|l| {
        let l = l.trim();
        !l.is_empty() && !l.starts_with('#')
    });
    if has_requirement {
        let out = Command::new(python_exe)
            .args(["-I", "-m", "pip", "download"])
            .args([
                "--disable-pip-version-check",
                "--no-input",
                "--only-binary=:all:",
            ])
            .arg("--dest")
            .arg(wheels_dir)
            .arg("-r")
            .arg(requirements)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::piped())
            .output()?;
        if !out.status.success() {
            log_tail("pip download", &out.stderr);
            return refuse("pip download failed", NO_WHEEL);
        }
    }
    let mut lock = Vec::new();
    for entry in fs::read_dir(wheels_dir)? {
        let entry = entry?;
        let filename = entry.file_name();
        let wheel = filename
            .to_str()
            .filter(|f| entry.file_type().is_ok_and(|t| t.is_file()) && plain_wheel_name(f));
        let Some(filename) = wheel else {
            return refuse("pip download left a file that is not a wheel", NO_WHEEL);
        };
        let (name, version) = parse_wheel_name(filename).expect("plain_wheel_name parsed it");
        lock.push(Locked {
            name: name.into(),
            version: version.into(),
            filename: filename.into(),
            sha256: sha256_file(&entry.path())?,
        });
    }
    lock.sort_by(|a, b| a.filename.cmp(&b.filename));
    Ok(lock)
}

/// The last 300 bytes of a child's stderr, escaped, on this process's stderr
/// (the daemon log): never in a refusal, which carries no third party's text.
pub(crate) fn log_tail(what: &str, stderr: &[u8]) {
    let tail = &stderr[stderr.len().saturating_sub(300)..];
    eprintln!(
        "vk-capital: {what} failed; stderr tail: {}",
        String::from_utf8_lossy(tail).escape_debug()
    );
}

/// `(name, version)` of a wheel file name
/// `{name}-{version}(-{build})?-{python}-{abi}-{platform}.whl`, or `None`.
pub(crate) fn parse_wheel_name(filename: &str) -> Option<(&str, &str)> {
    let stem = filename.strip_suffix(".whl")?;
    let parts: Vec<&str> = stem.split('-').collect();
    if !(5..=6).contains(&parts.len()) || parts.iter().any(|p| p.is_empty()) {
        return None;
    }
    Some((parts[0], parts[1]))
}

/// A wheel file name that is safe as one path component and one pip
/// argument: parses as a wheel name and holds only `[A-Za-z0-9._+!-]`.
pub(crate) fn plain_wheel_name(filename: &str) -> bool {
    filename
        .bytes()
        .all(|b| b.is_ascii_alphanumeric() || b"._+!-".contains(&b))
        && parse_wheel_name(filename).is_some()
}

/// A deterministic tar of every regular file under `dir`: entries sorted by
/// their `/`-joined relative path (bytes), GNU headers, mtime 0, uid/gid 0,
/// mode 0644 (0755 for a file with an execute bit, on Unix). Directories are
/// implied by the paths; links are skipped, never followed. A name that is
/// not valid Unicode is refused.
pub fn tar_dir(dir: &Path) -> Result<Vec<u8>> {
    let mut files = BTreeMap::<String, PathBuf>::new();
    for entry in walkdir::WalkDir::new(dir).min_depth(1) {
        let entry = entry.map_err(std::io::Error::from)?;
        if !entry.file_type().is_file() {
            continue;
        }
        let rel = entry.path().strip_prefix(dir).expect("walked under dir");
        let Some(parts) = rel
            .components()
            .map(|c| c.as_os_str().to_str())
            .collect::<Option<Vec<_>>>()
        else {
            return refuse(
                format!("a file name under {} is not valid Unicode", dir.display()),
                "rename it",
            );
        };
        files.insert(parts.join("/"), entry.into_path());
    }
    let mut b = tar::Builder::new(Vec::new());
    for (rel, path) in &files {
        let data = fs::read(path)?;
        let mut h = tar::Header::new_gnu();
        h.set_entry_type(tar::EntryType::Regular);
        h.set_size(data.len() as u64);
        h.set_mtime(0);
        h.set_uid(0);
        h.set_gid(0);
        h.set_mode(if executable(path)? { 0o755 } else { 0o644 });
        b.append_data(&mut h, rel, data.as_slice())?;
    }
    Ok(b.into_inner()?)
}

#[cfg(unix)]
fn executable(path: &Path) -> std::io::Result<bool> {
    use std::os::unix::fs::PermissionsExt;
    Ok(fs::metadata(path)?.permissions().mode() & 0o111 != 0)
}

#[cfg(not(unix))]
fn executable(_: &Path) -> std::io::Result<bool> {
    Ok(false)
}

/// Extracts a tar under `dest`, refusing the first entry that is absolute,
/// has a `..` or a drive or stream (`:`) in its path, or is anything but a
/// regular file or a directory (a link, a device, a FIFO). What was extracted
/// before it stays. No entry's path is ever echoed.
pub fn untar_into(bytes: &[u8], dest: &Path) -> Result<()> {
    unpack(bytes, dest, |_, _| Ok(()))
}

/// [`untar_into`] over any reader, with `rule` run on each entry's checked
/// path and header before the entry is written: its refusal stops the
/// extraction there.
pub(crate) fn unpack<R: Read>(
    reader: R,
    dest: &Path,
    mut rule: impl FnMut(&Path, &tar::Header) -> Result<()>,
) -> Result<()> {
    fs::create_dir_all(dest)?;
    let mut archive = tar::Archive::new(reader);
    for entry in archive.entries().or_else(unreadable)? {
        let mut entry = entry.or_else(unreadable)?;
        let path = checked_path(&entry)?;
        rule(&path, entry.header())?;
        match entry.unpack_in(dest) {
            Ok(true) => {}
            Ok(false) => {
                return refuse("tar entry would escape the destination", ALTERED);
            }
            Err(e) => {
                return refuse(
                    format!("tar entry could not be written ({:?})", e.kind()),
                    "check the destination is writable and has room",
                )
            }
        }
    }
    Ok(())
}

/// Runs [`untar_into`]'s checks and `rule` on every entry of `bytes`, writing
/// nothing: a layer is refused whole before its first byte lands.
pub(crate) fn scan(bytes: &[u8], mut rule: impl FnMut(&Path) -> Result<()>) -> Result<()> {
    let mut archive = tar::Archive::new(bytes);
    for entry in archive.entries().or_else(unreadable)? {
        rule(&checked_path(&entry.or_else(unreadable)?)?)?;
    }
    Ok(())
}

/// The entry's path, refused unless it is relative, has no `..`, no drive and
/// no stream (`:`), and the entry is a regular file or a directory.
fn checked_path<R: Read>(entry: &tar::Entry<'_, R>) -> Result<PathBuf> {
    let kind = entry.header().entry_type();
    let path = entry.path().or_else(unreadable)?.into_owned();
    let plain = path.components().all(|c| match c {
        Component::Normal(n) => !n.to_string_lossy().contains(':'),
        Component::CurDir => true,
        _ => false, // `..`, a root, a drive prefix
    });
    if !plain || !(kind.is_file() || kind.is_dir()) {
        return refuse(
            "tar entry would escape the destination, or is a link or a special file",
            ALTERED,
        );
    }
    Ok(path)
}

/// A tar (or gzip) stream that does not parse: the kind of error, never its
/// text, which can quote an entry's path.
fn unreadable<T>(e: std::io::Error) -> Result<T> {
    refuse(
        format!("archive does not read as a tar ({:?})", e.kind()),
        ALTERED,
    )
}

/// Refuses unless `dir` holds exactly the wheels `lock` names, each a plain
/// wheel file name whose name and version are the entry's, with the entry's
/// SHA-256. Names the entry by its index, never by its text.
pub(crate) fn check_lock(dir: &Path, lock: &[Locked]) -> Result<()> {
    const REPACK: &str = "repack the driver: its lock and wheels disagree";
    for (i, l) in lock.iter().enumerate() {
        if !plain_wheel_name(&l.filename)
            || parse_wheel_name(&l.filename) != Some((l.name.as_str(), l.version.as_str()))
        {
            return refuse(format!("lock entry {i} is malformed"), REPACK);
        }
        let wheel = dir.join(&l.filename);
        if !wheel.is_file() || sha256_file(&wheel)? != l.sha256 {
            return refuse(
                format!("lock entry {i}: its wheel is missing or its sha256 differs"),
                REPACK,
            );
        }
    }
    let present = fs::read_dir(dir)?
        .map(|e| e.map(|e| e.file_name()))
        .collect::<std::io::Result<BTreeSet<_>>>()?;
    let locked: BTreeSet<std::ffi::OsString> =
        lock.iter().map(|l| l.filename.as_str().into()).collect();
    if present != locked {
        return refuse(
            "the wheels layer holds a file the lock does not name",
            REPACK,
        );
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tar_of(path: &str, kind: tar::EntryType) -> Vec<u8> {
        let mut b = tar::Builder::new(Vec::new());
        let mut h = tar::Header::new_gnu();
        h.set_entry_type(kind);
        h.set_size(if kind.is_file() { 1 } else { 0 });
        h.set_mode(0o644);
        if kind.is_symlink() || kind.is_hard_link() {
            h.set_link_name("target").unwrap();
        }
        // `append_data` refuses `..` and absolute paths: write the name raw.
        let name = &mut h.as_gnu_mut().unwrap().name;
        name[..path.len()].copy_from_slice(path.as_bytes());
        h.set_cksum();
        let data: &[u8] = if kind.is_file() { b"x" } else { b"" };
        b.append(&h, data).unwrap();
        b.into_inner().unwrap()
    }

    #[test]
    fn untar_refuses_absolute_parent_drive_stream_and_link_entries() {
        for (path, kind) in [
            ("/abs.txt", tar::EntryType::Regular),
            ("a/../../up.txt", tar::EntryType::Regular),
            ("C:/drive.txt", tar::EntryType::Regular),
            ("C:drive-relative.txt", tar::EntryType::Regular),
            ("file.txt:stream", tar::EntryType::Regular),
            ("sym-x", tar::EntryType::Symlink),
            ("hard-x", tar::EntryType::Link),
            ("fifo-x", tar::EntryType::Fifo),
        ] {
            let t = tempfile::tempdir().unwrap();
            let e = untar_into(&tar_of(path, kind), t.path()).unwrap_err();
            let msg = e.to_string();
            assert!(msg.contains("escape"), "{path}: {msg}");
            assert!(!msg.contains(path), "{path} echoed: {msg}");
            assert_eq!(fs::read_dir(t.path()).unwrap().count(), 0, "{path}");
        }
    }

    #[test]
    fn tar_dir_then_untar_into_round_trips_nested_files() {
        let src = tempfile::tempdir().unwrap();
        fs::create_dir_all(src.path().join("pkg/sub")).unwrap();
        fs::write(src.path().join("pkg/sub/m.py"), b"M").unwrap();
        fs::write(src.path().join("pkg.txt"), b"T").unwrap();
        let long = "d".repeat(120); // past the 100-byte name field: a GNU long name
        fs::create_dir_all(src.path().join(&long)).unwrap();
        fs::write(src.path().join(&long).join("f"), b"L").unwrap();
        let bytes = tar_dir(src.path()).unwrap();
        let names: Vec<String> = tar::Archive::new(bytes.as_slice())
            .entries()
            .unwrap()
            .map(|e| {
                e.unwrap()
                    .path()
                    .unwrap()
                    .to_string_lossy()
                    .replace('\\', "/")
            })
            .collect();
        assert_eq!(
            names,
            [format!("{long}/f"), "pkg.txt".into(), "pkg/sub/m.py".into()]
        );
        let dst = tempfile::tempdir().unwrap();
        untar_into(&bytes, dst.path()).unwrap();
        assert_eq!(
            crate::fingerprint::hash_tree(dst.path()).unwrap(),
            crate::fingerprint::hash_tree(src.path()).unwrap()
        );
    }

    #[test]
    fn wheel_names_parse_to_name_and_version() {
        assert_eq!(
            parse_wheel_name("pip_install_test-0.5-py3-none-any.whl"),
            Some(("pip_install_test", "0.5"))
        );
        assert_eq!(
            parse_wheel_name("greenlet-3.1.1-1-cp313-cp313-win_amd64.whl"),
            Some(("greenlet", "3.1.1"))
        );
        for bad in [
            "x-1.0.tar.gz",
            "x-1.0-py3-any.whl",
            "-1.0-py3-none-any.whl",
            "x-1.0-a-b-c-d-e.whl",
        ] {
            assert_eq!(parse_wheel_name(bad), None, "{bad}");
        }
        assert!(!plain_wheel_name("..\\x-1.0-py3-none-any.whl"));
        assert!(!plain_wheel_name("x-1.0-py3-none-any.whl\n"));
        assert!(plain_wheel_name("x-1!1.0+local-py3-none-any.whl"));
    }

    #[test]
    fn a_requirements_without_a_requirement_resolves_to_an_empty_lock_without_pip() {
        let t = tempfile::tempdir().unwrap();
        let req = t.path().join("requirements.txt");
        fs::write(&req, b"# nothing\n\n").unwrap();
        let wheels = t.path().join("wheels");
        let lock = resolve(&t.path().join("no-python.exe"), &req, &wheels).unwrap();
        assert!(lock.is_empty());
        assert!(wheels.is_dir());
    }

    #[test]
    fn check_lock_refuses_a_changed_wheel_an_extra_file_and_a_malformed_entry() {
        let t = tempfile::tempdir().unwrap();
        let f = "x-1.0-py3-none-any.whl";
        fs::write(t.path().join(f), b"wheel").unwrap();
        let good = Locked {
            name: "x".into(),
            version: "1.0".into(),
            filename: f.into(),
            sha256: sha256_file(&t.path().join(f)).unwrap(),
        };
        check_lock(t.path(), std::slice::from_ref(&good)).unwrap();

        let changed = Locked {
            sha256: "0".repeat(64),
            ..good.clone()
        };
        let e = check_lock(t.path(), &[changed]).unwrap_err().to_string();
        assert!(e.contains("lock entry 0") && e.contains("sha256"), "{e}");

        let lying = Locked {
            version: "2.0".into(),
            ..good.clone()
        };
        let e = check_lock(t.path(), &[lying]).unwrap_err().to_string();
        assert!(e.contains("malformed"), "{e}");

        fs::write(t.path().join("extra.whl"), b"?").unwrap();
        let e = check_lock(t.path(), &[good]).unwrap_err().to_string();
        assert!(e.contains("does not name"), "{e}");
    }
}
