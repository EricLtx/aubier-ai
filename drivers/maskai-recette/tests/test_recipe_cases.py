"""Ruling R2, the plan pre-flight: the case ids the recipes' harness instructions
name are cases of `cases.json`. The harness cannot read `cases.json`; it passes the
ids it was told, and an id the driver does not know is a refusal in the middle of a
live run. Standard library only: no browser, no driver import."""
import json
import os
import re
import unittest

TESTS = os.path.dirname(os.path.abspath(__file__))
DRIVER = os.path.dirname(TESTS)
REPO = os.path.dirname(os.path.dirname(DRIVER))

# The four shapes of case id: the N grid's (N4-2, N2-1a), the M grid's of the second
# recette (M1-J1a, M3-B2, M0-3; PROCEDURE-2 §8.10), the corpus-v2 oracles' (V2-L1, V2-V5)
# and the fixture's (fixture-case-1) — whole tokens only, so `V2-V12` is not read as `V2-V1`
# nor `xN1-1` as `N1-1`.
CASE_ID = re.compile(r"(?<![\w-])(?:N\d-\d+[a-z]?|M\d-[A-Z]?\d+[a-z]?|V2-[LV]\d|fixture-case-\d)(?!\w)")
# The rules every harness step carries, from « Règles. » to the last forbidden tool.
RULES = re.compile(r"Règles\. .*?vk_write_decision\. ", re.S)
LEVEL = re.compile(r"^Niveau ([NM]\d) ")
# Each recipe and the grid it runs: every case of that grid, level by level, once.
GRIDS = {"recette-maskai.json": "N", "recette-maskai-2.json": "M"}


def load(*parts: str):
    with open(os.path.join(*parts), encoding="utf-8") as f:
        return json.load(f)


def harness_steps(recipe: dict) -> list:
    return [s for s in recipe["steps"] if s["kind"] == "harness"]


