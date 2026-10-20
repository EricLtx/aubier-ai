import contextlib
import copy
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from vkdriver.hash_dir import hash_dir
from vkforge import __main__ as entry
from vkforge import canon, generate, mine, ontology, plan, qualify
from vkforge.errors import Refusal
from vkforge.tests import ooxml_fixtures
from vkforge.tests.ooxml_fixtures import OTHER_SALT, SALT

FIXTURES = Path(__file__).resolve().parent / "fixtures"
HOUSE_POST = {"title_style": True, "body_font": True, "footer_text": True, "pdf_exists": True}
NOTHING_REPAIRED = {"title_style": False, "body_font": False, "footer_text": False, "pdf_exists": True}

# A stand-in for the generated driver.py (L-P51): protocol 1 by hand, stdlib only. Per document name it
# sleeps past the deadline or exits without answering; otherwise it writes the PDF and answers ok
# without repairing anything — "pdf" leaves the document as it found it, "resave" rewrites it unrepaired.
STAND_IN = r'''import json
import os
import sys
import time
import zipfile
from pathlib import Path

VERSION, ACTION, DEFAULT, BEHAVIOUR = @CONFIG@

with open(Path(os.environ["VK_DRIVER_WORK_DIR"]) / "pids.log", "a", encoding="utf-8") as f:
    f.write(str(os.getpid()) + "\n")
print(json.dumps({"hello": {"protocol": 1, "driver": "stand-in", "version": VERSION, "actions": [ACTION]}}),
      flush=True)
for line in sys.stdin:
    request = json.loads(line)
    doc = Path(request["args"]["document"])
    what = BEHAVIOUR.get(doc.name, DEFAULT)
    if what == "sleep":
        time.sleep(60)
    if what == "exit":
        os._exit(3)
    Path(request["args"]["pdf"]).write_bytes(b"%PDF-1.4 stand-in\n")
    if what == "resave":
        with zipfile.ZipFile(doc) as zf:
            members = [(info.filename, zf.read(info)) for info in zf.infolist()]
        with zipfile.ZipFile(doc, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, data in members:
                zf.writestr(name, data)
    print(json.dumps({"id": request["id"], "ok": True, "result": {"text": "stand-in"}, "evidence": []}), flush=True)
'''


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


def write_artefacts(driver: Path, p: dict, inv: dict) -> Path:
    """plan.json and invariants.json beside the driver, in canonical form, as `vk driver learn` leaves
    them in its --out; that directory."""
    out = driver.parent
    plan.write_plan(p, [], out)
    canon.write(inv, out / "invariants.json")
    return out


def qualify_argv(driver: Path, corpus: Path, out: Path, work: Path, salt_file: Path | None) -> list:
    """`vkforge qualify` on the fake application over the artefacts in `out`; no --salt-file when None."""
    argv = ["qualify", "--driver", str(driver), "--corpus", str(corpus), "--plan", str(out / "plan.json"),
            "--invariants", str(out / "invariants.json"), "--work", str(work), "--fake"]
    return argv if salt_file is None else [*argv, "--salt-file", str(salt_file)]


def stand_in(driver: Path, p: dict, default: str, behaviour: dict | None = None) -> Path:
    """The generated driver with STAND_IN as its driver.py and its manifest hashed again: vendor, hash and
    hello still hold, only the action differs."""
    config = repr((canon.sha256(p), p["actions"][0]["name"], default, behaviour or {}))
    (driver / "driver.py").write_text(STAND_IN.replace("@CONFIG@", config), encoding="utf-8")
    manifest = json.loads((driver / "manifest.json").read_text(encoding="utf-8"))
    manifest["program"]["files_sha256"] = hash_dir(str(driver))
    canon.write(manifest, driver / "manifest.json")
    return driver


def alive(pid: int) -> bool:
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    kernel32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    handle = kernel32.OpenProcess(0x00100000 | 0x1000, False, pid)     # SYNCHRONIZE | QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        return kernel32.WaitForSingleObject(handle, 0) == 0x102        # WAIT_TIMEOUT: still running
    finally:
        kernel32.CloseHandle(handle)


