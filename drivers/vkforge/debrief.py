"""vkforge debrief --run DIR --out DIR [--until TASK] — the post-launch retro-learning loop.

Founder decision 2026-10-17; requirements: docs/technical/debriefs/2026-10-17-maskai-driver.md §4.
Reads what the node holds in clear after a governed run of a driver and writes debrief.json (the
signals), proposals.json (what to change, each with its evidence lines) and debrief.md (sections
1-3 of a debrief). Deterministic, stdlib only, no model, no socket. Nothing is applied here: the
operator applies a proposal; for a learned driver the deliberate run proposes the rule (spec §5.3).

Run directory (fixed names; only journal.jsonl and tasks/ are required):
  journal.jsonl          the run's journal (the driver's work directory)
  journal-*.jsonl        journals set aside during the run (a first try, kept)
  driver.log             the driver's stderr, as the host appends it
  vkd-console.log        the daemon's console: harness notes `harness log task=… harness=…`
  tasks/<task_id>.json   `vk task show <task_id> --json`, one per task of the run
  dmesg.json             `vk dmesg -n N --json` covering the run (`kind`, `wall_ms` are read)
  cases.json             the driver's cases (`level`, `kind`, `budget_s` per case are read)
  manifest.json          the driver's manifest (`kind: com` marks a learned driver)

Every file is UTF-8 (a BOM tolerated) and checked for the fields read; anything else is one
refusal line naming the file and the field (L-P15). The append-only files are bounded to the run:
from the first task's creation to the last task's end plus a minute, harness notes of the run's
tasks only. The evidence of proposals.json never carries an answer, a sent text or a document's
content (ruling L-P57): a journal line is cut before them, a driver.log line keeps the driver's
own words; a harness note, the operator's own vk_log line, is kept as it is.
"""
import argparse
import contextlib
import json
import math
import re
import statistics
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .errors import Refusal

KINDS = ("selector", "modal", "budget", "oracle", "recipe")
REASONS = ("timeout", "dialog", "precondition", "host", "internal")
EVIDENCE_CAP = 8
CLIP = 240
# What stays human, per kind (debrief 2026-10-17 §4): the debrief proposes, never writes.
HUMAN = {
    "selector": "l'opérateur corrige SELECTORS dans driver.py : nouveau driver_id, remontage signé",
    "modal": "l'opérateur qualifie la modale (bénigne ou sécurité) puis l'ajoute à KNOWN_MODALS : "
             "nouveau driver_id, remontage signé",
    "budget": "l'opérateur change budget_s dans cases.json : nouvelle inputs_sha256, déclarée dans le delta du rapport",
    "oracle": "l'opérateur change l'oracle ou l'évaluation : nouvelle inputs_sha256 ; un verdict publié ne se "
              "corrige que par une ligne supersedes écrite par un humain",
    "recipe": "l'opérateur change la consigne de la recette (recipes/*.json), relue et committée",
}
# A learned driver's code is never edited by hand (spec §3.3): its selector and modal fixes go
# through the deliberate run, which proposes a projection rule a human reviews (spec §5.3).
HUMAN_LEARNED = ("driver appris : son code ne s'édite pas ; vk driver learn --deliberate VERBE propose la règle, "
                 "relue par un humain (spec §5.3)")

ABANDON = re.compile(r"abandon à (\d+(?:[.,]\d+)?) s \(3 × budget (\d+(?:[.,]\d+)?) s\)")
OVER = re.compile(r"latence (\d+(?:[.,]\d+)?) s > budget (\d+(?:[.,]\d+)?) s")
EMPTY = re.compile(r"réponse vide")
ANSWER = re.compile(r"réponse : «(.*)", re.S)
HEADER = re.compile(r"\bmaskai\s+[a-z][a-z0-9_-]*/[a-z0-9][a-z0-9._:-]*|aucune donn[ée]e personnelle d[ée]tect[ée]e"
                    r"|anonymis[ée]\s*→|l'aiguilleur", re.I)
LIVE = re.compile(r"·\s*en direct|affichage encore en mouvement")
UNSENT = "texte envoyé non observé"
UNSEEN_DOC = re.compile(r"je n['\u2019]ai pas acc[èe]s|aucun (?:document|fichier)|ne (?:vois|retrouve) aucun", re.I)
ANSI = re.compile(r"\x1b\[[0-9;]*m")
NOTE = re.compile(r"^(\S+)\s.*?harness log task=(\S+) harness=(.*)$")
REFUSAL = re.compile(r"refus\w*\s*\((%s)\)" % "|".join(REASONS))
MODAL = re.compile(r"modale inconnue\s*:\s*(.+?)(?:\s+[—–]\s+|\s*;|\s*$)")
LOGLINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ ([A-Z]+) (\S+) (.*)$")
MAINT = re.compile(r"service in maintenance|service en maintenance", re.I)
DISMISSED = re.compile(r"known modal (\S+) dismissed")
LEVEL = re.compile(r"^\s*Niveau (N\d+)")
NO_ARTEFACT = "harness produced no artefact"


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _num(s: str) -> float:
    return float(s.replace(",", "."))


def _clip(s: str) -> str:
    s = " ".join(s.split())
    return s if len(s) <= CLIP else s[: CLIP - 1] + "…"


def _status(s) -> str:
    return s if isinstance(s, str) else "; ".join(f"{k}: {v}" for k, v in s.items())


def _stats(values: list) -> dict:
    v = sorted(values)
    return {"n": len(v), "median": statistics.median(v), "p90": v[max(0, math.ceil(0.9 * len(v)) - 1)], "max": v[-1]}


