import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vkdriver.protocol import DriverRefusal
from vkforge import canon, inventory, mine, plan
from vkforge.runtime import com, fake
from vkforge.runtime.fake import FakeApplication
from vkforge.tests import ooxml_fixtures
from vkforge.tests.ooxml_fixtures import FOOTER

FIXTURES = Path(__file__).resolve().parent / "fixtures"
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
