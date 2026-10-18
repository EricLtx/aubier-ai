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
    /// A desktop application driven through its COM object model; the
    /// application's process is not in the driver's Job (spike C).
    Com,
}

/// Where the driver's process must live. In the manifest, so in `driver_id`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum ExecutionContext {
    #[default]
    Interactive,
    Service,
}

impl ExecutionContext {
    pub fn is_default(&self) -> bool {
        *self == ExecutionContext::Interactive
    }
}

/// Where the tree the kernel mounts comes from. Verified by path, never
/// trusted as declared; not part of `driver_id`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum DriverSource {
    #[default]
    Development,
    Installed,
}

impl DriverSource {
    pub fn is_default(&self) -> bool {
        *self == DriverSource::Development
    }
}

/// The process the host spawns for this driver.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct DriverProgram {
    /// `python-playwright` for the pilot; informative, part of the identity.
    pub runtime: String,
    /// argv the host spawns. Relative paths are relative to the manifest
    /// file's directory (the mount's `root`, pilot ruling R6), so the
    /// identity does not vary per machine; the process runs in the driver's
    /// work directory.
    pub command: Vec<String>,
    /// `hash_dir` of the driver's directory (see [`hash_dir`]).
    pub files_sha256: String,
    pub browser_channel: Option<String>,
    pub headless: bool,
    /// The committed-memory cap on the driver's whole process tree, in bytes,
    /// at least 256 MiB (`MIN_DRIVER_MEMORY`). Absent, the daemon applies
    /// the kind's default (6 GiB for a browser: a headed Chrome with two
    /// tabs, Node and Python). A browser starved by its cap fails
    /// allocations inside the page ("Aw, Snap") or dies; the remedy is a
    /// larger value and a remount. Part of the identity when present; not
    /// serialised when absent, so a manifest that never named it keeps its
    /// `driver_id`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[schemars(range(min = 268_435_456))]
    pub max_memory_bytes: Option<u64>,
}

/// The least `program.max_memory_bytes` a manifest may name: 256 MiB. A
/// floor, not a recommendation: it refuses a cap written in the wrong unit
/// (MiB where bytes are meant) before a browser is starved by it.
pub const MIN_DRIVER_MEMORY: u64 = 256 * 1024 * 1024;

/// One action the driver implements, and the shape of what it may return.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
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

