#!/usr/bin/env python3
"""Lignes de correction du journal d'une recette (procédure Aubier, PROCEDURE-3.md §6, E8).

Usage :
    python -I scripts/recette/correct.py WORK_DIR --cases M1-J1a,M3-B2 --reason "…" [--driver DIR] [--write]

WORK_DIR est le dossier de travail du driver maskai-recette : son journal (journal.jsonl), ses
observations (observations.json) et son registre des conversations. Pour chaque cas nommé, le script
rejoue hors ligne, sans navigateur, le jugement de verdict_oracle du driver (`Recette._judge`, le code
même du driver, chargé depuis --driver, par défaut drivers/maskai-recette du dépôt) sur l'observation
gardée du cas, et le compare à la ligne en vigueur du cas : la dernière ligne `exec` ou `correction`
qu'aucune ligne ne remplace (journal.effective).

Avec --reread-header, l'en-tête gardé de la carte de réponse (`guard.header`) est d'abord relu avec les
règles du driver d'aujourd'hui (`parse_guard` : bannière du modèle, panneau de garde), en mémoire
seulement : c'est la correction du 18/10, quand la bannière d'un modèle sans fournisseur n'était pas lue.

Sans --write, il affiche ce qu'il écrirait et ne touche à rien. Avec --write, il ajoute au journal une
ligne par cas dont le verdict, la preuve ou les ENF changent : `step` « correction », `supersedes`
(l'index, parmi les lignes non vides, de la ligne remplacée), `reason`, et les champs d'une ligne
d'exécution (inputs_sha256 et ui_profile_sha256 des règles qui jugent maintenant, config, sent_*,
error_class…). Un cas inchangé n'a pas de ligne.

Ce qui ne se rejoue pas, et le dit : un cas sans ligne en vigueur (verdict_oracle le journalisera), un
cas jugé par une autre action (charge_concurrente, un abandon), un cas jugé sur une observation partagée
(@load, @list, @model, @web) dont le pilote n'a pas gardé l'instantané (antérieur à la v2.3).

Le script n'écrit jamais observations.json. Le driver garde ses observations en mémoire : arrêter
(démonter) le driver avant --write. Code de retour : 0 ; 1 si un cas nommé ne peut pas être rejoué ;
2 sur une erreur d'usage. Python 3.10 ou plus, bibliothèque standard (le driver charge Playwright s'il
est là, sans en avoir besoin ici).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
DEFAULT_DRIVER = os.path.join(REPO, "drivers", "maskai-recette")
COMPARED = ("verdict", "evidence", "nfr")


def load_driver(driver_dir: str):
    """Le module driver.py du dossier `driver_dir`, chargé sans écrire de bytecode à côté."""
    path = os.path.join(driver_dir, "driver.py")
    if not os.path.isfile(path):
        raise SystemExit(f"pas de driver.py sous {driver_dir}")
    spec = importlib.util.spec_from_file_location("maskai_recette_driver_correct", path)
    module = importlib.util.module_from_spec(spec)
    was, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = was
    return module


def in_force(events: list, cid: str) -> int | None:
    """L'index (parmi les lignes non vides) de la ligne en vigueur du cas : la dernière ligne exec ou
    correction du cas qu'aucune ligne `supersedes` ne remplace ; None s'il n'y en a pas."""
    replaced = {e["supersedes"] for e in events if isinstance(e.get("supersedes"), int)}
    found = [i for i, e in enumerate(events)
             if i not in replaced and e.get("case") == cid and e.get("step") in ("exec", "correction")]
    return found[-1] if found else None


def reread_header(recette, drv, cid: str) -> bool:
    """L'en-tête gardé de la carte du cas relu avec les règles du driver d'aujourd'hui (parse_guard), dans
    l'observation en mémoire (celle du cas, ou l'instantané d'une observation partagée) ; rend True s'il y
    en avait un. observations.json n'est jamais écrit."""
    own = recette.observations.get(cid) or {}
    obs = own.get("observed") if own.get("from") else own
    guard = (obs or {}).get("guard") or {}
    if not guard.get("header"):
        return False
    guard.update(drv.parse_guard(guard["header"], "", False))
    return True


