//! The driver package on disk: a standard OCI image layout (`oci-layout`,
//! `index.json`, `blobs/sha256/<hex>`) holding one image manifest with one
//! layer per component, and one referrer that carries the publisher's
//! signature. Reading it back checks every blob's size and digest against the
//! descriptor that names it and refuses on the first that differs, naming it.
//!
//! OS analogy: the layout is a firmware image with its partition table;
//! `open` is the boot ROM walking the table and checking each partition's CRC
//! before a byte of it is used. The vendor signature is not checked here:
//! that is `signature::verify`, against a pinned key.

use std::collections::BTreeMap;
use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};

use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use vk_contracts::package::{
    Descriptor, DriverPackage, Signature, ANN_COMPONENT, ANN_CREATED, ANN_DRIVER_ID, ANN_NAME,
    ANN_PUBLISHER, ANN_PYTHON_RELEASE, ANN_PYTHON_SHA256, ANN_VERSION, COMPONENTS,
    DRIVER_ARTIFACT_TYPE, MEDIA_FILES, MEDIA_LOCK, MEDIA_MANIFEST, MEDIA_PYTHON, MEDIA_WHEELS,
    OCI_EMPTY_TYPE, OCI_MANIFEST_TYPE, SIGNATURE_ARTIFACT_TYPE,
};

use crate::error::{refuse, Result};

/// The media type of the one blob a signature referrer carries.
const MEDIA_SIGNATURE: &str = "application/vnd.aubier.signature.v1+json";
/// The signature referrer's annotation that carries the publisher's public
/// key, as the PEM `PublisherKey::public_pem` prints — the way cosign hangs
/// the certificate on its signature manifest. An installer that has not
/// pinned the key yet shows it to the administrator (TOFU, packaging spec
/// D8); it is trusted for nothing until then, and only if its id is the
/// signature's and the signature verifies under it.
pub const ANN_PUBLISHER_KEY: &str = "org.aubier.publisher.public_key";
/// The empty config blob both manifests point at (`OCI_EMPTY_TYPE`).
const EMPTY: &[u8] = b"{}";
const ALTERED: &str = "do not install this copy; fetch the package again from its publisher";
const NOT_A_PACKAGE: &str = "point at a directory written by vk driver pack";

/// The bytes of the five components, one layer each.
#[derive(Debug)]
pub struct Layers {
    pub manifest: Vec<u8>,
    pub files: Vec<u8>,
    pub lock: Vec<u8>,
    pub wheels: Vec<u8>,
    pub python: Vec<u8>,
}

/// The image manifest's annotations, and the python layer's two.
#[derive(Debug)]
pub struct Annotations {
    pub name: String,
    pub version: String,
    pub driver_id: String,
    /// RFC 3339.
    pub created: String,
    pub publisher_key_id: String,
    pub python_release: String,
    pub python_upstream_sha256: String,
}

/// A layout read back by [`open`], every digest checked.
#[derive(Debug)]
pub struct Opened {
    pub package: DriverPackage,
    /// The image manifest's digest: what the signature signs.
    pub package_digest: String,
    /// The image manifest blob as read, whose SHA-256 is `package_digest`:
    /// what an install keeps, verbatim, in `.package/manifest.json`.
    pub package_bytes: Vec<u8>,
    /// Read and matched to `package_digest`; its Ed25519 signature is not
    /// verified here.
    pub signature: Signature,
    /// The publisher's public key the signature referrer carries
    /// ([`ANN_PUBLISHER_KEY`]), unparsed and untrusted; `None` for a layout
    /// written before it did.
    pub publisher_pem: Option<String>,
    pub layout: PathBuf,
}

/// `sha256:` and the 64 lowercase hex digits of the SHA-256 of `bytes`.
pub fn digest_of(bytes: &[u8]) -> String {
    format!("sha256:{:x}", Sha256::digest(bytes))
}

