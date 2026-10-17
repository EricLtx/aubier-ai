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
use zeroize::Zeroizing;

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

    fn from_seed(seed: &Seed) -> PublisherKey {
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

impl PinnedKey {
    /// The key as the PEM `PublisherKey::public_pem` prints, rendered here
    /// from the key's 32 bytes: never a third party's text.
    pub fn pem(&self) -> String {
        pem_of(&self.verifying)
    }
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
///
/// `sig.signed` is ignored on purpose: `package_digest` is the digest the
/// caller computed itself from the bytes in hand, and that is what the
/// signature must cover (Tasks 8 and 9 rely on this). A claimed digest inside
/// the signature file proves nothing.
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
        let sidecar = serde_json::json!({
            "pinned_at": rfc3339_utc(std::time::SystemTime::now()),
            "pinned_by": pinned_by,
        });
        // The sidecar first, the `.pub` last: the `.pub` is the commit point
        // (`get` and `list` look at nothing else), so a crash in between leaves
        // no pin, and the next `pin` completes it.
        write_atomic(
            &self.dir.join(format!("{hex}.json")),
            &serde_json::to_vec_pretty(&sidecar)?,
        )?;
        write_atomic(
            &self.dir.join(format!("{hex}.pub")),
            pem_of(&key.verifying).as_bytes(),
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

/// Write-to-temp, sync, rename: the file is whole or absent, never torn.
fn write_atomic(path: &Path, bytes: &[u8]) -> Result<()> {
    let mut tmp_name = path.file_name().unwrap_or_default().to_os_string();
    tmp_name.push(".tmp");
    let tmp = path.with_file_name(tmp_name);
    let mut file = std::fs::File::create(&tmp)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    drop(file);
    std::fs::rename(&tmp, path)?;
    Ok(())
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

/// The seed on its way from the keyring or the file into the signing key:
/// wiped when dropped, as is every text it travels as (as the node device
/// key's, `vk-kernel/src/presence.rs`).
type Seed = Zeroizing<[u8; 32]>;

/// The keyring's seed; when there is none, a fresh one stored there if
/// `create`, else `None`. Keyring errors are not echoed: they come from the OS.
fn keyring_seed(create: bool) -> Result<Option<Seed>> {
    const REMEDY: &str = "check the OS credential store, or use a key file";
    let Ok(entry) = keyring::Entry::new(KEYRING_SERVICE, KEYRING_USER) else {
        return refuse(
            "the OS keyring entry for the publisher key is unusable",
            REMEDY,
        );
    };
    match entry.get_password() {
        Ok(text) => decode(&Zeroizing::new(text)).map(Some),
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

/// A new key file appears whole or not at all: the seed is written to a
/// private temp file beside it, synced, then hard-linked onto the final name
/// (which fails if the name exists, so there is no exists-then-write window and
/// a crash leaves no empty key file). Born `0o600` on Unix; on Windows the file
/// inherits the directory's ACL.
fn file_seed(path: &Path, create: bool) -> Result<Option<Seed>> {
    if !create {
        return match std::fs::read_to_string(path) {
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
            Err(e) => Err(e.into()),
            Ok(text) => decode(Zeroizing::new(text).trim()).map(Some),
        };
    }
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let mut tmp_name = path.file_name().unwrap_or_default().to_os_string();
    tmp_name.push(format!(".tmp-{}", std::process::id()));
    let tmp = path.with_file_name(tmp_name);
    let _ = std::fs::remove_file(&tmp);
    let mut opts = std::fs::OpenOptions::new();
    opts.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        opts.mode(0o600);
    }
    let seed = fresh();
    let written = opts.open(&tmp).and_then(|mut file| {
        file.write_all(encode(&seed).as_bytes())?;
        file.sync_all()
    });
    let linked = written.and_then(|()| std::fs::hard_link(&tmp, path));
    let _ = std::fs::remove_file(&tmp);
    match linked {
        Ok(()) => Ok(Some(seed)),
        Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {
            let text = Zeroizing::new(std::fs::read_to_string(path)?);
            decode(text.trim()).map(Some)
        }
        Err(e) => Err(e.into()),
    }
}

fn fresh() -> Seed {
    let mut seed = Zeroizing::new([0u8; 32]);
    rand::rngs::OsRng.fill_bytes(&mut *seed);
    seed
}

fn encode(seed: &[u8; 32]) -> Zeroizing<String> {
    Zeroizing::new(b64().encode(seed))
}

fn decode(text: &str) -> Result<Seed> {
    let bytes = b64().decode(text).ok().map(Zeroizing::new);
    match bytes {
        Some(v) if v.len() == 32 => {
            let mut seed = Zeroizing::new([0u8; 32]);
            seed.copy_from_slice(&v);
            Ok(seed)
        }
        _ => refuse(
            "the publisher key is not a base64 32-byte seed",
            "restore the key from a backup, or create a new publisher key",
        ),
    }
}

/// `YYYY-MM-DDTHH:MM:SSZ`, by civil-from-days (Howard Hinnant's algorithm).
pub fn rfc3339_utc(t: std::time::SystemTime) -> String {
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
    fn a_sidecar_without_its_pub_is_not_a_pin_and_pin_completes_it() {
        let t = tempfile::tempdir().unwrap();
        let k = PublisherKey::load_or_create(&KeySource::File(t.path().join("k"))).unwrap();
        let pinned = parse_public_pem(&k.public_pem()).unwrap();
        let pins = Pins::at(t.path());
        pins.pin(&pinned, "S-1").unwrap();
        let dir = t.path().join("capital/publishers");
        let hex = k.key_id().trim_start_matches("sha256:").to_string();
        std::fs::remove_file(dir.join(format!("{hex}.pub"))).unwrap(); // a crash before the commit point
        assert!(pins.get(&k.key_id()).unwrap().is_none());
        assert!(pins.list().unwrap().is_empty());
        pins.pin(&pinned, "S-2").unwrap();
        assert_eq!(pins.get(&k.key_id()).unwrap().unwrap().key_id, k.key_id());
        assert_eq!(pins.list().unwrap(), vec![k.key_id()]);
        let mut names: Vec<_> = std::fs::read_dir(&dir)
            .unwrap()
            .map(|e| e.unwrap().file_name().into_string().unwrap())
            .collect();
        names.sort();
        assert_eq!(names, vec![format!("{hex}.json"), format!("{hex}.pub")]);
    }
    #[test]
    fn creating_a_key_file_leaves_one_file_and_never_replaces_an_existing_key() {
        let t = tempfile::tempdir().unwrap();
        let path = t.path().join("publisher.key");
        let k1 = PublisherKey::load_or_create(&KeySource::File(path.clone())).unwrap();
        assert_eq!(std::fs::read_dir(t.path()).unwrap().count(), 1);
        let k2 = PublisherKey::load_or_create(&KeySource::File(path)).unwrap();
        assert_eq!(k1.key_id(), k2.key_id());
        assert_eq!(std::fs::read_dir(t.path()).unwrap().count(), 1);
    }
    #[test]
    fn verify_refuses_hostile_signatures_without_panicking() {
        let t = tempfile::tempdir().unwrap();
        let k = PublisherKey::load_or_create(&KeySource::File(t.path().join("k"))).unwrap();
        let pinned = parse_public_pem(&k.public_pem()).unwrap();
        let digest = "sha256:".to_string() + &"a".repeat(64);
        let good = k.sign(&digest);
        verify(&pinned, &digest, &good).unwrap();

        let mut other_algorithm = good.clone();
        other_algorithm.algorithm = "rsa".into();
        assert!(verify(&pinned, &digest, &other_algorithm)
            .unwrap_err()
            .to_string()
            .contains("algorithm"));

        for bad in [
            "",
            "!!! not base64 !!!",
            "AAAA",                   // valid base64, 3 bytes
            &b64().encode([7u8; 63]), // one byte short
            &b64().encode([7u8; 65]), // one byte long
            &b64().encode([0u8; 64]), // right length, wrong bytes
        ] {
            let mut sig = good.clone();
            sig.signature = bad.to_string();
            assert!(
                verify(&pinned, &digest, &sig)
                    .unwrap_err()
                    .to_string()
                    .contains("does not verify"),
                "accepted {bad:?}"
            );
        }

        // Valid over another digest, even when `signed` claims this one.
        let other = "sha256:".to_string() + &"b".repeat(64);
        let mut sig = k.sign(&other);
        sig.signed = digest.clone();
        assert!(verify(&pinned, &digest, &sig)
            .unwrap_err()
            .to_string()
            .contains("does not verify"));
    }
    #[test]
    fn the_seed_and_its_texts_are_wiped_on_drop() {
        fn wiped<T: zeroize::ZeroizeOnDrop>() {}
        wiped::<Seed>();
        wiped::<Zeroizing<String>>();
        let seed = fresh();
        assert_eq!(*decode(&encode(&seed)).unwrap(), *seed);
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
