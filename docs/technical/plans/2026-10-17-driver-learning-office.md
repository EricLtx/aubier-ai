# Driver Learning from the Firm's Documents (Office COM) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `vk driver learn` turns a corpus of the firm's Word documents into a qualified, mountable driver — inventory of the application's type library, deterministic OOXML mining, a typed plan, a deterministic generator, a qualification replay — and the same engine, fed presentations, produces the PowerPoint driver with no application-specific code.

**Architecture:** A Python package `drivers/vkforge/` holds five pure stages (`inventory`, `bind` in `ontology.py`, `mine`, `plan`, `generate`) and a replay (`qualify`), each a file in and a file out, plus one thin COM adapter (`runtime/com.py`, vendored into every generated driver) with a repository-only fake twin (`runtime/fake.py`); a seventh stage, `debrief`, reads a governed run's evidence and writes proposals for the operator (founder decision 2026-10-17, Task 11). The kernel gains `DriverKind::Com`, the `applications` field, the `DriverPlan` schema, two ledger kinds (`driver.learned`, `driver.qualified`), a `learned` table, the qualification gate at mount, and the human act `driver.learn`, which spawns `vkforge` under the Job governor. The generated driver speaks protocol 1 through its own vendored copy of `drivers/vkdriver/protocol.py`, exactly like the hand-written MaskAI driver.

**Tech Stack:** Python 3.13 (stdlib, `lxml==6.1.3`, `comtypes==1.4.17` on Windows only), Rust (MSVC) for contracts/kernel/IPC/CLI, Office Open XML read with `zipfile` + `lxml`, GitHub Actions (`driver-browser` on Ubuntu for the Python suite, new `driver-learn` on Windows for the end-to-end).

**Spec:** `docs/technical/specs/2026-10-17-driver-learning-office-design.md` (decisions L1–L8, contracts §4, flows §5, failure modes §6, tests §7, exit §8, TCB §11). The driver contract of `docs/technical/specs/2026-10-14-device-driver-contract-and-recette-pilot-design.md` (rulings R5, R6, R18, R25, R28) stays binding.

**Revision 2026-10-18.** Revised on the pre-flight scan (`.superpowers/sdd/2026-10-17-driver-learning-office/preflight-scan.md`, 69 rows) and the controller's rulings L-P7 … L-P34 (SDD ledger `progress.md`). Fixed again on the rescan of plan `887b877` (`.superpowers/sdd/2026-10-17-driver-learning-office/preflight-rescan.md`, 175 rows, 18 not consistent), by rulings L-P22 (amended) and L-P25 … L-P34. Every interface below is written identically in the task that produces it and in every task that consumes it; line ranges are those of master `bcffc16`.

## Global Constraints

**Scope and contracts**

- **Packaging is merged** (master `bcffc16`): `ExecutionContext`, `DriverSource`, `needs_input_desktop`, the `DriverLink` seam and its `SpawnSpec` (`crates/vk-driver/src/link.rs`) and `crates/vk-capital` are in master. This plan does not touch those fields, `link.rs` or `crates/vk-capital`; it changes `crates/vk-ipc/src/capital.rs` once (ruling L-P9, Task 9). Its contract additions are exactly: `DriverKind::Com`; `DriverManifest.applications: Vec<String>` (default empty, `skip_serializing_if = "Vec::is_empty"`, so every existing `driver_id` is unchanged); `DriverProgram.runtime = "python-com"` (a string value, no enum); the `DriverPlan` schema in `crates/vk-contracts/src/plan.rs`; the payloads `DriverLearned` and `DriverQualified` in `crates/vk-contracts/src/driver.rs`; the kinds `driver.learned` (human act, presence proof like `driver.mount`) and `driver.qualified` (kernel-written); the store table `learned`. No other ledger kind (ruling L-P5).
- Validation per kind: `kind: com` requires non-empty `applications` (each an upper-case executable name ending in `.EXE`, no path) and allows empty `hosts`; `kind: browser` keeps today's rules (non-empty `hosts`) and `applications` must be empty.
- `drivers/vkforge/` is Python 3.13 with the stdlib, `lxml==6.1.3` and `comtypes==1.4.17; sys_platform == "win32"` only (`drivers/vkforge/requirements.txt`); `comtypes` is imported lazily and only on Windows. No `python-docx`, no model call, no socket inside the package.

**How vkforge runs**

