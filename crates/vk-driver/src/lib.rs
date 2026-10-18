//! The driver process host: a long-lived child the kernel starts, caps and
//! talks to by JSON lines on stdio (pilot spec §4.2).
//!
//! A *driver* is a peripheral — the pilot's is a browser — behind a process
//! this kernel starts from its [`DriverManifest`]. [`DriverHost::spawn`]
//! starts that process in its work directory with the environment the
//! manifest implies, puts it under the [`Governor`] it is handed the moment it
//! exists (fail closed on Windows, as the harness does), and reads its hello:
//! protocol 1 and an action set covering the manifest's, or the tree is killed
//! and the mount refused. [`DriverHost::act`] is one request and one response:
//! the host creates a per-action evidence directory, writes the request line,
//! and waits — polling the kernel's `should_stop` every 250 ms and sampling
//! the egress of every process under the governor (the whole tree under the
//! Job on Windows, the leader alone when ungoverned) every 500 ms — until the
//! response with that id arrives, the deadline passes or a STOP comes; the
//! last two kill the tree. Evidence comes back by name, never inline: the
//! driver names a file the manifest declares for the action, and the host
//! reads `<evidence dir>/<name>` — a plain token, so it cannot leave the
//! directory — opened without following a link and only if it is a regular
//! file, treats the path the driver gives as a cross-check, holds the size
//! under the per-file and per-action caps, and removes the directory whatever
//! happened.
//!
//! What the host is, and is not. It is a plain value the daemon owns, with no
//! lock inside and no global state: the kernel calls `act` with its own lock
//! released, exactly as it runs a harness, because a driver action lasts
//! seconds to minutes and nothing of the kernel may wait on it. It spawns the
//! driver and nothing else. It does not validate the manifest (the kernel did,
//! at mount), does not check `args` against the action's schema (the kernel
//! does, before calling), does not enforce the host allow-list (the driver
//! does, from `VK_DRIVER_HOSTS`; the host only observes egress) and does not
//! write the ledger. Every refusal is a [`HostError`] the kernel turns into a
//! `driver.acted` or `driver.unmounted` payload.
//!
//! A host whose driver died — killed at a deadline or a STOP, or exited on its
//! own — stays a value: every further `act` answers [`HostError::Dead`], and
//! the kernel's remedy is an unmount and a remount.
use serde_json::Value;
use std::collections::BTreeSet;
use std::fs::{File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::ExitStatus;
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};
use vk_contracts::driver::{DriverAction, DriverManifest};
use vk_harness::confine::Governor;
use vk_harness::netwatch;

pub mod link;
pub mod protocol;

use link::{open_no_follow, DriverLink, LocalLink, SpawnSpec};
use protocol::HelloLine;
pub use protocol::{EvidenceRef, Hello, Request, Response, RESPONSE_REASONS};

/// How long a freshly spawned driver has to print its hello.
pub const HELLO_TIMEOUT: Duration = Duration::from_secs(30);
/// The one protocol this host speaks.
pub const PROTOCOL: u32 = 1;

/// How often `act` asks `should_stop` while a request is in flight.
const POLL_EVERY: Duration = Duration::from_millis(250);
/// How often the driver's egress is sampled: the harness's own interval.
const SAMPLE_EVERY: Duration = vk_harness::launch::SAMPLE_EVERY;
/// The ceilings on what one action may hand back as evidence: per file, and
/// summed over the action.
#[derive(Debug, Clone, Copy)]
pub struct EvidenceCaps {
    pub per_file: u64,
    pub per_action: u64,
}

/// One evidence file, read once from under the evidence directory.
#[derive(Debug, Clone)]
pub struct EvidenceFile {
    pub name: String,
    pub mime: String,
    pub bytes: Vec<u8>,
}

/// What one act came to: the driver's answer, the evidence read back, how long
/// the driver took, and the remote endpoints its processes were seen connected
/// to while it worked.
#[derive(Debug, Clone)]
pub struct ActOutcome {
    pub ok: bool,
    pub reason: Option<String>,
    pub detail: Option<String>,
    pub result: Value,
    pub evidence: Vec<EvidenceFile>,
    pub duration_ms: u64,
    /// Remote `ip:port` endpoints seen in established TCP connections of the
    /// driver's processes while the action ran: the whole tree under the Job
    /// on Windows; the leader only when ungoverned. Sampled every 500 ms,
    /// deduplicated, sorted.
    pub hosts_seen: Vec<String>,
}

