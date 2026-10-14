//! Drivers (pilot spec §4.3–4.4): the registry of mounted peripherals and the
//! two kernel-side halves of `driver.act`.
//!
//! A driver is a peripheral — the pilot's is a browser — behind a process the
//! daemon starts, caps and talks to; the harness reaches it only through the
//! `driver.act` syscall. The kernel owns no process. What it owns is the table
//! of what is mounted (`drivers/<name>`, keyed by the manifest's name), the
//! three directories a driver may touch, the seven refusals of §4.3 in order,
//! and the two moments under the lock around the daemon's call —
//! [`RealKernel::driver_act_begin`] before it, [`RealKernel::driver_act_settle`]
//! after. That is the shape of `harness_launch` / `harness_settle`, for the
//! same reason: the call blocks for up to `max_action_ms`, and the harness that
//! issued it holds no kernel lock meanwhile.
//!
//! What reaches the ledger is hashes (P10, Ruling 28): `driver.mounted` names
//! the identity, `driver.acted` the hashes of what was asked and what came
//! back, `driver.unmounted` the reason. The words live in the evidence, under
//! the register's label plus the third-party origin.
use crate::tasks::{
    StepKind, StepStatus, TaskStatus, DEFAULT_MAX_ACTIONS, HARNESS_MAX_FILE_BYTES,
    HARNESS_MAX_RUN_BYTES,
};
use crate::{store_failed, RealKernel};
use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use vk_contracts::driver::{
    DriverActed, DriverAction, DriverManifest, DriverMounted, DriverUnmounted,
};
use vk_contracts::labels::Origin;
use vk_contracts::principal::Principal;
use vk_contracts::register::{Evidence, Register};
use vk_contracts::syscalls::{Ctx, Kernel, KernelError};
use vk_contracts::{hash_bytes, hash_canonical};
use vk_driver::{ActOutcome, EvidenceCaps, HostError};

/// The metadata table of mounted drivers, keyed by the manifest's `name`.
const DRIVERS: &str = "drivers";

/// The reason `boot` degrades a row whose stored manifest no longer validates.
pub const MANIFEST_INVALID_AT_BOOT: &str = "manifest invalid at boot";

/// Whether a mounted driver may be acted on.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case", tag = "state")]
pub enum DriverState {
    Ready,
    /// Not usable until remounted: the host timed out, died or broke protocol
    /// under an action, or the manifest no longer validates at boot. The row
    /// stays, so `vk driver show` can say why.
    Degraded {
        reason: String,
    },
}

/// One mounted driver, as the `drivers` table stores it.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct DriverRow {
    /// The manifest as mounted, with `governed` set to what the host observed
    /// rather than what the file said.
    pub manifest: DriverManifest,
    /// `DriverManifest::driver_id`: the identity, `governed` excluded.
    pub driver_id: String,
    /// The directory the manifest's relative `program.command` resolves
    /// against (Ruling R6). Per machine, so no part of the identity; kept here
    /// for the daemon, which is the only thing that spawns. The kernel neither
    /// resolves nor checks it.
    pub root: PathBuf,
    pub mounted_ms: u64,
    pub state: DriverState,
    /// When this driver's last action settled, successfully or not; `0` for
    /// one that has never acted. What the cadence refusal (§4.3, item 4)
    /// counts from.
    pub last_act_ms: u64,
}

/// The three directories a driver may touch, all under
/// `<state_dir>/drivers/<name>/`: the browser profile (cookies, a session),
/// the driver's scratch, and where it writes the evidence files an action
/// returns. Made private at mount; the evidence root's protection is the
/// kernel's, because a driver that could replace `<evidence>/<id>` with a link
/// would escape the host's final-component check (Ruling R9).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DriverDirs {
    pub profile: PathBuf,
    pub work: PathBuf,
    pub evidence: PathBuf,
}

