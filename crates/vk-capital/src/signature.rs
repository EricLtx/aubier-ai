//! The publisher's key and the package signature. The Ed25519 secret lives in
//! the OS keyring (service `vk`, user `publisher`) or, for tests and CI, in a
//! file; the public half is pinned per node, under the state directory, the way
//! a boot ROM carries the hash of the key it will accept firmware from.
//!
//! OS analogy: signing is the factory flashing a signature next to the image;
//! `verify` is the bootloader checking it against the fused public key, and
//! `Pins` is the fuse bank.
use crate::error::{refuse, Result};
use base64::Engine;
use ed25519_dalek::{Signature as DalekSig, Signer, SigningKey, VerifyingKey};
use rand::RngCore;
use sha2::{Digest, Sha256};
use std::io::Write as _;
use std::path::{Path, PathBuf};
use vk_contracts::package::Signature;

pub const KEYRING_SERVICE: &str = "vk";
pub const KEYRING_USER: &str = "publisher";

const PEM_BEGIN: &str = "-----BEGIN AUBIER PUBLISHER KEY-----";
const PEM_END: &str = "-----END AUBIER PUBLISHER KEY-----";
const MESSAGE_PREFIX: &str = "aubier-driver-package|";
const ALGORITHM: &str = "ed25519";

pub enum KeySource {
    Keyring,
    File(PathBuf),
}

/// The publisher's signing key. Not `Clone`; `SigningKey` wipes itself on drop.
pub struct PublisherKey {
    signing: SigningKey,
}

impl PublisherKey {
    /// Creates a key if none exists under the source, else loads it. The
    /// keyring entry is `vk` / `publisher`, a base64 32-byte seed, like the
    /// node device key (`vk-kernel/src/presence.rs::keyring_seed`).
    pub fn load_or_create(source: &KeySource) -> Result<PublisherKey> {
        let seed = match source {
            KeySource::Keyring => keyring_seed(true)?,
            KeySource::File(path) => file_seed(path, true)?,
        };
        Ok(PublisherKey::from_seed(&seed.expect("created when absent")))
    }

    /// Loads the key if there is one, and never creates it.
    pub fn load(source: &KeySource) -> Result<Option<PublisherKey>> {
        let seed = match source {
            KeySource::Keyring => keyring_seed(false)?,
            KeySource::File(path) => file_seed(path, false)?,
        };
        Ok(seed.map(|s| PublisherKey::from_seed(&s)))
    }

    fn from_seed(seed: &[u8; 32]) -> PublisherKey {
        PublisherKey {
            signing: SigningKey::from_bytes(seed),
        }
    }

    /// `sha256:<hex of the 32 public key bytes>`.
    pub fn key_id(&self) -> String {
        key_id_of(&self.signing.verifying_key())
    }

    /// The public half, as the one-line PEM a publisher hands to installers.
    pub fn public_pem(&self) -> String {
        pem_of(&self.signing.verifying_key())
    }

    /// Signs the UTF-8 bytes of `aubier-driver-package|<digest>`.
    pub fn sign(&self, package_digest: &str) -> Signature {
        let sig = self.signing.sign(&message(package_digest));
        Signature {
            key_id: self.key_id(),
            algorithm: ALGORITHM.into(),
            signed: package_digest.into(),
            signature: b64().encode(sig.to_bytes()),
        }
    }
}

/// A publisher public key a node has been told to trust.
#[derive(Debug, Clone)]
pub struct PinnedKey {
    pub key_id: String,
    pub verifying: VerifyingKey,
}

pub fn key_id_of(verifying: &VerifyingKey) -> String {
    format!("sha256:{:x}", Sha256::digest(verifying.to_bytes()))
}

fn pem_of(verifying: &VerifyingKey) -> String {
    format!(
        "{PEM_BEGIN}\n{}\n{PEM_END}\n",
        b64().encode(verifying.to_bytes())
    )
}

/// Reads the PEM a publisher hands out: exactly the begin line, one base64
/// line of the 32 public key bytes, the end line.
pub fn parse_public_pem(text: &str) -> Result<PinnedKey> {
    const REMEDY: &str = "ask the publisher for their key again, exactly as exported";
    let lines: Vec<&str> = text
        .lines()
        .map(str::trim)
        .filter(|l| !l.is_empty())
        .collect();
    let [PEM_BEGIN, body, PEM_END] = lines.as_slice() else {
        return refuse(
            "not a publisher public key: expected one base64 line between the BEGIN and END lines",
            REMEDY,
        );
    };
    let Ok(bytes) = b64().decode(body) else {
        return refuse("publisher public key is not base64", REMEDY);
    };
    let Ok(bytes) = <[u8; 32]>::try_from(bytes.as_slice()) else {
        return refuse("publisher public key is not 32 bytes", REMEDY);
    };
    let Ok(verifying) = VerifyingKey::from_bytes(&bytes) else {
        return refuse("publisher public key is not a valid Ed25519 point", REMEDY);
    };
    Ok(PinnedKey {
        key_id: key_id_of(&verifying),
        verifying,
    })
}