/// Why the host refused, or could not finish, what it was asked. `Protocol`
/// also carries the host-side failures the protocol has no better word for —
/// a program that could not be started, a tree that could not be contained, a
/// directory that could not be made — each typed by its [`ProtocolFault`],
/// with its cause in `detail`.
///
/// The messages are for a log, not for the ledger: a detail may name a path,
/// a hash, or quote the driver's own output — the site's words with it. The
/// kernel words the ledger from the variant and the fault alone (Ruling 28,
/// final review I1).
#[derive(Debug, thiserror::Error)]
pub enum HostError {
    #[error("driver printed no hello within {0:?}")]
    HelloTimeout(Duration),
    #[error("driver protocol: {detail}")]
    Protocol {
        fault: ProtocolFault,
        detail: String,
    },
    #[error("driver exited: {0}")]
    Dead(String),
    #[error("action exceeded its deadline; driver tree killed")]
    Timeout,
    #[error("stopped")]
    Stopped,
    #[error("evidence {0} is not under the evidence directory")]
    EvidenceEscape(String),
    #[error("evidence {name} is {len} bytes, above {cap}")]
    EvidenceTooLarge { name: String, len: u64, cap: u64 },
    #[error("evidence {0} is not a name the manifest allows for this action")]
    EvidenceNotDeclared(String),
}

/// Which kind of failure a [`HostError::Protocol`] is: a closed set, so a
/// reader of the error decides from the kind and never from the words.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ProtocolFault {
    /// The program could not be started, or what it needs could not be made
    /// first: an empty command, the work directory, the log, the evidence
    /// root.
    NotStarted,
    /// The governor refused the tree, so it was killed before it ran
    /// (Windows, fail closed).
    NotContained,
    /// The first line was not a hello this host takes: unreadable, another
    /// protocol, or the manifest's actions missing from it.
    BadHello,
    /// A response under another request's id: a driver out of step.
    ForeignId,
    /// A response under this request's id that this host cannot read, or
    /// one that lists an evidence file twice.
    MalformedResponse,
    /// A host-side I/O failure during an act: the evidence directory could
    /// not be made, or a named evidence file could not be read.
    HostIo,
}

/// A [`HostError::Protocol`] of this `fault`.
fn protocol(fault: ProtocolFault, detail: impl Into<String>) -> HostError {
    HostError::Protocol {
        fault,
        detail: detail.into(),
    }
}

/// A running driver and the host's end of its link. Owned by the daemon as a
/// plain value; dropping it kills the tree and reaps the child.
pub struct DriverHost {
    /// The driver's life and its lines: today a [`LocalLink`], the process
    /// this daemon spawned. Declared before the governor so it is dropped —
    /// the kill — first.
    link: Box<dyn DriverLink>,
    pid: u32,
    /// The governor the tree is under: asked for its process list at every
    /// egress sample, and dropped last — after the link's kill — so a Job
    /// Object closing terminates whatever the driver left running.
    governor: Box<dyn Governor>,
    governed: bool,
    /// Every time the driver's tree is signalled, counted; shared with the
    /// link and with [`TreeSignals`] so a test reads it after the drop.
    signals: Arc<AtomicU32>,
    next_id: u64,
    hello: Hello,
    evidence_root: PathBuf,
    /// `<work_dir>/driver.log`, append: the driver's stderr goes there through
    /// its own handle, and the host appends the stray stdout lines it ignores.
    log: File,
    log_path: PathBuf,
}

/// How many times a host has signalled its driver's tree — a kill, or a sweep
/// that sent a signal — readable after the host is dropped. A test seam: the
/// proof that a tree is signalled once and never again (M18).
#[doc(hidden)]
#[derive(Debug, Clone)]
pub struct TreeSignals(Arc<AtomicU32>);

impl TreeSignals {
    pub fn count(&self) -> u32 {
        self.0.load(Ordering::SeqCst)
    }
}

/// The response to one request, with what the wait recorded alongside it.
struct Exchange {
    response: Response,
    duration_ms: u64,
    hosts_seen: Vec<String>,
}

