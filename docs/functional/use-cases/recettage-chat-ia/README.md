# Cas d'usage Aubier — recette fonctionnelle et benchmark d'un chat IA

Un agent exécute, journalise et rejoue la recette d'un assistant IA professionnel. Première
cible : MaskAI (`app.maskai.fr`), 12–13 octobre 2026.

| Fichier | Rôle |
|---|---|
| `PROCEDURE.md` | la procédure Aubier (7 étapes, grille N0–N9, six exigences non fonctionnelles, barème, leçons, annexes techniques) |
| `2026-10-13-maskai-benchmark.md` | le rapport consolidé des deux exécutions (version publique, infrastructure expurgée) |
| `2026-10-17-maskai-benchmark.md` | le rapport de la première recette gouvernée par le noyau (2026-10-17), rédigé par l'arche de rédaction et signé ; delta contre le 13/10 |
| `runs/2026-10-17/` | les fichiers publiés par la tâche (verdict, rapport, mesure de chargement, journal des 34 cas) ; captures d'écran retirées (compte visible) |

**Réserves sur le rapport du 17/10** (débriefing du pilote, `docs/technical/debriefs/2026-10-17-maskai-driver.md`) : le rapport est un artefact signé et n'est pas modifié ; le débriefing relève que deux verdicts (N5-1, N9-3) ne tiennent que par la bannière du modèle lue avec la réponse, que 8 réponses sur 21 ont été capturées coupées (le pilote cesse d'attendre à la fin du flux serveur), que l'état des pièces jointes n'a jamais été réellement observé (le P0 « pièces jointes hors service » n'est pas séparé d'un défaut de synchronisation du pilote), et que la recherche web était en « auto » alors que le 13/10 était en « off ». Ces points sont l'objet de la version 2 du pilote et de la seconde recette.
| `journal-v2.jsonl` | le journal append-only de la seconde exécution (une ligne JSON par test) |
| `corpus/` | corpus v1 : 10 documents fictifs, 9 formats, `MANIFESTE.md`, `oracles.json`, générateur |
| `corpus-v2/` | corpus v2 : 5 documents légers et 5 volumineux, `README.md`, `oracles-v2.json`, générateurs |
| `PROCEDURE-2.md` | la seconde recette, « MaskAI en profondeur » : delta de `PROCEDURE.md`, grille M0–M7 (61 cas), budgets par classe, deux bases du delta ; recette gouvernée `recipes/recette-maskai-2.json` |
| `corpus-v3/` | corpus v3, l'axe masquage : 6 documents fictifs, une famille de données identifiantes chacun, `README.md`, `oracles-v3.json` (en pièce jointe et collé), générateur |
| `../../../scripts/recette/` | `evaluer.py` (oracles → verdicts), `journal.py` (journal, rendu, agrégats), tests |
| `../../../.claude/skills/recettage-chat-ia/` | la compétence que l'agent charge pour rejouer la recette |

Rejouer sur une nouvelle version : ouvrir la session dans Chrome, charger la compétence,
suivre `PROCEDURE.md`, journaliser chaque test, évaluer contre les oracles, n'écrire que le
delta du rapport.