/// Verifies `sig` over `package_digest` with `pinned`; refuses on a key id
/// mismatch and on a bad signature, with distinct messages.
pub fn verify(pinned: &PinnedKey, package_digest: &str, sig: &Signature) -> Result<()> {
    const ALTERED: &str = "the package was altered or re-signed; refuse it";
    if sig.key_id != pinned.key_id {
        return refuse(
            "signature key id is not the pinned key id",
            "pin the publisher at install, or refuse the package",
        );
    }
    if sig.algorithm != ALGORITHM {
        return refuse("signature algorithm is not ed25519", ALTERED);
    }
    let dalek = b64()
        .decode(&sig.signature)
        .ok()
        .and_then(|raw| DalekSig::from_slice(&raw).ok());
    let verified = dalek.is_some_and(|dalek| {
        pinned
            .verifying
            .verify_strict(&message(package_digest), &dalek)
            .is_ok()
    });
    if !verified {
        return refuse("signature does not verify against the pinned key", ALTERED);
    }
    Ok(())
}

/// `<state_dir>/capital/publishers/<hex>.pub` (the PEM) and `<hex>.json` (who
/// pinned it, and when); `<hex>` is the key id without `sha256:`.
pub struct Pins {
    dir: PathBuf,
}

impl Pins {
    pub fn at(state_dir: &Path) -> Pins {
        Pins {
            dir: state_dir.join("capital").join("publishers"),
        }
    }

    pub fn get(&self, key_id: &str) -> Result<Option<PinnedKey>> {
        let hex = hex_of(key_id)?;
        let text = match std::fs::read_to_string(self.dir.join(format!("{hex}.pub"))) {
            Ok(text) => text,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(e) => return Err(e.into()),
        };
        let key = parse_public_pem(&text)?;
        if key.key_id != key_id {
            return refuse(
                format!("the pin file for {key_id} holds another key"),
                "delete the pin and pin the publisher again",
            );
        }
        Ok(Some(key))
    }

    /// Pins `key`; a key already pinned keeps its original sidecar.
    pub fn pin(&self, key: &PinnedKey, pinned_by: &str) -> Result<()> {
        if key_id_of(&key.verifying) != key.key_id {
            return refuse(
                "the key id does not match the key",
                "build the pinned key with parse_public_pem",
            );
        }
        if self.get(&key.key_id)?.is_some() {
            return Ok(());
        }
        let hex = hex_of(&key.key_id)?;
        std::fs::create_dir_all(&self.dir)?;
        std::fs::write(self.dir.join(format!("{hex}.pub")), pem_of(&key.verifying))?;
        let sidecar = serde_json::json!({
            "pinned_at": rfc3339_utc(std::time::SystemTime::now()),
            "pinned_by": pinned_by,
        });
        std::fs::write(
            self.dir.join(format!("{hex}.json")),
            serde_json::to_vec_pretty(&sidecar)?,
        )?;
        Ok(())
    }

    /// The key ids pinned, sorted.
    pub fn list(&self) -> Result<Vec<String>> {
        let entries = match std::fs::read_dir(&self.dir) {
            Ok(entries) => entries,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(vec![]),
            Err(e) => return Err(e.into()),
        };
        let mut ids = vec![];
        for entry in entries {
            let name = entry?.file_name();
            if let Some(stem) = name.to_str().and_then(|n| n.strip_suffix(".pub")) {
                if is_hex64(stem) {
                    ids.push(format!("sha256:{stem}"));
                }
            }
        }
        ids.sort();
        Ok(ids)
    }
}

fn b64() -> base64::engine::GeneralPurpose {
    base64::engine::general_purpose::STANDARD
}

fn message(package_digest: &str) -> Vec<u8> {
    format!("{MESSAGE_PREFIX}{package_digest}").into_bytes()
}

fn is_hex64(s: &str) -> bool {
    s.len() == 64 && s.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
}

/// The hex of a key id, which is also a file name: so it is checked, never
/// trusted.
fn hex_of(key_id: &str) -> Result<&str> {
    match key_id.strip_prefix("sha256:") {
        Some(hex) if is_hex64(hex) => Ok(hex),
        _ => refuse(
            "a key id is `sha256:` and 64 lowercase hex digits",
            "pass the key id as printed by the publisher",
        ),
    }
}

