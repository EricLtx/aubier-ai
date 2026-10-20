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
import urllib.parse
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
            if c["kind"] in ("message", "attach_question") and not c.get("action"):
                self.assertIn("question", c, c["id"])
            if c.get("action"):  # v2.3: the one action that plays the case, an action of the manifest
                self.assertIn(c["action"], manifest_evidence(), c["id"])
            if c.get("action") in ("renommer_conversation", "supprimer_conversation"):  # a conversation a question created
                self.assertEqual(c["kind"], "list", c["id"])
                self.assertIn("question", cases[c["same_conversation_as"]], c["id"])
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
        for n in range(10):  # PROCEDURE-2 §3 + PROCEDURE-3 §3: 108 cases since 19/10, M5 has one
            self.assertGreaterEqual(len(levels.get(f"M{n}", [])), 1, f"M{n}")
        self.assertEqual(sum(len(v) for k, v in levels.items() if k.startswith("M")), 108)
        self.assertEqual(len(levels["M3"]), 42, "3 questions × 14 models (18/10)")
        self.assertEqual((len(levels["M8"]), len(levels["M9"])), (8, 6), "recette 3: M8-1 … M8-8 (v2.3), M9-1 … M9-6")
        self.assertEqual({c["id"]: c["action"] for c in levels["M8"] if c.get("action")},
                         {"M8-4": "lister_conversations", "M8-5": "renommer_conversation",
                          "M8-6": "supprimer_conversation", "M8-8": "ouvrir_avis"})
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

    def test_headers_seen_on_2026_10_18_name_the_model_without_a_provider_or_with_two_slashes(self):
        """M3 on fourteen models (18/10): the Mistral and the older Claude entries sign « maskai
        <model> » without a provider, deepseek signs with two slashes; each is the chosen model."""
        drv = load_driver()
        for header, model in (("Aucune donnée personnelle détectée L'Aiguilleur : PASS maskai claude-haiku-4-5", "claude-haiku-4-5"),
                              ("Aucune donnée personnelle détectée L'Aiguilleur : PASS maskai mistral-large-4", "mistral-large-4"),
                              ("Aucune donnée personnelle détectée L'Aiguilleur : PASS maskai tensorix/deepseek/deepseek-v4-pro-0813",
                               "tensorix/deepseek/deepseek-v4-pro-0813"),
                              ("Aucune donnée personnelle détectée L'Aiguilleur : PASS maskai google/eu.claude-haiku-5-5",
                               "google/eu.claude-haiku-5-5")):
            g = drv.parse_guard(header + " OK", "OK", True)
            self.assertEqual(g["model"], model, header)
        self.assertTrue(drv.model_matches("claude-haiku-4-5", "claude-haiku-4-5"))
        self.assertTrue(drv.model_matches("deepseek v4 pro", "tensorix/deepseek/deepseek-v4-pro-0813"))
        self.assertTrue(drv.model_matches("mistral-large-4", "mistral-large-4"))
        self.assertFalse(drv.model_matches("mistral-large-3", "mistral-large-4"))

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

    def test_the_details_panel_gives_the_text_without_its_label(self):
        """v2.2: the sent text is the panel's text only; its label never reaches the digest."""
        drv = load_driver()
        q = "Rédige une relance pour M. Hugo Bernard au sujet du loyer."
        sent = "Rédige une relance pour <PERSON_1> au sujet du loyer."
        label = "Texte anonymisé envoyé au LLM :"
        self.assertEqual(drv.details_text(label + "\n\n" + sent, None, q), (sent, label))
        self.assertEqual(drv.details_text(label + " " + sent, None, q), (sent, label))
        self.assertEqual(drv.details_text("Anonymized text sent to the LLM\n" + sent, None, q),
                         (sent, "Anonymized text sent to the LLM"))
        # any first line ending with « : » that the question does not hold is a label
        self.assertEqual(drv.details_text("Message transmis au modèle :\n" + sent, None, q),
                         (sent, "Message transmis au modèle :"))
        # never a line of the question itself (corpus v3 questions open with « Voici un document : »)
        doc = "Voici un document :\n\nAGENCE RHÔNE GESTION"
        self.assertEqual(drv.details_text(doc, None, doc), (doc, None))
        # nor a first line that holds a masking token: that is sent text (the question's own line,
        # masked, is not in the question)
        greet = "Bonjour <PERSON_1> :\nMerci pour le virement de mars."
        self.assertEqual(drv.details_text(greet, None, "Bonjour M. Hugo Bernard :\nMerci pour le virement de mars."),
                         (greet, None))
        # the panel's text block first; a token in <code> is not the message
        self.assertEqual(drv.details_text(label + "\n" + sent, sent, q), (sent, label))
        self.assertEqual(drv.details_text("Libellé : " + sent, "<PERSON_1>", q), ("Libellé : " + sent, None))
        self.assertEqual(drv.details_text("  ", None, q), (None, None))
        self.assertEqual(drv.sent_digest(drv.details_text(label + "\n" + sent, None, q)[0]), drv.sent_digest(sent))


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
                         "ambigu entre : Gemini 3 Flash | Gemini 3 Pro ; relu : google/eu.claude-haiku-5-5 ; micro : absent")

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
            # v2.3 (E9): the chip's raw reading goes to chip.json, the evidence only
            self.assertEqual({e["name"] for e in ev}, {"attach.png", "network.json", "chip.json"})
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
            # before sending, cost about 3 s a case by design (v1: 5 s a run); v2.2 adds the card's
            # « Détails », opened, read and closed (under a second)
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
        time.sleep(4)
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
        # v2.3: the search, rename and delete of the conversation every-long created, the survey, the probe
        listing = {"level": "M8", "kind": "list", "budget_s": 10, "nfr": ["disponibilite"], "must": [], "must_not": [],
                   "pii": []}
        recette.cases["every-search"] = {**listing, "id": "every-search", "action": "lister_conversations",
                                         "question": "Bonjour"}
        recette.cases["every-rename"] = {**listing, "id": "every-rename", "action": "renommer_conversation",
                                         "same_conversation_as": "every-long", "question": "Banc renommé"}
        recette.cases["every-delete"] = {**listing, "id": "every-delete", "action": "supprimer_conversation",
                                         "same_conversation_as": "every-long"}
        recette.cases["every-avis"] = {**listing, "id": "every-avis", "kind": "message", "action": "ouvrir_avis"}
        self.assertEqual(run("lister_conversations", {"case": "every-search"})["count"], 1)
        self.assertIn("titre relu « Banc renommé »", run("renommer_conversation", {"case": "every-rename"})["text"])
        self.assertEqual(run("supprimer_conversation", {"case": "every-delete"})["state"], "supprimée")
        self.assertEqual(run("ouvrir_avis", {"case": "every-avis"})["closed_by"], "croix")
        self.assertEqual(run("sonder_interface")["errors"], [])
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
        # v2.2: the « Détails » trigger follows the guard panel on every card (18/10)
        self.assertEqual(guard["guard_text"], "Aucune donnée personnelle détectée L'Aiguilleur : PASS Détails")
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

    def test_a_popover_that_shows_a_status_is_not_taken_for_the_sent_text(self):
        """If the live popover only says « no PII detected », the leak oracle must say it could not
        see the sent text, never judge the status as if it were the text (a false « no leak »).
        v2.2: the popover is the fallback of a card without « Détails » (`nodetails=1`)."""
        drv, recette, A = self.make()
        recette.entry = f"http://127.0.0.1:{self.port}/new?nodetails=1"
        recette.open()
        recette.cases["v2-status"] = {"id": "v2-status", "level": "N9", "kind": "message",
                                      "question": "Relance pour M. Hugo Bernard POPOVER_STATUS", "budget_s": 5,
                                      "nfr": ["conformite"], "must": ["relance"], "must_not": [],
                                      "pii": ["Hugo Bernard"]}
        self.act(A, "nouvelle_conversation")
        res, ev = self.act(A, "poser_question", {"case": "v2-status"})
        sent = self.evidence_json(ev, "sent.json")
        self.assertIsNone(sent["source"])
        # a card without the trigger: source null, and the card says why; the popover says why not it
        self.assertEqual(sent["rejected"], "pas de bouton Détails sur la carte")
        self.assertEqual(sent["popover_rejected"], "le popover ne montre pas le texte de la question")
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
        must not hold every answer 30 s, nor be clicked at an abandon. Since round 2 (click-targets-4) the
        stop is looked for in the composer only: the lookalike sits there, where it can still be taken."""
        drv, recette, A = self.make()
        recette.open()
        self.act(A, "nouvelle_conversation")
        page = recette.browser.page()
        page.evaluate("""() => { const b = document.createElement('button');
            b.setAttribute('aria-label', 'Stop sharing'); b.textContent = 'Stop sharing';
            b.onclick = () => { window.__lookalike = (window.__lookalike || 0) + 1; };
            document.querySelector('#composer').prepend(b); }""")
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
class CardDetailsOnFixture(FixtureBench):
    """Pilot v2.2: the sent text read in the answer card's « Détails » collapsible, in the markup of
    a card stored by recette 2 on 18/10; the composer popover as the fallback of a card without it."""

    IBAN = "FR76 3000 6000 0112 3456 7890 189"
    LABEL = "Texte anonymisé envoyé au LLM :"
    SENT = "Rédige une relance pour le virement sur l'IBAN <IBAN_1> avant vendredi."
    STATE_JS = """() => [...document.querySelectorAll('article')].map((a) => {
        const t = a.querySelector('[data-slot="collapsible-trigger"]'), c = a.querySelector('[data-slot="collapsible-content"]');
        return [t.getAttribute('aria-expanded'), c.dataset.state, c.hidden]; })"""

    def iban_case(self, recette, cid: str, extra: str = "") -> None:
        recette.cases[cid] = {"id": cid, "level": "N9", "kind": "message",
                              "question": f"Rédige une relance pour le virement sur l'IBAN {self.IBAN} avant vendredi.{extra}",
                              "budget_s": 5, "nfr": ["conformite"], "must": ["relance"], "must_not": [],
                              "pii": [self.IBAN]}

    def test_the_sent_text_is_read_from_the_card_details_after_the_answer(self):
        drv, recette, A = self.make()
        recette.open()
        self.iban_case(recette, "v22-iban")
        self.act(A, "nouvelle_conversation")
        res, ev = self.act(A, "poser_question", {"case": "v22-iban"})
        sent = self.evidence_json(ev, "sent.json")
        self.assertEqual((sent["source"], sent["closed"], sent["content_settled"]), ("card-details", True, True), sent)
        self.assertEqual(sent["trigger"], drv.SELECTORS["card_details_trigger"][0], "structure first")
        self.assertEqual(sent["trigger_label"], "Voir le texte anonymisé envoyé au LLM")
        self.assertEqual((sent["content"], sent["content_state"]), (drv.SELECTORS["card_details_content"], "open"))
        self.assertEqual(sent["card"], {"selector": drv.SELECTORS["assistant_message"][0], "index": 0})
        self.assertIn('data-slot="collapsible-content"', sent["content_html"])
        # the text only: the panel's text block, its label kept apart
        self.assertEqual((sent["text"], sent["label"], sent["block"]), (self.SENT, self.LABEL, "pre"))
        self.assertTrue(sent["content_text"].startswith(self.LABEL), sent["content_text"])
        self.assertNotIn("rejected", sent)
        self.assertIn(self.IBAN, sent["post_body"], "the POST is the raw text, masked by the server")
        self.assertEqual(sent["composer_popover"]["source"], "composer-popover", "read before sending, kept, unused")
        # sent_to_model is the card's text: the token, never the IBAN
        self.assertEqual(res["sent_source"], "card-details")
        self.assertEqual(res["sent_to_model"], self.SENT)
        # the answer and its header were read before the panel opened; the panel is closed again
        self.assertNotIn(self.LABEL, res["answer"])
        self.assertEqual(self.evidence_json(ev, "guard.json")["tokens"], ["<IBAN_1>"])
        page = recette.browser.page()
        self.assertEqual(page.evaluate(self.STATE_JS), [["false", "closed", True]])
        # the leak check is evaluated on it: tokenised, OK
        res, _ = self.act(A, "verdict_oracle", {"case": "v22-iban"})
        self.assertEqual((res["verdict"], res["leaks"]), ("OK", []))
        line = journal_lines(recette.work)[-1]
        self.assertNotIn("texte envoyé non observé", line["evidence"])
        self.assertEqual(line["sent_sha256"], drv.sent_digest(self.SENT), "the text's digest, never the label's")
        # The same question with LEAK: the « Détails » shows the IBAN in clear: KO sécurité.
        self.iban_case(recette, "v22-leak", " LEAK")
        self.act(A, "nouvelle_conversation")
        _, ev = self.act(A, "poser_question", {"case": "v22-leak"})
        sent = self.evidence_json(ev, "sent.json")
        self.assertEqual(sent["source"], "card-details", "read on the card, never the fallback (LEAK leaks both)")
        self.assertEqual(sent["text"], f"Rédige une relance pour le virement sur l'IBAN {self.IBAN} avant vendredi. LEAK")
        res, _ = self.act(A, "verdict_oracle", {"case": "v22-leak"})
        self.assertEqual((res["verdict"], res["leaks"]), ("KO", [self.IBAN]))
        line = journal_lines(recette.work)[-1]
        self.assertIn("securite", line["nfr"], "a leak to the model is KO sécurité")
        self.assertIn("KO sécurité : donnée identifiante en clair", line["evidence"])
        self.assertNotIn("texte envoyé non observé", line["evidence"])
        # Two cards in one conversation: only the last card's trigger is clicked, and its text read;
        # another panel of that card, already open between the trigger and its own content (a
        # « Réflexion », say), is never taken for it.
        page.evaluate("""() => { window.__first_clicks = 0; document.querySelector('article [data-slot="collapsible-trigger"]')
                                   .addEventListener('click', () => { window.__first_clicks += 1; });
            new MutationObserver((records) => { for (const r of records) for (const n of r.addedNodes) {
                if (n.tagName !== 'ARTICLE') continue;
                const other = document.createElement('div');
                other.dataset.slot = 'collapsible-content'; other.dataset.state = 'open'; other.className = 'other';
                other.textContent = 'Réflexion : le modèle a pesé deux réponses';
                n.insertBefore(other, n.querySelector('[data-slot="collapsible-content"]')); } })
              .observe(document.getElementById('messages'), { childList: true }); }""")
        res, ev = self.act(A, "envoyer_message", {"text": "Et pour le loyer de mars ?"})
        self.assertEqual(self.evidence_json(ev, "sent.json")["card"]["index"], 1)
        self.assertEqual(res["sent_to_model"], "Et pour le loyer de mars ?")
        self.assertEqual(page.evaluate("() => window.__first_clicks"), 0, "never the first card's")
        page.evaluate("() => document.querySelectorAll('.other').forEach((o) => o.remove())")
        self.assertEqual(page.evaluate(self.STATE_JS), [["false", "closed", True]] * 2)
        # An open collapsible is no dialog: opened by hand, the guard neither refuses it nor closes it.
        page.locator("article").last.locator('[data-slot="collapsible-trigger"]').click()
        self.assertFalse(page.evaluate("""(m) => [...document.querySelectorAll('[data-slot="collapsible-content"]')]
                                              .some((c) => !!c.closest(m) || !!c.querySelector(m))""",
                                       drv.SELECTORS["modal"]))
        self.act(A, "capture_ecran")
        self.assertEqual(page.evaluate(self.STATE_JS)[-1], ["true", "open", False], "left as the founder set it")

    def test_without_a_details_button_the_composer_popover_is_the_fallback(self):
        """`nodetails=1`: cards without the collapsible. The popover read before sending is the sent
        text (v2), closed again; the card's reading says why it was not used."""
        drv, recette, A = self.make()
        recette.entry = f"http://127.0.0.1:{self.port}/new?nodetails=1"
        recette.open()
        recette.cases["v2-pii"] = {"id": "v2-pii", "level": "N9", "kind": "message",
                                   "question": "Rédige une relance courtoise pour M. Hugo Bernard au sujet du loyer.",
                                   "budget_s": 5, "nfr": ["conformite"], "must": ["relance"], "must_not": [],
                                   "pii": ["Hugo Bernard"]}
        self.act(A, "nouvelle_conversation")
        res, ev = self.act(A, "poser_question", {"case": "v2-pii"})
        page = recette.browser.page()
        self.assertEqual(page.evaluate("() => document.querySelectorAll('[data-slot=\"collapsible-trigger\"]').length"), 0)
        sent = self.evidence_json(ev, "sent.json")
        self.assertEqual((sent["source"], sent["closed"], sent["block_found"]), ("composer-popover", "escape", True))
        self.assertEqual(sent["text"], "Rédige une relance courtoise pour <PERSON_1> au sujet du loyer.")
        self.assertEqual((sent["card"]["source"], sent["card"]["missing"], sent["card"]["rejected"]),
                         (None, "trigger", "pas de bouton Détails sur la carte"))
        self.assertNotIn("rejected", sent)
        self.assertIn("1 PII", sent["trigger_label"])
        self.assertIn("Hugo Bernard", sent["post_body"], "the POST is the raw text, masked by the server")
        self.assertEqual(res["sent_source"], "composer-popover")
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

    def test_the_reveal_toggle_is_never_pressed(self):
        """The card's PII reveal toggle (18/10) is never pressed; its state at read time is recorded,
        pressed or not, and left as it is."""
        for revealed in (False, True):
            with self.subTest(revealed=revealed):
                drv, recette, A = self.make()
                if revealed:
                    recette.entry = f"http://127.0.0.1:{self.port}/new?revealed=1"
                recette.open()
                self.iban_case(recette, "v22-reveal")
                self.act(A, "nouvelle_conversation")
                _, ev = self.act(A, "poser_question", {"case": "v22-reveal"})
                sent = self.evidence_json(ev, "sent.json")
                self.assertEqual((sent["source"], sent["reveal_pressed"]), ("card-details", revealed))
                page = recette.browser.page()
                self.assertEqual(page.evaluate("() => window.__reveal_clicks"), 0)
                self.assertEqual(page.locator('article [data-slot="pii-reveal-toggle"]').get_attribute("aria-pressed"),
                                 "true" if revealed else "false")
                recette.browser.close()

    def test_a_details_panel_that_shows_a_status_is_not_taken_for_the_sent_text(self):
        """A « Détails » that shows a status line, not the text: no sent text observed, never a false
        « no leak »; and no fallback to the popover, since the card has its trigger."""
        drv, recette, A = self.make()
        recette.open()
        recette.cases["v22-status"] = {"id": "v22-status", "level": "N9", "kind": "message",
                                       "question": "Relance pour M. Hugo Bernard DETAILS_STATUS", "budget_s": 5,
                                       "nfr": ["conformite"], "must": ["relance"], "must_not": [],
                                       "pii": ["Hugo Bernard"]}
        self.act(A, "nouvelle_conversation")
        res, ev = self.act(A, "poser_question", {"case": "v22-status"})
        sent = self.evidence_json(ev, "sent.json")
        self.assertEqual(sent["source"], "card-details", "read on the card; rejected says why it does not count")
        self.assertEqual(sent["rejected"], "le panneau « Détails » ne montre pas le texte de la question")
        self.assertIn("Aucune PII détectée", sent["text"], "kept as read, for the debrief")
        self.assertEqual((sent["closed"], sent["composer_popover"]["source"]), (True, "composer-popover"))
        self.assertNotIn("sent_to_model", res)
        self.act(A, "verdict_oracle", {"case": "v22-status"})
        self.assertIn("texte envoyé non observé", journal_lines(recette.work)[-1]["evidence"])

    def test_the_text_fallbacks_never_click_the_reveal_toggle_or_a_button_of_the_answer(self):
        """The `:has-text("Détails")` selectors: never the PII reveal toggle, even one carrying a
        hidden « Afficher les détails » ahead of the trigger (`nostructure=1`); never a « Détails »
        button inside the answer's body."""
        drv, recette, A = self.make()
        recette.entry = f"http://127.0.0.1:{self.port}/new?nostructure=1"
        recette.open()
        self.iban_case(recette, "v22-nostructure")
        self.act(A, "nouvelle_conversation")
        _, ev = self.act(A, "poser_question", {"case": "v22-nostructure"})
        sent = self.evidence_json(ev, "sent.json")
        self.assertEqual((sent["source"], sent["trigger"], sent["closed"], sent["text"]),
                         ("card-details", drv.SELECTORS["card_details_trigger"][2], True, self.SENT), sent)
        page = recette.browser.page()
        self.assertEqual(page.evaluate("() => window.__reveal_clicks"), 0, "the reveal toggle was never pressed")
        self.assertEqual(page.locator('article [data-slot="pii-reveal-toggle"]').get_attribute("aria-pressed"), "false")
        recette.browser.close()
        # A card without « Détails » whose answer holds a « Détails » button: never clicked.
        drv, recette, A = self.make()
        recette.entry = f"http://127.0.0.1:{self.port}/new?nodetails=1"
        recette.open()
        self.message_case(recette, "v22-body", "Bonjour BODY_DETAILS")
        self.act(A, "nouvelle_conversation")
        res, ev = self.act(A, "poser_question", {"case": "v22-body"})
        self.assertIn("Détails", res["answer"])
        sent = self.evidence_json(ev, "sent.json")
        self.assertEqual((sent["card"]["missing"], sent["card"]["rejected"]), ("trigger", "pas de bouton Détails sur la carte"))
        self.assertEqual(recette.browser.page().evaluate("() => window.__body_clicks || 0"), 0)

    def test_the_aria_controls_branch_reads_the_panel_the_trigger_names(self):
        """Radix's CollapsibleTrigger names its content (aria-controls, `controls=1`): read there."""
        drv, recette, A = self.make()
        recette.entry = f"http://127.0.0.1:{self.port}/new?controls=1"
        recette.open()
        self.iban_case(recette, "v22-controls")
        self.act(A, "nouvelle_conversation")
        _, ev = self.act(A, "poser_question", {"case": "v22-controls"})
        sent = self.evidence_json(ev, "sent.json")
        named = recette.browser.page().evaluate(
            """() => document.querySelector('article [data-slot="collapsible-trigger"]').getAttribute('aria-controls')""")
        self.assertTrue(named)
        self.assertEqual((sent["source"], sent["content"], sent["closed"], sent["text"]),
                         ("card-details", "#" + named, True, self.SENT))

    def test_a_panel_that_does_not_open_is_left_as_it_was_and_the_host_deadline_is_respected(self):
        """A trigger whose click opens nothing: no blind second click, `source` stays card-details
        with `rejected`, no fallback to the popover. Under MARGIN_S + 3 s before the host's
        deadline, the panel is not read at all."""
        drv, recette, A = self.make()
        recette.open()
        self.iban_case(recette, "v22-dead")
        self.act(A, "nouvelle_conversation")
        page = recette.browser.page()
        page.evaluate("""() => { window.__dead_clicks = 0;
            new MutationObserver((records) => { for (const r of records) for (const n of r.addedNodes) {
                if (n.tagName !== 'ARTICLE') continue;
                const t = n.querySelector('[data-slot="collapsible-trigger"]');
                t.onclick = null; t.addEventListener('click', () => { window.__dead_clicks += 1; }); } })
              .observe(document.getElementById('messages'), { childList: true }); }""")
        res, ev = self.act(A, "poser_question", {"case": "v22-dead"})
        sent = self.evidence_json(ev, "sent.json")
        self.assertEqual((sent["source"], sent["rejected"], sent["closed"], sent["text"]),
                         ("card-details", "le panneau ne s'est pas ouvert", None, None), sent)
        self.assertEqual(sent["composer_popover"]["source"], "composer-popover", "valid, yet no fallback")
        self.assertNotIn("sent_to_model", res)
        self.assertEqual(page.evaluate("() => window.__dead_clicks"), 1, "the open click only")
        self.assertEqual(page.evaluate(self.STATE_JS), [["false", "closed", True]])
        self.act(A, "verdict_oracle", {"case": "v22-dead"})
        self.assertIn("texte envoyé non observé", journal_lines(recette.work)[-1]["evidence"])
        # The host's deadline: fewer than MARGIN_S + 3 s left, the panel is not even probed.
        drv.remaining_s = lambda: drv.MARGIN_S + 2
        turn = {"count0": 0, "text": recette.cases["v22-dead"]["question"], "pii": [self.IBAN]}
        info = recette._read_card_details(page, turn)
        self.assertEqual((info["source"], info["rejected"], info["text"]), ("card-details", "délai de l'hôte", None))
        self.assertEqual(page.evaluate("() => window.__dead_clicks"), 1, "no click")


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
        self.assertTrue(all(t["display_stable"] and t["sent_source"] == "card-details" for t in turns), turns)
        self.assertEqual([t["sent_to_model"] for t in turns], [t["question"] for t in turns],
                         "each turn read on its own card, its text only")
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
                                      "choisi : Claude Haiku 5.5 ; relu : google/eu.claude-haiku-5-5 ; micro : idle")
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
                                          "banner": "google/eu.gemini-3-flash", "mic": "idle"})

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
        self.assertEqual(recette._composer_now(page), {"model": "google/eu.gemini-3-flash", "web": "off", "mic": "idle"})

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

    def test_a_profile_not_signed_in_is_a_precondition_refusal_not_a_load_measure(self):
        """18/10, live: the driver's Chrome profile was not signed in; the entry page loaded, then
        sent the frame to authentification.maskai.fr, and M0-1 measured that login page as the app.
        Now: « non connecté », within seconds, on mesurer_chargement and on every action that needs
        the app; signed in again, the measure is the app's."""
        drv, recette, A = self.make()
        recette.entry = f"http://127.0.0.1:{self.port}/new?login=1"
        recette.open()
        with self.assertRaises(drv.DriverRefusal) as refused:
            self.act(A, "mesurer_chargement")
        self.assertEqual(refused.exception.reason, "precondition")
        self.assertIn("non connecté", refused.exception.detail)
        self.assertIn("localhost", refused.exception.detail)
        self.assertNotIn("@load", recette.observations)
        t0 = time.monotonic()
        with self.assertRaises(drv.DriverRefusal) as refused:
            self.act(A, "nouvelle_conversation")
        self.assertLess(time.monotonic() - t0, 12, "no 15 s wait for a textarea that will not come")
        self.assertIn("non connecté", refused.exception.detail)
        recette.entry = f"http://127.0.0.1:{self.port}/new"  # signed in: the entry page stays
        res, _ = self.act(A, "mesurer_chargement")
        self.assertIn("127.0.0.1", res["text"])

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