/// `layout/blobs/sha256/<hex>`: the file name is the digest without `sha256:`.
pub fn blob_path(layout: &Path, digest: &str) -> PathBuf {
    let hex = digest.strip_prefix("sha256:").unwrap_or(digest);
    layout.join("blobs").join("sha256").join(hex)
}

/// Writes `oci-layout`, the five blobs, the image manifest blob and `index.json`
/// (one entry). Returns the image manifest and its digest. Rejects an existing
/// non-empty `out`. A write that fails midway removes what it wrote, so
/// `out` is left as it was found: absent, or an empty directory.
pub fn write(out: &Path, layers: &Layers, ann: &Annotations) -> Result<(DriverPackage, String)> {
    if out.exists() && (!out.is_dir() || fs::read_dir(out)?.next().is_some()) {
        return refuse(
            format!("{} exists and is not an empty directory", out.display()),
            "choose a new --out directory, or empty that one",
        );
    }
    // Armed before the first write: whatever happens next, a layout is
    // written whole or not at all.
    let mut undo = Undo::new();
    let written = write_layout(out, layers, ann, &mut undo)?;
    undo.disarm();
    Ok(written)
}

/// [`write`]'s writes, each one noted in `undo` before it is made.
fn write_layout(
    out: &Path,
    layers: &Layers,
    ann: &Annotations,
    undo: &mut Undo,
) -> Result<(DriverPackage, String)> {
    let blobs = out.join("blobs").join("sha256");
    let mut missing: Vec<&Path> = blobs.ancestors().take_while(|d| !d.exists()).collect();
    missing.reverse();
    for dir in missing {
        undo.dirs.push(dir.to_path_buf());
        fs::create_dir(dir)?;
    }
    put_file(
        undo,
        &out.join("oci-layout"),
        br#"{"imageLayoutVersion":"1.0.0"}"#,
    )?;
    let mut put_blob = |media_type: &str, bytes: &[u8]| -> Result<Descriptor> {
        let digest = digest_of(bytes);
        put_file(undo, &blob_path(out, &digest), bytes)?;
        Ok(Descriptor {
            media_type: media_type.into(),
            digest,
            size: bytes.len() as u64,
            annotations: BTreeMap::new(),
        })
    };
    let config = put_blob(OCI_EMPTY_TYPE, EMPTY)?;
    let blobs: [(&str, &[u8]); 5] = [
        (MEDIA_MANIFEST, &layers.manifest),
        (MEDIA_FILES, &layers.files),
        (MEDIA_LOCK, &layers.lock),
        (MEDIA_WHEELS, &layers.wheels),
        (MEDIA_PYTHON, &layers.python),
    ];
    let mut descriptors = Vec::with_capacity(blobs.len());
    for (component, (media_type, bytes)) in COMPONENTS.into_iter().zip(blobs) {
        let mut d = put_blob(media_type, bytes)?;
        d.annotations.insert(ANN_COMPONENT.into(), component.into());
        if component == "python" {
            d.annotations
                .insert(ANN_PYTHON_RELEASE.into(), ann.python_release.clone());
            d.annotations
                .insert(ANN_PYTHON_SHA256.into(), ann.python_upstream_sha256.clone());
        }
        descriptors.push(d);
    }
    let package = DriverPackage {
        schema_version: 2,
        media_type: OCI_MANIFEST_TYPE.into(),
        artifact_type: DRIVER_ARTIFACT_TYPE.into(),
        config,
        layers: descriptors,
        annotations: BTreeMap::from([
            (ANN_NAME.into(), ann.name.clone()),
            (ANN_VERSION.into(), ann.version.clone()),
            (ANN_DRIVER_ID.into(), ann.driver_id.clone()),
            (ANN_CREATED.into(), ann.created.clone()),
            (ANN_PUBLISHER.into(), ann.publisher_key_id.clone()),
        ]),
    };
    let manifest = put_blob(OCI_MANIFEST_TYPE, &serde_json::to_vec_pretty(&package)?)?;
    let index = json!({
        "schemaVersion": 2,
        "manifests": [index_entry(&manifest, DRIVER_ARTIFACT_TYPE)],
    });
    put_file(
        undo,
        &out.join("index.json"),
        &serde_json::to_vec_pretty(&index)?,
    )?;
    Ok((package, manifest.digest))
}