def _answer(e: dict):
    m = ANSWER.search(e.get("evidence") or "")
    return m.group(1) if m else None


# Every input refusal points at the copies the operator made (L-P15: one line, never a traceback).
REMEDY = ("the run directory holds copies: copy that file again as runbook recette-maskai §16 does, "
          "or drop the line from the copy")
# L-P57: evidence never carries an answer, a sent text or a document's content. A journal line is
# cut where its evidence starts quoting the product (`réponse : «`), the sent text (`texte envoyé`)
# or the oracle's must-patterns, which spell the documents' facts (`manque :`).
CUT = re.compile(r"réponse : «|texte envoyé|manque :")
_TYPES = {"text": str, "integer": int, "number": (int, float), "object": dict, "list": list, "boolean": bool}


def _read(root: Path, name: str) -> str:
    """A file of the run directory as text: UTF-8, a BOM tolerated. UTF-16 — what `>` writes in
    Windows PowerShell 5.1 — and any other encoding are refused in one line."""
    data = (root / name).read_bytes()
    if data[:2] in (b"\xff\xfe", b"\xfe\xff") or b"\x00" in data[:64]:
        raise Refusal(f"{name} is UTF-16", "rewrite it with Out-File -Encoding utf8")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise Refusal(f"{name} is not UTF-8 (byte {e.start})", "rewrite it with Out-File -Encoding utf8") from None


def _lines(text: str) -> list:
    """Physical lines, numbered as an editor numbers them: blank ones count, and only a newline
    ends a line (a JSON string may hold U+2028, which `splitlines` would cut)."""
    return [x.rstrip("\r") for x in text.split("\n")]


def _json(at: str, text: str):
    """A JSON document or journal line of the run directory, or the refusal naming where it breaks:
    a journal line cut mid-write, a dump that is not JSON."""
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise Refusal(f"{at} is not JSON ({e})", REMEDY) from None


def _is(value, kinds) -> bool:
    for k in kinds:
        if k == "null":
            if value is None:
                return True
        elif not (k in ("integer", "number") and isinstance(value, bool)) and isinstance(value, _TYPES[k]):
            return True
    return False


def _object(value, at: str) -> dict:
    if not isinstance(value, dict):
        raise Refusal(f"{at} is not a JSON object", REMEDY)
    return value


def _field(doc: dict, key: str, at: str, kinds: tuple, required: bool = True):
    """`doc[key]`, of one of `kinds`, or the refusal naming the file, the place and the field."""
    if key not in doc:
        if required:
            raise Refusal(f"{at}: no field {key}", REMEDY)
        return None
    if not _is(doc[key], kinds):
        raise Refusal(f"{at}: {key} is not {' or '.join(kinds)}", REMEDY)
    return doc[key]


def _when(text: str, at: str, field: str) -> datetime:
    """An ISO 8601 time with its UTC offset: a naive one would be read in this machine's zone."""
    try:
        t = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        raise Refusal(f"{at}: {field} {text!r} is not an ISO 8601 time", REMEDY) from None
    if t.utcoffset() is None:
        raise Refusal(f"{at}: {field} {text} has no UTC offset",
                      "journal.py writes the offset (…T15:31+02:00): add it to the line in the copy")
    return t


def _task_shape(t, at: str) -> dict:
    """A `vk task show --json` dump, the fields the debrief reads."""
    _object(t, at)
    _field(t, "id", at, ("text",))
    _field(t, "created_ms", at, ("integer",))
    _field(t, "status", at, ("text", "object"))
    for i, s in enumerate(_field(t, "steps", at, ("list",))):
        sat = f"{at} steps[{i}]"
        kind = _field(_object(s, sat), "kind", sat, ("object",))
        _field(kind, "kind", f"{sat}.kind", ("text",), required=False)
        _field(kind, "instruction", f"{sat}.kind", ("text", "null"), required=False)
        for d in _field(kind, "drivers", f"{sat}.kind", ("list",), required=False) or []:
            if not isinstance(d, str):
                raise Refusal(f"{sat}.kind: drivers holds a non-text entry", REMEDY)
        _field(s, "status", sat, ("text", "object"))
        _field(s, "started_ms", sat, ("integer", "null"), required=False)
        _field(s, "ended_ms", sat, ("integer", "null"), required=False)
    for i, u in enumerate(_field(t, "usage", at, ("list", "null"), required=False) or []):
        uat = f"{at} usage[{i}]"
        _object(u, uat)
        _field(u, "step_index", uat, ("integer", "null"), required=False)
        _field(u, "cost_list_usd", uat, ("number", "null"), required=False)
    return t


def _line_shape(e, at: str) -> dict:
    """A journal line (scripts/recette/journal.py), the fields the debrief reads."""
    _object(e, at)
    _field(e, "ts", at, ("text",))
    _field(e, "case", at, ("text",))
    if _field(e, "step", at, ("text", "null"), required=False) in ("exec", "correction"):
        _field(e, "verdict", at, ("text",))
    _field(e, "timing_s", at, ("number", "null"), required=False)
    _field(e, "evidence", at, ("text", "null"), required=False)
    _field(e, "supersedes", at, ("integer", "null"), required=False)
    for key in ("inputs_sha256", "ui_profile_sha256"):
        _field(e, key, at, ("text", "null"), required=False)
    for node, ms in (_field(e, "pipeline_ms", at, ("object", "null"), required=False) or {}).items():
        if not _is(ms, ("number",)):
            raise Refusal(f"{at}: pipeline_ms.{node} is not a number", REMEDY)
    for key in ("model", "web"):
        _field(_field(e, "config", at, ("object", "null"), required=False) or {}, key, f"{at} config",
               ("text", "number", "boolean", "null"), required=False)
    return e