- **One invocation everywhere** (the kernel's spawn, CI, tests that spawn it, runbooks): `python -I drivers/vkforge/__main__.py <stage> …` — never `python -I -m vkforge`: `-I` implies `-P`, so neither the working directory nor the script's directory is on `sys.path` and `-m` cannot find the package. `__main__.py` puts `drivers/` on `sys.path` itself, as `drivers/maskai-recette/driver.py` does for `vkdriver`; `drivers/vkforge/tests/__init__.py` does the same for the suite, whatever discovery top-level is used. `vkforge <stage> …` in this plan is shorthand for that line.
- **The stage table** `STAGES` is built in `__main__.py` by explicit imports (`{"inventory": inventory, "bind": ontology, "mine": mine, "plan": plan, "generate": generate, "qualify": qualify}`, plus `"debrief": debrief` from Task 11); a stage module defines `add_arguments(parser) -> None` and `run(ns) -> int` and does nothing at import — a stage that registered itself through `from .__main__ import STAGES` would fill a second copy of `__main__` (the running script is `__main__`, not `vkforge.__main__`) and `main` would see an empty table.
- **L-P14 (stubs first):** Task 0 creates every stage module (`inventory.py`, `ontology.py`, `mine.py`, `plan.py`, `generate.py`, `qualify.py`) as a stub — `add_arguments` adds nothing, `run` raises `Refusal("not implemented", "wait for Task N")`; Tasks 3–8 **modify** them, never create them.
- **L-P15 (refusal voice):** a refusal is one line, `vkforge <stage>: <what>; remedy: <remedy>`, everywhere: `main` writes exactly that to stderr and exits 2, and the kernel's refusal of a failed stage is that last stderr line, verbatim (R28). The generated driver's own refusal reasons are the protocol's five words.
- **L-P2 (how `learn` runs):** the spec's "run as a driver of the kernel" is a governed one-shot process: `vk-ipc` spawns `<python> -I <root>/vkforge/__main__.py <stage> …` under `vk_harness::confine::for_this_platform(1 << 30, None)` with a deadline, `current_dir = <root>`, `<root>` being the `drivers/` directory the daemon was started with (`vkd --vkforge-root`), never through protocol 1 (a pipeline is not request/response). The Job governor does not block the network; the package opens no socket, which a test pins (no `socket`/`urllib`/`http`/`requests` import). The working directory, the deadline and the kill of the whole tree are pinned by Task 9's `vk_forge_a_stage_past_its_deadline_is_killed_in_its_root` (L-P30).
- **L-P12 (interpreters):** `ForgeConfig.python` is `VK_FORGE_PYTHON` when set, else the vkforge virtualenv `%LOCALAPPDATA%\vk\venvs\vkforge\Scripts\python.exe` that `drivers/vkforge/setup.ps1` makes (Task 9). A generated driver's development command is `[".venv/Scripts/python.exe", "-I", "driver.py"]`, `.venv` being a junction to that same virtualenv, made by the generator (`--venv`, default the virtualenv on Windows) and outside the fingerprint as every `.venv` is (`hash_dir`); the packed command is the packaging chantier's (`python/python.exe -E -s -B …`).
- **L-P17 (lock):** `driver.learn`, `driver.qualify`, `driver.plan_show` (Task 9) and `driver.debrief` (Task 11) are dispatched **before** the general kernel lock, as `driver.pack` is (`crates/vk-ipc/src/server.rs` 882–895): `dispatch` already runs on tokio's blocking pool (`spawn_blocking`, `server.rs` 575), so the stages run there and the kernel lock is taken only in short phases. Task 9 moves the test `Node` helper of `crates/vk-ipc/tests/capital.rs` (226–413) into `crates/vk-ipc/tests/common/mod.rs`, shared by `capital.rs` and `forge.rs`.

**Artefacts, hashes, interfaces (ruling L-P13)**

- Every artefact a stage writes is `vkforge.canon.dumps(obj)` (sorted keys, `indent=1`, `ensure_ascii=False`, UTF-8, one trailing `\n`); every `*_sha256` of an artefact is `"sha256:" + SHA-256 of the file's bytes`, so `canon.sha256(obj)` (the hash of `canon.dumps(obj)`) equals the hash of the file the stage wrote. The hand-maintained ontology file is stored in that canonical form too (Task 4 pins it).
- `plan_sha256` = SHA-256 of `plan.json`'s bytes, the same in Python (`plan.plan_sha256(path)`) and in Rust (`vk_contracts::plan::plan_sha256(&bytes)` = `hash_bytes`), with no canonicalisation. `corpus_schema_sha256` = `canon.sha256(invariants["invariants"])` — the invariants list alone, without the corpus block — stored as `invariants.json`'s `corpus.schema_sha256`. `invariants_sha256`, `catalogue_sha256`, `ontology_sha256` are file hashes as above.
- A `DriverPlan` action carries `reversible` (true only when every step's ontology verb is reversible) and an optional `note` (the `partial` bindings its steps use); an invariant the plan cannot turn into steps is listed in the sidecar `plan-excluded.json` (`[{"invariant", "why"}]`), never in `plan.json`.
- A miner ambiguity is `{"invariant", "kind", "candidates": [{"value", "support"}]}` (candidates by support, descending, then by `canon.dumps(value)`); a plan ambiguity is `{"invariant", "resolved_by": "arch:<arch_id>" | "human" | "purged", "value"?}`, `value` present exactly when it was resolved — always one of the miner's candidates.
- The miner emits `where` on observations and on the invariants of kind `paragraph_style` (`first` → `title_style`, `all` → `body_style`) and `font_run` (`body` → `body_font`, `title` → `title_font`).
- Selectors (`ParagraphSelector`): `first | body | all | index:<n> | title`.
- `qualify(driver_dir, corpus, plan, invariants, work, fake, deadline_s)`; `qualification.json` is `<work>/qualification.json`; the mount gate compares that file's SHA-256 with the `qualification_sha256` the kernel stored on the driver's `learned` row at qualify time.
- The generator resolves `$secret.<id>` through `--invariants FILE` (the invariant's value, a redacted token; or the plan ambiguity's `value` for a resolved one) and `--vault FILE` (`{token: clear}`), and refuses a secret it cannot resolve.

**Plan rulings**

- **L-P1 (fake application):** the generated driver's COM calls go through one adapter, `drivers/vkforge/runtime/com.py` (`Application(binding)`: `probe`, `open`, `call`, `close`, `quit`). `FakeApplication(binding, config)` in `drivers/vkforge/runtime/fake.py` implements the same verb table on the OOXML of the file directly (with `ooxml.write_parts`, the miner's writer twin), so `qualify` runs against the fake in CI and against the real Word on the node, through the same driver bytes.
- **L-P19 (how the fake is reached):** the adapter is vendored into every generated driver (`vendor/vkforge_runtime/com.py`); the fake stays in the repository and is injected only by `vkforge qualify --fake`, which writes `<work>/fake.json` (`{"vkforge_root": "<drivers/>"}`; Task 7's dialog test adds `"dialog_on"`, L-P33) into the work directory it runs the driver in; the generated driver loads the fake when, and only when, that file is in its work directory (`VK_DRIVER_WORK_DIR`). Never an environment variable: `VKFORGE_FAKE` does not exist. A mount's work directory is the kernel's private `<state_dir>/drivers/<name>/work`, which nothing writes a `fake.json` into.
- **L-P3 (fallback, spec §5.3):** `vk driver learn --deliberate VERB` produces a *proposed projection rule* from the verb's ontology entry and the catalogue slice through one task with one `Draft` step on the given arch (no harness, no application driving in this chantier), written to `<out>/proposals/<verb>.json` for human review; COM-call tracing of a live deliberate run is deferred to a later chantier and said so in the TCB.
- **L-P4 (the gate):** a `kind: com` driver mounts only when its `driver_id` has a `driver.qualified` record on the `learned` table meeting the plan's thresholds (`passes ≥ min_passes`, `passes / (passes + failures) ≥ min_rate`) and its `qualification.json` still hashes to the stored `qualification_sha256`.
- **L-P5 (the debrief, founder decision 2026-10-17; no ledger row, founder 2026-10-18):** `vkforge debrief` is a stage like the others (deterministic, stdlib only, no model, no socket) whose requirements are `docs/technical/debriefs/2026-10-17-maskai-driver.md` §4; `driver.debrief` is a machine act (no presence proof) run under the same governor as `learn`'s stages; it adds **no ledger kind** and it **applies nothing**: its proposals are the operator's to apply, or, for a learned driver, the deliberate run's (L-P3).
- **L-P6 (the generated driver vendors `vkdriver`):** the generator writes into the generated driver's directory `vendor/vkdriver/` — a byte copy of `drivers/vkdriver/*.py` (the `vkdriver` vkforge itself imported), without `test_*.py` or `__pycache__` — and the generated `driver.py` resolves `vkdriver` vendor-first (`DRIVER_DIR/vendor/vkdriver`), then the repository layout (`<repo>/drivers/vkdriver`, the driver at `drivers/office-com/<name>/`, spec §3.2); never one directory up. `vendor/` is inside the driver directory, so it is in `hash_dir`'s fingerprint (`files_sha256`, hence `driver_id`). Whatever spawns a generated driver (Task 7's test, Task 8's qualification, the kernel's mount, CI, the node) runs it from its own directory, with no repository around it. (The same vendor-first pattern is in `drivers/maskai-recette/driver.py` on branch `maskai-driver-v2`, not yet on master; the generated driver's code is self-contained and does not wait for it.)
- **L-P7 (identity):** Task 1's identity test is the existing `the_pilot_manifest_keeps_its_id_with_the_new_fields_absent` over the frozen fixture `contracts/examples/driver_manifest/valid/identity-frozen-2026-10-17.json` and the existing `PILOT_ID`; no new constant, never the live `maskai.json`.
- **L-P8 (schema per kind):** the per-kind `applications` rules are in the JSON Schema too, through schemars `if`/`then` (as for the service context): `com` ⇒ `applications` required with `minItems: 1`; `browser` ⇒ `applications` `maxItems: 0`. An invalid example under `contracts/examples/*/invalid/` is one the schema refuses; anything only `validate()` can refuse is pinned by a Rust test instead.
- **L-P9 (no `pack.json`):** a generated driver carries `vendor/vkdriver/` and **no** `pack.json`. `vk driver pack` of a directory without `pack.json` skips its repository `vkdriver/` copy when `vendor/vkdriver` already exists, and asks for no `Cargo.toml` above the driver in that case (Task 9: three lines in `stage_files`, `crates/vk-ipc/src/capital.rs` 373–413).
- **L-P10 (the model call):** the ambiguity call is `infer` with `Capability::Plan` (`crates/vk-contracts/src/arch.rs`: `Generate, Embed, Predict, Perceive, Plan, Judge`; there is no `Draft` capability), through one task with one `Plan` step.
- **L-P11 (mount record):** `driver.mounted` carries no kind (`DriverMounted` is unchanged); a test reads the kind from the row's manifest (`driver.show`).
- **L-P16 (no override):** there is no `--unqualified` flag (an unlisted CLI surface); `MountParams` is unchanged. A `kind: com` driver mounts only when qualified (L-P4); the fixture dry run qualifies first.
- **L-P18 (CI naming and guard):** the new smoke tests are named `vk_forge_*`, outside the `driver-install` job's filter `smoke vk_driver_` (`.github/workflows/ci.yml` 294); in the three-OS `test` job, every Rust test that spawns vkforge first runs the vkforge interpreter on `-I -c "import lxml, comtypes"` and, when that fails, says so on stderr and returns.
- **L-P20 (the mock arch):** no `VK_MOCK_ANSWER` seam exists or is added. The ambiguity test uses the mock arch's deterministic echo (`MockAdapter::complete`, `crates/vk-kernel/src/arch.rs` 249–258: the role, then the first 200 characters of the prompt): the register's goal is the ambiguity JSON, so the echo carries the first candidate's value, which the answer rule (Task 9) reads as the choice; the test asserts `resolved_by: arch:<id>` and that recorded value.
- **L-P21 (fixtures in order):** Task 3 ends with a step run by the controller on the founder's node — recording `catalogue-word.json` and `catalogue-powerpoint.json` — before Task 4 starts; Task 4's tests write and pin `drivers/vkforge/tests/fixtures/binding-word.json`, Task 6's write and pin `invariants-word-maison.json` and `plan-word-maison.json`, all three committed, for Task 7 and Task 8.
- **L-P22 (Office version, spec §6; amended on the rescan):** the type-library fingerprint lives **outside** the contracts. Task 3's catalogue carries `typelib_sha256` (the SHA-256 of the type library file's bytes, its path read from the registry); Task 4's `bind` copies it, with the catalogue's `typelib` `{guid, version}`, into `binding-<app>.json`; Task 7 writes the binding into the generated driver as `binding.json`, inside `hash_dir`, hence in `driver_id`. The generated driver's `probe` (`app().probe()`, before an action opens its document) recomputes it from the live registry and refuses `precondition` "office version changed; remedy: vk driver qualify" when it differs. `DriverManifest` is unchanged; the kernel never loads COM.
- **L-P23 (`--deliberate`'s flags; provisional):** spec §4.10's `vk driver learn --deliberate VERB --app A` is a usage example, not an interface table: the plan's required `--out` and `--arch` stand (Task 10). Provisional — a CLI surface: the founder confirms it in the Task 2 checkpoint batch, and the spec's §5 usage line is aligned at the merge.
- **L-P25 (a `Ctx` for the two stages that write the ledger):** `forge::learn` and `forge::qualify` take `ctx: &Ctx`; their pre-lock arms build it exactly as the `driver.pack` arm does (`crates/vk-ipc/src/server.rs` 882–895: one short lock phase, `ctx_for(&k, presence.take(), now)`), and `ctx_for` (753), private today, becomes `pub(crate)` (Task 9).
- **L-P26 (`ForgeConfig::at` is infallible):** `ForgeConfig::at(root: PathBuf) -> ForgeConfig`; a root without `vkforge/__main__.py` is refused by `run_stage` before its first spawn (`vkforge root not found; remedy: start vkd with --vkforge-root <drivers/>`), so `vkd` does `a.vkforge_root.map(ForgeConfig::at)` into an `Option<ForgeConfig>` (Task 9).
- **L-P27 (the kind on screen):** `render::driver` (`crates/vk-cli/src/render.rs` 368–394) gains a `kind` row, pinned by one test (Task 9); the JSON output already carries the manifest. Rendering, not CLI surface.
- **L-P28 (Task 3's count):** until Step 6 records the catalogues, five tests of `test_inventory.py` fail (word, powerpoint, hash-stable, restrict, cli); the live one is skipped off the node.
- **L-P29 (the proposal's answer):** the proposal file records the arch's answer verbatim — the Draft step's decision, `draft: <text>` (`crates/vk-kernel/src/tasks.rs` 917) — so Task 10's test asserts the `draft: ` prefix.
- **L-P30 (the governor pinned):** Task 9's `vk_forge_a_stage_past_its_deadline_is_killed_in_its_root` gives the daemon a stand-in root, as `--vkforge-root` gives one (`ForgeConfig::at`), whose `vkforge/__main__.py` writes `os.getcwd()` (and the ids of itself and of a child it starts) to a file, then sleeps 60 s: the kernel refuses within the deadline plus 5 s, the file names the root, and no python process of the stage survives (Job governor). `ForgeConfig.stage_deadline` (`STAGE_DEADLINE` from `at`) lets the test set 5 s.
- **L-P31 (no application name in the adapter):** `runtime/com.py` names no application. The ProgID, the collection (`Documents`/`Presentations`) and the arguments of `Open` come from the ontology's `applications` block through the binding's `application` — with the two other per-application values the adapter needs, the arguments of the document's `Close` and the `DisplayAlerts` value that silences the application (Tasks 4, 7); `inventory.APPS` keeps no ProgID. The fake stays Word `.docx` only, declared in the TCB (Task 10); the PowerPoint driver is qualified on the real PowerPoint at Task 12, which is how spec §8.2 is proven.
- **L-P32 (supervision, spec §2 L8 and §5.2):** "supervised, every act approved" holds for the **mounted** learned driver: every `vk_driver_act` is a job step under the harness today. `qualify` spawns the driver directly, inside its own stage's Job (the governor), as a stage — a deviation declared in the TCB and in the self-review notes (Task 10).
- **L-P33 (dialogs, spec §6):** `com.guarded` runs each COM call on its own thread, joined for `CALL_DEADLINE_S` (30 s); past it, the top-level windows of the application's processes are enumerated (`ctypes` `EnumWindows` + `GetWindowThreadProcessId`, class `#32770`): a dialog box found is closed (`WM_CLOSE`), never answered, and the action refuses `dialog`; none, it refuses `timeout`. The fake exposes the `__dialog__` hook (`fake.json` key `"dialog_on": "<verb>"`), so Task 7 tests `dialog` without Office (`test_a_surviving_dialog_is_a_dialog_refusal`).
- **L-P34 (TCB, spec §8.4):** Task 10 reproduces spec §5.5's data-boundary table in `contracts/tcb.md`.

**Always**

- Zero model tokens on the nominal path: an arch is called only for the miner's ambiguities, only when `--arch NAME` is given, by the kernel (Task 9) before the `plan` stage runs, through `infer` (`Capability::Plan`, L-P10) on a register that holds the ambiguity JSON and nothing else; with no arch the ambiguity is purged and listed. The `plan` stage itself calls nothing.
- The miner reads only `word/styles.xml`, `word/settings.xml`, `word/header*.xml`, `word/footer*.xml`, `word/numbering.xml`, `docProps/app.xml`, and in `word/document.xml` only `w:pPr`/`w:rPr`/`w:sectPr` attributes (never `w:t`); for `.pptx` the `ppt/slideMasters/*.xml`, `ppt/slideLayouts/*.xml`, `ppt/presentation.xml`, `docProps/app.xml`, and in `ppt/slides/*.xml` only `a:pPr`/`a:rPr` attributes (never `a:t`). Every free string (header and footer text, a custom style's id) is replaced by `<redacted:sha256:…>` (`mine.redact`) in `invariants.json`; its clear value goes to `<out>/.vault.json` (mode `0o600` where the OS honours it, deleted after generation).
- Generated files are byte-reproducible: written by `canon.dumps` or as fixed text with `\n` line endings, no timestamps; the manifest's `files_sha256` is `hash_dir` of the output directory (R5).
- **Placeholders:** no test body in this plan is `...`/`…` or a comment; every test a step names has its code in that step.
- Model tiering (founder rule): Sonnet for Tasks 0, 1, 2, 3, 5, 10; Opus for Tasks 4, 6, 7, 8, 9, 11; Task 12 is the controller's and the founder's, on the node; Fable for the final whole-branch review. One implementer at a time (node memory), one fresh reviewer per task before the next starts; founder checkpoints after Task 2 and at Task 12.
- Commits: conventional prefix, one sentence of intent, trailers (ruling L-P24: the same whatever model implements the task) `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01UUDLpkwxsZXmLptqCeNL2R`.

## Review Focus

1. **A corpus folder that mixes `.docx` with `.doc`, `.docm`, an encrypted `.docx` and a `~$lock` file** — the miner must skip them, count them in `corpus.skipped`, never open Word, and still produce invariants from the rest; Task 5's `test_a_mixed_folder_skips_what_is_not_ooxml_and_never_opens_office`.
2. **A French Office whose built-in style is named "Titre 1"** — in a French document the built-in Heading 1 has `w:styleId="Titre1"` and `w:name w:val="heading 1"`: the miner must anchor on the built-in name, and nothing generated may carry a localised name; Task 5's `test_a_french_style_id_maps_to_the_builtin_name` and Task 7's `test_no_localised_name_and_no_corpus_text_in_the_driver` (a byte search over every generated file, from a French corpus).
3. **Two body fonts at support 0.5 each** — no invariant reaches the threshold, the planner raises one ambiguity, and with no arch the plan is still produced (without the font action) and the ambiguity listed; Task 6's `test_a_tie_is_an_ambiguity_purged_without_a_resolver`.
4. **The document is open in the user's Word while `qualify` runs** — the adapter (and its fake) must refuse `precondition` (read-only document) within the deadline, never hang; Task 7's `test_a_read_only_document_is_a_precondition_refusal` and Task 8's `test_a_read_only_copy_is_a_failed_pass_not_a_hang`.
5. **The corpus changes between `mine` and `qualify`** — `content_sha256` differs, qualification is refused before any copy is made; Task 8's `test_a_changed_corpus_is_refused_before_any_copy`.
6. **An empty answer whose stream never reached `call_llm` (N9-2 of 17/10)** — the product failed, not the selector: the debrief lists it in `empty_without_llm` and never turns it into a selector proposal, while essai 1's N1-1 and N1-3 (call_llm 2444 and 2391 ms) make one; Task 11's `test_the_signals_are_the_debrief_s_verified_facts` and `test_six_proposals_each_carrying_its_evidence_lines`.

---

### Task 0: `drivers/vkforge/` skeleton, stage stubs, test runner, CI wiring (Sonnet)

**Model:** Sonnet (Global Constraints, tiering).

**Files:**
- Create: `drivers/vkforge/__init__.py` (empty), `drivers/vkforge/__main__.py`, `drivers/vkforge/errors.py`, `drivers/vkforge/canon.py`, the six stage stubs `drivers/vkforge/inventory.py`, `drivers/vkforge/ontology.py`, `drivers/vkforge/mine.py`, `drivers/vkforge/plan.py`, `drivers/vkforge/generate.py`, `drivers/vkforge/qualify.py` (ruling L-P14: Tasks 3–8 modify them), `drivers/vkforge/requirements.txt`, `drivers/vkforge/tests/__init__.py`, `drivers/vkforge/tests/test_package.py`
- Modify: `.github/workflows/ci.yml` (job `driver-browser`, lines 196–219: one `pip install` line and two steps)

**Interfaces:**
- Produces:
  - `vkforge.errors.Refusal(what: str, remedy: str)` — an `Exception` whose `str(e)` is `f"{what}; remedy: {remedy}"`, with attributes `what` and `remedy`.
  - `vkforge.canon`: `dumps(obj) -> bytes` (`json.dumps(obj, sort_keys=True, indent=1, ensure_ascii=False) + "\n"`, UTF-8), `sha256(obj) -> str` (`"sha256:" + hex`), `sha256_file(path) -> str`, `write(obj, path) -> None` (`Path.write_bytes`, so no newline translation).
  - The CLI entry `python -I drivers/vkforge/__main__.py <stage> …`, the only invocation (never `-m`); `vkforge.__main__.STAGES: dict[str, ModuleType]`, built by explicit imports, keys `inventory`, `bind`, `mine`, `plan`, `generate`, `qualify`; `vkforge.__main__.main(argv: list[str] | None = None) -> int`, which returns the stage's `run(ns)` or, on a `Refusal`, writes `vkforge <stage>: <what>; remedy: <remedy>\n` to stderr and returns 2 (L-P15).
  - The stage contract: a module with `add_arguments(parser: argparse.ArgumentParser) -> None` and `run(ns: argparse.Namespace) -> int`, registering nothing at import.
  - `drivers/vkforge/tests/__init__.py`, which puts `drivers/` on `sys.path` for every test module.

- [x] **Step 1: Failing tests** (`drivers/vkforge/tests/test_package.py`)

```python
import contextlib
import hashlib
import io
import pathlib
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[2]  # drivers/
ENTRY = ROOT / "vkforge" / "__main__.py"             # the one invocation: python -I <ENTRY> <stage> …

from vkforge import __main__ as entry  # noqa: E402  (tests/__init__.py put drivers/ on sys.path)
from vkforge import canon  # noqa: E402
from vkforge.errors import Refusal  # noqa: E402

STAGE_NAMES = ["bind", "generate", "inventory", "mine", "plan", "qualify"]


class PackageTest(unittest.TestCase):
    def test_the_cli_lists_its_stages_and_refuses_an_unknown_one_from_any_directory(self):
        # From a foreign working directory, under -I: what the kernel spawns (Task 9).
        out = subprocess.run([sys.executable, "-I", str(ENTRY), "--help"], cwd=tempfile.gettempdir(),
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        for stage in STAGE_NAMES:
            self.assertIn(stage, out.stdout)
        bad = subprocess.run([sys.executable, "-I", str(ENTRY), "nope"], cwd=tempfile.gettempdir(),
                             capture_output=True, text=True)
        self.assertNotEqual(bad.returncode, 0)

    def test_the_stage_table_is_built_in_main_and_no_stage_registers_itself(self):
        self.assertEqual(sorted(entry.STAGES), STAGE_NAMES)
        for name, module in entry.STAGES.items():
            self.assertTrue(callable(module.add_arguments) and callable(module.run), name)
            self.assertNotIn("STAGES", vars(module), f"{name}: a stage registers nothing at import")

    def test_a_refusal_is_one_line_in_the_kernel_voice(self):
        e = Refusal("corpus has 3 files", "more documents of one kind")
        self.assertEqual(str(e), "corpus has 3 files; remedy: more documents of one kind")
        self.assertEqual((e.what, e.remedy), ("corpus has 3 files", "more documents of one kind"))

        def refuse(ns):
            raise Refusal("corpus has 3 files", "more documents of one kind")

        stage = types.SimpleNamespace(add_arguments=lambda parser: None, run=refuse)
        err = io.StringIO()
        with mock.patch.dict(entry.STAGES, {"fake": stage}), contextlib.redirect_stderr(err):
            code = entry.main(["fake"])
        self.assertEqual(code, 2)
        self.assertEqual(err.getvalue(), "vkforge fake: corpus has 3 files; remedy: more documents of one kind\n")

    def test_canonical_bytes_are_sorted_indented_and_hashed_as_written(self):
        obj = {"b": [1, {"é": "ü"}], "a": None}
        data = canon.dumps(obj)
        self.assertEqual(data, '{\n "a": null,\n "b": [\n  1,\n  {\n   "é": "ü"\n  }\n ]\n}\n'.encode("utf-8"))
        path = pathlib.Path(tempfile.mkdtemp()) / "x.json"
        canon.write(obj, path)
        self.assertEqual(path.read_bytes(), data)
        self.assertEqual(canon.sha256(obj), canon.sha256_file(path))
        self.assertEqual(canon.sha256(obj), "sha256:" + hashlib.sha256(data).hexdigest())

    def test_the_package_opens_no_socket(self):
        for p in (ROOT / "vkforge").rglob("*.py"):
            if "tests" in p.parts:
                continue
            text = p.read_text(encoding="utf-8")
            for banned in ("import socket", "import urllib", "import http", "import requests",
                           "from socket", "from urllib", "from http"):
                self.assertNotIn(banned, text, f"{p}: {banned}")


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run** `python -I -m unittest discover -s drivers/vkforge/tests -t .` → FAIL (`ModuleNotFoundError: vkforge`).

- [x] **Step 3: Implement**

`tests/__init__.py` (under `-I`, and with CI's `discover -s drivers -p "test_*.py" -t .` importing the tests as `drivers.vkforge.tests.*`, nothing else puts `drivers/` on `sys.path`):

```python
"""The vkforge suite: `drivers/` on sys.path once, for every test module, whatever the discovery top-level."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
```

`errors.py`:

```python
class Refusal(Exception):
    """One line, the kernel's voice: what was wrong, then the remedy."""

    def __init__(self, what: str, remedy: str):
        super().__init__(f"{what}; remedy: {remedy}")
        self.what, self.remedy = what, remedy
```

`canon.py`:

```python
"""The one way vkforge writes and hashes an artefact (ruling L-P13): what is hashed is what is on disk."""
import hashlib
import json
from pathlib import Path


def dumps(obj) -> bytes:
    return (json.dumps(obj, sort_keys=True, indent=1, ensure_ascii=False) + "\n").encode("utf-8")


def sha256(obj) -> str:
    return "sha256:" + hashlib.sha256(dumps(obj)).hexdigest()


def sha256_file(path) -> str:
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(obj, path) -> None:
    Path(path).write_bytes(dumps(obj))
```

`__main__.py`:

```python
"""python -I drivers/vkforge/__main__.py <stage> …  — the learning pipeline, one stage per call.

The one way vkforge runs (the kernel's spawn, CI, the runbooks). Not `python -I -m vkforge`: -I
keeps the working directory and the script's directory off sys.path, so the package would not be
found. This file puts drivers/ there itself, as drivers/maskai-recette/driver.py does for vkdriver.
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent              # drivers/vkforge
sys.path.insert(0, str(HERE.parent))                 # drivers/: vkforge and vkdriver from the repository

from vkforge import generate, inventory, mine, ontology, plan, qualify  # noqa: E402
from vkforge.errors import Refusal  # noqa: E402

# The stage table, built here and nowhere else. A stage module defines add_arguments(parser) and
# run(ns) -> int and does nothing at import: run as a script this file is `__main__`, so a stage
# importing `vkforge.__main__` to register itself would fill a second copy of this table.
STAGES = {"inventory": inventory, "bind": ontology, "mine": mine, "plan": plan, "generate": generate,
          "qualify": qualify}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="vkforge")
    sub = p.add_subparsers(dest="stage", required=True)
    for name, module in STAGES.items():
        module.add_arguments(sub.add_parser(name))
    ns = p.parse_args(argv)
    try:
        return STAGES[ns.stage].run(ns)
    except Refusal as e:
        sys.stderr.write(f"vkforge {ns.stage}: {e}\n")   # L-P15: the one refusal line the kernel relays
        return 2


if __name__ == "__main__":
    sys.exit(main())
```

The six stubs (ruling L-P14), one per stage, the task number in the docstring and the remedy — `inventory.py` (Task 3), `ontology.py` (Task 4, stage `bind`), `mine.py` (Task 5), `plan.py` (Task 6), `generate.py` (Task 7), `qualify.py` (Task 8). `inventory.py`, the pattern of all six:

```python
"""vkforge inventory — the catalogue of an application's type library (filled by Task 3)."""
import argparse

from .errors import Refusal


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Nothing yet: Task 3 adds the stage's flags."""


def run(ns: argparse.Namespace) -> int:
    raise Refusal("not implemented", "wait for Task 3")
```

`requirements.txt`, exactly:

```text
lxml==6.1.3
comtypes==1.4.17; sys_platform == "win32"
```

CI, job `driver-browser` (`.github/workflows/ci.yml` 196–219): in the step "Install the driver's requirements and Chromium", after `pip install -r drivers/maskai-recette/requirements.txt`, the line `pip install -r drivers/vkforge/requirements.txt`; after the step "The MaskAI driver against its fixture page", two steps:

```yaml
      - name: The learning pipeline (vkforge), recorded catalogues and the fake application
        run: python -I -m unittest discover -s drivers/vkforge/tests -t .
      - name: vkforge runs as the kernel spawns it (the script path under -I, never -m)
        run: python -I drivers/vkforge/__main__.py --help
```

(The existing step "The driver contract tests", `discover -s drivers -p "test_*.py" -t .`, also collects the vkforge suite: it runs twice on Ubuntu, which is harmless.)

- [x] **Step 4: Run** `python -I -m unittest discover -s drivers/vkforge/tests -t .` → 5 tests green; `python -I -m unittest discover -s drivers -p "test_*.py" -t .` green; `python -I scripts/check-workflows.py` green.

- [x] **Step 5: Commit** `feat(vkforge): the learning pipeline's package, stage stubs, canonical writer and refusal voice` — done, `4227287`.

---

### Task 1: `DriverKind::Com` and `applications` (identity preserved) (Sonnet)

**Model:** Sonnet.

**Files:**
- Modify: `crates/vk-contracts/src/driver.rs` — `DriverKind` (14–18); `DriverManifest` (102–135): the field after `hosts` (120), and an `allOf` beside the existing `if`/`then` of its `#[schemars(extend(…))]` (106–114); `validate()` (227–328): the per-kind `match` replaces the `hosts.is_empty()` check (256–258); the tests module (443–745): one assertion added to `the_pilot_manifest_keeps_its_id_with_the_new_fields_absent` (459–466), a `word_com()` helper and three tests.
- Modify: `crates/vk-ipc/src/drivers.rs` — `COM_MAX_MEMORY` beside `BROWSER_MAX_MEMORY` (41) and the `Com` arm of `memory_cap_of` (45–52), the only exhaustive `match` on `DriverKind` in the workspace.
- Modify: `crates/vk-driver/src/lib.rs` — the doc comment of `DriverHost::spawn` (224–234) and `SpawnSpec.env` (290–310): one tuple after `VK_DRIVER_HOSTS` (309). `crates/vk-driver/src/link.rs` is not touched (M04).
- Modify: `crates/vk-driver/tests/fake_driver.rs` — `ENV_DRIVER` (54–64) and `the_environment_names_the_dirs_the_mode_and_the_hosts` (754–815).
- Modify: `crates/vk-kernel/tests/drivers.rs` (`maskai_like`, 95–140) and `crates/vk-kernel/tests/capital.rs` (`maskai_like`, 74–109): `applications: vec![]` in the two `DriverManifest { … }` literals (every other test builds its manifest from JSON).
- Create: `contracts/examples/driver_manifest/valid/word_com.json`, `contracts/examples/driver_manifest/invalid/com_without_applications.json`, `contracts/examples/driver_manifest/invalid/browser_with_applications.json`
- Regenerate: `contracts/schemas/driver_manifest.schema.json`

**Interfaces:**
- Produces: `DriverKind::Com` (serialised `"com"`); `#[serde(default, skip_serializing_if = "Vec::is_empty")] pub applications: Vec<String>` on `DriverManifest`, after `hosts`; the refusals `applications is empty: …`, `application `X`: an executable name, no path`, `application `X`: upper-case executable name ending in .EXE, …`, `applications must be empty for a browser driver: …`; `VK_DRIVER_APPLICATIONS` (the manifest's `applications` joined with commas) in every driver's environment; `const COM_MAX_MEMORY: u64 = 1 << 30` (private to `crates/vk-ipc/src/drivers.rs`).
- Consumes: the frozen identity fixture `contracts/examples/driver_manifest/valid/identity-frozen-2026-10-17.json` and the existing `PILOT_ID` (`driver.rs` 456–457), ruling L-P7.

- [ ] **Step 1: Failing tests** (in `driver.rs`'s tests module)

The identity test stays the existing one over the frozen fixture (ruling L-P7) and gains one assertion — it fails to compile until the field exists:

```rust
    #[test]
    fn the_pilot_manifest_keeps_its_id_with_the_new_fields_absent() {
        let m = manifest();
        assert_eq!(m.execution_context, ExecutionContext::Interactive);
        assert!(!m.needs_input_desktop);
        assert_eq!(m.source, DriverSource::Development);
        assert!(m.applications.is_empty(), "absent is empty, and never serialised");
        assert_eq!(m.driver_id(), PILOT_ID);
    }
```

New, after it:

```rust
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
        refuse(&|m| m.applications = vec!["winword.exe".into()], "upper-case");
        refuse(&|m| m.applications = vec!["WINWORD".into()], "ending in .EXE");
        refuse(&|m| m.applications = vec!["C:\\WINWORD.EXE".into()], "no path");
        refuse(&|m| m.applications = vec!["OFFICE/WINWORD.EXE".into()], "no path");
    }

    #[test]
    fn a_browser_driver_may_not_name_applications() {
        let mut m = manifest();
        m.applications = vec!["WINWORD.EXE".into()];
        let e = m.validate().unwrap_err().to_string();
        assert!(e.contains("applications must be empty"), "{e}");
        let mut m = manifest();
        m.hosts.clear();
        assert!(m.validate().unwrap_err().to_string().contains("hosts is empty"));
    }

    #[test]
    fn applications_are_identity_when_present_and_absent_when_empty() {
        let mut m = word_com();
        let id = m.driver_id();
        m.applications.push("POWERPNT.EXE".into());
        assert_ne!(m.driver_id(), id, "another program driven is another driver");
        let text = serde_json::to_string(&manifest()).unwrap();
        assert!(!text.contains("applications"), "{text}");
    }
```

`crates/vk-driver/tests/fake_driver.rs`: `ENV_DRIVER`'s `names` gains `"VK_DRIVER_APPLICATIONS"`; in `the_environment_names_the_dirs_the_mode_and_the_hosts`, the second (headless) block gains `assert_eq!(out.result["VK_DRIVER_APPLICATIONS"], "");`, and a third block follows it:

```rust
    // A com driver: no host, its applications comma-joined.
    let rig = Rig::new(ENV_DRIVER);
    let mut m = rig.manifest(rig.command(), env_action.clone());
    m.kind = vk_contracts::driver::DriverKind::Com;
    m.hosts.clear();
    m.applications = vec!["WINWORD.EXE".into(), "POWERPNT.EXE".into()];
    m.validate().unwrap();
    let mut host = rig.spawn(&m);
    let out = host
        .act(action(&m, "env"), &json!({}), Duration::from_secs(10), WIDE, &never)
        .expect("env answers");
    assert_eq!(out.result["VK_DRIVER_APPLICATIONS"], "WINWORD.EXE,POWERPNT.EXE");
    assert_eq!(out.result["VK_DRIVER_HOSTS"], "");
    host.shutdown();
```

(The second block's `env_action` is then passed as `env_action.clone()` so the third can reuse it.)

- [ ] **Step 2: Run** `cargo test --locked -p vk-contracts driver::` → compile error (`no field applications`, `no variant Com`).

- [ ] **Step 3: Implement**

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
    /// reports them (`WINWORD.EXE`); empty, and not serialised, for a browser
    /// driver, so every manifest written before this field keeps its id.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub applications: Vec<String>,
```

The schema (ruling L-P8): the existing `#[schemars(extend("if" = …, "then" = …))]` (106–114) keeps its service rule and gains, in the same `extend(…)`:

```rust
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
```

In `validate()`, the `if self.hosts.is_empty() { … }` at 256–258 becomes (the per-host loop after it stays and runs on an empty list harmlessly; the doc comment's list of checks names "hosts or applications, by kind"):

```rust
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
                    if a != &a.to_ascii_uppercase() || !a.ends_with(".EXE") {
                        return invalid(format!(
                            "application `{a}`: upper-case executable name ending in .EXE, as Windows reports it"
                        ));
                    }
                }
            }
        }
```

`crates/vk-ipc/src/drivers.rs`:

```rust
/// The memory cap on a com driver's tree when its manifest names none: the
/// driver's own Python and its COM client. The application's process (Word)
/// is the user's interactive instance, outside the Job (spike C).
const COM_MAX_MEMORY: u64 = 1 << 30;
```

and in `memory_cap_of`: `DriverKind::Com => COM_MAX_MEMORY,`.

`crates/vk-driver/src/lib.rs`, in `SpawnSpec.env` after `("VK_DRIVER_HOSTS".into(), manifest.hosts.join(","))`: `("VK_DRIVER_APPLICATIONS".into(), manifest.applications.join(",")),`; the doc comment of `spawn` names it beside `VK_DRIVER_HOSTS` ("the executables a com driver drives, comma-joined").

`contracts/examples/driver_manifest/valid/word_com.json`:

```json
{
  "name": "word-maison",
  "kind": "com",
  "program": {
    "runtime": "python-com",
    "command": [".venv/Scripts/python.exe", "-I", "driver.py"],
    "files_sha256": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
    "browser_channel": null,
    "headless": true
  },
  "hosts": [],
  "applications": ["WINWORD.EXE"],
  "actions": [
    {
      "name": "appliquer_style_maison",
      "reversible": false,
      "args": {
        "type": "object",
        "properties": { "document": { "type": "string" }, "pdf": { "type": "string" } },
        "required": ["document", "pdf"],
        "additionalProperties": false
      },
      "evidence": ["post.json"],
      "to_register": false
    }
  ],
  "min_interval_ms": 0,
  "max_action_ms": 120000,
  "clearance": { "max_scope": "business", "third_party_allowed": false },
  "governed": false
}
```

`invalid/com_without_applications.json`: the same file without the `"applications"` line (the schema's `required`). `invalid/browser_with_applications.json`: `contracts/examples/driver_manifest/invalid/no_actions.json`'s manifest with an `actions` array of one action (`{"name": "lire_reponse", "reversible": true, "args": {"type": "object", "additionalProperties": false}, "evidence": ["dom.html"], "to_register": false}`) and `"applications": ["WINWORD.EXE"]` (the schema's `maxItems: 0`). Each is invalid for that one reason only.

- [ ] **Step 4:** `cargo run -p vk-contracts --bin gen-schemas`; `cargo test --locked -p vk-contracts -p vk-ipc -p vk-driver -p vk-kernel` green (`every_example_validates_as_its_folder_says` included: the two invalid files fail the regenerated schema, ruling L-P8).

- [ ] **Step 5: Commit** `feat(contracts): DriverKind::Com and applications, in the schema and the validator; every identity unchanged`

---

### Task 2: `DriverPlan`, `driver.learned`, `driver.qualified` (Sonnet)

**Model:** Sonnet. **Founder checkpoint after this task** (the contracts the rest of the plan builds on).

**Files:**
- Create: `crates/vk-contracts/src/plan.rs`, `contracts/examples/driver_plan/valid/word_maison.json`, `contracts/examples/driver_plan/invalid/no_actions.json`, `contracts/examples/driver_plan/invalid/unknown_verb_shape.json`, `contracts/examples/driver_learned/valid/first.json`, `contracts/examples/driver_qualified/valid/first.json`
- Modify: `crates/vk-contracts/src/driver.rs` (`DriverLearned`, `DriverQualified` after `DriverUnmounted`, 436–441; `is_sha256` (167–174) becomes `pub(crate)` for `plan.rs`), `crates/vk-contracts/src/lib.rs` (`pub mod plan;` and three rows in `schema_registry`, 26–83), `crates/vk-contracts/src/ledger.rs` (`ALLOWED_KINDS` 256–297: `driver.learned`, `driver.qualified`; one test), `crates/vk-cli/src/man.rs` (`SCHEMAS`, 16–127: three rows), `contracts/README.md` §Drivers, `docs/technical/technical-readme.md` (the ledger payload table), `contracts/tcb.md` §Drivers (one sentence each, spec §11)
- Regenerate: `contracts/schemas/driver_plan.schema.json`, `contracts/schemas/driver_learned.schema.json`, `contracts/schemas/driver_qualified.schema.json`

**Interfaces:**
- Produces (exact; consumed by Tasks 6, 9, 10):

```rust
// crates/vk-contracts/src/plan.rs — the artefact a human reads and approves (spec §4.5)
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct DriverPlan {
    pub family: String,                 // "office-document"
    pub app: String,                    // "word" | "powerpoint"
    pub ontology_sha256: String,
    pub catalogue_sha256: String,
    pub invariants_sha256: String,
    pub actions: Vec<PlanAction>,
    #[serde(default)]
    pub ambiguities: Vec<Ambiguity>,
    pub qualification: Qualification,
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct PlanAction {
    pub name: String,
    /// True only when every step's ontology verb is reversible (L-P13).
    pub reversible: bool,
    pub steps: Vec<PlanStep>,
    pub pre: Vec<String>,
    /// Invariant ids, and `pdf_exists`: one per `verify_*` step, in their order.
    pub post: Vec<String>,
    /// The `partial` bindings the steps use, for the human reading the plan.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub note: Option<String>,
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct PlanStep {
    pub verb: String,
    #[serde(default)]
    pub args: serde_json::Map<String, Value>,
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Ambiguity {
    pub invariant: String,
    /// `arch:<arch_id>` | `human` | `purged`.
    pub resolved_by: String,
    /// The chosen candidate: present exactly when `resolved_by` is not `purged`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub value: Option<Value>,
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Qualification {
    pub min_passes: u32,
    pub min_rate: f64,
}
impl DriverPlan {
    /// Refusals, in this order, each naming its field and its remedy: family and app plain tokens;
    /// the three hashes `sha256:` + 64 lower-case hex; `actions is empty`; per action: name 1..32 of
    /// a-z and `_`, unique; steps non-empty and the first step's verb `open`; every verb 1..40 of a-z
    /// and `_`; every args key 1..32 of a-z, 0-9 and `_`, starting with a letter; every string arg
    /// starting with `$` is `$arg.<key>` or `$secret.<id>` (key/id as an args key); every `post` entry
    /// 1..40 of a-z, 0-9 and `_`, and as many entries as `verify_*` steps; every ambiguity's
    /// `resolved_by` is `purged`, `human` or `arch:<non-empty>`, with `value` present exactly when it
    /// is not `purged`; `min_passes ≥ 1`; `0 ≤ min_rate ≤ 1`.
    pub fn validate(&self) -> Result<(), String>;
}
/// `plan_sha256` (ruling L-P13): the SHA-256 of plan.json's bytes as they are on disk — no
/// re-serialisation, the same rule as vkforge's `plan.plan_sha256(path)`.
pub fn plan_sha256(bytes: &[u8]) -> String; // = crate::hash_bytes(bytes)

// crates/vk-contracts/src/driver.rs, after DriverUnmounted (spec §4.8)
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct DriverLearned { pub family: String, pub app: String, pub catalogue_sha256: String, pub ontology_sha256: String, pub corpus_schema_sha256: String, pub invariants_sha256: String, pub plan_sha256: String, pub driver_id: String, pub by: String }
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct DriverQualified { pub driver_id: String, pub passes: u32, pub failures: u32, pub qualification_sha256: String }
```

- [ ] **Step 1: Failing tests**

`plan.rs`, tests module:

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    const EXAMPLE: &[u8] = include_bytes!("../../../contracts/examples/driver_plan/valid/word_maison.json");

    fn example() -> DriverPlan {
        serde_json::from_slice(EXAMPLE).unwrap()
    }

    #[test]
    fn the_example_plan_validates_and_its_hash_is_the_hash_of_its_bytes() {
        let p = example();
        p.validate().unwrap();
        assert_eq!(plan_sha256(EXAMPLE), crate::hash_bytes(EXAMPLE));
        assert!(plan_sha256(EXAMPLE).starts_with("sha256:"));
        // The bytes, never a re-serialisation (L-P13): the same plan written otherwise is another hash.
        let rewritten = serde_json::to_vec(&p).unwrap();
        assert_eq!(serde_json::from_slice::<DriverPlan>(&rewritten).unwrap(), p);
        assert_ne!(plan_sha256(&rewritten), plan_sha256(EXAMPLE));
    }

    #[test]
    fn a_plan_without_actions_or_with_a_malformed_action_is_refused() {
        let refuse = |edit: &dyn Fn(&mut DriverPlan), needle: &str| {
            let mut p = example();
            edit(&mut p);
            let e = p.validate().unwrap_err();
            assert!(e.contains(needle), "{needle}: {e}");
        };
        refuse(&|p| p.actions.clear(), "actions is empty");
        refuse(&|p| p.actions[0].steps[1].verb = "Apply-Style".into(), "verb");
        refuse(&|p| { p.actions[0].steps.remove(0); }, "first step");
        refuse(&|p| { p.actions[0].post.pop(); }, "verify_");
        refuse(&|p| p.actions[0].name = "Appliquer".into(), "action name");
        refuse(&|p| { let a = p.actions[0].clone(); p.actions.push(a); }, "duplicate");
        refuse(&|p| { p.actions[0].steps[1].args.insert("from-x".into(), json!(1)); }, "from-x");
        refuse(&|p| p.ontology_sha256 = "sha256:abc".into(), "ontology_sha256");
        refuse(&|p| p.qualification.min_rate = 1.5, "min_rate");
        refuse(&|p| p.qualification.min_passes = 0, "min_passes");
    }

    #[test]
    fn a_reference_names_an_argument_or_a_secret_and_nothing_else() {
        let set = |v: Value| {
            let mut p = example();
            p.actions[0].steps[3].args.insert("text".into(), v);
            p.validate()
        };
        assert!(set(json!("$secret.footer_text")).is_ok());
        assert!(set(json!("$secret.")).unwrap_err().contains("$secret."));
        assert!(set(json!("$env.HOME")).unwrap_err().contains("$env.HOME"));
        assert!(set(json!("$arg.Document")).unwrap_err().contains("$arg.Document"));
    }

    #[test]
    fn an_ambiguity_carries_a_value_exactly_when_it_was_resolved() {
        let with = |resolved_by: &str, value: Option<Value>| {
            let mut p = example();
            p.ambiguities = vec![Ambiguity { invariant: "body_font".into(), resolved_by: resolved_by.into(), value }];
            p.validate()
        };
        let garamond = json!({"name": "Garamond", "size": 11});
        assert!(with("purged", None).is_ok());
        assert!(with("arch:sha256:abc", Some(garamond.clone())).is_ok());
        assert!(with("purged", Some(garamond.clone())).unwrap_err().contains("value"));
        assert!(with("arch:sha256:abc", None).unwrap_err().contains("value"));
        assert!(with("arch:", Some(garamond)).unwrap_err().contains("resolved_by"));
    }

    #[test]
    fn unknown_fields_are_refused_at_parse() {
        let mut v: Value = serde_json::from_slice(EXAMPLE).unwrap();
        v["actions"][0]["steps"][0]["target"] = json!("x");
        assert!(serde_json::from_value::<DriverPlan>(v).is_err());
        let mut v: Value = serde_json::from_slice(EXAMPLE).unwrap();
        v["excluded"] = json!([]);
        assert!(serde_json::from_value::<DriverPlan>(v).is_err(), "excluded lives in plan-excluded.json (L-P13)");
    }
}
```

`ledger.rs`, beside `boot_forced_is_an_allowed_kind`:

```rust
    #[test]
    fn driver_learned_and_driver_qualified_are_allowed_kinds() {
        assert!(is_allowed_kind("driver.learned"));
        assert!(is_allowed_kind("driver.qualified"));
        assert!(!is_allowed_kind("driver.learn"));
    }
```

- [ ] **Step 2: Run** `cargo test --locked -p vk-contracts plan::` → compile error (no module `plan`).

- [ ] **Step 3: Implement** the interface block; `validate` refusals in the order of its doc comment, each a sentence with its remedy (`"actions is empty: a plan holds at least one action; remedy: learn again from a corpus that yields an invariant"`, `"action appliquer_style_maison: the first step is open, the step that names the document; remedy: …"`, …). The examples:

`contracts/examples/driver_plan/valid/word_maison.json`:

```json
{
  "family": "office-document",
  "app": "word",
  "ontology_sha256": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
  "catalogue_sha256": "sha256:2222222222222222222222222222222222222222222222222222222222222222",
  "invariants_sha256": "sha256:3333333333333333333333333333333333333333333333333333333333333333",
  "actions": [
    {
      "name": "appliquer_style_maison",
      "reversible": false,
      "steps": [
        { "verb": "open", "args": { "path": "$arg.document" } },
        { "verb": "apply_paragraph_style", "args": { "selector": "first", "style": "Heading1" } },
        { "verb": "set_font_run", "args": { "selector": "body", "font": { "name": "Garamond", "size": 11 } } },
        { "verb": "set_footer", "args": { "text": "$secret.footer_text" } },
        { "verb": "save", "args": {} },
        { "verb": "export", "args": { "format": "pdf", "path": "$arg.pdf" } },
        { "verb": "verify_paragraph_style", "args": { "selector": "first", "style": "Heading1" } },
        { "verb": "verify_font_run", "args": { "selector": "body", "font": { "name": "Garamond", "size": 11 } } },
        { "verb": "verify_footer", "args": { "text": "$secret.footer_text" } },
        { "verb": "verify_pdf", "args": { "path": "$arg.pdf" } }
      ],
      "pre": ["document is .docx", "not open elsewhere"],
      "post": ["title_style", "body_font", "footer_text", "pdf_exists"]
    }
  ],
  "ambiguities": [],
  "qualification": { "min_passes": 10, "min_rate": 1.0 }
}
```

`invalid/no_actions.json`: the same without the `actions` key (refused by the schema's `required`). `invalid/unknown_verb_shape.json`: the same with one step `{ "verb": "open", "args": { "path": "$arg.document" }, "target": "first" }` (refused by `additionalProperties: false`, which `deny_unknown_fields` writes into the schema). Ruling L-P8: nothing else is put under `invalid/`, since the schema refuses only what its own rules say; the rest of `validate` is pinned by the Rust tests above.

`contracts/examples/driver_learned/valid/first.json`:

```json
{
  "family": "office-document",
  "app": "word",
  "catalogue_sha256": "sha256:2222222222222222222222222222222222222222222222222222222222222222",
  "ontology_sha256": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
  "corpus_schema_sha256": "sha256:4444444444444444444444444444444444444444444444444444444444444444",
  "invariants_sha256": "sha256:3333333333333333333333333333333333333333333333333333333333333333",
  "plan_sha256": "sha256:5555555555555555555555555555555555555555555555555555555555555555",
  "driver_id": "sha256:6666666666666666666666666666666666666666666666666666666666666666",
  "by": "laptop"
}
```

`contracts/examples/driver_qualified/valid/first.json`:

```json
{
  "driver_id": "sha256:6666666666666666666666666666666666666666666666666666666666666666",
  "passes": 10,
  "failures": 0,
  "qualification_sha256": "sha256:7777777777777777777777777777777777777777777777777777777777777777"
}
```

`schema_registry` rows `("driver_plan", schemars::schema_for!(plan::DriverPlan))`, `("driver_learned", schemars::schema_for!(driver::DriverLearned))`, `("driver_qualified", schemars::schema_for!(driver::DriverQualified))`; `ALLOWED_KINDS` gains, after `driver.packed`, a comment and the two kinds:

```rust
    // A human learned a driver from the firm's documents, and the kernel
    // recorded its qualification replay (driver-learning chantier, spec
    // §4.8): payloads `driver::DriverLearned`, `driver::DriverQualified`.
    "driver.learned",
    "driver.qualified",
```

`man.rs` `SCHEMAS`: the three rows, alphabetical like the rest. Docs: `contracts/README.md` §Drivers, one sentence on `DriverPlan` and the two payloads; `docs/technical/technical-readme.md`, two rows of the payload table (`driver.learned`, human act; `driver.qualified`, kernel-written); `contracts/tcb.md` §Drivers, one sentence: "A learned driver is identified like a written one and trusted no more: `driver.learned` records the hashes of what it was learned from, `driver.qualified` the hash of its qualification record."

- [ ] **Step 4:** `cargo run -p vk-contracts --bin gen-schemas`; `cargo test --locked -p vk-contracts -p vk-cli` green (`every_schema_in_the_repository_is_in_the_table`, `committed_schemas_match_generated`, `every_example_validates_as_its_folder_says`).

- [ ] **Step 5: Commit** `feat(contracts): DriverPlan, driver.learned and driver.qualified`

---

### Task 3: Inventory — the catalogue from a type library, and the recorded fixtures (Sonnet; last step: controller, on the node)

**Model:** Sonnet for Steps 1–5; the controller runs Step 6 on the founder's node, **before Task 4 starts** (ruling L-P21).

**Files:**
- Modify: `drivers/vkforge/inventory.py` (Task 0's stub: the module, its `add_arguments` and `run`)
- Create: `drivers/vkforge/tests/test_inventory.py`; by the controller in Step 6, `drivers/vkforge/tests/fixtures/catalogue-word.json` and `drivers/vkforge/tests/fixtures/catalogue-powerpoint.json` (recorded once on the founder's node, then committed; CI reads them)

**Interfaces:**
- Produces (consumed by Tasks 4, 6, 7, 9):

```python
# drivers/vkforge/inventory.py — stage `inventory` (STAGES["inventory"])
# No ProgID here: the adapter's ProgID is the ontology's `applications` block's (L-P31, Task 4).
APPS = {
    "word":       {"exe": "WINWORD.EXE",  "kind": "docx", "typelib": "{00020905-0000-0000-C000-000000000046}"},
    "powerpoint": {"exe": "POWERPNT.EXE", "kind": "pptx", "typelib": "{91493440-5A91-11CF-8700-00AA0060263B}"},
}
def typelib_path(guid: str) -> str              # HKCR\TypeLib\{guid}\<highest version>\0\win64, then \win32; Refusal if absent
def office_present(app: str) -> bool            # False off Windows; else typelib_path succeeds
def catalogue_from_typelib(app: str, path: str) -> dict   # comtypes.typeinfo.LoadTypeLibEx → the catalogue (spec §4.1),
                                                          # its "typelib_sha256" = canon.sha256_file(path) (L-P22)
def catalogue_from_fixture(app: str, fixture: Path) -> dict  # Refusal for an app not in APPS (checked first) or a file of another app
def restrict(cat: dict, types: list[str]) -> dict  # only the named types; every enum and every other key kept
def catalogue_sha256(cat: dict) -> str          # canon.sha256(cat) = SHA-256 of catalogue.json's bytes
def write_catalogue(cat: dict, out: Path) -> Path  # canon.write to out/catalogue.json; returns the path
# CLI: vkforge inventory --app {word,powerpoint} --out DIR [--fixture FILE] [--only-types NAME,NAME…]
#      prints "inventory: <n> types, <m> enums -> <out>/catalogue.json"; Office absent →
#      "vkforge inventory: type library of word {00020905-…} is not registered; remedy: install Microsoft 365 desktop"
```

Catalogue shape (spec §4.1): `{"app", "typelib": {"guid", "version"}, "typelib_sha256": "sha256:<hex of the type library file's bytes>", "types": [{"name", "kind": "dispatch|coclass|enum", "members": [{"name", "kind": "method|property_get|property_put", "params": [{"name", "type"}], "returns"}]}], "enums": {"WdBuiltinStyle": {"wdStyleHeading1": -2, …}}}`, types and members sorted by name.

- [ ] **Step 1: Failing tests** (`drivers/vkforge/tests/test_inventory.py`)

```python
import json
import sys
import tempfile
import unittest
from pathlib import Path

from vkforge import __main__ as entry
from vkforge import canon, inventory
from vkforge.errors import Refusal

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def reorder(x):
    """The same content with every object's keys in reverse order."""
    if isinstance(x, dict):
        return {k: reorder(x[k]) for k in reversed(list(x))}
    if isinstance(x, list):
        return [reorder(v) for v in x]
    return x


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
        self.assertRegex(cat["typelib_sha256"], r"^sha256:[0-9a-f]{64}$")   # L-P22: the Office it was recorded on

    def test_the_recorded_powerpoint_catalogue_has_placeholders_and_export(self):
        cat = inventory.catalogue_from_fixture("powerpoint", FIXTURES / "catalogue-powerpoint.json")
        self.assertIn("ppPlaceholderTitle", cat["enums"]["PpPlaceholderType"])
        self.assertIn("ppSaveAsPDF", cat["enums"]["PpSaveAsFileType"])

    def test_the_catalogue_hash_is_stable_across_key_order(self):
        cat = inventory.catalogue_from_fixture("word", FIXTURES / "catalogue-word.json")
        shuffled = reorder(cat)
        self.assertNotEqual(json.dumps(shuffled), json.dumps(cat), "the key order did change")
        self.assertEqual(inventory.catalogue_sha256(cat), inventory.catalogue_sha256(shuffled))
        out = Path(tempfile.mkdtemp())
        path = inventory.write_catalogue(shuffled, out)
        self.assertEqual(path, out / "catalogue.json")
        self.assertEqual(canon.sha256_file(path), inventory.catalogue_sha256(cat))

    def test_restrict_keeps_the_named_types_and_every_enum(self):
        cat = inventory.catalogue_from_fixture("word", FIXTURES / "catalogue-word.json")
        small = inventory.restrict(cat, ["Document", "Font"])
        self.assertEqual([t["name"] for t in small["types"]], ["Document", "Font"])
        self.assertEqual(small["enums"], cat["enums"])
        self.assertEqual((small["typelib"], small["typelib_sha256"]), (cat["typelib"], cat["typelib_sha256"]))

    @unittest.skipUnless(sys.platform == "win32" and inventory.office_present("word"), "Word not installed")
    def test_the_live_type_library_matches_the_fixture_on_the_members_the_ontology_needs(self):
        path = inventory.typelib_path(inventory.APPS["word"]["typelib"])
        live = inventory.catalogue_from_typelib("word", path)
        fixture = inventory.catalogue_from_fixture("word", FIXTURES / "catalogue-word.json")
        self.assertEqual(live["enums"]["WdBuiltinStyle"], fixture["enums"]["WdBuiltinStyle"])
        self.assertEqual(live["typelib_sha256"], canon.sha256_file(path), "the file's bytes (L-P22)")

    def test_an_unknown_app_is_refused_before_any_file_is_read(self):
        with self.assertRaises(Refusal) as cm:
            inventory.catalogue_from_fixture("excel", FIXTURES / "absent.json")
        self.assertEqual(str(cm.exception), "unknown app excel; remedy: --app word or --app powerpoint")

    def test_the_cli_writes_catalogue_json_from_a_fixture(self):
        out = Path(tempfile.mkdtemp()) / "out"
        code = entry.main(["inventory", "--app", "word", "--out", str(out),
                           "--fixture", str(FIXTURES / "catalogue-word.json"), "--only-types", "Document,Font"])
        self.assertEqual(code, 0)
        written = json.loads((out / "catalogue.json").read_text(encoding="utf-8"))
        self.assertEqual([t["name"] for t in written["types"]], ["Document", "Font"])
```

- [ ] **Step 2: Run** `python -I -m unittest discover -s drivers/vkforge/tests -t . -p test_inventory.py` → errors (the stub has no `catalogue_from_fixture`).

- [ ] **Step 3: Implement** `catalogue_from_typelib` with `comtypes.typeinfo.LoadTypeLibEx(path)` (`comtypes` imported inside the function, never at module top: the module imports on Linux); iterate `tlib.GetTypeInfoCount()`, for each `GetTypeInfo(i)` / `GetTypeAttr()`: `typekind` `TKIND_ENUM` → read `GetVarDesc(j)` constants; `TKIND_DISPATCH`/`TKIND_INTERFACE` → `GetFuncDesc(j)` for name (`GetDocumentation(memid)[0]`), `invkind` (`INVOKE_FUNC`→method, `INVOKE_PROPERTYGET`, `INVOKE_PROPERTYPUT`), params (`GetNames(memid, cMaxNames=cParams+1)`, types rendered through `TYPEDESC.vt` → a name table `VT_BSTR: "BSTR"`, `VT_VARIANT: "VARIANT"`, `VT_I4: "long"`, `VT_BOOL: "bool"`, `VT_DISPATCH: "IDispatch"`, else `f"vt{vt}"`); `TKIND_COCLASS` → listed with no members. `typelib_path`: `winreg.OpenKey(HKEY_CLASSES_ROOT, rf"TypeLib\{guid}")`, enumerate the version subkeys, take the highest (`major.minor` compared as hexadecimal numbers, as Task 7's `com.live_typelib_sha256` does), read `0\win64` then `0\win32`; the catalogue's `typelib` is `{"guid", "version"}` of that key and its `typelib_sha256` is `canon.sha256_file(path)` (L-P22). `office_present(app)` → `sys.platform == "win32"` and `typelib_path` does not refuse. `restrict` keeps `types` by name, in the catalogue's order. `run(ns)`: the fixture when `--fixture` is given, else the live type library; `restrict` when `--only-types`; `write_catalogue`; the print line of the interface; return 0.

- [ ] **Step 4: Run** the suite: five tests of this task fail until Step 6 — the five that read `catalogue-word.json`/`catalogue-powerpoint.json` (`…word_catalogue…`, `…powerpoint_catalogue…`, `…hash_is_stable…`, `test_restrict_…`, `test_the_cli_…`: `FileNotFoundError`), ruling L-P28; `test_an_unknown_app_…` passes; the live one is skipped off the node, and on the node it passes once Step 6 has run. Nothing hand-written stands in for the recorded fixtures (L-P21).

- [ ] **Step 5: Commit** `feat(vkforge): inventory of a type library into a catalogue`

- [ ] **Step 6: Record the fixtures on the founder's node** (controller, once, before Task 4). The vkforge virtualenv is made here by hand (Task 9's `setup.ps1` reuses it when it exists):

```powershell
$V = "$env:LOCALAPPDATA\vk\venvs\vkforge"
if (-not (Test-Path "$V\Scripts\python.exe")) { & (uv python find --managed-python 3.13) -m venv $V }
& "$V\Scripts\python.exe" -I -m pip install --disable-pip-version-check -r drivers\vkforge\requirements.txt
foreach ($app in "word", "powerpoint") {
  & "$V\Scripts\python.exe" -I drivers\vkforge\__main__.py inventory --app $app --out "$env:TEMP\vkforge-cat-$app"
  Copy-Item "$env:TEMP\vkforge-cat-$app\catalogue.json" "drivers\vkforge\tests\fixtures\catalogue-$app.json"
}
& "$V\Scripts\python.exe" -I -m unittest discover -s drivers/vkforge/tests -t . -p test_inventory.py
```

Expected: `inventory: <n> types, <m> enums -> …` twice, then every test of `test_inventory.py` green (7, the live one included); each recorded catalogue carries this node's `typelib_sha256` (L-P22), which the bindings and the generated drivers inherit. Size expected 1–3 MB each; above 4 MB, record again with `--only-types` naming the types the ontology's patterns walk (Task 4 lists them) — every enum is kept. **Commit** `test(vkforge): the Word and PowerPoint catalogues, recorded on the founder's node`.

---

### Task 4: The ontology `office-document.json` v1 and the binding (Opus)

**Model:** Opus.

**Files:**
- Create: `drivers/vkforge/ontology/office-document.json`, `drivers/vkforge/tests/test_ontology.py`; written by this task's test and committed, `drivers/vkforge/tests/fixtures/binding-word.json` (ruling L-P21)
- Modify: `drivers/vkforge/ontology.py` (Task 0's stub; the module of the stage `bind`)

**Interfaces:**
- Consumes: `inventory.catalogue_from_fixture`, `inventory.APPS` and the catalogue's `typelib`/`typelib_sha256` (Task 3), the recorded catalogues (Task 3, Step 6), `vkforge.canon` (Task 0).
- Produces (consumed by Tasks 6, 7, 9):

```python
# drivers/vkforge/ontology.py — stage `bind` (STAGES["bind"])
ONTOLOGY = Path(__file__).resolve().parent / "ontology" / "office-document.json"
ARG_TYPES = frozenset({"Path", "ParagraphSelector", "BuiltinStyle", "FontSpec", "Text", "Format"})
PRECONDITIONS = frozenset({"none", "document_open"})
POSTCONDITIONS = frozenset({"document_open", "paragraph_style", "font_run", "footer", "header", "pdf_exists", "saved", "none"})
APPLICATION_KEYS = ("alerts_off", "close", "collection", "open", "progid")   # the keys of each `applications` entry (L-P31)
def is_selector(s: str) -> bool                  # first | body | all | title | index:<n> (n ≥ 1)
def load(path: Path | None = None) -> dict       # default ONTOLOGY; validates
def validate(ont: dict) -> None                  # Refusal naming the verb and the field
def ontology_sha256(ont: dict) -> str            # canon.sha256(ont): the file is stored canonical
def bind(ont: dict, catalogue: dict) -> dict     # the binding below
def write_binding(binding: dict, out: Path) -> Path   # canon.write to out/binding-<app>.json; returns the path
# CLI: vkforge bind --catalogue FILE --out DIR [--ontology FILE]
#      prints "bind: <b> bound, <p> partial, <u> unbound on <app> -> <path>" then one line per unbound verb
```

Ontology file: `{"family": "office-document", "version": 1, "applications": {...}, "verbs": [...]}`, stored in `canon.dumps` form. The `applications` block (ruling L-P31) holds, per app of `inventory.APPS`, exactly the keys of `APPLICATION_KEYS` — everything `runtime/com.py` would otherwise have to name about one application; the adapter receives the app's entry as the binding's `application` and names no application itself:

```json
"applications": {
  "powerpoint": { "alerts_off": 1, "close": {}, "collection": "Presentations",
                  "open": { "WithWindow": false }, "progid": "PowerPoint.Application" },
  "word":       { "alerts_off": 0, "close": { "SaveChanges": false }, "collection": "Documents",
                  "open": { "AddToRecentFiles": false, "ReadOnly": false, "Visible": false },
                  "progid": "Word.Application" } }
```

(`progid` for `GetActiveObject`/`CreateObject`; `collection`, the application's member whose `Open(path, **open)` opens a document; `close`, the keyword arguments of the document's `Close` — PowerPoint's takes none; `alerts_off`, the `DisplayAlerts` value that silences the application: `wdAlertsNone` 0, `ppAlertsNone` 1.) Every verb (spec §4.2) has `verb`, `args` (name → a type of `ARG_TYPES`), `pre` (one of `PRECONDITIONS`), `post` (one of `POSTCONDITIONS`), `reversible` (bool: `true` for `open`, `close`, `select_paragraph`, `count_paragraphs`, `read_document_property` and every `verify_*`; `false` for every verb that writes the document or a file), and `projection` per app, written as **patterns** over the catalogue:

```json
{ "verb": "apply_paragraph_style",
  "args": { "selector": "ParagraphSelector", "style": "BuiltinStyle" },
  "pre": "document_open", "post": "paragraph_style", "reversible": false,
  "projection": {
    "word":       { "member": "Paragraph.Style", "constants": "WdBuiltinStyle" },
    "powerpoint": { "member": "Shape.PlaceholderFormat.Type", "constants": "PpPlaceholderType", "status": "partial" } } }
```

Binding (`binding-<app>.json`): `{"app", "ontology_sha256", "catalogue_sha256", "typelib": {"guid", "version"}, "typelib_sha256", "application": {<the ontology's applications[app]>}, "verbs": [{"verb", "status": "bound|partial|unbound", "member": "Type.Member[.Member]" | null, "constants": {"<Enum>": {"<name>": <int>, …}} | null, "why": "<which pattern failed>" | null}]}`, verbs sorted by name; `typelib` and `typelib_sha256` are the catalogue's, copied (L-P22: the generated driver's `probe` compares them with the live registry), `application` the ontology's entry for the app (L-P31). A runtime indexes it with `{v["verb"]: v for v in binding["verbs"]}`.

The v1 verb list (32 verbs), all with Word projections, PowerPoint projections where the model has a counterpart: `open`, `close`, `save`, `save_as`, `export` (pdf), `select_paragraph` (selectors `first | body | all | index:n | title`, `title` being PowerPoint's title placeholder and Word's first paragraph), `apply_paragraph_style`, `set_font_run` (name, size), `set_font_bold`, `set_font_italic`, `set_paragraph_alignment`, `set_paragraph_spacing`, `set_line_spacing`, `set_header` (text), `set_footer` (text), `set_page_margins`, `set_page_orientation`, `set_document_property` (title/author/subject), `insert_page_number`, `apply_numbering`, `set_table_style`, `set_table_borders`, `verify_paragraph_style`, `verify_font_run`, `verify_footer`, `verify_header`, `verify_pdf`, `count_paragraphs`, `read_document_property`, `set_slide_layout` (powerpoint only; word `unbound` by design), `set_placeholder_text`, `apply_theme`. The types the Word patterns walk: `Document`, `Documents`, `Paragraphs`, `Paragraph`, `Range`, `Font`, `Sections`, `Section`, `HeadersFooters`, `HeaderFooter`, `Style`, `PageSetup`, `Table`, `ListFormat`, `BuiltInDocumentProperties` (Step 6 of Task 3 restricts to these if a catalogue is too large).

- [ ] **Step 1: Failing tests** (`drivers/vkforge/tests/test_ontology.py`)

```python
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from vkforge import __main__ as entry
from vkforge import canon, inventory, ontology
from vkforge.errors import Refusal

FIXTURES = Path(__file__).resolve().parent / "fixtures"


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
        self.assertEqual((b["app"], b["catalogue_sha256"], b["ontology_sha256"]),
                         ("word", inventory.catalogue_sha256(self.word), ontology.ontology_sha256(self.ont)))
        self.assertEqual((b["typelib"], b["typelib_sha256"]), (self.word["typelib"], self.word["typelib_sha256"]))  # L-P22
        self.assertEqual(b["application"], self.ont["applications"]["word"])                                       # L-P31

    def test_the_same_ontology_binds_on_powerpoint_without_code(self):
        b = ontology.bind(self.ont, self.ppt)
        status = {v["verb"]: v["status"] for v in b["verbs"]}
        for verb in ("open", "set_font_run", "set_footer", "save", "export", "set_slide_layout", "set_placeholder_text",
                     "verify_font_run", "verify_footer", "verify_pdf"):
            self.assertIn(status[verb], ("bound", "partial"), verb)
        self.assertEqual(status["apply_paragraph_style"], "partial")
        self.assertEqual(status["apply_numbering"], "unbound")

    def test_a_pattern_that_matches_nothing_is_unbound_not_an_exception(self):
        ont = ontology.load()
        ont["verbs"].append({"verb": "levitate", "args": {}, "pre": "document_open", "post": "saved", "reversible": False,
                             "projection": {"word": {"member": "Document.Levitate", "constants": None}}})
        b = ontology.bind(ont, self.word)
        v = next(x for x in b["verbs"] if x["verb"] == "levitate")
        self.assertEqual((v["status"], v["member"], v["constants"]), ("unbound", None, None))
        self.assertIn("Document.Levitate", v["why"])

    def test_constants_are_resolved_to_numbers_by_their_builtin_names(self):
        b = ontology.bind(self.ont, self.word)
        v = next(x for x in b["verbs"] if x["verb"] == "apply_paragraph_style")
        styles = v["constants"]["WdBuiltinStyle"]
        self.assertEqual(styles["wdStyleHeading1"], -2)
        self.assertTrue(all(k.startswith("wdStyle") and isinstance(n, int) for k, n in styles.items()), "built-in anchors only")

    def test_the_ontology_file_validates_its_own_shape(self):
        bad = ontology.load()
        del bad["verbs"][0]["post"]
        with self.assertRaises(Refusal) as cm:
            ontology.validate(bad)
        self.assertIn(bad["verbs"][0]["verb"], str(cm.exception))
        bad = ontology.load()
        bad["verbs"][0]["args"] = {"x": "Colour"}
        with self.assertRaises(Refusal):
            ontology.validate(bad)

    def test_the_ontology_file_is_stored_canonical_with_32_verbs_each_reversible_or_not(self):
        self.assertEqual(ontology.ONTOLOGY.read_bytes(), canon.dumps(self.ont), "keep the file in canon.dumps form")
        names = [v["verb"] for v in self.ont["verbs"]]
        self.assertEqual(len(names), 32)
        self.assertEqual(len(set(names)), 32)
        self.assertTrue(all(isinstance(v["reversible"], bool) for v in self.ont["verbs"]))
        self.assertTrue(ontology.is_selector("index:3") and ontology.is_selector("title"))
        self.assertFalse(ontology.is_selector("index:0") or ontology.is_selector("Titre"))

    def test_the_applications_block_gives_the_adapter_what_it_must_not_name(self):
        # L-P31: runtime/com.py names no application; each app's entry reaches it as binding["application"].
        self.assertEqual(self.ont["applications"], {
            "powerpoint": {"alerts_off": 1, "close": {}, "collection": "Presentations",
                           "open": {"WithWindow": False}, "progid": "PowerPoint.Application"},
            "word": {"alerts_off": 0, "close": {"SaveChanges": False}, "collection": "Documents",
                     "open": {"AddToRecentFiles": False, "ReadOnly": False, "Visible": False},
                     "progid": "Word.Application"}})
        self.assertEqual(sorted(self.ont["applications"]), sorted(inventory.APPS))
        self.assertEqual(ontology.bind(self.ont, self.ppt)["application"], self.ont["applications"]["powerpoint"])
        bad = ontology.load()
        del bad["applications"]["powerpoint"]["collection"]
        with self.assertRaises(Refusal) as cm:
            ontology.validate(bad)
        self.assertIn("powerpoint", str(cm.exception))

    def test_the_committed_word_binding_is_what_bind_produces(self):
        # Ruling L-P21: written on the first run, compared on every later one; Tasks 6–8 read it.
        path = FIXTURES / "binding-word.json"
        produced = canon.dumps(ontology.bind(self.ont, self.word))
        if not path.exists():
            path.write_bytes(produced)
        self.assertEqual(path.read_bytes(), produced, "binding-word.json is stale: delete it, run this test, commit it")

    def test_the_cli_writes_the_binding_and_lists_the_unbound_verbs(self):
        out = Path(tempfile.mkdtemp())
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            self.assertEqual(entry.main(["bind", "--catalogue", str(FIXTURES / "catalogue-word.json"), "--out", str(out)]), 0)
        self.assertTrue((out / "binding-word.json").is_file())
        self.assertIn("set_slide_layout", printed.getvalue())
```

- [ ] **Step 2: Run** `python -I -m unittest discover -s drivers/vkforge/tests -t . -p test_ontology.py` → errors (stub).

- [ ] **Step 3: Implement** `bind`: for each verb and the catalogue's app, split `member` on `.`, walk the catalogue: the first segment is a type name (exact), each next segment a member name (exact) whose `returns` type (for a chain) names the next type (strip `*`/`IDispatch` wrappers; when the return type is `VARIANT`/`IDispatch`, accept the next segment if any type in the catalogue has that member — `partial`); `constants` must be an enum of the catalogue, and the binding carries that whole enum (numbers, keyed by the built-in constant names), else `unbound` with `why`. A projection that declares `"status": "partial"` stays `partial` when it binds. A verb with no projection for the app is `unbound` with `why: "no projection for <app>"`. The binding's `typelib` and `typelib_sha256` are copied from the catalogue (`bind` refuses a catalogue without `typelib_sha256`: `Refusal("catalogue of <app> has no typelib_sha256", "record it again with vkforge inventory")`, L-P22) and its `application` is `ont["applications"][app]` (L-P31). `validate(ont)` checks every verb's fields, arg types in `ARG_TYPES`, `pre` in `PRECONDITIONS`, `post` in `POSTCONDITIONS`, `reversible` a bool, verb names unique, and that `applications` has one entry per `inventory.APPS` key with exactly the keys of `APPLICATION_KEYS` (a refusal names the app and the key); the ontology file itself is written once with `canon.dumps` and edited only in that form.

- [ ] **Step 4: Run** the suite green (9 tests; the first run writes `binding-word.json`). **Commit** `feat(vkforge): the office-document ontology v1 and the binding by patterns over a catalogue` (the binding fixture included).

---

### Task 5: The miner — OOXML invariants, purge, no content (Sonnet)

**Model:** Sonnet.

**Files:**
- Create: `drivers/vkforge/ooxml.py` (shared reader helpers: open as zip, parse a part with `lxml`, namespaces), `drivers/vkforge/tests/ooxml_fixtures.py` (builds minimal valid `.docx`/`.pptx` with `zipfile` — no Office; also a script for the Rust tests), `drivers/vkforge/tests/test_mine.py`
- Modify: `drivers/vkforge/mine.py` (Task 0's stub)

**Interfaces:**
- Consumes: `vkforge.canon`, `vkforge.errors.Refusal` (Task 0).
- Produces (consumed by Tasks 6, 7, 8, 9):

```python
# drivers/vkforge/ooxml.py
NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
      "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
      "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
def is_encrypted(path: Path) -> bool           # starts with OLE: an encrypted OOXML package or a binary .doc
def open_package(path: Path) -> zipfile.ZipFile  # Refusal when not a zip or no [Content_Types].xml
def kind_of(zf: zipfile.ZipFile) -> str        # "docx" | "pptx" | Refusal("<name> is not OOXML", …)
def part(zf: zipfile.ZipFile, name: str)       # lxml root of a member, or None
def parts(zf: zipfile.ZipFile, pattern: str) -> list[str]  # member names matching an fnmatch pattern, sorted

# drivers/vkforge/mine.py — stage `mine` (STAGES["mine"])
MIN_FILES = 5
BUILTIN_STYLES = {"normal": "Normal", "title": "Title", "subtitle": "Subtitle", "quote": "Quote",
                  **{f"heading {n}": f"Heading{n}" for n in range(1, 10)}}   # w:name (lower-case) → built-in
INVARIANT_IDS = {("paragraph_style", "first"): "title_style", ("paragraph_style", "all"): "body_style",
                 ("font_run", "body"): "body_font", ("font_run", "title"): "title_font",
                 ("footer", None): "footer_text", ("header", None): "header_text",
                 ("export_format", None): "export_format", ("page_size", None): "page_size",
                 ("margins", None): "margins", ("orientation", None): "orientation",
                 ("numbering_present", None): "numbering_present"}
def redact(text: str) -> str                   # "<redacted:sha256:" + sha256(text.encode("utf-8")).hexdigest() + ">"
def mine_file(path: Path) -> dict              # {"kind": "docx"|"pptx", "obs": [{"kind", "where", "value"}], "vault": {token: clear}}
def content_sha256(corpus: Path) -> str        # "sha256:" + sha256 over the sorted per-file sha256 of the files mine_corpus mines
def mine_corpus(corpus: Path, threshold: float = 0.8) -> tuple[dict, dict]   # (invariants, vault)
def write_invariants(inv: dict, vault: dict, out: Path) -> None   # out/invariants.json and out/.vault.json, canon.dumps, the vault 0o600
# CLI: vkforge mine --corpus DIR --out DIR [--threshold 0.8]
#      prints "mine: <n> invariants, <m> ambiguities over <files> files (<skipped> skipped) -> <out>/invariants.json"

# drivers/vkforge/tests/ooxml_fixtures.py
FOOTER = "Cabinet Exemple – Confidentiel"
def docx(path, title_style="Heading1", body_font=("Garamond", 11), footer=None, header=None,
         personal=None, style_id=None, pdf=False) -> Path
def pptx(path, title_font=("Garamond", 32), footer=None) -> Path
def corpus(dir, garamond: int, calibri: int = 0) -> Path
# script: python -I drivers/vkforge/tests/ooxml_fixtures.py DIR GARAMOND [CALIBRI]  (Tasks 9, 10, 12)
```

`invariants.json`: `{"corpus": {"files", "skipped", "kind": "docx"|"pptx", "content_sha256", "schema_sha256"}, "threshold", "invariants": [{"id", "kind", "where"?, "value", "support"}], "ambiguities": [{"invariant", "kind", "candidates": [{"value", "support"}]}]}`, invariants sorted by id, ambiguities by invariant; `schema_sha256 = canon.sha256(inv["invariants"])` (ruling L-P13: the list alone, so no self-reference and the threshold is not part of it).

Observations of one file (`mine_file`), each `{"kind", "where", "value"}` (`where` is `null` for a kind with one place):
- `paragraph_style`, `where: "first"`: the first body paragraph's style — its `w:pStyle/@w:val` (a styleId), else the default paragraph style of `styles.xml` — mapped through `styles.xml`'s `w:style[@w:styleId]/w:name/@w:val`: a name in `BUILTIN_STYLES` gives `{"builtin": "<Name>"}`, anchored on the English built-in name and never on the styleId (French Word writes `w:styleId="Titre1"` for `w:name w:val="heading 1"`: Review Focus 2); any other style gives `{"custom": redact(styleId)}`.
- `paragraph_style`, `where: "all"`: emitted only when every paragraph after the first carries one explicit `w:pStyle` (a paragraph without one is in the default style, which a driver need not apply); same value mapping.
- `font_run`, `where: "body"` (docx): `{"name", "size"}` — the majority over the runs of the paragraphs after the first of `w:rFonts/@w:ascii` and `w:sz/2`, each read from the run, else its paragraph style, else `w:docDefaults`.
- `font_run`, `where: "title"` (pptx): the slide master's title placeholder (`p:ph[@type="title"]`) `a:latin/@typeface` and `a:rPr/@sz` / 100.
- `footer` / `header`: the text of `word/footer*.xml` / `word/header*.xml` (pptx: the `ftr` placeholder of the master), joined, as `redact(text)`; the clear text goes to the file's `vault`.
- `page_size`, `margins`, `orientation` from `w:sectPr` (`w:pgSz`, `w:pgMar`), when present; `numbering_present` when `numbering.xml` holds a `w:num`.
- `export_format`: `{"format": "pdf"}` when a sibling `<stem>.pdf` stands beside the document in the corpus folder.

`mine_corpus`: the files mined are `*.docx` and `*.pptx` that are neither `~$*` lock files nor OLE files; a `.pdf` beside a mined document with the same stem is that document's `export_format` observation; every other file is counted in `corpus.skipped` and never opened by Office. Refusals, in this order, all with the remedy `more documents of one kind`: `corpus mixes .docx and .pptx files`; `corpus has <n> files` (n < `MIN_FILES`, n the files mined); `no invariant reaches the threshold <t> over <n> files` (spec §6: no empty driver). Per `(kind, where)` group, support = files carrying a value / files mined, rounded to 4 places; the best value at or above the threshold is the invariant (`id` from `INVARIANT_IDS`, `where` kept when not null); below it, two values or more make an ambiguity (candidates by support, descending, then by `canon.dumps(value)`), one value alone is purged.

- [ ] **Step 1: Failing tests** (`drivers/vkforge/tests/test_mine.py`)

```python
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from vkforge import __main__ as entry
from vkforge import canon, mine
from vkforge.errors import Refusal
from vkforge.tests import ooxml_fixtures
from vkforge.tests.ooxml_fixtures import FOOTER


class MineTest(unittest.TestCase):
    def corpus(self, n=10, **kw) -> Path:
        d = Path(tempfile.mkdtemp())
        for i in range(n):
            ooxml_fixtures.docx(d / f"c{i}.docx", **kw)
        return d

    def test_ten_contracts_yield_the_house_invariants_at_full_support(self):
        d = self.corpus(10, title_style="Heading1", body_font=("Garamond", 11), footer=FOOTER, pdf=True)
        inv, vault = mine.mine_corpus(d)
        by_id = {i["id"]: i for i in inv["invariants"]}
        self.assertEqual(sorted(by_id), ["body_font", "export_format", "footer_text", "title_style"])
        self.assertEqual(by_id["title_style"], {"id": "title_style", "kind": "paragraph_style", "where": "first",
                                                "value": {"builtin": "Heading1"}, "support": 1.0})
        self.assertEqual(by_id["body_font"], {"id": "body_font", "kind": "font_run", "where": "body",
                                              "value": {"name": "Garamond", "size": 11}, "support": 1.0})
        self.assertEqual(by_id["export_format"]["value"], {"format": "pdf"})
        self.assertTrue(mine.redact(FOOTER).startswith("<redacted:sha256:"))
        self.assertEqual(by_id["footer_text"]["value"], mine.redact(FOOTER))
        self.assertNotIn("where", by_id["footer_text"])
        self.assertEqual(vault, {mine.redact(FOOTER): FOOTER})
        self.assertEqual((inv["corpus"]["files"], inv["corpus"]["skipped"], inv["corpus"]["kind"]), (10, 0, "docx"))
        self.assertEqual((inv["threshold"], inv["ambiguities"]), (0.8, []))

    def test_support_below_threshold_is_an_ambiguity_with_its_candidates(self):
        d = Path(tempfile.mkdtemp())
        for i in range(10):
            ooxml_fixtures.docx(d / f"c{i}.docx", body_font=("Garamond", 11) if i < 7 else ("Calibri", 12))
        inv, _ = mine.mine_corpus(d, threshold=0.8)
        self.assertNotIn("body_font", {i["id"] for i in inv["invariants"]})
        self.assertEqual(inv["ambiguities"], [{"invariant": "body_font", "kind": "font_run", "candidates": [
            {"value": {"name": "Garamond", "size": 11}, "support": 0.7},
            {"value": {"name": "Calibri", "size": 12}, "support": 0.3}]}])

    def test_no_byte_of_the_text_reaches_the_invariants(self):
        d = Path(tempfile.mkdtemp())
        secrets = ["Jean Dupont", "06 12 34 56 78", "jean.dupont@example.org", "1 rue des Lilas"]
        for i in range(6):
            ooxml_fixtures.docx(d / f"c{i}.docx", footer="Pied " + secrets[0], personal=" ".join(secrets))
        out = Path(tempfile.mkdtemp())
        inv, vault = mine.mine_corpus(d)
        mine.write_invariants(inv, vault, out)
        blob = (out / "invariants.json").read_bytes()
        for s in secrets:
            self.assertNotIn(s.encode("utf-8"), blob)
        self.assertEqual(blob, canon.dumps(inv))
        self.assertEqual(json.loads((out / ".vault.json").read_text(encoding="utf-8")),
                         {mine.redact("Pied Jean Dupont"): "Pied Jean Dupont"})
        if os.name == "posix":
            self.assertEqual((out / ".vault.json").stat().st_mode & 0o777, 0o600)

    def test_a_mixed_folder_skips_what_is_not_ooxml_and_never_opens_office(self):
        d = Path(tempfile.mkdtemp())
        for i in range(5):
            ooxml_fixtures.docx(d / f"c{i}.docx")
        (d / "old.doc").write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 64)     # binary Word
        (d / "locked.docx").write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 64)  # encrypted OOXML is an OLE file
        ooxml_fixtures.docx(d / "macro.docm")                                                # macro-enabled: never mined
        (d / "~$c1.docx").write_bytes(b"\0" * 10)                                           # Word's lock file
        (d / "notes.txt").write_text("x")
        inv, _ = mine.mine_corpus(d)
        self.assertEqual((inv["corpus"]["files"], inv["corpus"]["skipped"]), (5, 5))
        for module in ("mine.py", "ooxml.py"):
            text = (Path(mine.__file__).parent / module).read_text(encoding="utf-8")
            self.assertNotIn("comtypes", text, f"{module} never reaches Office")

    def test_fewer_than_five_files_is_refused(self):
        with self.assertRaises(Refusal) as cm:
            mine.mine_corpus(self.corpus(4))
        self.assertEqual(str(cm.exception), "corpus has 4 files; remedy: more documents of one kind")

    def test_a_corpus_of_two_kinds_is_refused(self):
        d = self.corpus(5)
        ooxml_fixtures.pptx(d / "p.pptx")
        with self.assertRaises(Refusal) as cm:
            mine.mine_corpus(d)
        self.assertEqual(str(cm.exception), "corpus mixes .docx and .pptx files; remedy: more documents of one kind")

    def test_a_corpus_where_no_invariant_reaches_the_threshold_is_refused(self):
        d = Path(tempfile.mkdtemp())
        styles = [("Heading1", "Arial"), ("Heading2", "Calibri"), ("Heading3", "Garamond"), ("Title", "Georgia"),
                  ("Subtitle", "Verdana")]
        for i, (style, font) in enumerate(styles):
            ooxml_fixtures.docx(d / f"c{i}.docx", title_style=style, body_font=(font, 10 + i))
        with self.assertRaises(Refusal) as cm:
            mine.mine_corpus(d)
        self.assertEqual(str(cm.exception),
                         "no invariant reaches the threshold 0.8 over 5 files; remedy: more documents of one kind")

    def test_pptx_masters_give_the_title_placeholder_font(self):
        d = Path(tempfile.mkdtemp())
        for i in range(6):
            ooxml_fixtures.pptx(d / f"p{i}.pptx", title_font=("Garamond", 32), footer="Confidentiel")
        inv, _ = mine.mine_corpus(d)
        by_id = {i["id"]: i for i in inv["invariants"]}
        self.assertEqual(by_id["title_font"], {"id": "title_font", "kind": "font_run", "where": "title",
                                               "value": {"name": "Garamond", "size": 32}, "support": 1.0})
        self.assertEqual(by_id["footer_text"]["value"], mine.redact("Confidentiel"))
        self.assertEqual(inv["corpus"]["kind"], "pptx")

    def test_a_paragraph_style_observation_says_where(self):
        doc = ooxml_fixtures.docx(Path(tempfile.mkdtemp()) / "c.docx", title_style="Heading1")
        got = mine.mine_file(doc)
        self.assertEqual(got["kind"], "docx")
        self.assertIn({"kind": "paragraph_style", "where": "first", "value": {"builtin": "Heading1"}}, got["obs"])
        self.assertIn({"kind": "font_run", "where": "body", "value": {"name": "Garamond", "size": 11}}, got["obs"])
        self.assertNotIn("all", [o["where"] for o in got["obs"]], "body paragraphs without w:pStyle: no `all` observation")

    def test_a_french_style_id_maps_to_the_builtin_name(self):
        # Review Focus 2: French Word declares Heading 1 as w:styleId="Titre1", w:name w:val="heading 1".
        doc = ooxml_fixtures.docx(Path(tempfile.mkdtemp()) / "c.docx", title_style="Heading1", style_id="Titre1")
        with zipfile.ZipFile(doc) as zf:
            self.assertIn(b'w:styleId="Titre1"', zf.read("word/styles.xml"))
        got = mine.mine_file(doc)
        self.assertIn({"kind": "paragraph_style", "where": "first", "value": {"builtin": "Heading1"}}, got["obs"])
        self.assertNotIn("Titre", json.dumps(got["obs"]))

    def test_the_schema_hash_covers_the_invariants_list_only(self):
        d = self.corpus(5, footer=FOOTER)
        inv, _ = mine.mine_corpus(d)
        self.assertEqual(inv["corpus"]["schema_sha256"], canon.sha256(inv["invariants"]))   # L-P13
        again, _ = mine.mine_corpus(d, threshold=0.9)
        self.assertEqual(again["corpus"]["schema_sha256"], inv["corpus"]["schema_sha256"], "the threshold is not the schema")

    def test_the_content_hash_names_the_mined_files_and_changes_with_them(self):
        d = self.corpus(5)
        inv, _ = mine.mine_corpus(d)
        self.assertEqual(inv["corpus"]["content_sha256"], mine.content_sha256(d))
        (d / "notes.txt").write_text("x")
        self.assertEqual(mine.content_sha256(d), inv["corpus"]["content_sha256"], "a skipped file is not content")
        ooxml_fixtures.docx(d / "c5.docx")
        self.assertNotEqual(mine.content_sha256(d), inv["corpus"]["content_sha256"])

    def test_the_cli_writes_the_invariants_and_the_vault(self):
        d = self.corpus(5, footer=FOOTER)
        out = Path(tempfile.mkdtemp()) / "out"
        self.assertEqual(entry.main(["mine", "--corpus", str(d), "--out", str(out)]), 0)
        self.assertEqual(sorted(p.name for p in out.iterdir()), [".vault.json", "invariants.json"])

    def test_the_fixture_script_builds_the_rust_tests_corpus(self):
        d = ooxml_fixtures.corpus(Path(tempfile.mkdtemp()) / "corpus", 7, 3)
        self.assertEqual(sorted(p.name for p in d.glob("*.docx")), [f"c{i}.docx" for i in range(10)])
        self.assertEqual(len(list(d.glob("*.pdf"))), 10)
        inv, _ = mine.mine_corpus(d)
        self.assertEqual([a["invariant"] for a in inv["ambiguities"]], ["body_font"])
```

- [ ] **Step 2: Run** `python -I -m unittest discover -s drivers/vkforge/tests -t . -p test_mine.py` → errors (no `ooxml_fixtures`, stub `mine`).

- [ ] **Step 3: Implement** the fixture builder first: a minimal `[Content_Types].xml`, `_rels/.rels`, `docProps/app.xml`, `word/document.xml` with one title paragraph (`w:pPr/w:pStyle w:val=<style_id or title_style>`) and three body paragraphs (no `w:pStyle`; runs with `w:rPr/w:rFonts w:ascii` + `w:sz` = 2 × size; `personal`, when given, in their `w:t`), `word/styles.xml` with a default `Normal` (`w:default="1"`) and the title style (`w:styleId` = `style_id or title_style`, `w:name` from `{"Normal": "Normal", "Title": "Title", "Subtitle": "Subtitle", "HeadingN": "heading N"}`), `word/footer1.xml`/`word/header1.xml` referenced from `word/_rels/document.xml.rels` and `w:sectPr/w:footerReference`/`w:headerReference` when `footer`/`header` is given, and a sibling `<stem>.pdf` (`b"%PDF-1.4 fake\n"`) when `pdf`; `pptx` with `ppt/presentation.xml`, one master whose title placeholder `p:ph type="title"` carries `a:rPr sz="<size × 100>"` + `a:latin typeface`, an `ftr` placeholder with the footer text when given, one layout, one slide; `corpus(dir, garamond, calibri)` writes `c0.docx` … with `title_style="Heading1"`, `footer=FOOTER`, `pdf=True`, Garamond 11 bodies then Calibri 12; the `__main__` block calls `corpus(Path(argv[1]), int(argv[2]), int(argv[3]) if len(argv) > 3 else 0)`. Then `ooxml.py`, `mine_file`, `mine_corpus` (aggregation, threshold, ambiguities, refusals in the order above), `content_sha256`, `write_invariants` (creates `out`; `canon.write`; `os.chmod(vault, 0o600)`), `add_arguments`/`run`.

- [ ] **Step 4: Run** the suite green (14 tests). **Commit** `feat(vkforge): the OOXML miner — invariants with support and place, ambiguities with their candidates, no content in the artefact`

---

### Task 6: The planner — invariants × binding → `plan.json`, ambiguities (Opus)

**Model:** Opus.

**Files:**
- Modify: `drivers/vkforge/plan.py` (Task 0's stub)
- Create: `drivers/vkforge/tests/test_plan.py`; written by this task's test and committed, `drivers/vkforge/tests/fixtures/invariants-word-maison.json` and `drivers/vkforge/tests/fixtures/plan-word-maison.json` (ruling L-P21)

**Interfaces:**
- Consumes: `ontology.load`, `ontology.bind`, the binding shape and `drivers/vkforge/tests/fixtures/binding-word.json` (Task 4); `inventory.APPS`, `inventory.catalogue_from_fixture` (Task 3); `mine.redact` and the invariants/ambiguity shapes (Task 5); `ooxml_fixtures.FOOTER` (Task 5); `canon` (Task 0); the `DriverPlan` rules of `crates/vk-contracts/src/plan.rs` (Task 2), mirrored by `validate`.
- Produces (consumed by Tasks 7, 8, 9):

```python
# drivers/vkforge/plan.py — stage `plan` (STAGES["plan"])
def step(verb: str, **args) -> dict              # {"verb": verb, "args": args}
# (invariant id, steps that produce it, the verify step that checks it), in step order
RULES = [
    ("title_style", lambda v: [step("apply_paragraph_style", selector="first", style=v["builtin"])],
                    lambda v: step("verify_paragraph_style", selector="first", style=v["builtin"])),
    ("body_font",   lambda v: [step("set_font_run", selector="body", font=v)],
                    lambda v: step("verify_font_run", selector="body", font=v)),
    ("title_font",  lambda v: [step("set_font_run", selector="title", font=v)],
                    lambda v: step("verify_font_run", selector="title", font=v)),
    ("header_text", lambda v: [step("set_header", text="$secret.header_text")],
                    lambda v: step("verify_header", text="$secret.header_text")),
    ("footer_text", lambda v: [step("set_footer", text="$secret.footer_text")],
                    lambda v: step("verify_footer", text="$secret.footer_text")),
]
EXPORT = ("export_format", lambda v: [step("export", format=v["format"], path="$arg.pdf")],
                           lambda v: step("verify_pdf", path="$arg.pdf"))   # post id: pdf_exists
EMITTED_VERBS = frozenset({"open", "apply_paragraph_style", "set_font_run", "set_header", "set_footer", "save", "export",
                           "verify_paragraph_style", "verify_font_run", "verify_header", "verify_footer", "verify_pdf"})
ACTION = {"word": "appliquer_style_maison", "powerpoint": "appliquer_style_presentation"}
QUALIFICATION = {"min_passes": 10, "min_rate": 1.0}
def build(invariants: dict, binding: dict, ontology: dict, resolve=None) -> tuple[dict, list]   # (plan, excluded)
def validate(plan: dict) -> None                 # Refusal; the rules of DriverPlan::validate (Task 2), same order
def plan_sha256(path: Path) -> str               # "sha256:" + SHA-256 of the file's bytes (L-P13)
def write_plan(plan: dict, excluded: list, out: Path) -> Path   # out/plan.json and out/plan-excluded.json (canon); returns plan.json
def load_resolver(path: Path)                    # resolver.json → resolve(ambiguity) -> {"value", "resolved_by"} | None
# CLI: vkforge plan --invariants FILE --binding FILE --out DIR [--ontology FILE] [--resolver FILE]
#      prints "plan: <action> <n> steps, post <ids>; ambiguities <k> (<p> purged); excluded <e> -> <out>/plan.json"
```

`resolver.json` (written by the kernel, Task 9): `{"<invariant id>": {"value": <one of the candidates>, "resolved_by": "arch:<arch_id>"}}`, only the ambiguities an arch resolved.

`build`:
1. `app = binding["app"]`, `kind = inventory.APPS[app]["kind"]`; `open` and `save` must be `bound` or `partial`, else `Refusal("verb <v> is unbound on <app>", "fix its projection in drivers/vkforge/ontology/office-document.json")`.
2. The values: every invariant's `value` by id; then each miner ambiguity, `resolve(amb)` if a resolver is given — an answer whose `value` equals one of `amb["candidates"][*]["value"]` is applied as if it were an invariant at threshold and recorded `{"invariant", "resolved_by": <answer's>, "value"}`; no resolver, `None`, or a value outside the candidates → `{"invariant", "resolved_by": "purged"}` (spec §6: never applied in part).
3. For each rule of `RULES`, then `EXPORT`, whose id has a value: its steps and its verify step; if one of their verbs is `unbound` or absent from the binding, the invariant goes to `excluded` (`{"invariant", "why": "verb <v> is unbound on <app>: <binding why>"}`); a `title_style` whose value is `{"custom": …}` is excluded (`"a custom style has no built-in anchor (v1)"`). An id with a value and no rule → `{"invariant": id, "why": "no rule for <id> in plan.RULES (v1)"}`.
4. Steps: `open(path="$arg.document")`, the produce steps in rule order, `save()`, the export steps, then the verify steps in the same order; `post` = the kept ids in that order, `export_format` written `pdf_exists`. No kept rule at all → `Refusal("no invariant can be driven on <app> (excluded: <ids>)", "read plan-excluded.json; vk driver learn --deliberate VERB proposes a binding")`.
5. One action: `ACTION[app]`, `pre = [f"document is .{kind}", "not open elsewhere"]`, `reversible` = every step verb's ontology `reversible`, `note` = `"; ".join(f"{verb} partial ({member})")` over the distinct partial verbs used, absent when none.
6. `family = ontology["family"]`, `ontology_sha256`/`catalogue_sha256` from the binding, `invariants_sha256 = canon.sha256(invariants)`, `ambiguities` in invariant order, `qualification = QUALIFICATION`. The CLI refuses an `invariants.json` whose bytes are not `canon.dumps` of its content (`"invariants.json is not in canonical form"`, remedy `run vkforge mine again`), so `canon.sha256(invariants)` is the file's hash.

- [ ] **Step 1: Failing tests** (`drivers/vkforge/tests/test_plan.py`)

```python
import contextlib
import copy
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from vkforge import __main__ as entry
from vkforge import canon, inventory, mine, ontology, plan
from vkforge.errors import Refusal
from vkforge.tests.ooxml_fixtures import FOOTER

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ARCH = "arch:sha256:" + "a" * 64
TIE = {"invariant": "body_font", "kind": "font_run", "candidates": [
    {"value": {"name": "Calibri", "size": 12}, "support": 0.5},
    {"value": {"name": "Garamond", "size": 11}, "support": 0.5}]}


def house_invariants() -> dict:
    """The house style of the fixture contracts, as Task 5's miner writes it."""
    invariants = [
        {"id": "body_font", "kind": "font_run", "where": "body", "value": {"name": "Garamond", "size": 11}, "support": 1.0},
        {"id": "export_format", "kind": "export_format", "value": {"format": "pdf"}, "support": 1.0},
        {"id": "footer_text", "kind": "footer", "value": mine.redact(FOOTER), "support": 1.0},
        {"id": "title_style", "kind": "paragraph_style", "where": "first", "value": {"builtin": "Heading1"}, "support": 1.0},
    ]
    return {"threshold": 0.8, "invariants": invariants, "ambiguities": [],
            "corpus": {"files": 10, "skipped": 0, "kind": "docx", "content_sha256": "sha256:" + "c" * 64,
                       "schema_sha256": canon.sha256(invariants)}}


def without(inv: dict, ident: str) -> dict:
    inv = copy.deepcopy(inv)
    inv["invariants"] = [i for i in inv["invariants"] if i["id"] != ident]
    inv["corpus"]["schema_sha256"] = canon.sha256(inv["invariants"])
    return inv


class PlanTest(unittest.TestCase):
    def setUp(self):
        self.ont = ontology.load()
        self.binding = json.loads((FIXTURES / "binding-word.json").read_text(encoding="utf-8"))

    def steps(self, p):
        return [(s["verb"], s["args"]) for s in p["actions"][0]["steps"]]

    def test_the_house_style_plan_has_its_steps_in_order(self):
        p, excluded = plan.build(house_invariants(), self.binding, self.ont)
        a = p["actions"][0]
        self.assertEqual(a["name"], "appliquer_style_maison")
        self.assertEqual([v for v, _ in self.steps(p)],
                         ["open", "apply_paragraph_style", "set_font_run", "set_footer", "save", "export",
                          "verify_paragraph_style", "verify_font_run", "verify_footer", "verify_pdf"])
        self.assertEqual(self.steps(p)[0], ("open", {"path": "$arg.document"}))
        self.assertEqual(self.steps(p)[1], ("apply_paragraph_style", {"selector": "first", "style": "Heading1"}))
        self.assertEqual(self.steps(p)[5], ("export", {"format": "pdf", "path": "$arg.pdf"}))
        self.assertEqual(a["post"], ["title_style", "body_font", "footer_text", "pdf_exists"])
        self.assertEqual(a["pre"], ["document is .docx", "not open elsewhere"])
        self.assertIs(a["reversible"], False)
        self.assertNotIn("note", a)
        self.assertEqual((p["family"], p["app"], p["qualification"]),
                         ("office-document", "word", {"min_passes": 10, "min_rate": 1.0}))
        self.assertEqual(p["invariants_sha256"], canon.sha256(house_invariants()))
        self.assertEqual((p["catalogue_sha256"], p["ontology_sha256"]),
                         (self.binding["catalogue_sha256"], self.binding["ontology_sha256"]))
        self.assertEqual(excluded, [])
        plan.validate(p)

    def test_a_secret_is_referenced_never_inlined(self):
        p, _ = plan.build(house_invariants(), self.binding, self.ont)
        self.assertIn(("set_footer", {"text": "$secret.footer_text"}), self.steps(p))
        blob = json.dumps(p, ensure_ascii=False)
        self.assertNotIn(mine.redact(FOOTER), blob)
        self.assertNotIn(FOOTER, blob)

    def test_an_unbound_verb_excludes_its_invariant_and_says_so(self):
        b = copy.deepcopy(self.binding)
        for v in b["verbs"]:
            if v["verb"] == "set_footer":
                v.update(status="unbound", member=None, constants=None, why="Section.Footers matched nothing")
        p, excluded = plan.build(house_invariants(), b, self.ont)
        verbs = [v for v, _ in self.steps(p)]
        self.assertNotIn("set_footer", verbs)
        self.assertNotIn("verify_footer", verbs)
        self.assertNotIn("footer_text", p["actions"][0]["post"])
        self.assertEqual(excluded, [{"invariant": "footer_text",
                                     "why": "verb set_footer is unbound on word: Section.Footers matched nothing"}])

    def test_a_tie_is_an_ambiguity_purged_without_a_resolver(self):
        inv = without(house_invariants(), "body_font")
        inv["ambiguities"] = [TIE]
        p, _ = plan.build(inv, self.binding, self.ont)
        self.assertNotIn("set_font_run", [v for v, _ in self.steps(p)])
        self.assertEqual(p["ambiguities"], [{"invariant": "body_font", "resolved_by": "purged"}])
        plan.validate(p)

    def test_a_resolver_answer_becomes_a_step_traced_to_the_arch(self):
        inv = without(house_invariants(), "body_font")
        inv["ambiguities"] = [TIE]
        garamond = {"name": "Garamond", "size": 11}
        p, _ = plan.build(inv, self.binding, self.ont, resolve=lambda amb: {"value": garamond, "resolved_by": ARCH})
        self.assertIn(("set_font_run", {"selector": "body", "font": garamond}), self.steps(p))
        self.assertIn("body_font", p["actions"][0]["post"])
        self.assertEqual(p["ambiguities"], [{"invariant": "body_font", "resolved_by": ARCH, "value": garamond}])
        plan.validate(p)

    def test_a_resolver_answer_outside_the_candidates_is_purged(self):
        inv = without(house_invariants(), "body_font")
        inv["ambiguities"] = [TIE]
        p, _ = plan.build(inv, self.binding, self.ont,
                          resolve=lambda amb: {"value": {"name": "Comic Sans MS", "size": 40}, "resolved_by": ARCH})
        self.assertEqual(p["ambiguities"], [{"invariant": "body_font", "resolved_by": "purged"}])
        self.assertNotIn("Comic", json.dumps(p))

    def test_the_resolver_file_is_read_by_invariant_id(self):
        path = Path(tempfile.mkdtemp()) / "resolver.json"
        canon.write({"body_font": {"value": {"name": "Garamond", "size": 11}, "resolved_by": ARCH}}, path)
        resolve = plan.load_resolver(path)
        self.assertEqual(resolve(TIE), {"value": {"name": "Garamond", "size": 11}, "resolved_by": ARCH})
        self.assertIsNone(resolve({"invariant": "title_style", "kind": "paragraph_style", "candidates": []}))

    def test_invariants_without_a_rule_are_listed_as_excluded(self):
        inv = house_invariants()
        inv["invariants"].append({"id": "body_style", "kind": "paragraph_style", "where": "all",
                                  "value": {"builtin": "Normal"}, "support": 1.0})
        _, excluded = plan.build(inv, self.binding, self.ont)
        self.assertEqual(excluded, [{"invariant": "body_style", "why": "no rule for body_style in plan.RULES (v1)"}])

    def test_a_corpus_with_nothing_drivable_is_refused(self):
        b = copy.deepcopy(self.binding)
        for v in b["verbs"]:
            if v["verb"] in ("apply_paragraph_style", "set_font_run", "set_footer", "export"):
                v.update(status="unbound", member=None, constants=None, why="test")
        with self.assertRaises(Refusal) as cm:
            plan.build(house_invariants(), b, self.ont)
        self.assertIn("no invariant can be driven on word", str(cm.exception))

    def test_the_same_planner_builds_the_powerpoint_plan(self):
        binding = ontology.bind(self.ont, inventory.catalogue_from_fixture("powerpoint", FIXTURES / "catalogue-powerpoint.json"))
        invariants = [{"id": "title_font", "kind": "font_run", "where": "title", "value": {"name": "Garamond", "size": 32},
                       "support": 1.0}]
        inv = {"threshold": 0.8, "invariants": invariants, "ambiguities": [],
               "corpus": {"files": 6, "skipped": 0, "kind": "pptx", "content_sha256": "sha256:" + "c" * 64,
                          "schema_sha256": canon.sha256(invariants)}}
        p, _ = plan.build(inv, binding, self.ont)
        a = p["actions"][0]
        self.assertEqual((p["app"], a["name"], a["post"]), ("powerpoint", "appliquer_style_presentation", ["title_font"]))
        self.assertEqual(a["pre"], ["document is .pptx", "not open elsewhere"])
        plan.validate(p)

    def test_a_plan_is_held_to_the_contract_rules(self):
        def refuse(edit, needle):
            p, _ = plan.build(house_invariants(), self.binding, self.ont)
            edit(p)
            with self.assertRaises(Refusal) as cm:
                plan.validate(p)
            self.assertIn(needle, str(cm.exception))

        refuse(lambda p: p["actions"].clear(), "actions is empty")
        refuse(lambda p: p["actions"][0]["steps"].pop(0), "first step")
        refuse(lambda p: p["actions"][0]["steps"][1].update(verb="Apply-Style"), "verb")
        refuse(lambda p: p["actions"][0]["steps"][3]["args"].update(text="$secret."), "$secret.")
        refuse(lambda p: p["actions"][0]["post"].pop(), "verify_")
        refuse(lambda p: p["actions"][0]["steps"][1]["args"].update({"from-x": 1}), "from-x")
        refuse(lambda p: p["ambiguities"].append({"invariant": "x", "resolved_by": "purged", "value": 1}), "value")

    def test_plan_sha256_is_the_hash_of_the_file_bytes(self):
        p, excluded = plan.build(house_invariants(), self.binding, self.ont)
        out = Path(tempfile.mkdtemp())
        path = plan.write_plan(p, excluded, out)
        self.assertEqual(path, out / "plan.json")
        self.assertEqual(plan.plan_sha256(path), "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(path.read_bytes(), canon.dumps(p))
        self.assertEqual(json.loads((out / "plan-excluded.json").read_text(encoding="utf-8")), excluded)

    def test_the_cli_reads_the_artefacts_and_refuses_hand_edited_invariants(self):
        work = Path(tempfile.mkdtemp())
        canon.write(house_invariants(), work / "invariants.json")
        out = work / "out"
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            self.assertEqual(entry.main(["plan", "--invariants", str(work / "invariants.json"),
                                         "--binding", str(FIXTURES / "binding-word.json"), "--out", str(out)]), 0)
        self.assertTrue((out / "plan.json").is_file())
        (work / "invariants.json").write_text(json.dumps(house_invariants()), encoding="utf-8")   # not canonical
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(entry.main(["plan", "--invariants", str(work / "invariants.json"),
                                         "--binding", str(FIXTURES / "binding-word.json"), "--out", str(work / "o2")]), 2)
        self.assertIn("vkforge plan: invariants.json is not in canonical form; remedy:", err.getvalue())

    def test_the_fixtures_for_tasks_7_and_8_are_committed(self):
        # Ruling L-P21: written on the first run, compared on every later one.
        inv = house_invariants()
        p, _ = plan.build(inv, self.binding, self.ont)
        for name, obj in (("invariants-word-maison.json", inv), ("plan-word-maison.json", p)):
            path = FIXTURES / name
            if not path.exists():
                canon.write(obj, path)
            self.assertEqual(path.read_bytes(), canon.dumps(obj), f"{name} is stale: delete it, run this test, commit it")
```

- [ ] **Step 2: Run** `python -I -m unittest discover -s drivers/vkforge/tests -t . -p test_plan.py` → errors (stub).

- [ ] **Step 3: Implement** `build`, `validate` (same refusals, same order, as `DriverPlan::validate` of Task 2, so a plan this stage writes is one the kernel parses: Task 9's Rust test checks the real file), `plan_sha256`, `write_plan`, `load_resolver`, `add_arguments`/`run`.

- [ ] **Step 4: Run** the suite green (14 tests; the first run writes the two fixtures). **Commit** `feat(vkforge): the planner — invariants and bound verbs become a typed plan; ambiguities resolved within their candidates or purged` (the two fixtures included).

---

### Task 7: The COM runtime, the fake application, the generator (Opus)

**Model:** Opus.

**Files:**
- Create: `drivers/vkforge/runtime/__init__.py` (empty), `drivers/vkforge/runtime/com.py`, `drivers/vkforge/runtime/fake.py`, `drivers/vkforge/tests/test_generate.py`, `drivers/vkforge/tests/test_runtime.py`
- Modify: `drivers/vkforge/generate.py` (Task 0's stub), `drivers/vkforge/ooxml.py` (`write_parts`, the miner's writer twin)

There is no template file: `generate.py` composes `driver.py` from fixed text and per-step lines (below), with no template engine.

**Interfaces:**
- Consumes: `drivers/vkforge/tests/fixtures/plan-word-maison.json`, `invariants-word-maison.json` (Task 6), `binding-word.json` (Task 4); `plan.build`, `plan.EMITTED_VERBS` (Task 6); `ontology.load` (Task 4); `mine.mine_file`, `mine.mine_corpus`, `mine.redact`, `ooxml_fixtures.docx`, `ooxml_fixtures.FOOTER` (Task 5); `inventory.APPS`, `inventory.office_present`, `inventory.typelib_path` (Task 3); the binding's `application`, `typelib` and `typelib_sha256` (Task 4; L-P22, L-P31); `VK_DRIVER_APPLICATIONS` in the driver's environment (Task 1); `canon`, `Refusal` (Task 0); `vkdriver.hash_dir.{hash_dir, list_files}` and `vkdriver.protocol.{DriverRefusal, serve}` (`drivers/vkdriver/`).
- Produces (consumed by Tasks 8, 9, 10, 12):

```python
# drivers/vkforge/runtime/com.py — the only module that touches COM; vendored as vendor/vkforge_runtime/com.py
# (L-P19). Imports only the stdlib, vkdriver.protocol and, lazily and on Windows only, comtypes. Names no
# application (L-P31): the ProgID, the collection, Open's and Close's arguments and DisplayAlerts are binding["application"].
CALL_DEADLINE_S = 30.0       # one COM call (L-P33)
VERBS: dict[str, Callable]   # verb → fn(app, doc, **args); every verb of plan.EMITTED_VERBS but open; verify_* return bool
def executables() -> set[str]                   # VK_DRIVER_APPLICATIONS, upper-case: the executables the manifest names
def dialogs(exes: set[str]) -> list             # the visible top-level #32770 windows of those processes; [] off Windows
def close_window(hwnd) -> None                  # PostMessageW(hwnd, WM_CLOSE): closed, never answered
def guarded(fn, what: str, deadline_s: float = CALL_DEADLINE_S, find_dialogs=None, close=None)
                                                # fn() on its own thread joined for deadline_s; past it, a dialog box found is
                                                # closed → DriverRefusal("dialog", …), none → DriverRefusal("timeout", …) (L-P33)
def live_typelib_sha256(guid: str) -> str       # the type library file the registry names for guid today, hashed (L-P22)
def probe(binding: dict) -> None                # ≠ binding["typelib_sha256"] → DriverRefusal("precondition",
                                                # "office version changed; remedy: vk driver qualify") (L-P22)
class Application:
    def __init__(self, binding: dict): ...      # GetActiveObject(progid), else CreateObject (the user's own instance, spike
                                                # verdict 3); DisplayAlerts = alerts_off; off Windows, or no such server →
                                                # DriverRefusal("precondition", "<progid> cannot be started: …")
    def probe(self) -> None: ...                # probe(self.binding)
    def open(self, path: str): ...              # <collection>.Open(path, **open); doc.ReadOnly → close it,
                                                # DriverRefusal("precondition", "document opened read-only (locked elsewhere)")
    def call(self, verb: str, doc, **args): ... # guarded VERBS[verb](self, doc, **args), the member and constants from the
                                                # binding ({v["verb"]: v for v in binding["verbs"]}); a built-in style "Heading1"
                                                # → the binding's wdStyleHeading1, never a localised name
    def close(self, doc) -> None: ...           # doc.Close(**close)
    def quit(self) -> None: ...                 # only when this adapter started the instance

# drivers/vkforge/runtime/fake.py — repository only, never vendored (L-P19); reached only through <work>/fake.json
VERBS: dict[str, Callable]   # the same keys as com.VERBS
FAKE_CALL_DEADLINE_S = 1.0   # the guard's deadline when the fake raises its dialog box (L-P33)
class FakeApplication:
    def __init__(self, binding: dict, config: dict | None = None): ...
        # Word .docx only (L-P31): another app → DriverRefusal("precondition", "the fake application implements Word .docx
        # only; remedy: qualify on the real application"); config is <work>/fake.json's content (L-P19), read for its
        # optional "dialog_on": "<verb>" (L-P33)
    def probe(self) -> None: ...                  # no type library behind the fake: nothing to compare (L-P22)
    def open(self, path: str): ...                # a file the OS says is read-only → DriverRefusal("precondition",
                                                  # "document opened read-only (locked elsewhere)"), as the adapter on doc.ReadOnly
    def call(self, verb: str, doc, **args): ...   # verb == dialog_on → self.__dialog__(verb); else edits the parts in memory;
                                                  # save writes them with ooxml.write_parts; export writes "%PDF-1.4 fake\n" +
                                                  # 1 186 bytes; verify_* re-read the saved file with mine.mine_file
    def __dialog__(self, verb: str): ...          # the hook (L-P33): the call blocks behind a box com.guarded closes → "dialog"
    def close(self, doc) -> None: ...
    def quit(self) -> None: ...

# drivers/vkforge/ooxml.py (added)
def write_parts(package: Path, parts: dict[str, bytes]) -> None   # each member written (added when absent), every other member
                                                                   # copied byte for byte, the package replaced with os.replace

# drivers/vkforge/generate.py — stage `generate` (STAGES["generate"])
VKFORGE_VENV = Path(os.environ["LOCALAPPDATA"]) / "vk" / "venvs" / "vkforge" if os.environ.get("LOCALAPPDATA") else None
def render_driver(plan: dict, binding: dict, name: str) -> str
def generate(plan: dict, binding: dict, invariants: dict, vault: dict, out: Path, name: str, venv: Path | None = None) -> None
# CLI: vkforge generate --plan FILE --binding FILE --invariants FILE --vault FILE --out DIR --name NAME [--venv DIR]
#      (--venv defaults to VKFORGE_VENV; prints "generate: <name> <files_sha256> -> <out>")
```

`runtime/com.py`, all of it but the `VERBS` table (Step 3 writes it after this code) — the per-call deadline and the window enumeration (L-P33), the probe (L-P22), the application read from the binding (L-P31):

```python
"""The COM adapter of a learned driver (L-P1): the only module that touches COM. Vendored into every
generated driver as vendor/vkforge_runtime/com.py (L-P19): the stdlib, vkdriver.protocol and, lazily
and on Windows only, comtypes. It names no application (L-P31): what differs between Word and
PowerPoint at this level is binding["application"], the ontology's applications block for the app."""
import ctypes
import hashlib
import os
import sys
import threading
from pathlib import Path

from vkdriver.protocol import DriverRefusal

CALL_DEADLINE_S = 30.0   # one COM call (L-P33); a qualification pass has 120 s (qualify.PASS_DEADLINE_S)
DIALOG_CLASS = "#32770"  # the Win32 dialog box class: a sign-in, an overwrite, a repair prompt
WM_CLOSE = 0x0010
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def executables() -> set:
    """The executables this driver drives: its manifest's `applications`, which the host puts in
    VK_DRIVER_APPLICATIONS (Task 1) and `vkforge qualify` too (Task 8)."""
    return {e.strip().upper() for e in os.environ.get("VK_DRIVER_APPLICATIONS", "").split(",") if e.strip()}


def dialogs(exes: set) -> list:
    """The visible top-level dialog boxes (class #32770) of the processes running one of `exes`."""
    if sys.platform != "win32" or not exes:
        return []
    from ctypes import wintypes

    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.EnumWindows.argtypes = (enum_proc, wintypes.LPARAM)
    user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                    ctypes.POINTER(wintypes.DWORD))
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    found = []

    def image_of(pid: int) -> str:
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ""
        try:
            buf, size = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
            ok = kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
            return Path(buf.value).name.upper() if ok else ""
        finally:
            kernel32.CloseHandle(handle)

    def each(hwnd, _lparam):
        name = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, name, 64)
        if name.value == DIALOG_CLASS and user32.IsWindowVisible(hwnd):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if image_of(pid.value) in exes:
                found.append(hwnd)
        return True

    user32.EnumWindows(enum_proc(each), 0)
    return found


def close_window(hwnd) -> None:
    """WM_CLOSE, posted: the box's own close, never one of its answers (spec §6)."""
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


def guarded(fn, what: str, deadline_s: float = CALL_DEADLINE_S, find_dialogs=None, close=None):
    """fn() on its own thread, joined for `deadline_s` (L-P33). Past it, the application's dialog boxes
    are closed and the action refuses `dialog`, the box never answered (spec §6); with none open it
    refuses `timeout`. A thread that finds comtypes loaded joins the multithreaded apartment that
    Application keeps alive, so the proxies one call's thread made serve the next."""
    find_dialogs = find_dialogs or (lambda: dialogs(executables()))
    close = close or close_window
    box = {}

    def run():
        com = sys.modules.get("comtypes")    # loaded by Application only: the fake's hook runs without COM
        if com is not None:
            com.CoInitializeEx(com.COINIT_MULTITHREADED)
        try:
            box["value"] = fn()
        except BaseException as e:  # noqa: BLE001 — raised again on the caller's thread
            box["error"] = e
        finally:
            if com is not None:
                com.CoUninitialize()

    worker = threading.Thread(target=run, name=f"com {what}", daemon=True)
    worker.start()
    worker.join(deadline_s)
    if worker.is_alive():
        boxes = find_dialogs()
        for hwnd in boxes:
            close(hwnd)
        if boxes:
            raise DriverRefusal("dialog", f"{what}: a dialog box of the application held the call; "
                                          "closed, never answered")
        raise DriverRefusal("timeout", f"{what}: no answer within {deadline_s:g} s")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def live_typelib_sha256(guid: str) -> str:
    """The type library file the registry names for `guid` today — HKCR\\TypeLib\\{guid}, its highest
    version (hexadecimal major.minor), 0\\win64 then 0\\win32, the path inventory read — hashed as the
    catalogue's `typelib_sha256` is (L-P22)."""
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"TypeLib\{guid}") as key:
                versions = [winreg.EnumKey(key, i) for i in range(winreg.QueryInfoKey(key)[0])]
            version = max(versions, key=lambda v: tuple(int(p, 16) for p in v.split(".")))
            for arch in ("win64", "win32"):
                try:
                    path = winreg.QueryValue(winreg.HKEY_CLASSES_ROOT, rf"TypeLib\{guid}\{version}\0\{arch}")
                    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()
                except OSError:
                    continue
        except (OSError, ValueError):
            pass
    raise DriverRefusal("precondition", f"type library {guid} is not registered; remedy: install Microsoft 365 desktop")


def probe(binding: dict) -> None:
    """L-P22: the type library on this node is the one the driver was learned on (binding.json is in its
    driver_id); else every action refuses before its document is opened."""
    if live_typelib_sha256(binding["typelib"]["guid"]) != binding["typelib_sha256"]:
        raise DriverRefusal("precondition", "office version changed; remedy: vk driver qualify")


class Application:
    """The application binding["application"] names, the user's own instance (spike verdict 3)."""

    def __init__(self, binding: dict):
        self.binding, self.spec = binding, binding["application"]
        self.verbs = {v["verb"]: v for v in binding["verbs"]}
        progid = self.spec["progid"]
        if sys.platform != "win32":
            raise DriverRefusal("precondition", f"{progid} cannot be started: not Windows")
        cookie = ctypes.c_void_p()
        ctypes.oledll.ole32.CoIncrementMTAUsage(ctypes.byref(cookie))   # the calls' apartment lives with the driver
        import comtypes
        import comtypes.client

        def start():
            try:
                return comtypes.client.GetActiveObject(progid), False
            except (OSError, comtypes.COMError):
                return comtypes.client.CreateObject(progid), True

        try:
            self.app, self.started = guarded(start, f"start {progid}")
            guarded(lambda: setattr(self.app, "DisplayAlerts", self.spec["alerts_off"]), "DisplayAlerts")
        except (OSError, comtypes.COMError) as e:
            raise DriverRefusal("precondition", f"{progid} cannot be started: {e}") from e

    def probe(self) -> None:
        probe(self.binding)

    def open(self, path: str):
        doc = guarded(lambda: getattr(self.app, self.spec["collection"]).Open(path, **self.spec["open"]), "open")
        if guarded(lambda: bool(doc.ReadOnly), "open"):
            self.close(doc)
            raise DriverRefusal("precondition", "document opened read-only (locked elsewhere)")
        return doc

    def call(self, verb: str, doc, **args):
        return guarded(lambda: VERBS[verb](self, doc, **args), verb)

    def close(self, doc) -> None:
        guarded(lambda: doc.Close(**self.spec["close"]), "close")

    def quit(self) -> None:
        if self.started:
            guarded(lambda: self.app.Quit(), "quit")
```

`runtime/fake.py`, the parts the dialog hook and the probe add (the verb table, `open`, `close`, `quit` are Step 3's):

```python
import threading

from vkdriver.protocol import DriverRefusal
from vkforge.runtime import com    # the repository's adapter: its guard is the one the real driver runs

FAKE_CALL_DEADLINE_S = 1.0   # the guard's deadline when the fake raises its dialog box: a test waits 1 s, not 30


class FakeApplication:
    def __init__(self, binding: dict, config: dict | None = None):
        if binding["app"] != "word":
            raise DriverRefusal("precondition", "the fake application implements Word .docx only; "
                                                "remedy: qualify on the real application")
        self.binding = binding
        self.verbs = {v["verb"]: v for v in binding["verbs"]}
        self.dialog_on = (config or {}).get("dialog_on")   # <work>/fake.json's, handed over by the driver (L-P33)

    def probe(self) -> None:
        """No type library behind the fake: nothing to compare (L-P22)."""

    def call(self, verb: str, doc, **args):
        if verb == self.dialog_on:
            return self.__dialog__(verb)
        return VERBS[verb](self, doc, **args)

    def __dialog__(self, verb: str):
        """fake.json's `dialog_on` (L-P33): the call blocks behind a modal box, as Word's does behind a
        sign-in or an overwrite prompt, until the adapter's own guard closes the box — so the refusal
        `dialog` comes from com.guarded, the code the real driver runs."""
        closed = threading.Event()
        return com.guarded(lambda: closed.wait(60), verb, deadline_s=FAKE_CALL_DEADLINE_S,
                           find_dialogs=lambda: [] if closed.is_set() else ["the fake's dialog box"],
                           close=lambda hwnd: closed.set())
```

`generate` refuses, before writing anything: a non-empty `out`; a `name` that is not 1 to 32 characters of `a-z`, `0-9`, `-`; a step verb (other than `open`) absent from `com.VERBS` (`"verb <v> has no runtime implementation"`, remedy `implement it in drivers/vkforge/runtime/com.py and fake.py`); an args key that is not a Python identifier or is a keyword; a `$secret.<id>` it cannot resolve (`"secret <id> has no clear value in the vault"`, remedy `learn again: the vault is deleted after generation`) — the token is `invariants`' value of `<id>`, else the plan ambiguity's `value` for `<id>`, and the clear text is `vault[token]`; a `venv` that has no `Scripts/python.exe` (`"no virtualenv at <dir>"`, remedy `powershell -ExecutionPolicy Bypass -File drivers\vkforge\setup.ps1`). Then it writes, every file with `\n` line endings:

- `driver.py` = `render_driver(plan, binding, name)`;
- `binding.json` (`canon.dumps(binding)`): the verbs, the `application` the adapter opens (L-P31) and the `typelib`/`typelib_sha256` its probe checks (L-P22) — inside `hash_dir`, hence in `driver_id`;
- `secrets.json` = `canon.dumps({id: clear})` for the ids the plan references, only when it references one (mode `0o600` where the OS honours it): a driver file, so hashed into `files_sha256` — a different footer is a different driver — and in clear on the node only (spec §5.5);
- `vendor/vkdriver/` — every `*.py` of `Path(vkdriver.protocol.__file__).parent` but `test_*.py`, byte for byte, sorted (L-P6); `vendor/vkforge_runtime/__init__.py` and `vendor/vkforge_runtime/com.py`, byte copies of `runtime/__init__.py` and `runtime/com.py` (L-P19); never `fake.py`, never a `pack.json` (L-P9);
- `.venv`, a junction (Windows, `_winapi.CreateJunction`) or a symlink to `venv`, when one is given (L-P12): outside the fingerprint, as every `.venv` is;
- `manifest.json` last (`canon.dumps`): `name`, `kind: com`, `program: {runtime: "python-com", command: [".venv/Scripts/python.exe", "-I", "driver.py"], files_sha256: hash_dir(out), browser_channel: null, headless: true}`, `hosts: []`, `applications: [inventory.APPS[plan["app"]]["exe"]]`, one action per plan action (`name`, `reversible` from the plan, `args`: `{"type": "object", "properties": {<every $arg.x the steps reference>: {"type": "string"}}, "required": [the same, sorted], "additionalProperties": false}`, `evidence: ["post.json"]`, `to_register: false`), `min_interval_ms: 0`, `max_action_ms: 120000`, `clearance: {max_scope: "business", third_party_allowed: false}`, `governed: false`.

`render_driver` for the fixture plan gives exactly this (`<…>` are the plan's four hashes; argument values are `repr` of the plan's values with dict keys sorted, keyword arguments in key order, `$arg.x` → `args["x"]`, `$secret.id` → `SECRETS["id"]`; the `open` step's `path` names the document argument; the `verify_*` steps fill `post` in the order of the action's `post`):

```python
"""word-maison: driver learned by vkforge, no timestamp.

ontology_sha256   <ontology_sha256>
catalogue_sha256  <catalogue_sha256>
invariants_sha256 <invariants_sha256>
plan_sha256       <plan_sha256>
"""
import hashlib
import json
import os
import sys
from pathlib import Path

DRIVER_DIR = Path(__file__).resolve().parent
VENDOR_DIR = DRIVER_DIR / "vendor"


def _first_dir(candidates: tuple, needs: tuple) -> Path:
    """The first directory holding every file of `needs`; else the last (the repository), so the
    import error names the development path."""
    for d in candidates:
        if all((d / n).is_file() for n in needs):
            return d
    return candidates[-1]


# vkdriver vendor-first (L-P6): the generator copied drivers/vkdriver into vendor/, so the driver runs
# alone wherever it was written; the repository layout (<repo>/drivers/vkdriver, the driver at
# drivers/office-com/<name>/) only without vendor/. Never one directory up. The COM adapter is always
# vendored (L-P19).
VKDRIVER_DIR = _first_dir((VENDOR_DIR / "vkdriver", DRIVER_DIR.parents[1] / "vkdriver"), ("protocol.py", "hash_dir.py"))
sys.path[:0] = [str(VKDRIVER_DIR.parent), str(VENDOR_DIR)]
from vkdriver.protocol import DriverRefusal, serve  # noqa: E402
from vkforge_runtime.com import Application  # noqa: E402

NAME = 'word-maison'
VERSION = '<plan_sha256>'
BINDING = json.loads((DRIVER_DIR / "binding.json").read_text(encoding="utf-8"))
SECRETS = (json.loads((DRIVER_DIR / "secrets.json").read_text(encoding="utf-8"))
           if (DRIVER_DIR / "secrets.json").is_file() else {})
_app = None


def app():
    """The COM adapter; its fake twin only when `vkforge qualify --fake` put fake.json in this run's
    work directory (L-P19): never an environment switch, never in a mount's work directory."""
    global _app
    if _app is None:
        fake = Path(os.environ.get("VK_DRIVER_WORK_DIR") or os.getcwd()) / "fake.json"
        if fake.is_file():
            config = json.loads(fake.read_text(encoding="utf-8"))
            sys.path.insert(0, config["vkforge_root"])
            from vkforge.runtime.fake import FakeApplication
            _app = FakeApplication(BINDING, config)
        else:
            _app = Application(BINDING)
    return _app


def _sha(path) -> str:
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


def appliquer_style_maison(args: dict, evidence_dir: str):
    doc_path = args["document"]
    if not Path(doc_path).is_file() or Path(doc_path).suffix.lower() != ".docx":
        raise DriverRefusal("precondition", "not a .docx file")
    app().probe()   # the type library this driver was learned on (L-P22), else "office version changed"
    before = _sha(doc_path)
    doc = app().open(doc_path)
    try:
        app().call('apply_paragraph_style', doc, selector='first', style='Heading1')
        app().call('set_font_run', doc, font={'name': 'Garamond', 'size': 11}, selector='body')
        app().call('set_footer', doc, text=SECRETS["footer_text"])
        app().call('save', doc)
        app().call('export', doc, format='pdf', path=args["pdf"])
        post = {
            'title_style': bool(app().call('verify_paragraph_style', doc, selector='first', style='Heading1')),
            'body_font': bool(app().call('verify_font_run', doc, font={'name': 'Garamond', 'size': 11}, selector='body')),
            'footer_text': bool(app().call('verify_footer', doc, text=SECRETS["footer_text"])),
            'pdf_exists': bool(app().call('verify_pdf', doc, path=args["pdf"])),
        }
    finally:
        app().close(doc)
    ev = Path(evidence_dir, "post.json")
    ev.write_text(json.dumps({"after_sha256": _sha(doc_path), "before_sha256": before, "post": post}, sort_keys=True),
                  encoding="utf-8")
    files = [{"name": "post.json", "mime": "application/json", "path": str(ev)}]
    if not all(post.values()):
        raise DriverRefusal("precondition", "postconditions not all held: " + ",".join(k for k, v in post.items() if not v),
                            evidence=files)
    return {"post": post, "text": "appliquer_style_maison ok"}, files


ACTIONS = {'appliquer_style_maison': appliquer_style_maison}

if __name__ == "__main__":
    serve(NAME, VERSION, ACTIONS)
```

(`.docx` is `inventory.APPS[plan["app"]]["kind"]`, `VERSION` the plan's `plan_sha256`; the ProgID is not in `driver.py`: the adapter reads it from `binding.json`'s `application`, L-P31. `binding.json` also carries the `typelib_sha256` that `app().probe()` checks, L-P22.)

- [ ] **Step 1: Failing tests**

`drivers/vkforge/tests/test_generate.py`:

```python
import copy
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from vkdriver.hash_dir import hash_dir, list_files
from vkdriver.protocol import DriverRefusal
from vkforge import __main__ as entry
from vkforge import canon, generate, inventory, mine, ontology, plan
from vkforge.errors import Refusal
from vkforge.runtime.fake import FakeApplication
from vkforge.tests import ooxml_fixtures
from vkforge.tests.ooxml_fixtures import FOOTER

FIXTURES = Path(__file__).resolve().parent / "fixtures"
DRIVERS = Path(__file__).resolve().parents[2]
GENERATED = ("binding.json", "driver.py", "manifest.json", "secrets.json",
             "vendor/vkdriver/__init__.py", "vendor/vkdriver/hash_dir.py", "vendor/vkdriver/protocol.py",
             "vendor/vkforge_runtime/__init__.py", "vendor/vkforge_runtime/com.py")


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def fake_venv() -> Path:
    venv = Path(tempfile.mkdtemp())
    (venv / "Scripts").mkdir()
    (venv / "Scripts" / "python.exe").write_bytes(b"MZ")
    return venv


class Session:
    """The generated driver as a host runs it: from its own directory, in a work directory, protocol 1 on stdio."""

    def __init__(self, driver_dir: Path, fake: bool, extra_env=None, fake_extra=None):
        self.work = Path(tempfile.mkdtemp())
        if fake:
            canon.write({"vkforge_root": str(DRIVERS), **(fake_extra or {})}, self.work / "fake.json")
        env = {**os.environ, "VK_DRIVER_WORK_DIR": str(self.work), "VK_DRIVER_PROFILE_DIR": tempfile.mkdtemp(),
               **(extra_env or {})}
        self.p = subprocess.Popen([sys.executable, "-I", str(driver_dir / "driver.py")], cwd=self.work, env=env,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8")
        self.hello = json.loads(self.p.stdout.readline())
        self.n = 0

    def act(self, action: str, args: dict) -> dict:
        self.n += 1
        request = {"id": self.n, "action": action, "args": args, "deadline_ms": 60000, "evidence_dir": tempfile.mkdtemp()}
        self.p.stdin.write(json.dumps(request) + "\n")
        self.p.stdin.flush()
        return json.loads(self.p.stdout.readline())

    def close(self):
        self.p.stdin.close()
        self.p.wait(timeout=10)


class GenerateTest(unittest.TestCase):
    def setUp(self):
        self.plan = fixture("plan-word-maison.json")
        self.binding = fixture("binding-word.json")
        self.invariants = fixture("invariants-word-maison.json")
        self.vault = {mine.redact(FOOTER): FOOTER}

    def gen(self, **kw) -> Path:
        out = Path(tempfile.mkdtemp()) / "word-maison"
        generate.generate(self.plan, self.binding, self.invariants, self.vault, out, name="word-maison", **kw)
        return out

    def test_generation_is_byte_reproducible_in_two_directories(self):
        a, b = self.gen(), self.gen()
        for f in GENERATED:
            self.assertEqual((a / f).read_bytes(), (b / f).read_bytes(), f)
        m = json.loads((a / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(m["program"]["files_sha256"], hash_dir(str(a)))
        self.assertEqual(sorted(rel for rel, _ in list_files(str(a))), [f for f in GENERATED if f != "manifest.json"])
        self.assertFalse((a / "pack.json").exists(), "no pack.json: the driver vendors its own (L-P9)")
        self.assertFalse((a / ".venv").exists())

    def test_the_manifest_is_a_com_driver_with_the_arguments_its_steps_reference(self):
        m = json.loads((self.gen() / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual((m["name"], m["kind"], m["hosts"], m["applications"]), ("word-maison", "com", [], ["WINWORD.EXE"]))
        self.assertEqual((m["program"]["runtime"], m["program"]["command"]),
                         ("python-com", [".venv/Scripts/python.exe", "-I", "driver.py"]))
        a = m["actions"][0]
        self.assertEqual((a["name"], a["args"]["required"], a["evidence"], a["reversible"], a["to_register"]),
                         ("appliquer_style_maison", ["document", "pdf"], ["post.json"], False, False))
        self.assertEqual(m["clearance"], {"max_scope": "business", "third_party_allowed": False})

    def test_the_generated_driver_imports_vkdriver_from_its_own_vendor(self):
        out = self.gen()                                       # a temp dir: no repository around it
        for f in ("__init__.py", "hash_dir.py", "protocol.py"):
            self.assertEqual((out / "vendor/vkdriver" / f).read_bytes(), (DRIVERS / "vkdriver" / f).read_bytes(), f)
        for f in ("__init__.py", "com.py"):
            self.assertEqual((out / "vendor/vkforge_runtime" / f).read_bytes(),
                             (DRIVERS / "vkforge/runtime" / f).read_bytes(), f)
        self.assertFalse((out / "vendor/vkdriver/test_protocol.py").exists())
        self.assertFalse((out / "vendor/vkdriver/__pycache__").exists())
        self.assertFalse((out / "vendor/vkforge_runtime/fake.py").exists(), "the fake stays in the repository (L-P19)")
        probe = ("import runpy, sys; runpy.run_path(sys.argv[1], run_name='probe'); "
                 "print(sys.modules['vkdriver.protocol'].__file__); print(sys.modules['vkforge_runtime.com'].__file__)")
        got = subprocess.run([sys.executable, "-I", "-c", probe, str(out / "driver.py")], cwd=tempfile.gettempdir(),
                             capture_output=True, text=True)
        self.assertEqual(got.returncode, 0, got.stderr)
        protocol, com = got.stdout.splitlines()
        self.assertEqual(Path(protocol).resolve().parent, (out / "vendor" / "vkdriver").resolve())
        self.assertEqual(Path(com).resolve().parent, (out / "vendor" / "vkforge_runtime").resolve())

    def test_no_localised_name_and_no_corpus_text_in_the_driver(self):
        # Review Focus 2, from a French corpus: Heading 1 declared as w:styleId="Titre1".
        corpus = Path(tempfile.mkdtemp())
        for i in range(5):
            ooxml_fixtures.docx(corpus / f"c{i}.docx", title_style="Heading1", style_id="Titre1", footer=FOOTER, pdf=True)
        inv, vault = mine.mine_corpus(corpus)
        p, _ = plan.build(inv, self.binding, ontology.load())
        out = Path(tempfile.mkdtemp()) / "word-maison"
        generate.generate(p, self.binding, inv, vault, out, name="word-maison")
        for rel, path in list_files(str(out)) + [("manifest.json", str(out / "manifest.json"))]:
            data = Path(path).read_bytes()
            self.assertNotIn(b"Titre", data, rel)
            if rel != "secrets.json":
                self.assertNotIn(FOOTER.encode("utf-8"), data, rel)   # the clear footer lives in secrets.json alone
        code = (out / "driver.py").read_bytes()
        self.assertIn(b'SECRETS["footer_text"]', code)
        self.assertIn(b"style='Heading1'", code)
        self.assertEqual(json.loads((out / "secrets.json").read_text(encoding="utf-8")), {"footer_text": FOOTER})

    def test_the_generated_driver_speaks_protocol_1_against_the_fake(self):
        doc = ooxml_fixtures.docx(Path(tempfile.mkdtemp()) / "c.docx", title_style="Normal", body_font=("Calibri", 12))
        s = Session(self.gen(), fake=True)
        self.assertEqual((s.hello["hello"]["protocol"], s.hello["hello"]["actions"]), (1, ["appliquer_style_maison"]))
        ans = s.act("appliquer_style_maison", {"document": str(doc), "pdf": str(doc.with_suffix(".pdf"))})
        s.close()
        self.assertTrue(ans["ok"], ans)
        self.assertEqual(ans["result"]["post"],
                         {"title_style": True, "body_font": True, "footer_text": True, "pdf_exists": True})
        obs = mine.mine_file(doc)["obs"]
        self.assertIn({"kind": "paragraph_style", "where": "first", "value": {"builtin": "Heading1"}}, obs)
        self.assertIn({"kind": "font_run", "where": "body", "value": {"name": "Garamond", "size": 11}}, obs)
        self.assertIn({"kind": "footer", "where": None, "value": mine.redact(FOOTER)}, obs)
        self.assertTrue(doc.with_suffix(".pdf").read_bytes().startswith(b"%PDF"))

    def test_a_read_only_document_is_a_precondition_refusal(self):
        # Review Focus 4: a document the user's Word holds opens read-only; adapter and fake refuse, never hang.
        doc = ooxml_fixtures.docx(Path(tempfile.mkdtemp()) / "c.docx")
        os.chmod(doc, stat.S_IREAD)
        try:
            with self.assertRaises(DriverRefusal) as cm:
                FakeApplication(self.binding).open(str(doc))
            self.assertEqual(cm.exception.reason, "precondition")
            self.assertIn("read-only", cm.exception.detail)
            s = Session(self.gen(), fake=True)
            started = time.monotonic()
            ans = s.act("appliquer_style_maison", {"document": str(doc), "pdf": str(doc.with_suffix(".pdf"))})
            s.close()
            self.assertLess(time.monotonic() - started, 30)
            self.assertEqual((ans["ok"], ans["reason"]), (False, "precondition"))
            self.assertFalse(doc.with_suffix(".pdf").exists())
        finally:
            os.chmod(doc, stat.S_IREAD | stat.S_IWRITE)

    def test_a_surviving_dialog_is_a_dialog_refusal(self):
        # Spec §6, L-P33: a modal box raised mid-action is closed, never answered, and the action refuses
        # `dialog`; fake.json's "dialog_on" makes the fake raise one on `save`, through com.guarded itself.
        doc = ooxml_fixtures.docx(Path(tempfile.mkdtemp()) / "c.docx", title_style="Normal", body_font=("Calibri", 12))
        before = doc.read_bytes()
        fake = FakeApplication(self.binding, {"dialog_on": "save"})
        opened = fake.open(str(doc))
        try:
            with self.assertRaises(DriverRefusal) as cm:
                fake.call("save", opened)
        finally:
            fake.close(opened)
        self.assertEqual(cm.exception.reason, "dialog")
        self.assertIn("never answered", cm.exception.detail)
        s = Session(self.gen(), fake=True, fake_extra={"dialog_on": "save"})
        started = time.monotonic()
        ans = s.act("appliquer_style_maison", {"document": str(doc), "pdf": str(doc.with_suffix(".pdf"))})
        s.close()
        self.assertLess(time.monotonic() - started, 30, "the guard's deadline, not the host's")
        self.assertEqual((ans["ok"], ans["reason"]), (False, "dialog"), ans)
        self.assertEqual(doc.read_bytes(), before, "nothing saved behind the box")
        self.assertFalse(doc.with_suffix(".pdf").exists())

    @unittest.skipIf(inventory.office_present("word"), "Word is installed here: the adapter would really run")
    def test_without_fake_json_the_driver_never_reaches_the_fake(self):
        # L-P19: no <work>/fake.json, no fake — and no environment variable can say otherwise.
        doc = ooxml_fixtures.docx(Path(tempfile.mkdtemp()) / "c.docx", title_style="Normal")
        before = doc.read_bytes()
        s = Session(self.gen(), fake=False, extra_env={"VKFORGE_FAKE": "1"})
        ans = s.act("appliquer_style_maison", {"document": str(doc), "pdf": str(doc.with_suffix(".pdf"))})
        s.close()
        self.assertEqual((ans["ok"], ans["reason"]), (False, "precondition"), ans)   # no COM server here
        self.assertEqual(doc.read_bytes(), before)

    def test_a_secret_missing_from_the_vault_is_refused(self):
        with self.assertRaises(Refusal) as cm:
            generate.generate(self.plan, self.binding, self.invariants, {}, Path(tempfile.mkdtemp()) / "x", name="word-maison")
        self.assertIn("secret footer_text has no clear value in the vault", str(cm.exception))

    def test_a_plan_verb_without_a_runtime_implementation_is_refused(self):
        p = copy.deepcopy(self.plan)
        p["actions"][0]["steps"].insert(1, {"verb": "set_table_style", "args": {}})
        with self.assertRaises(Refusal) as cm:
            generate.generate(p, self.binding, self.invariants, self.vault, Path(tempfile.mkdtemp()) / "x", name="word-maison")
        self.assertIn("verb set_table_style has no runtime implementation", str(cm.exception))

    def test_a_venv_is_linked_outside_the_fingerprint(self):
        plain, linked = self.gen(), self.gen(venv=fake_venv())
        self.assertTrue((linked / ".venv" / "Scripts" / "python.exe").is_file())
        files_sha256 = lambda d: json.loads((d / "manifest.json").read_text(encoding="utf-8"))["program"]["files_sha256"]
        self.assertEqual(files_sha256(plain), files_sha256(linked))
        with self.assertRaises(Refusal) as cm:
            self.gen(venv=Path(tempfile.mkdtemp()) / "absent")
        self.assertIn("no virtualenv at", str(cm.exception))

    def test_the_cli_generates_from_the_artefacts(self):
        work = Path(tempfile.mkdtemp())
        canon.write(self.vault, work / ".vault.json")
        out = work / "driver"
        code = entry.main(["generate", "--plan", str(FIXTURES / "plan-word-maison.json"),
                           "--binding", str(FIXTURES / "binding-word.json"),
                           "--invariants", str(FIXTURES / "invariants-word-maison.json"),
                           "--vault", str(work / ".vault.json"), "--out", str(out), "--name", "word-maison",
                           "--venv", str(fake_venv())])
        self.assertEqual(code, 0)
        self.assertTrue((out / "driver.py").is_file() and (out / ".venv").exists())
```

`drivers/vkforge/tests/test_runtime.py`:

```python
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vkdriver.protocol import DriverRefusal
from vkforge import canon, inventory, mine, plan
from vkforge.runtime import com, fake
from vkforge.runtime.fake import FakeApplication
from vkforge.tests import ooxml_fixtures
from vkforge.tests.ooxml_fixtures import FOOTER

FIXTURES = Path(__file__).resolve().parent / "fixtures"
HOUSE = [("apply_paragraph_style", {"selector": "first", "style": "Heading1"}),
         ("set_font_run", {"selector": "body", "font": {"name": "Garamond", "size": 11}}),
         ("set_footer", {"text": FOOTER}),
         ("save", {})]
CHECKS = [("verify_paragraph_style", {"selector": "first", "style": "Heading1"}),
          ("verify_font_run", {"selector": "body", "font": {"name": "Garamond", "size": 11}}),
          ("verify_footer", {"text": FOOTER})]


class RuntimeTest(unittest.TestCase):
    def setUp(self):
        self.binding = json.loads((FIXTURES / "binding-word.json").read_text(encoding="utf-8"))

    def house_style(self, app, doc_path: Path):
        doc = app.open(str(doc_path))
        try:
            for verb, args in HOUSE:
                app.call(verb, doc, **args)
            app.call("export", doc, format="pdf", path=str(doc_path.with_suffix(".pdf")))
            checks = [bool(app.call(verb, doc, **args)) for verb, args in CHECKS]
            checks.append(bool(app.call("verify_pdf", doc, path=str(doc_path.with_suffix(".pdf")))))
            wrong = bool(app.call("verify_footer", doc, text="un autre pied"))
        finally:
            app.close(doc)
        return checks, wrong

    def test_the_fake_and_the_adapter_share_one_verb_table(self):
        self.assertEqual(set(com.VERBS), set(fake.VERBS))
        self.assertLessEqual(plan.EMITTED_VERBS - {"open"}, set(com.VERBS), "every verb a plan can emit has a runtime")

    def test_the_fake_applies_the_house_style_and_its_checks_read_it_back(self):
        doc = ooxml_fixtures.docx(Path(tempfile.mkdtemp()) / "c.docx", title_style="Normal", body_font=("Calibri", 12))
        checks, wrong = self.house_style(FakeApplication(self.binding), doc)
        self.assertEqual((checks, wrong), ([True, True, True, True], False))
        obs = mine.mine_file(doc)["obs"]
        self.assertIn({"kind": "font_run", "where": "body", "value": {"name": "Garamond", "size": 11}}, obs)

    def test_a_changed_type_library_is_an_office_version_refusal(self):
        # L-P22: the binding carries the hash of the type library the driver was learned on; another one on
        # the node (Office updated) refuses before any document is opened. No Office needed: the registry
        # read is replaced, the comparison is the adapter's own.
        self.assertRegex(self.binding["typelib_sha256"], r"^sha256:[0-9a-f]{64}$")
        with mock.patch.object(com, "live_typelib_sha256", return_value=self.binding["typelib_sha256"]) as live:
            com.probe(self.binding)
        live.assert_called_once_with(self.binding["typelib"]["guid"])
        with mock.patch.object(com, "live_typelib_sha256", return_value="sha256:" + "0" * 64):
            with self.assertRaises(DriverRefusal) as cm:
                com.probe(self.binding)
        self.assertEqual((cm.exception.reason, cm.exception.detail),
                         ("precondition", "office version changed; remedy: vk driver qualify"))
        self.assertIsNone(FakeApplication(self.binding).probe(), "no type library behind the fake")

    @unittest.skipUnless(sys.platform == "win32" and inventory.office_present("word"), "Word not installed")
    def test_the_adapter_applies_the_house_style_on_the_real_word(self):
        doc = ooxml_fixtures.docx(Path(tempfile.mkdtemp()) / "c.docx", title_style="Normal", body_font=("Calibri", 12))
        guid = inventory.APPS["word"]["typelib"]
        self.assertEqual(com.live_typelib_sha256(guid), canon.sha256_file(inventory.typelib_path(guid)),
                         "the probe reads the file inventory hashed (L-P22)")
        app = com.Application(self.binding)
        try:
            checks, wrong = self.house_style(app, doc)
        finally:
            app.quit()
        self.assertEqual((checks, wrong), ([True, True, True, True], False))
        obs = mine.mine_file(doc)["obs"]
        self.assertIn({"kind": "paragraph_style", "where": "first", "value": {"builtin": "Heading1"}}, obs)
```

- [ ] **Step 2: Run** `python -I -m unittest discover -s drivers/vkforge/tests -t . -p "test_[gr]*.py"` → errors (no `vkforge.runtime`, stub `generate`).

- [ ] **Step 3: Implement** `ooxml.write_parts`; `runtime/com.py` (the code above — the guard, the probe, the application from the binding — then the `VERBS` table over the object model, each verb walking the binding's members and constants and reached only through `Application.call`, hence under `guarded` like `open`, `close` and `quit` (the adapter's every call to the application has its deadline); no application name in the module, L-P31); `runtime/fake.py` (the code above — Word only, `probe`, the `dialog_on` hook — and the same `VERBS` keys over the OOXML parts: `apply_paragraph_style` writes `w:pStyle` (adding the built-in style to `styles.xml` with its English `w:name` when absent), `set_font_run` rewrites `w:rFonts`/`w:sz` on the selected runs, `set_footer`/`set_header` write `word/footer1.xml`/`word/header1.xml` with their relationship and `w:sectPr` reference when absent, `save` writes the edited parts with `write_parts`, `export` writes the fake PDF, `verify_*` re-read the saved file through `mine.mine_file` and compare with the arguments (`verify_footer` compares `mine.redact(text)`)); `generate.py` (`render_driver`, the refusals, the layout above, `files_sha256` with `vkdriver.hash_dir.hash_dir(out)` last, `add_arguments`/`run`; the CLI reads `--vault` and deletes nothing — the kernel deletes the vault, Task 9).

- [ ] **Step 4: Run** the suite green (`test_generate.py` 12 tests, `test_runtime.py` 4, the real-Word ones skipped off the node). **Commit** `feat(vkforge): the COM adapter with its fake twin, and the generator — a protocol-1 driver from a plan, vendoring vkdriver and its adapter`

---

### Task 8: Qualification — replay on copies, postconditions re-mined (Opus)

**Model:** Opus.

**Files:**
- Modify: `drivers/vkforge/qualify.py` (Task 0's stub)
- Create: `drivers/vkforge/tests/test_qualify.py`

**Interfaces:**
- Consumes: `generate.generate` and the generated layout (Task 7: `driver.py`, `manifest.json`, `vendor/vkdriver/protocol.py`, `<work>/fake.json` read by the driver); `plan.build`, `plan.write_plan` (Task 6); `mine.mine_corpus`, `mine.mine_file`, `mine.content_sha256`, `mine.INVARIANT_IDS`, `ooxml_fixtures.corpus` (Task 5); `ontology.load`, `binding-word.json` (Task 4); `canon`, `Refusal` (Task 0); `vkdriver.hash_dir.hash_dir`.
- Produces (consumed by Tasks 9, 12):

```python
# drivers/vkforge/qualify.py — stage `qualify` (STAGES["qualify"])
PASS_DEADLINE_S = 120.0
HELLO_TIMEOUT_S = 30.0
def qualify(driver_dir: Path, corpus: Path, plan: dict, invariants: dict, work: Path, fake: bool,
            deadline_s: float = PASS_DEADLINE_S) -> dict    # the record, also written as <work>/qualification.json
# CLI: vkforge qualify --driver DIR --corpus DIR --plan FILE --invariants FILE --work DIR [--fake]
#      prints "qualify: <passes>/<n> passes (<failures> failures), fake <true|false> -> <work>/qualification.json"
```

`qualification.json` (`canon.dumps`): `{"files_sha256": <the manifest's>, "plan_sha256": canon.sha256(plan), "fake": bool, "passes", "failures", "rate": round(passes / n, 4), "runs": [{"index", "file_sha256", "duration_ms", "post": {<post id>: bool}, "ok", "reason"}]}` — runs in the order of the corpus files' names, which never appear in the record (spec §5.5: file names are content). The kernel, not this stage, computes `driver_id` from `manifest.json` (Task 9): Python cannot reproduce `hash_canonical`.

`qualify` refuses, in this order and **before any copy**: a non-empty `work` (`"work <dir> is not empty"`, remedy `a new directory: a qualification never reuses one`); a driver directory without `vendor/vkdriver/protocol.py` (`"driver <dir> does not vendor vkdriver"`, remedy `generate it again: the generator vendors vkdriver (L-P6)`); a driver whose `hash_dir` is not its manifest's `files_sha256`; a plan whose `invariants_sha256` is not `canon.sha256(invariants)` (`"plan and invariants do not belong together"`); `mine.content_sha256(corpus) != invariants["corpus"]["content_sha256"]` (`"corpus changed since vkforge mine"`, remedy `qualify on the corpus the driver was learned from, or learn again`). Then: `work` made; with `fake`, `<work>/fake.json` = `{"vkforge_root": <drivers/ of this vkforge>}` (L-P19); one driver process for the run, `[sys.executable, "-I", <driver_dir>/driver.py]` — the vkforge interpreter, the virtualenv the manifest's `.venv` links to (L-P12) — `cwd = work`, environment `VK_DRIVER_WORK_DIR = work`, `VK_DRIVER_PROFILE_DIR = work/profile`, `VK_DRIVER_APPLICATIONS` = the manifest's, never the repository's directory (L-P6); its hello read within `HELLO_TIMEOUT_S`. Per corpus file `i` (the files `mine_corpus` mines, sorted by name): `shutil.copy2` to `work/<i>/<name>` (the read-only attribute travels: a document Word holds stays locked); the request `{"id": i + 1, "action": <the plan's action>, "args": {"document": <copy>, "pdf": <copy>.pdf}}` (`pdf` when the manifest's action requires it), `"deadline_ms"`, `"evidence_dir": work/<i>/evidence`; an answer past `deadline_s` → the process is killed, the pass recorded `reason: "timeout"`, a new process started for the next; an `ok` answer → the copy re-mined with `mine.mine_file`: each post id holds when the observation of its `(kind, where)` (`mine.INVARIANT_IDS`, reversed) has the invariant's value — or the plan ambiguity's `value` for a resolved one — and `pdf_exists` when `<copy>.pdf` starts with `%PDF`; a refusal → `ok: false`, `reason` the protocol word (`dialog` when the adapter's guard closed a box, L-P33; `precondition` for a read-only copy or an Office version change, L-P22), `post` all false. The driver process is this stage's child, inside its Job (the governor), not a job step under the harness: the deviation from spec §5.2's "under the harness, supervised" that L-P32 declares.

- [ ] **Step 1: Failing tests** (`drivers/vkforge/tests/test_qualify.py`)

```python
import copy
import json
import os
import shutil
import stat
import tempfile
import time
import unittest
from pathlib import Path

from vkforge import __main__ as entry
from vkforge import canon, generate, mine, ontology, plan, qualify
from vkforge.errors import Refusal
from vkforge.tests import ooxml_fixtures

FIXTURES = Path(__file__).resolve().parent / "fixtures"
HOUSE_POST = {"title_style": True, "body_font": True, "footer_text": True, "pdf_exists": True}


def learned(corpus: Path):
    """What `vk driver learn` leaves (Task 9), built here in Python: the invariants, the plan, and the
    driver generated into a temp dir, so every pass also proves it runs with no repository around it."""
    inv, vault = mine.mine_corpus(corpus)
    binding = json.loads((FIXTURES / "binding-word.json").read_text(encoding="utf-8"))
    p, _ = plan.build(inv, binding, ontology.load())
    driver = Path(tempfile.mkdtemp()) / "word-maison" / "driver"
    generate.generate(p, binding, inv, vault, driver, name="word-maison")
    return driver, p, inv


class QualifyTest(unittest.TestCase):
    def setUp(self):
        self.corpus = ooxml_fixtures.corpus(Path(tempfile.mkdtemp()) / "corpus", 10)
        self.driver, self.plan, self.inv = learned(self.corpus)
        self.work = Path(tempfile.mkdtemp()) / "work"

    def run_qualify(self, work=None, **kw):
        return qualify.qualify(self.driver, self.corpus, self.plan, self.inv, work or self.work, fake=True, **kw)

    def test_ten_copies_pass_against_the_fake(self):
        rec = self.run_qualify()
        self.assertEqual((rec["passes"], rec["failures"], rec["rate"], rec["fake"]), (10, 0, 1.0, True))
        self.assertEqual([r["index"] for r in rec["runs"]], list(range(10)))
        for r in rec["runs"]:
            self.assertEqual((r["post"], r["ok"], r["reason"]), (HOUSE_POST, True, None))
            self.assertGreaterEqual(r["duration_ms"], 0)
        self.assertEqual(json.loads((self.work / "qualification.json").read_text(encoding="utf-8")), rec)
        self.assertEqual(mine.content_sha256(self.corpus), self.inv["corpus"]["content_sha256"], "the originals are never touched")

    def test_a_read_only_copy_is_a_failed_pass_not_a_hang(self):
        # Review Focus 4: the user's Word holds c3.docx; its copy keeps the read-only attribute.
        locked = self.corpus / "c3.docx"
        os.chmod(locked, stat.S_IREAD)
        try:
            started = time.monotonic()
            rec = self.run_qualify(deadline_s=30)
            self.assertLess(time.monotonic() - started, 300)
        finally:
            for p in [locked, *self.work.rglob("c3.docx")]:
                os.chmod(p, stat.S_IREAD | stat.S_IWRITE)
        self.assertEqual((rec["passes"], rec["failures"]), (9, 1))
        self.assertEqual((rec["runs"][3]["ok"], rec["runs"][3]["reason"]), (False, "precondition"))

    def test_a_changed_corpus_is_refused_before_any_copy(self):
        ooxml_fixtures.docx(self.corpus / "c10.docx")
        with self.assertRaises(Refusal) as cm:
            self.run_qualify()
        self.assertIn("corpus changed since vkforge mine", str(cm.exception))
        self.assertFalse(self.work.exists())

    def test_a_driver_without_its_vendor_is_refused_before_any_copy(self):
        bare = Path(tempfile.mkdtemp()) / "driver"
        shutil.copytree(self.driver, bare, ignore=shutil.ignore_patterns("vendor"))
        with self.assertRaises(Refusal) as cm:
            qualify.qualify(bare, self.corpus, self.plan, self.inv, self.work, fake=True)
        self.assertIn("does not vendor vkdriver", str(cm.exception))
        self.assertFalse(self.work.exists())

    def test_a_used_work_directory_or_a_foreign_plan_is_refused(self):
        self.work.mkdir(parents=True)
        (self.work / "x").write_text("x")
        with self.assertRaises(Refusal) as cm:
            self.run_qualify()
        self.assertIn("is not empty", str(cm.exception))
        other = copy.deepcopy(self.plan)
        other["invariants_sha256"] = "sha256:" + "0" * 64
        with self.assertRaises(Refusal) as cm:
            qualify.qualify(self.driver, self.corpus, other, self.inv, Path(tempfile.mkdtemp()) / "w", fake=True)
        self.assertIn("plan and invariants do not belong together", str(cm.exception))

    def test_the_record_is_reproducible_but_for_durations(self):
        a = self.run_qualify()
        b = self.run_qualify(work=Path(tempfile.mkdtemp()) / "w2")

        def strip(rec):
            return {**rec, "runs": [{k: v for k, v in r.items() if k != "duration_ms"} for r in rec["runs"]]}

        self.assertEqual(strip(a), strip(b))

    def test_the_record_names_the_plan_and_the_driver_by_their_hashes_and_no_file(self):
        rec = self.run_qualify()
        manifest = json.loads((self.driver / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual((rec["plan_sha256"], rec["files_sha256"]),
                         (canon.sha256(self.plan), manifest["program"]["files_sha256"]))
        self.assertNotIn("c0.docx", json.dumps(rec), "no file name in the record (spec §5.5)")

    def test_the_cli_qualifies_from_the_artefacts(self):
        out = self.driver.parent
        plan.write_plan(self.plan, [], out)
        canon.write(self.inv, out / "invariants.json")
        code = entry.main(["qualify", "--driver", str(self.driver), "--corpus", str(self.corpus),
                           "--plan", str(out / "plan.json"), "--invariants", str(out / "invariants.json"),
                           "--work", str(self.work), "--fake"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads((self.work / "qualification.json").read_text(encoding="utf-8"))["passes"], 10)
```

- [ ] **Step 2: Run** `python -I -m unittest discover -s drivers/vkforge/tests -t . -p test_qualify.py` → errors (stub).

- [ ] **Step 3: Implement** `qualify` as specified (a reader thread and a queue give each answer its deadline; the process is killed with its tree on a timeout), `add_arguments`/`run` (the CLI reads `--plan`/`--invariants`, refuses a file that is not in `canon.dumps` form, then calls `qualify`).

- [ ] **Step 4: Run** the suite green (8 tests). **Commit** `feat(vkforge): qualification — replay on copies, postconditions re-mined, a record the mount checks`

---

### Task 9: Kernel, IPC and CLI — `vk driver learn`, `vk driver qualify`, the ambiguity call, the gate, `vk driver plan show` (Opus)

**Model:** Opus.

**Files:**
- Create: `crates/vk-ipc/src/forge.rs`, `crates/vk-ipc/tests/common/mod.rs`, `crates/vk-ipc/tests/forge.rs`, `crates/vk-kernel/src/learn.rs`, `crates/vk-kernel/tests/learn.rs`, `drivers/vkforge/setup.ps1`
- Modify: `crates/vk-ipc/src/lib.rs` (`pub mod forge;` beside `pub mod drivers;`, 20–24)
- Modify: `crates/vk-ipc/src/server.rs` — `ServerConfig` (40–94: the field `forge: Option<crate::forge::ForgeConfig>`; its `Debug`, 96–109; `ServerConfig::new`, 114–126: `forge: None`); `takes_presence` (714–746: `driver.learn`); `ctx_for` (753) becomes `pub(crate)` (ruling L-P25); `dispatch` (784–): `forge_context` and three arms **before** the general lock, after the `driver.pack` arm (882–895), ruling L-P17, the `learn` and `qualify` arms building their `Ctx` as that arm does (L-P25); `boot.info` (958–): a `vkforge_root` field
- Modify: `crates/vk-ipc/src/drivers.rs` — `mount` (632–736): `check_qualified` after each of the two `check_context` calls (649, 664), before anything is spawned. `MountParams` (482–496) is **unchanged** (no `--unqualified`, L-P16).
- Modify: `crates/vk-ipc/src/capital.rs` — `stage_files` (373–413): three lines (L-P9), and one unit test beside `a_driver_with_a_vkdriver_of_its_own_beside_the_shared_one_is_refused` (1143–1167)
- Modify: `crates/vk-ipc/tests/capital.rs` — `PYTHON`, `python_available` (49–67), `Node` and its `impl` (226–413) and `call_error` (415–418) move to `tests/common/mod.rs`; the file gains `mod common; use common::*;` and loses its now-unused imports (L-P17)
- Modify: `crates/vk-kernel/src/lib.rs` (`pub mod learn;`, 2–7), `crates/vk-kernel/src/drivers.rs` (`mount_driver`, 566–678: `self.check_qualified(&manifest)?;` right after `self.check_context(&manifest, &root)?;`)
- Modify: `crates/vk-store/src/db.rs` (`TABLES`, 14–42: `learned`)
- Modify: `crates/vk-cli/src/main.rs` (`DriverCmd`, 528–562: `Learn`, `Qualify`, `Plan`; `enum PlanCmd`; their arms beside `DriverCmd::Umount`, 1198–1208), `crates/vk-cli/src/render.rs` (`learned`, `qualified`, `plan_shown`, beside `mounted_driver`, 1165; a `kind` row in `driver`, 368–394, and its test in the tests module, ruling L-P27), `crates/vkd/src/lib.rs` (`Args`, 21–135: `--vkforge-root`; the `ServerConfig` literal, 608–629: `forge`)
- Test: `crates/vk-cli/tests/smoke.rs` (helpers `forge_python`, `forge_available`, `forge_node`, `ooxml_corpus`; two tests)

**Interfaces:**
- Consumes: `DriverKind::Com`, `applications` (Task 1); `vk_contracts::plan::{DriverPlan, Ambiguity, Qualification, plan_sha256}`, `vk_contracts::driver::{DriverLearned, DriverQualified}`, the kinds `driver.learned`/`driver.qualified` (Task 2); the CLIs of the stages, verbatim: `inventory --app A --out DIR [--fixture FILE]` (Task 3), `bind --catalogue FILE --out DIR` (Task 4), `mine --corpus DIR --out DIR --threshold T` (Task 5), `plan --invariants FILE --binding FILE --out DIR [--resolver FILE]` (Task 6), `generate --plan FILE --binding FILE --invariants FILE --vault FILE --out DIR --name NAME` (Task 7), `qualify --driver DIR --corpus DIR --plan FILE --invariants FILE --work DIR [--fake]` (Task 8); the artefact names `catalogue.json`, `binding-<app>.json`, `invariants.json`, `.vault.json`, `plan.json`, `plan-excluded.json`, `resolver.json`, `driver/`, `<work>/qualification.json`; `drivers/vkforge/tests/ooxml_fixtures.py DIR GARAMOND [CALIBRI]` (Task 5); the recorded catalogues `drivers/vkforge/tests/fixtures/catalogue-<app>.json` (Task 3).
- Produces (consumed by Tasks 10, 11, 12):

```rust
// crates/vk-ipc/src/forge.rs — L-P2
#[derive(Debug, Clone)]
pub struct ForgeConfig {
    /// The `drivers/` directory: vkforge is `<root>/vkforge/__main__.py`.
    pub root: PathBuf,
    /// The interpreter (L-P12): `VK_FORGE_PYTHON`, else `%LOCALAPPDATA%\vk\venvs\vkforge\Scripts\python.exe`.
    pub python: PathBuf,
    /// `VK_FORGE_FIXTURES=1`, a test seam like `VK_TEST_PYTHON_RELEASE`: the recorded catalogues
    /// (`inventory --fixture`) and the fake application (`qualify --fake`). `vkd` warns when it is set.
    pub fixtures: bool,
    /// The deadline of each `learn` stage: `STAGE_DEADLINE` from `at`; a test sets it shorter
    /// (L-P30). `qualify` and `debrief` keep their own.
    pub stage_deadline: Duration,
}
impl ForgeConfig {
    /// `root` as `vkd --vkforge-root` gives it, with the interpreter and the seam read from the
    /// environment and `STAGE_DEADLINE`. Infallible (L-P26): a root without
    /// `vkforge/__main__.py` is refused by `run_stage`, at the first spawn.
    pub fn at(root: PathBuf) -> ForgeConfig;
}
pub struct StageRun { pub stage: &'static str, pub args: Vec<String>, pub exit: i32, pub duration_ms: u64, pub stdout_tail: String }
pub const STAGE_DEADLINE: Duration = Duration::from_secs(600);
/// Refuses, before spawning, a root without `vkforge/__main__.py`: `bad("vkforge root not found;
/// remedy: start vkd with --vkforge-root <drivers/>")` (L-P26). Then spawns
/// `<python> -I <root>/vkforge/__main__.py <stage> <args>` with `current_dir = root` — the
/// script path, never `-m` — under `vk_harness::confine::for_this_platform(1 << 30, None)`
/// (`Governor::contain` the moment the child exists, failing closed on Windows as the harness does),
/// stdout to `<work>/forge-<stage>.out`, stderr to `<work>/forge-<stage>.err`, killed at `deadline`
/// (`internal("vkforge <stage> ran past its deadline of <n> s; see <log>")`). A non-zero exit is
/// `bad(<the last non-empty line of the .err file>)`, verbatim: the stage speaks the kernel's voice
/// (L-P15); an exit with no line is `internal("vkforge <stage> exited <code>; see <log>")`.
pub fn run_stage(cfg: &ForgeConfig, stage: &'static str, args: &[String], work: &Path, deadline: Duration) -> Result<StageRun, RpcError>;
/// Both take the `Ctx` their pre-lock arm built as the `driver.pack` arm does (L-P25): `learn`'s is
/// human (`driver_learned`), `qualify`'s whatever the caller presented (`driver_qualified` is kernel-written).
pub fn learn(kernel: &Shared, cfg: &ForgeConfig, state_dir: &Path, ctx: &Ctx, p: Value) -> Result<Value, RpcError>;
pub fn qualify(kernel: &Shared, cfg: &ForgeConfig, state_dir: &Path, ctx: &Ctx, p: Value) -> Result<Value, RpcError>;
pub fn plan_show(p: Value) -> Result<Value, RpcError>;

// crates/vk-ipc/src/server.rs
/// The daemon's vkforge and its state directory (read in one short lock phase), or the refusal
/// "vkforge is not configured on this daemon; remedy: start vkd with --vkforge-root <repository>\drivers".
pub(crate) fn forge_context<'a>(kernel: &Shared, config: &'a ServerConfig) -> Result<(&'a ForgeConfig, PathBuf), RpcError>;

// crates/vk-kernel/src/learn.rs
pub const LEARNED: &str = "learned";                 // the vk-store table
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LearnedRow {
    pub learned: DriverLearned,
    pub name: String,                                // the manifest's
    pub out: PathBuf,                                // the learn output directory
    pub qualification: Qualification,                // the plan's thresholds, copied at learn
    #[serde(default)]
    pub qualified: Option<QualifiedRecord>,
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct QualifiedRecord { pub record: PathBuf, pub qualification_sha256: String, pub passes: u32, pub failures: u32 }
/// "Answer with one JSON object {\"invariant\": <the invariant above>, \"value\": <one candidate's value, verbatim>}."
pub const ANSWER_RULE: &str;
/// The answer rule (spec §6, "never applied in part"): at each `{` of `answer`, in order, one JSON
/// value is parsed; the first that is an object `{"invariant": <this ambiguity's>, "value": V}` with V
/// equal to a candidate's value, or that is itself equal to a candidate's value, chooses that value.
/// Nothing else ever comes back: an arch can only pick among what the miner listed.
pub fn answer_choice(answer: &str, ambiguity: &Value) -> Option<Value>;
impl RealKernel {
    /// A human act (I1 otherwise): `by` is set here from the principal, whatever the caller put; logs
    /// `driver.learned`, then writes the `learned` row keyed by `learned.driver_id`.
    pub fn driver_learned(&mut self, ctx: &Ctx, learned: DriverLearned, name: &str, out: &Path, qualification: Qualification) -> Result<(), KernelError>;
    /// Kernel-written: refused for a `driver_id` with no `learned` row ("driver <id> was not learned on
    /// this node; remedy: vk driver learn"); logs `driver.qualified`, then records `record`'s path,
    /// `qualification_sha256`, passes and failures on the row.
    pub fn driver_qualified(&mut self, ctx: &Ctx, q: DriverQualified, record: &Path) -> Result<(), KernelError>;
    pub fn learned(&self, driver_id: &str) -> Result<Option<LearnedRow>, KernelError>;
    /// The gate (L-P4): `Ok` for every kind but `com`; for `com`, a `learned` row with a record whose
    /// file still hashes to `qualification_sha256` and whose passes meet the thresholds, else
    /// `KernelError::Gate` — "driver <id> is not qualified; remedy: vk driver qualify --driver DIR
    /// --corpus DIR", "driver <id>: qualification.json changed since vk driver qualify; remedy: …",
    /// "driver <id> is not qualified: <p> passes of <n>, the plan asks <min_passes> at rate <min_rate>; remedy: …".
    pub fn check_qualified(&self, manifest: &DriverManifest) -> Result<(), KernelError>;
    /// The one model call of the pipeline (L-P10, L-P20): a task over a fresh register whose goal is
    /// `serde_json::to_string(ambiguity)` and nothing else (label scope `business`, constraint
    /// `ANSWER_RULE`, no evidence), with one `StepKind::Plan { arch_id }` step — `infer` with
    /// `Capability::Plan` — run at once; the answer is the register's last decision, `plan: ` removed,
    /// read by `answer_choice`.
    pub fn learn_ambiguity(&mut self, ctx: &Ctx, arch_id: &str, ambiguity: &Value) -> Result<Option<Value>, KernelError>;
}
```

IPC (all four are dispatched before the general kernel lock, L-P17):
- `driver.learn` (presence; in `takes_presence`): params `{family, app, corpus, out, threshold?, arch?}` (`deny_unknown_fields`; `arch` an arch id — the CLI resolves a name, as recipes do). Refused before any stage is spawned: a machine principal (`I1: learning a driver writes code this node will run, which is a human act; re-run it with a presence proof`); `family` other than `office-document` (`family <f> is not known; remedy: --family office-document`); `app` other than `word`/`powerpoint` (`app <a> is not known; remedy: --app word or --app powerpoint`); a relative `corpus`/`out` (`… is not absolute`); an `out` that holds anything (`out <dir> is not empty; remedy: a new directory`); an `out` whose last component is not a driver name (it becomes the driver's name: `word-maison`); `threshold` outside `(0, 1]`. Then, each stage through `run_stage` with the deadline `cfg.stage_deadline` (L-P30), logs under `<state_dir>/forge/<name>/`:
  1. `inventory --app A --out OUT` (with `--fixture <root>/vkforge/tests/fixtures/catalogue-A.json` when `cfg.fixtures`);
  2. `bind --catalogue OUT/catalogue.json --out OUT`;
  3. `mine --corpus CORPUS --out OUT --threshold T` — from here a guard deletes `OUT/.vault.json` on every way out;
  4. with `arch`: for each ambiguity of `OUT/invariants.json`, `learn_ambiguity` (one short lock phase each); the chosen values written to `OUT/resolver.json` as `{"<invariant>": {"value", "resolved_by": "arch:<arch>"}}`; then `plan --invariants OUT/invariants.json --binding OUT/binding-A.json --out OUT [--resolver OUT/resolver.json]`;
  5. `generate --plan OUT/plan.json --binding OUT/binding-A.json --invariants OUT/invariants.json --vault OUT/.vault.json --out OUT/driver --name <name>`;
  6. the vault deleted; `plan.json` read (bytes → `DriverPlan`, `validate`), `invariants.json`'s `corpus.schema_sha256`, `driver/manifest.json` (`DriverManifest`, `validate`); `driver_learned(ctx, DriverLearned { family, app, catalogue_sha256, ontology_sha256, corpus_schema_sha256, invariants_sha256, plan_sha256: plan_sha256(&bytes), driver_id: manifest.driver_id(), by }, name, OUT, plan.qualification)`;
  → `{driver_id, name, plan_sha256, out, unbound: [<verbs unbound in the binding>], ambiguities: <plan.ambiguities>, excluded: <plan-excluded.json>}`.
- `driver.qualify` (no presence): params `{driver, corpus}`, both absolute; `driver` a learned driver's directory (`<out>/driver`, with `<out>/plan.json` and `<out>/invariants.json` beside it, else `… is not a learned driver's directory; remedy: vk driver qualify --driver <learn out>\driver`); its `driver_id` must have a `learned` row (refused before spawning otherwise). `<state_dir>/drivers/<name>/work/qualify` is emptied (the daemon's own scratch) and the stage runs `qualify --driver DIR --corpus CORPUS --plan <out>/plan.json --invariants <out>/invariants.json --work <state_dir>/drivers/<name>/work/qualify [--fake when cfg.fixtures]`, logs under `<state_dir>/forge/qualify-<name>/`, deadline 60 s + 130 s per corpus document; the record's `plan_sha256` must be the `learned` row's (else `the plan changed since vk driver learn; remedy: learn again`); then `driver_qualified(ctx, DriverQualified { driver_id, passes, failures, qualification_sha256: hash_bytes(<record bytes>) }, <record>)` → `{driver_id, name, passes, failures, rate, fake, record}`.
- `driver.plan_show` (no presence, no kernel state): params `{dir}` → `{plan: <DriverPlan, validated>, plan_sha256, excluded: <plan-excluded.json, or []>}`.
- Mount: a `kind: com` manifest goes through `check_qualified` in `vk-ipc`'s `mount` before anything is spawned, and again in `mount_driver` under the lock.

CLI (`crates/vk-cli/src/main.rs`):

```rust
    /// Learn a driver from a corpus of the firm's documents (spec §4.10): the
    /// type library's catalogue, the binding, the mined invariants, a plan, a
    /// generated driver under `--out/driver`. A human act: signed with this
    /// node's device key; `driver.learned` goes on the ledger.
    Learn {
        #[arg(long)]
        family: String,
        #[arg(long)]
        app: String,
        #[arg(long, value_name = "DIR")]
        corpus: PathBuf,
        /// A new directory; its name is the driver's (word-maison).
        #[arg(long, value_name = "DIR")]
        out: PathBuf,
        #[arg(long, default_value_t = 0.8)]
        threshold: f64,
        /// The arch that may choose among an ambiguous invariant's candidates, by
        /// name or id; without it every ambiguity is purged, and no model is called.
        #[arg(long, value_name = "ARCH")]
        arch: Option<String>,
    },
    /// Replay a learned driver on copies of its corpus; `driver.qualified`
    /// records the result, and a `com` driver mounts only when it meets its
    /// plan's thresholds.
    Qualify {
        #[arg(long, value_name = "DIR")]
        driver: PathBuf,
        #[arg(long, value_name = "DIR")]
        corpus: PathBuf,
    },
    /// A learned driver's plan, for review before `vk driver qualify`.
    Plan {
        #[command(subcommand)]
        what: PlanCmd,
    },
```

`enum PlanCmd { Show { dir: PathBuf } }`. Arms: `Learn` — paths made absolute (`std::path::absolute`), `--arch` resolved through `arch.ls` as `resolve_arch_names` does (an id kept, a name to the one arch carrying it), then `presence(&c).await?` and `driver.learn`, shown with `render::learned`; `Qualify` → `driver.qualify`, `render::qualified`; `Plan { Show }` → `driver.plan_show`, `render::plan_shown`. Renders: `learned` — `learned <name>  <driver_id>`, `plan <plan_sha256> -> <out>\plan.json (read it: vk driver plan show <out>)`, `unbound: <verbs>|none`, `ambiguities: <invariant> <resolved_by>, …|none`, `excluded: <invariant> (<why>), …|none`; `qualified` — `qualified <name>: <passes>/<passes + failures> passes (<failures> failures), fake <bool> -> <record>`; `plan_shown` — per action `<name> (reversible: yes|no)`, its numbered steps `  <n>. <verb>(<key>=<value>, …)` (values as JSON, compact), `  pre: …`, `  post: <ids, comma-separated>`, `  note: …` when present, then `ambiguities:` and one line `  <invariant>  <resolved_by>[  <value>]` each (or `ambiguities: none`), then `excluded:` likewise (or `excluded: none`).

`vkd` (`crates/vkd/src/lib.rs`): `--vkforge-root DIR` (no default: the target directory is outside the checkout, so nothing can be guessed from the executable's place); `ServerConfig.forge = a.vkforge_root.map(ForgeConfig::at)`, an `Option<ForgeConfig>` (`at` is infallible, L-P26: a root without `vkforge/__main__.py` is not checked at start but refused by `run_stage` at the first spawn, `vkforge root not found; remedy: start vkd with --vkforge-root <drivers/>`); a `tracing::warn!` when `VK_FORGE_FIXTURES=1`. `boot.info` gains `"vkforge_root"` (null without one).

`drivers/vkforge/setup.ps1` (French, the pattern of `drivers/maskai-recette/setup.ps1`): `-Python`, `-VenvHome` (default `%LOCALAPPDATA%\vk\venvs\vkforge`); the base Python is `-Python`, else uv's managed 3.13, else `python`; a base under `WindowsApps` is refused, for a new and an existing virtualenv (R27); the virtualenv is reused when it exists (Task 3's Step 6 made it), else created; `pip install -r drivers\vkforge\requirements.txt`; `& <venv>\Scripts\python.exe -I -c "import lxml, comtypes"` must succeed; last line `VK_FORGE_PYTHON par défaut : <venv>\Scripts\python.exe`. No junction under `drivers/vkforge` (the daemon runs the virtualenv's interpreter by its path; each generated driver gets its own `.venv` junction from the generator).

- [ ] **Step 1: Failing tests**

`crates/vk-ipc/tests/common/mod.rs` — moved from `tests/capital.rs` (`PYTHON`, `python_available`, `Node` with its fields made `pub` and every method, `call_error`), with `#![allow(dead_code)]` (each test binary uses a part of it, and CI's clippy runs with `-D warnings`), and these additions:

```rust
/// The checkout's root, for the vkforge package and its fixture scripts.
pub fn repo_root() -> PathBuf {
    std::path::absolute(Path::new(env!("CARGO_MANIFEST_DIR")).join("../..")).unwrap()
}

/// `%LOCALAPPDATA%\vk\venvs\vkforge`, the virtualenv drivers/vkforge/setup.ps1 makes (L-P12).
pub fn vkforge_venv() -> PathBuf {
    PathBuf::from(std::env::var_os("LOCALAPPDATA").unwrap_or_default()).join("vk").join("venvs").join("vkforge")
}

/// The interpreter the daemon runs vkforge with: `VK_FORGE_PYTHON`, else the virtualenv's.
pub fn forge_python() -> PathBuf {
    std::env::var_os("VK_FORGE_PYTHON")
        .map(PathBuf::from)
        .unwrap_or_else(|| vkforge_venv().join("Scripts").join("python.exe"))
}

/// Whether the vkforge tests can run here (L-P18): the virtualenv a generated driver's `.venv` links
/// to exists, and the interpreter imports lxml and comtypes. Says why on stderr when not.
pub fn forge_available() -> bool {
    let python = forge_python();
    let venv = vkforge_venv().join("Scripts").join("python.exe");
    let imports = Command::new(&python)
        .args(["-I", "-c", "import lxml, comtypes"])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok_and(|s| s.success());
    if !(imports && venv.is_file()) {
        eprintln!(
            "vkforge: {} cannot import lxml and comtypes, or {} is absent; skipping \
             (drivers\\vkforge\\setup.ps1 makes the virtualenv)",
            python.display(),
            venv.display()
        );
        return false;
    }
    true
}

/// A corpus of fixture contracts (drivers/vkforge/tests/ooxml_fixtures.py): `garamond` bodies in
/// Garamond 11 then `calibri` in Calibri 12, each with the house title, footer and a sibling PDF.
pub fn ooxml_corpus(dir: &Path, garamond: u32, calibri: u32) -> PathBuf {
    let script = repo_root().join("drivers/vkforge/tests/ooxml_fixtures.py");
    let ok = Command::new(forge_python())
        .arg("-I")
        .arg(&script)
        .arg(dir)
        .arg(garamond.to_string())
        .arg(calibri.to_string())
        .status()
        .expect("run ooxml_fixtures.py")
        .success();
    assert!(ok, "ooxml_fixtures.py {}", dir.display());
    dir.to_path_buf()
}
```

`Node` gains `pub async fn start_with_forge(forge: ForgeConfig) -> Node`, which is today's `start` with `forge: Some(forge)` in its `ServerConfig` — the in-process form of `vkd --vkforge-root` (L-P26, L-P30) — and `Node::start()` becomes `Node::start_with_forge(ForgeConfig { python: forge_python(), fixtures: true, ..ForgeConfig::at(repo_root().join("drivers")) }).await` (`use vk_ipc::forge::ForgeConfig;` at the top of `common/mod.rs`); `Node` also gains `pub fn dir(&self) -> &Path { self.d.path() }` and `pub async fn call(&self, method: &str, params: Value) -> anyhow::Result<Value>` (`self.c.call(method, params, None)`, a machine call); `shutdown` drops the host of every mounted driver (names from `self.k.lock().unwrap().drivers()`), not only `fake`.

`crates/vk-ipc/tests/forge.rs`:

```rust
//! vk driver learn / qualify / plan show over the pipe (plan Task 9): a real kernel, this checkout's
//! vkforge spawned under the governor, the recorded catalogues and the fake application
//! (`ForgeConfig.fixtures`). Skipped, saying why on stderr, where `forge_available` says no (L-P18).
mod common;

use common::*;
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};
use vk_contracts::driver::{DriverLearned, DriverManifest, DriverQualified};
use vk_contracts::plan::{plan_sha256, Ambiguity, DriverPlan};
use vk_contracts::{hash_bytes, hash_canonical};
use vk_ipc::forge::ForgeConfig;

fn learn_params(corpus: &Path, out: &Path) -> Value {
    json!({"family": "office-document", "app": "word", "corpus": corpus, "out": out})
}

fn read_plan(out: &Path) -> (DriverPlan, Vec<u8>) {
    let bytes = std::fs::read(out.join("plan.json")).unwrap();
    (serde_json::from_slice(&bytes).unwrap(), bytes)
}

fn manifest_of(driver: &Path) -> DriverManifest {
    serde_json::from_slice(&std::fs::read(driver.join("manifest.json")).unwrap()).unwrap()
}

async fn learn_refusal(node: &Node, params: Value) -> String {
    let e = node.call_human("driver.learn", params).await.unwrap_err();
    call_error(&e).message.clone()
}

#[tokio::test]
async fn learn_runs_the_five_stages_and_writes_driver_learned() {
    if !forge_available() {
        return;
    }
    let node = Node::start().await;
    let corpus = ooxml_corpus(&node.dir().join("corpus"), 10, 0);
    let out = node.dir().join("word-maison");
    let r = node.call_human("driver.learn", learn_params(&corpus, &out)).await.unwrap();
    for f in ["catalogue.json", "binding-word.json", "invariants.json", "plan.json", "plan-excluded.json",
              "driver/driver.py", "driver/manifest.json"] {
        assert!(out.join(f).is_file(), "{f}");
    }
    assert!(!out.join(".vault.json").exists(), "the vault is deleted after generation");
    let (plan, bytes) = read_plan(&out);
    plan.validate().unwrap();
    let inv_bytes = std::fs::read(out.join("invariants.json")).unwrap();
    let inv: Value = serde_json::from_slice(&inv_bytes).unwrap();
    assert_eq!(plan.invariants_sha256, hash_bytes(&inv_bytes), "a file hash, as Python wrote it (L-P13)");
    let manifest = manifest_of(&out.join("driver"));
    assert_eq!(r["driver_id"], json!(manifest.driver_id()));
    assert_eq!(r["plan_sha256"], json!(plan_sha256(&bytes)));
    assert_eq!(r["ambiguities"], json!([]));
    let last = node.last_event();
    assert_eq!(last.kind, "driver.learned");
    assert_eq!(
        last.payload_hash,
        hash_canonical(&DriverLearned {
            family: "office-document".into(),
            app: "word".into(),
            catalogue_sha256: plan.catalogue_sha256.clone(),
            ontology_sha256: plan.ontology_sha256.clone(),
            corpus_schema_sha256: inv["corpus"]["schema_sha256"].as_str().unwrap().into(),
            invariants_sha256: plan.invariants_sha256.clone(),
            plan_sha256: plan_sha256(&bytes),
            driver_id: manifest.driver_id(),
            by: "laptop".into(),
        })
    );
    assert!(node.events("infer").is_empty() && node.events("task.submitted").is_empty(), "zero tokens");
    node.shutdown().await;
}

#[tokio::test]
async fn learn_is_a_human_act_and_refuses_an_unknown_family_or_a_used_out() {
    if !forge_available() {
        return;
    }
    let node = Node::start().await;
    let corpus = ooxml_corpus(&node.dir().join("corpus"), 10, 0);
    let out = node.dir().join("word-maison");
    let e = node.call("driver.learn", learn_params(&corpus, &out)).await.unwrap_err();
    assert!(call_error(&e).message.starts_with("I1: learning a driver"), "{e}");
    let m = learn_refusal(&node, json!({"family": "spreadsheet", "app": "word", "corpus": corpus, "out": out})).await;
    assert!(m.contains("family spreadsheet is not known; remedy: --family office-document"), "{m}");
    let m = learn_refusal(&node, json!({"family": "office-document", "app": "excel", "corpus": corpus, "out": out})).await;
    assert!(m.contains("app excel is not known; remedy: --app word or --app powerpoint"), "{m}");
    let m = learn_refusal(&node, json!({"family": "office-document", "app": "word", "corpus": "corpus", "out": out})).await;
    assert!(m.contains("is not absolute"), "{m}");
    let m = learn_refusal(&node, learn_params(&corpus, &node.dir().join("Word_Maison"))).await;
    assert!(m.contains("driver name"), "{m}");
    std::fs::create_dir_all(&out).unwrap();
    std::fs::write(out.join("x"), "x").unwrap();
    let m = learn_refusal(&node, learn_params(&corpus, &out)).await;
    assert!(m.contains("is not empty; remedy: a new directory"), "{m}");
    assert!(node.events("driver.learned").is_empty());
    assert!(!node.dir().join("forge").exists(), "every refusal came before a stage was spawned");
    node.shutdown().await;
}

#[tokio::test]
async fn qualify_writes_driver_qualified_and_the_mount_gate_opens() {
    if !forge_available() {
        return;
    }
    let node = Node::start().await;
    let corpus = ooxml_corpus(&node.dir().join("corpus"), 10, 0);
    let out = node.dir().join("word-maison");
    node.call_human("driver.learn", learn_params(&corpus, &out)).await.unwrap();
    let driver = out.join("driver");
    let manifest = manifest_of(&driver);
    let mount = json!({"manifest": manifest, "root": driver});
    // Learned, not qualified: refused before anything is spawned (L-P4; no override, L-P16).
    let e = node.call_human("driver.mount", mount.clone()).await.unwrap_err();
    assert!(call_error(&e).message.contains("is not qualified; remedy: vk driver qualify"), "{e}");
    let q = node.call("driver.qualify", json!({"driver": driver, "corpus": corpus})).await.unwrap();
    assert_eq!((q["passes"].as_u64(), q["failures"].as_u64(), q["fake"].as_bool()), (Some(10), Some(0), Some(true)));
    let record = PathBuf::from(q["record"].as_str().unwrap());
    let bytes = std::fs::read(&record).unwrap();
    let rec: Value = serde_json::from_slice(&bytes).unwrap();
    assert_eq!(
        rec["plan_sha256"],
        json!(plan_sha256(&std::fs::read(out.join("plan.json")).unwrap())),
        "one plan_sha256, in Python and in Rust (L-P13)"
    );
    let last = node.last_event();
    assert_eq!(last.kind, "driver.qualified");
    assert_eq!(
        last.payload_hash,
        hash_canonical(&DriverQualified {
            driver_id: manifest.driver_id(),
            passes: 10,
            failures: 0,
            qualification_sha256: hash_bytes(&bytes),
        })
    );
    // The gate opens; the kind is read from the row's manifest: driver.mounted carries none (L-P11).
    node.call_human("driver.mount", mount.clone()).await.unwrap();
    assert_eq!(node.last_event().kind, "driver.mounted");
    let row = node.call("driver.show", json!({"name": "word-maison"})).await.unwrap();
    assert_eq!(row["manifest"]["kind"], json!("com"));
    assert_eq!(row["manifest"]["applications"], json!(["WINWORD.EXE"]));
    node.call_human("driver.unmount", json!({"name": "word-maison"})).await.unwrap();
    // A record edited after qualify is not the one the kernel hashed.
    let edited = String::from_utf8(bytes).unwrap().replacen("\"passes\": 10", "\"passes\": 3", 1);
    std::fs::write(&record, edited).unwrap();
    let e = node.call_human("driver.mount", mount).await.unwrap_err();
    assert!(call_error(&e).message.contains("qualification.json changed since vk driver qualify"), "{e}");
    node.shutdown().await;
}

#[tokio::test]
async fn a_stage_that_fails_is_one_refusal_line_in_the_kernel_voice() {
    if !forge_available() {
        return;
    }
    let node = Node::start().await;
    let corpus = ooxml_corpus(&node.dir().join("corpus"), 2, 0);
    let out = node.dir().join("word-maison");
    let e = node.call_human("driver.learn", learn_params(&corpus, &out)).await.unwrap_err();
    assert_eq!(
        call_error(&e).message,
        "vkforge mine: corpus has 2 files; remedy: more documents of one kind",
        "the stage's own line, verbatim (L-P15)"
    );
    let mut left: Vec<String> = std::fs::read_dir(&out)
        .unwrap()
        .map(|e| e.unwrap().file_name().to_string_lossy().into_owned())
        .collect();
    left.sort();
    assert_eq!(left, ["binding-word.json", "catalogue.json"]);
    assert!(node.events("driver.learned").is_empty());
    node.shutdown().await;
}

#[tokio::test]
async fn an_ambiguity_with_a_mock_arch_is_resolved_and_traced() {
    if !forge_available() {
        return;
    }
    let node = Node::start().await;
    let mounted = node.call("arch.mount_mock", json!({"name": "mock"})).await.unwrap();
    let arch = mounted["arch_id"].as_str().unwrap().to_string();
    let corpus = ooxml_corpus(&node.dir().join("corpus"), 7, 3); // body fonts at 0.7 and 0.3: one ambiguity
    let out = node.dir().join("word-maison");
    let mut params = learn_params(&corpus, &out);
    params["arch"] = json!(arch);
    let r = node.call_human("driver.learn", params).await.unwrap();
    let garamond = json!({"name": "Garamond", "size": 11});
    let traced = Ambiguity {
        invariant: "body_font".into(),
        resolved_by: format!("arch:{arch}"),
        value: Some(garamond.clone()),
    };
    let (plan, _) = read_plan(&out);
    assert_eq!(plan.ambiguities, vec![traced.clone()]);
    let fonts: Vec<_> = plan.actions[0].steps.iter().filter(|s| s.verb == "set_font_run").collect();
    assert_eq!(fonts.len(), 1);
    assert_eq!(fonts[0].args["font"], garamond, "the first candidate, which the mock's echo carries (L-P20)");
    assert_eq!(r["ambiguities"], serde_json::to_value(vec![traced]).unwrap());
    let resolver: Value = serde_json::from_slice(&std::fs::read(out.join("resolver.json")).unwrap()).unwrap();
    assert_eq!(resolver, json!({"body_font": {"value": garamond, "resolved_by": format!("arch:{arch}")}}));
    assert_eq!(node.events("task.submitted").len(), 1, "one model call, for the one ambiguity");
    node.shutdown().await;
}

/// The stand-in vkforge of the deadline test (L-P30): it writes its working directory, its own id and
/// the id of a child it starts, then sleeps a minute — longer than any deadline the test gives it.
const SLEEPER: &str = r#"import os, subprocess, sys, time
from pathlib import Path
child = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(60)"])
Path(__file__).resolve().parents[1].joinpath("stage.txt").write_text(
    f"{os.getcwd()}\n{os.getpid()}\n{child.pid}\n", encoding="utf-8")
time.sleep(60)
"#;

/// Is the Windows process `pid` running? `tasklist` filtered on the id, in CSV, as
/// `crates/vk-driver/tests/fake_driver.rs` asks it (the forge tests run where comtypes imports: Windows).
fn process_alive(pid: u32) -> bool {
    std::process::Command::new("tasklist")
        .args(["/FI", &format!("PID eq {pid}"), "/NH", "/FO", "CSV"])
        .stdin(std::process::Stdio::null())
        .output()
        .map(|o| String::from_utf8_lossy(&o.stdout).contains(&format!("\"{pid}\"")))
        .unwrap_or(false)
}

/// L-P2, L-P30: a stage is a governed one-shot process — started in the vkforge root, refused by the
/// kernel within its deadline plus five seconds, and killed with its whole tree (the Job governor).
/// The root is a stand-in, given the way `vkd --vkforge-root` gives one (`ForgeConfig::at`).
#[tokio::test]
async fn vk_forge_a_stage_past_its_deadline_is_killed_in_its_root() {
    if !forge_available() {
        return;
    }
    let roots = tempfile::tempdir().unwrap();
    let root = roots.path().join("drivers");
    std::fs::create_dir_all(root.join("vkforge")).unwrap();
    std::fs::write(root.join("vkforge").join("__main__.py"), SLEEPER).unwrap();
    let deadline = Duration::from_secs(5);
    let node = Node::start_with_forge(ForgeConfig {
        python: forge_python(),
        stage_deadline: deadline,
        ..ForgeConfig::at(root.clone())
    })
    .await;
    let corpus = node.dir().join("corpus"); // never read: the first stage, inventory, is the sleeper
    std::fs::create_dir_all(&corpus).unwrap();
    let started = Instant::now();
    let e = node
        .call_human("driver.learn", learn_params(&corpus, &node.dir().join("word-maison")))
        .await
        .unwrap_err();
    let waited = started.elapsed();
    assert!(waited < deadline + Duration::from_secs(5), "the kernel answered after {waited:?}");
    let message = call_error(&e).message.clone();
    assert!(message.contains("vkforge inventory ran past its deadline of 5 s"), "{message}");
    let stage = std::fs::read_to_string(root.join("stage.txt")).expect("the stage ran and wrote stage.txt");
    let lines: Vec<&str> = stage.lines().collect();
    assert_eq!(
        std::fs::canonicalize(lines[0]).unwrap(),
        std::fs::canonicalize(&root).unwrap(),
        "current_dir is the vkforge root (L-P2)"
    );
    for pid in &lines[1..3] {
        let pid: u32 = pid.parse().unwrap();
        let gone_by = Instant::now() + Duration::from_secs(5);
        while process_alive(pid) {
            assert!(Instant::now() < gone_by, "python {pid} outlived its stage: the Job holds the tree");
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    }
    assert!(node.events("driver.learned").is_empty());
    node.shutdown().await;
}
```

`crates/vk-kernel/tests/learn.rs` (no Python: it runs on the three operating systems):

```rust
//! The kernel's side of driver learning (plan Task 9): the answer rule, the one model call on a
//! content-free register, the two ledger rows, the qualification gate.
use serde_json::{json, Value};
use std::path::Path;
use std::time::Duration;
use vk_contracts::arch::ArchManifest;
use vk_contracts::driver::{DriverLearned, DriverManifest, DriverQualified};
use vk_contracts::labels::{Clearance, Scope};
use vk_contracts::ledger::LedgerEvent;
use vk_contracts::plan::Qualification;
use vk_contracts::principal::Principal;
use vk_contracts::syscalls::{Ctx, Kernel, KernelError};
use vk_contracts::testing::KernelTestHooks;
use vk_contracts::{hash_bytes, hash_canonical};
use vk_kernel::learn::{answer_choice, ANSWER_RULE};
use vk_kernel::RealKernel;
```

followed by `open`, `machine`, `human` (device `phone-1`), `events_of` and `mock_manifest` copied from `crates/vk-kernel/tests/drivers.rs` (49–92, 236–275), and:

```rust
fn word_com() -> DriverManifest {
    serde_json::from_str(include_str!("../../../contracts/examples/driver_manifest/valid/word_com.json")).unwrap()
}

fn browser() -> DriverManifest {
    serde_json::from_str(include_str!(
        "../../../contracts/examples/driver_manifest/valid/identity-frozen-2026-10-17.json"
    ))
    .unwrap()
}

fn learned(driver_id: &str) -> DriverLearned {
    serde_json::from_value(json!({
        "family": "office-document", "app": "word",
        "catalogue_sha256": format!("sha256:{}", "2".repeat(64)), "ontology_sha256": format!("sha256:{}", "1".repeat(64)),
        "corpus_schema_sha256": format!("sha256:{}", "4".repeat(64)), "invariants_sha256": format!("sha256:{}", "3".repeat(64)),
        "plan_sha256": format!("sha256:{}", "5".repeat(64)), "driver_id": driver_id, "by": "whoever"
    }))
    .unwrap()
}

const TEN: Qualification = Qualification { min_passes: 10, min_rate: 1.0 };

fn tie() -> Value {
    json!({"invariant": "body_font", "kind": "font_run", "candidates": [
        {"value": {"name": "Garamond", "size": 11}, "support": 0.7},
        {"value": {"name": "Calibri", "size": 12}, "support": 0.3}]})
}

#[test]
fn the_answer_rule_takes_a_candidate_and_nothing_else() {
    let (garamond, calibri) = (json!({"name": "Garamond", "size": 11}), json!({"name": "Calibri", "size": 12}));
    let amb = tie();
    assert_eq!(answer_choice(r#"{"invariant": "body_font", "value": {"name": "Calibri", "size": 12}}"#, &amb), Some(calibri));
    assert_eq!(answer_choice(r#"Je choisis {"size": 11, "name": "Garamond"}."#, &amb), Some(garamond), "key order is not meaning");
    assert_eq!(answer_choice(r#"{"invariant": "body_font", "value": {"name": "Comic Sans MS", "size": 40}}"#, &amb), None);
    assert_eq!(answer_choice(r#"{"invariant": "title_style", "value": {"name": "Calibri", "size": 12}}"#, &amb), None);
    assert_eq!(answer_choice("Garamond", &amb), None);
    assert_eq!(answer_choice("", &amb), None);
    let tokens = json!({"invariant": "footer_text", "kind": "footer", "candidates": [
        {"value": "<redacted:sha256:aa>", "support": 0.5}, {"value": "<redacted:sha256:bb>", "support": 0.5}]});
    assert_eq!(answer_choice(&format!("PLAN: ROLE: plan\nGOAL: {tokens}"), &tokens), None, "an echo names no string");
    assert_eq!(answer_choice(r#"{"invariant": "footer_text", "value": "<redacted:sha256:bb>"}"#, &tokens), Some(json!("<redacted:sha256:bb>")));
}

#[test]
fn learn_ambiguity_calls_infer_once_on_a_content_free_register() {
    let d = tempfile::tempdir().unwrap();
    let mut k = open(d.path());
    let arch = k.register_arch(mock_manifest("mock"));
    let chosen = k.learn_ambiguity(&human(1), &arch, &tie()).unwrap();
    assert_eq!(chosen, Some(json!({"name": "Garamond", "size": 11})), "the mock's echo carries the first candidate (L-P20)");
    assert_eq!(events_of(&k, "task.submitted").len(), 1);
    assert!(!events_of(&k, "infer").is_empty());
    let tasks = k.tasks(&human(2)).unwrap();
    assert_eq!(tasks.len(), 1);
    let reg = k.read_register(&human(3), &tasks[0].register).unwrap();
    assert_eq!(reg.goal, serde_json::to_string(&tie()).unwrap(), "the ambiguity and nothing else");
    assert_eq!((reg.constraints.clone(), reg.evidence.len()), (vec![ANSWER_RULE.to_string()], 0));
    assert_eq!(reg.label.scope, Scope::Business);
}

#[test]
fn driver_learned_is_a_human_act_and_qualified_needs_a_learned_driver() {
    let d = tempfile::tempdir().unwrap();
    let mut k = open(d.path());
    let id = word_com().driver_id();
    let e = k.driver_learned(&machine(1), learned(&id), "word-maison", d.path(), TEN).unwrap_err();
    assert!(matches!(e, KernelError::I1(_)), "{e}");
    let record = d.path().join("qualification.json");
    std::fs::write(&record, b"{}\n").unwrap();
    let q = DriverQualified { driver_id: id.clone(), passes: 10, failures: 0, qualification_sha256: hash_bytes(b"{}\n") };
    let e = k.driver_qualified(&machine(2), q, &record).unwrap_err().to_string();
    assert!(e.contains("was not learned on this node; remedy: vk driver learn"), "{e}");
    k.driver_learned(&human(3), learned(&id), "word-maison", d.path(), TEN).unwrap();
    let rows = events_of(&k, "driver.learned");
    assert_eq!(rows.len(), 1);
    let by_the_device = DriverLearned { by: "phone-1".into(), ..learned(&id) };
    assert_eq!(rows[0].payload_hash, hash_canonical(&by_the_device), "by is the principal's, not the caller's");
    let row = k.learned(&id).unwrap().expect("the learned row");
    assert_eq!((row.name.as_str(), row.qualification.min_passes, row.qualified.is_none()), ("word-maison", 10, true));
}

#[test]
fn driver_qualified_then_the_com_gate_reads_the_record() {
    let d = tempfile::tempdir().unwrap();
    let mut k = open(d.path());
    let m = word_com();
    let id = m.driver_id();
    let e = k.check_qualified(&m).unwrap_err().to_string();
    assert!(e.contains("is not qualified; remedy: vk driver qualify"), "{e}");
    k.driver_learned(&human(1), learned(&id), "word-maison", d.path(), TEN).unwrap();
    assert!(k.check_qualified(&m).unwrap_err().to_string().contains("is not qualified"));
    let record = d.path().join("qualification.json");
    let write = |text: &str| {
        std::fs::write(&record, text).unwrap();
        hash_bytes(text.as_bytes())
    };
    let q = DriverQualified { driver_id: id.clone(), passes: 10, failures: 0, qualification_sha256: write("{\"passes\": 10}\n") };
    k.driver_qualified(&machine(2), q.clone(), &record).unwrap();
    assert_eq!(events_of(&k, "driver.qualified").len(), 1);
    k.check_qualified(&m).unwrap();
    write("{\"passes\": 3}\n");
    let e = k.check_qualified(&m).unwrap_err().to_string();
    assert!(e.contains("qualification.json changed since vk driver qualify"), "{e}");
    // Under the thresholds: recorded (spec §6), and refused at mount.
    let nine = DriverQualified { passes: 9, failures: 1, qualification_sha256: write("{\"passes\": 9}\n"), ..q };
    k.driver_qualified(&machine(3), nine, &record).unwrap();
    let e = k.check_qualified(&m).unwrap_err().to_string();
    assert!(e.contains("9 passes of 10"), "{e}");
    k.check_qualified(&browser()).unwrap();
}
```

`crates/vk-ipc/src/capital.rs`, beside the test at 1143–1167:

```rust
    /// A learned driver vendors its own `vkdriver` and carries no `pack.json`
    /// (ruling L-P9): it packs with its own copy alone, the shared library
    /// beside it is not copied, and no repository (`Cargo.toml`) is needed.
    #[test]
    fn a_driver_that_vendors_its_own_vkdriver_packs_it_alone_without_a_repository() {
        let t = tempfile::tempdir().unwrap();
        let drv = t.path().join("drivers").join("word-maison");
        write(&drv.join("driver.py"), "");
        write(&drv.join("vendor").join("vkdriver").join("protocol.py"), "vendored");
        write(&t.path().join("drivers").join("vkdriver").join("protocol.py"), "repository");
        let dest = t.path().join("files");
        stage_files(&drv, &dest).unwrap();
        assert_eq!(files_under(&dest), ["driver.py", "vendor/vkdriver/protocol.py"]);
        assert_eq!(std::fs::read_to_string(dest.join("vendor/vkdriver/protocol.py")).unwrap(), "vendored");
    }
```

`crates/vk-cli/src/render.rs`, in its tests module (ruling L-P27: `vk driver show` says the kind; the JSON output already carries the manifest):

```rust
    #[test]
    fn vk_driver_show_says_the_kind() {
        let shown = driver(&json!({"driver_id": "sha256:1", "state": {"state": "ready"}, "root": "C:\\d",
                                   "manifest": {"name": "word-maison", "kind": "com", "governed": false, "actions": []}}));
        assert!(shown.lines().any(|l| l.split_whitespace().eq(["kind", "com"])), "{shown}");
    }
```

`crates/vk-cli/tests/smoke.rs`, helpers beside `python_on_path` (2637) and two tests:

```rust
fn repo_drivers() -> PathBuf {
    std::path::absolute(std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../drivers")).unwrap()
}

fn vkforge_venv_python() -> PathBuf {
    PathBuf::from(std::env::var_os("LOCALAPPDATA").unwrap_or_default())
        .join("vk").join("venvs").join("vkforge").join("Scripts").join("python.exe")
}

/// `VK_FORGE_PYTHON`, else the vkforge virtualenv's interpreter (L-P12).
fn forge_python() -> PathBuf {
    std::env::var_os("VK_FORGE_PYTHON").map(PathBuf::from).unwrap_or_else(vkforge_venv_python)
}

/// The vkforge interpreter imports lxml and comtypes and the virtualenv exists; else says so (L-P18).
fn forge_available() -> bool {
    let imports = Command::new(forge_python())
        .args(["-I", "-c", "import lxml, comtypes"])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok_and(|s| s.success());
    if !(imports && vkforge_venv_python().is_file()) {
        eprintln!("vkforge: no interpreter with lxml and comtypes, or no virtualenv; skipping");
        return false;
    }
    true
}

/// A `vkd` with vkforge (`--vkforge-root`, `VK_FORGE_PYTHON`) and its test seam (`VK_FORGE_FIXTURES=1`),
/// over its own temporary state directory, and the shell that drives it.
fn forge_node() -> (tempfile::TempDir, Daemon, Shell) {
    let dir = tempfile::tempdir().unwrap();
    vk_contracts::testing::guard_state_dir(dir.path());
    let endpoint = vk_ipc::transport::test_endpoint().0;
    let root = repo_drivers();
    let mut cmd = vkd_cmd(dir.path(), &endpoint, &["--vkforge-root", root.to_str().unwrap()]);
    cmd.env("VK_FORGE_FIXTURES", "1").env("VK_FORGE_PYTHON", forge_python());
    let daemon = Daemon(cmd.stdout(Stdio::null()).stderr(Stdio::null()).spawn().expect("spawn vkd"));
    let sh = Shell { endpoint, node_key: dir.path().join("node.key") };
    wait_until(&sh, true, "vkd did not answer").expect("status");
    (dir, daemon, sh)
}

/// drivers/vkforge/tests/ooxml_fixtures.py DIR GARAMOND CALIBRI.
fn ooxml_corpus(dir: &std::path::Path, garamond: u32, calibri: u32) -> PathBuf {
    let script = repo_drivers().join("vkforge/tests/ooxml_fixtures.py");
    assert!(Command::new(forge_python())
        .arg("-I").arg(&script).arg(dir).arg(garamond.to_string()).arg(calibri.to_string())
        .status().unwrap().success());
    dir.to_path_buf()
}

#[test]
fn vk_forge_learn_then_qualify_then_mount_from_the_cli() {
    if !forge_available() {
        return;
    }
    let (dir, _daemon, sh) = forge_node();
    let corpus = ooxml_corpus(&dir.path().join("corpus"), 10, 0);
    let out = dir.path().join("word-maison");
    let (c, o) = (corpus.to_str().unwrap(), out.to_str().unwrap());
    let learned = sh.ok(&["driver", "learn", "--family", "office-document", "--app", "word", "--corpus", c, "--out", o]);
    assert!(learned.contains("learned word-maison"), "{learned}");
    let shown = sh.ok(&["driver", "plan", "show", o]);
    for needle in ["appliquer_style_maison", "set_font_run", "post: title_style, body_font, footer_text, pdf_exists"] {
        assert!(shown.contains(needle), "{needle}:\n{shown}");
    }
    let driver = out.join("driver");
    let qualified = sh.ok(&["driver", "qualify", "--driver", driver.to_str().unwrap(), "--corpus", c]);
    assert!(qualified.contains("10/10 passes"), "{qualified}");
    sh.ok(&["mount", "driver", "--manifest", driver.join("manifest.json").to_str().unwrap()]);
    let row = sh.json(&["driver", "show", "word-maison", "--json"]);
    assert_eq!(row["manifest"]["kind"], "com", "{row}"); // driver.mounted carries no kind (L-P11)
    let tail = sh.json(&["dmesg", "-n", "60", "--json"]);
    let kinds = dmesg_kinds(&tail);
    let at = |k: &str| kinds.iter().position(|x| *x == k).unwrap_or_else(|| panic!("no {k} in {kinds:?}"));
    assert!(at("driver.learned") < at("driver.qualified") && at("driver.qualified") < at("driver.mounted"));
    assert!(!kinds.contains(&"infer"), "zero model tokens on the nominal path");
    sh.ok(&["driver", "umount", "word-maison"]);
}

#[test]
fn vk_forge_plan_show_prints_the_actions_and_the_ambiguities() {
    if !forge_available() {
        return;
    }
    let (dir, _daemon, sh) = forge_node();
    let corpus = ooxml_corpus(&dir.path().join("corpus"), 7, 3);
    let out = dir.path().join("word-maison");
    let (c, o) = (corpus.to_str().unwrap(), out.to_str().unwrap());
    let learned = sh.ok(&["driver", "learn", "--family", "office-document", "--app", "word", "--corpus", c, "--out", o]);
    assert!(learned.contains("ambiguities: body_font purged"), "{learned}");
    let shown = sh.ok(&["driver", "plan", "show", o]);
    assert!(shown.contains("appliquer_style_maison (reversible: no)"), "{shown}");
    assert!(shown.contains("1. open(path=\"$arg.document\")"), "{shown}");
    assert!(!shown.contains("set_font_run"), "a purged ambiguity drives nothing:\n{shown}");
    assert!(shown.contains("ambiguities:\n  body_font  purged"), "{shown}");
    assert!(shown.contains("excluded: none"), "{shown}");
}
```

- [ ] **Step 2: Run** `cargo test --locked -p vk-kernel --test learn` → compile errors (no `vk_kernel::learn`); `cargo test --locked -p vk-ipc --test forge` → compile errors (no `common`, no `forge`); `cargo test --locked -p vk-cli vk_driver_show_says_the_kind` → FAIL (no `kind` row).

- [ ] **Step 3: Implement** as specified: `vk-store` `TABLES` gains `"learned"` (comment: one row per learned driver keyed by `driver_id`, typed `vk_kernel::learn::LearnedRow`); `learn.rs`; the `check_qualified` calls; `forge.rs` (`learn`, `qualify`, `plan_show`, `run_stage`); the `server.rs` arms, placed after the `driver.pack` arm:

```rust
    // vkforge (driver learning, plan Task 9; ruling L-P17): dispatched here,
    // before the general lock, as driver.pack is. Each stage is a process
    // that runs for seconds to minutes on this connection's blocking thread;
    // the kernel lock is taken only in short phases inside.
    if req.method == "driver.learn" {
        let ctx = {
            let k = lock(kernel)?;
            ctx_for(&k, presence.take(), now)?
        };
        if !ctx.principal.is_human() {
            return Err(invariant(
                "I1: learning a driver writes code this node will run, which is a human act; \
                 re-run it with a presence proof",
            ));
        }
        let (cfg, state_dir) = forge_context(kernel, config)?;
        return crate::forge::learn(kernel, cfg, &state_dir, &ctx, req.params);
    }
    if req.method == "driver.qualify" {
        // Kernel-written (`driver.qualified`), no presence required; the `Ctx` is built here as
        // for driver.pack, in one short lock phase (ruling L-P25).
        let ctx = {
            let k = lock(kernel)?;
            ctx_for(&k, presence.take(), now)?
        };
        let (cfg, state_dir) = forge_context(kernel, config)?;
        return crate::forge::qualify(kernel, cfg, &state_dir, &ctx, req.params);
    }
    if req.method == "driver.plan_show" {
        return crate::forge::plan_show(req.params);
    }
```

`stage_files` (L-P9), right after `if let Some(plan) = plan { return vendor(&plan, dest); }`:

```rust
    // A learned driver vendors its own vkdriver and has no pack.json (ruling
    // L-P9): nothing to copy beside it, and no repository is asked for.
    if dest.join("vendor").join("vkdriver").is_dir() {
        return Ok(());
    }
```

`ctx_for`'s `fn` becomes `pub(crate) fn` (L-P25); the CLI and its renders — `render::driver` gains the `kind` row right after `name` (L-P27):

```rust
    let mut rows = vec![
        ("name", text(&m["name"])),
        ("kind", text(&m["kind"])),
        ("driver_id", text(&v["driver_id"])),
        ("state", text(&v["state"]["state"])),
    ];
```

— `vkd`, `setup.ps1`; `tests/capital.rs` on `tests/common/mod.rs`.

- [ ] **Step 4:** `cargo test --locked -p vk-store -p vk-kernel -p vk-ipc -p vk-cli` green (the forge and smoke tests run where `forge_available` says yes, and say why on stderr where not); `cargo clippy --workspace --all-targets -- -D warnings` clean. **Commit** `feat(kernel,ipc,cli): vk driver learn and qualify — vkforge under the Job governor, the one model call, the qualification gate at mount`

---

### Task 10: The fallback `--deliberate`, the CI job `driver-learn`, docs and TCB (Sonnet)

**Model:** Sonnet.

**Files:**
- Modify: `crates/vk-ipc/src/forge.rs` (`deliberate`, reached from `learn` when `deliberate` is given), `crates/vk-ipc/tests/forge.rs` (one test), `crates/vk-cli/src/main.rs` (`DriverCmd::Learn`: `--deliberate VERB`), `crates/vk-cli/src/render.rs` (`proposed`), `.github/workflows/ci.yml` (job `driver-learn`, after `driver-install`), `scripts/check-workflows.py` (`EXPECTED_JOBS`, line 29: `driver-learn`), `docs/technical/technical-readme.md` (new section "Learned drivers"), `contracts/tcb.md` §Drivers (the sentences of Step 4, the L-P32 deviation and spec §5.5's table, rulings L-P31, L-P32, L-P34), `README.md` (status table)
- Create: `docs/technical/runbooks/learn-office-driver.md`

**Interfaces:**
- Consumes: `forge::learn`, `ForgeConfig`, `forge_context`, the `common` test module (`Node`, `forge_available`, `ooxml_corpus`, `repo_root`, `call_error`) and `learn_params` of `tests/forge.rs` (Task 9); `vk_kernel::tasks::StepKind::Draft`, `RealKernel::{create_task, run_task_step}`, `Kernel::read_register` (master); the ontology file (Task 4).
- Produces:
  - `driver.learn` with `deliberate: VERB` (presence): params `{family, app, out, arch, deliberate}` — `corpus` not required, `arch` required (`--deliberate needs --arch: a proposal is drafted by an arch; remedy: --arch NAME`), `out` a learn's output holding `catalogue.json` (`out <dir> holds no catalogue.json; remedy: --out names a learned driver's output`), the verb in the ontology. One task (`create_task` + `run_task_step`, one short lock phase) with one `StepKind::Draft { arch_id }` step over a register whose goal is `propose a projection rule as JSON {"member": "Type.Member", "constants": "<Enum>" | null} for the verb <verb> on <app>`, constraint the verb's ontology entry (JSON, read from `<cfg.root>/vkforge/ontology/office-document.json`), evidence the catalogue slice — the members of the types the verb's patterns name for any app and this catalogue has, else the catalogue's type names alone, at most 20 kB — label scope `business`; no harness, no application driving (L-P3). Written to `<out>/proposals/<verb>.json`: `{"verb", "app", "proposed_by": "arch:<id>", "task": <task id>, "rule": {"member", "constants"} | null, "answer": <the arch's answer verbatim, the Draft step's decision "draft: <text>", at most 2 000 characters>}` (ruling L-P29: the decision as `arch::raise` pushed it, `crates/vk-kernel/src/tasks.rs` 917; `rule` is the first JSON object of the answer with exactly those two keys, else null); nothing is written to the ontology, to `driver.py` or to the ledger beyond the task's own rows. → `{proposal: <that object>, path}`.
  - CLI: `Learn` gains `#[arg(long, value_name = "VERB", requires = "arch")] deliberate: Option<String>`, and `corpus` becomes `Option<PathBuf>` with `required_unless_present = "deliberate"`; `vk driver learn --deliberate VERB --app A --out DIR --arch NAME --family office-document`. Render `proposed`: `proposal for <verb> on <app> by <proposed_by> -> <path>`, then `rule: <member> / <constants>` or `rule: none (read the answer in the file)`, then `nothing applied: a human versions a rule into the ontology`.
  - CI job `driver-learn` (Windows): the vkforge suite, the generated-driver vendor test, and the three Rust suites, through the vkforge virtualenv.

- [ ] **Step 1: Failing tests** — `scripts/check-workflows.py`, first: `"ci.yml": {"test", "arch-ollama", "driver-browser", "driver-install", "driver-learn", "sign"}` (fails until the job exists). Then `crates/vk-ipc/tests/forge.rs`:

```rust
#[tokio::test]
async fn deliberate_writes_a_proposal_and_touches_nothing_else() {
    if !forge_available() {
        return;
    }
    let node = Node::start().await;
    let arch = node.call("arch.mount_mock", json!({"name": "mock"})).await.unwrap()["arch_id"]
        .as_str()
        .unwrap()
        .to_string();
    let corpus = ooxml_corpus(&node.dir().join("corpus"), 10, 0);
    let out = node.dir().join("word-maison");
    node.call_human("driver.learn", learn_params(&corpus, &out)).await.unwrap();
    let ontology = repo_root().join("drivers/vkforge/ontology/office-document.json");
    let hash_of = |p: &Path| hash_bytes(&std::fs::read(p).unwrap());
    let (ontology_before, driver_before) = (hash_of(&ontology), hash_of(&out.join("driver/driver.py")));
    let ask = json!({"family": "office-document", "app": "word", "out": out, "deliberate": "set_slide_layout"});
    let e = node.call_human("driver.learn", ask.clone()).await.unwrap_err();
    assert!(call_error(&e).message.contains("--deliberate needs --arch"), "{e}");
    let mut with_arch = ask;
    with_arch["arch"] = json!(arch);
    let r = node.call_human("driver.learn", with_arch).await.unwrap();
    let proposal: Value =
        serde_json::from_slice(&std::fs::read(out.join("proposals/set_slide_layout.json")).unwrap()).unwrap();
    assert_eq!(proposal["proposed_by"], json!(format!("arch:{arch}")));
    assert_eq!((proposal["verb"].as_str(), proposal["app"].as_str()), (Some("set_slide_layout"), Some("word")));
    assert_eq!(proposal["rule"], Value::Null, "the mock's echo is no {member, constants} rule");
    assert!(proposal["answer"].as_str().unwrap().starts_with("draft: "), "the Draft decision, verbatim (L-P29): {proposal}");
    assert_eq!(r["proposal"], proposal);
    assert_eq!(hash_of(&ontology), ontology_before, "the ontology is a human's to edit");
    assert_eq!(hash_of(&out.join("driver/driver.py")), driver_before, "never code");
    assert_eq!(node.events("driver.learned").len(), 1, "a proposal is not a learned driver");
    node.shutdown().await;
}
```

- [ ] **Step 2: The job** (`.github/workflows/ci.yml`, after `driver-install`):

```yaml
  driver-learn:
    # The learning pipeline end to end on Windows, without Office: the
    # recorded catalogues, the fake application, a generated corpus; then the
    # kernel path (learn, qualify, mount) through the daemon and the CLI.
    # Everything Python runs under the vkforge virtualenv setup.ps1 makes, the
    # one a generated driver's .venv links to (L-P12).
    runs-on: windows-latest
    timeout-minutes: 60
    env:
      CARGO_PROFILE_DEV_DEBUG: "0"
      CARGO_INCREMENTAL: "0"
      VK_FORGE_FIXTURES: "1"
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
      # Same pin as the `test` job: a Windows-native Perl for the vendored
      # OpenSSL that vk-web builds.
      - name: A Windows-native Perl for the vendored OpenSSL
        shell: pwsh
        run: |
          $perl = (Get-Command perl.exe -ErrorAction Stop).Source
          $os = & $perl -e 'print $^O'
          if ($os -ne 'MSWin32') {
            Write-Output "::error title=perl::$perl reports '$os'; OpenSSL's Configure needs an MSWin32 Perl"
            exit 1
          }
          "OPENSSL_SRC_PERL=$perl" | Out-File -FilePath $env:GITHUB_ENV -Append -Encoding utf8
          Write-Output "OPENSSL_SRC_PERL=$perl ($os)"
      - name: The vkforge virtualenv, as on the node (drivers/vkforge/setup.ps1)
        shell: pwsh
        run: |
          & .\drivers\vkforge\setup.ps1 -Python (Get-Command python).Source
          "VK_FORGE_PYTHON=$env:LOCALAPPDATA\vk\venvs\vkforge\Scripts\python.exe" | Out-File -FilePath $env:GITHUB_ENV -Append -Encoding utf8
      - name: vkforge suite (recorded catalogues and the fake application)
        shell: pwsh
        run: |
          & $env:VK_FORGE_PYTHON -I -m unittest discover -s drivers/vkforge/tests -t .
      - name: vkforge runs as the kernel spawns it (the script path under -I, never -m)
        shell: pwsh
        run: |
          & $env:VK_FORGE_PYTHON -I drivers/vkforge/__main__.py --help
      - name: A generated driver runs alone, vkdriver and its COM adapter from its own vendor/ (L-P6, L-P19)
        shell: pwsh
        run: |
          & $env:VK_FORGE_PYTHON -I -m unittest discover -s drivers/vkforge/tests -t . -p test_generate.py -k vendor
      - name: Build
        run: cargo build --locked -p vk-cli -p vkd -p vk-mcp
      - name: The kernel's side (the answer rule, the ambiguity call, the gate)
        run: cargo test --locked -p vk-kernel --test learn -- --nocapture
      - name: learn → qualify → mount through the daemon
        run: cargo test --locked -p vk-ipc --test forge -- --nocapture
      - name: The CLI end to end (the vk_forge_ smoke tests, L-P18)
        run: cargo test --locked -p vk-cli --test smoke vk_forge_ -- --nocapture
```

- [ ] **Step 3: Implement** `forge::deliberate` and its CLI as the interface says.

- [ ] **Step 4: Docs** — `docs/technical/technical-readme.md`, a section "Learned drivers": the six stages and their artefacts (names of Task 9's step list), the seams (`VK_FORGE_FIXTURES`, `VK_FORGE_PYTHON`, `--vkforge-root`, `<work>/fake.json` written by `qualify --fake` only), the `learned` table and the mount gate, the deliberate fallback. `contracts/tcb.md` §Drivers: spec §11's five sentences, plus L-P1/L-P19 (the fake is a test double of the adapter, reachable only through a `fake.json` that `vkforge qualify --fake` writes into its own work directory, never in a mount's), L-P2 (a governed one-shot process; the Job does not block the network, the package opens no socket, pinned by a test), L-P3 (the deliberate fallback proposes, never traces nor writes code), L-P4 (the qualification gate at mount for `kind: com`, on the hash of the record the kernel stored), L-P22 (a learned driver carries the hash of the type library it was learned on in its `binding.json`, hence in its `driver_id`, and refuses every action with `precondition` "office version changed" on another; the kernel itself never loads COM), L-P31 (the fake application implements Word `.docx` only: a PowerPoint driver is qualified against the real PowerPoint, on the node), L-P33 (a dialog box raised during an action is closed, never answered, and the action refuses `dialog`). Then the deviation of L-P32, in these words: "Spec §2 L8 and §5.2 — a learned driver runs supervised, every act approved: this holds for the **mounted** learned driver, each `vk_driver_act` being a job step under the harness. `vk driver qualify` runs the driver directly, as the child of its stage inside the stage's Job (the governor), not under the harness: the replay is on copies, and its record is what the mount checks." And spec §5.5's data-boundary table, reproduced as it stands (ruling L-P34):

```markdown
| Actor | Sees | Never sees |
|---|---|---|
| Miner (code) | the OOXML formatting parts | `document.xml` beyond formatting attributes |
| Arch (model) | `plan.ambiguities`: kinds, supports, numeric values, hashes | content, file names, free strings |
| Generated driver (on the node) | the clear values its actions need (footer text) | the original corpus |
| Ledger | hashes and counts | any artefact in clear |
```

`README.md`: one status row. `docs/technical/runbooks/learn-office-driver.md`, in French, the register of `docs/technical/runbooks/recette-maskai.md` (firmware analogies kept): `setup.ps1`; the daemon started with `--vkforge-root`; `vk driver learn`, `vk driver plan show`, `vk driver qualify`, `vk mount driver`, and `vk driver learn --deliberate`, each with its expected line (the render texts of Tasks 9 and 10).

- [ ] **Step 5:** `python -I scripts/check-workflows.py`, `python -I scripts/docs/check_docs.py`, `cargo test --locked -p vk-ipc --test forge` green; push, `gh run watch` (the `driver-learn` job green). **Commit** `feat(vkforge,ci): the deliberate fallback as a proposal; the driver-learn job; learned drivers in the readme, the TCB and a runbook`

---

### Task 11: The debrief stage — vkforge debrief (Opus)

**Model:** Opus.

Founder decision 2026-10-17: the post-launch retro-learning loop becomes a stage of vkforge. Requirements: `docs/technical/debriefs/2026-10-17-maskai-driver.md` §4 (inputs, signals, proposal format, what stays human, the gain on the 17/10 run) and the defects of its §2/§3 the stage must detect. After a governed run the stage reads what the node holds in clear and writes what to change; it changes nothing. Firmware analogy: the post-mortem of a crash dump — the core file and the console are read, a patch is proposed with the addresses that justify it, and a human reflashes.

**Files:**
- Create: `drivers/vkforge/debrief.py`, `drivers/vkforge/tests/test_debrief.py`, `drivers/vkforge/tests/debrief_fixtures.py`, `drivers/vkforge/tests/fixtures/debrief-2026-10-17/journal-essai1.jsonl`, `drivers/vkforge/tests/fixtures/debrief-2026-10-17/driver.log`, `drivers/vkforge/tests/fixtures/debrief-2026-10-17/vkd-console.log`
- Modify: `drivers/vkforge/__main__.py` (`debrief` imported and added to the `STAGES` table), `drivers/vkforge/tests/test_package.py` (Task 0's `STAGE_NAMES` gains `"debrief"`, Step 4), `crates/vk-ipc/src/forge.rs` (`debrief`, `DEBRIEF_DEADLINE`), `crates/vk-ipc/src/server.rs` (`dispatch`: `driver.debrief` before the general kernel lock, after Task 9's `driver.plan_show` arm, ruling L-P17; **not** added to `takes_presence`), `crates/vk-cli/src/main.rs` (`DriverCmd::Debrief { run, out, until }` and its dispatch arm), `crates/vk-cli/src/render.rs` (`debriefed`), `docs/technical/runbooks/recette-maskai.md` (new §16, French), `docs/technical/technical-readme.md` (the "Learned drivers" section of Task 10: one paragraph), `contracts/tcb.md` §Drivers (one sentence)
- Test: `crates/vk-ipc/tests/forge.rs` (two tests), `crates/vk-cli/tests/smoke.rs` (one test)

**Interfaces:**
- Consumes: `vkforge.errors.Refusal` and the stage table `STAGES` of `drivers/vkforge/__main__.py`, built by explicit imports (Task 0); from Task 9, `crate::forge::{ForgeConfig, run_stage}` (`run_stage(cfg: &ForgeConfig, stage: &'static str, args: &[String], work: &Path, deadline: Duration) -> Result<StageRun, RpcError>`) and `crate::server::forge_context(kernel, config) -> Result<(&ForgeConfig, PathBuf), RpcError>` (the daemon's vkforge and its state directory, read in one short lock phase); the test module `crates/vk-ipc/tests/common/mod.rs` (`Node` with `start`, `dir`, `call`, `last_event`, `shutdown`; `forge_available`, `forge_python`, `repo_root`) and, in `crates/vk-cli/tests/smoke.rs`, `forge_node() -> (tempfile::TempDir, Daemon, Shell)`, `forge_available()`, `forge_python()`; `vk_contracts::hash_bytes`; `crate::server::{bad, internal}`.
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

- Produces: the stage `python -I drivers/vkforge/__main__.py debrief --run DIR --out DIR [--until TASK]` (a stage like the others: `add_arguments` and `run`, nothing registered at import); `debrief.debrief(run: Path, until: str | None = None) -> (signals: dict, proposals: list, markdown: str)` and `debrief.write(signals, proposals, markdown, out: Path)` (refuses a non-empty `out`); the files `debrief.json` (signals, sorted keys, no absolute path, no timestamp of its own), `proposals.json`, `debrief.md` (sections 1–3 of a debrief in the register of the 17/10 one; §4 and §5 stay human); IPC `driver.debrief {run, out, until?}` → `{out, proposals: <n>, list: [{kind, target, confidence, proposal}], debrief_sha256, proposals_sha256}`; CLI `vk driver debrief --run DIR --out DIR [--until TASK]`.
- Proposal shape — the list form of the deliberate-run proposal (Task 10, spec §5.3: one object, `proposed_by`), here with `proposed_by: "debrief"` and the evidence lines:

```json
{"kind": "selector", "target": "assistant_message",
 "evidence": [{"at": "journal-essai1.jsonl:3", "task": "task-node-1-2", "text": "<the line, at most 240 characters>"}],
 "evidence_total": 2, "proposal": "<what to change, in French, the debrief's register>",
 "confidence": "high", "tasks": ["task-node-1-2"], "proposed_by": "debrief",
 "human": "<who applies it and what it costs: new driver_id, new inputs_sha256, a recipe commit>"}
```
`kind` ∈ {`selector`, `modal`, `budget`, `oracle`, `recipe`}; `confidence` ∈ {`high`, `medium`, `low`} (`low` is reserved: none of the six rules below emits it); `tasks` in run order.
- **Not a human act.** `driver.debrief` reads evidence and writes three files: a machine act, no presence proof, run as a governed one-shot vkforge process exactly like `learn`'s stages (L-P2: the Job governor, a deadline of 120 s, from the `drivers/` root), dispatched before the general kernel lock (L-P17). `driver.learn` stays vkforge's only human act.
- **Ledger: none** (founder decision 2026-10-18, ruling L-P5): no new kind, the three files only. The IPC answer carries `debrief_sha256` and `proposals_sha256`, so whoever applies a proposal can name the evidence that motivated it.
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

- [ ] **Step 2: Failing tests** (`tests/test_debrief.py`; the stage is called in-process, the CLI entry through `vkforge.__main__.main`; Task 0's test covers the script form the kernel spawns)

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
```

and in `__main__.py`, the import and the table (the module registers nothing itself):

```python
from vkforge import debrief, generate, inventory, mine, ontology, plan, qualify  # noqa: E402

STAGES = {"inventory": inventory, "bind": ontology, "mine": mine, "plan": plan, "generate": generate,
          "qualify": qualify, "debrief": debrief}
```

and in `drivers/vkforge/tests/test_package.py` (Task 0's), the stage list both of its stage tests read — `test_the_cli_lists_its_stages_and_refuses_an_unknown_one_from_any_directory` and `test_the_stage_table_is_built_in_main_and_no_stage_registers_itself` — gains `"debrief"`, in its sorted place:

```python
STAGE_NAMES = ["bind", "debrief", "generate", "inventory", "mine", "plan", "qualify"]
```

- [ ] **Step 5: Run** the vkforge suite green.

- [ ] **Step 6: Failing Rust tests.** `crates/vk-ipc/tests/forge.rs` (Task 9's file; `Node`, `forge_available`, `forge_python` and `repo_root` come from `crates/vk-ipc/tests/common/mod.rs`, which Task 9 created, and the file already starts with `mod common; use common::*;` and imports `json`, `Value`, `Path`, `PathBuf`, `hash_bytes`):

```rust
/// A run directory from `drivers/vkforge/tests/debrief_fixtures.py`: "17-10" or "clean".
fn debrief_run(dir: &Path, which: &str) -> PathBuf {
    let script = repo_root().join("drivers/vkforge/tests/debrief_fixtures.py");
    let ok = std::process::Command::new(forge_python())
        .arg("-I")
        .arg(&script)
        .arg(dir)
        .arg(which)
        .status()
        .expect("run the fixture builder")
        .success();
    assert!(ok, "debrief_fixtures.py {which}");
    dir.to_path_buf()
}

#[tokio::test]
async fn debrief_is_a_machine_act_that_writes_three_files_and_no_ledger_row() {
    if !forge_available() {
        return;
    }
    let node = Node::start().await;
    let run = debrief_run(&node.dir().join("run"), "17-10");
    let out = node.dir().join("debrief");
    let before = node.last_event().seq;
    let r = node.call("driver.debrief", json!({"run": run, "out": out})).await.unwrap(); // no presence proof
    for f in ["debrief.json", "proposals.json", "debrief.md"] {
        assert!(out.join(f).is_file(), "{f}");
    }
    assert_eq!(r["proposals"], json!(6));
    assert_eq!(r["list"][0]["target"], json!("assistant_message"));
    assert_eq!(r["proposals_sha256"], json!(hash_bytes(&std::fs::read(out.join("proposals.json")).unwrap())));
    assert_eq!(node.last_event().seq, before, "a debrief writes no ledger row (L-P5)");
    node.shutdown().await;
}

#[tokio::test]
async fn debrief_refuses_a_run_without_journal_and_a_used_out_before_spawning() {
    if !forge_available() {
        return;
    }
    let node = Node::start().await;
    let empty = node.dir().join("empty");
    std::fs::create_dir_all(&empty).unwrap();
    let e = node
        .call("driver.debrief", json!({"run": empty, "out": node.dir().join("o1")}))
        .await
        .unwrap_err()
        .to_string();
    assert!(e.contains("no journal.jsonl") && e.contains("; remedy: "), "{e}");
    let run = debrief_run(&node.dir().join("clean"), "clean");
    let used = node.dir().join("used");
    std::fs::create_dir_all(&used).unwrap();
    std::fs::write(used.join("x"), "x").unwrap();
    let e = node.call("driver.debrief", json!({"run": run, "out": used})).await.unwrap_err().to_string();
    assert!(e.contains("is not empty"), "{e}");
    assert!(!node.dir().join("o1").exists(), "nothing written on a refusal");
    assert!(!node.dir().join("forge").join("debrief").exists(), "both refused before a process was spawned");
    node.shutdown().await;
}
```

`crates/vk-cli/tests/smoke.rs` (`forge_node`, `forge_available` and `forge_python` are Task 9's helpers in that file; the name starts `vk_forge_`, outside the `driver-install` job's filter `vk_driver_`, ruling L-P18):

```rust
#[test]
fn vk_forge_debrief_prints_the_proposals_and_writes_no_ledger_row() {
    if !forge_available() {
        return;
    }
    let (dir, _daemon, sh) = forge_node();
    let run = dir.path().join("run");
    let script = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../drivers/vkforge/tests/debrief_fixtures.py");
    assert!(Command::new(forge_python()).arg("-I").arg(&script).arg(&run).arg("17-10").status().unwrap().success());
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
/// `learn`'s stages. A machine act: no presence proof and no ledger row
/// (ruling L-P5). Refuses before spawning what the stage would refuse, in
/// the kernel's voice.
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

`server.rs` `dispatch`, after Task 9's `driver.plan_show` arm — before the general kernel lock (ruling L-P17), with Task 9's `forge_context` for the `ForgeConfig` and the state directory; `takes_presence` is left as it is:

```rust
    // A machine act: it reads evidence the node holds in clear and writes
    // three files. No presence proof, no ledger row (ruling L-P5); before the
    // general lock, like the other vkforge methods (L-P17).
    if req.method == "driver.debrief" {
        let (cfg, state_dir) = forge_context(kernel, config)?;
        let p = &req.params;
        let path = |key: &str| p[key].as_str().map(PathBuf::from).ok_or_else(|| bad(key));
        return crate::forge::debrief(cfg, &state_dir, &path("run")?, &path("out")?, p["until"].as_str());
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

- [ ] **Step 10:** `python -I -m unittest discover -s drivers/vkforge/tests -t .`, `cargo test --locked -p vk-ipc --test forge`, `cargo test --locked -p vk-cli --test smoke vk_forge_debrief`, `python -I scripts/docs/check_docs.py` green (the `driver-learn` job of Task 10 runs the first three with no change: its smoke filter is `vk_forge_`). **Commit** `feat(vkforge,ipc,cli): the debrief stage — vk driver debrief proposes from a run's evidence, applies nothing, writes no ledger row`

---

### Task 12: The founder's node — exit criteria §8 (controller + founder, manual)

**Model:** the controller and the founder, on the node. **Founder checkpoint.**

- [ ] `powershell -ExecutionPolicy Bypass -File drivers\vkforge\setup.ps1` (uv CPython 3.13, `lxml`, `comtypes`; it reuses the virtualenv of Task 3's Step 6); `& "$env:LOCALAPPDATA\vk\venvs\vkforge\Scripts\python.exe" -I -m unittest discover -s drivers/vkforge/tests -t .` green, the `office_present` tests included (real Word: `test_the_live_type_library_…`, `test_the_adapter_applies_the_house_style_on_the_real_word`).
- [ ] The two catalogues are committed (Task 3, Step 6); the daemon runs with `--vkforge-root <repository>\drivers` and **without** `VK_FORGE_FIXTURES`.
- [ ] A corpus of 10 Word contracts (`python -I drivers\vkforge\tests\ooxml_fixtures.py <dir> 10` on the node, or real anonymised firm documents in a private folder): `vk driver learn --family office-document --app word --corpus … --out dist\word-maison`; `vk driver plan show dist\word-maison` read and approved; `vk driver qualify --driver dist\word-maison\driver --corpus …` → `10/10 passes (0 failures), fake false`, zero tokens (`vk dmesg` shows no `infer` between `driver.learned` and `driver.qualified`), under 15 s per document (the record's `duration_ms`); `vk mount driver --manifest dist\word-maison\driver\manifest.json` mounts; `vk driver show word-maison` shows the row `kind  com` (Task 9's `kind` row, L-P27; L-P11: `driver.mounted` carries no kind).
- [ ] The learned driver runs alone (L-P6, L-P9, L-P19): `dist\word-maison\driver\vendor\vkdriver\protocol.py` is byte-identical to `drivers\vkdriver\protocol.py` and `vendor\vkforge_runtime\com.py` to `drivers\vkforge\runtime\com.py`; there is no `pack.json` and no `fake.py` anywhere under `dist\word-maison\driver`; the qualification and the mount spawned `driver.py` from `dist\…`, with no repository around it.
- [ ] Optional, L-P9: `vk driver pack --manifest dist\word-maison\driver\manifest.json --version 0.1.0 --out dist\pack-word-maison` succeeds without a `pack.json` and its files layer holds `vendor/vkdriver/` once. (The installed copy has another `driver_id`; the gate refuses to mount it until the packaging chantier carries the qualification — see the self-review notes.)
- [ ] A corpus of 10 presentations: the same commands with `--app powerpoint --out dist\ppt-maison`; `git diff --stat` shows no file changed outside `drivers/vkforge/ontology/office-document.json` since the Word run (`runtime/com.py` names no application: the ProgID, `Presentations`, `Open`'s and `Close`'s arguments and `DisplayAlerts` come from the ontology's `applications` block, L-P31); qualification ≥ 9/10 against the real PowerPoint — the fake is Word `.docx` only, so this run is the proof of spec §8.2 (the plan's own thresholds, 10 at rate 1.0, decide whether it mounts).
- [ ] A fictitious document carrying personal data added to a copy of the corpus: `learn` again into `dist\purge-check`; `findstr /s` of each personal string over `dist\purge-check` finds it only under `driver\secrets.json` if it was a footer, nowhere otherwise.
- [ ] CI green on every job, `driver-learn` included; TCB updated; the chantier's SDD ledger closed; plan archived under `docs/archive/technical/plans/` at the merge (filing rule).

---

## Self-review notes (run by the author before handoff; revised 2026-10-18)

- **Spec coverage:** §3.2 components → Tasks 0, 3–9; §4.1 catalogue → 3; §4.2 ontology → 4; §4.3 binding → 4; §4.4 invariants + purge → 5; §4.5 plan → 2 (contract), 6 (producer); §4.6 generated driver → 7; §4.7 qualification → 8; §4.8 ledger → 2, 9; §4.9 contract additions → 1, 2; §4.10 CLI → 9, 10; §5.1–5.2 flows → 9; §5.3 fallback → 10 (L-P3, reduced); §5.4 PowerPoint → 4 (the `applications` block), 6, 7 (no application name in the adapter), 12 (L-P31); §5.5 boundaries → 5, 7, 8, 9, and the table in the TCB → 10 (L-P34); §2 L8 and §5.2 supervision → 10 (declared, L-P32); §6 failure modes → 3 (Office absent), 3/4/7 (Office version differs: the probe, L-P22), 5 (corpus too small, mixed, skipped, every invariant under the threshold), 6 (unbound, ambiguity, bad answer), 7 (unexpected dialog: the guard, L-P33), 7/8 (read-only, postconditions), 8 (corpus changed), 9 (stage failure; thresholds unmet → mount refused); §7 tests → every task; §8 → 12; §11 → 2, 10, 11.
- **Founder decisions not in the spec:** the debrief stage (2026-10-17) → Task 11, from the 17/10 debrief §4 and the defects of its §2/§3: D1, D2, D5, D8, D11 and the maintenance page become proposals; D4, D6, D7, D9, D10, D12, D13 and S15–S17 signals; S14 (the judge's claims against the journal, D3) is out — the judge's text is in the register, not in clear on the node. No ledger row for it (founder 2026-10-18, L-P5). Fixture outcome: three of the four manual loops of 17/10 answered before their relaunch, one left.
- **Open items of spec §10 settled:** verb list v1 (Task 4, 32 verbs); `lxml==6.1.3` and `comtypes==1.4.17` pinned (Task 0), type library located through the registry (Task 3); threshold default 0.8, one value for every kind, `--threshold` on the CLI (Task 5); no COM server in CI — the fake adapter (L-P1, L-P19, Task 7); `vk driver plan show` format (Task 9's `render::plan_shown`).
- **Plan rulings and where they land:** **L-P1** fake application behind the one adapter (Global Constraints; Tasks 7, 8); **L-P2** `learn` is a governed one-shot process, not a protocol-1 driver (Task 9 `run_stage`); **L-P3** the deliberate fallback is a proposal from the catalogue through one task with one Draft step, no live application driving (Task 10); **L-P4** a `kind: com` driver mounts only with a `driver.qualified` record meeting the plan's thresholds, whose file still hashes to the stored hash (Task 9 `check_qualified`); **L-P5** the debrief is a machine act, writes no ledger row and applies nothing (Task 11); **L-P6** the generated driver vendors `vkdriver` and resolves it vendor-first (Tasks 7, 8, 10, 12); **L-P7** identity on the frozen fixture and the existing `PILOT_ID` (Task 1); **L-P8** per-kind schema rules through `if`/`then`, invalid examples only what the schema refuses (Tasks 1, 2); **L-P9** no `pack.json`, `stage_files` skips the shared copy when `vendor/vkdriver` exists (Tasks 7, 9, 12); **L-P10** `Capability::Plan` for the ambiguity call (Task 9); **L-P11** no kind on `driver.mounted`, tests read the row (Tasks 9, 12); **L-P12** the vkforge virtualenv as interpreter and as the generated driver's `.venv` (Tasks 3, 7, 8, 9, 10); **L-P13** canonical artefacts and hashes, `reversible`/`note`, `plan-excluded.json`, the ambiguity shape, `where`, `qualify`'s signature, the selectors, `--vault`, the gate on the stored hash (Tasks 0, 2, 4–9); **L-P14** stage stubs in Task 0, modified by Tasks 3–8; **L-P15** `vkforge <stage>: …; remedy: …` everywhere (Tasks 0, 5, 9); **L-P16** no `--unqualified`, `MountParams` unchanged (Task 9); **L-P17** the four vkforge methods before the general lock, `Node` in `tests/common/mod.rs` (Tasks 9, 11); **L-P18** `vk_forge_*` smoke tests and the `lxml`/`comtypes` guard (Tasks 9, 10, 11); **L-P19** the adapter vendored, the fake reached only through `<work>/fake.json` (Tasks 7, 8, 10); **L-P20** the mock's echo and the answer rule, no `VK_MOCK_ANSWER` (Task 9); **L-P21** catalogues recorded on the node before Task 4, binding and plan fixtures committed by Tasks 4 and 6 (Tasks 3, 4, 6, 7, 8); **L-P22** (amended) `typelib_sha256` in the catalogue, the binding and the driver's `binding.json`, the probe's `precondition` refusal (Tasks 3, 4, 7, 10); **L-P23** `--deliberate` keeps `--out` and `--arch`, provisional until the Task 2 checkpoint (Task 10); **L-P24** the Fable trailer whatever the model (Always); **L-P25** `ctx: &Ctx` on `forge::learn`/`forge::qualify`, `ctx_for` `pub(crate)` (Task 9); **L-P26** `ForgeConfig::at` infallible, the root refused at the first spawn (Task 9); **L-P27** the `kind` row of `vk driver show` (Tasks 9, 12); **L-P28** five inventory tests wait for the recorded catalogues (Task 3); **L-P29** the proposal's `answer` is the `draft: ` decision verbatim (Task 10); **L-P30** the deadline, the root and the tree kill pinned (Task 9); **L-P31** the `applications` block, `Application(binding)`, a Word-only fake (Tasks 3, 4, 7, 10, 12); **L-P32** supervision holds for the mounted driver, `qualify`'s direct spawn declared (Tasks 8, 10); **L-P33** the per-call guard and the `dialog_on` hook (Tasks 7, 8); **L-P34** spec §5.5's table in the TCB (Task 10).
- **Invocation and stage table** (corrected while writing Task 11, both defects reproduced on CPython 3.13): `python -I -m vkforge` does not find the package (`-I` implies `-P`), so every invocation is `python -I drivers/vkforge/__main__.py <stage>` with `drivers/` put on `sys.path` by `__main__.py` and, for the suite, by `tests/__init__.py` (Tasks 0, 3, 9, 10, 11); `STAGES` is built in `__main__.py` by explicit imports and a stage module only defines `add_arguments` and `run` (Tasks 0, 3–8, 11).
- **Type consistency (exact names, producer → consumers):** `Refusal`, `canon.{dumps, sha256, sha256_file, write}`, `STAGES`, `main` (0) → every task; `inventory.{APPS, catalogue_from_fixture, catalogue_sha256, office_present, write_catalogue}` (3) → 4, 6, 7, 9; `ontology.{load, bind, ontology_sha256, ONTOLOGY}` and the binding shape (4) → 6, 7, 8, 10; `mine.{redact, mine_file, mine_corpus, content_sha256, write_invariants, INVARIANT_IDS}`, `ooxml_fixtures.{docx, pptx, corpus, FOOTER}` (5) → 6, 7, 8, 9; `plan.{build, validate, plan_sha256, write_plan, load_resolver, EMITTED_VERBS, RULES}` (6) → 7, 8, 9; the catalogue's `typelib`/`typelib_sha256` (3) → 4 → the binding's, with `application` (4) → 7; `generate.generate`, `com.{Application, VERBS, guarded, probe, live_typelib_sha256, CALL_DEADLINE_S}`, `fake.{FakeApplication, VERBS}` with `<work>/fake.json`'s `dialog_on`, `ooxml.write_parts` (7) → 8, 9; `qualify.qualify` (8) → 9; `DriverPlan`/`PlanAction`/`PlanStep`/`Ambiguity`/`Qualification`/`plan_sha256`, `DriverLearned`/`DriverQualified` (2) → 9, 10; `DriverKind::Com`/`applications` (1) → 7's manifest, 9's gate; `ForgeConfig` (with `stage_deadline`)/`ForgeConfig::at`/`run_stage`/`forge_context`, `forge::{learn, qualify}` with `ctx: &Ctx`, `common::{Node, Node::start_with_forge, forge_available, forge_python, repo_root, ooxml_corpus, call_error}`, smoke `forge_node`/`forge_python`/`forge_available` (9) → 10, 11; `LearnedRow`, `check_qualified`, `learn_ambiguity`, `answer_choice` (9) → 12.
- **Spec statements that differ from the code, left for the founder (not changed here):** (a) "the pipeline is itself run as a driver of the kernel (protocol 1, Job Object)" — protocol 1 is request/response with evidence directories; the plan runs the stages as a governed one-shot process (L-P2); (b) "no network" for governed processes — a Job Object does not block sockets on Windows; the plan pins "no socket in the package" by a test instead; (c) §5.3 "the COM calls are traced" in the deliberate run — deferred (L-P3); (d) §4.6 says `kind: com` driver's `hosts: []` — today's validator refuses empty hosts, hence Task 1's per-kind rule; (e) §3.2 names `vk driver learn --deliberate <verb>` as a human act producing "a proposed projection rule reviewed by a human before it is versioned" — the plan writes the proposal file only; versioning into the ontology stays a manual edit + commit; (f) the spec has no debrief stage — it comes from the founder's decision of 2026-10-17 (`docs/technical/debriefs/2026-10-17-maskai-driver.md` §4), and §3.2's components and §4.10's CLI table should name `debrief` and `vk driver debrief` at the spec's next revision. Added by this revision: (g) §4.4's `title_style.value = "Heading1"` is `{"builtin": "Heading1"}` here (a custom style is `{"custom": <token>}`); (h) §4.5's post entry `pdf exists` is the id `pdf_exists`; (i) §4.10's `vk driver learn --deliberate VERB --app A` needs `--out` (the learn output holding the catalogue, where the proposal is written) and `--arch` here (L-P23, provisional: the founder confirms it at the Task 2 checkpoint); (j) §7's "fake COM server registered for the job" is the fake adapter (L-P1, L-P19). Added on the rescan: (k) §2 L8 and §5.2 — "supervised, every act approved" and "each copy processed by the driver under the harness" hold for the mounted learned driver (every `vk_driver_act` is a job step under the harness); `qualify` spawns the driver directly, inside its stage's Job, as a stage (L-P32, declared in the TCB by Task 10); (l) §6's "catalogue hash changes → driver regenerated, never mounted on the old binding" is met at act time, not at mount: the kernel never loads COM, so the generated driver's probe refuses `precondition` "office version changed; remedy: vk driver qualify" on another type library (L-P22) — a re-qualification on the new Office then fails every pass, which is what sends the operator back to `vk driver learn`; (m) the fake application is Word `.docx` only, so spec §8.2's PowerPoint run is proven on the node against the real PowerPoint, not in CI (L-P31).
- **Consequences to know (not decided here):** a packed learned driver is installed with another `driver_id` (its `files_sha256` becomes the package fingerprint), so the gate (L-P4) refuses it until the packaging chantier carries the qualification across; `secrets.json` is a driver file and would travel in such a package. The PowerPoint driver of Task 12 may be qualified at 9/10 (spec §8.2) and still be refused at mount by the plan's own thresholds (10 at rate 1.0).
