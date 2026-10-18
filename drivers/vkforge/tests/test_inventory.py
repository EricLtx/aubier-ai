import json
import sys
import tempfile
import unittest
from pathlib import Path

from vkforge import __main__ as entry
from vkforge import canon, inventory
from vkforge.errors import Refusal

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def reorder(x):
    """The same content with every object's keys in reverse order."""
    if isinstance(x, dict):
        return {k: reorder(x[k]) for k in reversed(list(x))}
    if isinstance(x, list):
        return [reorder(v) for v in x]
    return x


class InventoryTest(unittest.TestCase):
    def test_the_recorded_word_catalogue_has_the_members_the_ontology_needs(self):
        cat = inventory.catalogue_from_fixture("word", FIXTURES / "catalogue-word.json")
        names = {t["name"] for t in cat["types"]}
        for t in ("Document", "Documents", "Paragraph", "Range", "Font", "Section", "HeaderFooter", "Style"):
            self.assertIn(t, names)
        self.assertEqual(cat["enums"]["WdBuiltinStyle"]["wdStyleHeading1"], -2)
        self.assertEqual(cat["enums"]["WdSaveFormat"]["wdFormatPDF"], 17)
        doc = next(t for t in cat["types"] if t["name"] == "Document")
        self.assertIn("SaveAs2", {m["name"] for m in doc["members"]})
        self.assertRegex(cat["typelib_sha256"], r"^sha256:[0-9a-f]{64}$")   # L-P22: the Office it was recorded on

    def test_the_recorded_powerpoint_catalogue_has_placeholders_and_export(self):
        cat = inventory.catalogue_from_fixture("powerpoint", FIXTURES / "catalogue-powerpoint.json")
        self.assertIn("ppPlaceholderTitle", cat["enums"]["PpPlaceholderType"])
        self.assertIn("ppSaveAsPDF", cat["enums"]["PpSaveAsFileType"])

    def test_the_catalogue_hash_is_stable_across_key_order(self):
        cat = inventory.catalogue_from_fixture("word", FIXTURES / "catalogue-word.json")
        shuffled = reorder(cat)
        self.assertNotEqual(json.dumps(shuffled), json.dumps(cat), "the key order did change")
        self.assertEqual(inventory.catalogue_sha256(cat), inventory.catalogue_sha256(shuffled))
        out = Path(tempfile.mkdtemp())
        path = inventory.write_catalogue(shuffled, out)
        self.assertEqual(path, out / "catalogue.json")
        self.assertEqual(canon.sha256_file(path), inventory.catalogue_sha256(cat))

    def test_restrict_keeps_the_named_types_and_every_enum(self):
        cat = inventory.catalogue_from_fixture("word", FIXTURES / "catalogue-word.json")
        small = inventory.restrict(cat, ["Document", "Font"])
        self.assertEqual([t["name"] for t in small["types"]], ["Document", "Font"])
        self.assertEqual(small["enums"], cat["enums"])
        self.assertEqual((small["typelib"], small["typelib_sha256"]), (cat["typelib"], cat["typelib_sha256"]))

    @unittest.skipUnless(sys.platform == "win32" and inventory.office_present("word"), "Word not installed")
    def test_the_live_type_library_matches_the_fixture_on_the_members_the_ontology_needs(self):
        path = inventory.typelib_path(inventory.APPS["word"]["typelib"])
        live = inventory.catalogue_from_typelib("word", path)
        fixture = inventory.catalogue_from_fixture("word", FIXTURES / "catalogue-word.json")
        self.assertEqual(live["enums"]["WdBuiltinStyle"], fixture["enums"]["WdBuiltinStyle"])
        self.assertEqual(live["typelib_sha256"], canon.sha256_file(path), "the file's bytes (L-P22)")

    def test_an_unknown_app_is_refused_before_any_file_is_read(self):
        with self.assertRaises(Refusal) as cm:
            inventory.catalogue_from_fixture("excel", FIXTURES / "absent.json")
        self.assertEqual(str(cm.exception), "unknown app excel; remedy: --app word or --app powerpoint")

    def test_the_cli_writes_catalogue_json_from_a_fixture(self):
        out = Path(tempfile.mkdtemp()) / "out"
        code = entry.main(["inventory", "--app", "word", "--out", str(out),
                           "--fixture", str(FIXTURES / "catalogue-word.json"), "--only-types", "Document,Font"])
        self.assertEqual(code, 0)
        written = json.loads((out / "catalogue.json").read_text(encoding="utf-8"))
        self.assertEqual([t["name"] for t in written["types"]], ["Document", "Font"])
