//! The daemon's half of driver packaging (packaging spec §4.3–4.5):
//! `driver.pack`, which builds a package out of a development driver, signs
//! it with this account's publisher key and records `driver.packed`; the
//! installed tree `driver.mount {installed: "name@version"}` names, verified
//! against its package before anything runs; the fingerprint that holds an
//! installed tree to its manifest at every spawn; and what `driver.ls` lists
//! of the install root.
//!
//! None of it runs under the kernel lock: a pack downloads an interpreter and
//! runs pip for minutes, and hashing an installed tree reads every file of
//! it. The kernel is asked, in short locks, for what only it knows — the
//! install root, the publisher key's source, the context rules — and records
//! the act last.
//!
//! OS analogy: a pack is the factory build — compile, flash a reference
//! board, read back its CRC, sign the image header; a mount by name is the
//! boot ROM checking the vendor signature against the fused key and the
//! flash CRC against the signed header before it jumps to the image.
use crate::server::{bad, internal, kerr, lock, ServerConfig, Shared};
use crate::{RpcError, E_INVARIANT, E_NOT_FOUND, E_STORE};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::io::Read;
use std::path::{Path, PathBuf};
use vk_capital::fingerprint::{self, Check, FingerprintCache};
use vk_capital::layout::{self, Annotations, Layers};
use vk_capital::python::{self, PINNED};
use vk_capital::signature::{self, Pins, PublisherKey};
use vk_capital::{materialise, wheels, CapitalError};
use vk_contracts::driver::{is_driver_name, DriverManifest, DriverSource};
use vk_contracts::package::{DriverPackage, Signature};
use vk_contracts::syscalls::Ctx;
use vk_kernel::capital::is_version;
use vk_kernel::drivers::DriverRow;

/// The most a manifest, an image manifest or a signature file may hold:
/// each is a few kilobytes, and nothing read from a tree is read unbounded.
const MAX_SMALL_FILE: u64 = 1024 * 1024;
/// The packed interpreter, relative to the installed tree, and its flags:
/// `-E` ignores every `PYTHON*` variable, `-s` the user's site directory,
/// `-B` writes no bytecode into a tree that is fingerprinted. Not `-I`: it
/// implies `-P`, which takes the script's directory off `sys.path`, and the
/// driver imports what sits beside it (`vkdriver`).
const PACKED_INTERPRETER: [&str; 4] = ["python/python.exe", "-E", "-s", "-B"];
const REINSTALL: &str = "reinstall the package, then remount";

/// What `driver.pack` takes, and nothing else. Every path is absolute: the
/// daemon's working directory is its own, never the shell's.
#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct PackParams {
    manifest: PathBuf,
    version: String,
    out: PathBuf,
    #[serde(default)]
    python_cache: Option<PathBuf>,
}

/// What a pack built in its stage, ready to be laid out.
struct Built {
    files_tar: Vec<u8>,
    wheels_tar: Vec<u8>,
    lock: Vec<wheels::Locked>,
    fingerprint: String,
    archive: PathBuf,
}

