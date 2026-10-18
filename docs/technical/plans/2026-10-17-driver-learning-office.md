# Driver Learning from the Firm's Documents (Office COM) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `vk driver learn` turns a corpus of the firm's Word documents into a qualified, mountable driver — inventory of the application's type library, deterministic OOXML mining, a typed plan, a deterministic generator, a qualification replay — and the same engine, fed presentations, produces the PowerPoint driver with no application-specific code.

**Architecture:** A Python package `drivers/vkforge/` holds five pure stages (`inventory`, `ontology`, `mine`, `plan`, `generate`) and a replay (`qualify`), each a file in and a file out, plus one thin COM runtime (`runtime/com.py`) with a fake double; a seventh module, `debrief`, reads a governed run's evidence and writes proposals for the operator (founder decision 2026-10-17, Task 11). The kernel gains `DriverKind::Com`, the `applications` field, the `DriverPlan` schema, two ledger kinds and the human act `driver.learn`, which spawns `vkforge` under the Job governor. The generated driver speaks protocol 1 through `drivers/vkdriver/protocol.py`, exactly like the hand-written MaskAI driver.

**Tech Stack:** Python 3.13 (stdlib, `lxml`, `comtypes` on Windows only), Rust (MSVC) for contracts/kernel/IPC/CLI, Office Open XML read with `zipfile` + `lxml`, GitHub Actions (`driver-browser` on Ubuntu for the Python suite, new `driver-learn` on Windows for the end-to-end).

**Spec:** `docs/technical/specs/2026-10-17-driver-learning-office-design.md` (decisions L1–L8, contracts §4, flows §5, failure modes §6, tests §7, exit §8, TCB §11). The driver contract of `docs/technical/specs/2026-10-14-device-driver-contract-and-recette-pilot-design.md` (rulings R5, R6, R18, R25, R28) stays binding.

## Global Constraints

- **Parallel chantier:** the packaging plan (`2026-10-17-driver-packaging-and-deployment.md`) adds `DriverSource`, `ExecutionContext`, `needs_input_desktop` to `DriverManifest` and a `DriverLink` seam in `vk-driver`. This plan does NOT touch those fields, `crates/vk-driver/src/link.rs`, or `crates/vk-capital`. Its contract additions are exactly: `DriverKind::Com`; `DriverManifest.applications: Vec<String>` (default empty, `skip_serializing_if = "Vec::is_empty"` so every existing `driver_id` is unchanged); `DriverProgram.runtime = "python-com"` (a string value, no enum); the `DriverPlan` schema in `crates/vk-contracts/src/plan.rs`; the payloads `DriverLearned` and `DriverQualified`; the kinds `driver.learned` (human act, presence proof like `driver.mount`) and `driver.qualified` (kernel-written).
- Validation: `kind: com` requires non-empty `applications` (each an upper-case executable name ending in `.EXE`, no path) and allows empty `hosts`; `kind: browser` keeps today's rules unchanged (non-empty `hosts`, `applications` must be empty).
- `drivers/vkforge/` is Python 3.13 with the stdlib, `lxml` and `comtypes` only; `comtypes` is imported lazily and only on Windows. No `python-docx`, no model call inside the package.
- **L-P1 (fake application):** the generated driver's COM calls go through one adapter module `drivers/vkforge/runtime/com.py` (`Application(name)`, `open(path)`, attribute access by the binding table). A `FakeApplication` driven by the same binding table edits the OOXML of the file directly (with the miner's writer twin), so `qualify` runs against the fake in CI and against the real Word on the node, through the same driver bytes.
- **L-P2 (how `learn` runs):** the spec's "run as a driver of the kernel" is implemented as a governed one-shot process: `vk-ipc` spawns `python -I -m vkforge <stage> …` under `vk_harness::confine::for_this_platform(1 GiB, None)` with a deadline, from the `drivers/vkforge` root the daemon was started with, never through protocol 1 (a pipeline is not request/response). The Job governor does not block the network; the stages that could reach it (`plan` with an arch) do so only through the kernel's `infer`, so the no-network claim of spec §3.3 rests on the code calling no socket, which a test pins (no `socket`/`urllib`/`http` import in the package).
- **L-P3 (fallback, spec §5.3):** `vk driver learn --deliberate VERB` produces a *proposed projection rule* from the catalogue slice and the verb's postcondition through the harness (Claude Code confined, one task), written to `<out>/proposals/<verb>.json` for human review; COM-call tracing of a live deliberate run is deferred to a later chantier and said so in the TCB.
- Zero model tokens on the nominal path: `plan` calls an arch only for `ambiguities`, only when `--arch NAME` is given, through the kernel's `infer` (`Capability::Draft`) on a register that holds the ambiguity schema and nothing else; with no arch the ambiguity is purged and listed.
- The miner reads only `word/styles.xml`, `word/settings.xml`, `word/header*.xml`, `word/footer*.xml`, `word/numbering.xml`, `docProps/app.xml`, and in `word/document.xml` only `w:pPr`/`w:rPr` attributes (never `w:t`); for `.pptx` the `ppt/slideMasters/*.xml`, `ppt/slideLayouts/*.xml`, `ppt/presentation.xml`, `docProps/app.xml`, and in `ppt/slides/*.xml` only `a:pPr`/`a:rPr` attributes (never `a:t`). Every free string is replaced by `<redacted:sha256:…>` in `invariants.json`; its clear value goes to `<out>/.vault.json` (private file, deleted after generation).
- Every refusal is one line in the kernel's voice ending in `remedy: …` (R28); the generated driver's own refusal reasons are the protocol's five words.
- Generated files are byte-reproducible: the generator writes with `\n` line endings, sorted keys, no timestamps; the manifest's `files_sha256` is `hash_dir` of the output directory (R5).
- **L-P5 (the debrief, founder decision 2026-10-17, Task 11):** `vkforge debrief` is a stage like the others (deterministic, stdlib only, no model, no socket) whose requirements are `docs/technical/debriefs/2026-10-17-maskai-driver.md` §4; `driver.debrief` is a machine act (no presence proof) run under the same governor as `learn`'s stages; it adds **no ledger kind** — the optional kernel-written row `driver.debriefed {run_task_ids, debrief_sha256, proposals_sha256}` is an open founder decision — and it **applies nothing**: its proposals are the operator's to apply, or, for a learned driver, the deliberate run's (L-P3).
- Model tiering (founder rule): Sonnet for Tasks 0, 1, 2, 3, 5, 10; Opus for Tasks 4, 6, 7, 8, 9, 11; Task 12 is the controller's and the founder's, on the node; Fable for the final whole-branch review. One fresh reviewer per task before the next starts.
- Commits: conventional prefix, one sentence of intent, trailers `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01UUDLpkwxsZXmLptqCeNL2R`.

## Review Focus

1. **A corpus folder that mixes `.docx` with `.doc`, `.docm`, an encrypted `.docx` and a `~$lock` file** — the miner must skip them, count them in `corpus.skipped`, never open Word, and still produce invariants from the rest; Task 5 adds the mixed-folder test.
2. **A French Office whose built-in style is named "Titre 1"** — nothing generated may depend on a localised name: the generated `driver.py` must not contain any `NameLocal` string from the catalogue, only built-in constants; Task 7 adds the string-search test over the generated bytes.
3. **Two body fonts at support 0.5 each** — no invariant reaches the threshold, the planner raises one ambiguity, and with no arch the plan is still produced (without the font action) and the ambiguity listed; Task 6 adds the tie test.
4. **The document is open in the user's Word while `qualify` runs** — the generated `open` must refuse `precondition` (read-only document) within the deadline, never hang; Task 7's runtime adapter test and Task 8's locked-copy test pin it.
5. **The corpus changes between `mine` and `qualify`** — `content_sha256` differs, qualification is refused before any copy is made; Task 8 adds the test.
6. **An empty answer whose stream never reached `call_llm` (N9-2 of 17/10)** — the product failed, not the selector: the debrief lists it in `empty_without_llm` and never turns it into a selector proposal, while essai 1's N1-1 and N1-3 (call_llm 2444 and 2391 ms) make one; Task 11's 17/10 test pins both.

---

### Task 0: `drivers/vkforge/` skeleton, test runner, CI wiring (Sonnet)

**Files:**
- Create: `drivers/vkforge/__init__.py`, `drivers/vkforge/__main__.py`, `drivers/vkforge/errors.py`, `drivers/vkforge/requirements.txt`, `drivers/vkforge/tests/__init__.py`, `drivers/vkforge/tests/test_package.py`
- Modify: `.github/workflows/ci.yml` (`driver-browser` job: install `drivers/vkforge/requirements.txt`, run the vkforge suite), `scripts/check-workflows.py` (no job change yet)

**Interfaces:**
- Produces: `vkforge.errors.Refusal(what, remedy)` (`str(e)` = `"<what>; remedy: <remedy>"`), the CLI entry `python -I -m vkforge <stage> …` with `argparse` subparsers registered by later tasks through `vkforge.__main__.STAGES: dict[str, Callable[[argparse.Namespace], int]]`.

- [ ] **Step 1: Failing test** (`tests/test_package.py`)

```python
import subprocess, sys, unittest, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]  # drivers/


class PackageTest(unittest.TestCase):
    def test_the_cli_lists_its_stages_and_refuses_an_unknown_one(self):
        out = subprocess.run([sys.executable, "-I", "-m", "vkforge", "--help"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        for stage in ("inventory", "bind", "mine", "plan", "generate", "qualify"):
            self.assertIn(stage, out.stdout)
        bad = subprocess.run([sys.executable, "-I", "-m", "vkforge", "nope"], cwd=ROOT, capture_output=True, text=True)
        self.assertNotEqual(bad.returncode, 0)

    def test_a_refusal_prints_one_line_with_a_remedy(self):
        from vkforge.errors import Refusal
        e = Refusal("corpus has 3 files", "more documents of one kind")
        self.assertEqual(str(e), "corpus has 3 files; remedy: more documents of one kind")

    def test_the_package_opens_no_socket(self):
        for p in (ROOT / "vkforge").rglob("*.py"):
            if "tests" in p.parts:
                continue
            text = p.read_text(encoding="utf-8")
            for banned in ("import socket", "import urllib", "import http", "import requests"):
                self.assertNotIn(banned, text, f"{p}: {banned}")
```

- [ ] **Step 2: Run** `python -I -m unittest discover -s drivers/vkforge/tests -t .` → FAIL (no module).

- [ ] **Step 3: Implement**

`errors.py`:

```python
class Refusal(Exception):
    """One line, the kernel's voice: what was wrong, then the remedy."""

    def __init__(self, what: str, remedy: str):
        super().__init__(f"{what}; remedy: {remedy}")
        self.what, self.remedy = what, remedy
```

`__main__.py`:

```python
"""python -I -m vkforge <stage> …  — the learning pipeline, one stage per call."""
import argparse
import sys

from .errors import Refusal

STAGES = {}  # name -> (add_arguments(parser), run(ns) -> int); filled by the stage modules


def _register():
    from . import inventory, ontology, mine, plan, generate, qualify  # noqa: F401  (each registers itself)


def main(argv=None) -> int:
    _register()
    p = argparse.ArgumentParser(prog="vkforge")
    sub = p.add_subparsers(dest="stage", required=True)
    for name, (add, _run) in STAGES.items():
        add(sub.add_parser(name))
    ns = p.parse_args(argv)
    try:
        return STAGES[ns.stage][1](ns)
    except Refusal as e:
        sys.stderr.write(f"vkforge {ns.stage}: {e}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
```

Each stage module (created empty in this task, filled later) ends with `STAGES["<name>"] = (add_arguments, run)` where both are module functions; in this task `add_arguments` adds nothing and `run` raises `Refusal("not implemented", "wait for the next task")`.

`requirements.txt`: `lxml==6.0.2` (or the current release) and `comtypes==1.4.12; sys_platform == "win32"` (pin the versions available at implementation time).

CI: in `driver-browser`, after the MaskAI requirements, `pip install -r drivers/vkforge/requirements.txt` and a step `python -I -m unittest discover -s drivers/vkforge/tests -t .`.

- [ ] **Step 4: Run green; commit** `feat(vkforge): the learning pipeline's package, CLI and refusal voice`

---

### Task 1: `DriverKind::Com` and `applications` (identity preserved) (Sonnet)

