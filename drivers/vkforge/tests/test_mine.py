import contextlib
import hashlib
import hmac
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path

from vkforge import __main__ as entry
from vkforge import canon, mine
from vkforge.errors import Refusal
from vkforge.tests import ooxml_fixtures
from vkforge.tests.ooxml_fixtures import FOOTER, OTHER_SALT, SALT

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def salt_file(salt: bytes = SALT) -> Path:
    """A learn's salt as `vk driver learn` leaves it: the bytes in a file of their own."""
    path = Path(tempfile.mkdtemp()) / ".salt"
    path.write_bytes(salt)
    return path


def a_corpus(n: int, **kw) -> Path:
    d = Path(tempfile.mkdtemp())
    for i in range(n):
        ooxml_fixtures.docx(d / f"c{i}.docx", **kw)
    return d


class MineTest(unittest.TestCase):
    def corpus(self, n=10, **kw) -> Path:
        return a_corpus(n, **kw)

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
        private = Path(tempfile.mkdtemp()) / "forge" / "word-maison" / ".vault.json"   # the daemon's (I-2)
        inv, vault = mine.mine_corpus(d)
        self.assertEqual(mine.write_invariants(inv, vault, out, private), private)
        self.assertEqual([p.name for p in out.iterdir()], ["invariants.json"], "no clear string in --out")
        blob = (out / "invariants.json").read_bytes()
        for s in secrets:
            self.assertNotIn(s.encode("utf-8"), blob)
        self.assertEqual(blob, canon.dumps(inv))
        self.assertEqual(json.loads(private.read_text(encoding="utf-8")),
                         {mine.redact("Pied Jean Dupont"): "Pied Jean Dupont"})
        if os.name == "posix":
            self.assertEqual(private.stat().st_mode & 0o777, 0o600)

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
            self.assertEqual(entry.main(["mine", "--corpus", str(d), "--out", str(out),
                                         "--salt-file", str(salt_file())]), 0)
        self.assertEqual(sorted(p.name for p in out.iterdir()), [".vault.json", "invariants.json"])
        self.assertEqual(printed.getvalue(), f"mine: 3 invariants, 0 ambiguities over 5 files (0 skipped) -> {out / 'invariants.json'}\n")
        blob = (out / "invariants.json").read_text(encoding="utf-8")
        self.assertIn(mine.redact(FOOTER, SALT), blob, "the CLI mines under the salt it is given (D-01)")
        self.assertNotIn("<redacted:sha256:", blob)

    def test_the_cli_writes_the_vault_where_vault_names_it_and_nothing_clear_in_out(self):
        """The daemon's form (final review I-2): `--vault` under its private forge directory."""
        d = self.corpus(5, footer=FOOTER)
        out = Path(tempfile.mkdtemp()) / "out"
        vault = Path(tempfile.mkdtemp()) / "forge" / "word-maison" / ".vault.json"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(entry.main(["mine", "--corpus", str(d), "--out", str(out), "--vault", str(vault),
                                         "--salt-file", str(salt_file())]), 0)
        self.assertEqual(sorted(p.name for p in out.iterdir()), ["invariants.json"])
        self.assertEqual(json.loads(vault.read_text(encoding="utf-8")), {mine.redact(FOOTER, SALT): FOOTER})
        self.assertNotIn(FOOTER.encode("utf-8"), (out / "invariants.json").read_bytes())
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(entry.main(["mine", "--corpus", str(d), "--out", str(Path(tempfile.mkdtemp()) / "o"),
                                         "--vault", str(d / "v" / ".vault.json"), "--salt-file", str(salt_file())]), 2)
        self.assertIn("is inside the corpus; remedy: write elsewhere", err.getvalue())

    def test_the_fixture_script_builds_the_rust_tests_corpus(self):
        d = ooxml_fixtures.corpus(Path(tempfile.mkdtemp()) / "corpus", 7, 3)
        self.assertEqual(sorted(p.name for p in d.glob("*.docx")), [f"c{i}.docx" for i in range(10)])
        self.assertEqual(len(list(d.glob("*.pdf"))), 10)
        inv, _ = mine.mine_corpus(d)
        self.assertEqual([a["invariant"] for a in inv["ambiguities"]], ["body_font"])

    def test_the_fixture_script_builds_the_footer_corpus_the_rust_test_mines(self):
        # The command line the Rust tests spawn (`ooxml_fixtures.py DIR footers`): ten contracts whose
        # footers all differ, a client's name in each, so that the footer is an ambiguity of ten
        # candidates (founder decision D-02's case). The Rust test's "0 of 10" counts these.
        d = Path(tempfile.mkdtemp()) / "corpus"
        subprocess.run([sys.executable, "-I", ooxml_fixtures.__file__, str(d), "footers"], check=True)
        self.assertEqual(sorted(p.name for p in d.glob("*.docx")), sorted(f"c{i}.docx" for i in range(10)))
        self.assertEqual(len(list(d.glob("*.pdf"))), 10)
        inv, vault = mine.mine_corpus(d, salt=SALT)
        (amb,) = inv["ambiguities"]
        self.assertEqual((amb["invariant"], len(amb["candidates"]), sorted(vault.values())),
                         ("footer_text", 10, sorted(ooxml_fixtures.FOOTERS)))


