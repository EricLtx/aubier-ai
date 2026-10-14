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
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, ExitStatus, Stdio};
use std::sync::mpsc::{self, Receiver, RecvTimeoutError};
use std::time::{Duration, Instant};
use vk_contracts::driver::{DriverAction, DriverManifest};
use vk_harness::confine::Governor;
use vk_harness::launch::kill_tree;
use vk_harness::netwatch;

pub mod protocol;

use protocol::HelloLine;
pub use protocol::{EvidenceRef, Hello, Request, Response};

/// How long a freshly spawned driver has to print its hello.
pub const HELLO_TIMEOUT: Duration = Duration::from_secs(30);
/// The one protocol this host speaks.
pub const PROTOCOL: u32 = 1;

/// How often `act` asks `should_stop` while a request is in flight.
const POLL_EVERY: Duration = Duration::from_millis(250);
/// How often the driver's egress is sampled: the harness's own interval.
const SAMPLE_EVERY: Duration = vk_harness::launch::SAMPLE_EVERY;
/// How long a driver that closed its stdout gets to finish exiting on its own
/// before what is left of its tree is killed and the exit read.
const EXIT_GRACE: Duration = Duration::from_secs(2);

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
/// directory that could not be made — each with its cause in the message.
#[derive(Debug, thiserror::Error)]
pub enum HostError {
    #[error("driver printed no hello within {0:?}")]
    HelloTimeout(Duration),
    #[error("driver protocol: {0}")]
    Protocol(String),
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

/// A running driver process and the host's end of its stdio. Owned by the
/// daemon as a plain value; dropping it kills the tree and reaps the child.
pub struct DriverHost {
    child: Child,
    stdin: ChildStdin,
    /// Every line the driver prints on stdout, from the reader thread, which
    /// ends on its own when the pipe closes.
    lines: Receiver<String>,
    /// The governor the tree is under: asked for its process list at every
    /// egress sample, and dropped last — after the kill in [`Drop`] — so a
    /// Job Object closing terminates whatever the driver left running.
    governor: Box<dyn Governor>,
    governed: bool,
    next_id: u64,
    hello: Hello,
    evidence_root: PathBuf,
    /// `<work_dir>/driver.log`, append: the driver's stderr goes there through
    /// its own handle, and the host appends the stray stdout lines it ignores.
    log: File,
    log_path: PathBuf,
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
    /// it); contain it under `governor` immediately, failing closed on Windows
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
            return Err(HostError::Protocol(
                "program.command is empty: nothing to spawn".into(),
            ));
        };
        std::fs::create_dir_all(work_dir)
            .map_err(|e| host_io(format!("create work dir {}", work_dir.display()), e))?;
        let log_path = work_dir.join("driver.log");
        let log = open_log(&log_path)?;
        let child_stderr = open_log(&log_path)?;
        let evidence_root = std::path::absolute(evidence_root).map_err(|e| {
            host_io(
                format!("resolve evidence root {}", evidence_root.display()),
                e,
            )
        })?;

