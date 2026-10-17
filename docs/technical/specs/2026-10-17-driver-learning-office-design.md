# Driver learning from the firm's documents — Design (Office COM, first family)

**Status:** approved section by section in brainstorming (2026-10-17), pending founder review of this document.
**Date:** 2026-10-17
**Scope:** how Aubier produces a driver for a desktop application **without a developer writing it**: a static analysis of the application's programming interface, a deterministic mining of the firm's own documents for the invariants that matter, a typed plan, a deterministic generator, and a qualification run. First family: Office documents through the COM object model (Word, then PowerPoint as the proof of extension). One implementation plan follows this spec.
**Inputs:** the founder's need expression of 2026-10-17 (below); the pilotes spikes A–D of 2026-10-05/10 (`.superpowers/spikes/2026-10-05-pilotes/report.md`, `report-D.md`, private); the pilot spec `2026-10-14-device-driver-contract-and-recette-pilot-design.md` and rulings R1–R28; the packaging spec `2026-10-16-driver-packaging-and-deployment-design.md`; `contracts/tcb.md` §Drivers.

---

## 1. Why this, now

The pilot driver for MaskAI is 1 566 lines of Python written by hand under the driver contract. It proved the contract (hashes, Job Object, protocol 1, human acts), and it proved the cost: every new application would need a developer. The founder's need expression of 2026-10-17: *the system learns the driver by reading the application and the firm's data, by comparative analysis, purging what is useless, for performance, conformity and maintainability; we must not be solicited to build it.* MaskAI is in maintenance, so the proving ground moves to Microsoft 365 on the desktop, where the spikes of October already showed what works: a driver compiled to the application's object model replays 10/10 at 5–7 s with zero tokens, while recorded clicks replay 5/10.

OS analogy, kept: the application's type library is the peripheral's **datasheet**; the firm's documents are the **register trace** that says which registers the firm's programs actually touch; the learned driver is the firmware built from both, in a clean room, loaded by the kernel only after it passed the bench.

## 2. Decisions log (founder, 2026-10-17)

| # | Decision | Notes |
|---|---|---|
| L1 | Source of learning: **static analysis of the application's programming interface first**; observation only chooses and parameterises | over observing the human, over a deliberate model run, over a curated taxonomy alone |
| L2 | First surface: **COM object model of the desktop Office applications**; Microsoft Graph later, on the roadmap | the user's session is the execution context; fits packaging and `vk-agent` |
| L3 | Relevance and purge criterion: **the firm's documents**; an action enters the driver only if the corpus exercises the invariant it produces or verifies | what is kept of the corpus is a schema of invariants, never content |
| L4 | Exit: a Word driver that applies the house style and exports PDF, learned from a corpus of contracts; then a PowerPoint driver produced by the **same engine** from a corpus of presentations, with no application-specific code | spike B's task, 10/10 |
| L5 | The driver's code is emitted by a **deterministic generator from a typed plan**; the model only names ambiguous invariants, on the schema alone | reproducible, hashable, reviewable on the plan, not on the code |
| L6 | Sovereignty: **deterministic local extraction**; only the invariant schema may reach an arch, local or cloud per the mount; content never leaves the node | |
| L7 | Approach 1 with the deliberate run (spike B) as a **bounded fallback** for unbound verbs and unresolved ambiguities, never the main path | |
| L8 | The learned driver runs **supervised** (every act approved) until evidence-gated promotion exists | founder decision of 2026-10-15 kept |

## 3. Architecture

### 3.1 Principle

Learning is an **offline pipeline** that produces a driver under the current driver contract. The kernel never runs a learned driver differently from a hand-written one. The pipeline is itself run as a driver of the kernel (a human act, a ledger row, a hash, a Job): Aubier learns under its own governance.

### 3.2 Components

