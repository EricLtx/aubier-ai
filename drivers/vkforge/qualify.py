"""vkforge qualify — the generated driver replayed on copies of the corpus, postconditions re-mined (stage `qualify`).

The qualification is the learned driver's bench test. The driver runs as the kernel runs a driver —
from its own directory, with no repository around it, one protocol-1 process for the run, `cwd` the
work directory — on a copy of every document the miner mined; the originals are never handed to it.
After each `ok` answer the miner reads the copy again: a postcondition holds when the observation of
its (kind, where) has the invariant's value (spec §5.2: checked in code, never taken from the driver's
word), `pdf_exists` when the PDF beside the copy starts with `%PDF`. `qualification.json` records, per
pass, the document's hash, the duration, each postcondition and the refusal word, and no file name
(spec §5.5: a name is content); the kernel hashes its bytes into `qualification_sha256`, which the
mount gate checks (Task 9).

With `fake`, `<work>/fake.json` makes the driver load the repository's fake application (L-P19). The
driver is this stage's child, inside the stage's Job (the governor), not a job step under the
harness: the deviation from spec §5.2's "under the harness, supervised" that L-P32 declares.
"""
import argparse
import json
import os
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from vkdriver.hash_dir import hash_dir
from vkdriver.protocol import PROTOCOL

from . import canon, mine, ontology
from .errors import Refusal

PASS_DEADLINE_S = 120.0
HELLO_TIMEOUT_S = 30.0
END_WAIT_S = 5.0        # stdin closed, the driver's own end (it quits the Office it started, L-P46) before a kill
VKFORGE_ROOT = Path(__file__).resolve().parent.parent       # drivers/: what fake.json names (L-P19)
REASONS = ("dialog", "timeout", "host", "precondition", "internal")    # protocol 1's refusal words
_WHERE = {ident: key for key, ident in mine.INVARIANT_IDS.items()}     # post id → the (kind, where) it observes
_NOT_THIS_PLAN = "pass the plan.json the driver was generated from, or generate it again"
_LATE = object()


def _kill_tree(proc: subprocess.Popen) -> None:
    """The driver and every process it started: taskkill /T on Windows, its session elsewhere. An
    Office instance a COM call started is not in the tree (DCOM launches it): never killed here."""
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=False)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
    try:
        proc.kill()
    except OSError:
        pass
    proc.wait()


class _Driver:
    """One process of the generated driver: requests on its stdin; its stdout read by a thread into a
    queue, so that each answer is awaited against its own deadline; its stderr appended to driver.log."""

    def __init__(self, command: list, work: Path, env: dict, log):
        self.proc = subprocess.Popen(command, cwd=work, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=log, **({} if sys.platform == "win32" else {"start_new_session": True}))
        self.lines = queue.Queue()
        self.reader = threading.Thread(target=self._read, name="driver stdout", daemon=True)
        self.reader.start()

    def _read(self) -> None:
        try:
            for raw in self.proc.stdout:
                self.lines.put(raw.decode("utf-8", "replace"))
        except (OSError, ValueError):
            pass
        finally:
            self.lines.put(None)                    # the end of its stdout: the process has ended
            self.proc.stdout.close()

    def line(self, until: float):
        """The next line the driver wrote; None when its stdout has ended; _LATE past `until`."""
        try:
            return self.lines.get(timeout=max(0.0, until - time.monotonic()))
        except queue.Empty:
            return _LATE

    def hello(self) -> dict | None:
        line = self.line(time.monotonic() + HELLO_TIMEOUT_S)
        try:
            hello = json.loads(line) if isinstance(line, str) else None
        except ValueError:
            return None
        return hello.get("hello") if isinstance(hello, dict) and isinstance(hello.get("hello"), dict) else None

    def ask(self, request: dict, deadline_s: float):
        """(the answer, None); (None, "timeout") past `deadline_s`; (None, "internal") when the process
        ended without answering. A line that is not the answer to this request is skipped."""
        until = time.monotonic() + deadline_s
        try:
            self.proc.stdin.write(json.dumps(request).encode("utf-8") + b"\n")
            self.proc.stdin.flush()
        except OSError:
            return None, "internal"
        while True:
            line = self.line(until)
            if line is _LATE:
                return None, "timeout"
            if line is None:
                return None, "internal"
            try:
                answer = json.loads(line)
            except ValueError:
                continue
            if isinstance(answer, dict) and answer.get("id") == request["id"]:
                return answer, None

    def end(self) -> None:
        """On every path: stdin closed, then END_WAIT_S for the driver's own end (serve returns, _quit
        quits the instance it started, L-P46/L-P49); only past that wait, the kill, with the tree."""
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(END_WAIT_S)
        except subprocess.TimeoutExpired:
            _kill_tree(self.proc)
        self.reader.join(END_WAIT_S)


