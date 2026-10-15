# Device driver contract and the MaskAI recette pilot — Design

**Status:** approved section by section in brainstorming (2026-10-14, remote-control session), pending founder review of this document; refined by the plan of 2026-10-14: the word is *driver* (`/devices` already names enrolled human devices) and the recette needs eleven business actions. As built, four points differ from this text: a dialog does not put the harness step in `waiting_human` (the harness ends the level and the operator resubmits, runbook §12), there is no rule that three timeouts fail the step, evidence kinds are the evidence file names, and the business actions are eleven plus `exporter_journal`.
**Date:** 2026-10-14
**Scope:** the kernel's first supervised device — a browser behind a driver, reachable only by syscall — and the first task that uses it: the Aubier recette of a chat AI (MaskAI), run as a governed process of the kernel from plan to release. One implementation plan follows this spec.
**Inputs:** founder decisions of 2026-10-14 (below); `docs/use-cases/recettage-chat-ia/PROCEDURE.md` (the recette: seven steps, grid N0–N9, six NFRs, journal format); the concept-1 spikes of 2026-10-05 (`.superpowers/spikes/2026-10-05-pilotes/report.md` and `report-D.md`, private); the system design `2026-09-22-verticalai-design.md` (§3.3 governed processes, §3.7 arch manifests, invariant I2, Rulings 10, 19, 22, 28); the harness fence as built (`crates/vk-harness/src/launch.rs`).

---

## 1. Why this, now

The recette of 12–13 October was run by an agent in the founder's Chrome, outside the kernel: nothing of it is on a ledger, no step was gated, the evidence is a folder. The pilot re-runs the same recette **as a kernel task**: planned by an arch, executed by confined harnesses, judged, approved twice by a human with a signed key, released. What it proves is **governance** — every step traced, every outbound act bounded by a manifest — on a case that also serves the MaskAI project, so development and analysis capacity is pooled.

The browser is where the recette happens, and the browser is outside the kernel. Rather than widen the harness fence to let the agent touch Chrome, the founder chose to **introduce the driver contract now, in its minimum viable form**: the browser becomes a *device* of the kernel, behind a driver the kernel starts, caps and talks to; the harness sees a syscall and nothing else. The concept-1 roadmap needed this contract anyway; the pilot is its first consumer.

OS analogy, kept deliberately: the kernel is the supervisor; the browser is a peripheral behind a driver; the harness is a user-mode task that holds a device descriptor and never the hardware.

## 2. Decisions log (founder, 2026-10-14)

| # | Decision | Notes |
|---|---|---|
| P1 | The pilot runs **now**, before the MVP specs | pools capacity with MaskAI; 4–6 agent-days once P4 is included |
| P2 | What it proves first: **governance** | every recette step is a kernel step on the ledger, human approval before the report; replay and driver qualification are not the pilot's claims |
| P3 | Execution N0–N9 is driven by the **kernel's Harness step**, one launch per level, no human during execution | the kernel bounds each run (budget, timeout, STOP) |
| P4 | The browser enters as a **device under the driver contract**, minimum viable — not through a wider fence, not through a pilot flag | "it becomes necessary anyway" |
| P5 | Driver granularity: **compiled business actions** (eight typed actions), not browser primitives | the lesson of the spikes: compile to the application's interface; zero tokens per action; the ledger reads business acts |
| P6 | Exit criterion: **one complete N0–N9 recette** on the current MaskAI through the chain, report released | replay on a new version is a later task |
| P7 | Daily mode on the founder's node: **interactive daemon first**, service later | structurally required here: a session-0 service cannot drive the desktop's Chrome; switch trigger = user-session agent shipped + first unattended soak week |
| P8 | First-boot runbook checks prerequisites **per mount**; Docker is not required | ollama → Docker, claude-code → the binary, anthropic → keyring secret + network |
| P9 | **Ten Harness steps**, one per level, rather than one | a failed level does not lose the others; resumption is per level |
| P10 | `device.acted` carries **hashes, never text** | Ruling 28 kept; text lives in labelled blobs |
| P11 | Driver in **Python + Playwright**, as the spikes | Rust (chromiumoxide) considered and declined for the pilot: +1 agent-day, spikes not reused |