/// Phase one's answer: everything phase two needs, nothing it must look up.
///
/// The daemon carries this across the call it makes with the lock released,
/// and hands it back to [`RealKernel::driver_act_settle`]. The `action` is the
/// manifest's own, copied, so settle checks the evidence names against the
/// manifest as it was when the act was admitted.
#[derive(Debug, Clone)]
pub struct DriverActPlan {
    pub task_id: String,
    pub step_index: usize,
    /// The driver's mounted name.
    pub name: String,
    pub driver_id: String,
    pub action: DriverAction,
    pub args: serde_json::Value,
    /// `hash_canonical(args)`, what the ledger carries instead of the args.
    pub args_hash: String,
    /// `begun_ms + max_action_ms`: when the host must have answered.
    pub deadline_ms: u64,
    /// The host's ceilings on evidence for this one action. The run's total
    /// is the kernel's, counted on the token at settle.
    pub caps: EvidenceCaps,
    pub dirs: DriverDirs,
    pub begun_ms: u64,
    /// The act's identity inside this process: what `in_flight` holds for the
    /// driver while the act runs, and what the settle must present to touch
    /// the row (review round 2). The settle of a plan begun before an unmount
    /// and remount goes into the record and nowhere else.
    pub act_id: u64,
}

/// The reason string the daemon hands [`RealKernel::driver_act_settle`] for a
/// host failure, from the host's own error. One mapping, here, so the ledger's
/// `reason` reads the same words everywhere: `timeout`, `dead: …`,
/// `protocol: …`, `stopped`, `hello timeout: …`, `evidence …`. A failure the
/// host has no variant for — the pool could not start the program — is the
/// daemon's own text (`driver would not start: …`). The settle degrades the
/// driver on every one of them but the three that start with `evidence`
/// (Ruling R12): those are read back after the action, with the tree alive
/// and in step, and `evidence too large: <name> …` is the one the settle
/// remembers as the run's `oversize`, so the prefix and the name's place in
/// it are load-bearing.
pub fn host_error_reason(e: &HostError) -> String {
    match e {
        HostError::Timeout => "timeout".into(),
        HostError::Dead(why) => format!("dead: {why}"),
        HostError::Protocol(why) => format!("protocol: {why}"),
        HostError::Stopped => "stopped".into(),
        HostError::HelloTimeout(after) => format!("hello timeout: {after:?}"),
        HostError::EvidenceEscape(name) => format!("evidence escape: {name}"),
        HostError::EvidenceTooLarge { name, len, cap } => {
            format!("evidence too large: {name} is {len} bytes, above {cap}")
        }
        HostError::EvidenceNotDeclared(name) => format!("evidence not declared: {name}"),
    }
}

impl RealKernel {
    /// `<state_dir>/drivers/<name>/{profile,work,evidence}`. A path, not a
    /// promise: the directories exist once the driver has been mounted.
    pub fn driver_dirs(&self, name: &str) -> DriverDirs {
        let base = self.store.state_dir.join("drivers").join(name);
        DriverDirs {
            profile: base.join("profile"),
            work: base.join("work"),
            evidence: base.join("evidence"),
        }
    }

    /// Every mounted driver, by name. A store that cannot list them fails the
    /// read (Ruling 43): an empty answer would say nothing is mounted.
    pub fn drivers(&self) -> Result<Vec<DriverRow>, KernelError> {
        Ok(self
            .store
            .db
            .list_json::<DriverRow>(DRIVERS)
            .map_err(store_failed)?
            .into_iter()
            .map(|(_, row)| row)
            .collect())
    }

    pub fn driver(&self, name: &str) -> Result<Option<DriverRow>, KernelError> {
        self.store.db.get_json(DRIVERS, name).map_err(store_failed)
    }

