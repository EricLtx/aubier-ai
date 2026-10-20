"""Tests de scripts/recette/correct.py (PROCEDURE-3.md §6, E8), sur un journal de banc :
python -I -m unittest discover -s scripts/recette -p "test_*.py"

Le journal du banc : N1-1 jugé KO par un évaluateur d'hier sur une réponse qui porte la valeur (ligne
écrite à la main), M0-3 et N3-2 jugés par le driver lui-même (M0-3 sur @model, son instantané gardé),
N7-1 jugé sur @list par un pilote v2.2 (sans instantané). Le driver est celui du dépôt, sans navigateur."""
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def load_correct():
    spec = importlib.util.spec_from_file_location("recette_correct", os.path.join(HERE, "correct.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def hand_line(case: str, verdict: str, evidence: str, **more) -> str:
    return json.dumps({"ts": "2026-10-18T18:01+02:00", "step": "exec", "case": case, "verdict": verdict,
                       "timing_s": 3.2, "evidence": evidence, "nfr": ["disponibilite", "performance"],
                       "inputs_sha256": "sha256:" + "0" * 64, "ui_profile_sha256": "sha256:" + "1" * 64, **more},
                      ensure_ascii=False) + "\n"


class CorrectTest(unittest.TestCase):
    def setUp(self):
        self.correct = load_correct()
        self.work = tempfile.mkdtemp(prefix="correct-work-")
        self.addCleanup(shutil.rmtree, self.work, True)
        self.journal = os.path.join(self.work, "journal.jsonl")
        with open(self.journal, "w", encoding="utf-8") as f:  # line 0: yesterday's wrong KO
            f.write(hand_line("N1-1", "KO", "manque : lisbonne ; réponse : « La capitale du Portugal est Lisbonne. »"))
        drv = self.correct.load_driver(self.correct.DEFAULT_DRIVER)
        profile = tempfile.mkdtemp(prefix="correct-profile-")
        self.addCleanup(shutil.rmtree, profile, True)
        recette = drv.Recette(env={"VK_DRIVER_WORK_DIR": self.work, "VK_DRIVER_PROFILE_DIR": profile,
                                   "VK_DRIVER_HOSTS": "app.maskai.fr"})
        self.inputs, self.ui = recette.inputs_sha256, recette.ui_profile_sha256
        recette.observations["N1-1"] = {
            "answer": "La capitale du Portugal est Lisbonne.", "timing_s": 3.2, "new_answer": True,
            "sent_to_model": "Quelle est la capitale du Portugal ? Réponds en une phrase.",
            "sent_info": {"source": "card-details"}, "guard": {"model": "google/eu.claude-haiku-5-5", "pii_count": 0},
            "composer": {"model": "google/eu.claude-haiku-5-5", "web": "off", "mic": "idle"}}
        recette.observations["N3-2"] = {"answer": "Il paie 2 505 € par trimestre.", "timing_s": 4.0}
        recette._shared("@model", {"answer": "options : claude haiku 5-5 | gemini 3.5 flash ; choisi : claude haiku 5-5 ; "
                                             "relu : google/eu.claude-haiku-5-5 ; micro : idle", "timing_s": 1.2})
        evidence = tempfile.mkdtemp(prefix="correct-ev-")
        self.addCleanup(shutil.rmtree, evidence, True)
        for cid in ("M0-3", "N3-2"):  # lines 1 and 2, by the driver itself
            recette.actions()["verdict_oracle"]({"case": cid}, evidence)
        recette.observations["N7-1"] = {"verdict": {"verdict": "OK"}, "from": "@list"}  # pilot v2.2: no snapshot
        # 18/10, M3: a banner without provider (« maskai claude-haiku-4-5 ») the driver of then did not read
        recette.observations["N3-1"] = {"answer": "Canberra, depuis 1913.", "timing_s": 2.0}
        recette.observations["M3-B1"] = {
            "answer": "Canberra, depuis 1913.", "timing_s": 4.0, "new_answer": True,
            "guard": {"model": None, "header": "Aucune donnée personnelle détectée L'Aiguilleur : PASS maskai "
                                               "claude-haiku-4-5"},
            "composer": {"model": "claude-haiku-4-5", "web": "off"}}
        recette._save_observations()
        with open(self.journal, "a", encoding="utf-8") as f:  # lines 3, 4 and 5
            f.write(hand_line("N7-1", "OK", "valeurs attendues présentes"))
            f.write(hand_line("M3-B1", "KO", "réponse signée « sans bannière », modèle choisi « claude-haiku-4-5 »",
                              timing_s=4.0))  # the observation's timing: the line it judged (robustness-5)
            # the same verdict and NFR as today's judgement, another evidence (m8)
            f.write(hand_line("N3-1", "OK", "une preuve d'hier", nfr=["veracite"], timing_s=2.0))

    def bytes(self) -> bytes:
        with open(self.journal, "rb") as f:
            return f.read()

    def lines(self) -> list:
        with open(self.journal, encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]

    def run_quiet(self, cases, write=False):
        return self.correct.run(self.work, cases, "oracle de N1-1 corrigé le 19/10", write_lines=write,
                                out=io.StringIO())

    def test_a_dry_run_says_what_it_would_write_and_touches_nothing(self):
        before = self.bytes()
        items, code = self.run_quiet(["N1-1", "N3-2", "M0-3", "N7-1", "X9-9"])
        self.assertEqual(self.bytes(), before, "a dry run writes nothing")
        status = {i["case"]: i["status"] for i in items}
        self.assertEqual(status["N1-1"], "à corriger")
        self.assertTrue(status["N3-2"].startswith("inchangé"))
        self.assertTrue(status["M0-3"].startswith("inchangé"), "a shared observation is replayed from its snapshot")
        self.assertIn("sans instantané", status["N7-1"])
        self.assertIn("cas inconnu", status["X9-9"])
        self.assertEqual(code, 1, "a case that cannot be replayed")
        line = next(i for i in items if i["case"] == "N1-1")["line"]
        self.assertEqual((line["step"], line["verdict"], line["supersedes"], line["reason"]),
                         ("correction", "OK", 0, "oracle de N1-1 corrigé le 19/10"))
        self.assertEqual((line["inputs_sha256"], line["ui_profile_sha256"]), (self.inputs, self.ui))
        self.assertEqual(line["config"]["mic"], "idle")

    def test_write_appends_one_correction_line_that_journal_effective_applies(self):
        items, code = self.run_quiet(["N1-1", "N3-2"], write=True)
        self.assertEqual(code, 0)
        lines = self.lines()
        self.assertEqual(len(lines), 7, "one line, for the one case that changes")
        new = lines[-1]
        self.assertEqual((new["step"], new["case"], new["verdict"], new["supersedes"]), ("correction", "N1-1", "OK", 0))
        self.assertEqual(new["reason"], "oracle de N1-1 corrigé le 19/10")
        self.assertEqual((new["inputs_sha256"], new["ui_profile_sha256"]), (self.inputs, self.ui),
                         "the hashes of the rules that judge now, as on an exec line")
        self.assertIn("ts", new)
        journal = self.correct.load_driver(self.correct.DEFAULT_DRIVER).journal
        effective = {e["case"]: e["verdict"] for e in journal.effective(lines)}
        self.assertEqual(effective["N1-1"], "OK")
        # Again: the correction is in force now, nothing more to write.
        items, _ = self.run_quiet(["N1-1"], write=True)
        self.assertTrue(items[0]["status"].startswith("inchangé"))
        self.assertEqual(items[0]["supersedes"], 6, "the line in force is the correction")
        self.assertEqual(len(self.lines()), 7)

    def test_the_stored_header_is_read_again_with_the_rules_of_today_on_request(self):
        """The 18/10 correction of M3: without --reread-header the stored observation is judged as it is
        (KO again); with it, the banner is read in the stored header (OK); observations.json is never
        written."""
        observations = os.path.join(self.work, "observations.json")
        with open(observations, "rb") as f:
            before = f.read()
        items, _ = self.run_quiet(["M3-B1"])
        self.assertEqual(items[0]["line"]["verdict"], "KO", "judged as stored: no banner read")
        self.assertNotIn("header_reread", items[0])
        items, code = self.correct.run(self.work, ["M3-B1"], "bannière relue", write_lines=True, out=io.StringIO(),
                                       reread=True)
        self.assertEqual((code, items[0]["status"], items[0]["header_reread"]), (0, "à corriger", True))
        new = self.lines()[-1]
        self.assertEqual((new["case"], new["verdict"], new["supersedes"], new["config"]["banner"]),
                         ("M3-B1", "OK", 4, "claude-haiku-4-5"))
        with open(observations, "rb") as f:
            self.assertEqual(f.read(), before, "the observations are never written")

    def test_m8_an_evidence_only_change_is_written_only_on_request(self):
        """m8: N3-1's line differs from today's judgement by its evidence alone: no correction by default
        (every replayed b-passage would get one), one with `evidence=True` (--evidence)."""
        items, _ = self.run_quiet(["N3-1"], write=True)
        self.assertTrue(items[0]["status"].startswith("inchangé"), items[0]["status"])
        self.assertEqual(len(self.lines()), 6)
        items, code = self.correct.run(self.work, ["N3-1"], "preuve réécrite", write_lines=True, out=io.StringIO(),
                                       evidence=True)
        self.assertEqual((code, items[0]["status"]), (0, "à corriger"))
        self.assertEqual((self.lines()[-1]["case"], self.lines()[-1]["supersedes"]), ("N3-1", 5))

    def test_m8_observations_set_aside_at_a_resumption_still_replay(self):
        """m8: a resumption sets observations.json aside (runbook §12): the cases journaled before it replay
        from that file (`observations=`, --observations), and the error says so."""
        observations = os.path.join(self.work, "observations.json")
        aside = os.path.join(self.work, "observations-essai1.json")
        os.replace(observations, aside)
        items, code = self.run_quiet(["N1-1"])
        self.assertEqual(code, 1)
        self.assertIn("--observations", items[0]["status"])
        items, code = self.correct.run(self.work, ["N1-1"], "reprise", out=io.StringIO(), observations=aside)
        self.assertEqual((code, items[0]["status"], items[0]["line"]["verdict"]), (0, "à corriger", "OK"))

    def test_robustness_5_an_observation_that_is_not_the_line_s_is_never_replayed(self):
        """robustness-5 (review of v2.3, round 2): a resumption, M9-1 asked again and journaled OK by the driver
        (its observation stamped with that line); the observation set aside at the resumption is the attempt
        before, never judged: refused, never a KO correction. The stamped one replays unchanged."""
        drv = self.correct.load_driver(self.correct.DEFAULT_DRIVER)
        aside = os.path.join(self.work, "observations-essai1.json")
        with open(os.path.join(self.work, "observations.json"), encoding="utf-8") as f:
            before = json.load(f)
        before["M9-1"] = {"answer": "Je ne sais pas.", "timing_s": 5.0, "new_answer": True}  # never judged
        with open(aside, "w", encoding="utf-8") as f:
            json.dump(before, f, ensure_ascii=False)
        profile = tempfile.mkdtemp(prefix="correct-profile-")
        self.addCleanup(shutil.rmtree, profile, True)
        recette = drv.Recette(env={"VK_DRIVER_WORK_DIR": self.work, "VK_DRIVER_PROFILE_DIR": profile,
                                   "VK_DRIVER_HOSTS": "app.maskai.fr"})
        recette.observations["M9-1"] = {"answer": "Le dépôt de garantie est limité à un mois de loyer hors charges "
                                                  "(loi du 6 juillet 1989, article 22).", "timing_s": 2.8, "new_answer": True}
        evidence = tempfile.mkdtemp(prefix="correct-ev-")
        self.addCleanup(shutil.rmtree, evidence, True)
        recette.actions()["verdict_oracle"]({"case": "M9-1"}, evidence)
        self.assertEqual(self.lines()[-1]["verdict"], "OK")
        out = io.StringIO()
        items, code = self.correct.run(self.work, ["M9-1"], "essai", observations=aside, out=out)
        self.assertEqual(code, 1)
        self.assertTrue(items[0]["status"].startswith("erreur"), items[0]["status"])
        self.assertIn("pas celle de la ligne en vigueur", items[0]["status"])
        self.assertIsNone(items[0]["line"])
        items, code = self.correct.run(self.work, ["M9-1"], "essai", out=io.StringIO())
        self.assertEqual((code, items[0]["status"]), (0, "inchangé : rien à corriger"))

    def test_m8_an_unstamped_observation_of_this_pilot_is_never_taken_for_an_older_one(self):
        """M-8 (review of round 2): an observation set aside at a resumption, never judged, that carries this
        pilot's keys (`notices`) and the very timing of the line in force: refused, never replayed."""
        aside = os.path.join(self.work, "observations-essai1.json")
        with open(os.path.join(self.work, "observations.json"), encoding="utf-8") as f:
            before = json.load(f)
        before["N3-1"] = {"answer": "Je ne sais pas.", "timing_s": 2.0, "new_answer": True, "notices": None}
        with open(aside, "w", encoding="utf-8") as f:
            json.dump(before, f, ensure_ascii=False)
        items, code = self.correct.run(self.work, ["N3-1"], "essai", observations=aside, out=io.StringIO())
        self.assertEqual(code, 1)
        self.assertIn("pas celle de la ligne en vigueur", items[0]["status"])

    def test_the_command_line(self):
        before = self.bytes()
        out = subprocess.run([sys.executable, "-I", os.path.join(HERE, "correct.py"), self.work, "--cases", "N1-1",
                              "--reason", "essai"], capture_output=True, text=True, encoding="utf-8", timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("N1-1 : à corriger (ligne en vigueur 0, KO)", out.stdout)
        self.assertIn("KO → OK", out.stdout)
        self.assertEqual(self.bytes(), before)
        out = subprocess.run([sys.executable, "-I", os.path.join(HERE, "correct.py"), self.work, "--cases", "N7-1",
                              "--reason", "essai"], capture_output=True, text=True, encoding="utf-8", timeout=120)
        self.assertEqual(out.returncode, 1)


if __name__ == "__main__":
    unittest.main()
