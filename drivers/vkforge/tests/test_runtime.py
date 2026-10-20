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
from vkforge.tests import ooxml_fixtures
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
        self.word, self.FullName, self.ReadOnly = word, path, False
        self.Styles = Styles()
        self.Paragraphs = Collection(Paragraph(self) for _ in range(4))
        self.Sections = Collection([types.SimpleNamespace(Footers=Bands(), Headers=Bands())])
        self.saved, self.exported, self.closed = 0, [], None

    def Save(self):
        self.saved += 1

    def ExportAsFixedFormat(self, path, fmt):
        self.exported.append((path, fmt))
        Path(path).write_bytes(b"%PDF-1.7 stub")

    def Close(self, **kw):
        self.closed = kw
        self.word.Documents.items.remove(self)
        getattr(self.word, "log", []).append(f"Close {Path(self.FullName).name}")


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


class SilentWord(StubWord):
    """A Word whose DisplayAlerts cannot be read: a session that cannot begin (ruling LF-8)."""

    def __init__(self):
        self.Documents = StubDocuments(self)
        self.missing, self.quits = set(), 0

    @property
    def DisplayAlerts(self):
        raise OSError("the application is not answering")


class OrderedWord(StubWord):
    """A Word started by automation, as the end of a session finds it (founder decision D-35, rulings
    PF-2 and PF-12): hidden until the hand-over. It writes into `log`, in order, what that end asks of
    it: each document closed (StubDocument.Close), each property set, each read of Visible, each Quit.
    Set true, Visible reads back `shows`: True as comtypes gives Word's VARIANT_BOOL, -1 as it gives
    PowerPoint's msoTrue, False or 0 for a window that will not show. The property `refuses` names
    cannot be set; UserControl reads False and cannot be set at all, as the pinned type library has
    Word's Application.UserControl (a get accessor only)."""

    def __init__(self, log: list, shows=True):
        super().__init__()
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

    @unittest.skipUnless(sys.platform == "win32" and inventory.office_present("word"), "Word not installed")
    @mock.patch.dict(os.environ, {"VK_DRIVER_APPLICATIONS": "WINWORD.EXE"})
    def test_the_adapter_applies_the_house_style_on_the_real_word(self):
        doc = ooxml_fixtures.docx(Path(tempfile.mkdtemp()) / "c.docx", title_style="Normal", body_font=("Calibri", 12))
        guid = inventory.APPS["word"]["typelib"]
        self.assertEqual(com.live_typelib_sha256(guid), canon.sha256_file(inventory.typelib_path(guid)),
                         "the probe reads the file inventory hashed (L-P22)")
        app = com.Application(self.binding)
        try:
            checks, wrong = self.house_style(app, doc)
        finally:
            app.quit()
        self.assertEqual((checks, wrong), ([True, True, True, True], False))
        obs = mine.mine_file(doc)["obs"]
        self.assertIn({"kind": "paragraph_style", "where": "first", "value": {"builtin": "Heading1"}}, obs)

    @unittest.skipUnless(sys.platform == "win32" and inventory.office_present("word"), "Word not installed")
    @mock.patch.dict(os.environ, {"VK_DRIVER_APPLICATIONS": "WINWORD.EXE"})
    def test_a_document_already_open_in_the_users_word_is_refused_and_left_open(self):
        # The plan's precondition "not open elsewhere", in the instance the adapter attaches to (spike
        # verdict 3): there Documents.Open hands back the user's own document, unsaved edits included,
        # which a save would write and a close would shut. Refused, and the user's document left as it was.
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
            app.quit()
        self.assertEqual(doc.read_bytes(), before)

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
        # process what is left of the clean-end window, then closes the Job, which ends whatever is
        # left; an attached instance has no Job at all.
        late = "quit: process 4242 still running at the end of the clean-end window; its Job ends it\n"
        for exits, said in ((True, ""), (False, late)):
            word, job = StubWord(), StubJob(exits)
            app = com.Application(self.binding, connect=lambda: (word, com.Owned(4242, job)))
            self.assertEqual(app.pid, 4242)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                app.quit()
            self.assertEqual((word.quits, job.calls, err.getvalue()), (1, ["wait", "close"], said))
            self.assertTrue(0 < job.waited <= com.QUIT_WAIT_S, job.waited)
            self.assertIsNone(app.pid, "the session let go of what it held")
        self.assertEqual(com.QUIT_WAIT_S, 3.0, "under the 5 s a host (L-P49) and qualify give a driver")
        user = StubWord()
        attached = com.Application(self.binding, connect=lambda: (user, None))
        attached.quit()
        self.assertEqual((user.quits, user.DisplayAlerts, attached.pid), (0, -1, None))

    def test_the_instance_survives_only_a_clean_end_inside_the_window(self):
        # Founder decision D-35, bound (1): what may keep the started instance runs inside the clean-end
        # window, QUIT_WAIT_S, under the host's 5 s grace (L-P49) — never on a call's 30 s. Past it (here
        # a listing that hangs) there is no disarm, and the Job, closed, takes the instance. An unclean
        # death never reaches this code: the Job takes the instance by itself
        # (test_a_held_process_dies_with_its_holder_and_a_disarmed_one_lives).
        word, job, hang = StubWord(), StubJob(), threading.Event()
        app = com.Application(self.binding, connect=lambda: (word, com.Owned(4242, job)))
        word.Documents.items.append(Hung(hang))
        err, began = io.StringIO(), time.monotonic()
        try:
            with mock.patch.object(com, "QUIT_WAIT_S", 0.5), contextlib.redirect_stderr(err):
                app.quit()
        finally:
            hang.set()
        self.assertLess(time.monotonic() - began, 2.0, "the window bounds the end, not a call's deadline")
        self.assertEqual((job.calls, word.quits), (["close"], 0), "no disarm, no Quit: the Job takes it")
        self.assertIn("quit: process 4242 not handed over (inventory: no answer within", err.getvalue())

    def test_the_drivers_own_documents_are_closed_first_and_never_keep_the_instance(self):
        # Founder decision D-35, bound (2): the driver first closes, without saving, every document it
        # opened and still holds (its close after the action failed); holding nothing else, the instance
        # is quit. A document of its own that will not close does not keep the instance: no Quit, no
        # disarm, and the Job, closed, takes it.
        word, job = StubWord(), StubJob()
        app = com.Application(self.binding, connect=lambda: (word, com.Owned(4242, job)))
        left = app.open("C:/stub/left.docx")
        app.quit()
        self.assertEqual((left.closed, len(word.Documents.items), word.quits), ({"SaveChanges": False}, 0, 1))
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
        # msoTrue), its own Job alone disarmed, DisplayAlerts put back, every reference released — and
        # stays, the user's document in it, the driver's closed. UserControl is never touched: Word's
        # has no setter, and neither has OrderedWord's. A Visible that cannot be set, or that reads back
        # false: no disarm, nothing put back, and the Job, closed, takes the instance. Another started
        # instance, holding nothing foreign, is quit and its Job closed armed.
        self.assertEqual(com.COM_TRUE, -1, "VARIANT_TRUE, and PowerPoint's msoTrue")
        for refuses, shows, why in [(None, True, None),                     # Word's Visible, read back
                                    (None, com.COM_TRUE, None),             # PowerPoint's, read back
                                    ("Visible", True, "Visible cannot be set"),
                                    (None, False, "hand-over: Visible reads back False"),
                                    (None, 0, "hand-over: Visible reads back 0")]:      # msoFalse
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
        # Rulings PF-2 and PF-12: inside QUIT_WAIT_S and before any other call — the driver's own
        # documents closed, the instance's listed, Visible set true and read back, then the disarm;
        # DisplayAlerts and the release come after it, best effort. A failure before the disarm (here
        # Visible refused, or read back false): no disarm, nothing put back, and the Job, closed, takes
        # the instance.
        for refuses, shows, want in [
                (None, True, ["Close own.docx", f"Visible {com.COM_TRUE}", "Visible? True", "disarm",
                              "DisplayAlerts -1", "close"]),
                ("Visible", True, ["Close own.docx", "close"]),
                (None, False, ["Close own.docx", f"Visible {com.COM_TRUE}", "Visible? False", "close"])]:
            log = []
            word, job = OrderedWord(log, shows), StubJob(log=log)
            app = com.Application(self.binding, connect=lambda: (word, com.Owned(4242, job)))
            app.open("C:/stub/own.docx")                              # its close after the action failed
            word.Documents.items.append(StubDocument(word, "C:/stub/the-users.docx"))
            word.refuses = refuses
            log.clear()                                               # the session's start is not the subject
            with contextlib.redirect_stderr(io.StringIO()):
                app.quit()
            self.assertEqual(log, want, (refuses, shows))

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

    def test_an_instance_no_job_holds_is_never_said_to_end_through_one(self):
        # Task 3 self-review, D-35 (5): when Windows refused the Job (`job: process … not held`, D-04), the
        # end's line says no Job ends the instance, or none was disarmed — never the contrary.
        word = StubWord()
        app = com.Application(self.binding, connect=lambda: (word, com.Owned(4242)))
        stuck = app.open("C:/stub/stuck.docx")
        stuck.Close = lambda **kw: (_ for _ in ()).throw(OSError("the document is in use"))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            app.quit()
        self.assertEqual((word.quits, err.getvalue()),
                         (0, "quit: process 4242 not handed over (the document is in use); no Job ends it\n"))
        word = StubWord()
        app = com.Application(self.binding, connect=lambda: (word, com.Owned(4242)))
        app.open("C:/stub/own.docx")
        word.Documents.items.append(StubDocument(word, "C:/stub/the-users.docx"))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            app.quit()
        self.assertEqual((word.quits, word.DisplayAlerts, err.getvalue()),
                         (0, -1, "quit: process 4242 handed to the user, 1 foreign document(s) open in it; "
                                 "no Job to disarm\n"))
