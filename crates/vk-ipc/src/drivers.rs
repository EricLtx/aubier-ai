//! The daemon's drivers (pilot spec §4.2–4.4): the pool of driver processes
//! this daemon owns, and the two `driver.*` syscalls that cannot run under the
//! kernel lock — `driver.mount`, which starts a process, and `driver.act`,
//! which waits on one.
//!
//! The kernel owns the table of what is mounted and the two moments around an
//! act (`driver_act_begin` before it, `driver_act_settle` after); it owns no
//! process. The processes are here: one [`DriverHost`] per mounted name,
//! spawned by [`DriverPool::ensure`] under this platform's governor — a Job
//! Object on Windows, capped at the manifest's `program.max_memory_bytes`
//! (6 GiB for a browser that names none), no CPU cap, failing closed as a
//! harness does —
//! and driven by [`DriverPool::act`] with neither the kernel lock nor the
//! pool's map held across the action: an act takes its host out of the map,
//! acts on the owned value and puts it back. That is the shape of
//! `harness.run`, for the same reason: an action lasts seconds to minutes, and
//! the harness that asked for it is calling back over MCP meanwhile.
//!
//! One name never has two processes: a spawn in progress is a marker every
//! other caller waits on, and an unmount kills the tree of an act still in
//! flight rather than letting it run on (Ruling R13) — a browser profile is
//! never open in two processes.
use crate::server::{bad, internal, kerr, lock, Shared};
use crate::RpcError;
use anyhow::{anyhow, Context};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::path::{Component, Path, PathBuf};
use std::sync::{Arc, Condvar, Mutex, MutexGuard, PoisonError};
use std::time::Duration;
use vk_contracts::driver::{hash_dir, DriverAction, DriverKind, DriverManifest};
use vk_contracts::syscalls::Ctx;
use vk_driver::{ActOutcome, DriverHost, EvidenceCaps, HostError};
use vk_kernel::drivers::{DriverActPlan, DriverDirs, DriverFailure, DriverRow, DriverState};
use vk_kernel::now_ms;

/// The memory cap on a browser driver's whole process tree when its manifest
/// names none (final review I2): 6 GiB. Not the harness's 2 GiB — a headed
/// Chrome with two tabs, Node and Python passes that, and the Job then fails
/// allocations inside the page ("Aw, Snap") or kills the tree, which reads as
/// a false KO or a dead driver mid-recette.
const BROWSER_MAX_MEMORY: u64 = 6 * 1024 * 1024 * 1024;

/// The memory cap a driver's tree is spawned under: the manifest's
/// `program.max_memory_bytes`, or its kind's default.
fn memory_cap_of(manifest: &DriverManifest) -> u64 {
    manifest
        .program
        .max_memory_bytes
        .unwrap_or(match manifest.kind {
            DriverKind::Browser => BROWSER_MAX_MEMORY,
        })
}

/// What the pool holds for one mounted name.
enum Slot {
    /// Waiting for the next act, with the identity it was spawned for: an
    /// `ensure` adopts it for that `driver_id` only (Task 3 re-review). Boxed:
    /// a host is a few hundred bytes, the two markers a few. `max_memory_bytes`
    /// is the cap its governor was made with.
    Idle {
        host: Box<DriverHost>,
        driver_id: String,
        max_memory_bytes: u64,
    },
    /// A spawn is under way with the map unlocked. The `ensure` that set it
    /// is the only one that clears it ([`Claim`]); every other caller waits
    /// on [`DriverPool::settled`], so a mount racing a lazy re-spawn starts
    /// one process, not two (reviewer minor 3).
    Spawning,
    /// Taken out by an act, which puts it back when it returns unless the
    /// slot is no longer this marker by then. `pid` is the driver's: what an
    /// unmount kills while the act holds the host (Ruling R13), and how the
    /// act knows the marker is still its own. `driver_id` is the identity the
    /// process was spawned for: an `ensure` or an act for another identity is
    /// refused as busy, naming it (Task 7 review).
    Acting {
        pid: u32,
        driver_id: String,
        max_memory_bytes: u64,
    },
}

/// The driver processes this daemon owns, by mounted name. Shared by every
/// connection through `ServerConfig::drivers`; no kernel state lives here.
pub struct DriverPool {
    hosts: Mutex<HashMap<String, Slot>>,
    /// Signalled whenever a `Spawning` marker is resolved, into a host or
    /// into nothing.
    settled: Condvar,
}