**Files:**
- Modify: `crates/vk-contracts/src/driver.rs` (`DriverKind` 14-18, `DriverManifest` 71-85, `validate` 166-268), `crates/vk-ipc/src/drivers.rs` (`memory_cap_of` 45-52: a `Com` driver's default cap is 1 GiB), `crates/vk-driver/src/lib.rs` (around 305-311: `VK_DRIVER_APPLICATIONS` env, comma-joined, beside `VK_DRIVER_HOSTS`)
- Create: `contracts/examples/driver_manifest/valid/word_com.json`, `contracts/examples/driver_manifest/invalid/com_without_applications.json`, `contracts/examples/driver_manifest/invalid/browser_with_applications.json`
- Regenerate: `contracts/schemas/driver_manifest.schema.json`

**Interfaces:**
- Produces: `DriverKind::Com` (serialised `"com"`), `#[serde(default, skip_serializing_if = "Vec::is_empty")] pub applications: Vec<String>` on `DriverManifest`, `pub const DRIVER_MEMORY_COM: u64 = 1 << 30`.

- [ ] **Step 1: Record the current identity (throwaway)** — as in the packaging plan's Task 1: a test that panics with `driver_id()` of `contracts/examples/driver_manifest/valid/maskai.json`; copy the string as `PILOT_ID`; delete the test. (If the packaging branch has already landed `PILOT_ID`, reuse its constant.)

- [ ] **Step 2: Failing tests** (in `driver.rs`)

```rust
#[test]
fn the_pilot_manifest_keeps_its_id_with_applications_absent() {
    let m: DriverManifest = serde_json::from_str(include_str!("../../../contracts/examples/driver_manifest/valid/maskai.json")).unwrap();
    assert!(m.applications.is_empty());
    assert_eq!(m.driver_id(), PILOT_ID);
}

#[test]
fn a_com_driver_names_its_applications_and_may_have_no_hosts() {
    let m: DriverManifest = serde_json::from_str(include_str!("../../../contracts/examples/driver_manifest/valid/word_com.json")).unwrap();
    assert_eq!(m.kind, DriverKind::Com);
    assert_eq!(m.applications, vec!["WINWORD.EXE"]);
    assert!(m.hosts.is_empty());
    m.validate().unwrap();
    let mut bad = m.clone();
    bad.applications.clear();
    assert!(bad.validate().unwrap_err().to_string().contains("applications is empty"));
    let mut bad = m.clone();
    bad.applications = vec!["winword.exe".into()];
    assert!(bad.validate().unwrap_err().to_string().contains("upper-case"));
    let mut bad = m.clone();
    bad.applications = vec!["C:\\WINWORD.EXE".into()];
    assert!(bad.validate().unwrap_err().to_string().contains("no path"));
}

#[test]
fn a_browser_driver_may_not_name_applications() {
    let mut m: DriverManifest = serde_json::from_str(include_str!("../../../contracts/examples/driver_manifest/valid/maskai.json")).unwrap();
    m.applications = vec!["WINWORD.EXE".into()];
    assert!(m.validate().unwrap_err().to_string().contains("applications must be empty"));
}
```

- [ ] **Step 3: Run** `cargo test --locked -p vk-contracts a_com_driver` → compile error.

- [ ] **Step 4: Implement**

```rust
pub enum DriverKind {
    Browser,
    /// A desktop application driven through its COM object model; the
    /// application's process is not in the driver's Job (spike C).
    Com,
}
```

Field after `hosts`:

```rust
    /// Executable names of the applications a `com` driver drives, as Windows
    /// reports them (`WINWORD.EXE`); empty for a browser driver.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub applications: Vec<String>,
```

In `validate()`, replace the `hosts.is_empty()` check with:

```rust
        match self.kind {
            DriverKind::Browser => {
                if self.hosts.is_empty() {
                    return invalid("hosts is empty: list the host names the driver may reach");
                }
                if !self.applications.is_empty() {
                    return invalid("applications must be empty for a browser driver: it drives pages, not programs");
                }
            }
            DriverKind::Com => {
                if self.applications.is_empty() {
                    return invalid("applications is empty: name the executables a com driver drives (WINWORD.EXE)");
                }
                for a in &self.applications {
                    if a.contains('\\') || a.contains('/') {
                        return invalid(format!("application `{a}`: an executable name, no path"));
                    }
                    if a != &a.to_ascii_uppercase() || !a.ends_with(".EXE") {
                        return invalid(format!("application `{a}`: upper-case executable name ending in .EXE, as Windows reports it"));
                    }
                }
            }
        }
```

(the per-host checks stay and run on an empty list harmlessly). `vk-ipc/src/drivers.rs`: `memory_cap_of` returns `DRIVER_MEMORY_COM` for `Com`. `vk-driver/src/lib.rs`: `.env("VK_DRIVER_APPLICATIONS", manifest.applications.join(","))`.

`word_com.json`: name `word-maison`, kind `com`, `program: {runtime: "python-com", command: [".venv/Scripts/python.exe","-I","driver.py"], files_sha256: "sha256:" + 64 hex, browser_channel: null, headless: true}`, `hosts: []`, `applications: ["WINWORD.EXE"]`, one action `appliquer_style_maison` with args `{document: string}` and evidence `["post.json"]`, `min_interval_ms: 0`, `max_action_ms: 120000`, clearance `business`, `governed: false`.

- [ ] **Step 5:** `cargo run -p vk-contracts --bin gen-schemas`; `cargo test --locked -p vk-contracts -p vk-ipc -p vk-driver` green.
- [ ] **Step 6: Commit** `feat(contracts): DriverKind::Com and applications; identities unchanged`

---

### Task 2: `DriverPlan`, `driver.learned`, `driver.qualified` (Sonnet)

**Files:**
- Create: `crates/vk-contracts/src/plan.rs`, `contracts/examples/driver_plan/valid/word_maison.json`, `contracts/examples/driver_plan/invalid/{unknown_verb_shape,no_actions}.json`, `contracts/examples/driver_learned/valid/first.json`, `contracts/examples/driver_qualified/valid/first.json`
- Modify: `crates/vk-contracts/src/lib.rs` (`pub mod plan`, registry), `crates/vk-contracts/src/ledger.rs` (`ALLOWED_KINDS` + two tests), `crates/vk-cli/src/man.rs` (`SCHEMAS` three rows), `contracts/README.md` §Drivers, `docs/technical/technical-readme.md` payload table, `contracts/tcb.md` §Drivers (one sentence each, spec §11)
- Regenerate: `contracts/schemas/{driver_plan,driver_learned,driver_qualified}.schema.json`

**Interfaces:**

```rust
// plan.rs — the artefact a human reads and approves (spec §4.5)
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct DriverPlan {
    pub family: String,                 // "office-document"
    pub app: String,                    // "word" | "powerpoint"
    pub ontology_sha256: String,
    pub catalogue_sha256: String,
    pub invariants_sha256: String,
    pub actions: Vec<PlanAction>,
    #[serde(default)] pub ambiguities: Vec<Ambiguity>,
    pub qualification: Qualification,
}
#[derive(…)] #[serde(deny_unknown_fields)]
pub struct PlanAction { pub name: String, pub reversible: bool, pub steps: Vec<PlanStep>, pub pre: Vec<String>, pub post: Vec<String> }
#[derive(…)] #[serde(deny_unknown_fields)]
pub struct PlanStep { pub verb: String, #[serde(default)] pub args: serde_json::Map<String, Value> }
#[derive(…)] #[serde(deny_unknown_fields)]
pub struct Ambiguity { pub invariant: String, pub resolved_by: String }   // "arch:<id>" | "human" | "purged"
#[derive(…)] #[serde(deny_unknown_fields)]
pub struct Qualification { pub min_passes: u32, pub min_rate: f64 }
impl DriverPlan {
    pub fn validate(&self) -> Result<(), String>;  // family/app plain tokens; ≥1 action; action names 1..32 of a-z and `_`, unique; every step verb 1..40 of a-z and `_`; `$arg.` and `$secret.` references well-formed; min_rate in 0..=1; min_passes ≥ 1
    pub fn plan_id(&self) -> String;                // hash_canonical(self)
}

// driver.rs additions
pub struct DriverLearned { pub family: String, pub app: String, pub catalogue_sha256: String, pub ontology_sha256: String, pub corpus_schema_sha256: String, pub invariants_sha256: String, pub plan_sha256: String, pub driver_id: String, pub by: String }
pub struct DriverQualified { pub driver_id: String, pub passes: u32, pub failures: u32, pub qualification_sha256: String }
```

- [ ] **Step 1: Failing tests** — `the_example_plan_validates_and_hashes_stably` (plan_id starts with `sha256:` and is equal across two parses), `a_plan_without_actions_or_with_a_bad_verb_is_refused`, `a_secret_reference_must_name_an_id` (`$secret.` alone refused), `driver_learned_and_qualified_are_allowed_kinds` (ledger tests pattern).
- [ ] **Step 2: Run** → compile error. **Step 3: Implement** as the interface block; regenerate schemas; man table rows; docs sentences.
- [ ] **Step 4:** `cargo test --locked -p vk-contracts -p vk-cli` green (`every_schema_in_the_repository_is_in_the_table`, examples validate).
- [ ] **Step 5: Commit** `feat(contracts): DriverPlan, driver.learned and driver.qualified`

---

### Task 3: Inventory — the catalogue from a type library, and the recorded fixtures (Sonnet)

**Files:**
- Create: `drivers/vkforge/inventory.py`, `drivers/vkforge/tests/test_inventory.py`, `drivers/vkforge/tests/fixtures/catalogue-word.json`, `drivers/vkforge/tests/fixtures/catalogue-powerpoint.json` (recorded ONCE on the founder's node by this task's step 5, then committed; the CI tests read them)
- Modify: `drivers/vkforge/__main__.py` (stage registered by the module)

**Interfaces:**

```python
# inventory.py
APPS = {
    "word":       {"exe": "WINWORD.EXE", "progid": "Word.Application",       "typelib": "{00020905-0000-0000-C000-000000000046}"},
    "powerpoint": {"exe": "POWERPNT.EXE", "progid": "PowerPoint.Application", "typelib": "{91493440-5A91-11CF-8700-00AA0060263B}"},
}
def typelib_path(guid: str) -> str: ...           # HKCR\TypeLib\{guid}\<ver>\0\win32|win64 → path; Refusal if absent
def catalogue_from_typelib(app: str, path: str) -> dict: ...  # comtypes.typeinfo.LoadTypeLibEx → the schema of spec §4.1
def catalogue_from_fixture(app: str, fixture: pathlib.Path) -> dict: ...
def catalogue_sha256(cat: dict) -> str: ...        # sha256 of the canonical JSON (sorted keys, no spaces)
def write_catalogue(cat: dict, out: pathlib.Path) -> None  # sorted keys, indent 1, "\n"
# CLI: vkforge inventory --app word --out DIR [--fixture FILE]
```

Catalogue shape (spec §4.1): `{"app", "typelib": {"guid","version"}, "types": [{"name", "kind": "dispatch|coclass|enum", "members": [{"name","kind": "method|property_get|property_put","params": [{"name","type"}],"returns"}]}], "enums": {"WdBuiltinStyle": {"wdStyleHeading1": -2, …}}}`, types and members sorted by name.

- [ ] **Step 1: Failing tests**

```python
class InventoryTest(unittest.TestCase):
    def test_the_recorded_word_catalogue_has_the_members_the_ontology_needs(self):
        cat = inventory.catalogue_from_fixture("word", FIXTURES / "catalogue-word.json")
        names = {t["name"] for t in cat["types"]}
        for t in ("Document", "Documents", "Paragraph", "Range", "Font", "Section", "HeaderFooter", "Style"):
            self.assertIn(t, names)
        self.assertEqual(cat["enums"]["WdBuiltinStyle"]["wdStyleHeading1"], -2)
        self.assertEqual(cat["enums"]["WdSaveFormat"]["wdFormatPDF"], 17)
        doc = next(t for t in cat["types"] if t["name"] == "Document")
        self.assertIn("SaveAs2", {m["name"] for m in doc["members"]})

    def test_the_recorded_powerpoint_catalogue_has_placeholders_and_export(self):
        cat = inventory.catalogue_from_fixture("powerpoint", FIXTURES / "catalogue-powerpoint.json")
        self.assertIn("ppPlaceholderTitle", cat["enums"]["PpPlaceholderType"])
        self.assertIn("ppSaveAsPDF", cat["enums"]["PpSaveAsFileType"])

    def test_the_catalogue_hash_is_stable_across_key_order(self):
        cat = inventory.catalogue_from_fixture("word", FIXTURES / "catalogue-word.json")
        shuffled = json.loads(json.dumps(cat)[::-1][::-1])  # same content
        self.assertEqual(inventory.catalogue_sha256(cat), inventory.catalogue_sha256(shuffled))

    @unittest.skipUnless(sys.platform == "win32" and inventory.office_present("word"), "Word not installed")
    def test_the_live_type_library_matches_the_fixture_on_the_members_the_ontology_needs(self):
        live = inventory.catalogue_from_typelib("word", inventory.typelib_path(inventory.APPS["word"]["typelib"]))
        fixture = inventory.catalogue_from_fixture("word", FIXTURES / "catalogue-word.json")
        self.assertEqual(live["enums"]["WdBuiltinStyle"], fixture["enums"]["WdBuiltinStyle"])

    def test_an_unknown_app_is_refused(self):
        with self.assertRaises(Refusal):
            inventory.catalogue_from_fixture("excel", FIXTURES / "catalogue-word.json")
```

- [ ] **Step 2: Run** → ImportError.

- [ ] **Step 3: Implement** `catalogue_from_typelib` with `comtypes.typeinfo.LoadTypeLibEx(path)`; iterate `tlib.GetTypeInfoCount()`, for each `GetTypeInfo(i)` / `GetTypeAttr()`: `typekind` `TKIND_ENUM` → read `GetVarDesc(j)` constants; `TKIND_DISPATCH`/`TKIND_INTERFACE` → `GetFuncDesc(j)` for name (`GetDocumentation(memid)[0]`), `invkind` (`INVOKE_FUNC`→method, `INVOKE_PROPERTYGET`, `INVOKE_PROPERTYPUT`), params (`GetNames(memid, cMaxNames=cParams+1)`, types rendered through `comtypes.typeinfo` `TYPEDESC.vt` → a name table `VT_BSTR: "BSTR"`, `VT_VARIANT: "VARIANT"`, `VT_I4: "long"`, `VT_BOOL: "bool"`, `VT_DISPATCH: "IDispatch"`, else `f"vt{vt}"`); `TKIND_COCLASS` → listed with no members. `typelib_path`: `winreg.OpenKey(HKEY_CLASSES_ROOT, rf"TypeLib\{guid}")`, enumerate version subkeys, take the highest, read `0\win64` then `0\win32`. `office_present(app)` → `typelib_path` succeeds.

- [ ] **Step 4: Run** the fixture tests green (write the two fixture files first from step 5 on the node; until then the implementer commits a minimal hand-written fixture carrying the members and enums the tests name, clearly marked `"recorded": false`, and step 5 replaces it).

- [ ] **Step 5: Record the fixtures on the founder's node** (controller, once): `python -I -m vkforge inventory --app word --out drivers/vkforge/tests/fixtures/` and the same for `powerpoint`; rename to `catalogue-<app>.json`; commit. Size expected 1–3 MB each; if above 4 MB, keep only the types the ontology's patterns name plus every enum (`--only-types` flag added to the stage).

- [ ] **Step 6: Commit** `feat(vkforge): inventory of a type library into a catalogue; recorded Word and PowerPoint catalogues`

---
### Task 4: The ontology `office-document.json` v1 and the binding (Opus)

**Files:**
- Create: `drivers/vkforge/ontology/office-document.json`, `drivers/vkforge/ontology.py`, `drivers/vkforge/tests/test_ontology.py`

**Interfaces:**

```python
# ontology.py
def load(path: pathlib.Path | None = None) -> dict          # default: the package's office-document.json; validates the shape
def ontology_sha256(ont: dict) -> str
def bind(ont: dict, catalogue: dict) -> dict                 # → binding-<app>.json shape below
def write_binding(binding: dict, out: pathlib.Path) -> None
# CLI: vkforge bind --catalogue FILE --out DIR [--ontology FILE]
```

Ontology verb shape (spec §4.2) — every verb has `verb`, `args` (name → type among `Path`, `ParagraphSelector`, `BuiltinStyle`, `FontSpec`, `Text`, `Format`), `pre`, `post` (a postcondition id the miner can check: `paragraph_style`, `font_run`, `footer`, `header`, `pdf_exists`, `saved`), and `projection` per app, written as **patterns** over the catalogue:

```json
{ "verb": "apply_paragraph_style",
  "args": { "selector": "ParagraphSelector", "style": "BuiltinStyle" },
  "pre": "document_open", "post": "paragraph_style",
  "projection": {
    "word":       { "member": "Paragraph.Style", "constants": "WdBuiltinStyle", "status": "bound" },
    "powerpoint": { "member": "Shape.PlaceholderFormat.Type", "constants": "PpPlaceholderType", "status": "partial" } } }
```

Binding shape: `{"app", "ontology_sha256", "catalogue_sha256", "verbs": [{"verb", "status": "bound|partial|unbound", "member": "Type.Member[.Member]", "constants": {"name": {...}} | null, "why": "<for unbound: which pattern failed>"}]}`.

The v1 verb list (32 verbs), all with Word projections, PowerPoint projections where the model has a counterpart: `open`, `close`, `save`, `save_as`, `export` (pdf), `select_paragraph` (`first | body | all | index:n`), `apply_paragraph_style`, `set_font_run` (name, size), `set_font_bold`, `set_font_italic`, `set_paragraph_alignment`, `set_paragraph_spacing`, `set_line_spacing`, `set_header` (text), `set_footer` (text), `set_page_margins`, `set_page_orientation`, `set_document_property` (title/author/subject), `insert_page_number`, `apply_numbering`, `set_table_style`, `set_table_borders`, `verify_paragraph_style`, `verify_font_run`, `verify_footer`, `verify_header`, `verify_pdf`, `count_paragraphs`, `read_document_property`, `set_slide_layout` (powerpoint only; word `unbound` by design), `set_placeholder_text`, `apply_theme`.

- [ ] **Step 1: Failing tests**

```python
class OntologyTest(unittest.TestCase):
    def setUp(self):
        self.ont = ontology.load()
        self.word = inventory.catalogue_from_fixture("word", FIXTURES / "catalogue-word.json")
        self.ppt = inventory.catalogue_from_fixture("powerpoint", FIXTURES / "catalogue-powerpoint.json")

    def test_every_verb_of_the_house_style_task_binds_on_word(self):
        b = ontology.bind(self.ont, self.word)
        status = {v["verb"]: v["status"] for v in b["verbs"]}
        for verb in ("open", "select_paragraph", "apply_paragraph_style", "set_font_run", "set_footer", "save", "export",
                     "verify_paragraph_style", "verify_font_run", "verify_footer", "verify_pdf"):
            self.assertEqual(status[verb], "bound", verb)
        self.assertEqual(status["set_slide_layout"], "unbound")

    def test_the_same_ontology_binds_on_powerpoint_without_code(self):
        b = ontology.bind(self.ont, self.ppt)
        status = {v["verb"]: v["status"] for v in b["verbs"]}
        for verb in ("open", "set_font_run", "set_footer", "save", "export", "set_slide_layout", "set_placeholder_text"):
            self.assertIn(status[verb], ("bound", "partial"), verb)
        self.assertEqual(status["apply_paragraph_style"], "partial")
        self.assertEqual(status["apply_numbering"], "unbound")

    def test_a_pattern_that_matches_nothing_is_unbound_not_an_exception(self):
        ont = ontology.load()
        ont["verbs"].append({"verb": "levitate", "args": {}, "pre": "document_open", "post": "saved",
                             "projection": {"word": {"member": "Document.Levitate", "constants": None}}})
        b = ontology.bind(ont, self.word)
        v = next(x for x in b["verbs"] if x["verb"] == "levitate")
        self.assertEqual(v["status"], "unbound")
        self.assertIn("Document.Levitate", v["why"])

    def test_constants_are_resolved_to_numbers_never_to_localised_names(self):
        b = ontology.bind(self.ont, self.word)
        v = next(x for x in b["verbs"] if x["verb"] == "apply_paragraph_style")
        self.assertEqual(v["constants"]["WdBuiltinStyle"]["wdStyleHeading1"], -2)
        self.assertNotIn("Titre", json.dumps(b))

    def test_the_ontology_file_validates_its_own_shape(self):
        bad = ontology.load()
        del bad["verbs"][0]["post"]
        with self.assertRaises(Refusal):
            ontology.validate(bad)
```

- [ ] **Step 2: Run** → ImportError. **Step 3: Implement** `bind`: for each verb and the catalogue's app, split `member` on `.`, walk the catalogue: the first segment is a type name (exact), each next segment a member name (exact) whose `returns` type (for a chain) names the next type (strip `*`/`IDispatch` wrappers; when the return type is `VARIANT`/`IDispatch`, accept the next segment if any type in the catalogue has that member — `partial`); `constants` must be an enum in the catalogue, else `unbound` with `why`. A verb with no projection for the app is `unbound` with `why: "no projection for <app>"`. `validate(ont)` checks every verb's fields, arg types in the allowed set, `post` in the postcondition set.

- [ ] **Step 4: Run green; commit** `feat(vkforge): the office-document ontology v1 and the binding by patterns over a catalogue`

---

### Task 5: The miner — OOXML invariants, purge, no content (Sonnet)

**Files:**
- Create: `drivers/vkforge/mine.py`, `drivers/vkforge/ooxml.py` (shared reader/writer helpers: open as zip, parse a part with `lxml`, namespaces), `drivers/vkforge/tests/ooxml_fixtures.py` (builds minimal valid `.docx`/`.pptx` with `zipfile` — no Office), `drivers/vkforge/tests/test_mine.py`

**Interfaces:**

```python
# ooxml.py
NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main", "a": "http://schemas.openxmlformats.org/drawingml/2006/main", "p": "http://schemas.openxmlformats.org/presentationml/2006/main"}
def open_package(path) -> zipfile.ZipFile      # Refusal when not a zip / no [Content_Types].xml
def kind_of(zf) -> str                         # "docx" | "pptx" | Refusal("not OOXML", …)
def part(zf, name) -> lxml.etree._Element | None
def is_encrypted(path) -> bool                 # OLE compound file signature D0 CF 11 E0 → encrypted OOXML

# mine.py
PARTS_DOCX = ("word/styles.xml", "word/settings.xml", "word/numbering.xml", "docProps/app.xml")  # + header*/footer* + pPr/rPr of document.xml
PARTS_PPTX = ("ppt/presentation.xml", "docProps/app.xml")                                      # + slideMasters/*, slideLayouts/*, pPr/rPr of slides
def mine_file(path) -> dict                    # one file's observations: {"kind", "obs": [{"kind": "font_run", "value": {...}}, ...]}
def mine_corpus(dir, threshold=0.8) -> tuple[dict, dict]   # (invariants.json dict, vault dict {redacted_hash: clear})
def write_invariants(inv, out: Path) -> None   # out/invariants.json (sorted keys) and out/.vault.json (0o600 where supported)
# CLI: vkforge mine --corpus DIR --out DIR [--threshold 0.8]
```

Observation kinds and values: `paragraph_style` (`{"builtin": "Heading1"}` mapped from `w:pStyle` through `styles.xml` `w:style/@w:styleId` → built-in alias when `w:name` is one of the English built-in names, else the styleId hashed), `font_run` (`{"name": …, "size": half-points/2}` from the body's majority `w:rFonts/@w:ascii` + `w:sz`), `footer` / `header` (text joined, **hashed**), `page_size`, `margins`, `orientation`, `numbering_present`, `export_format` (from `docProps/app.xml` `Application`? no — from the presence of a sibling `.pdf` with the same stem: `{"format": "pdf"}`), `title_style` (the first paragraph's style). Invariant support = files carrying the observation / files mined; an observation whose value differs across files is grouped by value, each value a candidate.

- [ ] **Step 1: Failing tests** (fixtures built by `ooxml_fixtures.docx(title_style="Heading1", body_font=("Garamond", 11), footer="Cabinet Exemple – Confidentiel", personal="Jean Dupont 06 12 34 56 78")` and `pptx(...)`)

```python
class MineTest(unittest.TestCase):
    def test_ten_contracts_yield_the_three_house_invariants_at_full_support(self):
        d = tempfile.mkdtemp()
        for i in range(10):
            ooxml_fixtures.docx(Path(d) / f"c{i}.docx", title_style="Heading1", body_font=("Garamond", 11), footer="Cabinet Exemple – Confidentiel")
        inv, vault = mine.mine_corpus(Path(d))
        by_id = {i["id"]: i for i in inv["invariants"]}
        self.assertEqual(by_id["title_style"]["value"], {"builtin": "Heading1"})
        self.assertEqual(by_id["body_font"]["value"], {"name": "Garamond", "size": 11})
        self.assertEqual(by_id["title_style"]["support"], 1.0)
        self.assertTrue(by_id["footer_text"]["value"].startswith("<redacted:sha256:"))
        self.assertEqual(vault[by_id["footer_text"]["value"]], "Cabinet Exemple – Confidentiel")
        self.assertEqual(inv["corpus"]["files"], 10)

    def test_support_below_threshold_is_purged(self):
        d = tempfile.mkdtemp()
        for i in range(10):
            ooxml_fixtures.docx(Path(d) / f"c{i}.docx", body_font=("Garamond", 11) if i < 7 else ("Calibri", 12))
        inv, _ = mine.mine_corpus(Path(d), threshold=0.8)
        self.assertNotIn("body_font", {i["id"] for i in inv["invariants"]})
        self.assertIn({"kind": "font_run", "candidates": 2}, inv["ambiguities"])

    def test_no_byte_of_the_text_reaches_the_invariants(self):
        d = tempfile.mkdtemp()
        secrets = ["Jean Dupont", "06 12 34 56 78", "jean.dupont@example.org", "1 rue des Lilas"]
        for i in range(6):
            ooxml_fixtures.docx(Path(d) / f"c{i}.docx", footer="Pied " + secrets[0], personal=" ".join(secrets))
        out = Path(tempfile.mkdtemp())
        inv, vault = mine.mine_corpus(Path(d))
        mine.write_invariants((inv, vault), out)
        blob = (out / "invariants.json").read_bytes()
        for s in secrets:
            self.assertNotIn(s.encode("utf-8"), blob)

    def test_a_mixed_folder_skips_what_is_not_ooxml_and_never_opens_office(self):
        d = Path(tempfile.mkdtemp())
        for i in range(5):
            ooxml_fixtures.docx(d / f"c{i}.docx")
        (d / "old.doc").write_bytes(b"\xd0\xcf\x11\xe0" + b"\0" * 64)       # binary Word
        (d / "locked.docx").write_bytes(b"\xd0\xcf\x11\xe0" + b"\0" * 64)    # encrypted OOXML is an OLE file
        (d / "~$c1.docx").write_bytes(b"\0" * 10)
        (d / "notes.txt").write_text("x")
        inv, _ = mine.mine_corpus(d)
        self.assertEqual(inv["corpus"]["files"], 5)
        self.assertEqual(inv["corpus"]["skipped"], 4)

    def test_fewer_than_five_files_is_refused(self):
        d = Path(tempfile.mkdtemp())
        for i in range(4):
            ooxml_fixtures.docx(d / f"c{i}.docx")
        with self.assertRaises(Refusal) as cm:
            mine.mine_corpus(d)
        self.assertIn("more documents of one kind", str(cm.exception))

    def test_pptx_masters_give_the_title_placeholder_font(self):
        d = Path(tempfile.mkdtemp())
        for i in range(6):
            ooxml_fixtures.pptx(d / f"p{i}.pptx", title_font=("Garamond", 32), footer="Confidentiel")
        inv, _ = mine.mine_corpus(d)
        by_id = {i["id"]: i for i in inv["invariants"]}
        self.assertEqual(by_id["title_font"]["value"], {"name": "Garamond", "size": 32})
        self.assertEqual(inv["corpus"]["kind"], "pptx")

    def test_a_corpus_of_two_kinds_is_refused(self): ...  # docx + pptx mixed → Refusal "one kind"
```

- [ ] **Step 2: Run** → ImportError. **Step 3: Implement** the fixtures builder first (a minimal `[Content_Types].xml`, `_rels/.rels`, `word/document.xml` with `w:p` paragraphs carrying `w:pPr/w:pStyle` and `w:rPr/w:rFonts`+`w:sz`, `word/styles.xml` with `Heading1` (`w:name w:val="heading 1"`), `word/footer1.xml` referenced from `word/_rels/document.xml.rels` and `w:sectPr/w:footerReference`; `pptx` with `ppt/presentation.xml`, one master with a title placeholder `p:ph type="title"` carrying `a:rPr sz="3200"` + `a:latin typeface`, one slide), then `mine_file`, then the aggregation with `support`, the threshold purge, the ambiguity list (`{"kind", "candidates"}`), `content_sha256` (sha256 over the sorted list of per-file sha256) and `schema_sha256` (sha256 of the invariants JSON). The text of a header/footer is reduced to `<redacted:sha256:…>` of its UTF-8 and stored in the vault.

- [ ] **Step 4: Run green; commit** `feat(vkforge): the OOXML miner — invariants with support, purge below the threshold, no content in the artefact`

---

### Task 6: The planner — invariants × binding → `plan.json`, ambiguities (Opus)

**Files:**
- Create: `drivers/vkforge/plan.py`, `drivers/vkforge/tests/test_plan.py`
- Modify: `drivers/vkforge/ontology/office-document.json` (if a rule needs a new `post` id)

**Interfaces:**

```python
# plan.py
RULES = [  # invariant kind → the steps that produce it and the postcondition that checks it
    ("title_style", lambda v: [step("select_paragraph", selector="first"), step("apply_paragraph_style", selector="first", style=v["builtin"])], "paragraph_style"),
    ("body_font",   lambda v: [step("select_paragraph", selector="body"), step("set_font_run", selector="body", font=v)], "font_run"),
    ("footer_text", lambda v: [step("set_footer", text=v)], "footer"),          # v is the redacted token → "$secret.footer_text"
    ("header_text", lambda v: [step("set_header", text=v)], "header"),
    ("export_format", lambda v: [step("export", format=v["format"], path="$arg.pdf")], "pdf_exists"),
    ("title_font",  lambda v: [step("set_font_run", selector="title", font=v)], "font_run"),   # pptx
]
def build(invariants: dict, binding: dict, ontology: dict, resolve=None) -> dict   # the DriverPlan dict; `resolve(ambiguity: dict) -> dict | None` is the optional arch hook
def plan_sha256(plan: dict) -> str
def write_plan(plan: dict, out: Path) -> None
# CLI: vkforge plan --invariants FILE --binding FILE --out DIR [--resolver FILE]   (resolver: a JSON file of pre-resolved answers, the kernel's seam; see Task 9)
```

`build`: one action `appliquer_style_maison` (word) / `appliquer_style_presentation` (pptx) with steps `open($arg.document)` + the rule steps for every invariant whose rule's verbs are all `bound` (or `partial`, kept with a `note`) in the binding, then `save`, then `export` when present; `pre: ["document is <kind>", "not open elsewhere"]`, `post` = the invariant ids + `pdf exists` when exported; `qualification: {min_passes: 10, min_rate: 1.0}`. An invariant whose rule needs an `unbound` verb → excluded, listed in `plan["excluded"]` (`{"invariant", "why": "verb X unbound"}`). Each miner ambiguity → `resolve(amb)` if given: an answer `{"invariant": id, "value": …}` is applied as if it were an invariant at threshold, recorded `resolved_by: "arch:<id>"`; `None`/no resolver → `resolved_by: "purged"`.

- [ ] **Step 1: Failing tests** — `the_house_style_plan_has_the_five_steps_in_order` (open, select first, apply style, select body, set font, set footer, save, export; `post` holds the three ids + `pdf exists`), `a_secret_is_referenced_never_inlined` (`$secret.footer_text` in the step, the redacted token nowhere, the clear text nowhere), `an_unbound_verb_excludes_its_invariant_and_says_so` (binding with `set_footer` flipped to `unbound` → no set_footer step, `excluded` names it), `a_tie_is_an_ambiguity_purged_without_a_resolver` (ambiguity `font_run` → plan still built, `ambiguities == [{"invariant": "body_font", "resolved_by": "purged"}]`), `a_resolver_answer_becomes_a_step_traced_to_the_arch`, `the_plan_validates_against_the_contract_schema` (use `jsonschema`? not a dependency — instead run `cargo`? no: the test shells out to `vk man driver_plan`? No daemon in CI. Keep it Python: a small `plan.validate_shape()` mirror of `DriverPlan::validate`, and Task 9's Rust smoke test validates the real file).

- [ ] **Step 2–4:** implement, green, commit `feat(vkforge): the planner — invariants and bound verbs become a typed plan; ambiguities resolved or purged`

---

### Task 7: The COM runtime, the fake application, the generator (Opus)

**Files:**
- Create: `drivers/vkforge/runtime/__init__.py`, `drivers/vkforge/runtime/com.py`, `drivers/vkforge/runtime/fake.py`, `drivers/vkforge/generate.py`, `drivers/vkforge/templates/driver.py.tmpl`, `drivers/vkforge/tests/test_generate.py`, `drivers/vkforge/tests/test_runtime.py`

**Interfaces:**

```python
# runtime/com.py — the only module that touches COM; copied into the generated driver's directory as `vkcom.py`
class Application:
    def __init__(self, progid: str, binding: dict, fake=None): ...   # comtypes.client.GetActiveObject(progid) then CreateObject; `fake` replaces COM in tests
    def open(self, path: str): ...                                    # Documents.Open(path, AddToRecentFiles=False, Visible=False) / Presentations.Open(path, WithWindow=False); ReadOnly → DriverRefusal("precondition", "document opened read-only (locked elsewhere)")
    def call(self, verb: str, doc, **args): ...                       # dispatches by binding["verbs"][verb]["member"]: a small table of verb → Python function over the COM object graph (apply_paragraph_style, set_font_run, set_footer, set_header, save, export, verify_*, select_paragraph, set_slide_layout, set_placeholder_text, …)
    def close(self, doc, save=False): ...
    def quit(self): ...                                               # only if this driver started the instance

# runtime/fake.py — FakeApplication(binding): the same verbs implemented on OOXML with ooxml.py's writer twin (`ooxml.write_part`): apply_paragraph_style writes w:pStyle, set_font_run rewrites w:rFonts/w:sz on the body runs, set_footer writes footer1.xml, export writes a 1 200-byte "%PDF-1.4 fake" file; verify_* re-read through mine.mine_file.
```

Generated driver layout (`<out>/driver.py`, `<out>/vkcom.py`, `<out>/binding.json`, `<out>/manifest.json`, `<out>/secrets.json` with private ACL on the node — written by the generator from the vault, never in the package of a later `vk driver pack`… it is a driver file and therefore hashed; the spec §5.5 accepts clear values in the driver on the node):

```python
# driver.py (template, rendered with the plan)
"""<name> — driver appris par vkforge le <date absent: no timestamp>; ontology <h1>, catalogue <h2>, invariants <h3>, plan <h4>."""
import json, os, sys, hashlib
from pathlib import Path
DRIVER_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DRIVER_DIR.parent))           # drivers/ — for vkdriver
sys.path.insert(0, str(DRIVER_DIR))
from vkdriver.protocol import DriverRefusal, serve     # noqa: E402
from vkcom import Application                          # noqa: E402

NAME = "{{name}}"; VERSION = "{{version}}"
BINDING = json.loads((DRIVER_DIR / "binding.json").read_text(encoding="utf-8"))
SECRETS = json.loads((DRIVER_DIR / "secrets.json").read_text(encoding="utf-8")) if (DRIVER_DIR / "secrets.json").exists() else {}
PROGID = "{{progid}}"
_app = None

def app():
    global _app
    if _app is None:
        _app = Application(PROGID, BINDING, fake=os.environ.get("VKFORGE_FAKE"))
    return _app

def _sha(path): return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()

{{#each actions}}
def {{name}}(args: dict, evidence_dir: str):
    doc_path = args["document"]
    if not Path(doc_path).is_file() or Path(doc_path).suffix.lower() != ".{{kind}}":
        raise DriverRefusal("precondition", "not a .{{kind}} file")
    before = _sha(doc_path)
    doc = app().open(doc_path)
    try:
        {{#each steps}}app().call("{{verb}}", doc, **{{args_literal}}){{/each}}
    finally:
        app().close(doc, save=False)
    post = { {{#each post}}"{{this}}": app().verify("{{this}}", doc_path, {{expected_literal}}),{{/each}} }
    ev = {"before_sha256": before, "after_sha256": _sha(doc_path), "post": post}
    Path(evidence_dir, "post.json").write_text(json.dumps(ev, sort_keys=True), encoding="utf-8")
    if not all(post.values()):
        raise DriverRefusal("precondition", "postconditions not all held: " + ",".join(k for k, v in post.items() if not v), evidence=[{"name": "post.json", "mime": "application/json", "path": str(Path(evidence_dir, "post.json"))}])
    return {"post": post, "text": "{{name}} ok"}, [{"name": "post.json", "mime": "application/json", "path": str(Path(evidence_dir, "post.json"))}]
{{/each}}

ACTIONS = { {{#each actions}}"{{name}}": {{name}},{{/each}} }

if __name__ == "__main__":
    serve(NAME, VERSION, ACTIONS)
```

(`{{…}}` is the plan's own tiny renderer in `generate.py` — `str.replace` over named markers and loops unrolled in Python, no template engine; `args_literal` renders `$arg.x` as `args["x"]` and `$secret.id` as `SECRETS["id"]`.)

`manifest.json`: `name` = `<app>-<profile>` given on the CLI, `kind: com`, `program: {runtime: "python-com", command: [".venv/Scripts/python.exe", "-I", "driver.py"], files_sha256: hash_dir(out), browser_channel: null, headless: true}`, `hosts: []`, `applications: [exe]`, `actions` from the plan (args schema `{"type":"object","properties":{"document":{"type":"string"},"pdf":{"type":"string"}},"required":["document"],"additionalProperties":false}`, `evidence: ["post.json"]`, `reversible: false`, `to_register: false`), `min_interval_ms: 0`, `max_action_ms: 120000`, `clearance: {max_scope: "business", third_party_allowed: false}`, `governed: false`. The generator computes `files_sha256` last, with `vkdriver.hash_dir.hash_dir(out)` (which excludes `manifest.json`).

- [ ] **Step 1: Failing tests**

```python
class GenerateTest(unittest.TestCase):
    def setUp(self):
        self.plan = json.loads((FIXTURES / "plan-word-maison.json").read_text())   # produced by Task 6's test and committed
        self.binding = json.loads((FIXTURES / "binding-word.json").read_text())
        self.vault = {"<redacted:sha256:…>": "Cabinet Exemple – Confidentiel"}

    def test_generation_is_byte_reproducible_in_two_directories(self):
        a, b = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
        generate.generate(self.plan, self.binding, self.vault, a, name="word-maison")
        generate.generate(self.plan, self.binding, self.vault, b, name="word-maison")
        for f in ("driver.py", "vkcom.py", "binding.json", "manifest.json", "secrets.json"):
            self.assertEqual((a / f).read_bytes(), (b / f).read_bytes(), f)
        m = json.loads((a / "manifest.json").read_text())
        self.assertEqual(m["program"]["files_sha256"], hash_dir(str(a)))

    def test_no_localised_name_and_no_corpus_text_in_the_driver(self):
        out = Path(tempfile.mkdtemp())
        generate.generate(self.plan, self.binding, self.vault, out, name="word-maison")
        code = (out / "driver.py").read_bytes()
        self.assertNotIn(b"Titre", code)
        self.assertNotIn("Cabinet Exemple".encode(), code)       # it is in secrets.json, not in the code
        self.assertIn(b'SECRETS["footer_text"]', code)

    def test_the_generated_driver_speaks_protocol_1_against_the_fake(self):
        out = Path(tempfile.mkdtemp())
        generate.generate(self.plan, self.binding, self.vault, out, name="word-maison")
        doc = Path(tempfile.mkdtemp()) / "c.docx"
        ooxml_fixtures.docx(doc, title_style="Normal", body_font=("Calibri", 12))
        env = {**os.environ, "VKFORGE_FAKE": "1", "VK_DRIVER_WORK_DIR": tempfile.mkdtemp(), "VK_DRIVER_PROFILE_DIR": tempfile.mkdtemp()}
        p = subprocess.Popen([sys.executable, "-I", str(out / "driver.py")], cwd=out, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8")
        hello = json.loads(p.stdout.readline())
        self.assertEqual(hello["hello"]["actions"], ["appliquer_style_maison"])
        evd = tempfile.mkdtemp()
        p.stdin.write(json.dumps({"id": "1", "action": "appliquer_style_maison", "args": {"document": str(doc), "pdf": str(doc.with_suffix(".pdf"))}, "deadline_ms": 60000, "evidence_dir": evd}) + "\n"); p.stdin.flush()
        ans = json.loads(p.stdout.readline())
        self.assertTrue(ans["ok"], ans)
        self.assertTrue(all(ans["result"]["post"].values()), ans["result"]["post"])
        p.stdin.close(); p.wait(timeout=10)
        obs = mine.mine_file(doc)
        self.assertIn({"kind": "paragraph_style", "value": {"builtin": "Heading1"}, "where": "first"}, obs["obs"])

    def test_a_read_only_document_is_a_precondition_refusal(self):
        # the fake raises the same DriverRefusal the COM adapter raises on doc.ReadOnly
        ...
```

`test_runtime.py`: `the_fake_and_the_adapter_share_one_verb_table` (every bound verb of the fixture binding has an implementation in both `Application` and `FakeApplication`; a verb missing in either fails the test), and, `skipUnless(office_present("word"))`, `the_adapter_applies_the_house_style_on_the_real_word` (runs on the node only).

- [ ] **Step 2–4:** implement (`generate.py` renders the template, writes `vkcom.py` as a copy of `runtime/com.py` with `runtime/fake.py` appended under `if fake:` import, writes `binding.json`, `secrets.json` from the vault entries the plan references, then `manifest.json`), green, commit `feat(vkforge): COM runtime with its fake twin, and the generator — a protocol-1 driver from a plan`

---

### Task 8: Qualification — replay on copies, postconditions in code (Opus)

**Files:**
- Create: `drivers/vkforge/qualify.py`, `drivers/vkforge/tests/test_qualify.py`

**Interfaces:**

```python
def qualify(driver_dir: Path, corpus: Path, invariants: dict, work: Path, fake: bool, deadline_s: float = 120.0) -> dict
# refuses when sha256 of the corpus != invariants["corpus"]["content_sha256"]; copies each file to work/<i>/<name>; runs the driver once per copy (one process for the whole run, protocol 1 over stdio, the same lines the kernel sends); re-mines each output with mine.mine_file and checks every id of plan["post"] holds; records per pass {file_sha256, duration_ms, post: {...}, ok, reason}
# → {"driver_id", "plan_sha256", "passes", "failures", "rate", "runs": [...]} ; written as qualification.json
# CLI: vkforge qualify --driver DIR --corpus DIR --invariants FILE --work DIR [--fake]
```

- [ ] **Step 1: Failing tests** — `ten_copies_pass_against_the_fake` (10/10, `rate == 1.0`, every `runs[i].post` all true, durations present), `a_locked_copy_is_a_failed_pass_not_a_hang` (make one copy read-only / `~$` lock marker the fake honours → that pass `ok False, reason "precondition"`, run completes under the deadline, 9/10), `a_changed_corpus_is_refused_before_any_copy` (append a file → `Refusal` "corpus changed", `work` empty), `the_record_is_reproducible_but_for_durations`.
- [ ] **Step 2–4:** implement, green, commit `feat(vkforge): qualification — replay on copies, postconditions re-mined, a record the mount checks`

---
### Task 9: Kernel, IPC and CLI — `vk driver learn`, `vk driver qualify`, the ambiguity call, `vk driver plan show` (Opus)

**Files:**
- Create: `crates/vk-ipc/src/forge.rs`, `crates/vk-ipc/tests/forge.rs`, `crates/vk-kernel/src/learn.rs`, `crates/vk-kernel/tests/learn.rs`
- Modify: `crates/vk-ipc/src/server.rs` (`takes_presence` 706-734: add `driver.learn`; `dispatch`: `driver.learn`, `driver.qualify`, `driver.plan_show`), `crates/vk-cli/src/main.rs` (`DriverCmd` 510-520: `Learn { family, app, corpus, out, threshold, arch, deliberate }`, `Qualify { driver, corpus }`, `Plan { what: PlanCmd::Show { dir } }`), `crates/vk-cli/src/render.rs`, `crates/vkd/src/lib.rs` (`--vkforge-root DIR`, default `<exe dir>/../../drivers` resolved at start, printed in `boot.info`; the daemon refuses to start learning if `vkforge/__main__.py` is absent)
- Test: `crates/vk-cli/tests/smoke.rs`

**Interfaces:**

```rust
// vk-ipc/src/forge.rs — L-P2
pub struct ForgeConfig { pub root: PathBuf /* drivers/ */, pub python: PathBuf /* the interpreter: drivers/vkforge/.venv/Scripts/python.exe or VK_FORGE_PYTHON */ }
pub struct StageRun { pub stage: &'static str, pub args: Vec<String>, pub stdout_tail: String, pub exit: i32, pub duration_ms: u64 }
/// Spawns `python -I -m vkforge <stage> <args>` with `current_dir = root`, under
/// `vk_harness::confine::for_this_platform(1 << 30, None)`, stdout/stderr to
/// `<work>/forge-<stage>.log`, killed at `deadline`. A non-zero exit is a
/// refusal whose text is the stage's last stderr line (vkforge speaks the
/// kernel's voice) — never more.
pub fn run_stage(cfg: &ForgeConfig, stage: &'static str, args: &[String], work: &Path, deadline: Duration) -> Result<StageRun, RpcError>;

// vk-ipc `driver.learn` (presence): params {family, app, corpus, out, threshold?, arch?}
//   1. refuse unless family == "office-document" and app ∈ {word, powerpoint}; refuse a non-empty `out`
//   2. run_stage inventory --app A --out OUT          (on CI: --fixture <root>/vkforge/tests/fixtures/catalogue-A.json when VK_FORGE_FIXTURES=1)
//   3. run_stage bind --catalogue OUT/catalogue.json --out OUT
//   4. run_stage mine --corpus CORPUS --out OUT --threshold T
//   5. if `arch`: for each ambiguity in OUT/invariants.json → kernel.learn_ambiguity(ctx, arch, amb) → OUT/resolver.json ; run_stage plan … --resolver OUT/resolver.json ; else run_stage plan …
//   6. run_stage generate --plan OUT/plan.json --binding OUT/binding-A.json --vault OUT/.vault.json --out OUT/driver --name <app>-<profile>   (profile = `out`'s dir name)
//   7. delete OUT/.vault.json ; read OUT/driver/manifest.json ; kernel.driver_learned(ctx, …) → the row
//   → {driver_id, plan_sha256, out, unbound: [...], ambiguities: [...], excluded: [...]}

// vk-ipc `driver.qualify` (no presence): params {driver: DIR, corpus: DIR}
//   run_stage qualify --driver DIR --corpus CORPUS --invariants DIR/../invariants.json --work <state_dir>/drivers/<name>/work/qualify [--fake when VK_FORGE_FIXTURES=1]
//   → kernel.driver_qualified(driver_id, passes, failures, sha256 of qualification.json) ; returns the record

// vk-kernel/src/learn.rs
pub fn driver_learned(&mut self, ctx: &Ctx, learned: DriverLearned) -> Result<(), KernelError>;   // human act (I1 otherwise), logs driver.learned
pub fn driver_qualified(&mut self, q: DriverQualified) -> Result<(), KernelError>;                 // logs driver.qualified; stores <state_dir>/drivers/<name>/qualification.json path in a `learned` table keyed by driver_id
/// The one model call of the pipeline: a register holding only the ambiguity
/// JSON (label scope `business`, no content), `infer(ctx, arch_id, Capability::Draft, reg)`,
/// the answer parsed as `{"invariant": "...", "value": ...}`; anything else → None (purged).
pub fn learn_ambiguity(&mut self, ctx: &Ctx, arch_id: &str, ambiguity: &Value) -> Result<Option<Value>, KernelError>;
/// Mount gate: a `kind: com` manifest whose `driver_id` has no `driver.qualified` row meeting the plan's thresholds is refused
/// "driver <id> is not qualified; remedy: vk driver qualify" — development mounts of hand-written com drivers pass with `--unqualified` (a human flag, recorded on `driver.mounted`? No: recorded in the daemon log only; the manifest's `governed` pattern applies — plan ruling L-P4).
```

- [ ] **Step 1: Failing tests**

`crates/vk-ipc/tests/forge.rs` (guarded by `python_available()`, with `VK_FORGE_FIXTURES=1` so the inventory reads the recorded catalogue and `qualify` uses the fake):

```rust
#[test]
fn learn_runs_the_five_stages_and_writes_driver_learned() {
    let node = Node::start();                                  // existing helper, extended with `forge: ForgeConfig { root: repo/drivers, python }`
    let corpus = node.dir.join("corpus");
    make_docx_corpus(&corpus, 10);                             // helper that shells to `python -I drivers/vkforge/tests/ooxml_fixtures.py <dir> 10`
    let out = node.dir.join("learn-out");
    let r = node.call_human("driver.learn", json!({"family": "office-document", "app": "word", "corpus": corpus, "out": out})).unwrap();
    assert!(out.join("driver/driver.py").exists());
    assert!(!out.join(".vault.json").exists(), "the vault is deleted after generation");
    let plan: vk_contracts::plan::DriverPlan = serde_json::from_slice(&std::fs::read(out.join("plan.json")).unwrap()).unwrap();
    plan.validate().unwrap();
    assert_eq!(node.last_event().kind, "driver.learned");
    assert_eq!(r["ambiguities"], json!([]));
}

#[test]
fn learn_is_a_human_act_and_refuses_an_unknown_family_or_a_used_out() { … }

#[test]
fn qualify_writes_driver_qualified_and_the_mount_gate_opens() {
    // after learn: driver.qualify → passes 10, failures 0, last event driver.qualified;
    // driver.mount {manifest: out/driver/manifest.json, root} succeeds (governed may be false), the row's kind is "com";
    // a second learned driver with its qualification file edited to passes 3 → mount refused "is not qualified"
}

#[test]
fn a_stage_that_fails_is_one_refusal_line_in_the_kernel_voice() {
    // corpus with 2 files → driver.learn error message == "mine: corpus has 2 files; remedy: more documents of one kind"; nothing under out but inventory and binding; no driver.learned row
}

#[test]
fn an_ambiguity_with_a_mock_arch_is_resolved_and_traced() {
    // corpus 7 Garamond + 3 Calibri (threshold 0.8) → with `arch: "mock"` (vk mount mock mock in Node): plan.ambiguities == [{"invariant": "body_font", "resolved_by": "arch:<mock id>"}] and the mock's answer (the mock returns the register text; the test's mock answer is shaped by a `VK_MOCK_ANSWER` seam already used by harness tests, or the ambiguity is purged when the mock echoes) — assert the trace, not the value
}
```

`crates/vk-kernel/tests/learn.rs`: `learn_ambiguity_calls_infer_once_on_a_content_free_register` (the register's text is the ambiguity JSON only; an `infer` row is written; a non-JSON answer → `None`), `driver_learned_refuses_a_machine_principal`, `driver_qualified_then_the_com_gate_reads_the_record`.

`smoke.rs`: `vk_driver_learn_then_qualify_then_mount_from_the_cli` (end to end with fixtures), `vk_driver_plan_show_prints_the_actions_and_the_ambiguities` (one block per action: name, steps `verb(args)`, post; then `ambiguities` and `excluded`).

- [ ] **Step 2: Run** → unknown methods. **Step 3: Implement** as specified. The `vkforge` interpreter on the node: `drivers/vkforge/setup.ps1` (copy of the MaskAI one with `requirements.txt` of vkforge, venv under `%LOCALAPPDATA%\vk\venvs\vkforge`, R27 guard kept); on CI, `VK_FORGE_PYTHON` = the runner's `python`.
- [ ] **Step 4:** `cargo test --locked -p vk-kernel -p vk-ipc -p vk-cli` green. **Step 5: Commit** `feat(kernel,ipc,cli): vk driver learn and qualify — vkforge under the Job governor, the one model call, the qualification gate at mount`

---

### Task 10: The fallback `--deliberate`, the CI job `driver-learn`, docs and TCB (Sonnet)

**Files:**
- Modify: `crates/vk-ipc/src/forge.rs` (`deliberate(cfg, kernel, ctx, verb, app, out)`), `crates/vk-cli/src/main.rs` (`--deliberate VERB` on `Learn`), `.github/workflows/ci.yml` (job `driver-learn`), `scripts/check-workflows.py` (`EXPECTED_JOBS` adds `driver-learn`), `docs/technical/technical-readme.md` (section "Learned drivers"), `contracts/tcb.md` §Drivers (spec §11 + L-P1..L-P4), `README.md` (status table), `docs/technical/runbooks/recette-maskai.md` (no change) and a new `docs/technical/runbooks/learn-office-driver.md` (the founder's procedure: `setup.ps1`, `vk driver learn`, `vk driver plan show`, `vk driver qualify`, `vk mount driver`)

**Interfaces:**
- `driver.learn` with `deliberate: VERB` (presence): creates a task with one `Draft` step on the given arch whose register holds the verb's ontology entry, the catalogue slice of the types its pattern names (members only, ≤ 20 kB), and the instruction "propose a projection rule as JSON {member, constants}"; the harness is not used (no application driving in this chantier, L-P3); the answer is written to `<out>/proposals/<verb>.json` with `proposed_by: arch:<id>` and returned; nothing is written to the ontology or to `driver.py`.

- [ ] **Step 1: Failing tests** — `vk-ipc/tests/forge.rs::deliberate_writes_a_proposal_and_touches_nothing_else` (mock arch; `proposals/set_slide_layout.json` exists, `ontology/office-document.json` unchanged by hash, no `driver.py`), `scripts/check-workflows.py` expectation updated first so it fails until the job exists.

- [ ] **Step 2: The job**

```yaml
  driver-learn:
    # The learning pipeline end to end on Windows, without Office: recorded
    # catalogues, the fake application, a generated corpus; then the kernel path
    # (learn, qualify, mount) through the CLI smoke test.
    runs-on: windows-latest
    timeout-minutes: 60
    env:
      CARGO_INCREMENTAL: "0"
      VK_FORGE_FIXTURES: "1"
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
      - name: Pin OPENSSL_SRC_PERL (same pwsh step as the test job)
        …
      - uses: actions/setup-python@v5
        with: { python-version: "3.13" }
      - run: pip install -r drivers/vkforge/requirements.txt
      - name: vkforge suite (fixtures and the fake application)
        run: python -I -m unittest discover -s drivers/vkforge/tests -t .
      - name: Build
        run: cargo build --locked -p vk-cli -p vkd -p vk-mcp
      - name: learn → qualify → mount through the daemon
        run: cargo test --locked -p vk-ipc --test forge -- --nocapture
      - name: The CLI end to end
        run: cargo test --locked -p vk-cli --test smoke vk_driver_learn_then_qualify_then_mount_from_the_cli -- --nocapture
```

- [ ] **Step 3: Docs** — technical readme: the six stages, the artefacts, the seams (`VK_FORGE_FIXTURES`, `VK_FORGE_PYTHON`, `VKFORGE_FAKE`), the mount gate; TCB: spec §11's five sentences, plus L-P1 (the fake is a test double of the adapter, never mounted on a node), L-P2 (a governed one-shot process; the Job does not block the network, the package opens no socket, pinned by a test), L-P3 (the deliberate fallback proposes, never traces nor writes code), L-P4 (the qualification gate at mount for `kind: com`); runbook `learn-office-driver.md` in French, embedded register, every command with its expected line.
- [ ] **Step 4:** `python -I scripts/check-workflows.py`, `python -I scripts/docs/check_docs.py`, push, `gh run watch`. **Step 5: Commit** `feat,ci,docs: the deliberate fallback as a proposal; the driver-learn job; learned drivers in the readme, the TCB and a runbook`

---

### Task 11: The debrief stage — vkforge debrief (Opus)

Founder decision 2026-10-17: the post-launch retro-learning loop becomes a stage of vkforge. Requirements: `docs/technical/debriefs/2026-10-17-maskai-driver.md` §4 (inputs, signals, proposal format, what stays human, the gain on the 17/10 run) and the defects of its §2/§3 the stage must detect. After a governed run the stage reads what the node holds in clear and writes what to change; it changes nothing. Firmware analogy: the post-mortem of a crash dump — the core file and the console are read, a patch is proposed with the addresses that justify it, and a human reflashes.

**Files:**
- Create: `drivers/vkforge/debrief.py`, `drivers/vkforge/tests/test_debrief.py`, `drivers/vkforge/tests/debrief_fixtures.py`, `drivers/vkforge/tests/fixtures/debrief-2026-10-17/journal-essai1.jsonl`, `drivers/vkforge/tests/fixtures/debrief-2026-10-17/driver.log`, `drivers/vkforge/tests/fixtures/debrief-2026-10-17/vkd-console.log`
- Modify: `drivers/vkforge/__main__.py` (`_register` imports `debrief`), `crates/vk-ipc/src/forge.rs` (`debrief`, `DEBRIEF_DEADLINE`), `crates/vk-ipc/src/server.rs` (`dispatch`: `driver.debrief`, **not** added to `takes_presence`), `crates/vk-cli/src/main.rs` (`DriverCmd::Debrief { run, out, until }` and its dispatch arm), `crates/vk-cli/src/render.rs` (`debriefed`), `docs/technical/runbooks/recette-maskai.md` (new §16, French), `docs/technical/technical-readme.md` (the "Learned drivers" section of Task 10: one paragraph), `contracts/tcb.md` §Drivers (one sentence)
- Test: `crates/vk-ipc/tests/forge.rs` (two tests), `crates/vk-cli/tests/smoke.rs` (one test)

**Interfaces:**
- Consumes: `vkforge.errors.Refusal` and the stage registry `vkforge.__main__.STAGES` (Task 0); `ForgeConfig`, `run_stage` and the `driver.learn` dispatch site with its state directory (Task 9); `vk_contracts::hash_bytes`; `crate::server::{bad, internal}`.
- Reads, in clear on the node (never a ledger payload: those are hashed) — the run directory, fixed names, only `journal.jsonl` and `tasks/` required; a missing optional input turns its signals to `null` or empty:

| File | Where it comes from | What is read |
|---|---|---|
| `journal.jsonl` | the driver's work directory (`$S\drivers\<name>\work\`) | each line: case, verdict, timing, evidence text, `pipeline_ms`, `inputs_sha256`, `ui_profile_sha256`, `config` |
| `journal-*.jsonl` | journals set aside during the run (a first try) | the same, for the suspects and the new-line counts, not for the per-case table |
| `driver.log` | the same directory | `%(asctime)s %(levelname)s %(name)s %(message)s` lines, the node's local time (the journal's UTC offset names it) |
| `vkd-console.log` | the daemon's console (runbook §16) | the notes `harness log task=<id> harness=<text>`, UTC, ANSI colours stripped, a BOM tolerated |
| `tasks/<id>.json` | `vk task show <id> --json`, one per task | status, `created_ms`, steps (kind, harness `instruction` starting `Niveau Nk`, status, `started_ms`, `ended_ms`), `usage` (`step_index`, `cost_list_usd`) |
| `dmesg.json` | `vk dmesg -n N --json` | `kind`, `wall_ms`: the `driver.acted` rows inside a step's window are its acts |
| `cases.json` | the driver's | `level`, `kind`, `budget_s` per case |
| `manifest.json` | the driver's | `kind` (`com` = a learned driver) |

- Produces: `vkforge debrief --run DIR --out DIR [--until TASK]` (a stage like the others); `debrief.debrief(run: Path, until: str | None = None) -> (signals: dict, proposals: list, markdown: str)` and `debrief.write(signals, proposals, markdown, out: Path)` (refuses a non-empty `out`); the files `debrief.json` (signals, sorted keys, no absolute path, no timestamp of its own), `proposals.json`, `debrief.md` (sections 1–3 of a debrief in the register of the 17/10 one; §4 and §5 stay human); IPC `driver.debrief {run, out, until?}` → `{out, proposals: <n>, list: [{kind, target, confidence, proposal}], debrief_sha256, proposals_sha256}`; CLI `vk driver debrief --run DIR --out DIR [--until TASK]`.
- Proposal shape — the list form of the deliberate-run proposal (Task 10, spec §5.3: one object, `proposed_by`), here with `proposed_by: "debrief"` and the evidence lines:

```json
{"kind": "selector", "target": "assistant_message",
 "evidence": [{"at": "journal-essai1.jsonl:3", "task": "task-node-1-2", "text": "<the line, at most 240 characters>"}],
 "evidence_total": 2, "proposal": "<what to change, in French, the debrief's register>",
 "confidence": "high", "tasks": ["task-node-1-2"], "proposed_by": "debrief",
 "human": "<who applies it and what it costs: new driver_id, new inputs_sha256, a recipe commit>"}
```
`kind` ∈ {`selector`, `modal`, `budget`, `oracle`, `recipe`}; `confidence` ∈ {`high`, `medium`, `low`} (`low` is reserved: none of the six rules below emits it); `tasks` in run order.
- **Not a human act.** `driver.debrief` reads evidence and writes three files: a machine act, no presence proof, run as a governed one-shot vkforge process exactly like `learn`'s stages (L-P2: the Job governor, a deadline of 120 s, from the `drivers/` root). `driver.learn` stays vkforge's only human act.
- **Ledger — FOUNDER DECISION, left open:** an optional kernel-written row `driver.debriefed {run_task_ids, debrief_sha256, proposals_sha256}` (like `driver.qualified`) would tie a later fix to the evidence that motivated it. **Default, implemented here: no new kind; the files only.** The IPC answer already carries both hashes, so the row, if decided, is one payload in `vk-contracts`, one `ALLOWED_KINDS` entry and one kernel call, with no change to the stage.
- **What stays human** (debrief §4): `SELECTORS` and `KNOWN_MODALS` (a new `driver_id`, a signed remount), budgets and oracles (a new `inputs_sha256`, declared in the report's delta), `supersedes` corrections, qualifying a modal (benign or security), the recette, every signature. The stage never writes into `driver.py`, `cases.json`, a recipe, the run directory or the ledger. For a learned driver (`manifest.json` with `kind: com` in the run directory) the selector and modal proposals name `vk driver learn --deliberate VERB` instead (spec §5.3): a learned driver's code is never edited by hand.

Signals of `debrief.json` and the defect each carries (debrief §2 D1–D13, §3, §4 S14–S17):

| Key | Defect | Rule |
|---|---|---|
| `cases` | — | per exec line of `journal.jsonl`, corrections applied: verdict, `timing_s`, `budget_s` (from the evidence `budget N s`, else `cases.json`), `aborted`, `over_budget`, `reasons` (`timeout` for an abandon line, plus the protocol word of every harness note `refus… (<reason>)` naming the case), task, `at` |
| `tasks`, `levels` | §1, S17 | per task, and per harness pass (a step whose instruction starts `Niveau Nk`): status, duration, acts, cost, journal lines of that level written in that task |
| `refusals` | §3 | `timeout` = the abandon lines; `dialog`, `precondition`, `host`, `internal` = the harness notes `refus… (<reason>)` |
| `selector_suspects` | D1 | a `réponse vide` line whose `pipeline_ms.call_llm` > 0, in every journal |
| `empty_without_llm` | D1 | a `réponse vide` line without `call_llm`: the product, not the selector |
| `modal_refusals` | D2 | the notes `modale inconnue : <text>`, grouped by text; `after_abort` counts those whose task's previous other note was an abandon |
| `known_modals_dismissed` | D2 | `driver.log` `known modal <name> dismissed`, counted |
| `sent_unobserved` | D4 | answered lines (`réponse : «`) saying `texte envoyé non observé` |
| `header_in_answer` | D5 | answered lines whose answer carries the model banner (`maskai <provider>/<model>`) or the guard panel |
| `cut_suspects` | D6 | answered lines carrying the live marker `· en direct` or the v2 note `affichage encore en mouvement` [I] |
| `attachment_unseen` | D7 | `attach_question` cases whose answer says the document is not there [I] |
| `concurrent_on_failing` | D9 | a `concurrent` case whose evidence names cases that aborted alone |
| `replays` | D10, S17 | passes `done` that wrote no line: count, acts, cost |
| `levels_without_artefact` | D11 | passes `failed: harness produced no artefact`, with the refusals noted in their window (`null` before the console starts) |
| `hashes` | D12 | the distinct `inputs_sha256` and `ui_profile_sha256`, their changes in run order, the lines without `ui_profile_sha256` |
| `constant_nodes` | D13 | pipeline nodes with one value on five lines or more |
| `maintenance` | recette 2 | `driver.log` `service in maintenance`; notes or journal lines `service en maintenance` |
| `composer` | S15 | the v2 lines' `config` (model, web), counted |
| `pipeline`, `latency_classes` | §3, S16 | per node, and per (case kind, budget): n, median, p90, max, over budget, aborts |
| `relaunches`, `acts_total` | §1, §4 | a task not `done` followed by another; all `driver.acted` rows |

Proposal rules (`propose`): D1 → `selector assistant_message` (high from two cases); D2 → `modal <text>` (high from two refusals); D8 → `budget <level>` when at least two and at least half of a level's journaled cases ended by abandon (high only if every one of those lines says the question left and the stream opened, medium otherwise); D5 → `oracle answer_scope` when the header is in at least half of three answered lines or more (medium: the stage does not see the oracles); a maintenance page → `recipe maintenance` (high); D11 → `recipe level_without_artefact` (high from two). A suspect that needs a screen or an observation to be confirmed (D4, D6, D7, D9, D10, D12, D13, S15) stays a signal in `debrief.md` §2, without a proposal: a proposal requires evidence that names the change. S14 (the judge's and the report's claims against the journal, D3) is out of this stage: the judge's text is in the register, not in clear on the node.

**Fixture outcome (the 17/10 run, pinned by the tests).** Six proposals: `selector assistant_message` (high; `journal-essai1.jsonl:3` and `:5`), `modal Votre avis` (high; `vkd-console.log:5` and `:6`), `budget N4` (medium; `journal.jsonl:14` to `:18`), `oracle answer_scope` (medium; 22 of 27 answered lines), `recipe level_without_artefact` (high; t7 and t16 N5), `recipe maintenance` (high; `driver.log:9`). Debriefed with `--until` at the end of t2, t7 and t16, the stage already holds the answer to the first three of the four manual loops of §4 — the selector (15:25 → 15:30), the relaunch without a fix (15:40 → 15:42), the modal (15:49 → 15:59); the fourth, the judge's table (16:25 → t39's full replay), needs S14. **Three manual loops saved, one left.**

- [ ] **Step 1: The fixtures.** Three excerpts under `drivers/vkforge/tests/fixtures/debrief-2026-10-17/`, written from the debrief's verified facts (the provenance of each is in the builder's docstring; the maintenance line is the v2 driver's, seen at recette 2's first try, not a 17/10 event), and the builder `tests/debrief_fixtures.py`, which copies the REAL journal `docs/functional/use-cases/recettage-chat-ia/runs/2026-10-17/journal.jsonl` and writes the task tables and the ledger rows of the five tasks.

`journal-essai1.jsonl` (essai 1, set aside on 17/10; N1-1 and N1-3 « réponse vide » with `call_llm`, D1):

```text
{"ts": "2026-10-17T15:23+02:00", "step": "exec", "case": "N0-1", "verdict": "OK", "timing_s": 0.1, "evidence": "texte envoyé non observé : fuite de PII non vérifiable ; réponse : « chargement de https://app.maskai.fr/new : ttfb 61 ms, DOMContentLoaded 99 ms, load 108 ms, FCP 240 ms, 38988 octets transférés (48 ressources) »", "nfr": ["performance"], "inputs_sha256": "sha256:02462040afbb7f397a09ad95b95b91a8035a919402dcd805c006f2fdc7d1c7eb"}
{"ts": "2026-10-17T15:23+02:00", "step": "exec", "case": "N0-2", "verdict": "OK", "timing_s": 0.1, "evidence": "texte envoyé non observé : fuite de PII non vérifiable ; réponse : « chargement de https://app.maskai.fr/new : ttfb 55 ms, DOMContentLoaded 93 ms, load 102 ms, FCP 231 ms, 222930 octets transférés (48 ressources) »", "nfr": ["performance"], "inputs_sha256": "sha256:02462040afbb7f397a09ad95b95b91a8035a919402dcd805c006f2fdc7d1c7eb"}
{"ts": "2026-10-17T15:24+02:00", "step": "exec", "case": "N1-1", "verdict": "KO", "timing_s": 5.2, "evidence": "réponse vide : échec silencieux", "pipeline_ms": {"detect_pii": 1904, "aiact_guard": 301, "anonymize": 0, "safety": 0, "retrieve_context": 92, "web_search": 288, "call_llm": 2444, "deanonymize": 1}, "nfr": ["disponibilite", "performance"], "inputs_sha256": "sha256:02462040afbb7f397a09ad95b95b91a8035a919402dcd805c006f2fdc7d1c7eb"}
{"ts": "2026-10-17T15:24+02:00", "step": "exec", "case": "N1-2", "verdict": "OK", "timing_s": 0.0, "evidence": "texte envoyé non observé : fuite de PII non vérifiable ; réponse : « aucun envoi »", "nfr": ["disponibilite"], "inputs_sha256": "sha256:02462040afbb7f397a09ad95b95b91a8035a919402dcd805c006f2fdc7d1c7eb"}
{"ts": "2026-10-17T15:25+02:00", "step": "exec", "case": "N1-3", "verdict": "KO", "timing_s": 11.8, "evidence": "réponse vide : échec silencieux", "pipeline_ms": {"detect_pii": 1210, "aiact_guard": 4467, "anonymize": 0, "safety": 0, "retrieve_context": 96, "web_search": 3561, "call_llm": 2391, "deanonymize": 1}, "nfr": ["disponibilite"], "inputs_sha256": "sha256:02462040afbb7f397a09ad95b95b91a8035a919402dcd805c006f2fdc7d1c7eb"}
```

`driver.log`:

```text
2026-10-17 15:22:31,118 INFO maskai-recette browser up: channel=chrome headless=False hosts=['app.maskai.fr']
2026-10-17 15:31:02,540 INFO maskai-recette browser up: channel=chrome headless=False hosts=['app.maskai.fr']
2026-10-17 15:42:24,906 INFO maskai-recette browser up: channel=chrome headless=False hosts=['app.maskai.fr']
2026-10-17 15:59:23,377 INFO maskai-recette browser up: channel=chrome headless=False hosts=['app.maskai.fr']
2026-10-17 16:06:15,602 INFO maskai-recette known modal avis dismissed ([role="dialog"][data-slot="dialog-content"] button[data-slot="dialog-close"])
2026-10-17 16:09:19,233 INFO maskai-recette known modal avis dismissed ([role="dialog"][data-slot="dialog-content"] button[data-slot="dialog-close"])
2026-10-17 16:10:19,871 INFO maskai-recette known modal avis dismissed ([role="dialog"][data-slot="dialog-content"] button[data-slot="dialog-close"])
2026-10-17 16:26:24,050 INFO maskai-recette browser up: channel=chrome headless=False hosts=['app.maskai.fr']
2026-10-17 16:29:14,207 WARNING maskai-recette service in maintenance: MAINTENANCE EN COURS ON REVIENT TRÈS VITE ! Mask AI FR fait une petite pause
```

`vkd-console.log` (kept from 15:42 local, as on 17/10; UTC):

```text
2026-10-17T13:42:00.004113Z  INFO vk_ipc::server: listening (a daemon line that is not a harness note: ignored)
2026-10-17T13:42:31.207114Z  INFO vk_ipc::server: harness log task=task-node-1-16 harness=N0 : rejeu, N0-1 et N0-2 déjà journalisés
2026-10-17T13:45:02.551930Z  INFO vk_ipc::server: harness log task=task-node-1-16 harness=N4 : V2-V1 joint, question posée
2026-10-17T13:49:04.880512Z  INFO vk_ipc::server: harness log task=task-node-1-16 harness=V2-V1 KO : abandon à 180 s (3 × budget 60 s)
2026-10-17T13:49:14.302877Z  INFO vk_ipc::server: harness log task=task-node-1-16 harness=N5 : nouvelle_conversation refusée (dialog) : modale inconnue : Votre avis — Aidez-nous à améliorer Mask AI
2026-10-17T13:49:22.640019Z  INFO vk_ipc::server: harness log task=task-node-1-16 harness=N5 : capture_ecran refusée (dialog) : modale inconnue : Votre avis — Aidez-nous à améliorer Mask AI
2026-10-17T13:49:31.118250Z  INFO vk_ipc::server: harness log task=task-node-1-16 harness=N5-1 : verdict_oracle refusé (precondition) : aucune observation pour N5-1
2026-10-17T13:49:38.774391Z  INFO vk_ipc::server: harness log task=task-node-1-16 harness=N5-2 : verdict_oracle refusé (precondition) : aucune observation pour N5-2
2026-10-17T13:49:45.020611Z  INFO vk_ipc::server: harness log task=task-node-1-16 harness=N5 : niveau terminé sans preuve, la modale bloque toute action
2026-10-17T14:06:09.413377Z  INFO vk_ipc::server: harness log task=task-node-1-25 harness=V2-V3 KO : abandon à 180 s (3 × budget 60 s)
2026-10-17T14:09:12.902114Z  INFO vk_ipc::server: harness log task=task-node-1-25 harness=V2-V4 KO : abandon à 180 s (3 × budget 60 s)
2026-10-17T14:10:13.551006Z  INFO vk_ipc::server: harness log task=task-node-1-25 harness=V2-V5 KO : abandon à 60 s (3 × budget 20 s)
2026-10-17T14:22:40.118734Z  INFO vk_ipc::server: harness log task=task-node-1-25 harness=N9-2 KO : réponse vide, aucune étape call_llm dans le flux
2026-10-17T14:31:52.330918Z  INFO vk_ipc::server: harness log task=task-node-1-39 harness=N0–N9 : 34 verdicts rejoués, aucune ligne ajoutée
```

`tests/debrief_fixtures.py`:

```python
"""Run directories for the debrief tests: the 17/10 run, and a clean one.

`run_2026_10_17(dir)` assembles the governed recette of 2026-10-17 (five tasks, node-1):
- `journal.jsonl` is the REAL published journal, copied from
  docs/functional/use-cases/recettage-chat-ia/runs/2026-10-17/journal.jsonl;
- `journal-essai1.jsonl`, `driver.log`, `vkd-console.log` are the excerpts of
  tests/fixtures/debrief-2026-10-17/, written from the debrief's verified facts
  (docs/technical/debriefs/2026-10-17-maskai-driver.md): essai 1's N1-1/N1-3 « réponse vide »
  with call_llm (D1), the unknown « Votre avis » modal refused twice in t16 and the two
  preconditions after it (D2, §3), the console kept from 15:42 only. The maintenance line of
  driver.log is NOT a 17/10 event: it is the v2 driver's line, seen at recette 2's first try,
  placed here so the fixture carries every defect the stage must detect;
- `tasks/*.json` and `dmesg.json` are built from TASKS below: per step the window, the
  `driver.acted` count and the cost; totals are the debrief's §1 [V] (291 acts, 4.245 USD,
  N4 = 809 s / 33 acts / 0.320 USD over t7+t16+t25, t39's replay 329 s / 55 acts / 0.674 USD);
  the split of N4 and of the N0-N3 replays between tasks is this fixture's;
- `cases.json` keeps level, kind and budget_s of the 34 cases of drivers/maskai-recette/cases.json.

`clean_run(dir)` is a run of the v2 driver with nothing to report.
Usable from Rust tests: `python -I drivers/vkforge/tests/debrief_fixtures.py DIR 17-10|clean`.
"""
import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
JOURNAL_17_10 = REPO / "docs/functional/use-cases/recettage-chat-ia/runs/2026-10-17/journal.jsonl"
EXCERPTS = HERE / "fixtures" / "debrief-2026-10-17"
TZ = timezone(timedelta(hours=2))
DAY = "2026-10-17"
RECIPE = ["plan", "N0", "N1", "N2", "N3", "N4", "N5", "N6", "N7", "N8", "N9", "judge", "approve", "draft", "approve", "release"]
NO_ARTEFACT = {"failed": "harness produced no artefact"}

# task -> (created, status, steps that ran: (step, start, end, status, driver.acted rows, cost USD)); the
# recipe's other steps are pending. Steps run in recipe order; a step absent from the list did not run.
TASKS = {
    "task-node-1-2": ("15:22:00", "stopped", [
        ("plan", "15:22:00", "15:22:25", "done", 0, 0.065), ("N0", "15:22:25", "15:23:40", "done", 7, 0.064),
        ("N1", "15:23:40", "15:26:00", "done", 14, 0.130)]),
    "task-node-1-7": ("15:30:00", "failed", [
        ("plan", "15:30:40", "15:31:00", "done", 0, 0.039), ("N0", "15:31:00", "15:32:08", "done", 7, 0.067),
        ("N1", "15:32:08", "15:33:21", "done", 14, 0.120), ("N2", "15:33:21", "15:35:50", "done", 16, 0.151),
        ("N3", "15:35:50", "15:38:24", "done", 18, 0.162), ("N4", "15:38:24", "15:39:40", "done", 7, 0.080),
        ("N5", "15:39:40", "15:40:20", NO_ARTEFACT, 4, 0.049)]),
    "task-node-1-16": ("15:42:00", "failed", [
        ("plan", "15:42:00", "15:42:20", "done", 0, 0.071), ("N0", "15:42:20", "15:43:00", "done", 5, 0.060),
        ("N1", "15:43:00", "15:43:40", "done", 5, 0.062), ("N2", "15:43:40", "15:44:20", "done", 5, 0.066),
        ("N3", "15:44:20", "15:45:00", "done", 5, 0.066), ("N4", "15:45:00", "15:49:10", "done", 9, 0.090),
        ("N5", "15:49:10", "15:49:50", NO_ARTEFACT, 4, 0.049)]),
    "task-node-1-25": ("15:59:00", "stopped", [
        ("plan", "15:59:00", "15:59:20", "done", 0, 0.060), ("N0", "15:59:20", "16:00:05", "done", 6, 0.064),
        ("N1", "16:00:05", "16:00:49", "done", 6, 0.066), ("N2", "16:00:49", "16:01:33", "done", 6, 0.070),
        ("N3", "16:01:33", "16:02:17", "done", 6, 0.070), ("N4", "16:02:17", "16:10:20", "done", 17, 0.150),
        ("N5", "16:10:50", "16:11:55", "done", 10, 0.101), ("N6", "16:11:55", "16:15:12", "done", 27, 0.281),
        ("N7", "16:15:12", "16:15:46", "done", 7, 0.075), ("N8", "16:15:46", "16:20:57", "done", 17, 0.163),
        ("N9", "16:20:57", "16:23:12", "done", 14, 0.146), ("judge", "16:23:12", "16:25:00", "done", 0, 0.349),
        ("approve", "16:25:00", None, "waiting_human", 0, None)]),
    "task-node-1-39": ("16:26:00", "done", [
        ("plan", "16:26:00", "16:26:20", "done", 0, 0.055), ("N0", "16:26:20", "16:27:34", "done", 5, 0.056),
        ("N1", "16:27:34", "16:28:02", "done", 5, 0.061), ("N2", "16:28:02", "16:28:31", "done", 6, 0.071),
        ("N3", "16:28:31", "16:28:53", "done", 6, 0.074), ("N4", "16:28:53", "16:29:21", "done", 7, 0.082),
        ("N5", "16:29:21", "16:29:44", "done", 4, 0.051), ("N6", "16:29:44", "16:30:24", "done", 7, 0.082),
        ("N7", "16:30:24", "16:30:47", "done", 4, 0.051), ("N8", "16:30:47", "16:31:20", "done", 6, 0.079),
        ("N9", "16:31:20", "16:31:49", "done", 5, 0.067), ("judge", "16:31:49", "16:33:30", "done", 0, 0.310),
        ("approve", "16:33:30", "18:12:30", "done", 0, None), ("draft", "18:12:30", "18:14:00", "done", 0, 0.220),
        ("approve", "18:14:00", "18:14:40", "done", 0, None), ("release", "18:14:40", "18:15:00", "done", 0, None)]),
}
# case -> level, kind, budget_s (drivers/maskai-recette/cases.json, the 34 cases of the 17/10 recette)
CASES = """N0-1 N0 load 3|N0-2 N0 load 3|N1-1 N1 message 10|N1-2 N1 message 10|N1-3 N1 message 10
|N2-1a N2 message 10|N2-1b N2 message 10|N2-2a N2 message 10|N2-2b N2 message 10|N3-1 N3 message 10
|N3-2 N3 message 10|N3-3 N3 message 10|N3-4 N3 message 10|V2-L5 N4 attach_question 20|V2-V1 N4 attach_question 60
|V2-V3 N4 attach_question 60|V2-V4 N4 attach_question 60|V2-V5 N4 attach_question 20|N5-1 N5 message 10
|N5-2 N5 message 10|V2-L1 N6 attach_question 20|V2-L2 N6 attach_question 20|V2-L3 N6 attach_question 20
|V2-L4 N6 attach_question 20|V2-V2 N6 attach_question 60|N7-1 N7 list 10|N7-2 N7 list 10|N8-1 N8 message 10
|N8-2 N8 message 10|N8-3 N8 message 10|N8-4 N8 concurrent 60|N9-1 N9 message 20|N9-2 N9 message 20|N9-3 N9 message 10"""


def ms(hms: str, day: str = DAY) -> int:
    return int(datetime.fromisoformat(f"{day}T{hms}").replace(tzinfo=TZ).timestamp() * 1000)


def step_kind(name: str) -> dict:
    if name in ("plan", "judge", "draft"):
        return {"kind": name, "arch_id": "claude-code/claude-sonnet-5"}
    if name == "approve":
        return {"kind": "approve"}
    if name == "release":
        return {"kind": "release", "to_dir": "recettage-chat-ia/runs/2026-10"}
    return {"kind": "harness", "name": "claude-code", "drivers": ["maskai-1"], "max_actions": 60, "timeout_s": 1800,
            "instruction": f"Niveau {name}. Tu es le harnais de la recette MaskAI (procédure Aubier)."}


def write_tasks(run: Path, tasks: dict, day: str = DAY) -> list:
    """tasks/<id>.json in the shape of `vk task show --json`; returns the ledger rows of the run."""
    (run / "tasks").mkdir(parents=True, exist_ok=True)
    rows = []
    for tid, (created, status, ran) in tasks.items():
        steps, usage, cursor = [], [], 0
        for i, name in enumerate(RECIPE):
            hit = ran[cursor] if cursor < len(ran) and ran[cursor][0] == name else None
            if hit is None:
                steps.append({"kind": step_kind(name), "status": "pending", "started_ms": None, "ended_ms": None, "tokens": 0})
                continue
            cursor += 1
            _, start, end, st, acts, cost = hit
            lo, hi = ms(start, day), ms(end, day) if end else None
            steps.append({"kind": step_kind(name), "status": st, "started_ms": lo, "ended_ms": hi, "tokens": 0})
            if cost is not None:
                usage.append({"arch_id": "claude-code/claude-sonnet-5", "task_id": tid, "step_index": i, "tokens_in": 0,
                              "tokens_in_measured": 0, "tokens_out": 0, "cost_list_usd": cost,
                              "duration_ms": (hi or lo) - lo, "ts_ms": hi or lo})
            rows += [{"kind": "driver.acted", "wall_ms": lo + (k + 1) * ((hi or lo) - lo) // (acts + 1)} for k in range(acts)]
            rows.append({"kind": "task.step", "wall_ms": hi or lo})
        rows.append({"kind": "task.created", "wall_ms": ms(created, day)})
        dump = {"id": tid, "register": f"reg-{tid}", "goal": "recette MaskAI", "artefact_type": "note", "steps": steps,
                "status": status, "created_ms": ms(created, day), "decisions": [], "usage": usage, "artefacts": []}
        (run / "tasks" / f"{tid}.json").write_text(json.dumps(dump, ensure_ascii=False, indent=1), encoding="utf-8")
    return rows


def write_dmesg(run: Path, rows: list) -> None:
    """`vk dmesg --json` keeps seq, kind, wall_ms and hashes; the debrief reads kind and wall_ms."""
    rows = sorted(rows, key=lambda r: (r["wall_ms"], r["kind"]))
    (run / "dmesg.json").write_text(json.dumps([{"seq": i + 1, **r, "payload_hash": "sha256:" + "0" * 64}
                                                for i, r in enumerate(rows)]), encoding="utf-8")


def run_2026_10_17(run: Path) -> Path:
    run.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(JOURNAL_17_10, run / "journal.jsonl")
    for name in ("journal-essai1.jsonl", "driver.log", "vkd-console.log"):
        shutil.copyfile(EXCERPTS / name, run / name)
    cases = {c: {"level": lv, "kind": k, "budget_s": int(b)}
             for c, lv, k, b in (x.split() for x in CASES.replace("\n", "").split("|"))}
    (run / "cases.json").write_text(json.dumps({"cases": cases}, indent=1), encoding="utf-8")
    write_dmesg(run, write_tasks(run, TASKS))
    return run


def clean_run(run: Path) -> Path:
    """One task, every step done, v2 lines: the body only, the sent text read, no abandon, no refusal."""
    run.mkdir(parents=True, exist_ok=True)
    ui, inputs = "sha256:" + "a" * 64, "sha256:" + "b" * 64
    lines = [("N0-1", "11:01", 0.1, "réponse : « chargement de https://app.maskai.fr/new : load 98 ms »", None),
             ("N1-1", "11:02", 4.8, "réponse : « La capitale du Portugal est Lisbonne. »", 1840),
             ("N1-2", "11:02", 0.0, "réponse : « aucun envoi »", None),
             ("N1-3", "11:03", 7.9, "réponse : « Relancez par écrit, rappelez l'échéance, proposez un échéancier. »", 2610)]
    with (run / "journal.jsonl").open("w", encoding="utf-8") as f:
        for case, hm, t, ev, llm in lines:
            e = {"ts": f"2026-10-20T{hm}+02:00", "step": "exec", "case": case, "verdict": "OK", "timing_s": t,
                 "evidence": ev, "nfr": ["performance"], "inputs_sha256": inputs, "ui_profile_sha256": ui}
            if llm:
                e["pipeline_ms"] = {"detect_pii": 900 + llm // 10, "anonymize": llm % 7, "call_llm": llm}
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    (run / "driver.log").write_text("2026-10-20 11:00:12,004 INFO maskai-recette browser up: channel=chrome "
                                    "headless=False hosts=['app.maskai.fr']\n", encoding="utf-8")
    (run / "vkd-console.log").write_text(
        "2026-10-20T09:02:40.000001Z  INFO vk_ipc::server: harness log task=task-node-1-50 harness=N1-1 OK\n",
        encoding="utf-8")
    rows = write_tasks(run, {"task-node-1-50": ("11:00:00", "done", [
        ("plan", "11:00:00", "11:00:10", "done", 0, 0.040), ("N0", "11:00:10", "11:01:30", "done", 3, 0.050),
        ("N1", "11:01:30", "11:03:40", "done", 9, 0.090)])}, day="2026-10-20")
    write_dmesg(run, rows)
    return run


if __name__ == "__main__":
    target, which = Path(sys.argv[1]), sys.argv[2]
    (run_2026_10_17 if which == "17-10" else clean_run)(target)
```

- [ ] **Step 2: Failing tests** (`tests/test_debrief.py`; the stage is called in-process, through `vkforge.__main__.main` for the CLI entry, so the suite does not depend on how Task 9 spawns the interpreter)

```python
import contextlib
import hashlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

DRIVERS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(DRIVERS))

from vkforge import debrief  # noqa: E402
from vkforge.errors import Refusal  # noqa: E402
from vkforge.tests import debrief_fixtures  # noqa: E402

RUN_17_10 = None


def setUpModule():
    global RUN_17_10
    RUN_17_10 = debrief_fixtures.run_2026_10_17(Path(tempfile.mkdtemp()) / "run")


def tree_sha(d: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(x for x in d.rglob("*") if x.is_file()):
        h.update(p.relative_to(d).as_posix().encode() + b"\0" + p.read_bytes())
    return h.hexdigest()


class Debrief17_10Test(unittest.TestCase):
    """The 17/10 run, debriefed from its artefacts (the real journal, the excerpts, the task tables)."""

    def setUp(self):
        self.s, self.p, self.md = debrief.debrief(RUN_17_10)

    def test_the_signals_are_the_debrief_s_verified_facts(self):
        s = self.s
        self.assertEqual(len(s["cases"]), 34)
        self.assertEqual(s["acts_total"], 291)
        self.assertAlmostEqual(sum(t["cost_usd"] for t in s["tasks"]), 4.245, places=3)
        self.assertEqual(s["refusals"], {"timeout": 5, "dialog": 2, "precondition": 2, "host": 0, "internal": 0})
        live = [p for p in s["levels"]["N4"] if p["new_lines"]]
        self.assertEqual([p["task"] for p in live], ["task-node-1-7", "task-node-1-16", "task-node-1-25"])
        self.assertEqual(sum(p["duration_s"] for p in live), 809)
        self.assertEqual(sum(p["acts"] for p in live), 33)
        self.assertAlmostEqual(sum(p["cost_usd"] for p in live), 0.320, places=3)
        v2v1 = next(c for c in s["cases"] if c["case"] == "V2-V1")
        self.assertEqual((v2v1["verdict"], v2v1["timing_s"], v2v1["budget_s"], v2v1["reasons"]), ("KO", 180.1, 60.0, ["timeout"]))
        self.assertEqual(next(c for c in s["cases"] if c["case"] == "N5-1")["reasons"], ["precondition"])
        self.assertEqual([x["case"] for x in s["selector_suspects"]], ["N1-1", "N1-3"])
        self.assertEqual([x["case"] for x in s["empty_without_llm"]], ["N9-2"])  # no call_llm: the product, not the selector
        self.assertEqual([(m["text"], m["count"], m["after_abort"]) for m in s["modal_refusals"]], [("Votre avis", 2, 2)])
        self.assertEqual(s["known_modals_dismissed"], {"avis": 3})
        self.assertEqual([(m["at"], m["task"]) for m in s["maintenance"]], [("driver.log:9", "task-node-1-39")])
        self.assertEqual(len(s["cut_suspects"]), 10)
        self.assertEqual([x["case"] for x in s["attachment_unseen"]], ["V2-L1", "V2-L2", "V2-L3", "V2-L4", "V2-V2"])
        self.assertEqual((s["header_in_answer"]["lines"], s["header_in_answer"]["of"]), (22, 27))
        self.assertEqual(s["sent_unobserved"], {"lines": 27, "of": 27})
        self.assertEqual(s["replays"], {"passes": 18, "acts": 99, "cost_usd": 1.198})
        self.assertEqual([(x["task"], x["level"], x["acts"], x["refusals_noted"]) for x in s["levels_without_artefact"]],
                         [("task-node-1-7", "N5", 4, None), ("task-node-1-16", "N5", 4, 4)])  # no console before 15:42
        self.assertEqual(len(s["hashes"]["inputs_sha256"]), 1)
        self.assertEqual(s["hashes"]["changes"], [])
        self.assertEqual(s["hashes"]["lines_without_ui_profile"], 39)
        self.assertEqual(s["constant_nodes"], {"anonymize": 0, "safety": 0})
        self.assertEqual(s["concurrent_on_failing"], [{"case": "N8-4", "at": "journal.jsonl:31", "pairs": ["V2-V1", "V2-V3"]}])
        self.assertEqual(s["composer"], [])  # the v1 lines of 17/10 carry no `config`
        self.assertEqual(len(s["relaunches"]), 4)

    def test_six_proposals_each_carrying_its_evidence_lines(self):
        got = [(p["kind"], p["target"], p["confidence"]) for p in self.p]
        self.assertEqual(got, [("selector", "assistant_message", "high"), ("modal", "Votre avis", "high"),
                               ("budget", "N4", "medium"), ("oracle", "answer_scope", "medium"),
                               ("recipe", "level_without_artefact", "high"), ("recipe", "maintenance", "high")])
        by = {(p["kind"], p["target"]): p for p in self.p}
        self.assertEqual([e["at"] for e in by["selector", "assistant_message"]["evidence"]],
                         ["journal-essai1.jsonl:3", "journal-essai1.jsonl:5"])
        self.assertIn("réponse vide : échec silencieux", by["selector", "assistant_message"]["evidence"][0]["text"])
        self.assertEqual([e["at"] for e in by["modal", "Votre avis"]["evidence"]], ["vkd-console.log:5", "vkd-console.log:6"])
        self.assertIn("modale inconnue", by["modal", "Votre avis"]["evidence"][0]["text"])
        self.assertEqual([e["at"] for e in by["budget", "N4"]["evidence"]], [f"journal.jsonl:{n}" for n in range(14, 19)])
        self.assertEqual([e["at"] for e in by["recipe", "maintenance"]["evidence"]], ["driver.log:9"])
        self.assertEqual(by["oracle", "answer_scope"]["evidence_total"], 22)
        self.assertEqual(len(by["oracle", "answer_scope"]["evidence"]), debrief.EVIDENCE_CAP)
        for p in self.p:
            self.assertTrue(p["evidence"], p)
            for e in p["evidence"]:
                self.assertEqual(set(e), {"at", "task", "text"})
                self.assertLessEqual(len(e["text"]), debrief.CLIP)
            self.assertEqual(p["proposed_by"], "debrief")
            self.assertIn("l'opérateur", p["human"])  # a proposal is never applied by the stage

    def test_each_manual_loop_of_17_10_had_its_answer_before_the_relaunch(self):
        """Fixture outcome (debrief §4, « Gain sur ce run »). Four manual loops on 17/10: the selector
        (t2 → t7), the relaunch without a fix (t7 → t16), the modal (t16 → t25), the judge's table
        (t25 → t39). A debrief run at the end of t2, t7 and t16 already held the proposal that answers
        each of the first three; the fourth needs S14 (the judge's claims against the journal), whose
        input — the judge's text — lives in the register, not in clear on the node: out of this stage.
        Three manual loops saved, one left."""
        def proposals_until(task):
            return {(p["kind"], p["target"], p["confidence"]) for p in debrief.debrief(RUN_17_10, until=task)[1]}
        self.assertEqual(proposals_until("task-node-1-2"), {("selector", "assistant_message", "high")})
        self.assertIn(("recipe", "level_without_artefact", "medium"), proposals_until("task-node-1-7"))
        after_t16 = proposals_until("task-node-1-16")
        self.assertIn(("modal", "Votre avis", "high"), after_t16)
        self.assertIn(("recipe", "level_without_artefact", "high"), after_t16)
        by = {(p["kind"], p["target"]): p for p in self.p}
        self.assertEqual(by["selector", "assistant_message"]["tasks"], ["task-node-1-2"])
        self.assertEqual(by["modal", "Votre avis"]["tasks"], ["task-node-1-16"])
        self.assertEqual(by["recipe", "level_without_artefact"]["tasks"], ["task-node-1-7", "task-node-1-16"])

    def test_the_markdown_is_sections_1_to_3_in_the_debrief_register(self):
        for heading in ("# Debrief automatique — maskai-1 (2026-10-17)", "## 1. Chronologie du run",
                        "## 2. Défauts révélés par le terrain", "## 3. Mesures utiles au prochain pilote"):
            self.assertIn(heading, self.md)
        self.assertNotIn("## 4.", self.md)
        self.assertNotIn("## 5.", self.md)
        self.assertIn("| task-node-1-25 | 15:59–16:25 |", self.md)
        for p in self.p:
            self.assertIn(f"{p['kind']} `{p['target']}` ({p['confidence']})", self.md)


class DebriefContractTest(unittest.TestCase):
    def test_a_clean_run_yields_no_proposal(self):
        run = debrief_fixtures.clean_run(Path(tempfile.mkdtemp()) / "run")
        s, p, _ = debrief.debrief(run)
        self.assertEqual(p, [])
        self.assertEqual(len(s["cases"]), 4)
        self.assertEqual(s["relaunches"], [])
        self.assertEqual(s["hashes"]["lines_without_ui_profile"], 0)

    def test_reproducible_bytes_and_nothing_written_outside_out(self):
        before = tree_sha(RUN_17_10)
        a, b = Path(tempfile.mkdtemp()) / "a", Path(tempfile.mkdtemp()) / "b"
        for out in (a, b):
            debrief.write(*debrief.debrief(RUN_17_10), out)
        for f in ("debrief.json", "proposals.json", "debrief.md"):
            self.assertEqual((a / f).read_bytes(), (b / f).read_bytes(), f)
            self.assertNotIn(b"\r\n", (a / f).read_bytes())
        self.assertEqual(sorted(x.name for x in a.iterdir()), ["debrief.json", "debrief.md", "proposals.json"])
        self.assertEqual(tree_sha(RUN_17_10), before, "the run directory is read, never written")
        self.assertNotIn(str(RUN_17_10.parent).encode(), (a / "debrief.json").read_bytes(), "no absolute path")

    def test_a_learned_driver_s_selector_fix_goes_to_the_deliberate_run(self):
        run = Path(tempfile.mkdtemp()) / "run"
        shutil.copytree(RUN_17_10, run)
        (run / "manifest.json").write_text(json.dumps({"name": "word-maison", "kind": "com"}), encoding="utf-8")
        p = {(x["kind"], x["target"]): x for x in debrief.debrief(run)[1]}
        self.assertIn("vk driver learn --deliberate", p["selector", "assistant_message"]["human"])
        self.assertIn("vk driver learn --deliberate", p["modal", "Votre avis"]["human"])
        self.assertNotIn("--deliberate", p["budget", "N4"]["human"])

    def test_refusals_are_one_line_with_a_remedy(self):
        empty = Path(tempfile.mkdtemp())
        with self.assertRaises(Refusal) as cm:
            debrief.debrief(empty)
        self.assertIn("no journal.jsonl", str(cm.exception))
        self.assertIn("; remedy: ", str(cm.exception))
        with self.assertRaises(Refusal) as cm:
            debrief.debrief(RUN_17_10, until="task-nope")
        self.assertIn("until: no task task-nope", str(cm.exception))
        used = Path(tempfile.mkdtemp())
        (used / "x").write_text("x")
        with self.assertRaises(Refusal) as cm:
            debrief.write(*debrief.debrief(RUN_17_10), used)
        self.assertIn("is not empty", str(cm.exception))

    def test_the_stage_is_registered_and_runs_from_the_cli_entry(self):
        from vkforge.__main__ import main
        out = Path(tempfile.mkdtemp()) / "out"
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            self.assertEqual(main(["debrief", "--run", str(RUN_17_10), "--out", str(out)]), 0)
        self.assertIn("debrief: 6 proposals", printed.getvalue())
        self.assertEqual(len(json.loads((out / "proposals.json").read_text(encoding="utf-8"))), 6)

    def test_harness_notes_are_read_through_ansi_colours(self):
        line = "\x1b[2m2026-10-17T13:49:14.302877Z\x1b[0m \x1b[32m INFO\x1b[0m vk_ipc::server: harness log task=t harness=x"
        m = debrief.NOTE.match(debrief.ANSI.sub("", line))
        self.assertEqual((m.group(2), m.group(3)), ("t", "x"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run** `python -I -m unittest discover -s drivers/vkforge/tests -t .` → ImportError (no `vkforge.debrief`).

- [ ] **Step 4: Implement** `drivers/vkforge/debrief.py` — this module was written and run against the tests above while this task was written (10 tests green, also under CI's `python -I -m unittest discover -s drivers -p "test_*.py" -t .`); stdlib only, no socket (Task 0's test covers it):

```python
"""vkforge debrief --run DIR --out DIR [--until TASK] — the post-launch retro-learning loop.

Founder decision 2026-10-17; requirements: docs/technical/debriefs/2026-10-17-maskai-driver.md §4.
Reads what the node holds in clear after a governed run of a driver and writes debrief.json (the
signals), proposals.json (what to change, each with its evidence lines) and debrief.md (sections
1-3 of a debrief). Deterministic, stdlib only, no model, no socket. Nothing is applied here: the
operator applies a proposal; for a learned driver the deliberate run proposes the rule (spec §5.3).

Run directory (fixed names; only journal.jsonl and tasks/ are required):
  journal.jsonl          the run's journal (the driver's work directory)
  journal-*.jsonl        journals set aside during the run (a first try, kept)
  driver.log             the driver's stderr, as the host appends it
  vkd-console.log        the daemon's console: harness notes `harness log task=… harness=…`
  tasks/<task_id>.json   `vk task show <task_id> --json`, one per task of the run
  dmesg.json             `vk dmesg -n N --json` covering the run (`kind`, `wall_ms` are read)
  cases.json             the driver's cases (`level`, `kind`, `budget_s` per case are read)
  manifest.json          the driver's manifest (`kind: com` marks a learned driver)
"""
import argparse
import json
import math
import re
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .errors import Refusal

KINDS = ("selector", "modal", "budget", "oracle", "recipe")
REASONS = ("timeout", "dialog", "precondition", "host", "internal")
EVIDENCE_CAP = 8
CLIP = 240
# What stays human, per kind (debrief 2026-10-17 §4): the debrief proposes, never writes.
HUMAN = {
    "selector": "l'opérateur corrige SELECTORS dans driver.py : nouveau driver_id, remontage signé",
    "modal": "l'opérateur qualifie la modale (bénigne ou sécurité) puis l'ajoute à KNOWN_MODALS : "
             "nouveau driver_id, remontage signé",
    "budget": "l'opérateur change budget_s dans cases.json : nouvelle inputs_sha256, déclarée dans le delta du rapport",
    "oracle": "l'opérateur change l'oracle ou l'évaluation : nouvelle inputs_sha256 ; un verdict publié ne se "
              "corrige que par une ligne supersedes écrite par un humain",
    "recipe": "l'opérateur change la consigne de la recette (recipes/*.json), relue et committée",
}
# A learned driver's code is never edited by hand (spec §3.3): its selector and modal fixes go
# through the deliberate run, which proposes a projection rule a human reviews (spec §5.3).
HUMAN_LEARNED = ("driver appris : son code ne s'édite pas ; vk driver learn --deliberate VERBE propose la règle, "
                 "relue par un humain (spec §5.3)")

ABANDON = re.compile(r"abandon à (\d+(?:[.,]\d+)?) s \(3 × budget (\d+(?:[.,]\d+)?) s\)")
OVER = re.compile(r"latence (\d+(?:[.,]\d+)?) s > budget (\d+(?:[.,]\d+)?) s")
EMPTY = re.compile(r"réponse vide")
ANSWER = re.compile(r"réponse : «(.*)", re.S)
HEADER = re.compile(r"\bmaskai\s+[a-z][a-z0-9_-]*/[a-z0-9][a-z0-9._:-]*|aucune donn[ée]e personnelle d[ée]tect[ée]e"
                    r"|anonymis[ée]\s*→|l'aiguilleur", re.I)
LIVE = re.compile(r"·\s*en direct|affichage encore en mouvement")
UNSENT = "texte envoyé non observé"
UNSEEN_DOC = re.compile(r"je n['\u2019]ai pas acc[èe]s|aucun (?:document|fichier)|ne (?:vois|retrouve) aucun", re.I)
ANSI = re.compile(r"\x1b\[[0-9;]*m")
NOTE = re.compile(r"^(\S+)\s.*?harness log task=(\S+) harness=(.*)$")
REFUSAL = re.compile(r"refus\w*\s*\((%s)\)" % "|".join(REASONS))
MODAL = re.compile(r"modale inconnue\s*:\s*(.+?)(?:\s+[—–]\s+|\s*;|\s*$)")
LOGLINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ ([A-Z]+) (\S+) (.*)$")
MAINT = re.compile(r"service in maintenance|service en maintenance", re.I)
DISMISSED = re.compile(r"known modal (\S+) dismissed")
LEVEL = re.compile(r"^\s*Niveau (N\d+)")
NO_ARTEFACT = "harness produced no artefact"


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _num(s: str) -> float:
    return float(s.replace(",", "."))


def _clip(s: str) -> str:
    s = " ".join(s.split())
    return s if len(s) <= CLIP else s[: CLIP - 1] + "…"


def _status(s) -> str:
    return s if isinstance(s, str) else "; ".join(f"{k}: {v}" for k, v in s.items())


def _stats(values: list) -> dict:
    v = sorted(values)
    return {"n": len(v), "median": statistics.median(v), "p90": v[max(0, math.ceil(0.9 * len(v)) - 1)], "max": v[-1]}


def _answer(e: dict):
    m = ANSWER.search(e.get("evidence") or "")
    return m.group(1) if m else None


class Run:
    """The run directory, loaded: every line keeps its place (`at`), its time and its task."""

    def __init__(self, root: Path, until=None):
        if not (root / "journal.jsonl").is_file():
            raise Refusal(f"no journal.jsonl under {root}",
                          "copy the driver's journal (its work directory) into the run directory")
        dumps = sorted((root / "tasks").glob("*.json")) if (root / "tasks").is_dir() else []
        if not dumps:
            raise Refusal(f"no task dump under {root / 'tasks'}",
                          "vk task show TASK --json > tasks/TASK.json for each task of the run")
        self.root, self.text = root, {}
        tasks = []
        for p in dumps:
            t = json.loads(p.read_text(encoding="utf-8-sig"))
            ends = [x for s in t["steps"] for x in (s.get("started_ms"), s.get("ended_ms")) if x]
            tasks.append(dict(t, _file=f"tasks/{p.name}", _end=max(ends + [t["created_ms"]])))
        self.tasks = sorted(tasks, key=lambda t: t["created_ms"])
        self.limit = None
        if until is not None:
            hit = [t for t in self.tasks if t["id"] == until]
            if not hit:
                raise Refusal(f"until: no task {until} in the run", "name a task of the run's tasks/ directory")
            self.limit = hit[0]["_end"]
            self.tasks = [t for t in self.tasks if t["created_ms"] <= self.limit]
        cases = root / "cases.json"
        self.cases = json.loads(cases.read_text(encoding="utf-8-sig"))["cases"] if cases.is_file() else {}
        manifest = root / "manifest.json"
        self.learned = manifest.is_file() and json.loads(manifest.read_text(encoding="utf-8-sig")).get("kind") == "com"
        self.journal = self._journal("journal.jsonl", effective=True)
        self.set_aside = [e for p in sorted(root.glob("journal-*.jsonl")) for e in self._journal(p.name, effective=False)]
        first = (self.journal or self.set_aside or [None])[0]
        # driver.log carries the node's local time without an offset: the journal's offset names it
        self.tz = timezone(first["_t0"].utcoffset() if first else timedelta(0))
        self.notes = self._notes(root / "vkd-console.log")
        self.log = self._driver_log(root / "driver.log")
        dmesg = root / "dmesg.json"
        rows = json.loads(dmesg.read_text(encoding="utf-8-sig")) if dmesg.is_file() else None
        self.ledger = None if rows is None else [r for r in rows if self._keep(r["wall_ms"])]

    def _keep(self, ms: int) -> bool:
        return self.limit is None or ms <= self.limit

    def _task(self, lo: int, hi: int):
        hit = [t["id"] for t in self.tasks if t["created_ms"] <= hi and t["_end"] >= lo]
        return hit[-1] if hit else None

    def _journal(self, name: str, effective: bool) -> list:
        events = [json.loads(x) for x in (self.root / name).read_text(encoding="utf-8-sig").splitlines() if x.strip()]
        dropped = {e["supersedes"] for e in events if effective and e.get("supersedes") is not None}
        out = []
        for i, e in enumerate(events):
            t0 = datetime.fromisoformat(e["ts"])
            lo = _ms(t0)
            hi = lo + (59_999 if len(e["ts"]) <= 22 else 999)  # "…T15:31+02:00" is a minute wide
            if i in dropped or not self._keep(lo):
                continue
            at = f"{name}:{i + 1}"
            self.text[at] = json.dumps(e, ensure_ascii=False)
            out.append(dict(e, _at=at, _t0=t0, _lo=lo, _task=self._task(lo, hi)))
        return out

    def _notes(self, path: Path):
        if not path.is_file():
            return None
        out = []
        for n, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            m = NOTE.match(ANSI.sub("", raw))
            if m and self._keep(ms := _ms(datetime.fromisoformat(m.group(1)))):
                self.text[f"{path.name}:{n}"] = m.group(3)
                out.append({"at": f"{path.name}:{n}", "ms": ms, "task": m.group(2), "text": m.group(3)})
        return out

    def _driver_log(self, path: Path):
        if not path.is_file():
            return None
        out = []
        for n, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            m = LOGLINE.match(raw)
            if m and self._keep(ms := _ms(datetime.fromisoformat(m.group(1)).replace(tzinfo=self.tz))):
                self.text[f"{path.name}:{n}"] = f"{m.group(2)} {m.group(4)}"
                out.append({"at": f"{path.name}:{n}", "ms": ms, "task": self._task(ms, ms), "text": m.group(4)})
        return out

    def evidence(self, items: list) -> list:
        return [{"at": x["at"], "task": x["task"], "text": _clip(self.text[x["at"]])} for x in items]


def signals(run: Run) -> dict:
    def level_of(cid):
        m = re.match(r"(N\d+)-", cid)
        return run.cases.get(cid, {}).get("level") or (m.group(1) if m else "?")

    notes = run.notes or []
    refusal_notes = [(n, m.group(1)) for n in notes for m in [REFUSAL.search(n["text"])] if m]
    lines = run.journal + run.set_aside
    cases = []
    for e in (x for x in run.journal if x.get("step") == "exec"):  # the run's verdicts, corrections applied
        ev, cid = e.get("evidence") or "", e["case"]
        ab, over = ABANDON.search(ev), OVER.search(ev)
        budget = _num((ab or over).group(2)) if (ab or over) else run.cases.get(cid, {}).get("budget_s")
        reasons = (["timeout"] if ab else []) + [
            r for n, r in refusal_notes if r != "timeout" and re.search(rf"\b{re.escape(cid)}\b", n["text"])]
        cases.append({"case": cid, "level": level_of(cid), "verdict": e["verdict"], "timing_s": e.get("timing_s"),
                      "budget_s": budget, "aborted": bool(ab), "over_budget": bool(ab or over), "reasons": reasons,
                      "at": e["_at"], "task": e["_task"]})

    def acts_in(lo, hi):
        if run.ledger is None:
            return None
        return sum(1 for r in run.ledger if r["kind"] == "driver.acted" and lo <= r["wall_ms"] <= hi)

    levels, tasks = {}, []
    for t in run.tasks:  # one pass per harness step whose instruction names its level
        usage = t.get("usage") or []

        def cost(i=None):
            return round(sum(u.get("cost_list_usd") or 0 for u in usage if i is None or u.get("step_index") == i), 3)

        harness = 0.0
        for i, s in enumerate(t["steps"]):
            m = LEVEL.match(s["kind"].get("instruction") or "") if s["kind"].get("kind") == "harness" else None
            if not m or not s.get("started_ms"):
                continue
            harness += cost(i)
            lo, hi = s["started_ms"], s.get("ended_ms") or s["started_ms"]
            p = {"task": t["id"], "step": i, "level": m.group(1), "status": _status(s["status"]),
                 "duration_s": round((hi - lo) / 1000), "acts": acts_in(lo, hi), "cost_usd": cost(i),
                 "new_lines": sum(1 for e in lines if e["_task"] == t["id"] and level_of(e["case"]) == m.group(1)),
                 "at": f"{t['_file']}#steps[{i}]", "_w": (lo, hi)}
            run.text[p["at"]] = f"{p['level']} : {p['status']} ({p['acts']} actes)"
            levels.setdefault(m.group(1), []).append(p)
        tasks.append({"id": t["id"], "status": t["status"], "window": [t["created_ms"], t["_end"]],
                      "levels": [lv for lv in sorted(levels) if any(p["task"] == t["id"] for p in levels[lv])],
                      "acts": acts_in(t["created_ms"], t["_end"]), "harness_usd": round(harness, 3), "cost_usd": cost()})
    passes = [p for ps in levels.values() for p in ps]
    replays = [p for p in passes if p["status"] == "done" and p["new_lines"] == 0]
    console_from = notes[0]["ms"] if notes else None
    no_artefact = [{"task": p["task"], "level": p["level"], "step": p["step"], "acts": p["acts"], "at": p["at"],
                    "refusals_noted": None if console_from is None or console_from > p["_w"][0] else sum(
                        1 for n, _ in refusal_notes if n["task"] == p["task"] and p["_w"][0] <= n["ms"] <= p["_w"][1])}
                   for p in passes if NO_ARTEFACT in p["status"]]
    modals = {}
    for i, n in enumerate(notes):  # after_abort: the task's previous other note was an abandon
        m = MODAL.search(n["text"])
        if m:
            before = [x for x in notes[:i] if x["task"] == n["task"] and not MODAL.search(x["text"])]
            e = modals.setdefault(m.group(1), {"text": m.group(1), "count": 0, "after_abort": 0, "lines": []})
            e["count"] += 1
            e["after_abort"] += bool(before and "abandon" in before[-1]["text"])
            e["lines"].append({"at": n["at"], "task": n["task"]})
    dismissed = {}
    for x in run.log or []:
        if m := DISMISSED.search(x["text"]):
            dismissed[m.group(1)] = dismissed.get(m.group(1), 0) + 1
    maintenance = ([{"at": x["at"], "task": x["task"]} for x in run.log or [] if MAINT.search(x["text"])]
                   + [{"at": n["at"], "task": n["task"]} for n in notes if MAINT.search(n["text"])]
                   + [{"at": e["_at"], "task": e["_task"]} for e in lines if MAINT.search(e.get("evidence") or "")])
    answered = [(e, a) for e in run.journal for a in [_answer(e)] if a is not None]
    pipelines = [e["pipeline_ms"] for e in run.journal if e.get("pipeline_ms")]
    nodes = sorted({k for p in pipelines for k in p})
    classes = {}
    for c in cases:
        if isinstance(c["timing_s"], (int, float)):
            classes.setdefault((run.cases.get(c["case"], {}).get("kind", "?"), c["budget_s"] or 0), []).append(c)
    hashes = {"lines_without_ui_profile": sum(1 for e in lines if "ui_profile_sha256" not in e), "changes": []}
    for field in ("inputs_sha256", "ui_profile_sha256"):
        seen, prev = [], None
        for e in sorted(lines, key=lambda e: (e["_lo"], e["_at"])):
            v = e.get(field)
            if v and v not in [x["value"] for x in seen]:
                seen.append({"value": v, "from": e["_at"]})
            if v and prev and v != prev:
                hashes["changes"].append({"field": field, "at": e["_at"], "from": prev, "to": v})
            prev = v or prev
        hashes[field] = seen
    header = [(e, a) for e, a in answered if HEADER.search(a)]
    aborted = sorted(c["case"] for c in cases if c["aborted"])
    concurrent = [{"case": c["case"], "at": c["at"], "pairs": pairs} for c in cases
                  if run.cases.get(c["case"], {}).get("kind") == "concurrent"
                  for pairs in [[x for x in aborted if re.search(rf"\b{re.escape(x)}\b", run.text[c["at"]])]] if pairs]
    composer = {}
    for e in run.journal:
        if e.get("config"):
            key = (e["config"].get("model"), e["config"].get("web"))
            composer[key] = composer.get(key, 0) + 1
    return {
        "run": {"tasks": [t["id"] for t in run.tasks], "until_ms": run.limit, "learned_driver": run.learned,
                "inputs": sorted(p.relative_to(run.root).as_posix() for p in run.root.rglob("*") if p.is_file()),
                "console_from_ms": console_from},
        "tasks": tasks,
        "acts_total": None if run.ledger is None else sum(1 for r in run.ledger if r["kind"] == "driver.acted"),
        "relaunches": [{"after": a["id"], "next": b["id"]} for a, b in zip(run.tasks, run.tasks[1:]) if a["status"] != "done"],
        "cases": cases,
        "levels": {k: [{x: v for x, v in p.items() if x != "_w"} for p in ps] for k, ps in sorted(levels.items())},
        "refusals": {r: sum(c["aborted"] for c in cases) if r == "timeout" else sum(1 for _, x in refusal_notes if x == r)
                     for r in REASONS},
        "selector_suspects": [{"case": e["case"], "at": e["_at"], "task": e["_task"], "call_llm_ms": e["pipeline_ms"]["call_llm"]}
                              for e in lines if EMPTY.search(e.get("evidence") or "")
                              and (e.get("pipeline_ms") or {}).get("call_llm", 0) > 0],
        "empty_without_llm": [{"case": e["case"], "at": e["_at"], "task": e["_task"]} for e in lines
                              if EMPTY.search(e.get("evidence") or "") and not (e.get("pipeline_ms") or {}).get("call_llm")],
        "modal_refusals": list(modals.values()),
        "known_modals_dismissed": dismissed,
        "maintenance": maintenance,
        "cut_suspects": [{"case": e["case"], "at": e["_at"]} for e, a in answered if LIVE.search(a)],
        "attachment_unseen": [{"case": e["case"], "at": e["_at"]} for e, a in answered
                              if run.cases.get(e["case"], {}).get("kind") == "attach_question" and UNSEEN_DOC.search(a)],
        "header_in_answer": {"lines": len(header), "of": len(answered),
                             "at": [{"at": e["_at"], "task": e["_task"]} for e, _ in header]},
        "sent_unobserved": {"lines": sum(1 for e, _ in answered if UNSENT in e["evidence"]), "of": len(answered)},
        "replays": {"passes": len(replays), "acts": sum(p["acts"] or 0 for p in replays),
                    "cost_usd": round(sum(p["cost_usd"] for p in replays), 3)},
        "levels_without_artefact": no_artefact,
        "concurrent_on_failing": concurrent,
        "composer": [{"model": m, "web": w, "lines": n} for (m, w), n in sorted(composer.items(), key=str)],
        "hashes": hashes,
        "constant_nodes": {k: vs[0] for k in nodes for vs in [[p[k] for p in pipelines if k in p]]
                           if len(vs) >= 5 and len(set(vs)) == 1},
        "pipeline": {k: _stats([p[k] for p in pipelines if k in p]) for k in nodes},
        "latency_classes": [{"kind": k, "budget_s": b, **_stats([c["timing_s"] for c in cs]),
                             "over_budget": sum(c["over_budget"] for c in cs), "aborted": sum(c["aborted"] for c in cs)}
                            for (k, b), cs in sorted(classes.items())],
    }


def propose(run: Run, s: dict) -> list:
    out, order = [], {t: i for i, t in enumerate(s["run"]["tasks"])}

    def add(kind, target, items, proposal, confidence):
        out.append({"kind": kind, "target": target, "evidence": run.evidence(items[:EVIDENCE_CAP]),
                    "evidence_total": len(items), "proposal": proposal, "confidence": confidence,
                    "tasks": sorted({x["task"] for x in items if x["task"]}, key=order.get), "proposed_by": "debrief",
                    "human": HUMAN_LEARNED if run.learned and kind in ("selector", "modal") else HUMAN[kind]})

    sus = s["selector_suspects"]  # D1: an empty answer although the stream carried call_llm
    if sus:
        add("selector", "assistant_message", sus,
            f"{', '.join(x['case'] for x in sus)} : réponse vide alors que le flux a porté call_llm "
            f"({', '.join(str(x['call_llm_ms']) for x in sus)} ms) : le sélecteur de la réponse ne vise plus la carte ; "
            "la relever sur dom.html ou la capture, corriger le sélecteur, sonder l'interface avant N0",
            "high" if len(sus) >= 2 else "medium")
    for m in s["modal_refusals"]:  # D2: a modal the driver does not know blocks every act
        add("modal", m["text"], m["lines"],
            f"KNOWN_MODALS += « {m['text']} » : sélecteur structurel relevé sur modal.html ou la capture, fermeture "
            "par la croix, jamais par « Envoyer »" + (f" ; {m['after_abort']}/{m['count']} refus suivent un abandon : "
                                                     "cliquer sur arrêt avant de rendre la main" if m["after_abort"] else ""),
            "high" if m["count"] >= 2 else "medium")
    by_level = {}
    for c in s["cases"]:
        by_level.setdefault(c["level"], []).append(c)
    for level, cs in sorted(by_level.items()):  # D8: a level whose cases end by abandon
        ab = [c for c in cs if c["aborted"]]
        if len(ab) >= 2 and 2 * len(ab) >= len(cs):
            profiled = all("question partie" in run.text[c["at"]] and "flux ouvert" in run.text[c["at"]] for c in ab)
            add("budget", level, ab,
                f"{level} : {len(ab)}/{len(cs)} cas abandonnés à 3 × le budget (budgets "
                f"{', '.join(f'{b:g}' for b in sorted({c['budget_s'] for c in ab}))} s) ; "
                + ("la question est partie et le flux a démarré : relever le budget de la classe au temps du produit"
                   if profiled else "sans profil d'abandon la faute produit n'est pas démontrée : instrumenter "
                                    "l'abandon (question partie, flux ouvert, dernier nœud) avant de juger, ou relever le budget"),
                "high" if profiled else "medium")
    h = s["header_in_answer"]  # D5: the judged text carries the header (model banner, guard panel)
    if h["of"] >= 3 and 2 * h["lines"] >= h["of"]:
        add("oracle", "answer_scope", h["at"],
            f"la réponse jugée contient l'en-tête (bannière du modèle, panneau de garde) sur {h['lines']}/{h['of']} "
            "lignes : un must qui nomme un modèle ou un mot de garde peut passer sur l'en-tête seul ; évaluer le "
            "corps seul, rejuger les cas dont un must nomme un modèle", "medium")
    if s["maintenance"]:
        add("recipe", "maintenance", s["maintenance"],
            f"la page de maintenance est apparue {len(s['maintenance'])} fois : la consigne suspend le niveau sur le "
            "refus « service en maintenance », sans juger, et le reprend quand le service revient ; les cas jugés "
            "pendant la fenêtre se rejouent", "high")
    na = s["levels_without_artefact"]  # D11: a harness ended a level without an artefact
    if na:
        add("recipe", "level_without_artefact", na,
            f"{len(na)} niveau(x) fini(s) sans artefact ({', '.join(x['task'] + ' ' + x['level'] for x in na)}) : "
            "la consigne termine un niveau par une note de refus (raison, dernier refus) quand tous ses actes sont "
            "refusés ; côté driver, une capture d'écran hors du garde-modale", "high" if len(na) >= 2 else "medium")
    return sorted(out, key=lambda p: (KINDS.index(p["kind"]), p["target"]))


def _hm(ms: int, tz) -> str:
    return datetime.fromtimestamp(ms / 1000, tz).strftime("%H:%M")


def _usd(x) -> str:
    return "—" if x is None else f"{x:.3f}".replace(".", ",")


def _n(x) -> str:
    return f"{x:g}".replace(".", ",")


def render(run: Run, s: dict, proposals: list) -> str:
    """Sections 1-3 of a debrief, in the register of docs/technical/debriefs/; 4 and 5 are human."""
    tz = run.tz
    drivers = sorted({d for t in run.tasks for st in t["steps"] for d in st["kind"].get("drivers", [])})
    day = datetime.fromtimestamp(run.tasks[0]["created_ms"] / 1000, tz).date().isoformat()
    ref = {(p["kind"], p["target"]): f"{p['kind']} `{p['target']}` ({p['confidence']})" for p in proposals}
    out = [f"# Debrief automatique — {', '.join(drivers) or 'driver'} ({day})", "",
           "Produit par `vkforge debrief` sur " + ", ".join(f"`{x}`" for x in s["run"]["inputs"]) + ". **[V]** lu sur "
           "pièce, **[I]** déduit. Heures locales, coûts USD des tables `vk task show`. Les sections 4 et 5 (cahier des "
           "charges, propositions retenues) restent humaines : `proposals.json` en est l'entrée.", "",
           "## 1. Chronologie du run", "",
           "| Tâche | Fenêtre | Niveaux | Statut | Actes | Harnais | Total |", "|---|---|---|---|---|---|---|"]
    for t in s["tasks"]:
        out.append(f"| {t['id']} | {_hm(t['window'][0], tz)}–{_hm(t['window'][1], tz)} | {', '.join(t['levels']) or '—'} "
                   f"| {t['status']} | {'—' if t['acts'] is None else t['acts']} | {_usd(t['harness_usd'])} | {_usd(t['cost_usd'])} |")
    out += ["", "| Niveau | Tâche | Statut | Durée | Actes | Coût | Lignes |", "|---|---|---|---|---|---|---|"]
    out += [f"| {lv} | {p['task']} | {p['status']} | {p['duration_s']} s | {'—' if p['acts'] is None else p['acts']} | "
            f"{_usd(p['cost_usd'])} | {p['new_lines']} |" for lv, ps in s["levels"].items() for p in ps]
    r = s["replays"]
    out += ["", f"Rejeux sans ligne nouvelle : {r['passes']} passes, {r['acts']} actes, {_usd(r['cost_usd'])} USD [V]. "
                f"Relances après un arrêt : {len(s['relaunches'])} [V].", "",
            "## 2. Défauts révélés par le terrain", "",
            "| Signal | Constat | Preuve | Proposition |", "|---|---|---|---|"]

    def row(signal, seen, items, key=None):
        at = ", ".join(x["at"] for x in items[:4]) + (" …" if len(items) > 4 else "")
        out.append(f"| {signal} | {seen} | {at or '—'} | {ref.get(key, '—')} |")

    sus = s["selector_suspects"]
    row("réponse vide, call_llm présent", (", ".join(x["case"] for x in sus) + " [V]") if sus else "aucune", sus,
        ("selector", "assistant_message"))
    for m in s["modal_refusals"]:
        row("modale inconnue", f"« {m['text']} » × {m['count']}, {m['after_abort']} après un abandon [V]", m["lines"],
            ("modal", m["text"]))
    for p in (p for p in proposals if p["kind"] == "budget"):
        row("abandons", f"{p['target']} : {p['evidence_total']} abandons [V]", p["evidence"], ("budget", p["target"]))
    h = s["header_in_answer"]
    row("en-tête dans la réponse jugée", f"{h['lines']}/{h['of']} [V]", h["at"], ("oracle", "answer_scope"))
    row("page de maintenance", f"{len(s['maintenance'])} [V]", s["maintenance"], ("recipe", "maintenance"))
    row("niveau fini sans artefact", f"{len(s['levels_without_artefact'])} [V]", s["levels_without_artefact"],
        ("recipe", "level_without_artefact"))
    row("réponse vide sans call_llm (produit)", ", ".join(x["case"] for x in s["empty_without_llm"]) or "aucune",
        s["empty_without_llm"])
    row("texte envoyé non observé", f"{s['sent_unobserved']['lines']}/{s['sent_unobserved']['of']} [V]", [])
    row("réponse coupée (marque de génération)", f"{len(s['cut_suspects'])} [I]", s["cut_suspects"])
    row("pièce jointe non vue du modèle", f"{len(s['attachment_unseen'])} [I]", s["attachment_unseen"])
    row("charge concurrente sur des cas déjà KO seuls", ", ".join(f"{x['case']} ({' + '.join(x['pairs'])})"
                                                                 for x in s["concurrent_on_failing"]) or "aucune",
        s["concurrent_on_failing"])
    row("configuration du compositeur", "; ".join(f"{c['model']} / web {c['web']} × {c['lines']}" for c in s["composer"])
        or "non relevée sur les lignes", [])
    hs = s["hashes"]
    row("empreintes", f"inputs_sha256 : {len(hs['inputs_sha256'])} valeur(s), {len(hs['changes'])} changement(s) ; "
                      f"ui_profile_sha256 absente sur {hs['lines_without_ui_profile']} lignes [V]", [])
    row("nœuds constants", (", ".join(f"{k} {v}" for k, v in s["constant_nodes"].items()) + " [V]")
        if s["constant_nodes"] else "aucun", [])
    out += ["", "## 3. Mesures utiles au prochain pilote", "",
            "| Nœud (ms) | n | Médiane | p90 | Max |", "|---|---|---|---|---|"]
    out += [f"| `{k}` | {v['n']} | {_n(v['median'])} | {_n(v['p90'])} | {_n(v['max'])} |" for k, v in s["pipeline"].items()]
    out += ["", "| Classe | Budget | n | Médiane | p90 | Max | Hors budget | Abandons |", "|---|---|---|---|---|---|---|---|"]
    out += [f"| {c['kind']} | {_n(c['budget_s'])} s | {c['n']} | {_n(c['median'])} s | {_n(c['p90'])} s | {_n(c['max'])} s | "
            f"{c['over_budget']}/{c['n']} | {c['aborted']} |" for c in s["latency_classes"]]
    out += ["", "Refus par raison (timeout lu au journal, les autres aux notes du harnais ; le ledger les hache) : "
            + ", ".join(f"`{k}` {v}" for k, v in s["refusals"].items()) + " [V].", ""]
    return "\n".join(out)


def debrief(root: Path, until=None) -> tuple:
    run = Run(root, until)
    s = signals(run)
    proposals = propose(run, s)
    return s, proposals, render(run, s, proposals)


def write(s: dict, proposals: list, md: str, out: Path) -> None:
    if out.exists() and any(out.iterdir()):
        raise Refusal(f"out {out} is not empty", "a new directory: a debrief never overwrites one")
    out.mkdir(parents=True, exist_ok=True)
    for name, value in (("debrief.json", s), ("proposals.json", proposals)):
        (out / name).write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=1) + "\n",
                                encoding="utf-8", newline="\n")
    (out / "debrief.md").write_text(md, encoding="utf-8", newline="\n")


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--run", required=True, help="the run directory (layout in the module docstring)")
    p.add_argument("--out", required=True, help="a new or empty directory")
    p.add_argument("--until", help="debrief only the evidence up to the end of this task")


