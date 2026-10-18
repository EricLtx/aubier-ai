import contextlib
import hashlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

DRIVERS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(DRIVERS))

from vkforge import debrief  # noqa: E402
from vkforge.errors import Refusal  # noqa: E402
from vkforge.tests import debrief_fixtures  # noqa: E402

RUN_17_10 = None


def setUpModule():
    global RUN_17_10
    RUN_17_10 = debrief_fixtures.run_2026_10_17(Path(tempfile.mkdtemp()) / "run")


def tree_sha(d: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(x for x in d.rglob("*") if x.is_file()):
        h.update(p.relative_to(d).as_posix().encode() + b"\0" + p.read_bytes())
    return h.hexdigest()


class Debrief17_10Test(unittest.TestCase):
    """The 17/10 run, debriefed from its artefacts (the real journal, the excerpts, the task tables)."""

    def setUp(self):
        self.s, self.p, self.md = debrief.debrief(RUN_17_10)

    def test_the_signals_are_the_debrief_s_verified_facts(self):
        s = self.s
        self.assertEqual(len(s["cases"]), 34)
        self.assertEqual(s["acts_total"], 291)
        self.assertAlmostEqual(sum(t["cost_usd"] for t in s["tasks"]), 4.245, places=3)
        self.assertEqual(s["refusals"], {"timeout": 5, "dialog": 2, "precondition": 2, "host": 0, "internal": 0})
        live = [p for p in s["levels"]["N4"] if p["new_lines"]]
        self.assertEqual([p["task"] for p in live], ["task-node-1-7", "task-node-1-16", "task-node-1-25"])
        self.assertEqual(sum(p["duration_s"] for p in live), 809)
        self.assertEqual(sum(p["acts"] for p in live), 33)
        self.assertAlmostEqual(sum(p["cost_usd"] for p in live), 0.320, places=3)
        v2v1 = next(c for c in s["cases"] if c["case"] == "V2-V1")
        self.assertEqual((v2v1["verdict"], v2v1["timing_s"], v2v1["budget_s"], v2v1["reasons"]), ("KO", 180.1, 60.0, ["timeout"]))
        self.assertEqual(next(c for c in s["cases"] if c["case"] == "N5-1")["reasons"], ["precondition"])
        self.assertEqual([x["case"] for x in s["selector_suspects"]], ["N1-1", "N1-3"])
        self.assertEqual([x["case"] for x in s["empty_without_llm"]], ["N9-2"])  # no call_llm: the product, not the selector
        self.assertEqual([(m["text"], m["count"], m["after_abort"]) for m in s["modal_refusals"]], [("Votre avis", 2, 2)])
        self.assertEqual(s["known_modals_dismissed"], {"avis": 3})
        self.assertEqual([(m["at"], m["task"]) for m in s["maintenance"]], [("driver.log:9", "task-node-1-39")])
        self.assertEqual(len(s["cut_suspects"]), 10)
        self.assertEqual([x["case"] for x in s["attachment_unseen"]], ["V2-L1", "V2-L2", "V2-L3", "V2-L4", "V2-V2"])
        self.assertEqual((s["header_in_answer"]["lines"], s["header_in_answer"]["of"]), (22, 27))
        self.assertEqual(s["sent_unobserved"], {"lines": 27, "of": 27})
        self.assertEqual(s["replays"], {"passes": 18, "acts": 99, "cost_usd": 1.198})
        self.assertEqual([(x["task"], x["level"], x["acts"], x["refusals_noted"]) for x in s["levels_without_artefact"]],
                         [("task-node-1-7", "N5", 4, None), ("task-node-1-16", "N5", 4, 4)])  # no console before 15:42
        self.assertEqual(len(s["hashes"]["inputs_sha256"]), 1)
        self.assertEqual(s["hashes"]["changes"], [])
        self.assertEqual(s["hashes"]["lines_without_ui_profile"], 39)
        self.assertEqual(s["constant_nodes"], {"anonymize": 0, "safety": 0})
        self.assertEqual(s["concurrent_on_failing"], [{"case": "N8-4", "at": "journal.jsonl:31", "pairs": ["V2-V1", "V2-V3"]}])
        self.assertEqual(s["composer"], [])  # the v1 lines of 17/10 carry no `config`
        self.assertEqual(len(s["relaunches"]), 4)

    def test_six_proposals_each_carrying_its_evidence_lines(self):
        got = [(p["kind"], p["target"], p["confidence"]) for p in self.p]
        self.assertEqual(got, [("selector", "assistant_message", "high"), ("modal", "Votre avis", "high"),
                               ("budget", "N4", "medium"), ("oracle", "answer_scope", "medium"),
                               ("recipe", "level_without_artefact", "high"), ("recipe", "maintenance", "high")])
        by = {(p["kind"], p["target"]): p for p in self.p}
        self.assertEqual([e["at"] for e in by["selector", "assistant_message"]["evidence"]],
                         ["journal-essai1.jsonl:3", "journal-essai1.jsonl:5"])
        self.assertIn("réponse vide : échec silencieux", by["selector", "assistant_message"]["evidence"][0]["text"])
        self.assertEqual([e["at"] for e in by["modal", "Votre avis"]["evidence"]], ["vkd-console.log:5", "vkd-console.log:6"])
        self.assertIn("modale inconnue", by["modal", "Votre avis"]["evidence"][0]["text"])
        self.assertEqual([e["at"] for e in by["budget", "N4"]["evidence"]], [f"journal.jsonl:{n}" for n in range(14, 19)])
        self.assertEqual([e["at"] for e in by["recipe", "maintenance"]["evidence"]], ["driver.log:9"])
        self.assertEqual(by["oracle", "answer_scope"]["evidence_total"], 22)
        self.assertEqual(len(by["oracle", "answer_scope"]["evidence"]), debrief.EVIDENCE_CAP)
        for p in self.p:
            self.assertTrue(p["evidence"], p)
            for e in p["evidence"]:
                self.assertEqual(set(e), {"at", "task", "text"})
                self.assertLessEqual(len(e["text"]), debrief.CLIP)
            self.assertEqual(p["proposed_by"], "debrief")
            self.assertIn("l'opérateur", p["human"])  # a proposal is never applied by the stage

    def test_each_manual_loop_of_17_10_had_its_answer_before_the_relaunch(self):
        """Fixture outcome (debrief §4, « Gain sur ce run »). Four manual loops on 17/10: the selector
        (t2 → t7), the relaunch without a fix (t7 → t16), the modal (t16 → t25), the judge's table
        (t25 → t39). A debrief run at the end of t2, t7 and t16 already held the proposal that answers
        each of the first three; the fourth needs S14 (the judge's claims against the journal), whose
        input — the judge's text — lives in the register, not in clear on the node: out of this stage.
        Three manual loops saved, one left."""
        def proposals_until(task):
            return {(p["kind"], p["target"], p["confidence"]) for p in debrief.debrief(RUN_17_10, until=task)[1]}
        self.assertEqual(proposals_until("task-node-1-2"), {("selector", "assistant_message", "high")})
        self.assertIn(("recipe", "level_without_artefact", "medium"), proposals_until("task-node-1-7"))
        after_t16 = proposals_until("task-node-1-16")
        self.assertIn(("modal", "Votre avis", "high"), after_t16)
        self.assertIn(("recipe", "level_without_artefact", "high"), after_t16)
        by = {(p["kind"], p["target"]): p for p in self.p}
        self.assertEqual(by["selector", "assistant_message"]["tasks"], ["task-node-1-2"])
        self.assertEqual(by["modal", "Votre avis"]["tasks"], ["task-node-1-16"])
        self.assertEqual(by["recipe", "level_without_artefact"]["tasks"], ["task-node-1-7", "task-node-1-16"])

    def test_the_markdown_is_sections_1_to_3_in_the_debrief_register(self):
        for heading in ("# Debrief automatique — maskai-1 (2026-10-17)", "## 1. Chronologie du run",
                        "## 2. Défauts révélés par le terrain", "## 3. Mesures utiles au prochain pilote"):
            self.assertIn(heading, self.md)
        self.assertNotIn("## 4.", self.md)
        self.assertNotIn("## 5.", self.md)
        self.assertIn("| task-node-1-25 | 15:59–16:25 |", self.md)
        for p in self.p:
            self.assertIn(f"{p['kind']} `{p['target']}` ({p['confidence']})", self.md)


class DebriefContractTest(unittest.TestCase):
    def test_a_clean_run_yields_no_proposal(self):
        run = debrief_fixtures.clean_run(Path(tempfile.mkdtemp()) / "run")
        s, p, _ = debrief.debrief(run)
        self.assertEqual(p, [])
        self.assertEqual(len(s["cases"]), 4)
        self.assertEqual(s["relaunches"], [])
        self.assertEqual(s["hashes"]["lines_without_ui_profile"], 0)

    def test_reproducible_bytes_and_nothing_written_outside_out(self):
        before = tree_sha(RUN_17_10)
        a, b = Path(tempfile.mkdtemp()) / "a", Path(tempfile.mkdtemp()) / "b"
        for out in (a, b):
            debrief.write(*debrief.debrief(RUN_17_10), out)
        for f in ("debrief.json", "proposals.json", "debrief.md"):
            self.assertEqual((a / f).read_bytes(), (b / f).read_bytes(), f)
            self.assertNotIn(b"\r\n", (a / f).read_bytes())
        self.assertEqual(sorted(x.name for x in a.iterdir()), ["debrief.json", "debrief.md", "proposals.json"])
        self.assertEqual(tree_sha(RUN_17_10), before, "the run directory is read, never written")
        self.assertNotIn(str(RUN_17_10.parent).encode(), (a / "debrief.json").read_bytes(), "no absolute path")

    def test_a_learned_driver_s_selector_fix_goes_to_the_deliberate_run(self):
        run = Path(tempfile.mkdtemp()) / "run"
        shutil.copytree(RUN_17_10, run)
        (run / "manifest.json").write_text(json.dumps({"name": "word-maison", "kind": "com"}), encoding="utf-8")
        p = {(x["kind"], x["target"]): x for x in debrief.debrief(run)[1]}
        self.assertIn("vk driver learn --deliberate", p["selector", "assistant_message"]["human"])
        self.assertIn("vk driver learn --deliberate", p["modal", "Votre avis"]["human"])
        self.assertNotIn("--deliberate", p["budget", "N4"]["human"])

    def test_refusals_are_one_line_with_a_remedy(self):
        empty = Path(tempfile.mkdtemp())
        with self.assertRaises(Refusal) as cm:
            debrief.debrief(empty)
        self.assertIn("no journal.jsonl", str(cm.exception))
        self.assertIn("; remedy: ", str(cm.exception))
        with self.assertRaises(Refusal) as cm:
            debrief.debrief(RUN_17_10, until="task-nope")
        self.assertIn("until: no task task-nope", str(cm.exception))
        used = Path(tempfile.mkdtemp())
        (used / "x").write_text("x")
        with self.assertRaises(Refusal) as cm:
            debrief.write(*debrief.debrief(RUN_17_10), used)
        self.assertIn("is not empty", str(cm.exception))

    def test_the_stage_is_registered_and_runs_from_the_cli_entry(self):
        from vkforge.__main__ import main
        out = Path(tempfile.mkdtemp()) / "out"
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            self.assertEqual(main(["debrief", "--run", str(RUN_17_10), "--out", str(out)]), 0)
        self.assertIn("debrief: 6 proposals", printed.getvalue())
        self.assertEqual(len(json.loads((out / "proposals.json").read_text(encoding="utf-8"))), 6)

    def test_harness_notes_are_read_through_ansi_colours(self):
        line = "\x1b[2m2026-10-17T13:49:14.302877Z\x1b[0m \x1b[32m INFO\x1b[0m vk_ipc::server: harness log task=t harness=x"
        m = debrief.NOTE.match(debrief.ANSI.sub("", line))
        self.assertEqual((m.group(2), m.group(3)), ("t", "x"))

    def test_a_correction_line_replaces_the_line_it_supersedes(self):
        """journal.py's corrections (step `correction`, `supersedes` = the 0-based index of the line
        it replaces), as the maskai-recette driver reads them: the correction is the case's verdict."""
        run = debrief_fixtures.clean_run(Path(tempfile.mkdtemp()) / "run")
        fix = {"ts": "2026-10-20T11:05+02:00", "step": "correction", "case": "N1-3", "verdict": "PARTIEL",
               "timing_s": 7.9, "evidence": "réponse : « Relancez par écrit. »", "supersedes": 3,
               "inputs_sha256": "sha256:" + "b" * 64, "ui_profile_sha256": "sha256:" + "a" * 64}
        with (run / "journal.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(fix, ensure_ascii=False) + "\n")
        cases = debrief.debrief(run)[0]["cases"]
        self.assertEqual([(c["case"], c["verdict"], c["at"]) for c in cases if c["case"] == "N1-3"],
                         [("N1-3", "PARTIEL", "journal.jsonl:5")])
        self.assertEqual(len(cases), 4)

    def test_an_unreadable_input_is_a_refusal_naming_its_line(self):
        run = debrief_fixtures.clean_run(Path(tempfile.mkdtemp()) / "run")
        with (run / "journal.jsonl").open("a", encoding="utf-8") as f:
            f.write('{"ts": "2026-10-20T11:06+02:00", "step": "exec", "case": "N2-1a", "verd\n')  # cut mid-write
        with self.assertRaises(Refusal) as cm:
            debrief.debrief(run)
        self.assertIn("journal.jsonl:5 is not JSON", str(cm.exception))
        self.assertIn("; remedy: ", str(cm.exception))

    def test_the_out_directory_is_never_the_run_directory_or_a_file(self):
        from vkforge.__main__ import main
        a_file = Path(tempfile.mkdtemp()) / "a-file"
        a_file.write_text("x")
        for out, said in ((RUN_17_10 / "debrief", "is inside the run directory"),
                          (a_file, "is not a directory")):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                code = main(["debrief", "--run", str(RUN_17_10), "--out", str(out)])
            self.assertEqual(code, 2)
            self.assertIn(said, err.getvalue())
            self.assertTrue(err.getvalue().startswith("vkforge debrief: "), err.getvalue())
        self.assertFalse((RUN_17_10 / "debrief").exists())


if __name__ == "__main__":
    unittest.main()
