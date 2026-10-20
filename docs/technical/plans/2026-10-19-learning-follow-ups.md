# Learning Follow-ups (salted tokens, vault after plan, the started Office held, gate at respawn, `vk driver act`, minors) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close what the founder decided on 2026-10-19 about the driver-learning chantier (merged at `22962c7`, tag `driver-learning`) — redaction tokens keyed by a per-learn salt (D-01), the vault cut down to what the plan uses before `generate` (D-02), the Office instance a learned driver starts identified by its process, the only one whose dialog boxes it closes (D-03), and held in a kill-on-close Job so it dies with the driver (D-04; its one strictly bounded exception, D-35 of 2026-10-20), the qualification gate held again when a `kind: com` driver is respawned (D-31), the operator verb `vk driver act` (D-28; its ledger kind, D-33 of 2026-10-20) — and the final review's parked minors.

**Architecture:** Python changes stay inside `drivers/vkforge/`: `mine` and `qualify` take the learn's salt from a file the kernel writes (`--salt-file`), `plan` rewrites the vault in place (`--vault`), the vendored COM adapter (`runtime/com.py`) learns to identify and hold the process it starts (ctypes Toolhelp32, the service manager and Job objects, stdlib only), `debrief` shows evidence by allowlist. The kernel side is three small seams: `vk-ipc/src/forge.rs` draws the salt and passes the two files, `vk-ipc/src/drivers.rs` runs the kernel's `check_qualified` before every spawn (a new `DriverFailure::Unqualified`), and a human act `driver.operate` with its kernel halves, IPC arm and CLI verb, recorded under the ledger kind the founder took (D-33).

**Tech Stack:** Python 3.13 (stdlib, `lxml==6.1.3`, `comtypes==1.4.17` on Windows only; `ctypes` for the process snapshot and the Job), Rust (MSVC) for `vk-contracts`, `vk-kernel`, `vk-ipc`, `vk-cli`; CI jobs `driver-browser` (Ubuntu, the vkforge suite), `driver-learn` (Windows, the vkforge suite under the virtualenv, `vk-kernel --test learn`, `vk-ipc --test forge`, `vk-cli --test smoke vk_forge_`).

**Spec:** `docs/technical/specs/2026-10-17-driver-learning-office-design.md` (§4.4–§4.5 tokens and vault, §5.2 qualify, §6 dialogs, §11 TCB, §12 rulings at implementation). Decisions: the founder's register of 2026-10-19, rows D-01–D-05 and D-27–D-31 (private: `docs/strategy/decisions/2026-10-18-decisions-ouvertes.md`), and the founder's decisions of 2026-10-20, D-33 and D-35 (private: `docs/strategy/decisions/2026-10-20-journal-des-decisions.md`; see **Amendments of 2026-10-20**). Evidence: the chantier's final review and re-review, its SDD ledger (rulings L-P1…L-P58) and the Task 12 node run (private: `docs/archive/technical/reviews/2026-10-19-driver-learning-final-review.md`, `…-final-rereview.md`, `…-sdd-ledger.md`, `…-task-12-node-run.md`). The archived plan `docs/archive/technical/plans/2026-10-17-driver-learning-office.md` is the code map of what this plan changes.

**Base:** master `22962c7`. Every `path:lines` below is a line range of `22962c7`. The tasks run in the order 1, 2, 3, 4, 6, then 5 (its ledger kind answered by the founder, D-33); a task that edits a function an earlier task already changed finds it by name, and its code blocks are written against the earlier task's result.

## Global Constraints

**Scope and contracts**

- Contracts frozen: no change to `DriverPlan`, `DriverLearned` or `DriverQualified`, and none of the tasks below makes one. The one contract addition is Task 5's ledger kind `driver.operated` and its payload `DriverOperated`, decided by the founder (D-33, 2026-10-20: the new kind, its author `by` mandatory; CLAUDE.md: ledger-kind decisions are the founder's); nothing else adds a kind to `ALLOWED_KINDS`.
- New `vk` CLI surface: exactly `vk driver act NAME ACTION [--args JSON]` (with the global `--json`), founder decision D-28 (Task 5). The vkforge stage flags `mine --salt-file`, `qualify --salt-file` and `plan --vault` belong to the stages and come with D-01 and D-02.
- `drivers/vkforge/` is Python 3.13 with the stdlib, `lxml==6.1.3` and `comtypes==1.4.17; sys_platform == "win32"` only (`drivers/vkforge/requirements.txt`); `comtypes` is imported lazily and on Windows only; no `psutil`: the process snapshot and the Job object go through `ctypes`. No network module anywhere in the package (`contracts/tcb.md`, pinned by `drivers/vkforge/tests/test_package.py`).
- `drivers/vkforge/runtime/com.py` is vendored into every generated driver (`vendor/vkforge_runtime/com.py`) and so lies inside `files_sha256`: Task 3's change makes every later learn a new driver identity — intended. A driver learned before this chantier keeps the adapter it vendored until it is learned again; it also has no salt (Task 1), so it cannot be qualified again and is learned again (runbook §8).

**How vkforge runs and speaks**