class RecipeCasesTest(unittest.TestCase):
    def setUp(self):
        self.cases = {c["id"]: c for c in load(DRIVER, "cases.json")["cases"]}
        self.recipes = {
            name: load(REPO, "recipes", name)
            for name in ("recette-maskai.json", "recette-maskai-2.json", "recette-fixture.json")
        }

    def test_the_case_pattern_reads_whole_ids_only(self):
        self.assertEqual(
            CASE_ID.findall("N2-1a, V2-V1 ; fixture-case-2 (N0-1) V2-V12 xN1-1 N1-1ab N1-12 -N3-1 V2-L3_"),
            ["N2-1a", "V2-V1", "fixture-case-2", "N0-1", "N1-12"])
        self.assertEqual(CASE_ID.findall("M1-J1a, M3-B2 ; M0-3 (M4-O1) xM1-J1a M1-J1ab M5-1"),
                         ["M1-J1a", "M3-B2", "M0-3", "M4-O1", "M5-1"])

    def test_the_rules_block_is_the_same_in_every_harness_step(self):
        """One rules block, twenty copies (ten N levels, two fixture levels, eight M levels):
        a correction made in one step and not the others would leave levels that refuse,
        replay or stop differently."""
        blocks = []
        for name, recipe in self.recipes.items():
            for i, step in enumerate(harness_steps(recipe)):
                found = RULES.findall(step["instruction"])
                self.assertEqual(len(found), 1, f"{name} harness step {i + 1}: one rules block")
                blocks.append((f"{name} harness step {i + 1}", found[0]))
        self.assertEqual(len(blocks), 20)
        first = blocks[0][1]
        for where, block in blocks[1:]:
            self.assertEqual(block, first, f"{where} differs from {blocks[0][0]}")
        self.assertIn("s'il refuse à son tour, passe au cas suivant sans rejouer le cas", first)

    def test_every_case_named_in_a_recipe_exists(self):
        for name, recipe in self.recipes.items():
            for i, step in enumerate(harness_steps(recipe)):
                named = CASE_ID.findall(step["instruction"])
                self.assertTrue(named, f"{name} harness step {i + 1} names no case")
                unknown = sorted(set(named) - set(self.cases))
                self.assertEqual(unknown, [], f"{name} harness step {i + 1} names cases not in cases.json")

    def test_each_level_names_only_its_own_cases(self):
        """A step announced as level Nx names cases of Nx, or the cases a concurrent
        case of Nx runs (N8-4 loads V2-V1 and V2-V3)."""
        for name, recipe in self.recipes.items():
            for i, step in enumerate(harness_steps(recipe)):
                m = LEVEL.match(step["instruction"])
                self.assertIsNotNone(m, f"{name} harness step {i + 1} does not start with « Niveau Nx »")
                level = m.group(1)
                allowed = {cid for cid, c in self.cases.items() if c["level"] == level}
                for c in self.cases.values():
                    if c["level"] == level:
                        allowed.update(c.get("cases", []))
                stray = sorted(set(CASE_ID.findall(step["instruction"])) - allowed)
                self.assertEqual(stray, [], f"{name} harness step {i + 1} ({level}) names other levels' cases")

    def test_each_recette_runs_every_case_of_its_grid_once_level_by_level(self):
        """recette-maskai.json runs the N grid N0 to N9, recette-maskai-2.json the M grid M0 to M7."""
        for name, grid_letter in GRIDS.items():
            steps = harness_steps(self.recipes[name])
            levels = sorted({c["level"] for c in self.cases.values() if c["level"].startswith(grid_letter)},
                            key=lambda lv: int(lv[1:]))
            self.assertEqual([LEVEL.match(s["instruction"]).group(1) for s in steps], levels, name)
            named = set()
            for s in steps:
                named.update(CASE_ID.findall(s["instruction"]))
            grid = {cid for cid, c in self.cases.items() if c["level"].startswith(grid_letter)
                    and not cid.startswith("fixture-")}
            self.assertEqual(sorted(grid - named), [], f"{name}: cases of the grid no step runs")
        self.assertEqual({c["level"][0] for c in self.cases.values()}, {"N", "M"})

    def test_each_recipe_drives_its_own_driver(self):
        """The recette acts on maskai-1 and the dry run on maskai-fixture, the names
        their manifests mount under, and each instruction tells the harness so."""
        for name, manifest in (("recette-maskai.json", "manifest.json"), ("recette-maskai-2.json", "manifest.json"),
                               ("recette-fixture.json", "manifest-fixture.json")):
            driver = load(DRIVER, manifest)["name"]
            for i, step in enumerate(harness_steps(self.recipes[name])):
                self.assertEqual(step["drivers"], [driver], f"{name} harness step {i + 1}")
                self.assertIn(f'driver="{driver}"', step["instruction"], f"{name} harness step {i + 1}")
                for word in ("vk_log", "vk_request_approval", "exporter_journal", "replayed", "dialog", "timeout"):
                    self.assertIn(word, step["instruction"], f"{name} harness step {i + 1}: {word}")
        self.assertNotIn("maskai-1", json.dumps(self.recipes["recette-fixture.json"]))

    def test_every_level_leaves_evidence_even_when_every_case_replays(self):
        """A harness step that attaches nothing fails (`harness produced no artefact`),
        and a level whose cases are all journaled already attaches nothing: replayed
        verdicts carry no evidence and an export of no new line writes no file. So
        every level takes a screenshot, unconditionally, before its export."""
        for name, recipe in self.recipes.items():
            for i, step in enumerate(harness_steps(recipe)):
                text = step["instruction"]
                self.assertRegex(text, r"capture_ecran[^.]*, toujours", f"{name} harness step {i + 1}")
                self.assertLess(text.rindex("capture_ecran"), text.rindex("exporter_journal"),
                                f"{name} harness step {i + 1}: the capture comes before the closing export")


if __name__ == "__main__":
    unittest.main()