/// What one line on stdout is, to the request `id` in flight.
enum Line {
    /// The answer to the request in flight.
    Response(Response),
    /// A JSON object whose `id` is some other request's: a driver out of step.
    Foreign(u64),
    /// Our `id`, but not a response this host can read.
    Malformed(String),
    /// Not JSON, or JSON without a numeric `id`: a stray line, logged.
    Other,
}

impl DriverHost {
    /// Spawn `manifest.program.command` with cwd = `work_dir` and the
    /// environment `VK_DRIVER_PROFILE_DIR`, `VK_DRIVER_WORK_DIR`,
    /// `VK_DRIVER_HEADLESS` (`0`|`1`), `VK_DRIVER_CHANNEL` (empty when the
    /// manifest names no channel) and `VK_DRIVER_HOSTS` (the manifest's hosts
    /// joined with commas, ruling R1 — the driver enforces the allow-list from
    /// it) and `VK_DRIVER_APPLICATIONS` (the executables a com driver drives,
    /// comma-joined; empty for a browser driver); contain it under `governor` immediately, failing closed on Windows
    /// as the harness does; read the hello within [`HELLO_TIMEOUT`]; refuse a
    /// protocol other than [`PROTOCOL`] and an action set that does not cover
    /// `manifest.actions`. The driver's stderr goes to `<work_dir>/driver.log`
    /// (append). A driver refused after it started is killed before this
    /// returns; nothing is left running on an `Err`.
    pub fn spawn(
        manifest: &DriverManifest,
        work_dir: &Path,
        profile_dir: &Path,
        evidence_root: &Path,
        governor: Box<dyn Governor>,
    ) -> Result<DriverHost, HostError> {
        Self::spawn_with_hello_timeout(
            manifest,
            work_dir,
            profile_dir,
            evidence_root,
            governor,
            HELLO_TIMEOUT,
        )
    }

    /// [`spawn`](Self::spawn) with the hello timeout as a parameter. The test
    /// seam: a test proves the refusal of a silent driver in a second rather
    /// than thirty. Production callers use `spawn`.
    pub fn spawn_with_hello_timeout(
        manifest: &DriverManifest,
        work_dir: &Path,
        profile_dir: &Path,
        evidence_root: &Path,
        governor: Box<dyn Governor>,
        hello_timeout: Duration,
    ) -> Result<DriverHost, HostError> {
        let argv = &manifest.program.command;
        let Some(program) = argv.first() else {
            return Err(protocol(
                ProtocolFault::NotStarted,
                "program.command is empty: nothing to spawn",
            ));
        };
        std::fs::create_dir_all(work_dir).map_err(|e| {
            host_io(
                ProtocolFault::NotStarted,
                format!("create work dir {}", work_dir.display()),
                e,
            )
        })?;
        let log_path = work_dir.join("driver.log");
        let log = open_log(&log_path)?;
        let evidence_root = std::path::absolute(evidence_root).map_err(|e| {
            host_io(
                ProtocolFault::NotStarted,
                format!("resolve evidence root {}", evidence_root.display()),
                e,
            )
        })?;

        let spec = SpawnSpec {
            argv: argv.clone(),
            work_dir: work_dir.to_path_buf(),
            env: vec![
                (
                    "VK_DRIVER_PROFILE_DIR".into(),
                    profile_dir.display().to_string(),
                ),
                ("VK_DRIVER_WORK_DIR".into(), work_dir.display().to_string()),
                (
                    "VK_DRIVER_HEADLESS".into(),
                    if manifest.program.headless { "1" } else { "0" }.into(),
                ),
                (
                    "VK_DRIVER_CHANNEL".into(),
                    manifest
                        .program
                        .browser_channel
                        .as_deref()
                        .unwrap_or("")
                        .into(),
                ),
                ("VK_DRIVER_HOSTS".into(), manifest.hosts.join(",")),
                (
                    "VK_DRIVER_APPLICATIONS".into(),
                    manifest.applications.join(","),
                ),
            ],
            log_file: log_path.clone(),
        };
        let signals = Arc::new(AtomicU32::new(0));
        let mut link: Box<dyn DriverLink> = Box::new(LocalLink::new(Arc::clone(&signals)));
        let spawned = link.spawn(spec).map_err(|e| {
            protocol(
                ProtocolFault::NotStarted,
                format!(
                    "`{program}` could not be started in {}: {}",
                    work_dir.display(),
                    cause(e)
                ),
            )
        })?;

        // Immediately after spawn, before the child can do much: contain the
        // tree, and remember whether the OS confirmed it.
        let child = link
            .child()
            .expect("a LocalLink holds its child once it has spawned");
        let governed = match governor.contain(child) {
            Ok(()) => governor.governed(),
            Err(e) => {
                if cfg!(windows) {
                    // Fail closed: the platform has a governor and it refused,
                    // so the driver does not run at all.
                    tracing::error!(error = %format!("{e:#}"), "driver not contained: killed before it ran");
                    let _ = link.kill();
                    return Err(protocol(
                        ProtocolFault::NotContained,
                        format!("not contained: {e:#}"),
                    ));
                }
                tracing::warn!(error = %format!("{e:#}"), "driver launched ungoverned: no governor on this platform");
                false
            }
        };
        link.set_governed(governed);

        let hello = match await_hello(link.as_mut(), hello_timeout, manifest) {
            Ok(hello) => hello,
            Err(e) => {
                tracing::warn!(error = %e, "driver refused at hello; tree killed");
                // A driver that died before its hello was swept, then reaped,
                // by `await_hello`: its id is not signalled again.
                if !matches!(e, HostError::Dead(_)) {
                    let _ = link.kill();
                }
                return Err(e);
            }
        };
        tracing::info!(
            driver = %hello.driver,
            version = %hello.version,
            pid = spawned.pid,
            governed,
            "driver mounted"
        );
        Ok(DriverHost {
            link,
            pid: spawned.pid,
            governor,
            governed,
            signals,
            next_id: 1,
            hello,
            evidence_root,
            log,
            log_path,
        })
    }