def plan(recette, drv, cases: list, reason: str, reread: bool = False) -> list:
    """Pour chaque cas : la ligne remplacée et la ligne de correction (ou pourquoi il n'y en a pas)."""
    events = drv.journal.load(str(recette.journal_path))
    out = []
    for cid in cases:
        item = {"case": cid, "supersedes": None, "old": None, "line": None, "status": None}
        out.append(item)
        case = recette.cases.get(cid)
        if case is None:
            item["status"] = "erreur : cas inconnu de cases.json"
            continue
        idx = in_force(events, cid)
        if idx is None:
            item["status"] = "erreur : aucune ligne en vigueur, rien à corriger (verdict_oracle le journalisera)"
            continue
        item["supersedes"], item["old"] = idx, events[idx]
        if case["kind"] == "concurrent":
            item["status"] = "erreur : jugé par charge_concurrente, pas par verdict_oracle"
            continue
        if reread:
            item["header_reread"] = reread_header(recette, drv, cid)
        try:
            j = recette._judge(case, offline=True)
        except ValueError as e:
            item["status"] = f"erreur : {e}"
            continue
        line = {"step": "correction", "case": cid, "verdict": j["verdict"],
                "timing_s": round(j["timing_s"], 1) if isinstance(j["timing_s"], (int, float)) else None,
                "evidence": drv.clean(j["evidence"])[:600],
                "pipeline_ms": {k: int(v) for k, v in (j["pipeline_ms"] or {}).items()} or None,
                "nfr": j["nfr"] or None, "supersedes": idx, "reason": reason,
                "inputs_sha256": recette.inputs_sha256, "ui_profile_sha256": recette.ui_profile_sha256,
                **drv.clean(j["extra"])}
        if all(line.get(k) == events[idx].get(k) for k in COMPARED):
            item["status"] = "inchangé : rien à corriger"
            continue
        item["line"], item["status"] = line, "à corriger"
        item["judged"] = j
    return out


def write(recette, item: dict) -> dict:
    """La ligne de correction ajoutée au journal (journal.py, comme une ligne d'exécution)."""
    j, line = item["judged"], item["line"]
    extra = {"reason": line["reason"], **j["extra"]}
    text = recette._journal_add(item["case"], j["verdict"], j["timing_s"], j["evidence"], j["nfr"],
                                j["pipeline_ms"], extra, step="correction", supersedes=line["supersedes"])
    return json.loads(text)


def run(work: str, cases: list, reason: str, driver_dir: str = DEFAULT_DRIVER, write_lines: bool = False,
        hosts: str = "app.maskai.fr", out=None, reread: bool = False) -> tuple[list, int]:
    """Le plan des corrections (et, avec `write_lines`, les lignes écrites) ; le code de retour."""
    out = out or sys.stdout
    if not os.path.isfile(os.path.join(work, "journal.jsonl")):
        raise SystemExit(f"pas de journal.jsonl sous {work}")
    drv = load_driver(driver_dir)
    with tempfile.TemporaryDirectory(prefix="correct-profile-") as profile:  # never used: no browser offline
        recette = drv.Recette(env={"VK_DRIVER_WORK_DIR": work, "VK_DRIVER_PROFILE_DIR": profile,
                                   "VK_DRIVER_HOSTS": hosts})
        items = plan(recette, drv, cases, reason, reread)
        for item in items:
            old = item["old"] or {}
            head = f"{item['case']} : {item['status']}"
            if item["supersedes"] is not None:
                head += f" (ligne en vigueur {item['supersedes']}, {old.get('verdict')})"
            print(head, file=out)
            if item["line"]:
                print(f"  {old.get('verdict')} → {item['line']['verdict']} ; {item['line']['evidence']}", file=out)
                if write_lines:
                    item["written"] = write(recette, item)
                    print("  écrite : " + json.dumps(item["written"], ensure_ascii=False), file=out)
                else:
                    print("  à écrire (--write) : " + json.dumps(item["line"], ensure_ascii=False), file=out)
    return items, (1 if any(i["status"].startswith("erreur") for i in items) else 0)


def main(argv: list) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # consoles Windows en cp1252
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("work", help="le dossier de travail du driver (journal.jsonl, observations.json)")
    p.add_argument("--cases", required=True, help="les cas à rejuger, séparés par des virgules")
    p.add_argument("--reason", required=True, help="pourquoi : la raison portée par chaque ligne de correction")
    p.add_argument("--driver", default=DEFAULT_DRIVER, help="le dossier du driver (par défaut : celui du dépôt)")
    p.add_argument("--hosts", default="app.maskai.fr", help="la liste d'hôtes du driver (sans effet hors ligne)")
    p.add_argument("--reread-header", action="store_true",
                   help="relire l'en-tête gardé de chaque carte avec les règles du driver d'aujourd'hui")
    p.add_argument("--write", action="store_true", help="ajouter les lignes au journal (sinon : à blanc)")
    args = p.parse_args(argv[1:])
    cases = [c.strip() for c in args.cases.split(",") if c.strip()]
    if not cases or not args.reason.strip():
        p.error("--cases et --reason ne peuvent pas être vides")
    _, code = run(args.work, cases, args.reason.strip(), args.driver, args.write, args.hosts,
                  reread=args.reread_header)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