/// `driver.pack {manifest, version, out, python_cache}`, for a principal
/// `dispatch` has already proved human (I1).
///
/// Everything that can be refused is refused before anything is written or
/// downloaded: the version (it becomes a path component), the paths, an
/// `--out` that holds anything, the manifest (it parses, validates and its
/// driver's files hash to its `files_sha256`), the context rules on the
/// installed copy at `<install_root>/<name>/<version>`, and a missing
/// publisher key. Then, in a short stage directory ([`Stage`]): the files
/// layer (the driver's files as `hash_dir` sees them, plus the shared
/// `vkdriver` library beside them, [`stage_files`]), the pinned CPython
/// release (`config.python_archive`, else fetched into `python_cache`, else
/// into `<state_dir>/capital/python`), the wheels of `requirements.txt`
/// resolved with that interpreter and locked, and the installed tree
/// materialised once to learn its fingerprint. The manifest's copy says
/// `source: installed`, carries the fingerprint as `files_sha256` and runs
/// the script under the packed interpreter; the five layers are laid out in
/// `out` and signed; `driver.packed` is recorded last. A pack that fails
/// once its layout is written leaves `out` as it found it.
pub(crate) fn pack(
    kernel: &Shared,
    config: &ServerConfig,
    ctx: Ctx,
    p: Value,
) -> Result<Value, RpcError> {
    let PackParams {
        manifest: manifest_file,
        version,
        out,
        python_cache,
    } = serde_json::from_value(p).map_err(|_| {
        bad("driver.pack takes manifest, version, out and python_cache, nothing else")
    })?;
    if !is_version(&version) {
        return Err(bad(&format!(
            "version {version:?} is not MAJOR.MINOR.PATCH in digits; remedy: pass a version such \
             as 1.0.0"
        )));
    }
    let paths = [
        ("--manifest", Some(&manifest_file)),
        ("--out", Some(&out)),
        ("--python-cache", python_cache.as_ref()),
    ];
    for (flag, path) in paths {
        if let Some(path) = path.filter(|p| !p.is_absolute()) {
            return Err(bad(&format!(
                "{flag} {} is not absolute; remedy: name it by its absolute path",
                path.display()
            )));
        }
    }
    if !empty_or_absent(&out) {
        return Err(bad(&format!(
            "{} exists and is not an empty directory; remedy: choose an empty --out",
            out.display()
        )));
    }
    let mut manifest = read_manifest(&manifest_file)?;
    let driver_dir = manifest_file
        .parent()
        .ok_or_else(|| bad("--manifest names no directory"))?;
    let script = crate::drivers::development_hold(&manifest, driver_dir)
        .map_err(|e| bad(&format!("{e:#}")))?;
    manifest.source = DriverSource::Installed;
    let (key_source, state_dir) = {
        let k = lock(kernel)?;
        let installed_at = k.install_root().join(&manifest.name).join(&version);
        k.check_context(&manifest, &installed_at).map_err(kerr)?;
        (k.publisher_key_source(), k.store().state_dir.clone())
    };
    // Loaded with the kernel lock released: a keyring read can block.
    let key = PublisherKey::load(&key_source)
        .map_err(refused)?
        .ok_or_else(|| gate("no publisher key; remedy: vk capital key init".into()))?;

    let stage = Stage::new().map_err(|e| io_failed("create the stage directory", &e))?;
    let cache = python_cache.unwrap_or_else(|| state_dir.join("capital").join("python"));
    let built = build(
        &stage.0,
        driver_dir,
        config.python_archive.as_deref(),
        &cache,
    )?;
    let mut packed = manifest;
    packed.program.files_sha256 = built.fingerprint.clone();
    packed.program.command = packed_command(&packed.program.command, &script);
    packed
        .validate()
        .map_err(|e| internal(&format!("the packed manifest does not validate: {e}")))?;
    // The bytes laid out are the bytes hashed: a release swapped on disk
    // since it was extracted is refused rather than shipped.
    let python_bytes =
        std::fs::read(&built.archive).map_err(|e| io_failed("read the python release", &e))?;
    if layout::digest_of(&python_bytes) != format!("sha256:{}", PINNED.sha256) {
        return Err(gate(
            "the python release changed on disk while it was packed; remedy: pack again".into(),
        ));
    }
    let encoded =
        |e: serde_json::Error| internal(&format!("a package component did not encode: {e}"));
    let layers = Layers {
        manifest: serde_json::to_vec(&packed).map_err(encoded)?,
        files: built.files_tar,
        lock: serde_json::to_vec_pretty(&built.lock).map_err(encoded)?,
        wheels: built.wheels_tar,
        python: python_bytes,
    };
    let annotations = Annotations {
        name: packed.name.clone(),
        version: version.clone(),
        driver_id: packed.driver_id(),
        created: signature::rfc3339_utc(std::time::SystemTime::now()),
        publisher_key_id: key.key_id(),
        python_release: PINNED.file.into(),
        python_upstream_sha256: PINNED.sha256.into(),
    };
    // Armed once the layout is written, never before: `layout::write`
    // refuses an `--out` that is not empty (something may have landed there
    // since the check above), and what this pack did not write is not this
    // pack's to remove.
    let existed = out.exists();
    let (_, package_digest) = layout::write(&out, &layers, &annotations).map_err(refused)?;
    let written = OutGuard {
        out: &out,
        existed,
        keep: false,
    };
    layout::write_signature(&out, &package_digest, &key.sign(&package_digest)).map_err(refused)?;
    // The act is recorded when it happens, not when it was asked for.
    let ctx = Ctx {
        now_ms: vk_kernel::now_ms(),
        ..ctx
    };
    let row = lock(kernel)?
        .driver_packed(&ctx, &packed, &version, &package_digest, &key.key_id())
        .map_err(kerr)?;
    written.keep();
    Ok(json!({
        "package_digest": package_digest,
        "files_sha256": row.files_sha256,
        "driver_id": row.driver_id,
        "name": row.name,
        "version": row.version,
        "out": out.display().to_string(),
    }))
}