1. **`drivers/vkforge/`** (Python package beside `drivers/vkdriver/`): the five stages, each a pure module with a file in and a file out, no shared state.
   - `inventory.py` — COM introspection through `comtypes` on the installed type library (not the running application) → `catalogue.json` per application: types, members, signatures, enums, built-in constants.
   - `ontology.py` + `ontology/office-document.json` — the abstract verbs of the "office document" family and their projection rules → `binding-<app>.json`.
   - `mine.py` — deterministic Office Open XML extraction (`zipfile` + `lxml`, no COM, no model) → `invariants.json` with supports; purge threshold as a parameter.
   - `plan.py` — invariants × bound verbs → `plan.json` (schema `DriverPlan` published in `vk-contracts`); an optional arch call through the harness, on the schema alone, to name an ambiguous invariant.
   - `generate.py` — `plan.json` + `binding-<app>.json` → `driver.py` + `manifest.json` under the driver contract, `files_sha256` by `hash_dir.py`. One generator; no per-application code.
   - `qualify.py` — replay on copies of the corpus under the Job Object, postconditions checked in code → `qualification.json`.
2. **The product, `drivers/office-com/<app>-<profile>/`**: the generated driver, protocol 1, actions compiled to the object model. No screen fallback in this chantier.
3. **Kernel (`vk-kernel`, `vk-contracts`)**: `DriverKind::Com`, the field `applications` (executable names, the counterpart of `hosts`), the ledger kinds `driver.learned` (human act) and `driver.qualified` (kernel-written), the `DriverPlan` schema. Nothing else moves on the Rust side.
4. **CLI (`vk-cli`)**: `vk driver learn` (human act), `vk driver qualify`, `vk driver learn --deliberate <verb>` (fallback, human act).

### 3.3 What the kernel enforces, and what it trusts

The kernel enforces the driver contract, the hash and the Job exactly as today; `learn` and `qualify` run as governed driver processes with no network. It trusts the invariant schema (produced by code that calls no model) and the qualification record (evidence in files it hashes). The plan is the artefact a human reads and approves; the code is never the object of review.

## 4. Contracts

Each stage writes one canonical JSON file, hashed, which is the interface of the next stage. A stage can be replayed alone from the previous artefact.

### 4.1 `catalogue.json` (per application, output of the inventory)

```json
{ "app": "word", "typelib": { "guid": "...", "version": "8.7" },
  "types": [ { "name": "Document", "members": [
      { "name": "SaveAs2", "kind": "method",
        "params": [ { "name": "FileName", "type": "VARIANT" }, { "name": "FileFormat", "type": "VARIANT" } ],
        "returns": "void" } ] } ],
  "enums": { "WdBuiltinStyle": { "wdStyleHeading1": -2 }, "WdSaveFormat": { "wdFormatPDF": 17 } } }
```

Never edited by hand; regenerated for every Office version; its hash identifies the peripheral's version.

### 4.2 Ontology `office-document.json` (per family, maintained by us, versioned)

A list of **abstract verbs**, each with a typed signature, a precondition, a postcondition checkable in code, and projection rules written as **patterns** over a catalogue (member names and return types), never hard-coded paths, so that a second application binds without code:

```json
{ "verb": "apply_paragraph_style",
  "args": { "selector": "ParagraphSelector", "style": "BuiltinStyle" },
  "pre": "a document is open", "post": "every selected paragraph reports the style",
  "projection": {
    "word":       { "member": "Paragraph.Style", "constants": "WdBuiltinStyle" },
    "powerpoint": { "member": "Shape.PlaceholderFormat.Type", "constants": "PpPlaceholderType", "status": "partial" } } }
```

A projection may declare itself `partial` where the second application has no exact counterpart: PowerPoint has no built-in paragraph styles, so "title style" binds to the title placeholder of the layout, and the postcondition is checked on the placeholder type, not on a style name. `set_font_run`, `set_footer`, `save`, `export` bind exactly on both.

Initial target: 30 to 40 verbs covering open, select, paragraph and character styles, font runs, headers and footers, tables, document properties, save, export, verify. Language-independent anchors only (built-in constants, never the French name "Titre 1").

### 4.3 `binding-<app>.json` (ontology × catalogue)

