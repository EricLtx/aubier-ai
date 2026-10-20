import contextlib
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from vkdriver.protocol import DriverRefusal
from vkforge import canon, inventory, mine, plan
from vkforge.errors import Refusal
from vkforge.runtime import com, fake
from vkforge.runtime.fake import FakeApplication
from vkforge.tests import NODE_STEP, node_office, ooxml_fixtures
from vkforge.tests.ooxml_fixtures import FOOTER

FIXTURES = Path(__file__).resolve().parent / "fixtures"
DRIVERS = Path(__file__).resolve().parents[2]


# A small Word-like object graph with the members binding-word.json names, so the adapter's walks run
# off Windows (no COM, no Office): the CI sees the code the real driver runs between guard and Word.
class Collection:
    def __init__(self, items=()):
        self.items = list(items)

    @property
    def Count(self):
        return len(self.items)

    def Item(self, i):
        if not 1 <= i <= len(self.items):
            raise OSError("the requested member of the collection does not exist")
        return self.items[i - 1]


class Styles:
    def Item(self, constant):
        return types.SimpleNamespace(NameLocal=f"style {constant}")    # the node's language, whatever it is


class Paragraph:
    def __init__(self, doc):
        self.doc, self._style = doc, doc.Styles.Item(-1)
        self.Range = types.SimpleNamespace(Font=types.SimpleNamespace(Name="Calibri", Size=12.0))

    @property
    def Style(self):
        return self._style

    @Style.setter
    def Style(self, constant):
        self._style = self.doc.Styles.Item(constant)


class Bands:
    def __init__(self):
        self.by_index = {}

    def Item(self, index):
        return self.by_index.setdefault(index, types.SimpleNamespace(Range=types.SimpleNamespace(Text="\r")))


class StubDocument:
    def __init__(self, word, path):
        self.word, self.path, self.ReadOnly = word, path, False
        self.Styles = Styles()
        self.Paragraphs = Collection(Paragraph(self) for _ in range(4))
        self.Sections = Collection([types.SimpleNamespace(Footers=Bands(), Headers=Bands())])
        self.saved, self.exported, self.closed = 0, [], None

    @property
    def FullName(self):
        """Its path; each read written into its Word's `log` when that Word keeps one (an OrderedWord:
        the inventory's reads, ruling PF-18)."""
        getattr(self.word, "log", []).append(f"FullName {Path(self.path).name}")
        return self.path

    def Save(self):
        self.saved += 1

    def ExportAsFixedFormat(self, path, fmt):
        self.exported.append((path, fmt))
        Path(path).write_bytes(b"%PDF-1.7 stub")

    def Close(self, **kw):
        self.closed = kw
        self.word.Documents.items.remove(self)
        getattr(self.word, "log", []).append(f"Close {Path(self.path).name}")


class StubDocuments(Collection):
    def __init__(self, word):
        super().__init__()
        self.word = word

    def Open(self, path, **kw):
        if path in self.word.missing:
            raise OSError("this file could not be found")
        known = next((d for d in self.items if d.FullName == path), None)
        if known is None:
            known = StubDocument(self.word, path)
            self.items.append(known)
        return known


class StubWord:
    def __init__(self):
        self.DisplayAlerts = -1                 # the user's own setting (wdAlertsAll)
        self.Documents = StubDocuments(self)
        self.ProtectedViewWindows = Collection()  # ruling PF-21: what Documents does not list
        self.missing, self.quits = set(), 0

    def Quit(self):
        self.quits += 1


class StubRegistry:
    """winreg as inventory and the adapter read it: TypeLib\\{guid}\\<version>\\0\\<arch>."""
    HKEY_CLASSES_ROOT = "HKCR"

    def __init__(self, guid, versions):
        self.guid, self.versions = guid, versions

    def OpenKey(self, root, path):
        if path != rf"TypeLib\{self.guid}":
            raise OSError("no such key")
        return contextlib.nullcontext("key")

    def EnumKey(self, key, i):
        names = list(self.versions)
        if i >= len(names):
            raise OSError("no more data")
        return names[i]

    def QueryValue(self, root, path):
        version, _, arch = path[len(rf"TypeLib\{self.guid}") + 1:].split("\\")
        try:
            return self.versions[version][arch]
        except KeyError:
            raise OSError("no such value") from None


class StubJob:
    """A Job as the adapter uses one (D-04), off Windows: what was asked of it, in order — into `log`
    when one is given, the list an OrderedWord writes to (ruling PF-2) — and how long it was waited for."""

    def __init__(self, exits: bool = True, log=None):
        self.calls, self.exits, self.waited = ([] if log is None else log), exits, None

    def wait(self, timeout_s: float) -> bool:
        self.calls.append("wait")
        self.waited = timeout_s
        return self.exits

    def disarm(self) -> None:
        self.calls.append("disarm")

    def close(self) -> None:
        self.calls.append("close")


class RefusingJob(StubJob):
    """A Job whose disarm Windows refuses (D-35 (4)): the hand-over must not stand (review tests-4)."""

    def disarm(self) -> None:
        super().disarm()
        raise OSError("the Job cannot be changed")


class SilentWord(StubWord):
    """A Word whose DisplayAlerts cannot be read: a session that cannot begin (ruling LF-8)."""

    def __init__(self):
        self.Documents = StubDocuments(self)
        self.missing, self.quits = set(), 0

    @property
    def DisplayAlerts(self):
        raise OSError("the application is not answering")


class OrderedDocuments(StubDocuments):
    """An OrderedWord's documents: each walk of the collection — its Count read — written into the log as
    `list` (ruling PF-18)."""

    @property
    def Count(self):
        self.word.log.append("list")
        return len(self.items)


class OrderedWord(StubWord):
    """A Word started by automation, as the end of a session finds it (founder decision D-35, rulings
    PF-2 and PF-12): hidden until the hand-over. It writes into `log`, in order, what that end asks of
    it: each document closed (StubDocument.Close), each walk of its documents (`list`) and each FullName
    read (ruling PF-18), each property set, each read of Visible, each Quit.
    Set true, Visible reads back `shows`: True as comtypes gives Word's VARIANT_BOOL, -1 as it gives
    PowerPoint's msoTrue, False or 0 for a window that will not show. The property `refuses` names
    cannot be set; UserControl reads False and cannot be set at all, as the pinned type library has
    Word's Application.UserControl (a get accessor only)."""

    def __init__(self, log: list, shows=True):
        super().__init__()
        self.Documents = OrderedDocuments(self)
        self.shown, self.shows, self.refuses, self.log = False, shows, None, log

    @property
    def Visible(self):
        self.log.append(f"Visible? {self.shown}")
        return self.shown

    @Visible.setter
    def Visible(self, value):
        self.shown = self.shows if value else False

    @property
    def UserControl(self):
        return False                                    # started by automation, not by the user

    def __setattr__(self, name, value):
        if name in ("Visible", "DisplayAlerts") and "log" in vars(self):
            if name == self.refuses:
                raise OSError(f"{name} cannot be set")
            super().__setattr__(name, value)
            self.log.append(f"{name} {value}")
        else:
            super().__setattr__(name, value)            # UserControl: AttributeError, it has no setter

    def Quit(self):
        self.log.append("Quit")
        super().Quit()


class StallingWord(StubWord):
    """A Word one call of whose end does not come back until `until` is set (D-35 (1), review tests-3):
    Quit, the Visible put or the Visible get, as `at` names it ("Quit", "Visible", "Visible?"); any
    other `at` leaves them alone (the test then hangs the driver's own close, or the listing)."""

    def __init__(self, at: str, until: threading.Event):
        super().__init__()
        self.at, self.until, self.shown = at, until, False

    @property
    def Visible(self):
        if self.at == "Visible?":
            self.until.wait(30)
        return self.shown

    @Visible.setter
    def Visible(self, value):
        if self.at == "Visible":
            self.until.wait(30)
        self.shown = value

    def Quit(self):
        if self.at == "Quit":
            self.until.wait(30)
        super().Quit()


class Hung:
    """A document whose FullName does not come back until `until` is set: a listing that hangs."""

    def __init__(self, until: threading.Event):
        self.until = until

    @property
    def FullName(self):
        self.until.wait(30)
        return "C:/stub/hung.docx"


class Unreadable:
    """A document whose FullName fails: a listing that cannot name what is open."""

    @property
    def FullName(self):
        raise OSError("the object invoked has disconnected from its clients")


# A process that holds another in a Job of the adapter's (D-04), then sleeps: argv = drivers/, the
# pid to hold, and "arm" or "disarm" (disarm, then close the Job, before sleeping).
HOLDER = """import sys, time
sys.path.insert(0, sys.argv[1])
from vkforge.runtime import com
job = com.Job.hold(int(sys.argv[2]))
if sys.argv[3] == "disarm":
    job.disarm()
    job.close()
print("held", flush=True)
time.sleep(120)
"""


HOUSE = [("apply_paragraph_style", {"selector": "first", "style": "Heading1"}),
         ("set_font_run", {"selector": "body", "font": {"name": "Garamond", "size": 11}}),
         ("set_footer", {"text": FOOTER}),
         ("save", {})]
