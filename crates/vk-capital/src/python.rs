//! The embedded interpreter: one pinned python-build-standalone release
//! (`install_only`, Windows x86_64, never the Microsoft Store Python, R27),
//! downloaded once, checked against its upstream SHA-256 before anything reads
//! it, and extracted to a top-level `python/` directory.
//!
//! OS analogy: the release table is the bootloader's pinned image hash; `fetch`
//! is the OTA download that is not flashed until its hash matches; `extract`
//! writes the image to its one partition and nowhere else.

use std::collections::BTreeSet;
use std::fs;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::time::Duration;

use sha2::{Digest, Sha256};

use crate::error::{refuse, Result};

/// One python-build-standalone release asset and its upstream SHA-256
/// (64 lowercase hex digits, from the release's `SHA256SUMS`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PythonRelease {
    pub tag: &'static str,
    pub file: &'static str,
    pub sha256: &'static str,
    pub url: &'static str,
}

/// The pinned release (plan open item §10, settled in Task 6): CPython 3.13.16,
/// python-build-standalone tag `20261013`, `install_only`, Windows x86_64 MSVC.
/// `sha256` is the line of the release's `SHA256SUMS` asset, fetched
/// 2026-10-17. Changing it changes every fingerprint packed afterwards.
pub const PINNED: PythonRelease = PythonRelease {
    tag: "20261013",
    file: "cpython-3.13.16+20261013-x86_64-pc-windows-msvc-install_only.tar.gz",
    sha256: "e1f9233e780fbe951e531b0eb207d794e1c3fb39d523a6f481f4fc01ef2b6ca5",
    url: "https://github.com/astral-sh/python-build-standalone/releases/download/20261013/cpython-3.13.16+20261013-x86_64-pc-windows-msvc-install_only.tar.gz",
};

/// The most a download may write: the archive is ~48 MB; a server that
/// streams more is cut off here and the hash check refuses what it sent.
const MAX_ARCHIVE_BYTES: u64 = 512 * 1024 * 1024;
const ALTERED: &str = "the download was altered or the table is stale; do not use it";

/// `<cache>/<file>` of [`PINNED`], downloaded if absent (reqwest blocking,
/// 10 min timeout) to `<file>.part`, hashed while written, renamed only when
/// the SHA-256 matches. A cached copy is hashed again on every call: one whose
/// SHA-256 differs is deleted and refused, never used.
pub fn fetch(cache: &Path) -> Result<PathBuf> {
    fs::create_dir_all(cache)?;
    let dest = cache.join(PINNED.file);
    if dest.is_file() {
        if sha256_file(&dest)? == PINNED.sha256 {
            return Ok(dest);
        }
        fs::remove_file(&dest)?;
        return refuse(
            format!("cached python release {} sha256 mismatch", PINNED.tag),
            "it was deleted; pack again to download it afresh",
        );
    }
    let part = cache.join(format!("{}.part", PINNED.file));
    let digest = download(&part).inspect_err(|_| {
        let _ = fs::remove_file(&part);
    })?;
    if digest != PINNED.sha256 {
        fs::remove_file(&part)?;
        return refuse(
            format!("python release {} sha256 mismatch", PINNED.tag),
            ALTERED,
        );
    }
    fs::rename(&part, &dest)?;
    Ok(dest)
}

/// GET [`PINNED`]`.url` into `part`; the hex SHA-256 of the bytes written.
fn download(part: &Path) -> Result<String> {
    const OFFLINE: &str = "check the network, then pack again";
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(600))
        .build()
        .or_else(|_| refuse("http client could not start", OFFLINE))?;
    let response = match client.get(PINNED.url).send() {
        Ok(r) if r.status().is_success() => r,
        Ok(r) => {
            return refuse(
                format!(
                    "python release {} download answered HTTP {}",
                    PINNED.tag,
                    r.status().as_u16()
                ),
                OFFLINE,
            )
        }
        Err(_) => {
            return refuse(
                format!("python release {} download failed", PINNED.tag),
                OFFLINE,
            )
        }
    };
    let mut body = response.take(MAX_ARCHIVE_BYTES);
    let mut file = fs::File::create(part)?;
    let mut h = Sha256::new();
    let mut buf = vec![0u8; 64 * 1024];
    loop {
        let n = match body.read(&mut buf) {
            Ok(0) => break,
            Ok(n) => n,
            Err(e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
            Err(_) => {
                return refuse(
                    format!("python release {} download broke off", PINNED.tag),
                    OFFLINE,
                )
            }
        };
        h.update(&buf[..n]);
        file.write_all(&buf[..n])?;
    }
    file.sync_all()?;
    Ok(format!("{:x}", h.finalize()))
}

/// The hex SHA-256 of a file, read in 64 KiB chunks.
pub(crate) fn sha256_file(path: &Path) -> std::io::Result<String> {
    let mut file = fs::File::open(path)?;
    let mut h = Sha256::new();
    let mut buf = vec![0u8; 64 * 1024];
    loop {
        match file.read(&mut buf)? {
            0 => break,
            n => h.update(&buf[..n]),
        }
    }
    Ok(format!("{:x}", h.finalize()))
}