For each verb: the resolved COM member, the constants, and `status: bound | partial | unbound`. An `unbound` verb is **excluded** from this application's driver and reported; it is where the fallback (§5.3) may propose a binding, which a human reviews before it is versioned into the ontology.

### 4.4 `invariants.json` (output of the miner, no content)

```json
{ "corpus": { "files": 37, "skipped": 2, "schema_sha256": "...", "content_sha256": "<aggregate hash, never the files>" },
  "threshold": 0.8,
  "invariants": [
    { "id": "body_font",   "kind": "font_run",        "value": { "name": "Garamond", "size": 11 }, "support": 0.94 },
    { "id": "title_style", "kind": "paragraph_style", "value": "Heading1",                        "support": 1.0 },
    { "id": "footer_text", "kind": "footer",          "value": "<redacted:sha256:...>",           "support": 0.89 } ] }
```

**Purge rules.** An invariant with support below the threshold does not exist. Every free string (footer text, company name, author) is replaced by its hash in this artefact and reappears in clear only in the generated driver **on the node**, never in an artefact that reaches an arch. No personal data can be an invariant by construction: the miner reads only the formatting parts (`styles.xml`, `settings.xml`, `header*.xml`/`footer*.xml`, `numbering.xml`, `docProps/app.xml`, the slide layouts and masters for `.pptx`) and, in `document.xml`/`slide*.xml`, only formatting attributes, never text runs.

### 4.5 `plan.json` — `DriverPlan` (schema published in `vk-contracts`)

```json
{ "family": "office-document", "app": "word",
  "ontology_sha256": "...", "catalogue_sha256": "...", "invariants_sha256": "...",
  "actions": [
    { "name": "appliquer_style_maison", "reversible": false,
      "steps": [ { "verb": "open", "args": { "path": "$arg.document" } },
                 { "verb": "apply_paragraph_style", "args": { "selector": "first", "style": "Heading1" } },
                 { "verb": "set_font_run", "args": { "selector": "body", "font": { "name": "Garamond", "size": 11 } } },
                 { "verb": "set_footer", "args": { "text": "$secret.footer_text" } },
                 { "verb": "save", "args": {} },
                 { "verb": "export", "args": { "format": "pdf", "path": "$arg.pdf" } } ],
      "pre": [ "document is .docx", "not open elsewhere" ],
      "post": [ "title_style", "body_font", "footer_text", "pdf exists" ] } ],
  "ambiguities": [ { "invariant": "heading_level_2", "resolved_by": "arch:<id>" } ],
  "qualification": { "min_passes": 10, "min_rate": 1.0 } }
```

`$secret.<id>` names a redacted value the generator resolves on the node from the miner's local vault (a file under the learn work directory, private ACL, deleted after generation). This is the artefact the human reads and approves.

### 4.6 The generated driver

`driver.py` + `manifest.json` under the current contract: `kind: com`, `applications: ["WINWORD.EXE"]`, `program.runtime: python-com`, `hosts: []`, actions = the plan's, `evidence` = postconditions as JSON plus the document's hash before and after. The header of `driver.py` carries the four upstream hashes (ontology, catalogue, invariants, plan). Generation is deterministic: same inputs, same bytes, proven by a two-directory test as for packaging.

### 4.7 `qualification.json`