/// The stage half of [`pack`]: the files layer, the interpreter, the wheels
/// and the materialised tree, all under `stage`.
fn build(
    stage: &Path,
    driver_dir: &Path,
    archive: Option<&Path>,
    cache: &Path,
) -> Result<Built, RpcError> {
    let files = stage.join("files");
    stage_files(driver_dir, &files)?;
    let archive = match archive {
        Some(archive) => archive.to_path_buf(),
        None => python::fetch(cache).map_err(refused)?,
    };
    let wheels_dir = stage.join("wheels");
    let requirements = driver_dir.join("requirements.txt");
    let lock = if requirements.is_file() {
        // pip resolves against the interpreter the tree will run.
        let interpreter =
            python::extract(&archive, &stage.join("interp"), PINNED.sha256).map_err(refused)?;
        wheels::resolve(&interpreter.join("python.exe"), &requirements, &wheels_dir)
            .map_err(refused)?
    } else {
        std::fs::create_dir_all(&wheels_dir)
            .map_err(|e| io_failed("create the wheels directory", &e))?;
        vec![]
    };
    let files_tar = wheels::tar_dir(&files).map_err(refused)?;
    let wheels_tar = wheels::tar_dir(&wheels_dir).map_err(refused)?;
    let tree = materialise::materialise(
        &stage.join("tree"),
        &files_tar,
        &archive,
        PINNED.sha256,
        &wheels_tar,
        &lock,
    )
    .map_err(refused)?;
    Ok(Built {
        files_tar,
        wheels_tar,
        lock,
        fingerprint: tree.fingerprint,
        archive,
    })
}

/// The packed command: the packed interpreter and its flags, then the
/// script and whatever followed it in the development command.
fn packed_command(command: &[String], script: &str) -> Vec<String> {
    let after = command
        .iter()
        .position(|a| a == script)
        .map_or(&[][..], |i| &command[i + 1..]);
    PACKED_INTERPRETER
        .iter()
        .map(|s| s.to_string())
        .chain(std::iter::once(script.to_string()))
        .chain(after.iter().cloned())
        .collect()
}

/// The manifest `driver.pack` was pointed at, parsed and validated. Read
/// bounded; a refusal names where it failed to parse, never what it read.
fn read_manifest(file: &Path) -> Result<DriverManifest, RpcError> {
    let bytes = read_bounded(file).map_err(|e| {
        bad(&format!(
            "the manifest {} cannot be read ({}); remedy: point --manifest at the driver's \
             manifest.json",
            file.display(),
            e.kind()
        ))
    })?;
    let manifest: DriverManifest = serde_json::from_slice(&bytes).map_err(|e| {
        bad(&format!(
            "the manifest {} does not parse (line {}, column {}); remedy: correct it, then pack \
             again",
            file.display(),
            e.line(),
            e.column()
        ))
    })?;
    manifest.validate().map_err(|e| bad(&e.to_string()))?;
    Ok(manifest)
}

