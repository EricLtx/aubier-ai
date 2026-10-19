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

v2.2 : texte envoyé lu sur la carte de réponse (le panneau « Détails », ouvert, lu et refermé par le
pilote après chaque réponse ; le popover « Chiffré » du compositeur, qui décrit l'état avant
l'envoi, n'est plus qu'un repli) ; l'évaluateur lit un lien `pii://` resté brut comme son texte (les
espaces insécables étaient déjà repliées). L'évaluateur change, donc `inputs_sha256` aussi : la
recette 3 ne porte pas l'empreinte de la recette 2, c'est attendu, et son delta doit le dire.

## Recette 3 (en cours)

La troisième recette, « de bout en bout » en boîte noire (décisions du fondateur du 18/10), est
décrite par `PROCEDURE-3.md`, delta de `PROCEDURE-2.md`. Sa grille M0–M9 rejoue les 94 cas M0–M7
et ajoute M8 (fonctionnalités) et M9 (pertinence) : 108 cas, tous dans
`drivers/maskai-recette/cases.json` (les quatorze de M8 et M9 aussi dans `cases-3-proposal.json`).
Le juge y applique un contrat de masquage écrit (§7, décision D-17), une grille de pertinence et une
lecture de la latence par couche, et marque une seule fois « indisponible » un canal pièce jointe en
erreur. La recette gouvernée, à dix niveaux, est `recipes/recette-maskai-3.json`. Sa première
exécution, les 18 et 19/10, n'est pas signée (décision du fondateur du 19/10) : elle se rejoue
entière avec le pilote v2.3.

v2.3 (19/10, PROCEDURE-3 §6, E1 à E11) : le pilote tient le registre des conversations qu'il crée
et ne renomme ni ne supprime que celle de M8-1, retrouvée par son adresse ; il joue la recherche
⌘K (M8-4), le renommage (M8-5), la suppression (M8-6), relit l'état du bouton de dictée sans jamais
le presser (M8-7) et ouvre puis ferme par sa croix l'enquête « Votre avis » sans rien envoyer (M8-8).
Chaque ligne porte désormais ce que le juge doit lire sans ouvrir les preuves : la source et les
types des jetons du texte envoyé, le compte du panneau de garde, l'état du micro, les modales
fermées, la liste des types de fichiers acceptés, et la classe d'une erreur (puce en échec, question
restée sans réponse) sans nom d'hôte ni pile, le message brut restant dans les preuves privées. Une
sonde en lecture seule relève le balisage de la page en M0, avant que M8 en ait besoin, et
`scripts/recette/correct.py` écrit les lignes de correction en rejouant hors ligne le jugement du
pilote.
