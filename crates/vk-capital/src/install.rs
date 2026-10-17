//! `vk driver install` and `vk driver uninstall`, the library half
//! (packaging spec §4.3, §5.2, D7–D9): a package laid out by `vk driver
//! pack` becomes the tree `<root>/<name>/<version>` a mount by
//! `name@version` runs, or is refused with nothing written.
//!
//! An install is an OS act by an administrator, off the ledger: the kernel
//! is not asked, and the mount that follows carries the package digest. It
//! trusts the publisher key the node pinned — or, the first time, the one
//! the administrator confirms on screen (TOFU, D8) — and nothing else: the
//! layout's digests (`layout::open`), the signature over the image manifest
//! (`signature::verify`), the annotations against the manifest layer, and
//! the tree it materialises against the fingerprint the publisher computed
//! (`files_sha256`), all before the tree is renamed into place. Versions are
//! immutable side by side (D9): an installed version is never overwritten.
//!
//! OS analogy: the bootloader's update path — check the image's partition
//! CRCs and vendor signature against the fused key (or burn the fuse, the
//! first time, on the operator's say-so), flash the spare slot, read the
//! slot back against the signed CRC, write-protect it, then switch the
//! slot in; a failed check erases the spare slot and boots nothing new.

use std::path::{Path, PathBuf};

use vk_contracts::driver::{is_driver_name, DriverManifest, DriverSource};
use vk_contracts::package::ANN_PYTHON_SHA256;

use crate::error::{refuse, CapitalError, Result};
use crate::materialise::{self, Materialised};
use crate::python::PINNED;
use crate::signature::{self, PinnedKey, Pins};
use crate::wheels::Locked;
use crate::{is_version, layout};

/// The longest path an installed tree may hold, root included (ruling
/// R-T6a): Windows' own limit is 260, and pip and the interpreter's tools do
/// not all opt in to longer ones.
const MAX_INSTALLED_PATH: usize = 240;
/// The deepest path of the pinned release, below the tree root
/// (`python/Lib/site-packages/pip/_internal/resolution/resolvelib/
/// __pycache__/found_candidates.cpython-313.pyc`): an install root deeper
/// than [`MAX_INSTALLED_PATH`] minus this cannot hold the interpreter.
const PYTHON_DEEPEST: usize = 105;
const ALTERED: &str = "do not install this copy; fetch the package again from its publisher";
/// What a path-length refusal recommends: never a root of the
/// administrator's own choosing, which may be one other accounts can write.
const SHORTER: &str = "give the driver a shorter name or version, or install under the default \
                       root, %ProgramFiles%\\Aubier\\drivers";

/// What an install did.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InstallReport {
    pub name: String,
    pub version: String,
    /// The image manifest's digest, which the signature signs.
    pub package_digest: String,
    /// The installed tree's fingerprint: the manifest's `files_sha256`.
    pub fingerprint: String,
    /// `<root>/<name>/<version>`.
    pub target: PathBuf,
    /// The publisher whose signature verified.
    pub publisher_key_id: String,
    /// Whether this install pinned the publisher (its first package here).
    pub pinned_now: bool,
}

/// What to do about a publisher key this node has not pinned.
pub enum Tofu<'a> {
    /// Show the key (its id and PEM) to the administrator: pin it and go on
    /// when the callback says yes, refuse when it says no.
    Confirm(&'a mut dyn FnMut(&PinnedKey) -> bool),
    /// Refuse: nobody is there to confirm a key.
    Refuse,
}

/// The tree builder [`install_with`] calls: [`materialise::materialise`]'s
/// signature, so that tests can stand in for the interpreter and pip.
pub type Materialiser<'a> =
    dyn Fn(&Path, &[u8], &Path, &str, &[u8], &[Locked]) -> Result<Materialised> + 'a;

/// Opens the layout, resolves the signature's key against `pins`; unknown key →
/// TOFU callback (pin on true, refuse on false); known key → verify, no prompt.
/// Checks the annotations against the manifest layer (name, version, driver id).
/// Refuses if `root/name/version` exists. Materialises into `root/name/version.part`,
/// compares the fingerprint with the manifest's `files_sha256` (mismatch → remove,
/// refuse "repack on a node with the same CPython release"), writes `.package/`
/// (image manifest + signature), renames `.part` → `version`, then pins.
///
/// `pinned_by` goes into the pin's sidecar (the administrator's SID).
/// `make_dir` creates `<root>/<name>` (when it is new) and `.part`, each of
/// which must not exist yet (`AlreadyExists` otherwise), with the
/// protection the tree is to have from its first byte: an elevated install
/// passes the read-only tree DACL's maker, so that nothing it materialises
/// — the interpreter and pip it runs as an administrator included — sits
/// for one moment in a directory another account can write.
pub fn install(
    layout: &Path,
    root: &Path,
    pins: &Pins,
    tofu: Tofu,
    pinned_by: &str,
    make_dir: &dyn Fn(&Path) -> Result<()>,
) -> Result<InstallReport> {
    install_with(
        layout,
        root,
        pins,
        tofu,
        pinned_by,
        make_dir,
        &materialise::materialise,
    )
}

