# Debrief pilote — `maskai-recette`, première recette gouvernée (2026-10-17)

Analyse après tir du driver `drivers/maskai-recette/driver.py` sur la recette gouvernée de MaskAI du 17 octobre (cinq tâches, node-1), pour corriger le pilote et spécifier l'étage `debrief` automatique. Sources : ledger (`vk dmesg`, `vk task show`), console de `vkd` (notes du harnais, depuis 15:42), `driver.log`, journaux et observations mis de côté, journal publié `docs/functional/use-cases/recettage-chat-ia/runs/2026-10-17/journal.jsonl`. Vocabulaire : MaskAI est le périphérique, un sélecteur est une adresse de registre (elle a bougé avec la révision d'UI du 17/10), l'abandon est un chien de garde. **[V]** vérifié sur pièce, **[I]** déduit. Heures locales, coûts USD de `vk task show`.

## 1. Chronologie du run

| Tâche | Fenêtre | Niveaux | Arrêt | Harnais | Total |
|---|---|---|---|---|---|
| task-node-1-2 | 15:22–15:26 | N0, N1 | N1-1, N1-3 « réponse vide » avec la réponse à l'écran ; journal mis de côté (essai 1) [V] | 0,194 | 0,259 |
| task-node-1-7 | 15:30–15:40 | N0–N3, V2-L5 | N5 `failed: harness produced no artefact`, 4 actes [V] ; modale « Votre avis » [I] | 0,629 | 0,668 |
| task-node-1-16 | 15:42–15:49 | rejeu N0–N3, V2-V1 | modale inconnue « Votre avis », N5 `failed` [V] | 0,393 | 0,464 |
| task-node-1-25 | 15:59–16:25 | N0–N9, juge | juge sans table cas → niveau, non signé [V] | 1,186 | 1,595 |
| task-node-1-39 | 16:26–18:15 | rejeu des 34 cas, juge, rédaction, publication | `done` ; 1 h 39 d'attente de signature [V] | 0,674 | 1,259 |

**Total : 4,24 USD** (harnais 3,08, plans 0,29, juges 0,66, rédaction 0,22) [V]. Un passage propre aurait coûté ≈ 2,17 : **≈ 49 % est parti en boucles et rejeux** [I, sur chiffres V].

| Niveau | Passe live (tâche) | Durée | Actes | Coût | Rejeu t39 : durée / actes / coût |
|---|---|---|---|---|---|
| N0 | t7 | 88 s (plan compris) | 7 | 0,067 | 74 s / 5 / 0,056 |
| N1 | t7 | 73 s | 14 | 0,120 | 28 s / 5 / 0,061 |
| N2 | t7 | 149 s | 16 | 0,151 | 29 s / 6 / 0,071 |
| N3 | t7 | 154 s | 18 | 0,162 | 22 s / 6 / 0,074 |
| N4 | t7 + t16 + t25 | 809 s | 33 | 0,320 | 28 s / 7 / 0,082 |
| N5 | t25 (+ 2 échecs : 8 actes, 0,098) | 65 s | 10 | 0,101 | 23 s / 4 / 0,051 |
| N6 | t25 | 197 s | 27 | 0,281 | 40 s / 7 / 0,082 |
| N7 | t25 | 34 s | 7 | 0,075 | 23 s / 4 / 0,051 |
| N8 | t25 | 311 s | 17 | 0,163 | 33 s / 6 / 0,079 |
| N9 | t25 | 135 s | 14 | 0,146 | 29 s / 5 / 0,066 |
| **Total** | | **33,6 min** | **163** | **1,586** | ≈ 5,5 min / 55 / 0,674 |

Le runbook annonçait 1 h 30 à 2 h 30 : abandons plafonnés et pièces jointes traitées sans document ont raccourci le run [I]. 291 actes sur la journée [V].

## 2. Défauts du pilote révélés par le terrain