    /// Whether the OS confirmed the driver tree is under its governor — the
    /// value the `driver.mounted` payload carries. Always false under the
    /// Noop governor.
    pub fn governed(&self) -> bool {
        self.governed
    }

    /// Whether the tree has been signalled and the leader reaped, so nothing
    /// will signal it again. A test's read-back only.
    #[doc(hidden)]
    pub fn swept(&self) -> bool {
        self.link.swept()
    }

    /// Every process the governor holds for this driver, as the governor
    /// itself lists them — on Windows the Job Object's own process list, the
    /// browser and anything it started included; empty when ungoverned. What
    /// the egress sampler reads (Ruling R8). A test's read-back only: the
    /// proof that a grandchild is in the tree (final review I5).
    #[doc(hidden)]
    pub fn tree_pids(&self) -> Vec<u32> {
        self.governor.pids()
    }

    /// The count of the times this host has signalled its driver's tree,
    /// still readable once the host is dropped. A test's read-back only.
    #[doc(hidden)]
    pub fn tree_signals(&self) -> TreeSignals {
        TreeSignals(Arc::clone(&self.signals))
    }

    /// What the driver said first.
    pub fn hello(&self) -> &Hello {
        &self.hello
    }

    /// The driver process's id: on Unix also the id of the process group it
    /// leads. What the daemon keeps while an act holds the host, so an
    /// unmount can kill the tree with [`kill_pid_tree`].
    pub fn pid(&self) -> u32 {
        self.pid
    }

    /// Has the driver process exited? `Some` once it has been reaped — after
    /// a deadline or STOP kill, a [`HostError::Dead`] outcome or `shutdown` —
    /// and `None` while it runs. The read-back the daemon puts in a
    /// `driver.unmounted` reason, and a test's proof that a kill killed.
    ///
    /// A driver gone that nothing had swept yet — one killed from outside
    /// while idle — is settled here in M18's order: its exit observed without
    /// reaping it ([`exited`]), what it left behind swept ([`sweep`]), then
    /// the reap. `shutdown` and `Drop` send nothing more to its id.
    pub fn try_wait(&mut self) -> std::io::Result<Option<ExitStatus>> {
        self.link.try_wait().map_err(|e| match e {
            link::LinkError::Io(e) => e,
            other => std::io::Error::other(other.to_string()),
        })
    }