def _shown_line(e: dict) -> str:
    """A journal line as evidence (L-P57): its fields, the evidence cut before any quoted text."""
    ev = e.get("evidence") or ""
    m = CUT.search(ev)
    if m:
        e = dict(e, evidence=(ev[: m.start()].rstrip(" ;:") + " […]").lstrip())
    return json.dumps(e, ensure_ascii=False)


def _shown_log(message: str) -> str:
    """A driver.log message as evidence (L-P57): the driver's own words before the first colon —
    what follows may be the page's text (a maintenance banner, a URL) and is dropped. A known
    modal's line, the driver's own name and selector, is kept whole."""
    if DISMISSED.search(message):
        return message
    head, colon, _ = message.partition(": ")
    return head + (" […]" if colon else "")


@contextlib.contextmanager
def _reading(where: str):
    """The last resort (L-P15) around reading one file: what the shape checks did not foresee is
    still one line naming the file, never a traceback."""
    try:
        yield
    except Refusal:
        raise
    except Exception as e:  # noqa: BLE001 — the point is that nothing escapes as a traceback
        raise _unexpected(where, e) from None


def _unexpected(where: str, e: Exception) -> Refusal:
    return Refusal(f"{where}: {type(e).__name__}: {' '.join(str(e).split())}", "check the run directory's files")