    /// Mount a driver: a human act (I1), as `vk mount anthropic` is.
    ///
    /// The manifest is validated, `governed` is set to what the host observed,
    /// the three directories are made private, the row is written and
    /// `driver.mounted` logged with `by` = the human's device. A repeat mount
    /// of the same `driver_id` over a `Ready` row is idempotent (Ruling 13):
    /// the row comes back and nothing is logged. Over a `Degraded` row it is a
    /// real mount — that is the remedy the degrade refusal names — and the
    /// cadence clock is kept, because the site's rate limit did not reset when
    /// this node's process died; an act left in flight on the dead process is
    /// forgotten with it. A different manifest under the same name is refused
    /// with its remedy: unmount first.
    ///
    /// `profile_dir_hash` on the event is the hash of the profile directory's
    /// *path*, not of its contents (Ruling R11): a browser profile changes on
    /// every run and a populated one is hundreds of MB with locked files.
    pub fn mount_driver(
        &mut self,
        ctx: &Ctx,
        mut manifest: DriverManifest,
        root: PathBuf,
        observed_governed: bool,
    ) -> Result<DriverRow, KernelError> {
        let by = match &ctx.principal {
            Principal::Human { device_id } => device_id.clone(),
            Principal::Machine { .. } => {
                return Err(KernelError::I1(
                    "mounting a driver is a human act: run it from an enrolled device".into(),
                ))
            }
        };
        manifest
            .validate()
            .map_err(|e| KernelError::Gate(e.to_string()))?;
        manifest.governed = observed_governed;
        let driver_id = manifest.driver_id();
        let name = manifest.name.clone();
        let existing = self.driver(&name)?;
        if let Some(row) = &existing {
            if row.driver_id != driver_id {
                return Err(KernelError::Gate(format!(
                    "driver {name} is already mounted with a different manifest; \
                     run `vk driver umount {name}` first"
                )));
            }
            if row.state == DriverState::Ready {
                return Ok(row.clone());
            }
        }
        let dirs = self.driver_dirs(&name);
        for dir in [&dirs.profile, &dirs.work, &dirs.evidence] {
            vk_store::paths::private_dir(dir).map_err(store_failed)?;
        }
        let profile_dir_hash = hash_bytes(dirs.profile.to_string_lossy().as_bytes());
        let row = DriverRow {
            manifest,
            driver_id: driver_id.clone(),
            root,
            mounted_ms: ctx.now_ms,
            state: DriverState::Ready,
            last_act_ms: existing.map(|r| r.last_act_ms).unwrap_or(0),
        };
        // Ledger before the row, as everywhere else in this kernel.
        self.log(
            "driver.mounted",
            ctx.now_ms,
            &DriverMounted {
                driver_id,
                name: name.clone(),
                manifest_hash: hash_canonical(&row.manifest),
                profile_dir_hash,
                governed: observed_governed,
                by,
            },
        )?;
        self.store
            .db
            .put_json(DRIVERS, &name, &row)
            .map_err(store_failed)?;
        // A fresh row starts with nothing in flight: whatever the previous
        // process was doing died with it, and its settle, if one still comes,
        // presents an act id this row never held. After the row is written, so
        // a mount that failed leaves the live act guarded.
        self.in_flight.remove(&name);
        Ok(row)
    }

    /// Drop a driver's row and log `driver.unmounted{reason}`. `None` for a
    /// name that is not mounted, with nothing logged. An act still in flight
    /// goes with the row (review round 1, finding 4): the daemon drops the
    /// host at unmount, so a settle that never comes must not hold the name
    /// busy for ever — and one that does come presents an act id nothing
    /// holds any more, and is stale. The flag goes once the row is gone, so
    /// an unmount that failed leaves the live act guarded (round 1, minor 3).
    /// The directories stay: the profile is a browser session an operator may
    /// want back on the next mount, and the evidence files are already blobs.
    pub fn unmount_driver(
        &mut self,
        name: &str,
        reason: &str,
    ) -> Result<Option<DriverRow>, KernelError> {
        let Some(row) = self.driver(name)? else {
            return Ok(None);
        };
        self.log(
            "driver.unmounted",
            crate::now_ms(),
            &DriverUnmounted {
                driver_id: row.driver_id.clone(),
                reason: reason.into(),
            },
        )?;
        self.store.db.delete(DRIVERS, name).map_err(store_failed)?;
        self.in_flight.remove(name);
        Ok(Some(row))
    }