def _rewrite(doc: Path, member: str, old: bytes, new: bytes) -> None:
    with zipfile.ZipFile(doc) as zf:
        members = {n: zf.read(n) for n in zf.namelist()}
    assert old in members[member]
    members[member] = members[member].replace(old, new)
    with zipfile.ZipFile(doc, "w", zipfile.ZIP_DEFLATED) as zf:
        for n, data in members.items():
            zf.writestr(n, data)


class MineRobustnessTest(unittest.TestCase):
    def test_a_damaged_member_is_skipped_and_the_run_goes_on(self):
        d = Path(tempfile.mkdtemp())
        for i in range(5):
            ooxml_fixtures.docx(d / f"c{i}.docx")
        bad = ooxml_fixtures.docx(d / "bad.docx", body_font=("Garamond", 11))
        _rewrite(bad, "word/document.xml", b"Titre du document", b"Titre du document" * 40)   # a deflated member
        with zipfile.ZipFile(bad) as zf:
            info = zf.getinfo("word/document.xml")
        raw = bytearray(bad.read_bytes())
        start = info.header_offset + 30 + len(info.filename.encode("utf-8"))
        for k in range(start + 4, start + 16):
            raw[k] ^= 0xFF
        bad.write_bytes(bytes(raw))
        with zipfile.ZipFile(bad) as zf, self.assertRaises((zlib.error, EOFError, ValueError)):
            zf.read("word/document.xml")             # the damage is real
        inv, _ = mine.mine_corpus(d)
        self.assertEqual((inv["corpus"]["files"], inv["corpus"]["skipped"]), (5, 1))

    def test_orientation_is_never_a_free_string(self):
        doc = ooxml_fixtures.docx(Path(tempfile.mkdtemp()) / "c.docx")
        _rewrite(doc, "word/document.xml", b"<w:sectPr>",
                 b'<w:sectPr><w:pgSz w:w="16838" w:h="11906" w:orient="Jean Dupont"/>'
                 b'<w:pgMar w:top="1" w:right="2" w:bottom="3" w:left="4"/>')
        obs = mine.mine_file(doc)["obs"]
        self.assertIn({"kind": "orientation", "where": None, "value": "landscape"}, obs)
        self.assertIn({"kind": "page_size", "where": None, "value": {"height": 11906, "width": 16838}}, obs)
        self.assertIn({"kind": "margins", "where": None, "value": {"top": 1, "right": 2, "bottom": 3, "left": 4}}, obs)
        self.assertNotIn("Dupont", json.dumps(obs))

    def test_out_inside_the_corpus_is_refused(self):
        d = Path(tempfile.mkdtemp())
        for i in range(5):
            ooxml_fixtures.docx(d / f"c{i}.docx")
        for out in (d, d / "out"):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(entry.main(["mine", "--corpus", str(d), "--out", str(out),
                                             "--salt-file", str(salt_file())]), 2)
            self.assertIn("is inside the corpus; remedy: write elsewhere", err.getvalue())
        self.assertEqual(sorted(p.name for p in d.iterdir()), [f"c{i}.docx" for i in range(5)])

    def test_a_custom_style_reaches_the_vault_by_its_name_and_the_artefact_by_its_token(self):
        d = Path(tempfile.mkdtemp())
        for i in range(5):
            ooxml_fixtures.docx(d / f"c{i}.docx", title_style="Clause Dupont", style_id="ClauseId7")
        inv, vault = mine.mine_corpus(d)
        token = mine.redact("Clause Dupont")
        by_id = {i["id"]: i for i in inv["invariants"]}
        self.assertEqual(by_id["title_style"]["value"], {"custom": token})
        self.assertEqual(vault, {token: "Clause Dupont"}, "the name, never the styleId")
        out = Path(tempfile.mkdtemp())
        mine.write_invariants(inv, vault, out)
        blob = (out / "invariants.json").read_bytes() + b"\n" + (out / ".vault.json").read_bytes()
        self.assertNotIn(b"ClauseId7", blob)
        self.assertNotIn(b"Dupont", (out / "invariants.json").read_bytes())

    def test_a_value_that_looks_like_a_token_does_not_break_the_vault(self):
        d = Path(tempfile.mkdtemp())
        for i in range(5):
            ooxml_fixtures.docx(d / f"c{i}.docx", body_font=("<redacted:sha256:feed>", 11))
        inv, vault = mine.mine_corpus(d)
        self.assertEqual(vault, {})

    def test_the_fixtures_are_byte_identical_whatever_the_platform(self):
        a = ooxml_fixtures.docx(Path(tempfile.mkdtemp()) / "a.docx", footer=FOOTER)
        with zipfile.ZipFile(a) as zf:
            for info in zf.infolist():
                self.assertEqual((info.date_time, info.create_system, info.compress_type, info.external_attr >> 16),
                                 ((1980, 1, 1, 0, 0, 0), 3, zipfile.ZIP_STORED, 0o644), info.filename)


