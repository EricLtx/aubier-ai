"""Run directories for the debrief tests: the 17/10 run, and a clean one.

`run_2026_10_17(dir)` assembles the governed recette of 2026-10-17 (five tasks, node-1). The
provenance of every value, "debrief" being docs/technical/debriefs/2026-10-17-maskai-driver.md:
- `journal.jsonl` is the REAL published journal, copied byte for byte from
  docs/functional/use-cases/recettage-chat-ia/runs/2026-10-17/journal.jsonl.
- `journal-essai1.jsonl` (tests/fixtures/debrief-2026-10-17/): from the debrief, the five cases of
  essai 1, N1-1 and N1-3 KO « réponse vide : échec silencieux » (§1, §2 D1 [V]) and N1-1's
  `call_llm` 2444 ms (§4); `inputs_sha256` is the published journal's (§2 D12: one value). The
  FIXTURE'S OWN, not the debrief's: the minutes (placed in t2's window 15:22–15:26 of §1), every
  `timing_s`, N1-1's other pipeline nodes, N1-3's whole `pipeline_ms` (`call_llm` 2391 among them),
  the N0 load figures and N1-2's « aucun envoi ». Essai 1's set-aside journal stays on the node,
  unpublished; its values are close to these, not equal.
- `vkd-console.log` (same directory): from the debrief, a console kept from 15:42 only (header);
  in t16 the unknown « Votre avis » modal refused `dialog` twice, `nouvelle_conversation` (§2 D2)
  and `capture_ecran` (§2 D11), then two `precondition` refusals (§3); the abandons of V2-V1 (t16)
  and V2-V3, V2-V4, V2-V5 (t25), at the minutes of their journal lines 15 to 18; N9-2's empty
  answer without `call_llm` (§2 D1, journal line 33); t39's replay of the 34 cases (§1). The
  FIXTURE'S OWN: the seconds, and the wording of every note (the harness's real notes are not
  published).
- `driver.log` (same directory): from the debrief, a browser start at the beginning of each of
  the five tasks (§1) and the known modal `avis` dismissed after the t25 abandons, 5 to 8 s later
  (§2 D2). The FIXTURE'S OWN: the times to the second, the number of dismissals (three, one per
  t25 abandon) and the dismissal's wording, which `DISMISSED` reads as it reads the driver's own
  « known modal avis dismissed by its button ». The maintenance line is NOT a 17/10 event: it is
  the v2 driver's line, seen at recette 2's first try, placed here so the fixture carries every
  defect the stage must detect.
- `tasks/*.json` and `dmesg.json` are built from TASKS below. From the debrief (§1 [V]): the five
  windows and the end of each task (N5 `failed: harness produced no artefact` in t7 and t16, 4
  acts and 0.049 USD each; t39 `done` after 1 h 39 waiting at the first approval), each task's
  harness and total cost, the live pass of every level (duration, acts, cost: N0-N3 in t7, N5-N9 in
  t25, N0's 88 s counting t7's plan), N4 = 809 s / 33 acts / 0.320 USD over t7+t16+t25, t39's
  replay 329 s / 55 acts / 0.674 USD, plans 0.29, judges 0.66, draft 0.22, 291 acts and 4.245 USD
  in all. The FIXTURE'S OWN: the statuses `stopped` of t2 and t25, the split of N4 and of the
  N0-N3 replays of t16 and t25 between tasks, every start and end time inside the windows, and the
  `driver.acted` times, spread evenly over each step.
- `cases.json` keeps level, kind and budget_s of the 34 cases of drivers/maskai-recette/cases.json
  (checked equal).

`clean_run(dir)` is a run of the v2 driver with nothing to report; every value is the fixture's own.
Usable from Rust tests: `python -I drivers/vkforge/tests/debrief_fixtures.py DIR 17-10|clean`.
"""
import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
JOURNAL_17_10 = REPO / "docs/functional/use-cases/recettage-chat-ia/runs/2026-10-17/journal.jsonl"
EXCERPTS = HERE / "fixtures" / "debrief-2026-10-17"
TZ = timezone(timedelta(hours=2))
DAY = "2026-10-17"
RECIPE = ["plan", "N0", "N1", "N2", "N3", "N4", "N5", "N6", "N7", "N8", "N9", "judge", "approve", "draft", "approve", "release"]
NO_ARTEFACT = {"failed": "harness produced no artefact"}