def _start(command: list, work: Path, env: dict, log, driver_dir: Path, plan_sha256: str) -> _Driver:
    """A driver process whose hello came within HELLO_TIMEOUT_S, speaks protocol 1 and names this plan
    (the generator's VERSION is canon.sha256(plan)); else it is ended and the stage refuses."""
    driver = _Driver(command, work, env, log)
    hello = driver.hello()
    refusal = None
    if hello is None:
        refusal = Refusal(f"driver {driver_dir} sent no hello within {HELLO_TIMEOUT_S:g} s",
                          f"read {work / 'driver.log'}, then generate it again")
    elif hello.get("protocol") != PROTOCOL:
        refusal = Refusal(f"driver {driver_dir} does not speak protocol {PROTOCOL}",
                          "generate it again: the generator vendors vkdriver (L-P6)")
    elif hello.get("version") != plan_sha256:
        refusal = Refusal(f"driver {driver_dir} was not generated from this plan", _NOT_THIS_PLAN)
    if refusal is not None:
        driver.end()
        raise refusal
    return driver


def _check(driver_dir: Path, corpus: Path, plan: dict, invariants: dict, work: Path) -> tuple[dict, dict, dict]:
    """Every refusal before any copy, in this order; (the manifest, the plan's action, the manifest's
    entry for it)."""
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
    if mine.content_sha256(corpus) != (invariants.get("corpus") or {}).get("content_sha256"):
        raise Refusal("corpus changed since vkforge mine",
                      "qualify on the corpus the driver was learned from, or learn again")
    action = plan["actions"][0]                     # v1: one action per plan (plan.build)
    entry = next((a for a in manifest.get("actions") or [] if isinstance(a, dict) and a.get("name") == action["name"]),
                 None)
    if entry is None:
        raise Refusal(f"driver {driver_dir} was not generated from this plan", _NOT_THIS_PLAN)
    return manifest, action, entry


def _expected(plan: dict, invariants: dict) -> dict:
    """{invariant id: the value its postcondition must find}: the invariant's, else the value of the
    plan's ambiguity an arch resolved (present exactly when resolved)."""
    resolved = {a["invariant"]: a["value"] for a in plan.get("ambiguities") or [] if a.get("value") is not None}
    return {**resolved, **{i["id"]: i["value"] for i in invariants.get("invariants") or [] if isinstance(i, dict)}}


