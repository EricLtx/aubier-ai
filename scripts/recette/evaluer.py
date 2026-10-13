#!/usr/bin/env python3
"""Évalue des réponses observées contre les oracles d'un corpus de recette (procédure Aubier).

Usage :
    python -I scripts/recette/evaluer.py ORACLES.json OBSERVATIONS.json [--json]

ORACLES.json : le fichier d'oracles du corpus (clé "cases", chaque cas ayant "id", "must",
"must_not", "pii"). Les motifs de "must" / "must_not" sont des expressions régulières
(insensibles à la casse) ; "pii" liste des valeurs littérales.

OBSERVATIONS.json : { "<id du cas>": { "answer": "...", "sent": "...", "timing_s": 12.3 } }
    answer : la réponse affichée à l'utilisateur ;
    sent   : le texte réellement envoyé au modèle, s'il est observable (sinon omettre) ;
    timing_s : optionnel, durée totale.

Verdict par cas : OK, PARTIEL, KO, NON TESTÉ.
    KO       : une valeur attendue manque, une valeur interdite apparaît, ou une donnée
               identifiante de l'inventaire part en clair vers le modèle ;
    PARTIEL  : tout est là mais un contrôle secondaire échoue (débordement de masque, latence
               hors budget) ;
    OK       : tout passe.

Sortie : un tableau lisible (ou du JSON avec --json). Code de retour 1 si un KO, sinon 0.
Aucune dépendance hors bibliothèque standard.
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata

SPAN_LEAK = re.compile(r"<[A-Z_]+_\d+>[^\s<,.;:)\]\"']")  # un masque collé à un caractère de mot
BROKEN_TOKEN = re.compile(r"(?<![<A-Z])[A-Z]+_\d+>")  # jeton dont le '<' a été avalé


def fold(s: str) -> str:
    """Minuscule, sans accents, espaces normalisés : les comparaisons tolèrent la typographie."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[\s  ]+", " ", s).lower()


def find(pattern: str, text: str) -> bool:
    return re.search(fold(pattern), fold(text)) is not None


def evaluate_case(case: dict, obs: dict | None, latency_budget_s: float | None = None) -> dict:
    out = {"id": case["id"], "verdict": "NON TESTÉ", "missing": [], "forbidden": [], "leaks": [],
           "span_issues": [], "timing_s": None, "notes": []}
    if not obs or not obs.get("answer"):
        return out
    answer = obs["answer"]
    sent = obs.get("sent")
    out["timing_s"] = obs.get("timing_s")

    out["missing"] = [p for p in case.get("must", []) if not find(p, answer)]
    out["forbidden"] = [p for p in case.get("must_not", []) if find(p, answer)]

    if sent is not None:
        for value in case.get("pii", []):
            if value.startswith("("):
                continue  # inventaire descriptif, pas une valeur
            if fold(value) in fold(sent):
                out["leaks"].append(value)
        out["span_issues"] = [m.group(0) for m in SPAN_LEAK.finditer(sent)]
        out["span_issues"] += [m.group(0) for m in BROKEN_TOKEN.finditer(sent)]
    else:
        out["notes"].append("texte envoyé non observé : fuite de PII non vérifiable")

    if out["missing"] or out["forbidden"] or out["leaks"]:
        out["verdict"] = "KO"
    elif out["span_issues"] or (latency_budget_s and out["timing_s"] and out["timing_s"] > latency_budget_s):
        out["verdict"] = "PARTIEL"
        if latency_budget_s and out["timing_s"] and out["timing_s"] > latency_budget_s:
            out["notes"].append(f"latence {out['timing_s']} s > budget {latency_budget_s} s")
    else:
        out["verdict"] = "OK"
    return out


def evaluate(oracles: dict, observations: dict) -> list[dict]:
    budget = (oracles.get("global_checks") or {}).get("latency_budget_s") or {}
    results = []
    for case in oracles["cases"]:
        size = case.get("size", "")
        b = budget.get("document_under_5_pages") if "volumineux" not in size else None
        results.append(evaluate_case(case, observations.get(case["id"]), b))
    return results


def render(results: list[dict]) -> str:
    lines = [f"{'cas':<10} {'verdict':<10} {'s':>6}  détail", "-" * 72]
    for r in results:
        detail = []
        if r["missing"]:
            detail.append("manque: " + ", ".join(r["missing"]))
        if r["forbidden"]:
            detail.append("interdit: " + ", ".join(r["forbidden"]))
        if r["leaks"]:
            detail.append("PII en clair: " + ", ".join(r["leaks"]))
        if r["span_issues"]:
            detail.append("débordements: " + ", ".join(r["span_issues"][:4]))
        detail += r["notes"]
        t = f"{r['timing_s']:.0f}" if r["timing_s"] else "-"
        lines.append(f"{r['id']:<10} {r['verdict']:<10} {t:>6}  {' | '.join(detail)}")
    counts = {}
    for r in results:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    lines.append("-" * 72)
    lines.append("bilan : " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # consoles Windows en cp1252
    if len(argv) < 3:
        print(__doc__)
        return 2
    with open(argv[1], encoding="utf-8") as f:
        oracles = json.load(f)
    with open(argv[2], encoding="utf-8") as f:
        observations = json.load(f)
    results = evaluate(oracles, observations)
    if "--json" in argv:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print(render(results))
    return 1 if any(r["verdict"] == "KO" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