# task -> (created, status, steps that ran: (step, start, end, status, driver.acted rows, cost USD)); the
# recipe's other steps are pending. Steps run in recipe order; a step absent from the list did not run.
TASKS = {
    "task-node-1-2": ("15:22:00", "stopped", [
        ("plan", "15:22:00", "15:22:25", "done", 0, 0.065), ("N0", "15:22:25", "15:23:40", "done", 7, 0.064),
        ("N1", "15:23:40", "15:26:00", "done", 14, 0.130)]),
    "task-node-1-7": ("15:30:00", "failed", [
        ("plan", "15:30:40", "15:31:00", "done", 0, 0.039), ("N0", "15:31:00", "15:32:08", "done", 7, 0.067),
        ("N1", "15:32:08", "15:33:21", "done", 14, 0.120), ("N2", "15:33:21", "15:35:50", "done", 16, 0.151),
        ("N3", "15:35:50", "15:38:24", "done", 18, 0.162), ("N4", "15:38:24", "15:39:40", "done", 7, 0.080),
        ("N5", "15:39:40", "15:40:20", NO_ARTEFACT, 4, 0.049)]),
    "task-node-1-16": ("15:42:00", "failed", [
        ("plan", "15:42:00", "15:42:20", "done", 0, 0.071), ("N0", "15:42:20", "15:43:00", "done", 5, 0.060),
        ("N1", "15:43:00", "15:43:40", "done", 5, 0.062), ("N2", "15:43:40", "15:44:20", "done", 5, 0.066),
        ("N3", "15:44:20", "15:45:00", "done", 5, 0.066), ("N4", "15:45:00", "15:49:10", "done", 9, 0.090),
        ("N5", "15:49:10", "15:49:50", NO_ARTEFACT, 4, 0.049)]),
    "task-node-1-25": ("15:59:00", "stopped", [
        ("plan", "15:59:00", "15:59:20", "done", 0, 0.060), ("N0", "15:59:20", "16:00:05", "done", 6, 0.064),
        ("N1", "16:00:05", "16:00:49", "done", 6, 0.066), ("N2", "16:00:49", "16:01:33", "done", 6, 0.070),
        ("N3", "16:01:33", "16:02:17", "done", 6, 0.070), ("N4", "16:02:17", "16:10:20", "done", 17, 0.150),
        ("N5", "16:10:50", "16:11:55", "done", 10, 0.101), ("N6", "16:11:55", "16:15:12", "done", 27, 0.281),
        ("N7", "16:15:12", "16:15:46", "done", 7, 0.075), ("N8", "16:15:46", "16:20:57", "done", 17, 0.163),
        ("N9", "16:20:57", "16:23:12", "done", 14, 0.146), ("judge", "16:23:12", "16:25:00", "done", 0, 0.349),
        ("approve", "16:25:00", None, "waiting_human", 0, None)]),
    "task-node-1-39": ("16:26:00", "done", [
        ("plan", "16:26:00", "16:26:20", "done", 0, 0.055), ("N0", "16:26:20", "16:27:34", "done", 5, 0.056),
        ("N1", "16:27:34", "16:28:02", "done", 5, 0.061), ("N2", "16:28:02", "16:28:31", "done", 6, 0.071),
        ("N3", "16:28:31", "16:28:53", "done", 6, 0.074), ("N4", "16:28:53", "16:29:21", "done", 7, 0.082),
        ("N5", "16:29:21", "16:29:44", "done", 4, 0.051), ("N6", "16:29:44", "16:30:24", "done", 7, 0.082),
        ("N7", "16:30:24", "16:30:47", "done", 4, 0.051), ("N8", "16:30:47", "16:31:20", "done", 6, 0.079),
        ("N9", "16:31:20", "16:31:49", "done", 5, 0.067), ("judge", "16:31:49", "16:33:30", "done", 0, 0.310),
        ("approve", "16:33:30", "18:12:30", "done", 0, None), ("draft", "18:12:30", "18:14:00", "done", 0, 0.220),
        ("approve", "18:14:00", "18:14:40", "done", 0, None), ("release", "18:14:40", "18:15:00", "done", 0, None)]),
}
# case -> level, kind, budget_s (drivers/maskai-recette/cases.json, the 34 cases of the 17/10 recette)
CASES = """N0-1 N0 load 3|N0-2 N0 load 3|N1-1 N1 message 10|N1-2 N1 message 10|N1-3 N1 message 10
|N2-1a N2 message 10|N2-1b N2 message 10|N2-2a N2 message 10|N2-2b N2 message 10|N3-1 N3 message 10
|N3-2 N3 message 10|N3-3 N3 message 10|N3-4 N3 message 10|V2-L5 N4 attach_question 20|V2-V1 N4 attach_question 60
|V2-V3 N4 attach_question 60|V2-V4 N4 attach_question 60|V2-V5 N4 attach_question 20|N5-1 N5 message 10
|N5-2 N5 message 10|V2-L1 N6 attach_question 20|V2-L2 N6 attach_question 20|V2-L3 N6 attach_question 20
|V2-L4 N6 attach_question 20|V2-V2 N6 attach_question 60|N7-1 N7 list 10|N7-2 N7 list 10|N8-1 N8 message 10
|N8-2 N8 message 10|N8-3 N8 message 10|N8-4 N8 concurrent 60|N9-1 N9 message 20|N9-2 N9 message 20|N9-3 N9 message 10"""


