"""The maskai-recette driver on its fixture page, in headless Chromium.

The fixture server (fixture/serve.py) runs on a free port; each test builds a
`Recette` over its own work and profile directories, as the host would, and
calls the actions directly (the protocol loop itself is tested in
drivers/vkdriver/test_protocol.py).
"""
import base64
import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

TESTS = os.path.dirname(os.path.abspath(__file__))
DRIVER_DIR = os.path.dirname(TESTS)

try:
    import playwright.sync_api  # noqa: F401
    HAVE_PLAYWRIGHT = True
except ImportError:
    HAVE_PLAYWRIGHT = False
NO_PLAYWRIGHT = ("playwright is not importable here: run drivers/maskai-recette/setup.ps1 and "
                 "run the tests with drivers/maskai-recette/.venv/Scripts/python.exe")

_instances = 0


def load_driver():
    """A fresh module instance of driver.py: what a relaunched driver process would load."""
    global _instances
    _instances += 1
    spec = importlib.util.spec_from_file_location(f"maskai_recette_driver_{_instances}",
                                                  os.path.join(DRIVER_DIR, "driver.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def journal_lines(work) -> list:
    path = os.path.join(work, "journal.jsonl")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def manifest_evidence() -> dict:
    with open(os.path.join(DRIVER_DIR, "manifest.json"), encoding="utf-8") as f:
        return {a["name"]: set(a["evidence"]) for a in json.load(f)["actions"]}


class CasesTest(unittest.TestCase):
    """cases.json covers the grids the recipes run, N0–N9 and M0–M7 (no browser needed)."""

    def test_cases_cover_n0_to_n9_and_m0_to_m7(self):
        drv = load_driver()
        cases = drv.load_cases()
        levels = {}
        for c in cases.values():
            levels.setdefault(c["level"], []).append(c)
            for key in ("id", "level", "kind", "nfr", "budget_s"):
                self.assertIn(key, c, c["id"])
            self.assertIn(c["kind"], ("load", "message", "attach_question", "list", "concurrent",
                                      "model", "web", "long"), c["id"])
            self.assertLessEqual(set(c["nfr"]), set(drv.journal.NFR), c["id"])
            if c["kind"] in ("message", "attach_question"):
                self.assertIn("question", c, c["id"])
            if c["kind"] in ("model", "web") and "question" not in c:  # a configuration case: an oracle
                self.assertTrue(c["must"], c["id"])
            if c["kind"] == "model":
                self.assertTrue(c["model"], c["id"])
            if c["kind"] == "web":
                self.assertIn(c["web"], drv.WEB_MODES, c["id"])
            if c["kind"] == "long":
                self.assertGreaterEqual(len(drv.long_questions(c, c["turns"])), 2, c["id"])
            if c["kind"] == "attach_question":
                drv.Recette._resolve(c["file"])
            if c.get("same_conversation_as"):
                self.assertIn(c["same_conversation_as"], cases, c["id"])
            for other in c.get("cases", []):
                self.assertIn(other, cases, c["id"])
            if c.get("replay_of"):
                first = cases[c["replay_of"]]
                self.assertEqual({k: v for k, v in first.items() if k not in ("id", "title")},
                                 {k: v for k, v in c.items() if k not in ("id", "title", "replay_of")},
                                 f"{c['id']} replays {first['id']} with the same entry")
        for n in range(10):
            self.assertGreaterEqual(len(levels.get(f"N{n}", [])), 2, f"N{n}")
        for n in range(8):  # PROCEDURE-2 §3: 61 cases, M5 has one
            self.assertGreaterEqual(len(levels.get(f"M{n}", [])), 1, f"M{n}")
        self.assertEqual(sum(len(v) for k, v in levels.items() if k.startswith("M")), 61)
        self.assertEqual(len([c for c in levels["M1"] if c.get("replay_of")]), 12)
        oracle_m = {c["oracle"] for c in cases.values() if c.get("oracle") and c["level"].startswith("M")}
        self.assertEqual(oracle_m, {f"V3-{x}{n}" for x in "JC" for n in range(1, 7)} | {f"DOC-{n:02d}" for n in range(1, 11)})
        oracle = sorted(c["id"] for c in cases.values() if c.get("oracle") and c["level"].startswith("N"))
        self.assertEqual(oracle, sorted(f"V2-{k}" for k in ("L1", "L2", "L3", "L4", "L5", "V1", "V2", "V3", "V4", "V5")))
        self.assertTrue(all(cases[i]["level"] in ("N4", "N6") for i in oracle))
        self.assertGreaterEqual(len([c for c in levels["N8"] if c["kind"] == "message"]), 3)
        for fid in ("fixture-case-1", "fixture-case-2"):
            self.assertTrue(cases[fid]["file"].startswith("fixture/corpus/"))
        # v2: one case of each new kind, at the level whose question it asks
        self.assertEqual({c["kind"]: c["level"] for c in cases.values()
                          if c["kind"] in ("model", "web", "long") and c["level"].startswith("N")
                          and not c["id"].startswith("fixture-")},
                         {"model": "N5", "web": "N1", "long": "N2"})
        self.assertEqual(cases["fixture-case-3"]["turns"], 20, "the dry run's 20-turn conversation")

    def test_an_unknown_nfr_is_refused_at_start(self):
        drv = load_driver()
        path = os.path.join(tempfile.mkdtemp(prefix="maskai-recette-"), "cases.json")
        self.addCleanup(shutil.rmtree, os.path.dirname(path), True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"cases": [{"id": "x", "level": "N1", "kind": "message", "question": "q",
                                  "nfr": ["securité"], "budget_s": 1}]}, f)
        with self.assertRaises(ValueError) as refused:
            drv.load_cases(drv.Path(path))
        self.assertIn("securité", str(refused.exception))


class PumpTest(unittest.TestCase):
    """The idle pump over stand-in objects (no browser needed)."""

    def test_an_error_without_a_message_does_not_break_the_pump(self):
        drv = load_driver()

        class Page:
            def is_closed(self):
                return False

            def wait_for_timeout(self, ms):
                raise drv.PWError("")

        class Context:
            pages = []

        b = drv.Browser("profile", None, True, ["127.0.0.1"])
        page = Page()
        b.context, b._page = Context(), page
        b.pump()  # an empty message once broke the log line (IndexError)
        self.assertIn(page, b._broken)
        self.assertIsNone(b._page)


class EvidenceJsonTest(unittest.TestCase):
    """Evidence.json under its cap, over plain values (no browser needed)."""

    def test_halving_the_longest_list_keeps_json_when_it_suffices(self):
        drv = load_driver()
        with tempfile.TemporaryDirectory() as d:
            ev = drv.Evidence(d)
            ev.json("network.json", {"events": [f"e{i:04d}" for i in range(1000)], "n": 1}, cap=2000)
            with open(os.path.join(d, "network.json"), "rb") as f:
                data = f.read()
            self.assertLessEqual(len(data), 2000)
            obj = json.loads(data)
            self.assertTrue(obj["truncated"])
            self.assertLess(len(obj["events"]), 1000)
            self.assertEqual(obj["events"][-1], "e0999", "the newest entries are kept")

    def test_a_list_that_cannot_shrink_ends_in_a_hard_cut(self):
        # Final review M2: the longest list of length 1 halves to itself, and
        # the loop never ended. Now a round that shrank nothing stops it, and
        # the text is cut under the cap with a marker.
        drv = load_driver()
        with tempfile.TemporaryDirectory() as d:
            ev = drv.Evidence(d)
            ev.json("network.json", {"events": ["x" * 5000], "n": 1}, cap=1000)
            with open(os.path.join(d, "network.json"), "rb") as f:
                data = f.read()
            self.assertLessEqual(len(data), 1000)
            self.assertIn("tronqué".encode("utf-8"), data)
            self.assertEqual([i["name"] for i in ev.items], ["network.json"])
            self.assertEqual(ev.dropped, [])


class ChipStateTest(unittest.TestCase):
    """The attachment chip's state machine (D7), over plain readings (no browser needed)."""

    def test_readings_to_raw_states(self):
        drv = load_driver()
        name = "upload_errors.csv"  # a name full of state words: they say nothing of the state

        def raw(text, labels=(), busy=False, attr=""):
            return drv.chip_state({"text": text, "labels": list(labels), "busy": busy, "attr": attr}, name)

        self.assertEqual(drv.chip_state(None, name), "absent")
        self.assertEqual(raw(f"{name} Uploading…"), "uploading")
        self.assertEqual(raw(f"{name} Indexing in progress"), "indexing")
        self.assertEqual(raw(name, busy=True), "indexing")
        self.assertEqual(raw(f"{name} Failed", labels=["Failed"]), "failed")
        self.assertEqual(raw(name, attr="error"), "failed")
        self.assertEqual(raw(f"{name} Ready"), "ready")
        self.assertEqual(raw(name, labels=[f"Retirer {name}"]), "unmarked",
                         "the « Retirer <file> » button is no state (D7 read it as attached)")

    def test_the_state_machine(self):
        drv = load_driver()
        w = drv.ChipWatch(quiet_s=2.0)
        for t, raw in ((0.0, "absent"), (0.1, "uploading"), (0.4, "indexing"), (0.9, "indexing")):
            w.feed(raw, t)
        self.assertEqual(w.state, "indexing")
        self.assertEqual((w.feed("unmarked", 1.2), w.ready_by), ("ready", "after_pending"))
        self.assertEqual([x["state"] for x in w.trace], ["uploading", "indexing", "ready"])
        q = drv.ChipWatch(quiet_s=2.0)
        self.assertEqual(q.feed("unmarked", 0.1), "present", "never ready at once (D7)")
        self.assertEqual(q.feed("unmarked", 1.0), "present")
        self.assertEqual((q.feed("unmarked", 2.2), q.ready_by), ("ready", "quiet"))
        f = drv.ChipWatch()
        f.feed("failed", 0.3)
        self.assertEqual(f.feed("unmarked", 5.0), "failed", "failed is final")
        m = drv.ChipWatch()
        self.assertEqual((m.feed("ready", 0.2), m.ready_by), ("ready", "marker"))


class GuardParseTest(unittest.TestCase):
    """The answer card's header as the 17/10 UI showed it (D5), from the published journal."""

    def test_headers_seen_on_2026_10_17(self):
        drv = load_driver()
        body = "La capitale du Portugal est Lisbonne."
        g = drv.parse_guard("Aucune donnée personnelle détectée\nL'Aiguilleur : PASS\nmaskai "
                            "google/eu.claude-haiku-5-5\n" + body, body, True)
        self.assertEqual(g["model"], "google/eu.claude-haiku-5-5")
        self.assertEqual(g["guard_text"], "Aucune donnée personnelle détectée L'Aiguilleur : PASS")
        self.assertIs(g["pii_detected"], False)
        self.assertFalse(g["live"])
        g = drv.parse_guard("Anonymisé → <PERSON_1> <CREDIT_CARD_1> L'Aiguilleur : PASS·Le Chirurgien : 2 PII "
                            "détectées Détails maskai google/eu.claude-haiku-5-5 noté", "noté", True)
        self.assertIs(g["pii_detected"], True)
        self.assertEqual(g["tokens"], ["<PERSON_1>", "<CREDIT_CARD_1>"])
        self.assertTrue(g["guard_text"].endswith("2 PII détectées Détails"), g)
        g = drv.parse_guard("Aucune donnée personnelle détectée L'Aiguilleur : PASS maskai google/eu.claude-haiku-5-5 "
                            "· en direct Voici trois conseils", "Voici trois conseils", True)
        self.assertTrue(g["live"], "« · en direct » after the banner: the answer was still being drawn")
        g = drv.parse_guard("Mis à jour le 17/10 L'Aiguilleur : PASS maskai google/eu.claude-haiku-5-5 OK", "OK", True)
        self.assertEqual(g["model"], "google/eu.claude-haiku-5-5", "a date is never the model")
        g = drv.parse_guard("Mis à jour le 17/10 L'Aiguilleur : PASS OK", "OK", True)
        self.assertIsNone(g["model"])
        g = drv.parse_guard("PASS", "PASS", True)  # a body that also reads like a header word
        self.assertIsNone(g["model"])
        self.assertEqual(g["header"], "")


class SentTextTest(unittest.TestCase):
    """Which popover text counts as the sent text (no browser needed)."""

    def test_the_popover_must_carry_the_question(self):
        drv = load_driver()
        q = "Rédige une relance courtoise pour M. Hugo Bernard au sujet du loyer."
        self.assertTrue(drv.looks_like_sent("Rédige une relance courtoise pour <PERSON_1> au sujet du loyer.", q))
        self.assertTrue(drv.looks_like_sent(q, q))
        self.assertFalse(drv.looks_like_sent("Encrypted — no PII detected", q))
        self.assertFalse(drv.looks_like_sent("", q))
        self.assertFalse(drv.looks_like_sent(None, q))
        # N9-1, fully masked: its inventory's own words are not counted against the masked text
        n91 = drv.load_cases()["N9-1"]
        masked = ("Rédige une relance courtoise pour <PERSON_1> (<IBAN_1>) au sujet d'un loyer impayé de 940 €.")
        self.assertFalse(drv.looks_like_sent(masked.replace("courtoise", "aimable").replace("sujet", "propos"),
                                             n91["question"]), "without the inventory taken out: rejected")
        self.assertTrue(drv.looks_like_sent(masked.replace("courtoise", "aimable").replace("sujet", "propos"),
                                            n91["question"], n91["pii"]))
        self.assertTrue(drv.looks_like_sent(masked, n91["question"], n91["pii"]))


class KindChecksTest(unittest.TestCase):
    """What the cases model, web and long check beyond their oracle (no browser needed)."""

    def test_a_label_matching_several_options_is_ambiguous_never_a_silent_pick(self):
        drv = load_driver()
        options = [{"index": i, "text": t, "value": "", "visible": True}
                   for i, t in enumerate(["Gemini 3 Flash", "Gemini 3 Pro", "Claude Haiku 5.5", "Claude Sonnet 5"])]
        texts = lambda found: [o["text"] for o in found]  # noqa: E731
        pick, found = drv.choose_option("gemini", options)
        self.assertEqual((pick, texts(found)), (None, ["Gemini 3 Flash", "Gemini 3 Pro"]))
        pick, found = drv.choose_option("claude", options)
        self.assertEqual((pick, texts(found)), (None, ["Claude Haiku 5.5", "Claude Sonnet 5"]))
        self.assertEqual(drv.choose_option("Gemini 3 Pro", options)[0]["text"], "Gemini 3 Pro")
        self.assertEqual(drv.choose_option("claude haiku 5-5", options)[0]["text"], "Claude Haiku 5.5")
        self.assertEqual(drv.choose_option("sonnet", options)[0]["text"], "Claude Sonnet 5")
        self.assertEqual(drv.choose_option("google/eu.claude-haiku-5-5", options)[0]["text"], "Claude Haiku 5.5")
        self.assertEqual(drv.choose_option("gpt", options), (None, []))
        record = {"options": texts(options), "candidates": ["Gemini 3 Flash", "Gemini 3 Pro"], "ambiguous": True,
                  "refused": "…", "read_back": "google/eu.claude-haiku-5-5"}
        self.assertEqual(drv.Recette.model_text(record),
                         "options : Gemini 3 Flash | Gemini 3 Pro | Claude Haiku 5.5 | Claude Sonnet 5 ; choisi : aucun ; "
                         "ambigu entre : Gemini 3 Flash | Gemini 3 Pro ; relu : google/eu.claude-haiku-5-5")

    def check(self, case, obs):
        drv = load_driver()
        r = {"verdict": "OK", "notes": []}
        nfr = drv.Recette._kind_checks(case, obs, r, [])
        return r, nfr

    def test_a_model_case_wants_the_banner_to_name_the_chosen_model(self):
        case = {"kind": "model", "model": "gemini", "question": "q"}
        r, nfr = self.check(case, {"guard": {"model": "google/eu.claude-haiku-5-5"},
                                   "composer": {"model": "google/eu.gemini-3-flash"}})
        self.assertEqual((r["verdict"], nfr), ("KO", ["conformite"]))
        self.assertIn("réponse signée « google/eu.claude-haiku-5-5 », modèle choisi « google/eu.gemini-3-flash »",
                      r["notes"][0])
        r, _ = self.check(case, {"guard": {"model": "google/eu.gemini-3-flash"}})
        self.assertEqual(r["verdict"], "OK")
        # « <modele-B> »: the banner is held to the model read on the composer when the question left
        case = {"kind": "model", "model": "<modele-B>", "question": "q"}
        r, _ = self.check(case, {"guard": {"model": "mistral/mistral-large-3"}, "composer": {"model": "Mistral Large 3"}})
        self.assertEqual(r["verdict"], "OK", r)
        r, _ = self.check(case, {"guard": {"model": "google/eu.claude-haiku-5-5"}, "composer": {"model": "Mistral Large 3"}})
        self.assertEqual(r["verdict"], "KO")
        # a configuration case has its oracle only
        r, nfr = self.check({"kind": "model", "model": "claude haiku 5-5"}, {"guard": {}})
        self.assertEqual((r["verdict"], r["notes"], nfr), ("OK", [], []))

    def test_a_web_case_wants_the_post_and_the_steps_to_follow_the_mode(self):
        case = {"kind": "web", "web": "off", "question": "q"}
        r, nfr = self.check(case, {"sent": json.dumps({"message": "x", "web_search": "auto"}), "pipeline_ms": {}})
        self.assertEqual((r["verdict"], nfr), ("KO", ["conformite"]))
        self.assertIn("le POST porte web_search=auto, recherche web choisie off", r["notes"][0])
        r, _ = self.check(case, {"sent": json.dumps({"web_search": "off"}), "pipeline_ms": {"web_search": 3665}})
        self.assertEqual(r["verdict"], "KO")
        self.assertIn("étape web_search de 3665 ms", r["notes"][0])
        r, _ = self.check(case, {"sent": json.dumps({"web_search": "off"}), "pipeline_ms": {"web_search": 3}})
        self.assertEqual(r["verdict"], "OK")

    def test_a_long_case_wants_every_turn_answered(self):
        turns = [{"turn": 1, "answer": "noté", "total_s": 4.1}, {"turn": 2, "answer": "", "total_s": 9.0},
                 {"turn": 3, "answer": "QT-2093", "total_s": 12.5}]
        r, nfr = self.check({"kind": "long"}, {"turns": turns})
        self.assertEqual((r["verdict"], nfr), ("KO", ["disponibilite"]))
        self.assertEqual(r["notes"], ["tours sans réponse : 2",
                                      "3 tours ; tour le plus lent : 3 en 12.5 s ; tour 1 en 4.1 s, tour 3 en 12.5 s"])


class ProfileAndBudgetTest(unittest.TestCase):
    """ui_profile_sha256 (D12) and the class budgets (no browser needed)."""

    def recette(self, drv):
        work, profile = tempfile.mkdtemp(prefix="maskai-recette-"), tempfile.mkdtemp(prefix="maskai-recette-")
        self.addCleanup(shutil.rmtree, work, True)
        self.addCleanup(shutil.rmtree, profile, True)
        return drv.Recette(env={"VK_DRIVER_WORK_DIR": work, "VK_DRIVER_PROFILE_DIR": profile,
                                "VK_DRIVER_HOSTS": "127.0.0.1"})

    def test_every_journal_line_carries_the_ui_profile_and_a_selector_change_shows(self):
        drv = load_driver()
        recette = self.recette(drv)
        self.assertRegex(recette.ui_profile_sha256, r"^sha256:[0-9a-f]{64}$")
        recette.observations["@load"] = {"answer": "chargement : ttfb 50 ms", "timing_s": 0.1}
        evidence_dir = tempfile.mkdtemp(prefix="maskai-recette-")
        self.addCleanup(shutil.rmtree, evidence_dir, True)
        recette.actions()["verdict_oracle"]({"case": "N0-1"}, evidence_dir)
        line = journal_lines(recette.work)[-1]
        self.assertEqual((line["ui_profile_sha256"], line["inputs_sha256"]),
                         (recette.ui_profile_sha256, recette.inputs_sha256))
        # One selector changed: another ui profile, the same verdict inputs (inputs_sha256 never saw it).
        drv2 = load_driver()
        drv2.SELECTORS["answer_body"] = ".prose"
        other = self.recette(drv2)
        self.assertNotEqual(other.ui_profile_sha256, recette.ui_profile_sha256)
        self.assertEqual(other.inputs_sha256, recette.inputs_sha256)
        # One known modal more: another ui profile too.
        drv3 = load_driver()
        drv3.KNOWN_MODALS.append({"name": "x", "selector": "#x", "dismiss": "#x button"})
        self.assertNotEqual(self.recette(drv3).ui_profile_sha256, recette.ui_profile_sha256)
        # The end-of-answer rule too (the recette 2 goal tells the judge so).
        drv4 = load_driver()
        drv4.DISPLAY_STABLE_S = 1.5
        self.assertNotEqual(self.recette(drv4).ui_profile_sha256, recette.ui_profile_sha256)
        self.assertEqual(self.recette(load_driver()).ui_profile_sha256, recette.ui_profile_sha256, "deterministic")

    def test_a_case_without_a_budget_takes_its_class_budget(self):
        drv = load_driver()
        self.assertEqual(drv.budget_of({"kind": "message", "budget_s": 20}), 20)
        self.assertEqual(drv.budget_of({"kind": "message"}), 10)
        self.assertEqual(drv.budget_of({"kind": "load"}), 3)
        # PROCEDURE-2 §5: attachment 20 s, web search 15 s, concurrent 15 s
        self.assertEqual(drv.budget_of({"kind": "attach_question"}), 20)
        self.assertEqual(drv.budget_of({"kind": "web"}), 15)
        self.assertEqual(drv.budget_of({"kind": "concurrent"}), 15)
        self.assertEqual(drv.budget_of({"kind": "message", "pii": ["x"]}), 20)
        self.assertEqual(drv.budget_of({"kind": "other"}), drv.DEFAULT_BUDGET_S)
        self.assertEqual(set(drv.CLASS_BUDGET_S), {"load", "message", "model", "web", "long", "list",
                                                   "attach_question", "concurrent"})


class VendorLookupTest(unittest.TestCase):
    """Ruling R-T8a: installed, the driver finds vkdriver, evaluer.py and journal.py under vendor/
    first, the repository second; the calibration hash is the same either way (no browser)."""

    PROBE = "\n".join([
        "import importlib.util, json, sys",
        "spec = importlib.util.spec_from_file_location('drv', sys.argv[1])",
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)",
        "print(json.dumps({'protocol': sys.modules['vkdriver.protocol'].__file__,",
        "                  'hash_dir': sys.modules['vkdriver.hash_dir'].__file__,",
        "                  'evaluer': m.evaluer.__file__, 'journal': m.journal.__file__,",
        "                  'inputs': [[k, str(p)] for k, p in m.calibration_files()]}))",
    ])

    def test_a_vendored_copy_is_imported_before_the_repository(self):
        repo = os.path.dirname(os.path.dirname(DRIVER_DIR))
        root = tempfile.mkdtemp(prefix="maskai-vendor-")
        self.addCleanup(shutil.rmtree, root, True)
        installed = os.path.join(root, "maskai-recette", "0.2.0")  # no repository around it
        vendor = os.path.join(installed, "vendor")
        shutil.copytree(os.path.join(repo, "drivers", "vkdriver"), os.path.join(vendor, "vkdriver"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        os.makedirs(os.path.join(vendor, "recette"))
        for name in ("evaluer.py", "journal.py"):
            shutil.copy(os.path.join(repo, "scripts", "recette", name), os.path.join(vendor, "recette", name))
        shutil.copy(os.path.join(DRIVER_DIR, "driver.py"), installed)
        out = subprocess.run([sys.executable, "-I", "-c", self.PROBE, os.path.join(installed, "driver.py")],
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr)
        got = json.loads(out.stdout.splitlines()[-1])
        for key in ("protocol", "hash_dir", "evaluer", "journal"):
            self.assertTrue(os.path.realpath(got[key]).startswith(os.path.realpath(vendor)), (key, got[key]))
        drv = load_driver()  # the repository's layout: no vendor/ beside it
        self.assertEqual(drv.VKDRIVER_DIR, drv.DRIVER_DIR.parent / "vkdriver")
        self.assertEqual(drv.SCRIPTS_DIR, drv.REPO / "scripts" / "recette")
        here = drv.calibration_files()
        self.assertEqual([k for k, _ in got["inputs"]], [k for k, _ in here], "the same names")
        self.assertEqual(drv.hash_files([(k, p) for k, p in got["inputs"]]), drv.hash_files(here),
                         "the same bytes under the same names: the same calibration hash")

    def test_pack_json_names_the_vendored_dependencies(self):
        with open(os.path.join(DRIVER_DIR, "pack.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"vendor": ["drivers/vkdriver", "scripts/recette/evaluer.py",
                                                       "scripts/recette/journal.py"]})


class FixtureBench(unittest.TestCase):
    """The fixture server and the helpers of the browser tests (no test of its own)."""

    @classmethod
    def setUpClass(cls):
        cls.server = subprocess.Popen(
            [sys.executable, "-I", os.path.join(DRIVER_DIR, "fixture", "serve.py"), "0"],
            stdout=subprocess.PIPE, text=True)
        cls.port = int(cls.server.stdout.readline().split()[1])

    @classmethod
    def tearDownClass(cls):
        cls.server.terminate()
        cls.server.wait(timeout=10)
        cls.server.stdout.close()

    def tmp(self) -> str:
        d = tempfile.mkdtemp(prefix="maskai-recette-")
        self.addCleanup(shutil.rmtree, d, True)
        return d

    def make(self, work=None, module=None, cases_path=None):
        drv = module or load_driver()
        recette = drv.Recette(env={
            "VK_DRIVER_WORK_DIR": work or self.tmp(), "VK_DRIVER_PROFILE_DIR": self.tmp(),
            "VK_DRIVER_HEADLESS": "1", "VK_DRIVER_CHANNEL": "", "VK_DRIVER_HOSTS": "127.0.0.1",
            "MASKAI_ENTRY_URL": f"http://127.0.0.1:{self.port}/new"},
            **({"cases_path": cases_path} if cases_path else {}))
        self.addCleanup(recette.browser.close)
        return drv, recette, recette.actions()

    def stats(self) -> dict:
        return json.load(urllib.request.urlopen(f"http://127.0.0.1:{self.port}/stats"))

    def popup_announced_after(self, t0_ms: int, wait, limit_s: float = 20) -> None:
        """Wait (with `wait()` between looks) until the busy page announced its popup."""
        deadline = time.monotonic() + limit_s
        while (self.stats()["created_ms"] or 0) <= t0_ms and time.monotonic() < deadline:
            wait()

    def message_case(self, recette, cid: str, question: str) -> None:
        recette.cases[cid] = {"id": cid, "level": "N8", "kind": "message", "question": question,
                              "budget_s": 5, "nfr": ["veracite"], "must": ["bonjour"], "must_not": [], "pii": []}

    def act(self, actions, name, args=None):
        result, evidence = actions[name](args or {}, self.tmp())
        for e in evidence:
            self.assertTrue(os.path.isfile(e["path"]), e)
        return result, evidence

    @staticmethod
    def evidence_json(evidence, name):
        with open(next(e["path"] for e in evidence if e["name"] == name), encoding="utf-8") as f:
            return json.load(f)


@unittest.skipUnless(HAVE_PLAYWRIGHT, NO_PLAYWRIGHT)
class DriverOnFixture(FixtureBench):

    def test_replay_ten_of_ten(self):
        drv, recette, A = self.make()
        recette.open()
        base = recette.cases["fixture-case-1"]
        for i in range(10):
            cid = f"fixture-case-1/run-{i + 1:02d}"  # ten distinct cases: ten real verdicts, none replayed
            recette.cases[cid] = {**base, "id": cid}
            t0 = time.monotonic()
            self.act(A, "nouvelle_conversation")
            res, ev = self.act(A, "joindre_document", {"case": cid})
            # v2 (D7): « ready » once the chip left its pending states, not « attached » at once
            self.assertEqual((res["state"], res["ready_by"]), ("ready", "after_pending"))
            self.assertEqual({e["name"] for e in ev}, {"attach.png", "network.json"})
            res, ev = self.act(A, "poser_question", {"case": cid})
            self.assertIn("QUEL EST LE MONTANT DU LOYER", res["answer"])
            self.assertIn("Quel est le montant du loyer", res["sent"])
            self.assertEqual([s["node"] for s in res["server_steps"]], ["retrieve", "generate"])
            # v2 (D4, D5): the card header and the sent text are evidence of their own
            self.assertEqual({e["name"] for e in ev},
                             {"dom.html", "guard.json", "sent.json", "network.json", "answer.png"})
            res, ev = self.act(A, "verdict_oracle", {"case": cid})
            self.assertEqual((res["verdict"], res["replayed"]), ("OK", False), res)
            self.assertEqual([e["name"] for e in ev], ["journal.jsonl"])
            # v2 (D6): 2 s of stillness after the stream's end, and the « Chiffré » popover read
            # before sending, cost about 3 s a case by design (v1: 5 s a run)
            self.assertLess(time.monotonic() - t0, 9, f"run {i + 1}")
        lines = journal_lines(recette.work)
        self.assertEqual(len({line["case"] for line in lines}), 10)
        self.assertTrue(all(line["verdict"] == "OK" and line["timing_s"] < 5 for line in lines), lines)

    def test_dialog_guard_reports_and_never_answers(self):
        drv, recette, A = self.make()
        recette.open()
        self.act(A, "nouvelle_conversation")
        with self.assertRaises(drv.DriverRefusal) as refused:
            self.act(A, "envoyer_message", {"text": "Ouvre TRIGGER_DIALOG"})
        self.assertEqual(refused.exception.reason, "dialog")
        self.assertIn("unexpected", refused.exception.detail)
        # The dialog is still open, so the page is held: nothing answered it.
        with self.assertRaises(drv.PWTimeout):
            recette.browser.call("() => window.__dialog_answered", timeout_ms=1000)
        with self.assertRaises(drv.DriverRefusal) as again:
            self.act(A, "capture_ecran")
        self.assertEqual(again.exception.reason, "dialog")
        # The founder resolves it in Chrome (here: dismisses it); the guard lets go.
        recette.browser.pending()["dialog"].dismiss()
        self.assertIs(recette.browser.call("() => window.__dialog_answered"), False)
        res, ev = self.act(A, "capture_ecran")
        self.assertEqual([e["name"] for e in ev], ["screen.png"])

    def test_host_allow_list(self):
        drv, recette, A = self.make()
        recette.open()
        self.act(A, "nouvelle_conversation")
        res, ev = self.act(A, "envoyer_message", {"text": "Montre une image EXTERNAL_IMG"})
        self.assertIn("EXTERNAL_IMG", res["answer"])
        # A third-party sub-resource is recorded, not blocked (Ruling R16: no routing).
        flux, _ = self.act(A, "capturer_flux")
        self.assertIn("example.invalid", [h["host"] for h in flux["third_party_hosts"]], flux)
        with open(next(e["path"] for e in ev if e["name"] == "network.json"), encoding="utf-8") as f:
            capture = json.load(f)
        self.assertIn("example.invalid", [h["host"] for h in capture["third_party_hosts"]])
        img = [r for r in capture["requests"] if "example.invalid" in r["url"]]
        self.assertTrue(img, capture["requests"])
        self.assertNotIn("BLOCKED", (img[0].get("failure") or "").upper(), "the browser tried it")
        # A navigation to a host off the list is refused before it starts.
        recette.entry = "http://example.invalid/new"
        with self.assertRaises(drv.DriverRefusal) as refused:
            self.act(A, "mesurer_chargement")
        self.assertEqual(refused.exception.reason, "host")

    def test_off_list_navigation_is_undone_and_refused(self):
        drv, recette, A = self.make()
        recette.open()
        self.act(A, "nouvelle_conversation")
        with self.assertRaises(drv.DriverRefusal) as refused:
            self.act(A, "envoyer_message", {"text": "Pars ailleurs NAVIGATE_AWAY"})
        self.assertEqual(refused.exception.reason, "host")
        self.assertIn("localhost", refused.exception.detail)
        self.assertEqual(recette.browser.page().url, recette.entry, "back on the entry page")
        res, _ = self.act(A, "capture_ecran")  # and the next action runs as usual
        self.assertIn("127.0.0.1", res["text"])

    def test_a_popup_opens_while_the_driver_idles(self):
        """Ruling R19: between requests the driver keeps servicing Playwright, or a busy page's
        popup (an SSO window at sign-in) waits for the next action to start."""
        work = self.tmp()
        env = dict(os.environ, VK_DRIVER_WORK_DIR=work, VK_DRIVER_PROFILE_DIR=self.tmp(),
                   VK_DRIVER_HEADLESS="1", VK_DRIVER_CHANNEL="", VK_DRIVER_HOSTS="127.0.0.1",
                   MASKAI_ENTRY_URL=f"http://127.0.0.1:{self.port}/busy")
        t0 = int(time.time() * 1000)
        with open(os.path.join(work, "driver.log"), "wb") as log:
            p = subprocess.Popen([sys.executable, "-I", os.path.join(DRIVER_DIR, "driver.py")], env=env,
                                 cwd=work, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log)
            try:
                self.assertIn("hello", json.loads(p.stdout.readline()))
                self.popup_announced_after(t0, lambda: time.sleep(0.2))  # the driver idles meanwhile
                time.sleep(1.5)
                ev = self.tmp()
                p.stdin.write((json.dumps({"id": 1, "action": "capture_ecran", "args": {},
                                           "deadline_ms": 60000, "evidence_dir": ev}) + "\n").encode())
                p.stdin.flush()
                self.assertTrue(json.loads(p.stdout.readline())["ok"])
                s = self.stats()
                self.assertIsNotNone(s["popup_ms"], "the popup never started")
                self.assertLess(s["popup_ms"] - s["created_ms"], 1000, s)
            finally:
                p.stdin.close()
                p.wait(timeout=60)
                p.stdout.close()

    def test_a_navigation_between_actions_is_not_charged_to_the_next(self):
        drv, recette, A = self.make()
        recette.open()
        page = recette.browser.page()
        # The founder, between two actions, follows a link off the list (a sign-in, say).
        page.evaluate(f"() => location.assign('http://localhost:{self.port}/elsewhere')")
        time.sleep(1.0)  # Python idle: the navigation's events wait to be delivered
        res, _ = self.act(A, "capture_ecran")  # not refused: it happened before the action
        self.assertIn("localhost", res["text"])
        self.act(A, "nouvelle_conversation")  # and an action that needs the app goes back to it
        self.assertTrue(recette.browser.page().url.startswith(f"http://127.0.0.1:{self.port}/"))

    def test_an_off_list_navigation_during_a_case_makes_it_ko_securite(self):
        drv, recette, A = self.make()
        recette.open()
        recette.cases["fixture-away"] = {"id": "fixture-away", "level": "N8", "kind": "message",
                                         "question": "Bonjour NAVIGATE_AWAY", "budget_s": 5,
                                         "nfr": ["veracite"], "must": ["bonjour"], "must_not": [], "pii": []}
        self.act(A, "nouvelle_conversation")
        with self.assertRaises(drv.DriverRefusal) as refused:
            self.act(A, "poser_question", {"case": "fixture-away"})
        self.assertEqual(refused.exception.reason, "host")
        self.assertNotIn("answer", recette.observations["fixture-away"], "the off-list page is no answer")
        res, _ = self.act(A, "verdict_oracle", {"case": "fixture-away"})
        self.assertEqual(res["verdict"], "KO")
        line = journal_lines(recette.work)[-1]
        self.assertIn("securite", line["nfr"])
        self.assertIn("navigation hors périmètre : localhost", line["evidence"])

    def test_a_late_dialog_still_reaches_the_verdict(self):
        drv, recette, A = self.make()
        recette.open()
        recette.cases["fixture-late"] = {"id": "fixture-late", "level": "N8", "kind": "message",
                                         "question": "Bonjour LATE_DIALOG", "budget_s": 5,
                                         "nfr": ["veracite"], "must": ["bonjour"], "must_not": [], "pii": []}
        self.act(A, "nouvelle_conversation")
        res, _ = self.act(A, "poser_question", {"case": "fixture-late"})  # over before the dialog opens
        self.assertIn("BONJOUR", res["answer"])
        time.sleep(2.5)
        res, _ = self.act(A, "verdict_oracle", {"case": "fixture-late"})
        self.assertEqual(res["verdict"], "KO")
        line = journal_lines(recette.work)[-1]
        self.assertIn("securite", line["nfr"])
        self.assertIn("dialogue inattendu (confirm) : late (après la réponse, +", line["evidence"])

    def test_a_closed_tab_keeps_the_pump_and_chrome(self):
        """The founder closes the frozen tab instead of answering its dialog, while the driver
        idles: the pump goes on through another tab, Chrome is not relaunched (session kept),
        and the dialog logged before the close still makes the verdict KO."""
        drv, recette, A = self.make()
        recette.open()
        b = recette.browser
        ctx = b.context
        ctx.add_cookies([{"name": "session", "value": "kept", "url": f"http://127.0.0.1:{self.port}/"}])

        def idle():  # what serve's idle hook does between requests
            time.sleep(0.1)
            b.pump()

        self.message_case(recette, "fixture-late", "Bonjour LATE_DIALOG")
        self.act(A, "nouvelle_conversation")
        self.act(A, "poser_question", {"case": "fixture-late"})
        deadline = time.monotonic() + 5
        while not b.dialog_log and time.monotonic() < deadline:
            idle()
        self.assertTrue(b.dialog_log, "the late dialog opened")
        t0 = int(time.time() * 1000)
        ctx.new_page().goto(f"http://127.0.0.1:{self.port}/busy")  # a second, busy tab
        b.page().close(run_before_unload=True)  # closed in Chrome; the client learns it later
        self.popup_announced_after(t0, idle)
        for _ in range(15):
            idle()
        s = self.stats()
        self.assertIsNotNone(s["popup_ms"], "the pump stopped: the popup never started")
        self.assertLess(s["popup_ms"] - s["created_ms"], 1000, s)
        res, _ = self.act(A, "verdict_oracle", {"case": "fixture-late"})
        self.assertEqual(res["verdict"], "KO")
        self.assertIn("dialogue inattendu (confirm) : late (après la réponse", journal_lines(recette.work)[-1]["evidence"])
        self.act(A, "capture_ecran")
        self.assertIs(b.context, ctx, "Chrome was relaunched")
        self.assertIn("kept", [c["value"] for c in ctx.cookies()])

    def test_an_off_list_navigation_after_the_answer_is_a_note_not_a_ko(self):
        """Ruling R20: what the operator does between actions is not the product's fault."""
        drv, recette, A = self.make()
        recette.open()
        self.message_case(recette, "fixture-after", "Bonjour")
        self.act(A, "nouvelle_conversation")
        self.act(A, "poser_question", {"case": "fixture-after"})
        recette.browser.page().evaluate(f"() => location.assign('http://localhost:{self.port}/idp')")
        time.sleep(1.0)
        res, _ = self.act(A, "verdict_oracle", {"case": "fixture-after"})
        self.assertEqual(res["verdict"], "OK")
        line = journal_lines(recette.work)[-1]
        self.assertNotIn("securite", line.get("nfr", []))
        self.assertIn("navigation hors périmètre après la réponse : localhost (+", line["evidence"])

    def test_charge_concurrente_checks_both_hosts_before_journaling(self):
        drv, recette, A = self.make()
        recette.open()
        self.message_case(recette, "fixture-away-c", "Bonjour NAVIGATE_AWAY")
        with self.assertRaises(drv.DriverRefusal) as refused:
            self.act(A, "charge_concurrente", {"cases": ["fixture-away-c", "fixture-case-2"]})
        self.assertEqual(refused.exception.reason, "host")
        line = journal_lines(recette.work)[-1]
        self.assertEqual((line["case"], line["verdict"]), ("charge_concurrente : fixture-away-c + fixture-case-2", "KO"))
        self.assertIn("securite", line["nfr"])
        self.assertIn("navigation hors périmètre : localhost", line["evidence"])

    def test_charge_concurrente_journals_after_its_last_look(self):
        """Task 4 re-review 3: the line is journaled only after the screenshot, the second tab's
        close and a last drain. A navigation off the list in that window makes it KO sécurité,
        never an OK the refusal then contradicts (and a re-run would replay); and the refusal
        sends the driver's own tab back to the entry page, not whichever tab now holds the
        index the navigation was recorded under."""
        drv, recette, A = self.make()
        recette.open()
        b = recette.browser
        away = f"http://localhost:{self.port}/elsewhere"
        real = drv.Evidence.screenshot
        opened = []

        def screenshot(ev, page, name):  # the window: a tab opens, the second tab leaves the list
            opened.append(b.context.new_page())
            page.evaluate(f"() => location.assign({away!r})")
            page.wait_for_url(away, timeout=10_000)
            real(ev, page, name)

        drv.Evidence.screenshot = screenshot
        with self.assertRaises(drv.DriverRefusal) as refused:
            self.act(A, "charge_concurrente", {"cases": ["fixture-case-1", "fixture-case-2"]})
        self.assertEqual(refused.exception.reason, "host")
        self.assertEqual({e["name"] for e in refused.exception.evidence}, {"network.json", "screen.png"})
        line = journal_lines(recette.work)[-1]
        self.assertEqual(line["verdict"], "KO", line)
        self.assertIn("securite", line["nfr"])
        self.assertIn("navigation hors périmètre : localhost", line["evidence"])
        self.assertEqual(b.page().url, recette.entry, "the driver's own tab is back on the entry page")
        self.assertEqual(opened[0].url, "about:blank", "the other tab was left alone")
        drv.Evidence.screenshot = real
        res, _ = self.act(A, "charge_concurrente", {"cases": ["fixture-case-1", "fixture-case-2"]})
        self.assertTrue(res["replayed"])
        self.assertEqual(json.loads(res["text"])["verdict"], "KO")

    def test_charge_concurrente_journals_a_dialog_of_its_last_look(self):
        """Task 7 review, minor 4: a dialog that opens in the driver's tab during the last look
        (here, the screenshot) makes the concurrent line KO sécurité with the dialog's text, as
        verdict_oracle words it, and the action a `dialog` refusal; never an OK line the
        refusal then contradicts."""
        drv, recette, A = self.make()
        recette.open()
        b = recette.browser
        real = drv.Evidence.screenshot

        def screenshot(ev, page, name):  # the window: the driver's own tab opens a dialog
            b.page().evaluate("() => { setTimeout(() => confirm('pendant la capture'), 0); }")
            deadline = time.monotonic() + 10
            while not b.pending() and time.monotonic() < deadline:
                page.wait_for_timeout(50)
            real(ev, page, name)

        drv.Evidence.screenshot = screenshot
        with self.assertRaises(drv.DriverRefusal) as refused:
            self.act(A, "charge_concurrente", {"cases": ["fixture-case-1", "fixture-case-2"]})
        self.assertEqual(refused.exception.reason, "dialog")
        line = journal_lines(recette.work)[-1]
        self.assertEqual(line["verdict"], "KO", line)
        self.assertIn("securite", line["nfr"])
        self.assertIn("dialogue inattendu (confirm) : pendant la capture", line["evidence"])
        b.pending()["dialog"].dismiss()  # the founder closes it
        drv.Evidence.screenshot = real
        res, _ = self.act(A, "charge_concurrente", {"cases": ["fixture-case-1", "fixture-case-2"]})
        self.assertTrue(res["replayed"])
        self.assertEqual(json.loads(res["text"])["verdict"], "KO")

    def test_a_lost_playwright_connection_heals_at_the_next_action(self):
        """Task 4 re-review 3: Playwright's node process dies (killed, crashed) while the driver
        idles. No context `close` event comes: the page fails, then every `new_page` does. That
        failing `new_page` marks the browser dead, and the next action relaunches it, rather
        than every action failing `internal` until a remount."""
        drv, recette, A = self.make()
        recette.open()
        b = recette.browser
        old = b.context
        connection = getattr(getattr(b.pw, "_impl_obj", None), "_connection", None)
        proc = getattr(getattr(connection, "_transport", None), "_proc", None)
        if proc is None:
            self.skipTest("this Playwright does not expose its node process (the pinned 1.64.0 does)")
        os.kill(proc.pid, signal.SIGTERM)

        def idle():  # what serve's idle loop does: pump, and log what escapes it
            time.sleep(0.1)
            try:
                b.pump()
            except Exception:  # noqa: BLE001 — serve logs it and goes on
                pass

        deadline = time.monotonic() + 10
        while b.started() and time.monotonic() < deadline:
            idle()
        self.assertFalse(b.started(), "the lost connection was never noticed")
        self.act(A, "nouvelle_conversation")
        res, _ = self.act(A, "capture_ecran")
        self.assertIn("127.0.0.1", res["text"])
        self.assertIsNot(b.context, old, "the browser was relaunched")

    def test_an_unreachable_redirect_is_a_host_refusal(self):
        drv, recette, A = self.make()
        recette.entry = f"http://127.0.0.1:{self.port}/redirect-unreachable"
        with self.assertRaises(drv.DriverRefusal) as refused:
            self.act(A, "mesurer_chargement")
        self.assertEqual(refused.exception.reason, "host")
        self.assertIn("unreachable.invalid", refused.exception.detail)

    def test_a_dialog_during_a_case_makes_it_ko_securite(self):
        drv, recette, A = self.make()
        recette.open()
        recette.cases["fixture-dialog"] = {"id": "fixture-dialog", "level": "N8", "kind": "message",
                                           "question": "Bonjour TRIGGER_DIALOG", "budget_s": 5,
                                           "nfr": ["veracite"], "must": ["bonjour"], "must_not": [], "pii": []}
        self.act(A, "nouvelle_conversation")
        with self.assertRaises(drv.DriverRefusal) as refused:
            self.act(A, "poser_question", {"case": "fixture-dialog"})
        self.assertEqual(refused.exception.reason, "dialog")
        recette.browser.pending()["dialog"].dismiss()  # the founder closes it
        res, _ = self.act(A, "verdict_oracle", {"case": "fixture-dialog"})
        self.assertEqual(res["verdict"], "KO")
        line = journal_lines(recette.work)[-1]
        self.assertEqual((line["case"], line["verdict"]), ("fixture-dialog", "KO"))
        self.assertIn("securite", line["nfr"])
        self.assertIn("dialogue inattendu (confirm) : unexpected", line["evidence"])

    def test_inputs_sha256_in_hello_and_journal(self):
        drv, recette, A = self.make()
        self.assertRegex(recette.version, r"^0\.2\.0\+[0-9a-f]{12}$")  # v2 of the driver
        self.assertTrue(recette.inputs_sha256.startswith("sha256:" + recette.version[6:]))
        # The hello of the driver process carries it.
        env = dict(os.environ, VK_DRIVER_WORK_DIR=self.tmp(), VK_DRIVER_PROFILE_DIR=self.tmp(),
                   VK_DRIVER_HEADLESS="1", VK_DRIVER_CHANNEL="", VK_DRIVER_HOSTS="127.0.0.1",
                   MASKAI_ENTRY_URL=f"http://127.0.0.1:{self.port}/new")
        p = subprocess.run([sys.executable, "-I", os.path.join(DRIVER_DIR, "driver.py")], env=env,
                           cwd=env["VK_DRIVER_WORK_DIR"], input=b"", capture_output=True, timeout=120)
        hello = json.loads(p.stdout.decode("utf-8").splitlines()[0])["hello"]
        self.assertEqual(hello["version"], recette.version)
        # Every journal line the driver appends carries the full hash.
        self.act(A, "mesurer_chargement")
        self.act(A, "verdict_oracle", {"case": "N0-1"})
        self.assertEqual(journal_lines(recette.work)[-1]["inputs_sha256"], recette.inputs_sha256)
        recette.browser.close()  # one sync Playwright per thread
        # One byte of cases.json changed: another hash, in the version and on the line.
        changed = os.path.join(self.tmp(), "cases.json")
        with open(os.path.join(DRIVER_DIR, "cases.json"), "rb") as src, open(changed, "wb") as dst:
            dst.write(src.read() + b"\n")
        drv2, recette2, A2 = self.make(module=load_driver(), cases_path=changed)
        self.assertNotEqual(recette2.inputs_sha256, recette.inputs_sha256)
        self.assertNotEqual(recette2.version, recette.version)
        self.act(A2, "mesurer_chargement")
        self.act(A2, "verdict_oracle", {"case": "N0-1"})
        self.assertEqual(journal_lines(recette2.work)[-1]["inputs_sha256"], recette2.inputs_sha256)

    def test_timeout_journals_ko_disponibilite(self):
        drv, recette, A = self.make()
        recette.open()
        recette.cases["fixture-slow"] = {"id": "fixture-slow", "level": "N8", "kind": "message",
                                         "question": "SLOW", "budget_s": 1, "nfr": ["securite"],
                                         "must": [], "must_not": [], "pii": []}
        self.act(A, "nouvelle_conversation")
        t0 = time.monotonic()
        with self.assertRaises(drv.DriverRefusal) as refused:
            self.act(A, "poser_question", {"case": "fixture-slow"})
        self.assertEqual(refused.exception.reason, "timeout")
        self.assertLess(time.monotonic() - t0, 7, "abandon at 3 × budget, not at the server's 8 s")
        line = journal_lines(recette.work)[-1]
        self.assertEqual((line["case"], line["verdict"], line["step"]), ("fixture-slow", "KO", "exec"))
        self.assertIn("disponibilite", line["nfr"])
        res, _ = self.act(A, "verdict_oracle", {"case": "fixture-slow"})
        self.assertEqual((res["verdict"], res["replayed"]), ("KO", True))

    def test_verdict_is_idempotent_after_relaunch(self):
        drv, recette, A = self.make()
        self.act(A, "nouvelle_conversation")
        self.act(A, "joindre_document", {"case": "fixture-case-1"})
        self.act(A, "poser_question", {"case": "fixture-case-1"})
        res, _ = self.act(A, "verdict_oracle", {"case": "fixture-case-1"})
        self.assertEqual((res["verdict"], res["replayed"]), ("OK", False))
        recette.browser.close()
        drv2, recette2, A2 = self.make(work=str(recette.work), module=load_driver())
        self.assertIsNot(drv2, drv)
        res, ev = self.act(A2, "verdict_oracle", {"case": "fixture-case-1"})
        self.assertEqual((res["verdict"], res["replayed"]), ("OK", True))
        self.assertEqual(ev, [])
        self.assertEqual(len(journal_lines(recette.work)), 1, "a replayed verdict appends nothing")
        self.assertFalse(recette2.browser.started(), "a verdict needs no browser")

    def test_failed_attachment_is_a_precondition_refusal(self):
        drv, recette, A = self.make()
        recette.cases["fixture-fail"] = {**recette.cases["fixture-case-1"], "id": "fixture-fail",
                                         "file": "fixture/corpus/fail.txt"}
        self.act(A, "nouvelle_conversation")
        with self.assertRaises(drv.DriverRefusal) as refused:
            self.act(A, "joindre_document", {"case": "fixture-fail"})
        self.assertEqual((refused.exception.reason, refused.exception.detail), ("precondition", "attachment failed"))
        self.assertIn("attach.png", [e["name"] for e in refused.exception.evidence])
        # The chip is read before sending: the question is not sent, the case is judged KO.
        with self.assertRaises(drv.DriverRefusal) as not_sent:
            self.act(A, "poser_question", {"case": "fixture-fail"})
        self.assertEqual(not_sent.exception.reason, "precondition")
        res, _ = self.act(A, "verdict_oracle", {"case": "fixture-fail"})
        self.assertEqual(res["verdict"], "KO")

    def test_every_action_answers_with_its_declared_evidence(self):
        declared = manifest_evidence()
        drv, recette, A = self.make()
        self.assertEqual(set(A), set(declared))
        seen = {}

        def run(name, args=None):
            res, ev = self.act(A, name, args)
            self.assertIsInstance(res.get("text"), str, name)
            self.assertLessEqual({e["name"] for e in ev}, declared[name], name)
            seen[name] = True
            return res

        res = run("mesurer_chargement")
        self.assertGreater(res["load_ms"], 0)
        self.assertIn(run("verdict_oracle", {"case": "N0-1"})["verdict"], ("OK", "PARTIEL"))
        run("nouvelle_conversation")
        self.assertEqual(run("poser_question", {"case": "N1-2"})["answer"], "aucun envoi")
        self.assertEqual(run("verdict_oracle", {"case": "N1-2"})["verdict"], "OK")
        run("joindre_document", {"case": "fixture-case-2"})
        run("poser_question", {"case": "fixture-case-2"})
        self.assertIn("DATE D'ÉCHÉANCE", run("lire_reponse")["answer"])
        self.assertGreater(len(run("capturer_flux")["events"]), 0)
        self.assertGreaterEqual(run("lister_conversations")["count"], 1)
        self.assertEqual(run("verdict_oracle", {"case": "N7-1"})["verdict"], "OK")
        run("capture_ecran")
        self.assertIn("BONJOUR", run("envoyer_message", {"text": "Bonjour"})["answer"])
        res = run("charge_concurrente", {"cases": ["fixture-case-1", "fixture-case-2"]})
        self.assertEqual(res["verdict"], "OK", res)
        self.assertTrue(res["a"]["answer"] and res["b"]["answer"])
        self.assertEqual(run("choisir_modele", {"model": "mistral"})["model"], "mistral/mistral-large-3")
        self.assertEqual(run("recherche_web", {"mode": "off"})["mode"], "off")
        run("nouvelle_conversation")
        recette.cases["every-long"] = {"id": "every-long", "level": "N2", "kind": "long", "budget_s": 5,
                                       "nfr": ["veracite"], "first": "Bonjour", "last": "Au revoir",
                                       "must": ["revoir"], "must_not": [], "pii": []}
        self.assertEqual(run("conversation_longue", {"case": "every-long", "turns": 2})["turns"], 2)
        n = len(journal_lines(recette.work))
        self.assertEqual(run("exporter_journal")["lines"], n)
        self.assertEqual(run("exporter_journal")["lines"], 0)
        self.assertEqual(set(seen), set(declared))



@unittest.skipUnless(HAVE_PLAYWRIGHT, NO_PLAYWRIGHT)
class DriverV2OnFixture(FixtureBench):
    """What the 2026-10-17 debrief asked of v2, on the bench in the 17/10 shapes."""

    def test_the_answer_is_the_body_and_the_header_goes_to_guard_json(self):
        drv, recette, A = self.make()
        recette.open()
        self.message_case(recette, "v2-body", "Bonjour, quelle heure est-il ?")
        self.act(A, "nouvelle_conversation")
        res, ev = self.act(A, "poser_question", {"case": "v2-body"})
        self.assertEqual(res["answer"], "BONJOUR, QUELLE HEURE EST-IL ? — pièces jointes : aucune")
        for word in ("maskai", "Aiguilleur", "claude-haiku", "en direct"):
            self.assertNotIn(word, res["answer"], "D5: the header is not the answer")
        guard = self.evidence_json(ev, "guard.json")
        self.assertEqual(guard["model"], "google/eu.claude-haiku-5-5")
        self.assertEqual(guard["guard_text"], "Aucune donnée personnelle détectée L'Aiguilleur : PASS")
        self.assertIs(guard["pii_detected"], False)
        self.assertTrue(guard["body_found"])
        self.assertEqual(res["model"], "google/eu.claude-haiku-5-5")
        res, ev = self.act(A, "lire_reponse")
        self.assertEqual(res["answer"], "BONJOUR, QUELLE HEURE EST-IL ? — pièces jointes : aucune")

    def test_the_answer_is_read_once_the_screen_stops_moving(self):
        """D6: the bench draws the answer about 1.5 s after the server's end, with a 1.2 s pause
        and the stop button already gone. v2 waits for 2 s of stillness: the answer is
        whole. The same bench read after 0.3 s of stillness (the old reader's) is cut: the
        bench does discriminate."""
        drv, recette, A = self.make()
        recette.open()
        q = "Voici une phrase assez longue pour que son affichage prenne du temps sur le banc"
        full = q.upper() + " — pièces jointes : aucune"
        self.message_case(recette, "v2-display", q)
        self.act(A, "nouvelle_conversation")
        res, ev = self.act(A, "poser_question", {"case": "v2-display"})
        self.assertEqual(res["answer"], full)
        self.assertGreater(res["display_s"], res["total_s"], "the screen still moved after the stream's end")
        self.assertTrue(self.evidence_json(ev, "guard.json")["display_stable"])
        res, _ = self.act(A, "verdict_oracle", {"case": "v2-display"})
        self.assertIsInstance(journal_lines(recette.work)[-1]["display_s"], float)
        drv.DISPLAY_STABLE_S = 0.3
        self.message_case(recette, "v2-cut", q)
        self.act(A, "nouvelle_conversation")
        res, _ = self.act(A, "poser_question", {"case": "v2-cut"})
        self.assertLess(len(res["answer"]), len(full), res["answer"])

    def test_the_chip_is_followed_until_ready(self):
        drv, recette, A = self.make()
        recette.open()
        self.act(A, "nouvelle_conversation")
        res, _ = self.act(A, "joindre_document", {"case": "fixture-case-1"})
        self.assertEqual((res["state"], res["ready_by"]), ("ready", "after_pending"))
        states = [x["state"] for x in res["trace"]]
        self.assertEqual(states[-1], "ready")
        self.assertIn("indexing", states)
        self.assertGreaterEqual(res["seconds"], 0.5, "the bench indexes for 0.65 s: never ready at once (D7)")
        res, _ = self.act(A, "poser_question", {"case": "fixture-case-1"})
        self.assertIn("pièces jointes : case1.txt", res["answer"])

    def test_a_chip_without_any_state_word_is_ready_only_after_a_quiet_spell(self):
        drv, recette, A = self.make()
        recette.cases["v2-instant"] = {**recette.cases["fixture-case-1"], "id": "v2-instant",
                                       "file": "fixture/corpus/instant.txt"}
        self.act(A, "nouvelle_conversation")
        res, _ = self.act(A, "joindre_document", {"case": "v2-instant"})
        self.assertEqual((res["state"], res["ready_by"]), ("ready", "quiet"))
        self.assertGreaterEqual(res["seconds"], drv.ATTACH_QUIET_S)
        self.assertEqual([x["state"] for x in res["trace"]], ["present", "ready"])

    def test_a_question_on_a_chip_not_ready_is_a_precondition_refusal_journaled_as_such(self):
        drv, recette, A = self.make()
        recette.cases["v2-slowidx"] = {**recette.cases["fixture-case-1"], "id": "v2-slowidx",
                                       "file": "fixture/corpus/slowidx.txt", "budget_s": 1}
        self.act(A, "nouvelle_conversation")
        with self.assertRaises(drv.DriverRefusal) as refused:
            self.act(A, "joindre_document", {"case": "v2-slowidx"})
        self.assertEqual((refused.exception.reason, refused.exception.extra["state"]), ("precondition", "indexing"))
        with self.assertRaises(drv.DriverRefusal) as not_sent:
            self.act(A, "poser_question", {"case": "v2-slowidx"})
        self.assertEqual(not_sent.exception.reason, "precondition")
        self.assertIn("pas prête (indexing)", not_sent.exception.detail)
        self.assertEqual(recette.browser.posts, [], "the question never left")
        res, _ = self.act(A, "verdict_oracle", {"case": "v2-slowidx"})
        self.assertEqual(res["verdict"], "KO")
        line = journal_lines(recette.work)[-1]
        self.assertIn("question non envoyée (précondition) : pièce jointe indexing", line["evidence"])
        self.assertIn("disponibilite", line["nfr"])

    def test_the_sent_text_is_read_in_the_popover_which_is_closed_again(self):
        drv, recette, A = self.make()
        recette.open()
        recette.cases["v2-pii"] = {"id": "v2-pii", "level": "N9", "kind": "message",
                                   "question": "Rédige une relance courtoise pour M. Hugo Bernard au sujet du loyer.",
                                   "budget_s": 5, "nfr": ["conformite"], "must": ["relance"], "must_not": [],
                                   "pii": ["Hugo Bernard"]}
        self.act(A, "nouvelle_conversation")
        res, ev = self.act(A, "poser_question", {"case": "v2-pii"})
        sent = self.evidence_json(ev, "sent.json")
        self.assertEqual((sent["source"], sent["closed"], sent["block_found"]), ("popover", "escape", True))
        self.assertEqual(sent["text"], "Rédige une relance courtoise pour <PERSON_1> au sujet du loyer.")
        self.assertIn("1 PII", sent["trigger_label"])
        self.assertIn("Hugo Bernard", sent["post_body"], "the POST is the raw text, masked by the server")
        self.assertEqual(res["sent_source"], "popover")
        page = recette.browser.page()
        self.assertTrue(page.evaluate("() => document.getElementById('enc-pop').hidden"), "closed again")
        self.assertIsNone(recette.browser.own_modal)
        guard = self.evidence_json(ev, "guard.json")
        self.assertEqual((guard["pii_detected"], guard["tokens"]), (True, ["<PERSON_1>"]))
        res, _ = self.act(A, "verdict_oracle", {"case": "v2-pii"})
        self.assertEqual((res["verdict"], res["leaks"]), ("OK", []))
        self.assertNotIn("texte envoyé non observé", journal_lines(recette.work)[-1]["evidence"])
        # The same question with LEAK: the popover shows the name in clear: a leak, KO.
        recette.cases["v2-leak"] = {**recette.cases["v2-pii"], "id": "v2-leak",
                                    "question": recette.cases["v2-pii"]["question"] + " LEAK"}
        self.act(A, "nouvelle_conversation")
        self.act(A, "poser_question", {"case": "v2-leak"})
        res, _ = self.act(A, "verdict_oracle", {"case": "v2-leak"})
        self.assertEqual((res["verdict"], res["leaks"]), ("KO", ["Hugo Bernard"]))
        line = journal_lines(recette.work)[-1]
        self.assertIn("securite", line["nfr"], "a leak to the model is KO sécurité")
        self.assertIn("KO sécurité : donnée identifiante en clair", line["evidence"])

    def test_a_popover_that_shows_a_status_is_not_taken_for_the_sent_text(self):
        """If the live popover only says « no PII detected », the leak oracle must say it could not
        see the sent text, never judge the status as if it were the text (a false « no leak »)."""
        drv, recette, A = self.make()
        recette.open()
        recette.cases["v2-status"] = {"id": "v2-status", "level": "N9", "kind": "message",
                                      "question": "Relance pour M. Hugo Bernard POPOVER_STATUS", "budget_s": 5,
                                      "nfr": ["conformite"], "must": ["relance"], "must_not": [],
                                      "pii": ["Hugo Bernard"]}
        self.act(A, "nouvelle_conversation")
        res, ev = self.act(A, "poser_question", {"case": "v2-status"})
        sent = self.evidence_json(ev, "sent.json")
        self.assertIsNone(sent["source"])
        self.assertEqual(sent["rejected"], "le popover ne montre pas le texte de la question")
        self.assertEqual(sent["text"], "Chiffré de bout en bout, aucune PII détectée", "kept as read, for the debrief")
        self.assertNotIn("sent_to_model", res)
        self.act(A, "verdict_oracle", {"case": "v2-status"})
        self.assertIn("texte envoyé non observé", journal_lines(recette.work)[-1]["evidence"])

    def test_the_popover_is_known_only_when_the_driver_opened_it(self):
        drv, recette, A = self.make()
        recette.open()
        self.act(A, "nouvelle_conversation")
        page = recette.browser.page()
        hidden = "() => document.getElementById('enc-pop').hidden"
        page.click("#enc")  # opened by hand: an unknown dialog for the driver
        with self.assertRaises(drv.DriverRefusal) as refused:
            self.act(A, "capture_ecran")
        self.assertEqual(refused.exception.reason, "dialog")
        self.assertIn("modale inconnue : Ce qui part au modèle", refused.exception.detail)
        self.assertFalse(page.evaluate(hidden), "the driver never closes what it did not open")
        page.keyboard.press("Escape")  # the founder closes it
        self.act(A, "capture_ecran")
        recette.browser.own_modal = "envoi"  # opened by the driver, left open by an action cut short
        page.click("#enc")
        self.act(A, "capture_ecran")
        self.assertTrue(page.evaluate(hidden), "the next guard closes it")
        self.assertIsNone(recette.browser.own_modal)

    def test_an_unknown_dialog_is_still_refused_and_never_answered(self):
        drv, recette, A = self.make()
        recette.open()
        self.act(A, "nouvelle_conversation")
        self.act(A, "envoyer_message", {"text": "Bonjour UNKNOWN_MODAL"})
        with self.assertRaises(drv.DriverRefusal) as refused:
            self.act(A, "nouvelle_conversation")
        self.assertEqual(refused.exception.reason, "dialog")
        self.assertIn("modale inconnue : Une fenêtre que personne", refused.exception.detail)
        self.assertFalse(recette.browser.page().evaluate("() => document.getElementById('mystery').hidden"))

    @staticmethod
    def pixel(recette, png_path, x, y) -> list:
        """The RGB of one pixel of a PNG, read by the browser itself (no imaging library)."""
        with open(png_path, "rb") as f:
            src = "data:image/png;base64," + base64.b64encode(f.read()).decode()
        page = recette.browser.context.new_page()
        try:
            return page.evaluate("""async ([src, x, y]) => {
                const img = new Image(); img.src = src; await img.decode();
                const c = document.createElement('canvas'); c.width = img.width; c.height = img.height;
                const g = c.getContext('2d'); g.drawImage(img, 0, 0);
                return [...g.getImageData(x, y, 1, 1).data].slice(0, 3); }""", [src, x, y])
        finally:
            page.close()

    def test_screenshots_mask_the_sidebar_and_the_account(self):
        drv, recette, A = self.make()
        recette.open()
        self.act(A, "nouvelle_conversation")
        magenta = [255, 0, 255]
        _, ev = self.act(A, "capture_ecran")
        shot = next(e["path"] for e in ev if e["name"] == "screen.png")
        self.assertEqual(self.pixel(recette, shot, 100, 300), magenta, "the sidebar (history) is masked")
        box = recette.browser.page().locator('[data-slot="sidebar-footer"]').bounding_box()
        self.assertEqual(self.pixel(recette, shot, int(box["x"] + 5), int(box["y"] + box["height"] / 2)), magenta,
                         "the account block is masked")
        self.assertNotEqual(self.pixel(recette, shot, 700, 300), magenta, "the conversation is not")
        _, ev = self.act(A, "joindre_document", {"case": "fixture-case-1"})
        attach = next(e["path"] for e in ev if e["name"] == "attach.png")
        self.assertEqual(self.pixel(recette, attach, 100, 300), magenta)
        _, ev = self.act(A, "poser_question", {"case": "fixture-case-1"})
        answer = next(e["path"] for e in ev if e["name"] == "answer.png")
        self.assertEqual(self.pixel(recette, answer, 100, 300), magenta)
        drv.SELECTORS["mask"] = []  # the same capture unmasked: the test does see a difference
        _, ev = self.act(A, "capture_ecran")
        self.assertNotEqual(self.pixel(recette, ev[0]["path"], 100, 300), magenta)

    def slow_case(self, recette) -> None:
        recette.cases["v2-slow"] = {"id": "v2-slow", "level": "N8", "kind": "message", "question": "SLOW",
                                    "budget_s": 1, "nfr": ["securite"], "must": [], "must_not": [], "pii": []}

    def test_a_clean_abort_stops_the_generation_and_no_survey_follows(self):
        drv, recette, A = self.make()
        recette.open()
        self.slow_case(recette)
        self.act(A, "nouvelle_conversation")
        with self.assertRaises(drv.DriverRefusal) as refused:
            self.act(A, "poser_question", {"case": "v2-slow"})
        self.assertEqual(refused.exception.reason, "timeout")
        abort = refused.exception.extra["abort"]
        self.assertEqual((abort["posted"], abort["stream_started"], abort["stop_clicked"], abort["stop_gone"]),
                         (True, True, True, True))
        line = journal_lines(recette.work)[-1]
        self.assertEqual((line["case"], line["verdict"]), ("v2-slow", "KO"))
        self.assertIn("question partie (POST vu), flux ouvert, arrêt cliqué", line["evidence"])
        self.assertEqual(line["ui_profile_sha256"], recette.ui_profile_sha256)
        res, _ = self.act(A, "nouvelle_conversation")
        self.assertIsNone(res["stopped_first"], "nothing was left in flight")
        time.sleep(1.5)  # the bench shows its survey 0.4 s after an abandoned generation
        self.act(A, "capture_ecran")
        self.assertEqual(recette.browser.call("() => window.__survey_shown"), 0)

    def test_timing_json_carries_the_short_url(self):
        drv, recette, A = self.make()
        recette.entry += "?jeton=secret"  # a query (a token, say) never reaches the evidence
        _, ev = self.act(A, "mesurer_chargement")
        timing = self.evidence_json(ev, "timing.json")
        self.assertEqual(timing["url"], drv.short_url(recette.entry))
        self.assertNotIn("secret", timing["url"])

    def test_a_wait_cut_by_the_action_deadline_is_non_teste_not_ko(self):
        """The action's deadline came before 3 × budget: the product did not have its time, so the
        line is NON TESTÉ without NFR, never KO disponibilité."""
        drv, recette, A = self.make()
        recette.open()
        self.slow_case(recette)
        recette.cases["v2-slow"]["budget_s"] = 5  # 15 s wanted; the host gives the action 12 s
        self.act(A, "nouvelle_conversation")
        protocol = sys.modules["vkdriver.protocol"]
        self.addCleanup(setattr, protocol, "_deadline_at", None)
        protocol._deadline_at = time.monotonic() + 12
        with self.assertRaises(drv.DriverRefusal) as refused:
            self.act(A, "poser_question", {"case": "v2-slow"})
        protocol._deadline_at = None
        self.assertEqual(refused.exception.reason, "timeout")
        self.assertIn("échéance de l'action", refused.exception.detail)
        line = journal_lines(recette.work)[-1]
        self.assertEqual((line["case"], line["verdict"], line.get("nfr")), ("v2-slow", "NON TESTÉ", None))
        self.assertIn("échéance de l'action avant 3 × le budget, pas de verdict sur le produit", line["evidence"])

    def test_a_stop_lookalike_visible_before_sending_is_neither_waited_for_nor_clicked(self):
        """The stop selectors are a guess: one that matches a permanent button (« Stop sharing »)
        must not hold every answer 30 s, nor be clicked at an abandon."""
        drv, recette, A = self.make()
        recette.open()
        self.act(A, "nouvelle_conversation")
        page = recette.browser.page()
        page.evaluate("""() => { const b = document.createElement('button');
            b.setAttribute('aria-label', 'Stop sharing'); b.textContent = 'Stop sharing';
            b.onclick = () => { window.__lookalike = (window.__lookalike || 0) + 1; };
            document.querySelector('main').prepend(b); }""")
        self.message_case(recette, "v2-lookalike", "Bonjour")
        t0 = time.monotonic()
        _, ev = self.act(A, "poser_question", {"case": "v2-lookalike"})
        self.assertLess(time.monotonic() - t0, 10, "no 30 s wait on a stop button that never goes")
        self.assertTrue(self.evidence_json(ev, "guard.json")["display_stable"])
        self.slow_case(recette)
        with self.assertRaises(drv.DriverRefusal) as refused:
            self.act(A, "poser_question", {"case": "v2-slow"})
        self.assertTrue(refused.exception.extra["abort"]["stop_skipped"])
        self.assertIsNone(page.evaluate("() => window.__lookalike"), "never clicked")
        self.assertIn("arrêt non cliqué", journal_lines(recette.work)[-1]["evidence"])

    def test_without_the_stop_the_survey_follows_and_is_closed_never_answered(self):
        """The counter-proof, v1's behaviour: no stop click, the generation is left in flight, the
        « Votre avis » survey follows (5/5 on 17/10, D2); the guard closes it by its cross."""
        drv, recette, A = self.make()
        recette.open()
        self.slow_case(recette)
        recette._stop_generation = lambda page: {"stop_clicked": False}
        self.act(A, "nouvelle_conversation")
        with self.assertRaises(drv.DriverRefusal):
            self.act(A, "poser_question", {"case": "v2-slow"})
        self.act(A, "nouvelle_conversation")
        time.sleep(1.5)
        self.assertEqual(recette.browser.call("() => window.__survey_shown"), 1)
        self.act(A, "capture_ecran")  # the guard closes the known modal by its cross
        self.assertEqual(recette.browser.call(
            "() => [window.__survey_answered, document.getElementById('avis').hidden]"), [False, True])


@unittest.skipUnless(HAVE_PLAYWRIGHT, NO_PLAYWRIGHT)
class NewActionsOnFixture(FixtureBench):
    """choisir_modele, recherche_web, conversation_longue (v2, design point 4)."""

    def test_choisir_modele_picks_the_option_and_reads_it_back(self):
        drv, recette, A = self.make()
        recette.open()
        self.act(A, "nouvelle_conversation")
        res, ev = self.act(A, "choisir_modele", {"model": "gemini"})
        self.assertEqual((res["model"], res["previous"], res["changed"]),
                         ("google/eu.gemini-3-flash", "google/eu.claude-haiku-5-5", True))
        rec = self.evidence_json(ev, "model.json")
        self.assertEqual(rec["picked"], {"text": "Gemini 3 Flash", "value": "google/eu.gemini-3-flash"})
        self.assertEqual(rec["options"], ["Claude Haiku 5.5", "Gemini 3 Flash", "Mistral Large 3"])
        self.assertTrue(rec["list_closed"])
        recette.cases["v2-model"] = {"id": "v2-model", "level": "N5", "kind": "model", "model": "gemini",
                                     "question": "Bonjour", "budget_s": 5, "nfr": ["conformite"],
                                     "must": ["bonjour"], "must_not": [], "pii": []}
        res, _ = self.act(A, "poser_question", {"case": "v2-model"})
        self.assertEqual(res["model"], "google/eu.gemini-3-flash", "the banner names the chosen model")
        res, _ = self.act(A, "verdict_oracle", {"case": "v2-model"})
        self.assertEqual(res["verdict"], "OK")
        self.assertIn("modèle choisi « google/eu.gemini-3-flash », réponse signée « google/eu.gemini-3-flash »",
                      journal_lines(recette.work)[-1]["evidence"])
        res, _ = self.act(A, "choisir_modele", {"model": "google/eu.claude-haiku-5-5"})  # back, by its value
        self.assertEqual((res["model"], res["changed"]), ("google/eu.claude-haiku-5-5", True))
        res, _ = self.act(A, "choisir_modele", {"model": "claude haiku"})  # already shown: options read all the same
        self.assertFalse(res["changed"])
        self.assertEqual(res["options"], ["Claude Haiku 5.5", "Gemini 3 Flash", "Mistral Large 3"])
        with self.assertRaises(drv.DriverRefusal) as refused:  # « 3 »: Gemini 3 Flash and Mistral Large 3
            self.act(A, "choisir_modele", {"model": "3"})
        self.assertEqual(refused.exception.reason, "precondition")
        self.assertIn("désigne plusieurs modèles de la liste : Gemini 3 Flash, Mistral Large 3", refused.exception.detail)
        self.assertIn("choisi : aucun ; ambigu entre : Gemini 3 Flash | Mistral Large 3",
                      recette.observations["@model"]["answer"])
        self.assertEqual(recette._composer_now(recette.browser.page())["model"], "google/eu.claude-haiku-5-5",
                         "nothing was chosen")
        with self.assertRaises(drv.DriverRefusal) as refused:
            self.act(A, "choisir_modele", {"model": "gpt-9"})
        self.assertEqual(refused.exception.reason, "precondition")
        self.assertIn("aucun modèle « gpt-9 » dans la liste", refused.exception.detail)
        self.assertTrue(recette.browser.page().evaluate("() => document.getElementById('model-list').hidden"),
                        "the list is closed again")
        recette.cases["v2-model-2"] = {**recette.cases["v2-model"], "id": "v2-model-2"}
        with self.assertRaises(drv.DriverRefusal) as refused:
            self.act(A, "poser_question", {"case": "v2-model-2"})
        self.assertIn("choisir_modele gemini d'abord", refused.exception.detail)

    def test_recherche_web_cycles_to_the_mode_and_the_web_case_checks_it(self):
        drv, recette, A = self.make()
        recette.open()
        self.act(A, "nouvelle_conversation")
        res, ev = self.act(A, "recherche_web", {"mode": "off"})
        self.assertEqual((res["mode"], res["previous"], res["clicks"]), ("off", "auto", 2))
        self.assertEqual(self.evidence_json(ev, "web.json")["seen"], ["auto", "on", "off"])
        recette.cases["v2-web"] = {"id": "v2-web", "level": "N1", "kind": "web", "web": "off", "question": "Bonjour",
                                   "budget_s": 5, "nfr": ["performance"], "must": ["bonjour"], "must_not": [], "pii": []}
        res, _ = self.act(A, "poser_question", {"case": "v2-web"})
        self.assertEqual([s["node"] for s in res["server_steps"]], ["retrieve", "generate"])
        res, _ = self.act(A, "verdict_oracle", {"case": "v2-web"})
        self.assertEqual(res["verdict"], "OK")
        self.assertIn("recherche web off ; POST web_search=off ; étape web_search absente",
                      journal_lines(recette.work)[-1]["evidence"])
        self.assertEqual(self.act(A, "recherche_web", {"mode": "on"})[0]["mode"], "on")
        recette.cases["v2-web-on"] = {**recette.cases["v2-web"], "id": "v2-web-on", "web": "on"}
        self.act(A, "nouvelle_conversation")
        res, _ = self.act(A, "poser_question", {"case": "v2-web-on"})
        self.assertIn("web_search", [s["node"] for s in res["server_steps"]])
        recette.cases["v2-web-2"] = {**recette.cases["v2-web"], "id": "v2-web-2"}
        with self.assertRaises(drv.DriverRefusal) as refused:
            self.act(A, "poser_question", {"case": "v2-web-2"})
        self.assertIn("recherche_web off d'abord (recherche web : on)", refused.exception.detail)

    def long_case(self, recette, cid, **over):
        recette.cases[cid] = {"id": cid, "level": "N2", "kind": "long", "budget_s": 5, "nfr": ["veracite"],
                              "first": "Retiens le code QT-2093", "filler": "Tour {i} : un synonyme de rapide",
                              "last": "Quel code au premier tour ?", "must": ["quel code au premier tour"],
                              "must_not": [], "pii": [], **over}

    def test_conversation_longue_asks_n_turns_in_one_conversation(self):
        drv, recette, A = self.make()
        recette.open()
        self.long_case(recette, "v2-long")
        self.act(A, "nouvelle_conversation")
        res, ev = self.act(A, "conversation_longue", {"case": "v2-long", "turns": 4})
        self.assertEqual(res["turns"], 4)
        self.assertEqual({e["name"] for e in ev}, {"turns.json", "answer.png"})
        turns = self.evidence_json(ev, "turns.json")["turns"]
        self.assertEqual([t["question"] for t in turns],
                         ["Retiens le code QT-2093", "Tour 2 : un synonyme de rapide",
                          "Tour 3 : un synonyme de rapide", "Quel code au premier tour ?"])
        self.assertEqual(turns[2]["answer"], "TOUR 3 : UN SYNONYME DE RAPIDE — pièces jointes : aucune")
        self.assertTrue(all(t["display_stable"] and t["sent_source"] == "popover" for t in turns), turns)
        self.assertEqual(recette.browser.call("() => document.querySelectorAll('article').length"), 4,
                         "one conversation")
        res, _ = self.act(A, "verdict_oracle", {"case": "v2-long"})
        self.assertEqual(res["verdict"], "OK")
        self.assertIn("4 tours ; tour le plus lent", journal_lines(recette.work)[-1]["evidence"])
        with self.assertRaises(drv.DriverRefusal) as refused:
            self.act(A, "conversation_longue", {"case": "fixture-case-1", "turns": 3})
        self.assertEqual(refused.exception.reason, "precondition")

    def test_an_abandoned_turn_is_journaled_with_the_turns_done(self):
        drv, recette, A = self.make()
        recette.open()
        self.long_case(recette, "v2-long-slow", budget_s=1, filler="SLOW tour {i}")
        self.act(A, "nouvelle_conversation")
        with self.assertRaises(drv.DriverRefusal) as refused:
            self.act(A, "conversation_longue", {"case": "v2-long-slow", "turns": 3})
        self.assertEqual(refused.exception.reason, "timeout")
        self.assertIn("au tour 2/3", refused.exception.detail)
        self.assertEqual([e["name"] for e in refused.exception.evidence], ["turns.json"])
        line = journal_lines(recette.work)[-1]
        self.assertEqual((line["case"], line["verdict"]), ("v2-long-slow", "KO"))
        self.assertIn("disponibilite", line["nfr"])
        self.assertIn("arrêt cliqué", line["evidence"])
        res, _ = self.act(A, "verdict_oracle", {"case": "v2-long-slow"})
        self.assertEqual((res["verdict"], res["replayed"]), ("KO", True))



@unittest.skipUnless(HAVE_PLAYWRIGHT, NO_PLAYWRIGHT)
class Recette2OnFixture(FixtureBench):
    """PROCEDURE-2 §8: what the second recette assumes of the pilot v2, on the bench."""

    def test_the_configuration_cases_are_judged_on_model_and_web(self):
        drv, recette, A = self.make()
        recette.open()
        with self.assertRaises(drv.DriverRefusal) as refused:
            self.act(A, "verdict_oracle", {"case": "M0-3"})
        self.assertIn("aucune observation pour M0-3", refused.exception.detail)
        res, _ = self.act(A, "choisir_modele", {"model": "claude haiku 5-5"})
        self.assertEqual(res["options"], ["Claude Haiku 5.5", "Gemini 3 Flash", "Mistral Large 3"])
        self.assertEqual(res["text"], "options : Claude Haiku 5.5 | Gemini 3 Flash | Mistral Large 3 ; "
                                      "choisi : Claude Haiku 5.5 ; relu : google/eu.claude-haiku-5-5")
        self.assertEqual(self.act(A, "poser_question", {"case": "M0-3"})[0]["answer"], res["text"])
        self.assertEqual(self.act(A, "verdict_oracle", {"case": "M0-3"})[0]["verdict"], "OK")
        res, _ = self.act(A, "recherche_web", {"mode": "off"})
        self.assertTrue(res["text"].startswith("mode : off (trouvé : auto)"), res["text"])
        self.assertEqual(self.act(A, "verdict_oracle", {"case": "M0-4"})[0]["verdict"], "OK")
        lines = journal_lines(recette.work)
        self.assertEqual([(x["case"], x["verdict"]) for x in lines], [("M0-3", "OK"), ("M0-4", "OK")])

    def test_a_model_read_in_the_list_at_run_time_is_held_to_the_banner(self):
        drv, recette, A = self.make()
        recette.open()
        recette.cases["v2-model-b"] = {"id": "v2-model-b", "level": "M3", "kind": "model", "model": "<modele-B>",
                                       "question": "Bonjour", "budget_s": 5, "nfr": ["conformite"],
                                       "must": ["bonjour"], "must_not": [], "pii": []}
        options = self.act(A, "choisir_modele", {"model": "claude haiku 5-5"})[0]["options"]
        self.act(A, "nouvelle_conversation")
        self.act(A, "choisir_modele", {"model": options[1]})  # what the harness copies from the list
        res, _ = self.act(A, "poser_question", {"case": "v2-model-b"})
        self.assertEqual(res["model"], "google/eu.gemini-3-flash")
        self.assertEqual(self.act(A, "verdict_oracle", {"case": "v2-model-b"})[0]["verdict"], "OK")
        line = journal_lines(recette.work)[-1]
        self.assertEqual(line["config"], {"model": "google/eu.gemini-3-flash", "web": "auto",
                                          "banner": "google/eu.gemini-3-flash"})

    def test_a_long_case_plays_its_messages_and_its_line_keeps_the_slowest_turn(self):
        drv, recette, A = self.make()
        recette.open()
        recette.cases["v2-messages"] = {"id": "v2-messages", "level": "M5", "kind": "long", "budget_s": 5, "turns": 3,
                                        "nfr": ["veracite"], "messages": ["Un", "Deux", "Trois", "Quatre"],
                                        "must": ["trois"], "must_not": [], "pii": []}
        self.act(A, "nouvelle_conversation")
        res, ev = self.act(A, "conversation_longue", {"case": "v2-messages", "turns": 3})
        turns = self.evidence_json(ev, "turns.json")
        self.assertEqual([t["question"] for t in turns["turns"]], ["Un", "Deux", "Trois"])
        slowest = max(turns["turns"], key=lambda t: t["total_s"])
        self.assertEqual(turns["slowest_turn"], slowest["turn"])
        self.assertEqual(self.act(A, "verdict_oracle", {"case": "v2-messages"})[0]["verdict"], "OK")
        line = journal_lines(recette.work)[-1]
        self.assertEqual(line["timing_s"], round(slowest["total_s"], 1))
        with self.assertRaises(drv.DriverRefusal) as refused:
            self.act(A, "conversation_longue", {"case": "v2-messages", "turns": 5})
        self.assertIn("turns must be 2 to 4", refused.exception.detail)

    def test_the_pinned_configuration_is_set_again_when_the_interface_loses_it(self):
        drv, recette, A = self.make()
        recette.open()
        self.act(A, "choisir_modele", {"model": "gemini"})
        self.act(A, "recherche_web", {"mode": "off"})
        res, _ = self.act(A, "nouvelle_conversation")
        self.assertIsNone(res["reapplied"], "kept by the page: nothing to set again")
        page = recette.browser.page()
        page.goto(recette.entry)  # a reload: the bench comes back with its defaults
        res, _ = self.act(A, "nouvelle_conversation")
        self.assertEqual(res["reapplied"], {"modèle": "google/eu.gemini-3-flash", "web": "off"})
        self.assertEqual(recette._composer_now(page), {"model": "google/eu.gemini-3-flash", "web": "off"})

    def test_charge_concurrente_sets_the_pinned_web_mode_in_both_tabs(self):
        drv, recette, A = self.make()
        recette.open()
        self.act(A, "recherche_web", {"mode": "off"})
        res, _ = self.act(A, "charge_concurrente", {"cases": ["fixture-case-1", "fixture-case-2"]})
        self.assertEqual((res["a"]["web"], res["b"]["web"]), ("off", "off"), "the second tab came up in auto")
        posts = [json.loads(p["body"])["web_search"] for p in recette.browser.posts[-2:]]
        self.assertEqual(posts, ["off", "off"])
        self.assertIn("recherche web off", journal_lines(recette.work)[-1]["evidence"])

    def test_an_attachment_leak_is_unverifiable_when_the_sent_text_lacks_the_document(self):
        drv, recette, A = self.make()
        recette.open()
        recette.cases["v2-attach-pii"] = {**recette.cases["fixture-case-1"], "id": "v2-attach-pii",
                                          "pii": ["850 euros"]}
        self.act(A, "nouvelle_conversation")
        self.act(A, "joindre_document", {"case": "v2-attach-pii"})
        _, ev = self.act(A, "poser_question", {"case": "v2-attach-pii"})
        self.assertIs(self.evidence_json(ev, "sent.json")["document_in_sent"], False)
        res, _ = self.act(A, "verdict_oracle", {"case": "v2-attach-pii"})
        self.assertEqual(res["leaks"], [])
        self.assertIn("le texte envoyé ne montre pas le document joint : fuite non vérifiable sur ce canal",
                      journal_lines(recette.work)[-1]["evidence"])

    def test_a_second_pass_is_compared_to_the_first(self):
        drv, recette, A = self.make()
        recette.open()
        base = {"level": "M1", "kind": "message", "question": "Relance pour M. Hugo Bernard, loyer impayé",
                "budget_s": 5, "nfr": ["securite"], "must": ["relance"], "must_not": [], "pii": ["Hugo Bernard"]}
        recette.cases["v2-a"] = {**base, "id": "v2-a"}
        recette.cases["v2-b"] = {**base, "id": "v2-b", "replay_of": "v2-a"}
        for cid in ("v2-a", "v2-b"):
            self.act(A, "nouvelle_conversation")
            self.act(A, "poser_question", {"case": cid})
            self.act(A, "verdict_oracle", {"case": cid})
        a, b = journal_lines(recette.work)[-2:]
        self.assertEqual(a["sent_sha256"], b["sent_sha256"])
        self.assertIn("passage a (v2-a) : OK ; texte envoyé identique", b["evidence"])



@unittest.skipUnless(HAVE_PLAYWRIGHT, NO_PLAYWRIGHT)
class FieldFixesOnFixture(FixtureBench):
    """The first live attempt of recette 2: MaskAI's maintenance page, and the French composer."""

    def maintenance_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/new?maintenance=1"

    def test_a_maintenance_page_is_a_precondition_refusal_and_a_ko_disponibilite_line(self):
        drv, recette, A = self.make()
        recette.open()
        self.message_case(recette, "v2-maint-1", "Bonjour")
        self.message_case(recette, "v2-maint-2", "Bonjour")
        recette.entry = self.maintenance_url()  # the service went into maintenance
        recette.browser.page().goto(recette.entry)
        with self.assertRaises(drv.DriverRefusal):
            self.act(A, "verdict_oracle", {"case": "v2-maint-1"})  # resumption probe: still to do
        t0 = time.monotonic()
        with self.assertRaises(drv.DriverRefusal) as refused:
            self.act(A, "nouvelle_conversation")
        self.assertLess(time.monotonic() - t0, 10, "no 15 s wait for a textarea that will not come")
        self.assertEqual((refused.exception.reason, refused.exception.detail), ("precondition", "service en maintenance"))
        res, _ = self.act(A, "verdict_oracle", {"case": "v2-maint-1"})
        self.assertEqual((res["verdict"], res["replayed"]), ("KO", False))
        line = journal_lines(recette.work)[-1]
        self.assertEqual((line["case"], line["evidence"]), ("v2-maint-1", "service en maintenance (page de maintenance affichée)"))
        self.assertIn("disponibilite", line["nfr"])
        # The next case is not judged on it: it is tried, and refused again on its own account.
        with self.assertRaises(drv.DriverRefusal) as probe:
            self.act(A, "verdict_oracle", {"case": "v2-maint-2"})
        self.assertIn("aucune observation pour v2-maint-2", probe.exception.detail)
        with self.assertRaises(drv.DriverRefusal):
            self.act(A, "poser_question", {"case": "v2-maint-2"})
        # Back in service: the guard reloads the stale maintenance page and the action goes through.
        recette.entry = f"http://127.0.0.1:{self.port}/new"
        self.act(A, "nouvelle_conversation")
        res, _ = self.act(A, "poser_question", {"case": "v2-maint-2"})
        self.assertIn("BONJOUR", res["answer"])
        self.assertEqual(self.act(A, "verdict_oracle", {"case": "v2-maint-2"})[0]["verdict"], "OK")

    def test_an_authentication_error_page_is_a_precondition_refusal_unless_the_entry_page_is_back(self):
        """18/10: a network blip bounced the session to /api/auth/error and the entry page came back
        on its own. The guard reloads the entry page once; an error page that stays is « session
        perdue », within seconds, never 15 s of waiting for a composer."""
        drv, recette, A = self.make()
        recette.open()
        self.act(A, "nouvelle_conversation")
        error_url = f"http://127.0.0.1:{self.port}/api/auth/error"
        recette.browser.page().goto(error_url)  # the blip: the entry page is still there behind it
        self.act(A, "nouvelle_conversation")
        recette.entry = error_url  # the session is gone: the reload lands on the error page again
        recette.browser.page().goto(error_url)
        t0 = time.monotonic()
        with self.assertRaises(drv.DriverRefusal) as refused:
            self.act(A, "nouvelle_conversation")
        self.assertLess(time.monotonic() - t0, 10, "no 15 s wait for a textarea that will not come")
        self.assertEqual(refused.exception.reason, "precondition")
        self.assertIn("session perdue", refused.exception.detail)

    def test_a_dictation_the_user_started_is_a_precondition_refusal_and_the_microphone_is_never_touched(self):
        """18/10: the composer has a dictation button (data-status). The driver never presses it; a
        dictation already running when an action starts is refused, the button left as it is."""
        drv, recette, A = self.make()
        recette.entry = f"http://127.0.0.1:{self.port}/new?dictating=1"
        recette.open()
        with self.assertRaises(drv.DriverRefusal) as refused:
            self.act(A, "nouvelle_conversation")
        self.assertEqual(refused.exception.reason, "precondition")
        self.assertIn("dictée en cours", refused.exception.detail)
        page = recette.browser.page()
        self.assertEqual(page.locator("#mic").get_attribute("data-status"), "listening")
        recette.entry = f"http://127.0.0.1:{self.port}/new"  # the user stopped dictating
        page.goto(recette.entry)
        self.act(A, "nouvelle_conversation")
        self.assertEqual(page.locator("#mic").get_attribute("data-status"), "idle")

    def test_maintenance_words_in_an_answer_are_no_maintenance(self):
        drv, recette, A = self.make()
        recette.open()
        self.act(A, "nouvelle_conversation")
        res, _ = self.act(A, "envoyer_message", {"text": "Maintenance en cours, on revient très vite"})
        self.assertIn("MAINTENANCE EN COURS", res["answer"])
        self.act(A, "nouvelle_conversation")  # the composer is there: no maintenance

    def test_the_harness_reads_refused_precondition_for_a_maintenance_page(self):
        work = self.tmp()
        env = dict(os.environ, VK_DRIVER_WORK_DIR=work, VK_DRIVER_PROFILE_DIR=self.tmp(),
                   VK_DRIVER_HEADLESS="1", VK_DRIVER_CHANNEL="", VK_DRIVER_HOSTS="127.0.0.1",
                   MASKAI_ENTRY_URL=self.maintenance_url())
        with open(os.path.join(work, "driver.log"), "wb") as log:
            p = subprocess.Popen([sys.executable, "-I", os.path.join(DRIVER_DIR, "driver.py")], env=env,
                                 cwd=work, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log)
            try:
                self.assertIn("hello", json.loads(p.stdout.readline()))
                p.stdin.write((json.dumps({"id": 1, "action": "nouvelle_conversation", "args": {},
                                           "deadline_ms": 60000, "evidence_dir": self.tmp()}) + "\n").encode())
                p.stdin.flush()
                answer = json.loads(p.stdout.readline())
            finally:
                p.stdin.close()
                p.wait(timeout=60)
                p.stdout.close()
        self.assertEqual((answer["id"], answer["ok"], answer["reason"]), (1, False, "precondition"))
        self.assertEqual((answer["result"]["refused"], answer["result"]["detail"]),
                         ("precondition", "service en maintenance"))

    def test_the_composer_buttons_are_found_by_structure_whatever_their_labels(self):
        drv, recette, A = self.make()
        recette.open()
        page = recette.browser.page()
        page.evaluate("""() => { document.getElementById('model').setAttribute('aria-label', 'Choisir le modèle');
                                document.getElementById('web').setAttribute('aria-label', 'Recherche sur le Web'); }""")
        res, _ = self.act(A, "choisir_modele", {"model": "gemini"})
        self.assertEqual(res["previous"], "Claude Haiku 5.5", "a label naming no model: the button's text")
        self.assertTrue(drv.model_matches("gemini", res["model"]), res)
        res, _ = self.act(A, "recherche_web", {"mode": "off"})
        self.assertEqual((res["mode"], res["previous"]), ("off", "auto"))
        # 18/10: the live composer is no <form>; the structure comes first, without it.
        self.assertEqual(drv.SELECTORS["model_button"][0], 'button[aria-haspopup="listbox"]')
        self.assertEqual(drv.SELECTORS["web_button"][0], 'button[data-mode]')


if __name__ == "__main__":
    unittest.main()