/// What the files layer carries: every regular file of the driver's
/// directory that `hash_dir` hashes — nothing under a `.venv`,
/// `__pycache__`, `tests` or `fixture` directory at any depth, no top-level
/// `manifest*.json` (the packed manifest is a layer of its own) — and, when
/// the directory has a sibling `vkdriver/`, that protocol library's `.py`
/// files, without its tests, under `vkdriver/` beside the driver's own.
///
/// Why beside: `drivers/maskai-recette/driver.py` puts its parent directory
/// first on `sys.path` and imports `vkdriver` from there. In an installed
/// tree that parent is `<install_root>/<name>`, which holds versions, not
/// libraries; but the script's own directory is on `sys.path` too (the
/// packed command runs without `-I`), so the library is found there, inside
/// the fingerprinted tree, with no change to the driver.
fn stage_files(driver_dir: &Path, dest: &Path) -> Result<(), RpcError> {
    let failed = |e: std::io::Error| io_failed("copy the driver's files into the stage", &e);
    copy_tree(driver_dir, dest, &|rel, is_dir| {
        let skipped = rel.components().any(|c| {
            matches!(
                c.as_os_str().to_str(),
                Some("__pycache__" | ".venv" | "tests" | "fixture")
            )
        });
        let top_manifest = !is_dir
            && rel.components().count() == 1
            && rel
                .to_str()
                .is_some_and(|n| n.starts_with("manifest") && n.ends_with(".json"));
        !skipped && !top_manifest
    })
    .map_err(failed)?;
    let shared = driver_dir.parent().map(|p| p.join("vkdriver"));
    let Some(shared) = shared.filter(|s| std::fs::symlink_metadata(s).is_ok_and(|m| m.is_dir()))
    else {
        return Ok(());
    };
    if dest.join("vkdriver").exists() {
        return Err(bad(
            "the driver directory has a vkdriver/ of its own beside the shared one in its parent; \
             remedy: keep one of the two",
        ));
    }
    copy_tree(&shared, &dest.join("vkdriver"), &|rel, is_dir| {
        let name = rel.file_name().and_then(|n| n.to_str()).unwrap_or_default();
        if is_dir {
            !matches!(name, "__pycache__" | ".venv" | "tests" | "fixture")
        } else {
            name.ends_with(".py") && !name.starts_with("test_") && !name.ends_with("_test.py")
        }
    })
    .map_err(failed)
}

/// Copies every regular file under `src` that `keep` accepts (given its
/// path relative to `src` and whether it is a directory; a directory it
/// refuses is not entered) to the same relative path under `dest`. Links
/// are neither followed nor copied, as `hash_dir` skips them.
fn copy_tree(src: &Path, dest: &Path, keep: &dyn Fn(&Path, bool) -> bool) -> std::io::Result<()> {
    std::fs::create_dir_all(dest)?;
    let mut pending = vec![src.to_path_buf()];
    while let Some(dir) = pending.pop() {
        for entry in std::fs::read_dir(&dir)? {
            let entry = entry?;
            let kind = entry.file_type()?;
            let path = entry.path();
            let rel = path.strip_prefix(src).expect("entry is under src");
            if kind.is_dir() && keep(rel, true) {
                pending.push(path);
            } else if kind.is_file() && keep(rel, false) {
                let to = dest.join(rel);
                if let Some(parent) = to.parent() {
                    std::fs::create_dir_all(parent)?;
                }
                std::fs::copy(&path, &to)?;
            }
        }
    }
    Ok(())
}

/// Whether `dir` is absent or an empty directory.
fn empty_or_absent(dir: &Path) -> bool {
    match std::fs::read_dir(dir) {
        Ok(mut entries) => entries.next().is_none(),
        Err(e) => e.kind() == std::io::ErrorKind::NotFound && !dir.exists(),
    }
}

/// Where a pack builds: `%LOCALAPPDATA%\vk\stage\<8 hex>` on Windows,
/// `$TMPDIR/vk-stage-<8 hex>` elsewhere. Short, because the interpreter's
/// deepest file lies 105 characters below the tree and Windows' path limit
/// is 260; never under the state directory, whose path may be long. Removed
/// when the pack ends, however it ends.
struct Stage(PathBuf);

impl Stage {
    fn new() -> std::io::Result<Stage> {
        let (base, prefix) = if cfg!(windows) {
            let local = std::env::var_os("LOCALAPPDATA")
                .filter(|v| !v.is_empty())
                .map_or_else(std::env::temp_dir, PathBuf::from);
            (local.join("vk").join("stage"), "")
        } else {
            (std::env::temp_dir(), "vk-stage-")
        };
        std::fs::create_dir_all(&base)?;
        loop {
            let dir = base.join(format!("{prefix}{:08x}", rand::random::<u32>()));
            match std::fs::create_dir(&dir) {
                Ok(()) => return Ok(Stage(dir)),
                Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(e) => return Err(e),
            }
        }
    }
}

impl Drop for Stage {
    fn drop(&mut self) {
        if let Err(e) = std::fs::remove_dir_all(&self.0) {
            tracing::warn!(stage = %self.0.display(), error = %e, "a pack's stage directory could not be removed");
        }
    }
}

