import contextlib
import copy
import io
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from vkdriver.hash_dir import hash_dir, list_files
from vkdriver.protocol import DriverRefusal
from vkforge import __main__ as entry
from vkforge import canon, generate, inventory, mine, ontology, plan
from vkforge.errors import Refusal
from vkforge.runtime import com
from vkforge.runtime.fake import FakeApplication
from vkforge.tests import ooxml_fixtures
from vkforge.tests.ooxml_fixtures import FOOTER

FIXTURES = Path(__file__).resolve().parent / "fixtures"
DRIVERS = Path(__file__).resolve().parents[2]
GENERATED = ("binding.json", "driver.py", "manifest.json", "secrets.json",
             "vendor/vkdriver/__init__.py", "vendor/vkdriver/hash_dir.py", "vendor/vkdriver/protocol.py",
             "vendor/vkforge_runtime/__init__.py", "vendor/vkforge_runtime/com.py")


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def fake_venv() -> Path:
    venv = Path(tempfile.mkdtemp())
    (venv / "Scripts").mkdir()
    (venv / "Scripts" / "python.exe").write_bytes(b"MZ")
    return venv


# A stand-in vkforge root whose FakeApplication records the end of the session in a file: the driver's
# own wiring (L-P46) seen from outside its process, with no Office.
RECORDING_FAKE = '''from pathlib import Path


class FakeApplication:
    def __init__(self, binding, config):
        self.marker = Path(config["quit_marker"])

    def probe(self):
        pass

    def open(self, path):
        return path

    def call(self, verb, doc, **args):
        if verb == "export":
            Path(args["path"]).write_bytes(b"%PDF-1.4 recording")
        return True

    def close(self, doc):
        pass

    def quit(self):
        with open(self.marker, "a", encoding="utf-8") as f:
            f.write("quit\\n")
'''


def recording_root() -> Path:
    root = Path(tempfile.mkdtemp())
    (root / "vkforge" / "runtime").mkdir(parents=True)
    (root / "vkforge" / "__init__.py").write_text("", encoding="utf-8")
    (root / "vkforge" / "runtime" / "__init__.py").write_text("", encoding="utf-8")
    (root / "vkforge" / "runtime" / "fake.py").write_text(RECORDING_FAKE, encoding="utf-8")
    return root


def winword_pids() -> set:
    listing = subprocess.run(["tasklist", "/FO", "CSV", "/NH", "/FI", "IMAGENAME eq WINWORD.EXE"],
                             capture_output=True, text=True, errors="replace").stdout
    return {line.split('","')[1] for line in listing.splitlines() if line.startswith('"WINWORD.EXE"')}


def driver_pid(proc: subprocess.Popen) -> int:
    """The process that runs driver.py: `proc` itself, or — `sys.executable` being a virtualenv's
    launcher on Windows — the interpreter the launcher started, its one python child."""
    children = [pid for pid, parent, exe in com.processes() or [] if parent == proc.pid and exe == "PYTHON.EXE"]
    return children[0] if children else proc.pid


def running(exes: set) -> set:
    """The ids of the processes running one of `exes` now; empty when there is no snapshot."""
    return set(com.pids_of(exes) or ())


# The PowerPoint of the node steps (ruling PF-5): the ontology's applications block, no verb.
PPT = {"application": json.loads((DRIVERS / "vkforge" / "ontology" / "office-document.json")
                                 .read_text(encoding="utf-8"))["applications"]["powerpoint"],
       "verbs": []}
# A driver's session in a process of its own (PF-5 (b)): argv = drivers/ and the application block. It
# prints the process the adapter identified as its own ("None" when it could not), then sleeps until killed.
STARTER = """import json, sys, time
sys.path.insert(0, sys.argv[1])
from vkforge.runtime import com
app = com.Application({"application": json.loads(sys.argv[2]), "verbs": []})
print(app.pid, flush=True)
time.sleep(120)
"""