- One invocation everywhere: `python -I drivers/vkforge/__main__.py <stage> …`, never `-m` (ruling L-P2's form).
- A refusal is one line, `vkforge <stage>: <what>; remedy: <remedy>` (L-P15); the kernel relays a failed stage's last stderr line verbatim (`run_stage`).
- Every artefact a stage writes goes through `vkforge.canon` (`canon.write`, `canon.dumps`: sorted keys, `indent=1`, UTF-8, one trailing newline).

**Build, test, review**

- MSVC toolchain only. `CARGO_TARGET_DIR=%USERPROFILE%\.cargo-target\aubier-learning`; `OPENSSL_SRC_PERL=%USERPROFILE%\.cargo-target\tools\strawberry-perl\perl\bin\perl.exe` (the chantier's build environment).
- Python tests run under the vkforge virtualenv that `drivers\vkforge\setup.ps1` makes, never the Microsoft Store Python (R27).
- Rust tests that spawn vkforge run with `VK_FORGE_REQUIRED=1`, as CI's `driver-learn` job does (ruling L-P56): a missing interpreter fails the test instead of skipping it.
- Before a commit that touches Rust: `cargo fmt --all --check` and `cargo clippy --workspace --all-targets -- -D warnings`. Before a commit that touches `docs/` or `contracts/`: `python -I scripts/docs/check_docs.py` exits 0.
- Never run `vk` against the founder's daemon: every Rust test starts a daemon of its own over a temporary state directory. The real-Office steps of Task 3 start and kill a Word of their own, and a PowerPoint of their own only when no `POWERPNT.EXE` runs beforehand (PF-5); the controller runs them on the node when the founder's session allows it.
- No placeholder: every test a step names has its code in that step.
- Model tiering (founder rule): **Opus** for Tasks 1, 3, 5; **Sonnet** for Tasks 2, 4, 6. One implementer at a time (node memory), one fresh reviewer per task before the next starts.
- Commits: conventional prefix, one sentence of intent, and exactly these two trailer lines (no others):
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_01UUDLpkwxsZXmLptqCeNL2R`

**How to run** (PowerShell, from the worktree root; the steps below use `$PY` and these variables):

```powershell
$env:CARGO_TARGET_DIR = "$env:USERPROFILE\.cargo-target\aubier-learning"
$env:OPENSSL_SRC_PERL = "$env:USERPROFILE\.cargo-target\tools\strawberry-perl\perl\bin\perl.exe"
$env:VK_FORGE_REQUIRED = "1"
$PY = "$env:LOCALAPPDATA\vk\venvs\vkforge\Scripts\python.exe"
& $PY -I -m unittest discover -s drivers/vkforge/tests -t .           # the whole vkforge suite
cargo test --locked -p vk-ipc --test forge -- --nocapture                 # learn, qualify, mount through a daemon
```

A commit is made with a message file, so the two trailers stay one paragraph (Git Bash):

```bash
git commit -F - <<'MSG'
<subject>

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UUDLpkwxsZXmLptqCeNL2R
MSG
```

## Review Focus

1. **The user opens a document of their own in the Word the driver started** (a double-clicked `.docx` can land in a running automation instance; PowerPoint is single-instance). The instance outlives a clean end only inside the five bounds of founder decision D-35 (LF-7): the driver's own documents closed first without saving, the foreign one identified by a `FullName` absent from the driver's exact list, the instance handed to the user (`Visible` set true and read back true, PF-12) and its Job alone disarmed — in that order, inside `QUIT_WAIT_S`, before any other call (PF-2) — then its `DisplayAlerts` put back and every reference released, and one stderr line with the PID and the count. A failure, a timeout or a doubt before the disarm (a `Visible` that cannot be set or reads back false among them), or an unclean death, and the Job takes the instance (D-04). Task 3: one test per bound — `test_the_instance_survives_only_a_clean_end_inside_the_window`, `test_the_drivers_own_documents_are_closed_first_and_never_keep_the_instance`, `test_a_document_is_foreign_only_when_the_driver_never_opened_its_name`, `test_the_instance_kept_is_handed_to_the_user_and_only_its_job_is_disarmed`, `test_every_firing_is_one_stderr_line_with_the_pid_and_the_count` — the order, `test_the_end_closes_then_lists_then_hands_over_then_disarms` (PF-2), and on Windows `test_a_held_process_dies_with_its_holder_and_a_disarmed_one_lives`.
2. **A Word or a PowerPoint of the user's already running, or one started at the same moment as the driver's.** The adapter attaches, or sees a start it cannot positively identify — none new (`CreateObject` handed back the running PowerPoint), two or more, or one whose parent is not the `DcomLaunch` service (the user's own start, from the shell) — and treats the instance as attached (PF-4): no Job, no dialog box closed, `dialog` refused with the box left open, one stderr line; the user's instance is never quit nor killed. Task 3: `test_only_a_positively_identified_process_is_owned`, `test_only_the_started_instance_s_boxes_are_closed`, and on the node (PF-5) `test_the_users_powerpoint_is_attached_never_quit_never_held_its_box_left_open`, `test_a_killed_driver_takes_the_powerpoint_it_started`.
3. **The salt changed under a learned driver** — `forge\<name>\` emptied by hand, or a second `learn` of the same name (another `--out`) drew a new one. `qualify` of the first must refuse in one line before any copy, never record 0/10 as if the driver were broken. Task 1: `test_the_learns_salt_qualifies_and_any_other_fails_closed`, `qualify_reads_the_learns_salt_and_fails_closed_without_it`.
4. **A corpus whose footers differ per document** (a client's name in each). Ten candidates enter the vault at `mine`; none may survive `plan` unless an arch resolved one, and then only that one. Task 2: `VaultTest`, `learn_leaves_no_purged_candidate_in_the_vault_before_generate`.
5. **A `com` driver left mounted `Ready` while the ground moved** — the daemon restarted without `VK_FORGE_FIXTURES`, or `qualification.json` edited. The gate runs before every start of the driver's process — at mount, and at the first act after a restart or a degrade (D-31) — and is not re-checked per act while that process lives (PF-7): the act that would start the process is refused before it starts, and the driver degraded with the closed word `unqualified` on the ledger, the gate's sentence going to the act's refusal, the daemon's log and `driver.log` (PF-6). Task 4: `the_gate_holds_again_when_an_act_respawns_a_com_driver`, which also shows an act served by a live process after the record was edited.

## Plan rulings

These are the controller's, taken while writing the plan; each task that carries one says so.

- **LF-1 (D-01, where the salt lives and how long).** The salt is 32 bytes from the OS generator (`rand::rngs::OsRng`), written by the kernel's `learn` to `<state_dir>/forge/<name>/.salt` — the daemon's private forge directory, beside the vault since final review I-2 — before the `mine` stage. It is **not** deleted with the vault: `qualify` re-mines the replayed copies and must give back the very tokens `invariants.json` holds. The founder's « gardé dans le coffre » is honoured by the directory: same ACL, same lifetime as the learn's logs; the next `learn` of the name clears it with them.
- **LF-2 (D-01, the unsalted form stays).** `mine.redact(text, salt=None)` keeps the pre-D-01 token `<redacted:sha256:HEX>`: the pinned fixtures were written with it, and the fake application compares like with like with it. `mine.tokens()` recognises both prefixes. The CLI requires `--salt-file`, so every token the pipeline writes from now on is `<redacted:hmac-sha256:HEX>`.
- **LF-3 (D-01, fail closed).** `qualify` refuses, before any copy, a salt that does not give back from the (unchanged) corpus every token the plan's postconditions expect: `the salt does not give back the <id> token of invariants.json; remedy: vk driver learn again: the salt and invariants.json are one learn's`. A missing or short salt file is refused by `mine.read_salt` in one line.
- **LF-4 (D-02, what the vault keeps).** After `plan`, exactly the tokens found at any depth of the value of every invariant an action keeps (its id among the action's `post`) and of every ambiguity the plan resolved; nothing else — a purged ambiguity's candidates, an excluded invariant's text (a custom style's name) — outlives the plan.
- **LF-5 (D-03, whose instance it is; rewritten by PF-4).** A process is the driver's only by positive identification: exactly one new process of the application's executable between a snapshot taken before `GetActiveObject`/`CreateObject` and one taken after, **and** its parent PID equal to the process id of the `DcomLaunch` service, read with `QueryServiceStatusEx` (the service that starts a COM local server). Anything else is attached: zero new (`CreateObject` handed back an instance already running — PowerPoint is single-instance), two or more, a parent other than `DcomLaunch` (a PowerPoint the user started at the same moment), a snapshot or the service query that failed, no executable named. An attached instance is never quit, never held in a Job, its dialog boxes never closed; a start the adapter cannot tell apart is one stderr line, `start: treated as attached: <why>`. There is no "started but unnamed" instance any more: it is attached.
- **LF-6 (D-03, which boxes close).** Past a call's deadline the guard lists the `#32770` boxes of the application's executables with their process. A box of the owned process is closed (never answered) and the action refuses `dialog`. A box of another process is never touched: with no owned process (attached, PF-4) a box of the application still means `dialog`, said to be left open; with an owned process, a box elsewhere did not hold this call, so the action refuses `timeout`.
- **LF-7 (D-04, the clean end) — founder decision D-35 (2026-10-20), which replaces the earlier text.** The exception is kept, strictly bounded. The Office instance the driver started survives the driver's end only if all five bounds hold: (1) a clean end, inside the clean-end window — `QUIT_WAIT_S = 3.0` s from the start of `quit()`, under the 5 s a kernel host (L-P49, `STOP_GRACE`) and `qualify` (`END_WAIT_S`) give a driver to end by itself; any unclean death leaves the Job to take the instance; (2) the driver first closes every document it opened, with `SaveChanges=False` (the binding's `close`; PowerPoint's `Close()` saves nothing); a document of its own that will not close does not protect the instance; (3) positive identification: a document is foreign only if its `FullName` is absent from the driver's exact list of the documents it opened (each `FullName` read back after its `Open`); any COM error, timeout or doubt during the inventory kills the instance — an `Open` whose document the driver never named is such a doubt; (4) the surviving instance is handed to the user, visible and under the user's control, as ruling PF-12 reads it: its `Application.Visible` set true (`COM_TRUE`, PowerPoint's `msoTrue`) and read back true, then the Job disarmed for that instance only (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` cleared on its own Job), then its `DisplayAlerts` restored and every COM reference the adapter holds released, these two best effort — visible, outside any Job, with no automation reference left, the instance is under the user's control; `UserControl` is not used (Word's `Application.UserControl` has no setter, PowerPoint's `Application` has none), and a `Visible` that cannot be set, or that reads back false, means no disarm; (5) every firing is recorded: one stderr line (`driver.log`) with the PID and the count of foreign documents, never a name or content. Outside these bounds, D-04 applies to the letter: the started instance dies with the driver — quit when nothing foreign is open, its process given what is left of the window, then its Job closed armed, which ends whatever remains. The order inside the window is PF-2's.
- **LF-8 (D-04, a session that cannot begin; narrowed by PF-4).** A start held past its deadline (a first-run prompt, a repair dialog) is held in a Job kept by the module, so it ends with the driver — only when its new process is positively owned (PF-4); otherwise it is attached and left alone, one stderr line. A session whose `DisplayAlerts` cannot be read or set closes the Job of the instance it started at once.
- **LF-9 (D-31, where the gate runs).** Boot spawns nothing (`RealKernel::boot` only degrades invalid rows, `crates/vk-kernel/src/lib.rs:1136-1141`, `crates/vk-kernel/src/drivers.rs:743-781`; the daemon's pool starts empty, `crates/vk-ipc/src/drivers.rs:123-127`), so a `Ready` row is respawned lazily by the first act (`run_act` → `DriverPool::act` → `ensure` → `spawn_host`, `crates/vk-ipc/src/drivers.rs:833-859, 241-263, 172-217, 438-456`). The gate therefore runs in `DriverPool::ensure`, before every spawn — mount and act alike — with the fixtures flag `serve_on` handed the kernel (final review I-1). Not at boot: the flag is not yet set there (`crates/vk-ipc/src/server.rs:450-457`), and a degrade at boot would write `driver.unmounted` for a driver nobody asked to run. A refusal is `DriverFailure::Unqualified(<the gate's sentence>)`, degrading the driver like `tampered`. As amended by PF-6: the ledger records the closed word `unqualified`, like `tampered` — on `driver.acted`, on `driver.unmounted` and on the degraded row — and the gate's sentence (a driver id and a remedy; when the record cannot be read, its path under the user's profile) goes to `detail()` (the daemon's log and `driver.log`) and to the act's RPC refusal (`unqualified: <sentence>`), never into the ledger (Ruling 28: hashes, never text; no path, no user name on the chain). As amended by PF-7: the gate runs before every start of the driver's process — at mount, and at the first act after a restart or a degrade — as founder decision D-31 says; it is not re-checked per act while the host lives.
- **LF-10 (D-28, the ledger kind — decided by the founder, D-33, 2026-10-20).** A human act whose author must be on the record does not fit `driver.acted` (its `task_id`/`step_index` name a harness step, and it carries no `by`). The founder took the new kind `driver.operated`, payload `DriverOperated` (Task 5's header), its author `by` mandatory (D-33): Task 5 is no longer blocked. The act's evidence goes into a register created for it (`task.submitted`, label bottom), read with `vk artefact cat` (a release on the ledger). `--args` is passed as typed: paths in it are the operator's to give absolute.
- **LF-11 (M-6, the copies go).** `qualify` deletes each pass's folder (`work/qualify/<i>/`: the copy, its PDF, its evidence) once `qualification.json` is written; a folder that cannot go (a copy still open in an attached Word) is one stderr line, never a failed stage — the record is already written.
- **LF-12 (M-8, evidence by allowlist).** A journal line shown as evidence keeps `ts`, `step`, `case`, `verdict`, `timing_s`, `evidence`, `nfr`, `supersedes`, `pipeline_ms`, in that order (evidence early: `CLIP` cuts at 240 characters), and its `evidence` text is replaced by the structural notes the journal script writes in its own words (`réponse vide : échec silencieux`, `abandon à N s (3 × budget N s) : pas de fin de réponse`, `latence N s > budget N s`, `écart d'envoi N s`, `refus (<reason>)`, `· en direct`), or `[…]`. `texte envoyé non observé` is not among them: the L-P57 pin forbids `texte envoyé` in evidence.

## Amendments of 2026-10-20

The pre-flight scan of this plan (2026-10-20, at `6ac4480`) led to two founder decisions and ten controller rulings, and the type libraries' answer to bound (4) of D-35 to an eleventh (PF-12); the rulings above and the tasks below carry them, each where it applies. Task 1 was dispatched from the text as it stood and is not changed here.

- **D-35** (founder; the scan's PF-1) replaces LF-7: the started instance survives the driver's end only if all five bounds hold; outside them, D-04 to the letter. Task 3.
- **D-33** (founder): the new ledger kind `driver.operated`, its author `by` mandatory; Task 5 is unblocked (LF-10).
- **PF-2**: inside `QUIT_WAIT_S`, before any other call — close own documents, inventory, hand-over, disarm; a failure, timeout or doubt before the disarm means no disarm; the `DisplayAlerts` restore and the release after it are best effort. Task 3.
- **PF-3**: the `Job` docstring says what the code does under D-35. Task 3.
- **PF-4**: a process is the driver's only by positive identification — one new process of the executable whose parent is the `DcomLaunch` service's process; anything else is attached, and "unnamed" is gone (LF-5, LF-6, LF-8). Task 3.
- **PF-5**: two PowerPoint node steps for the controller — the user's PowerPoint attached, a started one ending with its killed driver — run only when no `POWERPNT.EXE` runs beforehand. Task 3.
- **PF-6**: the ledger records the closed word `unqualified`; the gate's sentence goes to `detail()` and to the act's RPC refusal, never to the ledger (LF-9). Tasks 4 and 5.
- **PF-7**: the gate runs before every start of the driver's process, not per act while the host lives, and the test says so (LF-9, Review Focus 5). Task 4.
- **PF-8**: the readme's `qualify` row names the salt it reads — carried in Task 1's dispatch, not in this text. Task 1.
- **PF-9**: Task 5 also corrects the runbook's « seul écart de supervision » and `contracts/README.md:15`'s « Five schemas came with the pilot ». Task 5.
- **PF-10**: Task 6's Files entry for `debrief.py` is `:107-110`; `REMEDY` (`:104-106`) stays. Task 6.
- **PF-11**: Task 3 corrects the L-P49 sentence of `contracts/tcb.md:252` and says that drivers learned before this chantier keep the old adapter until learned again; Task 2's docs say D-02 holds on the kernel path. Tasks 2 and 3.
- **PF-12** (the controller's reading of D-35's bound (4); the five bounds are the controller's wording of the founder's strictly bounded exception, so their detail is a controller ruling): the instance is handed to the user, visible and under the user's control, when its `Application.Visible` is set true (PowerPoint: `msoTrue`) and read back true, its Job alone disarmed, its `DisplayAlerts` restored and every COM reference the adapter holds released. `UserControl` is not used — Word's `Application.UserControl` has no setter, PowerPoint's `Application` has none: a visible instance outside any Job, with no automation reference left, is under the user's control. Strictness stays: a `Visible` that cannot be set or read back, or that reads back false, means no disarm, and the Job takes the instance. PF-2's order becomes close own documents → inventory → make visible and read back → disarm → restore `DisplayAlerts` → release, the last two best effort (LF-7). Task 3.

## File Structure

| File | Task | Responsibility after this plan |
|---|---|---|
| `drivers/vkforge/mine.py` | 1 | `redact(text, salt)`, `read_salt`, `tokens`; mining under the learn's salt; `--salt-file` |
| `drivers/vkforge/qualify.py` | 1, 3 (docstrings), 6 | re-mining under the salt and the salt check (LF-3); the pass-folder deletion (LF-11); the M-5 re-check |
| `drivers/vkforge/plan.py` | 2 | `vault_tokens`, `reduce_vault`, `read_vault`; `--vault` |
| `drivers/vkforge/runtime/com.py` | 3 | the process snapshot and the `DcomLaunch` service's process (`dcom_launcher_pid`), `identified`, `Job`, `Owned`, `own`, the owner-aware guard, the adapter that identifies and holds what it starts and ends it within D-35's bounds |
| `drivers/vkforge/runtime/fake.py` | 3 | the fake's dialog in a started or an attached instance (`fake.json` `"attached"`) |
| `drivers/vkforge/debrief.py` | 6 | evidence by allowlist (LF-12) |
| `drivers/vkforge/inventory.py` | 6 | a fixture without `types`/`enums` refused (M-14) |
| `drivers/vkforge/tests/test_mine.py`, `test_qualify.py`, `test_plan.py`, `test_runtime.py`, `test_generate.py`, `test_debrief.py`, `test_inventory.py`, `test_package.py` | 1–3, 6 | the tests of each task |
| `crates/vk-ipc/src/forge.rs` | 1, 2, 6 | the salt drawn and passed; the vault passed to `plan`; qualify's logs under `forge/.qualify/<name>/` |
| `crates/vk-ipc/src/drivers.rs` | 4, 5 | the gate before every spawn; `driver.operate` |
| `crates/vk-kernel/src/drivers.rs` | 4, 5 | `DriverFailure::Unqualified` and `DriverFailure::refusal`; `driver_operate_begin`, `driver_operate_settle` |
| `crates/vk-ipc/src/server.rs` | 5 | the `driver.operate` arm, a human act |
| `crates/vk-contracts/src/driver.rs`, `ledger.rs`, `lib.rs`; `contracts/schemas/driver_operated.schema.json`; `contracts/examples/driver_operated/valid/first.json` | 5 | `DriverOperated`, `driver.operated` |
| `crates/vk-cli/src/main.rs`, `render.rs`, `man.rs` | 5 | `vk driver act`, its rendering, `vk man driver_operated` |
| `crates/vk-ipc/tests/forge.rs`, `drivers.rs`, `common/mod.rs`; `crates/vk-kernel/tests/drivers.rs`; `crates/vk-cli/tests/smoke.rs` | 1, 2, 4, 5, 6 | the daemon-level tests |
| `contracts/tcb.md`, `contracts/README.md`, `docs/technical/technical-readme.md`, `docs/technical/runbooks/learn-office-driver.md`, `docs/technical/runbooks/recette-maskai.md` | each | the sentences each task makes true |

---

### Task 1: D-01 — redaction tokens keyed by the learn's salt (Opus)

**Model:** Opus. **Decision:** D-01. **Rulings:** LF-1, LF-2, LF-3.

**Files:**
- Modify: `drivers/vkforge/mine.py:1-22` (docstring, `import hmac`), `:33-39` (constants, `redact`, new `read_salt`), `:109-120` (`_style_value`), `:143-203` (`_docx`), `:206-236` (`_pptx`), `:239-248` (`mine_file`), `:251-267` (`survey`), `:275-284` (`_tokens` becomes the public `tokens`), `:287-327` (`mine_corpus`), `:347-368` (`add_arguments`, `run`)
- Modify: `drivers/vkforge/qualify.py:1-22` (docstring), `:56-59` (constants), `:185-214` (`_check`, new `_salt_gives_back`), `:385-404` (`_prepare`), `:424-442` (`_post`), `:445-471` (`_pass`), `:474-508` (`qualify`), `:511-534` (`add_arguments`, `run`)
- Modify: `crates/vk-ipc/src/forge.rs:274-297` (beside `Vault`: `SALT_FILE`, `write_salt`), `:465-481` (the `mine` stage), `:836-919` (`qualify`: doc comment and `--salt-file`)
- Test: `drivers/vkforge/tests/test_mine.py` (imports `:1-15`; `:153-176`; `:226-235`; new `SaltTest`), `drivers/vkforge/tests/test_qualify.py` (imports `:1-18`; `learned` `:59-67`; `:193-205`; one new test), `crates/vk-ipc/tests/forge.rs` (`learn_runs_the_five_stages_and_writes_driver_learned` `:66-153`; new `qualify_reads_the_learns_salt_and_fails_closed_without_it`)
- Docs: `contracts/tcb.md:241`, `docs/technical/technical-readme.md:710`, `docs/technical/runbooks/learn-office-driver.md:28`, `:88`, `:161-184`

**Who calls `mine.redact`, and why each caller stays right:**

| Caller | Salt it uses | Why it stays correct |
|---|---|---|
| `mine._style_value`, the footer and header of `mine._docx`, the footer of `mine._pptx` (all through `mine_file`) | the learn's, from `mine_corpus(…, salt=…)` | one salt for the whole learn: the vault, `invariants.json` and every later re-mine under that salt give the same token for the same text |
| `mine._docx`, the set of `all`-style values compared within one file (`:160`) | the file's own (same as `:161`) | it compares like with like inside one call; the set's size is what is read |
| `qualify._check` (`mine.survey`), `qualify._prepare` and `qualify._post` (`mine.mine_file`) | the learn's, from `qualify --salt-file` | the values a postcondition expects come from the same learn's `invariants.json` (LF-3 checks it before any copy) |
| `runtime/fake.py` `verify_header`, `verify_footer` | none | `mine.redact(text)` against `fake._observed`, itself `mine.mine_file(doc.path)` with no salt: like with like whatever the learn's salt. The driver's own `post` is never the verdict; `qualify` re-mines the copy under the salt |
| `tests/test_plan.py` `house_invariants`, `tests/test_generate.py` `GenerateTest.setUp`, the unsalted tests of `tests/test_mine.py` | none | they pin the pre-D-01 form (LF-2) |
| the kernel (`vk-kernel/tests/learn.rs` answer-rule tokens) | — | a token is an opaque string to Rust; nothing parses its prefix |

**The pinned fixtures stay byte-identical.** `drivers/vkforge/tests/fixtures/invariants-word-maison.json` (its `footer_text` value is `mine.redact(FOOTER)`, no salt), `plan-word-maison.json` (its `invariants_sha256` is that file's hash), `driver-word-maison.expected` (`render_driver` of that plan, which prints the plan's hashes) and `binding-word.json` (no token at all). Proof: `test_the_fixtures_for_tasks_7_and_8_are_committed` (`test_plan.py`) and `test_render_driver_is_the_reviewed_text` (`test_generate.py`) pass unchanged, the new `test_unsalted_tokens_are_the_pinned_fixtures_own` pins the form, and Step 11 runs `git diff --exit-code drivers/vkforge/tests/fixtures`.

**Interfaces:**
- Consumes: nothing from another task.
- Produces (Python): `mine.SALT_BYTES = 32`; `mine.redact(text: str, salt: bytes | None = None) -> str` — `"<redacted:hmac-sha256:" + hmac.new(salt, text.encode("utf-8"), hashlib.sha256).hexdigest() + ">"` with a salt, `"<redacted:sha256:" + sha256 hex + ">"` without; `mine.read_salt(path) -> bytes` (exactly 32 bytes, else `Refusal`); `mine.tokens(value) -> Iterator[str]` (both prefixes, any depth, document order) — Task 2 uses it; `mine.mine_file(path, salt: bytes | None = None) -> dict`; `mine.survey(corpus, salt: bytes | None = None)`; `mine.mine_corpus(corpus, threshold: float = 0.8, salt: bytes | None = None)`; CLI `mine --salt-file FILE` (required); `qualify.qualify(driver_dir, corpus, plan, invariants, work, fake, deadline_s=PASS_DEADLINE_S, salt: bytes | None = None) -> dict`; `qualify._post(post, copy, expected, salt=None)`; `qualify._prepare(files, work, post, expected, learned_content, salt=None)` — Task 6 changes both; CLI `qualify --salt-file FILE` (required).
- Produces (Rust, `crates/vk-ipc/src/forge.rs`): `const SALT_FILE: &str = ".salt"`; `fn write_salt(path: &Path) -> Result<(), RpcError>`; `learn` passes `--salt-file <state_dir>/forge/<name>/.salt` to `mine`, `qualify` passes the same path to the `qualify` stage.

- [ ] **Step 1: Write the failing Python tests**

`drivers/vkforge/tests/test_mine.py` — imports (replace `:1-15`):

```python
import contextlib
import hashlib
import hmac
import io
import json
import os
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path

from vkforge import __main__ as entry
from vkforge import canon, mine
from vkforge.errors import Refusal
from vkforge.tests import ooxml_fixtures
from vkforge.tests.ooxml_fixtures import FOOTER

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SALT = bytes(range(32))
OTHER_SALT = bytes(range(1, 33))


def salt_file(salt: bytes = SALT) -> Path:
    """A learn's salt as `vk driver learn` leaves it: the bytes in a file of their own."""
    path = Path(tempfile.mkdtemp()) / ".salt"
    path.write_bytes(salt)
    return path


def a_corpus(n: int, **kw) -> Path:
    d = Path(tempfile.mkdtemp())
    for i in range(n):
        ooxml_fixtures.docx(d / f"c{i}.docx", **kw)
    return d
```

Replace `MineTest.test_the_cli_writes_the_invariants_and_the_vault` and `MineTest.test_the_cli_writes_the_vault_where_vault_names_it_and_nothing_clear_in_out` (`:153-176`) with:

```python
    def test_the_cli_writes_the_invariants_and_the_vault(self):
        d = self.corpus(5, footer=FOOTER)
        out = Path(tempfile.mkdtemp()) / "out"
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            self.assertEqual(entry.main(["mine", "--corpus", str(d), "--out", str(out),
                                         "--salt-file", str(salt_file())]), 0)
        self.assertEqual(sorted(p.name for p in out.iterdir()), [".vault.json", "invariants.json"])
        self.assertEqual(printed.getvalue(), f"mine: 3 invariants, 0 ambiguities over 5 files (0 skipped) -> {out / 'invariants.json'}\n")
        blob = (out / "invariants.json").read_text(encoding="utf-8")
        self.assertIn(mine.redact(FOOTER, SALT), blob, "the CLI mines under the salt it is given (D-01)")
        self.assertNotIn("<redacted:sha256:", blob)

    def test_the_cli_writes_the_vault_where_vault_names_it_and_nothing_clear_in_out(self):
        """The daemon's form (final review I-2): `--vault` under its private forge directory."""
        d = self.corpus(5, footer=FOOTER)
        out = Path(tempfile.mkdtemp()) / "out"
        vault = Path(tempfile.mkdtemp()) / "forge" / "word-maison" / ".vault.json"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(entry.main(["mine", "--corpus", str(d), "--out", str(out), "--vault", str(vault),
                                         "--salt-file", str(salt_file())]), 0)
        self.assertEqual(sorted(p.name for p in out.iterdir()), ["invariants.json"])
        self.assertEqual(json.loads(vault.read_text(encoding="utf-8")), {mine.redact(FOOTER, SALT): FOOTER})
        self.assertNotIn(FOOTER.encode("utf-8"), (out / "invariants.json").read_bytes())
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(entry.main(["mine", "--corpus", str(d), "--out", str(Path(tempfile.mkdtemp()) / "o"),
                                         "--vault", str(d / "v" / ".vault.json"), "--salt-file", str(salt_file())]), 2)
        self.assertIn("is inside the corpus; remedy: write elsewhere", err.getvalue())
```

Replace `MineRobustnessTest.test_out_inside_the_corpus_is_refused` (`:226-235`) with:

```python
    def test_out_inside_the_corpus_is_refused(self):
        d = Path(tempfile.mkdtemp())
        for i in range(5):
            ooxml_fixtures.docx(d / f"c{i}.docx")
        for out in (d, d / "out"):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(entry.main(["mine", "--corpus", str(d), "--out", str(out),
                                             "--salt-file", str(salt_file())]), 2)
            self.assertIn("is inside the corpus; remedy: write elsewhere", err.getvalue())
        self.assertEqual(sorted(p.name for p in d.iterdir()), [f"c{i}.docx" for i in range(5)])
```

Append at the end of the file:

```python
class SaltTest(unittest.TestCase):
    """Founder decision D-01: a learn's salt keys every token; with no salt, the pre-D-01 form the
    pinned fixtures carry (ruling LF-2)."""

    def test_a_salt_keys_the_token_and_two_salts_give_two_tokens(self):
        a = mine.redact(FOOTER, SALT)
        self.assertEqual(a, "<redacted:hmac-sha256:"
                         + hmac.new(SALT, FOOTER.encode("utf-8"), hashlib.sha256).hexdigest() + ">")
        self.assertEqual(a, mine.redact(FOOTER, SALT), "one salt, one token: the vault and qualify agree")
        self.assertNotEqual(a, mine.redact(FOOTER, OTHER_SALT), "another learn, another token")
        self.assertNotEqual(a, mine.redact(FOOTER))
        self.assertNotIn(hashlib.sha256(FOOTER.encode("utf-8")).hexdigest(), a,
                         "the bare hash a guessed footer would confirm is not in the token")

    def test_unsalted_tokens_are_the_pinned_fixtures_own(self):
        self.assertEqual(mine.redact(FOOTER),
                         "<redacted:sha256:" + hashlib.sha256(FOOTER.encode("utf-8")).hexdigest() + ">")
        fixture = json.loads((FIXTURES / "invariants-word-maison.json").read_text(encoding="utf-8"))
        footer = next(i for i in fixture["invariants"] if i["id"] == "footer_text")
        self.assertEqual(footer["value"], mine.redact(FOOTER), "invariants-word-maison.json stays byte-identical")

    def test_mining_under_a_salt_writes_salted_tokens_only(self):
        d = a_corpus(5, title_style="Clause Dupont", style_id="ClauseId7", footer=FOOTER)
        inv, vault = mine.mine_corpus(d, salt=SALT)
        by_id = {i["id"]: i for i in inv["invariants"]}
        self.assertEqual(by_id["footer_text"]["value"], mine.redact(FOOTER, SALT))
        self.assertEqual(by_id["title_style"]["value"], {"custom": mine.redact("Clause Dupont", SALT)})
        self.assertEqual(vault, {mine.redact(FOOTER, SALT): FOOTER,
                                 mine.redact("Clause Dupont", SALT): "Clause Dupont"})
        self.assertNotIn("<redacted:sha256:", json.dumps(inv))
        self.assertEqual(sorted(mine.tokens(inv)), sorted(vault), "tokens() sees the salted form")
        self.assertEqual(mine.mine_file(d / "c0.docx", SALT)["vault"], vault, "one file, the same tokens")

    def test_a_salt_file_is_read_whole_or_refused_in_one_line(self):
        self.assertEqual(mine.read_salt(salt_file()), SALT)
        for path, said in ((salt_file(SALT[:31]), "holds 31 bytes, not 32"),
                           (Path(tempfile.mkdtemp()) / "absent.salt", "cannot be read")):
            with self.assertRaises(Refusal) as cm:
                mine.read_salt(path)
            self.assertIn(f"salt file {path} {said}", str(cm.exception))
            self.assertIn("; remedy: vk driver learn again", str(cm.exception))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = entry.main(["mine", "--corpus", str(a_corpus(5)), "--out", str(Path(tempfile.mkdtemp()) / "out"),
                               "--salt-file", str(salt_file(b"short"))])
        self.assertEqual(code, 2)
        self.assertIn("vkforge mine: salt file ", err.getvalue())
```

`drivers/vkforge/tests/test_qualify.py` — after `NOTHING_REPAIRED` (`:22`) add:

```python
SALT = bytes(range(32))
OTHER_SALT = bytes(range(1, 33))
```

Replace `learned` (`:59-67`) with:

```python
def learned(corpus: Path, salt: bytes | None = None):
    """What `vk driver learn` leaves (Task 9), built here in Python: the invariants (under `salt`,
    D-01), the plan, and the driver generated into a temp dir, so every pass also proves it runs with
    no repository around it."""
    inv, vault = mine.mine_corpus(corpus, salt=salt)
    binding = json.loads((FIXTURES / "binding-word.json").read_text(encoding="utf-8"))
    p, _ = plan.build(inv, binding, ontology.load())
    driver = Path(tempfile.mkdtemp()) / "word-maison" / "driver"
    generate.generate(p, binding, inv, vault, driver, name="word-maison")
    return driver, p, inv
```

Replace `test_the_cli_qualifies_from_the_artefacts` (`:193-205`) with:

```python
    def test_the_cli_qualifies_from_the_artefacts(self):
        driver, p, inv = learned(self.corpus, SALT)
        out = driver.parent
        plan.write_plan(p, [], out)
        canon.write(inv, out / "invariants.json")
        salt = out / ".salt"
        salt.write_bytes(SALT)
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            code = entry.main(["qualify", "--driver", str(driver), "--corpus", str(self.corpus),
                               "--plan", str(out / "plan.json"), "--invariants", str(out / "invariants.json"),
                               "--work", str(self.work), "--fake", "--salt-file", str(salt)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads((self.work / "qualification.json").read_text(encoding="utf-8"))["passes"], 10)
        self.assertEqual(printed.getvalue(),
                         f"qualify: 10/10 passes (0 failures), fake true -> {self.work / 'qualification.json'}\n")
```

Add to `QualifyTest`:

```python
    def test_the_learns_salt_qualifies_and_any_other_fails_closed(self):
        # Founder decision D-01, ruling LF-3: the copies are re-mined under the learn's salt; any other
        # salt — none, another learn's — is refused before any copy, never recorded as a failed replay.
        driver, p, inv = learned(self.corpus, SALT)
        footer = next(i["value"] for i in inv["invariants"] if i["id"] == "footer_text")
        self.assertTrue(footer.startswith("<redacted:hmac-sha256:"), footer)
        rec = qualify.qualify(driver, self.corpus, p, inv, self.work, fake=True, salt=SALT)
        self.assertEqual((rec["passes"], rec["failures"]), (10, 0))
        for other in (OTHER_SALT, None):
            work = Path(tempfile.mkdtemp()) / "w"
            with self.assertRaises(Refusal) as cm:
                qualify.qualify(driver, self.corpus, p, inv, work, fake=True, salt=other)
            self.assertEqual(str(cm.exception),
                             "the salt does not give back the footer_text token of invariants.json; "
                             "remedy: vk driver learn again: the salt and invariants.json are one learn's")
            self.assertFalse(work.exists(), "refused before any copy")
        out = driver.parent
        plan.write_plan(p, [], out)
        canon.write(inv, out / "invariants.json")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = entry.main(["qualify", "--driver", str(driver), "--corpus", str(self.corpus),
                               "--plan", str(out / "plan.json"), "--invariants", str(out / "invariants.json"),
                               "--work", str(Path(tempfile.mkdtemp()) / "w"), "--fake",
                               "--salt-file", str(out / "absent.salt")])
        self.assertEqual(code, 2)
        self.assertIn("vkforge qualify: salt file ", err.getvalue())
        self.assertIn(" cannot be read (", err.getvalue())
```

- [ ] **Step 2: Run them, see them fail**

```powershell
& $PY -I -m unittest discover -s drivers/vkforge/tests -t . -p test_mine.py -v
& $PY -I -m unittest discover -s drivers/vkforge/tests -t . -p test_qualify.py -k salt -k cli -v
```

Expected: `TypeError: redact() takes 1 positional argument but 2 were given`, `AttributeError: module 'vkforge.mine' has no attribute 'read_salt'` (and `tokens`), the CLI tests exit through argparse (`unrecognized arguments: --salt-file`, a `SystemExit` the test reports as an error), `TypeError: mine_corpus() got an unexpected keyword argument 'salt'`.

- [ ] **Step 3: Implement the salt in `mine.py`**

Replace `:1-22` (docstring and imports) with:

```python
"""vkforge mine — the firm's documents read as a datasheet: invariants with their support, no content (stage `mine`).

The miner is deterministic and reads structure only: styles, fonts, footers and headers, page
setup, as OOXML declares them. It never opens Office and never reads the body text of a document.
What it does read of the text — a footer, a header, a custom style's name — is free text that can
carry a name or an address: it leaves `invariants.json` as a token, `<redacted:hmac-sha256:…>`,
the HMAC-SHA256 of the text keyed by the learn's salt (`--salt-file`, founder decision D-01), so a
guessed footer cannot be confirmed from `invariants.json` without that salt; its clear value goes
to the vault (`--vault FILE`, by default `<out>/.vault.json`), which stays on the node. The daemon
names the salt and the vault under its own private forge directory, never under `--out` (final
review I-2): on Windows a mode is no ACL, so the directory's ACL is the protection. The vault is
deleted after `generate`; the salt is not, because `qualify` re-mines the copies under it (LF-1).

A value is an invariant when the share of mined files carrying it (its support) reaches the
threshold; below it, two values or more are an ambiguity listed with their candidates, and a lone
value is purged (spec §6: what the firm does not do consistently is not a rule).
"""
import argparse
import hashlib
import hmac
import os
from pathlib import Path

from . import canon, ooxml
from .errors import Refusal
from .ooxml import NS, q
```

Replace `:33-39` (`REMEDY` … `redact`) with:

```python
REMEDY = "more documents of one kind"
SALT_BYTES = 32                      # one learn's salt (D-01): `vk driver learn` draws it, mine and qualify read it
_TOKEN = "<redacted:sha256:"         # no salt: the fixtures pinned before D-01, the fake application's reading (LF-2)
_SALTED = "<redacted:hmac-sha256:"   # under a learn's salt (D-01)
_TOKENS = (_TOKEN, _SALTED)
_SALT_REMEDY = "vk driver learn again: the learn writes its salt beside its vault, and qualify reads that file back"
_CHAIN = 32          # a basedOn chain is followed this far at most (a cycle in styles.xml)


def redact(text: str, salt: bytes | None = None) -> str:
    """Free text as invariants.json carries it (founder decision D-01). Under a learn's salt,
    `<redacted:hmac-sha256:HEX>`: the HMAC-SHA256 of the text's UTF-8 bytes keyed by the salt, so
    the same text gives the same token within one learn and another token in the next, and a
    guess cannot be checked against invariants.json without the salt. With no salt,
    `<redacted:sha256:HEX>`: the bare SHA-256 the fixtures pinned before D-01 carry (LF-2), and the
    reading the fake application compares like with like."""
    data = text.encode("utf-8")
    if salt is None:
        return _TOKEN + hashlib.sha256(data).hexdigest() + ">"
    return _SALTED + hmac.new(salt, data, hashlib.sha256).hexdigest() + ">"


def read_salt(path) -> bytes:
    """The learn's salt: exactly SALT_BYTES bytes read from `path`, or the one-line refusal (L-P15)."""
    try:
        salt = Path(path).read_bytes()
    except OSError as e:
        raise Refusal(f"salt file {path} cannot be read ({e.strerror or type(e).__name__})", _SALT_REMEDY) from None
    if len(salt) != SALT_BYTES:
        raise Refusal(f"salt file {path} holds {len(salt)} bytes, not {SALT_BYTES}", _SALT_REMEDY)
    return salt
```

(The old `_CHAIN = 32` line, `:35`, is the one kept here; the lines `:24-32` — `MIN_FILES`, `BUILTIN_STYLES`, `INVARIANT_IDS` — are unchanged.)

Replace `_style_value` (`:109-120`) with:

```python
def _style_value(sid: str, styles: _Styles, vault: dict, salt: bytes | None) -> dict | None:
    """The built-in anchor of a style, found by its English w:name and never by its styleId. A custom
    style is its w:name (what Word's Styles(name) takes) as a token under the learn's salt, the name
    going to the vault; the styleId is never stored. A style that styles.xml does not declare has no
    name: None."""
    name = styles.name(sid)
    if not name:
        return None
    if name.lower() in BUILTIN_STYLES:
        return {"builtin": BUILTIN_STYLES[name.lower()]}
    token = redact(name, salt)
    vault[token] = name
    return {"custom": token}
```

In `_docx` (`:143-203`): the signature becomes `def _docx(zf, obs: list, vault: dict, salt: bytes | None) -> None:`; `:154` becomes `value = None if first is None else _style_value(first, styles, vault, salt)`; `:160` becomes `values = {canon.dumps(_style_value(sid, styles, {}, salt)) for sid in explicit}`; `:161` becomes `value = _style_value(explicit[0], styles, vault, salt)`; and `:176-181` become:

```python
    for kind, tag, pattern in (("footer", "ftr", "word/footer*.xml"), ("header", "hdr", "word/header*.xml")):
        roots = [ooxml.part(zf, n) for n in ooxml.parts(zf, pattern)]
        text = _joined_text(roots, q("w", "p"), q("w", "t"))
        if text:
            token = redact(text, salt)
            vault[token] = text
            obs.append({"kind": kind, "where": None, "value": token})
```

In `_pptx` (`:206-236`): the signature becomes `def _pptx(zf, obs: list, vault: dict, salt: bytes | None) -> None:` and `:232-236` become:

```python
    footers = placeholder("ftr")
    text = _joined_text(footers[:1], q("a", "p"), q("a", "t"))
    if text:
        token = redact(text, salt)
        vault[token] = text
        obs.append({"kind": "footer", "where": None, "value": token})
```

Replace `mine_file` and `survey` (`:239-267`) with:

```python
def mine_file(path: Path, salt: bytes | None = None) -> dict:
    """One document's observations, its free text redacted under `salt` (D-01); `vault` maps each
    token it used to its clear value. Refusal when it is not OOXML."""
    path = Path(path)
    obs, vault = [], {}
    with ooxml.open_package(path) as zf:
        kind = ooxml.kind_of(zf)
        (_docx if kind == "docx" else _pptx)(zf, obs, vault, salt)
    if _pdf_of(path) is not None:
        obs.append({"kind": "export_format", "where": None, "value": {"format": "pdf"}})
    return {"kind": kind, "obs": obs, "vault": vault}


def survey(corpus: Path, salt: bytes | None = None) -> tuple[list, int]:
    """(the mined files with their observations under `salt`, the number of files skipped). A .pdf
    beside a mined document is that document's export_format, not a skipped file."""
    corpus = Path(corpus)
    if not corpus.is_dir():
        raise Refusal(f"corpus {corpus} is not a folder", "--corpus names a folder of .docx or .pptx files")
    files = sorted(p for p in corpus.iterdir() if p.is_file())
    mined = []
    for p in files:
        if p.suffix.lower() not in (".docx", ".pptx") or p.name.startswith("~$") or ooxml.is_encrypted(p):
            continue
        try:
            mined.append((p, mine_file(p, salt)))
        except Refusal:
            continue                                    # not a package after all: skipped, never an abort
    pdfs = {pdf for p, _ in mined if (pdf := _pdf_of(p)) is not None}
    return mined, len(files) - len(mined) - len(pdfs)
```

Replace `_tokens` (`:275-284`) with:

```python
def tokens(value):
    """Every redaction token at any depth of `value`, salted or not (LF-2), in document order."""
    if isinstance(value, str):
        if value.startswith(_TOKENS):
            yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from tokens(v)
    elif isinstance(value, list):
        for v in value:
            yield from tokens(v)
```

In `mine_corpus` (`:287-327`): the signature becomes `def mine_corpus(corpus: Path, threshold: float = 0.8, salt: bytes | None = None) -> tuple[dict, dict]:`, `:288` becomes `mined, skipped = survey(corpus, salt)`, and `:326` becomes `used = set(tokens(invariants)) | set(tokens(ambiguities))`.

Replace `add_arguments` and `run` (`:347-368`) with:

```python
def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--corpus", required=True, type=Path, help="folder of .docx or .pptx files of one kind")
    parser.add_argument("--out", required=True, type=Path, help="directory that receives invariants.json")
    parser.add_argument("--vault", type=Path,
                        help="file that receives the clear values (default <out>/.vault.json; the daemon names "
                             "one under its private forge directory)")
    parser.add_argument("--salt-file", required=True, type=Path,
                        help=f"the learn's salt, {SALT_BYTES} random bytes (vk driver learn writes it; D-01)")
    parser.add_argument("--threshold", type=float, default=0.8, help="support an invariant needs (default 0.8)")


def run(ns: argparse.Namespace) -> int:
    if not 0 < ns.threshold <= 1:
        raise Refusal(f"threshold {ns.threshold} is not in (0, 1]", "--threshold 0.8")
    corpus, out = Path(ns.corpus).resolve(), Path(ns.out).resolve()
    if out == corpus or corpus in out.parents:
        raise Refusal(f"out {ns.out} is inside the corpus", "write elsewhere")
    if ns.vault is not None and corpus in Path(ns.vault).resolve().parents:
        raise Refusal(f"vault {ns.vault} is inside the corpus", "write elsewhere")
    salt = read_salt(ns.salt_file)
    inv, vault = mine_corpus(ns.corpus, ns.threshold, salt)
    write_invariants(inv, vault, ns.out, ns.vault)
    print(f"mine: {len(inv['invariants'])} invariants, {len(inv['ambiguities'])} ambiguities over "
          f"{inv['corpus']['files']} files ({inv['corpus']['skipped']} skipped) -> {Path(ns.out) / 'invariants.json'}")
    return 0
```

- [ ] **Step 4: Implement the salt in `qualify.py`**

In the module docstring, the sentence that runs from `After each `ok` answer the miner reads the copy again:` to `… a copy whose bytes are still the perturbed ones was not saved.` (it spans `:9-12`; the words before and after it stay) becomes:

```text
After each `ok` answer the miner reads the copy again, under the learn's salt (`--salt-file`,
founder decision D-01: the tokens of `invariants.json` are keyed by it, LF-1): a postcondition
holds when the observation of its (kind, where) has the invariant's value (spec §5.2: checked in
code, never taken from the driver's word), `pdf_exists` when the PDF beside the copy starts with
`%PDF`; a copy whose bytes are still the perturbed ones was not saved. A salt that does not give
back the plan's tokens from the corpus is refused before any copy (LF-3).
```

After `_PERTURB_REMEDY` (`:59`) add:

```python
_SALT_REMEDY = "vk driver learn again: the salt and invariants.json are one learn's"
```

Replace `_check` (`:185-214`) with:

```python
def _check(driver_dir: Path, corpus: Path, plan: dict, invariants: dict, work: Path, salt: bytes | None) -> tuple:
    """Every refusal before any copy, in this order; (the manifest, the plan's action, the manifest's
    entry for it, the corpus files sorted by name — the one survey of the corpus, under the salt)."""
    if work.exists() and (not work.is_dir() or any(work.iterdir())):
        raise Refusal(f"work {work} is not empty", "a new directory: a qualification never reuses one")
    if not (driver_dir / "vendor" / "vkdriver" / "protocol.py").is_file():
        raise Refusal(f"driver {driver_dir} does not vendor vkdriver",
                      "generate it again: the generator vendors vkdriver (L-P6)")
    manifest, _ = ontology.read_json(driver_dir / "manifest.json", "manifest", "generate it again")
    program = manifest.get("program") if isinstance(manifest, dict) else None
    files_sha256 = program.get("files_sha256") if isinstance(program, dict) else None
    actual = hash_dir(str(driver_dir))
    if actual != files_sha256:
        raise Refusal(f"driver {driver_dir} files hash {actual}, not its manifest's files_sha256 {files_sha256}",
                      "generate it again: a learned driver's files are never edited")
    if canon.sha256(invariants) != plan.get("invariants_sha256"):
        raise Refusal("plan and invariants do not belong together", "pass the invariants.json the plan was built from")
    mined, _ = mine.survey(corpus, salt)
    files = sorted((p for p, _ in mined), key=lambda p: p.name)
    if _content_sha256(canon.sha256_file(p) for p in files) != _learned_content(invariants):
        raise Refusal(*_CORPUS_CHANGED)
    action = plan["actions"][0]                     # v1: one action per plan (plan.build)
    entry = next((a for a in manifest.get("actions") or [] if isinstance(a, dict) and a.get("name") == action["name"]),
                 None)
    if entry is None:
        raise Refusal(f"driver {driver_dir} was not generated from this plan", _NOT_THIS_PLAN)
    unknown = next((ident for ident in action["post"] if ident not in PERTURBABLE), None)
    if unknown is not None:
        raise Refusal(f"cannot perturb {unknown} for qualification", _PERTURB_REMEDY)
    _salt_gives_back(mined, action["post"], _expected(plan, invariants))
    return manifest, action, entry, files


def _salt_gives_back(mined: list, post: list, expected: dict) -> None:
    """Ruling LF-3 (founder decision D-01): every token a postcondition must find is one the miner
    gives back from the corpus under this salt. The corpus is the learned one (checked just above),
    so a token it does not give back means the salt is not the learn's — missing, replaced, another
    learn's of the same name. Refused before any copy, rather than every pass failing on a token no
    repair could ever reach."""
    seen = {(o["kind"], o["where"], canon.dumps(o["value"])) for _, result in mined for o in result["obs"]}
    for ident in post:
        value = expected.get(ident)
        if ident in _WHERE and next(mine.tokens(value), None) is not None:
            kind, where = _WHERE[ident]
            if (kind, where, canon.dumps(value)) not in seen:
                raise Refusal(f"the salt does not give back the {ident} token of invariants.json", _SALT_REMEDY)
```

In `_prepare` (`:385-404`): the signature becomes `def _prepare(files: list, work: Path, post: list, expected: dict, learned_content: str, salt: bytes | None = None) -> list:` and `:400` becomes `held = next((ident for ident, v in _post(post, c["copy"], expected, salt).items() if v), None)`.

Replace `_post` (`:424-442`) with:

```python
def _post(post: list, copy: Path, expected: dict, salt: bytes | None = None) -> dict:
    """Each post id of the plan, held or not on the copy as the miner reads it again under the
    learn's salt (values compared in canonical form, as the planner compares candidates)."""
    try:
        obs = mine.mine_file(copy, salt)["obs"]
    except (Refusal, OSError):                      # the action left no OOXML package behind it
        obs = []
    held = {}
    for ident in post:
        if ident == "pdf_exists":
            held[ident] = _is_pdf(copy.with_suffix(".pdf"))
        elif ident in _WHERE and ident in expected:
            kind, where = _WHERE[ident]
            want = canon.dumps(expected[ident])
            held[ident] = any(o["kind"] == kind and o["where"] == where and canon.dumps(o["value"]) == want
                              for o in obs)
        else:
            held[ident] = False
    return held
```

In `_pass` (`:445-471`): the signature becomes `def _pass(driver: _Driver, index: int, c: dict, action: dict, entry: dict, expected: dict, deadline_s: float, salt: bytes | None = None) -> tuple[dict, bool]:` and `:462` becomes `post = _post(action["post"], copy, expected, salt)`.

In `qualify` (`:474-508`): the signature becomes `def qualify(driver_dir: Path, corpus: Path, plan: dict, invariants: dict, work: Path, fake: bool, deadline_s: float = PASS_DEADLINE_S, salt: bytes | None = None) -> dict:`; `:478` becomes `manifest, action, entry, files = _check(driver_dir, corpus, plan, invariants, work, salt)`; `:484` becomes `copies = _prepare(files, work, action["post"], expected, _learned_content(invariants), salt)`; `:495` becomes `run, ended = _pass(driver, index, c, action, entry, expected, deadline_s, salt)`.

Replace `add_arguments` and `run` (`:511-534`) with:

```python
def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--driver", required=True, type=Path, help="the generated driver's directory")
    parser.add_argument("--corpus", required=True, type=Path, help="the corpus the driver was learned from")
    parser.add_argument("--plan", required=True, type=Path, help="plan.json the driver was generated from")
    parser.add_argument("--invariants", required=True, type=Path, help="invariants.json the plan was built from")
    parser.add_argument("--work", required=True, type=Path,
                        help="new or empty directory that receives the copies and qualification.json")
    parser.add_argument("--salt-file", required=True, type=Path,
                        help="the salt the learn drew (D-01): the copies are re-mined under it")
    parser.add_argument("--fake", action="store_true",
                        help="the repository's fake application instead of Office (L-P19)")


def run(ns: argparse.Namespace) -> int:
    p, data = ontology.read_json(ns.plan, "plan", "run vkforge plan again")
    if data != canon.dumps(p):                          # so the record's plan_sha256 is the file's hash
        raise Refusal("plan.json is not in canonical form", "run vkforge plan again")
    invariants, data = ontology.read_json(ns.invariants, "invariants", "run vkforge mine again")
    if data != canon.dumps(invariants):
        raise Refusal("invariants.json is not in canonical form", "run vkforge mine again")
    if not (isinstance(p, dict) and isinstance(invariants, dict)):
        raise Refusal("plan and invariants must each be a JSON object", "run vkforge learn again")
    salt = mine.read_salt(ns.salt_file)
    rec = qualify(ns.driver, ns.corpus, p, invariants, ns.work, ns.fake, salt=salt)
    print(f"qualify: {rec['passes']}/{rec['passes'] + rec['failures']} passes ({rec['failures']} failures), "
          f"fake {'true' if rec['fake'] else 'false'} -> {Path(ns.work) / 'qualification.json'}")
    return 0
```

- [ ] **Step 5: Run the Python tests, see them pass**

```powershell
& $PY -I -m unittest discover -s drivers/vkforge/tests -t . -p test_mine.py -v
& $PY -I -m unittest discover -s drivers/vkforge/tests -t . -p test_qualify.py -v
& $PY -I -m unittest discover -s drivers/vkforge/tests -t .
```

Expected: all pass; the last line of the whole suite `OK (skipped=…)` (the skips are the real-Office tests off the node and the by-design `test_without_fake_json_the_driver_never_reaches_the_fake` where Word is installed).

- [ ] **Step 6: Write the failing Rust tests** (`crates/vk-ipc/tests/forge.rs`)

In `learn_runs_the_five_stages_and_writes_driver_learned`, right after `let inv: Value = serde_json::from_slice(&inv_bytes).unwrap();` (`:122`), add:

```rust
    // Founder decision D-01: the tokens are keyed by this learn's salt, which stays in the private
    // forge directory after the vault is gone (ruling LF-1); no bare SHA-256 a guess could confirm.
    let text = std::str::from_utf8(&inv_bytes).unwrap();
    assert!(text.contains("<redacted:hmac-sha256:"), "{text}");
    assert!(!text.contains("<redacted:sha256:"), "{text}");
    let salt = node.dir().join("forge").join("word-maison").join(".salt");
    assert_eq!(
        std::fs::read(&salt).unwrap().len(),
        32,
        "the salt stays beside the deleted vault"
    );
```

Append the new test:

```rust
/// Founder decision D-01, rulings LF-1 and LF-3: `driver.qualify` hands the stage the salt the learn
/// drew. A salt replaced since, or gone, is refused in the stage's one line before any copy, and no
/// `driver.qualified` is written; the learn's own salt qualifies.
#[tokio::test]
async fn qualify_reads_the_learns_salt_and_fails_closed_without_it() {
    if !forge_available() {
        return;
    }
    let node = Node::start().await;
    let corpus = ooxml_corpus(&node.dir().join("corpus"), 10, 0);
    let out = node.dir().join("word-maison");
    node.call_human("driver.learn", learn_params(&corpus, &out))
        .await
        .unwrap();
    let params = json!({"driver": out.join("driver"), "corpus": corpus});
    let salt = node.dir().join("forge").join("word-maison").join(".salt");
    let drawn = std::fs::read(&salt).unwrap();
    std::fs::write(&salt, [7u8; 32]).unwrap();
    let e = node
        .call("driver.qualify", params.clone())
        .await
        .unwrap_err();
    assert_eq!(
        call_error(&e).message,
        "vkforge qualify: the salt does not give back the footer_text token of invariants.json; \
         remedy: vk driver learn again: the salt and invariants.json are one learn's"
    );
    std::fs::remove_file(&salt).unwrap();
    let e = node
        .call("driver.qualify", params.clone())
        .await
        .unwrap_err();
    let m = call_error(&e).message.clone();
    assert!(
        m.starts_with("vkforge qualify: salt file ") && m.contains(" cannot be read ("),
        "{m}"
    );
    assert!(node.events("driver.qualified").is_empty());
    std::fs::write(&salt, &drawn).unwrap();
    let q = node.call("driver.qualify", params).await.unwrap();
    assert_eq!(
        (q["passes"].as_u64(), q["failures"].as_u64()),
        (Some(10), Some(0))
    );
    node.shutdown().await;
}
```

- [ ] **Step 7: Run them, see them fail**

```powershell
cargo test --locked -p vk-ipc --test forge -- learn_runs_the_five_stages qualify_reads_the_learns_salt --nocapture
```

Expected: both fail at the first stage that needs the salt — `vkforge mine: …` is refused by argparse (`the following arguments are required: --salt-file`), relayed as the learn's refusal.

- [ ] **Step 8: Implement the salt in `crates/vk-ipc/src/forge.rs`**

After `impl Drop for Vault` (`:291-297`) add:

```rust
/// The learn's salt (founder decision D-01): `<state_dir>/forge/<name>/.salt`, 32 random bytes beside
/// the vault, in the same private directory. It keys every `<redacted:hmac-sha256:…>` token of
/// `invariants.json`. Unlike the vault it is not deleted after `generate` (ruling LF-1): `qualify`
/// re-mines the replayed copies under it. The next `learn` of the name clears it with the directory.
const SALT_FILE: &str = ".salt";

/// The salt's bytes from the OS generator, written to a new file at `path` (the learn cleared the
/// directory first, so the file is new).
fn write_salt(path: &Path) -> Result<(), RpcError> {
    use rand::RngCore;
    use std::io::Write;
    let mut salt = [0u8; 32];
    rand::rngs::OsRng.fill_bytes(&mut salt);
    std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .and_then(|mut file| file.write_all(&salt))
        .map_err(|e| io_failed("write the learn's salt", &e))
}
```

Replace the `mine` stage (`:465-481`) with:

```rust
    // 3. The invariants of the corpus, under this learn's salt (D-01). From here the vault may exist,
    // and goes on every way out. Both live under the daemon's forge directory, which `run_stage`
    // made private before the first stage started (I-2): the operator's `--out` never holds a clear
    // string, nor the key to its tokens.
    let salt = logs.join(SALT_FILE);
    write_salt(&salt)?;
    let vault = Vault(logs.join(".vault.json"));
    stage(
        "mine",
        vec![
            "--corpus".into(),
            arg(&corpus),
            "--out".into(),
            arg(&out),
            "--vault".into(),
            arg(&vault.0),
            "--salt-file".into(),
            arg(&salt),
            "--threshold".into(),
            threshold.to_string(),
        ],
    )?;
```

In `qualify` (`:836-919`): the doc comment's last sentence (`:841-843`) becomes `… The stage's work directory is the daemon's own scratch, `<state_dir>/drivers/<name>/work/qualify`, emptied first; it re-mines the copies under the learn's salt, `<state_dir>/forge/<name>/.salt` (D-01); its logs go under `<state_dir>/forge/qualify-<name>/`; its deadline is a minute and 130 s per document.`, and the `args` vector (`:903-914`) becomes:

```rust
    // The learn's salt (D-01): the stage re-mines the copies under it, and refuses one that does not
    // give back the plan's tokens (ruling LF-3) — or that is gone — in its own line.
    let salt = state_dir.join("forge").join(&name).join(SALT_FILE);
    let mut args = vec![
        "--driver".into(),
        arg(&driver),
        "--corpus".into(),
        arg(&corpus),
        "--plan".into(),
        arg(&plan_file),
        "--invariants".into(),
        arg(&invariants),
        "--work".into(),
        arg(&work),
        "--salt-file".into(),
        arg(&salt),
    ];
```

- [ ] **Step 9: Run the Rust tests, see them pass**

```powershell
cargo test --locked -p vk-ipc --test forge -- --nocapture
cargo test --locked -p vk-cli --test smoke vk_forge_ -- --nocapture
```

Expected: every `forge` test passes (the two above among them), and the three `vk_forge_` smoke tests too (they learn and qualify through the CLI, now with the salt in between).

- [ ] **Step 10: Docs**

`contracts/tcb.md:241` — in the bullet **What learning trusts, and what it does not**, replace the passage from `is free text that leaves `invariants.json` as a `<redacted:sha256:…>` token;` to `… and the vault is deleted when the driver is generated.` with:

```markdown
is free text that leaves `invariants.json` as a `<redacted:hmac-sha256:…>` token: the HMAC-SHA256 of the text keyed by a salt of 32 random bytes the kernel draws for each learn (founder decision D-01), so a guessed footer cannot be confirmed from `invariants.json` — which travels with `--out` — without that salt. Its clear value lives in a vault, `<state_dir>/forge/<name>/.vault.json`, under the daemon's private forge directory and the state directory's ACL, never in the operator's `--out` (final review I-2), and the vault is deleted when the driver is generated; the salt, `<state_dir>/forge/<name>/.salt`, sits beside it under the same ACL and is not deleted with it, because `vk driver qualify` re-mines the replayed copies under it — a salt replaced or gone is refused before any copy (ruling LF-3) — and the next `learn` of the name clears it.
```

`docs/technical/technical-readme.md:710` — the `mine` row's "Writes" cell becomes:

```markdown
`invariants.json` (values with their support, no text of the documents: a footer, a header or a custom style's name is a `<redacted:hmac-sha256:…>` token keyed by the learn's salt, `<state_dir>/forge/<name>/.salt`, which the kernel draws for each learn and `qualify` reads back); the clear values a footer needs go to the vault, `<state_dir>/forge/<name>/.vault.json` (`mine --vault`), under the daemon's private forge directory and the state directory's ACL, never under `--out` |
```

`docs/technical/runbooks/learn-office-driver.md` — after the `.vault.json` row of the §0 table (`:28`) add the row:

```markdown
| `.salt` | Le sel de l'apprentissage : 32 octets tirés au sort par `learn`, posés à côté du coffre (`<état>\forge\<nom>\.salt`). Les jetons de `invariants.json` en dépendent : sans lui, on ne peut pas vérifier un pied de page deviné. Il survit à `generate`, parce que `qualify` en a besoin. |
```

`:88` becomes: `Le grand-livre reçoit `driver.learned` ; le fichier `.vault.json` n'existe plus (il n'a jamais été dans `--out`) ; le sel `.salt` reste à côté, pour `qualify`.`

Rows added to the §8 table, after the `corpus changed since vkforge mine` row (`:173`):

```markdown
| `salt file <…>\.salt cannot be read (…); remedy: vk driver learn again: …` | le sel de l'apprentissage a disparu (répertoire `forge` vidé à la main), ou le driver a été appris avant le sel | réapprendre (§3), puis requalifier |
| `the salt does not give back the footer_text token of invariants.json; remedy: vk driver learn again: …` | le sel n'est plus celui de cet apprentissage : un autre `learn` du même nom l'a remplacé | réapprendre, puis requalifier |
```

- [ ] **Step 11: The whole suites, the fixtures, the docs check**

```powershell
& $PY -I -m unittest discover -s drivers/vkforge/tests -t .
git diff --exit-code drivers/vkforge/tests/fixtures
cargo test --locked -p vk-ipc --test forge -- --nocapture
cargo fmt --all --check
cargo clippy --workspace --all-targets -- -D warnings
python -I scripts/docs/check_docs.py
```

Expected: green; `git diff` prints nothing (the pinned fixtures did not move); `check_docs.py` prints nothing and exits 0.

- [ ] **Step 12: Commit**

```bash
git add drivers/vkforge/mine.py drivers/vkforge/qualify.py drivers/vkforge/tests/test_mine.py drivers/vkforge/tests/test_qualify.py crates/vk-ipc/src/forge.rs crates/vk-ipc/tests/forge.rs contracts/tcb.md docs/technical/technical-readme.md docs/technical/runbooks/learn-office-driver.md
git commit -F - <<'MSG'
feat(learning): redaction tokens keyed by a per-learn salt the daemon keeps beside the vault (D-01)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UUDLpkwxsZXmLptqCeNL2R
MSG
```

---

### Task 2: D-02 — the vault keeps only what the plan uses, before `generate` (Sonnet)

**Model:** Sonnet (a stage flag and one kernel argument, on Task 1's `mine.tokens`). **Decision:** D-02. **Rulings:** LF-4, PF-11 (the docs say D-02 holds on the kernel path).

**Files:**
- Modify: `drivers/vkforge/plan.py:20` (import `mine`), after `load_resolver` (`:295-311`: new `vault_tokens`, `read_vault`, `reduce_vault`), `:314-319` (`add_arguments`), `:322-335` (`run`)
- Modify: `crates/vk-ipc/src/forge.rs:482-490` (the `plan` stage's arguments)
- Test: `drivers/vkforge/tests/test_plan.py` (imports `:1-13`; new `VaultTest`), `crates/vk-ipc/tests/forge.rs` (new `FOOTER_CORPUS`, `footer_corpus`, `learn_leaves_no_purged_candidate_in_the_vault_before_generate`)
- Docs: `contracts/tcb.md:241`, `docs/technical/technical-readme.md:711`, `docs/technical/runbooks/learn-office-driver.md:28`

**Interfaces:**
- Consumes: `mine.tokens(value)` and `mine.mine_corpus(corpus, threshold=0.8, salt=None)` (Task 1); `generate.generate(plan, binding, invariants, vault, out, name, venv=None)` (unchanged); `plan.build(invariants, binding, ontology, resolve=None)` (unchanged).
- Produces: `plan.vault_tokens(plan: dict, invariants: dict) -> set[str]`; `plan.read_vault(path) -> dict` (a JSON object of strings, else `Refusal("vault <path> is not an object of clear strings", "run vkforge mine again")`); `plan.reduce_vault(path, vault: dict, plan: dict, invariants: dict) -> tuple[int, int]` — `(kept, of)`, the file rewritten in place by `canon.write`; CLI `plan --vault FILE` (optional): the vault is read before the plan is built, rewritten after `plan.json` is written, and the printed line gains `; vault kept K of V` before ` -> <plan.json>`. Rust: the kernel's `plan` stage gets `--vault <state_dir>/forge/<name>/.vault.json`, the file `mine` wrote and `generate` reads.

- [ ] **Step 1: Write the failing Python tests** (`drivers/vkforge/tests/test_plan.py`)

Imports (replace `:10-13`):

```python
from vkforge import __main__ as entry
from vkforge import canon, generate, inventory, mine, ontology, plan
from vkforge.errors import Refusal
from vkforge.tests import ooxml_fixtures
from vkforge.tests.ooxml_fixtures import FOOTER
```

After `TIE` (`:17-19`) add:

```python
FOOTERS = [f"Contrat client {n}" for n in range(10)]
VAULT_SALT = bytes(32)


def footer_corpus() -> Path:
    """Ten contracts whose footers all differ — a client's name in each: the footer is an ambiguity
    of ten token candidates (founder decision D-02's case)."""
    d = Path(tempfile.mkdtemp())
    for n, footer in enumerate(FOOTERS):
        ooxml_fixtures.docx(d / f"c{n}.docx", footer=footer, pdf=True)
    return d
```

Append at the end of the file:

```python
class VaultTest(unittest.TestCase):
    """Founder decision D-02, ruling LF-4: after plan, the vault keeps the values the plan uses and
    nothing else — here, none of ten per-document footers unless an arch chose one."""

    def setUp(self):
        self.ont = ontology.load()
        self.binding = json.loads((FIXTURES / "binding-word.json").read_text(encoding="utf-8"))
        self.inv, self.vault = mine.mine_corpus(footer_corpus(), salt=VAULT_SALT)
        (self.amb,) = self.inv["ambiguities"]
        self.assertEqual((self.amb["invariant"], len(self.amb["candidates"]), sorted(self.vault.values())),
                         ("footer_text", 10, sorted(FOOTERS)))
        self.dir = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def written_vault(self) -> Path:
        path = self.dir / ".vault.json"
        canon.write(self.vault, path)
        return path

    def test_a_purged_ambiguity_leaves_none_of_its_candidates_in_the_vault(self):
        p, _ = plan.build(self.inv, self.binding, self.ont)
        self.assertEqual(p["ambiguities"], [{"invariant": "footer_text", "resolved_by": "purged"}])
        self.assertEqual(plan.vault_tokens(p, self.inv), set())
        path = self.written_vault()
        self.assertEqual(plan.reduce_vault(path, self.vault, p, self.inv), (0, 10))
        self.assertEqual(path.read_bytes(), canon.dumps({}))

    def test_a_resolved_ambiguity_keeps_only_the_chosen_token_and_generate_still_resolves_it(self):
        chosen = self.amb["candidates"][3]["value"]
        p, _ = plan.build(self.inv, self.binding, self.ont,
                          resolve=lambda amb: {"value": chosen, "resolved_by": ARCH})
        self.assertEqual(plan.vault_tokens(p, self.inv), {chosen})
        path = self.written_vault()
        self.assertEqual(plan.reduce_vault(path, self.vault, p, self.inv), (1, 10))
        reduced = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(reduced, {chosen: self.vault[chosen]})
        out = self.dir / "word-maison"
        generate.generate(p, self.binding, self.inv, reduced, out, name="word-maison")
        self.assertEqual(json.loads((out / "secrets.json").read_text(encoding="utf-8")),
                         {"footer_text": self.vault[chosen]})

    def test_an_excluded_invariant_loses_its_token_and_a_kept_one_keeps_it(self):
        d = Path(tempfile.mkdtemp())
        for n in range(5):
            ooxml_fixtures.docx(d / f"c{n}.docx", title_style="Clause Dupont", style_id="ClauseId7", footer=FOOTER)
        inv, vault = mine.mine_corpus(d, salt=VAULT_SALT)
        p, excluded = plan.build(inv, self.binding, self.ont)
        self.assertIn({"invariant": "title_style", "why": "a custom style has no built-in anchor (v1)"}, excluded)
        footer = mine.redact(FOOTER, VAULT_SALT)
        self.assertEqual(plan.vault_tokens(p, inv), {footer})
        path = self.dir / "custom.vault.json"
        canon.write(vault, path)
        self.assertEqual(plan.reduce_vault(path, vault, p, inv), (1, 2))
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {footer: FOOTER},
                         "the custom style's name is gone, the footer stays")

    def test_the_cli_rewrites_the_vault_in_place_and_says_what_it_kept(self):
        canon.write(self.inv, self.dir / "invariants.json")
        path = self.written_vault()
        chosen = self.amb["candidates"][0]["value"]
        canon.write({"footer_text": {"value": chosen, "resolved_by": ARCH}}, self.dir / "resolver.json")
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            self.assertEqual(entry.main(["plan", "--invariants", str(self.dir / "invariants.json"),
                                         "--binding", str(FIXTURES / "binding-word.json"),
                                         "--out", str(self.dir / "out"), "--resolver", str(self.dir / "resolver.json"),
                                         "--vault", str(path)]), 0)
        self.assertIn(f"; vault kept 1 of 10 -> {self.dir / 'out' / 'plan.json'}\n", printed.getvalue())
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {chosen: self.vault[chosen]})
        for clear in FOOTERS:
            if clear != self.vault[chosen]:
                self.assertNotIn(clear.encode("utf-8"), path.read_bytes())

    def test_a_vault_that_is_not_an_object_of_clear_strings_is_refused_before_the_plan(self):
        path = self.dir / ".vault.json"
        path.write_text(json.dumps({"<redacted:sha256:aa>": 3}), encoding="utf-8")
        canon.write(self.inv, self.dir / "invariants.json")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(entry.main(["plan", "--invariants", str(self.dir / "invariants.json"),
                                         "--binding", str(FIXTURES / "binding-word.json"),
                                         "--out", str(self.dir / "out"), "--vault", str(path)]), 2)
        self.assertEqual(err.getvalue(), f"vkforge plan: vault {path} is not an object of clear strings; "
                                         "remedy: run vkforge mine again\n")
        self.assertFalse((self.dir / "out").exists(), "refused before the plan was written")
```

- [ ] **Step 2: Run them, see them fail**

```powershell
& $PY -I -m unittest discover -s drivers/vkforge/tests -t . -p test_plan.py -k Vault -v
```

Expected: `AttributeError: module 'vkforge.plan' has no attribute 'vault_tokens'` (and `reduce_vault`); the CLI tests exit through argparse (`unrecognized arguments: --vault`).

- [ ] **Step 3: Implement** (`drivers/vkforge/plan.py`)

`:20` becomes `from . import canon, inventory, mine, ontology`.

After `load_resolver` (`:311`) add:

```python
def vault_tokens(plan: dict, invariants: dict) -> set:
    """The tokens the generator may resolve (founder decision D-02, ruling LF-4): those at any depth
    of the value of every invariant an action of the plan keeps (its id among the action's `post`)
    and of every ambiguity the plan resolved. A purged ambiguity's candidates — per-document footers,
    a client's name in each — and an excluded invariant's text are not among them."""
    kept = {ident for action in plan["actions"] for ident in action["post"]}
    values = [i["value"] for i in invariants["invariants"] if i["id"] in kept]
    values += [a["value"] for a in plan["ambiguities"] if a.get("value") is not None]
    return {token for value in values for token in mine.tokens(value)}


def read_vault(path: Path) -> dict:
    """The vault mine wrote: a JSON object of clear strings, or the refusal (one line, L-P15)."""
    vault, _ = ontology.read_json(path, "vault", "run vkforge mine again")
    if not (isinstance(vault, dict) and all(isinstance(v, str) for v in vault.values())):
        raise Refusal(f"vault {path} is not an object of clear strings", "run vkforge mine again")
    return vault


def reduce_vault(path: Path, vault: dict, plan: dict, invariants: dict) -> tuple[int, int]:
    """Founder decision D-02: the vault at `path` rewritten in place — the same file, so the same
    private directory and ACL, by canon.write — keeping only vault_tokens(plan, invariants).
    (kept, of)."""
    keep = vault_tokens(plan, invariants)
    reduced = {token: vault[token] for token in sorted(vault) if token in keep}
    canon.write(reduced, Path(path))
    return len(reduced), len(vault)
```

Replace `add_arguments` and `run` (`:314-335`) with:

```python
def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--invariants", required=True, type=Path, help="invariants.json written by vkforge mine")
    parser.add_argument("--binding", required=True, type=Path, help="binding-<app>.json written by vkforge bind")
    parser.add_argument("--out", required=True, type=Path, help="directory that receives plan.json and plan-excluded.json")
    parser.add_argument("--ontology", type=Path, help="an ontology file instead of ontology/office-document.json")
    parser.add_argument("--resolver", type=Path, help="resolver.json: the ambiguities an arch resolved")
    parser.add_argument("--vault", type=Path,
                        help="the vault mine wrote: rewritten in place with only the values the plan uses (D-02)")


def run(ns: argparse.Namespace) -> int:
    ont = ontology.load(ns.ontology)
    invariants, data = ontology.read_json(ns.invariants, "invariants", "run vkforge mine again")
    if data != canon.dumps(invariants):                 # so canon.sha256(invariants) is the file's hash
        raise Refusal("invariants.json is not in canonical form", "run vkforge mine again")
    binding, _ = ontology.read_json(ns.binding, "binding", "run vkforge bind again")
    resolve = None if ns.resolver is None else load_resolver(ns.resolver)
    vault = None if ns.vault is None else read_vault(ns.vault)
    p, excluded = build(invariants, binding, ont, resolve)
    path = write_plan(p, excluded, ns.out)
    a = p["actions"][0]
    purged = sum(x["resolved_by"] == "purged" for x in p["ambiguities"])
    line = (f"plan: {a['name']} {len(a['steps'])} steps, post {','.join(a['post'])}; "
            f"ambiguities {len(p['ambiguities'])} ({purged} purged); excluded {len(excluded)}")
    if vault is not None:
        kept, of = reduce_vault(ns.vault, vault, p, invariants)
        line += f"; vault kept {kept} of {of}"
    print(f"{line} -> {path}")
    return 0
```

- [ ] **Step 4: Run the Python tests, see them pass**

```powershell
& $PY -I -m unittest discover -s drivers/vkforge/tests -t . -p test_plan.py -v
& $PY -I -m unittest discover -s drivers/vkforge/tests -t .
```

Expected: all pass; `test_the_fixtures_for_tasks_7_and_8_are_committed` still compares equal (the plan itself did not change).

- [ ] **Step 5: Write the failing Rust test** (`crates/vk-ipc/tests/forge.rs`, appended)

```rust
/// Ten contracts whose footers all differ — a client's name in each — written with
/// `drivers/vkforge/tests/ooxml_fixtures.py` (founder decision D-02's case).
const FOOTER_CORPUS: &str = r#"import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from vkforge.tests import ooxml_fixtures
d = Path(sys.argv[2])
d.mkdir(parents=True, exist_ok=True)
for n in range(10):
    ooxml_fixtures.docx(d / f"c{n}.docx", footer=f"Contrat client {n}", pdf=True)
"#;

fn footer_corpus(dir: &Path) -> PathBuf {
    let ok = std::process::Command::new(forge_python())
        .args(["-I", "-c", FOOTER_CORPUS])
        .arg(repo_root().join("drivers"))
        .arg(dir)
        .status()
        .expect("run the vkforge interpreter")
        .success();
    assert!(ok, "the footer corpus {}", dir.display());
    dir.to_path_buf()
}

/// Founder decision D-02, ruling LF-4: the kernel hands `plan` the vault, which it rewrites in place
/// before `generate` reads it. Ten footers that all differ are one ambiguity of ten token
/// candidates; with no arch it is purged, and the vault `generate` reads holds none of them.
#[tokio::test]
async fn learn_leaves_no_purged_candidate_in_the_vault_before_generate() {
    if !forge_available() {
        return;
    }
    let node = Node::start().await;
    let corpus = footer_corpus(&node.dir().join("corpus"));
    let out = node.dir().join("word-maison");
    let r = node
        .call_human("driver.learn", learn_params(&corpus, &out))
        .await
        .unwrap();
    assert_eq!(
        r["ambiguities"],
        json!([{"invariant": "footer_text", "resolved_by": "purged"}])
    );
    let said = std::fs::read_to_string(
        node.dir()
            .join("forge")
            .join("word-maison")
            .join("forge-plan.out"),
    )
    .unwrap();
    assert!(said.contains("; vault kept 0 of 10 -> "), "{said}");
    assert!(
        !out.join("driver").join("secrets.json").exists(),
        "no secret left to resolve"
    );
    node.shutdown().await;
}
```

- [ ] **Step 6: Run it, see it fail**

```powershell
cargo test --locked -p vk-ipc --test forge -- learn_leaves_no_purged_candidate --nocapture
```

Expected: FAIL on `said.contains("; vault kept 0 of 10 -> ")`: the kernel does not pass `--vault` yet, so `forge-plan.out` has no vault count.

- [ ] **Step 7: Implement** (`crates/vk-ipc/src/forge.rs:482-490`)

```rust
    // 4. With an arch, one model call per ambiguity; then the plan, which rewrites the vault in place
    // with only the values it keeps (founder decision D-02): a purged candidate — a client's name in
    // a per-document footer — never reaches `generate`.
    let mut args = vec![
        "--invariants".into(),
        arg(&invariants),
        "--binding".into(),
        arg(&binding),
        "--out".into(),
        arg(&out),
        "--vault".into(),
        arg(&vault.0),
    ];
```

- [ ] **Step 8: Run the Rust tests, see them pass**

```powershell
cargo test --locked -p vk-ipc --test forge -- --nocapture
cargo test --locked -p vk-cli --test smoke vk_forge_ -- --nocapture
```

- [ ] **Step 9: Docs**

`contracts/tcb.md:241` — after the sentence Task 1 wrote, ending `… and the next `learn` of the name clears it.`, insert:

```markdown
Between `plan` and `generate` the vault keeps only the clear values of the invariants the plan kept and of the ambiguities it resolved (founder decision D-02, ruling LF-4): a purged ambiguity's candidates — per-document footers, a client's name in each — and an excluded invariant's text are gone before `generate` reads it. This holds on the kernel's path: `vk driver learn` passes `--vault` to the `plan` stage. The flag is optional, and a vkforge `plan` run by hand without it leaves the vault as `mine` wrote it.
```

`docs/technical/technical-readme.md:711` — the `plan` row becomes:

```markdown
| `plan` | invariants, binding, the arch's answers if any, and the vault | `plan.json` (the `DriverPlan`: one action, its steps, `pre`, `post`, `qualification` thresholds: `min_passes` ten, or the number of documents mined when fewer, `min_rate` 1), `plan-excluded.json`; and the vault rewritten in place (`plan --vault`, which `vk driver learn` always passes) with only the values the plan uses — a purged ambiguity's candidates leave it before `generate` (founder decision D-02, on the kernel's path) |
```

`docs/technical/runbooks/learn-office-driver.md:28` — the `.vault.json` row becomes:

```markdown
| `.vault.json` | La mémoire de travail du mineur (le texte clair d'un pied de page), en RAM protégée : sous le répertoire privé du démon (`<état>\forge\<nom>\`), jamais dans le dossier `--out`. Après `plan`, elle ne garde que les valeurs dont le plan se sert : les candidats d'une ambiguïté purgée (dix pieds de page différents, un nom de client dans chacun) en sortent. Elle est effacée dès que `generate` rend la main. |
```

- [ ] **Step 10: The whole suites and the docs check**

```powershell
& $PY -I -m unittest discover -s drivers/vkforge/tests -t .
git diff --exit-code drivers/vkforge/tests/fixtures
cargo fmt --all --check
cargo clippy --workspace --all-targets -- -D warnings
python -I scripts/docs/check_docs.py
```

- [ ] **Step 11: Commit**

```bash
git add drivers/vkforge/plan.py drivers/vkforge/tests/test_plan.py crates/vk-ipc/src/forge.rs crates/vk-ipc/tests/forge.rs contracts/tcb.md docs/technical/technical-readme.md docs/technical/runbooks/learn-office-driver.md
git commit -F - <<'MSG'
feat(learning): the vault keeps only what the plan uses before generate reads it (D-02)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UUDLpkwxsZXmLptqCeNL2R
MSG
```

---

### Task 3: D-03 + D-04 + D-35 — the Office instance the driver started: identified by its process, its dialogs only, a kill-on-close Job, and a strictly bounded clean end (Opus)

**Model:** Opus. **Decisions:** D-03, D-04, D-35 (2026-10-20; it replaces the first LF-7). **Rulings:** LF-5 (as PF-4 rewrote it), LF-6, LF-7 (D-35; bound (4) as PF-12 reads it), LF-8, PF-2, PF-3, PF-4, PF-5, PF-11, PF-12.

`runtime/com.py` is vendored into every generated driver: this task is a new driver identity for every later learn (intended); no pinned golden moves (`driver-word-maison.expected` is `render_driver`'s `driver.py`, whose text does not change here, and `test_the_generated_driver_imports_vkdriver_from_its_own_vendor` compares the vendored `com.py` with the repository's at run time).

**Files:**
- Modify: `drivers/vkforge/runtime/com.py:1-4` (docstring), `:5-11` (imports: `time`), `:15-19` (constants), new code after `executables()` (`:22-25`: structures, `processes`, `pids_of`, `dcom_launcher_pid`, `identified`, `Job`, `Owned`, `_hold`, `own`), `:28-70` (`dialogs`), `:82-116` (`guarded`), `:184-305` (`Application`, whole class)
- Modify: `drivers/vkforge/runtime/fake.py:1-7` (docstring), `:21-23` (`FAKE_PROCESS`), `:64-70` (`FakeApplication.__init__`), `:98-105` (`__dialog__`)
- Modify: `drivers/vkforge/qualify.py:20-21` and `:70-71` (two docstring sentences)
- Test: `drivers/vkforge/tests/test_runtime.py` (imports `:1-22`; `StubDocument.Close` `:83-85`; the two real-Word tests, `:189-190` and `:204-205`, given `VK_DRIVER_APPLICATIONS`; the `connect=` lambdas at `:231`, `:309`, `:332`; `test_the_users_instance_gets_its_display_alerts_back_and_only_a_started_one_is_quit` `:282-303`, rewritten; new helpers and tests), `drivers/vkforge/tests/test_generate.py` (imports `:1-23`; `test_a_surviving_dialog_is_a_dialog_refusal` `:218-239`; new `driver_pid`, `running`, `PPT`, `STARTER`, the real-Word kill test and the two PowerPoint node tests of PF-5)
- Docs: `contracts/tcb.md:248`, `:250`, `:252` (the L-P49 sentence, PF-11); `docs/technical/runbooks/learn-office-driver.md:106`, `:116`, `:186-190`

**Interfaces:**
- Consumes: nothing from Tasks 1–2 (the adapter knows no salt).
- Produces (`drivers/vkforge/runtime/com.py`):
  - `QUIT_WAIT_S = 3.0`, the clean-end window (D-35 (1)); `COM_TRUE = -1`, VARIANT_TRUE — also PowerPoint's `msoTrue`, so one value sets Word's `bool` and PowerPoint's `MsoTriState` (L-P31); read back, Word's `bool` comes as `True` (comtypes converts a `VARIANT_BOOL`) and PowerPoint's `MsoTriState` as `-1`, and the hand-over takes nothing else for true (PF-12);
  - `processes() -> list[tuple[int, int, str]] | None` — `(pid, parent pid, EXE NAME)` from one Toolhelp32 snapshot; `None` off Windows or when the snapshot fails;
  - `pids_of(exes: set) -> dict[int, int] | None` — `{pid: parent pid}` of the processes running one of `exes`; `None` when `exes` is empty or there is no snapshot;
  - `dcom_launcher_pid() -> int | None` — the process id of the `DcomLaunch` service, read with `QueryServiceStatusEx`; `None` off Windows, when the service cannot be queried, or when it reports none (PF-4);
  - `identified(before, after, launcher) -> tuple[int | None, str | None]` — `(pid, None)` for the one new process whose parent is `launcher`, else `(None, why)` (PF-4);
  - `class Job` — `Job.hold(pid: int) -> Job` (classmethod; `OSError` when Windows refuses a step, always off Windows), `wait(timeout_s: float) -> bool`, `disarm() -> None`, `close() -> None` (idempotent);
  - `class Owned(pid: int, job=None)` — attributes `pid`, `job`;
  - `own(started: bool, before, after, launcher, hold=_hold) -> Owned | None` — `None` is attached, with one stderr line when a start could not be identified;
  - `dialogs(exes: set) -> list[tuple[hwnd, pid]]` (was a list of windows);
  - `guarded(fn, what, deadline_s=CALL_DEADLINE_S, find_dialogs=None, close=None, owner=None)` — `owner` a pid, a callable returning one, or `None`; `find_dialogs()` returns `(hwnd, pid)` pairs;
  - `Application(binding, connect=None)` where `connect() -> (application, owned: Owned | None)` — no `started` any more: an instance the adapter does not own is attached (PF-4); new property `Application.pid`; attributes `opened` (the `FullName` of every document the driver opened), `mine` (the documents it opened and has not closed) and `unnamed_opens` (its opens whose document it never named); `Application.quit()` as D-35, PF-2 and PF-12.
- Produces (`drivers/vkforge/runtime/fake.py`): `fake.json` key `"attached": true` — the fake's dialog box then belongs to an instance the driver did not start; `FAKE_PROCESS`, the token the fake's box carries as its process.

- [ ] **Step 1: Write the failing tests**

`drivers/vkforge/tests/test_runtime.py` — imports (replace `:1-22`):

```python
import contextlib
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from vkdriver.protocol import DriverRefusal
from vkforge import canon, inventory, mine, plan
from vkforge.errors import Refusal
from vkforge.runtime import com, fake
from vkforge.runtime.fake import FakeApplication
from vkforge.tests import ooxml_fixtures
from vkforge.tests.ooxml_fixtures import FOOTER

FIXTURES = Path(__file__).resolve().parent / "fixtures"
DRIVERS = Path(__file__).resolve().parents[2]
```

The `connect=` stubs now return the instance and what the adapter owns of it (`connect() -> (application, owned)`; an instance it does not own is attached, PF-4): `:231` becomes `app = com.Application(self.binding, connect=lambda: (word, com.Owned(4242, StubJob())))` — a started instance, quit at its end; `:309` and `:332` become `app = com.Application(self.binding, connect=lambda: (word, None))` — their subject is the close, not the end. (`:286`, `:292` and `:296` go with the test rewritten below.)

`StubDocument.Close` (`:83-85`) also writes the close into its Word's `log` when that Word keeps one (an `OrderedWord`, below):

```python
    def Close(self, **kw):
        self.closed = kw
        self.word.Documents.items.remove(self)
        getattr(self.word, "log", []).append(f"Close {Path(self.FullName).name}")
```

The two real-Word tests (`test_the_adapter_applies_the_house_style_on_the_real_word`, `:189-190`, and `test_a_document_already_open_in_the_users_word_is_refused_and_left_open`, `:204-205`) run with the executable a host gives a driver: under PF-4 the Word they start is identified through `VK_DRIVER_APPLICATIONS`, and without it would be attached — never quit, a hidden `WINWORD.EXE` left on the node. Under each one's `@unittest.skipUnless(…)` add:

```python
    @mock.patch.dict(os.environ, {"VK_DRIVER_APPLICATIONS": "WINWORD.EXE"})
```

`test_the_users_instance_gets_its_display_alerts_back_and_only_a_started_one_is_quit` (`:282-303`) becomes the following; its third case, a started instance left running with the driver's own document in it, is D-35's now (`test_the_drivers_own_documents_are_closed_first_and_never_keep_the_instance`):

```python
    def test_the_users_instance_gets_its_display_alerts_back_and_only_a_started_one_is_quit(self):
        # L-P47: DisplayAlerts is read, silenced, and put back whenever the instance is left running;
        # L-P46 under D-04 and PF-4: only the instance the adapter started and identified is quit — the
        # user's is attached, never quit.
        user = StubWord()
        attached = com.Application(self.binding, connect=lambda: (user, None))
        self.assertEqual(user.DisplayAlerts, 0, "alerts_off while the driver works")
        attached.quit()
        attached.quit()
        self.assertEqual((user.DisplayAlerts, user.quits, attached.app), (-1, 0, None))
        ours = StubWord()
        started = com.Application(self.binding, connect=lambda: (ours, com.Owned(4242, StubJob())))
        started.quit()
        self.assertEqual((ours.quits, ours.DisplayAlerts, started.app), (1, 0, None), "quit: nothing to put back")
```

After `StubRegistry` (it ends at `:136`), add:

```python
class StubJob:
    """A Job as the adapter uses one (D-04), off Windows: what was asked of it, in order — into `log`
    when one is given, the list an OrderedWord writes to (ruling PF-2) — and how long it was waited for."""

    def __init__(self, exits: bool = True, log=None):
        self.calls, self.exits, self.waited = ([] if log is None else log), exits, None

    def wait(self, timeout_s: float) -> bool:
        self.calls.append("wait")
        self.waited = timeout_s
        return self.exits

    def disarm(self) -> None:
        self.calls.append("disarm")

    def close(self) -> None:
        self.calls.append("close")


class SilentWord(StubWord):
    """A Word whose DisplayAlerts cannot be read: a session that cannot begin (ruling LF-8)."""

    def __init__(self):
        self.Documents = StubDocuments(self)
        self.missing, self.quits = set(), 0

    @property
    def DisplayAlerts(self):
        raise OSError("the application is not answering")


class OrderedWord(StubWord):
    """A Word started by automation, as the end of a session finds it (founder decision D-35, rulings
    PF-2 and PF-12): hidden until the hand-over. It writes into `log`, in order, what that end asks of
    it: each document closed (StubDocument.Close), each property set, each read of Visible, each Quit.
    Set true, Visible reads back `shows`: True as comtypes gives Word's VARIANT_BOOL, -1 as it gives
    PowerPoint's msoTrue, False or 0 for a window that will not show. The property `refuses` names
    cannot be set; UserControl reads False and cannot be set at all, as the pinned type library has
    Word's Application.UserControl (a get accessor only)."""

    def __init__(self, log: list, shows=True):
        super().__init__()
        self.shown, self.shows, self.refuses, self.log = False, shows, None, log

    @property
    def Visible(self):
        self.log.append(f"Visible? {self.shown}")
        return self.shown

    @Visible.setter
    def Visible(self, value):
        self.shown = self.shows if value else False

    @property
    def UserControl(self):
        return False                                    # started by automation, not by the user

    def __setattr__(self, name, value):
        if name in ("Visible", "DisplayAlerts") and "log" in vars(self):
            if name == self.refuses:
                raise OSError(f"{name} cannot be set")
            super().__setattr__(name, value)
            self.log.append(f"{name} {value}")
        else:
            super().__setattr__(name, value)            # UserControl: AttributeError, it has no setter

    def Quit(self):
        self.log.append("Quit")
        super().Quit()


class Hung:
    """A document whose FullName does not come back until `until` is set: a listing that hangs."""

    def __init__(self, until: threading.Event):
        self.until = until

    @property
    def FullName(self):
        self.until.wait(30)
        return "C:/stub/hung.docx"


class Unreadable:
    """A document whose FullName fails: a listing that cannot name what is open."""

    @property
    def FullName(self):
        raise OSError("the object invoked has disconnected from its clients")


# A process that holds another in a Job of the adapter's (D-04), then sleeps: argv = drivers/, the
# pid to hold, and "arm" or "disarm" (disarm, then close the Job, before sleeping).
HOLDER = """import sys, time
sys.path.insert(0, sys.argv[1])
from vkforge.runtime import com
job = com.Job.hold(int(sys.argv[2]))
if sys.argv[3] == "disarm":
    job.disarm()
    job.close()
print("held", flush=True)
time.sleep(120)
"""
```

Add to `RuntimeTest`:

```python
    def test_only_a_positively_identified_process_is_owned(self):
        # D-03, D-04, rulings LF-5 and PF-4: the adapter owns, and holds in its Job, only the process its
        # start added when it is the one new process of the executable and its parent is the DcomLaunch
        # service's process; anything else is attached — never held — and a start it cannot tell apart
        # is one stderr line.
        held, launcher = [], 900

        def hold(pid):
            held.append(pid)
            return f"job of {pid}"

        owned = com.own(True, {1: 4}, {1: 4, 7: launcher}, launcher, hold)
        self.assertEqual((owned.pid, owned.job, held), (7, "job of 7", [7]))
        for args, why in [
                # attached: GetActiveObject found it running
                ((False, {1: 4}, {1: 4, 9: launcher}, launcher), None),
                # CreateObject handed back a running PowerPoint
                ((True, {1: 4}, {1: 4}, launcher), "0 new processes of the application"),
                ((True, {1: 4}, {1: 4, 7: launcher, 8: launcher}, launcher), "2 new processes of the application"),
                # the user's PowerPoint, started from the shell at the same moment
                ((True, {1: 4}, {1: 4, 7: 33}, launcher), "process 7 was not started by the DCOM launcher"),
                ((True, None, {1: 4, 7: launcher}, launcher), "no snapshot of the application's processes"),
                ((True, {1: 4}, {1: 4, 7: launcher}, None), "the DcomLaunch service's process is unknown")]:
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertIsNone(com.own(*args, hold), args)
            self.assertEqual(err.getvalue(), "" if why is None else f"start: treated as attached: {why}\n", args)
        self.assertEqual(held, [7], "nothing else was held")
        self.assertEqual(com.identified({1: 4}, {1: 4, 5: launcher}, launcher), (5, None))
        self.assertIsNone(com.pids_of(set()), "no executable to look for: no snapshot, no owner")

    def test_only_the_started_instance_s_boxes_are_closed(self):
        # D-03, ruling LF-6: past the deadline a box of the owned process is closed and the action
        # refuses `dialog`; a box of another process is never touched — `dialog` (left open) when no
        # process is owned, `timeout` when the owned one shows none.
        hang = threading.Event()
        boxes = [(11, 100), (22, 200)]
        try:
            for owner, closed_want, reason, words in [
                    (100, [11], "dialog", "closed, never answered"),
                    (lambda: 100, [11], "dialog", "closed, never answered"),
                    (None, [], "dialog", "left open"),
                    (300, [], "timeout", "no answer within 0.2 s")]:
                closed = []
                with self.assertRaises(DriverRefusal) as cm:
                    com.guarded(lambda: hang.wait(30), "save", deadline_s=0.2, find_dialogs=lambda: boxes,
                                close=closed.append, owner=owner)
                self.assertEqual((cm.exception.reason, closed), (reason, closed_want), owner)
                self.assertIn(words, cm.exception.detail)
            with self.assertRaises(DriverRefusal) as cm:
                com.guarded(lambda: hang.wait(30), "save", deadline_s=0.2, find_dialogs=lambda: [],
                            close=closed.append, owner=None)
            self.assertEqual(cm.exception.reason, "timeout")
        finally:
            hang.set()

    def test_the_adapter_closes_the_boxes_of_the_instance_it_started_only(self):
        # D-03 through Application: the guard's owner is the session's started process.
        for connect, closed_want, words in [
                (lambda: (StubWord(), com.Owned(4242, StubJob())), [7], "closed, never answered"),
                (lambda: (StubWord(), None), [], "left open")]:
            app = com.Application(self.binding, connect=connect)
            app.deadline_s = app.cleanup_deadline_s = 0.3
            doc = app.open("C:/stub/c.docx")
            wedged, closed = threading.Event(), []
            doc.Save = lambda: wedged.wait(30)
            try:
                with mock.patch.object(com, "dialogs", lambda exes: [(7, 4242), (8, 999)]), \
                        mock.patch.object(com, "close_window", closed.append), \
                        self.assertRaises(DriverRefusal) as cm:
                    app.call("save", doc)
            finally:
                wedged.set()
            self.assertEqual((cm.exception.reason, closed), ("dialog", closed_want))
            self.assertIn(words, cm.exception.detail)

    def test_quit_waits_for_the_started_process_then_closes_its_job(self):
        # D-04, D-35: a clean end with nothing foreign quits the instance the adapter started, gives its
        # process what is left of the clean-end window, then closes the Job, which ends whatever is
        # left; an attached instance has no Job at all.
        late = "quit: process 4242 still running at the end of the clean-end window; its Job ends it\n"
        for exits, said in ((True, ""), (False, late)):
            word, job = StubWord(), StubJob(exits)
            app = com.Application(self.binding, connect=lambda: (word, com.Owned(4242, job)))
            self.assertEqual(app.pid, 4242)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                app.quit()
            self.assertEqual((word.quits, job.calls, err.getvalue()), (1, ["wait", "close"], said))
            self.assertTrue(0 < job.waited <= com.QUIT_WAIT_S, job.waited)
            self.assertIsNone(app.pid, "the session let go of what it held")
        self.assertEqual(com.QUIT_WAIT_S, 3.0, "under the 5 s a host (L-P49) and qualify give a driver")
        user = StubWord()
        attached = com.Application(self.binding, connect=lambda: (user, None))
        attached.quit()
        self.assertEqual((user.quits, user.DisplayAlerts, attached.pid), (0, -1, None))

    def test_the_instance_survives_only_a_clean_end_inside_the_window(self):
        # Founder decision D-35, bound (1): what may keep the started instance runs inside the clean-end
        # window, QUIT_WAIT_S, under the host's 5 s grace (L-P49) — never on a call's 30 s. Past it (here
        # a listing that hangs) there is no disarm, and the Job, closed, takes the instance. An unclean
        # death never reaches this code: the Job takes the instance by itself
        # (test_a_held_process_dies_with_its_holder_and_a_disarmed_one_lives).
        word, job, hang = StubWord(), StubJob(), threading.Event()
        app = com.Application(self.binding, connect=lambda: (word, com.Owned(4242, job)))
        word.Documents.items.append(Hung(hang))
        err, began = io.StringIO(), time.monotonic()
        try:
            with mock.patch.object(com, "QUIT_WAIT_S", 0.5), contextlib.redirect_stderr(err):
                app.quit()
        finally:
            hang.set()
        self.assertLess(time.monotonic() - began, 2.0, "the window bounds the end, not a call's deadline")
        self.assertEqual((job.calls, word.quits), (["close"], 0), "no disarm, no Quit: the Job takes it")
        self.assertIn("quit: process 4242 not handed over (inventory: no answer within", err.getvalue())

    def test_the_drivers_own_documents_are_closed_first_and_never_keep_the_instance(self):
        # Founder decision D-35, bound (2): the driver first closes, without saving, every document it
        # opened and still holds (its close after the action failed); holding nothing else, the instance
        # is quit. A document of its own that will not close does not keep the instance: no Quit, no
        # disarm, and the Job, closed, takes it.
        word, job = StubWord(), StubJob()
        app = com.Application(self.binding, connect=lambda: (word, com.Owned(4242, job)))
        left = app.open("C:/stub/left.docx")
        app.quit()
        self.assertEqual((left.closed, len(word.Documents.items), word.quits), ({"SaveChanges": False}, 0, 1))
        self.assertEqual(job.calls, ["wait", "close"])
        word, job = StubWord(), StubJob()
        app = com.Application(self.binding, connect=lambda: (word, com.Owned(4242, job)))
        stuck = app.open("C:/stub/stuck.docx")
        stuck.Close = lambda **kw: (_ for _ in ()).throw(OSError("the document is in use"))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            app.quit()
        self.assertEqual((word.quits, job.calls), (0, ["close"]), "no Quit, no disarm: the Job takes it")
        self.assertEqual(err.getvalue(),
                         "quit: process 4242 not handed over (the document is in use); its Job ends it\n")

    def test_a_document_is_foreign_only_when_the_driver_never_opened_its_name(self):
        # Founder decision D-35, bound (3): a document is foreign only when its FullName is absent from
        # the exact list of those the driver opened — one it opened and closed, open again in its
        # instance, is not. A listing that fails, or an open whose document the driver never named, is
        # a doubt: no disarm, and the Job, closed, takes the instance.
        word, job = StubWord(), StubJob()
        app = com.Application(self.binding, connect=lambda: (word, com.Owned(4242, job)))
        app.close(app.open("C:/stub/a.docx"))
        word.Documents.items.append(StubDocument(word, "C:/stub/a.docx"))       # its name is the driver's
        app.quit()
        self.assertEqual((word.quits, job.calls), (1, ["wait", "close"]), "not foreign: quit, never kept")
        word, job = StubWord(), StubJob()
        app = com.Application(self.binding, connect=lambda: (word, com.Owned(4242, job)))
        word.Documents.items += [StubDocument(word, "C:/stub/the-users.docx"), Unreadable()]
        with contextlib.redirect_stderr(io.StringIO()):
            app.quit()
        self.assertEqual((word.quits, job.calls), (0, ["close"]), "a listing that fails: the Job takes it")
        word, job, wedged = StubWord(), StubJob(), threading.Event()
        app = com.Application(self.binding, connect=lambda: (word, com.Owned(4242, job)))
        app.deadline_s = 0.2
        word.Documents.Open = lambda path, **kw: wedged.wait(30)               # an Open refused by its guard
        try:
            with self.assertRaises(DriverRefusal):
                app.open("C:/stub/slow.docx")
        finally:
            wedged.set()
        word.Documents.items.append(StubDocument(word, "C:/stub/the-users.docx"))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            app.quit()
        self.assertEqual((word.quits, job.calls), (0, ["close"]), "an open never named is a doubt")
        self.assertIn("(1 open(s) whose document the driver never named)", err.getvalue())

    def test_the_instance_kept_is_handed_to_the_user_and_only_its_job_is_disarmed(self):
        # Founder decision D-35, bound (4), as ruling PF-12 reads it: with a foreign document identified,
        # the instance is handed to the user, visible and under the user's control — Visible set true
        # and read back true (True as comtypes gives Word's VARIANT_BOOL, -1 as it gives PowerPoint's
        # msoTrue), its own Job alone disarmed, DisplayAlerts put back, every reference released — and
        # stays, the user's document in it, the driver's closed. UserControl is never touched: Word's
        # has no setter, and neither has OrderedWord's. A Visible that cannot be set, or that reads back
        # false: no disarm, nothing put back, and the Job, closed, takes the instance. Another started
        # instance, holding nothing foreign, is quit and its Job closed armed.
        self.assertEqual(com.COM_TRUE, -1, "VARIANT_TRUE, and PowerPoint's msoTrue")
        for refuses, shows, why in [(None, True, None),                     # Word's Visible, read back
                                    (None, com.COM_TRUE, None),             # PowerPoint's, read back
                                    ("Visible", True, "Visible cannot be set"),
                                    (None, False, "hand-over: Visible reads back False"),
                                    (None, 0, "hand-over: Visible reads back 0")]:      # msoFalse
            word, job = OrderedWord([], shows), StubJob()
            app = com.Application(self.binding, connect=lambda: (word, com.Owned(4242, job)))
            own = app.open("C:/stub/own.docx")
            word.Documents.items.append(StubDocument(word, "C:/stub/the-users.docx"))
            word.refuses = refuses
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                app.quit()
            self.assertEqual((own.closed, word.quits, app.app), ({"SaveChanges": False}, 0, None), shows)
            if why is None:
                self.assertEqual((word.shown, word.DisplayAlerts, job.calls), (shows, -1, ["disarm", "close"]))
                self.assertEqual([d.FullName for d in word.Documents.items], ["C:/stub/the-users.docx"])
            else:
                self.assertEqual((word.DisplayAlerts, job.calls), (0, ["close"]), "no disarm, nothing put back")
                self.assertEqual(err.getvalue(), f"quit: process 4242 not handed over ({why}); its Job ends it\n")
        other, armed = StubWord(), StubJob()
        idle = com.Application(self.binding, connect=lambda: (other, com.Owned(4343, armed)))
        idle.quit()
        self.assertEqual((other.quits, armed.calls), (1, ["wait", "close"]), "nothing foreign: quit, Job armed")

    def test_every_firing_is_one_stderr_line_with_the_pid_and_the_count(self):
        # Founder decision D-35, bound (5): an instance kept is recorded in one stderr line (driver.log),
        # its process id and the count of foreign documents — never a name, never a content.
        busy = StubWord()
        holding = com.Application(self.binding, connect=lambda: (busy, com.Owned(4242, StubJob())))
        holding.open("C:/stub/own.docx")
        busy.Documents.items += [StubDocument(busy, "C:/stub/Contrat Dupont.docx"),
                                 StubDocument(busy, "C:/stub/Bilan 2026.docx")]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            holding.quit()
        self.assertEqual(err.getvalue(),
                         "quit: process 4242 handed to the user, 2 foreign document(s) open in it; its Job disarmed\n")

    def test_the_end_closes_then_lists_then_hands_over_then_disarms(self):
        # Rulings PF-2 and PF-12: inside QUIT_WAIT_S and before any other call — the driver's own
        # documents closed, the instance's listed, Visible set true and read back, then the disarm;
        # DisplayAlerts and the release come after it, best effort. A failure before the disarm (here
        # Visible refused, or read back false): no disarm, nothing put back, and the Job, closed, takes
        # the instance.
        for refuses, shows, want in [
                (None, True, ["Close own.docx", f"Visible {com.COM_TRUE}", "Visible? True", "disarm",
                              "DisplayAlerts -1", "close"]),
                ("Visible", True, ["Close own.docx", "close"]),
                (None, False, ["Close own.docx", f"Visible {com.COM_TRUE}", "Visible? False", "close"])]:
            log = []
            word, job = OrderedWord(log, shows), StubJob(log=log)
            app = com.Application(self.binding, connect=lambda: (word, com.Owned(4242, job)))
            app.open("C:/stub/own.docx")                              # its close after the action failed
            word.Documents.items.append(StubDocument(word, "C:/stub/the-users.docx"))
            word.refuses = refuses
            log.clear()                                               # the session's start is not the subject
            with contextlib.redirect_stderr(io.StringIO()):
                app.quit()
            self.assertEqual(log, want, (refuses, shows))

    def test_a_session_that_cannot_begin_ends_the_instance_it_started(self):
        # Ruling LF-8: DisplayAlerts cannot be read — the session refuses `precondition`, and the Job of
        # the instance it started is closed at once (an armed Job ends that instance).
        job = StubJob()
        with self.assertRaises(DriverRefusal) as cm:
            com.Application(self.binding, connect=lambda: (SilentWord(), com.Owned(4242, job)))
        self.assertEqual(cm.exception.reason, "precondition")
        self.assertIn("cannot be started: the application is not answering", cm.exception.detail)
        self.assertEqual(job.calls, ["close"])

    @unittest.skipIf(sys.platform == "win32", "off Windows only")
    def test_off_windows_there_is_no_snapshot_and_no_job(self):
        self.assertIsNone(com.processes())
        self.assertIsNone(com.pids_of({"WINWORD.EXE"}))
        self.assertIsNone(com.dcom_launcher_pid())
        with self.assertRaises(OSError):
            com.Job.hold(os.getpid())

    @unittest.skipUnless(sys.platform == "win32", "a Toolhelp32 snapshot is Windows'")
    def test_the_snapshot_sees_this_process_by_its_executable(self):
        me = next(p for p in com.processes() if p[0] == os.getpid())
        self.assertTrue(me[2].endswith(".EXE"), me)
        self.assertEqual(com.pids_of({me[2]})[os.getpid()], me[1], "each process with its parent")
        launcher = com.dcom_launcher_pid()
        self.assertIn((launcher, "SVCHOST.EXE"), {(pid, exe) for pid, _, exe in com.processes()},
                      "the DcomLaunch service runs in a svchost: the parent of what COM starts (PF-4)")

    @unittest.skipUnless(sys.platform == "win32", "Job objects are Windows'")
    def test_a_held_process_dies_with_its_holder_and_a_disarmed_one_lives(self):
        # D-04 without Office: a holder puts a sleeper in a kill-on-close Job of its own and is killed
        # with TerminateProcess (os.kill on Windows): no atexit, no cleanup. The sleeper dies with it
        # (D-35 (1): an unclean death leaves the Job to take the instance); a sleeper whose own Job was
        # disarmed first (D-35 (4)) lives on, and so does one never held.
        base = getattr(sys, "_base_executable", sys.executable)     # the interpreter, not a venv launcher
        sleep = [base, "-I", "-c", "import time; time.sleep(120)"]
        held, disarmed, free = (subprocess.Popen(sleep) for _ in range(3))
        holders = [subprocess.Popen([base, "-I", "-c", HOLDER, str(DRIVERS), str(p.pid), how],
                                    stdout=subprocess.PIPE, text=True)
                   for p, how in ((held, "arm"), (disarmed, "disarm"))]
        try:
            for h in holders:
                self.assertEqual(h.stdout.readline().strip(), "held")
                os.kill(h.pid, signal.SIGTERM)                      # TerminateProcess
                h.wait(timeout=10)
            held.wait(timeout=10)                                    # TimeoutExpired: it outlived its holder
            time.sleep(1)
            self.assertIsNone(disarmed.poll(), "a disarmed Job lets its process outlive the handle")
            self.assertIsNone(free.poll(), "a process never held is never touched")
        finally:
            for p in (held, disarmed, free, *holders):
                p.kill()
                p.wait(timeout=10)
            for h in holders:
                h.stdout.close()
```

`drivers/vkforge/tests/test_generate.py` — add `import signal` to the standard-library imports (after `import shutil`, `:6`) and `from vkforge.runtime import com` after `from vkforge.errors import Refusal` (`:20`). After `winword_pids` (`:81-84`) add:

```python
def driver_pid(proc: subprocess.Popen) -> int:
    """The process that runs driver.py: `proc` itself, or — `sys.executable` being a virtualenv's
    launcher on Windows — the interpreter the launcher started, its one python child."""
    children = [pid for pid, parent, exe in com.processes() or [] if parent == proc.pid and exe == "PYTHON.EXE"]
    return children[0] if children else proc.pid


def running(exes: set) -> set:
    """The ids of the processes running one of `exes` now; empty when there is no snapshot."""
    return set(com.pids_of(exes) or ())


# The PowerPoint of the node steps (ruling PF-5): the ontology's applications block, no verb.
PPT = {"application": json.loads((DRIVERS / "vkforge" / "ontology" / "office-document.json")
                                 .read_text(encoding="utf-8"))["applications"]["powerpoint"],
       "verbs": []}
# A driver's session in a process of its own (PF-5 (b)): argv = drivers/ and the application block. It
# prints the process the adapter identified as its own ("None" when it could not), then sleeps until killed.
STARTER = """import json, sys, time
sys.path.insert(0, sys.argv[1])
from vkforge.runtime import com
app = com.Application({"application": json.loads(sys.argv[2]), "verbs": []})
print(app.pid, flush=True)
time.sleep(120)
"""
```

Replace `test_a_surviving_dialog_is_a_dialog_refusal` (`:218-239`) with:

```python
    def test_a_surviving_dialog_is_a_dialog_refusal(self):
        # Spec §6, L-P33, D-03: a modal box raised mid-action in the instance the driver started is
        # closed, never answered, and the action refuses `dialog`; in an instance it attached to
        # (fake.json's "attached") the box is left open and the action refuses `dialog` all the same.
        doc = ooxml_fixtures.docx(Path(tempfile.mkdtemp()) / "c.docx", title_style="Normal", body_font=("Calibri", 12))
        before = doc.read_bytes()
        for config, words in (({"dialog_on": "save"}, "never answered"),
                              ({"dialog_on": "save", "attached": True}, "left open")):
            app = FakeApplication(self.binding, config)
            opened = app.open(str(doc))
            try:
                with self.assertRaises(DriverRefusal) as cm:
                    app.call("save", opened)
            finally:
                app.close(opened)
            self.assertEqual(cm.exception.reason, "dialog")
            self.assertIn(words, cm.exception.detail)
            s = Session(self.gen(), fake=True, fake_extra=config)
            started = time.monotonic()
            ans = s.act("appliquer_style_maison", {"document": str(doc), "pdf": str(doc.with_suffix(".pdf"))})
            s.close()
            self.assertLess(time.monotonic() - started, 30, "the guard's deadline, not the host's")
            self.assertEqual((ans["ok"], ans["reason"]), (False, "dialog"), ans)
            self.assertEqual(doc.read_bytes(), before, "nothing saved behind the box")
            self.assertFalse(doc.with_suffix(".pdf").exists())
```

After `test_no_word_the_driver_started_outlives_its_run` (`:312-325`) add:

```python
    @unittest.skipUnless(sys.platform == "win32" and inventory.office_present("word"), "Word not installed")
    def test_a_killed_driver_takes_the_word_it_started_and_no_other(self):
        # D-03/D-04 on the real Word: the driver starts Word (no Word of the user's to attach to),
        # identifies it (PF-4), holds it in its Job, and is killed with TerminateProcess — no `_quit`,
        # no atexit. The Word it started ends within 10 s; every WINWORD that ran before still runs.
        import comtypes
        import comtypes.client
        try:
            comtypes.client.GetActiveObject("Word.Application")
        except (OSError, comtypes.COMError):
            pass
        else:
            self.skipTest("a running Word would be attached to, not started: close Word to run this test")
        exes = {"WINWORD.EXE"}
        before = running(exes)
        doc = ooxml_fixtures.docx(Path(tempfile.mkdtemp()) / "c.docx", title_style="Normal", body_font=("Calibri", 12))
        s = Session(self.gen(), fake=False, extra_env={"VK_DRIVER_APPLICATIONS": "WINWORD.EXE"})
        try:
            s.act("appliquer_style_maison", {"document": str(doc), "pdf": str(doc.with_suffix(".pdf"))})
            started = running(exes) - before
            self.assertEqual(len(started), 1, f"the driver started one Word: {started}")
            (word,) = started
            os.kill(driver_pid(s.p), signal.SIGTERM)                  # TerminateProcess: no _quit, no atexit
            deadline = time.monotonic() + 10
            while word in running(exes) and time.monotonic() < deadline:
                time.sleep(0.2)
            self.assertNotIn(word, running(exes), "the Word the driver started outlived it")
            self.assertLessEqual(before, running(exes), "a Word that ran before the test was touched")
        finally:
            s.p.kill()
            s.p.wait(timeout=10)
            s.p.stdout.close()
```

After it, the two PowerPoint tests of the node steps (ruling PF-5; Step 9 runs them). Each skips, saying so, when a `POWERPNT.EXE` runs before it starts: that PowerPoint is the founder's, never touched.

```python
    @unittest.skipUnless(sys.platform == "win32" and inventory.office_present("powerpoint"), "PowerPoint not installed")
    def test_the_users_powerpoint_is_attached_never_quit_never_held_its_box_left_open(self):
        # D-03, D-04, ruling PF-4, node step PF-5 (a): a PowerPoint the user started — here the test, from
        # the shell, on a fictitious deck — is attached: no process of the driver's, no Job, never quit;
        # a dialog box it raises during a call (its Open dialog, a #32770 window) is left open and the
        # call refused `dialog`.
        import comtypes
        import comtypes.client
        exes = {"POWERPNT.EXE"}
        if running(exes):
            self.skipTest("a PowerPoint is running: never touched; close it to run this node step")
        deck = ooxml_fixtures.pptx(Path(tempfile.mkdtemp()) / "fictif.pptx")
        os.startfile(deck)                                          # the user's PowerPoint: the shell starts it
        try:
            deadline = time.monotonic() + 60
            while True:
                try:
                    comtypes.client.GetActiveObject("PowerPoint.Application")
                    break
                except (OSError, comtypes.COMError):
                    self.assertLess(time.monotonic(), deadline, "the PowerPoint the shell started never registered")
                    time.sleep(0.5)
            users = running(exes)
            with mock.patch.dict(os.environ, {"VK_DRIVER_APPLICATIONS": "POWERPNT.EXE"}):
                app = com.Application(PPT)
                try:
                    self.assertIsNone(app.pid, "attached: no process of the driver's, no Job")
                    with self.assertRaises(DriverRefusal) as cm:    # msoFileDialogOpen: the common Open box
                        com.guarded(lambda: app.app.FileDialog(1).Show(), "file dialog", 5, owner=app.pid)
                    self.assertEqual(cm.exception.reason, "dialog")
                    self.assertIn("left open", cm.exception.detail)
                    boxes = com.dialogs(exes)
                    self.assertTrue(boxes, "the box is still open")
                    for hwnd, _ in boxes:                           # the user closes it
                        com.close_window(hwnd)
                finally:
                    app.quit()
            time.sleep(2)
            self.assertLessEqual(users, running(exes), "the user's PowerPoint was quit or killed")
        finally:
            try:
                comtypes.client.GetActiveObject("PowerPoint.Application").Quit()
            except (OSError, comtypes.COMError):
                pass
            deadline = time.monotonic() + 10
            while running(exes) and time.monotonic() < deadline:
                time.sleep(0.5)
            for pid in running(exes):                               # this test's own: none ran before it
                os.kill(pid, signal.SIGTERM)

    @unittest.skipUnless(sys.platform == "win32" and inventory.office_present("powerpoint"), "PowerPoint not installed")
    def test_a_killed_driver_takes_the_powerpoint_it_started(self):
        # D-04, ruling PF-4, node step PF-5 (b): no PowerPoint running, the adapter starts one, identifies
        # it — one new POWERPNT.EXE, its parent the DcomLaunch service — and holds it in its Job; the
        # process that holds the Job is killed with TerminateProcess (no quit, no atexit), and the
        # POWERPNT.EXE ends within 10 s.
        exes = {"POWERPNT.EXE"}
        if running(exes):
            self.skipTest("a PowerPoint is running: never touched; close it to run this node step")
        proc = subprocess.Popen([sys.executable, "-I", "-c", STARTER, str(DRIVERS), json.dumps(PPT["application"])],
                                stdout=subprocess.PIPE, text=True,
                                env={**os.environ, "VK_DRIVER_APPLICATIONS": "POWERPNT.EXE"})
        try:
            said = proc.stdout.readline().strip()
            self.assertTrue(said.isdigit(), f"the adapter did not identify the PowerPoint it started: {said!r}")
            started = int(said)
            self.assertIn(started, running(exes))
            os.kill(driver_pid(proc), signal.SIGTERM)               # TerminateProcess: no quit, no atexit
            deadline = time.monotonic() + 10
            while started in running(exes) and time.monotonic() < deadline:
                time.sleep(0.2)
            self.assertNotIn(started, running(exes), "the PowerPoint the driver started outlived it")
        finally:
            proc.kill()
            proc.wait(timeout=10)
            proc.stdout.close()
            for pid in running(exes):                               # this test's own: none ran before it
                os.kill(pid, signal.SIGTERM)
```

- [ ] **Step 2: Run them, see them fail**

```powershell
& $PY -I -m unittest discover -s drivers/vkforge/tests -t . -p test_runtime.py -v
& $PY -I -m unittest discover -s drivers/vkforge/tests -t . -p test_generate.py -k dialog -v
```

Expected: `AttributeError: module 'vkforge.runtime.com' has no attribute 'Owned'` (and `own`, `identified`, `COM_TRUE`, `QUIT_WAIT_S`, `processes`, `dcom_launcher_pid`) in every test that names them, `TypeError: guarded() got an unexpected keyword argument 'owner'`, and the attached fake closing its box (`"left open"` not in the detail). The real-Office tests skip off the node.

- [ ] **Step 3: Implement the snapshot, the Job and the ownership** (`drivers/vkforge/runtime/com.py`)

Replace the docstring (`:1-4`) with:

```python
"""The COM adapter of a learned driver (L-P1): the only module that touches COM. Vendored into every
generated driver as vendor/vkforge_runtime/com.py (L-P19): the stdlib, vkdriver.protocol and, lazily
and on Windows only, comtypes. It names no application (L-P31): what differs between Word and
PowerPoint at this level is binding["application"], the ontology's applications block for the app.
The instance it starts, it identifies by its process — the one new process of the application's
executable between a Toolhelp32 snapshot before the start and one after, its parent the DcomLaunch
service's process (D-03, PF-4) — and holds in a kill-on-close Job of its own (D-04): that instance's
dialog boxes are the only ones it closes, and it ends with the driver however the driver ends, save
the one strictly bounded exception of a clean end that hands it to the user (D-35). An instance it
attached to, or could not identify, it never quits, holds, or closes a box of."""
```

`import time` joins the imports (`:5-11`, after `import threading`). Replace the constants (`:15-19`) with:

```python
CALL_DEADLINE_S = 30.0   # one COM call (L-P33); a qualification pass has 90 s (qualify.PASS_DEADLINE_S)
CLEANUP_DEADLINE_S = 5.0  # the close that ends an action, and the end of the session: often after a timeout
QUIT_WAIT_S = 3.0        # the clean-end window (D-35 (1)), under the 5 s grace of a host (L-P49) and of qualify
COM_TRUE = -1            # VARIANT_TRUE, also PowerPoint's msoTrue: true for a bool and an MsoTriState alike (L-P31)
DIALOG_CLASS = "#32770"  # the Win32 dialog box class: a sign-in, an overwrite, a repair prompt
WM_CLOSE = 0x0010
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
PROCESS_SET_QUOTA = 0x0100
SYNCHRONIZE = 0x00100000
TH32CS_SNAPPROCESS = 0x00000002
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9           # JOBOBJECTINFOCLASS
WAIT_OBJECT_0 = 0x00000000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
SC_MANAGER_CONNECT = 0x0001
SERVICE_QUERY_STATUS = 0x0004
SC_STATUS_PROCESS_INFO = 0                          # SC_STATUS_TYPE
```

After `executables()` (`:22-25`) add:

```python
class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", ctypes.c_uint32), ("cntUsage", ctypes.c_uint32), ("th32ProcessID", ctypes.c_uint32),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", ctypes.c_uint32),
                ("cntThreads", ctypes.c_uint32), ("th32ParentProcessID", ctypes.c_uint32),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", ctypes.c_uint32),
                ("szExeFile", ctypes.c_wchar * 260)]


class _BASIC_LIMIT(ctypes.Structure):               # JOBOBJECT_BASIC_LIMIT_INFORMATION
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32)]