/// What a [`write`] under way has made, removed on drop unless it was
/// disarmed: the files (two blobs with one digest are one file), then the
/// directories it created — `out` and its missing ancestors among them —
/// deepest first. Nothing it did not make is touched, so an `out` that was
/// an empty directory stays one.
struct Undo {
    files: Vec<PathBuf>,
    dirs: Vec<PathBuf>,
    armed: bool,
}

impl Undo {
    fn new() -> Undo {
        Undo {
            files: vec![],
            dirs: vec![],
            armed: true,
        }
    }

    fn disarm(&mut self) {
        self.armed = false;
    }
}

impl Drop for Undo {
    fn drop(&mut self) {
        if !self.armed {
            return;
        }
        for file in &self.files {
            let _ = fs::remove_file(file);
        }
        for dir in self.dirs.iter().rev() {
            let _ = fs::remove_dir(dir);
        }
    }
}

/// Writes one file of a layout, noted in `undo` first so that a write that
/// fails halfway is removed too.
fn put_file(undo: &mut Undo, path: &Path, bytes: &[u8]) -> Result<()> {
    if !undo.files.iter().any(|f| f == path) {
        undo.files.push(path.to_path_buf());
    }
    #[cfg(test)]
    tests::FAULT.with(|f| match f.get() {
        Some(0) => Err(std::io::Error::other("a write the test made fail")),
        Some(n) => {
            f.set(Some(n - 1));
            Ok(())
        }
        None => Ok(()),
    })?;
    fs::write(path, bytes)?;
    Ok(())
}

/// Appends the signature referrer: the signature blob, a second image manifest
/// with `subject` = the driver manifest descriptor and the publisher's public
/// key as its [`ANN_PUBLISHER_KEY`] annotation, and its `index.json` entry.
pub fn write_signature(
    out: &Path,
    package_digest: &str,
    sig: &Signature,
    public_pem: &str,
) -> Result<()> {
    let subject = blob_path(out, package_digest);
    if !well_formed(package_digest) || !subject.is_file() {
        return refuse(
            format!(
                "signature: {package_digest} is not an image manifest in {}",
                out.display()
            ),
            "sign the digest layout::write returned, into the layout it wrote",
        );
    }
    const UNWRITTEN: &str = "write the layout with layout::write before signing it";
    let path = out.join("index.json");
    let mut index: Value = serde_json::from_slice(&fs::read(&path)?)
        .or_else(|e| refuse(unparsed("index.json", &e), UNWRITTEN))?;
    let Some(manifests) = index.get_mut("manifests").and_then(Value::as_array_mut) else {
        return refuse("index.json has no manifests list", UNWRITTEN);
    };
    if manifests
        .iter()
        .any(|e| e["artifactType"] == SIGNATURE_ARTIFACT_TYPE)
    {
        return refuse(
            "layout already carries a signature",
            "pack again into an empty --out",
        );
    }
    let subject_size = fs::metadata(&subject)?.len();
    let config = put_blob(out, OCI_EMPTY_TYPE, EMPTY)?;
    let blob = put_blob(out, MEDIA_SIGNATURE, &serde_json::to_vec_pretty(sig)?)?;
    let referrer = json!({
        "schemaVersion": 2,
        "mediaType": OCI_MANIFEST_TYPE,
        "artifactType": SIGNATURE_ARTIFACT_TYPE,
        "config": config,
        "layers": [blob],
        "subject": {
            "mediaType": OCI_MANIFEST_TYPE,
            "digest": package_digest,
            "size": subject_size,
        },
        "annotations": { ANN_PUBLISHER_KEY: public_pem },
    });
    let referrer = put_blob(
        out,
        OCI_MANIFEST_TYPE,
        &serde_json::to_vec_pretty(&referrer)?,
    )?;
    manifests.push(index_entry(&referrer, SIGNATURE_ARTIFACT_TYPE));
    fs::write(&path, serde_json::to_vec_pretty(&index)?)?;
    Ok(())
}

