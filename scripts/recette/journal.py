#!/usr/bin/env python3
"""Journal d'une exécution de recette (procédure Aubier) : un événement JSON par ligne.

Usage :
    python -I scripts/recette/journal.py JOURNAL.jsonl add --step exec --case "V2-L1" \
        --verdict PARTIEL --timing 57.9 --evidence "réponse conforme ; IBAN déborde" \
        [--pipeline detect_pii=30108 call_llm=9328] [--nfr performance,securite]
    python -I scripts/recette/journal.py JOURNAL.jsonl render      # tableau Markdown
    python -I scripts/recette/journal.py JOURNAL.jsonl stats       # agrégats par étape et par ENF

Chaque ligne : {"ts", "step", "case", "verdict", "timing_s", "pipeline_ms", "nfr", "evidence"}.
Le journal est append-only : on n'édite jamais une ligne, on en ajoute une qui la corrige
(step "correction", champ "supersedes" = index de la ligne corrigée). C'est le registre
d'où le rapport se reconstruit ; rien ne dépend de la mémoire de l'agent.
Aucune dépendance hors bibliothèque standard.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

NFR = ["performance", "veracite", "disponibilite", "securite", "conformite", "scalabilite"]
VERDICTS = ["OK", "PARTIEL", "KO", "NON TESTÉ", "en cours"]


def load(path: str) -> list[dict]:
    try:
        with open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
    except FileNotFoundError:
        return []


def add(path: str, args: argparse.Namespace) -> dict:
    event = {
        "ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="minutes"),
        "step": args.step, "case": args.case, "verdict": args.verdict,
        "timing_s": args.timing, "evidence": args.evidence,
    }
    if args.pipeline:
        event["pipeline_ms"] = {k: int(v) for k, v in (p.split("=") for p in args.pipeline)}
    if args.nfr:
        bad = [n for n in args.nfr.split(",") if n not in NFR]
        if bad:
            raise SystemExit(f"ENF inconnue(s) : {bad} ; attendu parmi {NFR}")
        event["nfr"] = args.nfr.split(",")
    if args.supersedes is not None:
        event["supersedes"] = args.supersedes
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


def effective(events: list[dict]) -> list[dict]:
    """Applique les corrections : une ligne 'supersedes' remplace la ligne visée."""
    out = list(events)
    for i, e in enumerate(events):
        if e.get("supersedes") is not None and 0 <= e["supersedes"] < len(out):
            out[e["supersedes"]] = None
    return [e for e in out if e is not None]


def render(events: list[dict]) -> str:
    rows = ["| # | étape | cas | verdict | durée (s) | preuve |", "|---|---|---|---|---|---|"]
    for i, e in enumerate(effective(events)):
        t = f"{e['timing_s']:.1f}" if isinstance(e.get("timing_s"), (int, float)) else "—"
        ev = (e.get("evidence") or "").replace("|", "\\|")
        rows.append(f"| {i} | {e.get('step','')} | {e.get('case','')} | {e.get('verdict','')} | {t} | {ev} |")
    return "\n".join(rows)


def stats(events: list[dict]) -> str:
    ev = effective(events)
    by_verdict: dict[str, int] = {}
    by_nfr: dict[str, dict[str, int]] = {}
    pipeline: dict[str, list[int]] = {}
    for e in ev:
        if e.get("step") != "exec":
            continue
        by_verdict[e.get("verdict", "?")] = by_verdict.get(e.get("verdict", "?"), 0) + 1
        for n in e.get("nfr", []):
            by_nfr.setdefault(n, {})
            by_nfr[n][e.get("verdict", "?")] = by_nfr[n].get(e.get("verdict", "?"), 0) + 1
        for k, v in (e.get("pipeline_ms") or {}).items():
            pipeline.setdefault(k, []).append(v)
    lines = ["Verdicts (étape exec) : " + ", ".join(f"{k} {v}" for k, v in sorted(by_verdict.items()))]
    for n in NFR:
        if n in by_nfr:
            lines.append(f"  {n:<14} " + ", ".join(f"{k} {v}" for k, v in sorted(by_nfr[n].items())))
    if pipeline:
        lines.append("Étapes serveur (ms) : médiane / max")
        for k, vs in pipeline.items():
            vs = sorted(vs)
            lines.append(f"  {k:<18} {vs[len(vs)//2]:>8} / {vs[-1]:>8}  (n={len(vs)})")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # consoles Windows en cp1252
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("journal")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("--step", required=True, choices=["reconnaissance", "instrumentation", "corpus", "exec", "jugement", "rapport", "rejeu", "correction"])
    a.add_argument("--case", required=True)
    a.add_argument("--verdict", required=True, choices=VERDICTS)
    a.add_argument("--timing", type=float)
    a.add_argument("--evidence", default="")
    a.add_argument("--pipeline", nargs="*", help="etape=ms …")
    a.add_argument("--nfr", help="liste séparée par des virgules parmi " + ",".join(NFR))
    a.add_argument("--supersedes", type=int)
    sub.add_parser("render")
    sub.add_parser("stats")
    args = p.parse_args(argv[1:])
    if args.cmd == "add":
        print(json.dumps(add(args.journal, args), ensure_ascii=False))
    elif args.cmd == "render":
        print(render(load(args.journal)))
    else:
        print(stats(load(args.journal)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