impl DriverPool {
    pub fn new() -> Arc<DriverPool> {
        Arc::new(DriverPool {
            hosts: Mutex::new(HashMap::new()),
            settled: Condvar::new(),
        })
    }

    /// The map, for the length of a lookup or an insert. A poisoned lock is
    /// taken over rather than unwrapped: every update of the map is a single
    /// `insert` or `remove`, so a panic elsewhere cannot leave it half-made.
    fn map(&self) -> MutexGuard<'_, HashMap<String, Slot>> {
        self.hosts.lock().unwrap_or_else(PoisonError::into_inner)
    }

    /// Wait until some spawn resolves, the map released meanwhile.
    fn wait<'a>(
        &self,
        map: MutexGuard<'a, HashMap<String, Slot>>,
    ) -> MutexGuard<'a, HashMap<String, Slot>> {
        self.settled
            .wait(map)
            .unwrap_or_else(PoisonError::into_inner)
    }

    /// Whether the pool holds a process for `name` — idle, starting or
    /// acting. A daemon that has just restarted holds none.
    pub fn has_host(&self, name: &str) -> bool {
        self.map().contains_key(name)
    }

    /// The id of `name`'s driver process, idle or acting; `None` while there
    /// is none, or while it is still starting.
    pub fn pid(&self, name: &str) -> Option<u32> {
        match self.map().get(name) {
            Some(Slot::Idle { host, .. }) => Some(host.pid()),
            Some(Slot::Acting { pid, .. }) => Some(*pid),
            Some(Slot::Spawning) | None => None,
        }
    }

    /// The memory cap `name`'s process was spawned under, idle or acting:
    /// the manifest's `program.max_memory_bytes`, or the kind's default
    /// (final review I2). `None` while there is no process, or while it is
    /// still starting. A test's read-back.
    #[doc(hidden)]
    pub fn memory_cap(&self, name: &str) -> Option<u64> {
        match self.map().get(name) {
            Some(Slot::Idle {
                max_memory_bytes, ..
            })
            | Some(Slot::Acting {
                max_memory_bytes, ..
            }) => Some(*max_memory_bytes),
            Some(Slot::Spawning) | None => None,
        }
    }

    /// Spawn (or re-spawn) the host for `row` with the platform governor
    /// (the manifest's memory cap, no CPU cap); returns what the host
    /// observed (`governed`).
    ///
    /// A live idle host spawned for this row's `driver_id` is kept and its
    /// `governed` returned: a repeat mount does not restart the browser. An
    /// acting one of this identity is the host the row describes — the kernel
    /// admits one act per driver — so the row's `governed` is returned; one
    /// acting for another identity (an orphan of a manifest unmounted since)
    /// is refused as busy, naming that identity. A spawn under way is
    /// waited for. Otherwise — no host, one that died idle (reviewer minor 2),
    /// or an idle one spawned for another identity: a manifest unmounted
    /// since, whose process an act's late `ensure` left behind (Task 3
    /// re-review) — this caller marks the slot `Spawning`, drops what was
    /// there, spawns with the map unlocked ([`spawn_host`]; a hello can take
    /// thirty seconds) and puts the host in its place.
    pub fn ensure(&self, row: &DriverRow, dirs: &DriverDirs) -> anyhow::Result<bool> {
        let name = &row.manifest.name;
        let mut map = self.map();
        let stale = loop {
            let alive = match map.get_mut(name) {
                Some(Slot::Spawning) => {
                    map = self.wait(map);
                    continue;
                }
                Some(Slot::Acting { driver_id, .. }) => {
                    if *driver_id == row.driver_id {
                        return Ok(row.manifest.governed);
                    }
                    return Err(anyhow!(busy(name, driver_id, &row.driver_id)));
                }
                Some(Slot::Idle {
                    host, driver_id, ..
                }) if *driver_id == row.driver_id => match host.try_wait() {
                    Ok(None) => Some(host.governed()),
                    _ => None,
                },
                Some(Slot::Idle { .. }) | None => None,
            };
            if let Some(governed) = alive {
                return Ok(governed);
            }
            break map.insert(name.clone(), Slot::Spawning);
        };
        drop(map);
        // A host that died idle, or one of another identity: killed, reaped
        // and its tree swept, with the map unlocked.
        drop(stale);
        let claim = Claim { pool: self, name };
        let (host, max_memory_bytes) = spawn_host(row, dirs)?;
        let governed = host.governed();
        self.map().insert(
            name.clone(),
            Slot::Idle {
                host,
                driver_id: row.driver_id.clone(),
                max_memory_bytes,
            },
        );
        drop(claim);
        Ok(governed)
    }

    /// Run one act for `row`'s driver, on a process of `row`'s identity only.
    ///
    /// [`ensure`](Self::ensure) runs first — it keeps a live host of this
    /// identity, re-spawns one that died idle or belongs to another identity,
    /// and refuses while another identity's act holds the slot — then the
    /// host is taken out for the act. The pool's mutex is held only to take
    /// the host out and to put it back, never for the duration of the act.
    /// An idle host of another identity found at that moment was put there
    /// since this `ensure` (another act's late one): it is left for the next
    /// `ensure` to replace, and the act goes round once more. A slot another
    /// act holds is refused as busy, naming the identity it acts for.
    ///
    /// The host goes back after an answer and after the host's own evidence
    /// refusals — read back after the action, with the driver alive and in
    /// step. After a timeout, a death, a protocol breach or a STOP the tree is
    /// already killed and the host is dropped, so the next `ensure` spawns a
    /// fresh one. The `Err` is the host's own error, typed
    /// ([`DriverFailure::Host`]), or the pool's account of why the act had
    /// no process ([`DriverFailure::WouldNotStart`]); the kernel words the
    /// ledger from the variant alone.
    #[allow(clippy::too_many_arguments)]
    pub fn act(
        &self,
        row: &DriverRow,
        dirs: &DriverDirs,
        action: &DriverAction,
        args: &Value,
        deadline: Duration,
        caps: EvidenceCaps,
        should_stop: &(dyn Fn() -> bool + Sync),
    ) -> Result<ActOutcome, DriverFailure> {
        let name = row.manifest.name.as_str();
        let mut taken = None;
        for _ in 0..2 {
            self.ensure(row, dirs)
                .map_err(|e| DriverFailure::WouldNotStart(format!("{e:#}")))?;
            let mut map = self.map();
            match map.remove(name) {
                Some(Slot::Idle {
                    host,
                    driver_id,
                    max_memory_bytes,
                }) if driver_id == row.driver_id => {
                    let pid = host.pid();
                    map.insert(
                        name.to_string(),
                        Slot::Acting {
                            pid,
                            driver_id: driver_id.clone(),
                            max_memory_bytes,
                        },
                    );
                    taken = Some((host, driver_id, max_memory_bytes));
                    break;
                }
                Some(idle @ Slot::Idle { .. }) => {
                    map.insert(name.to_string(), idle);
                }
                Some(Slot::Acting {
                    pid,
                    driver_id,
                    max_memory_bytes,
                }) => {
                    let why = busy(name, &driver_id, &row.driver_id);
                    map.insert(
                        name.to_string(),
                        Slot::Acting {
                            pid,
                            driver_id,
                            max_memory_bytes,
                        },
                    );
                    return Err(DriverFailure::WouldNotStart(why));
                }
                Some(Slot::Spawning) => {
                    map.insert(name.to_string(), Slot::Spawning);
                    return Err(DriverFailure::WouldNotStart(format!(
                        "driver {name}'s process is starting"
                    )));
                }
                None => {
                    return Err(DriverFailure::WouldNotStart(format!(
                        "no process is running for driver {name}"
                    )))
                }
            }
        }
        let Some((mut host, driver_id, max_memory_bytes)) = taken else {
            return Err(DriverFailure::WouldNotStart(format!(
                "driver {name}'s process was replaced by another manifest's twice under this act"
            )));
        };
        let pid = host.pid();
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
            let ours = matches!(map.get(name), Some(Slot::Acting { pid: p, .. }) if *p == pid);
            if ours && keep {
                map.insert(
                    name.to_string(),
                    Slot::Idle {
                        host,
                        driver_id,
                        max_memory_bytes,
                    },
                );
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
        outcome.map_err(DriverFailure::Host)
    }

    /// Forget `name`'s process and kill its tree, whatever it is doing; when
    /// this returns, nothing of `name` runs.
    ///
    /// An idle host is removed and dropped (killed, reaped) with the map
    /// unlocked. An acting one is held by its act, so its tree is killed by
    /// pid (Ruling R13) — **with the map held**: the acting thread must take
    /// the map to find its slot gone before it drops, and so reaps, its host,
    /// so the pid still names that driver when the kill lands. The act comes
    /// back with the host's failure, drops its host, and its settle is the
    /// kernel's stale path. A spawn under way is waited for, then dropped
    /// like an idle host.
    pub fn drop_host(&self, name: &str) {
        let mut map = self.map();
        loop {
            match map.get(name) {
                Some(Slot::Spawning) => map = self.wait(map),
                Some(Slot::Acting { pid, .. }) => {
                    let pid = *pid;
                    map.remove(name);
                    vk_driver::kill_pid_tree(pid);
                    return;
                }
                Some(Slot::Idle { .. }) | None => break,
            }
        }
        let gone = map.remove(name);
        drop(map);
        drop(gone);
    }

    /// After an act panicked: its host went with the unwinding (dropped, so
    /// killed and reaped), and its marker is cleared **without** a kill — the
    /// pid it holds may no longer name anything of ours. A spawn's marker is
    /// its `Claim`'s to clear, and an idle host is left as it is.
    fn clear_acting(&self, name: &str) {
        let mut map = self.map();
        if matches!(map.get(name), Some(Slot::Acting { .. })) {
            map.remove(name);
        }
    }
}

