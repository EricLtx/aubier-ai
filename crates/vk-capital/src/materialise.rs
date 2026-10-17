//! The installed tree, built from a package's layers, and its fingerprint.
//! Packing does it in a staging directory to learn `files_sha256`; installing
//! does it again under the install root and must find the same digest, so
//! every byte of the tree must depend on the layers alone, never on where the
//! tree sits or who builds it.
//!
//! The layers stay apart. The release is checked against its SHA-256 and
//! extracted first, and what it ships under `python/Scripts/` is read from it
//! alone. The wheels are installed next, by that interpreter, before the
//! driver's files exist. The driver's files land last: the whole layer is
//! refused before a byte of it lands if an entry names `python` or a
//! `pyvenv.cfg` at the root (in any case, with Windows' trailing dots and
//! spaces ignored); a `pyvenv.cfg` there would move `sys.prefix` to the root
//! and run its `.pth` files inside the pip of an elevated install. Then an
//! entry that would land on a path already in the tree is refused.
//!
//! The reproducibility rules (each one a difference observed or ruled out
//! between two trees built from the same layers in two directories):
//!
//! 1. **Wheels are installed by `name==version` from `--find-links`, never by
//!    file path.** pip writes `<dist>.dist-info/direct_url.json` (PEP 610),
//!    holding the absolute `file:///` URL of the wheel, for a requirement given
//!    as a path; for a name and version found through `--find-links` it writes
//!    none. The lock is checked against the wheels first (`check_lock`), so the
//!    name and version can only find the locked wheel.
//! 2. **pip runs isolated:** `python -I -m pip --isolated`, `PIP_CONFIG_FILE`
//!    set to the null device: no user site, no `PIP_*` variable, no pip
//!    configuration file of the node can change what is installed or where.
//!    `--no-compile`: no bytecode is written at install; `--no-cache-dir`:
//!    nothing is read from or written to the cache of the user who installs.
//! 3. **Console-script launchers are purged.** pip writes
//!    `python/Scripts/<script>.exe`, which embeds the absolute path of the
//!    interpreter: every file under `python/Scripts/` that the pristine release
//!    did not ship is deleted (plan ruling P-2).
//! 4. **The `RECORD` of each installed distribution lists only files the tree
//!    holds.** pip's `<dist>.dist-info/RECORD` carries the SHA-256 of each
//!    launcher it wrote, which differs with the interpreter's path; after the
//!    purges, every row naming a file that is no longer there is dropped
//!    (other rows, and their order, kept). Only the `*.dist-info` directories
//!    the install added are rewritten: the release's own (pip's, whose
//!    `RECORD` names launchers and bytecode the release does not ship) stay
//!    byte for byte as shipped.
//! 5. **Every `__pycache__` directory is deleted**, the release's included:
//!    the fingerprint hashes bytecode (ruling R-T8b) and the installed tree
//!    is run with `-B`, so an installed tree holds none and any `.pyc` that
//!    appears in one later is a change to the tree.
//!
//! OS analogy: materialising is flashing the image to a blank partition and
//! reading back its CRC; the rules strip what the flasher stamps with the
//! board's own address, so two boards flashed from one image read back one CRC.

use std::collections::BTreeSet;
use std::ffi::OsString;
use std::fs;
use std::path::{Component, Path, PathBuf};
use std::process::{Command, Stdio};

use crate::error::{refuse, Result};
use crate::wheels::Locked;
use crate::{fingerprint, python, wheels};

/// A materialised tree and its `hash_tree` fingerprint.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Materialised {
    pub root: PathBuf,
    pub fingerprint: String,
}

