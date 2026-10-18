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
| `../../../scripts/recette/` | `evaluer.py` (oracles → verdicts), `journal.py` (journal, rendu, agrégats), tests |
| `../../../.claude/skills/recettage-chat-ia/` | la compétence que l'agent charge pour rejouer la recette |

Rejouer sur une nouvelle version : ouvrir la session dans Chrome, charger la compétence,
suivre `PROCEDURE.md`, journaliser chaque test, évaluer contre les oracles, n'écrire que le
delta du rapport.

## Exécution du 2026-10-18 (recette 2, en attente de signature)

Seconde recette jouée sur le nœud avec le pilote v2.1 (branche `maskai-driver-v2`, 94 cas, les
quatorze modèles du sélecteur) : tâche `task-node-1-75`, verdict rejugé après trente lignes de
correction du journal (deux artefacts du banc : un oracle `` corrompu, une bannière de modèle non
lue sans fournisseur) — 48 OK, 17 PARTIEL, 29 KO. Non signé le 18/10 : la grille est jugée encore
injuste et incomplète pour servir une refonte (contrat de masquage absent de la rubrique du juge,
texte envoyé jamais observé, canal pièces jointes hors service côté infrastructure). Le rapport
reste privé tant que la fuite d'erreur interne constatée sur les pièces jointes (un message
d'erreur du serveur, avec un nom d'hôte interne et une pile logicielle, affiché dans la puce)
n'est pas corrigée. Décisions ouvertes :
registre du 18/10 (`docs/strategy/decisions/`, privé). Le fondateur a tranché la mesure en boîte
noire : la lecture du texte envoyé passera par le panneau « Détails » de la réponse.