class Run:
    """The run directory, loaded and checked: every line keeps its place (`at`, the physical line),
    its time and its task. The append-only files — the daemon's console, driver.log, the driver's
    journal, `vk dmesg` — are bounded to the run: from the first task's creation to the last task's
    end plus a minute — while a task is still running (`open`), to the debrief's call, at most 24 h
    after the last step started — and a harness note must name a task of the run."""

    def __init__(self, root: Path, until=None, now_ms=None):
        if not (root / "journal.jsonl").is_file():
            raise Refusal(f"no journal.jsonl under {root}",
                          "copy the driver's journal (its work directory) into the run directory")
        dumps = sorted((root / "tasks").glob("*.json")) if (root / "tasks").is_dir() else []
        if not dumps:
            raise Refusal(f"no task dump under {root / 'tasks'}",
                          "vk task show TASK --json | Out-File -Encoding utf8 tasks/TASK.json for each task of the run")
        self.root, self.text, self.shown = root, {}, {}
        tasks = []
        for p in dumps:
            at = f"tasks/{p.name}"
            with _reading(at):
                t = _task_shape(_json(at, _read(root, at)), at)
                ends = [x for s in t["steps"] for x in (s.get("started_ms"), s.get("ended_ms")) if x]
                # dumped mid-step: a step `running`, started and not ended, so the task has no end
                # yet (a step `waiting_human` is a pause at a human gate: the task does nothing)
                running = any(s["status"] == "running" and s.get("started_ms") and not s.get("ended_ms")
                              for s in t["steps"])
                tasks.append(dict(t, _file=at, _end=max(ends + [t["created_ms"]]), _running=running))
        self.tasks = sorted(tasks, key=lambda t: t["created_ms"])
        self.ids = {t["id"] for t in self.tasks}
        self.start = self.tasks[0]["created_ms"]
        # The window's end, and what set it (`to_by`). A task still running has no end yet: the
        # window then ends at the debrief's call, and never more than 24 h after the run's last step
        # started — the console and driver.log are appended across mounts, a later line is another
        # run's. `--until` a task that ended closes it earlier.
        self.open = any(t["_running"] for t in self.tasks)
        if not self.open:
            self.stop, self.stop_by = max(t["_end"] for t in self.tasks) + 60_000, "the last end + 60 s"
        else:
            cap = max([s["started_ms"] for t in self.tasks for s in t["steps"] if s.get("started_ms")]
                      + [self.start]) + 86_400_000
            now = int(time.time() * 1000) if now_ms is None else now_ms
            self.stop, self.stop_by = (now, "now") if now <= cap else (cap, "the last start + 24 h")
        self.limit = None
        if until is not None:
            hit = [t for t in self.tasks if t["id"] == until]
            if not hit:
                raise Refusal(f"until: no task {until} in the run", "name a task of the run's tasks/ directory")
            if not hit[0]["_running"]:  # a running task has no end: its window is the open run's
                self.limit = hit[0]["_end"]
                self.tasks = [t for t in self.tasks if t["created_ms"] <= self.limit]
                if self.limit < self.stop:
                    self.stop, self.stop_by = self.limit, "--until"
        self.cases = {}
        if (root / "cases.json").is_file():
            with _reading("cases.json"):
                doc = _object(_json("cases.json", _read(root, "cases.json")), "cases.json")
                cases = _field(doc, "cases", "cases.json", ("object",))
                for cid, c in cases.items():
                    cat = f"cases.json {cid}"
                    _object(c, cat)
                    _field(c, "level", cat, ("text",), required=False)
                    _field(c, "kind", cat, ("text",), required=False)
                    _field(c, "budget_s", cat, ("number", "null"), required=False)
                self.cases = cases
        self.learned = False
        if (root / "manifest.json").is_file():
            with _reading("manifest.json"):
                manifest = _object(_json("manifest.json", _read(root, "manifest.json")), "manifest.json")
                self.learned = manifest.get("kind") == "com"
        self.journal = self._journal("journal.jsonl", effective=True)
        self.set_aside = [e for p in sorted(root.glob("journal-*.jsonl")) for e in self._journal(p.name, effective=False)]
        first = (self.journal or self.set_aside or [None])[0]
        # driver.log carries the node's local time without an offset: the journal's offset names it
        self.tz = timezone(first["_t0"].utcoffset() if first else timedelta(0))
        self.notes = self._notes("vkd-console.log")
        self.log = self._driver_log("driver.log")
        self.ledger = None
        if (root / "dmesg.json").is_file():
            with _reading("dmesg.json"):
                rows = _json("dmesg.json", _read(root, "dmesg.json"))
                if not isinstance(rows, list):
                    raise Refusal("dmesg.json is not a JSON list", REMEDY)
                for i, r in enumerate(rows):
                    _field(_object(r, f"dmesg.json[{i}]"), "kind", f"dmesg.json[{i}]", ("text",))
                    _field(r, "wall_ms", f"dmesg.json[{i}]", ("integer",))
                self.ledger = [r for r in rows if self._in_run(r["wall_ms"])]
        if self.open:  # a running task ends, for its table row and its acts, on the newest line read
            seen = ([e["_lo"] for e in self.journal + self.set_aside] + [n["ms"] for n in self.notes or []]
                    + [x["ms"] for x in self.log or []] + [r["wall_ms"] for r in self.ledger or []])
            newest = max(seen + [t["_end"] for t in self.tasks])
            for t in self.tasks:
                if t["_running"]:
                    t["_end"] = newest

    def _in_run(self, lo: int, hi: int = None) -> bool:
        """Inside the run's window, and not after `--until`'s task."""
        hi = lo if hi is None else hi
        return hi >= self.start and lo <= self.stop and (self.limit is None or lo <= self.limit)

    def _task(self, lo: int, hi: int):
        hit = [t["id"] for t in self.tasks if t["created_ms"] <= hi and (t["_running"] or t["_end"] >= lo)]
        return hit[-1] if hit else None

    def _journal(self, name: str, effective: bool) -> list:
        events = []
        with _reading(name):
            for n, raw in enumerate(_lines(_read(self.root, name)), 1):
                if raw.strip():
                    at = f"{name}:{n}"
                    e = _line_shape(_json(at, raw), at)
                    t0 = _when(e["ts"], at, "ts")
                    lo = _ms(t0)
                    # "…T15:31+02:00" is a minute wide
                    events.append((at, e, t0, lo, lo + (59_999 if len(e["ts"]) <= 22 else 999)))
        if effective and not events:
            raise Refusal(f"{name} is empty", "a run that journaled at least one case")
        ours = [self._in_run(lo, hi) for _, _, _, lo, hi in events]
        # journal.py's corrections: `supersedes` indexes the non-blank lines. A correction belongs to
        # the run when the line it replaces does (a human may write it after the run); with --until,
        # one written after the limit has not happened yet.
        dropped, applied = set(), set()
        for j, (_, e, _, lo, _) in enumerate(events):
            s = e.get("supersedes") if effective else None
            if s is not None and 0 <= s < len(events) and ours[s] and (self.limit is None or lo <= self.limit):
                dropped.add(s)
                applied.add(j)
        out = []
        for j, (at, e, t0, lo, hi) in enumerate(events):
            if j in dropped or not (ours[j] or j in applied):
                continue
            self.text[at], self.shown[at] = json.dumps(e, ensure_ascii=False), _shown_line(e)
            out.append(dict(e, _at=at, _t0=t0, _lo=lo, _task=self._task(lo, hi)))
        return out

    def _notes(self, name: str):
        """The harness notes of the run's tasks (the console is appended across runs)."""
        if not (self.root / name).is_file():
            return None
        out = []
        with _reading(name):
            for n, raw in enumerate(_lines(_read(self.root, name)), 1):
                m = NOTE.match(ANSI.sub("", raw))
                if not m or m.group(2) not in self.ids:
                    continue
                at = f"{name}:{n}"
                ms = _ms(_when(m.group(1), at, "time"))
                if self._in_run(ms):
                    self.text[at] = self.shown[at] = m.group(3)  # the operator's own vk_log lines (L-P57)
                    out.append({"at": at, "ms": ms, "task": m.group(2), "text": m.group(3)})
        return out

    def _driver_log(self, name: str):
        """driver.log's lines inside the run's window (the host appends it across mounts)."""
        if not (self.root / name).is_file():
            return None
        out = []
        with _reading(name):
            for n, raw in enumerate(_lines(_read(self.root, name)), 1):
                m = LOGLINE.match(raw)
                if not m:
                    continue
                at = f"{name}:{n}"
                try:
                    local = datetime.fromisoformat(m.group(1))
                except ValueError:
                    raise Refusal(f"{at}: time {m.group(1)} is not a date", REMEDY) from None
                ms = _ms(local.replace(tzinfo=self.tz))
                if self._in_run(ms):
                    self.text[at] = f"{m.group(2)} {m.group(4)}"
                    self.shown[at] = f"{m.group(2)} {_shown_log(m.group(4))}"
                    out.append({"at": at, "ms": ms, "task": self._task(ms, ms), "text": m.group(4)})
        return out

    def evidence(self, items: list) -> list:
        """Evidence lines as proposals.json carries them (L-P57): `shown`, never the raw line."""
        return [{"at": x["at"], "task": x["task"], "text": _clip(self.shown.get(x["at"], self.text[x["at"]]))}
                for x in items]