## 3. Architecture

### 3.1 Components

1. **`vk-contracts`** gains `DeviceManifest`, three ledger payloads (`device_mounted`, `device_acted`, `device_unmounted`) and `TaskRecipe` (§5.1). Schemas generated; `schema_drift` and valid/invalid examples as for every contract.
2. **`vk-device`** (new crate): the driver process host. It spawns the driver under the same Job Object governor the harness uses (`vk_harness::confine`), speaks JSON lines over the driver's stdio (§4.2), bounds every action by a deadline, kills the tree on STOP or on a deadline, and samples the tree's egress as `netwatch` does. It depends on `vk-contracts` and `vk-harness` only; the kernel depends on it.
3. **Kernel (`vk-kernel`)**: `/devices/<name>` in the namespace, backed by the mounted device; the `device.act` syscall (§4.3), reserved to a harness lease token; mount and unmount of devices; `devices: [name]` on a Harness step; task recipes (§5.1); the Judge step attaches its verdict as an artefact (§5.2).
4. **`vk-mcp`**: one more tool, `vk_device_act(device, action, args)`, routed to `device.act`. **The harness fence does not change by one byte**: `--strict-mcp-config`, the deny list, the allow list `Read(./**)`, `Edit(./**)`, `mcp__vk__*` all stay (Rulings 19, 22).
5. **Driver `drivers/maskai-recette/`** (Python, Playwright, a dedicated persistent browser profile): the business actions of §4.5. It embeds the existing `scripts/recette/evaluer.py` and `journal.py`. It refuses any navigation outside the manifest's hosts and answers `ok:false, reason:"dialog"` on an unexpected dialog without ever answering it.
6. **`vk-cli`**: `vk mount device <manifest.json>`, `vk umount <device>`, `vk device show <name>`, `vk task submit --steps <recipe.json>`. `vk ls /devices` exists.

### 3.2 Where things run

Everything runs in the founder's **interactive session** under the interactive daemon (P7): vkd, the harness, the driver, and the Chrome the driver launches as its child — so the whole tree is inside the governor's Job Object and dies with the kernel. The browser profile is the device's own, under the state directory, created at mount with the same private ACL as the rest of the state directory. The founder signs in to MaskAI once, by hand, in that headed browser (procedure §4: the open session is the human's input, never the agent's). The kernel never sees a password.

### 3.3 What the kernel enforces, and what it trusts

The kernel enforces what it can see: the action name against the manifest, the cadence, the per-step action cap, the register's label against the device's clearance (I2), the task's STOP state, the deadline. It **trusts the driver** for the host allow-list — a Python process in the interactive session is not confined at the network level, no more than the harness is. Two things keep that honest, and are stated in `contracts/tcb.md` (§11): the driver's files are hashed into the manifest, so a changed driver is a new device id; and the egress sampled during each action is written on the ledger as `hosts_seen`, so a driver that talks to anything but its hosts is visible after the fact.

## 4. Contracts

### 4.1 `DeviceManifest` (spec §3.7 applied to a device)

