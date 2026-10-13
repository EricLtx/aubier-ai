//! The restricted-token launch, characterised on Windows (SP1b Task 10 group G).
//!
//! These are the spike's record, not a shipped fence. They are `#[ignore]`d — a
//! founder decision stands between the mechanism here and a shipped restricted
//! harness (see `crates/vk-harness/src/restrict.rs`'s "Measured limit" note and
//! the Task 10 report's `NEEDS_CONTEXT`), so they do not gate CI.
//!
//! **Safe to run with `--ignored`:**
//! `cargo test -p vk-harness --test restricted -- --ignored`. What a test here
//! does to this session's window station and desktop is one thing: grant
//! `RESTRICTED` under the module's guard and have the guard put both DACLs back
//! entry for entry. No test removes anything. The strip
//! (`strip_restricted_from_winsta_desktop`) matches an entry's shape, not its
//! origin, and sessions on this machine carry foreign `RESTRICTED` entries of
//! exactly those shapes (measured 2026-10-13), so it is an operator's hand
//! tool, unsafe by default, and no test calls it (Ruling 40). There is no
//! command for it either: an operator calls it from a scratch program of
//! their own (see its doc in `restrict.rs`). Its predicate,
//! `AceView::is_the_grant`, is unit-tested in `restrict.rs` on synthetic
//! entries, with no window station involved.
//!
//! Grants are serialised within the process by the module's own lock, and
//! every test here that reads those DACLs to compare them holds
//! [`session_objects`] for its whole body (a `before` read taken while another
//! test's grant is held would compare against that grant), so the suite needs
//! no `--test-threads=1`. Two *processes* running it at once are not
//! serialised and are unsupported.
//!
//! What they establish:
//!
//! * `the_mechanism_launches_a_deprivileged_child` — `CreateRestrictedToken` on
//!   this process's own token plus `CreateProcessAsUserW` runs a child and
//!   captures its output (spike 4a(iii), reconfirmed), and the child is
//!   contained in the Job Object before it runs. No grant is made.
//! * `a_restricting_sid_child_cannot_initialise` — the moment a restricting SID
//!   set that could deny the sensitive objects is used, a normal Win32 child
//!   fails at initialisation (`0xC0000142`). This is the measured limit that
//!   makes group G a founder decision rather than a ship. The grant it makes
//!   visibly changes both DACLs, and the guard puts them back entry for entry.
//! * `a_test_that_fails_while_holding_the_grant_still_restores` — the guard
//!   restores on unwinding too, so a failing assertion leaves nothing behind.
//! * `a_second_holder_waits_for_the_first_to_restore` — two holders in one
//!   process are serialised, so a snapshot is never restored over a later one.
//! * `foreign_restricted_entries_survive_a_grant_and_restore` — whatever
//!   `RESTRICTED` entries the session carried before the grant are there,
//!   unchanged, after the restore.
//!
//! Every DACL comparison here is on the entries as read back (type, flags, mask
//! and the trustee's SID bytes — the SID by value), never on an SDDL rendering
//! (Ruling 35).
#![cfg(windows)]

use std::os::windows::io::RawHandle;
use std::path::Path;
use std::sync::{Mutex, MutexGuard};
use std::time::{Duration, Instant};
use vk_harness::confine::Governor;
use vk_harness::restrict::{
    grant_restricted_on_winsta_desktop, grant_restricting_sid, render_aces, winsta_desktop_aces,
    AceView, RestrictedChild, RestrictedToken,
};

type Dacls = (Vec<AceView>, Vec<AceView>);

/// Held for the whole body of every test that reads this session's window
/// station and desktop DACLs: the module's own lock serialises the grants,
/// not a test's `before`/`after` reads around them.
fn session_objects() -> MutexGuard<'static, ()> {
    static SESSION_OBJECTS: Mutex<()> = Mutex::new(());
    // A test that panicked restored in its guard's `Drop`; the poison says
    // nothing about the objects.
    SESSION_OBJECTS
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

/// Run `cmd /c echo hi` under `token`, in `cwd`, to completion (or a deadline),
/// returning its exit code and captured stdout. `contain` is what the spawn
/// calls on the suspended child before resuming it; if it refuses, the spawn
/// kills the still-suspended child and the error comes back here.
fn cmd_echo(
    token: &RestrictedToken,
    cwd: &Path,
    cfg: &Path,
    contain: impl FnOnce(RawHandle, u32) -> anyhow::Result<()>,
) -> anyhow::Result<(i32, String)> {
    let sysroot = std::env::var("SystemRoot").unwrap_or_else(|_| String::from("C:\\Windows"));
    let out = cfg.join("out.txt");
    let err = cfg.join("err.txt");
    let child = RestrictedChild::spawn(
        token,
        Path::new(&format!("{sysroot}\\System32\\cmd.exe")),
        &["/c".into(), "echo".into(), "hi".into()],
        cwd,
        &out,
        &err,
        contain,
    )?;
    let deadline = Instant::now() + Duration::from_secs(15);
    let code = loop {
        match child.try_wait()? {
            Some(c) => break c,
            None if Instant::now() >= deadline => {
                child.kill();
                break -999;
            }
            None => std::thread::sleep(Duration::from_millis(50)),
        }
    };
    Ok((code, std::fs::read_to_string(&out).unwrap_or_default()))
}