    /// Mark a driver `Degraded{reason}` and log `driver.unmounted{reason}`:
    /// the process behind it is gone or untrusted, and the row stays so the
    /// refusal every later act gets can say why and name the remedy. A driver
    /// already degraded keeps its first reason and logs nothing more.
    pub fn degrade_driver(&mut self, name: &str, reason: &str) -> Result<(), KernelError> {
        let mut row = self
            .driver(name)?
            .ok_or_else(|| KernelError::NotFound(format!("driver {name}")))?;
        if matches!(row.state, DriverState::Degraded { .. }) {
            return Ok(());
        }
        self.log(
            "driver.unmounted",
            crate::now_ms(),
            &DriverUnmounted {
                driver_id: row.driver_id.clone(),
                reason: reason.into(),
            },
        )?;
        row.state = DriverState::Degraded {
            reason: reason.into(),
        };
        self.store
            .db
            .put_json(DRIVERS, name, &row)
            .map_err(store_failed)
    }

    /// `boot`: every stored manifest must still validate, or its row is
    /// degraded with [`MANIFEST_INVALID_AT_BOOT`]. Nothing is spawned — the
    /// daemon owns the processes — and a row already degraded is left as it
    /// is, so a second boot adds nothing.
    pub(crate) fn degrade_invalid_drivers(&mut self) -> Result<Vec<String>, KernelError> {
        let mut degraded = Vec::new();
        for row in self.drivers()? {
            if row.state == DriverState::Ready && row.manifest.validate().is_err() {
                self.degrade_driver(&row.manifest.name, MANIFEST_INVALID_AT_BOOT)?;
                tracing::warn!(
                    driver = %row.manifest.name,
                    "a mounted driver's manifest no longer validates; degraded, remount it"
                );
                degraded.push(row.manifest.name);
            }
        }
        Ok(degraded)
    }

