//! The installed fingerprint: a digest over the whole installed tree, and the
//! (size, mtime) cache that spares re-reading it on every mount.
//!
//! OS analogy: the digest is the image CRC the boot ROM checks; the cache is
//! the "flash unchanged since last verify" flag. Like a real flag it trusts
//! the metadata: a byte flipped with size and mtime preserved is not seen
//! until the next full rehash (the documented limit).

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::btree_map::Entry;
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

/// Whether `rel` (relative to the tree root) is left out of the fingerprint:
/// any `__pycache__` at any depth, a top-level `manifest*.json`, and the
/// top-level `.package/` directory.
fn excluded(rel: &Path) -> bool {
    let names: Vec<String> = rel
        .components()
        .map(|c| c.as_os_str().to_string_lossy().into_owned())
        .collect();
    if names.iter().any(|n| n == "__pycache__") {
        return true;
    }
    if names.first().is_some_and(|n| n == ".package") {
        return true;
    }
    names.len() == 1 && names[0].starts_with("manifest") && names[0].ends_with(".json")
}

/// Every regular file under `dir`, keyed by its `/`-joined relative path
/// (bytes, so the map iterates in the order `hash_dir` sorts). Links are
/// neither followed nor listed. Two names with one lossy UTF-8 spelling are
/// an `InvalidData` error: dropping one would leave a file out of the digest.
fn walk(root: &Path, dir: &Path, out: &mut BTreeMap<Vec<u8>, PathBuf>) -> std::io::Result<()> {
    for entry in std::fs::read_dir(dir)? {
        let entry = entry?;
        let kind = entry.file_type()?; // does not follow links
        let path = entry.path();
        let rel = path.strip_prefix(root).expect("entry is under root");
        if excluded(rel) {
            continue;
        }
        if kind.is_dir() {
            walk(root, &path, out)?;
        } else if kind.is_file() {
            let key = rel
                .components()
                .map(|c| c.as_os_str().to_string_lossy().into_owned())
                .collect::<Vec<_>>()
                .join("/")
                .into_bytes();
            match out.entry(key) {
                Entry::Vacant(v) => {
                    v.insert(path);
                }
                Entry::Occupied(o) => {
                    return Err(std::io::Error::new(
                        std::io::ErrorKind::InvalidData,
                        format!(
                            "{} and {} have one spelling once made UTF-8; rename one",
                            o.get().display(),
                            path.display()
                        ),
                    ));
                }
            }
        }
    }
    Ok(())
}

/// `hash_tree`: every regular file under `root` at any depth, path-sorted
/// (`/`-joined relative paths, bytes compared), framed `path\0bytes\0` into
/// SHA-256 — the framing of `vk_contracts::driver::hash_dir`. Excluded: any
/// `__pycache__` directory, a top-level `manifest*.json`, the top-level
/// `.package/` directory. Links are skipped, never followed.
pub fn hash_tree(root: &Path) -> std::io::Result<String> {
    let mut files = BTreeMap::new();
    walk(root, root, &mut files)?;
    let mut h = Sha256::new();
    for (rel, path) in &files {
        h.update(rel);
        h.update([0]);
        h.update(std::fs::read(path)?);
        h.update([0]);
    }
    Ok(format!("sha256:{:x}", h.finalize()))
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CacheEntry {
    pub path: String,
    pub size: u64,
    pub mtime_ns: i128,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FingerprintCache {
    pub fingerprint: String,
    pub entries: Vec<CacheEntry>,
}

/// The stat set of the tree, same exclusions and order as `hash_tree`.
pub fn stat_tree(root: &Path) -> std::io::Result<Vec<CacheEntry>> {
    let mut files = BTreeMap::new();
    walk(root, root, &mut files)?;
    files
        .iter()
        .map(|(rel, path)| {
            let md = std::fs::metadata(path)?;
            let mtime_ns = md
                .modified()?
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_nanos() as i128)
                .unwrap_or(0);
            Ok(CacheEntry {
                path: String::from_utf8_lossy(rel).into_owned(),
                size: md.len(),
                mtime_ns,
            })
        })
        .collect()
}

/// `Hit(fingerprint)` when the stat set equals the cache's, else
/// `Rehashed(fingerprint, new_cache)`.
pub enum Check {
    Hit(String),
    Rehashed(String, FingerprintCache),
}

pub fn check(root: &Path, cache: Option<&FingerprintCache>) -> std::io::Result<Check> {
    let entries = stat_tree(root)?;
    if let Some(c) = cache {
        if c.entries == entries {
            return Ok(Check::Hit(c.fingerprint.clone()));
        }
    }
    let fingerprint = hash_tree(root)?;
    Ok(Check::Rehashed(
        fingerprint.clone(),
        FingerprintCache {
            fingerprint,
            entries,
        },
    ))
}

/// `Ok(None)` when the file does not exist; a file that exists but does not
/// parse is an `InvalidData` error.
pub fn load_cache(file: &Path) -> std::io::Result<Option<FingerprintCache>> {
    match std::fs::read(file) {
        Ok(bytes) => serde_json::from_slice(&bytes)
            .map(Some)
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e)),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(e) => Err(e),
    }
}