def signals(run: Run) -> dict:
    def level_of(cid):
        m = re.match(r"(N\d+)-", cid)
        return run.cases.get(cid, {}).get("level") or (m.group(1) if m else "?")

    notes = run.notes or []
    refusal_notes = [(n, m.group(1)) for n in notes for m in [REFUSAL.search(n["text"])] if m]
    lines = run.journal + run.set_aside
    cases = []
    # the run's verdicts, corrections applied: a `correction` line replaces the line it supersedes
    for e in (x for x in run.journal if x.get("step") in ("exec", "correction")):
        ev, cid = e.get("evidence") or "", e["case"]
        ab, over = ABANDON.search(ev), OVER.search(ev)
        budget = _num((ab or over).group(2)) if (ab or over) else run.cases.get(cid, {}).get("budget_s")
        reasons = (["timeout"] if ab else []) + [
            r for n, r in refusal_notes if r != "timeout" and re.search(rf"\b{re.escape(cid)}\b", n["text"])]
        cases.append({"case": cid, "level": level_of(cid), "verdict": e["verdict"], "timing_s": e.get("timing_s"),
                      "budget_s": budget, "aborted": bool(ab), "over_budget": bool(ab or over), "reasons": reasons,
                      "at": e["_at"], "task": e["_task"]})

    def acts_in(lo, hi):
        if run.ledger is None:
            return None
        return sum(1 for r in run.ledger if r["kind"] == "driver.acted" and lo <= r["wall_ms"] <= hi)

    levels, tasks = {}, []
    for t in run.tasks:  # one pass per harness step whose instruction names its level
        usage = t.get("usage") or []

        def cost(i=None):
            return round(sum(u.get("cost_list_usd") or 0 for u in usage if i is None or u.get("step_index") == i), 3)

        harness = 0.0
        for i, s in enumerate(t["steps"]):
            m = LEVEL.match(s["kind"].get("instruction") or "") if s["kind"].get("kind") == "harness" else None
            if not m or not s.get("started_ms"):
                continue
            harness += cost(i)
            lo, hi = s["started_ms"], s.get("ended_ms") or s["started_ms"]
            p = {"task": t["id"], "step": i, "level": m.group(1), "status": _status(s["status"]),
                 "duration_s": round((hi - lo) / 1000), "acts": acts_in(lo, hi), "cost_usd": cost(i),
                 "new_lines": sum(1 for e in lines if e["_task"] == t["id"] and level_of(e["case"]) == m.group(1)),
                 "at": f"{t['_file']}#steps[{i}]", "_w": (lo, hi)}
            run.text[p["at"]] = f"{p['level']} : {p['status']} ({p['acts']} actes)"
            levels.setdefault(m.group(1), []).append(p)
        tasks.append({"id": t["id"], "status": t["status"], "window": [t["created_ms"], t["_end"]],
                      "levels": [lv for lv in sorted(levels) if any(p["task"] == t["id"] for p in levels[lv])],
                      "acts": acts_in(t["created_ms"], t["_end"]), "harness_usd": round(harness, 3), "cost_usd": cost()})
    passes = [p for ps in levels.values() for p in ps]
    replays = [p for p in passes if p["status"] == "done" and p["new_lines"] == 0]
    console_from = notes[0]["ms"] if notes else None
    no_artefact = [{"task": p["task"], "level": p["level"], "step": p["step"], "acts": p["acts"], "at": p["at"],
                    "refusals_noted": None if console_from is None or console_from > p["_w"][0] else sum(
                        1 for n, _ in refusal_notes if n["task"] == p["task"] and p["_w"][0] <= n["ms"] <= p["_w"][1])}
                   for p in passes if NO_ARTEFACT in p["status"]]
    modals = {}
    for i, n in enumerate(notes):  # after_abort: the task's previous other note was an abandon
        m = MODAL.search(n["text"])
        if m:
            before = [x for x in notes[:i] if x["task"] == n["task"] and not MODAL.search(x["text"])]
            e = modals.setdefault(m.group(1), {"text": m.group(1), "count": 0, "after_abort": 0, "lines": []})
            e["count"] += 1
            e["after_abort"] += bool(before and "abandon" in before[-1]["text"])
            e["lines"].append({"at": n["at"], "task": n["task"]})
    dismissed = {}
    for x in run.log or []:
        if m := DISMISSED.search(x["text"]):
            dismissed[m.group(1)] = dismissed.get(m.group(1), 0) + 1
    maintenance = ([{"at": x["at"], "task": x["task"]} for x in run.log or [] if MAINT.search(x["text"])]
                   + [{"at": n["at"], "task": n["task"]} for n in notes if MAINT.search(n["text"])]
                   + [{"at": e["_at"], "task": e["_task"]} for e in lines if MAINT.search(e.get("evidence") or "")])
    answered = [(e, a) for e in run.journal for a in [_answer(e)] if a is not None]
    pipelines = [e["pipeline_ms"] for e in run.journal if e.get("pipeline_ms")]
    nodes = sorted({k for p in pipelines for k in p})
    classes = {}
    for c in cases:
        if isinstance(c["timing_s"], (int, float)):
            classes.setdefault((run.cases.get(c["case"], {}).get("kind", "?"), c["budget_s"] or 0), []).append(c)
    hashes = {"lines_without_ui_profile": sum(1 for e in lines if "ui_profile_sha256" not in e), "changes": []}
    for field in ("inputs_sha256", "ui_profile_sha256"):
        seen, prev = [], None
        for e in sorted(lines, key=lambda e: (e["_lo"], e["_at"])):
            v = e.get(field)
            if v and v not in [x["value"] for x in seen]:
                seen.append({"value": v, "from": e["_at"]})
            if v and prev and v != prev:
                hashes["changes"].append({"field": field, "at": e["_at"], "from": prev, "to": v})
            prev = v or prev
        hashes[field] = seen
    header = [(e, a) for e, a in answered if HEADER.search(a)]
    aborted = sorted(c["case"] for c in cases if c["aborted"])
    concurrent = [{"case": c["case"], "at": c["at"], "pairs": pairs} for c in cases
                  if run.cases.get(c["case"], {}).get("kind") == "concurrent"
                  for pairs in [[x for x in aborted if re.search(rf"\b{re.escape(x)}\b", run.text[c["at"]])]] if pairs]
    composer = {}
    for e in run.journal:
        if e.get("config"):
            key = (e["config"].get("model"), e["config"].get("web"))
            composer[key] = composer.get(key, 0) + 1
    return {
        "run": {"tasks": [t["id"] for t in run.tasks], "until_ms": run.limit, "learned_driver": run.learned,
                "inputs": sorted(p.relative_to(run.root).as_posix() for p in run.root.rglob("*") if p.is_file()),
                "from_ms": run.start, "to_ms": run.stop, "to_by": run.stop_by, "open": run.open,
                "console_from_ms": console_from},
        "tasks": tasks,
        "acts_total": None if run.ledger is None else sum(1 for r in run.ledger if r["kind"] == "driver.acted"),
        "relaunches": [{"after": a["id"], "next": b["id"]} for a, b in zip(run.tasks, run.tasks[1:]) if a["status"] != "done"],
        "cases": cases,
        "levels": {k: [{x: v for x, v in p.items() if x != "_w"} for p in ps] for k, ps in sorted(levels.items())},
        "refusals": {r: sum(c["aborted"] for c in cases) if r == "timeout" else sum(1 for _, x in refusal_notes if x == r)
                     for r in REASONS},
        "selector_suspects": [{"case": e["case"], "at": e["_at"], "task": e["_task"], "call_llm_ms": e["pipeline_ms"]["call_llm"]}
                              for e in lines if EMPTY.search(e.get("evidence") or "")
                              and (e.get("pipeline_ms") or {}).get("call_llm", 0) > 0],
        "empty_without_llm": [{"case": e["case"], "at": e["_at"], "task": e["_task"]} for e in lines
                              if EMPTY.search(e.get("evidence") or "") and not (e.get("pipeline_ms") or {}).get("call_llm")],
        "modal_refusals": list(modals.values()),
        "known_modals_dismissed": dismissed,
        "maintenance": maintenance,
        "cut_suspects": [{"case": e["case"], "at": e["_at"]} for e, a in answered if LIVE.search(a)],
        "attachment_unseen": [{"case": e["case"], "at": e["_at"]} for e, a in answered
                              if run.cases.get(e["case"], {}).get("kind") == "attach_question" and UNSEEN_DOC.search(a)],
        "header_in_answer": {"lines": len(header), "of": len(answered),
                             "at": [{"at": e["_at"], "task": e["_task"]} for e, _ in header]},
        "sent_unobserved": {"lines": sum(1 for e, _ in answered if UNSENT in e["evidence"]), "of": len(answered)},
        "replays": {"passes": len(replays), "acts": sum(p["acts"] or 0 for p in replays),
                    "cost_usd": round(sum(p["cost_usd"] for p in replays), 3)},
        "levels_without_artefact": no_artefact,
        "concurrent_on_failing": concurrent,
        "composer": [{"model": m, "web": w, "lines": n} for (m, w), n in sorted(composer.items(), key=str)],
        "hashes": hashes,
        "constant_nodes": {k: vs[0] for k in nodes for vs in [[p[k] for p in pipelines if k in p]]
                           if len(vs) >= 5 and len(set(vs)) == 1},
        "pipeline": {k: _stats([p[k] for p in pipelines if k in p]) for k in nodes},
        "latency_classes": [{"kind": k, "budget_s": b, **_stats([c["timing_s"] for c in cs]),
                             "over_budget": sum(c["over_budget"] for c in cs), "aborted": sum(c["aborted"] for c in cs)}
                            for (k, b), cs in sorted(classes.items())],
    }