class _EXTENDED_LIMIT(ctypes.Structure):            # JOBOBJECT_EXTENDED_LIMIT_INFORMATION
    _fields_ = [("BasicLimitInformation", _BASIC_LIMIT), ("IoInfo", ctypes.c_uint64 * 6),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


class _SERVICE_STATUS_PROCESS(ctypes.Structure):    # SERVICE_STATUS_PROCESS: nine DWORDs
    _fields_ = [(name, ctypes.c_uint32) for name in (
        "dwServiceType", "dwCurrentState", "dwControlsAccepted", "dwWin32ExitCode", "dwServiceSpecificExitCode",
        "dwCheckPoint", "dwWaitHint", "dwProcessId", "dwServiceFlags")]


_K32 = None


def _kernel32():
    """kernel32 with the signatures the snapshot and the Job use, made once (Windows only)."""
    global _K32
    if _K32 is None:
        from ctypes import wintypes

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        k.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
        k.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W))
        k.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W))
        k.CreateJobObjectW.restype = wintypes.HANDLE
        k.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        k.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        k.OpenProcess.restype = wintypes.HANDLE
        k.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        k.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        k.WaitForSingleObject.restype = wintypes.DWORD
        k.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        k.CloseHandle.argtypes = (wintypes.HANDLE,)
        _K32 = k
    return _K32


