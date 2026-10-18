import contextlib
import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from vkforge import __main__ as entry
from vkforge import canon, mine
from vkforge.errors import Refusal
from vkforge.tests import ooxml_fixtures
from vkforge.tests.ooxml_fixtures import FOOTER


class MineTest(unittest.TestCase):
    def corpus(self, n=10, **kw) -> Path:
        d = Path(tempfile.mkdtemp())
        for i in range(n):
            ooxml_fixtures.docx(d / f"c{i}.docx", **kw)
        return d

    def test_ten_contracts_yield_the_house_invariants_at_full_support(self):
        d = self.corpus(10, title_style="Heading1", body_font=("Garamond", 11), footer=FOOTER, pdf=True)
        inv, vault = mine.mine_corpus(d)
        by_id = {i["id"]: i for i in inv["invariants"]}
        self.assertEqual(sorted(by_id), ["body_font", "export_format", "footer_text", "title_style"])
        self.assertEqual(by_id["title_style"], {"id": "title_style", "kind": "paragraph_style", "where": "first",
                                                "value": {"builtin": "Heading1"}, "support": 1.0})
        self.assertEqual(by_id["body_font"], {"id": "body_font", "kind": "font_run", "where": "body",
                                              "value": {"name": "Garamond", "size": 11}, "support": 1.0})
        self.assertEqual(by_id["export_format"]["value"], {"format": "pdf"})
        self.assertTrue(mine.redact(FOOTER).startswith("<redacted:sha256:"))
        self.assertEqual(by_id["footer_text"]["value"], mine.redact(FOOTER))
        self.assertNotIn("where", by_id["footer_text"])
        self.assertEqual(vault, {mine.redact(FOOTER): FOOTER})
        self.assertEqual((inv["corpus"]["files"], inv["corpus"]["skipped"], inv["corpus"]["kind"]), (10, 0, "docx"))
        self.assertEqual((inv["threshold"], inv["ambiguities"]), (0.8, []))

    def test_support_below_threshold_is_an_ambiguity_with_its_candidates(self):
        d = Path(tempfile.mkdtemp())
        for i in range(10):
            ooxml_fixtures.docx(d / f"c{i}.docx", body_font=("Garamond", 11) if i < 7 else ("Calibri", 12))
        inv, _ = mine.mine_corpus(d, threshold=0.8)
        self.assertNotIn("body_font", {i["id"] for i in inv["invariants"]})
        self.assertEqual(inv["ambiguities"], [{"invariant": "body_font", "kind": "font_run", "candidates": [
            {"value": {"name": "Garamond", "size": 11}, "support": 0.7},
            {"value": {"name": "Calibri", "size": 12}, "support": 0.3}]}])

    def test_no_byte_of_the_text_reaches_the_invariants(self):
        d = Path(tempfile.mkdtemp())
        secrets = ["Jean Dupont", "06 12 34 56 78", "jean.dupont@example.org", "1 rue des Lilas"]
        for i in range(6):
            ooxml_fixtures.docx(d / f"c{i}.docx", footer="Pied " + secrets[0], personal=" ".join(secrets))
        out = Path(tempfile.mkdtemp())
        inv, vault = mine.mine_corpus(d)
        mine.write_invariants(inv, vault, out)
        blob = (out / "invariants.json").read_bytes()
        for s in secrets:
            self.assertNotIn(s.encode("utf-8"), blob)
        self.assertEqual(blob, canon.dumps(inv))
        self.assertEqual(json.loads((out / ".vault.json").read_text(encoding="utf-8")),
                         {mine.redact("Pied Jean Dupont"): "Pied Jean Dupont"})
        if os.name == "posix":
            self.assertEqual((out / ".vault.json").stat().st_mode & 0o777, 0o600)

    def test_a_mixed_folder_skips_what_is_not_ooxml_and_never_opens_office(self):
        d = Path(tempfile.mkdtemp())
        for i in range(5):
            ooxml_fixtures.docx(d / f"c{i}.docx")
        (d / "old.doc").write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 64)     # binary Word
        (d / "locked.docx").write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 64)  # encrypted OOXML is an OLE file
        ooxml_fixtures.docx(d / "macro.docm")                                                # macro-enabled: never mined
        (d / "~$c1.docx").write_bytes(b"\0" * 10)                                           # Word's lock file
        (d / "notes.txt").write_text("x")
        inv, _ = mine.mine_corpus(d)
        self.assertEqual((inv["corpus"]["files"], inv["corpus"]["skipped"]), (5, 5))
        for module in ("mine.py", "ooxml.py"):
            text = (Path(mine.__file__).parent / module).read_text(encoding="utf-8")
            self.assertNotIn("comtypes", text, f"{module} never reaches Office")

    def test_fewer_than_five_files_is_refused(self):
        with self.assertRaises(Refusal) as cm:
            mine.mine_corpus(self.corpus(4))
        self.assertEqual(str(cm.exception), "corpus has 4 files; remedy: more documents of one kind")

    def test_a_corpus_of_two_kinds_is_refused(self):
        d = self.corpus(5)
        ooxml_fixtures.pptx(d / "p.pptx")
        with self.assertRaises(Refusal) as cm:
            mine.mine_corpus(d)
        self.assertEqual(str(cm.exception), "corpus mixes .docx and .pptx files; remedy: more documents of one kind")

    def test_a_corpus_where_no_invariant_reaches_the_threshold_is_refused(self):
        d = Path(tempfile.mkdtemp())
        styles = [("Heading1", "Arial"), ("Heading2", "Calibri"), ("Heading3", "Garamond"), ("Title", "Georgia"),
                  ("Subtitle", "Verdana")]
        for i, (style, font) in enumerate(styles):
            ooxml_fixtures.docx(d / f"c{i}.docx", title_style=style, body_font=(font, 10 + i))
        with self.assertRaises(Refusal) as cm:
            mine.mine_corpus(d)
        self.assertEqual(str(cm.exception),
                         "no invariant reaches the threshold 0.8 over 5 files; remedy: more documents of one kind")

    def test_pptx_masters_give_the_title_placeholder_font(self):
        d = Path(tempfile.mkdtemp())
        for i in range(6):
            ooxml_fixtures.pptx(d / f"p{i}.pptx", title_font=("Garamond", 32), footer="Confidentiel")
        inv, _ = mine.mine_corpus(d)
        by_id = {i["id"]: i for i in inv["invariants"]}
        self.assertEqual(by_id["title_font"], {"id": "title_font", "kind": "font_run", "where": "title",
                                               "value": {"name": "Garamond", "size": 32}, "support": 1.0})
        self.assertEqual(by_id["footer_text"]["value"], mine.redact("Confidentiel"))
        self.assertEqual(inv["corpus"]["kind"], "pptx")

    def test_a_paragraph_style_observation_says_where(self):
        doc = ooxml_fixtures.docx(Path(tempfile.mkdtemp()) / "c.docx", title_style="Heading1")
        got = mine.mine_file(doc)
        self.assertEqual(got["kind"], "docx")
        self.assertIn({"kind": "paragraph_style", "where": "first", "value": {"builtin": "Heading1"}}, got["obs"])
        self.assertIn({"kind": "font_run", "where": "body", "value": {"name": "Garamond", "size": 11}}, got["obs"])
        self.assertNotIn("all", [o["where"] for o in got["obs"]], "body paragraphs without w:pStyle: no `all` observation")

    def test_a_french_style_id_maps_to_the_builtin_name(self):
        # Review Focus 2: French Word declares Heading 1 as w:styleId="Titre1", w:name w:val="heading 1".
        doc = ooxml_fixtures.docx(Path(tempfile.mkdtemp()) / "c.docx", title_style="Heading1", style_id="Titre1")
        with zipfile.ZipFile(doc) as zf:
            self.assertIn(b'w:styleId="Titre1"', zf.read("word/styles.xml"))
        got = mine.mine_file(doc)
        self.assertIn({"kind": "paragraph_style", "where": "first", "value": {"builtin": "Heading1"}}, got["obs"])
        self.assertNotIn("Titre", json.dumps(got["obs"]))

    def test_the_schema_hash_covers_the_invariants_list_only(self):
        d = self.corpus(5, footer=FOOTER)
        inv, _ = mine.mine_corpus(d)
        self.assertEqual(inv["corpus"]["schema_sha256"], canon.sha256(inv["invariants"]))   # L-P13
        again, _ = mine.mine_corpus(d, threshold=0.9)
        self.assertEqual(again["corpus"]["schema_sha256"], inv["corpus"]["schema_sha256"], "the threshold is not the schema")

    def test_the_content_hash_names_the_mined_files_and_changes_with_them(self):
        d = self.corpus(5)
        inv, _ = mine.mine_corpus(d)
        self.assertEqual(inv["corpus"]["content_sha256"], mine.content_sha256(d))
        (d / "notes.txt").write_text("x")
        self.assertEqual(mine.content_sha256(d), inv["corpus"]["content_sha256"], "a skipped file is not content")
        ooxml_fixtures.docx(d / "c5.docx")
        self.assertNotEqual(mine.content_sha256(d), inv["corpus"]["content_sha256"])

    def test_the_cli_writes_the_invariants_and_the_vault(self):
        d = self.corpus(5, footer=FOOTER)
        out = Path(tempfile.mkdtemp()) / "out"
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            self.assertEqual(entry.main(["mine", "--corpus", str(d), "--out", str(out)]), 0)
        self.assertEqual(sorted(p.name for p in out.iterdir()), [".vault.json", "invariants.json"])
        self.assertEqual(printed.getvalue(), f"mine: 3 invariants, 0 ambiguities over 5 files (0 skipped) -> {out / 'invariants.json'}\n")

    def test_the_fixture_script_builds_the_rust_tests_corpus(self):
        d = ooxml_fixtures.corpus(Path(tempfile.mkdtemp()) / "corpus", 7, 3)
        self.assertEqual(sorted(p.name for p in d.glob("*.docx")), [f"c{i}.docx" for i in range(10)])
        self.assertEqual(len(list(d.glob("*.pdf"))), 10)
        inv, _ = mine.mine_corpus(d)
        self.assertEqual([a["invariant"] for a in inv["ambiguities"]], ["body_font"])