    /// Phase one of `driver.act`, under the kernel lock: the seven refusals of
    /// spec §4.3, in order, then one more of the kernel's own — a second act on
    /// a driver whose previous act has not settled — then the token's `actions`
    /// counter moves and the plan is returned. The daemon acts with the lock
    /// released and calls [`RealKernel::driver_act_settle`].
    ///
    /// Each refusal names its remedy and leaves nothing behind: no counter
    /// moves, nothing is in flight, nothing is logged. The action is on the
    /// ledger once it has happened, at settle.
    pub fn driver_act_begin(
        &mut self,
        token: &str,
        now_ms: u64,
        name: &str,
        action: &str,
        args: &serde_json::Value,
    ) -> Result<DriverActPlan, KernelError> {
        // 1. The token names a live lease on a `Harness` step that is `Running`.
        let session = self.harness_session(token, now_ms)?;
        let key = RealKernel::harness_token_key(token);
        let (step_index, actions, declared) = {
            let state = self
                .harness_tokens
                .get(&key)
                .ok_or_else(|| KernelError::I1("unknown harness token".into()))?;
            (state.step_index, state.actions, state.drivers.clone())
        };
        let task = self
            .task_row(&session.task_id)?
            .ok_or_else(|| KernelError::NotFound(session.task_id.clone()))?;
        let running = task
            .steps
            .get(step_index)
            .filter(|s| s.status == StepStatus::Running)
            .map(|s| &s.kind);
        let max_actions = match running {
            Some(StepKind::Harness { max_actions, .. }) => {
                max_actions.unwrap_or(DEFAULT_MAX_ACTIONS)
            }
            _ => return Err(KernelError::Gate("the harness step is not running".into())),
        };
        // 2. The step declared the driver; the driver is mounted and not degraded.
        if !declared.iter().any(|d| d == name) {
            return Err(KernelError::Gate(format!(
                "step {step_index} of task {} did not declare driver {name}",
                task.id
            )));
        }
        let row = self
            .driver(name)?
            .ok_or_else(|| KernelError::NotFound(format!("driver {name}")))?;
        if let DriverState::Degraded { reason } = &row.state {
            return Err(KernelError::Gate(format!(
                "driver {name} is degraded: {reason}; remount it"
            )));
        }
        // 3. The action is in the manifest and the args fit its schema.
        let act = row
            .manifest
            .action(action)
            .ok_or_else(|| KernelError::Gate(format!("driver {name} has no action {action}")))?
            .clone();
        act.validate_args(args)
            .map_err(|e| KernelError::Gate(format!("args for {action}: {e}")))?;
        // 4. The cadence: `min_interval_ms` since this driver's last settle.
        // `last_act_ms == 0` is a driver that has never acted, not one that
        // acted at the epoch.
        if row.last_act_ms > 0 {
            let since = now_ms.saturating_sub(row.last_act_ms);
            if since < row.manifest.min_interval_ms {
                return Err(KernelError::Gate(format!(
                    "driver {name}: {} ms before the next action",
                    row.manifest.min_interval_ms - since
                )));
            }
        }
        // 5. The step's own ceiling on actions.
        if actions >= max_actions {
            return Err(KernelError::Gate(format!(
                "step {step_index} reached its {max_actions} actions"
            )));
        }
        // 6. A STOP halts the driver exactly as it halts the scheduler.
        if matches!(task.status, TaskStatus::Stopped) || self.stops.stopped("node") {
            return Err(KernelError::Stopped("node".into()));
        }
        // 7. The register's label flows to the driver's clearance (I2). The
        // row as stored, not `read_register`: this asks whether the *driver*
        // may see the task, and the answer must name the driver, not the
        // harness that happens to be asking.
        let reg: Register = self
            .store
            .db
            .get_json("registers", &task.register.0)
            .map_err(store_failed)?
            .ok_or_else(|| KernelError::NotFound(task.register.0.clone()))?;
        if !reg.label.flows_to(&row.manifest.clearance) {
            return Err(KernelError::I2(format!(
                "register of task {} exceeds driver {name}'s clearance",
                task.id
            )));
        }
        // One action per driver at a time: the process behind it answers one
        // request at a time, and a second one would wait on the first's
        // deadline with nothing to show for it.
        if self.in_flight.contains_key(name) {
            return Err(KernelError::Gate(format!(
                "driver {name} has an action in flight; wait for it to settle"
            )));
        }
        self.act_counter += 1;
        let act_id = self.act_counter;
        self.in_flight.insert(name.to_string(), act_id);
        if let Some(state) = self.harness_tokens.get_mut(&key) {
            state.actions += 1;
        }
        Ok(DriverActPlan {
            task_id: task.id,
            step_index,
            name: name.into(),
            driver_id: row.driver_id,
            action: act,
            args: args.clone(),
            args_hash: hash_canonical(args),
            deadline_ms: now_ms.saturating_add(row.manifest.max_action_ms),
            caps: EvidenceCaps {
                per_file: HARNESS_MAX_FILE_BYTES,
                per_action: HARNESS_MAX_RUN_BYTES / 4,
            },
            dirs: self.driver_dirs(name),
            begun_ms: now_ms,
            act_id,
        })
    }

