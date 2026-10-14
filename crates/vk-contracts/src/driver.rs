//! Driver manifests (pilot spec §4). A driver is a peripheral — the pilot's is a
//! browser — that sits behind a process this kernel starts and is reachable only
//! by syscall. The manifest says what the process is, which hosts it may reach
//! and which actions it may be asked for; the kernel refuses anything else.
use crate::hash_canonical;
use crate::labels::Clearance;
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::path::Path;

/// What kind of peripheral the driver controls.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum DriverKind {
    Browser,
}

/// The process the host spawns for this driver.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct DriverProgram {
    /// `python-playwright` for the pilot; informative, part of the identity.
    pub runtime: String,
    /// argv the host spawns, relative paths resolved against the daemon's cwd.
    pub command: Vec<String>,
    /// `hash_dir` of the driver's directory (see [`hash_dir`]).
    pub files_sha256: String,
    pub browser_channel: Option<String>,
    pub headless: bool,
}

/// One action the driver implements, and the shape of what it may return.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct DriverAction {
    pub name: String,
    pub reversible: bool,
    /// A JSON Schema object the kernel validates `args` against.
    pub args: Value,
    /// Evidence file names this action may return (anything else is refused).
    pub evidence: Vec<String>,
    /// When true, `result.text` is appended to the register's evidence so a later
    /// Judge or Draft arch reads it (journal exports, oracle verdicts).
    #[serde(default)]
    pub to_register: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct DriverManifest {
    pub name: String,
    pub kind: DriverKind,
    pub program: DriverProgram,
    /// Host names only (no scheme, no port, no path), lower-case.
    pub hosts: Vec<String>,
    pub actions: Vec<DriverAction>,
    pub min_interval_ms: u64,
    pub max_action_ms: u64,
    pub clearance: Clearance,
    /// Read back from the host at mount, never trusted from the file.
    pub governed: bool,
}

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum DriverError {
    #[error("driver manifest: {0}")]
    Invalid(String),
}

fn invalid<T>(why: impl Into<String>) -> Result<T, DriverError> {
    Err(DriverError::Invalid(why.into()))
}

/// The artefact-kind rule, re-stated here because this crate must not depend on
/// the kernel: at most 32 bytes of `[A-Za-z0-9._-]`, not starting with `.`.
pub(crate) fn plain_token(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 32
        && !s.starts_with('.')
        && s.bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'.' | b'_' | b'-'))
}

/// Does `s` read like a hash: `sha256:` and 64 lower-case hex digits?
fn is_sha256(s: &str) -> bool {
    s.strip_prefix("sha256:").is_some_and(|h| {
        h.len() == 64
            && h.bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    })
}

/// The first key, at any depth, whose lower-cased name reads like a credential.
fn secret_key(v: &Value) -> Option<String> {
    const NEEDLES: [&str; 5] = ["password", "token", "secret", "api_key", "cookie"];
    match v {
        Value::Object(map) => map.iter().find_map(|(k, inner)| {
            let lower = k.to_ascii_lowercase();
            if NEEDLES.iter().any(|n| lower.contains(n)) {
                Some(k.clone())
            } else {
                secret_key(inner)
            }
        }),
        Value::Array(items) => items.iter().find_map(secret_key),
        _ => None,
    }
}

impl DriverManifest {
    /// `sha256:<hex>` of the canonical manifest with `governed` forced to false,
    /// so what the host observed does not change who the driver is. Formed like
    /// `ArchManifest::arch_id`: `hash_canonical` already carries the `sha256:`
    /// prefix, so none is added here.
    pub fn driver_id(&self) -> String {
        let mut m = self.clone();
        m.governed = false;
        hash_canonical(&m)
    }