class PilotV23Units(unittest.TestCase):
    """Pilot v2.3 (PROCEDURE-3 §6), what needs no browser: the sent-text inventory (E7), the error classes
    and their sanitising (E9, E10), the conversation menu's safety rule (E3), the shared observations."""

    def recette(self, drv):
        work, profile = tempfile.mkdtemp(prefix="maskai-recette-"), tempfile.mkdtemp(prefix="maskai-recette-")
        self.addCleanup(shutil.rmtree, work, True)
        self.addCleanup(shutil.rmtree, profile, True)
        return drv.Recette(env={"VK_DRIVER_WORK_DIR": work, "VK_DRIVER_PROFILE_DIR": profile,
                                "VK_DRIVER_HOSTS": "127.0.0.1"})

    def test_the_sent_text_inventory_and_the_masked_sets_compared(self):
        drv = load_driver()
        self.assertEqual(drv.token_inventory("Relance <PERSON_1>, <PERSON_2> au <PHONE_1> ; <PERSON_1>"),
                         {"PERSON": 3, "PHONE": 1})
        self.assertEqual(drv.token_inventory("sans jeton"), {})
        self.assertIsNone(drv.token_inventory(None), "not observed is not « no token »")
        self.assertEqual(drv.inventory_note({"PERSON": 2, "IBAN": 1}, {"IBAN": 1, "PERSON": 2}), "types masqués identiques")
        self.assertEqual(drv.inventory_note({"PERSON": 2, "IBAN": 1}, {"PERSON": 1, "PHONE": 3}),
                         "types masqués différents : +PHONE ×3 −IBAN −PERSON")
        self.assertEqual(drv.inventory_note({}, None), "types masqués non comparables (texte envoyé non observé au passage b)")
        g = drv.parse_guard("Anonymisé → <PERSON_1> <PHONE_1> L'Aiguilleur : PASS·Le Chirurgien : 2 PII détectées Détails "
                            "maskai google/eu.claude-haiku-5-5 noté", "noté", True)
        self.assertEqual(g["pii_count"], 2)
        self.assertEqual(drv.parse_guard("Aucune donnée personnelle détectée L'Aiguilleur : PASS maskai x/y OK", "OK",
                                         True)["pii_count"], 0)

    def test_an_error_shown_by_the_page_reaches_the_line_without_host_path_or_stack(self):
        drv = load_driver()
        raw = ('faildb.txt Failed: psycopg2.OperationalError: could not translate host name "docstore-db.internal.maskai.local" '
               'to address: Name or service not known Traceback (most recent call last): File "/srv/maskai/ingest/worker.py", '
               'line 88, in index')
        clean = drv.sanitise(raw, 500, keep=("faildb.txt",))
        for bad in ("docstore-db", "maskai.local", "/srv/", "worker.py", "Traceback", "line 88", "psycopg2."):
            self.assertNotIn(bad, clean)
        self.assertIn("faildb.txt", clean, "the attached file's own name is kept")
        self.assertIn("could not translate host name", clean)
        self.assertLessEqual(len(drv.sanitise(raw)), drv.EXCERPT_MAX)
        for text in ("connect to server at 10.0.3.4:5432 failed", "GET https://intra.example.net/v1/x?k=1 : 500",
                     "host=db-primary.svc.cluster.local port=5432", "at Object.run (/app/node_modules/x/index.js:12:7)",
                     "upstream pii-guard.internal:8443 unavailable"):
            out = drv.sanitise(text, 500)
            for bad in ("10.0.3.4", "intra.example", "db-primary", "node_modules", "index.js", "pii-guard", "8443"):
                self.assertNotIn(bad, out, text)
        self.assertTrue(drv.exposes_internals(raw, keep=("faildb.txt",)))
        self.assertFalse(drv.exposes_internals("case1.txt Failed", keep=("case1.txt",)))
        self.assertEqual(drv.attach_error_class(raw, "faildb.txt"), "service documentaire injoignable")
        self.assertEqual(drv.attach_error_class("x.pdf Failed: unsupported file format", "x.pdf"), "format refusé")
        self.assertEqual(drv.attach_error_class("x.pdf Échec : fichier trop volumineux", "x.pdf"), "fichier trop volumineux")
        self.assertEqual(drv.attach_error_class("x.pdf Failed", "x.pdf"), "échec de la pièce jointe")
        self.assertEqual(drv.attach_error_class("format_size_database.csv Failed", "format_size_database.csv"),
                         "échec de la pièce jointe", "the file's name says nothing of the error")

    def test_a_silent_failure_is_classed_from_the_stream_and_the_page(self):
        drv = load_driver()
        events = [[0, "<START>"], [10, 'data: {"node": "detect_pii"}'], [20, 'data: {"node": "detect_pii"}'],
                  [30, "event: error"],
                  [31, 'data: {"error": "psycopg2.OperationalError: could not translate host name \'pii-db.internal\' to add'],
                  [40, "<END>"]]
        d = drv.stream_digest(events)
        self.assertEqual(d["names"], ["<START>", "node:detect_pii ×2", "event:error", "error", "<END>"])
        self.assertEqual(d["last_node"], "detect_pii")
        self.assertIn("could not translate host name", d["error"], "a payload cut at 160 characters is still read")
        self.assertEqual(drv.failure_class(200, d["error"], [], d["names"], True), "service interne injoignable")
        self.assertEqual(drv.failure_class(502, None, [], [], True), "erreur serveur (HTTP 502)")
        self.assertEqual(drv.failure_class(429, None, [], [], True), "limite de débit (HTTP 429)")
        self.assertEqual(drv.failure_class(401, None, [], [], True), "session refusée (HTTP 401)")
        self.assertEqual(drv.failure_class(404, None, [], [], True), "requête refusée (HTTP 404)")
        self.assertEqual(drv.failure_class(200, "Rate limited", [], [], True), "erreur signalée par le flux")
        self.assertEqual(drv.failure_class(200, None, [["status", "Une erreur est survenue"]], [], True),
                         "erreur affichée par l'application")
        self.assertEqual(drv.failure_class(200, None, [], ["<START>", "<ABORT>"], True), "flux coupé")
        self.assertEqual(drv.failure_class(200, None, [], ["<START>", "node:detect_pii ×2", "<END>"], True),
                         "flux terminé sans réponse")
        self.assertEqual(drv.failure_class(None, None, [], [], False), "aucun flux")
        self.assertEqual(drv.stream_digest([[0, 'data: {"type": "error", "message": "quota"}']])["error"], "quota")
        self.assertIsNone(drv.stream_digest([[0, 'data: {"message": "bonjour"}']])["error"], "a message is no error")

    def test_a_menu_is_a_conversation_menu_only_with_one_rename_and_one_delete(self):
        """E3: never « Supprimer tout », never a menu that is not a conversation's."""
        drv = load_driver()

        def item(label, disabled=False):
            return {"text": drv.evaluer.fold(label), "label": label, "disabled": disabled}

        menu = [item("Renommer"), item("Partager"), item("Supprimer")]
        self.assertEqual(drv.menu_entry(menu, "supprimer")[0]["label"], "Supprimer")
        self.assertEqual(drv.menu_entry(menu, "renommer")[0]["label"], "Renommer")
        self.assertEqual(drv.menu_entry([item("Rename"), item("Delete chat")], "supprimer")[0]["label"], "Delete chat")
        for bad in ([item("Supprimer")], [item("Renommer"), item("Supprimer"), item("Supprimer tout l'historique")],
                    [item("Rename"), item("Delete"), item("Delete all")], [item("Renommer"), item("Supprimer", True)],
                    [item("Renommer"), item("Supprimer"), item("Supprimer définitivement")],
                    [item("Renommer"), item("Supprimer"), item("Vider la conversation")]):
            pick, why = drv.menu_entry(bad, "supprimer")
            self.assertIsNone(pick, bad)
            self.assertTrue(why)
        self.assertTrue(drv.same_title("Recette trois renommée", "Recette trois renommée"))
        self.assertTrue(drv.same_title("Recette trois, fiche pratique : qu'est-ce…",
                                       "Recette trois, fiche pratique : qu'est-ce qu'un état des lieux"))
        self.assertFalse(drv.same_title("Recette trois, fiche pratique", "Recette trois renommée"))

    def test_a_shared_observation_judges_one_case_unless_a_case_was_probed_before_it(self):
        """N7-1 and N7-2 on one listing (recette 1, both probed first); M8-4 never on M8-2's listing; M8-7
        never on the model chosen for M8-1's question; the snapshot kept for an offline replay."""
        drv = load_driver()
        recette = self.recette(drv)
        actions = recette.actions()
        ev = tempfile.mkdtemp(prefix="maskai-recette-")
        self.addCleanup(shutil.rmtree, ev, True)

        def verdict(cid):
            return actions["verdict_oracle"]({"case": cid}, ev)[0]["verdict"]

        for cid in ("N7-1", "N7-2"):
            with self.assertRaises(drv.DriverRefusal):
                verdict(cid)
        recette._shared("@list", {"answer": "2 conversations : Quelle est la capitale du Portugal ? | Bonjour",
                                  "timing_s": None})
        self.assertEqual((verdict("N7-1"), verdict("N7-2")), ("OK", "OK"))
        with self.assertRaises(drv.DriverRefusal):
            verdict("M8-2")
        recette._shared("@list", {"answer": "1 conversation : Recette trois, fiche pratique : qu'est-c", "timing_s": None})
        self.assertEqual(verdict("M8-2"), "OK")
        with self.assertRaises(drv.DriverRefusal) as refused:
            verdict("M8-4")
        self.assertEqual(refused.exception.detail, "aucune observation pour M8-4 : lister_conversations d'abord")
        recette._shared("@list", {"answer": "recherche « Recette trois » : 1 résultat : Recette trois, fiche pratique : "
                                            "qu'est-c", "timing_s": None, "case": "M8-4"})
        self.assertEqual(verdict("M8-4"), "OK")
        self.assertIn("recherche « Recette trois »", recette.observations["M8-4"]["observed"]["answer"])
        recette._shared("@model", {"answer": "options : claude haiku 5-5 ; choisi : claude haiku 5-5 ; relu : "
                                             "google/eu.claude-haiku-5-5 ; micro : idle", "timing_s": 1.0})
        recette.observations["M8-1"] = {"answer": "Un état des lieux d'entrée décrit le logement.", "timing_s": 3.0,
                                        "guard": {"model": "google/eu.claude-haiku-5-5"},
                                        "composer": {"model": "google/eu.claude-haiku-5-5", "web": "off", "mic": "idle"},
                                        "sent_info": {"source": None}}
        self.assertEqual(verdict("M8-1"), "OK")
        with self.assertRaises(drv.DriverRefusal) as refused:
            verdict("M8-7")
        self.assertIn("choisir_modele d'abord", refused.exception.detail)
        recette._shared("@model", {"answer": "options : claude haiku 5-5 ; choisi : claude haiku 5-5 ; relu : "
                                             "google/eu.claude-haiku-5-5 ; micro : idle", "timing_s": 1.0})
        self.assertEqual(verdict("M8-7"), "OK")
        line = journal_lines(recette.work)[-1]
        self.assertEqual((line["case"], line["modals_closed"]), ("M8-7", []))

    def test_every_selector_is_a_page_address_or_a_named_pattern(self):
        drv = load_driver()
        self.assertLessEqual(set(drv.SELECTOR_PATTERNS), set(drv.SELECTORS))
        for key, value in drv.SELECTORS.items():
            self.assertIsInstance(value, (str, list, dict), key)
            if key not in drv.SELECTOR_PATTERNS:
                self.assertIsInstance(value, (str, list), key)
        self.assertEqual([k["name"] for k in drv.KNOWN_MODALS if k.get("own")], ["envoi", "recherche", "renommer",
                                                                                   "suppression"])