/// Print both objects' entries under a heading (`--nocapture`): the report's
/// before/after evidence.
fn show(heading: &str, (winsta, desktop): &Dacls) {
    println!(
        "{heading}\n window station:\n{}\n desktop:\n{}",
        render_aces(winsta),
        render_aces(desktop)
    );
}

/// The entries on one object that name `RESTRICTED`, by SID value.
fn restricted_entries(v: &[AceView]) -> Vec<AceView> {
    v.iter().filter(|a| a.names_restricted()).cloned().collect()
}

/// The mechanism runs a deprivileged child (no restricting SIDs): the
/// `CreateRestrictedToken` + `CreateProcessAsUserW` path is sound — and the
/// child is contained before it runs: inside `contain` it is still suspended
/// and has written nothing, the Job Object takes it and reads back that it
/// holds it, and only then does it run and produce its output (Task 10
/// review, Minor 10). No window-station grant is involved here.
#[test]
#[ignore = "group G is a founder decision; run manually with --ignored"]
fn the_mechanism_launches_a_deprivileged_child() {
    let ws = tempfile::tempdir().unwrap();
    let cfg = tempfile::tempdir().unwrap();
    grant_restricting_sid(ws.path()).unwrap();
    grant_restricting_sid(cfg.path()).unwrap();
    // A deprivileged token, no restricting SIDs: an ordinary child.
    let token = RestrictedToken::build(&[], false, true).expect("a deprivileged token");
    let governor = vk_harness::confine::JobGovernor::new(256 << 20, None).expect("a job object");
    let out_path = cfg.path().join("out.txt");
    // What the child had written when `contain` looked: recorded inside the
    // closure, asserted outside it. A child that had already run is refused
    // there, so the spawn kills it while it is still suspended — no assert
    // inside the closure leaves an uncontained process behind.
    let mut seen_at_contain: Option<String> = None;
    let ran = cmd_echo(&token, ws.path(), cfg.path(), |handle, pid| {
        let early = std::fs::read_to_string(&out_path).unwrap_or_default();
        seen_at_contain = Some(early.clone());
        anyhow::ensure!(early.is_empty(), "the child ran before it was contained");
        governor.contain_process(handle, pid)
    });
    // The spawn's own error first (re-review 2, D2): a spawn that failed
    // before `contain` was called leaves `seen_at_contain` at `None`, and the
    // assert below would then blame early output for it. When `contain`
    // refused for early output, `ran` already carries that message.
    let (code, out) = ran.expect("spawn, contain and run the deprivileged cmd.exe");
    assert_eq!(
        seen_at_contain.as_deref(),
        Some(""),
        "the child ran before it was contained"
    );
    assert!(
        governor.governed(),
        "the Job Object read back that it holds the child"
    );
    assert_eq!(code, 0, "the deprivileged child must run: {out:?}");
    assert!(out.contains("hi"), "and produce its output: {out:?}");
}

/// A restricting SID set that could fence the sensitive objects cannot
/// initialise a normal Win32 process on this machine — the measured limit —
/// and the window-station/desktop grant made for the attempt is undone
/// exactly.
#[test]
#[ignore = "group G is a founder decision; run manually with --ignored"]
fn a_restricting_sid_child_cannot_initialise() {
    let _serial = session_objects();
    let ws = tempfile::tempdir().unwrap();
    let cfg = tempfile::tempdir().unwrap();
    grant_restricting_sid(ws.path()).unwrap();
    grant_restricting_sid(cfg.path()).unwrap();

    let before = winsta_desktop_aces().expect("read both DACLs before the grant");
    show("before the grant", &before);
    // The full set the module documents, plus the window station/desktop grant.
    let grant = grant_restricted_on_winsta_desktop().expect("winsta/desktop grant");
    let during = winsta_desktop_aces().expect("read both DACLs while the grant is held");
    show("while the grant is held", &during);
    // Not a count of RESTRICTED entries: the session may carry foreign ones,
    // which a count would accept whether or not the grant did anything.
    assert_ne!(
        during.0, before.0,
        "the grant changed nothing on the window station: the restore below would prove nothing"
    );
    assert_ne!(
        during.1, before.1,
        "the grant changed nothing on the desktop: the restore below would prove nothing"
    );

    let token = RestrictedToken::for_this_process().expect("the restricting token");
    let ran = cmd_echo(&token, ws.path(), cfg.path(), |_, _| Ok(()));

    // Restore explicitly, so a failure to restore fails the test rather than
    // being a line on stderr from the guard's `Drop`.
    grant
        .restore()
        .expect("restore the window station's and desktop's DACLs");
    let after = winsta_desktop_aces().expect("read both DACLs after the restore");
    show("after the guard restored", &after);
    // `after == before`, entry for entry and SID by value, is the whole
    // claim — foreign RESTRICTED entries included, which must come back as
    // they were, not vanish.
    assert_eq!(after, before, "both DACLs must be exactly what they were");

    let (code, out) = ran.expect("spawn the restricted cmd.exe");
    assert_ne!(
        code, 0,
        "a restricting-SID child is expected to fail to initialise; if this ever runs, the \
         fence can be shipped — revisit group G: {out:?}"
    );
}