    /// Every refusal names its remedy. Checked in this order: name, command,
    /// `files_sha256`, hosts, actions (count, duplicates, names, `args`,
    /// evidence), the interval ceiling, and last the credential-shaped keys.
    pub fn validate(&self) -> Result<(), DriverError> {
        let name_ok = !self.name.is_empty()
            && self.name.len() <= 32
            && self
                .name
                .bytes()
                .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-');
        if !name_ok {
            return invalid(format!(
                "name `{}` must be 1 to 32 characters of a-z, 0-9 and `-`",
                self.name
            ));
        }
        if self.program.command.is_empty() {
            return invalid("program.command is empty: give the argv the host should spawn");
        }
        if !is_sha256(&self.program.files_sha256) {
            return invalid(
                "program.files_sha256 must be `sha256:` and 64 lower-case hex digits \
                 (the `hash_dir` of the driver's directory)",
            );
        }
        if self.hosts.is_empty() {
            return invalid("hosts is empty: list the host names the driver may reach");
        }
        for h in &self.hosts {
            let bad = h.is_empty()
                || h.chars()
                    .any(|c| c == '/' || c == ':' || c.is_whitespace() || c.is_uppercase());
            if bad {
                return invalid(format!(
                    "host `{h}`: host names only, lower-case, no scheme, port or path"
                ));
            }
        }
        if self.actions.is_empty() {
            return invalid("actions is empty: declare at least one action the driver implements");
        }
        let mut seen = std::collections::BTreeSet::new();
        for a in &self.actions {
            if !seen.insert(a.name.as_str()) {
                return invalid(format!(
                    "duplicate action `{}`: action names must be unique",
                    a.name
                ));
            }
            let action_ok = !a.name.is_empty()
                && a.name.len() <= 32
                && a.name.bytes().all(|b| b.is_ascii_lowercase() || b == b'_');
            if !action_ok {
                return invalid(format!(
                    "action name `{}` must be 1 to 32 characters of a-z and `_`",
                    a.name
                ));
            }
            if a.args.get("type").and_then(Value::as_str) != Some("object") {
                return invalid(format!(
                    "action `{}`: args must be a JSON Schema object with \"type\": \"object\"",
                    a.name
                ));
            }
            for e in &a.evidence {
                if !plain_token(e) {
                    return invalid(format!(
                        "action `{}`: evidence name `{e}` must be a short plain token",
                        a.name
                    ));
                }
            }
        }
        if self.min_interval_ms > self.max_action_ms {
            return invalid(format!(
                "min_interval_ms ({}) exceeds max_action_ms ({}): no action could ever run",
                self.min_interval_ms, self.max_action_ms
            ));
        }
        let as_value = serde_json::to_value(self).expect("contract types always serialize");
        if let Some(k) = secret_key(&as_value) {
            return invalid(format!(
                "key `{k}` looks like a credential: a manifest is stored in the clear, \
                 keep secrets in the keyring"
            ));
        }
        Ok(())
    }

    pub fn action(&self, name: &str) -> Option<&DriverAction> {
        self.actions.iter().find(|a| a.name == name)
    }
}

/// `sha256:<hex>` over a driver's directory, so a changed file is a new driver.
///
/// Every regular file under `dir` is listed by its path relative to `dir` with
/// forward slashes; any path whose first component is `__pycache__`, `tests` or
/// `fixture` is skipped; the list is sorted by path; `path\0bytes\0` is fed to
/// SHA-256 for each entry. Directory order and build debris do not change it.
pub fn hash_dir(dir: &Path) -> std::io::Result<String> {
    fn walk(root: &Path, dir: &Path, out: &mut Vec<(String, Vec<u8>)>) -> std::io::Result<()> {
        for entry in std::fs::read_dir(dir)? {
            let entry = entry?;
            let path = entry.path();
            let rel: Vec<String> = path
                .strip_prefix(root)
                .expect("entry is under root")
                .components()
                .map(|c| c.as_os_str().to_string_lossy().into_owned())
                .collect();
            if matches!(
                rel.first().map(String::as_str),
                Some("__pycache__" | "tests" | "fixture")
            ) {
                continue;
            }
            let kind = entry.file_type()?;
            if kind.is_dir() {
                walk(root, &path, out)?;
            } else if kind.is_file() {
                out.push((rel.join("/"), std::fs::read(&path)?));
            }
        }
        Ok(())
    }
    let mut files = Vec::new();
    walk(dir, dir, &mut files)?;
    files.sort_by(|a, b| a.0.cmp(&b.0));
    let mut h = Sha256::new();
    for (path, bytes) in &files {
        h.update(path.as_bytes());
        h.update([0]);
        h.update(bytes);
        h.update([0]);
    }
    Ok(format!("sha256:{}", hex::encode(h.finalize())))
}

/// Ledger payload of `driver.mounted`: the identity and what the host read back.
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct DriverMounted {
    pub driver_id: String,
    pub name: String,
    pub manifest_hash: String,
    pub profile_dir_hash: String,
    pub governed: bool,
    pub by: String,
}

/// Ledger payload of `driver.acted`: one action, by hashes only — what was asked
/// and what came back live in the evidence, never on the ledger.
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct DriverActed {
    pub task_id: String,
    pub step_index: usize,
    pub driver_id: String,
    pub action: String,
    pub args_hash: String,
    pub result_hash: String,
    pub ok: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
    pub duration_ms: u64,
    pub evidence: Vec<String>,
    pub evidence_bytes: u64,
    pub hosts_seen: Vec<String>,
}

/// Ledger payload of `driver.unmounted`.
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct DriverUnmounted {
    pub driver_id: String,
    pub reason: String,
}