/// Extracts the `.tar.gz` whose top-level directory is `python/` into
/// `dest/python/` and returns that path. Every entry must lie under `python/`
/// and be a regular file or a directory (`wheels::unpack`'s rules); the first
/// that is not is refused, and what was extracted before it stays.
pub fn extract(archive: &Path, dest: &Path) -> Result<PathBuf> {
    let gz = flate2::read::GzDecoder::new(std::io::BufReader::new(fs::File::open(archive)?));
    fs::create_dir_all(dest)?;
    crate::wheels::unpack(gz, dest, Some("python"))?;
    let python = dest.join("python");
    if !python.join("python.exe").is_file() {
        return refuse(
            "python release archive has no python/python.exe",
            "use a python-build-standalone install_only archive for Windows x86_64",
        );
    }
    Ok(python)
}

/// The set of `/`-joined paths, relative to `python_dir/Scripts/`, of every
/// regular file there at any depth: what the pristine release ships, read after
/// extraction and before any pip install. Empty when the release has no
/// `Scripts/`.
pub fn pristine_scripts(python_dir: &Path) -> Result<BTreeSet<String>> {
    let scripts = python_dir.join("Scripts");
    let mut out = BTreeSet::new();
    if !scripts.is_dir() {
        return Ok(out);
    }
    for entry in walkdir::WalkDir::new(&scripts).min_depth(1) {
        let entry = entry.map_err(std::io::Error::from)?;
        if entry.file_type().is_file() {
            out.insert(scripts_rel(&scripts, entry.path()));
        }
    }
    Ok(out)
}

/// `path` relative to `scripts`, `/`-joined (lossy UTF-8, like `hash_tree`).
pub(crate) fn scripts_rel(scripts: &Path, path: &Path) -> String {
    path.strip_prefix(scripts)
        .expect("walked under Scripts")
        .components()
        .map(|c| c.as_os_str().to_string_lossy())
        .collect::<Vec<_>>()
        .join("/")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tar_gz(entries: &[(&str, &[u8])]) -> Vec<u8> {
        let mut b = tar::Builder::new(Vec::new());
        for (path, data) in entries {
            let mut h = tar::Header::new_gnu();
            h.set_size(data.len() as u64);
            h.set_mode(0o644);
            h.set_cksum();
            b.append_data(&mut h, path, *data).unwrap();
        }
        let tar = b.into_inner().unwrap();
        let mut gz = flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::fast());
        gz.write_all(&tar).unwrap();
        gz.finish().unwrap()
    }

    #[test]
    fn extract_puts_the_release_under_python_and_lists_its_scripts() {
        let t = tempfile::tempdir().unwrap();
        let archive = t.path().join("r.tar.gz");
        fs::write(
            &archive,
            tar_gz(&[
                ("python/python.exe", b"MZ"),
                ("python/Scripts/pip.exe", b"MZ"),
                ("python/Scripts/sub/x.cmd", b"@echo"),
                ("python/Lib/os.py", b"x"),
            ]),
        )
        .unwrap();
        let py = extract(&archive, &t.path().join("stage")).unwrap();
        assert_eq!(py, t.path().join("stage").join("python"));
        assert_eq!(fs::read(py.join("Lib/os.py")).unwrap(), b"x");
        let scripts: Vec<_> = pristine_scripts(&py).unwrap().into_iter().collect();
        assert_eq!(scripts, ["pip.exe", "sub/x.cmd"]);
    }

    #[test]
    fn extract_refuses_an_entry_outside_python() {
        let t = tempfile::tempdir().unwrap();
        let archive = t.path().join("r.tar.gz");
        fs::write(
            &archive,
            tar_gz(&[("python/python.exe", b"MZ"), ("other/x.py", b"x")]),
        )
        .unwrap();
        let e = extract(&archive, &t.path().join("stage")).unwrap_err();
        assert!(e.to_string().contains("outside python/"), "{e}");
        assert!(!t.path().join("stage/other").exists());
    }

    #[test]
    fn extract_refuses_an_archive_without_an_interpreter() {
        let t = tempfile::tempdir().unwrap();
        let archive = t.path().join("r.tar.gz");
        fs::write(&archive, tar_gz(&[("python/Lib/os.py", b"x")])).unwrap();
        let e = extract(&archive, &t.path().join("stage")).unwrap_err();
        assert!(e.to_string().contains("no python/python.exe"), "{e}");
    }

    #[test]
    fn a_cached_copy_with_another_sha256_is_deleted_and_refused() {
        let t = tempfile::tempdir().unwrap();
        let cached = t.path().join(PINNED.file);
        fs::write(&cached, b"not the release").unwrap();
        let e = fetch(t.path()).unwrap_err();
        assert!(e.to_string().contains("sha256 mismatch"), "{e}");
        assert!(!cached.exists());
    }
}
