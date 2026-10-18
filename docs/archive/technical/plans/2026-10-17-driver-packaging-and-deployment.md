# Driver Packaging and Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A driver leaves the development tree as a signed OCI-layout package that embeds its interpreter and libraries, installs under `%ProgramFiles%\Aubier\drivers\<name>\<version>\`, and mounts from there with its whole tree fingerprinted before every spawn.

**Architecture:** A new library crate `vk-capital` owns the package format (OCI layout, Ed25519 signature by a publisher key, lockfile, embedded CPython, installed fingerprint with a `(size, mtime)` cache). `vk-contracts` gains the manifest fields, payloads and the `DriverPackage` type. The kernel learns the publisher key, the human act `driver.pack`, mounting from an installed tree and the `tampered` degradation. `vk-cli` gains the verbs; `vk driver install|uninstall` run without the daemon in an elevated shell. `vk-driver` gets the `DriverLink` seam (`LocalLink` only). One Windows CI job packs, installs and mounts the fixture driver.

**Tech Stack:** Rust (MSVC), `ed25519-dalek` 2.2, `sha2`, `tar` 0.4, `flate2`, `walkdir`, `reqwest` blocking (already in the workspace), `keyring` 3.6; python-build-standalone `install_only` CPython 3.13 for Windows x86_64; `pip download` for wheels.

**Spec:** `docs/technical/specs/2026-10-16-driver-packaging-and-deployment-design.md` (decisions D1–D10, contracts §4, flows §5, failure modes §6, tests §7, exit criteria §8). The pilot spec `docs/technical/specs/2026-10-14-device-driver-contract-and-recette-pilot-design.md` and its rulings R5, R6, R15, R25, R27, R28 stay binding.

## Global Constraints

- Every refusal is one line ending in a remedy, in the kernel's voice (`remedy: …`), never a third party's text on the ledger (R28, final review I1).
- A human act is a request with a presence proof signed by the node's device key (`PresenceProof`, `vk-ipc/src/lib.rs`); nothing signs a payload. `driver.pack` follows `driver.mount`.
- The ledger kind of every new row is `Operational90d` (the only class in use); `driver.packed` is added to `ALLOWED_KINDS` with its test.
- `driver_id` of every committed manifest must not change: new manifest fields are absent-by-default and excluded from the canonical hash when at their default; `source` is always excluded (like `governed`).
- The fingerprint of an installed tree is `hash_tree`: every regular file at any depth, path-sorted, framed `path\0bytes\0` like `hash_dir`, excluding `__pycache__` directories, top-level `manifest*.json` and the top-level `.package/` directory. Links are never followed.
- The install root is `%ProgramFiles%\Aubier\drivers` (`std::env::var("ProgramFiles")`), overridable by `VK_DRIVER_INSTALL_ROOT` (the test seam, documented as such; the daemon reads it at start and prints it in `boot.info`).
- The embedded interpreter is one pinned python-build-standalone release, Windows x86_64, `install_only`, verified against its upstream SHA-256 before extraction. Wheels are installed into that interpreter's own `Lib\site-packages` with `--no-index --no-compile --no-warn-script-location --find-links <wheels>`; **no venv directory is created** (plan ruling P-1: the spec's `venv\` wording is replaced by direct installation into the embedded interpreter, which keeps D5 — everything embedded and hashed — and removes the relative-`pyvenv.cfg` risk). The packed manifest's command is `["python/python.exe", "-I", "driver.py"]`.
- Console-script launchers that `pip` writes under `python\Scripts\` carry the absolute path of the interpreter; `materialise` deletes every file under `python\Scripts\` that the pristine release did not ship, so the fingerprint is the same in any directory.
- Network is allowed only in `vk driver pack` (the CPython download and `pip download`); every other verb runs offline, tested with no network.
- Windows paths: no path that can exceed 260 characters inside the install tree (`python\` is at the tree root; wheels unpack flat).
- Model tiering (founder rule): Sonnet for tasks 1, 2, 3, 5, 10, 11; Opus for 4, 6, 7, 8, 9; Fable for the final whole-branch review. Each task gets a fresh reviewer before the next starts.
- Commit messages: conventional prefix, one sentence of intent, trailers `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01UUDLpkwxsZXmLptqCeNL2R`.

## Review Focus

1. **A package built on one machine installs on another with a different Python cache or temp path** — the fingerprint must match byte for byte; Task 6's two-directory test and the launcher purge pin this.
2. **An installed tree is rewritten while a driver is mounted and idle** — the next act must refuse and degrade with `tampered`, never run the rewritten code; Task 8's test tampers between two acts.
3. **A bare manifest that claims `source: installed`, or an installed manifest moved under the user's profile** — mount refused with the remedy; Task 7's tests cover both directions.
4. **`vk driver install` run from a non-elevated shell, or against a target that already exists** — nothing written, one-line refusal; Task 9's tests use `VK_DRIVER_INSTALL_ROOT` on a read-only directory and on an existing version.
5. **The publisher key id in the signature matches a pinned key but the signature does not verify** — refused with no TOFU prompt; Task 5 and Task 9 test it.

---

### Task 0: The `vk-capital` crate skeleton

**Files:**
- Create: `crates/vk-capital/Cargo.toml`, `crates/vk-capital/src/lib.rs`
- Modify: `Cargo.toml` (workspace members and `[workspace.dependencies]`)

**Interfaces:**
- Produces: the crate `vk_capital` with modules `fingerprint`, `layout`, `signature`, `python`, `wheels`, `materialise`, `install` (empty `pub mod` declarations are created by the tasks that fill them; this task creates the crate and the `error` module only).

- [ ] **Step 1: Add the crate to the workspace**

`Cargo.toml` (workspace root), in `members`, add `"crates/vk-capital"`. In `[workspace.dependencies]` add, keeping the alphabetical order the file uses:

```toml
flate2 = "1"
tar = "0.4"
walkdir = "2"
```

(`sha2`, `ed25519-dalek`, `base64`, `serde`, `serde_json`, `thiserror`, `reqwest`, `tempfile`, `keyring` are already workspace dependencies — reuse them with `workspace = true`.)

- [ ] **Step 2: Create the crate**

`crates/vk-capital/Cargo.toml`:

```toml
[package]
name = "vk-capital"
version = "0.1.0"
edition = "2021"
publish = false
description = "The capital package: OCI layout, publisher signature, installed fingerprint (first component: drivers)"

[dependencies]
vk-contracts = { path = "../vk-contracts" }
serde = { workspace = true }
serde_json = { workspace = true }
sha2 = { workspace = true }
ed25519-dalek = { workspace = true, features = ["rand_core"] }
rand = { workspace = true }
base64 = { workspace = true }
thiserror = { workspace = true }
tar = { workspace = true }
flate2 = { workspace = true }
walkdir = { workspace = true }
reqwest = { workspace = true }
keyring = { workspace = true }

[dev-dependencies]
tempfile = { workspace = true }
```

`crates/vk-capital/src/lib.rs`:

```rust
//! The capital package, first component: a driver that leaves the
//! development tree as a signed OCI image layout and comes back as an
//! installed, fully hashed tree. Depends on `vk-contracts` only.
//!
//! OS analogy: a package is a firmware image — everything the supervisor
//! will hash and run, nothing that depends on the board.

pub mod error;