/// Builds the installed tree under `root` (absent or empty, else refused):
/// checks every entry of `files` (tar) without writing (none may name the
/// interpreter), extracts the python archive to `root/python` once its
/// SHA-256 is `python_sha256` (every real caller passes
/// [`python::PINNED`]`.sha256`), checks the wheels against the lock in a
/// scratch directory beside `root` and installs them with
/// `python/python.exe -I -m pip --isolated install --no-index --no-compile
/// --no-cache-dir --no-warn-script-location --find-links <wheels>
/// <name==version in lock order>` (offline), deletes every file under
/// `python/Scripts/` not in `pristine_scripts`, extracts `files` at the root
/// (an entry on an existing path refused), deletes every `__pycache__` and
/// every row of an installed distribution's `RECORD` naming a deleted file,
/// then returns `hash_tree(root)`. On a refusal after the release check the
/// partial tree stays for the caller to remove.
pub fn materialise(
    root: &Path,
    files_tar: &[u8],
    python_archive: &Path,
    python_sha256: &str,
    wheels_tar: &[u8],
    lock: &[Locked],
) -> Result<Materialised> {
    if root.exists() && (!root.is_dir() || fs::read_dir(root)?.next().is_some()) {
        return refuse(
            format!("{} exists and is not an empty directory", root.display()),
            "materialise into a new directory",
        );
    }
    wheels::scan(files_tar, |path| {
        if on_interpreter(path) {
            return refuse(
                "files layer: an entry would land on the interpreter",
                "rename the driver's python/ directory or pyvenv.cfg",
            );
        }
        Ok(())
    })?;
    fs::create_dir_all(root)?;
    let python = python::extract(python_archive, root, python_sha256)?;
    let pristine = python::pristine_scripts(&python)?;
    let site_packages = python.join("Lib").join("site-packages");
    let shipped = dist_infos(&site_packages)?;
    install_wheels(root, &python, wheels_tar, lock)?;
    purge_scripts(&python, &pristine)?;
    wheels::unpack(files_tar, root, |path, _| {
        if fs::symlink_metadata(root.join(path)).is_ok() {
            return refuse(
                "files layer: an entry would land on an existing path",
                "repack the driver: its files layer writes one path twice",
            );
        }
        Ok(())
    })?;
    purge_pycache(root)?;
    prune_records(&site_packages, &shipped)?;
    Ok(Materialised {
        root: root.to_path_buf(),
        fingerprint: fingerprint::hash_tree(root)?,
    })
}

/// Whether a files-layer entry's first component is, as Windows resolves it
/// (case folded, trailing dots and spaces dropped), `python` or `pyvenv.cfg`.
fn on_interpreter(path: &Path) -> bool {
    path.components()
        .find_map(|c| match c {
            Component::Normal(n) => Some(n.to_string_lossy()),
            _ => None,
        })
        .is_some_and(|first| {
            let first = first.trim_end_matches(['.', ' ']);
            first.eq_ignore_ascii_case("python") || first.eq_ignore_ascii_case("pyvenv.cfg")
        })
}

/// Unpacks `wheels_tar` into a scratch directory beside `root` (same parent,
/// outside the tree), checks it against `lock`, and installs the lock with
/// pip, offline and isolated.
fn install_wheels(root: &Path, python: &Path, wheels_tar: &[u8], lock: &[Locked]) -> Result<()> {
    let scratch = Scratch::beside(root)?;
    wheels::untar_into(wheels_tar, &scratch.0)?;
    wheels::check_lock(&scratch.0, lock)?;
    if lock.is_empty() {
        return Ok(());
    }
    let null_device = if cfg!(windows) { "nul" } else { "/dev/null" };
    let out = Command::new(python.join("python.exe"))
        .args([
            "-I",
            "-m",
            "pip",
            "--isolated",
            "--disable-pip-version-check",
        ])
        .args([
            "--no-input",
            "install",
            "--no-index",
            "--no-compile",
            "--no-cache-dir",
        ])
        .args(["--no-warn-script-location", "--find-links"])
        .arg(&scratch.0)
        .args(lock.iter().map(|l| format!("{}=={}", l.name, l.version)))
        .env("PIP_CONFIG_FILE", null_device)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .output()?;
    if !out.status.success() {
        wheels::log_tail("pip install", &out.stderr);
        return refuse(
            "pip install of the locked wheels failed",
            "repack the driver: its wheels do not install offline into its interpreter",
        );
    }
    Ok(())
}

/// Deletes every file at any depth under `python/Scripts/` whose relative
/// path is not in `pristine`.
fn purge_scripts(python: &Path, pristine: &BTreeSet<String>) -> Result<()> {
    let scripts = python.join("Scripts");
    if !scripts.is_dir() {
        return Ok(());
    }
    for entry in walkdir::WalkDir::new(&scripts).min_depth(1) {
        let entry = entry.map_err(std::io::Error::from)?;
        if !entry.file_type().is_dir()
            && !pristine.contains(&python::scripts_rel(&scripts, entry.path()))
        {
            fs::remove_file(entry.path())?;
        }
    }
    Ok(())
}