def propose(run: Run, s: dict) -> list:
    out, order = [], {t: i for i, t in enumerate(s["run"]["tasks"])}

    def add(kind, target, items, proposal, confidence):
        out.append({"kind": kind, "target": target, "evidence": run.evidence(items[:EVIDENCE_CAP]),
                    "evidence_total": len(items), "proposal": proposal, "confidence": confidence,
                    "tasks": sorted({x["task"] for x in items if x["task"]}, key=order.get), "proposed_by": "debrief",
                    "human": HUMAN_LEARNED if run.learned and kind in ("selector", "modal") else HUMAN[kind]})

    sus = s["selector_suspects"]  # D1: an empty answer although the stream carried call_llm
    if sus:
        add("selector", "assistant_message", sus,
            f"{', '.join(x['case'] for x in sus)} : réponse vide alors que le flux a porté call_llm "
            f"({', '.join(str(x['call_llm_ms']) for x in sus)} ms) : le sélecteur de la réponse ne vise plus la carte ; "
            "la relever sur dom.html ou la capture, corriger le sélecteur, sonder l'interface avant N0",
            "high" if len(sus) >= 2 else "medium")
    for m in s["modal_refusals"]:  # D2: a modal the driver does not know blocks every act
        add("modal", m["text"], m["lines"],
            f"KNOWN_MODALS += « {m['text']} » : sélecteur structurel relevé sur modal.html ou la capture, fermeture "
            "par la croix, jamais par « Envoyer »" + (f" ; {m['after_abort']}/{m['count']} refus suivent un abandon : "
                                                     "cliquer sur arrêt avant de rendre la main" if m["after_abort"] else ""),
            "high" if m["count"] >= 2 else "medium")
    by_level = {}
    for c in s["cases"]:
        by_level.setdefault(c["level"], []).append(c)
    for level, cs in sorted(by_level.items()):  # D8: a level whose cases end by abandon
        ab = [c for c in cs if c["aborted"]]
        if len(ab) >= 2 and 2 * len(ab) >= len(cs):
            profiled = all("question partie" in run.text[c["at"]] and "flux ouvert" in run.text[c["at"]] for c in ab)
            add("budget", level, ab,
                f"{level} : {len(ab)}/{len(cs)} cas abandonnés à 3 × le budget (budgets "
                f"{', '.join(f'{b:g}' for b in sorted({c['budget_s'] for c in ab}))} s) ; "
                + ("la question est partie et le flux a démarré : relever le budget de la classe au temps du produit"
                   if profiled else "sans profil d'abandon la faute produit n'est pas démontrée : instrumenter "
                                    "l'abandon (question partie, flux ouvert, dernier nœud) avant de juger, ou relever le budget"),
                "high" if profiled else "medium")
    h = s["header_in_answer"]  # D5: the judged text carries the header (model banner, guard panel)
    if h["of"] >= 3 and 2 * h["lines"] >= h["of"]:
        add("oracle", "answer_scope", h["at"],
            f"la réponse jugée contient l'en-tête (bannière du modèle, panneau de garde) sur {h['lines']}/{h['of']} "
            "lignes : un must qui nomme un modèle ou un mot de garde peut passer sur l'en-tête seul ; évaluer le "
            "corps seul, rejuger les cas dont un must nomme un modèle", "medium")
    if s["maintenance"]:
        add("recipe", "maintenance", s["maintenance"],
            f"la page de maintenance est apparue {len(s['maintenance'])} fois : la consigne suspend le niveau sur le "
            "refus « service en maintenance », sans juger, et le reprend quand le service revient ; les cas jugés "
            "pendant la fenêtre se rejouent", "high")
    na = s["levels_without_artefact"]  # D11: a harness ended a level without an artefact
    if na:
        add("recipe", "level_without_artefact", na,
            f"{len(na)} niveau(x) fini(s) sans artefact ({', '.join(x['task'] + ' ' + x['level'] for x in na)}) : "
            "la consigne termine un niveau par une note de refus (raison, dernier refus) quand tous ses actes sont "
            "refusés ; côté driver, une capture d'écran hors du garde-modale", "high" if len(na) >= 2 else "medium")
    return sorted(out, key=lambda p: (KINDS.index(p["kind"]), p["target"]))