pub use error::CapitalError;
```

`crates/vk-capital/src/error.rs`:

```rust
#[derive(Debug, thiserror::Error)]
pub enum CapitalError {
    #[error("{0}")]
    Refused(String),
    #[error("io: {0}")]
    Io(#[from] std::io::Error),
    #[error("json: {0}")]
    Json(#[from] serde_json::Error),
}

pub type Result<T> = std::result::Result<T, CapitalError>;

/// A refusal in the kernel's voice: what was wrong, then `remedy: …`.
pub fn refuse<T>(what: impl Into<String>, remedy: &str) -> Result<T> {
    Err(CapitalError::Refused(format!("{}; remedy: {remedy}", what.into())))
}
```

- [ ] **Step 3: Build and commit**

Run: `cargo build --locked -p vk-capital`
Expected: compiles with no warnings.

```bash
git add Cargo.toml Cargo.lock crates/vk-capital
git commit -m "feat(vk-capital): the capital crate, empty but for its error type"
```

---

### Task 1: Manifest fields `execution_context`, `needs_input_desktop`, `source` (identity preserved)

**Files:**
- Modify: `crates/vk-contracts/src/driver.rs` (types at 14-85, `driver_id` at 156-160, `validate` at 166-268, tests at 380+)
- Modify: `contracts/schemas/driver_manifest.schema.json` (regenerated)
- Create: `contracts/examples/driver_manifest/valid/service_headless.json`, `contracts/examples/driver_manifest/invalid/service_headed.json`, `contracts/examples/driver_manifest/invalid/unknown_source.json`
- Test: `crates/vk-contracts/src/driver.rs` (unit tests), `crates/vk-contracts/tests/examples_validate.rs` (runs as is)

**Interfaces:**
- Produces: `pub enum ExecutionContext { Interactive, Service }` (snake_case), `pub enum DriverSource { Development, Installed }` (snake_case), fields on `DriverManifest`: `#[serde(default, skip_serializing_if = "ExecutionContext::is_default")] pub execution_context: ExecutionContext`, `#[serde(default, skip_serializing_if = "std::ops::Not::not")] pub needs_input_desktop: bool`, `#[serde(default, skip_serializing_if = "DriverSource::is_default")] pub source: DriverSource`. `driver_id()` also resets `source` to `Development` before hashing.

- [ ] **Step 1: Record the current identity of the committed example (throwaway)**

Add temporarily to the tests of `driver.rs`:

```rust
#[test]
fn print_current_id() {
    let m: DriverManifest = serde_json::from_str(include_str!("../../../contracts/examples/driver_manifest/valid/maskai.json")).unwrap();
    panic!("{}", m.driver_id());
}
```

Run: `cargo test --locked -p vk-contracts print_current_id`
Expected: FAIL with the panic message `sha256:<64 hex>`. Copy that string; it is `PILOT_ID` below. Delete the throwaway test.

- [ ] **Step 2: Write the failing tests**

```rust
const PILOT_ID: &str = "sha256:<the string from step 1>";

#[test]
fn the_pilot_manifest_keeps_its_id_with_the_new_fields_absent() {
    let m: DriverManifest = serde_json::from_str(include_str!("../../../contracts/examples/driver_manifest/valid/maskai.json")).unwrap();
    assert_eq!(m.execution_context, ExecutionContext::Interactive);
    assert!(!m.needs_input_desktop);
    assert_eq!(m.source, DriverSource::Development);
    assert_eq!(m.driver_id(), PILOT_ID);
}

#[test]
fn source_is_not_part_of_the_identity_but_execution_context_is() {
    let mut m: DriverManifest = serde_json::from_str(include_str!("../../../contracts/examples/driver_manifest/valid/maskai.json")).unwrap();
    m.source = DriverSource::Installed;
    assert_eq!(m.driver_id(), PILOT_ID, "installed or not, the same driver");
    m.execution_context = ExecutionContext::Service;
    m.program.headless = true;
    assert_ne!(m.driver_id(), PILOT_ID, "a driver moved to another context is another driver");
}

#[test]
fn a_service_driver_must_be_headless() {
    let mut m: DriverManifest = serde_json::from_str(include_str!("../../../contracts/examples/driver_manifest/valid/maskai.json")).unwrap();
    m.execution_context = ExecutionContext::Service;
    m.program.headless = false;
    let e = m.validate().unwrap_err().to_string();
    assert!(e.contains("execution_context service requires program.headless true"), "{e}");
}

#[test]
fn unknown_words_are_refused_at_parse() {
    let text = include_str!("../../../contracts/examples/driver_manifest/valid/maskai.json")
        .replacen("\"kind\": \"browser\"", "\"kind\": \"browser\", \"source\": \"packaged\"", 1);
    assert!(serde_json::from_str::<DriverManifest>(&text).is_err());
}
```

- [ ] **Step 3: Run them to see them fail**

Run: `cargo test --locked -p vk-contracts the_pilot_manifest_keeps_its_id -- --nocapture`
Expected: compile error (`ExecutionContext` not defined).

- [ ] **Step 4: Implement**

In `driver.rs`, after `DriverKind`:

```rust
/// Where the driver's process must live. In the manifest, so in `driver_id`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum ExecutionContext {
    #[default]
    Interactive,
    Service,
}

impl ExecutionContext {
    pub fn is_default(&self) -> bool { *self == ExecutionContext::Interactive }
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
    pub fn is_default(&self) -> bool { *self == DriverSource::Development }
}
```

Add to `DriverManifest`, after `clearance`:

```rust
    #[serde(default, skip_serializing_if = "ExecutionContext::is_default")]
    pub execution_context: ExecutionContext,
    /// The driver drives the UI by keyboard, mouse or UIA and needs the input
    /// desktop unlocked. Recorded now, enforced when the agent arrives.
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub needs_input_desktop: bool,
    #[serde(default, skip_serializing_if = "DriverSource::is_default")]
    pub source: DriverSource,
```

In `driver_id()`:

```rust
        let mut m = self.clone();
        m.governed = false;
        m.source = DriverSource::Development;
        hash_canonical(&m)
```

In `validate()`, after the `max_memory_bytes` check:

```rust
        if self.execution_context == ExecutionContext::Service && !self.program.headless {
            return invalid("execution_context service requires program.headless true: session 0 has no desktop");
        }
```

Every place that constructs a `DriverManifest` literal (kernel tests `maskai_like()`, `vk-ipc/tests/drivers.rs manifest()`, `vk-cli/tests/smoke.rs`, `vk-driver/tests/fake_driver.rs Rig::manifest`) gets the three fields with `..Default::default()` not available (the struct has no `Default`): add `execution_context: ExecutionContext::Interactive, needs_input_desktop: false, source: DriverSource::Development,` to each literal. `rg "min_interval_ms:" crates` lists them.

- [ ] **Step 5: Examples and schema**

`contracts/examples/driver_manifest/valid/service_headless.json`: a copy of `maskai.json` with `"execution_context": "service"` and `"headless": true`. `invalid/service_headed.json`: `"execution_context": "service"` with `"headless": false`. `invalid/unknown_source.json`: `"source": "packaged"`.

Run: `cargo run -p vk-contracts --bin gen-schemas`
Then: `cargo test --locked -p vk-contracts`
Expected: all green, including `committed_schemas_match_generated` and `every_example_validates_as_its_folder_says`.

- [ ] **Step 6: Commit**

```bash
git add crates contracts
git commit -m "feat(contracts): execution_context, needs_input_desktop and source on the driver manifest; identities unchanged"
```

---

### Task 2: `DriverPackage`, `driver_packed`, `driver_mounted` extensions, `tampered`, `driver.packed` kind

**Files:**
- Create: `crates/vk-contracts/src/package.rs`
- Modify: `crates/vk-contracts/src/driver.rs` (`DriverMounted` at 329-343), `crates/vk-contracts/src/lib.rs` (`schema_registry` 59-71, `pub mod package`), `crates/vk-contracts/src/ledger.rs` (`ALLOWED_KINDS` 286-291 + test), `crates/vk-kernel/src/drivers.rs` (`DRIVER_FAILURE_REASONS` 142-158 + test 951-1016), `crates/vk-cli/src/man.rs` (`SCHEMAS` 37-52), `contracts/README.md` §Drivers, `docs/technical/technical-readme.md` ~426, `contracts/tcb.md` §Drivers
- Create: `contracts/examples/driver_package/valid/fixture.json`, `contracts/examples/driver_packed/valid/first.json`, `contracts/schemas/{driver_package,driver_packed}.schema.json` (generated)

**Interfaces:**
- Produces (in `vk_contracts::package`):

```rust
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

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Descriptor {
    #[serde(rename = "mediaType")] pub media_type: String,
    pub digest: String,
    pub size: u64,
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub annotations: BTreeMap<String, String>,
}

/// The OCI image manifest of a driver package: one layer per component.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct DriverPackage {
    #[serde(rename = "schemaVersion")] pub schema_version: u32, // 2
    #[serde(rename = "mediaType")] pub media_type: String,     // OCI_MANIFEST_TYPE
    #[serde(rename = "artifactType")] pub artifact_type: String, // DRIVER_ARTIFACT_TYPE
    pub config: Descriptor,                                     // the empty descriptor
    pub layers: Vec<Descriptor>,                                // COMPONENTS order
    pub annotations: BTreeMap<String, String>,
}

impl DriverPackage {
    pub fn validate(&self) -> Result<(), String>;   // schema_version 2, types, five layers in order with the right media types and `org.aubier.component` annotations, required annotations present
    pub fn layer(&self, component: &str) -> Option<&Descriptor>;
    pub fn name(&self) -> &str;     // annotation org.aubier.driver.name
    pub fn version(&self) -> &str;  // org.aubier.driver.version
    pub fn driver_id(&self) -> &str;
    pub fn publisher_key_id(&self) -> &str;
}

pub const ANN_NAME: &str = "org.aubier.driver.name";
pub const ANN_VERSION: &str = "org.aubier.driver.version";
pub const ANN_DRIVER_ID: &str = "org.aubier.driver.id";
pub const ANN_CREATED: &str = "org.aubier.package.created";
pub const ANN_PUBLISHER: &str = "org.aubier.publisher.key_id";
pub const ANN_COMPONENT: &str = "org.aubier.component";
pub const ANN_PYTHON_SHA256: &str = "org.aubier.python.upstream_sha256";
pub const ANN_PYTHON_RELEASE: &str = "org.aubier.python.release";

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Signature {
    pub key_id: String,
    pub algorithm: String, // "ed25519"
    pub signed: String,    // the image manifest digest, sha256:…
    pub signature: String, // base64
}

/// `driver.packed` — a human act, written by the node that packs.
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
```

- `DriverMounted` gains `#[serde(default, skip_serializing_if = "Option::is_none")] pub package_digest: Option<String>` and `#[serde(default)] pub source: DriverSource`.
- `DRIVER_FAILURE_REASONS` gains `"tampered"`; `ALLOWED_KINDS` gains `"driver.packed"`.

- [ ] **Step 1: Failing tests** (in `package.rs`)

```rust
#[cfg(test)]
mod tests {
    use super::*;
    fn fixture() -> DriverPackage {
        serde_json::from_str(include_str!("../../../contracts/examples/driver_package/valid/fixture.json")).unwrap()
    }
    #[test]
    fn the_fixture_package_validates_and_names_its_five_layers() {
        let p = fixture();
        p.validate().unwrap();
        for c in COMPONENTS { assert!(p.layer(c).is_some(), "layer {c}"); }
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
```

In `ledger.rs` tests, following `liveness_renewed_is_an_allowed_kind`: `driver_packed_is_an_allowed_kind`. In `vk-kernel/src/drivers.rs` test `host_error_reasons_are_a_closed_vocabulary`, add `"tampered"` to the expected list.

- [ ] **Step 2: Run, expect compile failure**

Run: `cargo test --locked -p vk-contracts package`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement** `package.rs` as the interface block above, with `validate()`:

```rust
    pub fn validate(&self) -> Result<(), String> {
        if self.schema_version != 2 { return Err("schemaVersion must be 2".into()); }
        if self.media_type != OCI_MANIFEST_TYPE { return Err(format!("mediaType must be {OCI_MANIFEST_TYPE}")); }
        if self.artifact_type != DRIVER_ARTIFACT_TYPE { return Err(format!("artifactType must be {DRIVER_ARTIFACT_TYPE}")); }
        if self.config.media_type != OCI_EMPTY_TYPE { return Err("config must be the empty descriptor".into()); }
        let expected = [MEDIA_MANIFEST, MEDIA_FILES, MEDIA_LOCK, MEDIA_WHEELS, MEDIA_PYTHON];
        if self.layers.len() != 5 {
            return Err(format!("five layers in the order {:?}, got {}", COMPONENTS, self.layers.len()));
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
        for a in [ANN_NAME, ANN_VERSION, ANN_DRIVER_ID, ANN_CREATED, ANN_PUBLISHER] {
            if !self.annotations.contains_key(a) { return Err(format!("annotation {a} is required")); }
        }
        if self.layer("python").unwrap().annotations.get(ANN_PYTHON_SHA256).is_none() {
            return Err(format!("the python layer must carry {ANN_PYTHON_SHA256}"));
        }
        Ok(())
    }
```

`fixture.json`: a hand-written image manifest with five layers (digests of 64 zeros are fine for the contract example), annotations `maskai-fixture` / `0.1.0` / a `sha256:` driver id / `2026-10-17T00:00:00Z` / a key id. `driver_packed/valid/first.json`: one row with the seven fields.

Register in `lib.rs`: `("driver_package", schema_for!(package::DriverPackage))`, `("driver_packed", schema_for!(package::DriverPacked))`. Regenerate schemas. Add both to `man.rs` `SCHEMAS` with one-line descriptions. Add the kind and the reason word.

- [ ] **Step 4: Docs** — `contracts/README.md` §Drivers: two sentences (the package manifest and the `driver.packed` row; `driver_mounted` gains `package_digest` and `source`, old rows read with the defaults). `contracts/tcb.md` §Drivers: the §11 sentence "a packed driver's identity covers its interpreter and libraries (R15 closed for `source: installed`)". `docs/technical/technical-readme.md` payload table: two rows.

- [ ] **Step 5: Run everything that touches schemas**

Run: `cargo test --locked -p vk-contracts -p vk-cli --test '*' man` and `cargo test --locked -p vk-kernel host_error_reasons`
Expected: green; `every_schema_in_the_repository_is_in_the_table` passes.

- [ ] **Step 6: Commit**

```bash
git add crates contracts docs
git commit -m "feat(contracts): DriverPackage (OCI image manifest), driver.packed, package_digest and source on driver.mounted, tampered"
```

---
### Task 3: The installed fingerprint and its cache (`vk_capital::fingerprint`)

**Files:**
- Create: `crates/vk-capital/src/fingerprint.rs`
- Modify: `crates/vk-capital/src/lib.rs` (`pub mod fingerprint;`)
- Test: unit tests in the same file

**Interfaces:**
- Produces:

```rust
/// `hash_tree`: every regular file under `root` at any depth, path-sorted
/// (`/`-joined relative paths, bytes compared), framed `path\0bytes\0` into
/// SHA-256 — the framing of `vk_contracts::driver::hash_dir`. Excluded: any
/// `__pycache__` directory, a top-level `manifest*.json`, the top-level
/// `.package/` directory. Links are skipped, never followed.
pub fn hash_tree(root: &Path) -> std::io::Result<String>;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CacheEntry { pub path: String, pub size: u64, pub mtime_ns: i128 }

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FingerprintCache { pub fingerprint: String, pub entries: Vec<CacheEntry> }

/// The stat set of the tree, same exclusions and order as `hash_tree`.
pub fn stat_tree(root: &Path) -> std::io::Result<Vec<CacheEntry>>;

/// `Hit(fingerprint)` when the stat set equals the cache's, else `Rehashed(fingerprint, new_cache)`.
pub enum Check { Hit(String), Rehashed(String, FingerprintCache) }
pub fn check(root: &Path, cache: Option<&FingerprintCache>) -> std::io::Result<Check>;

pub fn load_cache(file: &Path) -> std::io::Result<Option<FingerprintCache>>;
pub fn store_cache(file: &Path, cache: &FingerprintCache) -> std::io::Result<()>;
```

- [ ] **Step 1: Failing tests**

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    fn tree(dir: &Path) {
        fs::create_dir_all(dir.join("python/Lib/__pycache__")).unwrap();
        fs::create_dir_all(dir.join(".package")).unwrap();
        fs::write(dir.join("driver.py"), b"print(1)\n").unwrap();
        fs::write(dir.join("manifest.json"), b"{}").unwrap();
        fs::write(dir.join("manifest-fixture.json"), b"{}").unwrap();
        fs::write(dir.join("python/python.exe"), b"MZ").unwrap();
        fs::write(dir.join("python/Lib/os.py"), b"x").unwrap();
        fs::write(dir.join("python/Lib/__pycache__/os.pyc"), b"y").unwrap();
        fs::write(dir.join(".package/signature.json"), b"{}").unwrap();
    }
    #[test]
    fn the_fingerprint_ignores_pycache_manifests_and_the_package_dir() {
        let a = tempfile::tempdir().unwrap();
        tree(a.path());
        let f1 = hash_tree(a.path()).unwrap();
        fs::write(a.path().join("python/Lib/__pycache__/os.pyc"), b"changed").unwrap();
        fs::write(a.path().join("manifest.json"), b"{\"changed\":1}").unwrap();
        fs::write(a.path().join(".package/signature.json"), b"changed").unwrap();
        assert_eq!(hash_tree(a.path()).unwrap(), f1);
        fs::write(a.path().join("python/Lib/os.py"), b"changed").unwrap();
        assert_ne!(hash_tree(a.path()).unwrap(), f1);
    }
    #[test]
    fn the_same_tree_in_two_directories_has_one_fingerprint() {
        let a = tempfile::tempdir().unwrap();
        let b = tempfile::tempdir().unwrap();
        tree(a.path());
        tree(b.path());
        assert_eq!(hash_tree(a.path()).unwrap(), hash_tree(b.path()).unwrap());
    }
    #[test]
    fn a_nested_manifest_json_is_hashed() {
        let a = tempfile::tempdir().unwrap();
        tree(a.path());
        let f1 = hash_tree(a.path()).unwrap();
        fs::write(a.path().join("python/manifest.json"), b"{}").unwrap();
        assert_ne!(hash_tree(a.path()).unwrap(), f1);
    }
    #[test]
    fn the_cache_hits_on_an_unchanged_stat_set_and_rehashes_on_any_change() {
        let a = tempfile::tempdir().unwrap();
        tree(a.path());
        let Check::Rehashed(f1, cache) = check(a.path(), None).unwrap() else { panic!() };
        assert!(matches!(check(a.path(), Some(&cache)).unwrap(), Check::Hit(f) if f == f1));
        // same size, new mtime: rehash, same fingerprint, new cache
        let p = a.path().join("driver.py");
        fs::write(&p, b"print(1)\n").unwrap();
        let t = filetime_now_plus(2);
        set_mtime(&p, t);
        let Check::Rehashed(f2, cache2) = check(a.path(), Some(&cache)).unwrap() else { panic!("stat changed") };
        assert_eq!(f2, f1);
        assert_ne!(cache2.entries, cache.entries);
        // a byte changed with the mtime preserved: the cache hits — the documented limit (§3.3)
        fs::write(&p, b"print(2)\n").unwrap();
        set_mtime(&p, t);
        assert!(matches!(check(a.path(), Some(&cache2)).unwrap(), Check::Hit(_)));
    }
    #[test]
    fn the_cache_round_trips_through_its_file() {
        let a = tempfile::tempdir().unwrap();
        tree(a.path());
        let Check::Rehashed(_, cache) = check(a.path(), None).unwrap() else { panic!() };
        let f = a.path().join("fingerprint.json");
        store_cache(&f, &cache).unwrap();
        assert_eq!(load_cache(&f).unwrap().unwrap(), cache);
        assert!(load_cache(&a.path().join("absent.json")).unwrap().is_none());
    }
}
```

`set_mtime` / `filetime_now_plus` are small test helpers using `std::fs::File::set_modified` (stable since 1.75) and `SystemTime::now() + Duration::from_secs(n)`.

- [ ] **Step 2: Run to see them fail**

Run: `cargo test --locked -p vk-capital fingerprint`
Expected: compile error.

- [ ] **Step 3: Implement**

```rust
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use sha2::{Digest, Sha256};

fn excluded(rel: &Path) -> bool {
    let mut comps = rel.components();
    let first = comps.next().map(|c| c.as_os_str().to_string_lossy().to_string()).unwrap_or_default();
    let depth = rel.components().count();
    if rel.components().any(|c| c.as_os_str() == "__pycache__") { return true; }
    if depth == 1 && first.starts_with("manifest") && first.ends_with(".json") { return true; }
    if first == ".package" { return true; }
    false
}

fn walk(root: &Path, dir: &Path, out: &mut BTreeMap<Vec<u8>, PathBuf>) -> std::io::Result<()> {
    for entry in std::fs::read_dir(dir)? {
        let entry = entry?;
        let ft = entry.file_type()?;           // does not follow links
        let path = entry.path();
        let rel = path.strip_prefix(root).unwrap().to_path_buf();
        if excluded(&rel) { continue; }
        if ft.is_dir() { walk(root, &path, out)?; }
        else if ft.is_file() {
            let key = rel.components().map(|c| c.as_os_str().to_string_lossy().to_string())
                .collect::<Vec<_>>().join("/").into_bytes();
            out.insert(key, path);
        }
    }
    Ok(())
}

pub fn hash_tree(root: &Path) -> std::io::Result<String> {
    let mut files = BTreeMap::new();
    walk(root, root, &mut files)?;
    let mut h = Sha256::new();
    for (rel, path) in &files {
        h.update(rel); h.update(b"\0");
        h.update(std::fs::read(path)?); h.update(b"\0");
    }
    Ok(format!("sha256:{:x}", h.finalize()))
}

pub fn stat_tree(root: &Path) -> std::io::Result<Vec<CacheEntry>> {
    let mut files = BTreeMap::new();
    walk(root, root, &mut files)?;
    files.iter().map(|(rel, path)| {
        let md = std::fs::metadata(path)?;
        let mtime = md.modified()?.duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos() as i128).unwrap_or(0);
        Ok(CacheEntry { path: String::from_utf8_lossy(rel).into_owned(), size: md.len(), mtime_ns: mtime })
    }).collect()
}