def _is_pdf(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"%PDF"
    except OSError:
        return False


def _post(post: list, copy: Path, expected: dict) -> dict:
    """Each post id of the plan, held or not on the copy as the miner reads it again (values compared
    in canonical form, as the planner compares candidates)."""
    try:
        obs = mine.mine_file(copy)["obs"]
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


def _pass(driver: _Driver, index: int, source: Path, work: Path, action: dict, entry: dict, expected: dict,
          deadline_s: float) -> tuple[dict, bool]:
    """(the run of corpus file `index`, whether the driver process must be ended): the document copied
    to work/<index>/ with its attributes (a read-only document stays read-only), the action asked once."""
    folder = work / str(index)
    evidence = folder / "evidence"
    evidence.mkdir(parents=True)
    copy = folder / source.name
    shutil.copy2(source, copy)
    file_sha256 = canon.sha256_file(copy)
    args = {"document": str(copy)}
    if "pdf" in ((entry.get("args") or {}).get("required") or []):
        args["pdf"] = str(copy.with_suffix(".pdf"))
    request = {"id": index + 1, "action": action["name"], "args": args, "deadline_ms": int(deadline_s * 1000),
               "evidence_dir": str(evidence)}
    started = time.monotonic()
    answer, fault = driver.ask(request, deadline_s)
    duration_ms = int((time.monotonic() - started) * 1000)
    none = {ident: False for ident in action["post"]}
    if fault is not None:
        post, ok, reason = none, False, fault
    elif answer.get("ok") is True:
        post = _post(action["post"], copy, expected)
        ok = all(post.values())
        # The word the generated driver itself answers when its own checks fail (Task 7): the same
        # condition carries the same word whoever saw it.
        reason = None if ok else "precondition"
    else:
        post, ok = none, False
        reason = answer.get("reason") if answer.get("reason") in REASONS else "internal"
    run = {"index": index, "file_sha256": file_sha256, "duration_ms": duration_ms, "post": post, "ok": ok,
           "reason": reason}
    return run, fault is not None


def qualify(driver_dir: Path, corpus: Path, plan: dict, invariants: dict, work: Path, fake: bool,
            deadline_s: float = PASS_DEADLINE_S) -> dict:
    """The record, also written as <work>/qualification.json (canon.write)."""
    driver_dir, corpus, work = Path(os.path.abspath(driver_dir)), Path(corpus), Path(os.path.abspath(work))
    manifest, action, entry = _check(driver_dir, corpus, plan, invariants, work)
    mined, _ = mine._survey(corpus)
    files = sorted((p for p, _ in mined), key=lambda p: p.name)
    work.mkdir(parents=True, exist_ok=True)
    if fake:
        canon.write({"vkforge_root": str(VKFORGE_ROOT)}, work / "fake.json")
    (work / "profile").mkdir()
    env = {**os.environ, "VK_DRIVER_WORK_DIR": str(work), "VK_DRIVER_PROFILE_DIR": str(work / "profile"),
           "VK_DRIVER_APPLICATIONS": ",".join(manifest.get("applications") or [])}
    # The vkforge interpreter: the virtualenv the manifest's .venv links to (L-P12).
    command = [sys.executable, "-I", str(driver_dir / "driver.py")]
    plan_sha256, expected = canon.sha256(plan), _expected(plan, invariants)
    runs = []
    with open(work / "driver.log", "ab") as log:
        driver = _start(command, work, env, log, driver_dir, plan_sha256)
        try:
            for index, source in enumerate(files):
                if driver is None:                  # the last one was killed: a new process for this pass
                    driver = _start(command, work, env, log, driver_dir, plan_sha256)
                run, ended = _pass(driver, index, source, work, action, entry, expected, deadline_s)
                runs.append(run)
                if ended:
                    driver.end()
                    driver = None
        finally:
            if driver is not None:
                driver.end()
    passes = sum(r["ok"] for r in runs)
    record = {"files_sha256": manifest["program"]["files_sha256"], "plan_sha256": plan_sha256, "fake": bool(fake),
              "passes": passes, "failures": len(runs) - passes,
              "rate": round(passes / len(runs), 4) if runs else 0.0, "runs": runs}
    canon.write(record, work / "qualification.json")
    return record


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--driver", required=True, type=Path, help="the generated driver's directory")
    parser.add_argument("--corpus", required=True, type=Path, help="the corpus the driver was learned from")
    parser.add_argument("--plan", required=True, type=Path, help="plan.json the driver was generated from")
    parser.add_argument("--invariants", required=True, type=Path, help="invariants.json the plan was built from")
    parser.add_argument("--work", required=True, type=Path,
                        help="new or empty directory that receives the copies and qualification.json")
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
    rec = qualify(ns.driver, ns.corpus, p, invariants, ns.work, ns.fake)
    print(f"qualify: {rec['passes']}/{rec['passes'] + rec['failures']} passes ({rec['failures']} failures), "
          f"fake {'true' if rec['fake'] else 'false'} -> {Path(ns.work) / 'qualification.json'}")
    return 0