/// Unknown fields are refused at parse time: a stray `token` or a typo such as
/// `to_registre` must fail loudly, not be dropped before `validate` can see it.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
// `validate` refuses a headed service driver; the schema says it too, so a
// manifest written by hand is refused before it reaches the kernel.
#[schemars(extend(
    "if" = {
        "properties": { "execution_context": { "const": "service" } },
        "required": ["execution_context"]
    },
    "then" = {
        "properties": { "program": { "properties": { "headless": { "const": true } } } }
    },
    // Per kind: what `validate` refuses about `applications`, the schema
    // refuses too, so a manifest written by hand fails before the kernel.
    "allOf" = [
        {
            "if": { "properties": { "kind": { "const": "com" } }, "required": ["kind"] },
            "then": { "required": ["applications"], "properties": { "applications": { "minItems": 1 } } }
        },
        {
            "if": { "properties": { "kind": { "const": "browser" } }, "required": ["kind"] },
            "then": { "properties": { "applications": { "maxItems": 0 } } }
        }
    ]
))]
pub struct DriverManifest {
    pub name: String,
    pub kind: DriverKind,
    pub program: DriverProgram,
    /// Host names only (no scheme, no port, no path), lower-case.
    pub hosts: Vec<String>,
    /// Executable names of the applications a `com` driver drives, as Windows
    /// reports them (`WINWORD.EXE`); empty, and not serialised, for a browser
    /// driver, so every manifest written before this field keeps its id.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub applications: Vec<String>,
    pub actions: Vec<DriverAction>,
    pub min_interval_ms: u64,
    pub max_action_ms: u64,
    pub clearance: Clearance,
    #[serde(default, skip_serializing_if = "ExecutionContext::is_default")]
    pub execution_context: ExecutionContext,
    /// The driver drives the UI by keyboard, mouse or UIA and needs the input
    /// desktop unlocked. Recorded now, enforced when the agent arrives.
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub needs_input_desktop: bool,
    #[serde(default, skip_serializing_if = "DriverSource::is_default")]
    pub source: DriverSource,
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

/// The driver name rule: 1 to 32 characters of `a-z`, `0-9` and `-`. A name
/// is a path component (`<state_dir>/drivers/<name>`, the install root's
/// `<name>/<version>`), so it is checked wherever one is read.
pub fn is_driver_name(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 32
        && s.bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
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

impl DriverAction {
    /// Hold `args` to this action's JSON Schema (pilot spec §4.3, item 3): the
    /// first failure, in the schema library's own words, so the refusal names
    /// the field. The one place in the workspace that knows the library; the
    /// kernel's `driver_act_begin` calls this and nothing else.
    ///
    /// Never panics: a schema that does not compile is a refusal like any
    /// other (`jsonschema::validate` would `expect` on it — under the kernel
    /// lock). `DriverManifest::validate` compiles every schema at mount, so
    /// this arm is for a row that reached the table some other way.
    pub fn validate_args(&self, args: &Value) -> Result<(), String> {
        jsonschema::validator_for(&self.args)
            .map_err(|e| format!("schema does not compile: {e}"))?
            .validate(args)
            .map_err(|e| e.to_string())
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
        m.source = DriverSource::Development;
        hash_canonical(&m)
    }

    /// Every refusal names its remedy. Checked in this order: name, command,
    /// `files_sha256`, the memory cap's floor, the service context's headless
    /// requirement, hosts or applications (by kind), actions (count, duplicates, names,
    /// `args`, evidence), the interval ceiling, and last the credential-shaped keys.
    pub fn validate(&self) -> Result<(), DriverError> {
        if !is_driver_name(&self.name) {
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
        if let Some(cap) = self.program.max_memory_bytes {
            if cap < MIN_DRIVER_MEMORY {
                return invalid(format!(
                    "program.max_memory_bytes ({cap}) is below {MIN_DRIVER_MEMORY} (256 MiB): \
                     name a larger cap, or leave it out for the kind's default"
                ));
            }
        }
        if self.execution_context == ExecutionContext::Service && !self.program.headless {
            return invalid(
                "execution_context service requires program.headless true: session 0 has no desktop",
            );
        }
        match self.kind {
            DriverKind::Browser => {
                if self.hosts.is_empty() {
                    return invalid("hosts is empty: list the host names the driver may reach");
                }
                if !self.applications.is_empty() {
                    return invalid(
                        "applications must be empty for a browser driver: it drives pages, not programs",
                    );
                }
            }
            DriverKind::Com => {
                if self.applications.is_empty() {
                    return invalid(
                        "applications is empty: name the executables a com driver drives (WINWORD.EXE)",
                    );
                }
                for a in &self.applications {
                    if a.contains('\\') || a.contains('/') {
                        return invalid(format!("application `{a}`: an executable name, no path"));
                    }
                    let stem = a.strip_suffix(".EXE").unwrap_or("");
                    let one_token = !stem.is_empty()
                        && stem.bytes().all(|b| {
                            b.is_ascii_uppercase() || b.is_ascii_digit() || b == b'_' || b == b'-'
                        });
                    if !one_token {
                        return invalid(format!(
                            "application `{a}`: upper-case executable name ending in .EXE, as Windows reports it"
                        ));
                    }
                    if self.applications.iter().filter(|b| *b == a).count() > 1 {
                        return invalid(format!(
                            "applications repeats `{a}`: name each executable once"
                        ));
                    }
                }
            }
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
            // Compiled here, at mount, so a schema the library will not take
            // (`"type": "strnig"`) is refused with its remedy and never reaches
            // `validate_args` under the kernel lock.
            if let Err(e) = jsonschema::validator_for(&a.args) {
                return invalid(format!(
                    "action `{}`: args schema does not compile ({e}); \
                     correct it in the manifest before mounting",
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
/// forward slashes; any path with a component equal to `__pycache__`, `.venv`,
/// `tests` or `fixture`, at any depth, is skipped, and so is any top-level file
/// named `manifest*.json` (the manifest carries this very hash, so it cannot be
/// part of it); the list is sorted by path; `path\0bytes\0` is fed to SHA-256
/// for each entry. Directory order, build debris and per-machine directories do
/// not change it.
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
            if rel
                .iter()
                .any(|c| matches!(c.as_str(), "__pycache__" | ".venv" | "tests" | "fixture"))
            {
                continue;
            }
            let kind = entry.file_type()?;
            if kind.is_dir() {
                walk(root, &path, out)?;
            } else if kind.is_file() {
                let top_manifest =
                    rel.len() == 1 && rel[0].starts_with("manifest") && rel[0].ends_with(".json");
                if top_manifest {
                    continue;
                }
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
    /// SHA-256 (`sha256:` + hex) of the profile directory's **path** as the
    /// kernel composes it (`<state_dir>/drivers/<name>/profile`), not of its
    /// contents (Ruling R11): a browser profile changes on every run and a
    /// populated one is hundreds of MB with locked files, so a content hash
    /// would be meaningless and could fail the remount a degraded driver's
    /// remedy asks for. It says which profile this mount drove.
    pub profile_dir_hash: String,
    pub governed: bool,
    pub by: String,
    /// The digest of the package this mount was installed from; absent for a
    /// development mount and on every row written before packaging.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub package_digest: Option<String>,
    /// Where the mounted tree came from; `development` on every row written
    /// before packaging.
    #[serde(default)]
    pub source: DriverSource,
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
    /// Remote endpoints seen in established TCP connections of the driver's
    /// processes while the action ran — the whole tree under the Job on
    /// Windows, the leader only when ungoverned; copied from the host's
    /// outcome (Ruling R8).
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
    // A frozen copy of the pilot manifest as it was when PILOT_ID was recorded: the live
    // maskai.json changes its files_sha256 with every driver fix, and this test pins the
    // identity rule, not the driver.
    fn manifest() -> DriverManifest {
        serde_json::from_str(include_str!(
            "../../../contracts/examples/driver_manifest/valid/identity-frozen-2026-10-17.json"
        ))
        .unwrap()
    }

    const PILOT_ID: &str =
        "sha256:174427596817f4843dbe1d6a0f618037430c0225b86becc7f2f206edbeccf142";

    #[test]
    fn the_pilot_manifest_keeps_its_id_with_the_new_fields_absent() {
        let m = manifest();
        assert_eq!(m.execution_context, ExecutionContext::Interactive);
        assert!(!m.needs_input_desktop);
        assert_eq!(m.source, DriverSource::Development);
        assert!(
            m.applications.is_empty(),
            "absent is empty, and never serialised"
        );
        assert_eq!(m.driver_id(), PILOT_ID);
    }

    fn word_com() -> DriverManifest {
        serde_json::from_str(include_str!(
            "../../../contracts/examples/driver_manifest/valid/word_com.json"
        ))
        .unwrap()
    }

    #[test]
    fn a_com_driver_names_its_applications_and_may_have_no_hosts() {
        let m = word_com();
        assert_eq!(m.kind, DriverKind::Com);
        assert_eq!(m.applications, vec!["WINWORD.EXE"]);
        assert!(m.hosts.is_empty());
        m.validate().unwrap();
        let refuse = |edit: &dyn Fn(&mut DriverManifest), needle: &str| {
            let mut bad = word_com();
            edit(&mut bad);
            let e = bad.validate().unwrap_err().to_string();
            assert!(e.contains(needle), "{needle}: {e}");
        };
        refuse(&|m| m.applications.clear(), "applications is empty");
        refuse(
            &|m| m.applications = vec!["winword.exe".into()],
            "upper-case",
        );
        refuse(
            &|m| m.applications = vec!["WINWORD".into()],
            "ending in .EXE",
        );
        refuse(
            &|m| m.applications = vec!["C:\\WINWORD.EXE".into()],
            "no path",
        );
        refuse(
            &|m| m.applications = vec!["OFFICE/WINWORD.EXE".into()],
            "no path",
        );
        refuse(
            &|m| m.applications = vec!["WIN WORD.EXE".into()],
            "upper-case",
        );
        refuse(
            &|m| m.applications = vec!["WINWORD.EXE".into(), "WINWORD.EXE".into()],
            "repeats",
        );
    }

    #[test]
    fn a_browser_driver_may_not_name_applications() {
        let mut m = manifest();
        m.applications = vec!["WINWORD.EXE".into()];
        let e = m.validate().unwrap_err().to_string();
        assert!(e.contains("applications must be empty"), "{e}");
        let mut m = manifest();
        m.hosts.clear();
        assert!(m
            .validate()
            .unwrap_err()
            .to_string()
            .contains("hosts is empty"));
    }

    #[test]
    fn applications_are_identity_when_present_and_absent_when_empty() {
        let mut m = word_com();
        let id = m.driver_id();
        m.applications.push("POWERPNT.EXE".into());
        assert_ne!(
            m.driver_id(),
            id,
            "another program driven is another driver"
        );
        let text = serde_json::to_string(&manifest()).unwrap();
        assert!(!text.contains("applications"), "{text}");
    }

    #[test]
    fn source_is_not_part_of_the_identity_but_execution_context_is() {
        let mut m = manifest();
        m.source = DriverSource::Installed;
        assert_eq!(m.driver_id(), PILOT_ID, "installed or not, the same driver");
        m.execution_context = ExecutionContext::Service;
        m.program.headless = true;
        assert_ne!(
            m.driver_id(),
            PILOT_ID,
            "a driver moved to another context is another driver"
        );
    }

    #[test]
    fn a_service_driver_must_be_headless() {
        let mut m = manifest();
        m.execution_context = ExecutionContext::Service;
        m.program.headless = false;
        let e = m.validate().unwrap_err().to_string();
        assert!(
            e.contains("execution_context service requires program.headless true"),
            "{e}"
        );
    }

    #[test]
    fn unknown_words_are_refused_at_parse() {
        let text = include_str!(
            "../../../contracts/examples/driver_manifest/valid/identity-frozen-2026-10-17.json"
        )
        .replacen(
            "\"kind\": \"browser\"",
            "\"kind\": \"browser\", \"source\": \"packaged\"",
            1,
        );
        let msg = serde_json::from_str::<DriverManifest>(&text)
            .unwrap_err()
            .to_string();
        assert!(
            msg.contains("unknown variant `packaged`"),
            "the refusal names the word: {msg}"
        );
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
    fn args_are_held_to_the_actions_schema() {
        let m = manifest();
        let a = m.action("joindre_document").unwrap();
        a.validate_args(&serde_json::json!({"case": "N1-1"}))
            .unwrap();
        let e = a
            .validate_args(&serde_json::json!({"case": 3}))
            .unwrap_err();
        assert!(e.contains("string"), "the refusal names the type: {e}");
        assert!(a
            .validate_args(&serde_json::json!({"case": "x", "extra": 1}))
            .is_err());
        assert!(a.validate_args(&serde_json::json!({})).is_err());
        // A schema the library will not compile is a refusal, never a panic.
        let broken = DriverAction {
            args: serde_json::json!({
                "type": "object",
                "properties": { "case": { "type": "strnig" } }
            }),
            ..a.clone()
        };
        let e = broken
            .validate_args(&serde_json::json!({"case": "x"}))
            .unwrap_err();
        assert!(e.contains("does not compile"), "{e}");
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
            &|m| {
                m.actions[0].args = serde_json::json!({
                    "type": "object",
                    "properties": { "case": { "type": "strnig" } }
                })
            },
            "does not compile",
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
    fn hash_dir_skips_per_machine_dirs_at_any_depth_and_top_level_manifests_only() {
        let d = tempfile::tempdir().unwrap();
        let put = |p: &str, b: &[u8]| {
            let f = d.path().join(p);
            std::fs::create_dir_all(f.parent().unwrap()).unwrap();
            std::fs::write(f, b).unwrap();
        };
        put("a.py", b"A");
        put("sub/m.py", b"M");
        let base = hash_dir(d.path()).unwrap();
        put("sub/__pycache__/x.pyc", b"junk");
        put(".venv/lib/x.py", b"venv");
        put("sub/.venv/y.py", b"venv");
        put("sub/tests/t.py", b"T");
        put("manifest.json", b"{}");
        put("manifest-fixture.json", b"{}");
        assert_eq!(
            base,
            hash_dir(d.path()).unwrap(),
            "debris and manifests are not identity"
        );
        put("sub/manifest.json", b"{}");
        assert_ne!(
            base,
            hash_dir(d.path()).unwrap(),
            "a nested manifest.json is a driver file"
        );
    }
    #[test]
    fn a_stray_key_or_a_typo_is_refused_at_parse_time() {
        let mut v = serde_json::to_value(manifest()).unwrap();
        v["program"]["token"] = serde_json::json!("abc");
        let e = serde_json::from_value::<DriverManifest>(v)
            .unwrap_err()
            .to_string();
        assert!(e.contains("token"), "{e}");
        let mut v = serde_json::to_value(manifest()).unwrap();
        v["api_key"] = serde_json::json!("abc");
        assert!(serde_json::from_value::<DriverManifest>(v).is_err());
        let mut v = serde_json::to_value(manifest()).unwrap();
        v["actions"][0]["to_registre"] = serde_json::json!(true);
        assert!(serde_json::from_value::<DriverManifest>(v).is_err());
    }
    #[test]
    fn the_invalid_secret_in_program_example_is_refused_for_its_token() {
        let e = serde_json::from_str::<DriverManifest>(include_str!(
            "../../../contracts/examples/driver_manifest/invalid/secret_in_program.json"
        ))
        .unwrap_err()
        .to_string();
        assert!(e.contains("token"), "{e}");
    }
    /// Final review I2: the memory cap on the driver's tree is the manifest's
    /// to say. Absent, it is `None` — the pool picks the kind's default — and
    /// leaves the identity of a manifest that never named it unchanged; named,
    /// it is part of the identity; below 256 MiB it is refused with its remedy.
    #[test]
    fn max_memory_bytes_is_optional_part_of_the_identity_and_has_a_floor() {
        let m = manifest();
        assert_eq!(
            m.program.max_memory_bytes,
            Some(6 * 1024 * 1024 * 1024),
            "the contract example says 6 GiB explicitly"
        );
        let mut v = serde_json::to_value(&m).unwrap();
        v["program"]
            .as_object_mut()
            .unwrap()
            .remove("max_memory_bytes");
        let absent: DriverManifest = serde_json::from_value(v).unwrap();
        assert_eq!(absent.program.max_memory_bytes, None);
        absent.validate().unwrap();
        // The identity is the hash of these bytes: with the field absent they
        // are the bytes of the manifest before the field existed, so an older
        // manifest keeps its `driver_id`.
        let bytes = serde_json::to_string(&absent).unwrap();
        assert!(!bytes.contains("max_memory_bytes"), "{bytes}");
        assert_ne!(absent.driver_id(), m.driver_id(), "a named cap is identity");

        let mut low = m.clone();
        low.program.max_memory_bytes = Some(256 * 1024 * 1024 - 1);
        let e = low.validate().unwrap_err().to_string();
        assert!(e.contains("max_memory_bytes"), "{e}");
        assert!(e.contains("268435456"), "the refusal names the floor: {e}");
        low.program.max_memory_bytes = Some(256 * 1024 * 1024);
        low.validate().unwrap();
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