```json
{
  "name": "maskai-1",
  "kind": "browser",
  "driver": {
    "runtime": "python-playwright",
    "command": ["python", "-I", "drivers/maskai-recette/driver.py"],
    "files_sha256": "<hash of the driver directory>",
    "browser_channel": "chrome",
    "headless": false
  },
  "hosts": ["app.maskai.fr"],
  "actions": [
    { "name": "nouvelle_conversation", "reversible": true,  "args": {}, "evidence": ["screenshot"] },
    { "name": "joindre_document",      "reversible": false, "args": { "case": "string" }, "evidence": ["screenshot", "network"] },
    { "name": "poser_question",        "reversible": false, "args": { "case": "string" }, "evidence": ["screenshot", "dom", "network", "journal"] },
    { "name": "lire_reponse",          "reversible": true,  "args": {}, "evidence": ["dom"] },
    { "name": "capturer_flux",         "reversible": true,  "args": {}, "evidence": ["network"] },
    { "name": "verdict_oracle",        "reversible": true,  "args": { "case": "string" }, "evidence": ["journal"] },
    { "name": "capture_ecran",         "reversible": true,  "args": {}, "evidence": ["screenshot"] },
    { "name": "exporter_journal",      "reversible": true,  "args": {}, "evidence": ["journal"] }
  ],
  "min_interval_ms": 2000,
  "max_action_ms": 120000,
  "clearance": { "max_scope": "task", "third_party_allowed": true },
  "governed": true
}
```

(`args` is shown abbreviated; in the contract it is a JSON Schema object per action.)

- **Identity.** The device id is the hash of the canonical manifest, as the arch id is of the arch manifest: a changed host, action, driver file or browser channel is a new device. `name` is the operator's alias and the namespace entry.
- **Clearance** plays the role it plays for an arch: a register whose label exceeds the device's clearance cannot talk to it. Sending a corpus document to MaskAI is a flow to a third party, so `third_party_allowed` is true and the fictional corpus carries a low label. Invariant I2 applied; no new mechanism.
- **`governed`** is *read back*, never declared (Ruling 10): true only when `vk-device` spawned the driver and the governor reports the tree assigned.
- **`headless: false`** for the pilot: the recette measures what the user sees, and spike D showed the page accessibility tree wakes lazily only in a visible window.

### 4.2 Driver protocol (JSON lines over stdio, protocol 1)

- **Hello.** The driver's first line: `{"hello": {"protocol": 1, "driver": "maskai-recette", "version": "...", "actions": [...]}}`. `vk-device` refuses a driver whose action set does not cover the manifest's, and a hello that does not arrive within 30 s.
- **Request.** `{"id": 17, "action": "poser_question", "args": {"case": "N3-02"}, "deadline_ms": 120000, "evidence_dir": "<absolute path>"}`. One request in flight per device.
- **Response.** `{"id": 17, "ok": true, "result": {...}, "evidence": [{"name": "answer.png", "mime": "image/png", "path": "<absolute path under evidence_dir>"}]}`, or `{"id": 17, "ok": false, "reason": "dialog|timeout|host|precondition|internal", "detail": "..."}`.
- **Evidence by path, not inline.** The driver writes evidence files under the per-request `evidence_dir` the host created; the host reads each once, refuses a path that does not resolve under that directory, caps each file at the harness's per-file limit (`HARNESS_MAX_FILE_BYTES`) and the action's total at a quarter of `HARNESS_MAX_RUN_BYTES`, then deletes the directory.
- **Termination.** A deadline passed without a response: the host kills the driver tree, the action answers `reason:"timeout"`, the device is marked `degraded`, and a `device.unmounted` with that reason is written; a remount is the remedy. An unparseable line, or a response with an unknown id, is `reason:"internal"` and takes the same path. STOP kills the tree at once.

### 4.3 Syscall `device.act`

Caller: a harness lease token, as for `harness.attach_artefact`. Payload: `{token, device, action, args}`. Under the kernel lock, in this order, each a refusal that names its remedy:

1. the token names a live lease on a `Harness` step that is `Running`;
2. that step's `devices` contains `device`, and the device is mounted and not `degraded`;
3. `action` is in the manifest and `args` validate against its schema;
4. `min_interval_ms` has elapsed since this device's previous `device.acted`;
5. the step's `max_actions` is not reached;
6. the task is not `Stopped` and no STOP covers its scope;
7. the register's label flows to the device's clearance (I2).