def _hm(ms: int, tz) -> str:
    return datetime.fromtimestamp(ms / 1000, tz).strftime("%H:%M")


def _usd(x) -> str:
    return "—" if x is None else f"{x:.3f}".replace(".", ",")


def _n(x) -> str:
    return f"{x:g}".replace(".", ",")


def _row(*cells) -> str:
    """A Markdown table row: a `|` inside a cell is escaped, as journal.py's `render` does, and a
    cell stays on one line."""
    return "| " + " | ".join(" ".join(str(c).split()).replace("|", "\\|") for c in cells) + " |"


def render(run: Run, s: dict, proposals: list) -> str:
    """Sections 1-3 of a debrief, in the register of docs/technical/debriefs/; 4 and 5 are human."""
    tz = run.tz
    drivers = sorted({d for t in run.tasks for st in t["steps"] for d in st["kind"].get("drivers", [])})
    day = datetime.fromtimestamp(run.tasks[0]["created_ms"] / 1000, tz).date().isoformat()
    ref = {(p["kind"], p["target"]): f"{p['kind']} `{p['target']}` ({p['confidence']})" for p in proposals}
    out = [f"# Debrief automatique — {', '.join(drivers) or 'driver'} ({day})", "",
           "Produit par `vkforge debrief` sur " + ", ".join(f"`{x}`" for x in s["run"]["inputs"]) + ". **[V]** lu sur "
           "pièce, **[I]** déduit. Heures locales, coûts USD des tables `vk task show`. Les sections 4 et 5 (cahier des "
           "charges, propositions retenues) restent humaines : `proposals.json` en est l'entrée.", "",
           "## 1. Chronologie du run", "",
           "| Tâche | Fenêtre | Niveaux | Statut | Actes | Harnais | Total |", "|---|---|---|---|---|---|---|"]
    for t in s["tasks"]:
        out.append(_row(t["id"], f"{_hm(t['window'][0], tz)}–{_hm(t['window'][1], tz)}", ", ".join(t["levels"]) or "—",
                        _status(t["status"]), "—" if t["acts"] is None else t["acts"], _usd(t["harness_usd"]),
                        _usd(t["cost_usd"])))
    out += ["", "| Niveau | Tâche | Statut | Durée | Actes | Coût | Lignes |", "|---|---|---|---|---|---|---|"]
    out += [_row(lv, p["task"], p["status"], f"{p['duration_s']} s", "—" if p["acts"] is None else p["acts"],
                 _usd(p["cost_usd"]), p["new_lines"]) for lv, ps in s["levels"].items() for p in ps]
    r = s["replays"]
    out += ["", f"Rejeux sans ligne nouvelle : {r['passes']} passes, {r['acts']} actes, {_usd(r['cost_usd'])} USD [V]. "
                f"Relances après un arrêt : {len(s['relaunches'])} [V].", "",
            "## 2. Défauts révélés par le terrain", "",
            "| Signal | Constat | Preuve | Proposition |", "|---|---|---|---|"]

    def row(signal, seen, items, key=None):
        at = ", ".join(x["at"] for x in items[:4]) + (" …" if len(items) > 4 else "")
        out.append(_row(signal, seen, at or "—", ref.get(key, "—")))

    sus = s["selector_suspects"]
    row("réponse vide, call_llm présent", (", ".join(x["case"] for x in sus) + " [V]") if sus else "aucune", sus,
        ("selector", "assistant_message"))
    for m in s["modal_refusals"]:
        row("modale inconnue", f"« {m['text']} » × {m['count']}, {m['after_abort']} après un abandon [V]", m["lines"],
            ("modal", m["text"]))
    for p in (p for p in proposals if p["kind"] == "budget"):
        row("abandons", f"{p['target']} : {p['evidence_total']} abandons [V]", p["evidence"], ("budget", p["target"]))
    h = s["header_in_answer"]
    row("en-tête dans la réponse jugée", f"{h['lines']}/{h['of']} [V]", h["at"], ("oracle", "answer_scope"))
    row("page de maintenance", f"{len(s['maintenance'])} [V]", s["maintenance"], ("recipe", "maintenance"))
    row("niveau fini sans artefact", f"{len(s['levels_without_artefact'])} [V]", s["levels_without_artefact"],
        ("recipe", "level_without_artefact"))
    row("réponse vide sans call_llm (produit)", ", ".join(x["case"] for x in s["empty_without_llm"]) or "aucune",
        s["empty_without_llm"])
    row("texte envoyé non observé", f"{s['sent_unobserved']['lines']}/{s['sent_unobserved']['of']} [V]", [])
    row("réponse coupée (marque de génération)", f"{len(s['cut_suspects'])} [I]", s["cut_suspects"])
    row("pièce jointe non vue du modèle", f"{len(s['attachment_unseen'])} [I]", s["attachment_unseen"])
    row("charge concurrente sur des cas déjà KO seuls", ", ".join(f"{x['case']} ({' + '.join(x['pairs'])})"
                                                                 for x in s["concurrent_on_failing"]) or "aucune",
        s["concurrent_on_failing"])
    row("configuration du compositeur", "; ".join(f"{c['model']} / web {c['web']} × {c['lines']}" for c in s["composer"])
        or "non relevée sur les lignes", [])
    hs = s["hashes"]
    row("empreintes", f"inputs_sha256 : {len(hs['inputs_sha256'])} valeur(s), {len(hs['changes'])} changement(s) ; "
                      f"ui_profile_sha256 absente sur {hs['lines_without_ui_profile']} lignes [V]", [])
    row("nœuds constants", (", ".join(f"{k} {v}" for k, v in s["constant_nodes"].items()) + " [V]")
        if s["constant_nodes"] else "aucun", [])
    out += ["", "## 3. Mesures utiles au prochain pilote", "",
            "| Nœud (ms) | n | Médiane | p90 | Max |", "|---|---|---|---|---|"]
    out += [_row(f"`{k}`", v["n"], _n(v["median"]), _n(v["p90"]), _n(v["max"])) for k, v in s["pipeline"].items()]
    out += ["", "| Classe | Budget | n | Médiane | p90 | Max | Hors budget | Abandons |", "|---|---|---|---|---|---|---|---|"]
    out += [_row(c["kind"], f"{_n(c['budget_s'])} s", c["n"], f"{_n(c['median'])} s", f"{_n(c['p90'])} s",
                 f"{_n(c['max'])} s", f"{c['over_budget']}/{c['n']}", c["aborted"]) for c in s["latency_classes"]]
    out += ["", "Refus par raison (timeout lu au journal, les autres aux notes du harnais ; le ledger les hache) : "
            + ", ".join(f"`{k}` {v}" for k, v in s["refusals"].items()) + " [V].", ""]
    return "\n".join(out)