pub fn check(root: &Path, cache: Option<&FingerprintCache>) -> std::io::Result<Check> {
    let entries = stat_tree(root)?;
    if let Some(c) = cache {
        if c.entries == entries { return Ok(Check::Hit(c.fingerprint.clone())); }
    }
    let fingerprint = hash_tree(root)?;
    Ok(Check::Rehashed(fingerprint.clone(), FingerprintCache { fingerprint, entries }))
}
```

`load_cache` returns `Ok(None)` on `NotFound`, else parses; `store_cache` writes pretty JSON to a temp file in the same directory then renames (the atomic-write pattern used by `vk-store`).

- [ ] **Step 4: Run, expect green; commit**

```bash
git add crates/vk-capital
git commit -m "feat(vk-capital): the installed fingerprint over the whole tree, and its (size, mtime) cache"
```

---

### Task 4: The OCI image layout — write, read, verify, tamper detection (`vk_capital::layout`)

**Files:**
- Create: `crates/vk-capital/src/layout.rs`
- Modify: `crates/vk-capital/src/lib.rs`
- Test: unit tests in the file, with small in-memory layers

**Interfaces:**
- Consumes: `vk_contracts::package::{DriverPackage, Descriptor, Signature, constants}`.
- Produces:

```rust
pub struct Layers { pub manifest: Vec<u8>, pub files: Vec<u8>, pub lock: Vec<u8>, pub wheels: Vec<u8>, pub python: Vec<u8> }
pub struct Annotations { pub name: String, pub version: String, pub driver_id: String, pub created: String, pub publisher_key_id: String, pub python_release: String, pub python_upstream_sha256: String }

pub fn digest_of(bytes: &[u8]) -> String;            // sha256:<hex>
pub fn blob_path(layout: &Path, digest: &str) -> PathBuf; // layout/blobs/sha256/<hex>

/// Writes `oci-layout`, the five blobs, the image manifest blob and `index.json`
/// (one entry). Returns the image manifest and its digest. Rejects an existing
/// non-empty `out`.
pub fn write(out: &Path, layers: &Layers, ann: &Annotations) -> Result<(DriverPackage, String)>;

/// Appends the signature referrer: the signature blob, a second image manifest
/// with `subject` = the driver manifest descriptor, and its `index.json` entry.
pub fn write_signature(out: &Path, package_digest: &str, sig: &Signature) -> Result<()>;

