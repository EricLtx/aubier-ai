import contextlib
import copy
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from vkforge import __main__ as entry
from vkforge import canon, inventory, mine, ontology, plan
from vkforge.errors import Refusal
from vkforge.tests.ooxml_fixtures import FOOTER

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ARCH = "arch:sha256:" + "a" * 64
TIE = {"invariant": "body_font", "kind": "font_run", "candidates": [
    {"value": {"name": "Calibri", "size": 12}, "support": 0.5},
    {"value": {"name": "Garamond", "size": 11}, "support": 0.5}]}


def house_invariants() -> dict:
    """The house style of the fixture contracts, as Task 5's miner writes it."""
    invariants = [
        {"id": "body_font", "kind": "font_run", "where": "body", "value": {"name": "Garamond", "size": 11}, "support": 1.0},
        {"id": "export_format", "kind": "export_format", "value": {"format": "pdf"}, "support": 1.0},
        {"id": "footer_text", "kind": "footer", "value": mine.redact(FOOTER), "support": 1.0},
        {"id": "title_style", "kind": "paragraph_style", "where": "first", "value": {"builtin": "Heading1"}, "support": 1.0},
    ]
    return {"threshold": 0.8, "invariants": invariants, "ambiguities": [],
            "corpus": {"files": 10, "skipped": 0, "kind": "docx", "content_sha256": "sha256:" + "c" * 64,
                       "schema_sha256": canon.sha256(invariants)}}


def without(inv: dict, ident: str) -> dict:
    inv = copy.deepcopy(inv)
    inv["invariants"] = [i for i in inv["invariants"] if i["id"] != ident]
    inv["corpus"]["schema_sha256"] = canon.sha256(inv["invariants"])
    return inv