/// The refusal for a slot another act holds: busy, and when that act is for
/// another identity — an orphan of a manifest unmounted since — which one,
/// and the remedy (`vk driver umount` kills an acting process, Ruling R13).
fn busy(name: &str, acting: &str, wanted: &str) -> String {
    if acting == wanted {
        format!("driver {name}'s process is busy with another act")
    } else {
        format!(
            "driver {name}'s process is busy with an act of another manifest ({acting}); wait \
             for it to end, or run `vk driver umount {name}` to kill it"
        )
    }
}

/// The `Spawning` marker one `ensure` set, cleared however that `ensure`
/// ends — a refusal, an error, a panic — and every waiter woken. A spawn that
/// succeeded has already put its host in the marker's place, which is left
/// as it is.
struct Claim<'a> {
    pool: &'a DriverPool,
    name: &'a str,
}

impl Drop for Claim<'_> {
    fn drop(&mut self) {
        let mut map = self.pool.map();
        if matches!(map.get(self.name), Some(Slot::Spawning)) {
            map.remove(self.name);
        }
        drop(map);
        self.pool.settled.notify_all();
    }
}

/// Start the driver `row` describes: its files held to `files_sha256` again
/// ([`files_hold`]: a file changed since the mount is a new driver, never the
/// old one re-spawned after a restart), the three directories made private
/// before the process exists, the command resolved against `row.root`
/// (Ruling R6, [`spawnable`]) and the platform governor made first, capped at
/// [`memory_cap_of`] the manifest — a governor the OS refuses is no spawn.
/// The host comes back with the cap its governor was made with.
fn spawn_host(row: &DriverRow, dirs: &DriverDirs) -> anyhow::Result<(Box<DriverHost>, u64)> {
    files_hold(&row.manifest, &row.root)?;
    for dir in [&dirs.profile, &dirs.work, &dirs.evidence] {
        vk_store::paths::private_dir(dir).with_context(|| format!("create {}", dir.display()))?;
    }
    let manifest = spawnable(&row.manifest, &row.root);
    let max_memory_bytes = memory_cap_of(&row.manifest);
    let governor =
        vk_harness::confine::for_this_platform(max_memory_bytes, None).context("governor")?;
    DriverHost::spawn(
        &manifest,
        &dirs.work,
        &dirs.profile,
        &dirs.evidence,
        governor,
    )
    .map(|host| (Box::new(host), max_memory_bytes))
    .map_err(|e| anyhow!("{e}"))
}