def run(ns: argparse.Namespace) -> int:
    s, proposals, md = debrief(Path(ns.run), ns.until)
    write(s, proposals, md, Path(ns.out))
    print(f"debrief: {len(proposals)} proposals -> {ns.out}")
    return 0


from .__main__ import STAGES  # noqa: E402  (Task 0's registry)

STAGES["debrief"] = (add_arguments, run)
```

and in `__main__.py`:

```python
def _register():
    from . import inventory, ontology, mine, plan, generate, qualify, debrief  # noqa: F401  (each registers itself)
```

- [ ] **Step 5: Run** the vkforge suite green.

- [ ] **Step 6: Failing Rust tests.** `crates/vk-ipc/tests/forge.rs` (Task 9's file and `Node` helper; written in Task 9's sketch style — follow the `Node` API Task 9 landed, `async` with `.await` if it is; if the file already has helpers for the repository root and the interpreter, use them and drop the two below):

```rust
use std::path::{Path, PathBuf}; // if the file does not import them yet

fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../..").canonicalize().unwrap()
}

fn forge_python() -> PathBuf {
    std::env::var_os("VK_FORGE_PYTHON").map(PathBuf::from).unwrap_or_else(|| "python".into())
}

/// A run directory from `drivers/vkforge/tests/debrief_fixtures.py`: "17-10" or "clean".
fn debrief_run(dir: &Path, which: &str) -> PathBuf {
    let script = repo_root().join("drivers/vkforge/tests/debrief_fixtures.py");
    let ok = std::process::Command::new(forge_python())
        .arg("-I").arg(&script).arg(dir).arg(which)
        .status().expect("run the fixture builder").success();
    assert!(ok, "debrief_fixtures.py {which}");
    dir.to_path_buf()
}