/// Reads `index.json`, finds the one driver manifest and the one signature
/// referrer, checks every blob's size and digest against its descriptor, and
/// the signature's `signed` against the manifest digest. Refuses on the first
/// mismatch, naming the component.
pub fn open(layout: &Path) -> Result<Opened> {
    let index = match fs::read(layout.join("index.json")) {
        Ok(bytes) => bytes,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            return refuse(
                format!("{} has no index.json", layout.display()),
                NOT_A_PACKAGE,
            )
        }
        Err(e) => return Err(e.into()),
    };
    let index: Value = serde_json::from_slice(&index)
        .or_else(|e| refuse(unparsed("index.json", &e), NOT_A_PACKAGE))?;
    let entries = index["manifests"]
        .as_array()
        .map(Vec::as_slice)
        .unwrap_or_default();
    let of_type = |t: &str| {
        entries
            .iter()
            .filter(|e| e["artifactType"] == t)
            .collect::<Vec<_>>()
    };
    let (driver, signature) = match (
        of_type(DRIVER_ARTIFACT_TYPE).as_slice(),
        of_type(SIGNATURE_ARTIFACT_TYPE).as_slice(),
    ) {
        ([d], [s]) => (*d, *s),
        _ => {
            return refuse(
                "index.json must name one driver manifest and one signature",
                NOT_A_PACKAGE,
            )
        }
    };

    let (package_digest, size) = descriptor_of(driver, "image manifest")?;
    let bytes = read_blob(layout, &package_digest, size, "image manifest")?;
    let package: DriverPackage = serde_json::from_slice(&bytes)
        .or_else(|e| refuse(unparsed("image manifest", &e), ALTERED))?;
    if let Err(e) = package.validate() {
        return refuse(format!("image manifest: {e}"), ALTERED);
    }
    read_blob(
        layout,
        &package.config.digest,
        package.config.size,
        "config",
    )?;
    // validate() put the layers in the order of COMPONENTS.
    for (layer, component) in package.layers.iter().zip(COMPONENTS) {
        read_blob(
            layout,
            &layer.digest,
            layer.size,
            &format!("layer {component}"),
        )?;
    }

    let (digest, size) = descriptor_of(signature, "signature manifest")?;
    let referrer: Value =
        serde_json::from_slice(&read_blob(layout, &digest, size, "signature manifest")?)
            .or_else(|e| refuse(unparsed("signature manifest", &e), ALTERED))?;
    if referrer["subject"]["digest"] != package_digest.as_str() {
        return refuse(
            format!("signature manifest: its subject is not the image manifest {package_digest}"),
            ALTERED,
        );
    }
    let publisher_pem = referrer["annotations"][ANN_PUBLISHER_KEY]
        .as_str()
        .map(str::to_string);
    let blob = match referrer["layers"].as_array().map(Vec::as_slice) {
        Some([blob]) => blob,
        _ => return refuse("signature manifest must carry one layer", ALTERED),
    };
    let (digest, size) = descriptor_of(blob, "signature")?;
    let signature: Signature =
        serde_json::from_slice(&read_blob(layout, &digest, size, "signature")?)
            .or_else(|e| refuse(unparsed("signature", &e), ALTERED))?;
    if signature.signed != package_digest {
        return refuse(
            format!("signature names another digest than the package {package_digest}"),
            ALTERED,
        );
    }
    Ok(Opened {
        package,
        package_digest,
        package_bytes: bytes,
        signature,
        publisher_pem,
        layout: layout.to_path_buf(),
    })
}

