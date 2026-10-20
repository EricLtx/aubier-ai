"""vkforge qualify — the generated driver replayed on copies of the corpus, postconditions re-mined (stage `qualify`).

The qualification is the learned driver's bench test. The driver runs as the kernel runs a driver —
from its own directory, with no repository around it, one protocol-1 process for the run, `cwd` the
work directory — on a copy of every document the miner mined; the originals are never handed to it.
The corpus documents already carry the invariants (that is how they were mined), so each copy is first
PERTURBED on every post id of the plan (L-P52: the title's style removed, the fonts changed, the footer
and header text replaced, no PDF beside it), with lxml and ooxml.write_parts, never Office: only a real
repair can then pass. After each `ok` answer the miner reads the copy again, under the learn's salt
(`--salt-file`, founder decision D-01: the tokens of `invariants.json` are keyed by it, LF-1): a
postcondition holds when the observation of its (kind, where) has the invariant's value (spec §5.2:
checked in code, never taken from the driver's word), `pdf_exists` when the PDF beside the copy starts
with `%PDF`; a copy whose bytes are still the perturbed ones was not saved. A salt that does not give
back the plan's tokens from the corpus is refused before any copy (LF-3). `qualification.json`
records, per pass, the document's hash, the duration, each postcondition and the reason, and no file
name (spec §5.5: a name is content); the kernel hashes its bytes into `qualification_sha256`, which
the mount gate checks.

A pass's reason is protocol 1's refusal word, or this stage's own: `postcondition` (the driver said ok,
the re-mined copy disagrees, L-P50) and `unsaved` (the driver said ok, the copy is byte for byte the
perturbed one). With `fake`, `<work>/fake.json` makes the driver load the repository's fake application
(L-P19). The driver is this stage's child, inside the stage's Job (the governor), not a job step under
the harness: the deviation from spec §5.2's "under the harness, supervised" that L-P32 declares. A pass
killed at its deadline never runs the driver's `_quit`; the Office instance the driver started and
identified ends with it all the same, through the kill-on-close Job the adapter holds it in (D-04),
while an instance it attached to — or could not identify (PF-4) — is left as the kill found it.
"""
import argparse
import json
import os
import queue
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

from lxml import etree
from vkdriver.hash_dir import hash_dir
from vkdriver.protocol import PROTOCOL

from . import canon, mine, ontology, ooxml
from .errors import Refusal
from .ooxml import NS, q

# L-P53: the worst case of one document, 90 s + END_WAIT_S + a new process's hello, stays under the
# 130 s per document the kernel gives the stage (Task 9).
PASS_DEADLINE_S = 90.0
HELLO_TIMEOUT_S = 10.0
END_WAIT_S = 5.0        # stdin closed, the driver's own end (its Office ends within D-35's bounds) before a kill
VKFORGE_ROOT = Path(__file__).resolve().parent.parent       # drivers/: what fake.json names (L-P19)
REASONS = ("dialog", "timeout", "host", "precondition", "internal")    # protocol 1's refusal words
PERTURBABLE = frozenset({"title_style", "body_font", "title_font", "header_text", "footer_text", "pdf_exists"})
PERTURBED_FONT = "Courier New"
PERTURBED_TEXT = "vkforge qualification: not the firm's text"
_UNDECLARED_STYLE = "VkQualifyPerturbed"    # a styleId no styles.xml declares: the miner sees no style
_WHERE = {ident: key for key, ident in mine.INVARIANT_IDS.items()}     # post id → the (kind, where) it observes
_NOT_THIS_PLAN = "pass the plan.json the driver was generated from, or generate it again"
_CORPUS_CHANGED = ("corpus changed since vkforge mine",
                   "qualify on the corpus the driver was learned from, or learn again")
_PERTURB_REMEDY = "teach drivers/vkforge/qualify.py to perturb it, so that only a real repair passes (L-P52)"
_SALT_REMEDY = "vk driver learn again: the salt and invariants.json are one learn's"
# CT_RPr's sequence up to w:sz: a child the perturbation adds goes after its predecessors (Word's schema)
_RPR_ORDER = ("rStyle", "rFonts", "b", "bCs", "i", "iCs", "caps", "smallCaps", "strike", "dstrike", "outline",
              "shadow", "emboss", "imprint", "noProof", "snapToGrid", "vanish", "webHidden", "color", "spacing",
              "w", "kern", "position", "sz")