/// The `--out` of a pack that has written its layout there: put back as it
/// was found — absent, or an empty directory (`existed`) — unless
/// [`keep`](Self::keep) is called once `driver.packed` is recorded. A
/// layout on disk is a package with its `driver.packed` row, or nothing.
struct OutGuard<'a> {
    out: &'a Path,
    existed: bool,
    keep: bool,
}

impl OutGuard<'_> {
    fn keep(mut self) {
        self.keep = true;
    }
}

impl Drop for OutGuard<'_> {
    fn drop(&mut self) {
        if self.keep {
            return;
        }
        let undone = std::fs::remove_dir_all(self.out).and_then(|()| {
            if self.existed {
                std::fs::create_dir(self.out)
            } else {
                Ok(())
            }
        });
        if let Err(e) = undone {
            tracing::warn!(out = %self.out.display(), error = %e, "a failed pack's --out could not be emptied");
        }
    }
}

/// A tree under the install root, verified for a mount by `name@version`.
pub(crate) struct Installed {
    pub manifest: DriverManifest,
    /// `<install_root>/<name>/<version>`, resolved as the kernel keeps it.
    pub root: PathBuf,
    pub package_digest: String,
    pub version: String,
}

/// The tree `driver.mount {installed: "name@version"}` names, refused unless
/// it is what its publisher signed (packaging spec §4.3):
///
/// 1. `spec` is `<name>@<MAJOR.MINOR.PATCH>`, the name by the manifest's
///    rule — both become path components;
/// 2. `<install_root>/<name>/<version>/manifest.json` exists, else the
///    version is not installed;
/// 3. `.package/manifest.json` is a driver package, whose digest — computed
///    here from the bytes in hand — is the package digest, and
///    `.package/signature.json` verifies over it against a publisher key
///    this node pinned;
/// 4. the package names this name and version, and its manifest layer is
///    the very `manifest.json` of the tree (which the fingerprint leaves
///    out, so only this ties it to the signature);
/// 5. that manifest validates, says `source: installed`, and the tree
///    hashes to its `files_sha256` ([`tree_holds`]; the fresh cache stored).
///
/// Nothing read from the tree is echoed before it is signed, and a refusal
/// names where, never what.
pub(crate) fn installed(kernel: &Shared, spec: &str) -> Result<Installed, RpcError> {
    let Some((name, version)) = spec
        .split_once('@')
        .filter(|(n, v)| is_driver_name(n) && is_version(v))
    else {
        return Err(bad(&format!(
            "{spec:?} is not name@version; remedy: vk mount driver <name>@<MAJOR.MINOR.PATCH>"
        )));
    };
    let (install_root, state_dir, cache_file) = {
        let k = lock(kernel)?;
        (
            k.install_root().to_path_buf(),
            k.store().state_dir.clone(),
            k.driver_fingerprint_cache(name),
        )
    };
    let root = vk_kernel::drivers::resolved(&install_root.join(name).join(version));
    let manifest_bytes = match read_bounded(&root.join("manifest.json")) {
        Ok(bytes) => bytes,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            return Err(RpcError {
                code: E_NOT_FOUND,
                message: format!("{spec} is not installed; remedy: vk driver install <package>"),
            })
        }
        Err(e) => {
            return Err(tree_refused(
                spec,
                &format!("its manifest.json cannot be read ({})", e.kind()),
            ))
        }
    };
    let package_dir = root.join(".package");
    let package_bytes = read_bounded(&package_dir.join("manifest.json")).map_err(|e| {
        tree_refused(
            spec,
            &format!("its .package/manifest.json cannot be read ({})", e.kind()),
        )
    })?;
    let package_digest = layout::digest_of(&package_bytes);
    let package: DriverPackage = serde_json::from_slice(&package_bytes)
        .map_err(|e| tree_refused(spec, &unparsed(".package/manifest.json", &e)))?;
    package
        .validate()
        .map_err(|e| tree_refused(spec, &format!("its package manifest: {e}")))?;
    let signature_bytes = read_bounded(&package_dir.join("signature.json")).map_err(|e| {
        tree_refused(
            spec,
            &format!("its .package/signature.json cannot be read ({})", e.kind()),
        )
    })?;
    let sig: Signature = serde_json::from_slice(&signature_bytes)
        .map_err(|e| tree_refused(spec, &unparsed(".package/signature.json", &e)))?;
    let pinned = Pins::at(&state_dir)
        .get(&sig.key_id)
        .map_err(|_| tree_refused(spec, "its signature names no well-formed publisher key"))?
        .ok_or_else(|| {
            // `get` checked the id's shape: `sha256:` and 64 hex digits.
            gate(format!(
                "publisher {} is not pinned on this node; remedy: vk driver install pins it",
                sig.key_id
            ))
        })?;
    signature::verify(&pinned, &package_digest, &sig).map_err(refused)?;
    if package.name() != name || package.version() != version {
        return Err(tree_refused(
            spec,
            "its package names another driver or version",
        ));
    }
    let signed_manifest = package.layer("manifest").map(|l| l.digest.as_str());
    if signed_manifest != Some(layout::digest_of(&manifest_bytes).as_str()) {
        return Err(gate(format!(
            "the installed manifest of {spec} is not the one its package signs; remedy: {REINSTALL}"
        )));
    }
    let manifest: DriverManifest = serde_json::from_slice(&manifest_bytes)
        .map_err(|e| tree_refused(spec, &unparsed("manifest.json", &e)))?;
    manifest
        .validate()
        .map_err(|e| tree_refused(spec, &e.to_string()))?;
    if manifest.source != DriverSource::Installed {
        return Err(tree_refused(
            spec,
            "its manifest does not say source installed",
        ));
    }
    let expected = &manifest.program.files_sha256;
    match tree_holds(&root, expected, Some(&cache_file))
        .map_err(|e| tree_refused(spec, &format!("its tree cannot be hashed ({})", e.kind())))?
    {
        Ok(()) => {}
        Err(actual) => {
            return Err(gate(format!(
                "installed tree fingerprint {actual} is not the manifest's {expected}; remedy: \
                 reinstall, then remount"
            )))
        }
    }
    Ok(Installed {
        manifest,
        root,
        package_digest,
        version: version.to_string(),
    })
}