/// The keyring's seed; when there is none, a fresh one stored there if
/// `create`, else `None`. Keyring errors are not echoed: they come from the OS.
fn keyring_seed(create: bool) -> Result<Option<[u8; 32]>> {
    const REMEDY: &str = "check the OS credential store, or use a key file";
    let Ok(entry) = keyring::Entry::new(KEYRING_SERVICE, KEYRING_USER) else {
        return refuse(
            "the OS keyring entry for the publisher key is unusable",
            REMEDY,
        );
    };
    match entry.get_password() {
        Ok(text) => decode(&text).map(Some),
        Err(keyring::Error::NoEntry) if !create => Ok(None),
        Err(keyring::Error::NoEntry) => {
            let seed = fresh();
            if entry.set_password(&encode(&seed)).is_err() {
                return refuse("the OS keyring refused to store the publisher key", REMEDY);
            }
            Ok(Some(seed))
        }
        Err(_) => refuse(
            "the OS keyring could not be read for the publisher key",
            REMEDY,
        ),
    }
}

/// An atomic `create_new`, so there is no exists-then-write window; born
/// `0o600` on Unix. On Windows the file inherits the directory's ACL.
fn file_seed(path: &Path, create: bool) -> Result<Option<[u8; 32]>> {
    if !create {
        return match std::fs::read_to_string(path) {
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
            Err(e) => Err(e.into()),
            Ok(text) => decode(text.trim()).map(Some),
        };
    }
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let mut opts = std::fs::OpenOptions::new();
    opts.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        opts.mode(0o600);
    }
    match opts.open(path) {
        Ok(mut file) => {
            let seed = fresh();
            file.write_all(encode(&seed).as_bytes())?;
            Ok(Some(seed))
        }
        Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {
            let text = std::fs::read_to_string(path)?;
            decode(text.trim()).map(Some)
        }
        Err(e) => Err(e.into()),
    }
}

fn fresh() -> [u8; 32] {
    let mut seed = [0u8; 32];
    rand::rngs::OsRng.fill_bytes(&mut seed);
    seed
}

fn encode(seed: &[u8; 32]) -> String {
    b64().encode(seed)
}

fn decode(text: &str) -> Result<[u8; 32]> {
    let seed = b64()
        .decode(text)
        .ok()
        .and_then(|v| <[u8; 32]>::try_from(v.as_slice()).ok());
    match seed {
        Some(seed) => Ok(seed),
        None => refuse(
            "the publisher key is not a base64 32-byte seed",
            "restore the key from a backup, or create a new publisher key",
        ),
    }
}