_LATE = object()


# --- the driver process ---------------------------------------------------------------------------

def _kill_tree(proc: subprocess.Popen) -> None:
    """The driver and every process it started: taskkill /T on Windows, its session elsewhere. An
    Office instance a COM call started is not in the tree (DCOM launches it): never killed here — once
    the adapter has identified it (PF-4), it ends through the adapter's own Job when the driver does
    (D-04)."""
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
        ends the instance it started within D-35's bounds, L-P46/L-P49); only past that wait, the kill,
        with the tree — and a killed driver's Office ends through its Job (D-04)."""
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
    try:
        hello = driver.hello()
    except BaseException:                           # an interrupt while waiting: the process is ended too
        driver.end()
        raise
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


# --- the checks before any copy ---------------------------------------------------------------------

def _check(driver_dir: Path, corpus: Path, plan: dict, invariants: dict, work: Path, salt: bytes | None) -> tuple:
    """Every refusal before any copy, in this order; (the manifest, the plan's action, the manifest's
    entry for it, the corpus files sorted by name — the one survey of the corpus, under the salt —
    and the values the postconditions expect, the ones the salt was checked against)."""
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
    expected = _expected(plan, invariants)
    _salt_gives_back(mined, action["post"], expected)
    return manifest, action, entry, files, expected


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


def _content_sha256(hashes) -> str:
    """mine.content_sha256's formula, over file hashes already taken."""
    return canon.sha256(sorted(hashes))


def _learned_content(invariants: dict):
    return (invariants.get("corpus") or {}).get("content_sha256")


def _expected(plan: dict, invariants: dict) -> dict:
    """{invariant id: the value its postcondition must find}: the invariant's, else the value of the
    plan's ambiguity an arch resolved (present exactly when resolved)."""
    resolved = {a["invariant"]: a["value"] for a in plan.get("ambiguities") or [] if a.get("value") is not None}
    return {**resolved, **{i["id"]: i["value"] for i in invariants.get("invariants") or [] if isinstance(i, dict)}}


# --- the perturbation (L-P52) -----------------------------------------------------------------------

def _xml(root) -> bytes:
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _first_child(parent, local: str):
    """`parent`'s w:<local>, created as its first child when absent (w:pPr in w:p, w:pStyle in w:pPr,
    w:rPr in w:r: each is first in its schema sequence)."""
    found = parent.find(f"w:{local}", NS)
    if found is None:
        found = etree.Element(q("w", local))
        parent.insert(0, found)
    return found


def _rpr_child(rpr, local: str):
    """`rpr`'s w:<local>, created after the children CT_RPr puts before it when absent."""
    found = rpr.find(f"w:{local}", NS)
    if found is not None:
        return found
    before = {q("w", n) for n in _RPR_ORDER[:_RPR_ORDER.index(local)]}
    at = 0
    for i, child in enumerate(rpr):
        if child.tag in before:
            at = i + 1
    element = etree.Element(q("w", local))
    rpr.insert(at, element)
    return element


def _other_size(value) -> float:
    """A size the invariant does not have: its own plus 2 (12 when it names none)."""
    size = value.get("size") if isinstance(value, dict) else None
    return size + 2 if isinstance(size, (int, float)) and not isinstance(size, bool) else 12


def _perturb_runs(paragraphs: list, value) -> bool:
    """Every run of `paragraphs` in PERTURBED_FONT at another size than the invariant's."""
    half_points = str(int(round(_other_size(value) * 2)))
    touched = False
    for p in paragraphs:
        for run in p.iter(q("w", "r")):
            rpr = _first_child(run, "rPr")
            fonts = _rpr_child(rpr, "rFonts")
            for theme in ("asciiTheme", "hAnsiTheme"):          # a theme font overrides the named one
                fonts.attrib.pop(q("w", theme), None)
            fonts.set(q("w", "ascii"), PERTURBED_FONT)
            fonts.set(q("w", "hAnsi"), PERTURBED_FONT)
            _rpr_child(rpr, "sz").set(q("w", "val"), half_points)
            touched = True
    return touched


def _replace_text(roots: list, t_tag: str) -> bool:
    """Each part's text replaced by PERTURBED_TEXT (its first text node), the part itself kept."""
    touched = False
    for root in roots:
        texts = list(root.iter(t_tag))
        if texts:
            texts[0].text = PERTURBED_TEXT
            for t in texts[1:]:
                t.text = ""
            touched = True
    return touched