#[test]
fn debrief_is_a_machine_act_that_writes_three_files_and_no_ledger_row() {
    if !python_available() { return; }
    let node = Node::start();
    let run = debrief_run(&node.dir.join("run"), "17-10");
    let out = node.dir.join("debrief");
    let before = node.last_event().seq;
    let r = node.call("driver.debrief", json!({"run": run, "out": out})).unwrap();   // no presence proof
    for f in ["debrief.json", "proposals.json", "debrief.md"] {
        assert!(out.join(f).is_file(), "{f}");
    }
    assert_eq!(r["proposals"], json!(6));
    assert_eq!(r["list"][0]["target"], json!("assistant_message"));
    assert_eq!(r["proposals_sha256"], json!(vk_contracts::hash_bytes(&std::fs::read(out.join("proposals.json")).unwrap())));
    assert_eq!(node.last_event().seq, before, "no ledger row: driver.debriefed is the founder's open decision");
}

#[test]
fn debrief_refuses_a_run_without_journal_and_a_used_out_before_spawning() {
    if !python_available() { return; }
    let node = Node::start();
    let empty = node.dir.join("empty");
    std::fs::create_dir_all(&empty).unwrap();
    let e = node.call("driver.debrief", json!({"run": empty, "out": node.dir.join("o1")})).unwrap_err().to_string();
    assert!(e.contains("no journal.jsonl") && e.contains("; remedy: "), "{e}");
    let run = debrief_run(&node.dir.join("clean"), "clean");
    let used = node.dir.join("used");
    std::fs::create_dir_all(&used).unwrap();
    std::fs::write(used.join("x"), "x").unwrap();
    let e = node.call("driver.debrief", json!({"run": run, "out": used})).unwrap_err().to_string();
    assert!(e.contains("is not empty"), "{e}");
    assert!(!node.dir.join("o1").exists(), "nothing written on a refusal");
}
```

`crates/vk-cli/tests/smoke.rs` (the daemon started the way Task 9's `vk_driver_learn_then_qualify_then_mount_from_the_cli` starts it — extract that start into `forge_node() -> (tempfile::TempDir, Daemon, Shell)` if it is inline — and the same Python guard):

```rust
#[test]
fn vk_driver_debrief_prints_the_proposals_and_writes_no_ledger_row() {
    let (dir, _daemon, sh) = forge_node();
    let run = dir.path().join("run");
    let script = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../drivers/vkforge/tests/debrief_fixtures.py");
    let python = std::env::var_os("VK_FORGE_PYTHON").unwrap_or_else(|| "python".into());
    assert!(Command::new(python).arg("-I").arg(&script).arg(&run).arg("17-10").status().unwrap().success());
    let out = dir.path().join("debrief");
    let head = sh.json(&["dmesg", "-n", "1", "--json"])[0]["seq"].clone();
    let printed = sh.ok(&["driver", "debrief", "--run", run.to_str().unwrap(), "--out", out.to_str().unwrap()]);
    for needle in ["6 proposals", "assistant_message", "Votre avis", "N4", "answer_scope",
                   "level_without_artefact", "maintenance", "nothing applied"] {
        assert!(printed.contains(needle), "{needle} missing:\n{printed}");
    }
    assert!(out.join("debrief.md").is_file());
    assert_eq!(sh.json(&["dmesg", "-n", "1", "--json"])[0]["seq"], head, "a debrief writes no ledger row");
}
```

- [ ] **Step 7: Run** `cargo test --locked -p vk-ipc --test forge debrief` → unknown method `driver.debrief`.

- [ ] **Step 8: Implement.** `crates/vk-ipc/src/forge.rs`:

```rust
use crate::server::{bad, internal};
use serde_json::{json, Value}; // if the file does not import them yet