/// The guard restores on unwinding: a test that fails while holding the grant
/// still leaves both DACLs exactly as they were.
#[test]
#[ignore = "group G is a founder decision; run manually with --ignored"]
fn a_test_that_fails_while_holding_the_grant_still_restores() {
    let _serial = session_objects();
    let before = winsta_desktop_aces().expect("read both DACLs before");
    let outcome = std::panic::catch_unwind(|| {
        let _grant = grant_restricted_on_winsta_desktop().expect("winsta/desktop grant");
        let during = winsta_desktop_aces().expect("read both DACLs while held");
        assert_ne!(
            during, before,
            "the grant must be visible, or the restore proves nothing"
        );
        panic!("a failing assertion, on purpose, while the grant is held");
    });
    let payload = outcome.expect_err("the closure must have panicked");
    let why = payload
        .downcast_ref::<&str>()
        .map(|s| s.to_string())
        .or_else(|| payload.downcast_ref::<String>().cloned())
        .unwrap_or_default();
    assert!(
        why.contains("on purpose"),
        "the closure panicked for another reason: {why}"
    );
    let after = winsta_desktop_aces().expect("read both DACLs after the unwind");
    show("after the unwind", &after);
    assert_eq!(
        after, before,
        "the guard's Drop must have restored both DACLs"
    );
}

/// Two holders in one process are serialised (Task 10 review, Minor 7): the
/// second grant blocks until the first guard has restored, so a snapshot is
/// never restored over a later one.
#[test]
#[ignore = "group G is a founder decision; run manually with --ignored"]
fn a_second_holder_waits_for_the_first_to_restore() {
    let _serial = session_objects();
    let before = winsta_desktop_aces().expect("read both DACLs before");
    let first = grant_restricted_on_winsta_desktop().expect("the first grant");
    let (tx, rx) = std::sync::mpsc::channel();
    let second = std::thread::spawn(move || {
        let guard = grant_restricted_on_winsta_desktop().expect("the second grant");
        tx.send(()).expect("report the grant");
        guard.restore().expect("restore the second");
    });
    assert!(
        rx.recv_timeout(Duration::from_millis(300)).is_err(),
        "the second holder must wait while the first is held"
    );
    first.restore().expect("restore the first");
    rx.recv_timeout(Duration::from_secs(5))
        .expect("the second holder proceeds once the first has restored");
    second.join().expect("the second holder's thread");
    let after = winsta_desktop_aces().expect("read both DACLs after");
    assert_eq!(after, before, "both DACLs are exactly what they were");
}

/// The `RESTRICTED` entries a session carries before any grant — foreign ones:
/// measured 2026-10-13, a fresh logon session on this machine had them on both
/// objects, in the very shapes the strip matches — are there, unchanged and in
/// place, after a grant and its restore. What is asserted is what is true of
/// any session; nothing assumes there are none.
#[test]
#[ignore = "group G is a founder decision; run manually with --ignored"]
fn foreign_restricted_entries_survive_a_grant_and_restore() {
    let _serial = session_objects();
    let before = winsta_desktop_aces().expect("read both DACLs as found");
    show("as found", &before);
    let found = (restricted_entries(&before.0), restricted_entries(&before.1));
    println!(
        "RESTRICTED entries found before any grant: window station {}, desktop {}",
        found.0.len(),
        found.1.len()
    );
    let grant = grant_restricted_on_winsta_desktop().expect("winsta/desktop grant");
    let during = winsta_desktop_aces().expect("read both DACLs while held");
    assert_ne!(
        during, before,
        "the grant must be visible, or the restore proves nothing"
    );
    grant
        .restore()
        .expect("restore the window station's and desktop's DACLs");
    let after = winsta_desktop_aces().expect("read both DACLs after the restore");
    show("after the restore", &after);
    // Entry for entry, SID by value: the foreign RESTRICTED entries found
    // above are therefore exactly the ones left (a per-object check of them
    // would add nothing — re-review 2, D3).
    assert_eq!(after, before, "both DACLs are exactly what they were");
}
