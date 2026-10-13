# Cas d'usage Aubier — recette fonctionnelle et benchmark d'un chat IA

Un agent exécute, journalise et rejoue la recette d'un assistant IA professionnel. Première
cible : MaskAI (`app.maskai.fr`), 12–13 octobre 2026.

| Fichier | Rôle |
|---|---|
| `PROCEDURE.md` | la procédure Aubier (7 étapes, grille N0–N9, six exigences non fonctionnelles, barème, leçons, annexes techniques) |
| `2026-10-13-maskai-benchmark.md` | le rapport consolidé des deux exécutions (version publique, infrastructure expurgée) |
| `journal-v2.jsonl` | le journal append-only de la seconde exécution (une ligne JSON par test) |
| `corpus/` | corpus v1 : 10 documents fictifs, 9 formats, `MANIFESTE.md`, `oracles.json`, générateur |
| `corpus-v2/` | corpus v2 : 5 documents légers et 5 volumineux, `README.md`, `oracles-v2.json`, générateurs |
| `../../../scripts/recette/` | `evaluer.py` (oracles → verdicts), `journal.py` (journal, rendu, agrégats), tests |
| `../../../.claude/skills/recettage-chat-ia/` | la compétence que l'agent charge pour rejouer la recette |

Rejouer sur une nouvelle version : ouvrir la session dans Chrome, charger la compétence,
suivre `PROCEDURE.md`, journaliser chaque test, évaluer contre les oracles, n'écrire que le
delta du rapport.