    /// One act. Creates `<evidence_root>/<id>/`, writes the request line, and
    /// waits for the response with that id — other lines (JSON without an
    /// `id`, or not JSON) are appended to `driver.log` and ignored; a line
    /// with another request's id, or an unreadable response under this one,
    /// kills the tree as a protocol error. `should_stop` is polled every
    /// 250 ms and the driver's egress sampled every 500 ms into `hosts_seen`.
    /// On the deadline or a STOP the tree is killed and the answer is
    /// [`HostError::Timeout`] or [`HostError::Stopped`]; a driver that exits
    /// first is [`HostError::Dead`] with its exit. Then each evidence file the
    /// response names is read once, by name (ruling R9): the name checked
    /// against the action's declaration, `<evidence_root>/<id>/<name>` opened
    /// without following a link and only if it is a regular file, the
    /// driver's `path` held to that same file as a cross-check, the size held
    /// to `caps` — and `<evidence_root>/<id>/` is removed, whatever happened.
    /// `duration_ms` is the time from the request written to the response
    /// read: the driver's time, not the host's.
    pub fn act(
        &mut self,
        action: &DriverAction,
        args: &Value,
        deadline: Duration,
        caps: EvidenceCaps,
        should_stop: &(dyn Fn() -> bool + Sync),
    ) -> Result<ActOutcome, HostError> {
        let id = self.next_id;
        self.next_id += 1;
        let dir = self.evidence_root.join(id.to_string());
        std::fs::create_dir_all(&dir).map_err(|e| {
            host_io(
                ProtocolFault::HostIo,
                format!("create evidence dir {}", dir.display()),
                e,
            )
        })?;

        let request = Request {
            id,
            action: &action.name,
            args,
            deadline_ms: millis(deadline),
            evidence_dir: dir.display().to_string(),
        };
        let line = serde_json::to_string(&request).expect("a request always serialises");

        let outcome = self
            .exchange(id, &line, deadline, should_stop)
            .and_then(|exchange| {
                let evidence =
                    self.collect_evidence(&exchange.response.evidence, action, &dir, caps)?;
                let Exchange {
                    response,
                    duration_ms,
                    hosts_seen,
                } = exchange;
                let (reason, detail) = Response::closed_reason(response.reason, response.detail);
                Ok(ActOutcome {
                    ok: response.ok,
                    reason,
                    detail,
                    result: response.result,
                    evidence,
                    duration_ms,
                    hosts_seen,
                })
            });
        if let Err(e) = std::fs::remove_dir_all(&dir) {
            tracing::warn!(dir = %dir.display(), error = %e, "the evidence directory could not be removed");
        }
        outcome
    }

    /// Kill the tree and reap the child. Dropping the host does the same.
    pub fn shutdown(self) {
        drop(self);
    }

    /// Send the request line and wait for its response, under the deadline,
    /// the STOP poll and the egress sampling. Every exit of this wait that
    /// leaves the driver out of step — deadline, STOP, a foreign id, an
    /// unreadable response — kills the tree first.
    fn exchange(
        &mut self,
        id: u64,
        line: &str,
        deadline: Duration,
        should_stop: &(dyn Fn() -> bool + Sync),
    ) -> Result<Exchange, HostError> {
        let started = Instant::now();
        if let Err(e) = self.link.send_line(line) {
            let exit = self.settle_dead();
            return Err(HostError::Dead(format!(
                "{exit} (the request could not be written: {})",
                cause(e)
            )));
        }
        let deadline_at = started + deadline;
        let leader = self.pid;
        let mut hosts = BTreeSet::new();
        let mut last_sample: Option<Instant> = None;
        let response = loop {
            if last_sample.is_none_or(|t| t.elapsed() >= SAMPLE_EVERY) {
                // Every process the governor holds — the whole tree under the
                // Job on Windows, where a browser's sockets sit levels below
                // the leader — and the leader, which is all there is to see
                // when ungoverned (ruling R8).
                let mut pids = self.governor.pids();
                pids.push(leader);
                pids.sort_unstable();
                pids.dedup();
                for pid in pids {
                    hosts.extend(netwatch::sample(pid));
                }
                last_sample = Some(Instant::now());
            }
            if should_stop() {
                self.kill("stop");
                return Err(HostError::Stopped);
            }
            let now = Instant::now();
            if now >= deadline_at {
                self.kill("deadline");
                return Err(HostError::Timeout);
            }
            match self.link.recv_line(now + POLL_EVERY.min(deadline_at - now)) {
                Ok(Some(text)) => match classify(&text, id) {
                    Line::Response(response) => break response,
                    Line::Other => self.log_line(&text),
                    Line::Foreign(other) => {
                        self.kill("foreign response id");
                        return Err(protocol(
                            ProtocolFault::ForeignId,
                            format!("response id {other} while request {id} was in flight"),
                        ));
                    }
                    Line::Malformed(why) => {
                        self.kill("malformed response");
                        return Err(protocol(
                            ProtocolFault::MalformedResponse,
                            format!("response {id} is unreadable ({why}): {}", excerpt(&text)),
                        ));
                    }
                },
                Ok(None) => {}
                Err(_) => return Err(HostError::Dead(self.settle_dead())),
            }
        };
        Ok(Exchange {
            response,
            duration_ms: millis(started.elapsed()),
            hosts_seen: hosts.into_iter().collect(),
        })
    }