The kernel then **releases its lock** — the pattern of the harness run: the call blocks for up to `max_action_ms`, and the harness that issued it holds no kernel lock meanwhile — calls `vk-device`, and re-takes the lock to: store each evidence file as a labelled blob (scope: the task; data class: the register's; origins: the register's plus the device), attach each as an `ArtefactRef` whose kind is `<action>/<evidence name>`, write `device.acted`, and answer the harness with `result` only, never the evidence bytes.

### 4.4 Ledger payloads (retention class of the arch events)

- `device.mounted` — `{device_id, name, manifest_hash, profile_dir_hash, governed, by: device_key_id}`. Mounting is a human act, signed with the node's device key as `vk mount anthropic` is.
- `device.acted` — `{task_id, step_index, device_id, action, args_hash, result_hash, ok, reason?, duration_ms, evidence: [blob_hash, ...], hosts_seen: [...]}`. **Hashes only** (P10, Ruling 28).
- `device.unmounted` — `{device_id, reason: "operator" | "degraded" | "timeout" | "internal"}`.

### 4.5 The driver's business actions (compiled, deterministic, zero tokens)

| Action | What it does in Chrome | Returns |
|---|---|---|
| `nouvelle_conversation` | opens a fresh conversation (procedure §6 step 4: one per case) | conversation marker |
| `joindre_document {case}` | attaches the case's corpus file through the hidden input behind the paper-clip (annexe B), verifies the attachment state | attachment state, timing |
| `poser_question {case}` | sends the oracle's question word for word, waits for the complete answer with the response-stream interceptor armed (annexe A), abandon deadline at three times the case budget | answer text, time to first text, complete time, server steps, text sent to the model |
| `lire_reponse` | the last answer's text and DOM extract | text |
| `capturer_flux` | the network and console capture since the last call | summary |
| `verdict_oracle {case}` | `evaluer.py` on the last answer against the case's oracle; appends the journal line (annexe C) | OK / PARTIEL / KO / KO sécurité, with details |
| `capture_ecran` | screenshot | — |
| `exporter_journal` | the append-only `journal.jsonl` of this device, as evidence | line count |

Dialog guard (spike finding 2): Playwright `dialog` events are never accepted; a known in-app modal is recognised by selector and reported; any unknown modal is `reason:"dialog"`. The driver reports, never guesses.

## 5. The task

### 5.1 Recipes: `vk task submit --steps recette.json`

The kernel already runs an arbitrary `Vec<StepKind>`; it is the CLI's `submit` that insists on exactly one producing step. A **recipe** is a `TaskRecipe` contract — `{goal, artefact_kind, constraints[], steps[]}` — whose steps add two things to today's kinds: a Harness step carries `devices[]`, `instruction`, `timeout_s` and `max_actions`; and a task may hold several Harness and several Approve steps. `HARNESS_PROMPT` remains the default when a recipe gives no instruction.

### 5.2 The recette recipe

| # | Step | Does |
|---|---|---|
| 1 | Plan (arch) | reads the register — goal, constraints (= procedure §5, rules of conduct), evidence (case list, oracle summary) — and writes the execution plan: level order, budget per case, abandon deadline at three times the budget |
| 2–11 | Harness × 10, N0 to N9, `devices: ["maskai-1"]` | runs the level's cases through `vk_device_act`; ends with `exporter_journal`. Per-step token and time ceilings; `max_actions` 200 |
| 12 | Judge (arch) | reads the journal and the oracle verdicts, classifies the PARTIEL cases, proposes the global verdict, raises each KO sécurité as an open question, **and attaches `verdict.md` as an artefact** so the next step has something to sign |
| 13 | Approve n°1 | the founder, signed: KO sécurité and global verdict (procedure §6 step 5) |
| 14 | Draft (arch) | the report in the format of `2026-10-13-maskai-benchmark.md`, public version redacted by register constraint |
| 15 | Approve n°2 | the report before release (procedure §6 step 6) |
| 16 | Release to `recettage-chat-ia/runs/<date>/` | report, journal, evidence |