def _default_builtin(styles):
    """The value the miner gives a paragraph without w:pStyle: its default paragraph style's built-in."""
    if styles is None:
        return None
    for s in styles.findall("w:style", NS):
        if s.get(q("w", "type")) == "paragraph" and s.get(q("w", "default")) in ("1", "true"):
            name = s.find("w:name", NS)
            builtin = mine.BUILTIN_STYLES.get(((name.get(q("w", "val")) if name is not None else None) or "").lower())
            return None if builtin is None else {"builtin": builtin}
    return None


def _perturb_docx(zf, post: set, expected: dict) -> dict:
    edits = {}
    doc = ooxml.part(zf, "word/document.xml")
    paragraphs = [] if doc is None else list(doc.iter(q("w", "p")))
    touched = False
    if "title_style" in post and paragraphs:
        first = paragraphs[0]
        ppr = first.find("w:pPr", NS)
        ps = None if ppr is None else ppr.find("w:pStyle", NS)
        if canon.dumps(_default_builtin(ooxml.part(zf, "word/styles.xml"))) == canon.dumps(expected.get("title_style")):
            # Without w:pStyle the title would be in the invariant's style itself: an undeclared one instead.
            _first_child(_first_child(first, "pPr"), "pStyle").set(q("w", "val"), _UNDECLARED_STYLE)
            touched = True
        elif ps is not None:
            ppr.remove(ps)
            touched = True
    if "title_font" in post and paragraphs:
        touched = _perturb_runs(paragraphs[:1], expected.get("title_font")) or touched
    if "body_font" in post:
        touched = _perturb_runs(paragraphs[1:], expected.get("body_font")) or touched
    if touched:
        edits["word/document.xml"] = doc
    for ident, pattern in (("footer_text", "word/footer*.xml"), ("header_text", "word/header*.xml")):
        if ident in post:
            for name in ooxml.parts(zf, pattern):
                root = ooxml.part(zf, name)
                if root is not None and _replace_text([root], q("w", "t")):
                    edits[name] = root
    return edits


def _perturb_pptx(zf, post: set, expected: dict) -> dict:
    """The first slide master, where the miner reads a presentation's title font and footer."""
    masters = ooxml.parts(zf, "ppt/slideMasters/slideMaster*.xml")
    master = ooxml.part(zf, masters[0]) if masters else None
    if master is None:
        return {}

    def placeholder(kind):
        return master.xpath(f'.//p:sp[p:nvSpPr/p:nvPr/p:ph[@type="{kind}"]]', namespaces=NS)

    touched = False
    if "title_font" in post:
        size = str(int(round(_other_size(expected.get("title_font")) * 100)))
        for root in placeholder("title") + master.xpath("p:txStyles/p:titleStyle/a:lvl1pPr", namespaces=NS):
            for latin in root.iter(q("a", "latin")):
                latin.set("typeface", PERTURBED_FONT)
                touched = True
            for rpr in root.iter(q("a", "rPr"), q("a", "defRPr")):
                if rpr.get("sz") is not None:
                    rpr.set("sz", size)
                    touched = True
    if "footer_text" in post:
        touched = _replace_text(placeholder("ftr")[:1], q("a", "t")) or touched
    return {masters[0]: master} if touched else {}


def _perturb(copy: Path, post: list, expected: dict) -> None:
    """The copy made non-conforming on every post id (no PDF beside it for `pdf_exists`), its file mode
    kept: a read-only document stays read-only, as Word holds it."""
    copy.with_suffix(".pdf").unlink(missing_ok=True)
    with ooxml.open_package(copy) as zf:
        edits = (_perturb_docx if ooxml.kind_of(zf) == "docx" else _perturb_pptx)(zf, set(post), expected)
    if not edits:
        return
    mode = stat.S_IMODE(os.stat(copy).st_mode)
    os.chmod(copy, mode | stat.S_IWRITE)
    try:
        ooxml.write_parts(copy, {name: _xml(root) for name, root in sorted(edits.items())})
    finally:
        os.chmod(copy, mode)