    /// Read back the evidence a response names, each file once and by name
    /// (ruling R9): the name must pass the artefact-kind rule and be declared
    /// for the action, so `<dir>/<name>` cannot leave the directory; that path
    /// is what is read — opened without following a link of any kind, and
    /// only if it is a regular file. The driver's `path` is a cross-check and
    /// never what is opened: it must resolve to that same file, or the
    /// evidence is an escape — a path that does not exist or sits anywhere
    /// else is `EvidenceEscape`, not a protocol error. Sizes stay under the
    /// per-file cap and the running per-action total.
    fn collect_evidence(
        &self,
        refs: &[EvidenceRef],
        action: &DriverAction,
        dir: &Path,
        caps: EvidenceCaps,
    ) -> Result<Vec<EvidenceFile>, HostError> {
        let mut total = 0u64;
        let mut seen = BTreeSet::new();
        let mut files = Vec::with_capacity(refs.len());
        for r in refs {
            if !plain_token(&r.name) || !action.evidence.contains(&r.name) {
                return Err(HostError::EvidenceNotDeclared(r.name.clone()));
            }
            if !seen.insert(r.name.as_str()) {
                return Err(protocol(
                    ProtocolFault::MalformedResponse,
                    format!("evidence {} is listed twice", r.name),
                ));
            }
            let ours = dir.join(&r.name);
            // The path the driver gives must be our file. Both sides resolve
            // the same way, so a link at either shows as a difference, and a
            // path that does not exist resolves to nothing.
            match (std::fs::canonicalize(&r.path), std::fs::canonicalize(&ours)) {
                (Ok(claimed), Ok(expected)) if claimed == expected => {}
                _ => return Err(HostError::EvidenceEscape(r.path.clone())),
            }
            let at = |e: std::io::Error| {
                protocol(
                    ProtocolFault::HostIo,
                    format!("evidence {} at {}: {e}", r.name, ours.display()),
                )
            };
            // Only a regular file at `<dir>/<name>` itself: a symlink, a
            // junction or a directory there is refused here, and a link
            // planted between this check and the open is not followed by it.
            if !std::fs::symlink_metadata(&ours)
                .map_err(at)?
                .file_type()
                .is_file()
            {
                return Err(HostError::EvidenceEscape(r.name.clone()));
            }
            let mut file = open_no_follow(&ours).map_err(at)?;
            let meta = file.metadata().map_err(at)?;
            if !meta.file_type().is_file() {
                return Err(HostError::EvidenceEscape(r.name.clone()));
            }
            let len = meta.len();
            if len > caps.per_file {
                return Err(HostError::EvidenceTooLarge {
                    name: r.name.clone(),
                    len,
                    cap: caps.per_file,
                });
            }
            if total.saturating_add(len) > caps.per_action {
                return Err(HostError::EvidenceTooLarge {
                    name: r.name.clone(),
                    len: total.saturating_add(len),
                    cap: caps.per_action,
                });
            }
            // Through the handle already open, bounded one past the cap so a
            // file that grew since its metadata was read is caught.
            let mut bytes = Vec::with_capacity(usize::try_from(len).unwrap_or(0));
            (&mut file)
                .take(caps.per_file.saturating_add(1))
                .read_to_end(&mut bytes)
                .map_err(at)?;
            let read = u64::try_from(bytes.len()).unwrap_or(u64::MAX);
            if read > caps.per_file {
                return Err(HostError::EvidenceTooLarge {
                    name: r.name.clone(),
                    len: read,
                    cap: caps.per_file,
                });
            }
            total = total.saturating_add(read);
            if total > caps.per_action {
                return Err(HostError::EvidenceTooLarge {
                    name: r.name.clone(),
                    len: total,
                    cap: caps.per_action,
                });
            }
            files.push(EvidenceFile {
                name: r.name.clone(),
                mime: r.mime.clone(),
                bytes,
            });
        }
        Ok(files)
    }

