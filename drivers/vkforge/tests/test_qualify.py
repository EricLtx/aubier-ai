import copy
import json
import os
import shutil
import stat
import tempfile
import time
import unittest
from pathlib import Path

from vkforge import __main__ as entry
from vkforge import canon, generate, mine, ontology, plan, qualify
from vkforge.errors import Refusal
from vkforge.tests import ooxml_fixtures

FIXTURES = Path(__file__).resolve().parent / "fixtures"
HOUSE_POST = {"title_style": True, "body_font": True, "footer_text": True, "pdf_exists": True}


def learned(corpus: Path):
    """What `vk driver learn` leaves (Task 9), built here in Python: the invariants, the plan, and the
    driver generated into a temp dir, so every pass also proves it runs with no repository around it."""
    inv, vault = mine.mine_corpus(corpus)
    binding = json.loads((FIXTURES / "binding-word.json").read_text(encoding="utf-8"))
    p, _ = plan.build(inv, binding, ontology.load())
    driver = Path(tempfile.mkdtemp()) / "word-maison" / "driver"
    generate.generate(p, binding, inv, vault, driver, name="word-maison")
    return driver, p, inv


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
        out = self.driver.parent
        plan.write_plan(self.plan, [], out)
        canon.write(self.inv, out / "invariants.json")
        code = entry.main(["qualify", "--driver", str(self.driver), "--corpus", str(self.corpus),
                           "--plan", str(out / "plan.json"), "--invariants", str(out / "invariants.json"),
                           "--work", str(self.work), "--fake"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads((self.work / "qualification.json").read_text(encoding="utf-8"))["passes"], 10)