class QualifyTest(unittest.TestCase):
    def setUp(self):
        self.corpus = ooxml_fixtures.corpus(Path(tempfile.mkdtemp()) / "corpus", 10)
        self.driver, self.plan, self.inv = learned(self.corpus)
        self.work = Path(tempfile.mkdtemp()) / "work"

    def run_qualify(self, work=None, **kw):
        return qualify.qualify(self.driver, self.corpus, self.plan, self.inv, work or self.work, fake=True, **kw)

    def test_ten_copies_pass_against_the_fake(self):
        rec = self.run_qualify()
        self.assertEqual((rec["passes"], rec["failures"], rec["rate"], rec["fake"]), (10, 0, 1.0, True))
        self.assertEqual([r["index"] for r in rec["runs"]], list(range(10)))
        for r in rec["runs"]:
            self.assertEqual((r["post"], r["ok"], r["reason"]), (HOUSE_POST, True, None))
            self.assertGreaterEqual(r["duration_ms"], 0)
        self.assertEqual(json.loads((self.work / "qualification.json").read_text(encoding="utf-8")), rec)
        self.assertEqual(mine.content_sha256(self.corpus), self.inv["corpus"]["content_sha256"], "the originals are never touched")

    def test_a_six_document_corpus_learns_qualifies_six_of_six_and_meets_its_plan(self):
        """Final review I-3: six documents learn, the plan asks six passes (min(10, n)), the replay
        makes six of six, and the gate's arithmetic (`passes >= min_passes`, rate) holds."""
        corpus = ooxml_fixtures.corpus(Path(tempfile.mkdtemp()) / "corpus", 6)
        driver, p, inv = learned(corpus)
        self.assertEqual((inv["corpus"]["files"], p["qualification"]), (6, {"min_passes": 6, "min_rate": 1.0}))
        rec = qualify.qualify(driver, corpus, p, inv, self.work, fake=True)
        self.assertEqual((rec["passes"], rec["failures"], rec["rate"]), (6, 0, 1.0))
        q = p["qualification"]
        self.assertTrue(rec["passes"] >= q["min_passes"] and rec["passes"] / len(rec["runs"]) >= q["min_rate"])

    def test_a_read_only_copy_is_a_failed_pass_not_a_hang(self):
        # Review Focus 4: the user's Word holds c3.docx; its copy keeps the read-only attribute.
        locked = self.corpus / "c3.docx"
        os.chmod(locked, stat.S_IREAD)
        try:
            started = time.monotonic()
            rec = self.run_qualify(deadline_s=30)
            self.assertLess(time.monotonic() - started, 300)
        finally:
            for p in [locked, *self.work.rglob("c3.docx")]:
                os.chmod(p, stat.S_IREAD | stat.S_IWRITE)
        self.assertEqual((rec["passes"], rec["failures"]), (9, 1))
        self.assertEqual((rec["runs"][3]["ok"], rec["runs"][3]["reason"]), (False, "precondition"))

    def test_a_changed_corpus_is_refused_before_any_copy(self):
        ooxml_fixtures.docx(self.corpus / "c10.docx")
        with self.assertRaises(Refusal) as cm:
            self.run_qualify()
        self.assertIn("corpus changed since vkforge mine", str(cm.exception))
        self.assertFalse(self.work.exists())

    def test_a_driver_without_its_vendor_is_refused_before_any_copy(self):
        bare = Path(tempfile.mkdtemp()) / "driver"
        shutil.copytree(self.driver, bare, ignore=shutil.ignore_patterns("vendor"))
        with self.assertRaises(Refusal) as cm:
            qualify.qualify(bare, self.corpus, self.plan, self.inv, self.work, fake=True)
        self.assertIn("does not vendor vkdriver", str(cm.exception))
        self.assertFalse(self.work.exists())

    def test_a_used_work_directory_or_a_foreign_plan_is_refused(self):
        self.work.mkdir(parents=True)
        (self.work / "x").write_text("x")
        with self.assertRaises(Refusal) as cm:
            self.run_qualify()
        self.assertIn("is not empty", str(cm.exception))
        other = copy.deepcopy(self.plan)
        other["invariants_sha256"] = "sha256:" + "0" * 64
        with self.assertRaises(Refusal) as cm:
            qualify.qualify(self.driver, self.corpus, other, self.inv, Path(tempfile.mkdtemp()) / "w", fake=True)
        self.assertIn("plan and invariants do not belong together", str(cm.exception))

    def test_the_record_is_reproducible_but_for_durations(self):
        a = self.run_qualify()
        b = self.run_qualify(work=Path(tempfile.mkdtemp()) / "w2")

        def strip(rec):
            return {**rec, "runs": [{k: v for k, v in r.items() if k != "duration_ms"} for r in rec["runs"]]}

        self.assertEqual(strip(a), strip(b))

    def test_the_record_names_the_plan_and_the_driver_by_their_hashes_and_no_file(self):
        rec = self.run_qualify()
        manifest = json.loads((self.driver / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual((rec["plan_sha256"], rec["files_sha256"]),
                         (canon.sha256(self.plan), manifest["program"]["files_sha256"]))
        self.assertNotIn("c0.docx", json.dumps(rec), "no file name in the record (spec §5.5)")

    def test_the_cli_qualifies_from_the_artefacts(self):
        driver, p, inv = learned(self.corpus, SALT)
        out = write_artefacts(driver, p, inv)
        salt = out / ".salt"
        salt.write_bytes(SALT)
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            code = entry.main(qualify_argv(driver, self.corpus, out, self.work, salt))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads((self.work / "qualification.json").read_text(encoding="utf-8"))["passes"], 10)
        self.assertEqual(printed.getvalue(),
                         f"qualify: 10/10 passes (0 failures), fake true -> {self.work / 'qualification.json'}\n")

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
        out = write_artefacts(driver, p, inv)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = entry.main(qualify_argv(driver, self.corpus, out, Path(tempfile.mkdtemp()) / "w",
                                           out / "absent.salt"))
        self.assertEqual(code, 2)
        self.assertIn("vkforge qualify: salt file ", err.getvalue())
        self.assertIn(" cannot be read (", err.getvalue())

    def test_a_perturbation_that_left_the_footer_is_refused_under_the_learns_salt(self):
        # Task review tests-5, L-P52 under D-01: `_prepare` reads each perturbed copy again under the
        # learn's salt, so a footer the perturbation left in place is seen held and refused.
        driver, p, inv = learned(self.corpus, SALT)
        perturb = qualify._perturb

        def leaves_the_footer(copy, post, expected):
            perturb(copy, [ident for ident in post if ident != "footer_text"], expected)

        with mock.patch.object(qualify, "_perturb", leaves_the_footer), self.assertRaises(Refusal) as cm:
            qualify.qualify(driver, self.corpus, p, inv, self.work, fake=True, salt=SALT)
        self.assertIn("cannot perturb footer_text for qualification", str(cm.exception))

    def test_the_stage_refuses_to_run_without_a_salt_file(self):
        # Task review tests-4: `vkforge qualify` requires the learn's salt (D-01); it never re-mines
        # the copies unsalted.
        out = write_artefacts(self.driver, self.plan, self.inv)
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            entry.main(qualify_argv(self.driver, self.corpus, out, self.work, None))
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("the following arguments are required: --salt-file", err.getvalue())
        self.assertFalse(self.work.exists())

    # --- L-P52: perturb then repair; L-P50/L-P51: the stage's own reasons, the timeout and the crash ---

    def test_the_perturbation_breaks_every_post_and_keeps_the_file_mode(self):
        post, expected = self.plan["actions"][0]["post"], qualify._expected(self.plan, self.inv)
        doc = Path(tempfile.mkdtemp()) / "c0.docx"
        shutil.copy2(self.corpus / "c0.docx", doc)
        doc.with_suffix(".pdf").write_bytes(b"%PDF-1.4 corpus\n")
        self.assertEqual(qualify._post(post, doc, expected), HOUSE_POST, "the corpus already conforms")
        os.chmod(doc, stat.S_IREAD)
        try:
            qualify._perturb(doc, post, expected)
            self.assertFalse(os.access(doc, os.W_OK), "a read-only document stays read-only")
        finally:
            os.chmod(doc, stat.S_IREAD | stat.S_IWRITE)
        self.assertEqual(qualify._post(post, doc, expected), {k: False for k in HOUSE_POST})

    def test_a_driver_that_repairs_nothing_never_passes(self):
        # Review Important 1: the corpus already conforms, so only the perturbation makes a no-op fail.
        stand_in(self.driver, self.plan, "pdf")
        rec = self.run_qualify(deadline_s=30)
        self.assertEqual((rec["passes"], rec["failures"]), (0, 10))
        for r in rec["runs"]:
            self.assertEqual((r["ok"], r["reason"], r["post"]), (False, "unsaved", NOTHING_REPAIRED))

    def test_a_driver_that_saves_without_repairing_fails_its_postconditions(self):
        # Review Important 2, L-P50: an ok answer the re-mined copy contradicts is `postcondition`.
        stand_in(self.driver, self.plan, "resave")
        rec = self.run_qualify(deadline_s=30)
        self.assertEqual((rec["passes"], rec["failures"]), (0, 10))
        for r in rec["runs"]:
            self.assertEqual((r["ok"], r["reason"], r["post"]), (False, "postcondition", NOTHING_REPAIRED))

    def test_the_strays_of_a_corpus_are_repaired_too(self):
        corpus = ooxml_fixtures.corpus(Path(tempfile.mkdtemp()) / "corpus", 10, 2)     # c10, c11: Calibri 12
        driver, p, inv = learned(corpus)
        rec = qualify.qualify(driver, corpus, p, inv, Path(tempfile.mkdtemp()) / "w", fake=True)
        self.assertEqual((rec["passes"], rec["failures"]), (12, 0))
        self.assertEqual([r["post"] for r in rec["runs"]], [HOUSE_POST] * 12)
        rec = qualify.qualify(stand_in(driver, p, "resave"), corpus, p, inv, Path(tempfile.mkdtemp()) / "w", fake=True)
        self.assertEqual((rec["passes"], rec["failures"]), (0, 12))
        self.assertEqual({r["reason"] for r in rec["runs"]}, {"postcondition"})

    def test_a_pass_past_its_deadline_or_a_dead_driver_fails_and_a_new_process_takes_the_next(self):
        stand_in(self.driver, self.plan, "pdf", {"c3.docx": "sleep", "c5.docx": "exit"})
        started = time.monotonic()
        rec = self.run_qualify(deadline_s=2)
        self.assertLess(time.monotonic() - started, 60, "the deadline, then END_WAIT_S, then the kill")
        reasons = [r["reason"] for r in rec["runs"]]
        self.assertEqual(reasons, ["unsaved"] * 3 + ["timeout", "unsaved", "internal"] + ["unsaved"] * 4)
        self.assertEqual(rec["runs"][3]["post"], {k: False for k in HOUSE_POST})
        pids = [int(x) for x in (self.work / "pids.log").read_text(encoding="utf-8").split()]
        self.assertEqual(len(pids), 3, "one process, then one after the timeout, then one after the crash")
        self.assertEqual([pid for pid in pids if alive(pid)], [], "no driver process outlives the stage")

    def test_a_post_id_qualify_cannot_perturb_is_refused_before_any_pass(self):
        other = copy.deepcopy(self.plan)
        other["actions"][0]["post"][0] = "page_size"
        with self.assertRaises(Refusal) as cm:
            qualify.qualify(self.driver, self.corpus, other, self.inv, self.work, fake=True)
        self.assertIn("cannot perturb page_size for qualification", str(cm.exception))
        self.assertFalse(self.work.exists())