/// A path a manifest's command may name a file under its root with: relative
/// and made of plain names only (`driver.py`, `vkdriver/driver.py`,
/// `./driver.py`) — no `..`, no root, no drive.
fn under_root(p: &Path) -> bool {
    p.is_relative()
        && p.components()
            .all(|c| matches!(c, Component::Normal(_) | Component::CurDir))
}

/// The manifest as the host spawns it (Ruling R6): every `command` element
/// that is [`under_root`] and names a file under `root` becomes that file's
/// absolute path. The mounted manifest keeps its relative command, because
/// the driver's identity must not vary per machine; the process's working
/// directory stays the driver's work directory.
fn spawnable(manifest: &DriverManifest, root: &Path) -> DriverManifest {
    let mut m = manifest.clone();
    for arg in &mut m.program.command {
        let rel = Path::new(arg.as_str());
        let file = root.join(rel);
        if under_root(rel) && file.is_file() {
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
/// `.py`, which must be [`under_root`], resolved against `root` — hashes to
/// `files_sha256`, so the code that runs is the code the manifest names.
/// Under the root because anything else breaks that: `../x.py` would hash one
/// directory while the process ran another, and an absolute path would ignore
/// the root and bind the manifest to one machine (review round 1, Important
/// 1). Checked at mount and again before every spawn: a file changed since
/// the mount is a new driver, never the old one re-spawned after a restart.
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
    anyhow::ensure!(
        under_root(Path::new(script)),
        "program.command paths must be relative to the manifest's directory, without `..`: \
         `{script}` is not"
    );
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
/// Phase two, unlocked, makes sure of a host — spawning one if the pool has
/// none live, which is what a daemon restart leaves — and acts, polling the
/// node's STOP; a panic in it is an act that failed, never an act left in
/// flight. Phase three, under the lock, settles on every outcome and answers
/// `{ok: true, result}`.
///
/// A STOP degrades the driver: the host kills the tree, the settle records
/// `stopped`, and after the STOP is lifted the operator remounts. An unmount
/// while the act runs kills the tree too (Ruling R13), and the settle is the
/// kernel's stale path.
///
/// A failed act's detail — a path, a hash, an excerpt of the driver's own
/// output — goes to this daemon's log and to the driver's `driver.log`
/// ([`report_failure`]) before the settle, which records the closed word
/// alone (Ruling 28, final review I1).
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
        pool.clear_acting(&plan.name);
        Err(DriverFailure::Panicked)
    });
    if let Err(failure) = &outcome {
        report_failure(&plan, failure);
    }
    let result = lock(kernel)?
        .driver_act_settle(&token, now_ms(), &plan, outcome)
        .map_err(kerr)?;
    Ok(json!({ "ok": true, "result": result }))
}