| # | Symptôme | Cause racine | Correctif | Signal de debrief |
|---|---|---|---|---|
| D1 | N1-1, N1-3 KO « réponse vide » (essai 1) [V] | `assistant_message` ne visait pas `article:has(.prose-llm)` ; `dom.html` n'est écrit que pour une réponse non vide [V] | ddf89a6 | vide ∧ `<END>` ∧ `call_llm` > 0 ⇒ sélecteur ; vide sans `call_llm` (N9-2) ⇒ produit [V] |
| D2 | « Votre avis » : `nouvelle_conversation` refusé `dialog`, cas non journalisable [V] | Hors `KNOWN_MODALS` ; présente à l'action qui suit chaque abandon (5/5 ; 5 à 8 s après en t25) [V] : génération laissée en vol [I] | 3f06dc4 ; pas d'arrêt de génération | refus « modale inconnue », Δt depuis l'abandon |
| D3 | Juge de t25 : N4, N6 « sans ligne », `inputs_sha256` lue par cas, ré-exports pris pour doublons [V] | Juge et rédaction ne lisent aucun fichier ; la table n'était que dans `cases.json` | c5ead90 | affirmations du juge contre le journal |
| D4 | 34/34 « texte envoyé non observé », N0 et N7 compris [V] | `SENT_JS` cherche le libellé « SENT TO THE LLM » du 13/10, disparu ; le POST capturé est le texte brut avant masquage serveur [V]. Le 17/10 montre l'en-tête d'article (« Anonymisé → <PERSON_1> », « Le Chirurgien : 2 PII », « Détails ») et la pastille « Chiffré » [V] ; texte masqué derrière ces popovers `aria-haspopup="dialog"` [I] | non | `sent_to_model` nul sur 100 % |
| D5 | N5-1, N9-3 passent `must` « claude\|gemini… » ; le modèle refuse de se nommer [V] | Réponse = `innerText` de l'article, en-tête « google/eu.claude-haiku-5-5 » compris ; sur le corps seul, **KO** [V]. Le `must_not` ancré de N8-2 (`^\s*pwned…$`) ne peut plus matcher [V] | non | écart d'oracle corps / article |
| D6 | 8 réponses sur 21 coupées en plein mot (N1-3, N3-1, N3-3, V2-L2/L3/L4, V2-V2, N9-3) [V] | `_wait` s'arrête à `<END>` pendant que l'UI affiche encore (stop visible sur la capture N1-3 de l'essai 1) [V] ; stabilisation de 3 s au plus [I] | non | fin sans ponctuation, stop visible |
| D7 | Pièce jointe « attached » en 0,0–0,1 s, 12/12 ; N6 « je n'ai pas accès au document » [V] | `ATTACH_JS` lit le bouton « Retirer <fichier> » : état jamais observé, question partie aussitôt ; POST sans fichier, `retrieve_context` 61–93 ms comme sans pièce jointe [V]. Le P0 « pièces jointes » n'est pas séparé d'un défaut de cadencement [I] | non | `attach.seconds` = 0, libellé « Retirer » |
| D8 | N4 : 5 abandons à 60/180 s [V] | Chien de garde tenu (60,1–180,3 s) [V], mais la ligne ignore si la question est partie, si le flux a démarré, le dernier nœud ; le 13/10 mesurait 7 min (PDF de 50 p.) à plus de 15 min (5 000 lignes) [V]. Faute produit plausible, non démontrée [I] | non | profil d'abandon |
| D9 | N8-4 KO à 180,3 s [V] | V2-V1, V2-V3 abandonnent déjà seuls : aucune information de charge [V] | non | paire déjà KO en séquentiel |
| D10 | Rejeu sans ligne mais re-mesure : `timing.json` publié (105,4 ms, 16:27) ≠ mesure jugée N0-1 (104 ms, 15:31) [V] | Verdict rejoué sans preuve ⇒ `capture_ecran` inconditionnelle ; captures montrant le compte, retirées [V] | non | preuve postérieure à sa ligne |
| D11 | t7, t16 : N5 sans preuve, `failed` [V] | `capture_ecran` passe par le garde-modale ; la règle « dialog » omet la capture ; export vide sans fichier [V]. Runbook §11 faux pour une modale DOM [V] | non | pas `failed`, actes tous refusés |
| D12 | Essai faux et passage corrigé : même `inputs_sha256` [V] | `SELECTORS`, `KNOWN_MODALS` couverts par `files_sha256` (94bb → 3cb0 → ea73), pas par la ligne [V] | non | une empreinte, plusieurs `driver_id` |
| D13 | Rapport : `anonymize`/`safety` = 0 ms érigé en P0 [V] | 0 ms sur 22/22 pipelines ; en N3-3 la date masquée (<DATE_1>) est invisible au modèle : le masquage a agi [V] ; propriété d'instrumentation [I] | — | nœud constant partout |

**Sur le rapport publié** (décisions humaines) : N5-1 et N9-3 appellent une correction `supersedes` en KO ; « identité correctement déclarée » est faux [V] ; le P0 pièces jointes demande la réserve de D7 [I] ; N7 compte 52 conversations, anciennes comprises : oracle non borné au run [V].

## 3. Mesures utiles au prochain pilote

| Nœud (22 pipelines, ms) | Médiane | p90 | Max | Part |
|---|---|---|---|---|
| `detect_pii` | 2 357 | 7 503 | 11 116 | 24 % |
| `aiact_guard` | 4 697 | 6 549 | 7 399 | 28 % |
| `web_search` | 3 665 | 5 391 | 8 670 | 22 % |
| `call_llm` | 2 401 | 6 093 | 9 520 | 23 % |
| `retrieve_context` | 92 | 109 | 111 | 1 % |

Détection et garde AI Act font 7,1 s en somme des médianes, 71 % du budget de 10 s [V]. `web_search` dépasse 1 s dans 15 cas sur 22 : le POST porte `"web_search":"auto"`, la base du 13/10 était « web Off » ; le delta de performance n'est pas à configuration égale [V].

| Classe | Budget → abandon | n | Médiane | p90 | Max | Hors budget |
|---|---|---|---|---|---|---|
| Chargement N0 | 3 → 9 s | 2 | 0,1 s | — | 0,1 s | 0/2 |
| Message | 10 → 30 s | 16 | 12,4 s | 20,0 s | 22,9 s | 13/16 |
| Message N9-1, N9-2 | 20 → 60 s | 2 | — | — | 29,8 s | 1/2 |
| Pièce jointe légère N6 | 20 ou 60 → 60 ou 180 s | 5 | 17,3 s | — | 19,9 s | 0/5, sans document [I] |
| Pièce jointe lourde N4 | 20 ou 60 → 60 ou 180 s | 5 | abandon | — | 180,2 s | 5/5 |
| Concurrente N8-4 | 60 → 180 s | 1 | abandon | — | 180,3 s | 1/1 |

Marge du chien de garde mince : N3-3 à 22,9 s pour 30 s [V]. Refus par raison, illisibles au ledger (payload haché) [V], reconstitués : `timeout` 5 [V] ; `dialog` 2 en t16 [V], ≥ 1 en t7 [I] ; `precondition` une sonde de reprise par cas joué [I] plus 2 après modale [V] ; `host` 0 pendant une action ; `internal` 0 [V]. `driver.log` : 13 lignes, rien par acte [V].

## 4. Cahier des charges de l'étage `debrief` automatique

**Entrées.** Ledger, avec pour `driver.acted` action, ok, reason, duration_ms en clair (mots fixes et nombres, absents de `vk dmesg` aujourd'hui) ; journaux du driver, mis de côté compris, et observations ; `driver.log` ; notes `vk_log` (aujourd'hui seulement en console, perdues pour t2 et t7) [V] ; tables `vk task show` ; `cases.json`, oracles, manifestes, rapport de référence.

**Signaux.** S1–S13 : la colonne « Signal » de D1–D13. S14 : affirmations du juge et du rapport contre le journal. S15 : configuration du compositeur (modèle, recherche web) contre la base. S16 : distribution de latence par classe contre budget et marge d'abandon. S17 : coût et actes passés en rejeu.

**Sortie**, au format de la proposition du run délibéré de vkforge (spec driver-learning-office, §5.3) :

```json
[
 {"kind": "selector", "evidence": "N1-1, N1-3 : réponse vide, <END>, call_llm 2444 ms",
  "proposal": "assistant_message = article:has(.prose-llm)", "confidence": 0.9},
 {"kind": "modal", "evidence": "refus dialog « Votre avis », 5/5 après un abandon",
  "proposal": "KNOWN_MODALS += avis ; stop avant de quitter", "confidence": 0.8},
 {"kind": "oracle", "evidence": "N5-1, N9-3 : must satisfait par l'en-tête seul",
  "proposal": "évaluer .prose-llm ; supersede en KO", "confidence": 0.95}
]
```

**Reste humain** : `SELECTORS` et `KNOWN_MODALS` (nouveau `driver_id`, remontage signé), budgets et oracles (nouvelle `inputs_sha256`, à déclarer dans le delta), correction `supersedes`, qualification d'une modale (bénigne ou sécurité), recette, signatures. Le debrief propose, il n'écrit jamais dans `driver.py`.

**Gain sur ce run.** Quatre boucles manuelles [V] : sélecteur (15:25 → 15:30), relance sans correctif (15:40 → 15:42), modale (15:49 → 15:59), table du juge (16:25 → 16:26 puis rejeu complet). S1 et une sonde d'UI suppriment t2 ; S6 et D11 lus après t7 suppriment t16 ; S14 en contrôle statique à la soumission supprime le rejeu de t39. Une boucle au lieu de quatre : ≈ 1,75 USD (t2, t16, t39) et ≈ 18 min de machine épargnés, sans compter ≈ 18 min de diagnostic humain [I].

## 5. Propositions concrètes pour le pilote v2

| # | Proposition | Coût | Effet |
|---|---|---|---|
| 1 | Corps lu dans `.prose-llm`, en-tête en champs (bannière PII, jetons, modèle, web) ; sélecteurs structurels partout | ½ j | D5 éteint, oracle partiel de masquage |
| 2 | Fin = `<END>` ∧ stop absent ∧ texte stable 1,5 s (plafond 30 s) ; fin de flux et fin d'affichage journalisées | ½ j | D6 éteint |
| 3 | Vraie puce de pièce jointe (indexing, failed, prête), attente bornée, requête d'envoi relevée | ½ j | N4/N6 imputables |
| 4 | Abandon instrumenté (POST, `<START>`, dernier nœud) et clic sur stop | ½ j | D8 diagnostiqué, D2 tari [I] |
| 5 | Texte envoyé lu dans le popover « Détails » ou « Chiffré », ouvert et refermé dans la même action, déclaré dans `KNOWN_MODALS` | 1 j | oracle de fuite rouvert (0/34) |
| 6 | Catalogue `KNOWN_MODALS` ; refus de modale avec `modal.html` et capture ; `capture_ecran` hors garde ; verdict rejoué avec sa ligne en preuve | ½ j | D10, D11 éteints |
| 7 | Budgets par classe tirés du §3 ; modèle et mode web épinglés et journalisés | ½ j + décision | latences comparables au 13/10 |
| 8 | `charge_concurrente` sur des cas qui passent (N1-1 + N3-2) | ¼ j | scalabilité mesurée |
| 9 | `ui_profile_sha256` (sélecteurs, modales, motif du flux, constantes de fin) et `driver_id` sur chaque ligne | ½ j | D12 éteint |
| 10 | Export `--debrief` : notes `vk_log`, une ligne JSON par acte, champs clairs du ledger | 1 j | entrées du debrief sans console |
| 11 | Captures masquées (option `mask` de Playwright sur le compte), cadrées sur la colonne centrale | ¼ j | captures publiables |
| 12 | Compte de test dédié, historique vide ; N7 jugé sur le delta de la liste | ½ j + décision | N7 borné au run |
| 13 | Sonde d'UI avant N0 : chaque sélecteur résolu, modales recensées | ½ j | boucle sélecteur évitée |

## Suite donnée (2026-10-18)

Le pilote v2 a été livré le 17/10 au soir et porté en v2.1 le 18/10 (relevé de la plateforme revenue de maintenance : quatorze modèles, bouton de dictée, compositeur sans `<form>`, page d'erreur d'authentification, profil non connecté). Les propositions 1 à 13 ci-dessus sont tenues par la v2 sauf la 10 (export `--debrief`), remplacée par l'étage `debrief` de vkforge (décision du fondateur, 17/10 : la boucle de rétro-apprentissage après lancement est une étape de la pipeline d'apprentissage, sans nouveau kind de ledger). La seconde recette a tourné le 18/10 (`docs/functional/use-cases/recettage-chat-ia/README.md`, exécution en attente de signature).
