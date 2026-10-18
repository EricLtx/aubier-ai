import contextlib
import io
import json
import os
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
        app = com.Application(self.binding, connect=lambda: (word, True))
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
        # L-P47: attached to the user's instance, DisplayAlerts is read, silenced, and put back at the end;
        # L-P46: only an instance the adapter started is quit, and never one still holding a document.
        user = StubWord()
        attached = com.Application(self.binding, connect=lambda: (user, False))
        self.assertEqual(user.DisplayAlerts, 0, "alerts_off while the driver works")
        attached.quit()
        attached.quit()
        self.assertEqual((user.DisplayAlerts, user.quits, attached.app), (-1, 0, None))
        ours = StubWord()
        started = com.Application(self.binding, connect=lambda: (ours, True))
        started.quit()
        self.assertEqual((ours.quits, started.app), (1, None))
        busy = StubWord()
        holding = com.Application(self.binding, connect=lambda: (busy, True))
        holding.open("C:/stub/left.docx")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            holding.quit()
        self.assertEqual((busy.quits, len(busy.Documents.items)), (0, 1), "nothing closed, nothing quit")
        self.assertEqual(err.getvalue(), "quit: Word.Application still holds documents; left running\n")

    def test_the_cleanup_close_is_bounded_and_the_first_refusal_wins(self):
        # After a step hangs, the driver's `finally` close meets the same wedged application: it gets the
        # short cleanup deadline, its refusal goes to stderr, and the host hears the step's refusal.
        word = StubWord()
        app = com.Application(self.binding, connect=lambda: (word, True))
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
