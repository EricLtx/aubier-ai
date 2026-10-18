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
    sent_json : à défaut de sent, le chemin d'un sent.json du driver maskai-recette (relatif au
             fichier d'observations) ; son texte compte quand il vient du panneau « Détails » de la
             carte de réponse (pilote v2.2), du popover « Chiffré » du compositeur ou du panneau du
             13/10 (`source`) et n'a pas été écarté (`rejected`) ;
    timing_s : optionnel, durée totale.

La réponse et le texte envoyé sont normalisés avant toute recherche (`normalise`) : un lien
« [x](pii://…) » resté brut devient x, une espace insécable ou fine devient une espace.

Verdict par cas : OK, PARTIEL, KO, NON TESTÉ.
    KO       : une valeur attendue manque, une valeur interdite apparaît, ou une donnée
               identifiante de l'inventaire part en clair vers le modèle (KO sécurité :
               `nfr` porte alors « securite ») ;
    PARTIEL  : tout est là mais un contrôle secondaire échoue (débordement de masque, latence
               hors budget) ;
    OK       : tout passe.

Sortie : un tableau lisible (ou du JSON avec --json). Code de retour 1 si un KO, sinon 0.
Aucune dépendance hors bibliothèque standard.
"""
from __future__ import annotations

import json
import os
import re
import sys
import unicodedata

SPAN_LEAK = re.compile(r"<[A-Z_]+_\d+>[^\s<,.;:)\]\"']")  # un masque collé à un caractère de mot
# Jeton dont le '<' a été avalé. Le type peut porter des '_' (<CREDIT_CARD_1>) : le regard arrière
# exclut '_', pour qu'un jeton bien formé ne se lise pas « CARD_1> », et le type se lit en entier.
BROKEN_TOKEN = re.compile(r"(?<![<A-Z_])[A-Z]+(?:_[A-Z]+)*_\d+>")
# Les textes de sent.json qui sont le texte envoyé : le panneau « Détails » de la carte de réponse
# (v2.2), le popover « Chiffré » du compositeur (« composer-popover » en v2.2, « popover » dans les
# sent.json de la recette 2), le panneau du 13/10.
SENT_SOURCES = ("card-details", "composer-popover", "popover", "panel")
# Deux artefacts de rendu relevés le 18/10 (recette 2) : une valeur que MaskAI remet en clair dans la
# réponse reste un lien markdown brut, « FR55 [3078 8009 0002 8479](pii://%3CCREDIT_CARD_1%3E) 1365
# 147 », que le motif « fr55 ?3078 … » ne lit pas ; et la typographie française met une espace
# insécable (U+00A0), insécable fine (U+202F) ou fine (U+2009, U+2007, U+200A) entre un nombre et
# son unité.
PII_LINK = re.compile(r"\[([^\[\]\n]*)\]\(pii://[^)\s]*\)")
ODD_SPACES = re.compile("[\u00a0\u202f\u2007\u2009\u200a]")


def fold(s: str) -> str:
    """Minuscule, sans accents, espaces normalisés : les comparaisons tolèrent la typographie."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[\s  ]+", " ", s).lower()


def normalise(text: str) -> str:
    """Le texte d'une page tel qu'un oracle doit le lire : un lien « [x](pii://…) » devient x, une
    espace insécable ou fine devient une espace. S'applique aux réponses et au texte envoyé, avant
    `find` et `pii_in`, jamais aux motifs ni aux valeurs de l'inventaire."""
    return ODD_SPACES.sub(" ", PII_LINK.sub(r"\1", text))


def find(pattern: str, text: str) -> bool:
    return re.search(fold(pattern), fold(normalise(text))) is not None


def pii_in(value: str, sent: str) -> bool:
    """Une valeur de l'inventaire `pii` est-elle dans le texte envoyé ? Aux accents et à la casse
    près, et aux séparateurs près : les blancs, '.' et '-' de la valeur valent n'importe quelle
    suite de blancs, '.' ou '-', même vide (« FR55 3078 … » trouve l'IBAN compact). Tout le reste se
    lit littéralement (échappé) : « 12.5 » ne trouve pas « 1205 »."""
    parts = [re.escape(p) for p in re.split(r"[\s.\-]+", fold(value)) if p]
    if not parts:
        return False
    return re.search(r"[\s.\-]*".join(parts), fold(normalise(sent))) is not None


def sent_from_json(path: str) -> str | None:
    """Le texte envoyé que porte un sent.json du driver, s'il en porte un ; sinon None."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if data.get("source") in SENT_SOURCES and not data.get("rejected") and data.get("text"):
        return data["text"]
    return None


def evaluate_case(case: dict, obs: dict | None, latency_budget_s: float | None = None) -> dict:
    out = {"id": case["id"], "verdict": "NON TESTÉ", "missing": [], "forbidden": [], "leaks": [],
           "span_issues": [], "timing_s": None, "notes": []}
    if not obs or not obs.get("answer"):
        return out
    answer = obs["answer"]
    sent = obs.get("sent")
    if sent is None and obs.get("sent_json"):
        sent = sent_from_json(obs["sent_json"])
    out["timing_s"] = obs.get("timing_s")

    out["missing"] = [p for p in case.get("must", []) if not find(p, answer)]
    out["forbidden"] = [p for p in case.get("must_not", []) if find(p, answer)]

    if sent is not None:
        for value in case.get("pii", []):
            if value.startswith("("):
                continue  # inventaire descriptif, pas une valeur
            if pii_in(value, sent):
                out["leaks"].append(value)
        out["span_issues"] = [m.group(0) for m in SPAN_LEAK.finditer(sent)]
        out["span_issues"] += [m.group(0) for m in BROKEN_TOKEN.finditer(sent)]
    else:
        out["notes"].append("texte envoyé non observé : fuite de PII non vérifiable")

    if out["leaks"]:  # une donnée de l'inventaire en clair vers le modèle : KO sécurité
        out["nfr"] = ["securite"]
        out["notes"].append("KO sécurité : donnée identifiante en clair dans le texte envoyé")
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
    base = os.path.dirname(os.path.abspath(argv[2]))
    for obs in observations.values():  # sent.json paths are relative to the observations file
        if isinstance(obs, dict) and obs.get("sent_json") and not os.path.isabs(obs["sent_json"]):
            obs["sent_json"] = os.path.join(base, obs["sent_json"])
    results = evaluate(oracles, observations)
    if "--json" in argv:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print(render(results))
    return 1 if any(r["verdict"] == "KO" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