/// [`install`], with the tree built by `materialiser` (the test seam; every
/// real caller goes through [`install`] and [`materialise::materialise`],
/// which extracts only the pinned release).
///
/// In this order, every check before the first write: the layout's digests;
/// the annotations as path components; the signature's key id and the
/// publisher annotation; the manifest layer against the annotations (name,
/// driver id, `source: installed`); the interpreter the package names
/// against the one this build pins; the target and an install under way;
/// the deepest path the interpreter would reach; the publisher the name is
/// bound to on this node ([`bound_publisher`], ruling R-F1: every version
/// of the name already installed was signed by this package's key); then
/// the signature — under the pinned key with no prompt, or under the key
/// the package carries (its id the signature's) before the administrator is
/// asked to pin it.
/// Neither `root` nor `<root>/<name>` may be a link or a junction. Then
/// `make_dir` makes `<name>` if it is new and `<version>.part` always —
/// one that appears meanwhile is another install's, refused and left to it
/// — the tree is built in the `.part`, its paths and its fingerprint
/// checked, `manifest.json` and `.package/` written, the `.part` renamed
/// into place and the publisher pinned (when it was confirmed now): a
/// rename that fails pins nothing, a pin that fails takes the tree back
/// out. A refusal from the first write on removes what this install made.
pub fn install_with(
    layout: &Path,
    root: &Path,
    pins: &Pins,
    tofu: Tofu,
    pinned_by: &str,
    make_dir: &dyn Fn(&Path) -> Result<()>,
    materialiser: &Materialiser<'_>,
) -> Result<InstallReport> {
    let opened = layout::open(layout)?;
    let package = &opened.package;
    let (name, version) = (package.name().to_string(), package.version().to_string());
    if !is_driver_name(&name) || !is_version(&version) {
        return refuse(
            "the package's name and version annotations are not a driver name and a \
             MAJOR.MINOR.PATCH version",
            ALTERED,
        );
    }
    let spec = format!("{name}@{version}");
    let sig = &opened.signature;
    if !is_key_id(&sig.key_id) || package.publisher_key_id() != sig.key_id {
        return refuse(
            format!(
                "the signature of {spec} names no well-formed publisher key, or not the one its \
                 annotations name"
            ),
            ALTERED,
        );
    }
    let manifest_bytes = layout::read_layer(&opened, "manifest")?;
    let manifest: DriverManifest = serde_json::from_slice(&manifest_bytes).or_else(|e| {
        refuse(
            format!(
                "the manifest layer of {spec} does not parse (line {}, column {})",
                e.line(),
                e.column()
            ),
            ALTERED,
        )
    })?;
    // Its signature is not verified yet: what `validate` would quote of it
    // (a name, a host) is the package's text, and stays out of the refusal.
    if manifest.validate().is_err() {
        return refuse(
            format!("the manifest layer of {spec} does not validate"),
            ALTERED,
        );
    }
    if manifest.name != name
        || manifest.driver_id() != package.driver_id()
        || manifest.source != DriverSource::Installed
    {
        return refuse(
            format!(
                "the annotations of {spec} do not name the installed driver its manifest layer \
                 holds"
            ),
            ALTERED,
        );
    }
    let python_sha256 = package
        .layer("python")
        .and_then(|l| l.annotations.get(ANN_PYTHON_SHA256));
    if python_sha256.map(String::as_str) != Some(PINNED.sha256) {
        return refuse(
            format!(
                "{spec} carries another python release than the one this vk pins ({})",
                PINNED.file
            ),
            "repack the driver with this version of vk",
        );
    }

    let parent = root.join(&name);
    let target = parent.join(&version);
    let part = parent.join(format!("{version}.part"));
    not_a_link(root, "the install root")?;
    not_a_link(&parent, "the install directory of the driver")?;
    if std::fs::symlink_metadata(&target).is_ok() {
        return refuse(
            format!("{spec} is already installed at {}", target.display()),
            &format!(
                "a version is never overwritten: pack a new version, or vk driver uninstall \
                 {spec} first"
            ),
        );
    }
    if std::fs::symlink_metadata(&part).is_ok() {
        return refuse(
            format!(
                "an install of {spec} is under way, or was cut short: {} exists",
                part.display()
            ),
            &format!("remove {}, then install again", part.display()),
        );
    }
    let reach = path_len(&part) + 1 + PYTHON_DEEPEST;
    if reach > MAX_INSTALLED_PATH {
        return refuse(
            format!(
                "the interpreter under {} would reach paths of {reach} characters, more than \
                 {MAX_INSTALLED_PATH}",
                part.display()
            ),
            SHORTER,
        );
    }

    bound_publisher(&parent, &spec, &name, &sig.key_id)?;
    let (publisher, confirmed) = publisher(&opened, &spec, pins, tofu)?;

    // From here on something is written: the guard removes it on any refusal.
    let lock: Vec<Locked> =
        serde_json::from_slice(&layout::read_layer(&opened, "lock")?).or_else(|e| {
            refuse(
                format!(
                    "the lock layer of {spec} does not parse (line {}, column {})",
                    e.line(),
                    e.column()
                ),
                ALTERED,
            )
        })?;
    let files = layout::read_layer(&opened, "files")?;
    let wheels = layout::read_layer(&opened, "wheels")?;
    std::fs::create_dir_all(root)?;
    not_a_link(root, "the install root")?;
    let mut guard = Partial {
        paths: vec![],
        made_parent: None,
        armed: true,
    };
    let parent_made = match std::fs::symlink_metadata(&parent) {
        Ok(_) => Err(CapitalError::Io(std::io::ErrorKind::AlreadyExists.into())),
        Err(_) => make_dir(&parent),
    };
    match parent_made {
        Ok(()) => guard.made_parent = Some(parent.clone()),
        // An earlier install's, or made meanwhile by another install of the
        // driver: it must be a plain directory.
        Err(CapitalError::Io(e)) if e.kind() == std::io::ErrorKind::AlreadyExists => {
            not_a_link(&parent, "the install directory of the driver")?
        }
        Err(e) => return Err(e),
    }
    match make_dir(&part) {
        Ok(()) => guard.paths.push(part.clone()),
        Err(CapitalError::Io(e)) if e.kind() == std::io::ErrorKind::AlreadyExists => {
            return refuse(
                format!(
                    "an install of {spec} is under way, or was cut short: {} exists",
                    part.display()
                ),
                &format!("remove {}, then install again", part.display()),
            )
        }
        Err(e) => return Err(e),
    }
    let archive = parent.join(format!(
        "{version}.python-{}.tar.gz",
        crate::unique_suffix()
    ));
    guard.paths.push(archive.clone());
    std::fs::write(&archive, layout::read_layer(&opened, "python")?)?;
    let built = materialiser(&part, &files, &archive, PINNED.sha256, &wheels, &lock)?;
    let _ = std::fs::remove_file(&archive);

    let deepest = deepest_path(&part)?;
    let reach = deepest.saturating_sub(".part".len());
    if reach > MAX_INSTALLED_PATH {
        return refuse(
            format!(
                "the tree of {spec} would hold a path of {reach} characters at {}, more than \
                 {MAX_INSTALLED_PATH}",
                target.display()
            ),
            SHORTER,
        );
    }
    let expected = &manifest.program.files_sha256;
    if &built.fingerprint != expected {
        return refuse(
            format!(
                "the tree of {spec} hashes to {}, not to its manifest's files_sha256 {expected}",
                built.fingerprint
            ),
            "repack on a node with the same CPython release",
        );
    }
    std::fs::write(part.join("manifest.json"), &manifest_bytes)?;
    let dot_package = part.join(".package");
    std::fs::create_dir_all(&dot_package)?;
    std::fs::write(dot_package.join("manifest.json"), &opened.package_bytes)?;
    std::fs::write(
        dot_package.join("signature.json"),
        serde_json::to_vec_pretty(sig)?,
    )?;
    std::fs::rename(&part, &target)?;
    // In place: from here a refusal takes the installed tree back out.
    guard.paths = vec![target.clone()];
    if confirmed {
        pins.pin(&publisher, pinned_by)?;
    }
    guard.armed = false;
    Ok(InstallReport {
        name,
        version,
        package_digest: opened.package_digest.clone(),
        fingerprint: built.fingerprint,
        target,
        publisher_key_id: publisher.key_id,
        pinned_now: confirmed,
    })
}

/// The publisher a driver name is bound to on this node (ruling R-F1): a
/// name already installed — at least one `MAJOR.MINOR.PATCH` directory
/// under `name_dir`, `<root>/<name>`; not a `.part`, not a tree moved
/// aside — takes a new version only from the key that signed it, read off
/// each version's `.package/signature.json`. Another key is refused before
/// the administrator is asked anything and with nothing written, even a
/// key this node pins for another driver: pinning a key is trusting a
/// publisher, not handing it every name. A version whose signature cannot
/// be read cannot be shown to be the same publisher's, and binds the name
/// too. The remedy is the administrator's: uninstall every version.
fn bound_publisher(name_dir: &Path, spec: &str, name: &str, key_id: &str) -> Result<()> {
    let remedy = format!("vk driver uninstall every version of {name} first");
    let entries = match std::fs::read_dir(name_dir) {
        Ok(entries) => entries,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(()),
        Err(e) => return Err(e.into()),
    };
    let mut versions = vec![];
    for entry in entries {
        let entry = entry?;
        let Some(version) = entry.file_name().to_str().map(str::to_string) else {
            continue;
        };
        if is_version(&version) && entry.file_type()?.is_dir() {
            versions.push(version);
        }
    }
    versions.sort();
    for version in versions {
        let file = name_dir
            .join(&version)
            .join(".package")
            .join("signature.json");
        match signed_by(&file) {
            Some(signer) if signer == key_id => {}
            Some(_) => {
                return refuse(
                    format!(
                        "the publisher of {spec} differs from the one that signed {name}@{version}"
                    ),
                    &remedy,
                )
            }
            None => {
                return refuse(
                    format!(
                        "the publisher that signed {name}@{version} cannot be read from {}",
                        file.display()
                    ),
                    &remedy,
                )
            }
        }
    }
    Ok(())
}