class PlanTest(unittest.TestCase):
    def setUp(self):
        self.ont = ontology.load()
        self.binding = json.loads((FIXTURES / "binding-word.json").read_text(encoding="utf-8"))

    def steps(self, p):
        return [(s["verb"], s["args"]) for s in p["actions"][0]["steps"]]

    def test_the_house_style_plan_has_its_steps_in_order(self):
        p, excluded = plan.build(house_invariants(), self.binding, self.ont)
        a = p["actions"][0]
        self.assertEqual(a["name"], "appliquer_style_maison")
        self.assertEqual([v for v, _ in self.steps(p)],
                         ["open", "apply_paragraph_style", "set_font_run", "set_footer", "save", "export",
                          "verify_paragraph_style", "verify_font_run", "verify_footer", "verify_pdf"])
        self.assertEqual(self.steps(p)[0], ("open", {"path": "$arg.document"}))
        self.assertEqual(self.steps(p)[1], ("apply_paragraph_style", {"selector": "first", "style": "Heading1"}))
        self.assertEqual(self.steps(p)[5], ("export", {"format": "pdf", "path": "$arg.pdf"}))
        self.assertEqual(a["post"], ["title_style", "body_font", "footer_text", "pdf_exists"])
        self.assertEqual(a["pre"], ["document is .docx", "not open elsewhere"])
        self.assertIs(a["reversible"], False)
        self.assertNotIn("note", a)
        self.assertEqual((p["family"], p["app"], p["qualification"]),
                         ("office-document", "word", {"min_passes": 10, "min_rate": 1.0}))
        self.assertEqual(p["invariants_sha256"], canon.sha256(house_invariants()))
        self.assertEqual((p["catalogue_sha256"], p["ontology_sha256"]),
                         (self.binding["catalogue_sha256"], self.binding["ontology_sha256"]))
        self.assertEqual(excluded, [])
        plan.validate(p)

    def test_a_secret_is_referenced_never_inlined(self):
        p, _ = plan.build(house_invariants(), self.binding, self.ont)
        self.assertIn(("set_footer", {"text": "$secret.footer_text"}), self.steps(p))
        blob = json.dumps(p, ensure_ascii=False)
        self.assertNotIn(mine.redact(FOOTER), blob)
        self.assertNotIn(FOOTER, blob)

    def test_an_unbound_verb_excludes_its_invariant_and_says_so(self):
        b = copy.deepcopy(self.binding)
        for v in b["verbs"]:
            if v["verb"] == "set_footer":
                v.update(status="unbound", member=None, constants=None, why="Section.Footers matched nothing")
        p, excluded = plan.build(house_invariants(), b, self.ont)
        verbs = [v for v, _ in self.steps(p)]
        self.assertNotIn("set_footer", verbs)
        self.assertNotIn("verify_footer", verbs)
        self.assertNotIn("footer_text", p["actions"][0]["post"])
        self.assertEqual(excluded, [{"invariant": "footer_text",
                                     "why": "verb set_footer is unbound on word: Section.Footers matched nothing"}])

    def test_a_tie_is_an_ambiguity_purged_without_a_resolver(self):
        inv = without(house_invariants(), "body_font")
        inv["ambiguities"] = [TIE]
        p, _ = plan.build(inv, self.binding, self.ont)
        self.assertNotIn("set_font_run", [v for v, _ in self.steps(p)])
        self.assertEqual(p["ambiguities"], [{"invariant": "body_font", "resolved_by": "purged"}])
        plan.validate(p)

    def test_a_resolver_answer_becomes_a_step_traced_to_the_arch(self):
        inv = without(house_invariants(), "body_font")
        inv["ambiguities"] = [TIE]
        garamond = {"name": "Garamond", "size": 11}
        p, _ = plan.build(inv, self.binding, self.ont, resolve=lambda amb: {"value": garamond, "resolved_by": ARCH})
        self.assertIn(("set_font_run", {"selector": "body", "font": garamond}), self.steps(p))
        self.assertIn("body_font", p["actions"][0]["post"])
        self.assertEqual(p["ambiguities"], [{"invariant": "body_font", "resolved_by": ARCH, "value": garamond}])
        plan.validate(p)

    def test_a_resolver_answer_outside_the_candidates_is_purged(self):
        inv = without(house_invariants(), "body_font")
        inv["ambiguities"] = [TIE]
        p, _ = plan.build(inv, self.binding, self.ont,
                          resolve=lambda amb: {"value": {"name": "Comic Sans MS", "size": 40}, "resolved_by": ARCH})
        self.assertEqual(p["ambiguities"], [{"invariant": "body_font", "resolved_by": "purged"}])
        self.assertNotIn("Comic", json.dumps(p))

    def test_the_resolver_file_is_read_by_invariant_id(self):
        path = Path(tempfile.mkdtemp()) / "resolver.json"
        canon.write({"body_font": {"value": {"name": "Garamond", "size": 11}, "resolved_by": ARCH}}, path)
        resolve = plan.load_resolver(path)
        self.assertEqual(resolve(TIE), {"value": {"name": "Garamond", "size": 11}, "resolved_by": ARCH})
        self.assertIsNone(resolve({"invariant": "title_style", "kind": "paragraph_style", "candidates": []}))

    def test_invariants_without_a_rule_are_listed_as_excluded(self):
        inv = house_invariants()
        inv["invariants"].append({"id": "body_style", "kind": "paragraph_style", "where": "all",
                                  "value": {"builtin": "Normal"}, "support": 1.0})
        _, excluded = plan.build(inv, self.binding, self.ont)
        self.assertEqual(excluded, [{"invariant": "body_style", "why": "no rule for body_style in plan.RULES (v1)"}])

    def test_a_corpus_with_nothing_drivable_is_refused(self):
        b = copy.deepcopy(self.binding)
        for v in b["verbs"]:
            if v["verb"] in ("apply_paragraph_style", "set_font_run", "set_footer", "export"):
                v.update(status="unbound", member=None, constants=None, why="test")
        with self.assertRaises(Refusal) as cm:
            plan.build(house_invariants(), b, self.ont)
        self.assertIn("no invariant can be driven on word", str(cm.exception))

    def test_the_same_planner_builds_the_powerpoint_plan(self):
        binding = ontology.bind(self.ont, inventory.catalogue_from_fixture("powerpoint", FIXTURES / "catalogue-powerpoint.json"))
        invariants = [{"id": "title_font", "kind": "font_run", "where": "title", "value": {"name": "Garamond", "size": 32},
                       "support": 1.0}]
        inv = {"threshold": 0.8, "invariants": invariants, "ambiguities": [],
               "corpus": {"files": 6, "skipped": 0, "kind": "pptx", "content_sha256": "sha256:" + "c" * 64,
                          "schema_sha256": canon.sha256(invariants)}}
        p, _ = plan.build(inv, binding, self.ont)
        a = p["actions"][0]
        self.assertEqual((p["app"], a["name"], a["post"]), ("powerpoint", "appliquer_style_presentation", ["title_font"]))
        self.assertEqual(a["pre"], ["document is .pptx", "not open elsewhere"])
        plan.validate(p)

    def test_a_plan_is_held_to_the_contract_rules(self):
        def refuse(edit, needle):
            p, _ = plan.build(house_invariants(), self.binding, self.ont)
            edit(p)
            with self.assertRaises(Refusal) as cm:
                plan.validate(p)
            self.assertIn(needle, str(cm.exception))

        refuse(lambda p: p["actions"].clear(), "actions is empty")
        refuse(lambda p: p["actions"][0]["steps"].pop(0), "first step")
        refuse(lambda p: p["actions"][0]["steps"][1].update(verb="Apply-Style"), "verb")
        refuse(lambda p: p["actions"][0]["steps"][3]["args"].update(text="$secret."), "$secret.")
        refuse(lambda p: p["actions"][0]["post"].pop(), "verify_")
        refuse(lambda p: p["actions"][0]["steps"][1]["args"].update({"from-x": 1}), "from-x")
        refuse(lambda p: p["ambiguities"].append({"invariant": "x", "resolved_by": "purged", "value": 1}), "value")

    def test_plan_sha256_is_the_hash_of_the_file_bytes(self):
        p, excluded = plan.build(house_invariants(), self.binding, self.ont)
        out = Path(tempfile.mkdtemp())
        path = plan.write_plan(p, excluded, out)
        self.assertEqual(path, out / "plan.json")
        self.assertEqual(plan.plan_sha256(path), "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(path.read_bytes(), canon.dumps(p))
        self.assertEqual(json.loads((out / "plan-excluded.json").read_text(encoding="utf-8")), excluded)

    def test_the_cli_reads_the_artefacts_and_refuses_hand_edited_invariants(self):
        work = Path(tempfile.mkdtemp())
        canon.write(house_invariants(), work / "invariants.json")
        out = work / "out"
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            self.assertEqual(entry.main(["plan", "--invariants", str(work / "invariants.json"),
                                         "--binding", str(FIXTURES / "binding-word.json"), "--out", str(out)]), 0)
        self.assertTrue((out / "plan.json").is_file())
        (work / "invariants.json").write_text(json.dumps(house_invariants()), encoding="utf-8")   # not canonical
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(entry.main(["plan", "--invariants", str(work / "invariants.json"),
                                         "--binding", str(FIXTURES / "binding-word.json"), "--out", str(work / "o2")]), 2)
        self.assertIn("vkforge plan: invariants.json is not in canonical form; remedy:", err.getvalue())

    def test_the_fixtures_for_tasks_7_and_8_are_committed(self):
        # Ruling L-P21: written on the first run, compared on every later one.
        inv = house_invariants()
        p, _ = plan.build(inv, self.binding, self.ont)
        for name, obj in (("invariants-word-maison.json", inv), ("plan-word-maison.json", p)):
            path = FIXTURES / name
            if not path.exists():
                canon.write(obj, path)
            self.assertEqual(path.read_bytes(), canon.dumps(obj), f"{name} is stale: delete it, run this test, commit it")
