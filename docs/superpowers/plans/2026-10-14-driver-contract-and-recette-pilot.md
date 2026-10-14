# Driver contract and the MaskAI recette pilot — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Prerequisite:** master `d7a2fcc` (SP1b + `--claude-bin` + `liveness.renewed`), CI green on the five jobs; branch `pilot-recette-device` holds the spec (`1c321b6`).

**Goal:** Make the browser a *driver* of the kernel — a process the kernel starts, caps and talks to by syscall, never visible to the harness — and run the Aubier recette of MaskAI as a sixteen-step kernel task (plan, ten harness levels, judge, approve, draft, approve, release), so that the first governed recette happens as early as the minimum works and feeds the MaskAI REX.

**Architecture:** `vk-contracts` gains `DriverManifest`, three ledger payloads and `TaskRecipe`. A new crate `vk-driver` hosts the driver process (JSON lines over stdio, Job Object governor, deadline, evidence by path, egress sampling). The kernel keeps a persisted driver registry, `/drivers` in the namespace, and runs `driver.act` in three phases like a harness run — checks under the lock, the act with the lock released, settle under the lock — attaching evidence as labelled artefacts and writing `driver.acted` with hashes only. The daemon holds the live driver processes in a pool beside the kernel mutex; `vk-mcp` exposes one more tool, `vk_driver_act`; the fence does not change. The driver itself is Python + Playwright with eleven compiled business actions and a dialog guard.

**Tech Stack:** Rust workspace as today (`serde`, `schemars`, `jsonschema 0.30` already in `vk-contracts`, `windows` crate Job Objects via `vk-harness::confine`); Python 3.13 with `playwright` (Chromium/Chrome channel) for the driver and its fixture; `unittest` for Python tests.

**Spec:** `docs/superpowers/specs/2026-10-14-device-driver-contract-and-recette-pilot-design.md`. Executors read both. **Two refinements the plan makes to the spec**, both recorded in the spec's status line at Task 0: (1) the word is **driver**, not device — `/drivers`, `DriverManifest`, `driver.act`, `driver.mounted|acted|unmounted`, `vk mount driver`, `vk_driver_act` — because `/devices` and `device.enrolled` already name the enrolled *human* devices (founder, 2026-10-14); (2) a complete N0–N9 recette needs **eleven** business actions, not eight (page-load measurement, free-text message, conversation listing, concurrent load join the list).

## Order of work — the live recette comes first

The founder's constraint: the recette must run as soon as the minimum works, because it feeds an external project (the MaskAI REX). So:

- **Milestone A — vertical slice (Tasks 0–5):** contracts → driver host → kernel → daemon/CLI/MCP → the Python driver → recipe, runbook and **the first live recette on the founder's node**. Nothing in A is optional, nothing in A is polish. A ends when the REX material (report, journal, evidence, ledger) is released.
- **Milestone B — hardening (Tasks 6–7):** the CI `driver-browser` job, the Windows Job Object test for the driver tree, the TCB and README text, the failure-mode tests the live run did not exercise, the whole-branch review, merge and tag.

Every Milestone A task ends with its own tests green and a commit; a reviewer may still reject B items without reopening A.

## Execution tiering (founder rule, 2026-10-13)

| Tier | Model / effort | Tasks |
|---|---|---|
| Hard — process host, deadline kill, lock discipline, the seven refusals, label flow | Fable 5.1 / xhigh | 1, 2 |
| Standard — daemon pool, MCP tool, CLI verbs, Python driver, recipe and live run | Opus 5 / high | 3, 4, 5, 7 |
| Mechanical — contracts and schemas, CI job, docs | Sonnet 5 / medium | 0, 6 |
| Final review | Fable 5.1 / xhigh | 7 (whole branch) |

**Founder checkpoints:** any new ledger kind beyond the three here, any contract field not in this plan, any CLI verb not listed here, the arches chosen for plan/judge/draft (Task 5), and the go for the live run (Task 5) go to the founder before the task continues.

## Global Constraints