#[cfg(test)]
mod tests {
    use super::*;
    fn manifest() -> DriverManifest {
        serde_json::from_str(include_str!(
            "../../../contracts/examples/driver_manifest/valid/maskai.json"
        ))
        .unwrap()
    }

    #[test]
    fn valid_example_validates_and_has_a_stable_id() {
        let m = manifest();
        m.validate().unwrap();
        let mut g = m.clone();
        g.governed = true;
        assert_eq!(
            m.driver_id(),
            g.driver_id(),
            "governed is read back, not identity"
        );
        assert!(m.driver_id().starts_with("sha256:"));
        assert_eq!(m.driver_id().len(), 7 + 64, "one prefix, not two");
    }
    #[test]
    fn host_with_scheme_or_path_is_refused() {
        let mut m = manifest();
        m.hosts = vec!["https://app.maskai.fr/chat".into()];
        let e = m.validate().unwrap_err().to_string();
        assert!(e.contains("host names only"), "{e}");
    }
    #[test]
    fn a_secret_shaped_key_anywhere_is_refused() {
        let mut m = manifest();
        m.actions[0].args =
            serde_json::json!({"type":"object","properties":{"cookie":{"type":"string"}}});
        assert!(m.validate().unwrap_err().to_string().contains("cookie"));
    }
    #[test]
    fn duplicate_action_is_refused() {
        let mut m = manifest();
        let a = m.actions[0].clone();
        m.actions.push(a);
        assert!(m.validate().unwrap_err().to_string().contains("duplicate"));
    }
    #[test]
    fn each_remaining_refusal_names_its_remedy() {
        let refuse = |edit: &dyn Fn(&mut DriverManifest), needle: &str| {
            let mut m = manifest();
            edit(&mut m);
            let e = m.validate().unwrap_err().to_string();
            assert!(e.contains(needle), "{needle}: {e}");
        };
        refuse(&|m| m.name = String::new(), "name");
        refuse(&|m| m.name = "MaskAI".into(), "name");
        refuse(&|m| m.program.command.clear(), "program.command");
        refuse(
            &|m| m.program.files_sha256 = "sha256:abc".into(),
            "files_sha256",
        );
        refuse(&|m| m.hosts.clear(), "hosts is empty");
        refuse(
            &|m| m.hosts = vec!["App.maskai.fr".into()],
            "host names only",
        );
        refuse(
            &|m| m.hosts = vec!["app.maskai.fr:443".into()],
            "host names only",
        );
        refuse(&|m| m.actions.clear(), "actions is empty");
        refuse(&|m| m.actions[0].name = "Bad-Name".into(), "action name");
        refuse(
            &|m| m.actions[0].args = serde_json::json!({"type":"string"}),
            "args",
        );
        refuse(
            &|m| m.actions[0].evidence = vec![".hidden".into()],
            "evidence",
        );
        refuse(
            &|m| m.min_interval_ms = m.max_action_ms + 1,
            "min_interval_ms",
        );
    }
    #[test]
    fn the_secret_scan_is_case_blind_and_looks_inside_arrays() {
        let v = serde_json::json!({"a": [{"b": {"AuthToken": "x"}}]});
        assert_eq!(secret_key(&v).as_deref(), Some("AuthToken"));
        assert_eq!(secret_key(&serde_json::json!({"a": ["token"]})), None);
    }
    #[test]
    fn hash_dir_is_order_independent_and_skips_pycache_tests_fixture() {
        let d = tempfile::tempdir().unwrap();
        std::fs::write(d.path().join("b.py"), b"B").unwrap();
        std::fs::write(d.path().join("a.py"), b"A").unwrap();
        std::fs::create_dir_all(d.path().join("__pycache__")).unwrap();
        std::fs::write(d.path().join("__pycache__/x.pyc"), b"junk").unwrap();
        let h1 = hash_dir(d.path()).unwrap();
        std::fs::write(d.path().join("__pycache__/y.pyc"), b"more").unwrap();
        assert_eq!(h1, hash_dir(d.path()).unwrap());
        // Known answer, so the Python side (Task 4) can assert the same string.
        assert_eq!(
            h1,
            "sha256:".to_string() + &hex::encode(sha2::Sha256::digest(b"a.py\0A\0b.py\0B\0"))
        );
    }
    #[test]
    fn hash_dir_walks_subdirectories_with_forward_slashes() {
        let d = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(d.path().join("lib")).unwrap();
        std::fs::create_dir_all(d.path().join("tests")).unwrap();
        std::fs::write(d.path().join("lib/m.py"), b"M").unwrap();
        std::fs::write(d.path().join("tests/t.py"), b"T").unwrap();
        assert_eq!(
            hash_dir(d.path()).unwrap(),
            "sha256:".to_string() + &hex::encode(sha2::Sha256::digest(b"lib/m.py\0M\0"))
        );
    }
}