class SaltTest(unittest.TestCase):
    """Founder decision D-01: a learn's salt keys every token; with no salt, the pre-D-01 form the
    pinned fixtures carry (ruling LF-2)."""

    def test_a_salt_keys_the_token_and_two_salts_give_two_tokens(self):
        a = mine.redact(FOOTER, SALT)
        self.assertEqual(a, "<redacted:hmac-sha256:"
                         + hmac.new(SALT, FOOTER.encode("utf-8"), hashlib.sha256).hexdigest() + ">")
        self.assertEqual(a, mine.redact(FOOTER, SALT), "one salt, one token: the vault and qualify agree")
        self.assertNotEqual(a, mine.redact(FOOTER, OTHER_SALT), "another learn, another token")
        self.assertNotEqual(a, mine.redact(FOOTER))
        self.assertNotIn(hashlib.sha256(FOOTER.encode("utf-8")).hexdigest(), a,
                         "the bare hash a guessed footer would confirm is not in the token")

    def test_unsalted_tokens_are_the_pinned_fixtures_own(self):
        self.assertEqual(mine.redact(FOOTER),
                         "<redacted:sha256:" + hashlib.sha256(FOOTER.encode("utf-8")).hexdigest() + ">")
        fixture = json.loads((FIXTURES / "invariants-word-maison.json").read_text(encoding="utf-8"))
        footer = next(i for i in fixture["invariants"] if i["id"] == "footer_text")
        self.assertEqual(footer["value"], mine.redact(FOOTER), "invariants-word-maison.json stays byte-identical")

    def test_mining_under_a_salt_writes_salted_tokens_only(self):
        d = a_corpus(5, title_style="Clause Dupont", style_id="ClauseId7", footer=FOOTER)
        inv, vault = mine.mine_corpus(d, salt=SALT)
        by_id = {i["id"]: i for i in inv["invariants"]}
        self.assertEqual(by_id["footer_text"]["value"], mine.redact(FOOTER, SALT))
        self.assertEqual(by_id["title_style"]["value"], {"custom": mine.redact("Clause Dupont", SALT)})
        self.assertEqual(vault, {mine.redact(FOOTER, SALT): FOOTER,
                                 mine.redact("Clause Dupont", SALT): "Clause Dupont"})
        self.assertNotIn("<redacted:sha256:", json.dumps(inv))
        self.assertEqual(sorted(mine.tokens(inv)), sorted(vault), "tokens() sees the salted form")
        self.assertEqual(mine.mine_file(d / "c0.docx", SALT)["vault"], vault, "one file, the same tokens")

    def test_a_presentation_footer_is_salted_too(self):
        # Task review tests-1: the footer of a slide master (`_pptx`) is keyed by the salt as well.
        d = Path(tempfile.mkdtemp())
        for i in range(5):
            ooxml_fixtures.pptx(d / f"p{i}.pptx", footer=FOOTER)
        inv, vault = mine.mine_corpus(d, salt=SALT)
        by_id = {i["id"]: i for i in inv["invariants"]}
        self.assertEqual(by_id["footer_text"]["value"], mine.redact(FOOTER, SALT))
        self.assertEqual(vault, {mine.redact(FOOTER, SALT): FOOTER})
        self.assertNotIn("<redacted:sha256:", json.dumps(inv))

    def test_a_custom_body_style_and_a_header_are_salted_too(self):
        # Task review tests-1: the style every body paragraph carries (`where: all`), and the header.
        d = a_corpus(5, body_style="Corps Dupont", header="En-tête Dupont")
        inv, vault = mine.mine_corpus(d, salt=SALT)
        by_id = {i["id"]: i for i in inv["invariants"]}
        self.assertEqual(by_id["body_style"]["value"], {"custom": mine.redact("Corps Dupont", SALT)})
        self.assertEqual(by_id["header_text"]["value"], mine.redact("En-tête Dupont", SALT))
        self.assertEqual(vault, {mine.redact("Corps Dupont", SALT): "Corps Dupont",
                                 mine.redact("En-tête Dupont", SALT): "En-tête Dupont"})
        self.assertNotIn("<redacted:sha256:", json.dumps(inv))

    def test_the_stage_refuses_to_run_without_a_salt_file(self):
        # Task review tests-4, ruling LF-2: `vkforge mine` requires the salt; it never falls back to
        # the unsalted form.
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            entry.main(["mine", "--corpus", str(a_corpus(5)), "--out", str(Path(tempfile.mkdtemp()) / "out")])
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("the following arguments are required: --salt-file", err.getvalue())

    def test_a_salt_file_is_read_whole_or_refused_in_one_line(self):
        self.assertEqual(mine.read_salt(salt_file()), SALT)
        for path, said in ((salt_file(SALT[:31]), "holds 31 bytes, not 32"),
                           (salt_file(SALT + b"\0"), "holds 33 bytes, not 32"),
                           (salt_file(b""), "holds 0 bytes, not 32"),
                           (Path(tempfile.mkdtemp()) / "absent.salt", "cannot be read")):
            with self.assertRaises(Refusal) as cm:
                mine.read_salt(path)
            self.assertIn(f"salt file {path} {said}", str(cm.exception))
            self.assertIn("; remedy: vk driver learn again", str(cm.exception))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = entry.main(["mine", "--corpus", str(a_corpus(5)), "--out", str(Path(tempfile.mkdtemp()) / "out"),
                               "--salt-file", str(salt_file(b"short"))])
        self.assertEqual(code, 2)
        self.assertIn("vkforge mine: salt file ", err.getvalue())