def debrief(root: Path, until=None, now_ms=None) -> tuple:
    """`now_ms`: the call's time, which ends the window of a run whose task is still running
    (default: now). A run whose tasks all ended does not read it: its bytes are the same at any time."""
    run = Run(root, until, now_ms)
    s = signals(run)
    proposals = propose(run, s)
    return s, proposals, render(run, s, proposals)


def write(s: dict, proposals: list, md: str, out: Path) -> None:
    if out.exists() and not out.is_dir():
        raise Refusal(f"out {out} is not a directory", "a new directory: a debrief never overwrites one")
    if out.exists() and any(out.iterdir()):
        raise Refusal(f"out {out} is not empty", "a new directory: a debrief never overwrites one")
    out.mkdir(parents=True, exist_ok=True)
    for name, value in (("debrief.json", s), ("proposals.json", proposals)):
        (out / name).write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=1) + "\n",
                                encoding="utf-8", newline="\n")
    (out / "debrief.md").write_text(md, encoding="utf-8", newline="\n")


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--run", required=True, help="the run directory (layout in the module docstring)")
    p.add_argument("--out", required=True, help="a new or empty directory")
    p.add_argument("--until", help="debrief only the evidence up to the end of this task")


def run(ns: argparse.Namespace) -> int:
    now_ms = int(time.time() * 1000)  # the stage's start: an open run's window ends here
    root, out = Path(ns.run), Path(ns.out)
    if out.resolve().is_relative_to(root.resolve()):
        raise Refusal(f"out {out} is inside the run directory",
                      "a new directory outside it: the run directory is read, never written")
    try:
        s, proposals, md = debrief(root, ns.until, now_ms)
    except Refusal:
        raise
    except Exception as e:  # noqa: BLE001 — the last resort (L-P15): one line, never a traceback
        raise _unexpected("the run directory", e) from None
    try:
        write(s, proposals, md, out)
    except OSError as e:
        raise Refusal(f"out {out} cannot be written ({e.strerror})", "a directory this user can write") from None
    print(f"debrief: {len(proposals)} proposals -> {ns.out}")
    return 0