Per pass: document hash, duration, each postcondition held or not, refusal reason (protocol's five words). The driver is mountable when the record meets the plan's `qualification` thresholds.

### 4.8 Ledger payloads

- **`driver.learned`** (new kind, human act, signed with the device key) — `{ family, app, catalogue_sha256, ontology_sha256, corpus_schema_sha256, invariants_sha256, plan_sha256, driver_id, by }`.
- **`driver.qualified`** (new kind, kernel-written at the end of a qualify run) — `{ driver_id, passes, failures, qualification_sha256 }`.
- `driver.mounted` is unchanged; a learned driver mounts like any other and the ledger links it to its `driver.learned` row through `driver_id`.

### 4.9 Contract additions

`DriverKind::Com`; `DriverManifest.applications: Vec<String>` (executable names, upper-case as Windows reports them, required when `kind: com`, empty otherwise); `DriverProgram.runtime: "python-com"`; the `DriverPlan` schema; the two payloads. Identity test: the MaskAI pilot manifest keeps its `driver_id` (new fields absent = defaults, excluded from the canonical hash when at their default).

### 4.10 CLI surface

| Verb | Human act | Does |
|---|---|---|
| `vk driver learn --family F --app A --corpus DIR --out DIR [--threshold 0.8]` | yes: `driver.learned` | stages 1–5, prints the unbound verbs and the purged ambiguities |
| `vk driver qualify --driver DIR --corpus DIR` | no (kernel writes `driver.qualified`) | stage 6 |
| `vk driver learn --deliberate VERB --app A` | yes | the fallback (§5.3); output is a proposed projection rule, not code |
| `vk driver plan show DIR` | no | prints `plan.json` in the operator's register for review |
| `vk mount driver --manifest DIR/manifest.json` | yes (as today) | unchanged |

## 5. Flows

### 5.1 Learn (one human act, five stages, nothing leaves the node)

1. `vk driver learn --family office-document --app word --corpus C:\...\contrats --out dist\word-maison`: signed; the kernel runs `vkforge` as a governed driver process, no network.
2. **Inventory**: `comtypes` opens Word's type library; `catalogue.json`. Under 10 s; deterministic per Office version.
3. **Binding**: ontology × catalogue → `binding-word.json`; unbound verbs on the output.
4. **Mining**: each corpus file opened read-only as a zip; formatting parts walked; `invariants.json`. About 50 ms per file; the files are never copied at this stage.
5. **Planning**: invariants × bound verbs → `plan.json`. For each invariant the rules cannot turn into an action (two competing styles at close support, a variable footer), one **question to the arch** goes through the harness with the schema alone (`kind`, `support`, non-textual values, hashes for the rest); the answer is a proposed verb and parameters, recorded in `plan.ambiguities` with `resolved_by: arch:<id>`. With no arch mounted, the invariant is purged and listed. A plan without ambiguity never calls a model: **zero tokens on the nominal path**.
6. **Generation**: `driver.py` + `manifest.json`, hashed.
7. The human reads `plan.json`, then runs `qualify`.

### 5.2 Qualify and mount

Copies of the corpus under `%LOCALAPPDATA%\Aubier\drivers\<name>\work\`; Word started as the user's own interactive instance (spike verdict 3: never a second, unsigned instance); each copy processed by the driver under the harness, supervised; postconditions checked **in code** on the resulting OOXML (the miner re-reads the output: every invariant of the plan's `post` must be present at support 1.0). `qualification.json`, then `driver.qualified`. Then `vk mount driver --manifest dist\word-maison\manifest.json`: a driver like any other.

### 5.3 Fallback: the deliberate run (bounded)

Triggered only for an `unbound` verb or an unresolved ambiguity, on a human's request. The model drives the application under the harness (spike B's method), the COM calls are traced, and the output is a **proposed projection rule** for the ontology, reviewed by a human before it is versioned. The fallback never writes into `driver.py`.

### 5.4 Extension to PowerPoint

Stages 2–6 with `--app powerpoint --corpus ...\presentations`. What changes: the catalogue, the binding, the corpus. What does not: the ontology, the miner, the planner, the generator, the qualifier. A verb needed by PowerPoint and `unbound` is an ontology defect, fixed in `office-document.json`, not in code.

### 5.5 Data boundaries

| Actor | Sees | Never sees |
|---|---|---|
| Miner (code) | the OOXML formatting parts | `document.xml` beyond formatting attributes |
| Arch (model) | `plan.ambiguities`: kinds, supports, numeric values, hashes | content, file names, free strings |
| Generated driver (on the node) | the clear values its actions need (footer text) | the original corpus |
| Ledger | hashes and counts | any artefact in clear |

## 6. Failure modes

| Event | Reaction |
|---|---|
| Office absent or type library not found | `learn` refused before anything: `remedy: install Microsoft 365 desktop` |
| Office version differs from the catalogue's | catalogue hash changes → driver regenerated, never mounted on the old binding |
| Corpus under 5 files, or every invariant under the threshold | `learn` refused: `remedy: more documents of one kind` (no empty driver) |
| Corpus file encrypted, corrupt or not OOXML (`.doc`) | skipped, counted in `corpus.skipped`, never opened by Word |
| Required verb `unbound` | action excluded and listed; plan produced without it; the human decides on the fallback |
| Ambiguity with no arch mounted | invariant purged and listed; plan produced |
| Arch answer outside the expected schema | ambiguity purged; never applied in part |
| Unexpected dialog during qualification (sign-in, overwrite) | refusal `dialog` (R28), pass counted as failed, never answered automatically |
| Postconditions held on fewer copies than the plan's rate | `driver.qualified` written with the failures; mount refused under the threshold |
| Generation not reproducible | build test fails; no driver delivered |
| Corpus changed between mining and qualification | `content_sha256` differs → qualification refused |

## 7. Testing

- **Contracts**: schemas `DriverPlan`, `driver_learned`, `driver_qualified`, `DriverKind::Com`, `applications`; the MaskAI pilot manifest keeps its `driver_id`.
- **Inventory**: a catalogue from a recorded type-library fixture in CI (no Office); the real Word on the founder's node.
- **Ontology/binding**: every verb bound on Word **and** PowerPoint from fixture catalogues; a verb with no matching pattern is `unbound`, not an exception.
- **Miner**: synthetic corpus (the 10 contracts of `make_fixtures.py` plus 10 generated presentations) → expected invariants; a document carrying fictitious personal data → no byte of its text in `invariants.json` (string search).
- **Planner**: fixture invariants → expected plan without an arch; with a fake arch, the resolved ambiguity is traced.
- **Generator**: reproducibility; the generated driver passes `test_protocol.py`; `files_sha256` consistent.
- **Qualification, end to end (Windows CI job `driver-learn`)**: a **fake COM application** (a Python COM server registered for the job, exposing a subset of Word's model) → `learn → qualify → mount → act` without Office. On the founder's node: the real Word, then the real PowerPoint.

## 8. Exit criteria

1. On the founder's node, `vk driver learn` on a corpus of Word contracts produces a driver whose qualification is 10/10, zero tokens, under 15 s per document; `driver.learned` and `driver.qualified` on the ledger.
2. The **same** `vkforge` (same files, same hash), fed a corpus of presentations, produces a PowerPoint driver qualified at 9/10 or better, with no line added anywhere but `office-document.json`.
3. A fictitious document carrying personal data goes through the pipeline and none of that data appears in any artefact other than the generated driver.
4. CI green, `driver-learn` job included; `contracts/tcb.md` updated with the data boundaries of §5.5.

## 9. Out of scope

Microsoft Graph (roadmap); screen/UIA fallback; Excel and Outlook; evidence-gated promotion; service mode (the COM driver runs under the interactive daemon, then under `vk-agent` when it arrives); packaging of the learned driver (the packaging spec applies unchanged once the driver exists); observation of the human's session.

## 10. Open items for the plan, not for this spec

The exact verb list of `office-document.json` v1; the `comtypes` pin and how the type library is located (registry vs. `tlbinf32`); the purge threshold's default and whether it is per kind; how the fake COM server is registered in CI without elevation; the review format of `vk driver plan show`. None of these changes a contract.

## 11. TCB statement additions

`contracts/tcb.md` §Drivers: a learned driver is identified like a written one and trusted no more; the kernel trusts the miner's schema because the miner calls no model and reads no text run; the only data that may reach an arch during learning is the ambiguity list of §4.5, and the ledger records which arch answered; the deliberate-run fallback produces a rule for human review, never code; qualification is a replay on copies under the Job, and its record is what the mount checks.