class ReadVaultTest(unittest.TestCase):
    """The vault's one reader, `mine.read_vault`, next to its writer: `plan` reads the vault to cut it
    down (D-02) and `generate` to resolve the plan's secrets, and both refuse alike."""

    def test_the_vault_mine_wrote_is_read_back_whole(self):
        inv, vault = mine.mine_corpus(a_corpus(5, footer=FOOTER), salt=SALT)
        path = mine.write_invariants(inv, vault, Path(tempfile.mkdtemp()) / "out")
        self.assertEqual(mine.read_vault(path), {mine.redact(FOOTER, SALT): FOOTER})

    def test_a_vault_that_is_not_an_object_of_clear_strings_is_refused_in_one_line(self):
        for label, content in (("a list", "[1, 2]"), ("a string", '"x"'), ("null", "null"),
                               ("a value that is no string", '{"<redacted:hmac-sha256:aa>": 3}')):
            with self.subTest(label):
                path = Path(tempfile.mkdtemp()) / ".vault.json"
                path.write_text(content, encoding="utf-8")
                with self.assertRaises(Refusal) as cm:
                    mine.read_vault(path)
                self.assertEqual(str(cm.exception),
                                 f"vault {path} is not an object of clear strings; remedy: run vkforge mine again")

    def test_a_vault_that_is_absent_or_not_json_is_refused_by_name(self):
        gone = Path(tempfile.mkdtemp()) / ".vault.json"
        with self.assertRaises(Refusal) as cm:
            mine.read_vault(gone)
        self.assertEqual(str(cm.exception), f"vault {gone} not found; remedy: run vkforge mine again")
        broken = Path(tempfile.mkdtemp()) / ".vault.json"
        broken.write_text("{", encoding="utf-8")
        with self.assertRaises(Refusal) as cm:
            mine.read_vault(broken)
        self.assertEqual(str(cm.exception), f"vault {broken} is not JSON; remedy: run vkforge mine again")
