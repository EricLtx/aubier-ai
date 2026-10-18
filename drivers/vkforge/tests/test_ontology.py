import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from vkforge import __main__ as entry
from vkforge import canon, inventory, ontology
from vkforge.errors import Refusal

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class OntologyTest(unittest.TestCase):
    def setUp(self):
        self.ont = ontology.load()
        self.word = inventory.catalogue_from_fixture("word", FIXTURES / "catalogue-word.json")
        self.ppt = inventory.catalogue_from_fixture("powerpoint", FIXTURES / "catalogue-powerpoint.json")

    def test_every_verb_of_the_house_style_task_binds_on_word(self):
        b = ontology.bind(self.ont, self.word)
        status = {v["verb"]: v["status"] for v in b["verbs"]}
        for verb in ("open", "select_paragraph", "apply_paragraph_style", "set_font_run", "set_footer", "save", "export",
                     "verify_paragraph_style", "verify_font_run", "verify_footer", "verify_pdf"):
            self.assertEqual(status[verb], "bound", verb)
        self.assertEqual(status["set_slide_layout"], "unbound")
        self.assertEqual((b["app"], b["catalogue_sha256"], b["ontology_sha256"]),
                         ("word", inventory.catalogue_sha256(self.word), ontology.ontology_sha256(self.ont)))
        self.assertEqual((b["typelib"], b["typelib_sha256"]), (self.word["typelib"], self.word["typelib_sha256"]))  # L-P22
        self.assertEqual(b["application"], self.ont["applications"]["word"])                                       # L-P31

    def test_the_same_ontology_binds_on_powerpoint_without_code(self):
        b = ontology.bind(self.ont, self.ppt)
        status = {v["verb"]: v["status"] for v in b["verbs"]}
        for verb in ("open", "set_font_run", "set_footer", "save", "export", "set_slide_layout", "set_placeholder_text",
                     "verify_font_run", "verify_footer", "verify_pdf"):
            self.assertIn(status[verb], ("bound", "partial"), verb)
        self.assertEqual(status["apply_paragraph_style"], "partial")
        self.assertEqual(status["apply_numbering"], "unbound")

    def test_a_pattern_that_matches_nothing_is_unbound_not_an_exception(self):
        ont = ontology.load()
        ont["verbs"].append({"verb": "levitate", "args": {}, "pre": "document_open", "post": "saved", "reversible": False,
                             "projection": {"word": {"member": "Document.Levitate", "constants": None}}})
        b = ontology.bind(ont, self.word)
        v = next(x for x in b["verbs"] if x["verb"] == "levitate")
        self.assertEqual((v["status"], v["member"], v["constants"]), ("unbound", None, None))
        self.assertIn("Document.Levitate", v["why"])

    def test_a_chain_follows_return_types_and_a_variant_step_binds_partial(self):
        ont = ontology.load()
        for name, member, constants in (("style_name", "Paragraph.Style.NameLocal", None),   # Style returns VARIANT
                                        ("name_length", "Document.Name.Length", None),       # Name returns BSTR
                                        ("colour", "Paragraph.Range.Font", "WdColour")):     # no such enum
            ont["verbs"].append({"verb": name, "args": {}, "pre": "document_open", "post": "none", "reversible": True,
                                 "projection": {"word": {"member": member, "constants": constants}}})
        b = {v["verb"]: v for v in ontology.bind(ont, self.word)["verbs"]}
        self.assertEqual((b["style_name"]["status"], b["style_name"]["member"]), ("partial", "Paragraph.Style.NameLocal"))
        self.assertIn("Paragraph.Style returns VARIANT", b["style_name"]["why"])
        self.assertEqual(b["name_length"]["status"], "unbound")
        self.assertIn("Document.Name returns BSTR", b["name_length"]["why"])
        self.assertEqual((b["colour"]["status"], b["colour"]["why"]),
                         ("unbound", "constants WdColour matched no enum of the catalogue"))
        self.assertEqual(b["set_font_run"]["why"], None, "a bound verb says no why")

    def test_constants_are_resolved_to_numbers_by_their_builtin_names(self):
        b = ontology.bind(self.ont, self.word)
        v = next(x for x in b["verbs"] if x["verb"] == "apply_paragraph_style")
        styles = v["constants"]["WdBuiltinStyle"]
        self.assertEqual(styles["wdStyleHeading1"], -2)
        self.assertTrue(all(k.startswith("wdStyle") and isinstance(n, int) for k, n in styles.items()), "built-in anchors only")

    def test_the_ontology_file_validates_its_own_shape(self):
        bad = ontology.load()
        del bad["verbs"][0]["post"]
        with self.assertRaises(Refusal) as cm:
            ontology.validate(bad)
        self.assertIn(bad["verbs"][0]["verb"], str(cm.exception))
        bad = ontology.load()
        bad["verbs"][0]["args"] = {"x": "Colour"}
        with self.assertRaises(Refusal):
            ontology.validate(bad)

    def test_the_ontology_file_is_stored_canonical_with_32_verbs_each_reversible_or_not(self):
        self.assertEqual(ontology.ONTOLOGY.read_bytes(), canon.dumps(self.ont), "keep the file in canon.dumps form")
        names = [v["verb"] for v in self.ont["verbs"]]
        self.assertEqual(len(names), 32)
        self.assertEqual(len(set(names)), 32)
        self.assertTrue(all(isinstance(v["reversible"], bool) for v in self.ont["verbs"]))
        self.assertTrue(ontology.is_selector("index:3") and ontology.is_selector("title"))
        self.assertFalse(ontology.is_selector("index:0") or ontology.is_selector("Titre"))

    def test_the_applications_block_gives_the_adapter_what_it_must_not_name(self):
        # L-P31: runtime/com.py names no application; each app's entry reaches it as binding["application"].
        self.assertEqual(self.ont["applications"], {
            "powerpoint": {"alerts_off": 1, "close": {}, "collection": "Presentations",
                           "open": {"WithWindow": False}, "progid": "PowerPoint.Application"},
            "word": {"alerts_off": 0, "close": {"SaveChanges": False}, "collection": "Documents",
                     "open": {"AddToRecentFiles": False, "ReadOnly": False, "Visible": False},
                     "progid": "Word.Application"}})
        self.assertEqual(sorted(self.ont["applications"]), sorted(inventory.APPS))
        self.assertEqual(ontology.bind(self.ont, self.ppt)["application"], self.ont["applications"]["powerpoint"])
        bad = ontology.load()
        del bad["applications"]["powerpoint"]["collection"]
        with self.assertRaises(Refusal) as cm:
            ontology.validate(bad)
        self.assertIn("powerpoint", str(cm.exception))

    def test_the_committed_word_binding_is_what_bind_produces(self):
        # Ruling L-P21: written on the first run, compared on every later one; Tasks 6–8 read it.
        path = FIXTURES / "binding-word.json"
        produced = canon.dumps(ontology.bind(self.ont, self.word))
        if not path.exists():
            path.write_bytes(produced)
        self.assertEqual(path.read_bytes(), produced, "binding-word.json is stale: delete it, run this test, commit it")

    def test_the_cli_writes_the_binding_and_lists_the_unbound_verbs(self):
        out = Path(tempfile.mkdtemp())
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            self.assertEqual(entry.main(["bind", "--catalogue", str(FIXTURES / "catalogue-word.json"), "--out", str(out)]), 0)
        self.assertTrue((out / "binding-word.json").is_file())
        self.assertIn("set_slide_layout", printed.getvalue())

    def test_a_catalogue_without_its_fingerprint_or_an_unreadable_file_is_a_refusal_never_a_traceback(self):
        # L-P22: no typelib_sha256, no binding (the generated driver's probe would have nothing to compare).
        cat = {k: v for k, v in self.word.items() if k != "typelib_sha256"}
        with self.assertRaises(Refusal) as cm:
            ontology.bind(self.ont, cat)
        self.assertEqual(str(cm.exception), "catalogue of word has no typelib_sha256; "
                                            "remedy: record it again with vkforge inventory")
        # L-P15/L-P39: a file the stage cannot read is one refusal line, exit 2, nothing written.
        d = Path(tempfile.mkdtemp())
        (d / "bad.json").write_bytes(b"{not json")
        (d / "compact.json").write_text(json.dumps(self.ont), encoding="utf-8")   # valid, but its hash is not canon's
        word = str(FIXTURES / "catalogue-word.json")
        for flags in (["--catalogue", str(d / "absent.json")], ["--catalogue", str(d / "bad.json")],
                      ["--catalogue", word, "--ontology", str(d / "bad.json")],
                      ["--catalogue", word, "--ontology", str(d / "compact.json")]):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(entry.main(["bind", *flags, "--out", str(d / "out")]), 2, flags)
            self.assertRegex(err.getvalue(), r"^vkforge bind: [^\n]+; remedy: [^\n]+\n$")
        self.assertFalse((d / "out").exists(), "nothing is written on a refusal")


if __name__ == "__main__":
    unittest.main()
