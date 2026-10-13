---
name: recettage-chat-ia
description: Recette fonctionnelle et benchmark d'un chat IA (web, piloté dans Chrome) selon la procédure Aubier — grille N0–N9, six exigences non fonctionnelles, corpus de documents fictifs avec oracles, journal append-only, rapport delta. À utiliser dès qu'on demande de tester, recetter, benchmarker ou rejouer la recette d'un assistant IA (MaskAI ou autre).
---

# Recette d'un chat IA — procédure Aubier

Lis d'abord `docs/use-cases/recettage-chat-ia/PROCEDURE.md` (les sept étapes, la grille
N0–N9, le barème). Ce fichier ne contient que ce que l'agent doit savoir pour exécuter
sans se tromper.

## Règles non négociables

- Données **fictives** uniquement : le corpus du dépôt ou un corpus généré pour l'occasion.
- La session est ouverte par l'humain. Jamais de création de compte, jamais de mot de passe.
- Ne rien supprimer qui existait avant ; lister ce qui a été créé.
- Pas de retour utilisateur, de message, d'achat ni de changement de réglage non remis.
- Un défaut de sécurité (fuite d'information interne, contournement du masquage) est signalé
  tout de suite à l'humain et le rapport reste privé tant qu'il n'est pas corrigé.

## Outils du dépôt

| Besoin | Commande |
|---|---|
| Générer le corpus v1 (10 docs) | `powershell -File docs/use-cases/recettage-chat-ia/corpus/generer-corpus.ps1 -Out <dir>` |
| Générer le corpus v2 (légers + volumineux) | `python -I docs/use-cases/recettage-chat-ia/corpus-v2/gen-corpus-v2.py <dir>` puis `powershell -File .../gen-corpus-v2.ps1 -Out <dir>` |
| Journaliser un test | `python -I scripts/recette/journal.py <run>.jsonl add --step exec --case <id> --verdict <OK\|PARTIEL\|KO> --timing <s> --nfr performance,securite --evidence "…"` |
| Évaluer contre les oracles | `python -I scripts/recette/evaluer.py <oracles.json> <observations.json>` |
| Agrégats pour le rapport | `python -I scripts/recette/journal.py <run>.jsonl stats` |

Le journal est le registre : chaque mesure y entre avant d'aller dans le rapport.

## Ce que l'agent a appris (à relire avant chaque exécution)

| Situation | Règle |
|---|---|
| Un script exécuté dans la page meurt à 45 s | attendre par tranches < 36 s ; mesurer avec un horodatage posé avant l'envoi |
| Le sélecteur de fichiers natif n'est pas pilotable | déposer le fichier dans le `input[type=file]` caché derrière le trombone (`find` puis `file_upload`) |
| Taper un long texte est lent et fragile | fixer la valeur du `textarea` par programme (setter natif + événement `input`), puis taper un espace |
| Un texte avec accents passe par le shell Windows | l'encoder en base64 et le décoder dans la page (`decodeURIComponent(escape(atob(b64)))`) |
| Une pièce jointe peut échouer avant l'envoi | lire l'état de la puce (`aria-label` « Failed ») avant d'envoyer |
| Les compteurs affichés peuvent se contredire | recouper panneau, indicateur et événements serveur |
| Un défaut de masquage se voit dans le texte envoyé, pas dans la réponse | relever « ce qui part au modèle » à chaque test ; chercher les masques collés à un mot et les jetons cassés |
| Le même test peut donner deux résultats | rejouer deux fois tout test de masquage |
| Une réponse polie peut cacher un échec | « je ne vois pas de document » = KO de l'outil |
| Le temps d'attente vient rarement du modèle | horodater chaque étape serveur (intercepteur SSE, annexe A de la procédure) |
| Deux traitements lourds en parallèle peuvent faire tomber un service | tester la charge concurrente **exprès**, en le journalisant comme test de disponibilité, pas par accident |
| Un traitement long peut ne jamais se terminer | fixer un délai d'abandon par test (3 × le budget) et le journaliser comme KO disponibilité |

## Exigences non fonctionnelles à couvrir

Chaque test est rattaché à au moins une ENF : performance, véracité, disponibilité,
sécurité, conformité, scalabilité. Le rapport donne un score par ENF (barème dans la
procédure) et une recommandation par ENF.
