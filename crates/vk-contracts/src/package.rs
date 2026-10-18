//! The driver package: an OCI image manifest with one layer per component, so
//! a registry, a signature and a mirror work on it unchanged. The kernel's
//! own work on it is to read it, check its shape and name its identity; the
//! bytes of each layer are checked by digest elsewhere.

use std::collections::BTreeMap;

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

pub const DRIVER_ARTIFACT_TYPE: &str = "application/vnd.aubier.driver.v1";
pub const SIGNATURE_ARTIFACT_TYPE: &str = "application/vnd.aubier.signature.v1";
pub const OCI_MANIFEST_TYPE: &str = "application/vnd.oci.image.manifest.v1+json";
pub const OCI_EMPTY_TYPE: &str = "application/vnd.oci.empty.v1+json";
pub const MEDIA_MANIFEST: &str = "application/vnd.aubier.driver.manifest.v1+json";
pub const MEDIA_FILES: &str = "application/vnd.aubier.driver.files.v1.tar";
pub const MEDIA_LOCK: &str = "application/vnd.aubier.driver.lock.v1+json";
pub const MEDIA_WHEELS: &str = "application/vnd.aubier.driver.wheels.v1.tar";
pub const MEDIA_PYTHON: &str = "application/vnd.aubier.driver.python.v1.tar";
pub const COMPONENTS: [&str; 5] = ["manifest", "files", "lock", "wheels", "python"];

pub const ANN_NAME: &str = "org.aubier.driver.name";
pub const ANN_VERSION: &str = "org.aubier.driver.version";
pub const ANN_DRIVER_ID: &str = "org.aubier.driver.id";
pub const ANN_CREATED: &str = "org.aubier.package.created";
pub const ANN_PUBLISHER: &str = "org.aubier.publisher.key_id";
pub const ANN_COMPONENT: &str = "org.aubier.component";
pub const ANN_PYTHON_SHA256: &str = "org.aubier.python.upstream_sha256";
pub const ANN_PYTHON_RELEASE: &str = "org.aubier.python.release";

/// An OCI content descriptor: what a layer or the config is, by digest.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Descriptor {
    #[serde(rename = "mediaType")]
    pub media_type: String,
    pub digest: String,
    pub size: u64,
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub annotations: BTreeMap<String, String>,
}

/// The OCI image manifest of a driver package: one layer per component.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct DriverPackage {
    /// Always 2.
    #[serde(rename = "schemaVersion")]
    pub schema_version: u32,
    /// `application/vnd.oci.image.manifest.v1+json`.
    #[serde(rename = "mediaType")]
    pub media_type: String,
    /// `application/vnd.aubier.driver.v1`.
    #[serde(rename = "artifactType")]
    pub artifact_type: String,
    /// The empty descriptor: a driver package has no config.
    pub config: Descriptor,
    /// The five components, in the order of [`COMPONENTS`].
    pub layers: Vec<Descriptor>,
    pub annotations: BTreeMap<String, String>,
}

