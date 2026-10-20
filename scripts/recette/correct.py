#!/usr/bin/env python3
"""Lignes de correction du journal d'une recette (procédure Aubier, PROCEDURE-3.md §6, E8).

Usage :
    python -I scripts/recette/correct.py WORK_DIR --cases M1-J1a,M3-B2 --reason "…" [--driver DIR]
        [--observations FICHIER] [--evidence] [--reread-header] [--write]

WORK_DIR est le dossier de travail du driver maskai-recette : son journal (journal.jsonl), ses
observations (observations.json) et son registre des conversations. Pour chaque cas nommé, le script
rejoue hors ligne, sans navigateur, le jugement de verdict_oracle du driver (`Recette._judge`, le code
même du driver, chargé depuis --driver, par défaut drivers/maskai-recette du dépôt) sur l'observation
gardée du cas, et le compare à la ligne en vigueur du cas : la dernière ligne `exec` ou `correction`
qu'aucune ligne ne remplace (journal.effective).

Avec --reread-header, l'en-tête gardé de la carte de réponse (`guard.header`) est d'abord relu avec les
règles du driver d'aujourd'hui (`parse_guard` : bannière du modèle, panneau de garde), en mémoire
seulement : c'est la correction du 18/10, quand la bannière d'un modèle sans fournisseur n'était pas lue.

Avec --observations FICHIER, les observations sont lues dans ce fichier au lieu de
WORK_DIR/observations.json : à une reprise, le runbook (§12) met observations.json de côté, et les cas
journalisés avant elle se rejouent sur le fichier mis de côté (revue de la v2.3, m8).

Sans --write, il affiche ce qu'il écrirait et ne touche à rien. Avec --write, il ajoute au journal une
ligne par cas dont le verdict ou les ENF changent (avec --evidence, la preuve seule aussi : sans lui, une
preuve rédigée autrement, la note de rejeu d'un passage b par exemple, n'est pas une correction) : `step`
« correction », `supersedes` (l'index, parmi les lignes non vides, de la ligne remplacée), `reason`, et
les champs d'une ligne d'exécution (inputs_sha256 et ui_profile_sha256 des règles qui jugent maintenant,
config, sent_*, error_class…). Un cas inchangé n'a pas de ligne.

Ce qui ne se rejoue pas, et le dit : un cas sans ligne en vigueur (verdict_oracle le journalisera), un
cas jugé par une autre action (charge_concurrente, un abandon), un cas jugé sur une observation partagée
(@load, @list, @model, @web) dont le pilote n'a pas gardé l'instantané (antérieur à la v2.3), et une
observation qui n'est pas celle de la ligne en vigueur (revue de la v2.3, round 2, robustness-5) : depuis le
round 2, le verdict noté sur l'observation porte l'empreinte de sa ligne (`line_sha256`), qui doit être celle
de la ligne en vigueur ; une observation sans empreinte (antérieure) doit redonner le délai de la ligne. Une
observation mise de côté à une reprise et jamais jugée n'est donc jamais rejouée en correction.

Le script n'écrit jamais observations.json. Le driver garde ses observations en mémoire : arrêter
(démonter) le driver avant --write. Code de retour : 0 ; 1 si un cas nommé ne peut pas être rejoué ;
2 sur une erreur d'usage. Python 3.10 ou plus, bibliothèque standard (le driver charge Playwright s'il
est là, sans en avoir besoin ici).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
DEFAULT_DRIVER = os.path.join(REPO, "drivers", "maskai-recette")
COMPARED = ("verdict", "nfr")  # with --evidence, the evidence too (review of v2.3, m8)
OBSERVATIONS_HINT = (" ; à une reprise, les observations d'avant sont dans le fichier mis de côté : "
                     "--observations FICHIER")


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


def line_texts(path: str) -> list[str]:
    """Les lignes non vides du journal, telles qu'écrites (sans leur fin de ligne), dans l'ordre des index de
    journal.load et de in_force."""
    try:
        with open(path, encoding="utf-8") as f:
            return [line.rstrip("\r\n") for line in f if line.strip()]
    except FileNotFoundError:
        return []


def line_sha256(text: str) -> str:
    """L'empreinte d'une ligne de journal, comme le driver la note sur le verdict de l'observation
    (`line_sha256`, revue de la v2.3, round 2, robustness-5)."""
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


# Round 3 (M-8): keys that only an observation of the pilot since its review's round 2 carries; such an
# observation without the stamp of a line was never judged, it is never « older ».
THIS_PILOT_KEYS = ("notices", "untested")

NOT_THE_LINE = ("erreur : l'observation gardée du cas n'est pas celle de la ligne en vigueur ({why}) : rien n'est "
                "rejoué ; à une reprise, rejouer sur le fichier d'observations qui la porte (--observations)")


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


def plan(recette, drv, cases: list, reason: str, reread: bool = False, evidence: bool = False,
         observations: str | None = None) -> list:
    """Pour chaque cas : la ligne remplacée et la ligne de correction (ou pourquoi il n'y en a pas). Le
    verdict et les ENF sont comparés ; la preuve aussi avec `evidence`."""
    events = drv.journal.load(str(recette.journal_path))
    texts = line_texts(str(recette.journal_path))
    compared = COMPARED + (("evidence",) if evidence else ())
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
        # robustness-5: the observation replayed is the one that judged the line in force, never another
        stamp = ((recette.observations.get(cid) or {}).get("verdict") or {}).get("line_sha256")
        if stamp is not None and (idx >= len(texts) or stamp != line_sha256(texts[idx])):
            item["status"] = NOT_THE_LINE.format(why="son verdict porte l'empreinte d'une autre ligne")
            continue
        own = recette.observations.get(cid) or {}
        if stamp is None and any(k in own or k in (own.get("observed") or {}) for k in THIS_PILOT_KEYS):
            item["status"] = NOT_THE_LINE.format(why="observation de ce pilote jamais jugée : sans empreinte de ligne")
            continue
        if reread:
            item["header_reread"] = reread_header(recette, drv, cid)
        try:
            j = recette._judge(case, offline=True)
        except ValueError as e:
            item["status"] = f"erreur : {e}" + ("" if observations else OBSERVATIONS_HINT)
            continue
        line = {"step": "correction", "case": cid, "verdict": j["verdict"],
                "timing_s": round(j["timing_s"], 1) if isinstance(j["timing_s"], (int, float)) else None,
                "evidence": drv.clean(j["evidence"])[:600],
                "pipeline_ms": {k: int(v) for k, v in (j["pipeline_ms"] or {}).items()} or None,
                "nfr": j["nfr"] or None, "supersedes": idx, "reason": reason,
                "inputs_sha256": recette.inputs_sha256, "ui_profile_sha256": recette.ui_profile_sha256,
                **drv.clean(j["extra"])}
        if stamp is None and line["timing_s"] != events[idx].get("timing_s"):  # an observation of before round 2
            item["status"] = NOT_THE_LINE.format(why=f"délai rejoué {line['timing_s']} s, délai de la ligne "
                                                     f"{events[idx].get('timing_s')} s")
            continue
        if all(line.get(k) == events[idx].get(k) for k in compared):
            item["status"] = "inchangé : rien à corriger"
            if not evidence and line.get("evidence") != events[idx].get("evidence"):
                item["status"] += " (la preuve seule diffère : --evidence pour l'écrire)"
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
        hosts: str = "app.maskai.fr", out=None, reread: bool = False, evidence: bool = False,
        observations: str | None = None) -> tuple[list, int]:
    """Le plan des corrections (et, avec `write_lines`, les lignes écrites) ; le code de retour.
    `observations` : le fichier d'observations à rejouer (par défaut WORK_DIR/observations.json), lu,
    jamais écrit."""
    out = out or sys.stdout
    if not os.path.isfile(os.path.join(work, "journal.jsonl")):
        raise SystemExit(f"pas de journal.jsonl sous {work}")
    if observations is not None and not os.path.isfile(observations):
        raise SystemExit(f"pas de fichier d'observations {observations}")
    drv = load_driver(driver_dir)
    with tempfile.TemporaryDirectory(prefix="correct-profile-") as profile:  # never used: no browser offline
        recette = drv.Recette(env={"VK_DRIVER_WORK_DIR": work, "VK_DRIVER_PROFILE_DIR": profile,
                                   "VK_DRIVER_HOSTS": hosts})
        if observations is not None:
            with open(observations, encoding="utf-8") as f:
                recette.observations = json.load(f)
        items = plan(recette, drv, cases, reason, reread, evidence, observations)
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
    p.add_argument("--observations", default=None,
                   help="le fichier d'observations à rejouer (par défaut WORK_DIR/observations.json ; à une "
                        "reprise, celui qui a été mis de côté)")
    p.add_argument("--evidence", action="store_true",
                   help="écrire aussi une correction quand seule la preuve change")
    p.add_argument("--write", action="store_true", help="ajouter les lignes au journal (sinon : à blanc)")
    args = p.parse_args(argv[1:])
    cases = [c.strip() for c in args.cases.split(",") if c.strip()]
    if not cases or not args.reason.strip():
        p.error("--cases et --reason ne peuvent pas être vides")
    _, code = run(args.work, cases, args.reason.strip(), args.driver, args.write, args.hosts,
                  reread=args.reread_header, evidence=args.evidence, observations=args.observations)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