def processes():
    """Every process of the machine as (id, parent id, executable name in upper case), from one
    Toolhelp32 snapshot (ctypes, no psutil); None off Windows or when the snapshot fails."""
    if sys.platform != "win32":
        return None
    k = _kernel32()
    snap = k.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE_VALUE:
        return None
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        found = []
        ok = k.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            found.append((entry.th32ProcessID, entry.th32ParentProcessID, entry.szExeFile.upper()))
            ok = k.Process32NextW(snap, ctypes.byref(entry))
        return found
    finally:
        k.CloseHandle(snap)


def pids_of(exes: set):
    """{process id: parent process id} of the processes running one of `exes`; None when there is no
    executable to look for or no snapshot — the process the adapter starts cannot then be identified
    (PF-4)."""
    listed = processes() if exes else None
    return None if listed is None else {pid: parent for pid, parent, exe in listed if exe in exes}


def dcom_launcher_pid():
    """The process id of the DcomLaunch service — the parent of every local server COM starts, hence of
    the instance a CreateObject starts (ruling PF-4) — read with QueryServiceStatusEx; None off
    Windows, when the service cannot be queried, or when it reports no process."""
    if sys.platform != "win32":
        return None
    from ctypes import wintypes

    a = ctypes.WinDLL("advapi32", use_last_error=True)
    a.OpenSCManagerW.restype = wintypes.HANDLE
    a.OpenSCManagerW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD)
    a.OpenServiceW.restype = wintypes.HANDLE
    a.OpenServiceW.argtypes = (wintypes.HANDLE, wintypes.LPCWSTR, wintypes.DWORD)
    a.QueryServiceStatusEx.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                       ctypes.POINTER(wintypes.DWORD))
    a.CloseServiceHandle.argtypes = (wintypes.HANDLE,)
    manager = a.OpenSCManagerW(None, None, SC_MANAGER_CONNECT)
    if not manager:
        return None
    try:
        service = a.OpenServiceW(manager, "DcomLaunch", SERVICE_QUERY_STATUS)
        if not service:
            return None
        try:
            status, needed = _SERVICE_STATUS_PROCESS(), wintypes.DWORD()
            if not a.QueryServiceStatusEx(service, SC_STATUS_PROCESS_INFO, ctypes.byref(status),
                                          ctypes.sizeof(status), ctypes.byref(needed)):
                return None
            return status.dwProcessId or None
        finally:
            a.CloseServiceHandle(service)
    finally:
        a.CloseServiceHandle(manager)