/// Deletes every `__pycache__` directory under `root`.
fn purge_pycache(root: &Path) -> Result<()> {
    let mut found = Vec::new();
    let mut walk = walkdir::WalkDir::new(root).into_iter();
    while let Some(entry) = walk.next() {
        let entry = entry.map_err(std::io::Error::from)?;
        if entry.file_type().is_dir() && entry.file_name() == "__pycache__" {
            found.push(entry.into_path());
            walk.skip_current_dir();
        }
    }
    for dir in found {
        fs::remove_dir_all(dir)?;
    }
    Ok(())
}

/// The names of the `*.dist-info` directories directly under `site_packages`
/// (empty when it does not exist).
fn dist_infos(site_packages: &Path) -> Result<BTreeSet<OsString>> {
    let mut out = BTreeSet::new();
    if !site_packages.is_dir() {
        return Ok(out);
    }
    for entry in fs::read_dir(site_packages)? {
        let entry = entry?;
        let name = entry.file_name();
        if entry.file_type()?.is_dir() && name.to_string_lossy().ends_with(".dist-info") {
            out.insert(name);
        }
    }
    Ok(out)
}

/// Rewrites the `RECORD` of each `*.dist-info` under `site_packages` that is
/// not in `shipped`, without the rows whose file (relative to
/// `site_packages`) is no longer there. A `RECORD` that is absent or not
/// UTF-8 is left as it is.
fn prune_records(site_packages: &Path, shipped: &BTreeSet<OsString>) -> Result<()> {
    for name in dist_infos(site_packages)?.difference(shipped) {
        let record = site_packages.join(name).join("RECORD");
        let bytes = match fs::read(&record) {
            Ok(bytes) => bytes,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => continue,
            Err(e) => return Err(e.into()),
        };
        let Ok(text) = String::from_utf8(bytes) else {
            continue;
        };
        let kept: String = text
            .split_inclusive('\n')
            .filter(|row| record_path(row).is_none_or(|p| site_packages.join(p).exists()))
            .collect();
        if kept.len() != text.len() {
            fs::write(&record, kept)?;
        }
    }
    Ok(())
}

/// The first CSV field of a `RECORD` row (the file's path), unquoted; `None`
/// for a blank row.
fn record_path(row: &str) -> Option<String> {
    let row = row.trim_end_matches(['\r', '\n']);
    if row.is_empty() {
        return None;
    }
    let Some(quoted) = row.strip_prefix('"') else {
        return Some(row.split(',').next().unwrap_or_default().to_string());
    };
    let mut out = String::new();
    let mut chars = quoted.chars().peekable();
    while let Some(c) = chars.next() {
        match c {
            '"' if chars.peek() == Some(&'"') => {
                out.push('"');
                chars.next();
            }
            '"' => break,
            c => out.push(c),
        }
    }
    Some(out)
}

/// A fresh directory `<root>.wheels-<unique>` beside `root` (same parent:
/// under the install root at install, where the user's `%TEMP%` cannot slip a
/// wheel in between `check_lock` and pip), removed on drop: where the wheels
/// wait for pip, outside the tree being fingerprinted.
struct Scratch(PathBuf);