/// Whether the installed tree at `root` hashes to `expected`, its manifest's
/// `files_sha256`, through the `(size, mtime)` cache in `cache_file`: a cache
/// whose stat set is the tree's answers without a read; otherwise the tree
/// is hashed whole and, when it holds, the fresh cache is stored (best
/// effort: a cache that cannot be written costs a rehash, nothing else). A
/// cache that matches the stat set but names another fingerprint is not
/// trusted to condemn the tree either — it may be another version's — so the
/// tree is hashed whole before it is. `Err(actual)` when it does not hold.
pub(crate) fn tree_holds(
    root: &Path,
    expected: &str,
    cache_file: Option<&Path>,
) -> std::io::Result<Result<(), String>> {
    let cache = cache_file.and_then(|f| fingerprint::load_cache(f).ok().flatten());
    let fresh = match fingerprint::check(root, cache.as_ref())? {
        Check::Hit(fingerprint) if fingerprint == expected => return Ok(Ok(())),
        Check::Hit(_) => {
            let entries = fingerprint::stat_tree(root)?;
            FingerprintCache {
                fingerprint: fingerprint::hash_tree(root)?,
                entries,
            }
        }
        Check::Rehashed(_, fresh) => fresh,
    };
    if fresh.fingerprint != expected {
        return Ok(Err(fresh.fingerprint));
    }
    if let Some(file) = cache_file {
        let stored = file
            .parent()
            .map_or(Ok(()), std::fs::create_dir_all)
            .and_then(|()| fingerprint::store_cache(file, &fresh));
        if let Err(e) = stored {
            tracing::warn!(cache = %file.display(), error = %e, "an installed tree's fingerprint cache could not be stored");
        }
    }
    Ok(Ok(()))
}