    /// A stray stdout line: into `driver.log`, attributed, so the driver's
    /// own stderr and the host's notes read apart.
    fn log_line(&mut self, line: &str) {
        tracing::debug!(line, "stray driver stdout line, logged");
        if let Err(e) = writeln!(self.log, "[host] stdout: {line}") {
            tracing::warn!(path = %self.log_path.display(), error = %e, "a stray stdout line could not be appended to driver.log");
        }
    }

    /// Kill the driver tree now and reap the child: the signal first, while
    /// the leader still pins its id, then the reap.
    fn kill(&mut self, why: &str) {
        tracing::warn!(pid = self.pid, why, "driver tree killed");
        let _ = self.link.kill();
    }

    /// The driver closed its stdout: its exit, worded for a `Dead` message
    /// (see [`DriverLink::settle_dead`]).
    fn settle_dead(&mut self) -> String {
        settle(self.link.as_mut())
    }
}

/// Kill the driver tree whose leader is `pid`, from outside the thread that
/// owns its [`DriverHost`] — what an unmount does to a host an act holds
/// (Ruling R13). The owning act sees the driver die and answers
/// [`HostError::Dead`]; dropping its host then kills and reaps what is left.
///
/// On Windows, `taskkill /PID <pid> /T /F`: Windows does not reuse an id
/// while a handle to the process is open, and the host's `Child` holds one
/// until the host is dropped, so the id still names this driver as long as
/// the caller keeps the host from being dropped meanwhile (the daemon kills
/// with its pool locked, which is where the acting thread must pass before it
/// drops its host). On Unix, `SIGKILL` to the process group the host made the
/// driver lead at spawn (`process_group(0)`, the harness's convention), so
/// the browser below it goes too. **Caveat:** a Unix id is free for reuse
/// once the leader has been reaped, and the host reaps a driver that exited
/// on its own; a driver that died in the instant before this call could, in
/// principle, leave its group id to an unrelated group. The window is the
/// span between that reap and this signal.
pub fn kill_pid_tree(pid: u32) {
    #[cfg(windows)]
    {
        use std::process::{Command, Stdio};
        let _ = Command::new("taskkill")
            .args(["/PID", &pid.to_string(), "/T", "/F"])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
    }
    #[cfg(unix)]
    {
        // Never 0 or 1: `kill(0, …)` would signal our own group and
        // `kill(-1, …)` every process we may signal.
        if let Ok(pgid) = i32::try_from(pid) {
            if pgid > 1 {
                // SAFETY: a negative pid signals the process group the host
                // made this driver lead; no other process of ours is in it.
                unsafe {
                    libc::kill(-pgid, libc::SIGKILL);
                }
            }
        }
    }
}

/// The driver's first line, within `timeout`: a hello of this protocol whose
/// actions cover the manifest's. The caller kills the tree on an `Err` other
/// than `Dead`, which the link has already settled.
fn await_hello(
    link: &mut dyn DriverLink,
    timeout: Duration,
    manifest: &DriverManifest,
) -> Result<Hello, HostError> {
    let line = match link.recv_line(Instant::now() + timeout) {
        Ok(Some(line)) => line,
        Ok(None) => return Err(HostError::HelloTimeout(timeout)),
        Err(_) => {
            return Err(HostError::Dead(format!(
                "{} before any hello",
                settle(link)
            )));
        }
    };
    let hello = serde_json::from_str::<HelloLine>(&line)
        .map_err(|e| {
            protocol(
                ProtocolFault::BadHello,
                format!("the first line is not a hello ({e}): {}", excerpt(&line)),
            )
        })?
        .hello;
    if hello.protocol != PROTOCOL {
        return Err(protocol(
            ProtocolFault::BadHello,
            format!("protocol {} is not {PROTOCOL}", hello.protocol),
        ));
    }
    let missing: Vec<&str> = manifest
        .actions
        .iter()
        .map(|a| a.name.as_str())
        .filter(|name| !hello.actions.iter().any(|h| h == name))
        .collect();
    if !missing.is_empty() {
        return Err(protocol(
            ProtocolFault::BadHello,
            format!(
                "the driver does not implement {} of the manifest's actions: {}",
                missing.len(),
                missing.join(", ")
            ),
        ));
    }
    Ok(hello)
}