        let mut cmd = Command::new(program);
        cmd.args(&argv[1..])
            .current_dir(work_dir)
            .env("VK_DRIVER_PROFILE_DIR", profile_dir)
            .env("VK_DRIVER_WORK_DIR", work_dir)
            .env(
                "VK_DRIVER_HEADLESS",
                if manifest.program.headless { "1" } else { "0" },
            )
            .env(
                "VK_DRIVER_CHANNEL",
                manifest.program.browser_channel.as_deref().unwrap_or(""),
            )
            .env("VK_DRIVER_HOSTS", manifest.hosts.join(","))
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::from(child_stderr));
        #[cfg(unix)]
        {
            // The driver leads its own process group, so `kill_tree` can
            // signal the group: the harness's `spawn` does the same.
            use std::os::unix::process::CommandExt;
            cmd.process_group(0);
        }
        let mut child = cmd.spawn().map_err(|e| {
            HostError::Protocol(format!(
                "`{program}` could not be started in {}: {e}",
                work_dir.display()
            ))
        })?;

        // Immediately after spawn, before the child can do much: contain the
        // tree, and remember whether the OS confirmed it.
        let governed = match governor.contain(&child) {
            Ok(()) => governor.governed(),
            Err(e) => {
                if cfg!(windows) {
                    // Fail closed: the platform has a governor and it refused,
                    // so the driver does not run at all.
                    tracing::error!(error = %format!("{e:#}"), "driver not contained: killed before it ran");
                    kill_tree(&mut child);
                    let _ = child.wait();
                    return Err(HostError::Protocol(format!("not contained: {e:#}")));
                }
                tracing::warn!(error = %format!("{e:#}"), "driver launched ungoverned: no governor on this platform");
                false
            }
        };

        let stdin = child.stdin.take().expect("stdin was piped");
        let stdout = child.stdout.take().expect("stdout was piped");
        let lines = read_lines(stdout);

        let hello = match await_hello(&lines, &mut child, hello_timeout, manifest, governed) {
            Ok(hello) => hello,
            Err(e) => {
                tracing::warn!(error = %e, "driver refused at hello; tree killed");
                kill_tree(&mut child);
                let _ = child.wait();
                return Err(e);
            }
        };
        tracing::info!(
            driver = %hello.driver,
            version = %hello.version,
            pid = child.id(),
            governed,
            "driver mounted"
        );
        Ok(DriverHost {
            child,
            stdin,
            lines,
            governor,
            governed,
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

    /// What the driver said first.
    pub fn hello(&self) -> &Hello {
        &self.hello
    }

    /// Has the driver process exited? `Some` once it has been reaped — after
    /// a deadline or STOP kill, a [`HostError::Dead`] outcome or `shutdown` —
    /// and `None` while it runs. The read-back the daemon puts in a
    /// `driver.unmounted` reason, and a test's proof that a kill killed.
    pub fn try_wait(&mut self) -> std::io::Result<Option<ExitStatus>> {
        self.child.try_wait()
    }

    /// One act. Creates `<evidence_root>/<id>/`, writes the request line, and
    /// waits for the response with that id — other lines (JSON without an
    /// `id`, or not JSON) are appended to `driver.log` and ignored; a line
    /// with another request's id, or an unreadable response under this one,
    /// kills the tree as a protocol error. `should_stop` is polled every
    /// 250 ms and the driver's egress sampled every 500 ms into `hosts_seen`.
    /// On the deadline or a STOP the tree is killed and the answer is
    /// [`HostError::Timeout`] or [`HostError::Stopped`]; a driver that exits
    /// first is [`HostError::Dead`] with its exit. Then each declared evidence
    /// file is read once — opened, its real path checked to lie under the
    /// evidence directory, its name checked against the action's declaration,
    /// its size against `caps` — and `<evidence_root>/<id>/` is removed,
    /// whatever happened. `duration_ms` is the time from the request written
    /// to the response read: the driver's time, not the host's.
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
        std::fs::create_dir_all(&dir)
            .map_err(|e| host_io(format!("create evidence dir {}", dir.display()), e))?;

        let request = Request {
            id,
            action: &action.name,
            args,
            deadline_ms: millis(deadline),
            evidence_dir: dir.display().to_string(),
        };
        let mut line = serde_json::to_string(&request).expect("a request always serialises");
        line.push('\n');

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
                Ok(ActOutcome {
                    ok: response.ok,
                    reason: response.reason,
                    detail: response.detail,
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
    pub fn shutdown(mut self) {
        self.terminate();
    }

    /// Write the request line and wait for its response, under the deadline,
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
        if let Err(e) = self
            .stdin
            .write_all(line.as_bytes())
            .and_then(|()| self.stdin.flush())
        {
            let exit = reap(&mut self.child, self.governed);
            return Err(HostError::Dead(format!(
                "{exit} (the request could not be written: {e})"
            )));
        }
        let deadline_at = started + deadline;
        let leader = self.child.id();
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
            match self.lines.recv_timeout(POLL_EVERY.min(deadline_at - now)) {
                Ok(text) => match classify(&text, id) {
                    Line::Response(response) => break response,
                    Line::Other => self.log_line(&text),
                    Line::Foreign(other) => {
                        self.kill("foreign response id");
                        return Err(HostError::Protocol(format!(
                            "response id {other} while request {id} was in flight"
                        )));
                    }
                    Line::Malformed(why) => {
                        self.kill("malformed response");
                        return Err(HostError::Protocol(format!(
                            "response {id} is unreadable ({why}): {}",
                            excerpt(&text)
                        )));
                    }
                },
                Err(RecvTimeoutError::Timeout) => {}
                Err(RecvTimeoutError::Disconnected) => {
                    return Err(HostError::Dead(reap(&mut self.child, self.governed)));
                }
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
                return Err(HostError::Protocol(format!(
                    "evidence {} is listed twice",
                    r.name
                )));
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
                HostError::Protocol(format!("evidence {} at {}: {e}", r.name, ours.display()))
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

    /// Kill the driver tree now and reap the child.
    fn kill(&mut self, why: &str) {
        tracing::warn!(pid = self.child.id(), why, "driver tree killed");
        kill_tree(&mut self.child);
        // `kill_tree` ends with this too; said again here so a `taskkill` that
        // failed cannot leave the wait below open-ended.
        let _ = self.child.kill();
        let _ = self.child.wait();
    }

    /// Kill what still runs and reap; idempotent, so `shutdown` and `Drop`
    /// may both run it. A leader already reaped still has its tree swept
    /// where no governor will ([`sweep`]); under the Job Object the drop of
    /// the governor, right after this, does that.
    fn terminate(&mut self) {
        if matches!(self.child.try_wait(), Ok(Some(_))) {
            sweep(&mut self.child, self.governed);
        } else {
            kill_tree(&mut self.child);
            let _ = self.child.kill();
        }
        let _ = self.child.wait();
    }
}

impl Drop for DriverHost {
    fn drop(&mut self) {
        self.terminate();
    }
}

/// The driver's first line, within `timeout`: a hello of this protocol whose
/// actions cover the manifest's. The caller kills the tree on an `Err`.
fn await_hello(
    lines: &Receiver<String>,
    child: &mut Child,
    timeout: Duration,
    manifest: &DriverManifest,
    governed: bool,
) -> Result<Hello, HostError> {
    let line = match lines.recv_timeout(timeout) {
        Ok(line) => line,
        Err(RecvTimeoutError::Timeout) => return Err(HostError::HelloTimeout(timeout)),
        Err(RecvTimeoutError::Disconnected) => {
            return Err(HostError::Dead(format!(
                "{} before any hello",
                reap(child, governed)
            )));
        }
    };
    let hello = serde_json::from_str::<HelloLine>(&line)
        .map_err(|e| {
            HostError::Protocol(format!(
                "the first line is not a hello ({e}): {}",
                excerpt(&line)
            ))
        })?
        .hello;
    if hello.protocol != PROTOCOL {
        return Err(HostError::Protocol(format!(
            "protocol {} is not {PROTOCOL}",
            hello.protocol
        )));
    }
    let missing: Vec<&str> = manifest
        .actions
        .iter()
        .map(|a| a.name.as_str())
        .filter(|name| !hello.actions.iter().any(|h| h == name))
        .collect();
    if !missing.is_empty() {
        return Err(HostError::Protocol(format!(
            "the driver does not implement {} of the manifest's actions: {}",
            missing.len(),
            missing.join(", ")
        )));
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

/// The reader thread over the driver's stdout: one `String` per line, bytes
/// that are not UTF-8 replaced rather than dropped. It ends on its own when
/// the pipe closes or the host is gone.
fn read_lines(stdout: impl Read + Send + 'static) -> Receiver<String> {
    let (tx, rx) = mpsc::channel();
    std::thread::spawn(move || {
        let mut reader = BufReader::new(stdout);
        let mut buf = Vec::new();
        loop {
            buf.clear();
            match reader.read_until(b'\n', &mut buf) {
                Ok(0) => break,
                Ok(_) => {
                    let line = String::from_utf8_lossy(&buf);
                    let line = line.trim_end_matches(['\n', '\r']);
                    if tx.send(line.to_owned()).is_err() {
                        break;
                    }
                }
                Err(e) if e.kind() == std::io::ErrorKind::Interrupted => {}
                Err(_) => break,
            }
        }
    });
    rx
}

/// The driver closed its stdout. An exiting process closes its pipes a moment
/// before it is gone, so it gets [`EXIT_GRACE`] to finish on its own and
/// report its true exit; what is still running after that is killed. Either
/// way, what the leader may have left behind is then swept where no governor
/// will ([`sweep`]). The answer is the exit, worded for a `Dead` message.
fn reap(child: &mut Child, governed: bool) -> String {
    let until = Instant::now() + EXIT_GRACE;
    let exit = loop {
        match child.try_wait() {
            Ok(Some(status)) => break exit_label(status),
            Ok(None) if Instant::now() < until => std::thread::sleep(Duration::from_millis(20)),
            Ok(None) => {
                tracing::warn!(
                    pid = child.id(),
                    "driver closed its stdout but kept running; tree killed"
                );
                kill_tree(child);
                let _ = child.kill();
                break match child.wait() {
                    Ok(status) => format!(
                        "stdout closed while running; killed ({})",
                        exit_label(status)
                    ),
                    Err(e) => {
                        format!("stdout closed while running; the kill could not be reaped: {e}")
                    }
                };
            }
            Err(e) => break format!("exit unreadable: {e}"),
        }
    };
    sweep(child, governed);
    exit
}

/// Sweep what a leader that is gone may have left behind, where no governor
/// will: on Unix `kill_tree` signals the leader's own process group, exact and
/// free even once the leader is reaped, so it always runs; on Windows an
/// ungoverned tree gets `taskkill /T` as a best effort (it finds children by
/// parent pid, and nothing once the parent is gone), while a governed tree is
/// left to the Job Object, which terminates every member when the governor
/// drops.
fn sweep(child: &mut Child, governed: bool) {
    if cfg!(unix) || !governed {
        kill_tree(child);
    }
}

fn exit_label(status: ExitStatus) -> String {
    match status.code() {
        Some(code) => format!("exit code {code}"),
        None => status.to_string(),
    }
}

/// Open `path` for reading without following a link at it: on Windows
/// `FILE_FLAG_OPEN_REPARSE_POINT` opens a reparse point (a symlink, a
/// junction) as itself and never as its target; on Unix `O_NOFOLLOW` refuses
/// a symlink at the final component. What the handle then is, its metadata
/// says, and the caller refuses anything but a regular file.
fn open_no_follow(path: &Path) -> std::io::Result<File> {
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        const FILE_FLAG_OPEN_REPARSE_POINT: u32 = 0x0020_0000;
        options.custom_flags(FILE_FLAG_OPEN_REPARSE_POINT);
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW);
    }
    options.open(path)
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

fn open_log(path: &Path) -> Result<File, HostError> {
    OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .map_err(|e| host_io(format!("open {}", path.display()), e))
}

/// A host-side I/O failure, worded with what was attempted.
fn host_io(what: String, e: std::io::Error) -> HostError {
    HostError::Protocol(format!("{what}: {e}"))
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