- **Workspace rules as before:** MSVC toolchain, `CARGO_TARGET_DIR` outside OneDrive, `cargo clippy --workspace --all-targets -- -D warnings` clean, `cargo fmt` clean, `schema_drift` green after every contract change (`cargo run -p vk-contracts --bin gen-schemas`).
- **Ledger kinds added, exactly three:** `driver.mounted`, `driver.acted`, `driver.unmounted`, appended to `ALLOWED_KINDS` in `crates/vk-contracts/src/ledger.rs`. `driver.acted` carries **hashes and sizes, never argument or result text** (spec P10, Ruling 28).
- **The harness fence does not change** (Rulings 19, 22): `permission_rules` and `launch_line` in `crates/vk-harness/src/launch.rs` are untouched; the only new capability a harness gets is the `vk_driver_act` tool behind `mcp__vk__*`.
- **Mounting a driver is a human act** (I1): `driver.mount` is in `takes_presence` and refuses a machine principal, exactly as a cloud `arch.mount` does.
- **Evidence caps:** per file `HARNESS_MAX_FILE_BYTES` (2 MiB), per action `HARNESS_MAX_RUN_BYTES / 4` (4 MiB), per harness run `HARNESS_MAX_RUN_BYTES` (16 MiB, the existing `bytes_attached` counter). A breach refuses the act and is remembered in `oversize`, so settle fails the step by name, as for `harness.attach_artefact`.
- **Artefact kinds are plain tokens** (`validate_artefact_kind`: ≤ 32 bytes of `[A-Za-z0-9._-]`, not starting with `.`): each evidence file is attached under its own file name as kind (`answer.png`, `dom.html`, `network.json`, `journal.jsonl`, `timing.json`); the action it came from is on the `driver.acted` row.
- **Driver identity:** `driver_id = "sha256:" + hash_canonical(manifest with governed = false)`; `program.files_sha256` is `hash_dir(<driver dir>)` = SHA-256 over, for each regular file under the directory in sorted relative-path order excluding `__pycache__/`, `tests/` and `fixture/`: the UTF-8 relative path with `/` separators, a `0x00` byte, the file bytes, a `0x00` byte. Implemented identically in Rust (`vk_contracts::driver::hash_dir`) and Python (`drivers/vkdriver/hash_dir.py`), cross-checked by a test.
- **One request in flight per driver**; `min_interval_ms` is enforced by the kernel from the previous `driver.acted` wall time of that driver.
- **The driver runs in the founder's interactive session** under the interactive daemon (spec P7). The browser profile is `<state_dir>/drivers/<name>/profile/`, the work dir `<state_dir>/drivers/<name>/work/`, evidence staging `<state_dir>/drivers/<name>/evidence/<request id>/`, all created with `vk_store::paths::private_dir`.
- **Python is run with `-I`** everywhere this plan launches it (driver, fixture server, tests), and the driver directory is never the current directory of an interpreter that reads untrusted files.
- **No secrets in manifests or recipes** (`MountSpec::validate`'s rule applies to `DriverManifest::validate`: a key named `password`, `token`, `secret`, `api_key` or `cookie` anywhere in the manifest is refused).

## Review Focus

Inputs the spec implies but no task's tests would otherwise exercise, most likely to bite first. Each line names the owning task, which adds the test.

1. **A driver that prints its hello and then a non-JSON line mid-run** (a Playwright warning on stdout) must not kill the act: the host skips lines that are not JSON objects with an `id` and logs them to the driver log — Task 1.
2. **Two harness steps of the same task declare the same driver and the first is relaunched after `WaitingHuman`:** `driver_act_begin` must accept the new token for the same step index and refuse a token of a *different* task on a driver whose previous act is still in flight — Task 2.
3. **A manifest whose `hosts` contains a scheme or a path (`https://app.maskai.fr/chat`)** is refused at validation with the remedy "host names only" — Task 0.
4. **An evidence file that is a symlink or junction out of the evidence directory** must be refused (`EvidenceEscape`) before any byte is read: the host opens the file, then checks the opened handle's canonical path is under the evidence dir — Task 1.
5. **A recipe with two `approve` steps where the second follows a `draft` whose arch returned an empty string:** the draft step fails ("returned nothing to attach") and the task is `Failed`, never `WaitingHuman` on a stale subject — Task 2 (existing behaviour, pinned by a recipe-shaped test).

---

## File structure

```
crates/vk-contracts/src/driver.rs        NEW: DriverKind, DriverProgram, DriverAction, DriverManifest (+validate, driver_id, action), DriverMounted, DriverActed, DriverUnmounted, hash_dir, DriverError
crates/vk-contracts/src/recipe.rs        NEW: TaskRecipe, RecipeStep (serde-identical to vk_kernel::tasks::StepKind)
crates/vk-contracts/src/lib.rs           modified: `pub mod driver; pub mod recipe;`, schema_registry() entries
crates/vk-contracts/src/ledger.rs        modified: three kinds in ALLOWED_KINDS
contracts/schemas/{driver_manifest,driver_mounted,driver_acted,driver_unmounted,task_recipe}.schema.json   GENERATED
contracts/examples/driver_manifest/{valid,invalid}/*.json, contracts/examples/task_recipe/{valid,invalid}/*.json
crates/vk-driver/                        NEW crate: src/lib.rs (DriverHost, Hello, ActOutcome, EvidenceFile, EvidenceCaps, HostError), src/protocol.rs (wire types), tests/fake_driver.rs (+ tests/fake_driver.py written by the test)
crates/vk-harness/src/launch.rs          modified: `pub fn kill_tree` (was private) so vk-driver reuses it
crates/vk-kernel/src/drivers.rs          NEW: MountedDriver, DriverState, DriverRow, DriverActPlan, mount_driver, unmount_driver, drivers, driver, driver_dirs, driver_act_begin, driver_act_settle
crates/vk-kernel/src/tasks.rs            modified: StepKind::Harness { name, drivers, instruction, max_actions, timeout_s }; create_task(constraints, evidence); Judge attaches verdict.md; harness_launch uses the step's instruction and records drivers on the token
crates/vk-kernel/src/lib.rs              modified: `pub mod drivers;`, HarnessTokenState { actions, step_index, drivers }, drivers map field, boot re-reads the drivers table
crates/vk-kernel/src/ns.rs               modified: `/drivers`, `/drivers/<name>`
crates/vk-kernel/tests/drivers.rs        NEW: the seven refusals, the ledger row, the evidence labels, the recipe run to Done, resumption
crates/vk-ipc/src/drivers.rs             NEW: DriverPool (daemon-side live hosts)
crates/vk-ipc/src/server.rs              modified: driver.mount / driver.unmount / driver.show / driver.ls / driver.act arms; takes_presence; task.create constraints+evidence; harness.run honours instruction/timeout_s
crates/vk-mcp/src/tools.rs               modified: vk_driver_act
crates/vk-cli/src/main.rs                modified: `vk mount driver --manifest FILE`, `vk driver ls|show|umount`, `vk task submit --steps FILE`
crates/vk-cli/src/render.rs              modified: ls renders /drivers, driver show
drivers/vkdriver/protocol.py             NEW: the JSON-lines loop every Python driver uses
drivers/vkdriver/hash_dir.py             NEW: the directory hash, same algorithm as Rust
drivers/maskai-recette/driver.py         NEW: the eleven actions over Playwright
drivers/maskai-recette/cases.json        NEW: every N0–N9 case the recipe runs (id, level, kind, text or oracle ref, nfr)
drivers/maskai-recette/manifest.json     NEW: the DriverManifest the founder mounts
drivers/maskai-recette/fixture/index.html, fixture/serve.py   NEW: the local stand-in chat page
drivers/maskai-recette/tests/test_driver.py                   NEW: 10/10 replay on the fixture, dialog guard, host refusal
recipes/recette-maskai.json              NEW: the sixteen-step TaskRecipe
docs/runbooks/recette-maskai.md          NEW: the founder's runbook for the live run
.github/workflows/ci.yml                 modified (Task 6): driver-browser job
docs/technical-readme.md, contracts/tcb.md, contracts/README.md   modified (Task 6)
```

---

## Milestone A — vertical slice

### Task 0: Contracts — `DriverManifest`, three payloads, `TaskRecipe`, ledger kinds, schemas (Sonnet)

**Files:**
- Create: `crates/vk-contracts/src/driver.rs`, `crates/vk-contracts/src/recipe.rs`
- Modify: `crates/vk-contracts/src/lib.rs` (mods + `schema_registry`), `crates/vk-contracts/src/ledger.rs` (`ALLOWED_KINDS`)
- Create: `contracts/examples/driver_manifest/valid/maskai.json`, `contracts/examples/driver_manifest/invalid/{host_with_scheme,secret_in_program,duplicate_action,no_actions}.json`, `contracts/examples/task_recipe/valid/recette.json`, `contracts/examples/task_recipe/invalid/{two_releases,unknown_kind}.json`
- Modify: the spec's status line (refinements 1 and 2 above)
- Test: `crates/vk-contracts/src/driver.rs` (unit), the existing `schema_drift` and examples tests pick the new types up through `schema_registry`

**Interfaces:**
- Produces (used by Tasks 1–5):

```rust
// crates/vk-contracts/src/driver.rs
use crate::labels::Clearance;
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum DriverKind { Browser }

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct DriverProgram {
    /// `python-playwright` for the pilot; informative, part of the identity.
    pub runtime: String,
    /// argv the host spawns, relative paths resolved against the daemon's cwd.
    pub command: Vec<String>,
    /// `hash_dir` of the driver's directory (see Global Constraints).
    pub files_sha256: String,
    pub browser_channel: Option<String>,
    pub headless: bool,
}

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

impl DriverManifest {
    /// `sha256:<hex>` of the canonical manifest with `governed` forced to false,
    /// so what the host observed does not change who the driver is.
    pub fn driver_id(&self) -> String;
    /// Refusals, each naming its remedy: empty name or a name that is not
    /// `[a-z0-9-]{1,32}`; empty command; `files_sha256` not `sha256:` + 64 hex;
    /// empty hosts; a host containing `/`, `:`, whitespace or upper-case;
    /// no actions; a duplicate action name; an action name not `[a-z_]{1,32}`;
    /// an `args` that is not a JSON object with `"type": "object"`;
    /// an evidence name failing the artefact-kind rule; `min_interval_ms > max_action_ms`;
    /// any key named password|token|secret|api_key|cookie anywhere in the manifest.
    pub fn validate(&self) -> Result<(), DriverError>;
    pub fn action(&self, name: &str) -> Option<&DriverAction>;
}

/// See Global Constraints for the exact algorithm.
pub fn hash_dir(dir: &std::path::Path) -> std::io::Result<String>;

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct DriverMounted { pub driver_id: String, pub name: String, pub manifest_hash: String, pub profile_dir_hash: String, pub governed: bool, pub by: String }

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct DriverActed {
    pub task_id: String, pub step_index: usize, pub driver_id: String, pub action: String,
    pub args_hash: String, pub result_hash: String, pub ok: bool,
    #[serde(skip_serializing_if = "Option::is_none")] pub reason: Option<String>,
    pub duration_ms: u64, pub evidence: Vec<String>, pub evidence_bytes: u64, pub hosts_seen: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct DriverUnmounted { pub driver_id: String, pub reason: String }
```

```rust
// crates/vk-contracts/src/recipe.rs
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case", tag = "kind")]
pub enum RecipeStep {
    Plan { arch_id: String },
    Draft { arch_id: String },
    Judge { arch_id: String },
    Harness {
        name: String,
        #[serde(default)] drivers: Vec<String>,
        #[serde(default)] instruction: Option<String>,
        #[serde(default)] max_actions: Option<u32>,
        #[serde(default)] timeout_s: Option<u64>,
    },
    Approve,
    Release { to_dir: String },
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct TaskRecipe {
    pub goal: String,
    pub artefact_kind: String,
    #[serde(default)] pub constraints: Vec<String>,
    #[serde(default)] pub evidence: Vec<String>,
    pub steps: Vec<RecipeStep>,
}

impl TaskRecipe {
    /// Refusals: empty goal; artefact_kind failing the artefact-kind rule; no steps;
    /// more than one Release; a Release not last; an Approve as the first step;
    /// a Harness naming a driver twice.
    pub fn validate(&self) -> Result<(), String>;
}
```

- [ ] **Step 1: Write the failing unit tests in `driver.rs`**

```rust
#[cfg(test)]
mod tests {
    use super::*;
    fn manifest() -> DriverManifest { serde_json::from_str(include_str!("../../../contracts/examples/driver_manifest/valid/maskai.json")).unwrap() }

    #[test]
    fn valid_example_validates_and_has_a_stable_id() {
        let m = manifest();
        m.validate().unwrap();
        let mut g = m.clone(); g.governed = true;
        assert_eq!(m.driver_id(), g.driver_id(), "governed is read back, not identity");
        assert!(m.driver_id().starts_with("sha256:"));
    }
    #[test]
    fn host_with_scheme_or_path_is_refused() {
        let mut m = manifest(); m.hosts = vec!["https://app.maskai.fr/chat".into()];
        let e = m.validate().unwrap_err().to_string();
        assert!(e.contains("host names only"), "{e}");
    }
    #[test]
    fn a_secret_shaped_key_anywhere_is_refused() {
        let mut m = manifest();
        m.actions[0].args = serde_json::json!({"type":"object","properties":{"cookie":{"type":"string"}}});
        assert!(m.validate().unwrap_err().to_string().contains("cookie"));
    }
    #[test]
    fn duplicate_action_is_refused() {
        let mut m = manifest(); let a = m.actions[0].clone(); m.actions.push(a);
        assert!(m.validate().unwrap_err().to_string().contains("duplicate"));
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
        assert_eq!(h1, "sha256:".to_string() + &hex::encode(sha2::Sha256::digest(b"a.py\0A\0b.py\0B\0")));
    }
}
```

- [ ] **Step 2: Run to verify failure**

Run: `cargo test -p vk-contracts driver::`
Expected: compile error, `driver` module missing.

- [ ] **Step 3: Implement `driver.rs` and `recipe.rs`** as in the Interfaces block. `hash_dir`: walk with `std::fs::read_dir` recursively, collect `(rel_path_with_forward_slashes, bytes)` for regular files, skip any path whose first component is `__pycache__`, `tests` or `fixture`, sort by path, feed `path\0bytes\0` into `sha2::Sha256`, return `"sha256:" + hex`. `validate` implements every refusal listed, in that order; the secret-key scan walks the manifest as `serde_json::Value` recursively over object keys (lower-cased). `driver_id` clones, sets `governed = false`, returns `format!("sha256:{}", crate::hash_canonical(&m))` — check `hash_canonical` already prefixes; match the arch id convention (`ArchManifest::arch_id`) exactly.

- [ ] **Step 4: Register the schemas and the kinds**

In `crates/vk-contracts/src/lib.rs` add `pub mod driver; pub mod recipe;` and to `schema_registry()` the five entries: `("driver_manifest", schema_for!(driver::DriverManifest))`, `("driver_mounted", …)`, `("driver_acted", …)`, `("driver_unmounted", …)`, `("task_recipe", schema_for!(recipe::TaskRecipe))`, following the existing entries' exact form. In `ledger.rs` append to `ALLOWED_KINDS`:

```rust
    // A driver — a peripheral behind a process this kernel starts — was mounted,
    // acted on behalf of a harness step, or was unmounted (founder decisions
    // 2026-10-14, driver-contract pilot). `driver.acted` carries hashes only.
    "driver.mounted",
    "driver.acted",
    "driver.unmounted",
```

- [ ] **Step 5: Write the examples**

`contracts/examples/driver_manifest/valid/maskai.json` — the spec §4.1 manifest with `name: "maskai-1"`, `kind: "browser"`, `program: {runtime: "python-playwright", command: ["python","-I","drivers/maskai-recette/driver.py"], files_sha256: "sha256:" + 64 zeros, browser_channel: "chrome", headless: false}`, `hosts: ["app.maskai.fr"]`, the eleven actions of Task 4 with `args` as JSON Schema objects (`{"type":"object","properties":{"case":{"type":"string"}},"required":["case"],"additionalProperties":false}` for the case-taking ones, `{"type":"object","additionalProperties":false}` otherwise), `min_interval_ms: 2000`, `max_action_ms: 120000`, `clearance: {"max_scope":"business","third_party_allowed":true}`, `governed: false`. The four invalid files each break one rule named in their file name. `task_recipe/valid/recette.json` is Task 5's recipe; `two_releases.json` and `unknown_kind.json` break the two rules.

- [ ] **Step 6: Regenerate and run**

Run: `cargo run -p vk-contracts --bin gen-schemas && cargo test -p vk-contracts`
Expected: five new schema files written; all tests PASS including `schema_drift` and the examples tests.

- [ ] **Step 7: Spec status line** — append to the spec's **Status** line: `; refined by the plan of 2026-10-14: the word is *driver* (`/devices` already names enrolled human devices) and the recette needs eleven business actions.`

- [ ] **Step 8: Commit**

```bash
git add crates/vk-contracts contracts docs/superpowers/specs
git commit -m "feat(contracts): DriverManifest, driver.mounted/acted/unmounted payloads, TaskRecipe; three ledger kinds; hash_dir"
```

---

### Task 1: `vk-driver` — the driver process host (Fable)

**Files:**
- Create: `crates/vk-driver/Cargo.toml`, `crates/vk-driver/src/lib.rs`, `crates/vk-driver/src/protocol.rs`
- Modify: `crates/vk-harness/src/launch.rs` (`pub fn kill_tree`)
- Test: `crates/vk-driver/tests/fake_driver.rs` (writes `fake_driver.py` into a temp dir and runs it with `python -I`; skipped with a message when no `python` is on PATH)

**Interfaces:**
- Consumes: `vk_contracts::driver::{DriverManifest, DriverAction}`, `vk_harness::confine::{Governor, for_this_platform}`, `vk_harness::netwatch::sample(pid)`, `vk_harness::launch::kill_tree`.
- Produces:

```rust
// crates/vk-driver/src/protocol.rs — the wire
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Hello { pub protocol: u32, pub driver: String, pub version: String, pub actions: Vec<String> }
#[derive(Debug, Serialize)]
pub struct Request<'a> { pub id: u64, pub action: &'a str, pub args: &'a serde_json::Value, pub deadline_ms: u64, pub evidence_dir: String }
#[derive(Debug, Deserialize)]
pub struct EvidenceRef { pub name: String, pub mime: String, pub path: String }
#[derive(Debug, Deserialize)]
pub struct Response { pub id: u64, pub ok: bool, #[serde(default)] pub reason: Option<String>, #[serde(default)] pub detail: Option<String>, #[serde(default)] pub result: serde_json::Value, #[serde(default)] pub evidence: Vec<EvidenceRef> }

// crates/vk-driver/src/lib.rs
pub const HELLO_TIMEOUT: Duration = Duration::from_secs(30);
pub const PROTOCOL: u32 = 1;

#[derive(Debug, Clone, Copy)]
pub struct EvidenceCaps { pub per_file: u64, pub per_action: u64 }

#[derive(Debug, Clone)]
pub struct EvidenceFile { pub name: String, pub mime: String, pub bytes: Vec<u8> }

#[derive(Debug, Clone)]
pub struct ActOutcome {
    pub ok: bool, pub reason: Option<String>, pub detail: Option<String>,
    pub result: serde_json::Value, pub evidence: Vec<EvidenceFile>,
    pub duration_ms: u64, pub hosts_seen: Vec<String>,
}

#[derive(Debug, thiserror::Error)]
pub enum HostError {
    #[error("driver printed no hello within {0:?}")] HelloTimeout(Duration),
    #[error("driver protocol: {0}")] Protocol(String),
    #[error("driver exited: {0}")] Dead(String),
    #[error("action exceeded its deadline; driver tree killed")] Timeout,
    #[error("stopped")] Stopped,
    #[error("evidence {0} is not under the evidence directory")] EvidenceEscape(String),
    #[error("evidence {name} is {len} bytes, above {cap}")] EvidenceTooLarge { name: String, len: u64, cap: u64 },
    #[error("evidence {0} is not a name the manifest allows for this action")] EvidenceNotDeclared(String),
}

pub struct DriverHost { /* child, stdin, line receiver thread, governor, governed, next_id, hello, evidence_root, log path */ }

impl DriverHost {
    /// Spawn `manifest.program.command` with cwd = `work_dir`, env `VK_DRIVER_PROFILE_DIR`,
    /// `VK_DRIVER_WORK_DIR`, `VK_DRIVER_HEADLESS` (0|1), `VK_DRIVER_CHANNEL`; contain under
    /// `governor` immediately (fail closed on Windows as the harness does); read the
    /// hello within HELLO_TIMEOUT; refuse protocol != 1 and an action set that does not
    /// cover `manifest.actions`. stderr goes to `<work_dir>/driver.log` (append).
    pub fn spawn(manifest: &DriverManifest, work_dir: &Path, profile_dir: &Path, evidence_root: &Path, governor: Box<dyn Governor>) -> Result<DriverHost, HostError>;
    pub fn governed(&self) -> bool;
    pub fn hello(&self) -> &Hello;
    /// One act. Creates `<evidence_root>/<id>/`, writes the request line, waits for the
    /// response with that id (other lines: JSON without `id` or non-JSON → appended to
    /// driver.log, ignored), polling `should_stop` every 250 ms and sampling
    /// `netwatch::sample(child pid)` every 500 ms into `hosts_seen`; on deadline or stop
    /// kills the tree (`kill_tree`) and returns Timeout/Stopped; reads each declared
    /// evidence file once (open, then canonicalise the opened path, refuse escapes,
    /// refuse undeclared names, cap per file and per action), removes `<evidence_root>/<id>/`.
    pub fn act(&mut self, action: &DriverAction, args: &serde_json::Value, deadline: Duration, caps: EvidenceCaps, should_stop: &(dyn Fn() -> bool + Sync)) -> Result<ActOutcome, HostError>;
    /// Kill the tree and reap. Also `Drop`.
    pub fn shutdown(self);
}
```

- [ ] **Step 1: Write the failing integration test with a scripted driver**

`tests/fake_driver.rs` writes this Python to a temp dir as `fake_driver.py` (the test skips with `eprintln!("no python on PATH; skipping")` if `python --version` fails):

```python
import json, os, sys, time
def say(o): sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
say({"hello": {"protocol": 1, "driver": "fake", "version": "0", "actions": ["echo", "slow", "escape", "big", "noise", "die"]}})
for line in sys.stdin:
    r = json.loads(line); a = r["action"]; ev = r["evidence_dir"]
    if a == "echo":
        p = os.path.join(ev, "note.txt"); open(p, "w").write("hi")
        say({"id": r["id"], "ok": True, "result": {"echo": r["args"]}, "evidence": [{"name": "note.txt", "mime": "text/plain", "path": p}]})
    elif a == "slow": time.sleep(30)
    elif a == "escape":
        say({"id": r["id"], "ok": True, "result": {}, "evidence": [{"name": "note.txt", "mime": "text/plain", "path": os.path.join(ev, "..", "..", "secret.txt")}]})
    elif a == "big":
        p = os.path.join(ev, "big.bin"); open(p, "wb").write(b"x" * (3 * 1024 * 1024))
        say({"id": r["id"], "ok": True, "result": {}, "evidence": [{"name": "big.bin", "mime": "application/octet-stream", "path": p}]})
    elif a == "noise":
        sys.stdout.write("Playwright warning: something\n"); sys.stdout.flush()
        say({"id": r["id"], "ok": True, "result": {"after": "noise"}})
    elif a == "die": sys.exit(3)
```

Tests (each spawns a host over a manifest built in the test with those six actions, `evidence: ["note.txt","big.bin"]` where relevant, `NoopGovernor` unless Windows):

```rust
#[test] fn hello_then_echo_returns_result_and_evidence()      // ok, result.echo == args, 1 evidence "note.txt" of 2 bytes, evidence dir removed afterwards
#[test] fn a_slow_action_is_killed_at_the_deadline()           // deadline 1 s → Err(Timeout) within 3 s; child.try_wait() is Some
#[test] fn an_evidence_path_outside_the_dir_is_refused()       // Err(EvidenceEscape), and the file was never read (secret.txt created by the test is untouched: check mtime/atime not needed — assert error variant only)
#[test] fn evidence_above_the_per_file_cap_is_refused()        // caps.per_file = 2 MiB → Err(EvidenceTooLarge{..})
#[test] fn non_json_lines_on_stdout_are_logged_not_fatal()     // "noise" → ok, result.after == "noise"; driver.log contains "Playwright warning"
#[test] fn a_driver_that_exits_is_reported_dead()              // "die" → Err(Dead(_)) mentioning exit code 3
#[test] fn an_undeclared_evidence_name_is_refused()            // action with evidence: [] but response carries note.txt → Err(EvidenceNotDeclared)
#[test] fn no_hello_within_the_timeout_is_refused()            // command `python -I -c "import time; time.sleep(60)"` with a 1 s HELLO_TIMEOUT via a `spawn_with_hello_timeout` test hook → Err(HelloTimeout)
#[test] fn stop_kills_the_tree()                                // should_stop returns true after 200 ms during "slow" → Err(Stopped) within 2 s
```

- [ ] **Step 2: Run to verify failure**

Run: `cargo test -p vk-driver`
Expected: crate does not exist / compile error.

- [ ] **Step 3: Implement**

`Cargo.toml`: `vk-contracts`, `vk-harness` (path), `anyhow`, `serde`, `serde_json`, `thiserror`, `tracing`, `tempfile` (dev). In `vk-harness/src/launch.rs` change `fn kill_tree(child: &mut Child)` to `pub fn kill_tree(child: &mut Child)` and add a one-line doc. In `lib.rs`:

- `spawn`: `Command::new(&cmd[0]).args(&cmd[1..]).current_dir(work_dir).envs(...).stdin(piped).stdout(piped).stderr(File append driver.log)`. Contain: `governor.contain(&child)`; on `Err` and `cfg!(windows)` → `kill_tree`, wait, return `Protocol("not contained: …")`; `governed = governor.governed()`. Start a reader thread over `BufReader::new(stdout).lines()` sending `String`s through `std::sync::mpsc::channel`. Hello: `recv_timeout(HELLO_TIMEOUT)`; parse; check `protocol == PROTOCOL` and `manifest.actions.iter().all(|a| hello.actions.contains(&a.name))`.
- `act`: `id = next_id; next_id += 1`; `let dir = evidence_root.join(id.to_string()); vk_store` is not a dependency here — create with `std::fs::create_dir_all` (the kernel pre-protects `evidence_root`). Write the request line + flush. Loop: `recv_timeout(250 ms)`: on line → try `serde_json::from_str::<Value>`; if object with numeric `id == id` → parse `Response`, break; else append the line to driver.log; on `RecvTimeoutError::Disconnected` → `kill_tree`, `Dead(format!("exit {:?}", child.try_wait()))`; every 500 ms `hosts_seen.extend(netwatch::sample(pid))` (dedupe at the end); if `Instant::now() > started + deadline` → `kill_tree`, `Timeout`; if `should_stop()` → `kill_tree`, `Stopped`. Then evidence: for each `EvidenceRef`: name must pass `validate_artefact_kind`'s rule (re-implement locally: ≤32, `[A-Za-z0-9._-]`, not leading `.`) **and** be in `action.evidence`; `let f = File::open(&r.path)`; canonical path of the opened file (`std::fs::canonicalize(&r.path)` after open; on Windows strip the `\\?\` prefix on both sides) must start with canonical `dir`; `metadata.len()` ≤ caps.per_file and running total ≤ caps.per_action; read via the already-open handle with `take(cap+1)`; then `remove_dir_all(dir)` whatever happened. Return `ActOutcome`.
- `Drop`: `kill_tree` + `wait`.

- [ ] **Step 4: Run tests** — `cargo test -p vk-driver` → all PASS (on Windows the `JobGovernor` variant too; run with `for_this_platform(1 GiB, None)` under `#[cfg(windows)]` in one extra test `tree_is_contained_on_windows`).

- [ ] **Step 5: Clippy + commit**

```bash
cargo clippy -p vk-driver -p vk-harness --all-targets -- -D warnings
git add crates/vk-driver crates/vk-harness Cargo.lock
git commit -m "feat(vk-driver): driver process host — hello, JSON-lines act, deadline kill, evidence by path with caps, egress sampling"
```

---

### Task 2: Kernel — driver registry, `/drivers`, `driver.act` in three phases, recipe-shaped tasks (Fable)

**Files:**
- Create: `crates/vk-kernel/src/drivers.rs`, `crates/vk-kernel/tests/drivers.rs`
- Modify: `crates/vk-kernel/src/lib.rs` (mod, fields, `HarnessTokenState`, boot), `crates/vk-kernel/src/tasks.rs` (`StepKind::Harness` fields, `create_task`, Judge arm, `harness_launch`), `crates/vk-kernel/src/ns.rs`
- Modify: every `create_task(` and `StepKind::Harness {` call site the compiler reports (kernel tests, `vk-ipc/src/server.rs`, `vk-cli/tests/smoke.rs`) — add `&[]`, `&[]` and `..Default::default()`-style fields.

**Interfaces:**
- Consumes: Task 0 types; `vk_driver::{ActOutcome, EvidenceCaps}` (the kernel depends on `vk-driver` for the outcome type only; it never spawns).
- Produces:

```rust
// tasks.rs
pub enum StepKind {
    Plan { arch_id: String },
    Draft { arch_id: String },
    Judge { arch_id: String },
    Harness {
        name: String,
        #[serde(default)] drivers: Vec<String>,
        #[serde(default)] instruction: Option<String>,
        #[serde(default)] max_actions: Option<u32>,
        #[serde(default)] timeout_s: Option<u64>,
    },
    Approve,
    Release { to_dir: String },
}
pub const DEFAULT_MAX_ACTIONS: u32 = 200;
pub fn create_task(&mut self, ctx: &Ctx, goal: &str, artefact_type: &str, label: Label, constraints: &[String], evidence: &[String], steps: Vec<StepKind>) -> Result<Task, KernelError>;
// HarnessLaunch gains: pub timeout_s: Option<u64>  (the daemon takes min(step, daemon))

// drivers.rs
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case", tag = "state")]
pub enum DriverState { Ready, Degraded { reason: String } }

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DriverRow { pub manifest: DriverManifest, pub driver_id: String, pub mounted_ms: u64, pub state: DriverState, pub last_act_ms: u64 }

pub struct DriverDirs { pub profile: PathBuf, pub work: PathBuf, pub evidence: PathBuf }

/// Phase one's answer: everything phase two needs, nothing it must look up.
#[derive(Debug, Clone)]
pub struct DriverActPlan {
    pub task_id: String, pub step_index: usize, pub name: String, pub driver_id: String,
    pub action: DriverAction, pub args: serde_json::Value, pub args_hash: String,
    pub deadline_ms: u64, pub caps: EvidenceCaps, pub dirs: DriverDirs, pub begun_ms: u64,
}

impl RealKernel {
    pub fn driver_dirs(&self, name: &str) -> DriverDirs;          // <state_dir>/drivers/<name>/{profile,work,evidence}
    /// Human only (I1). Validates, computes driver_id, protects the three dirs, persists
    /// `drivers/<name>`, logs `driver.mounted` (by = the human's device_id). A repeat mount of
    /// the same driver_id is idempotent (Ruling 13); a different manifest under the same name
    /// is refused with the remedy `vk driver umount <name>` first.
    pub fn mount_driver(&mut self, ctx: &Ctx, manifest: DriverManifest, observed_governed: bool) -> Result<DriverRow, KernelError>;
    pub fn unmount_driver(&mut self, name: &str, reason: &str) -> Result<Option<DriverRow>, KernelError>;   // logs driver.unmounted
    pub fn drivers(&self) -> Result<Vec<DriverRow>, KernelError>;
    pub fn driver(&self, name: &str) -> Result<Option<DriverRow>, KernelError>;
    pub fn degrade_driver(&mut self, name: &str, reason: &str) -> Result<(), KernelError>;  // state := Degraded, logs driver.unmounted{reason} (the row stays so `vk driver show` explains)
    /// Phase one under the lock: the seven refusals of spec §4.3, in order. Also: refuses
    /// a second in-flight act on the same driver (`in_flight` set). Increments the token's
    /// `actions` counter. Returns the plan; the daemon acts with the lock released.
    pub fn driver_act_begin(&mut self, token: &str, now_ms: u64, name: &str, action: &str, args: &serde_json::Value) -> Result<DriverActPlan, KernelError>;
    /// Phase two under the lock: clears in_flight; on Ok(outcome): attaches each evidence file
    /// as an artefact (kind = file name, label = register's label with Origin::ThirdPartyInbound
    /// added), counts bytes on the token (oversize remembered), appends `result.text` to the
    /// register's evidence when `action.to_register`, updates last_act_ms, logs `driver.acted`,
    /// returns `result`; on Err(host error): logs `driver.acted{ok:false, reason}`, degrades the
    /// driver for Timeout/Dead/Protocol, returns `Err(KernelError::Gate(reason))`.
    pub fn driver_act_settle(&mut self, token: &str, now_ms: u64, plan: &DriverActPlan, outcome: Result<ActOutcome, String>) -> Result<serde_json::Value, KernelError>;
}
```

The seven refusals, with their exact texts (tests match on the quoted fragment):
1. `harness_session(token)` fails → its own I1 error; the session's step must be `Running` and a `Harness` kind: `Gate("the harness step is not running")`.
2. step's `drivers` lacks `name`: `Gate("step {i} of task {id} did not declare driver {name}")`; driver row missing: `NotFound("driver {name}")`; `Degraded`: `Gate("driver {name} is degraded: {reason}; remount it")`.
3. action not in manifest: `Gate("driver {name} has no action {action}")`; args fail the JSON Schema (`jsonschema::validate(&schema, args)`): `Gate("args for {action}: {first error}")`.
4. `now_ms - row.last_act_ms < min_interval_ms`: `Gate("driver {name}: {remaining} ms before the next action")`.
5. `token.actions >= max_actions`: `Gate("step {i} reached its {n} actions")`.
6. task `Stopped` or `self.stops.stopped("node")`: `KernelError::Stopped("node")`.
7. `!register.label.flows_to(&manifest.clearance)`: `KernelError::I2("register of task {id} exceeds driver {name}'s clearance")`.

- [ ] **Step 1: Write the failing tests** (`tests/drivers.rs`, built on `tests/harness.rs`'s `open`/`machine`/`human` helpers — copy them, do not import across test binaries). A manifest fixture `maskai_like()` with actions `echo {case}` (evidence `note.txt`, `to_register: true`) and `look` (no evidence). Mount through `k.mount_driver(&human(1), m, true)`. Create a task with `create_task(&ctx, "recette", "report", Label::bottom(), &["fictional data only".into()], &["cases: N0-1".into()], vec![StepKind::Harness { name: "claude-code".into(), drivers: vec!["maskai-1".into()], instruction: Some("do".into()), max_actions: Some(2), timeout_s: None }, StepKind::Approve, StepKind::Release { to_dir: "runs".into() }])`, lease it with `harness_launch(&ctx, &t.id, "claude-code", 60_000)` to get a token.

```rust
#[test] fn mount_is_a_human_act_and_is_idempotent()            // machine ctx → I1; human mounts; second identical mount → same row, one driver.mounted event; different manifest same name → Gate "umount"
#[test] fn ls_drivers_and_show_one()                            // ns::resolve "/drivers" lists "maskai-1"; "/drivers/maskai-1" → Entry::Driver{row} with state ready
#[test] fn refusal_1_unknown_token_and_step_not_running()
#[test] fn refusal_2_undeclared_driver_missing_driver_degraded_driver()
#[test] fn refusal_3_unknown_action_and_bad_args()              // args {"case": 3} → "args for echo"
#[test] fn refusal_4_cadence()                                   // settle one act at now=1000 then begin at now=1500 with min_interval 2000 → "ms before"
#[test] fn refusal_5_max_actions()                              // max_actions 2: third begin refused
#[test] fn refusal_6_stop()                                      // k.stop(...) then begin → Stopped
#[test] fn refusal_7_label_above_clearance()                    // task label scope Personal vs clearance max_scope Business → I2
#[test] fn settle_attaches_evidence_logs_hashes_only_and_feeds_the_register()
    // outcome with evidence note.txt "hi" and result {"text":"journal line"}: register.artefacts has kind "note.txt", blob label origins contain third_party_inbound; ledger last kind "driver.acted" with args_hash/result_hash/evidence[0] == blob hash, no "hi" and no "journal line" anywhere in the payload JSON; register.evidence.last().content == "journal line"
#[test] fn settle_on_host_timeout_degrades_the_driver()         // Err("timeout") → driver.acted ok:false reason "timeout", row Degraded, driver.unmounted{reason:"timeout"} logged, begin now refuses with "degraded"
#[test] fn in_flight_act_blocks_a_second_begin_on_the_same_driver_until_settled()
#[test] fn a_relaunched_step_after_waiting_human_acts_again()   // Review Focus 2: settle, revoke run (simulate settle of harness with a failed run), harness_launch again → new token begins fine
#[test] fn judge_attaches_verdict_md()                           // mock arch judge → register.artefacts.last().kind == "verdict.md"; approval_subject names it
#[test] fn a_recipe_with_two_approves_and_a_draft_that_returns_nothing_fails_not_waits()   // Review Focus 5
#[test] fn harness_launch_uses_the_step_instruction_and_default_prompt_otherwise()
#[test] fn create_task_writes_constraints_and_evidence_into_the_register()
#[test] fn boot_rereads_the_drivers_table()                     // reopen the store: drivers() still lists maskai-1 with its state
#[test] fn recipe_steps_deserialize_as_step_kinds()             // every RecipeStep of contracts/examples/task_recipe/valid/recette.json round-trips through serde_json into a StepKind with identical JSON
```

- [ ] **Step 2: Run to verify failure** — `cargo test -p vk-kernel --test drivers` → compile errors.

- [ ] **Step 3: Implement** in this order: (a) `StepKind::Harness` fields + fix call sites; (b) `create_task` with `constraints`/`evidence` → after `submit_task`, read the register, push constraints and `Evidence { content, origin: Origin::OwnerAuthored, source_hash: hash_canonical(&content) }`, `write_register`; (c) Judge arm: after `infer`, `let reg = read_register; if let Some(q) = reg.open_questions.last() { self.attach_artefact(ctx, register, "verdict.md", q.as_bytes())?; }`; (d) `harness_launch`: `prompt: instruction.clone().unwrap_or_else(|| HARNESS_PROMPT.into())`, `timeout_s`, and `HarnessTokenState { …, actions: 0, step_index: i, drivers: drivers.clone() }`; (e) `drivers.rs` as specified — table `"drivers"`, key `name`; `in_flight: HashSet<String>` on `RealKernel`; `driver_act_begin` runs the seven checks then `self.in_flight.insert(name)`; `driver_act_settle` always removes `in_flight` first; the evidence label is `let mut l = reg.label.clone(); l.origins.insert(Origin::ThirdPartyInbound);` and the attach goes through a new private `attach_artefact_labelled(ctx, reg_id, kind, bytes, label)` that `attach_artefact` now delegates to; (f) `ns.rs`: root lists `"drivers"`; `["drivers"] => Entry::Dir { entries: names }`; `["drivers", name] => Entry::Driver(Box<DriverRow>)` (new variant; rename nothing about the existing `Device` variant); (g) `lib.rs` boot: nothing to spawn (the daemon does), but `boot()` validates every stored manifest and marks a row whose manifest no longer validates `Degraded{reason:"manifest invalid at boot"}`.

- [ ] **Step 4: Run** — `cargo test -p vk-kernel` and `cargo test --workspace` (call sites) → PASS.

- [ ] **Step 5: Clippy + commit**

```bash
cargo clippy --workspace --all-targets -- -D warnings
git add crates/vk-kernel crates/vk-ipc crates/vk-cli
git commit -m "feat(kernel): driver registry and /drivers; driver.act in three phases with the seven refusals; recipe-shaped harness steps; judge attaches verdict.md"
```

---

### Task 3: Daemon pool, `driver.*` syscalls, `vk_driver_act`, CLI verbs, `vk task submit --steps` (Opus)

**Files:**
- Create: `crates/vk-ipc/src/drivers.rs`
- Modify: `crates/vk-ipc/src/server.rs`, `crates/vk-ipc/src/lib.rs` (mod), `crates/vk-ipc/Cargo.toml` (`vk-driver`)
- Modify: `crates/vk-mcp/src/tools.rs`
- Modify: `crates/vk-cli/src/main.rs`, `crates/vk-cli/src/render.rs`
- Test: `crates/vk-ipc/tests/drivers.rs` (over the in-process transport the existing tests use, with Task 1's fake driver), `crates/vk-mcp` unit test for the tool mapping, `crates/vk-cli/tests/smoke.rs` additions

**Interfaces:**
- Produces:

```rust
// crates/vk-ipc/src/drivers.rs
pub struct DriverPool { hosts: Mutex<HashMap<String, vk_driver::DriverHost>> }
impl DriverPool {
    pub fn new() -> Arc<DriverPool>;
    /// Spawn (or re-spawn) the host for `row` with the platform governor (2 GiB, no CPU cap);
    /// returns what the host observed (`governed`).
    pub fn ensure(&self, row: &DriverRow, dirs: &DriverDirs) -> anyhow::Result<bool>;
    /// Run one act; the pool's mutex is held for this driver only for the duration of the act.
    pub fn act(&self, name: &str, action: &DriverAction, args: &Value, deadline: Duration, caps: EvidenceCaps, should_stop: &(dyn Fn() -> bool + Sync)) -> Result<ActOutcome, String>;
    pub fn drop_host(&self, name: &str);
}
// ServerConfig gains: pub drivers: Arc<DriverPool>
```

Syscalls (all in `dispatch`, following the `arch.*` arms' style):
- `driver.mount {manifest}` — in `takes_presence`; **refuses a machine principal** (`invariant("I1: mounting a driver puts a browser under this node's control, which is a human act; re-run it with a presence proof")`); validates the manifest; `hash_dir` of the directory of `command`'s script argument (the first arg ending in `.py`) must equal `files_sha256` (`bad("driver files hash …; regenerate the manifest with drivers/vkdriver/hash_dir.py")`); gets `driver_dirs` under the lock; releases the lock; `pool.ensure(row_candidate, dirs)` → `governed`; re-takes the lock → `mount_driver(ctx, manifest, governed)`; answers `{driver_id, name, governed, already_mounted}`.
- `driver.unmount {name}` → `pool.drop_host` after `unmount_driver(name, "operator")`, out from under the lock.
- `driver.ls`, `driver.show {name}` → the rows.
- `driver.act {token, driver, action, args}` — **outside the lock like `harness.run`**: `let plan = lock → driver_act_begin`; `if pool has no host for name → pool.ensure(row, dirs)` (lazy re-spawn after a daemon restart; failure → settle with `Err("driver would not start: …")`); `outcome = pool.act(...)` with `should_stop` = the `harness_run` closure; `lock → driver_act_settle` → answer `{ok: true, result}`. Not in `takes_presence`.

CLI:
- `vk mount driver --manifest FILE` (new `MountCmd::Driver { manifest: PathBuf }`): reads the file, `presence(&c)`, calls `driver.mount`, renders `render::mounted_driver`.
- `vk driver ls | show NAME | umount NAME` (new `Cmd::Driver { what: DriverCmd }`).
- `vk ls /drivers` renders through `render::ls` (`Some("drivers")` branch lists names; `/drivers/<name>` prints name, driver_id, state, hosts, actions, governed).
- `vk task submit --steps FILE` (`conflicts_with_all = ["plan","draft","harness","judge","approve","release"]`, and `goal` becomes optional when `--steps` is given): reads `TaskRecipe`, `validate()`, posts `task.create { goal, artefact_type: artefact_kind, constraints, evidence, steps }`.
- `harness.run` honours the plan's `timeout_s`: `timeout = min(settings.timeout, step timeout)`; the `HarnessConfig.prompt` is `harness_prompt(&launch_plan.prompt)` already.

vk-mcp: add to `tool_list` —

```json
{ "name": "vk_driver_act",
  "description": "Act through a driver this task's step declared (a browser the kernel controls). Returns the action's result; evidence is attached to the register by the kernel.",
  "inputSchema": { "type": "object", "properties": { "driver": {"type":"string"}, "action": {"type":"string"}, "args": {"type":"object"} }, "required": ["driver","action"], "additionalProperties": false } }
```
and in `call`: `"vk_driver_act" => ("driver.act", json!({ "token": token, "driver": str_arg("driver")?, "action": str_arg("action")?, "args": args.get("args").cloned().unwrap_or(json!({})) }))`; `render`: pretty JSON of `v["result"]`.

- [ ] **Step 1: Write the failing tests**
  - `vk-ipc/tests/drivers.rs`: `mount_requires_presence_then_mounts_and_spawns` (machine → E_INVARIANT; human → `governed` present, `vk ls /drivers` shows it); `act_round_trip_through_the_daemon` (create a recipe task with one harness step declaring the driver, `harness_launch` directly on the kernel to mint a token, then `driver.act` over the transport → result echoed, ledger has `driver.acted`); `act_without_token_is_refused`; `files_hash_mismatch_is_refused`; `unmount_drops_the_host` (pool has no host afterwards).
  - `vk-mcp/src/tools.rs` test: `vk_driver_act_maps_to_driver_act_with_the_token_and_default_args`.
  - `vk-cli/tests/smoke.rs`: `task_submit_with_steps_posts_the_recipe` against the smoke daemon with a mock arch: a recipe `plan(mock) → approve → release` creates a 3-step task; `vk driver ls` prints an empty table.
- [ ] **Step 2: Run to verify failure** — `cargo test -p vk-ipc --test drivers -p vk-mcp -p vk-cli --test smoke`.
- [ ] **Step 3: Implement** as specified. `DriverPool::act` must not hold the whole-map mutex during the act: hold it only to `remove` the host, act on the owned host, then re-insert (or drop it on Timeout/Dead so `ensure` re-spawns next time).
- [ ] **Step 4: Run** — the three test targets plus `cargo test --workspace` → PASS.
- [ ] **Step 5: Clippy + commit**

```bash
git add crates/vk-ipc crates/vk-mcp crates/vk-cli
git commit -m "feat(daemon,mcp,cli): driver pool and driver.* syscalls outside the lock; vk_driver_act; vk mount driver, vk driver ls/show/umount, vk task submit --steps"
```

---

### Task 4: The MaskAI recette driver — Python + Playwright, eleven actions, fixture, tests (Opus)

**Files:**
- Create: `drivers/vkdriver/__init__.py`, `drivers/vkdriver/protocol.py`, `drivers/vkdriver/hash_dir.py`
- Create: `drivers/maskai-recette/driver.py`, `drivers/maskai-recette/cases.json`, `drivers/maskai-recette/manifest.json`, `drivers/maskai-recette/requirements.txt` (`playwright==1.5x.y`, pinned to the version `pip index versions playwright` shows as current on 2026-10-14)
- Create: `drivers/maskai-recette/fixture/index.html`, `drivers/maskai-recette/fixture/serve.py`
- Create: `drivers/maskai-recette/tests/test_driver.py`, `drivers/maskai-recette/tests/test_hash_dir.py`
- Modify: `.gitignore` (`drivers/**/__pycache__/`, `drivers/**/.venv/`)

**Interfaces:**
- Consumes: protocol 1 of Task 1; `scripts/recette/evaluer.py` (`evaluate_case(case, obs, latency_budget_s)`), `scripts/recette/journal.py` (`add`, `effective`); oracles `docs/use-cases/recettage-chat-ia/corpus-v2/oracles-v2.json` and corpus files; the procedure's annexes A (stream interceptor on `/chat/stream`), B (textarea setter + `input` event + a typed space), and the paper-clip `input[type=file]`.
- Produces: the manifest the founder mounts and the eleven actions, each returning `result` with at least `{"text": "<one-line summary>"}` so `to_register` actions feed the register:

| Action | args | Does | result | evidence |
|---|---|---|---|---|
| `mesurer_chargement` | — | navigates to `https://app.maskai.fr/`, waits for `networkidle`, reads `performance.getEntriesByType("navigation")[0]` | `{ttfb_ms, dom_content_loaded_ms, load_ms, transfer_bytes, text}` | `timing.json`, `screen.png` |
| `nouvelle_conversation` | — | clicks the new-conversation control (selector list in `SELECTORS` at the top of driver.py: `[aria-label*="Nouvelle"]`, `button:has-text("Nouvelle conversation")`, fallback: navigate to `/`), arms the annexe-A interceptor, clears `window.__ev` | `{conversation: <url>, text}` | — |
| `joindre_document` | `{case}` | resolves `cases[case].file` under `corpus/` or `corpus-v2/`, `set_input_files` on the hidden `input[type=file]`, waits ≤ 60 s for the attachment chip; reads its `aria-label`; `Failed` → `ok:false, reason:"precondition", detail:"attachment failed"` | `{state, seconds, text}` | `attach.png`, `network.json` |
| `poser_question` | `{case}` | sets the textarea (annexe B), types a space, presses Enter; waits for `<END>` in `window.__ev` or the stop button to disappear, abandon at `3 × budget` (budget from oracles `global_checks.latency_budget_s`, default 60 s) → `ok:false, reason:"timeout"` **and journals KO disponibilité**; reads the last assistant message text; reads the request body sent to `/chat/stream` from the network capture (`sent`) | `{answer, sent, ttft_s, total_s, server_steps, text}` | `dom.html` (last answer's outerHTML), `network.json`, `answer.png` |
| `envoyer_message` | `{text}` | same as `poser_question` with free text (N1, N2, N3, N8 injections) | same | same |
| `lire_reponse` | — | the last assistant message text + outerHTML | `{answer, text}` | `dom.html` |
| `capturer_flux` | — | `window.__ev` since the last call + console messages + failed requests (status ≥ 400) | `{events, console, failed, text}` | `network.json` |
| `verdict_oracle` | `{case}` | `evaluate_case(case, last observation for that case, budget)` → verdict; appends the journal line (`step: "exec"`, `case`, `verdict`, `timing_s`, `nfr`, `evidence` summary); idempotent: a case already journaled returns the journaled verdict with `replayed: true` | `{verdict, missing, forbidden, leaks, span_issues, replayed, text}` | `journal.jsonl` (the new line only) |
| `lister_conversations` | — | reads the sidebar titles (N7) | `{titles, count, text}` | `screen.png` |
| `charge_concurrente` | `{cases: [a, b]}` | opens a second tab, attaches and asks both heavy cases within 2 s of each other, waits for both or abandon; journals one `disponibilite` line | `{a: {...}, b: {...}, text}` | `network.json`, `screen.png` |
| `capture_ecran` | — | screenshot | `{text}` | `screen.png` |
| `exporter_journal` | — | the journal lines appended since the previous export (cursor in `work/journal.cursor`) | `{lines, text: <those lines joined>}` | `journal.jsonl` |

(Twelve rows: `exporter_journal` plus the eleven business actions; `capture_ecran` counts among the eleven.)

Dialog guard: `page.on("dialog", lambda d: record_and_leave(d))` never accepts; any action that sees a dialog pending, or a `[role=dialog]` / `.modal` not in `KNOWN_MODALS` (cookie banner selector, which it dismisses by its documented button), returns `ok:false, reason:"dialog", detail: <dialog text ≤ 200 chars>`. Host allow-list: a `page.on("request")` handler aborts any request whose host is not in `VK_DRIVER_HOSTS` (env from the manifest, passed by the host — add `VK_DRIVER_HOSTS` to Task 1's env list, comma-separated) and a navigation outside returns `reason:"host"`. Browser: `launch_persistent_context(user_data_dir=VK_DRIVER_PROFILE_DIR, channel=VK_DRIVER_CHANNEL or None, headless=VK_DRIVER_HEADLESS=="1", accept_downloads=False)`.

- [ ] **Step 1: `protocol.py` and its test**

```python
# drivers/vkdriver/protocol.py
import json, sys, traceback
PROTOCOL = 1
def serve(name: str, version: str, actions: dict):
    """actions: {name: fn(args: dict, evidence_dir: str) -> (result: dict, evidence: list[dict])}.
    fn may raise DriverRefusal(reason, detail) for dialog|timeout|host|precondition."""
    out = sys.stdout
    def say(o): out.write(json.dumps(o, ensure_ascii=False) + "\n"); out.flush()
    say({"hello": {"protocol": PROTOCOL, "driver": name, "version": version, "actions": sorted(actions)}})
    for line in sys.stdin:
        line = line.strip()
        if not line: continue
        req = json.loads(line); rid = req["id"]
        fn = actions.get(req["action"])
        if fn is None: say({"id": rid, "ok": False, "reason": "internal", "detail": "no such action"}); continue
        try:
            result, evidence = fn(req.get("args") or {}, req["evidence_dir"])
            say({"id": rid, "ok": True, "result": result, "evidence": evidence})
        except DriverRefusal as r:
            say({"id": rid, "ok": False, "reason": r.reason, "detail": r.detail[:200]})
        except Exception as e:  # noqa: BLE001 — the host must always get an answer
            sys.stderr.write(traceback.format_exc())
            say({"id": rid, "ok": False, "reason": "internal", "detail": f"{type(e).__name__}: {e}"[:200]})
class DriverRefusal(Exception):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(detail); self.reason = reason; self.detail = detail
```

Test `test_protocol.py`: feed two requests on a pipe to a `serve` with an `echo` action and one raising `DriverRefusal("dialog","Save changes?")`; assert the hello line and both responses. Run: `python -I -m unittest discover -s drivers -p "test_*.py"`.

- [ ] **Step 2: `hash_dir.py` + cross-check test** — same algorithm as Task 0; `test_hash_dir.py` builds the `a.py`/`b.py`/`__pycache__` directory of Task 0's test and asserts the identical known-answer string. Run the unit tests → PASS.

- [ ] **Step 3: The fixture** — `fixture/index.html`: a page with a `textarea`, a hidden `input[type=file]` behind a paper-clip button that shows a chip with `aria-label="Attached: <name>"` (or `"Failed"` when the file name contains `fail`), a sidebar `<ul id="conversations">`, a "Nouvelle conversation" button, a cookie banner `#cookies` with `button#accept`, and a `fetch("/chat/stream", {method:"POST", body})` the page makes on send; `serve.py` (`http.server`, port from argv) answers `/chat/stream` with an SSE-like body streaming `{"node":"retrieve"}`, `{"node":"generate"}`, then the answer text in 3 chunks (the answer echoes the question upper-cased, and for a body containing `TRIGGER_DIALOG` the page calls `alert("unexpected")` after the answer; for `SLOW` it sleeps 8 s). Add `fixture-case-*` entries in `cases.json` whose `file` points into `fixture/corpus/` (two tiny txt files) and whose `must` matches the echo.

- [ ] **Step 4: `cases.json`** — one entry per N0–N9 case the recipe runs, authored from `PROCEDURE.md` §3 and the 2026-10-13 report: `{"id","level":"N4","kind":"attach_question|message|load|list|concurrent","file"?: "...", "question"?: "...", "oracle"?: "V2-L1", "nfr": ["disponibilite","veracite"], "budget_s": 60}`. Oracle-bound cases (`oracle`) take `must`/`must_not`/`pii` from `oracles-v2.json`; free cases (N1 empty send, N3 factual/calculation/trap, N8 three injection prompts, N9 "what leaves to the model") carry their own `must`/`must_not`. Minimum: 2 cases per level, the 10 oracle cases in N4/N6, 3 injections in N8.

- [ ] **Step 5: `driver.py`** — `SELECTORS` dict at top; `Browser` class wrapping the persistent context; one function per action as in the table; `observations: dict[case_id, obs]` kept in memory and persisted to `work/observations.json` after every action (so a relaunched harness finds them); journal via `journal.add` on `work/journal.jsonl`; `main()` builds the actions dict and calls `serve("maskai-recette", "0.1.0", actions)`. Evidence files are written under `evidence_dir` and referenced by absolute path.

- [ ] **Step 6: `tests/test_driver.py`** (requires `playwright install chromium`; the test skips with a message when `playwright` is not importable): starts `serve.py` on a free port, sets `VK_DRIVER_HOSTS=127.0.0.1`, `VK_DRIVER_HEADLESS=1`, `VK_DRIVER_PROFILE_DIR=<tmp>`, imports `driver` and runs the actions directly:
  - `test_replay_ten_of_ten`: 10 × (`nouvelle_conversation`, `joindre_document fixture-case-1`, `poser_question fixture-case-1`, `verdict_oracle`) → 10 OK verdicts, each `< 5 s`.
  - `test_dialog_guard_reports_and_never_answers`: `envoyer_message TRIGGER_DIALOG` → `DriverRefusal("dialog")`; the page's `window.__dialog_answered` stays false.
  - `test_host_allow_list`: `envoyer_message` whose fixture reply embeds an `<img src="http://example.invalid/x">` → the request is aborted and the capture lists it under `blocked`.
  - `test_timeout_journals_ko_disponibilite`: `SLOW` with `budget_s: 1` → `DriverRefusal("timeout")` and a journal line `verdict: "KO"` with `nfr` containing `disponibilite`.
  - `test_verdict_is_idempotent_after_relaunch`: new `driver` module instance over the same work dir → `verdict_oracle` returns `replayed: true`.
  - `test_failed_attachment_is_a_precondition_refusal`: file `fail.txt` → `DriverRefusal("precondition")`.
  Run: `python -I -m unittest discover -s drivers/maskai-recette/tests -t .` → PASS.

- [ ] **Step 7: `manifest.json`** — the Task 0 valid example with `files_sha256` = `python -I drivers/vkdriver/hash_dir.py drivers/maskai-recette` and `command: ["python","-I","drivers/maskai-recette/driver.py"]`; `cargo test -p vk-contracts` still green (copy the file over the example).

- [ ] **Step 8: Commit**

```bash
git add drivers .gitignore contracts/examples
git commit -m "feat(drivers): maskai-recette driver — protocol loop, eleven compiled actions over Playwright, dialog guard, host allow-list, fixture page and 10/10 tests"
```

---

### Task 5: Recipe, runbook, and the first live recette (Opus; founder in the loop)

**Files:**
- Create: `recipes/recette-maskai.json`, `docs/runbooks/recette-maskai.md`
- Create after the run: `docs/use-cases/recettage-chat-ia/runs/<date>/` (released by the kernel, then copied in by the runbook's last step)

**Interfaces:**
- Consumes: everything above. The recipe:

```json
{
  "goal": "Recette fonctionnelle et benchmark de MaskAI (app.maskai.fr), version courante, selon PROCEDURE.md: niveaux N0 à N9, six ENF, verdicts par oracles, rapport public expurgé.",
  "artefact_kind": "report.md",
  "constraints": [
    "Données fictives uniquement (corpus/ et corpus-v2/).",
    "Pas de création de compte, pas de mot de passe, pas d'achat, pas de message au nom de l'utilisateur.",
    "Ne rien supprimer qui existait avant ; lister ce qui a été créé.",
    "Un défaut de sécurité est signalé tout de suite ; le rapport reste privé tant qu'il n'est pas corrigé.",
    "Version publique : aucune adresse interne, aucun compte, aucune donnée personnelle réelle."
  ],
  "evidence": [
    "Procédure : sept étapes, grille N0–N9, barème §7 de PROCEDURE.md (résumé joint).",
    "Cas : voir drivers/maskai-recette/cases.json ; identifiants par niveau dans l'instruction de chaque pas."
  ],
  "steps": [
    { "kind": "plan", "arch_id": "<PLAN_ARCH>" },
    { "kind": "harness", "name": "claude-code", "drivers": ["maskai-1"], "max_actions": 60, "timeout_s": 1800,
      "instruction": "Niveau N0 (socle). Appelle vk_driver_act sur le driver maskai-1 : mesurer_chargement trois fois, puis capture_ecran, puis exporter_journal. Consigne chaque résultat avec vk_log. N'écris aucun fichier." },
    { "kind": "harness", "name": "claude-code", "drivers": ["maskai-1"], "max_actions": 80, "timeout_s": 2400,
      "instruction": "Niveau N1 (conversation). Pour chaque cas de la liste [N1-1, N1-2, N1-3] : nouvelle_conversation ; envoyer_message avec le texte du cas (cases.json) ; verdict_oracle. Termine par exporter_journal." },
    "… one harness step per level N2–N9, same shape, the case ids of cases.json for that level; N4 and N6 use joindre_document before poser_question; N8 includes charge_concurrente {cases:[V2-V1, V2-V3]} …",
    { "kind": "judge", "arch_id": "<JUDGE_ARCH>" },
    { "kind": "approve" },
    { "kind": "draft", "arch_id": "<DRAFT_ARCH>" },
    { "kind": "approve" },
    { "kind": "release", "to_dir": "recettage-chat-ia/runs/2026-10" }
  ]
}
```

- [ ] **Step 1: Founder checkpoint — arches.** Ask (one AskUserQuestion): plan/judge/draft on the `claude-code` arches (`vk mount claude-code` mounts draft + judge) or on `anthropic` (`vk secret set anthropic`, `vk mount anthropic`); Chrome or Edge channel. Fill `<PLAN_ARCH>`, `<JUDGE_ARCH>`, `<DRAFT_ARCH>` with the arch ids `vk ls /arches` prints.

- [ ] **Step 2: Write the recipe** with all sixteen steps spelled out (no ellipsis in the committed file) and `TaskRecipe::validate` green: `vk task submit --steps recipes/recette-maskai.json` against the smoke daemon with mock arches creates a 16-step task (add this as a `vk-cli` smoke test with the arch ids replaced by the mock's).

- [ ] **Step 3: Write the runbook** `docs/runbooks/recette-maskai.md` (French, embedded-engineer register, numbered, each step with the exact command and the expected output line):
  1. Prerequisites per mount (spec P8): Python 3.13 + `pip install -r drivers/maskai-recette/requirements.txt` + `playwright install chromium` (or Chrome channel); `claude` on PATH if `claude-code` arches; `vk secret set anthropic` if API arch; no Docker needed.
  2. Build: `cargo build --release -p vk-cli -p vkd -p vk-mcp`.
  3. Start the interactive daemon: `vkd --foreground` in its own terminal (Task 7 adds the logon scheduled task; for the pilot a terminal is the documented way).
  4. Enrol/verify the device key; `vk liveness renew --business <id>`.
  5. Mount arches; `vk ls /arches`.
  6. `vk mount driver --manifest drivers/maskai-recette/manifest.json` → Chrome opens on the dedicated profile → **sign in to MaskAI by hand** → `vk ls /drivers` shows `ready`, `governed: true`.
  7. `vk task submit --steps recipes/recette-maskai.json` → task id.
  8. `vk task step <id>` (plan) → `vk harness run <id>` **ten times**, reading `vk task show <id>` between runs; on `waiting_human` (dialog), resolve in Chrome and re-run.
  9. `vk task step <id>` (judge) → `vk approve <id>` (KO sécurité + verdict) → `vk task step <id>` (draft) → `vk approve <id>` → `vk task step <id>` (release).
  10. `vk ls /ledger` and `vk task show <id>`: the release path; copy the released directory into `docs/use-cases/recettage-chat-ia/runs/<date>/`, redact per constraint 5, commit.
  11. What to do on each failure mode of spec §6.

- [ ] **Step 4: Dry run on the fixture** — a second recipe `recipes/recette-fixture.json` (N0 + one N4 step, mock arches) against `serve.py` with a `manifest-fixture.json` (`hosts: ["127.0.0.1"]`, `headless: true`) proves the whole chain end to end on the developer machine: plan → harness → judge → approve → draft → approve → release, `vk ls /ledger` shows `driver.mounted`, ≥ 4 `driver.acted`, 2 `approval.recorded`, 1 `artefact.released`. Record the ledger tail in the task report.

- [ ] **Step 5: Founder checkpoint — go for the live run.** Then the live run on the founder's node, per the runbook, with Claude driving the terminal in the session and the founder approving. Deliverables: the released report, journal and evidence; the ledger tail; the delta against `2026-10-13-maskai-benchmark.md`.

- [ ] **Step 6: Commit** — recipe, runbook, the fixture recipe, the smoke test, and the run directory.

```bash
git add recipes docs/runbooks crates/vk-cli/tests docs/use-cases/recettage-chat-ia/runs
git commit -m "feat(recette): sixteen-step MaskAI recipe, founder runbook, fixture dry run; first governed recette released"
```

**Milestone A exit:** the REX material exists and `vk ls /ledger` proves the chain. Tell the founder in French, embedded register, what the ledger shows and what the recette found.

---

## Milestone B — hardening

### Task 6: CI `driver-browser` job, Windows containment test, documentation (Sonnet)

**Files:**
- Modify: `.github/workflows/ci.yml`, `docs/technical-readme.md`, `contracts/tcb.md`, `contracts/README.md`
- Test: the CI job itself; `crates/vk-driver/tests/fake_driver.rs::tree_is_contained_on_windows` already runs in `test` on `windows-latest`

- [ ] **Step 1: CI job** — after `arch-ollama`, add:

```yaml
  driver-browser:
    # The MaskAI driver against its local fixture page, headless Chromium: the
    # eleven actions, the dialog guard and the host allow-list. Every push.
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.13" }
      - run: pip install -r drivers/maskai-recette/requirements.txt && python -m playwright install --with-deps chromium
      - run: python -I -m unittest discover -s drivers -p "test_*.py" -t .
      - run: python -I -m unittest discover -s drivers/maskai-recette/tests -t .
```

- [ ] **Step 2: README** — in `docs/technical-readme.md` add after the arches sections "### Drivers: a browser this kernel controls" — what a driver is, `vk mount driver`, `vk driver ls|show|umount`, `vk ls /drivers`, the three ledger kinds, what `driver.acted` carries, the evidence as artefacts, `vk task submit --steps`, and the recette runbook link. Keep the register's voice (one fact per sentence).
- [ ] **Step 3: TCB** — `contracts/tcb.md` gains "## Drivers (pilot)" with spec §11's text verbatim, plus the env variables the host passes and the fact that the driver's Python runs with `-I`.
- [ ] **Step 4: contracts/README** — mention the five new schemas and that `driver_manifest.program.files_sha256` is `hash_dir`.
- [ ] **Step 5: Push, arm a CI watcher (Windows job ≈ 45–60 min), fix what it says, commit.**

```bash
git add .github docs contracts
git commit -m "ci(driver-browser): fixture-driven driver tests on every push; docs: drivers in the README, TCB and contracts index"
```

### Task 7: Failure-mode tests the live run did not exercise, logon task, whole-branch review, merge (Opus; review Fable)

**Files:**
- Modify: `crates/vk-kernel/tests/drivers.rs`, `crates/vk-ipc/tests/drivers.rs`, `crates/vk-driver/tests/fake_driver.rs`
- Create: `scripts/vkd-logon-task.ps1` (registers a Windows scheduled task "Aubier vkd" at logon, `vkd --foreground`, restart on failure every minute up to 3 times — spec P7 condition (b))
- Modify: `docs/runbooks/recette-maskai.md` step 3 to point at the script

- [ ] **Step 1: Tests to add** (each written failing first, then made green):
  - kernel: `hosts_seen_from_the_host_lands_on_driver_acted`; `evidence_bytes_count_toward_the_run_cap_and_oversize_fails_settle` (per-run 16 MiB via `bytes_attached`); `a_degraded_driver_can_be_remounted_after_umount`; `boot_marks_a_manifest_that_no_longer_validates_degraded`.
  - daemon: `a_dead_host_is_respawned_lazily_on_the_next_act` (kill the fake driver's process between two acts → second act degrades, `vk driver umount` + `vk mount driver` → acts again); `two_acts_on_two_drivers_run_concurrently` (pool mutex per driver).
  - host: `evidence_symlink_out_of_the_dir_is_refused` (Review Focus 4, Unix symlink / Windows junction via `mklink /J` when available).
- [ ] **Step 2: The logon task script** with `Register-ScheduledTask` (`-Settings (New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1))`, `-Trigger (New-ScheduledTaskTrigger -AtLogOn)`, interactive token). Document in the runbook; founder-run, elevated.
- [ ] **Step 3: Whole-branch review** — `superpowers:requesting-code-review` with the Fable reviewer over `master..pilot-recette-device`, two rounds maximum, fix wave committed as `fix(pilot): review round N`.
- [ ] **Step 4: Merge and tag** — `superpowers:finishing-a-development-branch`: founder decides merge; `git merge --no-ff pilot-recette-device` into master, tag `pilot-driver`, push, CI watcher armed.
- [ ] **Step 5: Memory** — update `next-chantiers-2026-10-14.md`: pilot done, what the REX said, what the concept-1 driver contract inherits.

---

## Self-review (done at writing time)

**Spec coverage.** §3.1 components → Tasks 0–4; §3.2 interactive session, profile under state dir → Tasks 2 (dirs), 3 (pool), 7 (logon task); §3.3 trust statement → Task 6 TCB + `hosts_seen` Tasks 1–2; §4.1 manifest → Task 0 (`governed` read back in Task 3's mount); §4.2 protocol → Task 1 + Task 4 `protocol.py`; §4.3 seven refusals → Task 2 tests by number; §4.4 payloads → Task 0, written in Task 2; §4.5 actions → Task 4 (eleven, refinement noted); §5.1 recipes → Tasks 0, 2, 3; §5.2 sixteen steps → Task 5 recipe; Judge attaches `verdict.md` → Task 2; §5.3 resumption → Task 4 (`observations.json`, `replayed`) and Task 2 test; §6 failure modes → dialog/timeout/host in Task 4 tests, dead/mute in Tasks 1–3 and 7, STOP in Task 1, daemon restart in Task 7, runaway in Task 2; §7 tests → Tasks 0–4, 6; §8 exit criteria → Task 5 step 4 (fixture) and step 5 (live); §9 out of scope → nothing here builds Robot Framework, promotion, the service path or replay mode; §10 open items → Task 5 step 1; §11 TCB → Task 6.

**Placeholders.** The recipe in Task 5 shows one abbreviated line for levels N2–N9 in this plan; step 2 of that task requires the committed file to spell all sixteen out — the abbreviation is of the plan text, not of the deliverable. `<PLAN_ARCH>` etc. are filled at Task 5 step 1 by a founder decision, which is the only way those values can exist.

**Type consistency.** `DriverActPlan.caps: EvidenceCaps` comes from `vk_driver` (Task 1) and is used by Task 2; `ActOutcome` likewise; `DriverRow`/`DriverDirs` defined in Task 2 and consumed by Task 3's pool; `RecipeStep` (contracts) and `StepKind` (kernel) share the serde shape, pinned by Task 2's test `recipe_steps_deserialize_as_step_kinds`. `vk_driver_act` args → `driver.act {token, driver, action, args}` → `driver_act_begin(token, now, name, action, args)`: same four names throughout.

**Review Focus.** Lines 1, 4 → Task 1 tests; 2, 5 → Task 2 tests; 3 → Task 0 test. All five have an owning test.