/// How long a debrief may run: it reads a run's files, a few megabytes.
pub const DEBRIEF_DEADLINE: Duration = Duration::from_secs(120);

/// `driver.debrief` (founder decision 2026-10-17): vkforge's debrief stage on
/// a run directory the operator assembled, under the same governor as
/// `learn`'s stages. A machine act: no presence proof and no ledger row (an
/// optional `driver.debriefed` row is the founder's open decision). Refuses
/// before spawning what the stage would refuse, in the kernel's voice.
pub fn debrief(cfg: &ForgeConfig, state_dir: &Path, run: &Path, out: &Path, until: Option<&str>) -> Result<Value, RpcError> {
    if !run.join("journal.jsonl").is_file() {
        return Err(bad(&format!(
            "no journal.jsonl under {}; remedy: copy the driver's journal (its work directory) into the run directory",
            run.display()
        )));
    }
    if std::fs::read_dir(out).map(|mut d| d.next().is_some()).unwrap_or(false) {
        return Err(bad(&format!(
            "out {} is not empty; remedy: a new directory: a debrief never overwrites one",
            out.display()
        )));
    }
    let mut args = vec!["--run".to_string(), run.display().to_string(), "--out".into(), out.display().to_string()];
    if let Some(task) = until {
        args.extend(["--until".to_string(), task.to_string()]);
    }
    let work = state_dir.join("forge").join("debrief");
    std::fs::create_dir_all(&work).map_err(|e| internal(&format!("{}: {e}", work.display())))?;
    run_stage(cfg, "debrief", &args, &work, DEBRIEF_DEADLINE)?;
    let read = |name: &str| std::fs::read(out.join(name)).map_err(|e| internal(&format!("{name}: {e}")));
    let (signals, proposals) = (read("debrief.json")?, read("proposals.json")?);
    let list: Vec<Value> =
        serde_json::from_slice(&proposals).map_err(|e| internal(&format!("proposals.json: {e}")))?;
    Ok(json!({
        "out": out,
        "proposals": list.len(),
        "list": list.iter()
            .map(|p| json!({"kind": p["kind"], "target": p["target"], "confidence": p["confidence"], "proposal": p["proposal"]}))
            .collect::<Vec<_>>(),
        "debrief_sha256": vk_contracts::hash_bytes(&signals),
        "proposals_sha256": vk_contracts::hash_bytes(&proposals),
    }))
}
```

`server.rs` `dispatch`, beside `driver.learn` (Task 9), with the `ForgeConfig` and state directory that arm uses; `takes_presence` is left as it is:

```rust
        // A machine act: it reads evidence the node holds in clear and writes
        // three files. No presence, no ledger row (`driver.debriefed` is the
        // founder's open decision).
        "driver.debrief" => {
            let path = |key: &str| p[key].as_str().map(PathBuf::from).ok_or_else(|| bad(key));
            forge::debrief(&forge_cfg, &state_dir, &path("run")?, &path("out")?, p["until"].as_str())
        }