pub struct Opened { pub package: DriverPackage, pub package_digest: String, pub signature: Signature, pub layout: PathBuf }
/// Reads `index.json`, finds the one driver manifest and the one signature
/// referrer, checks every blob's size and digest against its descriptor, and
/// the signature's `signed` against the manifest digest. Refuses on the first
/// mismatch, naming the component.
pub fn open(layout: &Path) -> Result<Opened>;

pub fn read_layer(opened: &Opened, component: &str) -> Result<Vec<u8>>;
```

- [ ] **Step 1: Failing tests**

```rust
#[cfg(test)]
mod tests {
    use super::*;
    fn layers() -> Layers {
        Layers { manifest: b"{\"name\":\"x\"}".to_vec(), files: b"FILES".to_vec(), lock: b"[]".to_vec(), wheels: b"WHEELS".to_vec(), python: b"PY".to_vec() }
    }
    fn ann() -> Annotations {
        Annotations { name: "x".into(), version: "1.0.0".into(), driver_id: "sha256:".to_string() + &"0".repeat(64), created: "2026-10-17T00:00:00Z".into(), publisher_key_id: "sha256:".to_string() + &"1".repeat(64), python_release: "cpython-3.13.x".into(), python_upstream_sha256: "2".repeat(64) }
    }
    fn sig(d: &str) -> Signature { Signature { key_id: "sha256:".to_string() + &"1".repeat(64), algorithm: "ed25519".into(), signed: d.into(), signature: "AAAA".into() } }

    #[test]
    fn write_then_open_round_trips_and_reads_every_layer() {
        let t = tempfile::tempdir().unwrap();
        let out = t.path().join("pkg");
        let (pkg, digest) = write(&out, &layers(), &ann()).unwrap();
        pkg.validate().unwrap();
        write_signature(&out, &digest, &sig(&digest)).unwrap();
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
            write_signature(&out, &digest, &sig(&digest)).unwrap();
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
        write_signature(&out, &digest, &sig(&digest)).unwrap();
        let p = blob_path(&out, &digest);
        let mut text = std::fs::read_to_string(&p).unwrap();
        text = text.replace("\"1.0.0\"", "\"9.9.9\"");
        std::fs::write(&p, text).unwrap();
        assert!(open(&out).unwrap_err().to_string().contains("image manifest"));
    }
    #[test]
    fn a_signature_that_names_another_digest_is_refused() {
        let t = tempfile::tempdir().unwrap();
        let out = t.path().join("pkg");
        let (_, digest) = write(&out, &layers(), &ann()).unwrap();
        write_signature(&out, &digest, &sig(&("sha256:".to_string() + &"f".repeat(64)))).unwrap();
        assert!(open(&out).unwrap_err().to_string().contains("signature"));
    }
    #[test]
    fn writing_into_a_non_empty_directory_is_refused() {
        let t = tempfile::tempdir().unwrap();
        std::fs::write(t.path().join("stray"), b"x").unwrap();
        assert!(write(t.path(), &layers(), &ann()).is_err());
    }
}
```

- [ ] **Step 2: Run, expect compile failure.**

- [ ] **Step 3: Implement**

`write`: create `out` (refuse if it exists and is not empty), write `oci-layout` = `{"imageLayoutVersion":"1.0.0"}`; for each component write the blob under `blobs/sha256/<hex>` and build its `Descriptor` with the media type, `ANN_COMPONENT` and, for `python`, `ANN_PYTHON_RELEASE`/`ANN_PYTHON_SHA256`; the config descriptor is the empty blob `{}` (`OCI_EMPTY_TYPE`, size 2, also written as a blob); serialise the `DriverPackage` with `serde_json::to_vec_pretty`, write it as a blob, and `index.json` = `{"schemaVersion":2,"manifests":[{"mediaType":OCI_MANIFEST_TYPE,"artifactType":DRIVER_ARTIFACT_TYPE,"digest":…,"size":…}]}`.

`write_signature`: the signature blob is `serde_json::to_vec_pretty(sig)`; the referrer manifest is a raw `serde_json::Value`: `{"schemaVersion":2,"mediaType":OCI_MANIFEST_TYPE,"artifactType":SIGNATURE_ARTIFACT_TYPE,"config":<empty descriptor>,"layers":[<signature blob descriptor, mediaType "application/vnd.aubier.signature.v1+json">],"subject":{"mediaType":OCI_MANIFEST_TYPE,"digest":package_digest,"size":<size>}}`; append its descriptor to `index.json` `manifests`.

`open`: parse `index.json`; exactly one entry with `artifactType == DRIVER_ARTIFACT_TYPE` and one with `SIGNATURE_ARTIFACT_TYPE`, else refuse `index.json must name one driver manifest and one signature`; read the driver manifest blob, check `digest_of(bytes) == entry.digest` (`image manifest digest mismatch`), parse and `validate()`; for each layer read the blob and check size and digest (`layer <component>: digest mismatch, the package was altered`); read the signature referrer, check its `subject.digest` equals the package digest, parse the signature blob, check `sig.signed == package_digest` (`signature names another digest`). Nothing here verifies the Ed25519 signature: that is Task 5's `verify`, called by the installer with a pinned key.

- [ ] **Step 4: Run, green; commit**

```bash
git add crates/vk-capital
git commit -m "feat(vk-capital): OCI image layout for a driver package, read back with every digest checked"
```

---

### Task 5: Publisher key and signature (`vk_capital::signature`)

**Files:**
- Create: `crates/vk-capital/src/signature.rs`
- Modify: `crates/vk-capital/src/lib.rs`
- Test: unit tests in the file

**Interfaces:**
- Consumes: `ed25519_dalek::{SigningKey, VerifyingKey, Signature as DalekSig, Signer, Verifier}`, `vk_contracts::package::Signature`.
- Produces:

```rust
pub const KEYRING_SERVICE: &str = "vk";
pub const KEYRING_USER: &str = "publisher";

pub enum KeySource { Keyring, File(PathBuf) }

pub struct PublisherKey { signing: SigningKey }
impl PublisherKey {
    /// Creates a key if none exists under the source, else loads it. The
    /// keyring entry is `vk` / `publisher`, a base64 32-byte seed, like the
    /// node device key (`vk-kernel/src/presence.rs::keyring_seed`).
    pub fn load_or_create(source: &KeySource) -> Result<PublisherKey>;
    pub fn load(source: &KeySource) -> Result<Option<PublisherKey>>;
    pub fn key_id(&self) -> String;              // sha256:<hex of the 32 public key bytes>
    pub fn public_pem(&self) -> String;          // "-----BEGIN AUBIER PUBLISHER KEY-----\n<base64 32 bytes>\n-----END AUBIER PUBLISHER KEY-----\n"
    pub fn sign(&self, package_digest: &str) -> Signature; // signs the UTF-8 bytes of "aubier-driver-package|<digest>"
}

pub struct PinnedKey { pub key_id: String, pub verifying: VerifyingKey }
pub fn parse_public_pem(text: &str) -> Result<PinnedKey>;
pub fn key_id_of(verifying: &VerifyingKey) -> String;

/// Verifies `sig` over `package_digest` with `pinned`; refuses on a key id
/// mismatch and on a bad signature, with distinct messages.
pub fn verify(pinned: &PinnedKey, package_digest: &str, sig: &Signature) -> Result<()>;

/// `<state_dir>/capital/publishers/<key_id>.pub` + `<key_id>.json` sidecar.
pub struct Pins { dir: PathBuf }
impl Pins {
    pub fn at(state_dir: &Path) -> Pins;
    pub fn get(&self, key_id: &str) -> Result<Option<PinnedKey>>;
    pub fn pin(&self, key: &PinnedKey, pinned_by: &str) -> Result<()>; // writes .pub and {"pinned_at":<rfc3339>,"pinned_by":…}
    pub fn list(&self) -> Result<Vec<String>>;
}
```

- [ ] **Step 1: Failing tests**

```rust
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
    }
    #[test]
    fn sign_then_verify_with_the_pinned_public_key() {
        let t = tempfile::tempdir().unwrap();
        let k = PublisherKey::load_or_create(&KeySource::File(t.path().join("k"))).unwrap();
        let digest = "sha256:".to_string() + &"a".repeat(64);
        let sig = k.sign(&digest);
        assert_eq!(sig.algorithm, "ed25519");
        assert_eq!(sig.key_id, k.key_id());
        let pinned = parse_public_pem(&k.public_pem()).unwrap();
        verify(&pinned, &digest, &sig).unwrap();
        let other = "sha256:".to_string() + &"b".repeat(64);
        assert!(verify(&pinned, &other, &sig).unwrap_err().to_string().contains("does not verify"));
    }
    #[test]
    fn a_signature_from_another_key_with_the_same_claimed_id_is_refused_without_a_prompt() {
        let t = tempfile::tempdir().unwrap();
        let k1 = PublisherKey::load_or_create(&KeySource::File(t.path().join("k1"))).unwrap();
        let k2 = PublisherKey::load_or_create(&KeySource::File(t.path().join("k2"))).unwrap();
        let digest = "sha256:".to_string() + &"a".repeat(64);
        let mut sig = k2.sign(&digest);
        sig.key_id = k1.key_id();                       // claims k1
        let pinned = parse_public_pem(&k1.public_pem()).unwrap();
        assert!(verify(&pinned, &digest, &sig).unwrap_err().to_string().contains("does not verify"));
        let mut sig = k2.sign(&digest);
        sig.key_id = "sha256:".to_string() + &"c".repeat(64);
        assert!(verify(&pinned, &digest, &sig).unwrap_err().to_string().contains("key id"));
    }
    #[test]
    fn pins_are_files_under_the_state_dir() {
        let t = tempfile::tempdir().unwrap();
        let k = PublisherKey::load_or_create(&KeySource::File(t.path().join("k"))).unwrap();
        let pins = Pins::at(t.path());
        assert!(pins.get(&k.key_id()).unwrap().is_none());
        pins.pin(&parse_public_pem(&k.public_pem()).unwrap(), "S-1-5-21-1").unwrap();
        assert_eq!(pins.get(&k.key_id()).unwrap().unwrap().key_id, k.key_id());
        assert_eq!(pins.list().unwrap(), vec![k.key_id()]);
        let sidecar = std::fs::read_to_string(t.path().join("capital/publishers").join(format!("{}.json", k.key_id().trim_start_matches("sha256:")))).unwrap();
        assert!(sidecar.contains("S-1-5-21-1"));
    }
}
```

- [ ] **Step 2: Run, expect compile failure.**

- [ ] **Step 3: Implement**

The seed handling copies `vk-kernel/src/presence.rs` (`keyring_seed`, base64 32 bytes, `SigningKey::from_bytes`), with `KeySource::File` reading/writing the base64 seed as a private file (`vk_store::paths::private_dir` is not reachable from this crate — write with `std::fs::write` and, on Windows, leave the ACL to the state dir's). `key_id` = `format!("sha256:{:x}", Sha256::digest(verifying.to_bytes()))`. `sign` = base64 of the 64-byte signature over `format!("aubier-driver-package|{package_digest}").as_bytes()`. `verify`: `if sig.key_id != pinned.key_id → refuse("signature key id … is not the pinned key …", "pin the publisher at install, or refuse the package")`; decode base64; `pinned.verifying.verify_strict(msg, &sig)` → `refuse("signature does not verify against the pinned key", "the package was altered or re-signed; refuse it")`. File names under `capital/publishers/` use the hex without the `sha256:` prefix.

- [ ] **Step 4: Run, green; commit**

```bash
git add crates/vk-capital
git commit -m "feat(vk-capital): the publisher key (Ed25519, keyring or file), package signature, and pinned public keys"
```

---

### Task 6: Embedded CPython, wheels, lockfile and `materialise` (`vk_capital::{python,wheels,materialise}`)

**Files:**
- Create: `crates/vk-capital/src/python.rs`, `crates/vk-capital/src/wheels.rs`, `crates/vk-capital/src/materialise.rs`
- Modify: `crates/vk-capital/src/lib.rs`
- Test: `crates/vk-capital/tests/materialise.rs` (needs a real interpreter: the test uses the `uv`-managed or `PYTHON_FOR_TESTS` interpreter as a stand-in release when `VK_TEST_PYTHON_RELEASE` names a local `install_only` archive, and is `#[ignore]`d otherwise; the CI job of Task 11 runs it with the archive it downloads)