/// Phase two of `driver.act`: a host for the driver, then the act itself.
///
/// [`DriverPool::act`] runs `ensure` every time, on the row read here under a
/// short lock: it keeps a live host of the row's identity, re-spawns one that
/// died idle rather than letting the act find it dead and degrade a driver
/// that could simply be started again (reviewer minor 2), replaces one of
/// another identity, and fills a restarted daemon's empty pool. It runs
/// **before** the act's clock starts: the act gets the action's whole
/// `max_action_ms`, and a spawn's time — a hello can take thirty seconds — is
/// on top of it, not charged to it (minor 9). A host that cannot be had is
/// [`DriverFailure::WouldNotStart`], with the daemon's account of why.
fn run_act(
    kernel: &Shared,
    pool: &DriverPool,
    plan: &DriverActPlan,
    should_stop: &(dyn Fn() -> bool + Sync),
) -> Result<ActOutcome, DriverFailure> {
    let row = kernel
        .lock()
        .map_err(|_| DriverFailure::WouldNotStart("kernel lock poisoned".into()))?
        .driver(&plan.name)
        .map_err(|e| DriverFailure::WouldNotStart(e.to_string()))?
        .ok_or_else(|| {
            DriverFailure::WouldNotStart(format!("driver {} is no longer mounted", plan.name))
        })?;
    // `deadline_ms` is `begun_ms + max_action_ms`: the action's whole budget,
    // counted from the act itself.
    let budget = Duration::from_millis(plan.deadline_ms.saturating_sub(plan.begun_ms));
    pool.act(
        &row,
        &plan.dirs,
        &plan.action,
        &plan.args,
        budget,
        plan.caps,
        should_stop,
    )
}

/// Where a failed act's detail goes, since the ledger takes only its word
/// (Ruling 28, final review I1): this daemon's log at `warn`, and a line in
/// the driver's own `<work>/driver.log`, beside the driver's stderr and the
/// host's stray-stdout notes, where an operator reading `vk driver show`'s
/// degraded reason looks next. The log line is best effort: a work directory
/// that cannot be written to is said in the daemon's log and changes nothing
/// else — the settle still records the failure.
fn report_failure(plan: &DriverActPlan, failure: &DriverFailure) {
    let reason = failure.reason();
    let detail = failure.detail();
    tracing::warn!(
        driver = %plan.name,
        action = %plan.action.name,
        task = %plan.task_id,
        %reason,
        %detail,
        "driver act failed"
    );
    let path = plan.dirs.work.join("driver.log");
    let line = format!(
        "[daemon] act {} on task {} failed: {reason}: {detail}\n",
        plan.action.name, plan.task_id
    );
    let written = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
        .and_then(|mut log| std::io::Write::write_all(&mut log, line.as_bytes()));
    if let Err(e) = written {
        tracing::warn!(path = %path.display(), error = %e, "a failed act's detail could not be appended to driver.log");
    }
}
