//! The link between the host and one driver (spec §4.6): the seam that keeps
//! the manifest, the ledger and this crate from moving again when the
//! user-session agent (`vk-agent`) arrives.
//!
//! A [`DriverLink`] is a line-oriented channel to a driver plus the handle on
//! its life: start it, send a line, receive a line before a deadline, read its
//! evidence directory, kill it. [`LocalLink`] is the process the daemon spawns
//! itself, spoken to over its stdio — everything [`DriverHost`](crate::DriverHost)
//! did with the `Child` before this seam existed, moved here unchanged.
//! [`AgentLink`] is declared for the next chantier and answers
//! [`LinkError::NotAvailable`] to every call.
//!
//! The tree discipline (M18) lives in [`LocalLink`]: the tree is signalled once
//! and never after its leader is reaped, in the order *observe the exit
//! without reaping, sweep, reap*. The host only decides when.
use std::fs::{File, OpenOptions};
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, ExitStatus, Stdio};
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::mpsc::{self, Receiver, RecvTimeoutError};
use std::sync::Arc;
use std::time::{Duration, Instant};
use vk_harness::launch::kill_tree;

/// How long a driver that closed its stdout gets to finish exiting on its own
/// before what is left of its tree is killed and the exit read.
const EXIT_GRACE: Duration = Duration::from_secs(2);

/// How long a running driver whose stdin the host closed — the protocol's end
/// — gets to exit on its own before its tree is killed (ruling L-P49): a
/// learned driver quits the Office instance it started on its way out (L-P46).
const STOP_GRACE: Duration = Duration::from_secs(5);

/// What a link needs to start a driver: the command line, where it runs, the
/// environment it is given on top of the daemon's, and the file its stderr is
/// appended to.
#[derive(Debug, Clone)]
pub struct SpawnSpec {
    pub argv: Vec<String>,
    pub work_dir: PathBuf,
    pub env: Vec<(String, String)>,
    pub log_file: PathBuf,
}

/// A driver that was started.
#[derive(Debug, Clone, Copy)]
pub struct Spawned {
    pub pid: u32,
}

/// One file read back from an evidence directory by [`DriverLink::evidence`].
/// Not the host's own `EvidenceFile`, which also carries the mime type the
/// driver declared: a link knows files, not the protocol.
#[derive(Debug, Clone)]
pub struct EvidenceFile {
    pub name: String,
    pub bytes: Vec<u8>,
}