**Interfaces:**
- Produces:

```rust
// python.rs
pub struct PythonRelease { pub tag: &'static str, pub file: &'static str, pub sha256: &'static str, pub url: &'static str }
/// The pinned release (plan open item §10, settled here): the latest
/// python-build-standalone 3.13 `install_only` Windows x86_64 archive at
/// implementation time. The implementer fills `tag`, `file`, `sha256` from
/// https://github.com/astral-sh/python-build-standalone/releases (the
/// `SHA256SUMS` asset), e.g. tag `20250918`, file
/// `cpython-3.13.7+20250918-x86_64-pc-windows-msvc-install_only.tar.gz`.
pub const PINNED: PythonRelease = PythonRelease { … };
/// Downloads to `<cache>/<file>` if absent (reqwest blocking, 10 min timeout),
/// checks SHA-256 before anything else reads it, refuses and deletes on mismatch.
pub fn fetch(cache: &Path) -> Result<PathBuf>;
/// Extracts the `.tar.gz` whose top-level directory is `python/` into `dest/python/`.
pub fn extract(archive: &Path, dest: &Path) -> Result<PathBuf>;
/// The set of relative paths the pristine release ships under `python/Scripts/`
/// (read after extraction, before any pip install).
pub fn pristine_scripts(python_dir: &Path) -> Result<BTreeSet<String>>;

// wheels.rs
#[derive(Serialize, Deserialize, PartialEq, Eq, Debug, Clone)]
pub struct Locked { pub name: String, pub version: String, pub filename: String, pub sha256: String }
/// `python -m pip download --only-binary=:all: --no-deps … -r requirements.txt`
/// is NOT used: dependencies must resolve. Runs
/// `<python> -m pip download --only-binary=:all: --dest <wheels> -r <requirements>`
/// (network), then builds the lock from the `.whl` files found (name/version
/// parsed from the filename, SHA-256 computed). Refuses if any non-`.whl` file
/// landed (an sdist means a wheel was missing).
pub fn resolve(python_exe: &Path, requirements: &Path, wheels_dir: &Path) -> Result<Vec<Locked>>;
pub fn tar_dir(dir: &Path) -> Result<Vec<u8>>;      // deterministic: entries path-sorted, mtime 0, uid/gid 0, mode 0644/0755
pub fn untar_into(bytes: &[u8], dest: &Path) -> Result<()>; // refuses any entry that escapes `dest` or is a link

// materialise.rs
pub struct Materialised { pub root: PathBuf, pub fingerprint: String }
/// Builds the installed tree under `root`: extracts `files` (tar) at the root,
/// extracts the python archive to `root/python`, installs the wheels with
/// `python/python.exe -m pip install --no-index --no-compile --no-warn-script-location --find-links <wheels> <each filename in lock order>`,
/// deletes every file under `python/Scripts/` not in `pristine_scripts`, deletes
/// every `__pycache__`, then returns `hash_tree(root)`.
pub fn materialise(root: &Path, files_tar: &[u8], python_archive: &Path, wheels_tar: &[u8], lock: &[Locked]) -> Result<Materialised>;
```

- [ ] **Step 1: Failing tests** (`tests/materialise.rs`)

```rust
use std::path::PathBuf;
fn release() -> Option<PathBuf> { std::env::var_os("VK_TEST_PYTHON_RELEASE").map(PathBuf::from) }

#[test]
fn the_same_inputs_materialise_to_one_fingerprint_in_two_directories() {
    let Some(archive) = release() else { eprintln!("VK_TEST_PYTHON_RELEASE unset: skipped"); return; };
    let t = tempfile::tempdir().unwrap();
    // a two-file driver and a requirements with one pure wheel that has a console script
    let src = t.path().join("src");
    std::fs::create_dir_all(&src).unwrap();
    std::fs::write(src.join("driver.py"), b"import pip_install_test\nprint('hello')\n").unwrap();
    std::fs::write(src.join("requirements.txt"), b"pip-install-test==0.5\n").unwrap();
    let files_tar = vk_capital::wheels::tar_dir(&src).unwrap();
    let py = vk_capital::python::extract(&archive, &t.path().join("stage")).unwrap();
    let wheels_dir = t.path().join("wheels");
    let lock = vk_capital::wheels::resolve(&py.join("python.exe"), &src.join("requirements.txt"), &wheels_dir).unwrap();
    assert_eq!(lock.len(), 1);
    let wheels_tar = vk_capital::wheels::tar_dir(&wheels_dir).unwrap();
    let a = vk_capital::materialise::materialise(&t.path().join("a"), &files_tar, &archive, &wheels_tar, &lock).unwrap();
    let b = vk_capital::materialise::materialise(&t.path().join("b"), &files_tar, &archive, &wheels_tar, &lock).unwrap();
    assert_eq!(a.fingerprint, b.fingerprint);
    // the launcher pip wrote is gone, the module is importable with -I
    assert!(!a.root.join("python/Scripts/pip-install-test.exe").exists());
    let out = std::process::Command::new(a.root.join("python/python.exe")).args(["-I", "driver.py"]).current_dir(&a.root).output().unwrap();
    assert!(String::from_utf8_lossy(&out.stdout).contains("hello"), "{:?}", out);
}

#[test]
fn a_tar_entry_that_escapes_is_refused() {
    let mut buf = Vec::new();
    {
        let mut b = tar::Builder::new(&mut buf);
        let data = b"x";
        let mut h = tar::Header::new_gnu();
        h.set_size(1); h.set_mode(0o644); h.set_cksum();
        b.append_data(&mut h, "../escape.txt", &data[..]).unwrap();
        b.finish().unwrap();
    }
    let t = tempfile::tempdir().unwrap();
    assert!(vk_capital::wheels::untar_into(&buf, t.path()).unwrap_err().to_string().contains("escape"));
}

#[test]
fn tar_dir_is_deterministic() {
    let t = tempfile::tempdir().unwrap();
    std::fs::write(t.path().join("b.txt"), b"2").unwrap();
    std::fs::write(t.path().join("a.txt"), b"1").unwrap();
    let x = vk_capital::wheels::tar_dir(t.path()).unwrap();
    std::thread::sleep(std::time::Duration::from_millis(1100));
    std::fs::write(t.path().join("a.txt"), b"1").unwrap(); // new mtime, same bytes
    assert_eq!(x, vk_capital::wheels::tar_dir(t.path()).unwrap());
}

#[test]
fn the_pinned_release_table_is_well_formed() {
    let r = &vk_capital::python::PINNED;
    assert!(r.file.ends_with("-x86_64-pc-windows-msvc-install_only.tar.gz"));
    assert_eq!(r.sha256.len(), 64);
    assert!(r.url.starts_with("https://github.com/astral-sh/python-build-standalone/releases/download/"));
}

#[test]
#[ignore = "network: downloads the pinned CPython release"]
fn the_pinned_release_downloads_and_matches_its_sha256() {
    let t = tempfile::tempdir().unwrap();
    let p = vk_capital::python::fetch(t.path()).unwrap();
    assert!(p.exists());
}
```

(`pip-install-test` is a tiny pure-Python package on PyPI that installs a console script; if it is unavailable at implementation time, use `six` for importability and a second package with a script.)

- [ ] **Step 2: Run, expect compile failures.**

- [ ] **Step 3: Implement**

`python::fetch`: `reqwest::blocking::Client::builder().timeout(Duration::from_secs(600)).build()`, GET `url`, stream to `<cache>/<file>.part`, hash while writing, compare to `sha256` (`refuse("python release … sha256 mismatch", "the download was altered or the table is stale; do not use it")` and delete), rename. `extract`: `flate2::read::GzDecoder` → `tar::Archive`, entries must start with `python/`, no links, no `..`; unpack under `dest`. `pristine_scripts`: `read_dir(python/Scripts)` file names.

`wheels::resolve`: `Command::new(python_exe).args(["-m","pip","download","--only-binary=:all:","--dest",wheels_dir,"-r",requirements])`, refuse on non-zero with the last 300 bytes of stderr in the daemon log only (the refusal says `pip download failed; remedy: every requirement needs a wheel for this interpreter`). Parse `<name>-<version>-…whl` by splitting on `-`. `tar_dir`: walk sorted, `Header::new_gnu`, `set_mtime(0)`, `set_uid(0)`, `set_gid(0)`, `set_mode(if executable 0o755 else 0o644)`, `append_data`. `untar_into`: for each entry, `entry.path()?` must be relative, contain no `..` component, and `entry.header().entry_type().is_file() || is_dir()`; else refuse `tar entry … would escape or is a link`.

`materialise`: as the interface says; delete `__pycache__` directories with a walk after the install; delete `python/Scripts/<new>` files; then `fingerprint::hash_tree(root)`.

- [ ] **Step 4: Run locally with a release**