/// `YYYY-MM-DDTHH:MM:SSZ`, by civil-from-days (Howard Hinnant's algorithm).
fn rfc3339_utc(t: std::time::SystemTime) -> String {
    let secs = t
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.as_secs()) as i64;
    let (days, rem) = (secs.div_euclid(86_400), secs.rem_euclid(86_400));
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = yoe + era * 400 + i64::from(month <= 2);
    format!(
        "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}Z",
        rem / 3_600,
        rem % 3_600 / 60,
        rem % 60
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn a_file_key_is_created_once_and_reloaded() {
        let t = tempfile::tempdir().unwrap();
        let src = KeySource::File(t.path().join("publisher.key"));
        assert!(PublisherKey::load(&src).unwrap().is_none());
        let k1 = PublisherKey::load_or_create(&src).unwrap();
        let k2 = PublisherKey::load(&src).unwrap().unwrap();
        assert_eq!(k1.key_id(), k2.key_id());
        assert!(k1.key_id().starts_with("sha256:"));
        assert_eq!(
            PublisherKey::load_or_create(&src).unwrap().key_id(),
            k1.key_id()
        );
    }
    #[test]
    fn sign_then_verify_with_the_pinned_public_key() {
        let t = tempfile::tempdir().unwrap();
        let k = PublisherKey::load_or_create(&KeySource::File(t.path().join("k"))).unwrap();
        let digest = "sha256:".to_string() + &"a".repeat(64);
        let sig = k.sign(&digest);
        assert_eq!(sig.algorithm, "ed25519");
        assert_eq!(sig.key_id, k.key_id());
        assert_eq!(sig.signed, digest);
        let pinned = parse_public_pem(&k.public_pem()).unwrap();
        verify(&pinned, &digest, &sig).unwrap();
        let other = "sha256:".to_string() + &"b".repeat(64);
        assert!(verify(&pinned, &other, &sig)
            .unwrap_err()
            .to_string()
            .contains("does not verify"));
    }
    #[test]
    fn a_signature_from_another_key_with_the_same_claimed_id_is_refused_without_a_prompt() {
        let t = tempfile::tempdir().unwrap();
        let k1 = PublisherKey::load_or_create(&KeySource::File(t.path().join("k1"))).unwrap();
        let k2 = PublisherKey::load_or_create(&KeySource::File(t.path().join("k2"))).unwrap();
        let digest = "sha256:".to_string() + &"a".repeat(64);
        let mut sig = k2.sign(&digest);
        sig.key_id = k1.key_id(); // claims k1
        let pinned = parse_public_pem(&k1.public_pem()).unwrap();
        assert!(verify(&pinned, &digest, &sig)
            .unwrap_err()
            .to_string()
            .contains("does not verify"));
        let mut sig = k2.sign(&digest);
        sig.key_id = "sha256:".to_string() + &"c".repeat(64);
        assert!(verify(&pinned, &digest, &sig)
            .unwrap_err()
            .to_string()
            .contains("key id"));
    }
    #[test]
    fn pins_are_files_under_the_state_dir() {
        let t = tempfile::tempdir().unwrap();
        let k = PublisherKey::load_or_create(&KeySource::File(t.path().join("k"))).unwrap();
        let pins = Pins::at(t.path());
        assert!(pins.get(&k.key_id()).unwrap().is_none());
        pins.pin(&parse_public_pem(&k.public_pem()).unwrap(), "S-1-5-21-1")
            .unwrap();
        assert_eq!(pins.get(&k.key_id()).unwrap().unwrap().key_id, k.key_id());
        assert_eq!(pins.list().unwrap(), vec![k.key_id()]);
        let sidecar = std::fs::read_to_string(
            t.path()
                .join("capital/publishers")
                .join(format!("{}.json", k.key_id().trim_start_matches("sha256:"))),
        )
        .unwrap();
        assert!(sidecar.contains("S-1-5-21-1"));
        assert!(sidecar.contains("pinned_at"));
    }
    #[test]
    fn the_pem_is_exactly_the_wrapper_and_malformed_ones_are_refused() {
        let t = tempfile::tempdir().unwrap();
        let k = PublisherKey::load_or_create(&KeySource::File(t.path().join("k"))).unwrap();
        let pem = k.public_pem();
        assert!(pem.starts_with("-----BEGIN AUBIER PUBLISHER KEY-----\n"));
        assert!(pem.ends_with("\n-----END AUBIER PUBLISHER KEY-----\n"));
        for bad in [
            "",
            "not a key",
            &pem.replace("AUBIER PUBLISHER", "PUBLIC"),
            &pem.replace("-----END", "x\n-----END"),
            "-----BEGIN AUBIER PUBLISHER KEY-----\nAAAA\n-----END AUBIER PUBLISHER KEY-----\n",
        ] {
            assert!(parse_public_pem(bad).is_err(), "accepted {bad:?}");
        }
    }
    #[test]
    fn a_key_id_that_is_not_a_digest_never_becomes_a_path() {
        let t = tempfile::tempdir().unwrap();
        let pins = Pins::at(t.path());
        assert!(pins.get("sha256:../../x").is_err());
        assert!(pins.get("sha256:ABC").is_err());
    }
    #[test]
    fn a_pin_file_swapped_for_another_key_is_refused_and_repinning_keeps_the_first_record() {
        let t = tempfile::tempdir().unwrap();
        let k1 = PublisherKey::load_or_create(&KeySource::File(t.path().join("k1"))).unwrap();
        let k2 = PublisherKey::load_or_create(&KeySource::File(t.path().join("k2"))).unwrap();
        let pins = Pins::at(t.path());
        pins.pin(&parse_public_pem(&k1.public_pem()).unwrap(), "first")
            .unwrap();
        pins.pin(&parse_public_pem(&k1.public_pem()).unwrap(), "second")
            .unwrap();
        let hex = k1.key_id().trim_start_matches("sha256:").to_string();
        let dir = t.path().join("capital/publishers");
        let sidecar = std::fs::read_to_string(dir.join(format!("{hex}.json"))).unwrap();
        assert!(sidecar.contains("first") && !sidecar.contains("second"));
        std::fs::write(dir.join(format!("{hex}.pub")), k2.public_pem()).unwrap();
        assert!(pins
            .get(&k1.key_id())
            .unwrap_err()
            .to_string()
            .contains("another key"));
    }
    #[test]
    fn the_timestamp_is_rfc3339_utc() {
        use std::time::{Duration, UNIX_EPOCH};
        assert_eq!(rfc3339_utc(UNIX_EPOCH), "1970-01-01T00:00:00Z");
        // 2024-02-29T12:34:56Z, a leap day.
        assert_eq!(
            rfc3339_utc(UNIX_EPOCH + Duration::from_secs(1_709_210_096)),
            "2024-02-29T12:34:56Z"
        );
    }
}