/// Why a link call failed.
#[derive(Debug, thiserror::Error)]
pub enum LinkError {
    #[error("link: {0}")]
    Io(#[from] std::io::Error),
    #[error("link: not available: {0}")]
    NotAvailable(&'static str),
    #[error("link: closed")]
    Closed,
}

/// The host's end of a driver, wherever the driver runs.
///
/// The first seven methods are the seam of spec §4.6. The last four
/// ([`set_governed`](Self::set_governed), [`try_wait`](Self::try_wait),
/// [`settle_dead`](Self::settle_dead), [`swept`](Self::swept)) are what the
/// host still asks of a driver's life once the `Child` is behind the link: the
/// public `DriverHost::try_wait` and its test read-back need them.
pub trait DriverLink: Send {
    /// Start the driver. Called once.
    fn spawn(&mut self, spec: SpawnSpec) -> Result<Spawned, LinkError>;
    /// Send one line; the terminator is the link's to add.
    fn send_line(&mut self, line: &str) -> Result<(), LinkError>;
    /// The next line the driver prints, without its terminator. `Ok(None)`
    /// when `deadline` passes first; [`LinkError::Closed`] when the driver
    /// closed its output and nothing more will come.
    fn recv_line(&mut self, deadline: Instant) -> Result<Option<String>, LinkError>;
    /// The regular files directly under `dir`, by name, none read through a
    /// link. No size cap: the caps are the host's policy (`DriverHost::act`
    /// reads evidence by declared name, under its caps).
    fn evidence(&mut self, dir: &Path) -> Result<Vec<EvidenceFile>, LinkError>;
    /// Kill the driver's whole tree now and reap it. Idempotent.
    fn kill(&mut self) -> Result<(), LinkError>;
    /// Is the driver still running?
    fn alive(&mut self) -> bool;
    /// `LocalLink` only: the process, which the governor needs a handle on.
    fn child(&self) -> Option<&Child>;

    /// Tell the link whether the OS confirmed the tree is under its governor:
    /// an ungoverned tree is swept at exit, a governed one is left to the Job.
    fn set_governed(&mut self, governed: bool);
    /// Has the driver exited? `Some` once it has been reaped; a driver gone
    /// that nothing had swept yet is settled here (observe, sweep, reap).
    fn try_wait(&mut self) -> Result<Option<ExitStatus>, LinkError>;
    /// The driver closed its output: settle what is left of it (grace, sweep,
    /// reap, or kill) and say how it ended, worded for a `Dead` message.
    fn settle_dead(&mut self) -> Result<String, LinkError>;
    /// Has the tree been signalled and the leader reaped, so nothing will
    /// signal it again? A test's read-back only.
    #[doc(hidden)]
    fn swept(&self) -> bool;
}

struct Proc {
    child: Child,
    /// `None` once [`LocalLink::terminate`] has closed it to stop the driver.
    stdin: Option<ChildStdin>,
    /// Every line the driver prints on stdout, from the reader thread, which
    /// ends on its own when the pipe closes.
    lines: Receiver<String>,
}

/// The process the daemon spawns itself, spoken to over its stdio. Dropping it
/// kills the tree and reaps the child.
pub struct LocalLink {
    proc: Option<Proc>,
    governed: bool,
    /// The tree has been signalled and the leader reaped — by a kill, the reap
    /// after the driver closed its stdout, or a `try_wait` that saw it gone.
    /// Nothing signals it again: a reaped leader's id is free for reuse, and a
    /// group signal sent later could reach someone else's group (the
    /// harness's M18 rule).
    swept: bool,
    /// Every time the tree is signalled, counted; shared with the host's
    /// `TreeSignals` so a test reads it after the drop.
    signals: Arc<AtomicU32>,
}

impl LocalLink {
    /// A link that has not started anything yet; `signals` counts the times
    /// its tree is signalled.
    pub fn new(signals: Arc<AtomicU32>) -> LocalLink {
        LocalLink {
            proc: None,
            governed: false,
            swept: false,
            signals,
        }
    }

    /// Kill what still runs and reap; idempotent, so `Drop` may run it after
    /// any kill. A tree already swept — killed, reaped after it closed its
    /// stdout, seen gone by `try_wait` — is left as it is: its leader is
    /// reaped and its id may name another group by now. Otherwise in M18's
    /// order: a leader seen exited — observed without reaping it
    /// ([`exited`]) — has what it left behind swept where no governor will
    /// ([`sweep`]), a leader still running (or whose exit cannot be observed)
    /// has its tree killed, and only then is it reaped. Under the Job Object
    /// the drop of the governor, right after the link's, ends what is left.
    ///
    /// A leader still running is first stopped the way the protocol ends
    /// (ruling L-P49): its stdin is closed and it gets [`STOP_GRACE`] to exit
    /// on its own — a learned driver quits the Office instance it started on
    /// its way out (L-P46), which a kill would leave running. Its exit is
    /// observed as above, without reaping it; one that is still running when
    /// the grace is over is killed as before.
    fn terminate(&mut self) {
        let Some(proc) = self.proc.as_mut() else {
            return;
        };
        if !self.swept {
            if matches!(exited(&mut proc.child), Ok(false)) {
                drop(proc.stdin.take());
                let until = Instant::now() + STOP_GRACE;
                while matches!(exited(&mut proc.child), Ok(false)) && Instant::now() < until {
                    std::thread::sleep(Duration::from_millis(20));
                }
            }
            match exited(&mut proc.child) {
                Ok(true) => sweep(&mut proc.child, self.governed, &self.signals),
                Ok(false) | Err(_) => {
                    signal_tree(&mut proc.child, &self.signals);
                    let _ = proc.child.kill();
                }
            }
            self.swept = true;
        }
        let _ = proc.child.wait();
    }
}

impl DriverLink for LocalLink {
    fn spawn(&mut self, spec: SpawnSpec) -> Result<Spawned, LinkError> {
        if self.proc.is_some() {
            return Err(std::io::Error::new(
                std::io::ErrorKind::AlreadyExists,
                "the link already started its driver",
            )
            .into());
        }
        let Some(program) = spec.argv.first() else {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidInput,
                "argv is empty: nothing to spawn",
            )
            .into());
        };
        let stderr = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&spec.log_file)?;
        let mut cmd = Command::new(program);
        cmd.args(&spec.argv[1..])
            .current_dir(&spec.work_dir)
            .envs(spec.env.iter().map(|(k, v)| (k, v)))
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::from(stderr));
        #[cfg(unix)]
        {
            // The driver leads its own process group, so `kill_tree` can
            // signal the group: the harness's `spawn` does the same.
            use std::os::unix::process::CommandExt;
            cmd.process_group(0);
        }
        let mut child = cmd.spawn()?;
        let stdin = child.stdin.take().expect("stdin was piped");
        let stdout = child.stdout.take().expect("stdout was piped");
        let pid = child.id();
        self.proc = Some(Proc {
            child,
            stdin: Some(stdin),
            lines: read_lines(stdout),
        });
        Ok(Spawned { pid })
    }

    fn send_line(&mut self, line: &str) -> Result<(), LinkError> {
        let proc = self.proc.as_mut().ok_or(LinkError::Closed)?;
        let stdin = proc.stdin.as_mut().ok_or(LinkError::Closed)?;
        let mut bytes = Vec::with_capacity(line.len() + 1);
        bytes.extend_from_slice(line.as_bytes());
        bytes.push(b'\n');
        stdin.write_all(&bytes).and_then(|()| stdin.flush())?;
        Ok(())
    }

    fn recv_line(&mut self, deadline: Instant) -> Result<Option<String>, LinkError> {
        let proc = self.proc.as_mut().ok_or(LinkError::Closed)?;
        match proc
            .lines
            .recv_timeout(deadline.saturating_duration_since(Instant::now()))
        {
            Ok(line) => Ok(Some(line)),
            Err(RecvTimeoutError::Timeout) => Ok(None),
            Err(RecvTimeoutError::Disconnected) => Err(LinkError::Closed),
        }
    }

    fn evidence(&mut self, dir: &Path) -> Result<Vec<EvidenceFile>, LinkError> {
        let mut files = Vec::new();
        for entry in std::fs::read_dir(dir)? {
            let entry = entry?;
            // `DirEntry::file_type` does not follow a link: a symlink, a
            // junction or a directory is skipped, never read through.
            if !entry.file_type()?.is_file() {
                continue;
            }
            let mut bytes = Vec::new();
            open_no_follow(&entry.path())?.read_to_end(&mut bytes)?;
            files.push(EvidenceFile {
                name: entry.file_name().to_string_lossy().into_owned(),
                bytes,
            });
        }
        files.sort_by(|a, b| a.name.cmp(&b.name));
        Ok(files)
    }

    /// Signal the tree first, while the leader still pins its id, then reap.
    fn kill(&mut self) -> Result<(), LinkError> {
        let Some(proc) = self.proc.as_mut() else {
            return Ok(());
        };
        signal_tree(&mut proc.child, &self.signals);
        // `kill_tree` ends with this too; said again here so a `taskkill` that
        // failed cannot leave the wait below open-ended.
        let _ = proc.child.kill();
        let _ = proc.child.wait();
        self.swept = true;
        Ok(())
    }

    fn alive(&mut self) -> bool {
        matches!(self.try_wait(), Ok(None))
    }

    fn child(&self) -> Option<&Child> {
        self.proc.as_ref().map(|p| &p.child)
    }

    fn set_governed(&mut self, governed: bool) {
        self.governed = governed;
    }

    fn try_wait(&mut self) -> Result<Option<ExitStatus>, LinkError> {
        let proc = self.proc.as_mut().ok_or(LinkError::Closed)?;
        if self.swept {
            return Ok(proc.child.try_wait()?);
        }
        if !exited(&mut proc.child)? {
            return Ok(None);
        }
        sweep(&mut proc.child, self.governed, &self.signals);
        self.swept = true;
        Ok(Some(proc.child.wait()?))
    }

    /// The first time, [`reap`] sweeps the tree and settles it; a leader
    /// already swept and reaped — a second act on a dead host — is only read
    /// back, its id never signalled again.
    fn settle_dead(&mut self) -> Result<String, LinkError> {
        let proc = self.proc.as_mut().ok_or(LinkError::Closed)?;
        if self.swept {
            return Ok(match proc.child.try_wait() {
                Ok(Some(status)) => exit_label(status),
                Ok(None) => "killed; exit not read".into(),
                Err(e) => format!("exit unreadable: {e}"),
            });
        }
        let exit = reap(&mut proc.child, self.governed, &self.signals);
        self.swept = true;
        Ok(exit)
    }

    fn swept(&self) -> bool {
        self.swept
    }
}

