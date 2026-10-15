"""The maskai-recette driver on its fixture page, in headless Chromium.

The fixture server (fixture/serve.py) runs on a free port; each test builds a
`Recette` over its own work and profile directories, as the host would, and
calls the actions directly (the protocol loop itself is tested in
drivers/vkdriver/test_protocol.py).
"""
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
    """cases.json covers the grid the recipe runs (no browser needed)."""

    def test_cases_cover_n0_to_n9(self):
        drv = load_driver()
        cases = drv.load_cases()
        levels = {}
        for c in cases.values():
            levels.setdefault(c["level"], []).append(c)
            for key in ("id", "level", "kind", "nfr", "budget_s"):
                self.assertIn(key, c, c["id"])
            self.assertIn(c["kind"], ("load", "message", "attach_question", "list", "concurrent"), c["id"])
            self.assertLessEqual(set(c["nfr"]), set(drv.journal.NFR), c["id"])
            if c["kind"] in ("message", "attach_question"):
                self.assertIn("question", c, c["id"])
            if c["kind"] == "attach_question":
                drv.Recette._resolve(c["file"])
            if c.get("same_conversation_as"):
                self.assertIn(c["same_conversation_as"], cases, c["id"])
            for other in c.get("cases", []):
                self.assertIn(other, cases, c["id"])
        for n in range(10):
            self.assertGreaterEqual(len(levels.get(f"N{n}", [])), 2, f"N{n}")
        oracle = sorted(c["id"] for c in cases.values() if c.get("oracle"))
        self.assertEqual(oracle, sorted(f"V2-{k}" for k in ("L1", "L2", "L3", "L4", "L5", "V1", "V2", "V3", "V4", "V5")))
        self.assertTrue(all(cases[i]["level"] in ("N4", "N6") for i in oracle))
        self.assertGreaterEqual(len([c for c in levels["N8"] if c["kind"] == "message"]), 3)
        for fid in ("fixture-case-1", "fixture-case-2"):
            self.assertTrue(cases[fid]["file"].startswith("fixture/corpus/"))

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


@unittest.skipUnless(HAVE_PLAYWRIGHT, NO_PLAYWRIGHT)
class DriverOnFixture(unittest.TestCase):
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
            self.assertEqual(res["state"], "attached")
            self.assertEqual({e["name"] for e in ev}, {"attach.png", "network.json"})
            res, ev = self.act(A, "poser_question", {"case": cid})
            self.assertIn("QUEL EST LE MONTANT DU LOYER", res["answer"])
            self.assertIn("Quel est le montant du loyer", res["sent"])
            self.assertEqual([s["node"] for s in res["server_steps"]], ["retrieve", "generate"])
            self.assertEqual({e["name"] for e in ev}, {"dom.html", "network.json", "answer.png"})
            res, ev = self.act(A, "verdict_oracle", {"case": cid})
            self.assertEqual((res["verdict"], res["replayed"]), ("OK", False), res)
            self.assertEqual([e["name"] for e in ev], ["journal.jsonl"])
            self.assertLess(time.monotonic() - t0, 5, f"run {i + 1}")
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
        self.assertRegex(recette.version, r"^0\.1\.0\+[0-9a-f]{12}$")
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
        n = len(journal_lines(recette.work))
        self.assertEqual(run("exporter_journal")["lines"], n)
        self.assertEqual(run("exporter_journal")["lines"], 0)
        self.assertEqual(set(seen), set(declared))


if __name__ == "__main__":
    unittest.main()