/// The key id a `.package/signature.json` names, or `None` when the file
/// cannot be read (past 64 KiB included) or is not a signature.
fn signed_by(file: &Path) -> Option<String> {
    use std::io::Read;
    let mut bytes = vec![];
    std::fs::File::open(file)
        .ok()?
        .take(64 * 1024 + 1)
        .read_to_end(&mut bytes)
        .ok()?;
    if bytes.len() > 64 * 1024 {
        return None;
    }
    serde_json::from_slice::<vk_contracts::package::Signature>(&bytes)
        .ok()
        .map(|s| s.key_id)
}

/// The publisher key the signature of `opened` verifies under, and whether
/// the administrator confirmed it now (so it is to be pinned): the pinned
/// key of the signature's id, with no prompt; otherwise the key the
/// package carries, refused unless its id is the signature's and the
/// signature verifies under it, and then only if `tofu` confirms it.
fn publisher(
    opened: &layout::Opened,
    spec: &str,
    pins: &Pins,
    tofu: Tofu,
) -> Result<(PinnedKey, bool)> {
    let sig = &opened.signature;
    let pinned =
        match pins.get(&sig.key_id) {
            Ok(pinned) => pinned,
            Err(CapitalError::Io(e)) => return refuse(
                format!(
                    "the pin of publisher {} on this node cannot be read ({})",
                    sig.key_id,
                    e.kind()
                ),
                "check the permissions of the state directory's capital/publishers, or pin the \
                 publisher again",
            ),
            Err(e) => return Err(e),
        };
    if let Some(pinned) = pinned {
        signature::verify(&pinned, &opened.package_digest, sig)?;
        return Ok((pinned, false));
    }
    let Some(pem) = opened.publisher_pem.as_deref() else {
        return refuse(
            format!(
                "publisher {} is not pinned on this node, and {spec} does not carry its key",
                sig.key_id
            ),
            "repack the driver with this version of vk, so the package carries its publisher key",
        );
    };
    let carried = signature::parse_public_pem(pem).or_else(|_| {
        refuse(
            format!("the publisher key {spec} carries is not a publisher public key"),
            ALTERED,
        )
    })?;
    if carried.key_id != sig.key_id {
        return refuse(
            format!("the publisher key {spec} carries is not the key its signature names"),
            ALTERED,
        );
    }
    signature::verify(&carried, &opened.package_digest, sig)?;
    let confirmed = match tofu {
        Tofu::Confirm(confirm) => confirm(&carried),
        Tofu::Refuse => {
            return refuse(
                format!(
                    "publisher {} is not pinned on this node, and this install may not pin it",
                    sig.key_id
                ),
                "run vk driver install from a terminal and confirm the publisher's key",
            )
        }
    };
    if !confirmed {
        return refuse(
            format!(
                "publisher {} was refused by the administrator, so {spec} is not installed",
                sig.key_id
            ),
            "install it again and confirm the key if you trust its publisher",
        );
    }
    Ok((carried, true))
}

/// Removes `root/name/version`; `mounted` says whether the daemon reports it mounted.
///
/// Refused, with nothing removed, when the name or version is not one (both
/// become path components), when the version is mounted, when the root or
/// `<root>/<name>` is a link or a junction, when it is not installed, and
/// when it is not a directory `vk driver install` made (a link is not
/// followed). The tree is moved aside to `<version>.removing-<unique>`
/// before it is deleted: Windows refuses that move while a file in it is
/// open — a driver still running, a shell, an editor — and the uninstall is
/// refused with the tree whole, never half removed. The name's directory
/// goes too once it is empty.
pub fn uninstall(root: &Path, name: &str, version: &str, mounted: bool) -> Result<()> {
    if !is_driver_name(name) || !is_version(version) {
        return refuse(
            format!("{name:?}@{version:?} is not a driver name and a MAJOR.MINOR.PATCH version"),
            "vk driver uninstall <name>@<MAJOR.MINOR.PATCH>",
        );
    }
    if mounted {
        return refuse(
            format!("{name}@{version} is mounted"),
            &format!("vk driver umount {name} first"),
        );
    }
    let parent = root.join(name);
    let target = parent.join(version);
    not_a_link(root, "the install root")?;
    not_a_link(&parent, "the install directory of the driver")?;
    match std::fs::symlink_metadata(&target) {
        Ok(m) if m.is_dir() && !is_link(&m) => {}
        Ok(_) => {
            return refuse(
                format!(
                    "{} is not a directory vk driver install made",
                    target.display()
                ),
                "remove it by hand",
            )
        }
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            return refuse(
                format!("{name}@{version} is not installed under {}", root.display()),
                "vk driver ls lists what is installed",
            )
        }
        Err(e) => return Err(e.into()),
    }
    let aside = parent.join(format!("{version}.removing-{}", crate::unique_suffix()));
    if let Err(e) = std::fs::rename(&target, &aside) {
        return refuse(
            format!(
                "{name}@{version} cannot be moved aside to be removed ({}): a file of it is open \
                 — a driver still running, a shell, an editor — or this shell may not write \
                 there",
                e.kind()
            ),
            &format!(
                "vk driver umount {name} and close what holds its files, from an elevated shell, \
                 then uninstall again"
            ),
        );
    }
    if let Err(e) = std::fs::remove_dir_all(&aside) {
        return refuse(
            format!(
                "{name}@{version} was moved aside to {} and could not be removed ({})",
                aside.display(),
                e.kind()
            ),
            &format!("remove {} by hand", aside.display()),
        );
    }
    // Only an empty one: `remove_dir` refuses a directory that holds anything.
    let _ = std::fs::remove_dir(&parent);
    Ok(())
}

/// Refused when `dir` exists and is a link of any kind — a symlink, or on
/// Windows a junction or any other reparse point — or not a directory: an
/// elevated install would write, and an uninstall remove, wherever it
/// points. An absent `dir` passes: it is made later, by this install.
fn not_a_link(dir: &Path, what: &str) -> Result<()> {
    match std::fs::symlink_metadata(dir) {
        Ok(m) if is_link(&m) => refuse(
            format!("{what} {} is a link or a junction", dir.display()),
            "make it a plain directory only administrators can write, or install under the \
             default root (%ProgramFiles%\\Aubier\\drivers, VK_DRIVER_INSTALL_ROOT unset)",
        ),
        Ok(m) if !m.is_dir() => refuse(
            format!("{what} {} is not a directory", dir.display()),
            "remove it, then install again",
        ),
        Ok(_) => Ok(()),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(e) => Err(e.into()),
    }
}

/// Whether `m` (read without following links) is a link: a symlink, or —
/// on Windows, by its attribute — a junction or any other reparse point.
fn is_link(m: &std::fs::Metadata) -> bool {
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x400;
        if m.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
            return true;
        }
    }
    m.file_type().is_symlink()
}

/// What an install has written and must remove if it is refused: the
/// `.part` tree and the interpreter archive beside it, then the name's
/// directory if this install made it and it is empty again.
struct Partial {
    paths: Vec<PathBuf>,
    made_parent: Option<PathBuf>,
    armed: bool,
}

impl Drop for Partial {
    fn drop(&mut self) {
        if !self.armed {
            return;
        }
        for path in &self.paths {
            let _ = match std::fs::symlink_metadata(path) {
                Ok(m) if m.is_dir() => std::fs::remove_dir_all(path),
                Ok(_) => std::fs::remove_file(path),
                Err(_) => Ok(()),
            };
        }
        if let Some(parent) = &self.made_parent {
            let _ = std::fs::remove_dir(parent);
        }
    }
}