def ms(hms: str, day: str = DAY) -> int:
    return int(datetime.fromisoformat(f"{day}T{hms}").replace(tzinfo=TZ).timestamp() * 1000)


def step_kind(name: str) -> dict:
    if name in ("plan", "judge", "draft"):
        return {"kind": name, "arch_id": "claude-code/claude-sonnet-5"}
    if name == "approve":
        return {"kind": "approve"}
    if name == "release":
        return {"kind": "release", "to_dir": "recettage-chat-ia/runs/2026-10"}
    return {"kind": "harness", "name": "claude-code", "drivers": ["maskai-1"], "max_actions": 60, "timeout_s": 1800,
            "instruction": f"Niveau {name}. Tu es le harnais de la recette MaskAI (procédure Aubier)."}


def write_tasks(run: Path, tasks: dict, day: str = DAY) -> list:
    """tasks/<id>.json in the shape of `vk task show --json`; returns the ledger rows of the run."""
    (run / "tasks").mkdir(parents=True, exist_ok=True)
    rows = []
    for tid, (created, status, ran) in tasks.items():
        steps, usage, cursor = [], [], 0
        for i, name in enumerate(RECIPE):
            hit = ran[cursor] if cursor < len(ran) and ran[cursor][0] == name else None
            if hit is None:
                steps.append({"kind": step_kind(name), "status": "pending", "started_ms": None, "ended_ms": None, "tokens": 0})
                continue
            cursor += 1
            _, start, end, st, acts, cost = hit
            lo, hi = ms(start, day), ms(end, day) if end else None
            steps.append({"kind": step_kind(name), "status": st, "started_ms": lo, "ended_ms": hi, "tokens": 0})
            if cost is not None:
                usage.append({"arch_id": "claude-code/claude-sonnet-5", "task_id": tid, "step_index": i, "tokens_in": 0,
                              "tokens_in_measured": 0, "tokens_out": 0, "cost_list_usd": cost,
                              "duration_ms": (hi or lo) - lo, "ts_ms": hi or lo})
            rows += [{"kind": "driver.acted", "wall_ms": lo + (k + 1) * ((hi or lo) - lo) // (acts + 1)} for k in range(acts)]
            rows.append({"kind": "task.step", "wall_ms": hi or lo})
        rows.append({"kind": "task.created", "wall_ms": ms(created, day)})
        dump = {"id": tid, "register": f"reg-{tid}", "goal": "recette MaskAI", "artefact_type": "note", "steps": steps,
                "status": status, "created_ms": ms(created, day), "decisions": [], "usage": usage, "artefacts": []}
        (run / "tasks" / f"{tid}.json").write_text(json.dumps(dump, ensure_ascii=False, indent=1), encoding="utf-8")
    return rows


def write_dmesg(run: Path, rows: list) -> None:
    """`vk dmesg --json` keeps seq, kind, wall_ms and hashes; the debrief reads kind and wall_ms."""
    rows = sorted(rows, key=lambda r: (r["wall_ms"], r["kind"]))
    (run / "dmesg.json").write_text(json.dumps([{"seq": i + 1, **r, "payload_hash": "sha256:" + "0" * 64}
                                                for i, r in enumerate(rows)]), encoding="utf-8")


def run_2026_10_17(run: Path) -> Path:
    run.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(JOURNAL_17_10, run / "journal.jsonl")
    for name in ("journal-essai1.jsonl", "driver.log", "vkd-console.log"):
        shutil.copyfile(EXCERPTS / name, run / name)
    cases = {c: {"level": lv, "kind": k, "budget_s": int(b)}
             for c, lv, k, b in (x.split() for x in CASES.replace("\n", "").split("|"))}
    (run / "cases.json").write_text(json.dumps({"cases": cases}, indent=1), encoding="utf-8")
    write_dmesg(run, write_tasks(run, TASKS))
    return run


def clean_run(run: Path) -> Path:
    """One task, every step done, v2 lines: the body only, the sent text read, no abandon, no refusal."""
    run.mkdir(parents=True, exist_ok=True)
    ui, inputs = "sha256:" + "a" * 64, "sha256:" + "b" * 64
    lines = [("N0-1", "11:01", 0.1, "réponse : « chargement de https://app.maskai.fr/new : load 98 ms »", None),
             ("N1-1", "11:02", 4.8, "réponse : « La capitale du Portugal est Lisbonne. »", 1840),
             ("N1-2", "11:02", 0.0, "réponse : « aucun envoi »", None),
             ("N1-3", "11:03", 7.9, "réponse : « Relancez par écrit, rappelez l'échéance, proposez un échéancier. »", 2610)]
    with (run / "journal.jsonl").open("w", encoding="utf-8") as f:
        for case, hm, t, ev, llm in lines:
            e = {"ts": f"2026-10-20T{hm}+02:00", "step": "exec", "case": case, "verdict": "OK", "timing_s": t,
                 "evidence": ev, "nfr": ["performance"], "inputs_sha256": inputs, "ui_profile_sha256": ui}
            if llm:
                e["pipeline_ms"] = {"detect_pii": 900 + llm // 10, "anonymize": llm % 7, "call_llm": llm}
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    (run / "driver.log").write_text("2026-10-20 11:00:12,004 INFO maskai-recette browser up: channel=chrome "
                                    "headless=False hosts=['app.maskai.fr']\n", encoding="utf-8")
    (run / "vkd-console.log").write_text(
        "2026-10-20T09:02:40.000001Z  INFO vk_ipc::server: harness log task=task-node-1-50 harness=N1-1 OK\n",
        encoding="utf-8")
    rows = write_tasks(run, {"task-node-1-50": ("11:00:00", "done", [
        ("plan", "11:00:00", "11:00:10", "done", 0, 0.040), ("N0", "11:00:10", "11:01:30", "done", 3, 0.050),
        ("N1", "11:01:30", "11:03:40", "done", 9, 0.090)])}, day="2026-10-20")
    write_dmesg(run, rows)
    return run


if __name__ == "__main__":
    target, which = Path(sys.argv[1]), sys.argv[2]
    (run_2026_10_17 if which == "17-10" else clean_run)(target)
