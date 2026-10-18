import contextlib
import hashlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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


def stage(run: Path, out: Path = None, until: str = None) -> tuple:
    """The stage through its CLI entry, as the kernel spawns it: (exit code, stderr)."""
    from vkforge.__main__ import main
    argv = ["debrief", "--run", str(run), "--out", str(out or Path(tempfile.mkdtemp()) / "out")]
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        code = main(argv + (["--until", until] if until else []))
    return code, err.getvalue()


def task_dump(run: Path) -> Path:
    return run / "tasks" / "task-node-1-50.json"


def edit_json(path: Path, change) -> None:
    doc = json.loads(path.read_text(encoding="utf-8"))
    new = change(doc)
    path.write_text(json.dumps(doc if new is None else new), encoding="utf-8")


def edit_line(path: Path, n: int, change) -> None:
    """Line n (1-based) of a JSON-lines file, changed by `change(event) -> event`."""
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[n - 1] = json.dumps(change(json.loads(lines[n - 1])), ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def drop(key):
    def change(doc):
        doc.pop(key)
        return doc
    return change


def with_cases(run: Path, budget) -> None:
    (run / "cases.json").write_text(json.dumps({"cases": {"N1-1": {"level": "N1", "kind": "message",
                                                                    "budget_s": budget}}}), encoding="utf-8")


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

    def test_the_evidence_carries_no_answer_no_sent_text_and_no_page_text(self):
        """Ruling L-P57: proposals.json's evidence is cut before « réponse : « », « texte envoyé » and
        the oracle's must-patterns (« manque : »); a driver.log line keeps the driver's own words;
        a harness note, the operator's own vk_log line, is kept as it is."""
        s, p, _ = debrief.debrief(RUN_17_10)
        texts = [e["text"] for x in p for e in x["evidence"]]
        for cut in ("réponse : «", "texte envoyé", "manque :", "Aucune donnée personnelle", "MAINTENANCE EN COURS"):
            self.assertFalse([t for t in texts if cut in t], cut)
        by = {(x["kind"], x["target"]): x for x in p}
        scope = by["oracle", "answer_scope"]["evidence"][0]
        self.assertIn('"evidence": "[…]"', scope["text"])  # the line's fields stay, its quoted text goes
        self.assertIn('"case": "N1-1"', scope["text"])
        self.assertEqual(by["recipe", "maintenance"]["evidence"][0]["text"], "WARNING service in maintenance […]")
        self.assertIn("modale inconnue : Votre avis — Aidez-nous", by["modal", "Votre avis"]["evidence"][0]["text"])
        md = debrief.debrief(RUN_17_10)[2]
        self.assertNotIn("réponse : «", md)


# Each probe breaks one thing in a copy of the clean run; each must end in one refusal line naming
# the file and the field (L-P15), never a traceback (review I1, I2, M3).
PROBES = [
    ("a task dump without steps", lambda r: edit_json(task_dump(r), drop("steps")),
     "tasks/task-node-1-50.json: no field steps"),
    ("a task dump without created_ms", lambda r: edit_json(task_dump(r), drop("created_ms")),
     "tasks/task-node-1-50.json: no field created_ms"),
    ("a task dump without id", lambda r: edit_json(task_dump(r), drop("id")),
     "tasks/task-node-1-50.json: no field id"),
    ("a task dump that is a list", lambda r: edit_json(task_dump(r), lambda d: [d]),
     "tasks/task-node-1-50.json is not a JSON object"),
    ("a step kind that is a string", lambda r: edit_json(task_dump(r), lambda d: d["steps"][0].update(kind="plan")),
     "tasks/task-node-1-50.json steps[0]: kind is not object"),
    ("a journal line without ts", lambda r: edit_line(r / "journal.jsonl", 1, drop("ts")),
     "journal.jsonl:1: no field ts"),
    ("a journal line whose ts is not a time", lambda r: edit_line(r / "journal.jsonl", 1, lambda e: dict(e, ts="hier")),
     "journal.jsonl:1: ts 'hier' is not an ISO 8601 time"),
    ("a naive ts", lambda r: edit_line(r / "journal.jsonl", 1, lambda e: dict(e, ts="2026-10-20T11:01")),
     "journal.jsonl:1: ts 2026-10-20T11:01 has no UTC offset"),
    ("an exec line without case", lambda r: edit_line(r / "journal.jsonl", 2, drop("case")),
     "journal.jsonl:2: no field case"),
    ("an exec line without verdict", lambda r: edit_line(r / "journal.jsonl", 2, drop("verdict")),
     "journal.jsonl:2: no field verdict"),
    ("a journal line that is a list", lambda r: edit_line(r / "journal.jsonl", 2, lambda e: [e]),
     "journal.jsonl:2 is not a JSON object"),
    ("a pipeline_ms value that is text", lambda r: edit_line(
        r / "journal.jsonl", 2, lambda e: dict(e, pipeline_ms=dict(e["pipeline_ms"], call_llm="1840"))),
     "journal.jsonl:2: pipeline_ms.call_llm is not a number"),
    ("dmesg.json that is an object", lambda r: (r / "dmesg.json").write_text('{"rows": []}', encoding="utf-8"),
     "dmesg.json is not a JSON list"),
    ("a dmesg row without wall_ms", lambda r: edit_json(r / "dmesg.json", lambda rows: [drop("wall_ms")(rows[0])] + rows[1:]),
     "dmesg.json[0]: no field wall_ms"),
    ("cases.json without cases", lambda r: (r / "cases.json").write_text('{"N1-1": {}}', encoding="utf-8"),
     "cases.json: no field cases"),
    ("a budget_s that is text", lambda r: with_cases(r, "10"), "cases.json N1-1: budget_s is not number or null"),
    ("manifest.json that is a list", lambda r: (r / "manifest.json").write_text("[]", encoding="utf-8"),
     "manifest.json is not a JSON object"),
    ("a harness note whose time is not ISO", lambda r: (r / "vkd-console.log").write_text(
        "hier INFO vk_ipc::server: harness log task=task-node-1-50 harness=N1 : x\n", encoding="utf-8"),
     "vkd-console.log:1: time 'hier' is not an ISO 8601 time"),
    ("a driver.log line whose date does not exist", lambda r: (r / "driver.log").write_text(
        "2026-13-45 11:00:12,004 INFO maskai-recette browser up\n", encoding="utf-8"),
     "driver.log:1: time 2026-13-45 11:00:12 is not a date"),
    ("a task dump written by > in Windows PowerShell 5.1", lambda r: task_dump(r).write_bytes(
        task_dump(r).read_text(encoding="utf-8").encode("utf-16")),
     "tasks/task-node-1-50.json is UTF-16; remedy: rewrite it with Out-File -Encoding utf8"),
    ("a journal written by >", lambda r: (r / "journal.jsonl").write_bytes(
        (r / "journal.jsonl").read_text(encoding="utf-8").encode("utf-16")),
     "journal.jsonl is UTF-16; remedy: rewrite it with Out-File -Encoding utf8"),
    ("a journal in another code page", lambda r: (r / "journal.jsonl").write_bytes(
        (r / "journal.jsonl").read_text(encoding="utf-8").encode("cp1252")),
     "journal.jsonl is not UTF-8"),
    ("an empty journal", lambda r: (r / "journal.jsonl").write_text("\n", encoding="utf-8"),
     "journal.jsonl is empty; remedy: a run that journaled at least one case"),
    ("no task dump", lambda r: task_dump(r).unlink(),
     "remedy: vk task show TASK --json | Out-File -Encoding utf8 tasks/TASK.json"),
]


class DebriefInputTest(unittest.TestCase):
    """What a real node hands the stage: malformed copies, other encodings, append-only logs."""

    def test_every_malformed_input_is_one_refusal_line_naming_the_file_and_the_field(self):
        self.assertGreaterEqual(len(PROBES), 15)
        for label, breaks, said in PROBES:
            with self.subTest(label):
                run = debrief_fixtures.clean_run(Path(tempfile.mkdtemp()) / "run")
                breaks(run)
                code, err = stage(run)
                self.assertEqual(code, 2, err)
                self.assertTrue(err.startswith("vkforge debrief: "), err)
                self.assertEqual(err.count("\n"), 1, err)
                self.assertIn("; remedy: ", err)
                self.assertIn(said, err)

    def test_the_last_resort_turns_anything_else_into_one_line(self):
        run = debrief_fixtures.clean_run(Path(tempfile.mkdtemp()) / "run")
        with mock.patch.object(debrief, "signals", side_effect=KeyError("steps")):
            code, err = stage(run)
        self.assertEqual((code, err), (2, "vkforge debrief: the run directory: KeyError: 'steps'; "
                                          "remedy: check the run directory's files\n"))

    def test_append_only_logs_are_bounded_to_the_run(self):
        """The console, driver.log, the journal and vk dmesg hold other runs' lines: a note of a task
        that is not the run's, a line of another day, are not read (review I3)."""
        run = debrief_fixtures.clean_run(Path(tempfile.mkdtemp()) / "run")
        foreign = [
            "2026-10-17T13:49:14.302877Z  INFO vk_ipc::server: harness log task=task-node-1-16 harness=N5 : "
            "nouvelle_conversation refusée (dialog) : modale inconnue : Votre avis — Aidez-nous",
            "2026-10-17T13:49:22.640019Z  INFO vk_ipc::server: harness log task=task-node-1-16 harness=N5 : "
            "capture_ecran refusée (dialog) : modale inconnue : Votre avis — Aidez-nous",
            "2026-10-21T09:02:40.000001Z  INFO vk_ipc::server: harness log task=task-node-1-50 harness=N1 : "
            "verdict_oracle refusé (precondition) : rejeu du lendemain"]
        console = run / "vkd-console.log"
        console.write_text("\n".join(foreign) + "\n" + console.read_text(encoding="utf-8"), encoding="utf-8")
        log = run / "driver.log"
        log.write_text(log.read_text(encoding="utf-8") + "2026-10-21 10:29:14,207 WARNING maskai-recette service "
                       "in maintenance: MAINTENANCE EN COURS\n", encoding="utf-8")
        with (run / "journal.jsonl").open("a", encoding="utf-8") as f:
            for case in ("N1-1", "N1-3"):
                f.write(json.dumps({"ts": "2026-10-19T15:24+02:00", "step": "exec", "case": case, "verdict": "KO",
                                    "timing_s": 5.0, "evidence": "réponse vide : échec silencieux",
                                    "pipeline_ms": {"call_llm": 2444}}, ensure_ascii=False) + "\n")
        edit_json(run / "dmesg.json", lambda rows: rows + [{"seq": 99, "kind": "driver.acted", "wall_ms": 1}])
        s, p, _ = debrief.debrief(run)
        self.assertEqual(p, [])
        self.assertEqual(s["refusals"], {"timeout": 0, "dialog": 0, "precondition": 0, "host": 0, "internal": 0})
        self.assertEqual((s["maintenance"], s["selector_suspects"], s["modal_refusals"]), ([], [], []))
        self.assertEqual(len(s["cases"]), 4)
        self.assertEqual(s["acts_total"], 12)
        self.assertEqual(s["run"]["console_from_ms"], debrief_fixtures.ms("11:02:40", "2026-10-20"))
        self.assertEqual((s["run"]["from_ms"], s["run"]["to_ms"]),
                         (debrief_fixtures.ms("11:00:00", "2026-10-20"), debrief_fixtures.ms("11:04:40", "2026-10-20")))

    def test_line_references_count_blank_lines_and_supersedes_does_not(self):
        """`file:line` is the physical line, as an editor shows it (review M2); journal.py's
        `supersedes` indexes the non-blank lines."""
        run = debrief_fixtures.clean_run(Path(tempfile.mkdtemp()) / "run")
        journal = run / "journal.jsonl"
        lines = journal.read_text(encoding="utf-8").splitlines()
        fix = {"ts": "2026-10-20T11:04+02:00", "step": "correction", "case": "N1-3", "verdict": "PARTIEL",
               "timing_s": 7.9, "evidence": "réponse : « Relancez. »", "supersedes": 3}
        journal.write_text("\n".join(lines[:1] + [""] + lines[1:] + [json.dumps(fix, ensure_ascii=False)]) + "\n",
                           encoding="utf-8")
        cases = debrief.debrief(run)[0]["cases"]
        self.assertEqual([(c["case"], c["at"]) for c in cases],
                         [("N0-1", "journal.jsonl:1"), ("N1-1", "journal.jsonl:3"), ("N1-2", "journal.jsonl:4"),
                          ("N1-3", "journal.jsonl:6")])
        self.assertEqual(cases[-1]["verdict"], "PARTIEL")

    def test_a_bar_in_a_cell_is_escaped(self):
        run = debrief_fixtures.clean_run(Path(tempfile.mkdtemp()) / "run")
        with (run / "vkd-console.log").open("a", encoding="utf-8") as f:
            f.write("2026-10-20T09:03:00.000001Z  INFO vk_ipc::server: harness log task=task-node-1-50 harness=N1 : "
                    "nouvelle_conversation refusée (dialog) : modale inconnue : A | B\n")
        md = debrief.debrief(run)[2]
        self.assertIn("« A \\| B » × 1", md)
        self.assertIn("modal `A \\| B` (medium)", md)


if __name__ == "__main__":
    unittest.main()
