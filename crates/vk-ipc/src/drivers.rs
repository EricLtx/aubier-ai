//! The daemon's drivers (pilot spec §4.2–4.4): the pool of driver processes
//! this daemon owns, and the two `driver.*` syscalls that cannot run under the
//! kernel lock — `driver.mount`, which starts a process, and `driver.act`,
//! which waits on one.
//!
//! The kernel owns the table of what is mounted and the two moments around an
//! act (`driver_act_begin` before it, `driver_act_settle` after); it owns no
//! process. The processes are here: one [`DriverHost`] per mounted name,
//! spawned by [`DriverPool::ensure`] under this platform's governor — a Job
//! Object on Windows, 2 GiB, no CPU cap, failing closed as a harness does —
//! and driven by [`DriverPool::act`] with neither the kernel lock nor the
//! pool's map held across the action: an act takes its host out of the map,
//! acts on the owned value and puts it back. That is the shape of
//! `harness.run`, for the same reason: an action lasts seconds to minutes, and
//! the harness that asked for it is calling back over MCP meanwhile.
use crate::server::{bad, internal, kerr, lock, Shared};
use crate::RpcError;
use anyhow::{anyhow, Context};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::path::{Component, Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};
use std::time::Duration;
use vk_contracts::driver::{hash_dir, DriverAction, DriverManifest};
use vk_contracts::syscalls::Ctx;
use vk_driver::{ActOutcome, DriverHost, EvidenceCaps, HostError};
use vk_kernel::drivers::{host_error_reason, DriverActPlan, DriverDirs, DriverRow, DriverState};
use vk_kernel::now_ms;

/// The memory cap on a driver's whole process tree: the harness's own.
const DRIVER_MAX_MEMORY: u64 = 2 * 1024 * 1024 * 1024;

/// What the pool holds for one mounted name.
enum Slot {
    /// Waiting for the next act. Boxed: a host is a few hundred bytes, the
    /// marker a few dozen.
    Idle {
        driver_id: String,
        host: Box<DriverHost>,
    },
    /// Taken out by the act holding `ticket`, which puts it back when it
    /// returns — unless the slot is no longer its own by then: an unmount
    /// removed it, or a remount put a new host in its place.
    Acting {
        driver_id: String,
        governed: bool,
        ticket: u64,
    },
}

/// The driver processes this daemon owns, by mounted name. Shared by every
/// connection through `ServerConfig::drivers`; no kernel state lives here.
pub struct DriverPool {
    hosts: Mutex<HashMap<String, Slot>>,
    tickets: AtomicU64,
}

impl DriverPool {
    pub fn new() -> Arc<DriverPool> {
        Arc::new(DriverPool {
            hosts: Mutex::new(HashMap::new()),
            tickets: AtomicU64::new(1),
        })
    }