def identified(before, after, launcher) -> tuple:
    """The process a start added, positively identified (D-03, ruling PF-4): (pid, None) when exactly
    one process of the executable is in `after` and not in `before` (snapshots {pid: parent}) and its
    parent is `launcher`, the DcomLaunch service's process; (None, why) for anything else — no
    snapshot, no launcher, none or several new (another start at the same moment), a parent that is
    not the launcher (a start from the shell: the user's)."""
    if before is None or after is None:
        return None, "no snapshot of the application's processes"
    if launcher is None:
        return None, "the DcomLaunch service's process is unknown"
    new = sorted(set(after) - set(before))
    if len(new) != 1:
        return None, f"{len(new)} new processes of the application"
    if after[new[0]] != launcher:
        return None, f"process {new[0]} was not started by the DCOM launcher"
    return new[0], None


def _limit(job, flags: int) -> None:
    info = _EXTENDED_LIMIT()
    info.BasicLimitInformation.LimitFlags = flags
    if not _kernel32().SetInformationJobObject(job, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                                               ctypes.byref(info), ctypes.sizeof(info)):
        raise ctypes.WinError(ctypes.get_last_error())


class Job:
    """A Job object of the adapter's own holding the Office process it started and identified (founder
    decisions D-04 and D-35): created with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, its handle not
    inheritable (no security attributes) and held by this driver process alone. However this process
    ends — a qualification pass killed at its deadline, the host's kill (a STOP, an act's deadline, an
    umount during an act, the end of its 5 s grace), a crash — the OS closes the handle and the process
    in the Job ends with it; a clean end closes it too, after Quit, and what is left of the process
    ends the same way. The one exception is D-35's: at a clean end inside the clean-end window, the
    driver's own documents closed and a foreign one positively identified, the instance is handed to
    the user and `disarm` clears the limit of this instance's Job alone — its process then outlives the
    handles. Any failure before that disarm leaves the Job armed. Never made for an instance the adapter
    attached to or could not identify (PF-4)."""

    def __init__(self, job, process, pid: int):
        self.job, self.process, self.pid = job, process, pid

    @classmethod
    def hold(cls, pid: int) -> "Job":
        """Process `pid` assigned to a new kill-on-close Job; OSError when Windows refuses a step."""
        if sys.platform != "win32":
            raise OSError("Job objects are Windows'")
        k = _kernel32()
        job = k.CreateJobObjectW(None, None)
        if not job:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            _limit(job, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
            process = k.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE | SYNCHRONIZE, False, pid)
            if not process:
                raise ctypes.WinError(ctypes.get_last_error())
            if not k.AssignProcessToJobObject(job, process):
                error = ctypes.WinError(ctypes.get_last_error())
                k.CloseHandle(process)
                raise error
        except BaseException:
            k.CloseHandle(job)
            raise
        return cls(job, process, pid)

    def wait(self, timeout_s: float) -> bool:
        """Whether the held process ended within `timeout_s`."""
        return _kernel32().WaitForSingleObject(self.process, int(timeout_s * 1000)) == WAIT_OBJECT_0

    def disarm(self) -> None:
        """The kill-on-close limit cleared, on this Job alone: its process outlives the handles (D-35 (4))."""
        _limit(self.job, 0)

    def close(self) -> None:
        """Both handles closed, once; an armed Job then ends what is left in it."""
        k = _kernel32()
        for name in ("process", "job"):
            handle = getattr(self, name)
            if handle:
                setattr(self, name, None)
                k.CloseHandle(handle)


class Owned:
    """The instance this adapter started and identified (D-03, PF-4): its process id, and the Job that
    ends it with the driver (D-04) — None when Windows refused the assignment (logged, tolerated)."""

    def __init__(self, pid: int, job=None):
        self.pid, self.job = pid, job


_HELD = []    # the Jobs of identified instances whose start was refused (LF-8, PF-4): they end with this process


def _hold(pid: int):
    """The Job that ends `pid` with this driver (D-04), or None: the failure goes to stderr (the
    driver's driver.log) and is tolerated — the instance then outlives a killed driver, as before."""
    try:
        return Job.hold(pid)
    except OSError as e:
        sys.stderr.write(f"job: process {pid} not held: {e}\n")
        return None


def own(started: bool, before, after, launcher, hold=_hold):
    """What the adapter owns of the instance it has (D-03; rulings LF-5 and PF-4): Owned(pid, hold(pid))
    only when it started the instance and identified its process positively (`identified`); None —
    attached — otherwise: an instance it found running (GetActiveObject), or one it cannot tell from the
    user's (CreateObject handed back a running PowerPoint, several new processes, a parent other than
    the DcomLaunch service, no snapshot, no launcher). An attached instance is never quit, never held in
    a Job, its dialog boxes never closed; a start that cannot be told apart is one stderr line."""
    if not started:
        return None
    pid, why = identified(before, after, launcher)
    if pid is None:
        sys.stderr.write(f"start: treated as attached: {why}\n")
        return None
    return Owned(pid, hold(pid))
```

In `dialogs` (`:28-70`): the docstring becomes `"""The visible top-level dialog boxes (class #32770) of the processes running one of `exes`, each as (window, process id): the guard closes only those of the instance the adapter started and identified (D-03, PF-4)."""` and `:66` becomes `found.append((hwnd, pid.value))`.

Replace `guarded` (`:82-116`) with:

```python
def guarded(fn, what: str, deadline_s: float = CALL_DEADLINE_S, find_dialogs=None, close=None, owner=None):
    """fn() on its own thread, joined for `deadline_s` (L-P33). Past it, the dialog boxes of the
    application's executables are listed with their process (D-03): those of `owner` — the process of
    the instance the adapter started, or a callable naming it at that moment — are closed, never
    answered, and the action refuses `dialog`. A box of any other process is never touched (LF-6):
    with no owner known (an attached instance: the user's, or one the adapter could not identify) a box of the
    application still means `dialog`, said to be left open; with an owner known, a box elsewhere did
    not hold this call, and the action refuses `timeout`, as it does with none open. A thread that
    finds comtypes loaded joins the multithreaded apartment that Application keeps alive, so the
    proxies one call's thread made serve the next."""
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
        pid = owner() if callable(owner) else owner
        seen = find_dialogs()
        ours = [hwnd for hwnd, process in seen if pid is not None and process == pid]
        for hwnd in ours:
            close(hwnd)
        if ours:
            raise DriverRefusal("dialog", f"{what}: a dialog box of the instance the driver started held the call; "
                                          "closed, never answered")
        if seen and pid is None:
            raise DriverRefusal("dialog", f"{what}: a dialog box of the application is open; left open: "
                                          "the instance is not one the driver owns")
        raise DriverRefusal("timeout", f"{what}: no answer within {deadline_s:g} s")
    if "error" in box:
        raise box["error"]
    return box.get("value")
```

- [ ] **Step 4: Implement the adapter that identifies and holds what it starts, and ends it within D-35's bounds** (replace `class Application`, `:184-305`, whole)

Bound (4) as ruling PF-12 reads it: the hand-over is `Visible` set true and read back true, never `UserControl` (the type libraries: Self-review notes, §5).

```python
class Application:
    """The application binding["application"] names: the user's own instance when one is running
    (spike verdict 3), else one the adapter starts. The adapter changes only its DisplayAlerts, read
    first and put back by quit() whenever the instance is left running (L-P47), and the Visible of the
    instance it hands to the user (D-35, PF-12). An instance it started and identified by its process
    (D-03, PF-4) is the only one whose dialog boxes it closes, and is held in a kill-on-close Job of
    the adapter's (D-04): quit() ends it — save D-35's bounded exception — and a driver that dies
    takes it along. Any other instance is attached: never quit, never held. `connect` returns
    (application, owned), owned an Owned or None; the default is the running instance, else a new
    one. What the driver opens is written down for the end (D-35): `opened`, the FullName of every
    document it opened; `mine`, those it has not closed; `unnamed_opens`, its opens whose document it
    never named."""

    def __init__(self, binding: dict, connect=None):
        self.binding, self.spec = binding, binding["application"]
        self.verbs = {v["verb"]: v for v in binding["verbs"]}
        self.deadline_s, self.cleanup_deadline_s = CALL_DEADLINE_S, CLEANUP_DEADLINE_S
        self.app, self.owned, self.alerts_before, self.failed = None, None, None, None
        self.opened, self.mine, self.unnamed_opens = set(), [], 0   # what the driver opened (D-35)
        progid = self.spec["progid"]
        try:
            self.app, self.owned = (connect or self._connect)()
            self.alerts_before = self._guard(lambda: self.app.DisplayAlerts, "DisplayAlerts")
            self._guard(lambda: setattr(self.app, "DisplayAlerts", self.spec["alerts_off"]), "DisplayAlerts")
        except BaseException as e:
            if self.owned is not None and self.owned.job is not None:
                self.owned.job.close()                  # LF-8: the instance started for a session that cannot begin
            if isinstance(e, _com_errors()):
                raise DriverRefusal("precondition", f"{progid} cannot be started: {e}") from e
            raise

    @property
    def pid(self):
        """The process of the instance this adapter started and identified (D-03, PF-4); None otherwise."""
        return None if self.owned is None else self.owned.pid

    def _guard(self, fn, what: str, deadline_s: float | None = None):
        """guarded() with this session's call deadline by default, and the started process as the one
        whose dialog boxes it may close (D-03)."""
        return guarded(fn, what, self.deadline_s if deadline_s is None else deadline_s, owner=self.pid)

    def _connect(self) -> tuple:
        progid = self.spec["progid"]
        if sys.platform != "win32":
            raise DriverRefusal("precondition", f"{progid} cannot be started: not Windows")
        cookie = ctypes.c_void_p()
        ctypes.oledll.ole32.CoIncrementMTAUsage(ctypes.byref(cookie))   # the calls' apartment lives with the driver
        import comtypes
        import comtypes.client

        exes, launcher = executables(), dcom_launcher_pid()
        before = pids_of(exes)                          # before GetActiveObject: an attach starts nothing

        def start():
            try:
                return comtypes.client.GetActiveObject(progid), False
            except (OSError, comtypes.COMError):
                return comtypes.client.CreateObject(progid), True

        try:
            # A box raised while the instance starts (a first-run prompt) is closed only when the new
            # process is positively identified by then (D-03, PF-4).
            app, started = guarded(start, f"start {progid}",
                                   owner=lambda: identified(before, pids_of(exes), launcher)[0])
        except DriverRefusal:
            held = own(True, before, pids_of(exes), launcher)   # LF-8: a positively owned process only (PF-4)
            if held is not None:
                _HELD.append(held.job)                  # it ends with this driver
            raise
        return app, own(started, before, pids_of(exes) if started else None, launcher)

    def _documents(self):
        return getattr(self.app, self.spec["collection"])

    def probe(self) -> None:
        probe(self.binding)

    def open(self, path: str):
        """The document, opened. A failure while opening (no such file, a damaged one, the application's
        refusal) is `precondition`, as the fake's; a failure inside a verb is `internal` in both. The
        plan's "not open elsewhere" holds in this very instance too: there Open hands back a document the
        user already has open, unsaved edits included; refused, and that document is not closed. What
        the driver opens is written down for the end of the session (D-35): its FullName, read back after
        the Open, in `opened`, and the document in `mine` until it is closed; an Open whose document is
        never named — refused by its guard, or its name unreadable — stays counted in `unnamed_opens`,
        a doubt that keeps the instance from being handed over."""
        self.failed = None                                  # a new action begins with its document
        try:
            open_before = self._guard(lambda: {d.FullName for d in _items(self._documents())}, "open")
            self.unnamed_opens += 1                         # D-35 (3): until its document is named
            try:
                doc = self._guard(lambda: self._documents().Open(path, **self.spec["open"]), "open")
            except _com_errors():
                self.unnamed_opens -= 1                     # the application refused: it opened nothing
                raise
            name = self._guard(lambda: doc.FullName, "open")
            self.unnamed_opens -= 1
            already = name in open_before
            if not already:
                self.opened.add(name)                       # D-35 (3): the driver's exact list
                self.mine.append(doc)                       # D-35 (2): closed at the end if still open
            read_only = not already and self._guard(lambda: bool(doc.ReadOnly), "open")
        except _com_errors() as e:
            raise DriverRefusal("precondition", f"document cannot be opened: {e}") from e
        if already:
            raise DriverRefusal("precondition", "document already open in the application (the user's window)")
        if read_only:
            self.failed = "open"
            self.close(doc)
            raise DriverRefusal("precondition", "document opened read-only (locked elsewhere)")
        return doc

    def call(self, verb: str, doc, **args):
        try:
            return self._guard(lambda: VERBS[verb](self, doc, **args), verb)
        except BaseException as e:
            self.failed = verb                              # the action refuses: its close is a cleanup
            if isinstance(e, _com_errors()):
                raise DriverRefusal("internal", f"{verb}: {e}") from e
            raise

    def close(self, doc) -> None:
        """Closed without saving. After a refusal in this action, the close is a cleanup against an
        application that may be wedged: the short cleanup deadline, its own refusal written to stderr,
        never raised, so the action's first refusal is the one the host gets. After a successful action,
        the normal deadline, and a failure is `internal` "close failed: …": a document the driver opened
        must not stay open, hidden, in the user's instance without the host hearing of it. One that did
        not close stays in `mine`, closed again at the end of the session (D-35 (2))."""
        if self.failed is not None:
            try:
                self._guard(lambda: doc.Close(**self.spec["close"]), "close", self.cleanup_deadline_s)
            except (DriverRefusal, *_com_errors()) as e:
                sys.stderr.write(f"close: {e}\n")
                return
        else:
            try:
                self._guard(lambda: doc.Close(**self.spec["close"]), "close")
            except (DriverRefusal, *_com_errors()) as e:
                raise DriverRefusal("internal", f"close failed: {e}") from e
        self._closed(doc)

    def _closed(self, doc) -> None:
        """`doc` is closed: no longer the driver's to close at the end (D-35 (2))."""
        self.mine = [d for d in self.mine if d is not doc]

    def quit(self) -> None:
        """The end of the session, once (L-P46, L-P47; founder decisions D-04 and D-35). An attached
        instance — the user's, or one whose process the adapter could not identify (PF-4) — is never
        quit and gets its DisplayAlerts back. The instance the adapter started and identified ends with
        the driver unless D-35's five bounds all hold (`_end_started`, inside the clean-end window that
        starts here); one handed to the user gets its DisplayAlerts back too, best effort, after the
        disarm (PF-2, PF-12). Every COM reference the adapter holds — the application, and any document
        of its own still listed — is then released, and the Job's handles closed: armed, the Job ends
        what is left of the instance — after a Quit, once its process has had what is left of the
        window; disarmed, the instance is the user's: visible, outside any Job, no automation reference
        left (PF-12). A failure is written to stderr, never raised: this runs when serve returns and at
        exit."""
        if self.app is None:
            return
        owned, end = self.owned, time.monotonic() + QUIT_WAIT_S
        ending = "attached" if owned is None else self._end_started(owned, end)
        try:
            if ending in ("attached", "kept") and self.alerts_before is not None:
                self._guard(lambda: setattr(self.app, "DisplayAlerts", self.alerts_before), "DisplayAlerts",
                            self.cleanup_deadline_s)
        except (DriverRefusal, *_com_errors()) as e:
            sys.stderr.write(f"quit: {e}\n")
        finally:
            held, self.app, self.owned, self.mine = [self.app, *self.mine], None, None, []
            try:      # every reference the adapter holds, dropped in the apartment it was made in (PF-12)
                self._guard(lambda: (held.clear(), gc.collect()), "release", self.cleanup_deadline_s)
            except DriverRefusal as e:
                sys.stderr.write(f"quit: {e}\n")
            if owned is not None and owned.job is not None:
                self._close_job(owned, end, ending)

    def _bounded(self, fn, what: str, end: float):
        """fn() under the guard, given what is left of the clean-end window that ends at `end` (D-35
        (1)): the window over is refused like any timeout."""
        left = end - time.monotonic()
        if left <= 0:
            raise DriverRefusal("timeout", f"{what}: the clean-end window is over")
        return self._guard(fn, what, left)

    def _end_started(self, owned, end: float) -> str:
        """The bounded end of the instance this adapter started and identified (founder decision D-35,
        rulings PF-2 and PF-12): inside the clean-end window, before any other call, in this order —
        (2) every document the driver opened and still holds is closed without saving (the binding's
        close: Word's SaveChanges=False; PowerPoint's Close saves nothing); (3) the instance's documents
        are listed by FullName, one foreign only when its FullName is not in `opened`; (4) with a
        foreign document, the instance is handed to the user — Visible set to COM_TRUE and read back
        true: True, as comtypes gives Word's VARIANT_BOOL, or COM_TRUE, PowerPoint's msoTrue; never
        UserControl, which Word's Application cannot set and PowerPoint's does not have — and then,
        only then, this instance's Job alone is disarmed; (5) one stderr line says so, the process id
        and the count, never a name. Returns "kept" (handed over), "quit" (nothing foreign: Quit sent)
        or "ended": a failure, a timeout or a doubt before the disarm — an open the driver never named,
        a document of its own that will not close, a listing that fails, a Visible that cannot be set
        or reads back false, the window over — means no disarm, and the Job, closed, takes the instance
        (D-04)."""
        try:
            if self.unnamed_opens:
                raise DriverRefusal("internal",
                                    f"{self.unnamed_opens} open(s) whose document the driver never named")
            for doc in list(self.mine):                                                  # (2)
                self._bounded(lambda d=doc: d.Close(**self.spec["close"]), "close", end)
                self._closed(doc)
            self._bounded(gc.collect, "quit", end)
            names = self._bounded(lambda: [d.FullName for d in _items(self._documents())], "inventory", end)
            foreign = sum(1 for name in names if name not in self.opened)                # (3)
            if not foreign:
                self._bounded(lambda: self.app.Quit(), "quit", end)
                return "quit"
            self._bounded(lambda: setattr(self.app, "Visible", COM_TRUE), "hand-over", end)      # (4)
            shown = self._bounded(lambda: self.app.Visible, "hand-over", end)
            if shown is not True and shown != COM_TRUE:     # Word's reads back True, PowerPoint's -1 (PF-12)
                raise DriverRefusal("internal", f"hand-over: Visible reads back {shown!r}")
            if owned.job is not None:
                owned.job.disarm()
        except Exception as e:  # noqa: BLE001 — any failure before the disarm: no disarm (PF-2)
            sys.stderr.write(f"quit: process {owned.pid} not handed over ({e}); its Job ends it\n")
            return "ended"
        sys.stderr.write(f"quit: process {owned.pid} handed to the user, {foreign} foreign document(s) "
                         "open in it; its Job disarmed\n")                                # (5)
        return "kept"

    def _close_job(self, owned, end: float, ending: str) -> None:
        """The started instance's Job, once every reference is released (D-04): after a Quit, its
        process waited for what is left of the clean-end window; then both handles closed — armed, the
        Job ends what is left of the process; disarmed (D-35), the process is the user's."""
        try:
            if ending == "quit" and not owned.job.wait(max(0.0, end - time.monotonic())):
                sys.stderr.write(f"quit: process {owned.pid} still running at the end of the clean-end "
                                 "window; its Job ends it\n")
        except OSError as e:
            sys.stderr.write(f"quit: {e}\n")
        finally:
            owned.job.close()
```

- [ ] **Step 5: The fake's dialog in a started or an attached instance** (`drivers/vkforge/runtime/fake.py`)

The docstring's sentence `Its dialog box (fake.json's "dialog_on") is closed by com.guarded, the guard the real driver runs (L-P33).` (`:5-6`) becomes `Its dialog box (fake.json's "dialog_on") goes through com.guarded, the guard the real driver runs (L-P33): closed in the instance the driver started, left open — and refused all the same — when fake.json says "attached" (D-03).`

After `READ_ONLY` (`:23`) add:

```python
FAKE_PROCESS = "the fake's instance"   # the process the fake's dialog box belongs to (D-03): there is no real one
```

`FakeApplication.__init__` (`:64-70`) gains, after `self.dialog_on = …`:

```python
        self.attached = bool((config or {}).get("attached"))   # fake.json's: the box is not the driver's instance's (D-03)
```

Replace `__dialog__` (`:98-105`) with:

```python
    def __dialog__(self, verb: str):
        """fake.json's `dialog_on` (L-P33): the call blocks behind a modal box, as Word's does behind a
        sign-in or an overwrite prompt. In the instance the driver started — the default — com.guarded
        closes it and the refusal is `dialog`; with fake.json's `"attached": true` the box belongs to an
        instance the driver did not start, so the guard leaves it open and refuses `dialog` all the
        same (D-03) — the code the real driver runs, either way."""
        closed = threading.Event()
        return com.guarded(lambda: closed.wait(60), verb, deadline_s=FAKE_CALL_DEADLINE_S,
                           find_dialogs=lambda: [] if closed.is_set() else [("the fake's dialog box", FAKE_PROCESS)],
                           close=lambda hwnd: closed.set(), owner=None if self.attached else FAKE_PROCESS)
```

- [ ] **Step 6: The two qualify docstrings** (`drivers/vkforge/qualify.py`)

`:20-21`, the sentence `A pass killed at its deadline never runs the driver's `_quit`: the Office instance it started stays running.` becomes `A pass killed at its deadline never runs the driver's `_quit`; the Office instance the driver started and identified ends with it all the same, through the kill-on-close Job the adapter holds it in (D-04), while an instance it attached to — or could not identify (PF-4) — is left as the kill found it.`

`:70-71`, the sentence `An Office instance a COM call started is not in the tree (DCOM launches it): never killed here.` becomes `An Office instance a COM call started is not in the tree (DCOM launches it): never killed here — once the adapter has identified it (PF-4), it ends through the adapter's own Job when the driver does (D-04).`

- [ ] **Step 7: Run the tests, see them pass**

```powershell
& $PY -I -m unittest discover -s drivers/vkforge/tests -t . -p test_runtime.py -v
& $PY -I -m unittest discover -s drivers/vkforge/tests -t . -p test_generate.py -v
& $PY -I -m unittest discover -s drivers/vkforge/tests -t .
```

