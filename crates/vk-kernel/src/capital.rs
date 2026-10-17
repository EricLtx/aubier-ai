//! The kernel's half of driver packaging (packaging spec §4.4–4.5): the
//! publisher key and the human act `driver.pack`.
//!
//! The publisher key signs packages and nothing else — never a ledger row,
//! never a mount: the node's device key keeps those roles. Its secret lives in
//! the OS keyring (`vk` / `publisher`) or, when `VK_PUBLISHER_KEY_FILE` names
//! one, in a file: the seam `VK_NODE_KEY_FILE` gives the device key. Its
//! public half is pinned under `<state_dir>/capital/publishers/` the moment it
//! is made, so the node that packs accepts what it packs (self-pinned).
//!
//! OS analogy: the key is the factory's signing key, the pin the fused public
//! key the boot ROM checks firmware against, and `driver.packed` the line in
//! the factory log naming the image that was flashed.
use crate::{store_failed, RealKernel};
use serde::Serialize;
use std::path::PathBuf;
use vk_capital::signature::{parse_public_pem, KeySource, Pins, PublisherKey};
use vk_capital::CapitalError;
use vk_contracts::driver::{DriverManifest, DriverSource};
use vk_contracts::package::DriverPacked;
use vk_contracts::principal::Principal;
use vk_contracts::syscalls::{Ctx, KernelError};

/// What `vk capital key init` and `vk capital key show` print.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct PublisherKeyInfo {
    /// `sha256:` and the hex of the 32 public key bytes.
    pub key_id: String,
    /// The public half, as the PEM a publisher hands to installers.
    pub public_pem: String,
    /// Whether this node's pins hold the key.
    pub pinned: bool,
}

impl RealKernel {
    /// `vk capital key init`: a human act. Creates the publisher key (keyring
    /// `vk`/`publisher`, or `VK_PUBLISHER_KEY_FILE` when set — the same file
    /// seam `VK_NODE_KEY_FILE` gives the device key) and self-pins its public
    /// key, the sidecar naming `self:<device>` as who pinned it. Idempotent: a
    /// second call reports the existing key, and pins it again only if its
    /// pin went. Nothing reaches the ledger: the key signs packages, and the
    /// package is what `driver.packed` records.
    pub fn publisher_key_init(&mut self, ctx: &Ctx) -> Result<PublisherKeyInfo, KernelError> {
        let Principal::Human { device_id } = &ctx.principal else {
            return Err(KernelError::I1(
                "creating the publisher key is a human act: run it from an enrolled device".into(),
            ));
        };
        let key =
            PublisherKey::load_or_create(&self.publisher_key_source()).map_err(capital_failed)?;
        let public = parse_public_pem(&key.public_pem()).map_err(capital_failed)?;
        Pins::at(&self.store.state_dir)
            .pin(&public, &format!("self:{device_id}"))
            .map_err(capital_failed)?;
        self.publisher_key_info(&key)
    }

    /// `vk capital key show`: the publisher key, never creating one; `None`
    /// when this account has none.
    pub fn publisher_key_show(&self) -> Result<Option<PublisherKeyInfo>, KernelError> {
        match PublisherKey::load(&self.publisher_key_source()).map_err(capital_failed)? {
            Some(key) => self.publisher_key_info(&key).map(Some),
            None => Ok(None),
        }
    }

    fn publisher_key_info(&self, key: &PublisherKey) -> Result<PublisherKeyInfo, KernelError> {
        let key_id = key.key_id();
        let pinned = Pins::at(&self.store.state_dir)
            .get(&key_id)
            .map_err(capital_failed)?
            .is_some();
        Ok(PublisherKeyInfo {
            key_id,
            public_pem: key.public_pem(),
            pinned,
        })
    }

    /// The file a test named ([`RealKernel::with_publisher_key_file`]), else
    /// `VK_PUBLISHER_KEY_FILE`, read at each call, else the keyring.
    fn publisher_key_source(&self) -> KeySource {
        let file = self.publisher_key_file.clone().or_else(|| {
            std::env::var_os("VK_PUBLISHER_KEY_FILE")
                .filter(|f| !f.is_empty())
                .map(PathBuf::from)
        });
        match file {
            Some(file) => KeySource::File(file),
            None => KeySource::Keyring,
        }
    }

    /// `driver.pack`: a human act. The manifest is the packed copy, and the
    /// version, the package digest and the publisher key id are what `vk-ipc`
    /// built and signed; the kernel validates the manifest (incl. the context
    /// rules), writes the `driver.packed` row and returns it.
    ///
    /// What is packed is an installed manifest, so the context rules are
    /// checked on a copy that says `source: installed`, at the path it will
    /// have once installed, `<install_root>/<name>/<version>`. The version is
    /// `MAJOR.MINOR.PATCH` in digits, refused otherwise before it becomes a
    /// path or a field. The digests are the caller's, recorded as given: the
    /// ledger carries the payload's hash, and the package they name is
    /// checked by digest wherever it is opened. Every refusal leaves the
    /// ledger as it was.
    pub fn driver_packed(
        &mut self,
        ctx: &Ctx,
        manifest: &DriverManifest,
        version: &str,
        package_digest: &str,
        publisher_key_id: &str,
    ) -> Result<DriverPacked, KernelError> {
        let by = match &ctx.principal {
            Principal::Human { device_id } => device_id.clone(),
            Principal::Machine { .. } => {
                return Err(KernelError::I1(
                    "packing a driver is a human act: run it from an enrolled device".into(),
                ))
            }
        };
        manifest
            .validate()
            .map_err(|e| KernelError::Gate(e.to_string()))?;
        if !is_version(version) {
            return Err(KernelError::Gate(format!(
                "version {version:?} is not MAJOR.MINOR.PATCH in digits; remedy: pass a version \
                 such as 1.0.0"
            )));
        }
        let mut installed = manifest.clone();
        installed.source = DriverSource::Installed;
        let root = self.install_root.join(&manifest.name).join(version);
        self.check_context(&installed, &root)?;
        let row = DriverPacked {
            driver_id: manifest.driver_id(),
            name: manifest.name.clone(),
            version: version.into(),
            package_digest: package_digest.into(),
            files_sha256: manifest.program.files_sha256.clone(),
            publisher_key_id: publisher_key_id.into(),
            by,
        };
        self.log("driver.packed", ctx.now_ms, &row)?;
        Ok(row)
    }
}

/// `MAJOR.MINOR.PATCH`, each a run of ASCII digits: the version rule of
/// `vk mount driver <name>@<version>`.
fn is_version(v: &str) -> bool {
    let parts: Vec<&str> = v.split('.').collect();
    parts.len() == 3
        && parts
            .iter()
            .all(|p| !p.is_empty() && p.bytes().all(|b| b.is_ascii_digit()))
}

/// A `vk-capital` refusal is already one line ending in its remedy, in the
/// kernel's voice: a gate. Anything else is the store or the OS failing.
fn capital_failed(e: CapitalError) -> KernelError {
    match e {
        CapitalError::Refused(why) => KernelError::Gate(why),
        other => store_failed(other),
    }
}