    /// The map, for the length of a lookup or an insert. A poisoned lock is
    /// taken over rather than unwrapped: every update of the map is a single
    /// `insert` or `remove`, so a panic elsewhere cannot leave it half-made.
    fn map(&self) -> MutexGuard<'_, HashMap<String, Slot>> {
        self.hosts.lock().unwrap_or_else(PoisonError::into_inner)
    }

    /// Whether the pool holds a process for `name`, idle or acting. A daemon
    /// that has just restarted holds none: the first act re-spawns it.
    pub fn has_host(&self, name: &str) -> bool {
        self.map().contains_key(name)
    }

    /// Spawn (or re-spawn) the host for `row` with the platform governor
    /// (2 GiB, no CPU cap); returns what the host observed (`governed`).
    ///
    /// A live host already held for the same `driver_id` is kept, idle or
    /// acting, and its `governed` returned: a repeat mount does not restart
    /// the browser. Otherwise the driver's files are held to `files_sha256`
    /// again, the three directories are made private, the command is resolved
    /// against `row.root` (Ruling R6, [`spawnable`]) and the driver is spawned
    /// with the map unlocked — a hello can take thirty seconds — then put in
    /// the pool. A governor the OS refuses is no spawn.
    pub fn ensure(&self, row: &DriverRow, dirs: &DriverDirs) -> anyhow::Result<bool> {
        let name = &row.manifest.name;
        match self.map().get_mut(name) {
            Some(Slot::Idle { driver_id, host }) if *driver_id == row.driver_id => {
                if matches!(host.try_wait(), Ok(None)) {
                    return Ok(host.governed());
                }
            }
            Some(Slot::Acting {
                driver_id,
                governed,
                ..
            }) if *driver_id == row.driver_id => return Ok(*governed),
            _ => {}
        }
        files_hold(&row.manifest, &row.root)?;
        for dir in [&dirs.profile, &dirs.work, &dirs.evidence] {
            vk_store::paths::private_dir(dir)
                .with_context(|| format!("create {}", dir.display()))?;
        }
        let manifest = spawnable(&row.manifest, &row.root);
        let governor =
            vk_harness::confine::for_this_platform(DRIVER_MAX_MEMORY, None).context("governor")?;
        let host = DriverHost::spawn(
            &manifest,
            &dirs.work,
            &dirs.profile,
            &dirs.evidence,
            governor,
        )
        .map(Box::new)
        .map_err(|e| anyhow!("{e}"))?;
        let governed = host.governed();
        // The guard is gone at the end of this statement, so what the insert
        // replaced — a dead or stale host — is dropped (its tree killed) with
        // the map unlocked.
        let replaced = self.map().insert(
            name.clone(),
            Slot::Idle {
                driver_id: row.driver_id.clone(),
                host,
            },
        );
        drop(replaced);
        Ok(governed)
    }

    /// Run one act; the pool's mutex is held for this driver only to take the
    /// host out and to put it back, never for the duration of the act.
    ///
    /// The host goes back after an answer and after the host's own evidence
    /// refusals — read back after the action, with the driver alive and in
    /// step. After a timeout, a death, a protocol breach or a STOP the tree is
    /// already killed and the host is dropped, so the next `ensure` spawns a
    /// fresh one. The `Err` is [`host_error_reason`] unaltered: the kernel
    /// parses `evidence too large: <name> …` out of it.
    pub fn act(
        &self,
        name: &str,
        action: &DriverAction,
        args: &Value,
        deadline: Duration,
        caps: EvidenceCaps,
        should_stop: &(dyn Fn() -> bool + Sync),
    ) -> Result<ActOutcome, String> {
        let ticket = self.tickets.fetch_add(1, Ordering::Relaxed);
        let (driver_id, mut host) = {
            let mut map = self.map();
            match map.remove(name) {
                Some(Slot::Idle { driver_id, host }) => {
                    let acting = Slot::Acting {
                        driver_id: driver_id.clone(),
                        governed: host.governed(),
                        ticket,
                    };
                    map.insert(name.to_string(), acting);
                    (driver_id, host)
                }
                Some(acting @ Slot::Acting { .. }) => {
                    map.insert(name.to_string(), acting);
                    return Err(format!(
                        "driver would not start: driver {name}'s process is busy with another act"
                    ));
                }
                None => {
                    return Err(format!(
                        "driver would not start: no process is running for driver {name}"
                    ))
                }
            }
        };
        let outcome = host.act(action, args, deadline, caps, should_stop);
        let keep = matches!(
            &outcome,
            Ok(_)
                | Err(HostError::EvidenceEscape(_)
                    | HostError::EvidenceTooLarge { .. }
                    | HostError::EvidenceNotDeclared(_))
        );
        let leftover = {
            let mut map = self.map();
            let ours =
                matches!(map.get(name), Some(Slot::Acting { ticket: t, .. }) if *t == ticket);
            if ours && keep {
                map.insert(name.to_string(), Slot::Idle { driver_id, host });
                None
            } else {
                if ours {
                    map.remove(name);
                }
                Some(host)
            }
        };
        // Unlocked: dropping a host kills its tree and reaps it.
        drop(leftover);
        outcome.map_err(|e| host_error_reason(&e))
    }

    /// Forget `name`'s host and kill its tree. An act in progress holds its
    /// host outside the map; it finds its slot gone when it returns and drops
    /// the host then, and the kernel's act id makes that act's settle stale.
    pub fn drop_host(&self, name: &str) {
        let gone = self.map().remove(name);
        // Unlocked, as in `act`.
        drop(gone);
    }
}