/// The bytes of one component's layer, its size and digest checked again on
/// the bytes returned: the file may have changed since [`open`].
pub fn read_layer(opened: &Opened, component: &str) -> Result<Vec<u8>> {
    let Some(layer) = opened.package.layer(component) else {
        return refuse(
            format!("no layer {component} in a driver package"),
            &format!("name one of {COMPONENTS:?}"),
        );
    };
    read_blob(
        &opened.layout,
        &layer.digest,
        layer.size,
        &format!("layer {component}"),
    )
}

/// Writes `bytes` as a blob of a layout already written; its descriptor,
/// without annotations.
fn put_blob(layout: &Path, media_type: &str, bytes: &[u8]) -> Result<Descriptor> {
    let digest = digest_of(bytes);
    fs::write(blob_path(layout, &digest), bytes)?;
    Ok(Descriptor {
        media_type: media_type.into(),
        digest,
        size: bytes.len() as u64,
        annotations: BTreeMap::new(),
    })
}

/// An `index.json` entry for an image manifest of `artifact_type`.
fn index_entry(manifest: &Descriptor, artifact_type: &str) -> Value {
    json!({
        "mediaType": OCI_MANIFEST_TYPE,
        "artifactType": artifact_type,
        "digest": manifest.digest,
        "size": manifest.size,
    })
}

/// `sha256:` and 64 lowercase hex digits: the only digest whose blob path
/// stays under `blobs/sha256/`.
fn well_formed(digest: &str) -> bool {
    digest.strip_prefix("sha256:").is_some_and(|hex| {
        hex.len() == 64 && hex.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
    })
}

/// A refusal for JSON that does not parse: where, never what. serde's message
/// can echo a key from the file verbatim, newlines included, and a refusal
/// carries no third party's text.
fn unparsed(what: &str, e: &serde_json::Error) -> String {
    format!(
        "{what} does not parse (line {}, column {})",
        e.line(),
        e.column()
    )
}

/// The `digest` and `size` of a descriptor held as raw JSON.
fn descriptor_of(v: &Value, what: &str) -> Result<(String, u64)> {
    match (v["digest"].as_str(), v["size"].as_u64()) {
        (Some(digest), Some(size)) => Ok((digest.to_string(), size)),
        _ => refuse(
            format!("{what}: its descriptor has no digest or no size"),
            ALTERED,
        ),
    }
}

/// The blob `digest` names, refused unless the digest is well formed (no name
/// read from a file reaches the file system unchecked), the blob is a regular
/// file and not a link (a link could name a FIFO, whose read never returns, or
/// a device, whose read never ends), it holds `size` bytes (read no further
/// than one past) and they hash to `digest`.
fn read_blob(layout: &Path, digest: &str, size: u64, what: &str) -> Result<Vec<u8>> {
    if !well_formed(digest) {
        return refuse(
            format!("{what}: its digest is not sha256:<64 hex>"),
            ALTERED,
        );
    }
    let path = blob_path(layout, digest);
    let not_a_file = || refuse(format!("{what}: blob is not a regular file"), ALTERED);
    match fs::symlink_metadata(&path) {
        Ok(m) if m.is_file() => {}
        Ok(_) => return not_a_file(),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            return refuse(format!("{what}: blob {digest} is missing"), ALTERED)
        }
        Err(e) => return Err(e.into()),
    }
    let file = fs::File::open(&path)?;
    if !file.metadata()?.is_file() {
        return not_a_file(); // replaced since the check above
    }
    let mut bytes = Vec::new();
    file.take(size.saturating_add(1)).read_to_end(&mut bytes)?;
    if bytes.len() as u64 != size {
        return refuse(
            format!(
                "{what}: size mismatch (its descriptor says {size} bytes), the package was altered"
            ),
            ALTERED,
        );
    }
    if digest_of(&bytes) != digest {
        return refuse(
            format!("{what}: digest mismatch, the package was altered"),
            ALTERED,
        );
    }
    Ok(bytes)
}

#[cfg(test)]
mod tests {
    use super::*;