/// `driver.ls`: every mounted row, with its `name`, its `source`, the
/// version an installed one was mounted from (its root's last step, which
/// the kernel holds to `<name>/<version>`) and the versions of its name
/// installed; then one row per name installed and not mounted, in state
/// `installed`. Sorted by name.
pub(crate) fn listing(rows: Vec<DriverRow>, install_root: &Path) -> Result<Value, RpcError> {
    let mut installed = installed_versions(install_root).map_err(|e| RpcError {
        code: E_STORE,
        message: format!(
            "cannot list the install root {}: {e}",
            install_root.display()
        ),
    })?;
    let mut listed: Vec<Value> = vec![];
    for row in rows {
        let name = row.manifest.name.clone();
        let source = row.manifest.source;
        let version = (source == DriverSource::Installed)
            .then(|| {
                row.root
                    .file_name()
                    .and_then(|v| v.to_str())
                    .map(str::to_string)
            })
            .flatten();
        let mut v = serde_json::to_value(&row)
            .map_err(|e| internal(&format!("cannot encode response: {e}")))?;
        v["name"] = json!(name);
        v["source"] = json!(source);
        v["version"] = json!(version);
        v["installed_versions"] = json!(installed.remove(&name).unwrap_or_default());
        listed.push(v);
    }
    for (name, versions) in installed {
        listed.push(json!({
            "name": name,
            "state": {"state": "installed"},
            "source": "installed",
            "version": null,
            "installed_versions": versions,
        }));
    }
    listed.sort_by(|a, b| a["name"].as_str().cmp(&b["name"].as_str()));
    Ok(Value::Array(listed))
}

/// The versions installed under `install_root`, by name: every
/// `<name>/<version>` directory, the name by the manifest's rule and the
/// version `MAJOR.MINOR.PATCH`, that holds a `manifest.json`, the versions in
/// numeric order. Anything else there — a file, a link, an install still
/// under way (`<version>.part`) — is not a version. An absent root installs
/// nothing.
fn installed_versions(install_root: &Path) -> std::io::Result<BTreeMap<String, Vec<String>>> {
    let mut out = BTreeMap::new();
    let entries = match std::fs::read_dir(install_root) {
        Ok(entries) => entries,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(out),
        Err(e) => return Err(e),
    };
    for entry in entries {
        let entry = entry?;
        let Some(name) = entry.file_name().to_str().map(str::to_string) else {
            continue;
        };
        if !entry.file_type()?.is_dir() || !is_driver_name(&name) {
            continue;
        }
        let mut versions = vec![];
        for version in std::fs::read_dir(entry.path())? {
            let version = version?;
            let Some(v) = version.file_name().to_str().map(str::to_string) else {
                continue;
            };
            if version.file_type()?.is_dir()
                && is_version(&v)
                && version.path().join("manifest.json").is_file()
            {
                versions.push(v);
            }
        }
        if !versions.is_empty() {
            versions.sort_by_key(|v| version_key(v));
            out.insert(name, versions);
        }
    }
    Ok(out)
}

/// A `MAJOR.MINOR.PATCH` as a key that sorts numerically, at any length.
fn version_key(v: &str) -> Vec<(usize, String)> {
    v.split('.')
        .map(|part| {
            let digits = part.trim_start_matches('0');
            (digits.len(), digits.to_string())
        })
        .collect()
}

/// A file read whole, refused past [`MAX_SMALL_FILE`].
fn read_bounded(path: &Path) -> std::io::Result<Vec<u8>> {
    let file = std::fs::File::open(path)?;
    let mut bytes = Vec::new();
    file.take(MAX_SMALL_FILE + 1).read_to_end(&mut bytes)?;
    if bytes.len() as u64 > MAX_SMALL_FILE {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            "larger than a manifest may be",
        ));
    }
    Ok(bytes)
}

/// Where JSON failed to parse, never what it held: serde's message can quote
/// the file.
fn unparsed(what: &str, e: &serde_json::Error) -> String {
    format!(
        "its {what} does not parse (line {}, column {})",
        e.line(),
        e.column()
    )
}

/// An installed tree that is not what its package says, refused in the
/// kernel's voice with the one remedy there is.
fn tree_refused(spec: &str, why: &str) -> RpcError {
    gate(format!(
        "the installed tree of {spec} is refused: {why}; remedy: {REINSTALL}"
    ))
}

/// A refusal of the kind the kernel's gates give.
fn gate(message: String) -> RpcError {
    RpcError {
        code: E_INVARIANT,
        message,
    }
}

/// A `vk-capital` failure: a refusal is already one line in the kernel's
/// voice, ending in its remedy; anything else is the disk or the encoder.
fn refused(e: CapitalError) -> RpcError {
    match e {
        CapitalError::Refused(why) => gate(why),
        CapitalError::Io(e) => io_failed("pack", &e),
        CapitalError::Json(_) => internal("a package component did not encode"),
    }
}

fn io_failed(what: &str, e: &std::io::Error) -> RpcError {
    RpcError {
        code: E_STORE,
        message: format!("{what}: {e}"),
    }
}