/// The manifest as the host spawns it (Ruling R6): every `command` element
/// that is a relative path — no `..`, no drive — to a file under `root`
/// becomes that file's absolute path. The mounted manifest keeps its relative
/// command, because the driver's identity must not vary per machine; the
/// process's working directory stays the driver's work directory.
fn spawnable(manifest: &DriverManifest, root: &Path) -> DriverManifest {
    let mut m = manifest.clone();
    for arg in &mut m.program.command {
        let rel = Path::new(arg.as_str());
        let plain = rel.is_relative()
            && rel
                .components()
                .all(|c| matches!(c, Component::Normal(_) | Component::CurDir));
        let file = root.join(rel);
        if plain && file.is_file() {
            *arg = file.display().to_string();
        }
    }
    m
}

/// What `driver.mount` takes, and nothing else: an unknown key is a bad
/// request, not a silently ignored one.
#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct MountParams {
    manifest: DriverManifest,
    /// The absolute directory the manifest's relative command resolves
    /// against: `vk mount driver` sends the manifest file's own directory.
    root: PathBuf,
}

/// Everything `driver.mount` checks before anything runs: the manifest
/// validates; `root` is an absolute directory (Ruling R6); and the driver's
/// files hold to `files_sha256` ([`files_hold`]).
fn checked_mount(manifest: &DriverManifest, root: &Path) -> anyhow::Result<()> {
    manifest.validate()?;
    anyhow::ensure!(
        root.is_absolute(),
        "root {} is not absolute: give the directory the manifest's command resolves against \
         by its absolute path (vk mount driver sends the manifest file's own directory)",
        root.display()
    );
    anyhow::ensure!(root.is_dir(), "root {} is not a directory", root.display());
    files_hold(manifest, root)
}

/// The directory of the command's script — its first element ending in
/// `.py`, resolved against `root` — hashes to `files_sha256`, so the code that
/// runs is the code the manifest names. Checked at mount and again before
/// every spawn: a file changed since the mount is a new driver, never the old
/// one re-spawned after a restart.
fn files_hold(manifest: &DriverManifest, root: &Path) -> anyhow::Result<()> {
    let script = manifest
        .program
        .command
        .iter()
        .find(|a| a.ends_with(".py"))
        .ok_or_else(|| {
            anyhow!(
                "program.command names no `.py` script, so there are no driver files to hold \
                 to files_sha256"
            )
        })?;
    let script = root.join(script);
    anyhow::ensure!(
        script.is_file(),
        "the driver script {} is not a file",
        script.display()
    );
    let dir = script
        .parent()
        .ok_or_else(|| anyhow!("the driver script {} has no directory", script.display()))?;
    let actual = hash_dir(dir).with_context(|| format!("hash {}", dir.display()))?;
    anyhow::ensure!(
        actual == manifest.program.files_sha256,
        "driver files hash {actual} (of {}) is not the manifest's files_sha256 {}; regenerate \
         the manifest with drivers/vkdriver/hash_dir.py",
        dir.display(),
        manifest.program.files_sha256
    );
    Ok(())
}

/// `driver.mount {manifest, root}`, for a principal `dispatch` has already
/// proved human (I1). Checked before anything runs; then the process is
/// started with the kernel lock released and the row written under it with
/// what the host observed (`governed`). A different manifest under a mounted
/// name is the kernel's refusal, given under the lock that read the row and
/// before anything is spawned; a repeat mount keeps the row and the process
/// it has (Ruling 13), re-spawning only one a restart took away.
pub(crate) fn mount(
    kernel: &Shared,
    pool: &DriverPool,
    ctx: Ctx,
    p: Value,
) -> Result<Value, RpcError> {
    let MountParams { manifest, root } = serde_json::from_value(p).map_err(|e| {
        bad(&format!(
            "driver.mount takes manifest and root, nothing else: {e}"
        ))
    })?;
    checked_mount(&manifest, &root).map_err(|e| bad(&format!("{e:#}")))?;
    let name = manifest.name.clone();
    let driver_id = manifest.driver_id();
    let (existing, dirs) = {
        let mut k = lock(kernel)?;
        let existing = k.driver(&name).map_err(kerr)?;
        if existing.as_ref().is_some_and(|r| r.driver_id != driver_id) {
            return Err(match k.mount_driver(&ctx, manifest, root, false) {
                Err(e) => kerr(e),
                Ok(_) => internal("a different manifest was mounted without its process"),
            });
        }
        (existing, k.driver_dirs(&name))
    };
    let ready = existing.filter(|r| r.state == DriverState::Ready);
    // The row as stored for a repeat mount, root included; otherwise the row
    // this mount is about to write.
    let row = ready.clone().unwrap_or_else(|| DriverRow {
        manifest: manifest.clone(),
        driver_id: driver_id.clone(),
        root: root.clone(),
        mounted_ms: ctx.now_ms,
        state: DriverState::Ready,
        last_act_ms: 0,
    });
    let had_host = pool.has_host(&name);
    let governed = pool
        .ensure(&row, &dirs)
        .map_err(|e| bad(&format!("driver {name} would not start: {e:#}")))?;
    let mounted = lock(kernel)?.mount_driver(&ctx, manifest, root, governed);
    let row = match mounted {
        Ok(row) => row,
        Err(e) => {
            // The process this mount started goes with its refusal; one that
            // was already running stays.
            if !had_host {
                pool.drop_host(&name);
            }
            return Err(kerr(e));
        }
    };
    let already_mounted = ready.is_some_and(|r| r.mounted_ms == row.mounted_ms);
    Ok(json!({
        "driver_id": row.driver_id,
        "name": row.manifest.name,
        "governed": row.manifest.governed,
        "already_mounted": already_mounted,
    }))
}