/// `sha256:` and 64 lowercase hex digits.
fn is_key_id(id: &str) -> bool {
    id.strip_prefix("sha256:").is_some_and(|hex| {
        hex.len() == 64 && hex.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
    })
}

/// The length of `path` as Windows counts it: UTF-16 code units.
fn path_len(path: &Path) -> usize {
    path.to_string_lossy().encode_utf16().count()
}

/// The length of the longest path under `root`, `root` included.
fn deepest_path(root: &Path) -> Result<usize> {
    let mut deepest = path_len(root);
    for entry in walkdir::WalkDir::new(root).min_depth(1) {
        let entry = entry.map_err(std::io::Error::from)?;
        deepest = deepest.max(path_len(entry.path()));
    }
    Ok(deepest)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::fingerprint;
    use crate::layout::{Annotations, Layers};
    use crate::signature::{parse_public_pem, KeySource, PublisherKey};
    use std::cell::Cell;
    use vk_contracts::package::{DriverPackage, Signature};

    /// A node: an install root that does not exist yet and the pins of a
    /// state directory, under one temporary directory.
    struct Node {
        t: tempfile::TempDir,
        root: PathBuf,
        pins: Pins,
    }

    fn node() -> Node {
        let t = tempfile::tempdir().unwrap();
        let root = t.path().join("installed");
        let pins = Pins::at(&t.path().join("state"));
        Node { t, root, pins }
    }

    fn key(dir: &Path, file: &str) -> PublisherKey {
        PublisherKey::load_or_create(&KeySource::File(dir.join(file))).unwrap()
    }

    /// The materialiser of these tests: the files layer untarred and
    /// nothing else (no interpreter, no pip), fingerprinted as an install
    /// fingerprints.
    fn files_only(
        root: &Path,
        files: &[u8],
        _python: &Path,
        _sha256: &str,
        _wheels: &[u8],
        _lock: &[Locked],
    ) -> Result<Materialised> {
        std::fs::create_dir_all(root)?;
        crate::wheels::untar_into(files, root)?;
        Ok(Materialised {
            root: root.to_path_buf(),
            fingerprint: fingerprint::hash_tree(root)?,
        })
    }

    /// What a package says, each part changeable to make a bad one.
    struct Pack<'a> {
        name: &'a str,
        version: &'a str,
        signer: &'a PublisherKey,
        /// The key id the signature claims, when not the signer's.
        claimed: Option<String>,
        /// The public key the layout carries, when not the signer's.
        carried: Option<String>,
        /// The name the manifest layer holds, when not `name`.
        manifest_name: Option<&'a str>,
        python_sha256: &'a str,
    }

    fn pack_of<'a>(name: &'a str, version: &'a str, signer: &'a PublisherKey) -> Pack<'a> {
        Pack {
            name,
            version,
            signer,
            claimed: None,
            carried: None,
            manifest_name: None,
            python_sha256: PINNED.sha256,
        }
    }

    /// Writes the package `p` describes under `dir` and returns its layout:
    /// one script as its files, no wheels, a stand-in for the interpreter,
    /// the manifest's `files_sha256` the fingerprint [`files_only`] gives.
    fn pack(dir: &Path, p: Pack) -> PathBuf {
        let n = crate::unique_suffix();
        let src = dir.join(format!("src-{n}"));
        std::fs::create_dir_all(&src).unwrap();
        std::fs::write(src.join("driver.py"), "print('hello')\n").unwrap();
        let files = crate::wheels::tar_dir(&src).unwrap();
        let empty = dir.join(format!("wheels-{n}"));
        std::fs::create_dir_all(&empty).unwrap();
        let wheels = crate::wheels::tar_dir(&empty).unwrap();
        let tree = files_only(
            &dir.join(format!("tree-{n}")),
            &files,
            Path::new("unused"),
            "",
            &wheels,
            &[],
        )
        .unwrap();
        let manifest: DriverManifest = serde_json::from_value(serde_json::json!({
            "name": p.manifest_name.unwrap_or(p.name),
            "kind": "browser",
            "program": {
                "runtime": "python",
                "command": ["python/python.exe", "-E", "-s", "-B", "driver.py"],
                "files_sha256": tree.fingerprint,
                "browser_channel": null,
                "headless": true
            },
            "hosts": ["example.invalid"],
            "actions": [{
                "name": "echo",
                "reversible": true,
                "args": {"type": "object"},
                "evidence": [],
                "to_register": false
            }],
            "min_interval_ms": 0,
            "max_action_ms": 60_000,
            "clearance": {"max_scope": "business", "third_party_allowed": true},
            "source": "installed",
            "governed": false
        }))
        .unwrap();
        let out = dir.join(format!("pkg-{n}"));
        let key_id = p.claimed.clone().unwrap_or_else(|| p.signer.key_id());
        let (_, digest) = layout::write(
            &out,
            &Layers {
                manifest: serde_json::to_vec(&manifest).unwrap(),
                files,
                lock: b"[]".to_vec(),
                wheels,
                python: b"PY".to_vec(),
            },
            &Annotations {
                name: p.name.into(),
                version: p.version.into(),
                driver_id: manifest.driver_id(),
                created: "2026-10-17T00:00:00Z".into(),
                publisher_key_id: key_id.clone(),
                python_release: PINNED.file.into(),
                python_upstream_sha256: p.python_sha256.into(),
            },
        )
        .unwrap();
        let mut sig = p.signer.sign(&digest);
        sig.key_id = key_id;
        let carried = p.carried.unwrap_or_else(|| p.signer.public_pem());
        layout::write_signature(&out, &digest, &sig, &carried).unwrap();
        out
    }

    /// The tree maker of a non-elevated install: a plain directory, which
    /// must not exist yet.
    fn plain_dir(dir: &Path) -> Result<()> {
        std::fs::create_dir(dir)?;
        Ok(())
    }

    /// Nothing was installed under `root`: no version, no `.part`, no
    /// interpreter archive, not even an empty directory — `root` itself may
    /// be there, empty.
    fn nothing_under(root: &Path) {
        let left: Vec<PathBuf> = walkdir::WalkDir::new(root)
            .min_depth(1)
            .into_iter()
            .filter_map(|e| e.ok())
            .map(|e| e.into_path())
            .collect();
        assert!(left.is_empty(), "left under {root:?}: {left:?}");
    }

    /// `at` made a link to `target`: a junction on Windows (it needs no
    /// privilege), a symlink elsewhere.
    fn link(target: &Path, at: &Path) {
        #[cfg(windows)]
        {
            let made = std::process::Command::new("cmd")
                .args(["/c", "mklink", "/J"])
                .arg(at)
                .arg(target)
                .output()
                .unwrap();
            assert!(
                made.status.success(),
                "mklink /J: {}",
                String::from_utf8_lossy(&made.stderr)
            );
        }
        #[cfg(unix)]
        std::os::unix::fs::symlink(target, at).unwrap();
    }

    /// The tree maker of an elevated install, as `vk driver install` uses
    /// it: the read-only tree DACL on Windows, mode 0755 elsewhere.
    #[cfg(windows)]
    fn protected_dir(dir: &Path) -> Result<()> {
        vk_store::win_acl::create_readonly_tree_dir(dir).map_err(|e| {
            match e.downcast_ref::<std::io::Error>().map(std::io::Error::kind) {
                Some(std::io::ErrorKind::AlreadyExists) => {
                    CapitalError::Io(std::io::ErrorKind::AlreadyExists.into())
                }
                _ => CapitalError::Refused(format!("{e:#}")),
            }
        })
    }

    #[cfg(unix)]
    fn protected_dir(dir: &Path) -> Result<()> {
        use std::os::unix::fs::{DirBuilderExt, PermissionsExt};
        std::fs::DirBuilder::new().mode(0o755).create(dir)?;
        std::fs::set_permissions(dir, std::fs::Permissions::from_mode(0o755))?;
        Ok(())
    }

    /// `dir` holds exactly the read-only tree DACL, read back by value.
    #[cfg(windows)]
    fn assert_protected(dir: &Path) {
        use vk_store::win_acl::{
            path_dacl, ADMINISTRATORS_SID, FILE_ALL_ACCESS_MASK, LOCAL_SYSTEM_SID,
            READ_AND_EXECUTE_MASK, USERS_SID,
        };
        let view = path_dacl(dir).unwrap();
        assert!(view.protected, "{view:?}");
        let mut held: Vec<(String, u32)> = view
            .entries
            .iter()
            .map(|e| (e.sid.clone(), e.mask))
            .collect();
        held.sort();
        let mut wanted = vec![
            (ADMINISTRATORS_SID.to_string(), FILE_ALL_ACCESS_MASK),
            (LOCAL_SYSTEM_SID.to_string(), FILE_ALL_ACCESS_MASK),
            (USERS_SID.to_string(), READ_AND_EXECUTE_MASK),
        ];
        wanted.sort();
        assert_eq!(held, wanted, "{view:?}");
    }

    #[cfg(unix)]
    fn assert_protected(dir: &Path) {
        use std::os::unix::fs::PermissionsExt;
        let mode = std::fs::metadata(dir).unwrap().permissions().mode();
        assert_eq!(mode & 0o777, 0o755, "{mode:o}");
    }

    #[test]
    fn an_unknown_publisher_is_pinned_on_confirmation_and_refused_otherwise() {
        let n = node();
        let k = key(n.t.path(), "k");
        let asked = Cell::new(0);
        let mut yes = |shown: &PinnedKey| {
            asked.set(asked.get() + 1);
            assert_eq!(shown.key_id, k.key_id());
            true
        };
        let first = pack(n.t.path(), pack_of("fake", "1.0.0", &k));
        let r = install_with(
            &first,
            &n.root,
            &n.pins,
            Tofu::Confirm(&mut yes),
            "S-1-5-21-1",
            &plain_dir,
            &files_only,
        )
        .unwrap();
        assert!(r.pinned_now);
        assert_eq!(asked.get(), 1);
        assert_eq!(r.publisher_key_id, k.key_id());
        assert!(n.pins.get(&k.key_id()).unwrap().is_some(), "pinned");
        assert_eq!(r.target, n.root.join("fake").join("1.0.0"));
        assert!(r.target.join("driver.py").is_file());

        let second = pack(n.t.path(), pack_of("fake", "1.1.0", &k));
        let r = install_with(
            &second,
            &n.root,
            &n.pins,
            Tofu::Confirm(&mut yes),
            "S-1-5-21-1",
            &plain_dir,
            &files_only,
        )
        .unwrap();
        assert!(!r.pinned_now);
        assert_eq!(
            asked.get(),
            1,
            "a pinned publisher is not asked about again"
        );

        let fresh = node();
        let mut no = |_: &PinnedKey| {
            asked.set(asked.get() + 1);
            false
        };
        let e = install_with(
            &first,
            &fresh.root,
            &fresh.pins,
            Tofu::Confirm(&mut no),
            "S-1-5-21-1",
            &plain_dir,
            &files_only,
        )
        .unwrap_err()
        .to_string();
        assert!(e.contains("refused by the administrator"), "{e}");
        assert!(e.contains("; remedy: "), "{e}");
        assert_eq!(asked.get(), 2);
        nothing_under(&fresh.root);
        assert!(fresh.pins.list().unwrap().is_empty(), "nothing pinned");
    }

    /// A driver name installed on a node binds its publisher (ruling R-F1):
    /// a package of that name signed by another key is refused before any
    /// prompt, with nothing written and nothing pinned — even when this
    /// node already pins that other key for another driver; the same key
    /// installs the next version; once every version of the name is
    /// uninstalled, another publisher may install it again.
    #[test]
    fn a_name_installed_from_one_publisher_refuses_another_publishers_package() {
        let n = node();
        let k1 = key(n.t.path(), "k1");
        let k2 = key(n.t.path(), "k2");
        let mut yes = |_: &PinnedKey| true;
        install_with(
            &pack(n.t.path(), pack_of("fake", "1.0.0", &k1)),
            &n.root,
            &n.pins,
            Tofu::Confirm(&mut yes),
            "S-1-5-21-1",
            &plain_dir,
            &files_only,
        )
        .unwrap();

        let other = pack(n.t.path(), pack_of("fake", "1.1.0", &k2));
        let asked = Cell::new(0);
        let mut ask = |_: &PinnedKey| {
            asked.set(asked.get() + 1);
            true
        };
        let e = install_with(
            &other,
            &n.root,
            &n.pins,
            Tofu::Confirm(&mut ask),
            "S-1-5-21-1",
            &plain_dir,
            &files_only,
        )
        .unwrap_err()
        .to_string();
        assert_eq!(
            e,
            "the publisher of fake@1.1.0 differs from the one that signed fake@1.0.0; remedy: \
             vk driver uninstall every version of fake first"
        );
        assert_eq!(asked.get(), 0, "no prompt");
        assert!(!n.root.join("fake").join("1.1.0").exists());
        assert!(!n.root.join("fake").join("1.1.0.part").exists());
        assert!(
            n.pins.get(&k2.key_id()).unwrap().is_none(),
            "nothing pinned"
        );

        // A key this node pins for another driver is no key for this name.
        install_with(
            &pack(n.t.path(), pack_of("other", "1.0.0", &k2)),
            &n.root,
            &n.pins,
            Tofu::Confirm(&mut ask),
            "S-1-5-21-1",
            &plain_dir,
            &files_only,
        )
        .unwrap();
        assert_eq!(asked.get(), 1, "k2 was pinned for `other`");
        let e = install_with(
            &other,
            &n.root,
            &n.pins,
            Tofu::Confirm(&mut ask),
            "S-1-5-21-1",
            &plain_dir,
            &files_only,
        )
        .unwrap_err()
        .to_string();
        assert!(
            e.contains("differs from the one that signed fake@1.0.0"),
            "{e}"
        );
        assert_eq!(asked.get(), 1, "still no prompt for fake");

        // The same publisher installs the next version, unprompted.
        install_with(
            &pack(n.t.path(), pack_of("fake", "1.2.0", &k1)),
            &n.root,
            &n.pins,
            Tofu::Confirm(&mut ask),
            "S-1-5-21-1",
            &plain_dir,
            &files_only,
        )
        .unwrap();
        assert_eq!(asked.get(), 1);

        // The remedy: every version of the name gone, another publisher may.
        uninstall(&n.root, "fake", "1.0.0", false).unwrap();
        uninstall(&n.root, "fake", "1.2.0", false).unwrap();
        install_with(
            &other,
            &n.root,
            &n.pins,
            Tofu::Confirm(&mut ask),
            "S-1-5-21-1",
            &plain_dir,
            &files_only,
        )
        .unwrap();
    }

    /// A version of the name whose signature cannot be read cannot be shown
    /// to be the same publisher's: the install is refused, naming it.
    #[test]
    fn a_version_whose_publisher_cannot_be_read_binds_the_name_too() {
        let n = node();
        let k = key(n.t.path(), "k");
        let stray = n.root.join("fake").join("1.0.0");
        std::fs::create_dir_all(&stray).unwrap();
        let mut ask = |_: &PinnedKey| true;
        let e = install_with(
            &pack(n.t.path(), pack_of("fake", "1.1.0", &k)),
            &n.root,
            &n.pins,
            Tofu::Confirm(&mut ask),
            "S-1-5-21-1",
            &plain_dir,
            &files_only,
        )
        .unwrap_err()
        .to_string();
        assert!(
            e.contains("the publisher that signed fake@1.0.0 cannot be read"),
            "{e}"
        );
        assert!(
            e.ends_with("; remedy: vk driver uninstall every version of fake first"),
            "{e}"
        );
        assert!(!n.root.join("fake").join("1.1.0").exists());
    }

    #[test]
    fn a_known_key_with_a_bad_signature_is_refused_without_a_prompt() {
        let n = node();
        let k1 = key(n.t.path(), "k1");
        let k2 = key(n.t.path(), "k2");
        n.pins
            .pin(&parse_public_pem(&k1.public_pem()).unwrap(), "S-1-5-21-1")
            .unwrap();
        let forged = pack(
            n.t.path(),
            Pack {
                claimed: Some(k1.key_id()),
                carried: Some(k1.public_pem()),
                ..pack_of("fake", "1.0.0", &k2)
            },
        );
        let asked = Cell::new(0);
        let mut ask = |_: &PinnedKey| {
            asked.set(asked.get() + 1);
            true
        };
        let e = install_with(
            &forged,
            &n.root,
            &n.pins,
            Tofu::Confirm(&mut ask),
            "S-1-5-21-1",
            &plain_dir,
            &files_only,
        )
        .unwrap_err()
        .to_string();
        assert!(e.contains("does not verify"), "{e}");
        assert_eq!(asked.get(), 0, "no prompt");
        nothing_under(&n.root);
    }

    #[test]
    fn a_fingerprint_mismatch_removes_the_tree_and_refuses() {
        let n = node();
        let k = key(n.t.path(), "k");
        let layout = pack(n.t.path(), pack_of("fake", "1.0.0", &k));
        let elsewhere = |root: &Path,
                         files: &[u8],
                         python: &Path,
                         sha: &str,
                         wheels: &[u8],
                         lock: &[Locked]|
         -> Result<Materialised> {
            assert!(
                python.is_file(),
                "the interpreter layer is handed over as a file"
            );
            let built = files_only(root, files, python, sha, wheels, lock)?;
            Ok(Materialised {
                fingerprint: format!("sha256:{}", "0".repeat(64)),
                ..built
            })
        };
        let e = install_with(
            &layout,
            &n.root,
            &n.pins,
            Tofu::Confirm(&mut |_| true),
            "S-1-5-21-1",
            &plain_dir,
            &elsewhere,
        )
        .unwrap_err()
        .to_string();
        assert!(e.contains("repack"), "{e}");
        assert!(!n.root.join("fake").join("1.0.0").exists());
        assert!(!n.root.join("fake").join("1.0.0.part").exists());
        nothing_under(&n.root);
        assert!(n.pins.list().unwrap().is_empty(), "nothing pinned");
    }

    #[test]
    fn an_existing_version_is_refused_untouched() {
        let n = node();
        let k = key(n.t.path(), "k");
        let target = n.root.join("fake").join("1.0.0");
        std::fs::create_dir_all(&target).unwrap();
        std::fs::write(target.join("marker"), b"mine").unwrap();
        let layout = pack(n.t.path(), pack_of("fake", "1.0.0", &k));
        let asked = Cell::new(0);
        let mut ask = |_: &PinnedKey| {
            asked.set(asked.get() + 1);
            true
        };
        let e = install_with(
            &layout,
            &n.root,
            &n.pins,
            Tofu::Confirm(&mut ask),
            "S-1-5-21-1",
            &plain_dir,
            &files_only,
        )
        .unwrap_err()
        .to_string();
        assert!(e.contains("already installed"), "{e}");
        assert!(e.contains("; remedy: "), "{e}");
        assert_eq!(std::fs::read(target.join("marker")).unwrap(), b"mine");
        assert_eq!(asked.get(), 0, "refused before the prompt");
    }

    #[test]
    fn uninstall_refuses_a_mounted_version_and_removes_an_idle_one() {
        let n = node();
        let k = key(n.t.path(), "k");
        let layout = pack(n.t.path(), pack_of("fake", "1.0.0", &k));
        let r = install_with(
            &layout,
            &n.root,
            &n.pins,
            Tofu::Confirm(&mut |_| true),
            "S-1-5-21-1",
            &plain_dir,
            &files_only,
        )
        .unwrap();
        let e = uninstall(&n.root, "fake", "1.0.0", true)
            .unwrap_err()
            .to_string();
        assert_eq!(
            e,
            "fake@1.0.0 is mounted; remedy: vk driver umount fake first"
        );
        assert!(r.target.join("driver.py").is_file(), "untouched");

        uninstall(&n.root, "fake", "1.0.0", false).unwrap();
        assert!(!r.target.exists());
        assert!(!n.root.join("fake").exists(), "an empty name goes with it");

        let e = uninstall(&n.root, "fake", "1.0.0", false)
            .unwrap_err()
            .to_string();
        assert!(e.contains("fake@1.0.0 is not installed"), "{e}");
        assert!(e.contains("; remedy: "), "{e}");
        for (name, version) in [("Fake", "1.0.0"), ("fake", "1.0"), ("..", "1.0.0")] {
            let e = uninstall(&n.root, name, version, false)
                .unwrap_err()
                .to_string();
            assert!(e.contains("; remedy: "), "{name}@{version}: {e}");
        }
    }

    #[test]
    fn the_installed_tree_carries_the_package_manifest_and_signature() {
        let n = node();
        let k = key(n.t.path(), "k");
        let layout = pack(n.t.path(), pack_of("fake", "1.0.0", &k));
        let opened = layout::open(&layout).unwrap();
        let r = install_with(
            &layout,
            &n.root,
            &n.pins,
            Tofu::Confirm(&mut |_| true),
            "S-1-5-21-1",
            &plain_dir,
            &files_only,
        )
        .unwrap();
        assert_eq!(r.package_digest, opened.package_digest);
        let package = r.target.join(".package");
        let bytes = std::fs::read(package.join("manifest.json")).unwrap();
        assert_eq!(layout::digest_of(&bytes), r.package_digest, "verbatim");
        let parsed: DriverPackage = serde_json::from_slice(&bytes).unwrap();
        assert_eq!((parsed.name(), parsed.version()), ("fake", "1.0.0"));
        let sig: Signature =
            serde_json::from_slice(&std::fs::read(package.join("signature.json")).unwrap())
                .unwrap();
        let pinned = n.pins.get(&sig.key_id).unwrap().unwrap();
        signature::verify(&pinned, &r.package_digest, &sig).unwrap();
        assert_eq!(
            std::fs::read(r.target.join("manifest.json")).unwrap(),
            layout::read_layer(&opened, "manifest").unwrap(),
            "the manifest layer, verbatim"
        );
        let manifest: DriverManifest =
            serde_json::from_slice(&std::fs::read(r.target.join("manifest.json")).unwrap())
                .unwrap();
        assert_eq!(manifest.program.files_sha256, r.fingerprint);
        assert_eq!(fingerprint::hash_tree(&r.target).unwrap(), r.fingerprint);
        let beside: Vec<_> = std::fs::read_dir(n.root.join("fake"))
            .unwrap()
            .map(|e| e.unwrap().file_name())
            .collect();
        assert_eq!(beside, ["1.0.0"], "no .part, no interpreter archive left");
    }

    /// What the package says must hold together, and is refused before the
    /// administrator is asked and before anything is written: a publisher
    /// this install may not pin, a carried key that is not the signature's,
    /// a signature that does not verify under the carried key, an
    /// interpreter this build does not pin, a manifest layer that is not
    /// the driver the annotations name.
    #[test]
    fn what_the_package_says_must_hold_together_before_anything_is_written() {
        let n = node();
        let k1 = key(n.t.path(), "k1");
        let k2 = key(n.t.path(), "k2");
        let not_pinned = PINNED.sha256.replace('e', "f");
        let cases: Vec<(Pack, &str)> = vec![
            (
                Pack {
                    carried: Some(k2.public_pem()),
                    ..pack_of("fake", "1.0.0", &k1)
                },
                "is not the key its signature names",
            ),
            (
                Pack {
                    claimed: Some(k1.key_id()),
                    carried: Some(k1.public_pem()),
                    ..pack_of("fake", "1.0.0", &k2)
                },
                "does not verify",
            ),
            (
                Pack {
                    python_sha256: &not_pinned,
                    ..pack_of("fake", "1.0.0", &k1)
                },
                "python release",
            ),
            (
                Pack {
                    manifest_name: Some("other"),
                    ..pack_of("fake", "1.0.0", &k1)
                },
                "annotations",
            ),
            (
                // Not signed yet: nothing of it reaches the refusal.
                Pack {
                    manifest_name: Some("Bad\nlayer files: OK"),
                    ..pack_of("fake", "1.0.0", &k1)
                },
                "does not validate",
            ),
        ];
        let asked = Cell::new(0);
        for (p, says) in cases {
            let layout = pack(n.t.path(), p);
            let mut ask = |_: &PinnedKey| {
                asked.set(asked.get() + 1);
                true
            };
            let e = install_with(
                &layout,
                &n.root,
                &n.pins,
                Tofu::Confirm(&mut ask),
                "S-1-5-21-1",
                &plain_dir,
                &files_only,
            )
            .unwrap_err()
            .to_string();
            assert!(e.contains(says), "{says}: {e}");
            assert!(e.contains("; remedy: "), "{e}");
            assert_eq!(e.lines().count(), 1, "{e}");
            assert!(!e.contains("files: OK"), "{e}");
        }
        let layout = pack(n.t.path(), pack_of("fake", "1.0.0", &k1));
        let e = install_with(
            &layout,
            &n.root,
            &n.pins,
            Tofu::Refuse,
            "S-1-5-21-1",
            &plain_dir,
            &files_only,
        )
        .unwrap_err()
        .to_string();
        assert!(e.contains("is not pinned on this node"), "{e}");
        assert_eq!(asked.get(), 0, "nobody was asked");
        nothing_under(&n.root);
        assert!(n.pins.list().unwrap().is_empty());
    }

    /// A tree maker that fails leaves nothing installed and nothing
    /// pinned; a tree whose paths would pass the 240 characters Windows
    /// tools can be trusted with is refused, before anything is written
    /// when the interpreter alone would pass them, and the remedy never
    /// sends the administrator to a root of their own choosing.
    #[test]
    fn an_install_that_fails_late_leaves_nothing_and_a_deep_root_is_refused_early() {
        let n = node();
        let k = key(n.t.path(), "k");
        let layout = pack(n.t.path(), pack_of("fake", "1.0.0", &k));
        let refused = |_: &Path| -> Result<()> { crate::error::refuse("no ACL", "test") };
        let e = install_with(
            &layout,
            &n.root,
            &n.pins,
            Tofu::Confirm(&mut |_| true),
            "S-1-5-21-1",
            &refused,
            &files_only,
        )
        .unwrap_err()
        .to_string();
        assert!(e.contains("no ACL"), "{e}");
        nothing_under(&n.root);
        assert!(!n.root.join("fake").join("1.0.0").exists());
        assert!(n.pins.list().unwrap().is_empty(), "nothing pinned");

        let deep = n.t.path().join("d".repeat(150)).join("installed");
        let e = install_with(
            &layout,
            &deep,
            &n.pins,
            Tofu::Confirm(&mut |_| true),
            "S-1-5-21-1",
            &plain_dir,
            &files_only,
        )
        .unwrap_err()
        .to_string();
        assert!(e.contains("240"), "{e}");
        assert!(e.contains("; remedy: "), "{e}");
        assert!(!e.contains("VK_DRIVER_INSTALL_ROOT"), "{e}");
        assert!(!deep.exists(), "refused before anything was written");

        let deeper = |root: &Path,
                      files: &[u8],
                      python: &Path,
                      sha: &str,
                      wheels: &[u8],
                      lock: &[Locked]|
         -> Result<Materialised> {
            let built = files_only(root, files, python, sha, wheels, lock)?;
            let dir = root.join("w".repeat(120)).join("x".repeat(120));
            std::fs::create_dir_all(&dir)?;
            std::fs::write(dir.join("m.py"), b"")?;
            Ok(Materialised {
                fingerprint: fingerprint::hash_tree(root)?,
                ..built
            })
        };
        let e = install_with(
            &layout,
            &n.root,
            &n.pins,
            Tofu::Confirm(&mut |_| true),
            "S-1-5-21-1",
            &plain_dir,
            &deeper,
        )
        .unwrap_err()
        .to_string();
        assert!(e.contains("240"), "{e}");
        assert!(!e.contains("VK_DRIVER_INSTALL_ROOT"), "{e}");
        nothing_under(&n.root);
    }

    /// Neither the install root nor `<root>/<name>` may be a link or a
    /// junction: an elevated install would write, and an elevated uninstall
    /// remove, wherever it points. Both are refused, and nothing goes
    /// through (review fix 1).
    #[test]
    fn a_link_at_the_root_or_the_name_is_refused_and_nothing_goes_through_it() {
        let n = node();
        let k = key(n.t.path(), "k");
        let layout = pack(n.t.path(), pack_of("fake", "1.0.0", &k));
        let elsewhere = n.t.path().join("elsewhere");
        std::fs::create_dir_all(elsewhere.join("1.0.0")).unwrap();
        std::fs::write(elsewhere.join("1.0.0").join("driver.py"), b"theirs").unwrap();
        let untouched = || {
            let all: Vec<PathBuf> = walkdir::WalkDir::new(&elsewhere)
                .min_depth(1)
                .into_iter()
                .map(|e| e.unwrap().into_path())
                .collect();
            assert_eq!(
                all,
                [
                    elsewhere.join("1.0.0"),
                    elsewhere.join("1.0.0").join("driver.py")
                ]
            );
            assert_eq!(
                std::fs::read(elsewhere.join("1.0.0").join("driver.py")).unwrap(),
                b"theirs"
            );
        };
        std::fs::create_dir_all(&n.root).unwrap();
        link(&elsewhere, &n.root.join("fake"));
        let linked_root = n.t.path().join("linked-root");
        link(&elsewhere, &linked_root);
        for root in [&n.root, &linked_root] {
            let e = install_with(
                &layout,
                root,
                &n.pins,
                Tofu::Confirm(&mut |_| true),
                "S-1-5-21-1",
                &plain_dir,
                &files_only,
            )
            .unwrap_err()
            .to_string();
            assert!(e.contains("is a link or a junction"), "{e}");
            assert!(e.contains("; remedy: "), "{e}");
            untouched();
        }
        for root in [&n.root, &linked_root] {
            let e = uninstall(root, "fake", "1.0.0", false)
                .unwrap_err()
                .to_string();
            assert!(e.contains("is a link or a junction"), "{e}");
            untouched();
        }
        let e = uninstall(&linked_root, "1.0.0", "1.0.0", false);
        assert!(e.is_err(), "a version under a linked root is not removed");
        untouched();
        assert!(n.pins.list().unwrap().is_empty(), "nothing pinned");
    }

    /// The tree maker makes `<name>` (when it is new) and `.part` before
    /// anything is materialised in them: the materialiser finds its root
    /// made, and empty. A `.part` another install made between the check
    /// and the making is refused, and left to its maker (review fix 1).
    #[test]
    fn the_tree_maker_makes_the_part_first_and_never_adopts_one() {
        let n = node();
        let k = key(n.t.path(), "k");
        let layout = pack(n.t.path(), pack_of("fake", "1.0.0", &k));
        let made = std::cell::RefCell::new(Vec::<PathBuf>::new());
        let maker = |dir: &Path| -> Result<()> {
            std::fs::create_dir(dir)?;
            made.borrow_mut().push(dir.to_path_buf());
            Ok(())
        };
        let part = n.root.join("fake").join("1.0.0.part");
        let checked = |root: &Path,
                       files: &[u8],
                       python: &Path,
                       sha: &str,
                       wheels: &[u8],
                       lock: &[Locked]|
         -> Result<Materialised> {
            assert_eq!(*made.borrow(), [n.root.join("fake"), part.clone()]);
            assert_eq!(root, part);
            assert_eq!(std::fs::read_dir(root)?.count(), 0, "made, and empty");
            files_only(root, files, python, sha, wheels, lock)
        };
        install_with(
            &layout,
            &n.root,
            &n.pins,
            Tofu::Confirm(&mut |_| true),
            "S-1-5-21-1",
            &maker,
            &checked,
        )
        .unwrap();

        let second = pack(n.t.path(), pack_of("fake", "1.1.0", &k));
        let racing = |dir: &Path| -> Result<()> {
            // The other install makes it first; this one finds it there.
            std::fs::create_dir(dir)?;
            std::fs::write(dir.join("theirs"), b"x")?;
            std::fs::create_dir(dir)?;
            Ok(())
        };
        let e = install_with(
            &second,
            &n.root,
            &n.pins,
            Tofu::Confirm(&mut |_| true),
            "S-1-5-21-1",
            &racing,
            &files_only,
        )
        .unwrap_err()
        .to_string();
        assert!(e.contains("under way"), "{e}");
        assert!(e.contains("; remedy: "), "{e}");
        assert!(
            n.root
                .join("fake")
                .join("1.1.0.part")
                .join("theirs")
                .is_file(),
            "left to its maker"
        );
    }

    /// Elevated, `.part` is born with the tree's protection — the read-only
    /// DACL on Windows, mode 0755 elsewhere — so the interpreter and pip,
    /// run as an administrator, never run from a directory another account
    /// can write into (review fix 1).
    #[test]
    fn the_part_is_protected_before_the_interpreter_runs() {
        let n = node();
        let k = key(n.t.path(), "k");
        let layout = pack(n.t.path(), pack_of("fake", "1.0.0", &k));
        // `<name>` is the test's own: this account, no administrator, may
        // then remove the `.part` it is not allowed to write into.
        std::fs::create_dir_all(n.root.join("fake")).unwrap();
        let checked = |root: &Path,
                       _: &[u8],
                       _: &Path,
                       _: &str,
                       _: &[u8],
                       _: &[Locked]|
         -> Result<Materialised> {
            assert_protected(root);
            refuse("checked", "none")
        };
        let e = install_with(
            &layout,
            &n.root,
            &n.pins,
            Tofu::Confirm(&mut |_| true),
            "S-1-5-21-1",
            &protected_dir,
            &checked,
        )
        .unwrap_err()
        .to_string();
        assert!(e.starts_with("checked"), "{e}");
        assert!(
            !n.root.join("fake").join("1.0.0.part").exists(),
            "removed with the refusal"
        );
    }

    /// The publisher is pinned only once the tree is in place: a rename
    /// that fails — another install of the version got there first — leaves
    /// no pin and the other tree as it was; a pin that cannot be written
    /// takes the installed tree back out.
    #[test]
    fn the_publisher_is_pinned_only_once_the_tree_is_in_place() {
        let n = node();
        let k = key(n.t.path(), "k");
        let layout = pack(n.t.path(), pack_of("fake", "1.0.0", &k));
        let target = n.root.join("fake").join("1.0.0");
        let beaten = |root: &Path,
                      files: &[u8],
                      python: &Path,
                      sha: &str,
                      wheels: &[u8],
                      lock: &[Locked]|
         -> Result<Materialised> {
            let built = files_only(root, files, python, sha, wheels, lock)?;
            std::fs::create_dir_all(&target)?;
            std::fs::write(target.join("theirs"), b"x")?;
            Ok(built)
        };
        let e = install_with(
            &layout,
            &n.root,
            &n.pins,
            Tofu::Confirm(&mut |_| true),
            "S-1-5-21-1",
            &plain_dir,
            &beaten,
        );
        assert!(e.is_err());
        assert!(n.pins.list().unwrap().is_empty(), "nothing pinned");
        assert!(!n.root.join("fake").join("1.0.0.part").exists());
        assert_eq!(std::fs::read(target.join("theirs")).unwrap(), b"x");

        let fresh = node();
        let blocked = fresh.t.path().join("blocked");
        std::fs::create_dir_all(&blocked).unwrap();
        std::fs::write(blocked.join("capital"), b"a file where a directory goes").unwrap();
        let e = install_with(
            &layout,
            &fresh.root,
            &Pins::at(&blocked),
            Tofu::Confirm(&mut |_| true),
            "S-1-5-21-1",
            &plain_dir,
            &files_only,
        );
        assert!(e.is_err());
        nothing_under(&fresh.root);
    }

    /// A tree with a file open in it — a driver still running, a shell, an
    /// editor — is refused whole rather than half removed: it is moved aside
    /// before it is deleted, and Windows refuses to move a directory while a
    /// file in it is open (review fix 4).
    #[cfg(windows)]
    #[test]
    fn uninstall_refuses_a_tree_with_an_open_file_and_leaves_it_whole() {
        let n = node();
        let k = key(n.t.path(), "k");
        let layout = pack(n.t.path(), pack_of("fake", "1.0.0", &k));
        let r = install_with(
            &layout,
            &n.root,
            &n.pins,
            Tofu::Confirm(&mut |_| true),
            "S-1-5-21-1",
            &plain_dir,
            &files_only,
        )
        .unwrap();
        let held = std::fs::File::open(r.target.join("driver.py")).unwrap();
        let e = uninstall(&n.root, "fake", "1.0.0", false)
            .unwrap_err()
            .to_string();
        assert!(e.contains("cannot be moved aside"), "{e}");
        assert!(e.contains("; remedy: "), "{e}");
        assert_eq!(
            std::fs::read(r.target.join("driver.py")).unwrap(),
            b"print('hello')\n"
        );
        let beside: Vec<_> = std::fs::read_dir(n.root.join("fake"))
            .unwrap()
            .map(|e| e.unwrap().file_name())
            .collect();
        assert_eq!(beside, ["1.0.0"], "nothing moved, nothing left aside");
        drop(held);
        uninstall(&n.root, "fake", "1.0.0", false).unwrap();
        assert!(!n.root.join("fake").exists());
    }
}