impl Scratch {
    fn beside(root: &Path) -> std::io::Result<Self> {
        let name = root
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_default();
        let dir = root.with_file_name(format!("{name}.wheels-{}", crate::unique_suffix()));
        fs::create_dir(&dir)?;
        Ok(Self(dir))
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_installed_record_loses_its_rows_naming_purged_files_and_a_shipped_one_is_kept() {
        let t = tempfile::tempdir().unwrap();
        let sp = t.path().join("python/Lib/site-packages");
        let pip = sp.join("pip-1.dist-info");
        fs::create_dir_all(&pip).unwrap();
        let shipped_record = "../../Scripts/pip.exe,sha256=ee,5\n";
        fs::write(pip.join("RECORD"), shipped_record).unwrap();
        let shipped = dist_infos(&sp).unwrap();
        let info = sp.join("x-1.0.dist-info");
        fs::create_dir_all(&info).unwrap();
        fs::create_dir_all(t.path().join("python/Scripts")).unwrap();
        fs::write(sp.join("x.py"), b"").unwrap();
        fs::write(sp.join("a,b.py"), b"").unwrap();
        fs::write(info.join("METADATA"), b"").unwrap();
        let record = "x.py,sha256=aa,1\r\n\
                      \"a,b.py\",sha256=bb,2\r\n\
                      ../../Scripts/x.exe,sha256=cc,3\r\n\
                      x/__pycache__/x.cpython-313.pyc,,\r\n\
                      x-1.0.dist-info/METADATA,sha256=dd,4\r\n\
                      x-1.0.dist-info/RECORD,,\r\n";
        fs::write(info.join("RECORD"), record).unwrap();
        prune_records(&sp, &shipped).unwrap();
        assert_eq!(
            fs::read_to_string(info.join("RECORD")).unwrap(),
            "x.py,sha256=aa,1\r\n\
             \"a,b.py\",sha256=bb,2\r\n\
             x-1.0.dist-info/METADATA,sha256=dd,4\r\n\
             x-1.0.dist-info/RECORD,,\r\n"
        );
        assert_eq!(
            fs::read_to_string(pip.join("RECORD")).unwrap(),
            shipped_record
        );
    }

    #[test]
    fn a_quoted_record_path_unescapes_doubled_quotes() {
        assert_eq!(
            record_path("\"a\"\"b,c\",sha256=x,1\n").as_deref(),
            Some("a\"b,c")
        );
        assert_eq!(record_path("plain,,\n").as_deref(), Some("plain"));
        assert_eq!(record_path("\r\n"), None);
    }

    #[test]
    fn scripts_not_shipped_by_the_release_and_every_pycache_are_purged() {
        let t = tempfile::tempdir().unwrap();
        let py = t.path().join("python");
        fs::create_dir_all(py.join("Scripts/sub")).unwrap();
        fs::create_dir_all(py.join("Lib/x/__pycache__")).unwrap();
        fs::create_dir_all(t.path().join("__pycache__")).unwrap();
        fs::write(py.join("Scripts/pip.exe"), b"MZ").unwrap();
        fs::write(py.join("Scripts/sub/keep.cmd"), b"").unwrap();
        let pristine = python::pristine_scripts(&py).unwrap();
        fs::write(py.join("Scripts/tool.exe"), b"MZ C:\\abs\\python.exe").unwrap();
        fs::write(py.join("Scripts/sub/new.py"), b"").unwrap();
        fs::write(py.join("Lib/x/__pycache__/m.pyc"), b"").unwrap();
        fs::write(t.path().join("__pycache__/d.pyc"), b"").unwrap();
        purge_scripts(&py, &pristine).unwrap();
        purge_pycache(t.path()).unwrap();
        assert!(py.join("Scripts/pip.exe").exists());
        assert!(py.join("Scripts/sub/keep.cmd").exists());
        assert!(!py.join("Scripts/tool.exe").exists());
        assert!(!py.join("Scripts/sub/new.py").exists());
        assert!(!py.join("Lib/x/__pycache__").exists());
        assert!(!t.path().join("__pycache__").exists());
    }

    #[test]
    fn a_root_that_is_not_empty_is_refused() {
        let t = tempfile::tempdir().unwrap();
        fs::write(t.path().join("stale.py"), b"").unwrap();
        let e = materialise(
            t.path(),
            &[],
            Path::new("absent.tar.gz"),
            python::PINNED.sha256,
            &[],
            &[],
        )
        .unwrap_err();
        assert!(e.to_string().contains("not an empty directory"), "{e}");
    }

    /// A tar of `entries` in the order given (duplicates kept).
    fn tar_of(entries: &[(&str, &[u8])]) -> Vec<u8> {
        let mut b = tar::Builder::new(Vec::new());
        for (path, data) in entries {
            let mut h = tar::Header::new_gnu();
            h.set_size(data.len() as u64);
            h.set_mode(0o644);
            b.append_data(&mut h, path, *data).unwrap();
        }
        b.into_inner().unwrap()
    }

    /// A stand-in release (no working interpreter: tests that reach pip use
    /// the real one) and its SHA-256.
    fn fake_release(dir: &Path) -> (PathBuf, String) {
        let tar = tar_of(&[
            ("python/python.exe", b"MZ"),
            ("python/Scripts/.empty", b""),
            ("python/Lib/site-packages/README.txt", b"r"),
        ]);
        let mut gz = flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::fast());
        std::io::Write::write_all(&mut gz, &tar).unwrap();
        let archive = dir.join("release.tar.gz");
        fs::write(&archive, gz.finish().unwrap()).unwrap();
        let sha = crate::python::sha256_file(&archive).unwrap();
        (archive, sha)
    }