```

`vk-cli/src/main.rs`, in `DriverCmd`:

```rust
    /// Debrief a governed run from its evidence: signals, proposals with the
    /// lines that support them, and sections 1-3 of a debrief. Not a human
    /// act: it reads files the node holds in clear and writes three files; it
    /// applies nothing and writes no ledger row.
    Debrief {
        /// The run directory: journal.jsonl and tasks/<id>.json, optionally
        /// journal-*.jsonl, driver.log, vkd-console.log, dmesg.json, cases.json.
        #[arg(long)]
        run: PathBuf,
        /// A new or empty directory for debrief.json, proposals.json, debrief.md.
        #[arg(long)]
        out: PathBuf,
        /// Debrief only the evidence up to the end of this task.
        #[arg(long)]
        until: Option<String>,
    },
```

and its arm (the daemon resolves no relative path of the shell's):

```rust
        Cmd::Driver { what: DriverCmd::Debrief { run, out, until } } => show(
            cli,
            c.call(
                "driver.debrief",
                json!({ "run": std::path::absolute(run)?, "out": std::path::absolute(out)?, "until": until }),
                None,
            )
            .await?,
            render::debriefed,
        ),
```

`render.rs`:

```rust
/// `vk driver debrief`: one line per proposal (kind, target, confidence, what
/// to change), then the files, and that nothing was applied.
pub fn debriefed(v: &Value) -> String {
    let s = |x: &Value| x.as_str().unwrap_or("").to_string();
    let mut lines = vec![format!("debrief: {} proposals -> {}", v["proposals"], s(&v["out"]))];
    for p in v["list"].as_array().into_iter().flatten() {
        lines.push(format!("  {:<8} {:<24} {:<6}  {}", s(&p["kind"]), s(&p["target"]), s(&p["confidence"]), s(&p["proposal"])));
    }
    lines.push("files: debrief.md, debrief.json, proposals.json; nothing applied: each proposal is the operator's to apply".into());
    lines.join("\n")
}
```

- [ ] **Step 9: Docs.** `docs/technical/runbooks/recette-maskai.md`, a new last section:

````markdown
## 16. Après le tir : le debrief automatique

Le debrief lit ce que le nœud garde en clair après un tir (journaux, `driver.log`, console du démon, tables des tâches) et propose quoi changer, chaque proposition avec les lignes qui la fondent. Il ne change rien : c'est le post-mortem d'un crash dump, pas un correctif. Ce qui suit reste à vous : sélecteurs et modales (nouveau `driver_id`, remontage signé), budgets et oracles (nouvelle `inputs_sha256`, déclarée dans le delta), consigne de la recette, corrections `supersedes`, signatures.

Pour garder la console, lancez le démon du §3.2 ainsi (Windows PowerShell 5.1 : `Add-Content -Encoding UTF8` écrit de l'UTF-8) :
```powershell
vkd --auto-enroll-node --harness-timeout-secs 2700 | ForEach-Object { $_; Add-Content -Path "$S\vkd-console.log" -Value $_ -Encoding UTF8 }
```
Après le tir, assemblez le dossier et lancez le debrief. `$T` liste les tâches du tir (`vk ps`), ici celles du 17/10 ; un journal mis de côté au §8 se copie aussi, renommé `journal-<suffixe>.jsonl` :
```powershell
$T = "task-node-1-2", "task-node-1-7", "task-node-1-16", "task-node-1-25", "task-node-1-39"
$R = "$S\debrief\run"; New-Item -ItemType Directory -Force "$R\tasks" | Out-Null
Copy-Item "$S\drivers\maskai-1\work\journal.jsonl", "$S\drivers\maskai-1\work\driver.log", "$S\vkd-console.log", drivers\maskai-recette\cases.json $R
foreach ($t in $T) { vk task show $t --json | Out-File -Encoding utf8 "$R\tasks\$t.json" }
vk dmesg -n 5000 --json | Out-File -Encoding utf8 "$R\dmesg.json"
vk driver debrief --run $R --out "$S\debrief\out"
```
Attendu : `debrief: N proposals -> …`, une ligne par proposition (genre, cible, confiance, quoi changer), puis `nothing applied`. Lisez `debrief.md` (sections 1 à 3) ; les sections 4 et 5 sont les vôtres. Aucune ligne n'est ajoutée au ledger. `--until TASK` rejoue le debrief tel qu'il aurait été à la fin de cette tâche.
````

`technical-readme.md`, in Task 10's "Learned drivers" section: one paragraph — the debrief stage (founder decision 2026-10-17), the run directory, the three files, the proposal shape, a machine act with no ledger row and nothing applied, `--deliberate` for a learned driver's fixes. `contracts/tcb.md` §Drivers: "The debrief stage (`vk driver debrief`) reads only what the node already holds in clear — the driver's journals and log, the daemon's console, the task tables, the ledger's kinds and times — and writes three files; it is trusted with nothing: it never writes into a driver, a recipe, a case table, the run directory or the ledger, and its proposals are applied by a human."

- [ ] **Step 10:** `python -I -m unittest discover -s drivers/vkforge/tests -t .`, `cargo test --locked -p vk-ipc --test forge`, `cargo test --locked -p vk-cli --test smoke vk_driver_debrief`, `python -I scripts/docs/check_docs.py` green (the `driver-learn` job of Task 10 runs the first two with no change). **Commit** `feat(vkforge,ipc,cli): the debrief stage — vk driver debrief proposes from a run's evidence, applies nothing, writes no ledger row`