Expected: all pass. On Windows `test_the_snapshot_sees_this_process_by_its_executable` and `test_a_held_process_dies_with_its_holder_and_a_disarmed_one_lives` run (CI's `driver-learn` job runs them too); elsewhere `test_off_windows_there_is_no_snapshot_and_no_job` runs. The real-Office tests skip off the node.

- [ ] **Step 8: On the node, the real Word (controller, when the founder's session allows; it starts and kills a Word of its own)**

Close every Word window first (a running Word would be attached to, and the test skips saying so). Then:

```powershell
& $PY -I -m unittest discover -s drivers/vkforge/tests -t . -p test_generate.py -k takes_the_word_it_started -v
& $PY -I -m unittest discover -s drivers/vkforge/tests -t . -p test_generate.py -k outlives_its_run -v
& $PY -I -m unittest discover -s drivers/vkforge/tests -t . -p test_runtime.py -k real_word -v
Get-Process WINWORD -ErrorAction SilentlyContinue
```

Expected: the three pass (`test_a_killed_driver_takes_the_word_it_started_and_no_other`, `test_no_word_the_driver_started_outlives_its_run`, `test_the_adapter_applies_the_house_style_on_the_real_word`) and `Get-Process` lists no `WINWORD`. The controller records the run in the chantier's ledger; a failure of the first (Word survives the kill) means Windows refused the Job assignment (`job: process … not held: …`) or the start was not identified (`start: treated as attached: …`): the driver's stderr says which, and it goes to the ledger as a finding.

- [ ] **Step 9: On the node, the real PowerPoint (controller, ruling PF-5; only when no PowerPoint runs)**

The founder's own PowerPoint is never touched. First:

```powershell
Get-Process POWERPNT -ErrorAction SilentlyContinue
```

If it lists a `POWERPNT`, both steps are skipped and the skip is reported in the chantier's ledger (the two tests skip too, saying so). Otherwise:

```powershell
& $PY -I -m unittest discover -s drivers/vkforge/tests -t . -p test_generate.py -k powerpoint -v
Get-Process POWERPNT -ErrorAction SilentlyContinue
```

Expected: both pass, and `Get-Process` lists no `POWERPNT`. (a) `test_the_users_powerpoint_is_attached_never_quit_never_held_its_box_left_open`: the PowerPoint the test started from the shell on a fictitious deck is attached — no process of the driver's, no Job — its Open dialog box is left open and the call refused `dialog`, and it still runs once the session has ended (the test then quits it). (b) `test_a_killed_driver_takes_the_powerpoint_it_started`: the adapter starts PowerPoint, identifies it (its parent the `DcomLaunch` service) and holds it in its Job; the process holding the Job is killed with TerminateProcess and the `POWERPNT.EXE` ends within 10 s. The controller records both in the chantier's ledger; (b) reading something other than a process id means the start was not identified (`start: treated as attached: …` on the starter's stderr) — a finding for the ledger, never a test to loosen.

- [ ] **Step 10: Docs**

`contracts/tcb.md:248` — the bullet becomes:

```markdown
- **A dialog box is closed, never answered (ruling L-P33) — and only in the Office instance the driver started (founder decision D-03).** A dialog raised during an action is dismissed by the adapter and the action refuses `dialog`; nothing is ever clicked through. The adapter identifies the instance it starts by its process — the one process of the application's executable that appeared when it started it, from a Toolhelp32 snapshot before and after, whose parent is the `DcomLaunch` service's process (ruling PF-4) — and `dialogs()` closes the `#32770` windows of that process only. Attached to the user's own instance, or when it cannot identify the process it started (none or several appeared, another parent, no snapshot), it treats the instance as attached and closes nothing: a dialog box of the application still makes the action refuse `dialog`, and the box is left open for the user.
```

`contracts/tcb.md:250` — in **Known gaps of the learning path**, the last sentence `A qualification pass killed on its timeout orphans the Office instance the driver started (its `_quit` never ran) and the operator closes it.` becomes:

```markdown
The Office instance the driver started and identified is held in a Job object the adapter creates with kill-on-close, its handle held by the driver process alone and never inherited (founder decision D-04): however the driver ends — a qualification pass killed at its deadline, the host's kill, a crash — that instance ends with it, and an instance the driver attached to is never in it. A clean end ends it too, save one strictly bounded exception (founder decision D-35): inside the clean-end window (3 s, under the host's 5 s grace) the driver closes without saving every document it opened, lists the instance's documents, and only when one is foreign — its `FullName` absent from the exact list of those the driver opened — hands the instance to the user (ruling PF-12): its `Visible` set true and read back true, then that instance's Job alone disarmed, its `DisplayAlerts` put back and every reference the driver holds released — visible, outside any Job, with no automation reference left, the instance is the user's — one line of `driver.log` giving the process id and the count, never a name. Any failure, timeout or doubt before the disarm (a `Visible` that cannot be set, or reads back false, among them) leaves the Job to take the instance, and an unclean death takes it with whatever the user opened in it. An instance whose process the adapter cannot identify (ruling PF-4) is treated as attached — never quit, never held, its boxes never closed — even when the driver started it, and outlives the driver, as does one whose assignment to the Job Windows refused (both said in `driver.log`). A driver learned before this chantier keeps the adapter it vendored, without a Job, until it is learned again.
```

`contracts/tcb.md:252` — in **Failure leaves the driver degraded, and the operator remounts (pilot ruling R12).**, the sentence `Since ruling L-P49 the host first closes the driver's stdin and gives it 5 s to exit on its own (a learned driver quits the Office instance it started), then kills the tree: for every driver — a browser driver mid-action under a human STOP or `vk driver umount` included — the kill comes up to 5 s later.` becomes (ruling PF-11; the code gives a STOP or an umount during an act no grace):

```markdown
Since ruling L-P49 a host dropped while its driver is idle — an idle `vk driver umount`, a host `ensure` replaces, the process of a refused mount — first closes the driver's stdin and gives it 5 s to exit on its own (a learned driver ends the Office instance it started on its way out, founder decision D-35), then kills the tree. A STOP, an act's deadline, a protocol fault or a `vk driver umount` during an act gets no grace: the host kills the tree at once (`DriverHost::kill`, `kill_pid_tree`), and a learned driver's Office ends through its Job (D-04).
```

`docs/technical/runbooks/learn-office-driver.md:106` — after `… (d'où la réserve 3 du §8).` add: `Le Word qu'il lance, il le reconnaît à son processus (un seul nouveau WINWORD.EXE, lancé par le service DcomLaunch) et le tient dans un Job « tuer à la fermeture » : ce Word ne survit pas au driver, sauf l'exception bornée de la réserve 2.`

`:116` — `Le driver ferme les boîtes de dialogue sans jamais répondre (le §8 en dit les limites).` becomes `Le driver ferme, sans jamais répondre, les boîtes de dialogue du Word qu'il a lancé et reconnu, et seulement celles-là (le §8 en dit les limites).`

`:189-190` — reserves 2 and 3 become:

```markdown
2. **Un rejeu tué à son délai n'abandonne plus le Word qu'il a lancé.** Le driver reconnaît le Word qu'il lance (un seul nouveau `WINWORD.EXE`, lancé par le service `DcomLaunch`) et le tient dans un Job « tuer à la fermeture » : tué, il l'emporte ; à une fin normale, il le ferme aussi. Si Windows a refusé de le mettre dans le Job (`driver.log` : `job: process … not held`), ou si le driver n'a pas pu le reconnaître (`start: treated as attached: …`), il le traite comme le vôtre et ce Word reste : on le ferme à la main, sans enregistrer. Un Word que vous aviez ouvert vous-même n'est jamais dans ce Job. Une seule exception, bornée (décision D-35) : à une fin normale, si le driver trouve dans son Word un document que vous y avez ouvert vous-même, il ferme d'abord les siens sans enregistrer, vous rend ce Word visible et le laisse ouvert ; `driver.log` le dit (`quit: process N handed to the user, K foreign document(s) open in it; its Job disarmed`), sans nom de document. Au moindre doute, ou si le driver meurt, ce Word part avec votre document. Un driver appris avant cette version garde l'ancien adaptateur, sans Job, jusqu'à ce qu'on le réapprenne (§3).
3. **Les boîtes de dialogue de votre propre Word ne sont jamais fermées.** Le driver ne ferme que celles du Word qu'il a lancé et reconnu. Attaché à votre Word, il refuse `dialog` et laisse la boîte ouverte : on y répond soi-même, puis on requalifie.
```

- [ ] **Step 11: The whole suite and the docs check**

```powershell
& $PY -I -m unittest discover -s drivers/vkforge/tests -t .
git diff --exit-code drivers/vkforge/tests/fixtures
python -I scripts/docs/check_docs.py
```

- [ ] **Step 12: Commit**

```bash
git add drivers/vkforge/runtime/com.py drivers/vkforge/runtime/fake.py drivers/vkforge/qualify.py drivers/vkforge/tests/test_runtime.py drivers/vkforge/tests/test_generate.py contracts/tcb.md docs/technical/runbooks/learn-office-driver.md
git commit -F - <<'MSG'
feat(vkforge): the Office a learned driver starts is identified by its process and its DCOM parent, the only one whose dialogs it closes, held in a kill-on-close Job, and kept at a clean end only within D-35's bounds (D-03, D-04, D-35)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UUDLpkwxsZXmLptqCeNL2R
MSG
```

---

### Task 4: D-31 (final re-review F-1) — the qualification gate before every spawn of a `kind: com` driver (Sonnet)

**Model:** Sonnet (one closure, one failure variant, one test; the kernel's `check_qualified` is unchanged). **Decision:** D-31. **Rulings:** LF-9, PF-6 (the ledger keeps the word `unqualified`), PF-7 (the gate holds per start of the process, not per act).

**Where the drivers come back after a restart (the finding LF-9 rests on).** `RealKernel::boot` restores nothing that runs: `degrade_invalid_drivers` (`crates/vk-kernel/src/drivers.rs:743-781`, called at `crates/vk-kernel/src/lib.rs:1141`) only degrades rows whose manifest no longer validates — "Nothing is spawned here — the daemon owns the processes". The daemon's `DriverPool` starts empty (`crates/vk-ipc/src/drivers.rs:123-127`: "A daemon that has just restarted holds none"). A `Ready` row is respawned lazily by the first `driver.act`: `act` → `run_act` (`:833-859`) → `DriverPool::act` (`:241-263`) → `ensure` (`:172-217`) → `spawn_host` (`:438-456`). `mount` also reaches the same `ensure` (`:723-726`) after its own gate (`:656-661`, `:674-678`). So the gate goes into `ensure`, right before `spawn_host`: one place for mount and act, run only when a process is about to start, with the fixtures flag the daemon's `serve_on` set (final review I-1) — which is why it is not run at boot, where the flag is not set yet (`crates/vk-ipc/src/server.rs:450-457`).

**How the test restarts the daemon.** As the pipe tests already do (`act_round_trip_through_the_daemon`, `crates/vk-ipc/tests/drivers.rs:577`; the tampered tree, `crates/vk-ipc/tests/capital.rs:793`): `pool.drop_host` leaves the pool as a restart leaves it, and `set_forge_fixtures(false)` is what `serve_on` sets on a daemon started without the seam (`crates/vk-ipc/src/server.rs:454-457`, the I-1 test does the same, `crates/vk-ipc/tests/forge.rs:279`). The kernel's tables live in the store and come back unchanged from a restart; nothing else the act path reads is in memory.

**Files:**
- Modify: `crates/vk-kernel/src/drivers.rs:137-170` (`DRIVER_FAILURE_REASONS` and its doc), `:366-422` (`DriverFailure`: new `Unqualified`, `reason`, `detail`, new `refusal`), `:1081` (`driver_act_settle`'s refusal), `:1321-1335` (the vocabulary test)
- Modify: `crates/vk-ipc/src/drivers.rs:22-34` (imports), `:156-217` (`ensure`: a `gate`), `:219-263` (`act`: a `gate`, the error mapping), `:525-541` (after `Tampered`: `Unqualified`, `qualification_gate`), `:723-726` (`mount`), `:822-859` (`run_act`)
- Modify: `crates/vk-ipc/tests/drivers.rs:1173`, `:1229-1237` (the two direct calls of `ensure` and `act`)
- Modify: `crates/vk-ipc/tests/common/mod.rs:256-299` (`token` → `token_for`)
- Test: `crates/vk-ipc/tests/forge.rs` (imports `:10`; new `the_gate_holds_again_when_an_act_respawns_a_com_driver`)
- Docs: `contracts/tcb.md:246`, `docs/technical/technical-readme.md:733-741`, `docs/technical/runbooks/learn-office-driver.md:61`, `:181`

**Interfaces:**
- Consumes: `RealKernel::check_qualified(&self, manifest: &DriverManifest) -> Result<(), KernelError>` (unchanged, `crates/vk-kernel/src/learn.rs:228-283`); `RealKernel::set_forge_fixtures(&mut self, on: bool)`.
- Produces: `vk_kernel::drivers::DriverFailure::Unqualified(String)` — `reason()` = `"unqualified"`, the closed word, like `tampered` (PF-6), `detail()` = the sentence; `DriverFailure::refusal(&self) -> String` — what the act's caller is told: `reason()`, and for `Unqualified` `"unqualified: <sentence>"`; `DRIVER_FAILURE_REASONS` gains `"unqualified"`; `vk_ipc::drivers::DriverPool::ensure(&self, row: &DriverRow, dirs: &DriverDirs, gate: &dyn Fn(&DriverRow) -> anyhow::Result<()>) -> anyhow::Result<bool>`; `DriverPool::act(&self, row, dirs, action, args, deadline, caps, should_stop, gate: &dyn Fn(&DriverRow) -> anyhow::Result<()>) -> Result<ActOutcome, DriverFailure>`; private `qualification_gate(kernel: &Shared) -> impl Fn(&DriverRow) -> anyhow::Result<()> + '_` and `struct Unqualified(String)`; test helper `common::Node::token_for(&self, drivers: &[&str]) -> String` (Task 5 does not use it; Task 4's test does).

- [ ] **Step 1: The test helper** (`crates/vk-ipc/tests/common/mod.rs:256-299`)

Replace `token` with:

```rust
    /// A lease token for a harness step that declared `fake`.
    pub async fn token(&self) -> String {
        self.token_for(&["fake"]).await
    }

    /// A lease token for a harness step that declared `drivers`.
    pub async fn token_for(&self, drivers: &[&str]) -> String {
        let task = self
            .c
            .call(
                "task.create",
                json!({
                    "goal": "recette",
                    "artefact_type": "report",
                    "constraints": ["fictional data only"],
                    "evidence": ["cases: N0-1"],
                    "steps": [
                        {"kind": "harness", "name": "claude-code", "drivers": drivers},
                        {"kind": "approve"}
                    ]
                }),
                None,
            )
            .await
            .unwrap();
        let machine = vk_contracts::syscalls::Ctx {
            principal: Principal::Machine {
                node_id: "n1".into(),
                lease_id: "test".into(),
            },
            clearance: Clearance {
                max_scope: Scope::Personal,
                third_party_allowed: true,
            },
            partition: "local".into(),
            now_ms: vk_kernel::now_ms(),
        };
        self.k
            .lock()
            .unwrap()
            .harness_launch(
                &machine,
                task["id"].as_str().unwrap(),
                "claude-code",
                60_000,
            )
            .unwrap()
            .token
    }
```

- [ ] **Step 2: Write the failing test** (`crates/vk-ipc/tests/forge.rs`)

`:10` becomes `use vk_contracts::driver::{DriverLearned, DriverManifest, DriverQualified, DriverUnmounted};`. Append:

```rust
/// Founder decision D-31 (final re-review F-1), ruling LF-9: the qualification gate holds before every
/// spawn of a `kind: com` driver's process, not at mount alone. Boot spawns nothing, so after a
/// restart the first act spawns the driver: here a pool emptied as a restart leaves it, and the
/// fixtures flag a daemon started without the seam sets. The act is refused before any process
/// starts, its refusal carrying the gate's sentence, and the driver degraded with the closed word
/// `unqualified`: the sentence goes to `driver.log`, never to the ledger (ruling PF-6). The gate runs
/// before every start of the process, not at each act (ruling PF-7): a `qualification.json` changed
/// since `vk driver qualify` does not stop an act its live process serves, and refuses the next start.
#[tokio::test]
async fn the_gate_holds_again_when_an_act_respawns_a_com_driver() {
    if !forge_available() {
        return;
    }
    let node = Node::start().await;
    let corpus = ooxml_corpus(&node.dir().join("corpus"), 10, 0);
    let out = node.dir().join("word-maison");
    node.call_human("driver.learn", learn_params(&corpus, &out))
        .await
        .unwrap();
    let driver = out.join("driver");
    let q = node
        .call(
            "driver.qualify",
            json!({"driver": driver, "corpus": corpus}),
        )
        .await
        .unwrap();
    assert_eq!(q["fake"], json!(true), "{q}");
    let manifest = manifest_of(&driver);
    let id = manifest.driver_id();
    let mount = json!({"manifest": manifest, "root": driver});
    node.call_human("driver.mount", mount.clone())
        .await
        .unwrap();
    let token = node.token_for(&["word-maison"]).await;
    // A document that is not there: the generated action refuses `precondition` before it reaches
    // Office, so the process is seen to answer and no application is started.
    let act = json!({
        "token": token,
        "driver": "word-maison",
        "action": "appliquer_style_maison",
        "args": {"document": node.dir().join("absent.docx"), "pdf": node.dir().join("absent.pdf")}
    });
    let answered = node.c.call("driver.act", act.clone(), None).await.unwrap();
    assert_eq!(answered["ok"], json!(true), "{answered}");
    assert!(
        node.pool.has_host("word-maison"),
        "the gate held at the mount: the process runs"
    );

    // The restart without the seam, as the act path meets it: no process, the flag off.
    node.pool.drop_host("word-maison");
    node.k.lock().unwrap().set_forge_fixtures(false);
    let sentence = format!(
        "driver {id} was qualified on the fake application; remedy: vk driver qualify on the \
         real one"
    );
    let e = node.c.call("driver.act", act.clone(), None).await.unwrap_err();
    assert!(
        call_error(&e).message.ends_with(&format!("unqualified: {sentence}")),
        "the act's refusal carries the gate's sentence: {e}"
    );
    assert!(
        !node.pool.has_host("word-maison"),
        "refused before any process started"
    );
    // The ledger and the row keep the closed word alone; the sentence — a driver id, a remedy —
    // goes to the refusal above and to driver.log, never to the chain (PF-6, Ruling 28).
    let row = node.listed("word-maison").await;
    assert_eq!(
        (
            row["state"]["state"].as_str(),
            row["state"]["reason"].as_str()
        ),
        (Some("degraded"), Some("unqualified")),
        "{row}"
    );
    assert_eq!(
        node.events("driver.unmounted").last().unwrap().payload_hash,
        hash_canonical(&DriverUnmounted {
            driver_id: id.clone(),
            reason: "unqualified".into(),
        })
    );
    let log = std::fs::read_to_string(node.dir().join("drivers/word-maison/work/driver.log"))
        .unwrap();
    assert!(log.contains(&format!("failed: unqualified: {sentence}")), "{log}");

    // With the seam back a remount opens and starts the process. The gate runs before every start
    // of the driver's process (D-31), not at each act (PF-7): a record edited while that process
    // lives does not stop the next act, which the live process serves; the next start is refused.
    node.k.lock().unwrap().set_forge_fixtures(true);
    node.call_human("driver.mount", mount).await.unwrap();
    assert!(node.pool.has_host("word-maison"), "the remount started the process");
    let record = PathBuf::from(q["record"].as_str().unwrap());
    let edited = std::fs::read_to_string(&record)
        .unwrap()
        .replacen("\"passes\": 10", "\"passes\": 3", 1);
    std::fs::write(&record, edited).unwrap();
    let answered = node.c.call("driver.act", act.clone(), None).await.unwrap();
    assert_eq!(answered["ok"], json!(true), "{answered}");
    assert!(
        node.pool.has_host("word-maison"),
        "a live process serves the act: no start, so no gate"
    );
    // A restart, an idle death or a degrade leaves no process: the next act starts one.
    node.pool.drop_host("word-maison");
    let e = node.c.call("driver.act", act, None).await.unwrap_err();
    assert!(
        call_error(&e).message.ends_with(&format!(
            "unqualified: driver {id}: qualification.json changed since vk driver qualify; remedy: \
             vk driver qualify --driver DIR --corpus DIR"
        )),
        "{e}"
    );
    assert!(!node.pool.has_host("word-maison"));
    node.shutdown().await;
}
```

- [ ] **Step 3: Run it, see it fail**

```powershell
cargo test --locked -p vk-ipc --test forge -- the_gate_holds_again --nocapture
```

Expected: FAIL at `call_error(&e)` of the second act — today the act after `drop_host` respawns the driver without the gate and answers `ok` (`called Result::unwrap_err() on an Ok value`).

- [ ] **Step 4: The failure variant** (`crates/vk-kernel/src/drivers.rs`)

In the doc comment of `DRIVER_FAILURE_REASONS` (`:137-152`), after the sentence about `evidence too large`, add: `` `unqualified` is the qualification gate's refusal of a spawn (founder decision D-31), a bare word like `tampered`: the gate's sentence — a driver id, a remedy, and when the record cannot be read its path — is the failure's [`DriverFailure::detail`] and the act's refusal ([`DriverFailure::refusal`]), never the record (ruling PF-6). `` and append `"unqualified",` after `"tampered",` in the list (`:169`).

In `enum DriverFailure` (`:372-388`), after `Tampered`, add:

```rust
    /// The qualification gate refused the spawn of a `kind: com` driver's process (founder decision
    /// D-31): the kernel's own sentence from `RealKernel::check_qualified` — no record, a record
    /// changed since `vk driver qualify`, a replay on the fake application on a daemon without the
    /// seam, a replay under the plan's thresholds. Found by the daemon before a spawn, as
    /// [`Self::Tampered`] is; the driver is degraded with the word `unqualified` and never started.
    /// The sentence names the driver's id and, when the record cannot be read, its path under the
    /// user's profile: it goes to [`Self::detail`] and to [`Self::refusal`], never to the ledger
    /// (ruling PF-6, Ruling 28).
    Unqualified(String),
```

In `reason()` (`:398-405`) add the arm `DriverFailure::Unqualified(_) => "unqualified".into(),` and in `detail()` (`:410-421`) the arm `DriverFailure::Unqualified(sentence) => sentence.clone(),`. After `detail()` add:

```rust
    /// What the act's caller is told when the act failed: the ledger's word, and after it, for the
    /// qualification gate, the gate's sentence (founder decision D-31, ruling PF-6) — an answer on the
    /// pipe to the caller who asked, never the record.
    pub fn refusal(&self) -> String {
        match self {
            DriverFailure::Unqualified(sentence) => format!("unqualified: {sentence}"),
            other => other.reason(),
        }
    }
```

In `driver_act_settle`'s failure branch, `return Err(KernelError::Gate(reason));` (`:1081`) becomes `return Err(KernelError::Gate(failure.refusal()));`: the record and the degrade just above keep `reason`, the word; the caller is told the sentence too.

In the test `host_error_reasons_are_a_closed_vocabulary` (`:1321-1335`), before the final loop add:

```rust
        // The gate's sentence as `leak` says it — a path, a hash, words: none of it on the record.
        let unqualified = DriverFailure::Unqualified(leak.into());
        assert_eq!(unqualified.reason(), "unqualified", "the closed word alone (PF-6)");
        assert_eq!(unqualified.detail(), leak, "the sentence is the detail, for the logs");
        assert_eq!(unqualified.refusal(), format!("unqualified: {leak}"), "and the refusal");
        assert_eq!(DriverFailure::Tampered.refusal(), "tampered", "any other is refused by its word");
```

and the final loop's list becomes `["would not start", "panicked", "tampered", "unqualified"]`.

- [ ] **Step 5: The gate before every spawn** (`crates/vk-ipc/src/drivers.rs`)

Imports (`:30-34`): add `use vk_contracts::syscalls::KernelError;` (beside `use vk_contracts::syscalls::Ctx;`).

After `impl std::error::Error for Tampered {}` (`:541`) add:

```rust
/// The qualification gate refused to start a `kind: com` driver's process (founder decision D-31):
/// the kernel's sentence. The act that needed the process is refused and the kernel degrades the
/// driver with the word `unqualified` ([`DriverFailure::Unqualified`]); the sentence goes to the logs
/// and to the act's refusal, never to the ledger (ruling PF-6).
#[derive(Debug)]
pub(crate) struct Unqualified(pub(crate) String);

impl std::fmt::Display for Unqualified {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for Unqualified {}

/// The gate [`DriverPool::ensure`] runs before it starts a process (founder decision D-31, final
/// re-review F-1, ruling LF-9): the kernel's `check_qualified` on the row's manifest, in one short
/// lock phase — `Ok` for every kind but `com` — with the fixtures flag this daemon's `serve_on`
/// handed the kernel (final review I-1). Boot spawns nothing, so this is where a learned driver is
/// held again after a restart, an idle death or a replaced identity. A store that cannot be read is
/// a spawn refused, not an unqualified driver.
fn qualification_gate(kernel: &Shared) -> impl Fn(&DriverRow) -> anyhow::Result<()> + '_ {
    move |row: &DriverRow| {
        let k = kernel.lock().map_err(|_| anyhow!("kernel lock poisoned"))?;
        match k.check_qualified(&row.manifest) {
            Ok(()) => Ok(()),
            Err(KernelError::Gate(sentence)) => Err(anyhow::Error::new(Unqualified(sentence))),
            Err(e) => Err(anyhow!("the qualification gate could not be read: {e}")),
        }
    }
}
```

`ensure` (`:156-217`): its doc comment gains, after `… and puts the host in its place.`, the sentence `` Before that spawn, and only then, `gate` runs on the row — the qualification gate (D-31, [`qualification_gate`]); its refusal is this `ensure`'s, nothing is spawned and the marker is cleared. ``; its signature becomes `pub fn ensure(&self, row: &DriverRow, dirs: &DriverDirs, gate: &dyn Fn(&DriverRow) -> anyhow::Result<()>) -> anyhow::Result<bool> {`; and between `let claim = Claim { pool: self, name };` (`:204`) and `let (host, max_memory_bytes) = spawn_host(row, dirs)?;` (`:205`) add:

```rust
        // The image is booted only with its test report in order (D-31): mount and act alike.
        gate(row)?;
```

`act` (`:219-263`): its signature gains a last parameter `gate: &dyn Fn(&DriverRow) -> anyhow::Result<()>,` (after `should_stop`); its doc comment's last sentence becomes `… or [`DriverFailure::Tampered`] when the installed tree it would have started no longer hashes to its fingerprint, or [`DriverFailure::Unqualified`] when the qualification gate refused to start a `com` driver; the kernel words the ledger from the variant alone.`; and the `ensure` call (`:255-263`) becomes:

```rust
            self.ensure(row, dirs, gate).map_err(|e| {
                // The installed tree changed since its install, or a learned driver no longer
                // passes its gate: the spawn refusals the kernel degrades by its own word.
                if e.is::<Tampered>() {
                    DriverFailure::Tampered
                } else if let Some(Unqualified(sentence)) = e.downcast_ref::<Unqualified>() {
                    DriverFailure::Unqualified(sentence.clone())
                } else {
                    DriverFailure::WouldNotStart(format!("{e:#}"))
                }
            })?;
```

`mount` (`:723-726`) becomes:

```rust
    let had_host = pool.has_host(&name);
    let governed = pool
        .ensure(&row, &dirs, &qualification_gate(kernel))
        .map_err(|e| bad(&format!("driver {name} would not start: {e:#}")))?;
```

`run_act` (`:822-859`): its doc comment gains, after `… fills a restarted daemon's empty pool.`, the sentence `Every spawn it causes is held to the qualification gate first ([`qualification_gate`], D-31).`; and the `pool.act(…)` call (`:850-858`) becomes:

```rust
    pool.act(
        &row,
        &plan.dirs,
        &plan.action,
        &plan.args,
        budget,
        plan.caps,
        should_stop,
        &qualification_gate(kernel),
    )
```

- [ ] **Step 6: The two direct calls in the driver tests** (`crates/vk-ipc/tests/drivers.rs`)

After `fn machine()` (it ends at `:470`) add:

```rust
/// No gate: what `ensure` and `act` run before a spawn when the caller is a test of the pool itself
/// (the qualification gate, D-31, concerns `kind: com` drivers only; these are browsers).
fn no_gate(_: &vk_kernel::drivers::DriverRow) -> anyhow::Result<()> {
    Ok(())
}
```

`:1173` becomes `node.pool.ensure(&row_a, &dirs, &no_gate).unwrap();`, and the `pool.act(…)` of `:1229-1237` gains `&no_gate` as its last argument, after `&never`.

- [ ] **Step 7: Run the tests, see them pass**

```powershell
cargo test --locked -p vk-kernel --lib drivers -- --nocapture
cargo test --locked -p vk-ipc --test forge -- --nocapture
cargo test --locked -p vk-ipc --test drivers -- --nocapture
cargo test --locked -p vk-ipc --test capital -- --nocapture
```

Expected: green; `the_gate_holds_again_when_an_act_respawns_a_com_driver` passes; `act_round_trip_through_the_daemon` and the tampered test (`capital`) are unchanged — a browser driver passes the gate untouched.

- [ ] **Step 8: Docs**

`contracts/tcb.md:246` — at the end of **The qualification gate at mount (ruling L-P4).**, before `There is no override (ruling L-P16).`, insert:

```markdown
The gate is held again before every start of the driver's process, not at mount alone (founder decision D-31): boot spawns nothing, so the first act after a daemon restart, after a degrade or an idle death, and the act on a replaced identity each check it — with the fixtures flag this daemon set when it started. It is not checked again at each act while that process lives: a `qualification.json` edited under a running driver is caught at the next start of its process. A driver that fails there is never started: its act is refused with the gate's sentence (`unqualified: <sentence>`), and it is degraded with the word `unqualified` alone — the sentence names the driver's id and, when the record cannot be read, its path, so it goes to the daemon's log and `driver.log`, never to the ledger (Ruling 28) — which a remount clears once the record is right again.
```

`docs/technical/technical-readme.md` — at the end of the **The mount gate.** paragraph (`:733-741`), after `… it is mounted from its learn directory.`, add:

```markdown
The gate is checked again before every start of the driver's process (founder decision D-31)
— at mount, and at the first act after a daemon restart or a degrade — not at each act while
the process lives: a daemon restarted without `VK_FORGE_FIXTURES=1`, or a `qualification.json`
edited while the driver stayed mounted, refuses the act that would start the process, before
it starts. The act's refusal carries the gate's sentence; the driver is degraded with the word
`unqualified`, and the sentence goes to the daemon's log and `driver.log`, never to the ledger.
```

`docs/technical/runbooks/learn-office-driver.md:61` — after `… un démon relancé sans la prise refuse de monter ce driver (`was qualified on the fake application; remedy: vk driver qualify on the real one`)` insert `, et s'il était déjà monté, refuse l'acte qui relancerait son processus (le premier après la relance du démon ou une dégradation), avant de le relancer, et le dégrade (`unqualified`)`.

A row added to the §8 table, after the `was qualified on the fake application` row (`:181`):

```markdown
| `unqualified: driver <id> …; remedy: …` (le refus d'un acte ; le driver passe `degraded`, raison `unqualified` : la phrase est dans `driver.log`, jamais dans le grand-livre) | la porte de qualification, rejouée avant de relancer le processus d'un driver monté, a refusé : démon relancé sans la prise, ou `qualification.json` modifié (vu au prochain lancement du processus, pas à chaque acte) | requalifier (§5.1), puis remonter (§5.2) |
```

- [ ] **Step 9: The whole workspace, the docs check**

```powershell
cargo test --locked --workspace --no-fail-fast
cargo fmt --all --check
cargo clippy --workspace --all-targets -- -D warnings
python -I scripts/docs/check_docs.py
```

- [ ] **Step 10: Commit**

```bash
git add crates/vk-kernel/src/drivers.rs crates/vk-ipc/src/drivers.rs crates/vk-ipc/tests/drivers.rs crates/vk-ipc/tests/common/mod.rs crates/vk-ipc/tests/forge.rs contracts/tcb.md docs/technical/technical-readme.md docs/technical/runbooks/learn-office-driver.md
git commit -F - <<'MSG'
feat(drivers): the qualification gate holds before every spawn of a learned driver, a restart's first act included (D-31)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UUDLpkwxsZXmLptqCeNL2R
MSG
```

---

### Task 5: D-28 — `vk driver act NAME ACTION [--args JSON]`, one action by hand under a presence proof (Opus)

> **Decided by the founder (D-33, 2026-10-20): the new kind `driver.operated`, its author `by` mandatory.** Task 5 is no longer blocked. Kind: **`driver.operated`**. Payload **`DriverOperated`**:
> `{ task_id: String, driver_id: String, action: String, args_hash: String, result_hash: String, ok: bool, reason?: String, duration_ms: u64, evidence: [String], evidence_bytes: u64, hosts_seen: [String], by: String }`
> — the hashes-only shape of `driver.acted`, without its `step_index` (there is no harness step) and with `by`, the device key that signed the act, never optional; `task_id` names the register the act's evidence went into. `driver.acted` is unchanged.
> **Why `driver.acted` does not fit** (ruling LF-10): its `task_id` and `step_index` name the running `Harness` step whose lease token authorised the act (`RealKernel::driver_act_begin`, `crates/vk-kernel/src/drivers.rs:827-951`), and it carries no author. A human's act recorded there would either invent a step or leave off the ledger the person who changed a firm's document — when every other human act on a driver names its device (`driver.mounted.by`, `driver.learned.by`).
> It is dispatched last, after Task 6: it consumes Task 4's `run_act` and `DriverFailure::refusal`.

**Model:** Opus. **Decisions:** D-28 (the CLI surface), D-33 (the ledger kind `driver.operated`, `by` mandatory). **Rulings:** LF-10, PF-6 (the act's refusal), PF-9 (two stale sentences).

**Files:**
- Modify: `crates/vk-contracts/src/driver.rs` (after `DriverActed`, `:471-491`: `DriverOperated`), `crates/vk-contracts/src/lib.rs:69` (the schema registry), `crates/vk-contracts/src/ledger.rs:296-301` (`ALLOWED_KINDS`), `:458-463` (a test)
- Create: `contracts/schemas/driver_operated.schema.json` (written by `cargo run -p vk-contracts --bin gen-schemas`), `contracts/examples/driver_operated/valid/first.json`
- Modify: `crates/vk-cli/src/man.rs` (`SCHEMAS`: a row after `driver_mounted`)
- Modify: `crates/vk-kernel/src/drivers.rs:21-37` (imports), after `DriverActPlan` (`:113-135`: `DriverOperatePlan`), after `driver_act_settle` (it ends at `:1238`: `driver_operate_begin`, `driver_operate_settle`)
- Modify: `crates/vk-ipc/src/drivers.rs` (after `act`, `:785-820`: `OperateParams`, `operate`)
- Modify: `crates/vk-ipc/src/server.rs:731-766` (`takes_presence`), after the `driver.act` arm (`:1030-1035`: the `driver.operate` arm)
- Modify: `crates/vk-cli/src/main.rs:104-111` (the `Driver` command's doc), `:528-625` (`DriverCmd::Act`), after the `Umount` arm (`:1272-1282`: the `Act` arm); `crates/vk-cli/src/render.rs` (after `unmounted_driver`, `:1359-1361`: `operated`; a test in `mod tests`)
- Test: `crates/vk-kernel/tests/drivers.rs` (imports `:24-27`; two tests), `crates/vk-ipc/tests/drivers.rs` (two tests), `crates/vk-cli/tests/smoke.rs` (`vk_forge_learn_then_qualify_then_mount_from_the_cli`, `:2738-2803`)
- Docs: `contracts/tcb.md:249` (the supervision bullet, and a new bullet after it), `contracts/README.md:15`, `:18`, `docs/technical/technical-readme.md:373-379`, `:422-431`, `docs/technical/runbooks/learn-office-driver.md:126` (the « seul écart de supervision » sentence) and a new §5.3 after it

**Interfaces:**
- Consumes: `run_act(kernel: &Shared, pool: &DriverPool, plan: &DriverActPlan, should_stop) -> Result<ActOutcome, DriverFailure>` with the qualification gate inside (Task 4); `DriverFailure::refusal()` (Task 4: the word, and for the gate its sentence); `report_failure(plan: &DriverActPlan, failure: &DriverFailure)`; `DriverPool::clear_acting(&self, name: &str)`; `RealKernel::{submit_task, read_register, attach_artefact_labelled, degrade_driver, touch_driver, driver_dirs, driver}`; in `server.rs`, `ctx_for` and `invariant`; in `main.rs`, `presence(&Client)` and `show`.
- Produces: `vk_contracts::driver::DriverOperated` (the payload above); ledger kind `driver.operated`; `vk_kernel::drivers::DriverOperatePlan { act: DriverActPlan, register: RegisterId, by: String }`; `RealKernel::driver_operate_begin(&mut self, ctx: &Ctx, name: &str, action: &str, args: &serde_json::Value) -> Result<DriverOperatePlan, KernelError>`; `RealKernel::driver_operate_settle(&mut self, ctx: &Ctx, now_ms: u64, plan: &DriverOperatePlan, outcome: Result<ActOutcome, DriverFailure>) -> Result<serde_json::Value, KernelError>` → `{task_id, ok, reason, result, evidence: [{name, hash, bytes}]}`; IPC method `driver.operate`, params `{driver: String, action: String, args?: Value}` (`deny_unknown_fields`, `args` defaults to `{}`), a human act (presence proof), answer = the settle's object plus `driver` and `action`; CLI `vk driver act NAME ACTION [--args JSON]` (+ the global `--json`); `render::operated(v: &Value) -> String`.

- [ ] **Step 1: The contract** (`crates/vk-contracts`)

The failing test, in `ledger.rs` after `driver_learned_and_driver_qualified_are_allowed_kinds` (`:458-463`):

```rust
    #[test]
    fn driver_operated_is_an_allowed_kind() {
        assert!(is_allowed_kind("driver.operated"));
        assert!(!is_allowed_kind("driver.operate"));
    }
```

`cargo test --locked -p vk-contracts driver_operated` fails (`driver.operated` is not listed). Then:

`ALLOWED_KINDS` (`:296-301`), after `"driver.qualified",`:

```rust
    // A human acted on a mounted driver by hand — `vk driver act`, no harness, no arch (founder
    // decision D-28): payload `driver::DriverOperated`, hashes only and the device that signed it.
    "driver.operated",
```

`driver.rs`, after `DriverActed` (`:491`):

```rust
/// Ledger payload of `driver.operated` (founder decision D-28): an operator acted on a mounted driver
/// by hand — `vk driver act`, under a presence proof, one action of the manifest, no harness and no
/// arch. Hashes only, as `driver.acted`: `task_id` names the register the act's evidence went into,
/// `by` the device key that signed the act.
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct DriverOperated {
    pub task_id: String,
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
    /// The remote endpoints seen while the action ran, as on `driver.acted` (Ruling R8).
    pub hosts_seen: Vec<String>,
    pub by: String,
}
```

`lib.rs` — after the `("driver_acted", schemars::schema_for!(driver::DriverActed)),` row (`:69`):

```rust
        (
            "driver_operated",
            schemars::schema_for!(driver::DriverOperated),
        ),
```

`contracts/examples/driver_operated/valid/first.json`:

```json
{
  "task_id": "task-node-1-7",
  "driver_id": "sha256:6666666666666666666666666666666666666666666666666666666666666666",
  "action": "appliquer_style_maison",
  "args_hash": "sha256:8888888888888888888888888888888888888888888888888888888888888888",
  "result_hash": "sha256:9999999999999999999999999999999999999999999999999999999999999999",
  "ok": true,
  "duration_ms": 812,
  "evidence": ["sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"],
  "evidence_bytes": 214,
  "hosts_seen": [],
  "by": "laptop"
}
```

`crates/vk-cli/src/man.rs` — in `SCHEMAS`, between the `driver_mounted` and the `driver_package` rows:

```rust
    (
        "driver_operated",
        include_str!("../../../contracts/schemas/driver_operated.schema.json"),
    ),
```

Then:

```powershell
cargo run --locked -p vk-contracts --bin gen-schemas
cargo test --locked -p vk-contracts -p vk-cli --lib
cargo test --locked -p vk-contracts --test schema_drift --test examples_validate
```

Expected: `contracts/schemas/driver_operated.schema.json` written; `driver_operated_is_an_allowed_kind`, `committed_schemas_match_generated`, `every_example_validates_as_its_folder_says` and `every_schema_in_the_repository_is_in_the_table` pass.

- [ ] **Step 2: Write the failing kernel tests** (`crates/vk-kernel/tests/drivers.rs`)

The import of `vk_contracts::driver` (`:24-27`) gains `DriverOperated`. Append:

```rust
/// Founder decision D-28 (`vk driver act`): an operator's act by hand is a human act on a mounted
/// driver's own actions, refused in `driver.act`'s order before anything is recorded — no register,
/// no `driver.operated`, nothing in flight.
#[test]
fn an_operator_act_is_a_human_act_refused_in_order_before_anything_is_recorded() {
    let d = tempfile::tempdir().unwrap();
    let mut k = open(d.path());
    mount(&mut k);
    let e = k
        .driver_operate_begin(&machine(2), "maskai-1", "echo", &echo_args())
        .unwrap_err();
    assert!(matches!(e, KernelError::I1(_)), "{e}");
    let e = k
        .driver_operate_begin(&human(2), "ghost", "echo", &echo_args())
        .unwrap_err();
    assert!(matches!(e, KernelError::NotFound(_)), "{e}");
    let e = k
        .driver_operate_begin(&human(2), "maskai-1", "nope", &echo_args())
        .unwrap_err();
    assert!(
        e.to_string().ends_with("driver maskai-1 has no action nope"),
        "{e}"
    );
    let e = k
        .driver_operate_begin(&human(2), "maskai-1", "echo", &serde_json::json!({}))
        .unwrap_err();
    assert!(e.to_string().contains("args for echo"), "{e}");
    k.stop(&human(3), "node").unwrap();
    let e = k
        .driver_operate_begin(&human(4), "maskai-1", "echo", &echo_args())
        .unwrap_err();
    assert!(matches!(e, KernelError::Stopped(ref s) if s == "node"), "{e}");
    assert!(
        events_of(&k, "task.submitted").is_empty(),
        "no register for a refused act"
    );
    assert!(events_of(&k, "driver.operated").is_empty());
}

/// Founder decision D-28: the act's evidence goes into a register of its own, at that register's
/// label plus the third-party origin, and `driver.operated` records the act by hashes, with the
/// device that signed it; a second act waits for the first to settle; a host failure degrades the
/// driver as a harness act's does (Ruling R12).
#[test]
fn an_operator_act_settles_into_its_own_register_and_records_driver_operated() {
    let d = tempfile::tempdir().unwrap();
    let mut k = open(d.path());
    let row = mount(&mut k);
    let plan = k
        .driver_operate_begin(&human(10), "maskai-1", "echo", &echo_args())
        .unwrap();
    assert_eq!(
        (plan.by.as_str(), plan.act.step_index, plan.act.begun_ms),
        ("phone-1", 0, 10)
    );
    assert_eq!(plan.act.deadline_ms, 10 + 60_000);
    assert_eq!(events_of(&k, "task.submitted").len(), 1, "the act's register");
    let e = k
        .driver_operate_begin(&human(11), "maskai-1", "look", &serde_json::json!({}))
        .unwrap_err();
    assert!(e.to_string().contains("in flight"), "{e}");

    let answer = k
        .driver_operate_settle(&human(20), 20, &plan, Ok(echo_outcome("journal line")))
        .unwrap();
    let hash = answer["evidence"][0]["hash"].as_str().unwrap().to_string();
    assert_eq!(
        answer,
        serde_json::json!({
            "task_id": plan.act.task_id,
            "ok": true,
            "reason": null,
            "result": {"text": "journal line"},
            "evidence": [{"name": "note.txt", "hash": hash, "bytes": 19}],
        })
    );
    let env = k.store().blobs.envelope(&hash).unwrap();
    assert!(
        env.label.origins.contains(&Origin::ThirdPartyInbound),
        "{:?}",
        env.label
    );
    assert_eq!(
        k.read_artefact(&human(21), &hash).unwrap(),
        b"hello from the site".to_vec()
    );
    let operated = events_of(&k, "driver.operated");
    assert_eq!(operated.len(), 1);
    assert_eq!(
        operated[0].payload_hash,
        hash_canonical(&DriverOperated {
            task_id: plan.act.task_id.clone(),
            driver_id: row.driver_id.clone(),
            action: "echo".into(),
            args_hash: hash_canonical(&echo_args()),
            result_hash: hash_canonical(&serde_json::json!({"text": "journal line"})),
            ok: true,
            reason: None,
            duration_ms: 7,
            evidence: vec![hash.clone()],
            evidence_bytes: 19,
            hosts_seen: vec!["1.2.3.4:443".into()],
            by: "phone-1".into(),
        }),
        "hashes only, and who acted"
    );
    assert!(
        events_of(&k, "driver.acted").is_empty(),
        "not a harness step's act"
    );
    assert_eq!(k.driver("maskai-1").unwrap().unwrap().last_act_ms, 20);

    // A host failure degrades the driver, as a harness act's does (Ruling R12).
    let plan = k
        .driver_operate_begin(&human(30), "maskai-1", "look", &serde_json::json!({}))
        .unwrap();
    let e = k
        .driver_operate_settle(&human(40), 40, &plan, Err(HostError::Timeout.into()))
        .unwrap_err();
    assert!(e.to_string().ends_with("timeout"), "{e}");
    assert_eq!(
        k.driver("maskai-1").unwrap().unwrap().state,
        DriverState::Degraded {
            reason: "timeout".into()
        }
    );
    assert_eq!(events_of(&k, "driver.operated").len(), 2);
    assert!(k.ledger().verify_chain());
}
```

```powershell
cargo test --locked -p vk-kernel --test drivers -- an_operator_act --nocapture
```

Expected: compile error (`no method named driver_operate_begin`).

- [ ] **Step 3: Implement the kernel's two halves** (`crates/vk-kernel/src/drivers.rs`)

Imports (`:28-35`): `vk_contracts::driver::{…}` gains `DriverOperated`; `use vk_contracts::labels::Origin;` becomes `use vk_contracts::labels::{Label, Origin};`; `use vk_contracts::register::{Evidence, Register};` becomes `use vk_contracts::register::{Evidence, Register, RegisterId};`.

After `DriverActPlan` (`:135`) add:

```rust
/// What phase one of `driver.operate` hands the daemon (founder decision D-28): the act, shaped as
/// `driver.act`'s plan so the daemon runs it the same way, the register its evidence goes into, and
/// the device that signed it.
#[derive(Debug, Clone)]
pub struct DriverOperatePlan {
    pub act: DriverActPlan,
    pub register: RegisterId,
    pub by: String,
}
```

In `impl RealKernel`, after `driver_act_settle` (it ends at `:1238`) add:

```rust
    /// Phase one of `driver.operate` (founder decision D-28, `vk driver act`), under the kernel lock:
    /// a human acts on a mounted driver by hand, one action of its manifest, with no harness and no
    /// arch. The refusals of `driver_act_begin` that need no harness step, in its order: a machine
    /// principal (I1 — the daemon refused it first, and the rule does not rest on the daemon alone);
    /// a driver not mounted, or degraded; an action the manifest does not list, or args its schema
    /// refuses; the cadence; a STOP of the node; an act already in flight. Each leaves nothing
    /// behind. Then the act's own register — `task.submitted`, label bottom: it holds only what the
    /// driver hands back, at that label plus the third-party origin, so it flows to any driver's
    /// clearance — and the plan, whose `step_index` is 0, the register's one act.
    pub fn driver_operate_begin(
        &mut self,
        ctx: &Ctx,
        name: &str,
        action: &str,
        args: &serde_json::Value,
    ) -> Result<DriverOperatePlan, KernelError> {
        let by = match &ctx.principal {
            Principal::Human { device_id } => device_id.clone(),
            Principal::Machine { .. } => {
                return Err(KernelError::I1(
                    "acting on a driver by hand changes what it drives, which is a human act: run \
                     it from an enrolled device"
                        .into(),
                ))
            }
        };
        let row = self
            .driver(name)?
            .ok_or_else(|| KernelError::NotFound(format!("driver {name}")))?;
        if let DriverState::Degraded { reason } = &row.state {
            return Err(KernelError::Gate(format!(
                "driver {name} is degraded: {reason}; remount it"
            )));
        }
        let act = row
            .manifest
            .action(action)
            .ok_or_else(|| KernelError::Gate(format!("driver {name} has no action {action}")))?
            .clone();
        act.validate_args(args)
            .map_err(|e| KernelError::Gate(format!("args for {action}: {e}")))?;
        if row.last_act_ms > 0 {
            let since = ctx.now_ms.saturating_sub(row.last_act_ms);
            if since < row.manifest.min_interval_ms {
                return Err(KernelError::Gate(format!(
                    "driver {name}: {} ms before the next action",
                    row.manifest.min_interval_ms - since
                )));
            }
        }
        if self.stops.stopped("node") {
            return Err(KernelError::Stopped("node".into()));
        }
        if self.in_flight.contains_key(name) {
            return Err(KernelError::Gate(format!(
                "driver {name} has an action in flight; wait for it to settle"
            )));
        }
        let register =
            self.submit_task(ctx, &format!("vk driver act {name} {action}"), Label::bottom())?;
        let task_id = self.read_register(ctx, &register)?.task_id;
        self.act_counter += 1;
        let act_id = self.act_counter;
        self.in_flight.insert(name.to_string(), act_id);
        Ok(DriverOperatePlan {
            act: DriverActPlan {
                task_id,
                step_index: 0,
                name: name.into(),
                driver_id: row.driver_id,
                action: act,
                args: args.clone(),
                args_hash: hash_canonical(args),
                deadline_ms: ctx.now_ms.saturating_add(row.manifest.max_action_ms),
                caps: EvidenceCaps {
                    per_file: HARNESS_MAX_FILE_BYTES,
                    per_action: HARNESS_MAX_RUN_BYTES / 4,
                },
                dirs: self.driver_dirs(name),
                begun_ms: ctx.now_ms,
                act_id,
            },
            register,
            by,
        })
    }

    /// Phase three of `driver.operate`, under the kernel lock, once the daemon has the host's answer:
    /// `driver_act_settle`'s rules for one act with no harness step. A settle that is not the act in
    /// flight (an unmount and a remount came between) logs `driver.operated` with `stale act: …` and
    /// touches nothing else. A failure logs `driver.operated{ok: false, reason}` — the closed word
    /// (Ruling 28) — and degrades the driver unless it is one of the host's evidence refusals (Ruling
    /// R12). An answer's evidence is held to the manifest's names and to the act's caps, then each
    /// file is attached to the act's register at its label plus the third-party origin, under this
    /// human's context; `driver.operated` names the hashes. Returns `{task_id, ok, reason, result,
    /// evidence: [{name, hash, bytes}]}`: the bytes stay in the store, read by `vk artefact cat` (a
    /// release on the ledger). The action's `to_register` is not applied: no later step reads this
    /// register.
    pub fn driver_operate_settle(
        &mut self,
        ctx: &Ctx,
        now_ms: u64,
        plan: &DriverOperatePlan,
        outcome: Result<ActOutcome, DriverFailure>,
    ) -> Result<serde_json::Value, KernelError> {
        let act = &plan.act;
        let live = self.in_flight.get(&act.name) == Some(&act.act_id);
        if live {
            self.in_flight.remove(&act.name);
        }
        let base = DriverOperated {
            task_id: act.task_id.clone(),
            driver_id: act.driver_id.clone(),
            action: act.action.name.clone(),
            args_hash: act.args_hash.clone(),
            result_hash: hash_canonical(&serde_json::Value::Null),
            ok: false,
            reason: None,
            duration_ms: now_ms.saturating_sub(act.begun_ms),
            evidence: vec![],
            evidence_bytes: 0,
            hosts_seen: vec![],
            by: plan.by.clone(),
        };
        if !live {
            let stale = "stale act: the driver was remounted since";
            let record = match &outcome {
                Ok(o) => DriverOperated {
                    result_hash: hash_canonical(&o.result),
                    reason: Some(stale.into()),
                    duration_ms: o.duration_ms,
                    hosts_seen: o.hosts_seen.clone(),
                    ..base
                },
                Err(failure) => DriverOperated {
                    reason: Some(format!("stale act: {}", failure.reason())),
                    ..base
                },
            };
            self.log("driver.operated", now_ms, &record)?;
            return Err(KernelError::Gate(stale.into()));
        }
        let outcome = match outcome {
            Ok(outcome) => outcome,
            Err(failure) => {
                let reason = failure.reason();
                self.log(
                    "driver.operated",
                    now_ms,
                    &DriverOperated {
                        reason: Some(reason.clone()),
                        ..base
                    },
                )?;
                let read_back = matches!(
                    &failure,
                    DriverFailure::Host(
                        HostError::EvidenceEscape(_)
                            | HostError::EvidenceNotDeclared(_)
                            | HostError::EvidenceTooLarge { .. }
                    )
                );
                if !read_back {
                    match self.degrade_driver(&act.name, &reason) {
                        Ok(()) | Err(KernelError::NotFound(_)) => {}
                        Err(e) => return Err(e),
                    }
                }
                self.touch_driver(&act.name, now_ms)?;
                // The record keeps the word; the operator is told the gate's sentence too (PF-6).
                return Err(KernelError::Gate(failure.refusal()));
            }
        };
        let total: u64 = outcome.evidence.iter().map(|e| e.bytes.len() as u64).sum();
        let refused = outcome
            .evidence
            .iter()
            .find(|e| !act.action.evidence.contains(&e.name))
            .map(|e| {
                format!(
                    "evidence {} is not a name the manifest allows for {}",
                    e.name, act.action.name
                )
            })
            .or_else(|| {
                outcome
                    .evidence
                    .iter()
                    .find(|e| e.bytes.len() as u64 > act.caps.per_file)
                    .map(|e| {
                        format!(
                            "{} is {} bytes, above the {}-byte limit for one file",
                            e.name,
                            e.bytes.len(),
                            act.caps.per_file
                        )
                    })
            })
            .or_else(|| {
                (total > act.caps.per_action).then(|| {
                    format!(
                        "evidence of {} ({total} bytes) is above the {}-byte limit for one act",
                        act.action.name, act.caps.per_action
                    )
                })
            });
        if let Some(why) = refused {
            self.log(
                "driver.operated",
                now_ms,
                &DriverOperated {
                    result_hash: hash_canonical(&outcome.result),
                    reason: Some(why.clone()),
                    duration_ms: outcome.duration_ms,
                    hosts_seen: outcome.hosts_seen.clone(),
                    ..base
                },
            )?;
            self.touch_driver(&act.name, now_ms)?;
            return Err(KernelError::Gate(why));
        }
        let mut label = self.read_register(ctx, &plan.register)?.label;
        label.origins.insert(Origin::ThirdPartyInbound);
        let mut hashes = Vec::with_capacity(outcome.evidence.len());
        let mut evidence = Vec::with_capacity(outcome.evidence.len());
        for file in &outcome.evidence {
            let env = self.attach_artefact_labelled(
                ctx,
                &plan.register,
                &file.name,
                &file.bytes,
                Some(label.clone()),
            )?;
            evidence.push(serde_json::json!({
                "name": file.name,
                "hash": env.hash,
                "bytes": file.bytes.len(),
            }));
            hashes.push(env.hash);
        }
        self.log(
            "driver.operated",
            now_ms,
            &DriverOperated {
                result_hash: hash_canonical(&outcome.result),
                ok: outcome.ok,
                reason: outcome.reason.clone(),
                duration_ms: outcome.duration_ms,
                evidence: hashes,
                evidence_bytes: total,
                hosts_seen: outcome.hosts_seen.clone(),
                ..base
            },
        )?;
        self.touch_driver(&act.name, now_ms)?;
        Ok(serde_json::json!({
            "task_id": act.task_id,
            "ok": outcome.ok,
            "reason": outcome.reason,
            "result": outcome.result,
            "evidence": evidence,
        }))
    }
```

(`env.hash` is moved into `hashes` after the `json!` that copies it: `json!` serialises a borrowed `String` by value. If the compiler asks, write `"hash": env.hash.clone()`.)

```powershell
cargo test --locked -p vk-kernel --test drivers -- --nocapture
```

Expected: the two new tests pass, and every other test of the file still passes.

- [ ] **Step 4: Write the failing IPC tests** (`crates/vk-ipc/tests/drivers.rs`, appended)

```rust
/// Founder decision D-28: `driver.operate` — `vk driver act` — is one action of a mounted driver's
/// manifest by a human, with no harness and no arch. A machine principal is refused, and so are a
/// name that is not mounted and an action the manifest does not list, each before anything is
/// recorded.
#[tokio::test]
async fn operate_is_a_human_act_on_a_mounted_driver_s_own_actions() {
    if !python_available() {
        return;
    }
    let drv = driver_dir();
    let node = Node::start().await;
    node.mount(drv.path()).await;
    let echo = json!({"driver": "fake", "action": "echo", "args": {"case": "N0-1"}});
    let err = node.c.call("driver.operate", echo, None).await.unwrap_err();
    let e = call_error(&err);
    assert_eq!(e.code, vk_ipc::E_INVARIANT, "{err}");
    assert!(e.message.starts_with("I1: acting on a driver by hand"), "{err}");
    let proof = node.proof().await;
    let err = node
        .c
        .call(
            "driver.operate",
            json!({"driver": "ghost", "action": "echo"}),
            Some(proof),
        )
        .await
        .unwrap_err();
    assert_eq!(call_error(&err).code, vk_ipc::E_NOT_FOUND, "{err}");
    let proof = node.proof().await;
    let err = node
        .c
        .call(
            "driver.operate",
            json!({"driver": "fake", "action": "nope"}),
            Some(proof),
        )
        .await
        .unwrap_err();
    assert!(
        call_error(&err)
            .message
            .ends_with("driver fake has no action nope"),
        "{err}"
    );
    assert_eq!(node.events("driver.operated"), 0);
    assert_eq!(node.events("task.submitted"), 0, "no register for a refused act");
    node.shutdown().await;
}

/// Founder decision D-28: a human's act runs the driver's action and answers its result and its
/// evidence — attached to a register of its own, read back by `artefact.read` — and records
/// `driver.operated`, not `driver.acted`.
#[tokio::test]
async fn operate_runs_one_action_without_a_harness_and_records_driver_operated() {
    if !python_available() {
        return;
    }
    let drv = driver_dir();
    let node = Node::start().await;
    node.mount(drv.path()).await;
    let proof = node.proof().await;
    let answer = node
        .c
        .call(
            "driver.operate",
            json!({"driver": "fake", "action": "echo", "args": {"case": "N0-1"}}),
            Some(proof),
        )
        .await
        .unwrap();
    assert_eq!(
        (
            answer["driver"].as_str(),
            answer["action"].as_str(),
            answer["ok"].as_bool()
        ),
        (Some("fake"), Some("echo"), Some(true)),
        "{answer}"
    );
    assert_eq!(answer["result"]["echo"]["case"], "N0-1", "{answer}");
    let evidence = answer["evidence"].as_array().unwrap();
    assert_eq!(
        (
            evidence.len(),
            evidence[0]["name"].as_str(),
            evidence[0]["bytes"].as_u64()
        ),
        (1, Some("note.txt"), Some(2))
    );
    assert_eq!(node.last_event(), "driver.operated");
    assert_eq!(
        (
            node.events("driver.operated"),
            node.events("driver.acted"),
            node.events("task.submitted")
        ),
        (1, 0, 1)
    );
    let proof = node.proof().await;
    let read = node
        .c
        .call(
            "artefact.read",
            json!({"hash": evidence[0]["hash"]}),
            Some(proof),
        )
        .await
        .unwrap();
    assert_eq!(read["bytes_base64"], "aGk=", "the driver's note.txt: \"hi\"");
    node.shutdown().await;
}
```

```powershell
cargo test --locked -p vk-ipc --test drivers -- operate_ --nocapture
```

Expected: FAIL — `this method does not take a presence proof` (the method is unknown to `takes_presence`), and without a proof an unknown method.

- [ ] **Step 5: The IPC arm** (`crates/vk-ipc/src/drivers.rs`, `crates/vk-ipc/src/server.rs`)

`drivers.rs`, after `act` (`:820`):

```rust
/// What `driver.operate` takes, and nothing else (founder decision D-28): the driver and the action
/// by name, and `args` (an empty object when absent). No token: a human act, not a harness's.
#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct OperateParams {
    driver: String,
    action: String,
    #[serde(default = "no_args")]
    args: Value,
}

/// `driver.operate {driver, action, args}` (founder decision D-28, `vk driver act`): one action on a
/// mounted driver by a human — the presence proof checked by `dispatch` — with no harness and no
/// arch, in `driver.act`'s three phases: the kernel's refusals and the act's register under the lock,
/// the act with the lock released (the qualification gate before any spawn, D-31; a STOP polled),
/// the settle under the lock (`driver.operated`). The answer is the settle's, with the driver and the
/// action named back.
pub(crate) fn operate(
    kernel: &Shared,
    pool: &DriverPool,
    ctx: Ctx,
    p: Value,
) -> Result<Value, RpcError> {
    let OperateParams {
        driver,
        action,
        args,
    } = serde_json::from_value(p).map_err(|e| {
        bad(&format!(
            "driver.operate takes driver, action and args, nothing else: {e}; remedy: vk driver act \
             NAME ACTION [--args JSON]"
        ))
    })?;
    let plan = lock(kernel)?
        .driver_operate_begin(&ctx, &driver, &action, &args)
        .map_err(kerr)?;
    let stop_kernel = kernel.clone();
    let should_stop = move || {
        stop_kernel
            .lock()
            .map(|k| k.stopped_scopes().iter().any(|s| s == "node"))
            .unwrap_or(false)
    };
    let acted = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        run_act(kernel, pool, &plan.act, &should_stop)
    }));
    let outcome = acted.unwrap_or_else(|_| {
        pool.clear_acting(&plan.act.name);
        Err(DriverFailure::Panicked)
    });
    if let Err(failure) = &outcome {
        report_failure(&plan.act, failure);
    }
    let mut answer = lock(kernel)?
        .driver_operate_settle(&ctx, now_ms(), &plan, outcome)
        .map_err(kerr)?;
    answer["driver"] = json!(driver);
    answer["action"] = json!(action);
    Ok(answer)
}
```

`server.rs` — in `takes_presence` (`:731-766`), after `| "driver.learn"` add:

```rust
            // Acting on a mounted driver by hand changes what it drives (founder decision D-28):
            // see the refusal in `dispatch`.
            | "driver.operate"
```

and after the `driver.act` arm (`:1033-1035`) add:

```rust
    // **Acting on a mounted driver by hand is a human act** (founder decision D-28, `vk driver act`):
    // one action of the manifest, with no harness and no arch, changes what the driver drives — a
    // document, a page. Verified here, before the general lock, as the mount is; the act then runs
    // in `driver.act`'s three phases, the kernel lock released while the driver works.
    if req.method == "driver.operate" {
        let ctx = {
            let k = lock(kernel)?;
            ctx_for(&k, presence.take(), now)?
        };
        if !ctx.principal.is_human() {
            return Err(invariant(
                "I1: acting on a driver by hand changes what it drives, which is a human act; re-run \
                 it with a presence proof",
            ));
        }
        return crate::drivers::operate(kernel, &config.drivers, ctx, req.params);
    }
```

```powershell
cargo test --locked -p vk-ipc --test drivers -- --nocapture
```

Expected: the two new tests pass, and the file's other tests too.

- [ ] **Step 6: The CLI verb and its rendering** (`crates/vk-cli`)

The failing render test, in `render.rs`'s `mod tests`:

```rust
    /// `vk driver act` (founder decision D-28): how the act went, its result as compact JSON, then
    /// each evidence file with its hash and the human read that releases it.
    #[test]
    fn vk_driver_act_says_how_it_went_and_where_the_evidence_is() {
        let ok = operated(&json!({"driver": "word-maison", "action": "appliquer_style_maison",
            "ok": true, "reason": null, "result": {"text": "appliquer_style_maison ok"},
            "task_id": "task-node-1-7",
            "evidence": [{"name": "post.json", "hash": "sha256:ab", "bytes": 214}]}));
        assert_eq!(
            ok,
            "acted word-maison appliquer_style_maison: ok\n\
             result {\"text\":\"appliquer_style_maison ok\"}\n\
             evidence post.json sha256:ab (214 bytes; vk artefact cat sha256:ab)"
        );
        let refused = operated(&json!({"driver": "word-maison", "action": "appliquer_style_maison",
            "ok": false, "reason": "precondition", "result": null, "task_id": "task-node-1-8",
            "evidence": []}));
        assert_eq!(
            refused,
            "acted word-maison appliquer_style_maison: refused precondition\nresult null"
        );
    }
```

`cargo test --locked -p vk-cli --lib vk_driver_act` fails (`cannot find function operated`). Then, in `render.rs` after `unmounted_driver` (`:1359-1361`):

```rust
/// `vk driver act` (founder decision D-28): `ok`, or the driver's refusal word; the action's result
/// as compact JSON; then each evidence file, its hash and size, and the human read that releases it.
pub fn operated(v: &Value) -> String {
    let what = format!("acted {} {}", text(&v["driver"]), text(&v["action"]));
    let mut lines = vec![if v["ok"] == Value::Bool(true) {
        format!("{what}: ok")
    } else {
        format!("{what}: refused {}", text(&v["reason"]))
    }];
    lines.push(format!("result {}", v["result"]));
    for e in array(v, "evidence") {
        lines.push(format!(
            "evidence {} {} ({} bytes; vk artefact cat {})",
            text(&e["name"]),
            text(&e["hash"]),
            e["bytes"],
            text(&e["hash"])
        ));
    }
    lines.join("\n")
}
```

`main.rs` — the `Driver` command's doc (`:104-106`) becomes `/// The drivers a harness step may act through, or an operator by hand (vk driver act): written ones that drive a browser, and learned ones that drive Word or PowerPoint (vk driver learn learns one, vk mount driver mounts either).` (wrapped as the file wraps its doc comments). In `enum DriverCmd`, after `Umount { name: String },` (`:539`):

```rust
    /// Act on a mounted driver by hand: one action of its manifest, its arguments as a JSON object,
    /// with no harness and no arch (founder decision D-28). A human act, signed with this node's
    /// device key; `driver.operated` goes on the ledger, and the evidence into a register of its own
    /// (`vk artefact cat HASH` reads it). Paths in `--args` are absolute: the driver runs in its own
    /// work directory.
    Act {
        name: String,
        action: String,
        /// The action's arguments, a JSON object (`{}` when absent).
        #[arg(long, value_name = "JSON")]
        args: Option<String>,
    },
```

and after the `Umount` arm (`:1272-1282`):

```rust
        // Acting on a mounted driver by hand changes what it drives — a document, a page: a human
        // act (founder decision D-28), signed with this node's device key like the mount.
        Cmd::Driver {
            what: DriverCmd::Act { name, action, args },
        } => {
            let args: Value = match args {
                Some(text) => serde_json::from_str(&text).map_err(|e| {
                    anyhow!(
                        "--args is not JSON ({e}); remedy: --args '{{\"document\": \
                         \"C:\\\\dossier\\\\contrat.docx\"}}'"
                    )
                })?,
                None => json!({}),
            };
            let proof = presence(&c).await?;
            show(
                cli,
                c.call(
                    "driver.operate",
                    json!({ "driver": name, "action": action, "args": args }),
                    Some(proof),
                )
                .await?,
                render::operated,
            )
        }
```

The smoke test (`crates/vk-cli/tests/smoke.rs`, `vk_forge_learn_then_qualify_then_mount_from_the_cli`): after `assert_eq!(row["manifest"]["kind"], "com", "{row}");` (`:2784`) add:

```rust
    // Founder decision D-28: one action by hand, no harness, no arch. A document that is not there:
    // the generated driver refuses `precondition` before it reaches Office, so no application starts.
    let args = serde_json::json!({
        "document": dir.path().join("absent.docx"),
        "pdf": dir.path().join("absent.pdf"),
    })
    .to_string();
    let acted = sh.ok(&[
        "driver",
        "act",
        "word-maison",
        "appliquer_style_maison",
        "--args",
        args.as_str(),
    ]);
    assert!(
        acted.starts_with("acted word-maison appliquer_style_maison: refused precondition"),
        "{acted}"
    );
```

and the order assertion (`:2793-2796`) becomes:

```rust
    assert!(
        at("driver.learned") < at("driver.qualified")
            && at("driver.qualified") < at("driver.mounted")
            && at("driver.mounted") < at("driver.operated")
    );
```

```powershell
cargo test --locked -p vk-cli --lib -- --nocapture
cargo test --locked -p vk-cli --test smoke vk_forge_ -- --nocapture
```

- [ ] **Step 7: Docs**

`contracts/tcb.md:249` — in **Supervision of a learned driver**, `each `vk_driver_act` being a job step under the harness` becomes `each `vk_driver_act` being a job step under the harness, and each `vk driver act` a human act the operator signs`; after that bullet add:

```markdown
- **An operator may act on a mounted driver by hand (founder decision D-28).** `vk driver act NAME ACTION [--args JSON]` is a human act under a presence proof: one action the manifest lists, with no harness and no arch, held to the refusals of `driver.act` that need no harness step — a driver mounted and not degraded, the action's argument schema, the cadence, a STOP, one act at a time — and to the qualification gate before any spawn (D-31). Its evidence goes into a register created for the act (`task.submitted`), at that register's label plus the third-party origin, read with `vk artefact cat` (a release on the ledger); `driver.operated` records the act by hashes, with the device key that signed it.
```

`contracts/README.md:15`, `Five schemas came with the pilot, all generated like the rest:`, becomes `` Five schemas came with the pilot, and a sixth, `driver_operated`, with `vk driver act` (founder decisions D-28 and D-33); all are generated like the rest: `` — ruling PF-9: `driver_operated` is not counted among the pilot's schemas. `:18` becomes:

```markdown
- `driver_mounted`, `driver_acted`, `driver_operated`, `driver_unmounted` — the payloads of the `driver.mounted`, `driver.acted`, `driver.operated` and `driver.unmounted` ledger events (`driver_acted` and `driver_operated` carry hashes and sizes, never text; `driver_operated`, an operator's act by hand, also the device that signed it);
```

`docs/technical/technical-readme.md` — in the command block of **Drivers: a browser this kernel controls** (`:373-379`), after the `vk driver show` line add `vk driver act NAME ACTION --args JSON   # signed; one action by hand: no harness, no arch`; `Six ledger kinds record a driver` (`:422`) becomes `Seven ledger kinds record a driver`, and after the `driver.acted` row (`:427`) add:

```markdown
| `driver.operated` | an operator's act by hand (`vk driver act`, founder decision D-28): `task_id` (the register the evidence went into), `driver_id`, `action`, `args_hash`, `result_hash`, `ok`, `reason` when it failed (the words of `driver.acted`), `duration_ms`, `evidence`, `evidence_bytes`, `hosts_seen`, and `by`, the device key that signed it — a human act |
```

`docs/technical/runbooks/learn-office-driver.md:126` — the sentence `` Seul le rejeu de `qualify` tourne hors harnais, comme enfant de son étage, dans le Job (c'est le seul écart de supervision, `contracts/tcb.md`). `` becomes `` Deux chemins tournent hors harnais : le rejeu de `qualify`, comme enfant de son étage, dans le Job, et `vk driver act` (§5.3), l'acte humain d'un opérateur qui le signe de sa clé d'appareil, enregistré `driver.operated` (`contracts/tcb.md`). `` (ruling PF-9). After §5.2 (`:126`) add:

````markdown
**5.3 Agir à la main : `vk driver act`** (acte humain, décision D-28). Une action du driver monté, sans harnais ni arch — par exemple pour essayer un driver appris sur un vrai document avant de le confier à une recette :
```powershell
vk driver act word-maison appliquer_style_maison --args '{\"document\": \"C:\\dossiers\\contrat.docx\", \"pdf\": \"C:\\dossiers\\contrat.pdf\"}'
```
Windows PowerShell 5.1 retire les guillemets d'un argument passé à un programme : on les écrit `\"`. Attendu : `acted word-maison appliquer_style_maison: ok`, une ligne `result …`, puis `evidence post.json sha256:… (… bytes; vk artefact cat sha256:…)`. Le grand-livre reçoit `driver.operated` : des hashes, et l'appareil qui a signé. Les chemins de `--args` sont absolus : le driver tourne dans son propre répertoire de travail. Un refus du driver s'affiche `refused <mot>` (`precondition` pour un document absent, en lecture seule, ou déjà ouvert dans votre Word).
````

- [ ] **Step 8: The whole workspace, the docs check**

```powershell
cargo test --locked --workspace --no-fail-fast
cargo fmt --all --check
cargo clippy --workspace --all-targets -- -D warnings
python -I scripts/docs/check_docs.py
```

- [ ] **Step 9: Commit**

```bash
git add crates/vk-contracts/src/driver.rs crates/vk-contracts/src/lib.rs crates/vk-contracts/src/ledger.rs contracts/schemas/driver_operated.schema.json contracts/examples/driver_operated/valid/first.json crates/vk-cli/src/man.rs crates/vk-kernel/src/drivers.rs crates/vk-kernel/tests/drivers.rs crates/vk-ipc/src/drivers.rs crates/vk-ipc/src/server.rs crates/vk-ipc/tests/drivers.rs crates/vk-cli/src/main.rs crates/vk-cli/src/render.rs crates/vk-cli/tests/smoke.rs contracts/tcb.md contracts/README.md docs/technical/technical-readme.md docs/technical/runbooks/learn-office-driver.md
git commit -F - <<'MSG'
feat(drivers): vk driver act — one action on a mounted driver by hand, under a presence proof, recorded as driver.operated (D-28, D-33)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UUDLpkwxsZXmLptqCeNL2R
MSG
```

---

### Task 6: the parked minors — M-5, M-6, M-7, M-8, M-14, qualify's log directory, the import walk (Sonnet)

**Model:** Sonnet (seven small, separate fixes; each with its test). Runs after Task 3 (M-7 describes the attached instance once D-04 holds the started one) and after Task 1 (`_prepare` and `_post` carry the salt). **Rulings:** LF-11, LF-12, PF-10 (the Files entry of `debrief.py`).

**Files:**
- Modify: `drivers/vkforge/qualify.py` — module docstring (one sentence), `_prepare` (`:385-404`, M-5), `_post` (`:424-442`: split into `_held` and `_post`), new `_discard` (M-6), `qualify` (`:474-508`: the folders discarded after the record)
- Modify: `drivers/vkforge/debrief.py:19-24` (docstring), `:107-110` (`CUT` gives way to `SHOWN_FIELDS` and `STRUCTURAL`; `REMEDY`, at `:104-106`, stays: every debrief refusal names it — ruling PF-10), `:226-232` (`_shown_line`) — M-8
- Modify: `drivers/vkforge/inventory.py:166-181` (`catalogue_from_fixture`) — M-14
- Modify: `crates/vk-ipc/src/forge.rs` (`qualify`'s doc comment and its `logs`, `:841-843`, `:902`) — qualify's log directory
- Modify: `drivers/vkforge/tests/test_package.py:99-103`, `:112-119`, `:128-149` — the import walk
- Test: `drivers/vkforge/tests/test_qualify.py` (imports: `from unittest import mock`; two tests), `drivers/vkforge/tests/test_debrief.py` (`:273-277` docstring; one test), `drivers/vkforge/tests/test_inventory.py` (one test), `crates/vk-ipc/tests/forge.rs` (`qualify_writes_driver_qualified_and_the_mount_gate_opens`, `:213-321`)
- Docs: `contracts/tcb.md:243`, `:245`, `:250`; `docs/technical/technical-readme.md:713`, `:776-777`; `docs/technical/runbooks/learn-office-driver.md:114-116`, `:186-192`; `docs/technical/runbooks/recette-maskai.md:712`

**Interfaces:**
- Consumes: `mine.mine_file(path, salt=None)`, `qualify._prepare(…, salt=None)` and `qualify._post(post, copy, expected, salt=None)` as Task 1 left them; the adapter's behaviour as Task 3 left it (M-7's sentence).
- Produces: `qualify._held(post: list, obs: list, copy: Path, expected: dict) -> dict`; `qualify._discard(folder: Path) -> None`; `debrief.SHOWN_FIELDS: tuple[str, ...]`, `debrief.STRUCTURAL: tuple[re.Pattern, ...]`; the refusal `fixture <path> is not a catalogue` for a fixture without `types`/`enums`; qualify's logs under `<state_dir>/forge/.qualify/<name>/`; `test_package._imports` sees any receiver.

- [ ] **Step 1: Write the failing tests**

`drivers/vkforge/tests/test_qualify.py` — add `from unittest import mock` to the imports and, to `QualifyTest`:

```python
    def test_the_copies_go_once_the_record_is_written(self):
        """Final review M-6, ruling LF-11: the perturbed-then-repaired copies and their PDFs are
        deleted once qualification.json is written; the record, driver.log, the profile and fake.json
        stay."""
        rec = self.run_qualify()
        self.assertEqual(rec["passes"], 10)
        self.assertEqual(sorted(p.name for p in self.work.iterdir()),
                         ["driver.log", "fake.json", "profile", "qualification.json"])

    def test_a_perturbation_that_breaks_the_package_is_refused_before_any_pass(self):
        """Final review M-5: after the perturbation each copy is read through mine.mine_file, and one
        the miner cannot read is refused — never taken for a copy that holds nothing."""
        def breaks(copy, post, expected):
            copy.write_bytes(b"PK\x03\x04 not a package any more")

        with mock.patch.object(qualify, "_perturb", breaks):
            with self.assertRaises(Refusal) as cm:
                self.run_qualify()
        self.assertIn("copy 0 is no longer a package the miner reads after its perturbation", str(cm.exception))
        self.assertFalse((self.work / "qualification.json").exists())
```

`drivers/vkforge/tests/test_debrief.py` — the docstring of `test_the_evidence_carries_no_answer_no_sent_text_and_no_page_text` (`:274-277`) becomes `"""Ruling L-P57, by allowlist (final review M-8): proposals.json's evidence keeps a journal line's allowlisted fields and the structural notes of its evidence, never « réponse : « », « texte envoyé » or the oracle's must-patterns (« manque : »); a driver.log line keeps the driver's own words; the harness's vk_log notes (the arch's words) are kept as they are."""`, and add to `Debrief17_10Test`:

```python
    def test_a_journal_line_shows_its_allowlisted_fields_and_structural_notes_only(self):
        """Final review M-8, ruling LF-12: L-P57 by allowlist — a product's text quoted in other words,
        or a field a later journal adds, never reaches the evidence; the structural notes do."""
        line = {"ts": "2026-10-20T11:02+02:00", "step": "exec", "case": "N1-1", "verdict": "KO",
                "timing_s": 12.3, "nfr": ["performance"], "answer": "Lisbonne",
                "evidence": "le modèle a dit que Lisbonne est la capitale ; latence 12,3 s > budget 10 s ; "
                            "refus (timeout)"}
        self.assertEqual(json.loads(debrief._shown_line(line)),
                         {"ts": "2026-10-20T11:02+02:00", "step": "exec", "case": "N1-1", "verdict": "KO",
                          "timing_s": 12.3, "evidence": "latence 12,3 s > budget 10 s; refus (timeout)",
                          "nfr": ["performance"]})
        for evidence, shown in (("réponse : « La capitale est Lisbonne. »", "[…]"),
                                ("réponse vide : échec silencieux", "réponse vide : échec silencieux"),
                                ("abandon à 60 s (3 × budget 20 s) : pas de fin de réponse",
                                 "abandon à 60 s (3 × budget 20 s) : pas de fin de réponse"),
                                ("texte envoyé non observé : fuite de PII non vérifiable ; écart d'envoi 2,5 s",
                                 "écart d'envoi 2,5 s")):
            got = json.loads(debrief._shown_line({"ts": "t", "case": "N0-1", "evidence": evidence}))
            self.assertEqual(got["evidence"], shown, evidence)
```

`drivers/vkforge/tests/test_inventory.py` — add to `InventoryTest`:

```python
    def test_a_fixture_without_types_or_enums_is_one_refusal_line(self):
        """Final review M-14: a fixture with `app` but no `types` (or no `enums`, or a type without a
        name) is refused in one line, never a KeyError in run()."""
        d = Path(tempfile.mkdtemp())
        for name, cat in (("no-types.json", {"app": "word", "enums": {}}),
                          ("no-enums.json", {"app": "word", "types": []}),
                          ("types-not-a-list.json", {"app": "word", "types": {}, "enums": {}}),
                          ("nameless-type.json", {"app": "word", "types": [{"kind": "coclass"}], "enums": {}})):
            (d / name).write_text(json.dumps(cat), encoding="utf-8")
            with self.assertRaises(Refusal, msg=name) as cm:
                inventory.run(entry_args(["inventory", "--app", "word", "--out", str(d / "out"),
                                          "--fixture", str(d / name)]))
            self.assertEqual(str(cm.exception), f"fixture {d / name} is not a catalogue; "
                             "remedy: record it with inventory --app word --out DIR on a node with Office")
        self.assertFalse((d / "out").exists(), "nothing is written on a refusal")
```

`drivers/vkforge/tests/test_package.py` — replace `test_the_import_walk_sees_every_form` (`:112-119`) with:

```python
    def test_the_import_walk_sees_every_form(self):
        source = ("import os, socket\nfrom urllib import request\nfrom xmlrpc.client import ServerProxy\n"
                  "__import__('ssl')\nimport importlib\nimportlib.import_module('httpx')\n"
                  "from importlib import import_module\nimport_module('imaplib')\nimport_module(name)\n"
                  "import importlib as il\nil.import_module('poplib')\nimport builtins\n"
                  "builtins.__import__('ftplib')\na.b.import_module('smtplib')\nil.import_module(name)\n")
        found, dynamic = _imports(ast.parse(source))
        self.assertEqual(sorted(n.split(".")[0] for n in found if n.split(".")[0] in NETWORK_MODULES),
                         ["ftplib", "httpx", "imaplib", "poplib", "smtplib", "socket", "ssl", "urllib", "xmlrpc"])
        self.assertEqual(dynamic, [9, 15], "a module named by no constant is refused, whatever names the call")
```

`crates/vk-ipc/tests/forge.rs` — in `qualify_writes_driver_qualified_and_the_mount_gate_opens`, right after the `driver.qualify` call's `.unwrap();` (`:244`) add:

```rust
    // The qualification's logs live where no driver name reaches (`.qualify`), not under
    // `forge/qualify-<name>`, which a learn of a driver named `qualify-word-maison` would clear.
    assert!(node
        .dir()
        .join("forge")
        .join(".qualify")
        .join("word-maison")
        .join("forge-qualify.out")
        .is_file());
    assert!(!node.dir().join("forge").join("qualify-word-maison").exists());
```

- [ ] **Step 2: Run them, see them fail**

```powershell
& $PY -I -m unittest discover -s drivers/vkforge/tests -t . -p test_qualify.py -k copies_go -k breaks_the_package -v
& $PY -I -m unittest discover -s drivers/vkforge/tests -t . -p test_debrief.py -k allowlisted -v
& $PY -I -m unittest discover -s drivers/vkforge/tests -t . -p test_inventory.py -k without_types -v
& $PY -I -m unittest discover -s drivers/vkforge/tests -t . -p test_package.py -k import_walk -v
cargo test --locked -p vk-ipc --test forge -- qualify_writes_driver_qualified --nocapture
```

Expected: the copy folders `0`…`9` still in `work`; the broken copy taken for one that holds nothing (`cannot perturb …` is not raised: every post id reads `False`, the passes then fail instead of a refusal); the journal line shown with its `answer` field and `evidence` cut at the blocklist; `KeyError: 'types'`; `ftplib`, `poplib`, `smtplib` missing from the walk and `dynamic == [9]`; `forge/.qualify/word-maison` absent.

- [ ] **Step 3: M-5 and M-6 in `qualify.py`**

In the module docstring, after `… is refused before any copy (LF-3).` (Task 1's sentence) add: `Once the record is written each pass's folder — the copy, its PDF, its evidence — is deleted (final review M-6, ruling LF-11): the firm's documents do not stay in the state directory.`

Replace `_prepare` with:

```python
def _prepare(files: list, work: Path, post: list, expected: dict, learned_content: str,
             salt: bytes | None = None) -> list:
    """Before the first pass: every document copied to work/<i>/ with its attributes, the copies'
    bytes checked against the corpus the driver was learned from, then each copy perturbed and read
    again through the miner under the learn's salt — a copy the miner can no longer read is refused
    (final review M-5), and a post id still held is refused (L-P52)."""
    copies = []
    for index, source in enumerate(files):
        folder = work / str(index)
        (folder / "evidence").mkdir(parents=True)
        copy = folder / source.name
        shutil.copy2(source, copy)
        copies.append({"copy": copy, "file_sha256": canon.sha256_file(copy)})
    if _content_sha256(c["file_sha256"] for c in copies) != learned_content:
        raise Refusal(*_CORPUS_CHANGED)             # a document edited after the check, before its copy
    for index, c in enumerate(copies):
        _perturb(c["copy"], post, expected)
        try:
            obs = mine.mine_file(c["copy"], salt)["obs"]
        except (Refusal, OSError) as e:
            why = e.what if isinstance(e, Refusal) else (e.strerror or type(e).__name__)
            raise Refusal(f"copy {index} is no longer a package the miner reads after its perturbation ({why})",
                          _PERTURB_REMEDY) from None
        held = next((ident for ident, v in _held(post, obs, c["copy"], expected).items() if v), None)
        if held is not None:
            raise Refusal(f"cannot perturb {held} for qualification", _PERTURB_REMEDY)
        c["perturbed_sha256"] = canon.sha256_file(c["copy"])
    return copies


def _discard(folder: Path) -> None:
    """A pass's folder — the copy, its PDF, its evidence — removed once the record is written (final
    review M-6, ruling LF-11). A read-only copy is made writable first; a folder that cannot go (a
    copy still open in a Word the driver attached to) is one stderr line, never a failed stage: the
    record is already written."""
    def writable(func, path, _exc):
        os.chmod(path, stat.S_IWRITE)
        func(path)

    try:
        shutil.rmtree(folder, onexc=writable)
    except OSError as e:
        sys.stderr.write(f"qualify: copy folder {folder.name} not deleted ({e.strerror or type(e).__name__})\n")
```

Replace `_post` with:

```python
def _held(post: list, obs: list, copy: Path, expected: dict) -> dict:
    """Each post id of the plan, held or not in `obs` — the copy as the miner read it (values compared
    in canonical form, as the planner compares candidates); `pdf_exists` from the PDF beside `copy`."""
    held = {}
    for ident in post:
        if ident == "pdf_exists":
            held[ident] = _is_pdf(copy.with_suffix(".pdf"))
        elif ident in _WHERE and ident in expected:
            kind, where = _WHERE[ident]
            want = canon.dumps(expected[ident])
            held[ident] = any(o["kind"] == kind and o["where"] == where and canon.dumps(o["value"]) == want
                              for o in obs)
        else:
            held[ident] = False
    return held


def _post(post: list, copy: Path, expected: dict, salt: bytes | None = None) -> dict:
    """_held over the copy as the miner reads it again after a pass, under the learn's salt; a copy
    that is no longer a package (the action left none behind it) holds nothing."""
    try:
        obs = mine.mine_file(copy, salt)["obs"]
    except (Refusal, OSError):
        obs = []
    return _held(post, obs, copy, expected)
```

In `qualify`, the last lines (`canon.write(record, work / "qualification.json")` and `return record`, `:507-508`) become:

```python
    canon.write(record, work / "qualification.json")
    for c in copies:                                # LF-11 (M-6): the firm's documents leave the state directory
        _discard(c["copy"].parent)
    return record
```

- [ ] **Step 4: M-8 in `debrief.py`**

The docstring's last sentence (`:22-24`) becomes: `The evidence of proposals.json never carries an answer, a sent text or a document's content (ruling L-P57), by allowlist (final review M-8): a journal line keeps SHOWN_FIELDS and, of its evidence text, the STRUCTURAL notes alone; a driver.log line keeps the driver's own words; the harness's vk_log notes (the arch's words) are kept as they are.`

Replace `:107-110` (the comment and `CUT`) with:

```python
# L-P57 by allowlist (final review M-8, ruling LF-12): a journal line as evidence keeps these fields,
# evidence early (CLIP cuts at 240 characters), and of its evidence text only the structural notes
# the journal script writes in its own words — an empty answer, an abandon, a latency over budget, a
# send gap, a refusal, a page still moving. An answer, a sent text, an oracle's must-pattern, or any
# field a later journal adds never reaches the evidence.
SHOWN_FIELDS = ("ts", "step", "case", "verdict", "timing_s", "evidence", "nfr", "supersedes", "pipeline_ms")
_NUM = r"\d+(?:[.,]\d+)?"
STRUCTURAL = (re.compile(r"réponse vide(?: : échec silencieux)?"),
              re.compile(rf"abandon à {_NUM} s(?: \(3 × budget {_NUM} s\))?(?: : pas de fin de réponse)?"),
              OVER,
              re.compile(rf"écart d'envoi {_NUM} s"),
              REFUSAL,
              LIVE)
```

Replace `_shown_line` (`:226-232`) with:

```python
def _shown_line(e: dict) -> str:
    """A journal line as evidence (L-P57, final review M-8): the allowlisted fields in SHOWN_FIELDS'
    order, and for its evidence text the structural notes alone, in the order they appear — `[…]`
    when it has none."""
    shown = {}
    for key in SHOWN_FIELDS:
        if key == "evidence":
            ev = e.get("evidence")
            if isinstance(ev, str) and ev.strip():
                notes = {}
                for rx in STRUCTURAL:
                    for m in rx.finditer(ev):
                        notes.setdefault(m.start(), m.group(0))
                shown["evidence"] = "; ".join(notes[at] for at in sorted(notes)) or "[…]"
        elif key in e:
            shown[key] = e[key]
    return json.dumps(shown, ensure_ascii=False)
```

`:411`'s comment becomes `# the harness's vk_log notes (the arch's words), kept whole (L-P57)`.

- [ ] **Step 5: M-14 in `inventory.py`**

Replace `catalogue_from_fixture` (`:166-181`) with:

```python
def catalogue_from_fixture(app: str, fixture: Path) -> dict:
    _app(app)                                               # before any file is read
    remedy = f"record it with inventory --app {app} --out DIR on a node with Office"
    fixture = Path(fixture)
    if not fixture.is_file():
        raise Refusal(f"fixture {fixture} not found", remedy)
    try:
        cat = json.loads(fixture.read_text(encoding="utf-8"))
    except (OSError, ValueError):                           # unreadable, not UTF-8, not JSON
        raise Refusal(f"fixture {fixture} is not a catalogue", remedy)
    if not isinstance(cat, dict):
        raise Refusal(f"fixture {fixture} is not a catalogue", remedy)
    if cat.get("app") != app:
        raise Refusal(f"{fixture} is the catalogue of {cat.get('app')}, not of {app}",
                      f"--app {cat.get('app')} or the fixture of {app}")
    types = cat.get("types")
    if not (isinstance(types, list) and all(isinstance(t, dict) and isinstance(t.get("name"), str) for t in types)
            and isinstance(cat.get("enums"), dict)):        # final review M-14: run() and restrict() read both
        raise Refusal(f"fixture {fixture} is not a catalogue", remedy)
    return cat
```

- [ ] **Step 6: qualify's logs out of a driver name's reach** (`crates/vk-ipc/src/forge.rs`)

In `qualify`'s doc comment, `its logs go under `<state_dir>/forge/qualify-<name>/`` becomes `its logs go under `<state_dir>/forge/.qualify/<name>/`, a directory no driver name reaches`; the `logs` line (`:902`) becomes:

```rust
    // `.qualify`, a name no driver can take (`is_driver_name`: a-z, 0-9 and `-`): a `learn` of a
    // driver named `qualify-<name>` clears `<state>/forge/qualify-<name>` and must never reach these
    // logs (final re-review, out-of-scope note 3).
    let logs = state_dir.join("forge").join(".qualify").join(&name);
```

- [ ] **Step 7: The import walk sees any receiver** (`drivers/vkforge/tests/test_package.py`)

The docstring of `test_the_package_opens_no_socket` (`:100-103`) becomes `"""No module of the package imports a network module (NETWORK_MODULES, the list the TCB names), in any form: `import a, socket`, `from socket import x`, `from urllib import request`, a call of `__import__` or `import_module` whatever names it (`importlib.import_module`, an alias of importlib, `builtins.__import__`); and no such call names a module the walk cannot read (final review M-4 and its re-review nit)."""`. Replace `_imports` (`:128-149`) with:

```python
def _imports(tree: ast.AST) -> tuple[list, list]:
    """(the module names `tree` imports, the line numbers of dynamic imports whose module is not a
    string constant): `import`, `from … import`, and any call of a function named `__import__` or
    `import_module`, whatever names it — `importlib.import_module`, an alias of importlib,
    `builtins.__import__` (re-review nit of M-4)."""
    found, dynamic = [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            found.append(node.module or "")
        elif isinstance(node, ast.Call):
            f = node.func
            called = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
            if called not in ("__import__", "import_module"):
                continue
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                found.append(node.args[0].value)
            else:
                dynamic.append(node.lineno)
    return found, dynamic
```

- [ ] **Step 8: Run the tests, see them pass**

```powershell
& $PY -I -m unittest discover -s drivers/vkforge/tests -t .
cargo test --locked -p vk-ipc --test forge -- --nocapture
```

Expected: the whole vkforge suite passes, the 17/10 debrief tests among them (`test_six_proposals_each_carrying_its_evidence_lines` still finds `réponse vide : échec silencieux`, `test_the_evidence_carries_no_answer_no_sent_text_and_no_page_text` still finds `"evidence": "[…]"` for N1-1 — `evidence` comes before `pipeline_ms`, so `CLIP` does not cut it off); the package itself still passes `test_the_package_opens_no_socket`.

- [ ] **Step 9: Docs**

`contracts/tcb.md:243` — `by `import`, `from … import`, `__import__` or `importlib.import_module`, and no dynamic import names its module other than by a constant, pinned by a test` becomes `by `import`, `from … import`, or a call of `__import__` or `import_module` whatever names it (`importlib.import_module`, an alias of `importlib`, `builtins.__import__`), and no such call names its module other than by a constant, pinned by a test`.

`contracts/tcb.md:245` — the passage from `The evidence its proposals carry never holds an answer, …` to `… passes whole.` becomes:

```markdown
The evidence its proposals carry never holds an answer, a sent text or a document's content (ruling L-P57), by allowlist (final review M-8): a journal line keeps its case, step, verdict, timings, NFRs and pipeline durations, and of its evidence text only the structural notes the journal script writes in its own words (an empty answer, an abandon, a latency over budget, a send gap, a refusal, a page still moving); a `driver.log` line keeps the driver's own words before the page's text; the harness's `vk_log` notes — the arch's words, not the operator's — are kept as they are.
```

`contracts/tcb.md:250` — at the end of **Known gaps of the learning path** (after Task 3's sentences) add:

```markdown
Attached to the user's own instance, a pass killed at its deadline leaves the copy it opened there open, hidden, and that instance's `DisplayAlerts` off: the driver died before its `quit` could put them back (final review M-7); the user closes the copy without saving, and Word's alerts come back at its next start.
```

`docs/technical/technical-readme.md:713` — the `qualify` row's "Writes" cell becomes `` `<state_dir>/drivers/<name>/work/qualify/qualification.json`; each pass's copy and PDF are deleted once it is written ``. `:776-777` — `cut before any answer, sent text or document content (ruling L-P57);` becomes `shown through an allowlist of the journal's fields and of the structural notes in its evidence, never an answer, a sent text or document content (ruling L-P57, final review M-8);`.

`docs/technical/runbooks/learn-office-driver.md` — after the §5.1 paragraph that begins `` `fake false` : le rejeu a eu lieu `` (`:114`) add the line: `Les copies rejouées sont effacées dès que `qualification.json` est écrit : sous `<état>\drivers\<nom>\work\qualify\` il ne reste que le rapport, `driver.log` et le profil.` `Les cinq réserves de l'opérateur` (`:186`) becomes `Les six réserves de l'opérateur`, and after reserve 5 (`:192`) add:

```markdown
6. **Attaché à votre Word, un rejeu tué à son délai y laisse sa copie ouverte, cachée, et `DisplayAlerts` coupé.** Le driver tué ne rend pas la main : la copie (sous `<état>\drivers\<nom>\work\qualify\`) reste ouverte dans votre Word, sans fenêtre, et Word n'affiche plus ses alertes. On ferme Word sans enregistrer cette copie ; ses alertes reviennent au prochain démarrage. Un Word lancé par le driver, lui, meurt avec le driver (réserve 2).
```

`docs/technical/runbooks/recette-maskai.md:712` becomes:

```markdown
Les preuves de `proposals.json` ne reprennent jamais le texte d'une réponse, le texte envoyé ni le contenu d'un document (décision L-P57), par liste blanche (revue finale M-8) : d'une ligne du journal il reste le cas, l'étape, le verdict, les durées, les exigences et les nœuds du pipeline, et de son texte de preuve seulement les notes de structure que le script du journal écrit lui-même (réponse vide, abandon, latence au-delà du budget, écart d'envoi, refus, affichage encore en mouvement). Une ligne de `driver.log` n'y garde que les mots du driver avant le premier deux-points (sélecteur, modale, refus, minutage), jamais le texte de la page. Les notes du harnais sont reprises telles quelles : ce sont les lignes `vk_log` que l'arch a écrites pendant le run, ses mots et non les vôtres. `debrief.md` et `debrief.json` ne portent que des identifiants de cas, des renvois `fichier:ligne`, des comptes et des titres de modale.
```

- [ ] **Step 10: The whole suites, the docs check**

```powershell
& $PY -I -m unittest discover -s drivers/vkforge/tests -t .
git diff --exit-code drivers/vkforge/tests/fixtures
cargo fmt --all --check
cargo clippy --workspace --all-targets -- -D warnings
python -I scripts/docs/check_docs.py
```

- [ ] **Step 11: Commit**

```bash
git add drivers/vkforge/qualify.py drivers/vkforge/debrief.py drivers/vkforge/inventory.py drivers/vkforge/tests/test_qualify.py drivers/vkforge/tests/test_debrief.py drivers/vkforge/tests/test_inventory.py drivers/vkforge/tests/test_package.py crates/vk-ipc/src/forge.rs crates/vk-ipc/tests/forge.rs contracts/tcb.md docs/technical/technical-readme.md docs/technical/runbooks/learn-office-driver.md docs/technical/runbooks/recette-maskai.md
git commit -F - <<'MSG'
fix(learning): the parked minors — a perturbed copy re-read through the miner, the copies deleted after the record, debrief evidence by allowlist, a fixture without types refused, qualify's logs out of a driver name's reach, the import walk on any receiver

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UUDLpkwxsZXmLptqCeNL2R
MSG
```

---

## Self-review notes (run by the author before handoff)

**1. Coverage of what was decided.**

| Decision or finding | What it asks | Task |
|---|---|---|
| D-01 | a random 32-byte salt per learn; `<redacted:hmac-sha256:HEX>` tokens; `--salt-file` required on `mine` and `qualify`, `salt=None` in Python for the pinned fixtures (named and proven unchanged); the kernel draws the salt in `forge/<name>/` and passes it to `mine`, `driver.qualify` passes the same file; the salt survives the vault (LF-1); a missing salt is one refusal line; every caller of `mine.redact` accounted for; tests: two salts two tokens, one salt one token, no unsalted token through the kernel, a wrong or missing salt fails closed | 1 |
| D-02 | `plan --vault` rewrites the vault in place with only the tokens of the kept invariants and the resolved ambiguities; the kernel passes it between `plan` and `generate`; test with per-document footers | 2 |
| D-03 | the started instance identified by a Toolhelp32 diff and its parent (exactly one new PID whose parent is the `DcomLaunch` service's process, PF-4; anything else attached); its `#32770` boxes only; attached → closes nothing, refuses `dialog` when a box of the executable is seen, `dialog` and `timeout` still told apart; stub tests off Windows; on the node, a PowerPoint attached and one started (PF-5) | 3 |
| D-04 | the started instance in a kill-on-close Job the adapter creates, its handle held by the driver and never inherited; never an attached instance; a failed assignment logged and tolerated; after `Quit`, the PID waited for what is left of the clean-end window before the handle closes; the fake's same behaviour where it applies; the real-Office kill tests on the node (Word; PowerPoint, PF-5); TCB and runbook sentences | 3 |
| D-35 (2026-10-20) | LF-7 replaced: the started instance survives only within five bounds — a clean end inside `QUIT_WAIT_S`, the driver's own documents closed first without saving, a foreign document identified by a `FullName` absent from the exact list, the hand-over (`Visible` set and read back true, never `UserControl`, PF-12) and its own Job alone disarmed, then `DisplayAlerts` back and every reference released, one stderr line with the PID and the count — in PF-2's order; else D-04 to the letter; one test per bound and one for the order | 3 |
| D-31 (F-1) | where drivers come back after a restart found (they do not at boot; lazily at the first act); `check_qualified` there with the fixtures flag; a failure degrades like `tampered` — the word `unqualified` on the ledger, the gate's sentence in the logs and the act's refusal (PF-6) — and never spawns; held per start of the process, not per act (PF-7); test through the daemon | 4 |
| D-28, D-33 | `vk driver act NAME ACTION [--args JSON]` (+ `--json`), a human act under presence, no harness or arch; IPC method `driver.operate {driver, action, args}`; the render line; tests for the refusals, the answer and the ledger row; the ledger kind `driver.operated`, `by` mandatory, decided by the founder (D-33, LF-10) | 5 |
| M-5, M-6, M-7, M-8, M-14, qualify's log directory, the import walk | the minors batch | 6 |
| PF-2 … PF-12 | the pre-flight scan's rulings, and the controller's reading of D-35's bound (4) (**Amendments of 2026-10-20**) | 2, 3, 4, 5, 6 (PF-8: Task 1's dispatch; PF-12: Task 3) |

Not a task: spec §12 gains one line per decision (D-01, D-02, D-03/D-04/D-35, D-28/D-33, D-31) at the merge, by the controller, as the chantier's own rulings were added; the plan is archived then.

**2. Placeholder scan.** No `TBD`, no "similar to Task N", no test without its code. The `…` that remain are literal: French runbook prose, the `[…]` evidence marker of LF-12, the `<redacted:…>` token families, and the elided private paths of the header.

**3. Names across tasks.** `mine.tokens` (Task 1) is what Task 2's `plan.vault_tokens` walks; `mine.redact(text, salt=None)`, `mine.read_salt(path)`, `mine.mine_file(path, salt=None)` keep one signature from Task 1 to Task 6; `qualify._post(post, copy, expected, salt=None)` keeps its signature when Task 6 splits out `_held(post, obs, copy, expected)`; `qualify._prepare(…, salt=None)` is the same function Task 6 rewrites; `DriverPool::ensure(row, dirs, gate)` and `DriverPool::act(…, should_stop, gate)` (Task 4) are reached by Task 5 only through `run_act(kernel, pool, &plan.act, &should_stop)`, unchanged; `DriverFailure::Unqualified(String)` (Task 4) is matched by `driver_act_settle`'s generic arm and by `driver_operate_settle`'s (Task 5), both recording `reason()` and answering `refusal()` (Task 4, PF-6); `DriverOperatePlan.act: DriverActPlan` is what `run_act` and `report_failure` take; `Node::token_for(&self, drivers: &[&str])` has the same shape in `tests/common/mod.rs` (Task 4) as in `tests/drivers.rs`'s own `Node`; `com.Application`'s `connect` returns a 2-tuple `(application, owned)` everywhere (the test stubs are rewritten in Task 3); `com.own(started, before, after, launcher, hold)` returns an `Owned` or `None`, and `com.pids_of` a `{pid: parent}` map, wherever a test reads them.

**4. Review Focus.** Each line has its test in the task that owns the code: 1 → Task 3 (one per bound of D-35: `test_the_instance_survives_only_a_clean_end_inside_the_window`, `test_the_drivers_own_documents_are_closed_first_and_never_keep_the_instance`, `test_a_document_is_foreign_only_when_the_driver_never_opened_its_name`, `test_the_instance_kept_is_handed_to_the_user_and_only_its_job_is_disarmed`, `test_every_firing_is_one_stderr_line_with_the_pid_and_the_count`; PF-2's `test_the_end_closes_then_lists_then_hands_over_then_disarms`; `test_a_held_process_dies_with_its_holder_and_a_disarmed_one_lives`); 2 → Task 3 (`test_only_a_positively_identified_process_is_owned`, `test_only_the_started_instance_s_boxes_are_closed`, and on the node `test_the_users_powerpoint_is_attached_never_quit_never_held_its_box_left_open`, `test_a_killed_driver_takes_the_powerpoint_it_started`); 3 → Task 1 (`test_the_learns_salt_qualifies_and_any_other_fails_closed`, `qualify_reads_the_learns_salt_and_fails_closed_without_it`); 4 → Task 2 (`VaultTest`, `learn_leaves_no_purged_candidate_in_the_vault_before_generate`); 5 → Task 4 (`the_gate_holds_again_when_an_act_respawns_a_com_driver`).

**5. For the reviewers.**
- Task 3 applies D-35 (LF-7): the started instance outlives a clean end only within the five bounds, in PF-2's order — the disarm comes before the `DisplayAlerts` restore and the release, so a slow restore can no longer leave an armed Job on an instance the end decided to keep; anything short of the disarm leaves the Job to take the instance, and the driver's own documents never keep it.
- Task 3 treats anything short of positive identification as an attach (PF-4): no new process (today's code would `Quit` a PowerPoint the user had open with no presentation), several, or a parent other than `DcomLaunch` (a PowerPoint the user starts at the same moment). The price: an Office the driver started but cannot identify is never quit and outlives the driver (`start: treated as attached: …` in `driver.log`); the in-process real-Word tests therefore run with `VK_DRIVER_APPLICATIONS`, as a host gives it.
- **Bound (4) against the type libraries — settled by ruling PF-12.** The catalogues pinned in `drivers/vkforge/tests/fixtures/` (`catalogue-word.json`, `catalogue-powerpoint.json`) give Word's `Application.UserControl` a get accessor only (the put is `Document.UserControl`'s) and PowerPoint's `Application` no `UserControl` at all, while both give `Application.Visible` a get and a put (Word's a `bool`, PowerPoint's an `MsoTriState`, hence `COM_TRUE = -1`). A hand-over that set `UserControl` would fail on the real Word and name a missing member on PowerPoint, so the exception could never fire. PF-12 drops `UserControl` — a visible instance outside any Job, with no automation reference left, is the user's — and keeps the strictness on `Visible`: set, then read back, and only `True` (comtypes, 1.4.17 in the node's venv, converts Word's `VARIANT_BOOL` to Python's `bool`) or `COM_TRUE` (PowerPoint's `msoTrue`) lets the disarm happen. `OrderedWord` reads back either form and, like Word's `Application`, refuses a `UserControl` put, so a hand-over that touched it fails the bound-4 test. No node step exercises the hand-over on the real applications.
- Task 4 gates every spawn, mount included (the mount already checked under the lock just before: the second check costs one hash of a small file), and nothing else: an act a live process serves is not gated (PF-7), and the test shows it.
- Task 5's evidence goes to a register with no task row: `vk task show` does not list it; `vk artefact cat HASH` reads its files, and the `artefact.released` it writes names the register's task id.
- `vk driver act --args` is passed as typed; the runbook gives the Windows PowerShell 5.1 quoting (`\"`).