class Session:
    """The generated driver as a host runs it: from its own directory, in a work directory, protocol 1 on stdio."""

    def __init__(self, driver_dir: Path, fake: bool, extra_env=None, fake_extra=None):
        self.work = Path(tempfile.mkdtemp())
        if fake:
            canon.write({"vkforge_root": str(DRIVERS), **(fake_extra or {})}, self.work / "fake.json")
        env = {**os.environ, "VK_DRIVER_WORK_DIR": str(self.work), "VK_DRIVER_PROFILE_DIR": tempfile.mkdtemp(),
               **(extra_env or {})}
        self.p = subprocess.Popen([sys.executable, "-I", str(driver_dir / "driver.py")], cwd=self.work, env=env,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8")
        self.hello = json.loads(self.p.stdout.readline())
        self.n = 0

    def act(self, action: str, args: dict) -> dict:
        self.n += 1
        request = {"id": self.n, "action": action, "args": args, "deadline_ms": 60000, "evidence_dir": tempfile.mkdtemp()}
        self.p.stdin.write(json.dumps(request) + "\n")
        self.p.stdin.flush()
        return json.loads(self.p.stdout.readline())

    def close(self):
        self.p.stdin.close()
        self.p.wait(timeout=10)
        self.p.stdout.close()


class GenerateTest(unittest.TestCase):
    def setUp(self):
        self.plan = fixture("plan-word-maison.json")
        self.binding = fixture("binding-word.json")
        self.invariants = fixture("invariants-word-maison.json")
        self.vault = {mine.redact(FOOTER): FOOTER}

    def gen(self, **kw) -> Path:
        out = Path(tempfile.mkdtemp()) / "word-maison"
        generate.generate(self.plan, self.binding, self.invariants, self.vault, out, name="word-maison", **kw)
        return out

    def test_generation_is_byte_reproducible_in_two_directories(self):
        a, b = self.gen(), self.gen()
        for f in GENERATED:
            self.assertEqual((a / f).read_bytes(), (b / f).read_bytes(), f)
        m = json.loads((a / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(m["program"]["files_sha256"], hash_dir(str(a)))
        self.assertEqual(sorted(rel for rel, _ in list_files(str(a))), [f for f in GENERATED if f != "manifest.json"])
        self.assertFalse((a / "pack.json").exists(), "no pack.json: the driver vendors its own (L-P9)")
        self.assertFalse((a / ".venv").exists())

    def test_the_manifest_is_a_com_driver_with_the_arguments_its_steps_reference(self):
        m = json.loads((self.gen() / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual((m["name"], m["kind"], m["hosts"], m["applications"]), ("word-maison", "com", [], ["WINWORD.EXE"]))
        self.assertEqual((m["program"]["runtime"], m["program"]["command"]),
                         ("python-com", [".venv/Scripts/python.exe", "-I", "driver.py"]))
        a = m["actions"][0]
        self.assertEqual((a["name"], a["args"]["required"], a["evidence"], a["reversible"], a["to_register"]),
                         ("appliquer_style_maison", ["document", "pdf"], ["post.json"], False, False))
        self.assertEqual(m["clearance"], {"max_scope": "business", "third_party_allowed": False})

    def test_the_generated_driver_imports_vkdriver_from_its_own_vendor(self):
        out = self.gen()                                       # a temp dir: no repository around it
        for f in ("__init__.py", "hash_dir.py", "protocol.py"):
            self.assertEqual((out / "vendor/vkdriver" / f).read_bytes(), (DRIVERS / "vkdriver" / f).read_bytes(), f)
        for f in ("__init__.py", "com.py"):
            self.assertEqual((out / "vendor/vkforge_runtime" / f).read_bytes(),
                             (DRIVERS / "vkforge/runtime" / f).read_bytes(), f)
        self.assertFalse((out / "vendor/vkdriver/test_protocol.py").exists())
        self.assertFalse((out / "vendor/vkdriver/__pycache__").exists())
        self.assertFalse((out / "vendor/vkforge_runtime/fake.py").exists(), "the fake stays in the repository (L-P19)")
        probe = ("import runpy, sys; runpy.run_path(sys.argv[1], run_name='probe'); "
                 "print(sys.modules['vkdriver.protocol'].__file__); print(sys.modules['vkforge_runtime.com'].__file__)")
        got = subprocess.run([sys.executable, "-I", "-c", probe, str(out / "driver.py")], cwd=tempfile.gettempdir(),
                             capture_output=True, text=True)
        self.assertEqual(got.returncode, 0, got.stderr)
        protocol, com = got.stdout.splitlines()
        self.assertEqual(Path(protocol).resolve().parent, (out / "vendor" / "vkdriver").resolve())
        self.assertEqual(Path(com).resolve().parent, (out / "vendor" / "vkforge_runtime").resolve())

    def test_no_localised_name_and_no_corpus_text_in_the_driver(self):
        # Review Focus 2, from a French corpus: Heading 1 declared as w:styleId="Titre1".
        corpus = Path(tempfile.mkdtemp())
        for i in range(5):
            ooxml_fixtures.docx(corpus / f"c{i}.docx", title_style="Heading1", style_id="Titre1", footer=FOOTER, pdf=True)
        inv, vault = mine.mine_corpus(corpus)
        p, _ = plan.build(inv, self.binding, ontology.load())
        out = Path(tempfile.mkdtemp()) / "word-maison"
        generate.generate(p, self.binding, inv, vault, out, name="word-maison")
        for rel, path in list_files(str(out)) + [("manifest.json", str(out / "manifest.json"))]:
            data = Path(path).read_bytes()
            self.assertNotIn(b"Titre", data, rel)
            if rel != "secrets.json":
                self.assertNotIn(FOOTER.encode("utf-8"), data, rel)   # the clear footer lives in secrets.json alone
        code = (out / "driver.py").read_bytes()
        self.assertIn(b'SECRETS["footer_text"]', code)
        self.assertIn(b"style='Heading1'", code)
        self.assertEqual(json.loads((out / "secrets.json").read_text(encoding="utf-8")), {"footer_text": FOOTER})

    def test_the_generated_driver_speaks_protocol_1_against_the_fake(self):
        doc = ooxml_fixtures.docx(Path(tempfile.mkdtemp()) / "c.docx", title_style="Normal", body_font=("Calibri", 12))
        s = Session(self.gen(), fake=True)
        self.assertEqual((s.hello["hello"]["protocol"], s.hello["hello"]["actions"]), (1, ["appliquer_style_maison"]))
        ans = s.act("appliquer_style_maison", {"document": str(doc), "pdf": str(doc.with_suffix(".pdf"))})
        s.close()
        self.assertTrue(ans["ok"], ans)
        self.assertEqual(ans["result"]["post"],
                         {"title_style": True, "body_font": True, "footer_text": True, "pdf_exists": True})
        obs = mine.mine_file(doc)["obs"]
        self.assertIn({"kind": "paragraph_style", "where": "first", "value": {"builtin": "Heading1"}}, obs)
        self.assertIn({"kind": "font_run", "where": "body", "value": {"name": "Garamond", "size": 11}}, obs)
        self.assertIn({"kind": "footer", "where": None, "value": mine.redact(FOOTER)}, obs)
        self.assertTrue(doc.with_suffix(".pdf").read_bytes().startswith(b"%PDF"))

    def test_a_read_only_document_is_a_precondition_refusal(self):
        # Review Focus 4: a document the user's Word holds opens read-only; adapter and fake refuse, never hang.
        doc = ooxml_fixtures.docx(Path(tempfile.mkdtemp()) / "c.docx")
        os.chmod(doc, stat.S_IREAD)
        try:
            with self.assertRaises(DriverRefusal) as cm:
                FakeApplication(self.binding).open(str(doc))
            self.assertEqual(cm.exception.reason, "precondition")
            self.assertIn("read-only", cm.exception.detail)
            s = Session(self.gen(), fake=True)
            started = time.monotonic()
            ans = s.act("appliquer_style_maison", {"document": str(doc), "pdf": str(doc.with_suffix(".pdf"))})
            s.close()
            self.assertLess(time.monotonic() - started, 30)
            self.assertEqual((ans["ok"], ans["reason"]), (False, "precondition"))
            self.assertFalse(doc.with_suffix(".pdf").exists())
        finally:
            os.chmod(doc, stat.S_IREAD | stat.S_IWRITE)

    def test_a_surviving_dialog_is_a_dialog_refusal(self):
        # Spec §6, L-P33, D-03: a modal box raised mid-action in the instance the driver started is
        # closed, never answered, and the action refuses `dialog`; in an instance it attached to
        # (fake.json's "attached") the box is left open and the action refuses `dialog` all the same.
        doc = ooxml_fixtures.docx(Path(tempfile.mkdtemp()) / "c.docx", title_style="Normal", body_font=("Calibri", 12))
        before = doc.read_bytes()
        for config, words in (({"dialog_on": "save"}, "never answered"),
                              ({"dialog_on": "save", "attached": True}, "left open")):
            app = FakeApplication(self.binding, config)
            opened = app.open(str(doc))
            try:
                with self.assertRaises(DriverRefusal) as cm:
                    app.call("save", opened)
            finally:
                app.close(opened)
            self.assertEqual(cm.exception.reason, "dialog")
            self.assertIn(words, cm.exception.detail)
            s = Session(self.gen(), fake=True, fake_extra=config)
            started = time.monotonic()
            ans = s.act("appliquer_style_maison", {"document": str(doc), "pdf": str(doc.with_suffix(".pdf"))})
            s.close()
            self.assertLess(time.monotonic() - started, 30, "the guard's deadline, not the host's")
            self.assertEqual((ans["ok"], ans["reason"]), (False, "dialog"), ans)
            self.assertEqual(doc.read_bytes(), before, "nothing saved behind the box")
            self.assertFalse(doc.with_suffix(".pdf").exists())

    @unittest.skipIf(inventory.office_present("word"), "Word is installed here: the adapter would really run")
    def test_without_fake_json_the_driver_never_reaches_the_fake(self):
        # L-P19: no <work>/fake.json, no fake — and no environment variable can say otherwise.
        doc = ooxml_fixtures.docx(Path(tempfile.mkdtemp()) / "c.docx", title_style="Normal")
        before = doc.read_bytes()
        s = Session(self.gen(), fake=False, extra_env={"VKFORGE_FAKE": "1"})
        ans = s.act("appliquer_style_maison", {"document": str(doc), "pdf": str(doc.with_suffix(".pdf"))})
        s.close()
        self.assertEqual((ans["ok"], ans["reason"]), (False, "precondition"), ans)   # no COM server here
        self.assertEqual(doc.read_bytes(), before)

    def test_a_secret_missing_from_the_vault_is_refused(self):
        with self.assertRaises(Refusal) as cm:
            generate.generate(self.plan, self.binding, self.invariants, {}, Path(tempfile.mkdtemp()) / "x", name="word-maison")
        self.assertIn("secret footer_text has no clear value in the vault", str(cm.exception))

    def test_a_plan_verb_without_a_runtime_implementation_is_refused(self):
        p = copy.deepcopy(self.plan)
        p["actions"][0]["steps"].insert(1, {"verb": "set_table_style", "args": {}})
        with self.assertRaises(Refusal) as cm:
            generate.generate(p, self.binding, self.invariants, self.vault, Path(tempfile.mkdtemp()) / "x", name="word-maison")
        self.assertIn("verb set_table_style has no runtime implementation", str(cm.exception))

    def test_a_venv_is_linked_outside_the_fingerprint(self):
        plain, linked = self.gen(), self.gen(venv=fake_venv())
        self.assertTrue((linked / ".venv" / "Scripts" / "python.exe").is_file())
        files_sha256 = lambda d: json.loads((d / "manifest.json").read_text(encoding="utf-8"))["program"]["files_sha256"]
        self.assertEqual(files_sha256(plain), files_sha256(linked))
        with self.assertRaises(Refusal) as cm:
            self.gen(venv=Path(tempfile.mkdtemp()) / "absent")
        self.assertIn("no virtualenv at", str(cm.exception))

    def test_the_cli_generates_from_the_artefacts(self):
        work = Path(tempfile.mkdtemp())
        canon.write(self.vault, work / ".vault.json")
        out = work / "driver"
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            code = entry.main(["generate", "--plan", str(FIXTURES / "plan-word-maison.json"),
                               "--binding", str(FIXTURES / "binding-word.json"),
                               "--invariants", str(FIXTURES / "invariants-word-maison.json"),
                               "--vault", str(work / ".vault.json"), "--out", str(out), "--name", "word-maison",
                               "--venv", str(fake_venv())])
        self.assertEqual(code, 0)
        self.assertTrue((out / "driver.py").is_file() and (out / ".venv").exists())
        files_sha256 = json.loads((out / "manifest.json").read_text(encoding="utf-8"))["program"]["files_sha256"]
        self.assertEqual(printed.getvalue(), f"generate: word-maison {files_sha256} -> {out}\n")

    def test_the_cli_refuses_a_vault_that_is_not_an_object_of_clear_strings(self):
        # The vault has one reader, mine.read_vault, which plan uses too: the same line from both stages,
        # before anything is written.
        work = Path(tempfile.mkdtemp())
        vault, out = work / ".vault.json", work / "driver"
        for label, content in (("a list", [1, 2]), ("a value that is no string", {mine.redact(FOOTER): 3})):
            with self.subTest(label):
                vault.write_text(json.dumps(content), encoding="utf-8")
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    code = entry.main(["generate", "--plan", str(FIXTURES / "plan-word-maison.json"),
                                       "--binding", str(FIXTURES / "binding-word.json"),
                                       "--invariants", str(FIXTURES / "invariants-word-maison.json"),
                                       "--vault", str(vault), "--out", str(out), "--name", "word-maison",
                                       "--venv", str(fake_venv())])
                self.assertEqual(code, 2)
                self.assertEqual(err.getvalue(), f"vkforge generate: vault {vault} is not an object of clear strings; "
                                                 "remedy: run vkforge mine again\n")
                self.assertFalse(out.exists())

    def test_render_driver_is_the_reviewed_text(self):
        # The plan's byte-exact driver.py (the brief's text, amended by L-P46/L-P47): _quit registered
        # with atexit inside app(), once the application exists, and called again when serve returns.
        expected = (FIXTURES / "driver-word-maison.expected").read_bytes()
        self.assertEqual(generate.render_driver(self.plan, self.binding, "word-maison").encode("utf-8"), expected)
        text = expected.decode("utf-8")
        self.assertLess(text.index("_app = Application(BINDING)"), text.index("atexit.register(_quit)"))
        self.assertTrue(text.endswith('    serve(NAME, VERSION, ACTIONS)\n    _quit()\n'))

    def test_the_driver_quits_its_application_once_when_serve_returns(self):
        # L-P46: stdin's end returns serve, the driver quits the application it used, once (atexit is
        # the backstop); a session that never touched the application quits nothing.
        driver, root = self.gen(), recording_root()
        doc = ooxml_fixtures.docx(Path(tempfile.mkdtemp()) / "c.docx")
        used, idle = root / "used.log", root / "idle.log"
        s = Session(driver, fake=True, fake_extra={"vkforge_root": str(root), "quit_marker": str(used)})
        ans = s.act("appliquer_style_maison", {"document": str(doc), "pdf": str(doc.with_suffix(".pdf"))})
        s.close()
        self.assertTrue(ans["ok"], ans)
        self.assertEqual(used.read_text(encoding="utf-8"), "quit\n")
        Session(driver, fake=True, fake_extra={"vkforge_root": str(root), "quit_marker": str(idle)}).close()
        self.assertFalse(idle.exists())

    @unittest.skipUnless(sys.platform == "win32" and inventory.office_present("word"), "Word not installed")
    def test_no_word_the_driver_started_outlives_its_run(self):
        # L-P46 on the real Word: the instance the driver started (none when the user's was running) is
        # gone once the driver has ended.
        before = winword_pids()
        doc = ooxml_fixtures.docx(Path(tempfile.mkdtemp()) / "c.docx", title_style="Normal", body_font=("Calibri", 12))
        s = Session(self.gen(), fake=False, extra_env={"VK_DRIVER_APPLICATIONS": "WINWORD.EXE"})
        ans = s.act("appliquer_style_maison", {"document": str(doc), "pdf": str(doc.with_suffix(".pdf"))})
        s.close()
        self.assertTrue(ans["ok"], ans)
        deadline = time.monotonic() + 30
        while winword_pids() - before and time.monotonic() < deadline:
            time.sleep(0.5)
        self.assertEqual(winword_pids() - before, set(), "a Word this run started is still running")

    @unittest.skipUnless(sys.platform == "win32" and inventory.office_present("word"), "Word not installed")
    def test_a_killed_driver_takes_the_word_it_started_and_no_other(self):
        # D-03/D-04 on the real Word: the driver starts Word (no Word of the user's to attach to),
        # identifies it (PF-4), holds it in its Job, and is killed with TerminateProcess — no `_quit`,
        # no atexit. The Word it started ends within 10 s; every WINWORD that ran before still runs.
        import comtypes
        import comtypes.client
        try:
            comtypes.client.GetActiveObject("Word.Application")
        except (OSError, comtypes.COMError):
            pass
        else:
            self.skipTest("a running Word would be attached to, not started: close Word to run this test")
        exes = {"WINWORD.EXE"}
        before = running(exes)
        doc = ooxml_fixtures.docx(Path(tempfile.mkdtemp()) / "c.docx", title_style="Normal", body_font=("Calibri", 12))
        s = Session(self.gen(), fake=False, extra_env={"VK_DRIVER_APPLICATIONS": "WINWORD.EXE"})
        try:
            s.act("appliquer_style_maison", {"document": str(doc), "pdf": str(doc.with_suffix(".pdf"))})
            started = running(exes) - before
            self.assertEqual(len(started), 1, f"the driver started one Word: {started}")
            (word,) = started
            os.kill(driver_pid(s.p), signal.SIGTERM)                  # TerminateProcess: no _quit, no atexit
            deadline = time.monotonic() + 10
            while word in running(exes) and time.monotonic() < deadline:
                time.sleep(0.2)
            self.assertNotIn(word, running(exes), "the Word the driver started outlived it")
            self.assertLessEqual(before, running(exes), "a Word that ran before the test was touched")
        finally:
            s.p.kill()
            s.p.wait(timeout=10)
            s.p.stdout.close()

    @unittest.skipUnless(sys.platform == "win32" and inventory.office_present("powerpoint"), "PowerPoint not installed")
    def test_the_users_powerpoint_is_attached_never_quit_never_held_its_box_left_open(self):
        # D-03, D-04, ruling PF-4, node step PF-5 (a): a PowerPoint the user started — here the test, from
        # the shell, on a fictitious deck — is attached: no process of the driver's, no Job, never quit;
        # a dialog box it raises during a call (its Open dialog, a #32770 window) is left open and the
        # call refused `dialog`.
        import comtypes
        import comtypes.client
        exes = {"POWERPNT.EXE"}
        if running(exes):
            self.skipTest("a PowerPoint is running: never touched; close it to run this node step")
        deck = ooxml_fixtures.pptx(Path(tempfile.mkdtemp()) / "fictif.pptx")
        os.startfile(deck)                                          # the user's PowerPoint: the shell starts it
        try:
            deadline = time.monotonic() + 60
            while True:
                try:
                    comtypes.client.GetActiveObject("PowerPoint.Application")
                    break
                except (OSError, comtypes.COMError):
                    self.assertLess(time.monotonic(), deadline, "the PowerPoint the shell started never registered")
                    time.sleep(0.5)
            users = running(exes)
            with mock.patch.dict(os.environ, {"VK_DRIVER_APPLICATIONS": "POWERPNT.EXE"}):
                app = com.Application(PPT)
                try:
                    self.assertIsNone(app.pid, "attached: no process of the driver's, no Job")
                    with self.assertRaises(DriverRefusal) as cm:    # msoFileDialogOpen: the common Open box
                        com.guarded(lambda: app.app.FileDialog(1).Show(), "file dialog", 5, owner=app.pid)
                    self.assertEqual(cm.exception.reason, "dialog")
                    self.assertIn("left open", cm.exception.detail)
                    boxes = com.dialogs(exes)
                    self.assertTrue(boxes, "the box is still open")
                    for hwnd, _ in boxes:                           # the user closes it
                        com.close_window(hwnd)
                finally:
                    app.quit()
            time.sleep(2)
            self.assertLessEqual(users, running(exes), "the user's PowerPoint was quit or killed")
        finally:
            try:
                comtypes.client.GetActiveObject("PowerPoint.Application").Quit()
            except (OSError, comtypes.COMError):
                pass
            deadline = time.monotonic() + 10
            while running(exes) and time.monotonic() < deadline:
                time.sleep(0.5)
            for pid in running(exes):                               # this test's own: none ran before it
                os.kill(pid, signal.SIGTERM)

    @unittest.skipUnless(sys.platform == "win32" and inventory.office_present("powerpoint"), "PowerPoint not installed")
    def test_a_killed_driver_takes_the_powerpoint_it_started(self):
        # D-04, ruling PF-4, node step PF-5 (b): no PowerPoint running, the adapter starts one, identifies
        # it — one new POWERPNT.EXE, its parent the DcomLaunch service — and holds it in its Job; the
        # process that holds the Job is killed with TerminateProcess (no quit, no atexit), and the
        # POWERPNT.EXE ends within 10 s.
        exes = {"POWERPNT.EXE"}
        if running(exes):
            self.skipTest("a PowerPoint is running: never touched; close it to run this node step")
        proc = subprocess.Popen([sys.executable, "-I", "-c", STARTER, str(DRIVERS), json.dumps(PPT["application"])],
                                stdout=subprocess.PIPE, text=True,
                                env={**os.environ, "VK_DRIVER_APPLICATIONS": "POWERPNT.EXE"})
        try:
            said = proc.stdout.readline().strip()
            self.assertTrue(said.isdigit(), f"the adapter did not identify the PowerPoint it started: {said!r}")
            started = int(said)
            self.assertIn(started, running(exes))
            os.kill(driver_pid(proc), signal.SIGTERM)               # TerminateProcess: no quit, no atexit
            deadline = time.monotonic() + 10
            while started in running(exes) and time.monotonic() < deadline:
                time.sleep(0.2)
            self.assertNotIn(started, running(exes), "the PowerPoint the driver started outlived it")
        finally:
            proc.kill()
            proc.wait(timeout=10)
            proc.stdout.close()
            for pid in running(exes):                               # this test's own: none ran before it
                os.kill(pid, signal.SIGTERM)

    def test_a_failed_generation_leaves_no_out(self):
        parent, venv = Path(tempfile.mkdtemp()), fake_venv()
        out = parent / "word-maison"
        with mock.patch.object(generate, "hash_dir", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                generate.generate(self.plan, self.binding, self.invariants, self.vault, out, name="word-maison",
                                  venv=venv)
        self.assertEqual(list(parent.iterdir()), [], "no out, no partial directory")
        self.assertTrue((venv / "Scripts" / "python.exe").is_file(), "the venv the link named is untouched")
