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

/// `pack.json`, beside a driver's manifest (ruling R-T8a): the repository
/// files outside the driver's directory that it needs at run time, each a
/// path relative to the repository root — the nearest directory above the
/// driver's that holds a `Cargo.toml`.
#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct PackJson {
    #[serde(default)]
    vendor: Vec<String>,
}

/// One path `pack.json` vendors: where it is, and where under the files
/// layer it lands (`vendor/…`).
struct Vendored {
    from: PathBuf,
    to: PathBuf,
    is_dir: bool,
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
        bad(
            "driver.pack takes manifest, version, out and python_cache, nothing else; remedy: \
             vk driver pack --manifest FILE --version V --out DIR",
        )
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
    let driver_dir = manifest_file.parent().ok_or_else(|| {
        bad("--manifest names no directory; remedy: name the manifest.json beside the driver's files")
    })?;
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
    manifest.validate().map_err(|e| {
        bad(&format!(
            "the manifest {} does not validate: {e}; remedy: correct it, then pack again",
            file.display()
        ))
    })?;
    Ok(manifest)
}

/// What the files layer carries: every regular file of the driver's
/// directory that `hash_dir` hashes — nothing under a `.venv`,
/// `__pycache__`, `tests` or `fixture` directory at any depth, no top-level
/// `manifest*.json` (the packed manifest is a layer of its own) — then the
/// repository files the driver needs from outside its directory:
///
/// - with a `pack.json` beside the manifest, exactly what it names
///   ([`vendor_plan`]), under `vendor/`: a directory as `vendor/<its
///   name>/` without tests or bytecode, a file as `vendor/<its directory's
///   name>/<file>`. The driver looks for them under `vendor/` first;
/// - without one, as before `pack.json` existed: when the directory has a
///   sibling `vkdriver/`, that protocol library's `.py` files, without its
///   tests, under `vkdriver/` beside the driver's own.
///
/// Why beside, in the second case: `drivers/maskai-recette/driver.py` puts
/// its parent directory first on `sys.path` and imports `vkdriver` from
/// there. In an installed tree that parent is `<install_root>/<name>`, which
/// holds versions, not libraries; but the script's own directory is on
/// `sys.path` too (the packed command runs without `-I`), so the library is
/// found there, inside the fingerprinted tree, with no change to the driver.
///
/// Everything `pack.json` can be refused for is refused before a file is
/// copied.
fn stage_files(driver_dir: &Path, dest: &Path) -> Result<(), RpcError> {
    let plan = vendor_plan(driver_dir)?;
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
    if let Some(plan) = plan {
        return vendor(&plan, dest);
    }
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

/// The paths `<driver_dir>/pack.json` vendors, every one checked: `None`
/// when there is no `pack.json`. Each entry is relative to the repository
/// root (the nearest directory above the driver's holding a `Cargo.toml`),
/// made of plain names only, there, and — links resolved — still inside the
/// repository; a file must sit in a directory, whose name it lands under;
/// no two entries may land on one place, nor one inside another (compared
/// as Windows compares names, case folded).
fn vendor_plan(driver_dir: &Path) -> Result<Option<Vec<Vendored>>, RpcError> {
    const REMEDY: &str = "correct pack.json beside the manifest, then pack again";
    let file = driver_dir.join("pack.json");
    let bytes = match read_bounded(&file) {
        Ok(bytes) => bytes,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => {
            return Err(bad(&format!(
                "{} cannot be read ({}); remedy: {REMEDY}",
                file.display(),
                e.kind()
            )))
        }
    };
    let spec: PackJson = serde_json::from_slice(&bytes).map_err(|e| {
        bad(&format!(
            "{} does not parse (line {}, column {}): it holds {{\"vendor\": [paths]}} and \
             nothing else; remedy: {REMEDY}",
            file.display(),
            e.line(),
            e.column()
        ))
    })?;
    let Some(repo) = driver_dir
        .ancestors()
        .skip(1)
        .find(|d| d.join("Cargo.toml").is_file())
    else {
        return Err(bad(&format!(
            "pack.json vendors repository files, and no directory above {} holds a Cargo.toml; \
             remedy: pack from a checkout of the repository, or remove pack.json",
            driver_dir.display()
        )));
    };
    let repo_resolved =
        std::fs::canonicalize(repo).map_err(|e| io_failed("resolve the repository root", &e))?;
    let mut plan: Vec<Vendored> = vec![];
    for entry in &spec.vendor {
        let rel = Path::new(entry);
        let outside = || {
            bad(&format!(
                "pack.json names {entry:?}, which is not a path that stays inside the repository \
                 {}; remedy: name it relative to the repository root, without `..`",
                repo.display()
            ))
        };
        if entry.is_empty()
            || !rel
                .components()
                .all(|c| matches!(c, std::path::Component::Normal(_)))
        {
            return Err(outside());
        }
        let from = repo.join(rel);
        let resolved = match std::fs::canonicalize(&from) {
            Ok(resolved) => resolved,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                return Err(bad(&format!(
                    "pack.json names {entry:?}, which is not there under {}; remedy: {REMEDY}",
                    repo.display()
                )))
            }
            Err(e) => return Err(io_failed("resolve a path pack.json names", &e)),
        };
        if !resolved.starts_with(&repo_resolved) {
            return Err(outside());
        }
        let is_dir = resolved.is_dir();
        let name = rel.file_name().expect("plain names only");
        let to = if is_dir {
            Path::new("vendor").join(name)
        } else {
            let Some(parent) = rel.parent().and_then(Path::file_name) else {
                return Err(bad(&format!(
                    "pack.json names {entry:?}, a file at the top of the repository: a vendored \
                     file lands under its directory's name, so it must be inside a directory; \
                     remedy: vendor a file inside a directory"
                )));
            };
            Path::new("vendor").join(parent).join(name)
        };
        let folded = |p: &Path| PathBuf::from(p.to_string_lossy().to_ascii_lowercase());
        if plan.iter().any(|v| {
            let (a, b) = (folded(&v.to), folded(&to));
            a.starts_with(&b) || b.starts_with(&a)
        }) {
            return Err(bad(&format!(
                "pack.json vendors two paths to {}; remedy: keep one of the two",
                to.display()
            )));
        }
        plan.push(Vendored {
            from: resolved,
            to,
            is_dir,
        });
    }
    Ok(Some(plan))
}