CHECKS = [("verify_paragraph_style", {"selector": "first", "style": "Heading1"}),
          ("verify_font_run", {"selector": "body", "font": {"name": "Garamond", "size": 11}}),
          ("verify_footer", {"text": FOOTER})]


class RuntimeTest(unittest.TestCase):
    def setUp(self):
        self.binding = json.loads((FIXTURES / "binding-word.json").read_text(encoding="utf-8"))

    def house_style(self, app, doc_path: Path):
        doc = app.open(str(doc_path))
        try:
            for verb, args in HOUSE:
                app.call(verb, doc, **args)
            app.call("export", doc, format="pdf", path=str(doc_path.with_suffix(".pdf")))
            checks = [bool(app.call(verb, doc, **args)) for verb, args in CHECKS]
            checks.append(bool(app.call("verify_pdf", doc, path=str(doc_path.with_suffix(".pdf")))))
            wrong = bool(app.call("verify_footer", doc, text="un autre pied"))
        finally:
            app.close(doc)
        return checks, wrong

    def test_the_fake_and_the_adapter_share_one_verb_table(self):
        self.assertEqual(set(com.VERBS), set(fake.VERBS))
        self.assertLessEqual(plan.EMITTED_VERBS - {"open"}, set(com.VERBS), "every verb a plan can emit has a runtime")

    def test_the_fake_applies_the_house_style_and_its_checks_read_it_back(self):
        doc = ooxml_fixtures.docx(Path(tempfile.mkdtemp()) / "c.docx", title_style="Normal", body_font=("Calibri", 12))
        checks, wrong = self.house_style(FakeApplication(self.binding), doc)
        self.assertEqual((checks, wrong), ([True, True, True, True], False))
        obs = mine.mine_file(doc)["obs"]
        self.assertIn({"kind": "font_run", "where": "body", "value": {"name": "Garamond", "size": 11}}, obs)

    def test_a_changed_type_library_is_an_office_version_refusal(self):
        # L-P22: the binding carries the hash of the type library the driver was learned on; another one on
        # the node (Office updated) refuses before any document is opened. No Office needed: the registry
        # read is replaced, the comparison is the adapter's own.
        self.assertRegex(self.binding["typelib_sha256"], r"^sha256:[0-9a-f]{64}$")
        with mock.patch.object(com, "live_typelib_sha256", return_value=self.binding["typelib_sha256"]) as live:
            com.probe(self.binding)
        live.assert_called_once_with(self.binding["typelib"]["guid"])
        with mock.patch.object(com, "live_typelib_sha256", return_value="sha256:" + "0" * 64):
            with self.assertRaises(DriverRefusal) as cm:
                com.probe(self.binding)
        self.assertEqual((cm.exception.reason, cm.exception.detail),
                         ("precondition", "office version changed; remedy: vk driver learn again"))
        self.assertIsNone(FakeApplication(self.binding).probe(), "no type library behind the fake")

    @unittest.skipUnless(node_office("word"), NODE_STEP)
    @mock.patch.dict(os.environ, {"VK_DRIVER_APPLICATIONS": "WINWORD.EXE"})
    def test_the_adapter_applies_the_house_style_on_the_real_word(self):
        if com.pids_of({"WINWORD.EXE"}) != {}:
            self.skipTest("a Word runs, or there is no snapshot: never touched (ruling PF-19)")
        doc = ooxml_fixtures.docx(Path(tempfile.mkdtemp()) / "c.docx", title_style="Normal", body_font=("Calibri", 12))
        guid = inventory.APPS["word"]["typelib"]
        self.assertEqual(com.live_typelib_sha256(guid), canon.sha256_file(inventory.typelib_path(guid)),
                         "the probe reads the file inventory hashed (L-P22)")
        app, err = com.Application(self.binding), io.StringIO()
        try:
            checks, wrong = self.house_style(app, doc)
        finally:
            pid = app.pid
            with contextlib.redirect_stderr(err):
                app.quit()
        self.assertIsNotNone(pid, "the start was identified: the end below is the started instance's")
        self.assertEqual((checks, wrong), ([True, True, True, True], False))
        self.assertNotIn("not handed over", err.getvalue(), "the end read the real Word's inventory and quit it")
        obs = mine.mine_file(doc)["obs"]
        self.assertIn({"kind": "paragraph_style", "where": "first", "value": {"builtin": "Heading1"}}, obs)

    @unittest.skipUnless(node_office("word"), NODE_STEP)
    @mock.patch.dict(os.environ, {"VK_DRIVER_APPLICATIONS": "WINWORD.EXE"})
    def test_a_document_already_open_in_the_users_word_is_refused_and_left_open(self):
        # The plan's precondition "not open elsewhere", in the instance the adapter attaches to (spike
        # verdict 3): there Documents.Open hands back the user's own document, unsaved edits included,
        # which a save would write and a close would shut. Refused, and the user's document left as it was.
        # A node step (PF-19): run with no Word open, the test plays the user in a Word the adapter starts.
        if com.pids_of({"WINWORD.EXE"}) != {}:
            self.skipTest("a Word runs, or there is no snapshot: never touched (ruling PF-19)")
        doc = ooxml_fixtures.docx(Path(tempfile.mkdtemp()) / "c.docx", title_style="Normal")
        before = doc.read_bytes()
        app = com.Application(self.binding)
        try:
            users = com.guarded(lambda: app.app.Documents.Open(str(doc), ReadOnly=False, Visible=False), "user")
            try:
                com.guarded(lambda: users.Range(0, 0).InsertBefore("EDIT "), "user's edit")
                with self.assertRaises(DriverRefusal) as cm:
                    app.open(str(doc))
                self.assertEqual(cm.exception.reason, "precondition")
                self.assertIn("already open", cm.exception.detail)
                self.assertEqual(com.guarded(lambda: users.Range(0, 5).Text, "user's text"), "EDIT ")
            finally:
                com.guarded(lambda: users.Close(SaveChanges=False), "user's close")
        finally:
            err, pid = io.StringIO(), app.pid
            with contextlib.redirect_stderr(err):
                app.quit()
        self.assertIsNotNone(pid, "the start was identified: the end below is the started instance's")
        self.assertEqual(doc.read_bytes(), before)
        self.assertNotIn("not handed over", err.getvalue(), "the end read the real Word's inventory and quit it")

    def test_the_adapter_walks_the_binding_over_a_stub_object_model(self):
        # The verbs' object-model code (heads, selectors, bands, styles, export) through Application.call,
        # off Windows: the members and constants come from binding-word.json, none from the adapter.
        word = StubWord()
        app = com.Application(self.binding, connect=lambda: (word, com.Owned(4242, StubJob())))
        pdf = Path(tempfile.mkdtemp()) / "c.pdf"
        doc = app.open("C:/stub/c.docx")
        app.call("apply_paragraph_style", doc, selector="first", style="Heading1")
        self.assertEqual(doc.Paragraphs.Item(1).Style.NameLocal, "style -2", "wdStyleHeading1, a constant")
        app.call("set_font_run", doc, selector="body", font={"name": "Garamond", "size": 11})
        self.assertEqual([(p.Range.Font.Name, p.Range.Font.Size) for p in doc.Paragraphs.items],
                         [("Calibri", 12.0)] + [("Garamond", 11)] * 3)
        app.call("set_footer", doc, text="Pied\nligne 2")
        self.assertEqual(doc.Sections.Item(1).Footers.Item(1).Range.Text, "Pied\rligne 2", "wdHeaderFooterPrimary")
        app.call("set_header", doc, text="En-tête")
        app.call("save", doc)
        app.call("export", doc, format="pdf", path=str(pdf))
        self.assertEqual((doc.saved, doc.exported), (1, [(os.path.abspath(pdf), 17)]), "wdExportFormatPDF")
        checks = [app.call("verify_paragraph_style", doc, selector="first", style="Heading1"),
                  app.call("verify_font_run", doc, selector="body", font={"name": "Garamond", "size": 11}),
                  app.call("verify_footer", doc, text="Pied\nligne 2"),
                  app.call("verify_header", doc, text="En-tête"),
                  app.call("verify_pdf", doc, path=str(pdf))]
        wrong = [app.call("verify_paragraph_style", doc, selector="first", style="Title"),
                 app.call("verify_font_run", doc, selector="body", font={"name": "Garamond", "size": 12}),
                 app.call("verify_footer", doc, text="un autre pied")]
        self.assertEqual((checks, wrong), ([True] * 5, [False] * 3))
        font = {"name": "Garamond", "size": 11}
        for call, reason, detail in [
                (lambda: app.call("set_font_run", doc, selector=None, font=font),
                 "precondition", "verb set_font_run needs a selector"),
                (lambda: app.call("set_font_run", doc, selector="index:9", font=font),
                 "precondition", "selector index:9 selects no paragraph of this document"),
                (lambda: app.call("apply_paragraph_style", doc, selector="first", style="NoSuchStyle"),
                 "internal", "no unique constant for NoSuchStyle in WdBuiltinStyle"),
                (lambda: app.open("C:/stub/c.docx"),
                 "precondition", "document already open in the application (the user's window)")]:
            with self.assertRaises(DriverRefusal) as cm:
                call()
            self.assertEqual((cm.exception.reason, cm.exception.detail), (reason, detail))
        # A failure inside a verb is `internal`, one while opening `precondition`: the fake's words.
        doc.Save = lambda: (_ for _ in ()).throw(OSError("the disk is full"))
        with self.assertRaises(DriverRefusal) as cm:
            app.call("save", doc)
        self.assertEqual((cm.exception.reason, cm.exception.detail), ("internal", "save: the disk is full"))
        word.missing.add("C:/stub/gone.docx")
        with self.assertRaises(DriverRefusal) as cm:
            app.open("C:/stub/gone.docx")
        self.assertEqual((cm.exception.reason, cm.exception.detail),
                         ("precondition", "document cannot be opened: this file could not be found"))
        app.close(doc)
        self.assertEqual(doc.closed, {"SaveChanges": False})
        app.quit()
        self.assertEqual(word.quits, 1)

    def test_the_users_instance_gets_its_display_alerts_back_and_only_a_started_one_is_quit(self):
        # L-P47: DisplayAlerts is read, silenced, and put back whenever the instance is left running;
        # L-P46 under D-04 and PF-4: only the instance the adapter started and identified is quit — the
        # user's is attached, never quit.
        user = StubWord()
        attached = com.Application(self.binding, connect=lambda: (user, None))
        self.assertEqual(user.DisplayAlerts, 0, "alerts_off while the driver works")
        attached.quit()
        attached.quit()
        self.assertEqual((user.DisplayAlerts, user.quits, attached.app), (-1, 0, None))
        ours = StubWord()
        started = com.Application(self.binding, connect=lambda: (ours, com.Owned(4242, StubJob())))
        started.quit()
        self.assertEqual((ours.quits, ours.DisplayAlerts, started.app), (1, 0, None), "quit: nothing to put back")

    def test_the_cleanup_close_is_bounded_and_the_first_refusal_wins(self):
        # After a step hangs, the driver's `finally` close meets the same wedged application: it gets the
        # short cleanup deadline, its refusal goes to stderr, and the host hears the step's refusal.
        word = StubWord()
        app = com.Application(self.binding, connect=lambda: (word, None))
        app.deadline_s, app.cleanup_deadline_s = 0.5, 0.5
        doc = app.open("C:/stub/c.docx")
        wedged = threading.Event()
        doc.Save = lambda: wedged.wait(30)
        doc.Close = lambda **kw: wedged.wait(30)
        err, started = io.StringIO(), time.monotonic()
        try:
            with contextlib.redirect_stderr(err), self.assertRaises(DriverRefusal) as cm:
                try:
                    app.call("save", doc)
                finally:
                    app.close(doc)
        finally:
            wedged.set()
        self.assertEqual((cm.exception.reason, cm.exception.detail), ("timeout", "save: no answer within 0.5 s"))
        self.assertEqual(err.getvalue(), "close: close: no answer within 0.5 s\n")
        self.assertLess(time.monotonic() - started, 5)

    def test_a_failed_close_after_a_successful_action_is_surfaced(self):
        # After an action whose every step answered, a close that fails or hangs would leave the document
        # open, hidden, in the user's instance: the host hears it as `internal`, nothing goes to stderr only.
        word = StubWord()
        app = com.Application(self.binding, connect=lambda: (word, None))
        app.deadline_s, app.cleanup_deadline_s = 0.5, 0.5
        locked = app.open("C:/stub/locked.docx")
        app.call("save", locked)
        locked.Close = lambda **kw: (_ for _ in ()).throw(OSError("the document is in use"))
        with self.assertRaises(DriverRefusal) as cm:
            app.close(locked)
        self.assertEqual((cm.exception.reason, cm.exception.detail),
                         ("internal", "close failed: the document is in use"))
        hung = app.open("C:/stub/hung.docx")
        app.call("save", hung)
        wedged = threading.Event()
        hung.Close = lambda **kw: wedged.wait(30)
        try:
            with self.assertRaises(DriverRefusal) as cm:
                app.close(hung)
        finally:
            wedged.set()
        self.assertEqual((cm.exception.reason, cm.exception.detail),
                         ("internal", "close failed: close: no answer within 0.5 s"))

    def test_a_constant_is_the_least_qualified_member_ending_with_the_value(self):
        style = {v["verb"]: v for v in self.binding["verbs"]}["apply_paragraph_style"]
        enum = style["constants"]["WdBuiltinStyle"]
        self.assertEqual([com._constant(style, s) for s in ("Heading1", "Title", "Normal")],
                         [enum["wdStyleHeading1"], enum["wdStyleTitle"], enum["wdStyleNormal"]])
        one = {"verb": "x", "constants": {"OneEnum": {"wdOnlyThing": 5}}}
        self.assertEqual(com._constant(one, "Thing"), 5)
        tie = {"verb": "x", "constants": {"TieEnum": {"xxBar": 1, "yyBar": 2}}}
        for entry, value, detail in [(one, "Other", "no unique constant for Other in OneEnum"),
                                     (tie, "Bar", "no unique constant for Bar in TieEnum"),
                                     (style, "", "no unique constant for  in WdBuiltinStyle")]:
            with self.assertRaises(DriverRefusal) as cm:
                com._constant(entry, value)
            self.assertEqual((cm.exception.reason, cm.exception.detail), ("internal", detail))

    def test_the_probe_walks_the_registry_as_inventory_does(self):
        # L-P22: an unparsable subkey skipped, a leftover higher version without a file skipped, win64
        # before win32 — the same path from both, hence the same hash as the catalogue's.
        guid = inventory.APPS["word"]["typelib"]
        registry = StubRegistry(guid, {"8.7": {"win64": r"C:\Office\old\MSWORD.OLB"}, "FLAGS": {},
                                       "9.0": {"win32": r"C:\Office\x86\MSWORD.OLB"}, "a.0": {}})
        with mock.patch.dict(sys.modules, {"winreg": registry}), mock.patch.object(sys, "platform", "win32"):
            self.assertEqual(com.typelib_path(guid), inventory.typelib_path(guid))
            self.assertEqual(com.typelib_path(guid), r"C:\Office\x86\MSWORD.OLB")
            registry.versions = {"FLAGS": {}, "9.0": {}}
            with self.assertRaises(DriverRefusal) as cm:
                com.typelib_path(guid)
            self.assertEqual(cm.exception.reason, "precondition")
            with self.assertRaises(Refusal):
                inventory.typelib_path(guid)

    @unittest.skipUnless(sys.platform == "win32" and inventory.office_present("word")
                         and inventory.office_present("powerpoint"), "Word and PowerPoint not installed")
    def test_the_probe_reads_the_file_inventory_reads_on_this_node(self):
        for app in ("word", "powerpoint"):
            guid = inventory.APPS[app]["typelib"]
            self.assertEqual(com.typelib_path(guid), inventory.typelib_path(guid), app)

    def test_the_fake_refuses_in_the_adapters_words(self):
        # Opening: precondition. Inside a verb: internal (the adapter's COMError). A missing selector:
        # precondition, as the adapter's.
        fake_app = FakeApplication(self.binding)
        with self.assertRaises(DriverRefusal) as cm:
            fake_app.open(str(Path(tempfile.mkdtemp()) / "absent.docx"))
        self.assertEqual(cm.exception.reason, "precondition")
        path = ooxml_fixtures.docx(Path(tempfile.mkdtemp()) / "c.docx")
        doc = fake_app.open(str(path))
        with self.assertRaises(DriverRefusal) as cm:
            fake_app.call("set_font_run", doc, selector=None, font={"name": "Garamond", "size": 11})
        self.assertEqual((cm.exception.reason, cm.exception.detail), ("precondition", "verb set_font_run needs a selector"))
        fake_app.call("set_footer", doc, text=FOOTER)
        path.unlink()                                   # the document gone under the application
        with self.assertRaises(DriverRefusal) as cm:
            fake_app.call("save", doc)
        self.assertEqual(cm.exception.reason, "internal")

    def test_only_a_positively_identified_process_is_owned(self):
        # D-03, D-04, rulings LF-5 and PF-4: the adapter owns, and holds in its Job, only the process its
        # start added when it is the one new process of the executable and its parent is the DcomLaunch
        # service's process; anything else is attached — never held — and a start it cannot tell apart
        # is one stderr line.
        held, launcher = [], 900

        def hold(pid):
            held.append(pid)
            return f"job of {pid}"

        owned = com.own(True, {1: 4}, {1: 4, 7: launcher}, launcher, hold)
        self.assertEqual((owned.pid, owned.job, held), (7, "job of 7", [7]))
        for args, why in [
                # attached: GetActiveObject found it running
                ((False, {1: 4}, {1: 4, 9: launcher}, launcher), None),
                # CreateObject handed back a running PowerPoint
                ((True, {1: 4}, {1: 4}, launcher), "0 new processes of the application"),
                ((True, {1: 4}, {1: 4, 7: launcher, 8: launcher}, launcher), "2 new processes of the application"),
                # the user's PowerPoint, started from the shell at the same moment
                ((True, {1: 4}, {1: 4, 7: 33}, launcher), "process 7 was not started by the DCOM launcher"),
                ((True, None, {1: 4, 7: launcher}, launcher), "no snapshot of the application's processes"),
                ((True, {1: 4}, {1: 4, 7: launcher}, None), "the DcomLaunch service's process is unknown")]:
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertIsNone(com.own(*args, hold), args)
            self.assertEqual(err.getvalue(), "" if why is None else f"start: treated as attached: {why}\n", args)
        self.assertEqual(held, [7], "nothing else was held")
        self.assertEqual(com.identified({1: 4}, {1: 4, 5: launcher}, launcher), (5, None))
        self.assertIsNone(com.pids_of(set()), "no executable to look for: no snapshot, no owner")

    def test_only_the_started_instance_s_boxes_are_closed(self):
        # D-03, ruling LF-6: past the deadline a box of the owned process is closed and the action
        # refuses `dialog`; a box of another process is never touched — `dialog` (left open) when no
        # process is owned, `timeout` when the owned one shows none.
        hang = threading.Event()
        boxes = [(11, 100), (22, 200)]
        try:
            for owner, closed_want, reason, words in [
                    (100, [11], "dialog", "closed, never answered"),
                    (lambda: 100, [11], "dialog", "closed, never answered"),
                    (None, [], "dialog", "left open"),
                    (300, [], "timeout", "no answer within 0.2 s")]:
                closed = []
                with self.assertRaises(DriverRefusal) as cm:
                    com.guarded(lambda: hang.wait(30), "save", deadline_s=0.2, find_dialogs=lambda: boxes,
                                close=closed.append, owner=owner)
                self.assertEqual((cm.exception.reason, closed), (reason, closed_want), owner)
                self.assertIn(words, cm.exception.detail)
            with self.assertRaises(DriverRefusal) as cm:
                com.guarded(lambda: hang.wait(30), "save", deadline_s=0.2, find_dialogs=lambda: [],
                            close=closed.append, owner=None)
            self.assertEqual(cm.exception.reason, "timeout")
        finally:
            hang.set()

    def test_the_adapter_closes_the_boxes_of_the_instance_it_started_only(self):
        # D-03 through Application: the guard's owner is the session's started process.
        for connect, closed_want, words in [
                (lambda: (StubWord(), com.Owned(4242, StubJob())), [7], "closed, never answered"),
                (lambda: (StubWord(), None), [], "left open")]:
            app = com.Application(self.binding, connect=connect)
            app.deadline_s = app.cleanup_deadline_s = 0.3
            doc = app.open("C:/stub/c.docx")
            wedged, closed = threading.Event(), []
            doc.Save = lambda: wedged.wait(30)
            try:
                with mock.patch.object(com, "dialogs", lambda exes: [(7, 4242), (8, 999)]), \
                        mock.patch.object(com, "close_window", closed.append), \
                        self.assertRaises(DriverRefusal) as cm:
                    app.call("save", doc)
            finally:
                wedged.set()
            self.assertEqual((cm.exception.reason, closed), ("dialog", closed_want))
            self.assertIn(words, cm.exception.detail)

    def test_quit_waits_for_the_started_process_then_closes_its_job(self):
        # D-04, D-35: a clean end with nothing foreign quits the instance the adapter started, gives its
        # process what is left of the clean-end window — not the whole window: here Quit took 0.3 s of a
        # 1.0 s window (review tests-3) — then closes the Job, which ends whatever is left; an attached
        # instance has no Job at all.
        late = "quit: process 4242 still running at the end of the clean-end window; its Job ends it\n"
        for exits, said in ((True, ""), (False, late)):
            word, job = StubWord(), StubJob(exits)
            quits = word.Quit
            word.Quit = lambda: (time.sleep(0.3), quits())
            app = com.Application(self.binding, connect=lambda: (word, com.Owned(4242, job)))
            self.assertEqual(app.pid, 4242)
            err = io.StringIO()
            with mock.patch.object(com, "QUIT_WAIT_S", 1.0), contextlib.redirect_stderr(err):
                app.quit()
            self.assertEqual((word.quits, job.calls, err.getvalue()), (1, ["wait", "close"], said))
            self.assertTrue(0 < job.waited <= 0.75, job.waited)
            self.assertIsNone(app.pid, "the session let go of what it held")
        self.assertEqual(com.QUIT_WAIT_S, 3.0, "under the 5 s a host (L-P49) and qualify give a driver")
        user = StubWord()
        attached = com.Application(self.binding, connect=lambda: (user, None))
        attached.quit()
        self.assertEqual((user.quits, user.DisplayAlerts, attached.pid), (0, -1, None))

    def test_the_instance_survives_only_a_clean_end_inside_the_window(self):
        # Founder decision D-35, bound (1): every call that may keep the started instance runs inside the
        # clean-end window, QUIT_WAIT_S, under the host's 5 s grace (L-P49), never on a call's deadline
        # (here 5 s, the window patched to 0.5 s): the driver's own document's close, the inventory (a
        # listing that hangs), Quit, Visible set and Visible read back (review tests-3). Past the window
        # there is no disarm, and the Job, closed, takes the instance. An unclean death never reaches this
        # code: the Job takes the instance by itself (test_a_held_process_dies_with_its_holder_and_a_disarmed_one_lives).
        for stall, what in (("close", "close"), ("inventory", "inventory"), ("Quit", "quit"),
                            ("Visible", "hand-over"), ("Visible?", "hand-over")):
            hang = threading.Event()
            word, job = StallingWord(stall, hang), StubJob()
            app = com.Application(self.binding, connect=lambda: (word, com.Owned(4242, job)))
            app.deadline_s = app.cleanup_deadline_s = 5.0
            own = app.open("C:/stub/own.docx")
            if stall == "close":
                own.Close = lambda **kw: hang.wait(30)
            elif stall == "inventory":
                word.Documents.items.append(Hung(hang))
            elif stall.startswith("Visible"):
                word.Documents.items.append(StubDocument(word, "C:/stub/the-users.docx"))
            err, began = io.StringIO(), time.monotonic()
            try:
                with mock.patch.object(com, "QUIT_WAIT_S", 0.5), contextlib.redirect_stderr(err):
                    app.quit()
            finally:
                hang.set()
            self.assertLess(time.monotonic() - began, 2.0, f"{stall}: the window bounds the end, not a call's deadline")
            self.assertEqual(job.calls, ["close"], f"{stall}: no disarm, no wait: the Job takes it")
            self.assertIn(f"quit: process 4242 not handed over ({what}: no answer within", err.getvalue(), stall)

    def test_the_drivers_own_documents_are_closed_first_and_never_keep_the_instance(self):
        # Founder decision D-35, bound (2): the driver first closes, without saving, every document it
        # opened and still holds (here two, whose closes after the action failed — review tests-3);
        # holding nothing else, the instance is quit. A document of its own that will not close does not
        # keep the instance: no Quit, no disarm, and the Job, closed, takes it.
        word, job = StubWord(), StubJob()
        app = com.Application(self.binding, connect=lambda: (word, com.Owned(4242, job)))
        left = [app.open("C:/stub/left.docx"), app.open("C:/stub/also-left.docx")]
        app.quit()
        self.assertEqual(([d.closed for d in left], len(word.Documents.items), word.quits),
                         ([{"SaveChanges": False}] * 2, 0, 1))
        self.assertEqual(job.calls, ["wait", "close"])
        word, job = StubWord(), StubJob()
        app = com.Application(self.binding, connect=lambda: (word, com.Owned(4242, job)))
        stuck = app.open("C:/stub/stuck.docx")
        stuck.Close = lambda **kw: (_ for _ in ()).throw(OSError("the document is in use"))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            app.quit()
        self.assertEqual((word.quits, job.calls), (0, ["close"]), "no Quit, no disarm: the Job takes it")
        self.assertEqual(err.getvalue(),
                         "quit: process 4242 not handed over (the document is in use); its Job ends it\n")

    def test_a_document_is_foreign_only_when_the_driver_never_opened_its_name(self):
        # Founder decision D-35, bound (3): a document is foreign only when its FullName is absent from
        # the exact list of those the driver opened — one it opened and closed, open again in its
        # instance, is not. A listing that fails, or an open whose document the driver never named, is
        # a doubt: no disarm, and the Job, closed, takes the instance.
        word, job = StubWord(), StubJob()
        app = com.Application(self.binding, connect=lambda: (word, com.Owned(4242, job)))
        app.close(app.open("C:/stub/a.docx"))
        word.Documents.items.append(StubDocument(word, "C:/stub/a.docx"))       # its name is the driver's
        app.quit()
        self.assertEqual((word.quits, job.calls), (1, ["wait", "close"]), "not foreign: quit, never kept")
        word, job = StubWord(), StubJob()
        app = com.Application(self.binding, connect=lambda: (word, com.Owned(4242, job)))
        word.Documents.items += [StubDocument(word, "C:/stub/the-users.docx"), Unreadable()]
        with contextlib.redirect_stderr(io.StringIO()):
            app.quit()
        self.assertEqual((word.quits, job.calls), (0, ["close"]), "a listing that fails: the Job takes it")
        word, job, wedged = StubWord(), StubJob(), threading.Event()
        app = com.Application(self.binding, connect=lambda: (word, com.Owned(4242, job)))
        app.deadline_s = 0.2
        word.Documents.Open = lambda path, **kw: wedged.wait(30)               # an Open refused by its guard
        try:
            with self.assertRaises(DriverRefusal):
                app.open("C:/stub/slow.docx")
        finally:
            wedged.set()
        word.Documents.items.append(StubDocument(word, "C:/stub/the-users.docx"))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            app.quit()
        self.assertEqual((word.quits, job.calls), (0, ["close"]), "an open never named is a doubt")
        self.assertIn("(1 open(s) whose document the driver never named)", err.getvalue())

    def test_the_instance_kept_is_handed_to_the_user_and_only_its_job_is_disarmed(self):
        # Founder decision D-35, bound (4), as ruling PF-12 reads it: with a foreign document identified,
        # the instance is handed to the user, visible and under the user's control — Visible set true
        # and read back true (True as comtypes gives Word's VARIANT_BOOL, -1 as it gives PowerPoint's
        # msoTrue, and nothing else: not msoCTrue's 1, not msoTriStateMixed's -2), its own Job alone
        # disarmed, DisplayAlerts put back, every reference released — and stays, the user's document in
        # it, the driver's closed. UserControl is never touched: Word's has no setter, and neither has
        # OrderedWord's. A Visible that cannot be set, or that reads back anything else, or a disarm
        # Windows refuses (review tests-4): nothing put back, and the Job, closed, takes the instance.
        # Another started instance, holding nothing foreign, is quit and its Job closed armed.
        self.assertEqual(com.COM_TRUE, -1, "VARIANT_TRUE, and PowerPoint's msoTrue")
        for refuses, shows, why in [(None, True, None),                     # Word's Visible, read back
                                    (None, com.COM_TRUE, None),             # PowerPoint's, read back
                                    ("Visible", True, "Visible cannot be set"),
                                    (None, False, "hand-over: Visible reads back False"),
                                    (None, 0, "hand-over: Visible reads back 0"),       # msoFalse
                                    (None, 1, "hand-over: Visible reads back 1"),       # msoCTrue
                                    (None, -2, "hand-over: Visible reads back -2")]:    # msoTriStateMixed
            word, job = OrderedWord([], shows), StubJob()
            app = com.Application(self.binding, connect=lambda: (word, com.Owned(4242, job)))
            own = app.open("C:/stub/own.docx")
            word.Documents.items.append(StubDocument(word, "C:/stub/the-users.docx"))
            word.refuses = refuses
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                app.quit()
            self.assertEqual((own.closed, word.quits, app.app), ({"SaveChanges": False}, 0, None), shows)
            if why is None:
                self.assertEqual((word.shown, word.DisplayAlerts, job.calls), (shows, -1, ["disarm", "close"]))
                self.assertEqual([d.FullName for d in word.Documents.items], ["C:/stub/the-users.docx"])
            else:
                self.assertEqual((word.DisplayAlerts, job.calls), (0, ["close"]), "no disarm, nothing put back")
                self.assertEqual(err.getvalue(), f"quit: process 4242 not handed over ({why}); its Job ends it\n")
        word, refusing = OrderedWord([]), RefusingJob()
        app = com.Application(self.binding, connect=lambda: (word, com.Owned(4242, refusing)))
        app.open("C:/stub/own.docx")
        word.Documents.items.append(StubDocument(word, "C:/stub/the-users.docx"))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            app.quit()
        self.assertEqual((refusing.calls, word.DisplayAlerts), (["disarm", "close"], 0),
                         "a refused disarm: no hand-over")
        self.assertEqual(err.getvalue(),
                         "quit: process 4242 not handed over (the Job cannot be changed); its Job ends it\n")
        other, armed = StubWord(), StubJob()
        idle = com.Application(self.binding, connect=lambda: (other, com.Owned(4343, armed)))
        idle.quit()
        self.assertEqual((other.quits, armed.calls), (1, ["wait", "close"]), "nothing foreign: quit, Job armed")

    def test_every_firing_is_one_stderr_line_with_the_pid_and_the_count(self):
        # Founder decision D-35, bound (5): an instance kept is recorded in one stderr line (driver.log),
        # its process id and the count of foreign documents — never a name, never a content.
        busy = StubWord()
        holding = com.Application(self.binding, connect=lambda: (busy, com.Owned(4242, StubJob())))
        holding.open("C:/stub/own.docx")
        busy.Documents.items += [StubDocument(busy, "C:/stub/Contrat Dupont.docx"),
                                 StubDocument(busy, "C:/stub/Bilan 2026.docx")]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            holding.quit()
        self.assertEqual(err.getvalue(),
                         "quit: process 4242 handed to the user, 2 foreign document(s) open in it; its Job disarmed\n")

    def test_the_end_closes_then_lists_then_hands_over_then_disarms(self):
        # Rulings PF-2, PF-12 and PF-18: inside QUIT_WAIT_S and before any other call — the driver's own
        # documents closed, the instance's listed (the walk, `list`, and each FullName read), Visible set
        # true and read back, then the disarm; DisplayAlerts and the release come after it, best effort.
        # Nothing foreign listed: Quit, and Visible never touched — the instance stays hidden. A failure
        # before the disarm (here Visible refused, or read back false): no disarm, nothing put back, and
        # the Job, closed, takes the instance.
        users = ["list", "FullName the-users.docx"]
        for refuses, shows, foreign, want in [
                (None, True, True, ["Close own.docx", *users, f"Visible {com.COM_TRUE}", "Visible? True", "disarm",
                                    "DisplayAlerts -1", "close"]),
                ("Visible", True, True, ["Close own.docx", *users, "close"]),
                (None, False, True, ["Close own.docx", *users, f"Visible {com.COM_TRUE}", "Visible? False", "close"]),
                (None, True, False, ["Close own.docx", "list", "Quit", "wait", "close"])]:
            log = []
            word, job = OrderedWord(log, shows), StubJob(log=log)
            app = com.Application(self.binding, connect=lambda: (word, com.Owned(4242, job)))
            app.open("C:/stub/own.docx")                              # its close after the action failed
            if foreign:
                word.Documents.items.append(StubDocument(word, "C:/stub/the-users.docx"))
            word.refuses = refuses
            log.clear()                                               # the session's start is not the subject
            with contextlib.redirect_stderr(io.StringIO()):
                app.quit()
            self.assertEqual(log, want, (refuses, shows, foreign))

    def test_a_session_that_cannot_begin_ends_the_instance_it_started(self):
        # Ruling LF-8: DisplayAlerts cannot be read — the session refuses `precondition`, and the Job of
        # the instance it started is closed at once (an armed Job ends that instance).
        job = StubJob()
        with self.assertRaises(DriverRefusal) as cm:
            com.Application(self.binding, connect=lambda: (SilentWord(), com.Owned(4242, job)))
        self.assertEqual(cm.exception.reason, "precondition")
        self.assertIn("cannot be started: the application is not answering", cm.exception.detail)
        self.assertEqual(job.calls, ["close"])

    @unittest.skipIf(sys.platform == "win32", "off Windows only")
    def test_off_windows_there_is_no_snapshot_and_no_job(self):
        self.assertIsNone(com.processes())
        self.assertIsNone(com.pids_of({"WINWORD.EXE"}))
        self.assertIsNone(com.dcom_launcher_pid())
        with self.assertRaises(OSError):
            com.Job.hold(os.getpid())

    @unittest.skipUnless(sys.platform == "win32", "a Toolhelp32 snapshot is Windows'")
    def test_the_snapshot_sees_this_process_by_its_executable(self):
        me = next(p for p in com.processes() if p[0] == os.getpid())
        self.assertTrue(me[2].endswith(".EXE"), me)
        self.assertEqual(com.pids_of({me[2]})[os.getpid()], me[1], "each process with its parent")
        launcher = com.dcom_launcher_pid()
        self.assertIn((launcher, "SVCHOST.EXE"), {(pid, exe) for pid, _, exe in com.processes()},
                      "the DcomLaunch service runs in a svchost: the parent of what COM starts (PF-4)")

    @unittest.skipUnless(sys.platform == "win32", "Job objects are Windows'")
    def test_a_held_process_dies_with_its_holder_and_a_disarmed_one_lives(self):
        # D-04 without Office: a holder puts a sleeper in a kill-on-close Job of its own and is killed
        # with TerminateProcess (os.kill on Windows): no atexit, no cleanup. The sleeper dies with it
        # (D-35 (1): an unclean death leaves the Job to take the instance); a sleeper whose own Job was
        # disarmed first (D-35 (4)) lives on, and so does one never held.
        base = getattr(sys, "_base_executable", sys.executable)     # the interpreter, not a venv launcher
        sleep = [base, "-I", "-c", "import time; time.sleep(120)"]
        held, disarmed, free = (subprocess.Popen(sleep) for _ in range(3))
        holders = [subprocess.Popen([base, "-I", "-c", HOLDER, str(DRIVERS), str(p.pid), how],
                                    stdout=subprocess.PIPE, text=True)
                   for p, how in ((held, "arm"), (disarmed, "disarm"))]
        try:
            for h in holders:
                self.assertEqual(h.stdout.readline().strip(), "held")
                os.kill(h.pid, signal.SIGTERM)                      # TerminateProcess
                h.wait(timeout=10)
            held.wait(timeout=10)                                    # TimeoutExpired: it outlived its holder
            time.sleep(1)
            self.assertIsNone(disarmed.poll(), "a disarmed Job lets its process outlive the handle")
            self.assertIsNone(free.poll(), "a process never held is never touched")
        finally:
            for p in (held, disarmed, free, *holders):
                p.kill()
                p.wait(timeout=10)
            for h in holders:
                h.stdout.close()

    def test_the_instance_handed_over_is_the_users_and_its_boxes_are_left_open(self):
        # Task 3 self-review, D-03 with D-35 (4): once handed over, the started instance is the user's — a
        # box that holds the DisplayAlerts restore (a Save As the user opened, say) is left open, never
        # closed, the restore's refusal goes to stderr, and the hand-over stands: its Job stays disarmed.
        wedged, closed = threading.Event(), []

        class Busy(StubWord):
            def __setattr__(self, name, value):
                if name == "DisplayAlerts" and value == -1 and "Documents" in vars(self):
                    wedged.wait(30)                         # the user's box holds the restore
                super().__setattr__(name, value)

        word, job = Busy(), StubJob()
        app = com.Application(self.binding, connect=lambda: (word, com.Owned(4242, job)))
        app.cleanup_deadline_s = 0.3
        app.open("C:/stub/own.docx")
        word.Documents.items.append(StubDocument(word, "C:/stub/the-users.docx"))
        err = io.StringIO()
        try:
            with mock.patch.object(com, "dialogs", lambda exes: [(7, 4242)]), \
                    mock.patch.object(com, "close_window", closed.append), contextlib.redirect_stderr(err):
                app.quit()
        finally:
            wedged.set()
        self.assertEqual((closed, job.calls), ([], ["disarm", "close"]), "the box left open, the Job disarmed")
        self.assertEqual(err.getvalue(),
                         "quit: process 4242 handed to the user, 1 foreign document(s) open in it; its Job disarmed\n"
                         "quit: DisplayAlerts: a dialog box of the application is open; left open: the instance is "
                         "not one the driver owns\n")

    def test_a_document_in_protected_view_is_foreign_unless_the_driver_opened_it(self):
        # Ruling PF-21 (review office-safety-5): the inventory reads the instance's Protected View windows
        # too, which its document collection does not list. A document there is foreign when its FullName
        # is not one the driver opened — the instance is handed over — and the driver's own, reopened
        # there, is not: bound (3) decides by name.
        for name, kept in (("C:/stub/downloaded.docx", True), ("C:/stub/own.docx", False)):
            word, job = StubWord(), StubJob()
            app = com.Application(self.binding, connect=lambda: (word, com.Owned(4242, job)))
            app.close(app.open("C:/stub/own.docx"))
            word.ProtectedViewWindows.items.append(types.SimpleNamespace(Document=StubDocument(word, name)))
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                app.quit()
            if kept:
                self.assertEqual((word.quits, job.calls), (0, ["disarm", "close"]), name)
                self.assertEqual(err.getvalue(), "quit: process 4242 handed to the user, 1 foreign document(s) "
                                                 "open in it; its Job disarmed\n")
            else:
                self.assertEqual((word.quits, job.calls, err.getvalue()), (1, ["wait", "close"], ""), name)

    def test_a_snapshot_cut_short_is_no_snapshot(self):
        # Review quality-4, PF-4's fail-closed rule: a walk of the Toolhelp32 snapshot that stops on an
        # error other than ERROR_NO_MORE_FILES is no snapshot (None), never a short list a start would
        # read as complete — a process already running would then pass for a new one.
        class Kernel:
            def __init__(self, rows, error):
                self.rows, self.error, self.at = rows, error, 0

            def CreateToolhelp32Snapshot(self, flags, pid):
                return 77

            def walk(self, snap, ref):
                if self.at >= len(self.rows):
                    return 0
                ref._obj.th32ProcessID, ref._obj.th32ParentProcessID, ref._obj.szExeFile = self.rows[self.at]
                self.at += 1
                return 1

            Process32FirstW = Process32NextW = walk

            def CloseHandle(self, handle):
                pass

        rows = [(4, 0, "System"), (1234, 4, "winword.exe")]
        for kernel, want in ((Kernel(rows, 18), [(4, 0, "SYSTEM"), (1234, 4, "WINWORD.EXE")]),   # ERROR_NO_MORE_FILES
                             (Kernel(rows[:1], 5), None),          # cut short after one row: access denied
                             (Kernel([], 5), None)):               # the first row refused
            with mock.patch.object(sys, "platform", "win32"), mock.patch.object(com, "_kernel32", lambda: kernel), \
                    mock.patch.object(com.ctypes, "get_last_error", lambda: kernel.error, create=True):
                self.assertEqual(com.processes(), want, kernel.error)

    def office_start(self, attach, create, now, err, **patches):
        """com.Application on its own start, comtypes' GetActiveObject and CreateObject replaced by `attach`
        and `create`, every snapshot read from `now["pids"]`, the DcomLaunch service's process 900, and the
        start's deadline 0.3 s, and the identity ("WINWORD.EXE", 900, 1234) for any process it identifies
        (rulings PF-4, PF-16, PF-17, PF-23). Windows only: the start loads comtypes."""
        import comtypes.client

        with contextlib.ExitStack() as stack:
            exes = patches.pop("exes", "WINWORD.EXE")
            stack.enter_context(mock.patch.dict(os.environ, {"VK_DRIVER_APPLICATIONS": exes}))
            stack.enter_context(mock.patch.object(comtypes.client, "GetActiveObject", attach))
            stack.enter_context(mock.patch.object(comtypes.client, "CreateObject", create))
            stack.enter_context(mock.patch.object(com, "pids_of", lambda exes: dict(now["pids"]) if exes else None))
            stack.enter_context(mock.patch.object(com, "dcom_launcher_pid", lambda: 900))
            stack.enter_context(mock.patch.object(com, "CALL_DEADLINE_S", 0.3))
            stack.enter_context(mock.patch.object(com, "identity", lambda pid: ("WINWORD.EXE", 900, 1234)))
            for name, value in patches.items():
                stack.enter_context(mock.patch.object(com, name, value))
            stack.enter_context(contextlib.redirect_stderr(err))
            return com.Application(self.binding)

    @unittest.skipUnless(sys.platform == "win32", "the adapter's start is Windows' (comtypes)")
    def test_a_start_owns_and_holds_only_what_it_started(self):
        # Rulings PF-17 and PF-4 (review office-safety-1, quality-2, tests-5): a start held past its
        # deadline closes a box, and holds a process, only when the start reached CreateObject and its one
        # new process is a child of the DcomLaunch service. Held in GetActiveObject — an attach, which
        # starts nothing — it owns nothing, even when such a process appeared meanwhile (another client's
        # start): its box is left open, nothing held. A new process of another parent is never held either.
        def nothing_running(progid):
            raise OSError("operation unavailable")           # MK_E_UNAVAILABLE: no instance to attach to

        for attach_hangs, parent, words, closed_want, held_want, said in [
                (True, 900, "left open", [], [], ""),
                (False, 900, "closed, never answered", [70], [7], ""),
                (False, 33, "left open", [], [], "start: treated as attached: process 7 was not started by the DCOM "
                                                 "launcher\n")]:
            hang, now, closed, held, jobs, err = threading.Event(), {"pids": {1: 4}}, [], [], [], io.StringIO()

            # Each case's own objects, bound now: a worker an earlier case left blocked must not reach them.
            def appear_then_hang(progid, parent=parent, now=now, hang=hang):
                now["pids"] = {1: 4, 7: parent}                # one new WINWORD.EXE appears meanwhile
                hang.wait(30)
                raise OSError("never answered")

            def hold(pid, held=held):
                held.append(pid)
                return StubJob()

            try:
                with self.assertRaises(DriverRefusal) as cm:
                    self.office_start(appear_then_hang if attach_hangs else nothing_running, appear_then_hang, now,
                                      err, dialogs=lambda exes: [(70, 7)], close_window=closed.append, _hold=hold,
                                      _HELD=jobs)
            finally:
                hang.set()
            self.assertEqual((cm.exception.reason, closed, held, len(jobs)),
                             ("dialog", closed_want, held_want, len(held_want)), (attach_hangs, parent))
            self.assertIn(words, cm.exception.detail)
            self.assertEqual(err.getvalue(), said)

    @unittest.skipUnless(sys.platform == "win32", "the adapter's start is Windows' (comtypes)")
    def test_a_start_windows_will_not_hold_ends_at_once_and_refuses(self):
        # Rulings PF-16, PF-23 and PF-25 (review spec-1): a start positively identified whose Job Windows
        # refuses is quit at once — no document is in it yet — and the session refuses `precondition`;
        # Owned.job is never None. A quit that fails leaves its process to be ended by its PID, once
        # checked to be the one identified; one line of driver.log says how the instance ended. The one
        # instance left outside a Job is one whose PID check fails or cannot be made (`not ended: …`).
        def nothing_running(progid):
            raise OSError("operation unavailable")

        for quits, ended, said in [
                (True, [], "quit"),
                (False, [(7, ("WINWORD.EXE", 900, 1234))], "not quit (the application is not answering); ended by its PID")]:
            word, now, err, seen = StubWord(), {"pids": {1: 4}}, io.StringIO(), []
            if not quits:
                word.Quit = lambda: (_ for _ in ()).throw(OSError("the application is not answering"))

            def start(progid, word=word, now=now):
                now["pids"] = {1: 4, 7: 900}
                return word

            with mock.patch.object(com.Job, "hold", side_effect=OSError("[WinError 5] Access is denied")), \
                    self.assertRaises(DriverRefusal) as cm:
                self.office_start(nothing_running, start, now, err,
                                  end_by_pid=lambda pid, known, seen=seen: seen.append((pid, known)))
            self.assertEqual((cm.exception.reason, cm.exception.detail),
                             ("precondition", "Word.Application cannot be started: process 7 cannot be held in a Job: "
                                              "[WinError 5] Access is denied"))
            self.assertEqual((word.quits, word.DisplayAlerts, seen), (1 if quits else 0, -1, ended), said)
            self.assertEqual(err.getvalue(), f"job: process 7 not held: [WinError 5] Access is denied; {said}\n")

    @unittest.skipUnless(sys.platform == "win32", "the adapter's start is Windows' (comtypes)")
    def test_a_new_start_first_ends_the_instance_a_refused_start_left(self):
        # Review quality-3, LF-8: the instance a start refused at its deadline left held ends at the next
        # start, before that start's snapshot: a retry never meets the driver's own half-started instance
        # and takes it for the user's (PowerPoint hands its running instance to CreateObject).
        now, err, user = {"pids": {1: 4, 5: 900}}, io.StringIO(), StubWord()
        left, seen = StubJob(), []
        left.pid = 5

        def end_it():
            left.calls.append("close")
            now["pids"].pop(5)                                 # closed armed, the Job ends process 5

        def attach(progid):
            seen.append(sorted(now["pids"]))                   # what the start's own snapshot sees
            return user

        left.close = end_it
        jobs = [left]
        app = self.office_start(attach, None, now, err, _HELD=jobs)
        self.assertEqual((left.calls, jobs, seen, app.pid), (["close"], [], [[1]], None))

    @unittest.skipUnless(sys.platform == "win32", "the adapter's start is Windows' (comtypes)")
    def test_a_start_with_no_executable_named_says_so(self):
        # Review quality-7: with VK_DRIVER_APPLICATIONS empty (a manifest without `applications`) no start
        # can be identified; driver.log names that cause, not a failed snapshot.
        word, err = StubWord(), io.StringIO()

        def nothing_running(progid):
            raise OSError("operation unavailable")

        app = self.office_start(nothing_running, lambda progid: word, {"pids": {1: 4}}, err, exes="")
        self.assertEqual((app.pid, err.getvalue()),
                         (None, "start: treated as attached: no executable named (VK_DRIVER_APPLICATIONS is empty)\n"))

    @unittest.skipUnless(sys.platform == "win32", "Job objects are Windows'")
    def test_a_job_waits_for_its_process_and_says_when_it_cannot(self):
        # Reviews quality-5 and tests-5: wait() tells a living process from an ended one, takes a negative
        # timeout as no wait at all (never some 49 days), and a wait Windows refuses — the handle closed —
        # raises, instead of reading as "still running".
        base = getattr(sys, "_base_executable", sys.executable)
        sleeper = subprocess.Popen([base, "-I", "-c", "import time; time.sleep(120)"])
        try:
            job = com.Job.hold(sleeper.pid)
            self.assertFalse(job.wait(0.1), "alive")
            waited = []
            negative = threading.Thread(target=lambda: waited.append(job.wait(-0.5)), daemon=True)
            negative.start()
            negative.join(5)
            self.assertEqual(waited, [False], "a negative timeout waits nothing")
            sleeper.kill()
            self.assertTrue(job.wait(10), "ended")
            job.close()
            with self.assertRaises(OSError):
                job.wait(0)
        finally:
            sleeper.kill()
            sleeper.wait(timeout=10)

    @unittest.skipUnless(sys.platform == "win32", "the adapter's start is Windows' (comtypes)")
    def test_a_timed_out_start_windows_will_not_hold_is_ended_by_its_pid(self):
        # Ruling PF-23: a start held past its deadline in CreateObject, its new process identified but
        # refused a Job by Windows, leaves the adapter no reference to quit it with: that one process is
        # ended by its PID — after the check that the PID still names it — and the session refuses
        # `precondition`. When the check fails, nothing is terminated, and the `job: … not held` line says so.
        def nothing_running(progid):
            raise OSError("operation unavailable")

        for why, said in [(None, "ended by its PID"),
                          ("process 7 is no longer the one identified",
                           "not ended: process 7 is no longer the one identified")]:
            hang, now, err, seen, jobs = threading.Event(), {"pids": {1: 4}}, io.StringIO(), [], []

            def appear_then_hang(progid, now=now, hang=hang):
                now["pids"] = {1: 4, 7: 900}
                hang.wait(30)
                raise OSError("never answered")

            def end_by_pid(pid, known, seen=seen, why=why):
                seen.append((pid, known))
                return why

            try:
                with mock.patch.object(com.Job, "hold", side_effect=OSError("[WinError 5] Access is denied")), \
                        self.assertRaises(DriverRefusal) as cm:
                    self.office_start(nothing_running, appear_then_hang, now, err, end_by_pid=end_by_pid,
                                      dialogs=lambda exes: [], _HELD=jobs)
            finally:
                hang.set()
            self.assertEqual((cm.exception.reason, cm.exception.detail),
                             ("precondition", "Word.Application cannot be started: process 7 cannot be held in a Job: "
                                              "[WinError 5] Access is denied"))
            self.assertEqual((seen, jobs), ([(7, ("WINWORD.EXE", 900, 1234))], []), why)
            self.assertEqual(err.getvalue(), "job: process 7 not held: [WinError 5] Access is denied; no reference to "
                                             f"quit it; {said}\n")

    def test_a_process_is_ended_by_its_pid_only_while_it_is_still_the_one_identified(self):
        # Ruling PF-23: ending a process by its PID reads again, through the very handle that ends it —
        # which pins the process — what names it beyond its number: its image, its parent, its creation
        # time; it terminates only when all three are those taken when it was identified, so a PID reused
        # since is never hit. A check that fails or cannot be made terminates nothing, and says why.
        known = ("WINWORD.EXE", 900, 1234)

        class Kernel:
            def __init__(self, opens=True, image=r"C:\Office\WINWORD.EXE", created=1234, reads=True):
                self.opens, self.image, self.created, self.reads = opens, image, created, reads
                self.ended, self.closed = [], []

            def OpenProcess(self, access, inherit, pid):
                return 55 if self.opens else 0

            def QueryFullProcessImageNameW(self, handle, flags, buf, size):
                if self.reads:
                    buf.value = self.image
                return int(self.reads)

            def GetProcessTimes(self, handle, created, exited, kernel, user):
                created._obj.dwLowDateTime, created._obj.dwHighDateTime = self.created & 0xFFFFFFFF, self.created >> 32
                return 1

            def TerminateProcess(self, handle, code):
                self.ended.append(handle)
                return 1

            def CloseHandle(self, handle):
                self.closed.append(handle)

        for kernel, listed, pid_known, why in [
                (Kernel(), [(7, 900, "WINWORD.EXE")], known, None),
                (Kernel(created=1235), [(7, 900, "WINWORD.EXE")], known, "process 7 is no longer the one identified"),
                (Kernel(created=1234 + (1 << 32)), [(7, 900, "WINWORD.EXE")], known,
                 "process 7 is no longer the one identified"),                      # the high word differs
                (Kernel(image=r"C:\Windows\NOTEPAD.EXE"), [(7, 900, "NOTEPAD.EXE")], known,
                 "process 7 is no longer the one identified"),
                (Kernel(), [(7, 33, "WINWORD.EXE")], known, "process 7 is no longer the one identified"),
                (Kernel(), [], known, "process 7 cannot be checked again"),          # gone from the snapshot
                (Kernel(reads=False), [(7, 900, "WINWORD.EXE")], known, "process 7 cannot be checked again"),
                (Kernel(opens=False), [(7, 900, "WINWORD.EXE")], known, "process 7 cannot be opened (error 5)"),
                (Kernel(), [(7, 900, "WINWORD.EXE")], None, "its identity was not taken when it was identified")]:
            with mock.patch.object(sys, "platform", "win32"), mock.patch.object(com, "_kernel32", lambda: kernel), \
                    mock.patch.object(com, "processes", lambda: listed), \
                    mock.patch.object(com.ctypes, "get_last_error", lambda: 5, create=True):
                self.assertEqual(com.end_by_pid(7, pid_known), why, (kernel.__dict__, listed))
            self.assertEqual(kernel.ended, [55] if why is None else [], why)
            self.assertEqual(kernel.closed, [55] if kernel.opens and pid_known else [], "the handle closed once")
        with mock.patch.object(sys, "platform", "win32"), mock.patch.object(com, "_kernel32", lambda: Kernel()), \
                mock.patch.object(com, "processes", lambda: [(7, 900, "WINWORD.EXE")]):
            self.assertEqual(com.identity(7), known, "the identity taken when the process is identified")

    @unittest.skipUnless(sys.platform == "win32", "process identities are Windows'")
    def test_a_sleeper_is_ended_by_its_pid_and_never_once_its_identity_changed(self):
        # Ruling PF-23 on Windows, on a process of the test's own: its identity read through a real handle
        # and a real snapshot; a creation time that differs terminates nothing, the identity taken when
        # the process was identified ends it.
        base = getattr(sys, "_base_executable", sys.executable)
        sleeper = subprocess.Popen([base, "-I", "-c", "import time; time.sleep(120)"])
        try:
            known = com.identity(sleeper.pid)
            self.assertEqual(known[:2], (Path(base).name.upper(), os.getpid()), known)
            self.assertEqual(com.end_by_pid(sleeper.pid, (*known[:2], known[2] + 1)),
                             f"process {sleeper.pid} is no longer the one identified")
            time.sleep(0.5)
            self.assertIsNone(sleeper.poll(), "another creation time: nothing terminated")
            self.assertIsNone(com.end_by_pid(sleeper.pid, known))
            self.assertEqual(sleeper.wait(timeout=10), 1, "ended by its PID (TerminateProcess, exit code 1)")
        finally:
            sleeper.kill()
            sleeper.wait(timeout=10)

    def abandoned_start(self, attach, create, now, err, dialogs=lambda exes: []):
        """com._start with stubs only (ruling PF-24): `attach` and `create` for comtypes' calls, the
        snapshots read from `now["pids"]`, the DcomLaunch service's process 900, a 0.3 s deadline, every
        process it identifies given the identity ("WINWORD.EXE", 900, 1234), and the Jobs it holds kept."""
        held, jobs = [], []

        def hold(pid):
            held.append(pid)
            return StubJob()

        with mock.patch.dict(os.environ, {"VK_DRIVER_APPLICATIONS": "WINWORD.EXE"}), \
                mock.patch.object(com, "pids_of", lambda exes: dict(now["pids"]) if exes else None), \
                mock.patch.object(com, "dcom_launcher_pid", lambda: 900), \
                mock.patch.object(com, "identity", lambda pid: ("WINWORD.EXE", 900, 1234)), \
                mock.patch.object(com, "dialogs", dialogs), mock.patch.object(com, "close_window", lambda hwnd: None), \
                mock.patch.object(com, "_hold", hold), mock.patch.object(com, "_HELD", jobs), \
                contextlib.redirect_stderr(err), self.assertRaises(DriverRefusal) as cm:
            com._start("Word.Application", attach, create, (OSError,), 0.3)
        return cm.exception, held, jobs

    def test_an_attach_that_fails_once_the_start_is_given_up_starts_nothing(self):
        # Ruling PF-24 (re-review new-office-safety-1, cases B and C): the start's branch is latched once,
        # under the lock the guard marks the start abandoned with; the refusal uses that value. An attach
        # that fails while the guard looks for boxes (B), or after the refusal (C), finds the start
        # abandoned: CreateObject is never called, nothing is held — not even a DcomLaunch child another
        # client started meanwhile.
        for case in ("B", "C"):
            release, created, done, now, err = (threading.Event(), threading.Event(), threading.Event(),
                                                {"pids": {1: 4}}, io.StringIO())

            def attach(progid, release=release, done=done, now=now):
                try:
                    release.wait(30)
                    now["pids"] = {1: 4, 7: 900}               # another client's Word, meanwhile
                    raise OSError("the user's Word went away")
                finally:
                    done.set()

            def create(progid, created=created):
                created.set()
                return StubWord()

            def boxes(exes, release=release, created=created):
                release.set()                                   # B: the attach fails during the scan
                created.wait(1)
                return [(70, 7)]

            try:
                refusal, held, jobs = self.abandoned_start(attach, create, now, err,
                                                           **({"dialogs": boxes} if case == "B" else {}))
            finally:
                release.set()                                   # C: the attach fails after the refusal
            done.wait(5)
            time.sleep(0.2)
            self.assertFalse(created.is_set(), f"{case}: an abandoned start called CreateObject")
            self.assertEqual((held, jobs, err.getvalue()), ([], [], ""), case)
            self.assertEqual(refusal.reason, "dialog" if case == "B" else "timeout", case)

    def test_a_create_in_flight_when_the_start_is_given_up_is_quit_when_it_returns(self):
        # Ruling PF-24: a CreateObject already called when the guard gives up is the driver's start: its
        # process, identified, is held; when the call returns after all, the worker quits what it got back
        # (no document is in it), and failing that ends it by its PID with PF-23's check; one line says so.
        for quits, said in ((True, "quit"),
                            (False, "not quit (the application is not answering); ended by its PID")):
            release, now, err, word, seen = threading.Event(), {"pids": {1: 4}}, io.StringIO(), StubWord(), []
            if not quits:
                word.Quit = lambda: (_ for _ in ()).throw(OSError("the application is not answering"))

            def nothing(progid):
                raise OSError("operation unavailable")

            def create(progid, release=release, now=now, word=word):
                now["pids"] = {1: 4, 7: 900}
                release.wait(30)
                return word

            try:
                with mock.patch.object(com, "end_by_pid", lambda pid, known, seen=seen: seen.append((pid, known))), \
                        contextlib.redirect_stderr(err):
                    refusal, held, jobs = self.abandoned_start(nothing, create, now, err)
                    release.set()
                    deadline = time.monotonic() + 5
                    while "start: abandoned" not in err.getvalue() and time.monotonic() < deadline:
                        time.sleep(0.05)
            finally:
                release.set()
            self.assertEqual((refusal.reason, held, len(jobs)), ("timeout", [7], 1), said)
            self.assertEqual((word.quits, seen), (1, []) if quits else (0, [(7, ("WINWORD.EXE", 900, 1234))]))
            self.assertEqual(err.getvalue(), f"start: abandoned at its deadline; process 7 {said}\n")

    def test_a_late_return_is_quit_only_when_positively_identified(self):
        # Ruling PF-26 (re-review N1, N2): what a CreateObject returns after the start was given up is quit
        # only when its process is positively identified — latched at the give-up, or found now, from a
        # fresh snapshot (one new process of the executable, child of the DcomLaunch service). A
        # PowerPoint the user runs, handed back by CreateObject (no new process), is released untouched
        # and said attached, never quit (D-03). An identified one whose Quit fails is ended by its PID.
        for appears, fails, quits, ended, said in [
                (None, False, 0, [], "treated as attached: 0 new processes of the application"),   # the user's
                ({7: 900}, False, 1, [], "process 7 quit"),                    # the driver's, spawned late
                ({7: 900}, True, 0, [7], "process 7 not quit (the application is not answering); ended by its PID"),
                ({7: 33}, False, 0, [], "treated as attached: process 7 was not started by the DCOM launcher")]:
            release, now, err, word, seen = threading.Event(), {"pids": {55: 33}}, io.StringIO(), StubWord(), []
            if fails:
                word.Quit = lambda: (_ for _ in ()).throw(OSError("the application is not answering"))

            def nothing(progid):
                raise OSError("operation unavailable")

            def create(progid, release=release, now=now, word=word, appears=appears):
                release.wait(30)
                if appears:
                    now["pids"] = {55: 33, **appears}
                return word

            try:
                with mock.patch.dict(os.environ, {"VK_DRIVER_APPLICATIONS": "POWERPNT.EXE"}), \
                        mock.patch.object(com, "pids_of", lambda exes, now=now: dict(now["pids"]) if exes else None), \
                        mock.patch.object(com, "dcom_launcher_pid", lambda: 900), \
                        mock.patch.object(com, "identity", lambda pid: ("POWERPNT.EXE", 900, 1234)), \
                        mock.patch.object(com, "dialogs", lambda exes: []), \
                        mock.patch.object(com, "_hold", lambda pid: StubJob()), mock.patch.object(com, "_HELD", []), \
                        mock.patch.object(com, "end_by_pid", lambda pid, known, seen=seen: seen.append(pid)), \
                        contextlib.redirect_stderr(err):
                    with self.assertRaises(DriverRefusal):
                        com._start("PowerPoint.Application", nothing, create, (OSError,), 0.3)
                    release.set()
                    deadline = time.monotonic() + 5
                    while "start: abandoned" not in err.getvalue() and time.monotonic() < deadline:
                        time.sleep(0.05)
            finally:
                release.set()
            self.assertEqual((word.quits, seen), (quits, ended), said)
            self.assertIn(f"start: abandoned at its deadline; {said}\n", err.getvalue())