    thread_local! {
        /// How many of `write`'s file writes succeed before the next one
        /// fails, on this test's thread; `None`: none fails.
        pub(super) static FAULT: std::cell::Cell<Option<usize>> =
            const { std::cell::Cell::new(None) };
    }

    /// A layout whose write fails midway — at each of its nine file writes
    /// in turn — leaves `out` as it was found: absent, or an empty
    /// directory (Task 8 review).
    #[test]
    fn a_write_that_fails_midway_leaves_out_as_it_found_it() {
        for written in 0..9 {
            let t = tempfile::tempdir().unwrap();
            let out = t.path().join("pkg");
            FAULT.with(|f| f.set(Some(written)));
            let absent = write(&out, &layers(), &ann());
            FAULT.with(|f| f.set(None));
            assert!(absent.is_err(), "write {written} failed");
            assert!(!out.exists(), "after {written} writes: {out:?} is gone");

            std::fs::create_dir(&out).unwrap();
            FAULT.with(|f| f.set(Some(written)));
            let empty = write(&out, &layers(), &ann());
            FAULT.with(|f| f.set(None));
            assert!(empty.is_err(), "write {written} failed");
            assert!(
                std::fs::read_dir(&out).unwrap().next().is_none(),
                "after {written} writes: {out:?} is empty again"
            );
        }
        let t = tempfile::tempdir().unwrap();
        FAULT.with(|f| f.set(Some(9)));
        let whole = write(&t.path().join("pkg"), &layers(), &ann());
        FAULT.with(|f| f.set(None));
        whole.expect("nine writes make a layout");
    }

    fn layers() -> Layers {
        Layers {
            manifest: b"{\"name\":\"x\"}".to_vec(),
            files: b"FILES".to_vec(),
            lock: b"[]".to_vec(),
            wheels: b"WHEELS".to_vec(),
            python: b"PY".to_vec(),
        }
    }
    fn ann() -> Annotations {
        Annotations {
            name: "x".into(),
            version: "1.0.0".into(),
            driver_id: "sha256:".to_string() + &"0".repeat(64),
            created: "2026-10-17T00:00:00Z".into(),
            publisher_key_id: "sha256:".to_string() + &"1".repeat(64),
            python_release: "cpython-3.13.x".into(),
            python_upstream_sha256: "2".repeat(64),
        }
    }
    /// What the referrer carries in these tests: `open` reads it, never
    /// parses it.
    const PEM: &str =
        "-----BEGIN AUBIER PUBLISHER KEY-----\nAAAA\n-----END AUBIER PUBLISHER KEY-----\n";

    #[test]
    fn the_signature_referrer_carries_the_publisher_key_and_open_returns_it() {
        let t = tempfile::tempdir().unwrap();
        let out = t.path().join("pkg");
        let (_, digest) = write(&out, &layers(), &ann()).unwrap();
        write_signature(&out, &digest, &sig(&digest), PEM).unwrap();
        let o = open(&out).unwrap();
        assert_eq!(o.publisher_pem.as_deref(), Some(PEM));
        assert_eq!(digest_of(&o.package_bytes), o.package_digest, "verbatim");
    }

    fn sig(d: &str) -> Signature {
        Signature {
            key_id: "sha256:".to_string() + &"1".repeat(64),
            algorithm: "ed25519".into(),
            signed: d.into(),
            signature: "AAAA".into(),
        }
    }