Arches are named by id in the recipe, so Claude Code (subscription) and the `anthropic` API arch are interchangeable; the runbook chooses.

### 5.3 Idempotent resumption

A Harness step relaunched after `WaitingHuman` or a daemon restart skips the cases already present in the device's journal. The journal is the driver's, on disk, append-only, with `supersedes` for corrections and never edited. A level is therefore re-runnable at the case boundary.

## 6. Failure modes

| Event | Reaction |
|---|---|
| Unexpected dialog | `reason:"dialog"`; the step goes `WaitingHuman`; the founder resolves it in Chrome; `vk task step` relaunches the level, journaled cases skipped |
| Action timeout | `reason:"timeout"`, journaled as KO disponibilité (procedure); three in a row within one level fail the step |
| Driver dead or mute | step `Failed(cause)`, device `degraded`, `device.unmounted{reason}`; remount |
| Navigation off the allow-list | refused by the driver, `reason:"host"`, on the ledger; a security signal, not a functional KO |
| Human STOP | existing mechanism: harness tree and in-flight action killed |
| Daemon killed by logout or sleep | at next boot the task resumes from the ledger; the journal is on disk (§5.3). Known cost of P7, accepted for the pilot |
| Runaway harness | `max_actions` and the step's time ceiling |

## 7. Testing

- **Contracts:** `schema_drift`; `examples/device_manifest/{valid,invalid}`, `examples/task_recipe/...`, the three payloads.
- **`vk-device`:** a fake driver (Python, run with `-I`) that speaks protocol 1, to test the hello timeout, the deadline kill, a malformed line, an unknown id, an evidence path escape, the evidence size caps, the `governed` read-back and STOP.
- **Kernel:** every refusal of §4.3 by number; `device.acted` written with hashes only; evidence attached with the right label; a recipe with ten Harness and two Approve steps runs to `Done`; Judge attaches `verdict.md`; resumption skips journaled cases.
- **`vk-mcp`:** `vk_device_act` routes to `device.act` with the token.
- **Driver:** a local fixture page under `drivers/maskai-recette/fixture/` imitating MaskAI's input, paper-clip input, streamed answer and one modal; replay 10/10 as in the spikes; the dialog guard exercised. `scripts/recette/test_evaluer.py` already exists.
- **CI:** a `device-browser` job, headless Chromium on the fixture, one level end to end; the Windows job runs the Job Object tests.
- **Live:** the real MaskAI only on the founder's node.

## 8. Exit criteria (P6)

One complete N0–N9 recette on the current MaskAI through the recipe. `vk ls /ledger` shows one `device.mounted`, the `device.acted` rows, two `approval.recorded` and one `artefact.released`. The report sits under `docs/use-cases/recettage-chat-ia/runs/<date>/` beside the journal and the evidence. The dialog guard has fired at least once in test. CI is green on every job, `device-browser` included.

## 9. Out of scope

Robot Framework runtime; evidence-gated promotion of drivers (automatic autonomy); the user-session agent and the Windows service path; replay mode (procedure step 7) as a kernel feature; browser primitives for discovery; a second device or a second chat AI; any OS-level network confinement of the driver.

## 10. Open items for the runbook, not for this spec

Which arches plan, judge and draft (subscription or API); Chrome or Edge channel on the founder's node; the MaskAI test account and its session; the exact export subpath name. None of these changes a contract.

## 11. TCB statement additions

`contracts/tcb.md` gains a "Devices (pilot)" section: the driver is trusted for the host allow-list and runs with the interactive user's rights inside the governor's Job Object; the kernel's enforcement is the list of §4.3; `hosts_seen` and the driver hash in the manifest are the detection and identity controls; the browser profile holds a live session to a third-party service and is protected by the state directory's ACL only.