impl Drop for LocalLink {
    fn drop(&mut self) {
        self.terminate();
    }
}

/// Declared for the next chantier, when the user-session agent runs the driver
/// in the user's own session: every call answers
/// [`LinkError::NotAvailable`]`("vk-agent")`.
pub struct AgentLink;

fn agent() -> LinkError {
    LinkError::NotAvailable("vk-agent")
}

impl DriverLink for AgentLink {
    fn spawn(&mut self, _spec: SpawnSpec) -> Result<Spawned, LinkError> {
        Err(agent())
    }
    fn send_line(&mut self, _line: &str) -> Result<(), LinkError> {
        Err(agent())
    }
    fn recv_line(&mut self, _deadline: Instant) -> Result<Option<String>, LinkError> {
        Err(agent())
    }
    fn evidence(&mut self, _dir: &Path) -> Result<Vec<EvidenceFile>, LinkError> {
        Err(agent())
    }
    fn kill(&mut self) -> Result<(), LinkError> {
        Err(agent())
    }
    fn alive(&mut self) -> bool {
        false
    }
    fn child(&self) -> Option<&Child> {
        None
    }
    fn set_governed(&mut self, _governed: bool) {}
    fn try_wait(&mut self) -> Result<Option<ExitStatus>, LinkError> {
        Err(agent())
    }
    fn settle_dead(&mut self) -> Result<String, LinkError> {
        Err(agent())
    }
    fn swept(&self) -> bool {
        false
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
/// report its true exit. In M18's order: its exit is observed without reaping
/// it ([`exited`]), what it may have left behind is swept ([`sweep`]), and
/// only then is it reaped. One still running after the grace, or whose exit
/// cannot be observed, has its tree killed and is then reaped; nothing is
/// signalled after a reap. The answer is the exit, worded for a `Dead`
/// message.
fn reap(child: &mut Child, governed: bool, signals: &AtomicU32) -> String {
    let until = Instant::now() + EXIT_GRACE;
    loop {
        match exited(child) {
            Ok(true) => {
                sweep(child, governed, signals);
                return match child.wait() {
                    Ok(status) => exit_label(status),
                    Err(e) => format!("exit unreadable: {e}"),
                };
            }
            Ok(false) if Instant::now() < until => std::thread::sleep(Duration::from_millis(20)),
            Ok(false) => {
                tracing::warn!(
                    pid = child.id(),
                    "driver closed its stdout but kept running; tree killed"
                );
                signal_tree(child, signals);
                let _ = child.kill();
                return match child.wait() {
                    Ok(status) => format!(
                        "stdout closed while running; killed ({})",
                        exit_label(status)
                    ),
                    Err(e) => {
                        format!("stdout closed while running; the kill could not be reaped: {e}")
                    }
                };
            }
            Err(e) => {
                // Not observable without reaping: killed first, while the
                // leader — alive or a zombie — still pins its id, then reaped.
                tracing::warn!(pid = child.id(), error = %e, "driver exit not observable; tree killed");
                signal_tree(child, signals);
                let _ = child.kill();
                return match child.wait() {
                    Ok(status) => {
                        format!("exit not observable ({e}); killed ({})", exit_label(status))
                    }
                    Err(e2) => format!("exit unreadable: {e}; {e2}"),
                };
            }
        }
    }
}

/// Has the leader exited? On Unix observed **without reaping it**
/// (`vk_harness::launch::exited_unreaped`, `waitid` with `WNOWAIT`): the
/// zombie still pins its process group's id while the group is signalled
/// (M18). On Windows there is no process group, and `try_wait` is the
/// observation; the host's handle keeps the id from being reused anyway.
fn exited(child: &mut Child) -> std::io::Result<bool> {
    #[cfg(unix)]
    {
        vk_harness::launch::exited_unreaped(child)
    }
    #[cfg(not(unix))]
    {
        child.try_wait().map(|status| status.is_some())
    }
}

/// Signal the driver's whole tree — `taskkill /T /F` on Windows, `SIGKILL`
/// to the process group it leads on Unix — and count it. Every caller does
/// this before the leader is reaped.
fn signal_tree(child: &mut Child, signals: &AtomicU32) {
    signals.fetch_add(1, Ordering::SeqCst);
    kill_tree(child);
}

/// Sweep what a leader that has exited may have left behind, where no
/// governor will. Called once per link (`swept`), between the observation of
/// the exit ([`exited`]) and the reap: on Unix the leader is then an unreaped
/// zombie, which pins its process group's id, so the group signal cannot
/// reach another group (M18); on Windows an ungoverned tree gets
/// `taskkill /T` as a best effort (it finds children by parent pid, and
/// nothing once the parent is gone), while a governed tree is left to the Job
/// Object, which terminates every member when the governor drops.
fn sweep(child: &mut Child, governed: bool, signals: &AtomicU32) {
    if cfg!(unix) || !governed {
        signal_tree(child, signals);
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
/// a symlink at the final component, and `O_NONBLOCK` keeps a FIFO planted
/// at the name between the caller's check and this open from blocking the
/// open for ever (a regular file reads the same with it). What the handle
/// then is, its metadata says, and the caller refuses anything but a regular
/// file.
pub(crate) fn open_no_follow(path: &Path) -> std::io::Result<File> {
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
        options.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
    }
    options.open(path)
}

#[cfg(test)]
mod tests {
    use super::*;

    const PYTHON: &str = if cfg!(windows) { "python" } else { "python3" };

    /// Is a `python` on PATH? When not, say so — the test is then skipped out
    /// loud, never failed and never silently green.
    fn python_available() -> bool {
        match Command::new(PYTHON)
            .arg("--version")
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
        {
            Ok(status) if status.success() => true,
            other => {
                eprintln!("no {PYTHON} on PATH; skipping ({other:?})");
                false
            }
        }
    }

    #[test]
    fn local_link_round_trips_a_line_with_a_python_echo() {
        if !python_available() {
            return;
        }
        let dir = tempfile::tempdir().expect("tempdir");
        let mut link = LocalLink::new(Arc::new(AtomicU32::new(0)));
        let spawned = link
            .spawn(SpawnSpec {
                argv: vec![
                    PYTHON.into(),
                    "-u".into(),
                    "-c".into(),
                    "import sys; sys.stdout.write(sys.stdin.readline()); sys.stdout.flush()".into(),
                ],
                work_dir: dir.path().to_path_buf(),
                env: vec![("VK_LINK_TEST".into(), "1".into())],
                log_file: dir.path().join("driver.log"),
            })
            .expect("spawn the echo");
        assert_eq!(Some(spawned.pid), link.child().map(Child::id));
        assert!(link.alive());
        link.send_line("ping").expect("send");
        let soon = Instant::now() + Duration::from_secs(20);
        assert_eq!(link.recv_line(soon).expect("recv"), Some("ping".into()));
        // The echo exits after one line: its stdout closes, the link says so.
        let soon = Instant::now() + Duration::from_secs(20);
        assert!(matches!(link.recv_line(soon), Err(LinkError::Closed)));
        link.kill().expect("kill");
        assert!(!link.alive());
    }

    #[test]
    fn local_link_reads_the_regular_files_of_a_directory() {
        let dir = tempfile::tempdir().expect("tempdir");
        std::fs::write(dir.path().join("b.txt"), b"two").expect("write");
        std::fs::write(dir.path().join("a.txt"), b"one").expect("write");
        std::fs::create_dir(dir.path().join("sub")).expect("mkdir");
        let files = LocalLink::new(Arc::default())
            .evidence(dir.path())
            .expect("evidence");
        let seen: Vec<_> = files
            .iter()
            .map(|f| (f.name.as_str(), &f.bytes[..]))
            .collect();
        assert_eq!(seen, [("a.txt", &b"one"[..]), ("b.txt", &b"two"[..])]);
    }

    #[test]
    fn agent_link_is_declared_and_not_available() {
        let mut link = AgentLink;
        let spec = SpawnSpec {
            argv: vec!["x".into()],
            work_dir: PathBuf::from("."),
            env: Vec::new(),
            log_file: PathBuf::from("driver.log"),
        };
        assert!(matches!(
            link.spawn(spec),
            Err(LinkError::NotAvailable("vk-agent"))
        ));
        assert!(matches!(
            link.send_line("x"),
            Err(LinkError::NotAvailable("vk-agent"))
        ));
        assert!(matches!(
            link.recv_line(Instant::now()),
            Err(LinkError::NotAvailable("vk-agent"))
        ));
        assert!(matches!(
            link.evidence(Path::new(".")),
            Err(LinkError::NotAvailable("vk-agent"))
        ));
        assert!(matches!(
            link.kill(),
            Err(LinkError::NotAvailable("vk-agent"))
        ));
        assert!(matches!(
            link.try_wait(),
            Err(LinkError::NotAvailable("vk-agent"))
        ));
        assert!(matches!(
            link.settle_dead(),
            Err(LinkError::NotAvailable("vk-agent"))
        ));
        assert!(!link.alive());
        assert!(!link.swept());
        assert!(link.child().is_none());
    }
}