/// What `driver.act` takes: the lease token `vk-mcp` injects, the driver and
/// the action by name, and `args` (an empty object when absent).
#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct ActParams {
    token: String,
    driver: String,
    action: String,
    #[serde(default = "no_args")]
    args: Value,
}

fn no_args() -> Value {
    json!({})
}

/// `driver.act {token, driver, action, args}`, in three phases like
/// `harness.run`, so the kernel lock is never held across the action.
///
/// Phase one, under the lock, is the kernel's seven refusals and the plan.
/// Phase two, unlocked, spawns the host if the pool has none — what a daemon
/// restart leaves — and acts, polling the node's STOP; a panic in it is an
/// act that failed, never an act left in flight. Phase three, under the lock,
/// settles on every outcome and answers `{ok: true, result}`.
///
/// A STOP degrades the driver: the host kills the tree, the settle records
/// `stopped`, and after the STOP is lifted the operator remounts.
pub(crate) fn act(kernel: &Shared, pool: &DriverPool, p: Value) -> Result<Value, RpcError> {
    let ActParams {
        token,
        driver,
        action,
        args,
    } = serde_json::from_value(p).map_err(|e| {
        bad(&format!(
            "driver.act takes token, driver, action and args, nothing else: {e}"
        ))
    })?;
    let plan = lock(kernel)?
        .driver_act_begin(&token, now_ms(), &driver, &action, &args)
        .map_err(kerr)?;
    let stop_kernel = kernel.clone();
    let should_stop = move || {
        stop_kernel
            .lock()
            .map(|k| k.stopped_scopes().iter().any(|s| s == "node"))
            .unwrap_or(false)
    };
    let acted = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        run_act(kernel, pool, &plan, &should_stop)
    }));
    let outcome = acted.unwrap_or_else(|_| {
        pool.drop_host(&plan.name);
        Err("the driver act panicked".to_string())
    });
    let result = lock(kernel)?
        .driver_act_settle(&token, now_ms(), &plan, outcome)
        .map_err(kerr)?;
    Ok(json!({ "ok": true, "result": result }))
}

/// Phase two of `driver.act`: a host for the driver — the pool's, or one
/// spawned now from the row, read under a short lock — then the act itself,
/// within what is left of the plan's deadline. A host that cannot be had is
/// the daemon's own reason, `driver would not start: …`.
fn run_act(
    kernel: &Shared,
    pool: &DriverPool,
    plan: &DriverActPlan,
    should_stop: &(dyn Fn() -> bool + Sync),
) -> Result<ActOutcome, String> {
    if !pool.has_host(&plan.name) {
        let row = kernel
            .lock()
            .map_err(|_| "driver would not start: kernel lock poisoned".to_string())?
            .driver(&plan.name)
            .map_err(|e| format!("driver would not start: {e}"))?
            .ok_or_else(|| {
                format!(
                    "driver would not start: driver {} is no longer mounted",
                    plan.name
                )
            })?;
        pool.ensure(&row, &plan.dirs)
            .map_err(|e| format!("driver would not start: {e:#}"))?;
    }
    let deadline = Duration::from_millis(plan.deadline_ms.saturating_sub(now_ms()));
    pool.act(
        &plan.name,
        &plan.action,
        &plan.args,
        deadline,
        plan.caps,
        should_stop,
    )
}