    #[test]
    fn write_then_open_round_trips_and_reads_every_layer() {
        let t = tempfile::tempdir().unwrap();
        let out = t.path().join("pkg");
        let (pkg, digest) = write(&out, &layers(), &ann()).unwrap();
        pkg.validate().unwrap();
        write_signature(&out, &digest, &sig(&digest), PEM).unwrap();
        assert!(out.join("oci-layout").exists());
        let o = open(&out).unwrap();
        assert_eq!(o.package_digest, digest);
        assert_eq!(read_layer(&o, "wheels").unwrap(), b"WHEELS");
        assert_eq!(o.package.name(), "x");
    }
    #[test]
    fn a_flipped_byte_in_any_blob_is_refused_by_name() {
        for component in ["files", "wheels", "manifest", "python", "lock"] {
            let t = tempfile::tempdir().unwrap();
            let out = t.path().join("pkg");
            let (pkg, digest) = write(&out, &layers(), &ann()).unwrap();
            write_signature(&out, &digest, &sig(&digest), PEM).unwrap();
            let d = &pkg.layer(component).unwrap().digest;
            let p = blob_path(&out, d);
            let mut bytes = std::fs::read(&p).unwrap();
            bytes[0] ^= 0x01;
            std::fs::write(&p, bytes).unwrap();
            let e = open(&out).unwrap_err().to_string();
            assert!(e.contains(component), "{component}: {e}");
        }
    }
    #[test]
    fn a_rewritten_image_manifest_is_refused_because_the_index_names_its_digest() {
        let t = tempfile::tempdir().unwrap();
        let out = t.path().join("pkg");
        let (_, digest) = write(&out, &layers(), &ann()).unwrap();
        write_signature(&out, &digest, &sig(&digest), PEM).unwrap();
        let p = blob_path(&out, &digest);
        let mut text = std::fs::read_to_string(&p).unwrap();
        text = text.replace("\"1.0.0\"", "\"9.9.9\"");
        std::fs::write(&p, text).unwrap();
        assert!(open(&out)
            .unwrap_err()
            .to_string()
            .contains("image manifest"));
    }
    #[test]
    fn a_signature_that_names_another_digest_is_refused() {
        let t = tempfile::tempdir().unwrap();
        let out = t.path().join("pkg");
        let (_, digest) = write(&out, &layers(), &ann()).unwrap();
        write_signature(
            &out,
            &digest,
            &sig(&("sha256:".to_string() + &"f".repeat(64))),
            PEM,
        )
        .unwrap();
        assert!(open(&out).unwrap_err().to_string().contains("signature"));
    }
    #[test]
    fn writing_into_a_non_empty_directory_is_refused() {
        let t = tempfile::tempdir().unwrap();
        std::fs::write(t.path().join("stray"), b"x").unwrap();
        assert!(write(t.path(), &layers(), &ann()).is_err());
    }
    #[test]
    fn read_layer_checks_the_bytes_it_returns() {
        let t = tempfile::tempdir().unwrap();
        let out = t.path().join("pkg");
        let (pkg, digest) = write(&out, &layers(), &ann()).unwrap();
        write_signature(&out, &digest, &sig(&digest), PEM).unwrap();
        let o = open(&out).unwrap();
        // altered between open and read: the read refuses, it does not trust open
        std::fs::write(blob_path(&out, &pkg.layer("python").unwrap().digest), b"PX").unwrap();
        let e = read_layer(&o, "python").unwrap_err().to_string();
        assert!(e.contains("python"), "{e}");
        assert!(read_layer(&o, "nope").is_err());
    }
    #[test]
    fn an_index_digest_that_is_not_sha256_hex_is_refused_before_any_read() {
        let t = tempfile::tempdir().unwrap();
        let out = t.path().join("pkg");
        let (_, digest) = write(&out, &layers(), &ann()).unwrap();
        write_signature(&out, &digest, &sig(&digest), PEM).unwrap();
        let index = out.join("index.json");
        let text = std::fs::read_to_string(&index).unwrap();
        std::fs::write(&index, text.replace(&digest, "sha256:../../../oci-layout")).unwrap();
        let e = open(&out).unwrap_err().to_string();
        assert!(e.contains("image manifest") && e.contains("64 hex"), "{e}");
    }
    fn signed() -> (tempfile::TempDir, PathBuf, String) {
        let t = tempfile::tempdir().unwrap();
        let out = t.path().join("pkg");
        let (_, digest) = write(&out, &layers(), &ann()).unwrap();
        write_signature(&out, &digest, &sig(&digest), PEM).unwrap();
        (t, out, digest)
    }
    /// A key a hostile publisher would plant: a newline, then a fake verdict.
    const HOSTILE: &str = "x\nlayer files: OK; remedy: none";
    fn with_hostile_key(json: &[u8]) -> Vec<u8> {
        let mut v: Value = serde_json::from_slice(json).unwrap();
        v[HOSTILE] = 0.into();
        serde_json::to_vec(&v).unwrap()
    }
    /// Writes `bytes` as a blob and points the `index.json` entry naming `old` at it.
    fn repoint_index(out: &Path, old: &str, bytes: &[u8]) {
        std::fs::write(blob_path(out, &digest_of(bytes)), bytes).unwrap();
        let path = out.join("index.json");
        let mut index: Value = serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
        for e in index["manifests"].as_array_mut().unwrap() {
            if e["digest"] == old {
                e["digest"] = digest_of(bytes).into();
                e["size"] = bytes.len().into();
            }
        }
        std::fs::write(&path, serde_json::to_vec(&index).unwrap()).unwrap();
    }
    fn assert_one_line_of_our_own(e: &str) {
        assert_eq!(e.lines().count(), 1, "{e}");
        assert!(
            !e.contains("files: OK") && !e.contains("remedy: none"),
            "{e}"
        );
    }
    #[test]
    fn an_unknown_key_in_the_image_manifest_does_not_reach_the_refusal() {
        let (_t, out, digest) = signed();
        let bytes = with_hostile_key(&std::fs::read(blob_path(&out, &digest)).unwrap());
        repoint_index(&out, &digest, &bytes);
        let e = open(&out).unwrap_err().to_string();
        assert!(e.contains("image manifest"), "{e}");
        assert_one_line_of_our_own(&e);
    }
    #[test]
    fn an_unknown_key_in_the_signature_does_not_reach_the_refusal() {
        let (_t, out, _) = signed();
        let index: Value =
            serde_json::from_slice(&std::fs::read(out.join("index.json")).unwrap()).unwrap();
        let old = index["manifests"][1]["digest"]
            .as_str()
            .unwrap()
            .to_string();
        let mut referrer: Value =
            serde_json::from_slice(&std::fs::read(blob_path(&out, &old)).unwrap()).unwrap();
        let sig_digest = referrer["layers"][0]["digest"]
            .as_str()
            .unwrap()
            .to_string();
        let bytes = with_hostile_key(&std::fs::read(blob_path(&out, &sig_digest)).unwrap());
        std::fs::write(blob_path(&out, &digest_of(&bytes)), &bytes).unwrap();
        referrer["layers"][0]["digest"] = digest_of(&bytes).into();
        referrer["layers"][0]["size"] = bytes.len().into();
        repoint_index(&out, &old, &serde_json::to_vec(&referrer).unwrap());
        let e = open(&out).unwrap_err().to_string();
        assert!(e.contains("signature"), "{e}");
        assert_one_line_of_our_own(&e);
    }
    #[test]
    fn a_blob_longer_than_its_descriptor_or_not_a_file_is_refused_by_name() {
        let (_t, out, _) = signed();
        let package = open(&out).unwrap().package;
        let files = blob_path(&out, &package.layer("files").unwrap().digest);
        std::fs::write(&files, b"FILES and more").unwrap();
        let e = open(&out).unwrap_err().to_string();
        assert!(e.contains("layer files: size mismatch"), "{e}");
        std::fs::remove_file(&files).unwrap();
        std::fs::create_dir(&files).unwrap();
        let e = open(&out).unwrap_err().to_string();
        assert!(e.contains("layer files: blob is not a regular file"), "{e}");
    }
    #[test]
    fn signing_a_layout_twice_is_refused_and_leaves_it_whole() {
        let (_t, out, digest) = signed();
        let e = write_signature(&out, &digest, &sig(&digest), PEM)
            .unwrap_err()
            .to_string();
        assert!(e.contains("layout already carries a signature"), "{e}");
        open(&out).unwrap();
    }
}