@unittest.skipUnless(HAVE_PLAYWRIGHT, NO_PLAYWRIGHT)
class PilotV23OnFixture(FixtureBench):
    """Pilot v2.3 (PROCEDURE-3 §6, E1 to E11) on the bench, in the shapes of 18–19/10."""

    TITLE = "Recette banc : premier message"
    ACCEPT = ".txt,.md,.json,.csv,.pdf,.docx,.xlsx,.pptx,.png,.jpg,.jpeg"
    HREFS = "() => [...document.querySelectorAll('#conversations a')].map((a) => a.getAttribute('href'))"
    TITLES = "() => [...document.querySelectorAll('#conversations a')].map((a) => a.textContent)"

    def first_message(self, recette, A, cid="t-a"):
        recette.cases[cid] = {"id": cid, "level": "M8", "kind": "message", "question": self.TITLE, "budget_s": 5,
                              "nfr": ["disponibilite"], "must": ["recette banc"], "must_not": [], "pii": []}
        self.act(A, "nouvelle_conversation")
        self.act(A, "poser_question", {"case": cid})
        return recette._registered(cid)

    @staticmethod
    def list_case(recette, cid, **over):
        recette.cases[cid] = {"id": cid, "level": "M8", "kind": "list", "budget_s": 10, "nfr": ["disponibilite"],
                              "must": [], "must_not": [], "pii": [], **over}

    def test_the_registry_holds_the_conversations_the_run_created(self):
        """E1: the conversation a question created, with its case, in the work directory; a question in it
        creates nothing; same_conversation_as resolves through the registry after a relaunch."""
        drv, recette, A = self.make()
        recette.open()
        entry = self.first_message(recette, A)
        page = recette.browser.page()
        self.assertEqual(entry["href"], drv.urlsplit(page.url).path)
        self.assertEqual((entry["case"], entry["state"], entry["title"], entry["via"]),
                         ("t-a", "créée", self.TITLE, "poser_question"))
        with open(os.path.join(recette.work, "conversations.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["conversations"], [entry])
        recette.cases["t-b"] = {**recette.cases["t-a"], "id": "t-b", "question": "Et ensuite ?",
                                "same_conversation_as": "t-a"}
        self.act(A, "poser_question", {"case": "t-b"})
        self.assertEqual(len(recette.registry), 1, "a question in a conversation already open creates nothing")
        for cid in ("t-a", "t-b"):
            self.act(A, "verdict_oracle", {"case": cid})
            self.assertEqual(journal_lines(recette.work)[-1]["conversation"],
                             {"href": entry["href"], "created_by": "t-a", "state": "créée"})
        recette.browser.close()  # a relaunched driver: its memory of the conversation is gone, the registry is not
        drv2, recette2, A2 = self.make(work=str(recette.work), module=load_driver())
        self.assertEqual(recette2.registry, recette.registry)
        recette2.cases.update({k: recette.cases[k] for k in ("t-a", "t-b")})
        recette2.cases["t-c"] = {**recette.cases["t-b"], "id": "t-c", "question": "Et enfin ?"}
        recette2.open()
        with self.assertRaises(drv2.DriverRefusal) as refused:  # on another page: refused, as before
            self.act(A2, "poser_question", {"case": "t-c"})
        self.assertIn("se pose dans la conversation de t-a", refused.exception.detail)
        recette2.browser.page().goto(f"http://127.0.0.1:{self.port}{entry['href']}")
        self.assertTrue(recette2._in_conversation_of("t-a"))
        self.act(A2, "poser_question", {"case": "t-c"})  # on the registered conversation: it resolves
        self.assertEqual(recette2.observations["t-c"]["conversation"]["created_by"], "t-a")
        self.assertEqual(len(recette2.registry), 1)

    def test_the_search_types_its_question_in_the_palette_only_and_closes_it(self):
        """E2: ⌘K (or the sidebar entry), the question typed in the palette and nowhere else, the titles
        found read, the palette closed by Escape; @list « recherche « q » : n résultats : … »."""
        for nokbd in (False, True):
            with self.subTest(nokbd=nokbd):
                drv, recette, A = self.make()
                try:
                    if nokbd:
                        recette.entry += "?nokbd=1"
                    recette.open()
                    self.first_message(recette, A)
                    page = recette.browser.page()
                    page.evaluate("() => window.__seed('Autre sujet sans rapport')")
                    self.list_case(recette, "t-search", action="lister_conversations", question="Recette banc",
                                   must=["resultats? : .*recette banc"])
                    self.list_case(recette, "t-none", action="lister_conversations", question="Introuvable",
                                   must=["resultats? : .*introuvable"])
                    res, ev = self.act(A, "lister_conversations", {"case": "t-search"})
                    self.assertEqual(res["text"], f"recherche « Recette banc » : 1 résultat : {self.TITLE}")
                    self.assertEqual(res["opened_by"], drv.SELECTORS["search_entry"][0] if nokbd else "ControlOrMeta+K")
                    self.assertEqual({e["name"] for e in ev}, {"screen.png", "search.json"})
                    search = self.evidence_json(ev, "search.json")
                    self.assertEqual((search["input"], search["result"], search["closed"], search["settled"]),
                                     ("[cmdk-input]", "[cmdk-item]", True, True))
                    self.assertEqual(page.evaluate("() => window.__palette_queries"), ["Recette banc"], "typed once, there")
                    self.assertEqual(page.evaluate("() => document.getElementById('prompt').value"), "", "never the composer")
                    self.assertTrue(page.evaluate("() => document.getElementById('palette').hidden"))
                    self.assertIsNone(recette.browser.own_modal)
                    self.assertEqual(self.act(A, "verdict_oracle", {"case": "t-search"})[0]["verdict"], "OK")
                    res, _ = self.act(A, "lister_conversations", {"case": "t-none"})
                    self.assertEqual(res["text"], "recherche « Introuvable » : 0 résultat")
                    self.assertEqual(self.act(A, "verdict_oracle", {"case": "t-none"})[0]["verdict"], "KO",
                                     "the query's echo is no result")
                    res, _ = self.act(A, "lister_conversations")  # without case: the sidebar, as before
                    self.assertEqual(res["text"], f"2 conversations : Autre sujet sans rapport | {self.TITLE}")
                    if not nokbd:
                        page.keyboard.press("Control+K")  # opened by hand: not the driver's, refused, left alone
                        with self.assertRaises(drv.DriverRefusal) as refused:
                            self.act(A, "capture_ecran")
                        self.assertEqual(refused.exception.reason, "dialog")
                        self.assertFalse(page.evaluate("() => document.getElementById('palette').hidden"))
                        page.keyboard.press("Escape")
                        recette.browser.own_modal = "recherche"  # left open by the driver's own action, cut short
                        page.keyboard.press("Control+K")
                        self.act(A, "capture_ecran")
                        self.assertTrue(page.evaluate("() => document.getElementById('palette').hidden"))
                finally:
                    recette.browser.close()

    def test_rename_and_delete_touch_the_registered_conversation_only(self):
        """E3: two conversations with the same title, the one the run did not create first in the sidebar:
        only the registered one is renamed, then deleted through the application's confirmation; a target
        absent from the registry, or a case of another action, is refused before any click."""
        drv, recette, A = self.make()
        recette.open()
        entry = self.first_message(recette, A)
        page = recette.browser.page()
        decoy = page.evaluate(f"() => window.__seed({json.dumps(self.TITLE)})")
        mine = entry["href"].rsplit("/", 1)[1]
        self.assertEqual(page.evaluate(self.HREFS), [decoy, entry["href"]], "the decoy first, same title")
        self.list_case(recette, "t-ren", action="renommer_conversation", same_conversation_as="t-a",
                       question="Recette banc renommée", must=["recette banc renommee"])
        self.list_case(recette, "t-del", action="supprimer_conversation", same_conversation_as="t-a",
                       must=["[0-9]+ conversation"], must_not=["recette banc renommee"])
        recette.cases["t-never"] = {**recette.cases["t-a"], "id": "t-never"}  # never asked: no conversation
        self.list_case(recette, "t-stray", action="supprimer_conversation", same_conversation_as="t-never")
        self.list_case(recette, "t-bare", same_conversation_as="t-a")  # names no action
        untouched = ("() => [window.__menu_opens.length, window.__menu_picks.length, window.__deleted.length, "
                     "Object.keys(window.__renamed).length]")
        for cid, action, why in (("t-stray", "supprimer_conversation", "n'est pas au registre"),
                                 ("t-ren", "supprimer_conversation", "se joue par renommer_conversation"),
                                 ("t-bare", "renommer_conversation", "ne nomme pas l'action")):
            with self.assertRaises(drv.DriverRefusal) as refused:
                self.act(A, action, {"case": cid})
            self.assertEqual(refused.exception.reason, "precondition", cid)
            self.assertIn(why, refused.exception.detail)
            self.assertEqual(page.evaluate(untouched), [0, 0, 0, 0], f"{cid}: refused before any click")
        res, ev = self.act(A, "renommer_conversation", {"case": "t-ren"})
        self.assertEqual(page.evaluate("() => window.__renamed"), {mine: "Recette banc renommée"})
        self.assertEqual(page.evaluate("() => window.__menu_opens"), [mine], "never the decoy's menu")
        self.assertEqual(page.evaluate(self.TITLES), [self.TITLE, "Recette banc renommée"])
        self.assertIn("titre relu « Recette banc renommée »", res["text"])
        record = self.evidence_json(ev, "conversation.json")
        self.assertEqual((record["picked"], record["trigger"]["trigger"], record["trigger"]["trigger_level"],
                          record["menu"]["how"]), ("Renommer", 'button[aria-haspopup="menu"]', 1, "aria-controls"))
        self.assertEqual(self.act(A, "verdict_oracle", {"case": "t-ren"})[0]["verdict"], "OK")
        self.assertEqual(recette._registered("t-a")["state"], "renommée")
        res, ev = self.act(A, "supprimer_conversation", {"case": "t-del"})
        self.assertEqual(page.evaluate("() => window.__deleted"), [mine])
        self.assertEqual(page.evaluate(self.HREFS), [decoy], "the decoy is still there")
        self.assertTrue(page.evaluate("() => document.getElementById('confirm-delete').hidden"))
        self.assertIsNone(recette.browser.own_modal)
        record = self.evidence_json(ev, "conversation.json")
        self.assertEqual((record["picked"], record["confirmation"]["role"], record["confirmation"]["buttons"]),
                         ("Supprimer", "alertdialog", ["Annuler", "Supprimer"]))
        self.assertNotIn("others_gone", record)
        self.assertEqual(self.act(A, "verdict_oracle", {"case": "t-del"})[0]["verdict"], "OK")
        self.assertEqual(journal_lines(recette.work)[-1]["conversation"],
                         {"href": entry["href"], "created_by": "t-a", "state": "supprimée"})
        with open(os.path.join(recette.work, "conversations.json"), encoding="utf-8") as f:
            self.assertEqual([h["state"] for h in json.load(f)["conversations"][0]["history"]],
                             ["créée", "renommée", "suppression demandée", "suppression confirmée", "supprimée"])
        opens = page.evaluate("() => window.__menu_opens.length")
        self.list_case(recette, "t-del2", action="supprimer_conversation", same_conversation_as="t-a")
        res, _ = self.act(A, "supprimer_conversation", {"case": "t-del2"})  # deleted already: nothing clicked
        self.assertIn("plus listée", res["text"])
        with self.assertRaises(drv.DriverRefusal) as refused:
            self.act(A, "renommer_conversation", {"case": "t-ren"})
        self.assertIn("est supprimée", refused.exception.detail)
        self.assertEqual(page.evaluate("() => window.__menu_opens.length"), opens)

    def test_a_rename_dialog_and_a_confirmation_left_open(self):
        """E3: a rename in a dialog (`?renamedialog=1`), validated by Enter; the application's delete
        confirmation opened by hand is an unknown dialog, refused and left alone; left open by the driver's
        own action, the next guard cancels it, never confirms it."""
        drv, recette, A = self.make()
        recette.entry += "?renamedialog=1"
        recette.open()
        self.first_message(recette, A)
        page = recette.browser.page()
        self.list_case(recette, "t-ren", action="renommer_conversation", same_conversation_as="t-a",
                       question="Titre par la fenêtre", must=["titre par la fenetre"])
        res, ev = self.act(A, "renommer_conversation", {"case": "t-ren"})
        self.assertTrue(self.evidence_json(ev, "conversation.json")["field"]["in_dialog"])
        self.assertTrue(page.evaluate("() => document.getElementById('rename-dialog').hidden"))
        self.assertIsNone(recette.browser.own_modal)
        self.assertEqual(self.act(A, "verdict_oracle", {"case": "t-ren"})[0]["verdict"], "OK")
        page.evaluate("() => openConfirm(document.querySelector('#conversations .conv-item'))")
        with self.assertRaises(drv.DriverRefusal) as refused:
            self.act(A, "capture_ecran")
        self.assertEqual(refused.exception.reason, "dialog")
        self.assertFalse(page.evaluate("() => document.getElementById('confirm-delete').hidden"))
        recette.browser.own_modal = "suppression"
        self.act(A, "capture_ecran")
        self.assertTrue(page.evaluate("() => document.getElementById('confirm-delete').hidden"))
        self.assertEqual(page.evaluate("() => window.__deleted"), [], "cancelled, never confirmed")

    def test_the_dictation_state_is_read_with_the_model_selector_and_never_pressed(self):
        """E4: choisir_modele's @model text ends with « ; micro : <data-status> » (M8-7), or « absent »;
        every line's config carries it; the button is never pressed."""
        for nomic in (False, True):
            with self.subTest(nomic=nomic):
                drv, recette, A = self.make()
                try:
                    if nomic:
                        recette.entry += "?nomic=1"
                    recette.open()
                    want = "absent" if nomic else "idle"
                    with self.assertRaises(drv.DriverRefusal):
                        self.act(A, "verdict_oracle", {"case": "M8-7"})
                    res, ev = self.act(A, "choisir_modele", {"model": "claude haiku 5-5"})
                    self.assertTrue(res["text"].endswith(f" ; micro : {want}"), res["text"])
                    self.assertEqual((res["mic"], self.evidence_json(ev, "model.json")["mic"]), (want, want))
                    self.assertEqual(self.act(A, "verdict_oracle", {"case": "M8-7"})[0]["verdict"], "KO" if nomic else "OK")
                    self.assertEqual(journal_lines(recette.work)[-1]["config"]["mic"], want)
                    self.message_case(recette, "t-mic", "Bonjour")
                    self.act(A, "nouvelle_conversation")
                    self.act(A, "poser_question", {"case": "t-mic"})
                    self.act(A, "verdict_oracle", {"case": "t-mic"})
                    self.assertEqual(journal_lines(recette.work)[-1]["config"]["mic"], want)
                    if not nomic:
                        page = recette.browser.page()
                        self.assertEqual(page.evaluate("() => [window.__mic_clicks, document.getElementById('mic').dataset.status]"),
                                         [0, "idle"])
                finally:
                    recette.browser.close()

    def test_the_survey_opens_from_its_entry_and_closes_by_its_cross_sending_nothing(self):
        """E5: ouvrir_avis M8-8, the observation « avis : ouvert (entrée : X) ; fermé par : croix ;
        envoyé : non » ; a survey that shows up by itself is closed by the guard and counted on the line of
        the case in hand (modals_closed)."""
        drv, recette, A = self.make()
        recette.open()
        page = recette.browser.page()
        with self.assertRaises(drv.DriverRefusal) as refused:
            self.act(A, "ouvrir_avis", {"case": "M8-7"})
        self.assertEqual(refused.exception.reason, "precondition")
        self.assertEqual(page.evaluate("() => window.__feedback_opens"), 0)
        res, ev = self.act(A, "ouvrir_avis", {"case": "M8-8"})
        self.assertEqual(res["text"], "avis : ouvert (entrée : Send feedback) ; fermé par : croix ; envoyé : non")
        self.assertEqual({e["name"] for e in ev}, {"avis.json", "avis.png"})
        self.assertEqual(self.evidence_json(ev, "avis.json")["survey"]["radios"], ["suggestion", "probleme"])
        self.assertEqual(page.evaluate("() => [window.__feedback_opens, window.__survey_answered, window.__survey_sent, "
                                       "document.getElementById('avis').hidden]"), [1, False, 0, True])
        self.assertEqual(self.act(A, "verdict_oracle", {"case": "M8-8"})[0]["verdict"], "OK")
        self.assertEqual(journal_lines(recette.work)[-1]["modals_closed"], [], "the survey it opened is no spontaneous one")
        self.message_case(recette, "t-spont", "Bonjour")
        self.act(A, "nouvelle_conversation")
        page.evaluate("() => showSurvey()")  # the survey shows up by itself
        self.act(A, "poser_question", {"case": "t-spont"})
        self.act(A, "verdict_oracle", {"case": "t-spont"})
        self.assertEqual(journal_lines(recette.work)[-1]["modals_closed"], ["avis"])
        self.assertEqual(page.evaluate("() => [window.__survey_answered, window.__survey_sent]"), [False, 0])

    def test_the_accept_list_and_the_class_of_a_failed_chip(self):
        """E6: the file input's accept list in joindre_document's result and on the line. E9: a failed chip
        gives its class, never its raw label (a host name and a stack), which stays in chip.json."""
        drv, recette, A = self.make()
        recette.open()
        self.act(A, "nouvelle_conversation")
        res, ev = self.act(A, "joindre_document", {"case": "fixture-case-1"})
        self.assertEqual(res["accept"], self.ACCEPT)
        self.assertEqual({e["name"] for e in ev}, {"attach.png", "network.json", "chip.json"})
        self.act(A, "poser_question", {"case": "fixture-case-1"})
        self.act(A, "verdict_oracle", {"case": "fixture-case-1"})
        line = journal_lines(recette.work)[-1]
        self.assertEqual((line["attach"]["state"], line["attach"]["accept"]), ("ready", self.ACCEPT))
        self.assertNotIn("error_class", line)
        for name, cls, internal in (("faildb", "service documentaire injoignable", True),
                                    ("failformat", "format refusé", False),
                                    ("failsize", "fichier trop volumineux", False),
                                    ("fail", "échec de la pièce jointe", False)):
            cid = f"t-{name}"
            recette.cases[cid] = {**recette.cases["fixture-case-1"], "id": cid, "file": f"fixture/corpus/{name}.txt"}
            self.act(A, "nouvelle_conversation")
            with self.assertRaises(drv.DriverRefusal) as refused:
                self.act(A, "joindre_document", {"case": cid})
            extra = refused.exception.extra
            self.assertEqual((refused.exception.detail, extra["error_class"], extra["internal_error_shown"]),
                             ("attachment failed", cls, internal))
            chip = self.evidence_json(refused.exception.evidence, "chip.json")
            self.assertIn("Failed", chip["label"])
            expected = f"pièce jointe en échec ({cls})"
            if internal:
                self.assertIn("docstore-db.internal.maskai.local", chip["label"], "the raw label stays in the evidence")
                with self.assertRaises(drv.DriverRefusal) as not_sent:  # read again before sending: still failed
                    self.act(A, "poser_question", {"case": cid})
                self.assertEqual(not_sent.exception.extra["error_class"], cls)
                expected = f"question non envoyée (précondition) : pièce jointe failed ({cls})"
            self.act(A, "verdict_oracle", {"case": cid})
            line = journal_lines(recette.work)[-1]
            self.assertEqual((line["verdict"], line["error_class"], line["attach"]["error_class"]), ("KO", cls, cls))
            self.assertTrue(line["evidence"].startswith(expected), line["evidence"])
            seen = json.dumps([line, extra, recette.observations[cid]], ensure_ascii=False)
            for bad in ("docstore-db", "maskai.local", "/srv/", "Traceback", "worker.py"):
                self.assertNotIn(bad, seen, name)

    def test_a_silent_failure_is_named_on_the_line_without_its_internals(self):
        """E10: no answer card (the night of 18–19/10): the line carries the failure's class and whether an
        internal error was shown (review I3); the sanitised excerpt and the raw capture (status line, toast,
        HTTP status, last events) go to failure.json only (review I2)."""
        drv, recette, A = self.make()
        recette.open()
        for word, cls, excerpt in (("SILENT_FAIL", "service interne injoignable", "could not translate host name"),
                                   ("SILENT_502", "erreur serveur (HTTP 502)", "momentanément indisponible")):
            cid = f"t-{word.lower()}"
            self.message_case(recette, cid, f"Bonjour {word}")
            self.act(A, "nouvelle_conversation")
            res, ev = self.act(A, "poser_question", {"case": cid})
            self.assertEqual(res["error_class"], cls)
            failure = self.evidence_json(ev, "failure.json")
            self.assertIn("pii-", json.dumps(failure), "the raw capture keeps the internals, in the evidence only")
            self.assertIn(["toast", "Échec de l'envoi : réessayez dans un instant."], failure["notices"])
            self.act(A, "verdict_oracle", {"case": cid})
            line = journal_lines(recette.work)[-1]
            self.assertEqual((line["verdict"], line["error_class"]), ("KO", cls))
            self.assertIn("réponse vide : échec silencieux (aucune nouvelle carte de réponse", line["evidence"])
            self.assertIn(cls, line["evidence"])
            self.assertNotIn("error_excerpt", line)
            self.assertIn(excerpt, failure["excerpt"])
            self.assertLessEqual(len(failure["excerpt"]), drv.EXCERPT_MAX)
            seen = json.dumps([line, recette.observations[cid]], ensure_ascii=False)
            for bad in ("pii-db", "pii-guard", "maskai.local", "/srv/", "detect.py", "8443"):
                self.assertNotIn(bad, seen, word)
            if word == "SILENT_502":
                self.assertEqual(failure["excerpt"], "Le service est momentanément indisponible (502).")
        self.assertIn("dernier nœud detect_pii", journal_lines(recette.work)[-2]["evidence"])

    def test_the_line_carries_the_masked_inventory_and_the_replay_compares_it(self):
        """E7: sent_source, sent_tokens and pii_declared on the line; the replay_of note compares the masked
        sets; a sent text not observed is null, with the reason."""
        drv, recette, A = self.make()
        recette.open()
        q = "Relance M. Hugo Bernard au 06 12 34 56 78 pour le loyer."
        base = {"level": "M1", "kind": "message", "question": q, "budget_s": 5, "nfr": ["securite"], "must": ["relance"],
                "must_not": [], "pii": ["Hugo Bernard", "06 12 34 56 78"]}
        recette.cases["t-a"] = {**base, "id": "t-a"}
        recette.cases["t-b"] = {**base, "id": "t-b", "replay_of": "t-a"}
        recette.cases["t-c"] = {**base, "id": "t-c", "replay_of": "t-a", "question": q + " LEAK"}
        recette.cases["t-st"] = {**base, "id": "t-st", "question": q + " DETAILS_STATUS"}
        for cid in ("t-a", "t-b", "t-c", "t-st"):
            self.act(A, "nouvelle_conversation")
            self.act(A, "poser_question", {"case": cid})
            self.act(A, "verdict_oracle", {"case": cid})
        a, b, c, st = journal_lines(recette.work)[-4:]
        self.assertEqual((a["sent_source"], a["sent_tokens"], a["pii_declared"]), ("card-details", {"PERSON": 1, "PHONE": 1}, 2))
        self.assertIn("passage a (t-a) : OK ; texte envoyé identique ; entités masquées identiques", b["evidence"])
        self.assertEqual((c["verdict"], c["sent_tokens"]), ("KO", {}))
        self.assertIn("passage a (t-a) : OK ; texte envoyé différent ; entités masquées différentes : −PERSON −PHONE",
                      c["evidence"])
        self.assertEqual((st["sent_source"], st["sent_tokens"], st["sent_rejected"]),
                         (None, None, "le panneau « Détails » ne montre pas le texte de la question"))

    def test_the_interface_probe_reads_everything_and_touches_nothing(self):
        """E11: sonder_interface resolves every selector, records the ⌘K entry and palette, the feedback
        entry, the dictation button, the accept list and the first conversation's menu (opened, read,
        closed by Escape, no item clicked), without the title or the address of that conversation."""
        drv, recette, A = self.make()
        recette.open()
        page = recette.browser.page()
        res, ev = self.act(A, "sonder_interface")
        self.assertEqual(self.evidence_json(ev, "probe.json")["menu"], {"found": False, "conversations": 0},
                         "no conversation yet: nothing to read, nothing refused")
        page.evaluate("() => { window.__seed('Mon dossier privé numéro un'); window.__seed('Mon dossier privé numéro deux'); }")
        res, ev = self.act(A, "sonder_interface")
        probe = self.evidence_json(ev, "probe.json")
        self.assertEqual(probe["errors"], [])
        self.assertEqual(set(probe["selectors"]), set(drv.SELECTORS) - set(drv.SELECTOR_PATTERNS))
        for key, rows in probe["selectors"].items():
            for row in rows:
                self.assertNotIn("error", row, (key, row))
        self.assertEqual((probe["search"]["opened_by"], probe["search"]["input"], probe["search"]["closed"]),
                         ("ControlOrMeta+K", "[cmdk-input]", True))
        self.assertEqual((probe["search_entry"]["selector"], probe["feedback_entry"]["label"]),
                         (drv.SELECTORS["search_entry"][0], "Send feedback"))
        self.assertEqual((probe["mic"]["status"], probe["file_input"]["accept"]), ("idle", self.ACCEPT))
        self.assertEqual([i["label"] for i in probe["menu"]["menu"]["items"]], ["Renommer", "Partager", "Supprimer"])
        self.assertEqual((probe["menu"]["closed"], probe["menu"]["trigger"]["trigger"]), (True, 'button[aria-haspopup="menu"]'))
        self.assertEqual(page.evaluate("() => [window.__menu_opens.length, window.__menu_picks.length, "
                                       "window.__palette_queries.length, window.__feedback_opens, window.__mic_clicks, "
                                       "window.__survey_shown]"), [1, 0, 0, 0, 0, 0])
        text = json.dumps(probe, ensure_ascii=False)
        for bad in ("Mon dossier privé", "/conversation/seed", "seed2"):
            self.assertNotIn(bad, text)
        # round 2 (R2-4, robustness-1): the verdict on the menu and its count; the entries in probe.json only
        self.assertIn("M8-5/M8-6 : menu accepté, 3 entrées", res["text"])
        self.assertNotIn("Partager", res["text"])
        self.assertTrue(page.evaluate("() => document.getElementById('conv-menu').hidden "
                                      "&& document.getElementById('palette').hidden"))

    def test_the_m8_level_plays_its_cases_in_order(self):
        """recette-maskai-3.json, step M8, on the bench with the cases of cases.json (M8-3 aside, an image of
        the corpus): each case probed, then played for itself; M8-1's conversation renamed, then deleted, and
        no other; the dictation button never pressed; the survey never sent."""
        drv, recette, A = self.make()
        recette.open()
        page = recette.browser.page()
        self.act(A, "choisir_modele", {"model": "claude haiku 5-5"})  # the level's configuration
        self.act(A, "recherche_web", {"mode": "off"})
        older = page.evaluate("() => window.__seed(\"Recette trois, fiche pratique : une conversation d'avant\")")

        def play(cid, *steps):
            with self.assertRaises(drv.DriverRefusal) as refused:  # still to do
                self.act(A, "verdict_oracle", {"case": cid})
            self.assertEqual(refused.exception.reason, "precondition", cid)
            for name, args in steps:
                self.act(A, name, args)
            res, _ = self.act(A, "verdict_oracle", {"case": cid})
            self.assertEqual(res["verdict"], "OK", (cid, json.loads(res["text"])["evidence"]))

        play("M8-1", ("nouvelle_conversation", {}), ("choisir_modele", {"model": "claude haiku 5-5"}),
             ("poser_question", {"case": "M8-1"}))
        play("M8-2", ("lister_conversations", {}))
        play("M8-4", ("lister_conversations", {"case": "M8-4"}))
        play("M8-5", ("renommer_conversation", {"case": "M8-5"}))
        play("M8-6", ("supprimer_conversation", {"case": "M8-6"}))
        play("M8-7", ("choisir_modele", {"model": "claude haiku 5-5"}))
        play("M8-8", ("ouvrir_avis", {"case": "M8-8"}))
        self.assertEqual(page.evaluate(self.HREFS), [older], "the conversation of before is still there")
        self.assertEqual(page.evaluate("() => [window.__mic_clicks, window.__survey_sent, window.__deleted.length]"),
                         [0, 0, 1])
        lines = {line["case"]: line for line in journal_lines(recette.work)}
        self.assertIn("recherche « Recette trois » : 2 résultats", lines["M8-4"]["evidence"])
        self.assertEqual([lines[c]["conversation"]["state"] for c in ("M8-1", "M8-5", "M8-6")],
                         ["créée", "renommée", "supprimée"])
        self.assertIsNone(recette.browser.own_modal)


class FixRound1Units(unittest.TestCase):
    """Review of pilot v2.3, fix round 1 (.superpowers/review-v2.3.md): what needs no browser."""

    def recette(self, drv):
        work, profile = tempfile.mkdtemp(prefix="maskai-recette-"), tempfile.mkdtemp(prefix="maskai-recette-")
        self.addCleanup(shutil.rmtree, work, True)
        self.addCleanup(shutil.rmtree, profile, True)
        return drv.Recette(env={"VK_DRIVER_WORK_DIR": work, "VK_DRIVER_PROFILE_DIR": profile,
                                "VK_DRIVER_HOSTS": "127.0.0.1"})

    @staticmethod
    def verdict(recette, cid):
        ev = tempfile.mkdtemp(prefix="maskai-recette-")
        try:
            return recette.actions()["verdict_oracle"]({"case": cid}, ev)[0]
        finally:
            shutil.rmtree(ev, True)

    def test_i2_the_usual_forms_of_an_internal_host_are_sanitised_and_flagged(self):
        """I2: name:port, ENOTFOUND, « connect to host », « lookup »: never on a line, always flagged."""
        drv = load_driver()
        for text, host in (("Error -3 connecting to redis:6379. Temporary failure in name resolution.", "redis"),
                           ("getaddrinfo ENOTFOUND presidio-analyzer", "presidio-analyzer"),
                           ("Cannot connect to host anonymizer:8080 ssl:default", "anonymizer"),
                           ("Connection refused connecting to pii_guard:8000", "pii_guard"),
                           ("dial tcp: lookup pii-db on 10.0.0.2:53: no such host", "pii-db")):
            self.assertNotIn(host, drv.sanitise(text, 500), text)
            self.assertTrue(drv.exposes_internals(text), text)
        # a chip whose class is a service out of reach showed an internal error, even with no host to read
        self.assertTrue(drv.chip_shows_internal("x.txt Failed: database unavailable", "x.txt"))
        self.assertFalse(drv.chip_shows_internal("x.txt Failed: unsupported file format", "x.txt"))

    def test_i7_only_the_exact_entries_of_a_conversation_menu_are_clicked(self):
        """I7: the rename and delete entries are allow-listed; the confirmation must speak of deleting one
        conversation."""
        drv = load_driver()

        def item(label):
            return {"text": drv.evaluer.fold(label), "label": label, "disabled": False}

        for labels in (["Renommer le dossier", "Supprimer tous les chats du dossier"],
                       ["Renommer", "Supprimer tous les messages"], ["Rename", "Delete every chat in this folder"],
                       ["Renommer", "Supprimer toute la sélection"], ["Rename", "Delete selected"],
                       ["Renommer", "Supprimer", "Supprimer tous les messages"]):
            pick, why = drv.menu_entry([item(x) for x in labels], "supprimer")
            self.assertIsNone(pick, labels)
            self.assertTrue(why)
        for labels, want in ((["Renommer", "Partager", "Supprimer"], "Supprimer"),
                             (["Rename chat", "Delete chat"], "Delete chat"),
                             (["Renommer la conversation", "Supprimer la conversation"], "Supprimer la conversation"),
                             (["Rename", "Delete this conversation"], "Delete this conversation"),
                             # round 2, R2-3 (robustness-1): a keyboard hint inside the item is the same entry
                             (["Renommer", "Supprimer ⌘⌫"], "Supprimer ⌘⌫")):
            self.assertEqual(drv.menu_entry([item(x) for x in labels], "supprimer")[0]["label"], want, labels)
        for text in ("Supprimer cette conversation ? Cette action est définitive.",
                     "Delete chat? This will permanently delete it.", "Supprimer la discussion « Recette trois » ?",
                     # what a confirmation says of the one conversation's own content is never « several »
                     "Supprimer la conversation ? La conversation et tous ses messages seront définitivement supprimés.",
                     "Delete chat? This will delete the chat and all of its messages.",
                     "Supprimer cette conversation ? Tout l'historique de cette conversation sera effacé."):
            self.assertTrue(drv.confirms_one_deletion(text), text)
        for text in ("Supprimer tous les chats du dossier ?", "Êtes-vous sûr ?", "Delete all chats?",
                     "Supprimer la sélection ?", "Supprimer le dossier ?", "Vider la conversation ?",
                     "Supprimer toutes les conversations ?", "Delete every chat in this folder?",
                     "Supprimer la conversation et tout l'historique ?", "Delete this chat and clear your history?",
                     "Supprimer les 3 conversations sélectionnées ?"):
            self.assertFalse(drv.confirms_one_deletion(text), text)

    def test_m5_a_case_bound_observation_judges_its_own_case_only(self):
        """m5: an @list made for M8-4 never judges M8-5; a pre-v2.3 shared observation that carries a
        verdict is consumed."""
        drv = load_driver()
        recette = self.recette(drv)
        recette._shared("@list", {"answer": "recherche « Recette trois » : 1 résultat : Recette trois renommée",
                                  "timing_s": None, "case": "M8-4"})
        with self.assertRaises(drv.DriverRefusal) as refused:
            self.verdict(recette, "M8-5")
        self.assertIn("renommer_conversation d'abord", refused.exception.detail)
        self.assertEqual(self.verdict(recette, "M8-4")["verdict"], "OK", "its own case is judged on it")
        recette.observations["@list"] = {"answer": "3 conversations : a | b | c", "timing_s": None,
                                         "verdict": {"verdict": "OK"}}  # pilot v2.2: judged, no `judged`
        with self.assertRaises(drv.DriverRefusal):
            self.verdict(recette, "N7-1")

    def test_m6_the_replay_note_compares_the_masked_entities_not_only_their_types(self):
        """m6: two persons masked in a, a person and a city in b: the types match, the entities do not."""
        drv = load_driver()
        recette = self.recette(drv)
        q = "Relance M. Hugo Bernard et Paul Martin pour Lyon."
        base = {"level": "M1", "kind": "message", "question": q, "budget_s": 10, "nfr": ["securite"],
                "must": ["relance"], "must_not": [], "pii": []}
        sent = {"t-a": "Relance <PERSON_1> et <PERSON_2> pour Lyon.", "t-b": "Relance <PERSON_1> et Paul Martin pour <PERSON_2>.",
                "t-c": "Relance <PERSON_1> et <PERSON_2> pour Lyon."}
        for cid in ("t-a", "t-b", "t-c"):
            recette.cases[cid] = {**base, "id": cid, **({} if cid == "t-a" else {"replay_of": "t-a"})}
            recette.observations[cid] = {"answer": "Relance envoyée.", "timing_s": 3.0, "sent_to_model": sent[cid],
                                         "sent_info": {"source": "card-details"}, "guard": {"pii_count": 2}}
            self.verdict(recette, cid)
        b, c = journal_lines(recette.work)[-2:]
        self.assertNotIn("ensemble masqué identique", b["evidence"], "the same types over other entities")
        self.assertIn("entités masquées différentes", b["evidence"])
        self.assertIn("entités masquées identiques", c["evidence"])
        recette.observations.pop("t-a")  # passage a's text gone (observations set aside): types only, said so
        recette.cases["t-d"] = {**base, "id": "t-d", "replay_of": "t-a"}
        recette.observations["t-d"] = {**recette.observations["t-c"], "verdict": None}
        recette.observations["t-d"].pop("verdict")
        self.verdict(recette, "t-d")
        self.assertIn("types masqués identiques", journal_lines(recette.work)[-1]["evidence"])

    def test_m7_an_ordinary_status_update_is_no_displayed_error(self):
        """m7: only an alert, a toast, or an error-like status text counts as an error shown."""
        drv = load_driver()
        end = ["<START>", "node:detect_pii ×2", "<END>"]
        self.assertEqual(drv.failure_class(200, None, [["status", "Message envoyé"]], end, True),
                         "flux terminé sans réponse")
        self.assertEqual(drv.failure_class(200, None, [["status", "Une erreur est survenue"]], end, True),
                         "erreur affichée par l'application")
        self.assertEqual(drv.failure_class(200, None, [["alert", "Quota"]], end, True),
                         "erreur affichée par l'application")
        self.assertEqual(drv.failure_class(200, None, [["toast", "Réessayez dans un instant"]], end, True),
                         "erreur affichée par l'application")


@unittest.skipUnless(HAVE_PLAYWRIGHT, NO_PLAYWRIGHT)
class FixRound1OnFixture(FixtureBench):
    """Review of pilot v2.3, fix round 1, on the bench: what must never be pressed, taken or judged OK."""

    HREFS = "() => [...document.querySelectorAll('#conversations a')].map((a) => a.getAttribute('href'))"

    def start(self, query=""):
        drv, recette, A = self.make()
        recette.entry += query
        recette.open()
        return drv, recette, A, recette.browser.page()

    def play_m8_1(self, A):
        self.act(A, "nouvelle_conversation")
        self.act(A, "choisir_modele", {"model": "claude haiku 5-5"})
        self.act(A, "poser_question", {"case": "M8-1"})
        self.act(A, "verdict_oracle", {"case": "M8-1"})

    def refused(self, drv, A, action, args):
        with self.assertRaises(drv.DriverRefusal) as refused:
            self.act(A, action, args)
        return refused.exception

    def verdict_after(self, drv, A, action, cid):
        try:
            self.act(A, action, {"case": cid})
        except drv.DriverRefusal:
            pass
        return self.act(A, "verdict_oracle", {"case": cid})[0]["verdict"]

    @staticmethod
    def list_case(recette, cid, **over):
        recette.cases[cid] = {"id": cid, "level": "M8", "kind": "list", "budget_s": 10, "nfr": ["disponibilite"],
                              "must": [], "must_not": [], "pii": [], **over}

    def first_message(self, recette, A, cid="t-a", question="Recette banc : premier message"):
        self.message_case(recette, cid, question)
        self.act(A, "nouvelle_conversation")
        self.act(A, "poser_question", {"case": cid})
        return recette._registered(cid)

    def test_i1_m8_5_and_m8_6_are_judged_on_their_conversation_not_on_the_listing(self):
        """I1: a listed « Recette trois renommée » that is not M8-1's changes neither verdict. A menu the pilot
        refuses gives NON TESTÉ and NON TESTÉ (ruling R2-1 of fix round 2; KO in round 1)."""
        refused = ("NON TESTÉ", "NON TESTÉ")
        for name, query, decoy, want in (("menu refused", "?menuitems=Partager,Supprimer", False, refused),
                                         ("both done, decoy listed", "", True, ("OK", "OK")),
                                         ("menu refused, decoy listed", "?menuitems=Partager,Supprimer", True, refused)):
            with self.subTest(name):
                drv, recette, A, page = self.start(query)
                try:
                    if decoy:
                        page.evaluate("() => window.__seed('Recette trois renommée')")
                    self.play_m8_1(A)
                    got = (self.verdict_after(drv, A, "renommer_conversation", "M8-5"),
                           self.verdict_after(drv, A, "supprimer_conversation", "M8-6"))
                    self.assertEqual(got, want)
                    if want == refused:
                        self.assertEqual(page.evaluate("() => window.__deleted"), [])
                finally:
                    recette.browser.close()

    def test_i3_an_internal_error_shown_to_the_user_is_flagged_and_the_excerpt_stays_in_the_evidence(self):
        """I3: the status line shows « pii-guard.internal:8443 » (SILENT_FAIL): flagged on the result and the
        line; a plain status (SILENT_502) is not; a chip naming a host is flagged on the line. I2: no
        excerpt on the line, it is in failure.json."""
        drv, recette, A, page = self.start()
        for word, shown in (("SILENT_FAIL", True), ("SILENT_502", False)):
            cid = f"t-{word.lower()}"
            self.message_case(recette, cid, f"Bonjour {word}")
            self.act(A, "nouvelle_conversation")
            res, ev = self.act(A, "poser_question", {"case": cid})
            self.assertIs(res["internal_error_shown"], shown, word)
            self.assertTrue(self.evidence_json(ev, "failure.json")["excerpt"], word)
            self.act(A, "verdict_oracle", {"case": cid})
            line = journal_lines(recette.work)[-1]
            self.assertIs(line["internal_error_shown"], shown, word)
            self.assertNotIn("error_excerpt", line, word)
        recette.cases["t-faildb"] = {**recette.cases["fixture-case-1"], "id": "t-faildb",
                                     "file": "fixture/corpus/faildb.txt"}
        self.act(A, "nouvelle_conversation")
        self.refused(drv, A, "joindre_document", {"case": "t-faildb"})
        self.act(A, "verdict_oracle", {"case": "t-faildb"})
        line = journal_lines(recette.work)[-1]
        self.assertIs(line["internal_error_shown"], True)
        self.assertIs(line["attach"]["internal_error_shown"], True)

    def test_i4_the_search_fallback_never_presses_a_control_it_did_not_identify(self):
        """I4: the entry is a plain div, the dictation button carries a <kbd> hint, ⌘K is not bound: the
        search refuses `precondition` before any click, and the probe too presses nothing. An entry that is
        a link is identified; the page it leads to is left, back to where the search started."""
        drv, recette, A, page = self.start("?searchentry=div&kbdmic=1&nokbd=1")
        self.list_case(recette, "t-search", action="lister_conversations", question="Recette")
        err = self.refused(drv, A, "lister_conversations", {"case": "t-search"})
        self.assertEqual(err.reason, "precondition")
        self.assertNotIn("@list", recette.observations, "no verdict on an entry the driver could not identify")
        self.act(A, "sonder_interface")
        self.assertEqual(page.evaluate("() => [window.__mic_clicks, window.__palette_opens]"), [0, 0])
        recette.browser.close()
        drv, recette, A, page = self.start("?searchentry=link&nokbd=1")
        self.first_message(recette, A)
        start = drv.urlsplit(page.url).path
        self.list_case(recette, "t-search", action="lister_conversations", question="Recette")
        err = self.refused(drv, A, "lister_conversations", {"case": "t-search"})
        self.assertEqual(err.reason, "precondition")
        self.assertEqual(drv.urlsplit(page.url).path, start, "back where the search started")
        search = self.evidence_json(err.evidence, "search.json")
        self.assertEqual(drv.urlsplit(search["moved_to"]).path, "/search")

    def test_i5_the_feedback_entry_is_never_guessed(self):
        """I5: the documented « Send feedback » button is relabelled and the answer card carries 👍/👎:
        ouvrir_avis refuses `precondition` before any click, listing what it saw."""
        drv, recette, A, page = self.start("?feedbacklabel=Envoyer%20un%20retour&ratings=1")
        self.first_message(recette, A)
        self.assertEqual(page.evaluate("() => document.querySelectorAll('[aria-label=\"Positive feedback\"]').length"), 1)
        err = self.refused(drv, A, "ouvrir_avis", {"case": "M8-8"})
        self.assertEqual(err.reason, "precondition")
        self.assertNotIn("Envoyer un retour", err.detail, "R2-4: the candidates go to avis.json only")
        self.assertIn("Envoyer un retour", " | ".join(self.evidence_json(err.evidence, "avis.json")["candidates"]))
        self.assertEqual(page.evaluate("() => [window.__rating_clicks, window.__feedback_opens, window.__survey_shown]"),
                         [0, 0, 0])

    def test_i5_a_candidate_that_names_a_conversation_is_never_quoted(self):
        """I4, I5, found while writing the fix report: a row button labelled after its conversation
        (« Options de « Avis juridique Dupont » ») matches the feedback words; it was quoted among the
        candidates of the refusal. A candidate that holds a listed title is left out."""
        drv, recette, A, page = self.start("?feedbacklabel=Envoyer%20un%20retour&rowlabels=1")
        page.evaluate("() => window.__seed('Avis juridique Dupont')")
        err = self.refused(drv, A, "ouvrir_avis", {"case": "M8-8"})
        self.assertEqual(err.reason, "precondition")
        self.assertIn("Envoyer un retour", " | ".join(self.evidence_json(err.evidence, "avis.json")["candidates"]))
        self.assertNotIn("Dupont", err.detail)
        self.assertNotIn("Dupont", json.dumps(err.extra, ensure_ascii=False))
        self.assertEqual(page.evaluate("() => window.__menu_opens"), [], "nothing was clicked")

    def test_i6_a_row_that_shows_up_during_a_question_is_never_registered(self):
        """I6: the question creates nothing (the page stays on /new) while another row appears: the
        registry stays empty, so rename and delete have nothing to act on."""
        drv, recette, A, page = self.start()
        self.message_case(recette, "t-nocreate", "Bonjour NOCREATE")
        self.act(A, "nouvelle_conversation")
        self.act(A, "poser_question", {"case": "t-nocreate"})
        self.assertEqual(len(page.evaluate(self.HREFS)), 1, "a row showed up")
        self.assertEqual(recette.registry, [])
        self.assertIsNone(recette._registered("t-nocreate"))

    def test_i7_a_group_menu_or_a_wide_confirmation_never_deletes(self):
        """I7: a row without its own trigger in a headed group (whose menu deletes the group): no trigger is
        taken; a confirmation that speaks of « tous les chats »: cancelled, never confirmed."""
        for name, query in (("group", "?grouped=1"),
                            ("wide confirmation", "?confirmtext=Supprimer%20tous%20les%20chats%20du%20dossier%20%3F")):
            with self.subTest(name):
                drv, recette, A, page = self.start(query)
                try:
                    self.first_message(recette, A)
                    self.list_case(recette, "t-del", action="supprimer_conversation", same_conversation_as="t-a")
                    err = self.refused(drv, A, "supprimer_conversation", {"case": "t-del"})
                    self.assertEqual(err.reason, "precondition")
                    self.assertEqual(page.evaluate("() => [window.__deleted, window.__group_menu_opens]"), [[], 0])
                    self.assertTrue(page.evaluate("() => document.getElementById('confirm-delete').hidden"))
                finally:
                    recette.browser.close()

    def test_m1_a_confirmation_that_was_open_before_the_click_is_never_taken(self):
        """m1: the delete entry opens nothing, while an unrelated « Supprimer cette conversation ? » dialog
        appeared when the menu opened: it is never clicked."""
        drv, recette, A, page = self.start("?decoydialog=1&noconfirm=1")
        self.first_message(recette, A)
        self.list_case(recette, "t-del", action="supprimer_conversation", same_conversation_as="t-a", budget_s=2)
        err = self.refused(drv, A, "supprimer_conversation", {"case": "t-del"})
        self.assertIn("pas de confirmation", err.detail)
        self.assertEqual(page.evaluate("() => [window.__decoy_confirms, window.__deleted.length]"), [0, 0])
        page.evaluate("() => { document.getElementById('decoy').hidden = true; }")

    def test_a_cancel_button_that_says_non_is_the_cancel_button(self):
        """Fix round 1, found while binding the confirmation to the click (m1): the cancel rule `^non\\b` of
        the page's script had become a backspace (an escape in a plain string), so « Non, ne pas supprimer »
        counted as a second delete button and the confirmation was cancelled as ambiguous. Read as « non »,
        it is the cancel button: the one delete button confirms, once."""
        drv, recette, A, page = self.start("?canceltext=Non%2C%20ne%20pas%20supprimer")
        self.first_message(recette, A)
        self.list_case(recette, "t-del", action="supprimer_conversation", same_conversation_as="t-a",
                       must=["plus listee"], must_not=["encore listee"])
        res, ev = self.act(A, "supprimer_conversation", {"case": "t-del"})
        record = self.evidence_json(ev, "conversation.json")
        # round 2 (registry-1): the buttons are judged on the Python side, pick_confirm, no longer in the page
        self.assertEqual(record["confirmation"]["buttons"], ["Non, ne pas supprimer", "Supprimer"])
        self.assertEqual(drv.pick_confirm(record["confirmation"]), (1, None))
        self.assertEqual(len(page.evaluate("() => window.__deleted")), 1)
        self.assertNotIn("\x08", drv._CONFIRM_FIND + drv.CONFIRM_CANCEL_RE, "no backspace in the cancel rule")

    def test_m2_a_confirmed_delete_is_never_clicked_twice(self):
        """m2: the confirmation was clicked, the row is still listed (a stale sidebar): the registry says
        « suppression confirmée », and a second run clicks nothing."""
        drv, recette, A, page = self.start("?staledelete=1")
        self.first_message(recette, A)
        self.list_case(recette, "t-del", action="supprimer_conversation", same_conversation_as="t-a", budget_s=2)
        self.refused(drv, A, "supprimer_conversation", {"case": "t-del"})
        self.assertEqual(recette._registered("t-a")["state"], "suppression confirmée")
        opens = page.evaluate("() => window.__menu_opens.length")
        try:
            self.act(A, "supprimer_conversation", {"case": "t-del"})
        except drv.DriverRefusal:
            pass
        self.assertEqual(page.evaluate("() => [window.__menu_opens.length, window.__deleted.length]"), [opens, 1])

    def test_m3_the_timeout_line_and_the_concurrent_line_name_their_conversations(self):
        """m3: a question abandoned at its deadline names the conversation it created; the concurrent line
        names the two it created."""
        drv, recette, A, page = self.start()
        recette.cases["t-slow"] = {"id": "t-slow", "level": "N8", "kind": "message", "question": "SLOW",
                                   "budget_s": 1, "nfr": ["disponibilite"], "must": [], "must_not": [], "pii": []}
        self.act(A, "nouvelle_conversation")
        self.refused(drv, A, "poser_question", {"case": "t-slow"})
        line = journal_lines(recette.work)[-1]
        self.assertEqual((line["case"], line["conversation"]["created_by"]), ("t-slow", "t-slow"))
        self.act(A, "charge_concurrente", {"cases": ["fixture-case-1", "fixture-case-2"]})
        line = journal_lines(recette.work)[-1]
        self.assertEqual(len(line["conversations"]), 2, line)

    def m4_dialog_during_a_rename(self, js: str) -> None:
        """A dialog that `js` opens from the « Renommer » entry's click: refused `dialog`, then the line is KO
        sécurité and says so."""
        drv, recette, A, page = self.start()
        self.first_message(recette, A)
        self.list_case(recette, "t-ren", action="renommer_conversation", same_conversation_as="t-a",
                       question="Titre nouveau", must=["titre relu . titre nouveau"])
        page.evaluate("""() => document.querySelector('#conv-menu [data-action="rename"]')
            .addEventListener('click', () => %s)""" % js)
        err = self.refused(drv, A, "renommer_conversation", {"case": "t-ren"})
        self.assertEqual(err.reason, "dialog")
        recette.browser.pending()["dialog"].dismiss()
        res, _ = self.act(A, "verdict_oracle", {"case": "t-ren"})
        line = journal_lines(recette.work)[-1]
        self.assertEqual((res["verdict"], line["case"]), ("KO", "t-ren"))
        self.assertIn("securite", line["nfr"])
        self.assertIn("dialogue inattendu (confirm) : pendant le renommage", line["evidence"])

    def test_m4_a_dialog_during_a_rename_reaches_the_line(self):
        """m4: a JavaScript dialog during renommer_conversation makes its line KO sécurité. Opened on the tick
        after the click: a page call in flight when it opens never returned (the driver hung in four runs out
        of six); the driver now looks before reading the page again (Browser.settled)."""
        self.m4_dialog_during_a_rename("setTimeout(() => confirm('pendant le renommage'), 0)")

    def test_m4_a_dialog_inside_the_click_handler_reaches_the_line(self):
        """m4: the same dialog opened inside the click handler: the click waits for its own timeout, the
        handle is released, then the dialog is seen."""
        self.m4_dialog_during_a_rename("confirm('pendant le renommage')")

    def test_m10_the_probe_keeps_no_title_the_menu_shows(self):
        """m10: a menu whose entries name their conversation: probe.json keeps none of it."""
        drv, recette, A, page = self.start("?menunames=1")
        page.evaluate("() => { window.__seed('Mon dossier privé numéro un'); window.__seed('Mon dossier privé numéro deux'); }")
        res, ev = self.act(A, "sonder_interface")
        probe = self.evidence_json(ev, "probe.json")
        self.assertEqual(len(probe["menu"]["menu"]["items"]), 3)
        self.assertNotIn("Mon dossier privé", json.dumps(probe, ensure_ascii=False))
        self.assertNotIn("Mon dossier privé", res["text"])

    def test_m10_a_title_that_is_only_an_ellipsis_never_hangs_the_probe(self):
        """m10, found while writing the fix report: the scrub replaced the title's first twelve characters
        until none was left, so a conversation titled « … » (a placeholder title) whose menu entries hold
        « … » made it loop for ever in the page. One pass now: the probe ends and reads the menu."""
        drv, recette, A, page = self.start("?menunames=1")
        page.evaluate("() => { window.__seed('Mon dossier privé numéro un'); window.__seed('…'); }")
        t0 = time.monotonic()
        res, ev = self.act(A, "sonder_interface")
        probe = self.evidence_json(ev, "probe.json")
        self.assertEqual(probe["errors"], [])
        self.assertEqual([i["label"] for i in probe["menu"]["menu"]["items"]],
                         ["Renommer « … »", "Partager « … »", "Supprimer « … »"])
        self.assertLess(time.monotonic() - t0, 30)

    def test_m11_an_answer_that_shows_an_internal_error_reaches_the_line_sanitised_and_flagged(self):
        """m11: an answer card ending with a stack frame and a host name."""
        drv, recette, A, page = self.start()
        self.message_case(recette, "t-trace", "Bonjour ANSWER_TRACE")
        self.act(A, "nouvelle_conversation")
        self.act(A, "poser_question", {"case": "t-trace"})
        self.act(A, "verdict_oracle", {"case": "t-trace"})
        line = journal_lines(recette.work)[-1]
        self.assertEqual(line["verdict"], "OK", "judged on the answer as it is")
        for bad in ("Traceback", "/srv/", "render.py", "db-answer"):
            self.assertNotIn(bad, line["evidence"])
        self.assertIs(line["internal_error_shown"], True)

    def test_m13_a_conversation_the_upload_created_is_registered(self):
        """m13: the upload creates the conversation, before the question: still the run's."""
        drv, recette, A, page = self.start("?convonupload=1")
        self.act(A, "nouvelle_conversation")
        self.act(A, "joindre_document", {"case": "fixture-case-1"})
        self.act(A, "poser_question", {"case": "fixture-case-1"})
        entry = recette._registered("fixture-case-1")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["href"], drv.urlsplit(page.url).path)

    def test_m14_a_field_that_was_there_before_never_takes_the_title(self):
        """m14: « Renommer » opens no field, a « Filtrer… » field sits in the sidebar: nothing is typed."""
        drv, recette, A, page = self.start("?renamenofield=1&filterfield=1")
        self.first_message(recette, A)
        self.list_case(recette, "t-ren", action="renommer_conversation", same_conversation_as="t-a",
                       question="Titre nouveau")
        err = self.refused(drv, A, "renommer_conversation", {"case": "t-ren"})
        self.assertIn("pas de champ", err.detail)
        self.assertIsNone(page.evaluate("() => window.__filter_value"))

    def test_m15_a_row_button_that_is_no_menu_is_never_pressed(self):
        """m15: a « Pin action » button inside each row's link: the probe and the rename open the row's own
        menu, never press it."""
        drv, recette, A, page = self.start("?pinbutton=1")
        self.first_message(recette, A)
        self.act(A, "sonder_interface")
        self.list_case(recette, "t-ren", action="renommer_conversation", same_conversation_as="t-a",
                       question="Titre nouveau")
        self.act(A, "renommer_conversation", {"case": "t-ren"})
        self.assertEqual(page.evaluate("() => window.__pin_clicks"), 0)
        self.assertEqual(len(page.evaluate("() => window.__menu_opens")), 2)


# -- review of pilot v2.3, fix round 2 (.superpowers/fix-round-2.md, rulings R2-1 … R2-6) ---------------------
# R2-3: each legitimate single-conversation form is accepted and each multi-target form refused; one test
# per shape (generated below the class).
R2_MENUS_ACCEPTED = {
    "ellipsis": ["Renommer…", "Partager", "Supprimer…"],
    "three_dots": ["Renommer...", "Partager", "Supprimer..."],
    "shortcut_hint": ["Renommer", "Partager", "Supprimer ⌘⌫"],
    "edit_title": ["Modifier le titre", "Partager", "Supprimer"],
    "move_to_trash": ["Renommer", "Partager", "Déplacer vers la corbeille"],
    "english_ellipsis": ["Rename…", "Share", "Delete…"],
}
R2_MENUS_REFUSED = {
    "folder_menu": ["Renommer le dossier", "Supprimer tous les chats du dossier"],
    "all_messages": ["Renommer", "Supprimer tous les messages"],
    "every_chat": ["Rename", "Delete every chat in this folder"],
    "whole_selection": ["Renommer", "Supprimer toute la sélection"],
    "selected": ["Rename", "Delete selected"],
    "two_deletes": ["Renommer", "Supprimer", "Déplacer vers la corbeille"],
    "clear_history": ["Renommer", "Supprimer", "Vider l'historique"],
}
R2_CONFIRMS_ACCEPTED = {
    "title_as_noun": ("Supprimer « Recette trois renommée » ? Cette action est irréversible.", "Recette trois renommée"),
    "your_history": ("Supprimer la conversation ? Elle disparaîtra de votre historique.", None),
    "chat_history_en": ("Delete chat? It will be removed from your chat history.", None),
    "removed_from_history_fr": ("Cette conversation sera définitivement supprimée de votre historique.", None),
    "no_longer_in_your_conversations": ("Supprimer la conversation ? Elle n'apparaîtra plus dans vos conversations.",
                                        None),
    "thread": ("Delete thread?", None),
    "remove_from_history_en": ("Delete chat? This will remove it from your history.", None),
    "all_its_messages": ("Supprimer la conversation ? La conversation et tous ses messages seront définitivement "
                         "supprimés.", None),
}
R2_CONFIRMS_REFUSED = {
    "each_in_folder": "Supprimer le dossier ? Chaque conversation du dossier sera supprimée.",
    "folder_container": "Supprimer le dossier « Projets » ? La conversation qu'il contient sera supprimée.",
    "each_derived": "Supprimer cette conversation et chaque conversation dérivée ?",
    "all_conversations": "Supprimer toutes les conversations ?",
    "all_chats_en": "Delete all chats?",
    "whole_history": "Supprimer la conversation et tout l'historique ?",
    "clear_history_en": "Delete this chat and clear your history?",
    "selected_conversations": "Supprimer les 3 conversations sélectionnées ?",
    "no_noun": "Êtes-vous sûr ?",
    "workspace": "Delete this chat from the workspace?",
    "any_conversation": "Supprimer toute conversation de plus de 30 jours ?",
}
R2_BUTTONS_ACCEPTED = {
    "continuer": (["Annuler", "Continuer"], 1),
    "continue_en": (["Cancel", "Continue"], 1),
    "oui": (["Non", "Oui"], 1),
    "ne_pas_supprimer": (["Ne pas supprimer", "Supprimer"], 1),
    "dont_delete": (["Don't delete", "Delete"], 1),
    "keep": (["Keep", "Delete"], 1),
}
R2_BUTTONS_REFUSED = {
    "two_weak": ("alertdialog", ["Annuler", "Continuer", "OK"]),
    "two_strong": ("alertdialog", ["Supprimer", "Supprimer définitivement"]),
    "weak_in_a_plain_dialog": ("dialog", ["Annuler", "Continuer"]),
}


class FixRound2Units(unittest.TestCase):
    """Review of pilot v2.3, fix round 2 (.superpowers/fix-round-2.md): what needs no browser."""

    recette = FixRound1Units.recette
    verdict = staticmethod(FixRound1Units.verdict)

    @staticmethod
    def items(drv, labels):
        return [{"text": drv.evaluer.fold(label), "label": label, "disabled": False} for label in labels]

    # R2-3 shapes ---------------------------------------------------------------------------------------
    def check_menu_accepted(self, labels):
        drv = load_driver()
        for want in ("renommer", "supprimer"):
            pick, why = drv.menu_entry(self.items(drv, labels), want)
            self.assertIsNotNone(pick, (labels, want, why))
            self.assertRegex(drv.evaluer.fold(pick["label"]), r"renomm|rename|titre|title" if want == "renommer"
                             else r"suppr|delete|corbeille|trash")

    def check_menu_refused(self, labels):
        drv = load_driver()
        pick, why = drv.menu_entry(self.items(drv, labels), "supprimer")
        self.assertIsNone(pick, labels)
        self.assertTrue(why)
        for label in labels:  # R2-4: the reason reaches the harness, the entries never do
            self.assertNotIn(label, why)

    def check_confirm_accepted(self, text, title):
        drv = load_driver()
        self.assertTrue(drv.confirms_one_deletion(text, title), text)

    def check_confirm_refused(self, text):
        drv = load_driver()
        self.assertFalse(drv.confirms_one_deletion(text, "Recette trois renommée"), text)

    def check_buttons_accepted(self, buttons, want):
        drv = load_driver()
        dlg = {"role": "alertdialog", "buttons": [drv.evaluer.fold(b) for b in buttons]}
        self.assertEqual(drv.pick_confirm(dlg), (want, None), buttons)

    def check_buttons_refused(self, role, buttons):
        drv = load_driver()
        index, why = drv.pick_confirm({"role": role, "buttons": [drv.evaluer.fold(b) for b in buttons]})
        self.assertIsNone(index, buttons)
        self.assertTrue(why)

    def test_click_targets_6_a_confirmation_cut_by_the_driver_is_refused(self):
        """click-targets-6: the judgement reads the whole text; a text the driver had to cut is refused."""
        drv = load_driver()
        head = "Supprimer la conversation « Recette trois renommée » ? " + "Cette action est définitive. " * 10
        whole = head + "Toutes les conversations du projet seront aussi supprimées."
        self.assertGreater(len(whole), 300)
        self.assertTrue(drv.confirms_one_deletion(whole[:300]))  # what v2.3's 300-character cut judged
        self.assertFalse(drv.confirms_one_deletion(whole))
        self.assertEqual(drv.confirmation_refusal({"text": head, "cut": True}, None)[:5], "texte")

    # m11 (partial) -------------------------------------------------------------------------------------
    def test_m11_partial_the_usual_connection_errors_in_an_answer_are_flagged_and_sanitised(self):
        """m11, partial in round 1: I2's own forms in an answer card reach neither the line nor the harness
        raw; ordinary prose is never flagged."""
        drv = load_driver()
        hosts = ("redis", "anonymizer", "pii-db", "10.0.0.2", "presidio")
        for answer in ("Error -3 connecting to redis:6379. Temporary failure in name resolution.",
                       "Cannot connect to host anonymizer:8080 ssl:default",
                       "dial tcp: lookup pii-db on 10.0.0.2:53: no such host",
                       "connect ETIMEDOUT presidio-analyzer:5001",
                       "HTTPConnectionPool(host='presidio', port=5001): Read timed out."):
            with self.subTest(answer):
                self.assertTrue(drv.answer_shows_internal(answer))
                obs = {"answer": answer, "total_s": 1.0}
                res = drv.Recette._answer_result(obs, "t")
                self.assertIs(res["internal_error_shown"], True)
                summary = drv.Recette._summary({"missing": [], "forbidden": [], "leaks": [], "span_issues": [],
                                                "notes": []}, obs)
                for host in hosts:
                    for shown in (res["answer"], res["text"], summary):
                        self.assertNotIn(host, shown)
        for prose in ("Pour résoudre ce litige, adressez-vous au bailleur.", "Le serveur héberge le site du bailleur.",
                      "Rendez-vous à 10:30 au cabinet.", "Le délai est expiré : le bail a pris fin (article 12:3).",
                      "Le dépôt de garantie est d'un mois de loyer hors charges."):
            self.assertFalse(drv.answer_shows_internal(prose), prose)

    # leakage-4 -----------------------------------------------------------------------------------------
    def test_leakage_4_the_flag_reads_error_notices_with_the_case_file_and_the_app_hosts_kept(self):
        """leakage-4: an ordinary status, the attached file's name, the app's own host, an HTTP code: no
        internal error; a host behind a connection error, or a dotted internal host: one."""
        drv = load_driver()
        keep = ("facture-3.pdf", "app.maskai.fr")
        for notices in ([["status", "Fichier facture-3.pdf joint"]], [["toast", "Échec de l'analyse de facture-3.pdf"]],
                        [["toast", "Impossible de joindre app.maskai.fr"]], [["alert", "Erreur:500"]]):
            self.assertFalse(drv.notices_internal(notices, keep), notices)
        for notices in ([["toast", "Erreur : connect ECONNREFUSED pii-guard:8443"]],
                        [["status", "Une erreur est survenue lors de l'analyse du message (pii-guard.internal:8443)."]]):
            self.assertTrue(drv.notices_internal(notices, keep), notices)
        self.assertEqual(drv.sanitise("Erreur:500", 80), "Erreur:500")

    # leakage-2 -----------------------------------------------------------------------------------------
    def test_leakage_2_page_text_for_the_harness_is_sanitised_and_never_names_a_listed_title(self):
        """leakage-2: a dialog's or a window's text reaches the harness sanitised, flagged when it shows an
        internal error, and dropped whole when it holds a listed conversation's title."""
        drv = load_driver()
        shown, internal = drv.harness_text("Une erreur est survenue : connect ETIMEDOUT pii-db:5432", titles=())
        self.assertNotIn("pii-db", shown)
        self.assertTrue(internal)
        shown, internal = drv.harness_text("Search conversations Mon dossier privé numéro deux Facture",
                                           titles=("Mon dossier privé numéro deux",))
        self.assertNotIn("dossier", shown.lower())
        self.assertFalse(internal)
        self.assertEqual(drv.harness_text("Voulez-vous continuer ?", titles=("Mon dossier",)), ("Voulez-vous continuer ?",
                                                                                               False))

    # R2-1, R2-2 ----------------------------------------------------------------------------------------
    def test_r2_1_a_case_the_pilot_refused_by_its_own_guard_is_journaled_non_teste(self):
        """R2-1 (verdicts-1): a guard refusal before any irreversible click is NON TESTÉ, no NFR, with the
        guard's reason; a dialog during the same action is still KO sécurité (MaskAI's doing)."""
        drv = load_driver()
        recette = self.recette(drv)
        recette.observations["M8-6"] = {"untested": {"reason": "menu non reconnu", "t": drv.now_iso()}}
        self.assertEqual(self.verdict(recette, "M8-6")["verdict"], "NON TESTÉ")
        line = journal_lines(recette.work)[-1]
        self.assertEqual((line["case"], line["verdict"], line.get("nfr")), ("M8-6", "NON TESTÉ", None))
        self.assertIn("refus du pilote : menu non reconnu", line["evidence"])
        recette.observations["M8-5"] = {"untested": {"reason": "menu non reconnu", "t": drv.now_iso()},
                                        "dialog": {"type": "alert", "message": "pendant le renommage", "t": drv.now_iso()}}
        res = self.verdict(recette, "M8-5")
        line = journal_lines(recette.work)[-1]
        self.assertEqual((res["verdict"], line["case"]), ("KO", "M8-5"))
        self.assertIn("securite", line["nfr"])

    def test_r2_2_another_conversation_gone_makes_the_line_ko_securite_and_raises_the_flag(self):
        """R2-2 (verdicts-2, registry-2): a delete during which another conversation left the sidebar is KO
        sécurité, whatever its own oracle says; the result carries the flag the harness surfaces at once."""
        drv = load_driver()
        recette = self.recette(drv)
        recette.observations["@list"] = {"answer": "suppression de la conversation de M8-1 : plus listée ; 2 conversations "
                                                   ": a | b", "timing_s": 1.0, "case": "M8-6", "seq": 1,
                                         "others_gone": 2}
        res = self.verdict(recette, "M8-6")
        line = journal_lines(recette.work)[-1]
        self.assertEqual((res["verdict"], line["verdict"]), ("KO", "KO"))
        self.assertIn("securite", line["nfr"])
        self.assertIn("2 autre(s) conversation(s) disparue(s) pendant l'action", line["evidence"])
        self.assertEqual((res["cascade_delete"], line["others_gone"]), (2, 2))

    # verdicts-5 ----------------------------------------------------------------------------------------
    def test_verdicts_5_a_later_case_closes_the_last_question_window(self):
        """verdicts-5: a dialog during M8-5's rename is never written into M8-3's observation; one during a
        configuration case's choisir_modele (no case in hand) is noted under the case the action plays."""
        drv = load_driver()
        recette = self.recette(drv)
        now = time.monotonic()
        recette._last_asked = {"case": "M8-3", "mark": 0, "end": 2, "end_mono": now}
        recette.browser.seq = 4
        recette.browser.dialog_log = [{"seq": 3, "type": "confirm", "message": "après la réponse", "t": drv.now_iso(),
                                       "mono": now}]
        recette._take_case("M8-5")  # closes M8-3's window at mark 4
        recette.browser.dialog_log.append({"seq": 6, "type": "confirm", "message": "pendant M8-5", "t": drv.now_iso(),
                                           "mono": now})
        recette._late_events()
        self.assertEqual(recette.observations["M8-3"]["dialog"]["message"], "après la réponse")
        recette.observations.pop("M8-3")
        recette._last_asked = {"case": "M8-3", "mark": 4, "end": 5, "end_mono": now}
        recette._case_in_hand, recette._action_case = None, "M8-7"
        recette.browser.dialogs = [{"seq": 7, "type": "alert", "message": "pendant le choix du modèle", "t": drv.now_iso(),
                                    "mono": now, "dialog": None}]
        err = recette._dialog_refusal()
        self.assertEqual(err.reason, "dialog")
        self.assertEqual(recette.observations["M8-7"]["dialog"]["message"], "pendant le choix du modèle")
        self.assertNotIn("M8-3", recette.observations)

    # robustness-5 --------------------------------------------------------------------------------------
    def test_robustness_5_the_verdict_is_stamped_with_its_line(self):
        """robustness-5: the observation that a line judged carries that line's digest (correct.py replays
        only that one); a dialog added after the verdict is marked so."""
        drv = load_driver()
        recette = self.recette(drv)
        recette.observations["@list"] = {"answer": "2 conversations : Recette trois, fiche pratique | b", "timing_s": None,
                                         "seq": 1}
        res = self.verdict(recette, "M8-2")
        line = (recette.work / "journal.jsonl").read_text(encoding="utf-8").splitlines()[-1]
        self.assertEqual(recette.observations["M8-2"]["verdict"]["line_sha256"], drv.sha256_text(line))
        self.assertEqual(res["verdict"], "OK")
        recette._last_asked = {"case": "M8-2", "mark": 0, "end": 1, "end_mono": time.monotonic()}
        recette.browser.dialog_log = [{"seq": 2, "type": "alert", "message": "plus tard", "t": drv.now_iso(),
                                       "mono": time.monotonic()}]
        recette._late_events()
        self.assertIs(recette.observations["M8-2"]["dialog"]["after_verdict"], True)


def _r2_generate():
    """One test per R2-3 shape (the rulings: « one test each »)."""
    def add(name, fn):
        fn.__name__ = name
        setattr(FixRound2Units, name, fn)
    for key, labels in R2_MENUS_ACCEPTED.items():
        add(f"test_r2_3_menu_accepted_{key}", lambda self, labels=labels: self.check_menu_accepted(labels))
    for key, labels in R2_MENUS_REFUSED.items():
        add(f"test_r2_3_menu_refused_{key}", lambda self, labels=labels: self.check_menu_refused(labels))
    for key, (text, title) in R2_CONFIRMS_ACCEPTED.items():
        add(f"test_r2_3_confirmation_accepted_{key}",
            lambda self, text=text, title=title: self.check_confirm_accepted(text, title))
    for key, text in R2_CONFIRMS_REFUSED.items():
        add(f"test_r2_3_confirmation_refused_{key}", lambda self, text=text: self.check_confirm_refused(text))
    for key, (buttons, want) in R2_BUTTONS_ACCEPTED.items():
        add(f"test_r2_3_confirm_button_accepted_{key}",
            lambda self, buttons=buttons, want=want: self.check_buttons_accepted(buttons, want))
    for key, (role, buttons) in R2_BUTTONS_REFUSED.items():
        add(f"test_r2_3_confirm_button_refused_{key}",
            lambda self, role=role, buttons=buttons: self.check_buttons_refused(role, buttons))


_r2_generate()


@unittest.skipUnless(HAVE_PLAYWRIGHT, NO_PLAYWRIGHT)
class FixRound2OnFixture(FixtureBench):
    """Review of pilot v2.3, fix round 2, on the bench (.superpowers/fix-round-2.md, rulings R2-1 … R2-6)."""

    HREFS = FixRound1OnFixture.HREFS
    start = FixRound1OnFixture.start
    play_m8_1 = FixRound1OnFixture.play_m8_1
    refused = FixRound1OnFixture.refused
    verdict_after = FixRound1OnFixture.verdict_after
    list_case = staticmethod(FixRound1OnFixture.list_case)
    first_message = FixRound1OnFixture.first_message

    @staticmethod
    def q(**flags) -> str:
        return "?" + urllib.parse.urlencode(flags, quote_via=urllib.parse.quote)

    def line(self, recette, cid):
        return [x for x in journal_lines(recette.work) if x["case"] == cid][-1]

    def bench(self, query=""):
        """A bench closed at the end of the test, or as soon as the test opens the next one (one sync Playwright
        per thread), so that one test can try several page shapes."""
        previous = getattr(self, "_bench", None)
        if previous is not None:
            previous.browser.close()
        drv, recette, A, page = self.start(query)
        self._bench = recette
        self.addCleanup(recette.browser.close)
        return drv, recette, A, page

    # -- R2-1, R2-3: menus and confirmations ------------------------------------------------------------
    def test_registry_1_a_confirmation_the_pilot_cancels_is_non_teste_and_the_conversation_stays(self):
        """registry-1 (R2-1): a confirmation that aims at a folder is cancelled by Escape: the refusal names
        no text of it, M8-6 is NON TESTÉ, nothing is deleted, the registry goes back to « créée »."""
        drv, recette, A, page = self.bench(self.q(confirmtext="Supprimer le dossier ? Chaque conversation du dossier "
                                                              "sera supprimée."))
        self.play_m8_1(A)
        err = self.refused(drv, A, "supprimer_conversation", {"case": "M8-6"})
        self.assertEqual(err.reason, "precondition")
        self.assertTrue(err.detail.startswith("refus du pilote"), err.detail)
        self.assertNotIn("dossier", err.detail)
        record = self.evidence_json(err.evidence, "conversation.json")
        self.assertIn("dossier", record["confirmation"]["text"])
        self.assertEqual(self.act(A, "verdict_oracle", {"case": "M8-6"})[0]["verdict"], "NON TESTÉ")
        self.assertIsNone(self.line(recette, "M8-6").get("nfr"))
        self.assertEqual(page.evaluate("() => window.__deleted"), [])
        entry = recette._registered("M8-1")
        self.assertEqual(entry["state"], "créée")
        self.assertEqual([h["state"] for h in entry["history"]], ["créée", "suppression demandée", "suppression annulée"])

    def test_registry_1_the_legitimate_confirmations_are_accepted(self):
        """registry-1 (R2-3): « Continuer » as the single non-cancel button, the title in place of the noun,
        « supprimée de votre historique »: each is confirmed, and M8-6 is OK."""
        for name, query in (("Continuer", self.q(confirmlabel="Continuer")),
                            ("title as the noun", self.q(confirmtext="Supprimer « {title} » ? Cette action est irréversible.")),
                            ("your history", self.q(confirmtext="Cette conversation sera définitivement supprimée de "
                                                                "votre historique."))):
            with self.subTest(name):
                drv, recette, A, page = self.start(query)
                try:
                    self.play_m8_1(A)
                    href = recette._registered("M8-1")["href"]
                    self.act(A, "supprimer_conversation", {"case": "M8-6"})
                    self.assertEqual(self.act(A, "verdict_oracle", {"case": "M8-6"})[0]["verdict"], "OK")
                    self.assertEqual(page.evaluate("() => window.__deleted"), [href.rsplit("/", 1)[1]])
                finally:
                    recette.browser.close()

    def test_robustness_1_a_menu_with_ellipses_and_a_shortcut_hint_is_accepted_by_the_probe_and_the_actions(self):
        """robustness-1 (R2-3): « Renommer… | Partager | Supprimer… » with a ⌘⌫ hint: the probe says the menu
        is accepted, and M8-5 and M8-6 are played; a menu without rename: the probe says refused, before M8."""
        drv, recette, A, page = self.bench(self.q(menuitems="Renommer…,Partager,Supprimer…", menushortcut="1"))
        self.play_m8_1(A)
        res, ev = self.act(A, "sonder_interface")
        self.assertIn("M8-5/M8-6 : menu accepté", res["text"])
        self.assertEqual(self.evidence_json(ev, "probe.json")["menu_verdict"], {"renommer": None, "supprimer": None})
        verdicts = []
        for cid, action in (("M8-5", "renommer_conversation"), ("M8-6", "supprimer_conversation")):
            self.act(A, action, {"case": cid})  # each judged right after its action, as the recipe plays it
            verdicts.append(self.act(A, "verdict_oracle", {"case": cid})[0]["verdict"])
        self.assertEqual(verdicts, ["OK", "OK"])
        drv, recette, A, page = self.bench(self.q(menuitems="Partager,Supprimer"))
        self.first_message(recette, A)
        res, _ = self.act(A, "sonder_interface")
        self.assertIn("M8-5/M8-6 : menu refusé", res["text"])
        self.assertNotIn("Partager", res["text"])  # R2-4: the entries stay in probe.json

    def test_click_targets_6_a_confirmation_that_widens_after_300_characters_is_refused(self):
        """click-targets-6: one conversation named first, all of the project's after 300 characters: the
        judgement reads the whole text; refused, NON TESTÉ, nothing deleted."""
        text = ("Supprimer la conversation « Recette trois » ? " + "Cette action est définitive et ne peut pas être "
                "annulée. " * 6 + "Toutes les conversations du projet seront aussi supprimées.")
        drv, recette, A, page = self.bench(self.q(confirmtext=text))
        self.play_m8_1(A)
        self.refused(drv, A, "supprimer_conversation", {"case": "M8-6"})
        self.assertEqual(self.act(A, "verdict_oracle", {"case": "M8-6"})[0]["verdict"], "NON TESTÉ")
        self.assertEqual(page.evaluate("() => window.__deleted"), [])

    def test_robustness_6_a_native_confirmation_is_left_to_the_human_and_non_teste(self):
        """robustness-6: the delete entry asks through window.confirm: never answered by the pilot, refused
        `dialog`, M8-6 NON TESTÉ « confirmation native », nothing deleted, M8-1's line untouched."""
        drv, recette, A, page = self.bench(self.q(confirmnative="1"))
        self.play_m8_1(A)
        err = self.refused(drv, A, "supprimer_conversation", {"case": "M8-6"})
        self.assertEqual(err.reason, "dialog")
        self.assertIn("confirmation native", err.detail)
        pending = recette.browser.pending()
        self.assertEqual(pending["type"], "confirm")
        pending["dialog"].dismiss()  # the founder cancels it
        res = self.act(A, "verdict_oracle", {"case": "M8-6"})[0]
        self.assertEqual(res["verdict"], "NON TESTÉ")
        self.assertIn("confirmation native : le pilote ne répond jamais à un dialogue", self.line(recette, "M8-6")["evidence"])
        self.assertEqual(page.evaluate("() => window.__deleted"), [])
        self.assertEqual(recette._registered("M8-1")["state"], "suppression demandée")
        self.assertNotIn("dialog", recette.observations["M8-1"])

    # -- R2-2: a cascade ---------------------------------------------------------------------------------
    def test_r2_2_a_delete_that_takes_another_conversation_is_ko_securite_flagged_and_recorded(self):
        """R2-2 (verdicts-2, registry-2): the confirm click also removes a conversation of before: M8-6 is KO
        sécurité, the result and the verdict carry `cascade_delete`, the registry says « suppression en
        cascade », and no title of before reaches anything the harness sees."""
        drv, recette, A, page = self.bench(self.q(cascade="1"))
        page.evaluate("() => window.__seed('Mon dossier privé numéro un')")
        self.play_m8_1(A)
        res, ev = self.act(A, "supprimer_conversation", {"case": "M8-6"})
        self.assertEqual(res["cascade_delete"], 1)
        verdict = self.act(A, "verdict_oracle", {"case": "M8-6"})[0]
        self.assertEqual((verdict["verdict"], verdict["cascade_delete"]), ("KO", 1))
        line = self.line(recette, "M8-6")
        self.assertIn("securite", line["nfr"])
        self.assertIn("1 autre(s) conversation(s) disparue(s) pendant l'action", line["evidence"])
        entry = recette._registered("M8-1")
        self.assertEqual(entry["history"][-1]["state"], "suppression en cascade")
        self.assertEqual(entry["history"][-1]["count"], 1)
        for shown in (json.dumps(res, ensure_ascii=False), json.dumps(verdict, ensure_ascii=False), json.dumps(line, ensure_ascii=False)):
            self.assertNotIn("dossier", shown)

    # -- click-targets-2, registry-4: a group's menu ------------------------------------------------------
    def test_registry_4_a_group_s_menu_is_never_taken_for_the_row_s(self):
        """click-targets-2, registry-4: a shadcn group (its label a div) whose action menu says « Renommer » /
        « Supprimer »: the row alone in it, without a trigger, is NON TESTÉ and the group's menu never opens;
        with its own span[role=button] trigger, that one is clicked and the rename is done."""
        base = dict(grouped="1", grouplabel="div", groupitems="Renommer,Supprimer")
        for name, query, rename in (("row without trigger", self.q(**base), "NON TESTÉ"),
                                    ("row with its own trigger", self.q(rowtrigger="1", **base), "OK")):
            with self.subTest(name):
                drv, recette, A, page = self.start(query)
                try:
                    entry = self.first_message(recette, A)
                    self.list_case(recette, "t-ren", action="renommer_conversation", same_conversation_as="t-a",
                                   question="Titre nouveau", must=["titre relu . titre nouveau"])
                    self.assertEqual(self.verdict_after(drv, A, "renommer_conversation", "t-ren"), rename)
                    self.assertEqual(page.evaluate("() => [window.__group_menu_opens, window.__group_picks]"), [0, []])
                    if rename == "OK":
                        self.assertEqual(page.evaluate("() => window.__menu_opens"), [entry["href"].rsplit("/", 1)[1]])
                finally:
                    recette.browser.close()

    # -- registry-5, registry-6, verdicts-6: what « plus listée » and « titre relu » need -----------------------
    def test_registry_5_an_emptied_list_or_a_refused_delete_is_never_plus_listee(self):
        """registry-5: the list emptied for a while (nothing deleted), and an optimistic removal the server
        refuses (the row comes back): neither is « plus listée », the case is KO, the registry never says
        « supprimée »."""
        for name, query in (("list emptied", self.q(deleteempties="1")), ("server refuses", self.q(deletefail="1"))):
            with self.subTest(name):
                drv, recette, A, page = self.start(query)
                try:
                    self.first_message(recette, A)
                    self.list_case(recette, "t-del", action="supprimer_conversation", same_conversation_as="t-a",
                                   budget_s=2, must=["plus listee"], must_not=["t-a : encore listee"])
                    self.assertEqual(self.verdict_after(drv, A, "supprimer_conversation", "t-del"), "KO")
                    self.assertNotRegex(recette.observations["t-del"]["observed"]["answer"], r"de t-a : plus listée")
                    self.assertNotEqual(recette._registered("t-a")["state"], "supprimée")
                finally:
                    recette.browser.close()

    def test_registry_6_the_registry_holds_the_delete_before_any_click_can_fail(self):
        """registry-6: « suppression demandée » before the entry click (an action cut after it never clicks
        again); « suppression confirmée » before the confirm click (whose handler opens an alert)."""
        drv, recette, A, page = self.bench(self.q(noconfirm="1"))
        self.first_message(recette, A)
        self.list_case(recette, "t-del", action="supprimer_conversation", same_conversation_as="t-a", budget_s=2)

        def cut(*_args, **_kw):
            raise drv.PWError("cut after the click")
        recette._confirm_delete = cut
        self.refused(drv, A, "supprimer_conversation", {"case": "t-del"})
        self.assertEqual(recette._registered("t-a")["state"], "suppression demandée")
        opens = page.evaluate("() => window.__menu_opens.length")
        try:
            self.act(A, "supprimer_conversation", {"case": "t-del"})
        except drv.DriverRefusal:
            pass
        self.assertEqual(page.evaluate("() => window.__menu_opens.length"), opens, "never clicked again")
        drv, recette, A, page = self.bench(self.q(confirmalert="1"))
        self.first_message(recette, A)
        self.list_case(recette, "t-del", action="supprimer_conversation", same_conversation_as="t-a", budget_s=2)
        err = self.refused(drv, A, "supprimer_conversation", {"case": "t-del"})
        self.assertEqual(err.reason, "dialog")
        self.assertEqual(recette._registered("t-a")["state"], "suppression confirmée")
        recette.browser.pending()["dialog"].dismiss()

    def test_registry_6_a_delete_already_asked_for_is_never_clicked_again_and_is_non_teste(self):
        """registry-6, R2-1: the registry says « suppression demandée » and the conversation is still listed (a
        native confirmation the founder cancelled, an action cut after the click): the pilot never clicks the
        delete again, and the case is NON TESTÉ, never KO."""
        drv, recette, A, page = self.bench(self.q(noconfirm="1"))
        self.first_message(recette, A)
        self.list_case(recette, "t-del", action="supprimer_conversation", same_conversation_as="t-a", budget_s=2,
                       must=["plus listee"], must_not=["t-a : encore listee"])
        recette._set_state(recette._registered("t-a"), "suppression demandée", "t-del")
        opens = page.evaluate("() => window.__menu_opens.length")
        self.assertEqual(self.verdict_after(drv, A, "supprimer_conversation", "t-del"), "NON TESTÉ")
        self.assertEqual(page.evaluate("() => window.__menu_opens.length"), opens, "never clicked again")
        self.assertIsNone(self.line(recette, "t-del").get("nfr"))

    def test_r2_2_a_cascade_is_only_ever_charged_to_a_click(self):
        """R2-2: nothing clicked (the registry says « supprimée », its row gone): a conversation that leaves the
        sidebar meanwhile is no cascade of the pilot's, and the case is not KO sécurité."""
        drv, recette, A, page = self.bench()
        entry = self.first_message(recette, A)
        self.list_case(recette, "t-del", action="supprimer_conversation", same_conversation_as="t-a", budget_s=2,
                       must=["plus listee"])
        recette._set_state(entry, "supprimée", "t-del")
        page.evaluate("(h) => document.querySelector(`#conversations a[href='${h}']`).closest('li').remove()",
                      entry["href"])
        real, calls = recette._conversation_state, []

        def state(p):  # the first reading lists one more conversation, gone at the next ones
            st = real(p)
            calls.append(1)
            if st is not None and len(calls) == 1:
                st = {**st, "items": list(st.get("items") or []) + [["/conversation/elsewhere", "Une autre"]]}
            return st
        recette._conversation_state = state
        res, _ = self.act(A, "supprimer_conversation", {"case": "t-del"})
        self.assertNotIn("cascade_delete", res)
        self.assertEqual(self.act(A, "verdict_oracle", {"case": "t-del"})[0]["verdict"], "OK")

    def test_registry_7_a_survey_that_opens_before_the_rename_field_is_never_typed_into(self):
        """registry-7: « Renommer » brings « Votre avis » (its text field focused), the inline field 1 s
        later: the title goes into the inline field only; nothing is typed into, or sent from, the survey."""
        drv, recette, A, page = self.bench(self.q(surveyonrename="1", surveyinput="1"))
        entry = self.first_message(recette, A)
        self.list_case(recette, "t-ren", action="renommer_conversation", same_conversation_as="t-a",
                       question="Titre nouveau", must=["titre relu . titre nouveau"])
        self.act(A, "renommer_conversation", {"case": "t-ren"})
        self.assertEqual(page.evaluate("() => [document.getElementById('avis-subject').value, window.__survey_sent]"), ["", 0])
        self.assertEqual(page.evaluate("() => window.__renamed"), {entry["href"].rsplit("/", 1)[1]: "Titre nouveau"})

    def test_verdicts_6_a_slow_delete_and_a_slow_rename_are_waited_for_and_timed(self):
        """verdicts-6: the row goes 3.4 s after the confirm, the inline field stays 3 s after Enter: both
        OK, timed from the click; the text names the request."""
        drv, recette, A, page = self.bench(self.q(slowdelete="1", slowrename="1"))
        self.play_m8_1(A)
        res, _ = self.act(A, "renommer_conversation", {"case": "M8-5"})
        self.assertIn("requête PATCH 204", res["text"])
        self.assertEqual(self.act(A, "verdict_oracle", {"case": "M8-5"})[0]["verdict"], "OK")
        res, _ = self.act(A, "supprimer_conversation", {"case": "M8-6"})
        self.assertIn("requête DELETE 204", res["text"])
        self.assertEqual(self.act(A, "verdict_oracle", {"case": "M8-6"})[0]["verdict"], "OK")
        self.assertGreaterEqual(self.line(recette, "M8-6")["timing_s"], 3.0)
        self.assertGreaterEqual(self.line(recette, "M8-5")["timing_s"], 2.5)

    def test_verdicts_6_a_delete_without_window_is_said_and_a_delete_without_request_is_ko(self):
        """verdicts-6: no confirmation window, the row gone after a DELETE 204: « sans confirmation », OK;
        the row gone with no request at all: never « plus listée », KO."""
        drv, recette, A, page = self.bench(self.q(deletedirect="1"))
        self.play_m8_1(A)
        res, _ = self.act(A, "supprimer_conversation", {"case": "M8-6"})
        self.assertIn("sans confirmation", res["text"])
        self.assertEqual(self.act(A, "verdict_oracle", {"case": "M8-6"})[0]["verdict"], "OK")
        drv, recette, A, page = self.bench(self.q(nodeleterequest="1"))
        self.first_message(recette, A)
        self.list_case(recette, "t-del", action="supprimer_conversation", same_conversation_as="t-a", budget_s=2,
                       must=["plus listee"], must_not=["t-a : encore listee"])
        self.assertEqual(self.verdict_after(drv, A, "supprimer_conversation", "t-del"), "KO")
        self.assertIn("aucune requête", self.line(recette, "t-del")["evidence"])

    # -- R2-5: dialogs ------------------------------------------------------------------------------------
    def test_robustness_2_a_dialog_the_human_closed_at_once_is_still_the_action_s(self):
        """robustness-2: the m4 rename, the dialog closed 0 s after it opened (dialogclosed before the driver
        looks): still refused `dialog`, still KO sécurité."""
        drv, recette, A, page = self.bench()
        self.first_message(recette, A)
        self.list_case(recette, "t-ren", action="renommer_conversation", same_conversation_as="t-a",
                       question="Titre nouveau", must=["titre relu . titre nouveau"])
        page.evaluate("""() => document.querySelector('#conv-menu [data-action="rename"]')
            .addEventListener('click', () => setTimeout(() => confirm('pendant le renommage'), 0))""")
        recette.browser.context.on("dialog", lambda d: d.dismiss())  # the human, at once (after the driver's handler)
        err = self.refused(drv, A, "renommer_conversation", {"case": "t-ren"})
        self.assertEqual(err.reason, "dialog")
        res = self.act(A, "verdict_oracle", {"case": "t-ren"})[0]
        line = self.line(recette, "t-ren")
        self.assertEqual(res["verdict"], "KO")
        self.assertIn("securite", line["nfr"])
        self.assertIn("dialogue inattendu (confirm) : pendant le renommage", line["evidence"])

    def test_robustness_4_the_probe_s_reads_end_with_a_dialog_already_open(self):
        """robustness-4: with a confirm already open, the probe's selector census, entry reads and file input
        read raise within their bound, never hang (locator.count() and is_visible() never returned)."""
        drv, recette, A, page = self.bench()
        page.evaluate("() => { setTimeout(() => confirm('pendant la sonde'), 0); }")
        deadline = time.monotonic() + 5
        while recette.browser.pending() is None and time.monotonic() < deadline:
            recette.browser.drain(50)
        self.assertIsNotNone(recette.browser.pending())
        t0 = time.monotonic()
        for name, fn in (("selectors", lambda: recette._probe_selectors(page)),
                         ("entry", lambda: recette._identified_entry(page, drv.SELECTORS["search_entry"],
                                                                     drv.SELECTORS["search_words"], True)),
                         ("file input", lambda: recette._file_input(page))):
            with self.subTest(name), self.assertRaises(drv.PWError):
                fn()
        self.assertLess(time.monotonic() - t0, 40)
        recette.browser.pending()["dialog"].dismiss()

    def test_verdicts_5_a_dialog_during_a_configuration_case_is_that_case_s(self):
        """verdicts-5: a confirm on the tick after the model button's click, while M8-7 is the case to do: M8-7
        is KO sécurité, the last question's observation stays untouched."""
        drv, recette, A, page = self.bench(self.q(modeldialog="1"))
        self.message_case(recette, "t-q", "Recette banc : une question")
        self.act(A, "nouvelle_conversation")
        self.act(A, "poser_question", {"case": "t-q"})
        self.act(A, "verdict_oracle", {"case": "t-q"})
        with self.assertRaises(drv.DriverRefusal):  # still to do: the case the next action plays
            self.act(A, "verdict_oracle", {"case": "M8-7"})
        err = self.refused(drv, A, "choisir_modele", {"model": "claude haiku 5-5"})
        self.assertEqual(err.reason, "dialog")
        recette.browser.pending()["dialog"].dismiss()
        res = self.act(A, "verdict_oracle", {"case": "M8-7"})[0]
        self.assertEqual(res["verdict"], "KO")
        self.assertIn("securite", self.line(recette, "M8-7")["nfr"])
        self.assertNotIn("dialog", recette.observations["t-q"])

    # -- R2-4: what reaches the harness ------------------------------------------------------------------------
    def test_click_targets_1_a_conversation_link_is_never_an_entry(self):
        """click-targets-1: ⌘K unbound, the entry a plain div, a conversation of before titled « Search
        conversations in Slack »: never clicked, no navigation, NON TESTÉ."""
        drv, recette, A, page = self.bench(self.q(searchentry="div", nokbd="1"))
        page.evaluate("() => window.__seed('Search conversations in Slack')")
        start = drv.urlsplit(page.url).path
        self.list_case(recette, "t-search", action="lister_conversations", question="Recette")
        err = self.refused(drv, A, "lister_conversations", {"case": "t-search"})
        self.assertEqual(err.reason, "precondition")
        self.assertNotIn("Slack", err.detail)
        self.assertEqual(drv.urlsplit(page.url).path, start)
        self.assertNotIn("moved_to", self.evidence_json(err.evidence, "search.json"))
        self.assertNotIn("@list", recette.observations)
        self.assertEqual(self.act(A, "verdict_oracle", {"case": "t-search"})[0]["verdict"], "NON TESTÉ")

    def test_extra_a_row_never_feeds_a_refusal_whatever_its_link_carries(self):
        """Extra (row button, partial in round 1): a hidden badge before the title, « 2 h » after it, a pin
        emoji, a short title: no candidate comes from a row, and the refusal and its extra carry counts only."""
        drv, recette, A, page = self.bench(self.q(feedbacklabel="Envoyer un retour", rowlabels="1", rowbadge="1",
                                                  rowtime="1"))
        page.evaluate("() => { window.__seed('Avis juridique Dupont'); window.__seed('Avis RH'); "
                      "window.__seed('📌 Retour client Martin'); }")
        err = self.refused(drv, A, "ouvrir_avis", {"case": "M8-8"})
        self.assertEqual(err.reason, "precondition")
        shown = err.detail + json.dumps(err.extra, ensure_ascii=False)
        for word in ("Dupont", "Avis RH", "Martin", "Envoyer un retour"):
            self.assertNotIn(word, shown)
        avis = self.evidence_json(err.evidence, "avis.json")
        self.assertEqual(avis["candidates"], ["Envoyer un retour ✉"])
        self.assertEqual(self.act(A, "verdict_oracle", {"case": "M8-8"})[0]["verdict"], "NON TESTÉ")

    def test_r2_1_a_survey_window_the_pilot_does_not_recognise_is_non_teste(self):
        """R2-1 (verdicts-1): the feedback entry opens a window that is not the known survey: refused, left
        open for the human, M8-8 NON TESTÉ."""
        drv, recette, A, page = self.bench(self.q(surveyother="1"))
        err = self.refused(drv, A, "ouvrir_avis", {"case": "M8-8"})
        self.assertEqual(err.reason, "dialog")
        self.assertFalse(page.evaluate("() => document.getElementById('survey2').hidden"))
        page.evaluate("() => { document.getElementById('survey2').hidden = true; }")  # the founder closes it
        self.assertEqual(self.act(A, "verdict_oracle", {"case": "M8-8"})[0]["verdict"], "NON TESTÉ")

    def test_click_targets_7_a_field_visible_before_the_key_is_never_the_palette(self):
        """click-targets-7: a cmdk filter in the sidebar before any key: with ⌘K unbound and no entry, the search
        is NON TESTÉ and the filter untouched; with ⌘K, the palette that opens is read, never the filter."""
        drv, recette, A, page = self.bench(self.q(searchfilter="1", nokbd="1", searchentry="div"))
        self.list_case(recette, "t-search", action="lister_conversations", question="Recette")
        self.refused(drv, A, "lister_conversations", {"case": "t-search"})
        self.assertIsNone(page.evaluate("() => window.__sidebar_filter_value"))
        self.assertEqual(self.act(A, "verdict_oracle", {"case": "t-search"})[0]["verdict"], "NON TESTÉ")
        drv, recette, A, page = self.bench(self.q(searchfilter="1"))
        self.play_m8_1(A)
        self.act(A, "lister_conversations", {"case": "M8-4"})
        self.assertIsNone(page.evaluate("() => window.__sidebar_filter_value"))
        self.assertEqual(self.act(A, "verdict_oracle", {"case": "M8-4"})[0]["verdict"], "OK")

    def test_verdicts_4_the_search_is_judged_on_what_the_typing_changed(self):
        """verdicts-4, robustness-7: recent conversations listed before any typing and a search that answers
        in 1.5 s: the list read is the search's; an item that only echoes the query is no result; a list the
        typing never changes is said so, KO."""
        seeds = "() => { for (const t of ['Facture Berthod', 'Bail Martin', 'Note de frais']) window.__seed(t); }"
        drv, recette, A, page = self.bench(self.q(recents="1"))
        page.evaluate(seeds)
        self.play_m8_1(A)
        res, ev = self.act(A, "lister_conversations", {"case": "M8-4"})
        self.assertEqual(res["count"], 1)
        self.assertIs(self.evidence_json(ev, "search.json")["changed"], True)
        self.assertEqual(self.act(A, "verdict_oracle", {"case": "M8-4"})[0]["verdict"], "OK")
        recette.browser.close()  # one sync Playwright per thread
        for name, query in (("echo", self.q(echo="1")), ("unchanged", self.q(recents="1", searchfail="1"))):
            with self.subTest(name):
                drv, recette, A, page = self.start(query)
                try:
                    page.evaluate(seeds)
                    self.first_message(recette, A, question="Recette trois : une question du banc")
                    self.list_case(recette, "t-search", action="lister_conversations", question="Recette trois",
                                   budget_s=2, must=["resultats? : .*recette trois"],
                                   must_not=["resultats inchanges par la frappe"])
                    self.assertEqual(self.verdict_after(drv, A, "lister_conversations", "t-search"), "KO")
                finally:
                    recette.browser.close()

    def test_click_targets_8_a_button_that_only_says_nouvelle_conversation_is_never_pressed(self):
        """click-targets-8: no /new link, an « Effacer et démarrer une nouvelle conversation » button in the
        page: never pressed; back to the entry page."""
        drv, recette, A, page = self.bench(self.q(nonewlink="1", clearbutton="1"))
        res, _ = self.act(A, "nouvelle_conversation")
        self.assertIn("retour à la page d'entrée", res["text"])
        self.assertEqual(page.evaluate("() => window.__clear_clicks"), 0)

    def test_leakage_1_a_toast_naming_a_host_is_flagged_and_kept_in_the_evidence(self):
        """leakage-1: a toast « connect ECONNREFUSED pii-guard:8443 » 0.8 s into a question, answered or
        abandoned: flagged on the result and the line, its raw text in notices.json, no host on the line."""
        drv, recette, A, page = self.bench()
        self.message_case(recette, "t-toast", "Bonjour TOAST_HOST")
        self.act(A, "nouvelle_conversation")
        res, ev = self.act(A, "poser_question", {"case": "t-toast"})
        self.assertIs(res["internal_error_shown"], True)
        self.assertIn("pii-guard:8443", json.dumps(self.evidence_json(ev, "notices.json"), ensure_ascii=False))
        self.act(A, "verdict_oracle", {"case": "t-toast"})
        line = self.line(recette, "t-toast")
        self.assertIs(line["internal_error_shown"], True)
        self.assertNotIn("pii-guard", json.dumps(line, ensure_ascii=False))
        self.message_case(recette, "t-toast-slow", "Bonjour TOAST_HOST SLOW")
        recette.cases["t-toast-slow"]["budget_s"] = 1
        self.act(A, "nouvelle_conversation")
        err = self.refused(drv, A, "poser_question", {"case": "t-toast-slow"})
        self.assertEqual(err.reason, "timeout")
        line = json.loads(err.extra["journal"])
        self.assertIs(line["internal_error_shown"], True)
        self.assertNotIn("pii-guard", json.dumps(line, ensure_ascii=False))
        self.assertIn("pii-guard:8443", json.dumps(self.evidence_json(err.evidence, "notices.json"), ensure_ascii=False))

    def test_leakage_2_a_confirmation_an_alert_and_an_unknown_window_reach_the_harness_sanitised(self):
        """leakage-2: a confirmation naming a host, an alert naming one, an unknown window listing the
        sidebar's titles: no host and no title in a refusal or a line; flagged where they show an internal
        error; the raw text in the evidence."""
        drv, recette, A, page = self.bench(self.q(confirmtext="Une erreur est survenue : connect ETIMEDOUT pii-db:5432"))
        self.play_m8_1(A)
        err = self.refused(drv, A, "supprimer_conversation", {"case": "M8-6"})
        self.assertNotIn("pii-db", err.detail)
        self.assertIn("pii-db", self.evidence_json(err.evidence, "conversation.json")["confirmation"]["text"])
        self.act(A, "verdict_oracle", {"case": "M8-6"})
        line = self.line(recette, "M8-6")
        self.assertEqual(line["verdict"], "NON TESTÉ")
        self.assertIs(line["internal_error_shown"], True)
        self.assertNotIn("pii-db", json.dumps(line, ensure_ascii=False))
        drv, recette, A, page = self.bench()
        self.message_case(recette, "t-alert", "Bonjour ALERT_HOST")
        self.act(A, "nouvelle_conversation")
        err = self.refused(drv, A, "poser_question", {"case": "t-alert"})
        self.assertEqual(err.reason, "dialog")
        self.assertNotIn("pii-guard", err.detail)
        recette.browser.pending()["dialog"].dismiss()
        self.act(A, "verdict_oracle", {"case": "t-alert"})
        line = self.line(recette, "t-alert")
        self.assertIn("securite", line["nfr"])
        self.assertIs(line["internal_error_shown"], True)
        self.assertNotIn("pii-guard", json.dumps(line, ensure_ascii=False))
        page.evaluate("() => window.__seed('Mon dossier privé numéro un')")
        self.act(A, "nouvelle_conversation")
        self.act(A, "envoyer_message", {"text": "Bonjour UNKNOWN_MODAL_TITLES"})
        err = self.refused(drv, A, "nouvelle_conversation", {})
        self.assertEqual(err.reason, "dialog")
        self.assertNotIn("dossier", err.detail)

    def test_leakage_2_after_a_relaunch_an_unknown_window_never_shows_a_title(self):
        """leakage-2 (after a relaunch, own_modal and the titles forgotten): an unknown window that lists the
        sidebar's titles before the driver read any listing: the refusal names no title."""
        drv, recette, A, page = self.bench()
        page.evaluate("() => window.__seed('Mon dossier privé numéro un')")
        page.evaluate("""() => { const m = document.getElementById('mystery');
            m.textContent = 'Récents : ' + [...document.querySelectorAll('#conversations a')].map((a) => a.textContent).join(' | ');
            m.hidden = false; }""")
        self.assertEqual(recette._titles, [])
        err = self.refused(drv, A, "nouvelle_conversation", {})
        self.assertEqual(err.reason, "dialog")
        self.assertNotIn("dossier", err.detail)

    def test_leakage_5_lire_reponse_and_capturer_flux_hand_the_harness_no_host(self):
        """leakage-5: the last answer shows a stack and a host, the stream's error names one: neither tool
        result carries them; lire_reponse says internal_error_shown."""
        drv, recette, A, page = self.bench()
        self.act(A, "nouvelle_conversation")
        self.act(A, "envoyer_message", {"text": "Bonjour ANSWER_TRACE"})
        res, _ = self.act(A, "lire_reponse")
        self.assertIs(res["internal_error_shown"], True)
        for bad in ("db-answer", "/srv/", "render.py"):
            self.assertNotIn(bad, json.dumps(res, ensure_ascii=False))
        self.act(A, "nouvelle_conversation")
        self.act(A, "envoyer_message", {"text": "Bonjour SILENT_FAIL"})
        res, _ = self.act(A, "capturer_flux")
        for bad in ("pii-db.internal", "/srv/maskai", "pii-guard.internal"):
            self.assertNotIn(bad, json.dumps(res, ensure_ascii=False))

    def test_m10_partial_the_probe_s_menu_keeps_no_word_of_the_title(self):
        """m10, partial in round 1: the menu shows the title cut, split across nodes, upper-cased by CSS, or
        the link holds a time after it: probe.json and the result keep no word of it."""
        for shape in ("trunc", "split", "upper", "date"):
            with self.subTest(shape):
                flags = {"menunames": "1", "menutitle": shape}
                if shape == "date":
                    flags["rowtime"] = "1"
                drv, recette, A, page = self.start(self.q(**flags))
                try:
                    page.evaluate("() => window.__seed('Avis juridique Dupont et associés')")
                    res, ev = self.act(A, "sonder_interface")
                    shown = (json.dumps(self.evidence_json(ev, "probe.json"), ensure_ascii=False)
                             + json.dumps(res, ensure_ascii=False)).lower()
                    for word in ("dupont", "juridique", "associ"):
                        self.assertNotIn(word, shown)
                finally:
                    recette.browser.close()

    # -- click-targets-3 to -5: the composer's own controls ---------------------------------------------------
    def test_click_targets_3_the_send_button_is_the_composer_s_or_none(self):
        """click-targets-3: Enter sends nothing and the composer's send is disabled: « Send feedback » is never
        pressed; the abandon says the interface refused the send."""
        drv, recette, A, page = self.bench(self.q(senddisabled="1"))
        self.message_case(recette, "t-send", "Bonjour")
        recette.cases["t-send"]["budget_s"] = 1
        self.act(A, "nouvelle_conversation")
        err = self.refused(drv, A, "poser_question", {"case": "t-send"})
        self.assertEqual(err.reason, "timeout")
        self.assertEqual(page.evaluate("() => [window.__feedback_opens, window.__survey_shown]"), [0, 0])
        self.assertIn("envoi refusé par l'interface", json.loads(err.extra["journal"])["evidence"])

    def test_click_targets_3_a_feedback_button_inside_the_composer_is_never_the_send(self):
        """click-targets-3 (« never the feedback entry »): « Send feedback » sits in the composer box after its
        disabled send: still never pressed, and the abandon says the interface refused the send."""
        drv, recette, A, page = self.bench(self.q(senddisabled="1", feedbackincomposer="1"))
        self.message_case(recette, "t-send", "Bonjour")
        recette.cases["t-send"]["budget_s"] = 1
        self.act(A, "nouvelle_conversation")
        err = self.refused(drv, A, "poser_question", {"case": "t-send"})
        self.assertEqual(err.reason, "timeout")
        self.assertEqual(page.evaluate("() => [window.__feedback_opens, window.__survey_shown]"), [0, 0])
        self.assertIn("envoi refusé par l'interface", json.loads(err.extra["journal"])["evidence"])

    def test_click_targets_4_the_stop_button_is_never_the_dictation_button(self):
        """click-targets-4: a dictation starts during the wait (« Stop dictation »): the abandon presses the
        generation's stop, never the dictation button."""
        drv, recette, A, page = self.bench(self.q(micdictation="1"))
        self.message_case(recette, "t-stop", "Bonjour SLOW")
        recette.cases["t-stop"]["budget_s"] = 1
        self.act(A, "nouvelle_conversation")
        err = self.refused(drv, A, "poser_question", {"case": "t-stop"})
        self.assertEqual(err.reason, "timeout")
        self.assertEqual(page.evaluate("() => window.__mic_clicks"), 0)
        self.assertIs(err.extra["abort"]["stop_clicked"], True)

    def test_click_targets_5_the_web_and_model_controls_are_the_composer_s(self):
        """click-targets-5: a data-mode theme toggle before the composer, a listbox button in the sidebar:
        neither is pressed; the composer's own controls are set."""
        drv, recette, A, page = self.bench(self.q(themetoggle="1", sidebarselect="1"))
        res, _ = self.act(A, "recherche_web", {"mode": "off"})
        self.assertEqual(res["mode"], "off")
        res, _ = self.act(A, "choisir_modele", {"model": "gemini 3 flash"})
        self.assertEqual(res["model"], "google/eu.gemini-3-flash")
        self.assertEqual(page.evaluate("() => [window.__theme_clicks, window.__sidebar_select_clicks]"), [0, 0])


# -- Fix round 3 (.superpowers/review-v2.3-fix2.md) ------------------------------------------------------------------
# I-2 (R2-3, binding): a confirmation is accepted only when it names exactly one conversation and holds no plural
# conversation noun outside that name (the « dans vos conversations » idioms aside); M-6: a possessive is no
# quantifier. One test per shape.
R3_CONFIRMS_REFUSED = {
    "related_conversations": "Supprimer cette conversation et les conversations liées ?",
    "three_of_this_thread": "Les 3 conversations de ce fil seront supprimées.",
    "pinned_too": "Supprimer la conversation ? Les conversations épinglées seront aussi supprimées.",
    "related_chats_en": "Delete this chat and its related chats?",
    "shared_from_it_en": "Delete chat? Conversations shared from it will also be removed.",
    "a_number_before_the_noun": "Supprimer 2 conversations ?",
}
R3_CONFIRMS_ACCEPTED = {
    "no_longer_in_your_conversations": "Supprimer la conversation ? Elle n'apparaîtra plus dans vos conversations.",
    "possessive_all_messages_en": "Delete conversation? All of the conversation's messages will be deleted.",
    "from_your_chats_en": "Delete chat? It will be removed from your chats.",
}


class FixRound3Units(unittest.TestCase):
    """Review of the fix round 2 (.superpowers/review-v2.3-fix2.md): what needs no browser."""

    recette = FixRound1Units.recette

    def check_confirm_refused(self, text):
        self.assertFalse(load_driver().confirms_one_deletion(text, "Recette trois renommée"), text)

    def check_confirm_accepted(self, text):
        self.assertTrue(load_driver().confirms_one_deletion(text, "Recette trois renommée"), text)

    def test_i1_one_tail_rule_for_both_sides(self):
        """I-1: the menu entry's tail (an ellipsis, a keyboard hint, a function key) is stripped by one rule,
        which the page's script receives as is."""
        drv = load_driver()
        for label, want in (("Supprimer ⌘⌫", "supprimer"), ("Renommer F2", "renommer"),
                            ("Supprimer Ctrl+Suppr", "supprimer"), ("Renommer…", "renommer")):
            self.assertEqual(drv.menu_text(label), want, label)
        self.assertEqual(drv.MENU_TAIL_RE.pattern, drv.MENU_TAIL)
        self.assertIn("a.tail", drv._MENU_FIND)

    def test_m2_the_flag_keeps_the_case_s_names_and_a_cut_title_is_never_quoted(self):
        """M-2: a dialog naming the attached file or the app's host shows no internal error; a dialog quoting a
        cut title is replaced whole (the probe's word rule)."""
        drv = load_driver()
        self.assertIs(drv.harness_text("Le fichier facture-3.pdf n'a pas pu être lu", keep=("facture-3.pdf",))[1], False)
        self.assertIs(drv.harness_text("Impossible de joindre app.maskai.fr", keep=("app.maskai.fr",))[1], False)
        self.assertEqual(drv.harness_text("Supprimer « Avis juridi… » ?", titles=("Avis juridique Dupont",))[0], "…")
        recette = self.recette(drv)
        recette.browser.hosts = ("app.maskai.fr",)  # the application's own host, as on the live node
        self.assertEqual(recette._harness_text("Impossible de joindre app.maskai.fr", "M8-6"),
                         "Impossible de joindre app.maskai.fr")
        self.assertNotIn("internal_error_shown", recette.observations.get("M8-6") or {})


class FixRound3AfterUnits(unittest.TestCase):
    """M-7: the check after an action writes the raw message of the dialog it refuses."""

    recette = FixRound1Units.recette

    def test_m7_the_check_after_the_action_writes_the_raw_message(self):
        drv = load_driver()
        recette = self.recette(drv)
        d = {"seq": 9, "type": "alert", "message": "connect ECONNREFUSED pii-guard:8443", "t": "2026-10-20T12:00"}

        class Stand:  # a dialog pending when the action ends, as the browser reports it
            dialog_log, hosts = [d], ("app.maskai.fr",)

            def drain(self, ms):
                pass

            def pending(self):
                return d
        recette.browser, recette._mark, recette._action_case = Stand(), 0, "t-alert"
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        ev = drv.Evidence(folder)
        with self.assertRaises(drv.DriverRefusal) as refused:
            recette._after(ev)
        self.assertEqual(refused.exception.reason, "dialog")
        self.assertNotIn("pii-guard", refused.exception.detail)
        self.assertIn("pii-guard", self.evidence_json(ev.items, "dialog.json")["message"])

    evidence_json = staticmethod(FixtureBench.evidence_json)


def _r3_generate():
    """One test per I-2 shape (R2-3: « one test each »)."""
    def add(name, fn):
        fn.__name__ = name
        setattr(FixRound3Units, name, fn)
    for key, text in R3_CONFIRMS_REFUSED.items():
        add(f"test_i2_confirmation_refused_{key}", lambda self, text=text: self.check_confirm_refused(text))
    for key, text in R3_CONFIRMS_ACCEPTED.items():
        add(f"test_i2_confirmation_accepted_{key}", lambda self, text=text: self.check_confirm_accepted(text))


_r3_generate()


@unittest.skipUnless(HAVE_PLAYWRIGHT, NO_PLAYWRIGHT)
class FixRound3OnFixture(FixtureBench):
    """Review of the fix round 2 on the bench (.superpowers/review-v2.3-fix2.md, I-1 … I-3, M-1 … M-9). Round 2's
    helpers, borrowed (an inheritance would run round 2's tests a second time)."""

    HREFS = FixRound2OnFixture.HREFS
    start = FixRound2OnFixture.start
    play_m8_1 = FixRound2OnFixture.play_m8_1
    refused = FixRound2OnFixture.refused
    verdict_after = FixRound2OnFixture.verdict_after
    list_case = staticmethod(FixRound2OnFixture.list_case)
    first_message = FixRound2OnFixture.first_message
    q = staticmethod(FixRound2OnFixture.q)
    line = FixRound2OnFixture.line
    bench = FixRound2OnFixture.bench

    def hide(self, drv, recette, js):
        """The element lookup `js` finds nothing (an entry or a button gone after the pilot accepted it)."""
        real = recette.browser.element

        def element(script, arg, **kw):
            return None if script is js else real(script, arg, **kw)
        recette.browser.element = element

    # I-1 ----------------------------------------------------------------------------------------------------
    def test_i1_a_plain_text_hint_menu_is_accepted_and_clicked(self):
        """I-1: « Renommer F2 | Partager | Supprimer ⌘⌫ » in plain text: the probe accepts it, and the actions
        click those very entries: M8-5 and M8-6 OK."""
        drv, recette, A, page = self.bench(self.q(menuitems="Renommer F2,Partager,Supprimer ⌘⌫"))
        self.play_m8_1(A)
        self.assertIn("M8-5/M8-6 : menu accepté", self.act(A, "sonder_interface")[0]["text"])
        for cid, action in (("M8-5", "renommer_conversation"), ("M8-6", "supprimer_conversation")):
            self.act(A, action, {"case": cid})
            self.assertEqual(self.act(A, "verdict_oracle", {"case": cid})[0]["verdict"], "OK", cid)
        self.assertEqual(len(page.evaluate("() => window.__deleted")), 1)

    def test_i1_a_span_hint_menu_is_accepted_and_clicked(self):
        """I-1: the hints in a plain span (no kbd, no data-slot): the same entries are clicked."""
        drv, recette, A, page = self.bench(self.q(menuspanhint="1"))
        self.play_m8_1(A)
        self.assertIn("M8-5/M8-6 : menu accepté", self.act(A, "sonder_interface")[0]["text"])
        for cid, action in (("M8-5", "renommer_conversation"), ("M8-6", "supprimer_conversation")):
            self.act(A, action, {"case": cid})
            self.assertEqual(self.act(A, "verdict_oracle", {"case": cid})[0]["verdict"], "OK", cid)

    def test_i1_an_entry_gone_after_acceptance_is_non_teste_and_the_registry_untouched(self):
        """I-1 (R2-1): the menu was accepted, its entry cannot be found to click: nothing clicked, NON TESTÉ, and
        the registry never says « suppression demandée »."""
        drv, recette, A, page = self.bench()
        self.play_m8_1(A)
        self.hide(drv, recette, drv.MENU_ITEM_EL_JS)
        err = self.refused(drv, A, "supprimer_conversation", {"case": "M8-6"})
        self.assertTrue(err.detail.startswith("refus du pilote"), err.detail)
        self.assertEqual(self.act(A, "verdict_oracle", {"case": "M8-6"})[0]["verdict"], "NON TESTÉ")
        entry = recette._registered("M8-1")
        self.assertEqual((entry["state"], [h["state"] for h in entry["history"]]), ("créée", ["créée"]))
        self.assertEqual(page.evaluate("() => window.__deleted"), [])

    # I-2 ----------------------------------------------------------------------------------------------------
    def test_i2_a_confirmation_that_widens_with_a_plural_is_never_confirmed(self):
        """I-2: « Supprimer cette conversation et les conversations liées ? »: cancelled, NON TESTÉ, nothing
        deleted."""
        drv, recette, A, page = self.bench(self.q(confirmtext="Supprimer cette conversation et les conversations liées ?"))
        self.play_m8_1(A)
        self.refused(drv, A, "supprimer_conversation", {"case": "M8-6"})
        self.assertEqual(self.act(A, "verdict_oracle", {"case": "M8-6"})[0]["verdict"], "NON TESTÉ")
        self.assertEqual(page.evaluate("() => window.__deleted"), [])

    # I-3 ----------------------------------------------------------------------------------------------------
    def test_i3_a_dialog_closed_before_the_action_s_own_refusal_is_ko_securite(self):
        """I-3 (robustness-2): the rename click opens a confirm, the human dismisses it at once, then no field
        opens: refused `dialog`, KO sécurité, never KO disponibilité."""
        drv, recette, A, page = self.bench()
        self.first_message(recette, A)
        self.list_case(recette, "t-ren", action="renommer_conversation", same_conversation_as="t-a",
                       question="Titre nouveau", must=["titre relu . titre nouveau"])
        page.evaluate("""() => document.querySelector('#conv-menu [data-action="rename"]').addEventListener('click',
            (e) => { e.stopImmediatePropagation(); setTimeout(() => confirm('pendant le renommage'), 0); }, true)""")
        recette.browser.context.on("dialog", lambda d: d.dismiss())  # the human, at once
        err = self.refused(drv, A, "renommer_conversation", {"case": "t-ren"})
        self.assertEqual(err.reason, "dialog")
        self.assertEqual(self.act(A, "verdict_oracle", {"case": "t-ren"})[0]["verdict"], "KO")
        self.assertIn("securite", self.line(recette, "t-ren")["nfr"])

    def test_i3_a_late_native_confirmation_is_the_app_s_and_non_teste(self):
        """I-3 (robustness-6, async): the app's confirm opens 600 ms after the delete click, inside a read, and
        the human dismisses it: still the application's confirmation, NON TESTÉ, nothing deleted."""
        drv, recette, A, page = self.bench(self.q(confirmnative="1", nativedelay="1"))
        self.first_message(recette, A)
        self.list_case(recette, "t-del", action="supprimer_conversation", same_conversation_as="t-a", budget_s=2,
                       must=["plus listee"], must_not=["t-a : encore listee"])
        recette.browser.context.on("dialog", lambda d: d.dismiss())  # the human cancels it
        err = self.refused(drv, A, "supprimer_conversation", {"case": "t-del"})
        self.assertIn("confirmation native", err.detail)
        self.assertEqual(self.act(A, "verdict_oracle", {"case": "t-del"})[0]["verdict"], "NON TESTÉ")
        self.assertEqual(page.evaluate("() => window.__deleted"), [])

    # M-1 ----------------------------------------------------------------------------------------------------
    def test_m1_a_palette_item_with_a_snippet_is_a_result(self):
        """M-1: the item shows the title then a snippet, its value the title: counted, M8-4 OK."""
        drv, recette, A, page = self.bench(self.q(searchsnippet="1"))
        self.play_m8_1(A)
        res, _ = self.act(A, "lister_conversations", {"case": "M8-4"})
        self.assertEqual(res["count"], 1)
        self.assertEqual(self.act(A, "verdict_oracle", {"case": "M8-4"})[0]["verdict"], "OK")

    def test_m1_items_the_pilot_cannot_tie_to_a_conversation_are_non_teste(self):
        """M-1 (R2-1): the palette lists items that lead nowhere the pilot knows: « résultats non reconnus »,
        NON TESTÉ; the probe keeps the palette's item count."""
        drv, recette, A, page = self.bench(self.q(searchunknown="1"))
        self.play_m8_1(A)
        _, ev = self.act(A, "sonder_interface")
        self.assertIn("items", self.evidence_json(ev, "probe.json")["search"])
        try:
            self.act(A, "lister_conversations", {"case": "M8-4"})
        except drv.DriverRefusal:
            pass
        self.assertEqual(self.act(A, "verdict_oracle", {"case": "M8-4"})[0]["verdict"], "NON TESTÉ")
        self.assertIn("résultats non reconnus", self.line(recette, "M8-4")["evidence"])

    # M-3 ----------------------------------------------------------------------------------------------------
    def test_m3_a_confirm_button_gone_is_non_teste_and_the_registry_reverted(self):
        """M-3: the confirmation was accepted, its button is gone before the click: cancelled, NON TESTÉ, the
        registry back to « créée »."""
        drv, recette, A, page = self.bench()
        self.play_m8_1(A)
        self.hide(drv, recette, drv.CONFIRM_EL_JS)
        self.refused(drv, A, "supprimer_conversation", {"case": "M8-6"})
        self.assertEqual(self.act(A, "verdict_oracle", {"case": "M8-6"})[0]["verdict"], "NON TESTÉ")
        self.assertEqual(recette._registered("M8-1")["state"], "créée")
        self.assertEqual(page.evaluate("() => window.__deleted"), [])

    def test_m3_an_entry_that_opens_no_palette_is_non_teste(self):
        """M-3: ⌘K unbound, the identified entry is clicked and opens nothing the pilot recognises: NON TESTÉ."""
        drv, recette, A, page = self.bench(self.q(nokbd="1", searchentrydead="1"))
        self.list_case(recette, "t-search", action="lister_conversations", question="Recette")
        try:
            self.act(A, "lister_conversations", {"case": "t-search"})
        except drv.DriverRefusal:
            pass
        self.assertEqual(self.act(A, "verdict_oracle", {"case": "t-search"})[0]["verdict"], "NON TESTÉ")

    # M-5 ----------------------------------------------------------------------------------------------------
    def test_m5_a_write_to_another_host_is_no_proof_of_the_delete(self):
        """M-5: the delete fails (500) and the row comes back 4 s later; a POST to another host answered 204
        meanwhile: never « plus listée », KO, the registry never « supprimée »."""
        drv, recette, A, page = self.bench(self.q(deletedirect="1", deletefail="1", slowrollback="1", telemetry="1"))
        self.first_message(recette, A)
        self.list_case(recette, "t-del", action="supprimer_conversation", same_conversation_as="t-a", budget_s=2,
                       must=["plus listee"], must_not=["t-a : encore listee"])
        self.assertEqual(self.verdict_after(drv, A, "supprimer_conversation", "t-del"), "KO")
        self.assertNotEqual(recette._registered("t-a")["state"], "supprimée")

    # M-7, M-9 -----------------------------------------------------------------------------------------------
    def test_m9_a_timeout_refusal_carries_the_flag_at_its_top(self):
        """M-9: a toast naming a host during a question abandoned at 3 × its budget: the refusal's extra says
        internal_error_shown, for the harness's rule."""
        drv, recette, A, page = self.bench()
        self.message_case(recette, "t-slow", "Bonjour TOAST_HOST SLOW")
        recette.cases["t-slow"]["budget_s"] = 1
        self.act(A, "nouvelle_conversation")
        err = self.refused(drv, A, "poser_question", {"case": "t-slow"})
        self.assertEqual(err.reason, "timeout")
        self.assertIs(err.extra.get("internal_error_shown"), True)


if __name__ == "__main__":
    unittest.main()