/// Atomic: pretty JSON to a temp file beside `file`, then a rename over it.
pub fn store_cache(file: &Path, cache: &FingerprintCache) -> std::io::Result<()> {
    let json = serde_json::to_vec_pretty(cache)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
    let mut tmp = file.as_os_str().to_owned();
    tmp.push(".tmp");
    let tmp = PathBuf::from(tmp);
    std::fs::write(&tmp, json)?;
    std::fs::rename(&tmp, file).inspect_err(|_| {
        let _ = std::fs::remove_file(&tmp);
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::path::Path;
    use std::time::{Duration, SystemTime};

    fn set_mtime(p: &Path, t: SystemTime) {
        fs::File::options()
            .write(true)
            .open(p)
            .unwrap()
            .set_modified(t)
            .unwrap();
    }
    fn filetime_now_plus(secs: u64) -> SystemTime {
        SystemTime::now() + Duration::from_secs(secs)
    }
    fn tree(dir: &Path) {
        fs::create_dir_all(dir.join("python/Lib/__pycache__")).unwrap();
        fs::create_dir_all(dir.join(".package")).unwrap();
        fs::write(dir.join("driver.py"), b"print(1)\n").unwrap();
        fs::write(dir.join("manifest.json"), b"{}").unwrap();
        fs::write(dir.join("manifest-fixture.json"), b"{}").unwrap();
        fs::write(dir.join("python/python.exe"), b"MZ").unwrap();
        fs::write(dir.join("python/Lib/os.py"), b"x").unwrap();
        fs::write(dir.join("python/Lib/__pycache__/os.pyc"), b"y").unwrap();
        fs::write(dir.join(".package/signature.json"), b"{}").unwrap();
    }
    #[test]
    fn the_fingerprint_ignores_pycache_manifests_and_the_package_dir() {
        let a = tempfile::tempdir().unwrap();
        tree(a.path());
        let f1 = hash_tree(a.path()).unwrap();
        fs::write(a.path().join("python/Lib/__pycache__/os.pyc"), b"changed").unwrap();
        fs::write(a.path().join("manifest.json"), b"{\"changed\":1}").unwrap();
        fs::write(a.path().join(".package/signature.json"), b"changed").unwrap();
        assert_eq!(hash_tree(a.path()).unwrap(), f1);
        fs::write(a.path().join("python/Lib/os.py"), b"changed").unwrap();
        assert_ne!(hash_tree(a.path()).unwrap(), f1);
    }
    #[test]
    fn the_same_tree_in_two_directories_has_one_fingerprint() {
        let a = tempfile::tempdir().unwrap();
        let b = tempfile::tempdir().unwrap();
        tree(a.path());
        tree(b.path());
        assert_eq!(hash_tree(a.path()).unwrap(), hash_tree(b.path()).unwrap());
    }
    #[test]
    fn a_nested_manifest_json_is_hashed() {
        let a = tempfile::tempdir().unwrap();
        tree(a.path());
        let f1 = hash_tree(a.path()).unwrap();
        fs::write(a.path().join("python/manifest.json"), b"{}").unwrap();
        assert_ne!(hash_tree(a.path()).unwrap(), f1);
    }
    #[test]
    fn the_cache_hits_on_an_unchanged_stat_set_and_rehashes_on_any_change() {
        let a = tempfile::tempdir().unwrap();
        tree(a.path());
        let Check::Rehashed(f1, cache) = check(a.path(), None).unwrap() else {
            panic!()
        };
        assert!(matches!(check(a.path(), Some(&cache)).unwrap(), Check::Hit(f) if f == f1));
        // same size, new mtime: rehash, same fingerprint, new cache
        let p = a.path().join("driver.py");
        fs::write(&p, b"print(1)\n").unwrap();
        let t = filetime_now_plus(2);
        set_mtime(&p, t);
        let Check::Rehashed(f2, cache2) = check(a.path(), Some(&cache)).unwrap() else {
            panic!("stat changed")
        };
        assert_eq!(f2, f1);
        assert_ne!(cache2.entries, cache.entries);
        // a byte changed with the mtime preserved: the cache hits — the documented limit (§3.3)
        fs::write(&p, b"print(2)\n").unwrap();
        set_mtime(&p, t);
        assert!(matches!(
            check(a.path(), Some(&cache2)).unwrap(),
            Check::Hit(_)
        ));
    }
    #[test]
    fn the_cache_round_trips_through_its_file() {
        let a = tempfile::tempdir().unwrap();
        tree(a.path());
        let Check::Rehashed(_, cache) = check(a.path(), None).unwrap() else {
            panic!()
        };
        let f = a.path().join("fingerprint.json");
        store_cache(&f, &cache).unwrap();
        assert_eq!(load_cache(&f).unwrap().unwrap(), cache);
        // a second store replaces the first
        store_cache(&f, &cache).unwrap();
        assert_eq!(load_cache(&f).unwrap().unwrap(), cache);
        assert!(load_cache(&a.path().join("absent.json")).unwrap().is_none());
    }
    #[test]
    fn the_framing_matches_hash_dir_on_a_tree_without_exclusions() {
        let a = tempfile::tempdir().unwrap();
        fs::create_dir_all(a.path().join("lib")).unwrap();
        fs::write(a.path().join("lib/m.py"), b"M").unwrap();
        fs::write(a.path().join("a.py"), b"A").unwrap();
        assert_eq!(
            hash_tree(a.path()).unwrap(),
            vk_contracts::driver::hash_dir(a.path()).unwrap()
        );
    }
    #[test]
    fn per_machine_dirs_are_hashed_unlike_hash_dir() {
        // The documented divergence from `hash_dir`: an installed tree has no
        // per-machine directory to leave out, so a change under one counts.
        for dir in [".venv", "tests", "fixture"] {
            let a = tempfile::tempdir().unwrap();
            tree(a.path());
            fs::create_dir_all(a.path().join(dir)).unwrap();
            fs::write(a.path().join(dir).join("x"), b"1").unwrap();
            let f1 = hash_tree(a.path()).unwrap();
            fs::write(a.path().join(dir).join("x"), b"2").unwrap();
            assert_ne!(hash_tree(a.path()).unwrap(), f1, "{dir}");
        }
    }
    #[cfg(windows)]
    fn not_unicode() -> std::ffi::OsString {
        use std::os::windows::ffi::OsStringExt;
        std::ffi::OsString::from_wide(&[0xD800]) // a lone surrogate
    }
    #[cfg(unix)]
    fn not_unicode() -> std::ffi::OsString {
        use std::os::unix::ffi::OsStringExt;
        std::ffi::OsString::from_vec(vec![0xFF])
    }
    #[test]
    fn two_names_with_one_lossy_spelling_are_an_error_not_a_dropped_file() {
        let a = tempfile::tempdir().unwrap();
        fs::write(a.path().join("\u{FFFD}"), b"A").unwrap();
        if fs::write(a.path().join(not_unicode()), b"B").is_err() {
            return; // the file system refuses the name (APFS): nothing can collide
        }
        let e = hash_tree(a.path()).unwrap_err();
        assert_eq!(e.kind(), std::io::ErrorKind::InvalidData, "{e}");
    }
}