/// What one stdout line is, to the request `id` in flight.
fn classify(text: &str, id: u64) -> Line {
    let Ok(value) = serde_json::from_str::<Value>(text) else {
        return Line::Other;
    };
    let Some(got) = value.get("id").and_then(Value::as_u64) else {
        return Line::Other;
    };
    if got != id {
        return Line::Foreign(got);
    }
    match serde_json::from_value::<Response>(value) {
        Ok(response) => Line::Response(response),
        Err(e) => Line::Malformed(e.to_string()),
    }
}

/// The artefact-kind rule, as `vk_contracts::driver::plain_token` states it
/// (crate-private there, so re-stated here rather than widening that API):
/// 1 to 32 bytes of `[A-Za-z0-9._-]`, not starting with `.`.
fn plain_token(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 32
        && !s.starts_with('.')
        && s.bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'.' | b'_' | b'-'))
}

/// A link failure worded for a `detail`: an I/O error as the OS says it, the
/// rest as the link does.
fn cause(e: link::LinkError) -> String {
    match e {
        link::LinkError::Io(e) => e.to_string(),
        other => other.to_string(),
    }
}

/// How the driver ended, once its link is closed ([`DriverLink::settle_dead`]).
fn settle(link: &mut dyn DriverLink) -> String {
    link.settle_dead().unwrap_or_else(cause)
}

fn open_log(path: &Path) -> Result<File, HostError> {
    OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .map_err(|e| {
            host_io(
                ProtocolFault::NotStarted,
                format!("open {}", path.display()),
                e,
            )
        })
}

/// A host-side I/O failure of this `fault`, worded with what was attempted.
fn host_io(fault: ProtocolFault, what: String, e: std::io::Error) -> HostError {
    protocol(fault, format!("{what}: {e}"))
}

fn millis(d: Duration) -> u64 {
    u64::try_from(d.as_millis()).unwrap_or(u64::MAX)
}

/// The first 200 characters of a line, for an error message.
fn excerpt(line: &str) -> String {
    let mut out: String = line.chars().take(200).collect();
    if line.chars().count() > 200 {
        out.push('…');
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_token_rule_matches_the_contracts_crate() {
        assert!(plain_token("note.txt"));
        assert!(plain_token("a-b_c.1"));
        assert!(!plain_token(""));
        assert!(!plain_token(".hidden"));
        assert!(!plain_token("has space"));
        assert!(!plain_token("sub/dir.txt"));
        assert!(!plain_token(&"x".repeat(33)));
        assert!(plain_token(&"x".repeat(32)));
    }

    #[test]
    fn lines_are_classified_by_their_id() {
        assert!(matches!(classify("Playwright warning", 1), Line::Other));
        assert!(matches!(classify(r#"{"event": "x"}"#, 1), Line::Other));
        assert!(matches!(
            classify(r#"{"id": "1", "ok": true}"#, 1),
            Line::Other
        ));
        assert!(matches!(
            classify(r#"{"id": 2, "ok": true}"#, 1),
            Line::Foreign(2)
        ));
        assert!(matches!(classify(r#"{"id": 1}"#, 1), Line::Malformed(_)));
        assert!(matches!(
            classify(r#"{"id": 1, "ok": true, "result": {"a": 1}}"#, 1),
            Line::Response(r) if r.ok && r.result["a"] == 1
        ));
    }

    #[test]
    fn an_excerpt_is_bounded() {
        assert_eq!(excerpt("short"), "short");
        let long = "x".repeat(500);
        let e = excerpt(&long);
        assert_eq!(e.chars().count(), 201);
        assert!(e.ends_with('…'));
    }
}