---

### Task 12: The founder's node — exit criteria §8 (controller + founder, manual)

- [ ] `drivers\vkforge\setup.ps1` on the node (uv CPython, `lxml`, `comtypes`); `python -I -m unittest discover -s drivers/vkforge/tests -t .` green including the `office_present` tests (real Word, real PowerPoint).
- [ ] Record the two catalogues (Task 3 step 5) if not yet committed; commit.
- [ ] A corpus of 10 Word contracts (the spike's `make_fixtures.py` on the node, or real anonymised firm documents in a private folder): `vk driver learn --family office-document --app word --corpus … --out dist\word-maison`; `vk driver plan show dist\word-maison` read and approved; `vk driver qualify --driver dist\word-maison\driver --corpus …` → 10/10, zero tokens (`vk dmesg` shows no `infer` between `driver.learned` and `driver.qualified`), under 15 s per document; `vk mount driver --manifest dist\word-maison\driver\manifest.json` → `driver.mounted` with `kind: com`.
- [ ] A corpus of 10 presentations: the same commands with `--app powerpoint --out dist\ppt-maison`; `git diff --stat` shows no file changed outside `drivers/vkforge/ontology/office-document.json` since the Word run; qualification ≥ 9/10.
- [ ] A fictitious document carrying personal data added to a copy of the corpus: `learn` again into `dist\purge-check`; `findstr /s` of each personal string over `dist\purge-check` finds it only under `driver\secrets.json` if it was a footer, nowhere otherwise.
- [ ] CI green on every job, `driver-learn` included; TCB updated; the chantier's SDD ledger closed; plan archived under `docs/archive/technical/plans/` at the merge (filing rule).

---

## Self-review notes (run by the author before handoff)

- Spec coverage: §3.2 components → Tasks 0, 3–9; §4.1 catalogue → 3; §4.2 ontology → 4; §4.3 binding → 4; §4.4 invariants + purge → 5; §4.5 plan → 2 (contract), 6 (producer); §4.6 generated driver → 7; §4.7 qualification → 8; §4.8 ledger → 2, 9; §4.9 contract additions → 1, 2; §4.10 CLI → 9, 10; §5.1–5.2 flows → 9; §5.3 fallback → 10 (L-P3, reduced); §5.4 PowerPoint → 4, 12; §5.5 boundaries → 5, 7, 9; §6 failure modes → 5 (corpus too small, mixed, skipped), 6 (unbound, ambiguity, bad answer), 7/8 (read-only, postconditions), 8 (corpus changed), 9 (stage failure); §7 tests → every task; §8 → 12; §11 → 10.
- Founder decision 2026-10-17, not in the spec (the debrief stage) → Task 11, from the 17/10 debrief §4 and the defects of its §2/§3: D1, D2, D5, D8, D11 and the maintenance page become proposals; D4, D6, D7, D9, D10, D12, D13 and S15–S17 signals; S14 (the judge's claims against the journal, D3) is out — the judge's text is in the register, not in clear on the node. Open founder decision: the optional `driver.debriefed` ledger row (default: none, L-P5). Fixture outcome: three of the four manual loops of 17/10 answered before their relaunch, one left.
- Open items of spec §10 settled: verb list v1 (Task 4, 32 verbs); `comtypes` pinned in `requirements.txt`, type library located through the registry (Task 3); threshold default 0.8, one value for every kind, `--threshold` on the CLI (Task 5); no COM server in CI — the fake adapter (L-P1, Task 7); `vk driver plan show` format (Task 9).
- Plan rulings: **L-P1** fake application behind the one adapter; **L-P2** `learn` is a governed one-shot process, not a protocol-1 driver; **L-P3** the deliberate fallback is a proposal from the catalogue through a Draft step, no live application driving in this chantier; **L-P4** a `kind: com` driver mounts only with a `driver.qualified` record meeting the plan's thresholds; **L-P5** the debrief is a machine act, writes no ledger row and applies nothing.
- Type consistency: `Refusal` (0) used everywhere; `catalogue_from_fixture`/`catalogue_sha256` (3) in 4, 9; `bind` output shape (4) consumed by 6, 7 (`binding.json` copied), 9; `mine_file`/`mine_corpus`/vault (5) in 6, 7, 8; `build`/`write_plan` (6) in 7, 9; `generate` (7) in 8, 9; `qualify` (8) in 9; `DriverPlan`/`DriverLearned`/`DriverQualified` (2) in 9; `DriverKind::Com`/`applications` (1) in 7's manifest and 9's gate; `Refusal` and `STAGES` (0), `ForgeConfig`/`run_stage` (9) in 11.
- Spec statements that differ from the code, left for the founder (not changed here): (a) "the pipeline is itself run as a driver of the kernel (protocol 1, Job Object)" — protocol 1 is request/response with evidence directories; the plan runs the stages as a governed one-shot process (L-P2); (b) "no network" for governed processes — a Job Object does not block sockets on Windows; the plan pins "no socket in the package" by a test instead; (c) §5.3 "the COM calls are traced" in the deliberate run — deferred (L-P3); (d) §4.6 says `kind: com` driver's `hosts: []` — today's validator refuses empty hosts, hence Task 1's per-kind rule; (e) §3.2 names `vk driver learn --deliberate <verb>` as a human act producing "a proposed projection rule reviewed by a human before it is versioned" — the plan writes the proposal file only; versioning into the ontology stays a manual edit + commit; (f) the spec has no debrief stage — it comes from the founder's decision of 2026-10-17 (`docs/technical/debriefs/2026-10-17-maskai-driver.md` §4), and §3.2's components and §4.10's CLI table should name `debrief` and `vk driver debrief` at the spec's next revision.