impl DriverPackage {
    /// Every refusal names the field and what it must be. Checked in this
    /// order: `schemaVersion`, the two media types, the config, the layers
    /// (count, then each in order: its `org.aubier.component` annotation, its
    /// media type, its digest), the required annotations, and last the python
    /// layer's upstream digest.
    pub fn validate(&self) -> Result<(), String> {
        if self.schema_version != 2 {
            return Err("schemaVersion must be 2".into());
        }
        if self.media_type != OCI_MANIFEST_TYPE {
            return Err(format!("mediaType must be {OCI_MANIFEST_TYPE}"));
        }
        if self.artifact_type != DRIVER_ARTIFACT_TYPE {
            return Err(format!("artifactType must be {DRIVER_ARTIFACT_TYPE}"));
        }
        if self.config.media_type != OCI_EMPTY_TYPE {
            return Err("config must be the empty descriptor".into());
        }
        let expected = [
            MEDIA_MANIFEST,
            MEDIA_FILES,
            MEDIA_LOCK,
            MEDIA_WHEELS,
            MEDIA_PYTHON,
        ];
        if self.layers.len() != 5 {
            return Err(format!(
                "five layers in the order {:?}, got {}",
                COMPONENTS,
                self.layers.len()
            ));
        }
        for (i, (layer, component)) in self.layers.iter().zip(COMPONENTS).enumerate() {
            if layer.annotations.get(ANN_COMPONENT).map(String::as_str) != Some(component) {
                return Err(format!("layer {i} must carry {ANN_COMPONENT}={component}"));
            }
            if layer.media_type != expected[i] {
                return Err(format!("layer {component} must be {}", expected[i]));
            }
            if !layer.digest.starts_with("sha256:") || layer.digest.len() != 71 {
                return Err(format!("layer {component}: digest must be sha256:<64 hex>"));
            }
        }
        for a in [
            ANN_NAME,
            ANN_VERSION,
            ANN_DRIVER_ID,
            ANN_CREATED,
            ANN_PUBLISHER,
        ] {
            if !self.annotations.contains_key(a) {
                return Err(format!("annotation {a} is required"));
            }
        }
        let python_has_digest = self
            .layer("python")
            .is_some_and(|l| l.annotations.contains_key(ANN_PYTHON_SHA256));
        if !python_has_digest {
            return Err(format!("the python layer must carry {ANN_PYTHON_SHA256}"));
        }
        Ok(())
    }

    /// The layer whose `org.aubier.component` annotation is `component`.
    pub fn layer(&self, component: &str) -> Option<&Descriptor> {
        self.layers
            .iter()
            .find(|l| l.annotations.get(ANN_COMPONENT).map(String::as_str) == Some(component))
    }

    fn annotation(&self, key: &str) -> &str {
        self.annotations.get(key).map(String::as_str).unwrap_or("")
    }

    pub fn name(&self) -> &str {
        self.annotation(ANN_NAME)
    }

    pub fn version(&self) -> &str {
        self.annotation(ANN_VERSION)
    }

    pub fn driver_id(&self) -> &str {
        self.annotation(ANN_DRIVER_ID)
    }

    pub fn publisher_key_id(&self) -> &str {
        self.annotation(ANN_PUBLISHER)
    }
}

/// A detached signature over a package: the image manifest digest, signed.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Signature {
    pub key_id: String,
    /// `ed25519`.
    pub algorithm: String,
    /// The image manifest digest that was signed, `sha256:` and 64 hex digits.
    pub signed: String,
    /// The signature, base64.
    pub signature: String,
}

/// Ledger payload of `driver.packed`: a human act, written by the node that
/// packs. It names the package by digest and the tree it holds by
/// `files_sha256`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct DriverPacked {
    pub driver_id: String,
    pub name: String,
    pub version: String,
    pub package_digest: String,
    pub files_sha256: String,
    pub publisher_key_id: String,
    pub by: String,
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fixture() -> DriverPackage {
        serde_json::from_str(include_str!(
            "../../../contracts/examples/driver_package/valid/fixture.json"
        ))
        .unwrap()
    }
    #[test]
    fn the_fixture_package_validates_and_names_its_five_layers() {
        let p = fixture();
        p.validate().unwrap();
        for c in COMPONENTS {
            assert!(p.layer(c).is_some(), "layer {c}");
        }
        assert_eq!(p.name(), "maskai-fixture");
        assert_eq!(p.version(), "0.1.0");
    }
    #[test]
    fn a_missing_layer_or_a_wrong_media_type_is_refused() {
        let mut p = fixture();
        p.layers.remove(2);
        assert!(p.validate().unwrap_err().contains("lock"));
        let mut p = fixture();
        p.layers[1].media_type = "application/octet-stream".into();
        assert!(p.validate().unwrap_err().contains("files"));
    }
    #[test]
    fn an_old_driver_mounted_row_still_reads() {
        let old = r#"{"driver_id":"sha256:aa","name":"x","manifest_hash":"sha256:bb","profile_dir_hash":"sha256:cc","governed":true,"by":"node:n"}"#;
        let row: crate::driver::DriverMounted = serde_json::from_str(old).unwrap();
        assert_eq!(row.package_digest, None);
        assert_eq!(row.source, crate::driver::DriverSource::Development);
    }
}