    fn materialise_files(dir: &Path, files: &[(&str, &[u8])]) -> (PathBuf, Result<Materialised>) {
        let (archive, sha) = fake_release(dir);
        let root = dir.join("tree");
        let empty = tar_of(&[]);
        let r = materialise(&root, &tar_of(files), &archive, &sha, &empty, &[]);
        (root, r)
    }

    fn assert_on_interpreter_and_nothing_written(name: &str) {
        let t = tempfile::tempdir().unwrap();
        let (root, r) = materialise_files(t.path(), &[("driver.py", b"print(1)"), (name, b"x")]);
        let e = r.unwrap_err().to_string();
        assert_eq!(
            e,
            "files layer: an entry would land on the interpreter; \
             remedy: rename the driver's python/ directory or pyvenv.cfg",
            "{name}"
        );
        assert!(!root.exists(), "{name}: something was written under root");
    }

    #[test]
    fn a_pyvenv_cfg_beside_the_interpreter_is_refused_before_anything_is_written() {
        for name in ["pyvenv.cfg", "PyVenv.CFG", "pyvenv.cfg. ", "./pyvenv.cfg"] {
            assert_on_interpreter_and_nothing_written(name);
        }
    }

    #[test]
    fn a_python_directory_in_any_spelling_is_refused_before_anything_is_written() {
        for name in [
            "Python/x.py",
            "PYTHON/Lib/site.py",
            "python./x.py",
            "python /x.py",
        ] {
            assert_on_interpreter_and_nothing_written(name);
        }
    }

    #[test]
    fn a_files_entry_on_a_release_path_is_refused_before_anything_is_written() {
        for name in ["python/python.exe", "python/Scripts/.empty"] {
            assert_on_interpreter_and_nothing_written(name);
        }
    }

    #[test]
    fn a_files_layer_that_writes_one_path_twice_is_refused() {
        let t = tempfile::tempdir().unwrap();
        let (root, r) = materialise_files(t.path(), &[("driver.py", b"1"), ("driver.py", b"2")]);
        let e = r.unwrap_err().to_string();
        assert!(
            e.contains("files layer: an entry would land on an existing path"),
            "{e}"
        );
        assert_eq!(fs::read(root.join("driver.py")).unwrap(), b"1");
    }

    #[test]
    fn a_release_that_is_not_the_expected_one_is_refused_before_it_is_extracted() {
        let t = tempfile::tempdir().unwrap();
        let (archive, _) = fake_release(t.path());
        let root = t.path().join("tree");
        let empty = tar_of(&[]);
        let e = materialise(&root, &empty, &archive, python::PINNED.sha256, &empty, &[])
            .unwrap_err()
            .to_string();
        assert!(e.contains("python release: sha256 mismatch"), "{e}");
        assert!(!root.join("python").exists());
    }

    #[test]
    fn the_release_lands_first_and_the_driver_files_beside_it() {
        let t = tempfile::tempdir().unwrap();
        let (root, r) =
            materialise_files(t.path(), &[("driver.py", b"print(1)"), ("lib/m.py", b"M")]);
        let m = r.unwrap();
        assert_eq!(fs::read(root.join("driver.py")).unwrap(), b"print(1)");
        assert_eq!(fs::read(root.join("lib/m.py")).unwrap(), b"M");
        assert!(root.join("python/python.exe").is_file());
        assert_eq!(m.fingerprint, fingerprint::hash_tree(&root).unwrap());
        // the wheels scratch was beside the root and is gone
        let left: Vec<_> = fs::read_dir(t.path())
            .unwrap()
            .map(|e| e.unwrap().file_name().to_string_lossy().into_owned())
            .collect();
        assert!(
            left.iter().all(|n| !n.starts_with("tree.wheels")),
            "{left:?}"
        );
    }

    #[test]
    fn the_wheels_scratch_is_created_beside_the_root_and_removed_on_drop() {
        let t = tempfile::tempdir().unwrap();
        let root = t.path().join("tree");
        let s = Scratch::beside(&root).unwrap();
        let dir = s.0.clone();
        assert_eq!(dir.parent(), root.parent());
        assert!(dir.is_dir());
        assert_ne!(dir, Scratch::beside(&root).unwrap().0);
        drop(s);
        assert!(!dir.exists());
    }
}