/// Copies what [`vendor_plan`] checked into the files under `dest`: a
/// directory without tests, bytecode or a `.venv`, a file as it is. A
/// driver whose own files already hold one of the places is refused before
/// anything is copied.
fn vendor(plan: &[Vendored], dest: &Path) -> Result<(), RpcError> {
    if let Some(taken) = plan
        .iter()
        .find(|v| std::fs::symlink_metadata(dest.join(&v.to)).is_ok())
    {
        return Err(bad(&format!(
            "the driver directory has a {} of its own, where pack.json vendors a repository \
             path; remedy: keep one of the two",
            taken.to.display()
        )));
    }
    let failed = |e: std::io::Error| io_failed("copy what pack.json vendors into the stage", &e);
    for v in plan {
        let to = dest.join(&v.to);
        if v.is_dir {
            copy_tree(&v.from, &to, &|rel, is_dir| {
                let name = rel.file_name().and_then(|n| n.to_str()).unwrap_or_default();
                if is_dir {
                    !matches!(name, "__pycache__" | ".venv" | "tests" | "fixture")
                } else {
                    let test = (name.starts_with("test_") && name.ends_with(".py"))
                        || name.ends_with("_test.py");
                    !test && !name.ends_with(".pyc") && !name.ends_with(".pyo")
                }
            })
            .map_err(failed)?;
        } else {
            if let Some(parent) = to.parent() {
                std::fs::create_dir_all(parent).map_err(failed)?;
            }
            std::fs::copy(&v.from, &to).map_err(failed)?;
        }
    }
    Ok(())
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
    if !is_key_id(&sig.key_id) {
        return Err(tree_refused(
            spec,
            "its signature names no well-formed publisher key",
        ));
    }
    let pinned = match Pins::at(&state_dir).get(&sig.key_id) {
        Ok(Some(pinned)) => pinned,
        Ok(None) => {
            return Err(gate(format!(
                "publisher {} is not pinned on this node; remedy: vk driver install pins it",
                sig.key_id
            )))
        }
        // The pin is there and is not a key: its own refusal says so, and how.
        Err(CapitalError::Refused(why)) => return Err(gate(why)),
        Err(e) => {
            let kind = match &e {
                CapitalError::Io(e) => e.kind().to_string(),
                _ => "not readable".into(),
            };
            return Err(gate(format!(
                "the pin of publisher {} on this node cannot be read ({kind}); remedy: check the \
                 permissions of {}, or pin the publisher again with vk driver install",
                sig.key_id,
                state_dir.join("capital").join("publishers").display()
            )));
        }
    };
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
/// nothing; a root that cannot be read is an error. An entry under it that
/// cannot be read is logged and skipped: one bad directory does not hide
/// every other driver from `driver.ls`.
fn installed_versions(install_root: &Path) -> std::io::Result<BTreeMap<String, Vec<String>>> {
    let mut out = BTreeMap::new();
    let entries = match std::fs::read_dir(install_root) {
        Ok(entries) => entries,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(out),
        Err(e) => return Err(e),
    };
    let skipped = |path: &Path, e: &std::io::Error| {
        tracing::warn!(path = %path.display(), error = %e, "an entry under the install root cannot be read; driver.ls leaves it out");
    };
    for entry in entries {
        let entry = match entry {
            Ok(entry) => entry,
            Err(e) => {
                skipped(install_root, &e);
                continue;
            }
        };
        let Some(name) = entry.file_name().to_str().map(str::to_string) else {
            continue;
        };
        match entry.file_type() {
            Ok(kind) if kind.is_dir() && is_driver_name(&name) => {}
            Ok(_) => continue,
            Err(e) => {
                skipped(&entry.path(), &e);
                continue;
            }
        }
        match versions_of(&entry.path()) {
            Ok(versions) if versions.is_empty() => {}
            Ok(mut versions) => {
                versions.sort_by_key(|v| version_key(v));
                out.insert(name, versions);
            }
            Err(e) => skipped(&entry.path(), &e),
        }
    }
    Ok(out)
}

/// The version directories of one name under the install root (see
/// [`installed_versions`]), in no order.
fn versions_of(name_dir: &Path) -> std::io::Result<Vec<String>> {
    let mut versions = vec![];
    for version in std::fs::read_dir(name_dir)? {
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
    Ok(versions)
}

/// `sha256:` and 64 lowercase hex digits: the shape of a publisher key id,
/// which also names its pin file.
fn is_key_id(id: &str) -> bool {
    id.strip_prefix("sha256:").is_some_and(|hex| {
        hex.len() == 64 && hex.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
    })
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

#[cfg(test)]
mod tests {
    use super::*;

    /// Every file under `dir`, `/`-joined and sorted.
    fn files_under(dir: &Path) -> Vec<String> {
        let mut out = vec![];
        let mut pending = vec![dir.to_path_buf()];
        while let Some(d) = pending.pop() {
            for entry in std::fs::read_dir(&d).unwrap() {
                let path = entry.unwrap().path();
                if path.is_dir() {
                    pending.push(path);
                } else {
                    let rel = path.strip_prefix(dir).unwrap().to_string_lossy();
                    out.push(rel.replace(std::path::MAIN_SEPARATOR, "/"));
                }
            }
        }
        out.sort();
        out
    }

    fn write(path: &Path, text: &str) {
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text).unwrap();
    }

    /// The files layer is what `hash_dir` hashes of the driver's directory
    /// and the shared `vkdriver` library's code beside it: no manifest, no
    /// `.venv`, no tests, no bytecode, in either.
    #[test]
    fn the_files_layer_is_the_hashed_files_and_the_shared_library_beside_them() {
        let t = tempfile::tempdir().unwrap();
        let drv = t.path().join("drivers").join("maskai-recette");
        for (rel, text) in [
            ("driver.py", "print(1)"),
            ("cases.json", "{}"),
            ("requirements.txt", ""),
            ("sub/helper.py", ""),
            ("manifest.json", "{}"),
            ("manifest-fixture.json", "{}"),
            (".venv/Scripts/python.exe", "MZ"),
            ("tests/test_driver.py", ""),
            ("fixture/page.html", ""),
            ("__pycache__/driver.cpython-313.pyc", ""),
            ("sub/manifest.json", "{}"),
        ] {
            write(&drv.join(rel), text);
        }
        let lib = t.path().join("drivers").join("vkdriver");
        for rel in [
            "__init__.py",
            "protocol.py",
            "hash_dir.py",
            "test_protocol.py",
            "protocol_test.py",
            "README.md",
            "__pycache__/protocol.cpython-313.pyc",
            "tests/test_more.py",
        ] {
            write(&lib.join(rel), "");
        }
        let dest = t.path().join("stage").join("files");
        stage_files(&drv, &dest).unwrap();
        assert_eq!(
            files_under(&dest),
            [
                "cases.json",
                "driver.py",
                "requirements.txt",
                "sub/helper.py",
                "sub/manifest.json",
                "vkdriver/__init__.py",
                "vkdriver/hash_dir.py",
                "vkdriver/protocol.py",
            ]
        );
    }

    /// A driver with no shared library beside it packs its own files alone;
    /// one that carries a `vkdriver/` of its own beside the shared one is
    /// refused rather than have one silently overwrite the other.
    #[test]
    fn a_driver_with_a_vkdriver_of_its_own_beside_the_shared_one_is_refused() {
        let t = tempfile::tempdir().unwrap();
        let drv = t.path().join("alone");
        write(&drv.join("driver.py"), "");
        let dest = t.path().join("files-alone");
        stage_files(&drv, &dest).unwrap();
        assert_eq!(files_under(&dest), ["driver.py"]);

        let drv = t.path().join("drivers").join("twice");
        write(&drv.join("driver.py"), "");
        write(&drv.join("vkdriver").join("protocol.py"), "");
        write(
            &t.path()
                .join("drivers")
                .join("vkdriver")
                .join("protocol.py"),
            "",
        );
        let e = stage_files(&drv, &t.path().join("files-twice")).unwrap_err();
        assert!(
            e.message.contains("remedy: keep one of the two"),
            "{}",
            e.message
        );
    }

    /// A repository laid out the way `pack.json` names it (ruling R-T8a):
    /// `<repo>/Cargo.toml`, the driver at `<repo>/drivers/d/`, the shared
    /// library at `<repo>/drivers/vkdriver/` and two scripts at
    /// `<repo>/scripts/recette/`, each beside debris a pack leaves out.
    fn repository(t: &Path, pack_json: &str) -> PathBuf {
        write(&t.join("Cargo.toml"), "[workspace]\n");
        let drv = t.join("drivers").join("d");
        write(&drv.join("driver.py"), "print(1)");
        write(&drv.join("pack.json"), pack_json);
        write(&drv.join("manifest.json"), "{}");
        for rel in [
            "__init__.py",
            "protocol.py",
            "test_protocol.py",
            "__pycache__/protocol.cpython-313.pyc",
            "tests/test_more.py",
        ] {
            write(&t.join("drivers").join("vkdriver").join(rel), "");
        }
        for rel in ["evaluer.py", "journal.py", "test_evaluer.py"] {
            write(&t.join("scripts").join("recette").join(rel), "");
        }
        drv
    }

    /// `pack.json` beside the manifest vendors repository files into the
    /// files layer: a directory under `vendor/<its name>/`, without tests or
    /// bytecode, a file under `vendor/<its directory's name>/`; the shared
    /// `vkdriver` is then copied only as `pack.json` says, never beside.
    #[test]
    fn a_pack_json_vendors_repository_files_under_vendor() {
        let t = tempfile::tempdir().unwrap();
        let drv = repository(
            t.path(),
            r#"{"vendor": ["drivers/vkdriver", "scripts/recette/evaluer.py", "scripts/recette/journal.py"]}"#,
        );
        let dest = t.path().join("stage").join("files");
        stage_files(&drv, &dest).unwrap();
        assert_eq!(
            files_under(&dest),
            [
                "driver.py",
                "pack.json",
                "vendor/recette/evaluer.py",
                "vendor/recette/journal.py",
                "vendor/vkdriver/__init__.py",
                "vendor/vkdriver/protocol.py",
            ]
        );
    }

    /// What `pack.json` may not name, refused in one line with its remedy
    /// before anything is copied: a path out of the repository, an absolute
    /// one, one that is not there, two entries that land on one place, a
    /// file at the repository's top, an unknown key; and no `Cargo.toml`
    /// above the driver means there is no repository to vendor from.
    #[test]
    fn a_pack_json_that_names_what_it_may_not_is_refused() {
        for (pack_json, says) in [
            (
                r#"{"vendor": ["../outside.py"]}"#,
                "stays inside the repository",
            ),
            (
                r#"{"vendor": ["C:/Windows/win.ini"]}"#,
                "stays inside the repository",
            ),
            (
                r#"{"vendor": ["/etc/passwd"]}"#,
                "stays inside the repository",
            ),
            (
                r#"{"vendor": ["scripts/recette/absent.py"]}"#,
                "is not there",
            ),
            (
                r#"{"vendor": ["drivers/vkdriver", "drivers/vkdriver"]}"#,
                "keep one of the two",
            ),
            (r#"{"vendor": ["Cargo.toml"]}"#, "inside a directory"),
            (r#"{"vendr": []}"#, "pack.json does not parse"),
        ] {
            let t = tempfile::tempdir().unwrap();
            let drv = repository(t.path(), pack_json);
            let dest = t.path().join("stage").join("files");
            let e = stage_files(&drv, &dest).unwrap_err();
            assert!(e.message.contains(says), "{pack_json}: {}", e.message);
            assert!(e.message.contains("; remedy: "), "{}", e.message);
            assert_eq!(e.message.lines().count(), 1, "{}", e.message);
            assert!(
                !dest.join("vendor").exists(),
                "{pack_json}: nothing vendored"
            );
        }
        let t = tempfile::tempdir().unwrap();
        let drv = repository(t.path(), r#"{"vendor": ["drivers/vkdriver"]}"#);
        std::fs::remove_file(t.path().join("Cargo.toml")).unwrap();
        let e = stage_files(&drv, &t.path().join("files")).unwrap_err();
        assert!(e.message.contains("Cargo.toml"), "{}", e.message);
        assert!(e.message.contains("; remedy: "), "{}", e.message);
    }

    /// The packed command is the packed interpreter and its flags, then the
    /// script and what followed it; the development interpreter is gone.
    #[test]
    fn the_packed_command_keeps_the_script_and_what_followed_it() {
        let dev: Vec<String> = [".venv/Scripts/python.exe", "-I", "driver.py", "--quiet"]
            .map(String::from)
            .to_vec();
        assert_eq!(
            packed_command(&dev, "driver.py"),
            [
                "python/python.exe",
                "-E",
                "-s",
                "-B",
                "driver.py",
                "--quiet"
            ]
        );
    }

    /// A manifest that does not validate is refused at pack with the
    /// literal `remedy:` token after the manifest's own words.
    #[test]
    fn a_manifest_that_does_not_validate_is_refused_with_a_remedy() {
        let t = tempfile::tempdir().unwrap();
        let file = t.path().join("manifest.json");
        write(
            &file,
            r#"{"name": "Not A Name", "kind": "browser",
                "program": {"runtime": "python", "command": ["python", "driver.py"],
                            "files_sha256": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
                            "browser_channel": null, "headless": true},
                "hosts": ["example.invalid"],
                "actions": [{"name": "echo", "reversible": true, "args": {"type": "object"},
                             "evidence": [], "to_register": false}],
                "min_interval_ms": 0, "max_action_ms": 60000,
                "clearance": {"max_scope": "business", "third_party_allowed": true},
                "source": "development", "governed": false}"#,
        );
        let e = read_manifest(&file).unwrap_err();
        assert!(e.message.contains("does not validate"), "{}", e.message);
        assert!(e.message.contains("; remedy: "), "{}", e.message);
        assert_eq!(e.message.lines().count(), 1, "{}", e.message);
    }

    /// One install-root entry that cannot be read is logged and skipped:
    /// `driver.ls` still lists every other version (Task 8 review).
    #[cfg(windows)]
    #[test]
    fn an_unreadable_entry_under_the_install_root_is_skipped_not_fatal() {
        let t = tempfile::tempdir().unwrap();
        write(&t.path().join("good/1.0.0/manifest.json"), "{}");
        let locked = t.path().join("locked");
        write(&locked.join("1.0.0/manifest.json"), "{}");
        // No inherited entry and none of its own: nobody may list it.
        let icacls = |args: &[&str]| {
            let ok = std::process::Command::new("icacls")
                .arg(&locked)
                .args(args)
                .stdout(std::process::Stdio::null())
                .status()
                .unwrap()
                .success();
            assert!(ok, "icacls {args:?}");
        };
        icacls(&["/inheritance:r"]);
        assert!(
            std::fs::read_dir(&locked).is_err(),
            "the entry is unreadable"
        );
        let listed = installed_versions(t.path());
        icacls(&["/reset"]);
        let listed = listed.unwrap();
        assert_eq!(
            listed,
            BTreeMap::from([("good".to_string(), vec!["1.0.0".to_string()])])
        );
    }

    /// Versions sort by number, not by text: `0.9.0` before `0.10.0`.
    #[test]
    fn versions_sort_numerically() {
        let mut v = vec!["1.0.0", "0.10.0", "0.9.0", "0.9.10", "0.9.9"];
        v.sort_by_key(|v| version_key(v));
        assert_eq!(v, ["0.9.0", "0.9.9", "0.9.10", "0.10.0", "1.0.0"]);
    }
}