def _prepare(files: list, work: Path, post: list, expected: dict, learned_content: str,
             salt: bytes | None = None) -> list:
    """Before the first pass: every document copied to work/<i>/ with its attributes, the copies'
    bytes checked against the corpus the driver was learned from, then each copy perturbed and read
    again — a post id still held is refused (L-P52)."""
    copies = []
    for index, source in enumerate(files):
        folder = work / str(index)
        (folder / "evidence").mkdir(parents=True)
        copy = folder / source.name
        shutil.copy2(source, copy)
        copies.append({"copy": copy, "file_sha256": canon.sha256_file(copy)})
    if _content_sha256(c["file_sha256"] for c in copies) != learned_content:
        raise Refusal(*_CORPUS_CHANGED)             # a document edited after the check, before its copy
    for c in copies:
        _perturb(c["copy"], post, expected)
        held = next((ident for ident, v in _post(post, c["copy"], expected, salt).items() if v), None)
        if held is not None:
            raise Refusal(f"cannot perturb {held} for qualification", _PERTURB_REMEDY)
        c["perturbed_sha256"] = canon.sha256_file(c["copy"])
    return copies


# --- one pass ----------------------------------------------------------------------------------------

def _is_pdf(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"%PDF"
    except OSError:
        return False


def _sha_or_none(path: Path):
    try:
        return canon.sha256_file(path)
    except OSError:
        return None


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


def _pass(driver: _Driver, index: int, c: dict, action: dict, entry: dict, expected: dict,
          deadline_s: float, salt: bytes | None = None) -> tuple[dict, bool]:
    """(the run of corpus file `index`, whether the driver process must be ended): the action asked
    once on the perturbed copy, then the copy read again."""
    copy = c["copy"]
    args = {"document": str(copy)}
    if "pdf" in ((entry.get("args") or {}).get("required") or []):
        args["pdf"] = str(copy.with_suffix(".pdf"))
    request = {"id": index + 1, "action": action["name"], "args": args, "deadline_ms": int(deadline_s * 1000),
               "evidence_dir": str(copy.parent / "evidence")}
    started = time.monotonic()
    answer, fault = driver.ask(request, deadline_s)
    duration_ms = int((time.monotonic() - started) * 1000)
    none = {ident: False for ident in action["post"]}
    if fault is not None:
        post, ok, reason = none, False, fault
    elif answer.get("ok") is True:
        post = _post(action["post"], copy, expected, salt)
        unsaved = c["perturbed_sha256"] != c["file_sha256"] and _sha_or_none(copy) == c["perturbed_sha256"]
        ok = not unsaved and bool(post) and all(post.values())
        reason = "unsaved" if unsaved else None if ok else "postcondition"     # L-P52, L-P50
    else:
        post, ok = none, False
        reason = answer.get("reason") if answer.get("reason") in REASONS else "internal"
    run = {"index": index, "file_sha256": c["file_sha256"], "duration_ms": duration_ms, "post": post, "ok": ok,
           "reason": reason}
    return run, fault is not None


def qualify(driver_dir: Path, corpus: Path, plan: dict, invariants: dict, work: Path, fake: bool,
            deadline_s: float = PASS_DEADLINE_S, salt: bytes | None = None) -> dict:
    """The record, also written as <work>/qualification.json (canon.write)."""
    driver_dir, corpus, work = Path(os.path.abspath(driver_dir)), Path(corpus), Path(os.path.abspath(work))
    manifest, action, entry, files, expected = _check(driver_dir, corpus, plan, invariants, work, salt)
    plan_sha256 = canon.sha256(plan)
    work.mkdir(parents=True, exist_ok=True)
    if fake:
        canon.write({"vkforge_root": str(VKFORGE_ROOT)}, work / "fake.json")
    (work / "profile").mkdir()
    copies = _prepare(files, work, action["post"], expected, _learned_content(invariants), salt)
    env = {**os.environ, "VK_DRIVER_WORK_DIR": str(work), "VK_DRIVER_PROFILE_DIR": str(work / "profile"),
           "VK_DRIVER_APPLICATIONS": ",".join(manifest.get("applications") or [])}
    # The vkforge interpreter: the virtualenv the manifest's .venv links to (L-P12).
    command = [sys.executable, "-I", str(driver_dir / "driver.py")]
    runs, driver = [], None
    with open(work / "driver.log", "ab") as log:
        try:
            for index, c in enumerate(copies):
                if driver is None:                  # the first pass, or the last process was ended
                    driver = _start(command, work, env, log, driver_dir, plan_sha256)
                run, ended = _pass(driver, index, c, action, entry, expected, deadline_s, salt)
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