    /// Phase two of `driver.act`, under the kernel lock, once the daemon has
    /// the host's answer.
    ///
    /// **The settle is matched to its act first**: the plan's `act_id` must be
    /// the one `in_flight` holds for the driver, or this is the settle of an
    /// act begun before an unmount and remount, arriving up to `max_action_ms`
    /// later (review round 2). A stale settle still logs `driver.acted` — the
    /// site was acted on — and then returns `Gate("stale act: …")` without
    /// touching the new row, its clock, its state, or the flag the live act
    /// holds. A live settle frees the driver before anything can fail.
    /// Then, on `Ok(outcome)`: each evidence
    /// file becomes an artefact under its own name, at the register's label
    /// plus `Origin::ThirdPartyInbound`, counted against the run's 16 MiB
    /// (Ruling 21.6; a breach is refused and remembered on the token, so the
    /// harness step fails naming the file); `result.text` is appended to the
    /// register's evidence when the action says `to_register`; `driver.acted`
    /// is logged with hashes only; the row's `last_act_ms` moves; the harness
    /// gets `result` and never the evidence bytes. On `Err(reason)` — the
    /// daemon's [`host_error_reason`], or its own words for a process that
    /// would not start — `driver.acted{ok:false, reason}` is logged and the
    /// row's clock moves. Then Ruling R12: the driver is degraded **unless the
    /// reason is one of the host's evidence refusals** (`evidence …`) — an
    /// escaping, oversize or undeclared file is read back *after* the action,
    /// with the tree alive and in step, where a process that would not start,
    /// printed no hello, timed out, died or broke protocol is not one to send
    /// the next request to. The host's too-large refusal is remembered on the
    /// token as `oversize`, exactly as the door below does, so the run fails
    /// naming the file (Ruling 21.6). The harness gets `Gate(reason)`. A
    /// token that no longer resolves — the run settled while the act was out
    /// — gets its I1 refusal back, after `driver.acted{ok:false}` has said
    /// the action happened.
    ///
    /// The acted event comes after the attachments because it names their
    /// hashes — the order `harness_settle` keeps between collection and its
    /// `task.step`. It comes before the row, as every event does.
    pub fn driver_act_settle(
        &mut self,
        token: &str,
        now_ms: u64,
        plan: &DriverActPlan,
        outcome: Result<ActOutcome, String>,
    ) -> Result<serde_json::Value, KernelError> {
        let live = self.in_flight.get(&plan.name) == Some(&plan.act_id);
        if live {
            self.in_flight.remove(&plan.name);
        }
        let key = RealKernel::harness_token_key(token);
        let base = DriverActed {
            task_id: plan.task_id.clone(),
            step_index: plan.step_index,
            driver_id: plan.driver_id.clone(),
            action: plan.action.name.clone(),
            args_hash: plan.args_hash.clone(),
            result_hash: hash_canonical(&serde_json::Value::Null),
            ok: false,
            reason: None,
            duration_ms: now_ms.saturating_sub(plan.begun_ms),
            evidence: vec![],
            evidence_bytes: 0,
            hosts_seen: vec![],
        };

        if !live {
            // The act this settle closes was begun before the driver was
            // unmounted and mounted again. The site was acted on, so the record
            // says so — with the host's reason, or with the staleness when
            // there was an outcome nothing is left to receive — and nothing
            // else moves: the row, its clock, its state and the flag belong to
            // the act that is live now.
            let stale = "stale act: the driver was remounted since";
            let record = match &outcome {
                Ok(o) => DriverActed {
                    result_hash: hash_canonical(&o.result),
                    reason: Some(stale.into()),
                    duration_ms: o.duration_ms,
                    hosts_seen: o.hosts_seen.clone(),
                    ..base
                },
                Err(reason) => DriverActed {
                    reason: Some(reason.clone()),
                    ..base
                },
            };
            self.log("driver.acted", now_ms, &record)?;
            return Err(KernelError::Gate(stale.into()));
        }

        let outcome = match outcome {
            Ok(outcome) => outcome,
            Err(reason) => {
                self.log(
                    "driver.acted",
                    now_ms,
                    &DriverActed {
                        reason: Some(reason.clone()),
                        ..base
                    },
                )?;
                if let Some(rest) = reason.strip_prefix("evidence") {
                    // The host read the files back after the action, with the
                    // tree alive and in step: nothing to degrade (Ruling R12).
                    // Its too-large refusal is the door's, remembered the same
                    // way, so the run fails naming the file (Ruling 21.6).
                    let too_large = rest
                        .strip_prefix(" too large: ")
                        .and_then(|r| r.split(" is ").next())
                        .filter(|name| !name.is_empty());
                    if let Some(file) = too_large {
                        if let Some(state) = self.harness_tokens.get_mut(&key) {
                            state.oversize.get_or_insert(file.to_string());
                        }
                    }
                } else {
                    // Every other host failure degrades (Ruling R12). A driver
                    // unmounted while its act was in flight has no row to
                    // degrade, and that is not an error of this settle.
                    match self.degrade_driver(&plan.name, &reason) {
                        Ok(()) | Err(KernelError::NotFound(_)) => {}
                        Err(e) => return Err(e),
                    }
                }
                self.touch_driver(&plan.name, now_ms)?;
                return Err(KernelError::Gate(reason));
            }
        };

        // What the host handed back is held to the manifest and to the run's
        // caps before any of it is kept: a name the action did not declare, a
        // file past the per-file cap, or a total that would take the run past
        // its limit refuses the whole action's evidence. The breach is
        // remembered on the token, exactly as `harness_attach_file` does, so
        // the harness step fails at settle naming the file.
        let total: u64 = outcome.evidence.iter().map(|e| e.bytes.len() as u64).sum();
        let attached_so_far = self
            .harness_tokens
            .get(&key)
            .map(|s| s.bytes_attached)
            .unwrap_or(0);
        let undeclared = outcome
            .evidence
            .iter()
            .find(|e| !plan.action.evidence.contains(&e.name))
            .map(|e| {
                format!(
                    "evidence {} is not a name the manifest allows for {}",
                    e.name, plan.action.name
                )
            });
        let oversize = outcome
            .evidence
            .iter()
            .find(|e| e.bytes.len() as u64 > HARNESS_MAX_FILE_BYTES)
            .map(|e| {
                (
                    e.name.clone(),
                    format!(
                        "{} is {} bytes, above the {HARNESS_MAX_FILE_BYTES}-byte limit for one file",
                        e.name,
                        e.bytes.len()
                    ),
                )
            })
            .or_else(|| {
                (attached_so_far + total > HARNESS_MAX_RUN_BYTES).then(|| {
                    (
                        outcome
                            .evidence
                            .first()
                            .map(|e| e.name.clone())
                            .unwrap_or_default(),
                        format!(
                            "evidence of {} ({total} bytes) would take the run past the \
                             {HARNESS_MAX_RUN_BYTES}-byte limit",
                            plan.action.name
                        ),
                    )
                })
            });
        if let Some((file, _)) = &oversize {
            if let Some(state) = self.harness_tokens.get_mut(&key) {
                state.oversize.get_or_insert(file.clone());
            }
        }
        if let Some(why) = undeclared.or(oversize.map(|(_, why)| why)) {
            self.log(
                "driver.acted",
                now_ms,
                &DriverActed {
                    result_hash: hash_canonical(&outcome.result),
                    reason: Some(why.clone()),
                    duration_ms: outcome.duration_ms,
                    hosts_seen: outcome.hosts_seen.clone(),
                    ..base
                },
            )?;
            self.touch_driver(&plan.name, now_ms)?;
            return Err(KernelError::Gate(why));
        }

        // The attachments, on the harness's behalf: the session that issued
        // the act, at the harness clearance, as `harness_attach_file` does. A
        // token that stopped resolving while the act ran — the harness run
        // timed out and settled with the browser still working — leaves the
        // action on the record all the same (Ruling 21.4: every exit path
        // settles): the site was acted on, and the evidence goes nowhere
        // because no run is left to receive it.
        let session = match self.harness_session(token, now_ms) {
            Ok(session) => session,
            Err(e) => {
                self.log(
                    "driver.acted",
                    now_ms,
                    &DriverActed {
                        result_hash: hash_canonical(&outcome.result),
                        reason: Some(format!("harness run ended before the action settled: {e}")),
                        duration_ms: outcome.duration_ms,
                        hosts_seen: outcome.hosts_seen.clone(),
                        ..base
                    },
                )?;
                self.touch_driver(&plan.name, now_ms)?;
                return Err(e);
            }
        };
        let reg = self.read_register(&session.ctx, &session.register)?;
        let mut label = reg.label.clone();
        label.origins.insert(Origin::ThirdPartyInbound);
        let mut hashes = Vec::with_capacity(outcome.evidence.len());
        for file in &outcome.evidence {
            let env = self.attach_artefact_labelled(
                &session.ctx,
                &session.register,
                &file.name,
                &file.bytes,
                Some(label.clone()),
            )?;
            hashes.push(env.hash);
            if let Some(state) = self.harness_tokens.get_mut(&key) {
                state.bytes_attached += file.bytes.len() as u64;
            }
        }
        // `to_register`: the result's text is what a later Judge or Draft arch
        // reads (a journal export, an oracle verdict). Only a successful
        // action's, and only when there is some: a failed action's words are
        // a reason, not evidence. The register read third-party text, so its
        // label says so.
        if plan.action.to_register && outcome.ok {
            let text = outcome
                .result
                .get("text")
                .and_then(serde_json::Value::as_str)
                .filter(|t| !t.trim().is_empty());
            if let Some(text) = text {
                let mut reg = self.read_register(&session.ctx, &session.register)?;
                reg.evidence.push(Evidence {
                    content: text.into(),
                    origin: Origin::ThirdPartyInbound,
                    source_hash: hash_canonical(&text),
                });
                reg.with_read(&label);
                self.write_register(&session.ctx, reg)?;
            }
        }
        self.log(
            "driver.acted",
            now_ms,
            &DriverActed {
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
        self.touch_driver(&plan.name, now_ms)?;
        Ok(outcome.result)
    }

    /// Move a driver's cadence clock to `now_ms`. A driver unmounted while
    /// its act was in flight has no row, and that is fine: the settle has
    /// nothing left to say about it.
    fn touch_driver(&mut self, name: &str, now_ms: u64) -> Result<(), KernelError> {
        if let Some(mut row) = self.driver(name)? {
            row.last_act_ms = now_ms;
            self.store
                .db
                .put_json(DRIVERS, name, &row)
                .map_err(store_failed)?;
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The ledger's words for each host failure: short, stable, and never
    /// carrying anything but the host's own message.
    #[test]
    fn host_error_reasons_are_short_stable_words() {
        assert_eq!(host_error_reason(&HostError::Timeout), "timeout");
        assert_eq!(host_error_reason(&HostError::Stopped), "stopped");
        assert_eq!(
            host_error_reason(&HostError::Dead("code 1".into())),
            "dead: code 1"
        );
        assert_eq!(
            host_error_reason(&HostError::Protocol("no reply".into())),
            "protocol: no reply"
        );
        assert!(
            host_error_reason(&HostError::HelloTimeout(std::time::Duration::from_secs(3)))
                .starts_with("hello timeout: ")
        );
        assert_eq!(
            host_error_reason(&HostError::EvidenceNotDeclared("x.png".into())),
            "evidence not declared: x.png"
        );
        assert_eq!(
            host_error_reason(&HostError::EvidenceEscape("x.png".into())),
            "evidence escape: x.png"
        );
        assert_eq!(
            host_error_reason(&HostError::EvidenceTooLarge {
                name: "x".into(),
                len: 2,
                cap: 1
            }),
            "evidence too large: x is 2 bytes, above 1"
        );
    }
}