Run (PowerShell): `$env:VK_TEST_PYTHON_RELEASE = "<path to the downloaded install_only .tar.gz>"; cargo test --locked -p vk-capital --test materialise`
Expected: green. (Get the archive once with `cargo test -p vk-capital --test materialise the_pinned_release_downloads -- --ignored` after setting `fetch`'s cache to a known dir — or download it by hand from the release page.)

- [ ] **Step 5: Commit**

```bash
git add crates/vk-capital
git commit -m "feat(vk-capital): pinned CPython release, wheel resolve and lock, deterministic tars, materialise with a reproducible fingerprint"
```

---
### Task 7: Kernel — publisher key, `driver.pack`, mount from an installed tree, context rules, `tampered`

**Files:**
- Modify: `crates/vk-kernel/src/drivers.rs` (`mount_driver` 289-358, `degrade_driver` 407-429, `DriverRow` 62-80, `DRIVER_FAILURE_REASONS` 142-158), `crates/vk-kernel/src/lib.rs` (new `capital` accessors beside `enroll_node_device` 2190)
- Create: `crates/vk-kernel/src/capital.rs`
- Test: `crates/vk-kernel/tests/drivers.rs` (helpers `open`, `human`, `machine`, `maskai_like`, `root` at 49-137), new `crates/vk-kernel/tests/capital.rs`

**Interfaces:**
- Consumes: `vk_capital::signature::{PublisherKey, KeySource, Pins, PinnedKey, verify}`, `vk_capital::layout`, `vk_capital::fingerprint`, `vk_contracts::driver::{DriverSource, ExecutionContext}`, `vk_contracts::package::DriverPacked`.
- Produces (on `RealKernel`):

```rust
/// Where installed drivers live: `VK_DRIVER_INSTALL_ROOT` if set, else
/// `%ProgramFiles%\Aubier\drivers`. Read once at boot, exposed in `boot.info`.
pub fn install_root(&self) -> &Path;

/// `vk capital key init`: a human act. Creates the publisher key (keyring
/// `vk`/`publisher`, or `VK_PUBLISHER_KEY_FILE` when set — the same file
/// seam `VK_NODE_KEY_FILE` gives the device key) and self-pins its public key.
/// Idempotent: a second call reports the existing key.
pub fn publisher_key_init(&mut self, ctx: &Ctx) -> Result<PublisherKeyInfo, KernelError>;
pub fn publisher_key_show(&self) -> Result<Option<PublisherKeyInfo>, KernelError>;
pub struct PublisherKeyInfo { pub key_id: String, pub public_pem: String, pub pinned: bool }

/// `driver.pack`: a human act. `packed` is what `vk-ipc` built (digest,
/// fingerprint, name, version, driver_id, key id); the kernel validates the
/// manifest (incl. the context rules), writes the row and returns it.
pub fn driver_packed(&mut self, ctx: &Ctx, manifest: &DriverManifest, version: &str, package_digest: &str, publisher_key_id: &str) -> Result<DriverPacked, KernelError>;

/// Extended signature: `package_digest` is `Some` for an installed tree.
pub fn mount_driver(&mut self, ctx: &Ctx, manifest: DriverManifest, root: PathBuf, observed_governed: bool, package_digest: Option<String>) -> Result<DriverRow, KernelError>;

/// The rules of spec §4.2, called by `mount_driver` and by `driver_packed`:
/// - `execution_context: service` → I1 "the service mode ships with vk-agent; remedy: execution_context interactive"
/// - `source: installed` but `root` not under `install_root()` → I1 "a manifest that says installed must live under <install_root>; remedy: install the package with vk driver install"
/// - `source: development` but `root` under `install_root()` → I1 "an installed tree mounts by name@version; remedy: vk mount driver <name>@<version>"
pub fn check_context(&self, manifest: &DriverManifest, root: &Path) -> Result<(), KernelError>;
```

- `DriverRow` gains `#[serde(default)] pub package_digest: Option<String>` and `#[serde(default)] pub fingerprint_cache: Option<PathBuf>` (the cache file path, `<state_dir>/drivers/<name>/fingerprint.json`). `driver.mounted` carries `package_digest` and `source`.
- `degrade_driver(name, "tampered")` is reachable through `DriverFailure::Tampered` (new arm; `reason()` → `"tampered"`).

- [ ] **Step 1: Failing tests** (`tests/capital.rs`, using the helpers of `tests/drivers.rs` copied into a `mod common`)

```rust
#[test]
fn the_publisher_key_is_created_once_self_pinned_and_shown() {
    let t = tempfile::tempdir().unwrap();
    std::env::set_var("VK_PUBLISHER_KEY_FILE", t.path().join("publisher.key"));
    let mut k = common::open(t.path());
    assert!(k.publisher_key_show().unwrap().is_none());
    let info = k.publisher_key_init(&common::human(1)).unwrap();
    assert!(info.pinned);
    assert!(t.path().join("capital/publishers").join(format!("{}.pub", info.key_id.trim_start_matches("sha256:"))).exists());
    let again = k.publisher_key_init(&common::human(2)).unwrap();
    assert_eq!(again.key_id, info.key_id);
    assert!(matches!(k.publisher_key_init(&common::machine(3)), Err(KernelError::I1(_))));
}

#[test]
fn driver_packed_is_a_human_act_on_the_ledger_with_the_six_hashes() {
    let t = tempfile::tempdir().unwrap();
    let mut k = common::open(t.path());
    let m = common::maskai_like();
    let row = k.driver_packed(&common::human(1), &m, "1.0.0", "sha256:aaaa", "sha256:bbbb").unwrap();
    assert_eq!(row.driver_id, m.driver_id());
    assert_eq!(row.version, "1.0.0");
    let last = k.ledger_tail(1).unwrap().pop().unwrap();
    assert_eq!(last.kind, "driver.packed");
    assert!(matches!(k.driver_packed(&common::machine(2), &m, "1.0.0", "sha256:a", "sha256:b"), Err(KernelError::I1(_))));
}

#[test]
fn a_service_context_is_refused_at_mount_with_the_remedy() {
    let t = tempfile::tempdir().unwrap();
    let mut k = common::open(t.path());
    let mut m = common::maskai_like();
    m.execution_context = ExecutionContext::Service;
    m.program.headless = true;
    let e = k.mount_driver(&common::human(1), m, common::root(), true, None).unwrap_err();
    assert!(e.to_string().contains("ships with vk-agent"), "{e}");
}

#[test]
fn source_is_verified_by_path_in_both_directions() {
    let t = tempfile::tempdir().unwrap();
    let install_root = t.path().join("pf").join("Aubier").join("drivers");
    std::env::set_var("VK_DRIVER_INSTALL_ROOT", &install_root);
    let mut k = common::open(t.path());
    let mut m = common::maskai_like();
    m.source = DriverSource::Installed;
    let e = k.mount_driver(&common::human(1), m.clone(), common::root(), true, Some("sha256:dd".into())).unwrap_err();
    assert!(e.to_string().contains("must live under"), "{e}");
    let installed = install_root.join("maskai-1").join("1.0.0");
    std::fs::create_dir_all(&installed).unwrap();
    let mut dev = common::maskai_like();
    dev.source = DriverSource::Development;
    let e = k.mount_driver(&common::human(2), dev, installed.clone(), true, None).unwrap_err();
    assert!(e.to_string().contains("mounts by name@version"), "{e}");
}

#[test]
fn an_installed_mount_records_the_package_digest_and_source() {
    let t = tempfile::tempdir().unwrap();
    let install_root = t.path().join("drivers-installed");
    std::env::set_var("VK_DRIVER_INSTALL_ROOT", &install_root);
    let mut k = common::open(t.path());
    let installed = install_root.join("maskai-1").join("1.0.0");
    std::fs::create_dir_all(&installed).unwrap();
    let mut m = common::maskai_like();
    m.source = DriverSource::Installed;
    let row = k.mount_driver(&common::human(1), m, installed, true, Some("sha256:dd".into())).unwrap();
    assert_eq!(row.package_digest.as_deref(), Some("sha256:dd"));
    let last = k.ledger_tail(1).unwrap().pop().unwrap();
    let p: vk_contracts::driver::DriverMounted = serde_json::from_value(last.payload).unwrap();
    assert_eq!(p.package_digest.as_deref(), Some("sha256:dd"));
    assert_eq!(p.source, DriverSource::Installed);
}

#[test]
fn tampered_degrades_the_driver_and_is_a_closed_word() {
    let t = tempfile::tempdir().unwrap();
    let mut k = common::open(t.path());
    k.mount_driver(&common::human(1), common::maskai_like(), common::root(), true, None).unwrap();
    k.degrade_driver("maskai-1", &DriverFailure::Tampered.reason()).unwrap();
    let row = k.drivers().into_iter().find(|r| r.name == "maskai-1").unwrap();
    assert!(matches!(row.state, DriverState::Degraded { ref reason } if reason == "tampered"));
}
```

(`ledger_tail(n)` — if the kernel has no such accessor, use the one the existing driver tests use to read the last event; `rg "fn .*tail\|last_event" crates/vk-kernel/src` finds it. Tests that set env vars must run with `--test-threads=1` or use the `serial_test` pattern already present in the workspace, if any; otherwise give each test its own kernel and read the install root through a `RealKernel::with_install_root(dir)` builder added for tests — prefer the builder, documented `#[doc(hidden)]`.)

- [ ] **Step 2: Run, expect compile failures.**

- [ ] **Step 3: Implement** `capital.rs` (the two key verbs and `driver_packed`), the `install_root` field read at construction (`RealKernel::open`), `check_context`, the `mount_driver` extension (`package_digest` into the row and the payload, `source` into the payload, `fingerprint_cache` path set for `Installed`), `DriverFailure::Tampered`, the reason word in `DRIVER_FAILURE_REASONS` (done in Task 2, keep the test in step).

`driver_packed`: refuse a non-human with `I1("packing a driver is a human act: run it from an enrolled device")`; `manifest.validate()`; `check_context` with `root = install_root()` joined with name/version (an installed manifest is what is packed: set a clone's `source = Installed` before the check); write `self.log("driver.packed", now, &DriverPacked { driver_id: manifest.driver_id(), name, version, package_digest, files_sha256: manifest.program.files_sha256.clone(), publisher_key_id, by: device_id })`.

- [ ] **Step 4: Run, green; commit**

```bash
git add crates/vk-kernel
git commit -m "feat(kernel): publisher key, driver.packed, mount from an installed tree with package_digest, context and source rules, tampered"
```

---

### Task 8: IPC and CLI — `vk capital key`, `vk driver pack`, `vk mount driver NAME@V`, `vk driver ls`, fingerprint before spawn

**Files:**
- Modify: `crates/vk-ipc/src/server.rs` (`takes_presence` 706-734, `dispatch` 772+, `driver.mount` arm 850-862, `driver.ls` 1285), `crates/vk-ipc/src/drivers.rs` (`MountParams` 470-477, `checked_mount` 482-492, `files_hold` 502-537, `mount` 546-605, `spawn_host` 422-440), `crates/vk-cli/src/main.rs` (`MountCmd` 283, `DriverCmd` 510-520, `Cmd` 72, `call` 1083-1098, `driver_manifest` 1559-1578), `crates/vk-cli/src/render.rs` (`drivers` 330-346, `mounted_driver` 1139-1153), `crates/vk-cli/src/man.rs` (verb list if it has one)
- Create: `crates/vk-ipc/src/capital.rs` (the `driver.pack` work: resolve, embed, materialise, layout, sign)
- Test: `crates/vk-ipc/tests/drivers.rs` (helpers `Node`, `manifest`, `mount_params` 209-444), new `crates/vk-ipc/tests/capital.rs`, `crates/vk-cli/tests/smoke.rs`

**Interfaces:**
- RPC methods (all through `dispatch`): `capital.key_init` (presence), `capital.key_show`, `driver.pack` (presence; params `{manifest: PathBuf, version: String, out: PathBuf, python_cache: Option<PathBuf>}` → `{package_digest, files_sha256, driver_id, name, version, out}`), `driver.mount` gains `{installed: "name@version"}` as an alternative to `{manifest, root}`; `driver.ls` rows gain `installed_versions: Vec<String>` for every name under the install root (merged with mounted rows; an installed, unmounted driver prints state `installed`).
- CLI: `vk capital key init|show`, `vk driver pack --manifest FILE --version V --out DIR [--python-cache DIR]`, `vk mount driver NAME@V` (positional, exclusive with `--manifest`), `vk driver ls` columns `NAME STATE GOVERNED SOURCE VERSION ACTIONS WHY`.
- `files_hold(manifest, root)` becomes `files_hold(row: &DriverRow)`: for `source: development` it keeps today's `hash_dir` of the script's directory; for `installed` it runs `fingerprint::check(root, cache)` with the row's cache file, stores a rehashed cache, and on a mismatch returns `DriverFailure::Tampered` (so `driver_act_settle` degrades with `tampered`).

- [ ] **Step 1: Failing tests**

`crates/vk-ipc/tests/capital.rs` — a fake package end to end **without a CPython download**: the `driver.pack` path takes a `python_archive` override in `ServerConfig` (test seam `VK_TEST_PYTHON_RELEASE`, the same variable as Task 6) and the test provides a tiny archive built from the test runner's own interpreter (`python_available()` pattern from `vk-driver/tests/fake_driver.rs`): a tar.gz with `python/python.exe` = a copy of the runner's `python.exe` is not a working interpreter (DLLs missing), so the test asserts the layout and the ledger only, and the materialise/run assertion lives in Task 11's CI job with the real archive.

```rust
#[test]
fn pack_writes_a_signed_layout_and_a_driver_packed_row() {
    let Some(archive) = std::env::var_os("VK_TEST_PYTHON_RELEASE") else { eprintln!("skipped"); return; };
    let node = Node::start();                      // existing helper
    std::env::set_var("VK_PUBLISHER_KEY_FILE", node.dir.join("publisher.key"));
    node.call_human("capital.key_init", json!({})).unwrap();
    let src = fixture_driver_dir(&node);           // writes driver.py + requirements.txt (empty) + manifest.json with the right files_sha256
    let out = node.dir.join("dist");
    let r = node.call_human("driver.pack", json!({"manifest": src.join("manifest.json"), "version": "0.1.0", "out": out, "python_cache": node.dir.join("pycache")})).unwrap();
    let opened = vk_capital::layout::open(&out).unwrap();
    assert_eq!(opened.package_digest, r["package_digest"]);
    let last = node.last_event();
    assert_eq!(last.kind, "driver.packed");
}

#[test]
fn a_second_pack_into_the_same_out_is_refused_before_anything_is_written() { … "remedy: choose an empty --out" … }

#[test]
fn mount_by_name_and_version_resolves_the_installed_tree_and_refuses_an_absent_version() {
    // VK_DRIVER_INSTALL_ROOT = node.dir/installed; copy the fixture driver tree there as maskai-fixture/0.1.0 with a `.package/` dir holding the image manifest and the signature of a key pinned under node.dir/capital/publishers; then driver.mount {installed:"maskai-fixture@0.1.0"} succeeds (governed may be false: no Job in tests) and {installed:"maskai-fixture@9.9.9"} is refused with "is not installed; remedy: vk driver install"
}

#[test]
fn an_act_after_a_byte_change_under_the_installed_tree_degrades_with_tampered() {
    // mount as above, act once through the existing act path of this test file (same call the test `…acts…` makes), flip a byte in driver.py, act again: the second act is refused and `driver.ls` shows state degraded, why "tampered"; the ledger's last driver.unmounted has reason "tampered"
}
```

`crates/vk-cli/tests/smoke.rs`: `vk_capital_key_init_and_show_print_the_key_id`, `vk_driver_ls_lists_installed_versions_before_any_mount` (an installed tree under `VK_DRIVER_INSTALL_ROOT`, no mount: one row `maskai-fixture installed - installed 0.1.0 … -`), `vk_mount_driver_by_name_prints_the_source_and_version`.

- [ ] **Step 2: Run, expect failures (methods unknown).**

- [ ] **Step 3: Implement**

`vk-ipc/src/capital.rs::pack(kernel, config, ctx, params)`:
1. Read and parse the manifest file (`DriverManifest`), set `source = Installed`, `validate()`.
2. `check_context` through the kernel (refuses `service`).
3. Refuse a non-empty `out`.
4. Stage dir under `<state_dir>/capital/stage/<uuid>`: `files_tar = tar_dir(driver_dir filtered as hash_dir does: no .venv, __pycache__, tests, fixture, top-level manifest*.json)`; `archive = python::fetch(python_cache or <state_dir>/capital/python)` unless `config.python_archive_override` (test seam) is set; `extract` into stage; `lock = wheels::resolve(stage python, requirements.txt, stage/wheels)`; `wheels_tar = tar_dir(stage/wheels)`.
5. `materialise(stage/tree, …)` → fingerprint; write it into the manifest copy's `program.files_sha256` and set `program.command = ["python/python.exe","-I","driver.py"]` (the script name is the first `.py` element of the original command).
6. `layout::write(out, Layers{manifest: canonical JSON of the copy, files, lock: serde_json(lock), wheels, python: archive bytes}, Annotations{…, created: now RFC 3339, publisher_key_id})`.
7. `PublisherKey::load(source)` — refuse `no publisher key; remedy: vk capital key init` — `sign(package_digest)`, `layout::write_signature`.
8. `kernel.driver_packed(ctx, &copy, version, &digest, &key_id)`.
9. Remove the stage dir; return the result.

`driver.mount` with `installed`: parse `name@version` (name by the manifest's rule, version by `^[0-9]+\.[0-9]+\.[0-9]+$`), `root = install_root/name/version`, refuse if `root/manifest.json` is absent (`… is not installed; remedy: vk driver install <package>`), read the manifest from the tree (`source` must be `Installed`), read `.package/manifest.json` + `.package/signature.json`, verify the signature against the pinned key (`Pins::at(state_dir).get(key_id)`, refuse `publisher … is not pinned on this node; remedy: vk driver install pins it`), `fingerprint::check(root, cache)` must equal `program.files_sha256` else refuse `installed tree fingerprint … is not the manifest's …; remedy: reinstall, then remount`, then `pool.ensure` and `kernel.mount_driver(…, Some(package_digest))`.

CLI: `MountCmd::Driver { manifest: Option<PathBuf>, installed: Option<String> }` with `#[arg(conflicts_with = "manifest")]`; `Cmd::Capital { what: CapitalCmd }`, `CapitalCmd::Key { what: KeyCmd }`, `KeyCmd::{Init, Show}`; `DriverCmd::Pack { manifest, version, out, python_cache }`. `render::drivers` adds `SOURCE` and `VERSION` columns.

- [ ] **Step 4: Run** `cargo test --locked -p vk-ipc -p vk-cli` (with `VK_TEST_PYTHON_RELEASE` set for the pack test); green.

- [ ] **Step 5: Commit**

```bash
git add crates/vk-ipc crates/vk-cli
git commit -m "feat(ipc,cli): vk capital key, vk driver pack, mount by name@version from the installed tree, fingerprint before every spawn, tampered"
```

---

### Task 9: `vk driver install` and `vk driver uninstall` (elevated, off the ledger, TOFU)

**Files:**
- Create: `crates/vk-capital/src/install.rs`, `crates/vk-cli/src/install.rs`
- Modify: `crates/vk-cli/src/main.rs` (`run` 656: these two verbs run without the daemon, like `Boot`/`Man`/`Secret`), `crates/vk-store/src/win_acl.rs` (new `create_readonly_tree_dir`)
- Test: `crates/vk-capital/src/install.rs` unit tests, `crates/vk-cli/tests/smoke.rs`

**Interfaces:**

```rust
// vk_capital::install
pub struct InstallReport { pub name: String, pub version: String, pub package_digest: String, pub fingerprint: String, pub target: PathBuf, pub pinned_now: bool }
pub enum Tofu<'a> { Confirm(&'a mut dyn FnMut(&PinnedKey) -> bool), Refuse }
/// Opens the layout, resolves the signature's key against `pins`; unknown key →
/// TOFU callback (pin on true, refuse on false); known key → verify, no prompt.
/// Checks the annotations against the manifest layer (name, version, driver id).
/// Refuses if `root/name/version` exists. Materialises into `root/name/version.part`,
/// compares the fingerprint with the manifest's `files_sha256` (mismatch → remove,
/// refuse "repack on a node with the same CPython release"), writes `.package/`
/// (image manifest + signature), applies the ACL, renames `.part` → `version`.
pub fn install(layout: &Path, root: &Path, pins: &Pins, tofu: Tofu, pinned_by: &str, acl: &dyn Fn(&Path) -> Result<()>) -> Result<InstallReport>;
/// Removes `root/name/version`; `mounted` says whether the daemon reports it mounted.
pub fn uninstall(root: &Path, name: &str, version: &str, mounted: bool) -> Result<()>;
```

- `vk-store::win_acl::create_readonly_tree_dir(dir)`: SDDL `D:P(A;OICI;FA;;;BA)(A;OICI;FA;;;SY)(A;OICI;0x1200a9;;;BU)` (administrators and SYSTEM full, built-in users read and execute), applied to the directory before the rename so children inherit. On non-Windows: a no-op.
- CLI `vk driver install PKG [--yes]`: the TOFU prompt prints `publisher key <key_id>\n<pem>\npin this publisher on this node? [y/N]` and reads one line from stdin (`--yes` answers y, for CI); `pinned_by` is the current user's SID (`whoami /user`-equivalent via `windows::Win32::Security` already in the workspace, or the user name on non-Windows). Elevation: the CLI tries `std::fs::create_dir_all(root)`; `PermissionDenied` → one line `vk driver install writes under <root>: run it from an elevated shell`. `vk driver uninstall NAME@V`: asks the daemon `driver.ls` if the endpoint answers; a mounted driver → `refused: maskai-1@1.0.0 is mounted; remedy: vk driver umount maskai-1 first`.

- [ ] **Step 1: Failing tests** (`install.rs` unit tests build a layout with `layout::write` from Task 4's in-memory layers and a `python` archive built from a fake `python/` dir — materialise is replaced in tests by a seam: `install_with(…, materialiser: &dyn Fn(&Path, …) -> Result<Materialised>)`; the real function uses `materialise::materialise`)

```rust
#[test] fn an_unknown_publisher_is_pinned_on_confirmation_and_refused_otherwise() { … Tofu::Confirm(&mut |_| true) → report.pinned_now; a second install of another version → pinned_now false, no callback called (assert via a counter); fresh pins + Tofu::Confirm(&mut |_| false) → Err contains "refused by the administrator", nothing under root }
#[test] fn a_known_key_with_a_bad_signature_is_refused_without_a_prompt() { … pin k1; package signed by k2 claiming k1 → Err contains "does not verify"; callback counter 0 }
#[test] fn a_fingerprint_mismatch_removes_the_tree_and_refuses() { … materialiser seam returns a different fingerprint → Err contains "repack"; root/name/version and .part absent }
#[test] fn an_existing_version_is_refused_untouched() { … pre-create root/name/version/marker; Err contains "already installed"; marker still there }
#[test] fn uninstall_refuses_a_mounted_version_and_removes_an_idle_one() { … }
#[test] fn the_installed_tree_carries_the_package_manifest_and_signature() { … root/name/version/.package/{manifest.json,signature.json} parse back }
```

`smoke.rs`: `vk_driver_install_refuses_without_elevation_message_when_the_root_is_read_only` (create the root as a read-only directory in a temp dir; assert the one-line message and no `.part`), `vk_driver_install_then_mount_by_name_end_to_end` guarded by `VK_TEST_PYTHON_RELEASE` (pack with the daemon, install with `--yes` into `VK_DRIVER_INSTALL_ROOT`, mount `name@version`, `vk driver ls` shows `installed 0.1.0`, umount, uninstall).

- [ ] **Step 2: Run, expect compile failures.** **Step 3: Implement** as specified. **Step 4: Run, green.**

- [ ] **Step 5: Commit**

```bash
git add crates/vk-capital crates/vk-cli crates/vk-store
git commit -m "feat(cli): vk driver install and uninstall — elevated, offline, TOFU on the publisher key, read-only tree"
```

---

### Task 10: `DriverLink` in `vk-driver` — `LocalLink` today, `AgentLink` declared

**Files:**
- Create: `crates/vk-driver/src/link.rs`
- Modify: `crates/vk-driver/src/lib.rs` (`DriverHost` 172-199, `spawn_with_hello_timeout` 265-399: the stdin/stdout/child handling moves behind `LocalLink`)
- Test: `crates/vk-driver/tests/fake_driver.rs` (`Rig`, every existing test must pass unchanged), new unit tests in `link.rs`

**Interfaces:**

```rust
pub struct SpawnSpec { pub argv: Vec<String>, pub work_dir: PathBuf, pub env: Vec<(String, String)>, pub log_file: PathBuf }
pub struct Spawned { pub pid: u32 }
pub struct EvidenceFile { pub name: String, pub bytes: Vec<u8> }
#[derive(Debug, thiserror::Error)]
pub enum LinkError { #[error("link: {0}")] Io(#[from] std::io::Error), #[error("link: not available: {0}")] NotAvailable(&'static str), #[error("link: closed")] Closed }

pub trait DriverLink: Send {
    fn spawn(&mut self, spec: SpawnSpec) -> Result<Spawned, LinkError>;
    fn send_line(&mut self, line: &str) -> Result<(), LinkError>;
    fn recv_line(&mut self, deadline: Instant) -> Result<Option<String>, LinkError>;
    fn evidence(&mut self, dir: &Path) -> Result<Vec<EvidenceFile>, LinkError>;
    fn kill(&mut self) -> Result<(), LinkError>;
    fn alive(&mut self) -> bool;
    fn child(&self) -> Option<&std::process::Child>; // LocalLink only: the governor needs the handle
}

pub struct LocalLink { … }   // the process the daemon spawns today
impl DriverLink for LocalLink { … }

/// Declared for the next chantier; every method returns NotAvailable("vk-agent").
pub struct AgentLink;
impl DriverLink for AgentLink { … }
```

`DriverHost` holds `Box<dyn DriverLink>`; `spawn` builds a `LocalLink` and keeps every observable behaviour (hello timeout, `driver.log`, reader thread, `kill_pid_tree`).

- [ ] **Step 1: Failing unit tests** — `local_link_round_trips_a_line_with_a_python_echo` (uses `python_available()`), `agent_link_is_declared_and_not_available` (every method returns `NotAvailable`).
- [ ] **Step 2: Run, expect compile failure.** **Step 3: Implement** by extraction, no behaviour change. **Step 4:** `cargo test --locked -p vk-driver -p vk-ipc` green, including `tree_is_contained_on_windows`.
- [ ] **Step 5: Commit** `refactor(vk-driver): the DriverLink seam — LocalLink carries today's process, AgentLink is declared for vk-agent`

---

### Task 11: CI job `driver-install`, docs, TCB

**Files:**
- Modify: `.github/workflows/ci.yml` (new job after `driver-browser`), `scripts/check-workflows.py` (`EXPECTED_JOBS` adds `driver-install`), `docs/technical/technical-readme.md` (new section "Driver packages"), `contracts/tcb.md` §Drivers (spec §11), `docs/technical/runbooks/recette-maskai.md` (new §6.0 "Depuis un paquet installé"), `README.md` (one line in the status table)

- [ ] **Step 1: The job**

```yaml
  driver-install:
    # Pack the fixture driver with the daemon, install it under a temporary
    # root standing in for %ProgramFiles%, mount it by name@version, run the
    # fixture tests from the installed tree, tamper, see `tampered`.
    runs-on: windows-latest
    timeout-minutes: 60
    env:
      CARGO_INCREMENTAL: "0"
      VK_DRIVER_INSTALL_ROOT: ${{ github.workspace }}\installed
      VK_TEST_PYTHON_RELEASE: ${{ github.workspace }}\pycache\release.tar.gz
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
      - name: Pin OPENSSL_SRC_PERL (same pwsh step as the test job)
        …
      - name: The pinned CPython release (cached by file name)
        uses: actions/cache@v4
        with:
          path: pycache
          key: pbs-${{ hashFiles('crates/vk-capital/src/python.rs') }}
      - name: Download it if the cache missed
        shell: pwsh
        run: cargo run --locked -p vk-cli -- capital python fetch --cache pycache --as release.tar.gz
      - name: Build
        run: cargo build --locked -p vk-cli -p vkd -p vk-mcp
      - name: vk-capital with the real release
        run: cargo test --locked -p vk-capital --test materialise
      - name: End to end (pack, install, mount, act, tamper)
        run: cargo test --locked -p vk-cli --test smoke vk_driver_install_then_mount_by_name_end_to_end -- --nocapture
      - name: Tampered through the IPC path
        run: cargo test --locked -p vk-ipc --test capital an_act_after_a_byte_change_under_the_installed_tree_degrades_with_tampered -- --nocapture
```

(`vk capital python fetch` is a small hidden verb added in this task to `vk-cli` so that CI downloads with the same code and SHA-256 check as `pack`: `KeyCmd` sibling `PythonCmd::Fetch { cache, r#as }`.)

- [ ] **Step 2:** `python -I scripts/check-workflows.py` green; push; `gh run watch`.
- [ ] **Step 3: Docs** — technical readme: the layout, the verbs, the install root, the fingerprint cache, the test seams (`VK_DRIVER_INSTALL_ROOT`, `VK_PUBLISHER_KEY_FILE`, `VK_TEST_PYTHON_RELEASE`); TCB: the five sentences of spec §11 plus plan ruling P-1 (no venv, wheels in the embedded interpreter) and the launcher purge; runbook §6.0: `vk capital key init`, `vk driver pack --manifest drivers\maskai-recette\manifest.json --version 1.0.0 --out dist\maskai-recette-1.0.0`, elevated `vk driver install dist\maskai-recette-1.0.0`, `vk mount driver maskai-1@1.0.0`, and the note that the profile under the state dir is reused.
- [ ] **Step 4: Commit** `ci,docs: the driver-install job; driver packages in the technical readme, the TCB and the runbook`

---

### Task 12: The founder's node — exit criteria §8

Manual, with the founder present (human acts). Not a subagent task; the controller drives it from the runbook §6.0 and records the result in the SDD ledger.

- [ ] `vk capital key init` on the node; key id noted in the ledger of the chantier (private).
- [ ] `vk driver pack` of `maskai-recette` 1.0.0; `driver.packed` on the ledger (`vk dmesg`).
- [ ] Elevated `vk driver install`; TOFU confirmed; tree under `%ProgramFiles%\Aubier\drivers\maskai-1\1.0.0\` with the read-only ACL (`icacls` shows BU read/execute).
- [ ] `vk mount driver maskai-1@1.0.0`; `driver.mounted` carries `package_digest` and `source: installed`.
- [ ] Fixture dry run (runbook §14) **from the installed driver** (`--manifest` replaced by `maskai-1@1.0.0` against the fixture recipe's manifest-fixture packed as `maskai-fixture@0.1.0`): 8 steps done.
- [ ] A byte changed under the installed tree (elevated), next act → `tampered`; reinstall, remount.
- [ ] CI green on every job; TCB updated; R15 closed in the TCB text.

---

## Self-review notes (run by the author before handoff)

- Spec coverage: §4.1 → Tasks 2, 4; §4.2 → 1, 7; §4.3 → 3, 6, 8; §4.4 → 2, 7; §4.5 → 5, 7, 9; §4.6 → 10; §4.7 → 8, 9, 11; §5.1–5.4 → 8, 9, 12; §6 → 7, 8, 9 (every row has a test); §7 → 1–11; §8 → 12; §11 → 11.
- Open items of spec §10 settled here: the pinned release (Task 6 table), elevation (Task 9: permission error → one line), the CI job name `driver-install` on `windows-latest`, the ACL string (Task 9), `vk driver ls` shows installed versions but not the pinned publisher (`vk capital key show` does).
- Plan rulings: **P-1** no venv, wheels into the embedded interpreter; **P-2** console-script launchers purged after install; **P-3** `.package/` excluded from the fingerprint; **P-4** `driver.packed` is a presence-proof act like `driver.mount`, nothing signs the payload; **P-5** the install root test seam is `VK_DRIVER_INSTALL_ROOT`, read at boot.
- Type consistency: `DriverSource`/`ExecutionContext` (Task 1) used in 2, 7, 8; `Pins`/`PinnedKey`/`verify` (5) used in 8, 9; `fingerprint::check` (3) used in 8; `layout::{write, write_signature, open, read_layer}` (4) used in 8, 9; `materialise` (6) used in 8, 9; `DriverFailure::Tampered` (7) used in 8.
