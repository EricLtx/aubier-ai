# Driver Learning from the Firm's Documents (Office COM) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `vk driver learn` turns a corpus of the firm's Word documents into a qualified, mountable driver — inventory of the application's type library, deterministic OOXML mining, a typed plan, a deterministic generator, a qualification replay — and the same engine, fed presentations, produces the PowerPoint driver with no application-specific code.

**Architecture:** A Python package `drivers/vkforge/` holds five pure stages (`inventory`, `ontology`, `mine`, `plan`, `generate`) and a replay (`qualify`), each a file in and a file out, plus one thin COM runtime (`runtime/com.py`) with a fake double. The kernel gains `DriverKind::Com`, the `applications` field, the `DriverPlan` schema, two ledger kinds and the human act `driver.learn`, which spawns `vkforge` under the Job governor. The generated driver speaks protocol 1 through `drivers/vkdriver/protocol.py`, exactly like the hand-written MaskAI driver.

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
- Model tiering (founder rule): Sonnet for Tasks 0, 1, 2, 3, 5, 10; Opus for Tasks 4, 6, 7, 8, 9; Fable for the final whole-branch review. One fresh reviewer per task before the next starts.
- Commits: conventional prefix, one sentence of intent, trailers `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01UUDLpkwxsZXmLptqCeNL2R`.

## Review Focus

1. **A corpus folder that mixes `.docx` with `.doc`, `.docm`, an encrypted `.docx` and a `~$lock` file** — the miner must skip them, count them in `corpus.skipped`, never open Word, and still produce invariants from the rest; Task 5 adds the mixed-folder test.
2. **A French Office whose built-in style is named "Titre 1"** — nothing generated may depend on a localised name: the generated `driver.py` must not contain any `NameLocal` string from the catalogue, only built-in constants; Task 7 adds the string-search test over the generated bytes.
3. **Two body fonts at support 0.5 each** — no invariant reaches the threshold, the planner raises one ambiguity, and with no arch the plan is still produced (without the font action) and the ambiguity listed; Task 6 adds the tie test.
4. **The document is open in the user's Word while `qualify` runs** — the generated `open` must refuse `precondition` (read-only document) within the deadline, never hang; Task 7's runtime adapter test and Task 8's locked-copy test pin it.
5. **The corpus changes between `mine` and `qualify`** — `content_sha256` differs, qualification is refused before any copy is made; Task 8 adds the test.

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

### Task 11: The founder's node — exit criteria §8 (controller + founder, manual)

- [ ] `drivers\vkforge\setup.ps1` on the node (uv CPython, `lxml`, `comtypes`); `python -I -m unittest discover -s drivers/vkforge/tests -t .` green including the `office_present` tests (real Word, real PowerPoint).
- [ ] Record the two catalogues (Task 3 step 5) if not yet committed; commit.
- [ ] A corpus of 10 Word contracts (the spike's `make_fixtures.py` on the node, or real anonymised firm documents in a private folder): `vk driver learn --family office-document --app word --corpus … --out dist\word-maison`; `vk driver plan show dist\word-maison` read and approved; `vk driver qualify --driver dist\word-maison\driver --corpus …` → 10/10, zero tokens (`vk dmesg` shows no `infer` between `driver.learned` and `driver.qualified`), under 15 s per document; `vk mount driver --manifest dist\word-maison\driver\manifest.json` → `driver.mounted` with `kind: com`.
- [ ] A corpus of 10 presentations: the same commands with `--app powerpoint --out dist\ppt-maison`; `git diff --stat` shows no file changed outside `drivers/vkforge/ontology/office-document.json` since the Word run; qualification ≥ 9/10.
- [ ] A fictitious document carrying personal data added to a copy of the corpus: `learn` again into `dist\purge-check`; `findstr /s` of each personal string over `dist\purge-check` finds it only under `driver\secrets.json` if it was a footer, nowhere otherwise.
- [ ] CI green on every job, `driver-learn` included; TCB updated; the chantier's SDD ledger closed; plan archived under `docs/archive/technical/plans/` at the merge (filing rule).

---

## Self-review notes (run by the author before handoff)

- Spec coverage: §3.2 components → Tasks 0, 3–9; §4.1 catalogue → 3; §4.2 ontology → 4; §4.3 binding → 4; §4.4 invariants + purge → 5; §4.5 plan → 2 (contract), 6 (producer); §4.6 generated driver → 7; §4.7 qualification → 8; §4.8 ledger → 2, 9; §4.9 contract additions → 1, 2; §4.10 CLI → 9, 10; §5.1–5.2 flows → 9; §5.3 fallback → 10 (L-P3, reduced); §5.4 PowerPoint → 4, 11; §5.5 boundaries → 5, 7, 9; §6 failure modes → 5 (corpus too small, mixed, skipped), 6 (unbound, ambiguity, bad answer), 7/8 (read-only, postconditions), 8 (corpus changed), 9 (stage failure); §7 tests → every task; §8 → 11; §11 → 10.
- Open items of spec §10 settled: verb list v1 (Task 4, 32 verbs); `comtypes` pinned in `requirements.txt`, type library located through the registry (Task 3); threshold default 0.8, one value for every kind, `--threshold` on the CLI (Task 5); no COM server in CI — the fake adapter (L-P1, Task 7); `vk driver plan show` format (Task 9).
- Plan rulings: **L-P1** fake application behind the one adapter; **L-P2** `learn` is a governed one-shot process, not a protocol-1 driver; **L-P3** the deliberate fallback is a proposal from the catalogue through a Draft step, no live application driving in this chantier; **L-P4** a `kind: com` driver mounts only with a `driver.qualified` record meeting the plan's thresholds.
- Type consistency: `Refusal` (0) used everywhere; `catalogue_from_fixture`/`catalogue_sha256` (3) in 4, 9; `bind` output shape (4) consumed by 6, 7 (`binding.json` copied), 9; `mine_file`/`mine_corpus`/vault (5) in 6, 7, 8; `build`/`write_plan` (6) in 7, 9; `generate` (7) in 8, 9; `qualify` (8) in 9; `DriverPlan`/`DriverLearned`/`DriverQualified` (2) in 9; `DriverKind::Com`/`applications` (1) in 7's manifest and 9's gate.
- Spec statements that differ from the code, left for the founder (not changed here): (a) "the pipeline is itself run as a driver of the kernel (protocol 1, Job Object)" — protocol 1 is request/response with evidence directories; the plan runs the stages as a governed one-shot process (L-P2); (b) "no network" for governed processes — a Job Object does not block sockets on Windows; the plan pins "no socket in the package" by a test instead; (c) §5.3 "the COM calls are traced" in the deliberate run — deferred (L-P3); (d) §4.6 says `kind: com` driver's `hosts: []` — today's validator refuses empty hosts, hence Task 1's per-kind rule; (e) §3.2 names `vk driver learn --deliberate <verb>` as a human act producing "a proposed projection rule reviewed by a human before it is versioned" — the plan writes the proposal file only; versioning into the ontology stays a manual edit + commit.
