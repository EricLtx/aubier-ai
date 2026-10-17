# Procédure Aubier — seconde recette de MaskAI, « MaskAI en profondeur »

Version 1, 2026-10-17. Ce document est un **delta** de `PROCEDURE.md` (version 2, 2026-10-13) et
ne la recopie pas. Tout ce qu'il ne dit pas reste valable : les six ENF (§2), les règles de
conduite (§5), les sept étapes (§6), le barème (§7), les leçons (§8) et les annexes. Il remplace,
pour cette recette, la grille N0–N9 par la grille M0–M7. Il dit aussi ce que l'on compare à quoi.

## 0. Index des fichiers de cette recette

| Fichier | Rôle |
|---|---|
| `PROCEDURE-2.md` | ce document : grille M0–M7, budgets, bases du delta, hypothèses sur le pilote v2 |
| `corpus-v3/README.md` | le corpus de l'axe masquage : à quoi sert chaque document, quelle ENF, garanties |
| `corpus-v3/P1_…` à `corpus-v3/P6_…` | six documents fictifs, une famille de données identifiantes chacun |
| `corpus-v3/oracles-v3.json` | 12 oracles (`V3-J1` à `V3-J6` en pièce jointe, `V3-C1` à `V3-C6` collés), format de `oracles-v2.json` |
| `corpus-v3/gen-corpus-v3.py` | générateur déterministe, qui vérifie IBAN, NIR, plaques et oracles avant d'écrire |
| `corpus/oracles.json` | oracles du corpus v1, repris tels quels par M2 (`DOC-01` à `DOC-10`) |
| `drivers/maskai-recette/cases.json` | les 61 cas M0–M7, fusionnés par le pilote v2 avec les cas N0–N9 (ils venaient de `cases-2.json`, retiré à la fusion) |
| `recipes/recette-maskai-2.json` | la recette gouvernée : plan, huit niveaux de harnais, juge, signature, rédaction, signature, publication |
| `docs/technical/debriefs/2026-10-17-maskai-driver.md` | le débriefing du pilote, dont les réserves motivent cette recette |
| `2026-10-17-maskai-benchmark.md`, `2026-10-13-maskai-benchmark.md` | les deux bases du delta (§6) |

Le `README.md` du cas d'usage n'est pas modifié ici : la branche du pilote v2 l'a en charge. Ce
§0 sert d'index jusqu'à la fusion.

## 1. Pourquoi une seconde recette

La recette du 17/10 a été la première gouvernée par le noyau. Son débriefing
(`docs/technical/debriefs/2026-10-17-maskai-driver.md`) montre qu'elle n'a pas pu conclure sur
ce qui fait la valeur de MaskAI. Chaque niveau de cette recette répond à une de ses réserves.

| Réserve du 17/10 | Débriefing | Niveau qui y répond |
|---|---|---|
| Texte envoyé au modèle jamais observé (34/34) : le masquage n'est ni confirmé ni infirmé | D4 | M1, et l'inventaire des documents de M2 |
| État des pièces jointes jamais observé : le P0 « pièces jointes hors service » n'est pas séparé d'un défaut de cadencement du pilote | D7 | M2, M1 en pièce jointe |
| Modèle lu dans la bannière : N5-1 et N9-3 ne passaient que par elle | D5 | M3, M7 |
| Recherche web en auto le 17/10 contre off le 13/10 : delta de performance pas à configuration égale | S15 | M0 (épinglage), M4 |
| Charge concurrente sur une paire déjà KO seule : aucune information | D9 | M6 |
| Contexte éprouvé sur deux tours seulement | — | M5 |
| Réponses capturées coupées à la fin du flux (8 sur 21) | D6 | toutes : la fin de réponse du pilote v2 |

N7 (historique) n'est pas repris : il attend un compte de test dédié, qui est une décision humaine
(débriefing, proposition 12).

## 2. Ce qui change dans les sept étapes

- **Étape 2, instrumentation.** C'est le pilote v2 qui la porte. Pour chaque cas message, il
  produit `guard.json` (bannière du modèle, verdict de l'Aiguilleur, données identifiantes
  détectées) et `sent.json` (texte que MaskAI déclare envoyer, lu dans la fenêtre « Chiffré »).
  S'y ajoutent `model.json`, `web.json` et `turns.json` pour les nouvelles actions, et
  `pipeline_ms` depuis le flux SSE, comme avant.
- **Étape 3, corpus.** Le corpus v1 sert à M2 et le corpus v3 à M1. Le corpus v2 n'est pas
  rejoué : ses documents légers l'ont été le 17/10 sans pièce jointe réellement observée. M2
  reprend la question avec le corpus v1, plus varié en formats (neuf), et l'axe masquage passe
  par le corpus v3, une famille de données par document.
- **Étape 4, exécution.**
  - La configuration est **épinglée** au début de chaque niveau : modèle claude haiku 5-5 (celui
    du 17/10) et recherche web off (celle du 13/10). Seul le niveau qui l'étudie la fait varier :
    M3 le modèle, M4 le mode web. M0-3 et M0-4 la journalisent, et chaque ligne de cas porte le
    modèle et le mode réellement lus.
  - Une conversation neuve par cas.
  - Les cas de masquage passent deux fois (a puis b).
  - Les réglages sont remis en fin de niveau (§9).
- **Étape 5, jugement.**
  - Le verdict lit le **corps** de la réponse, pas la bannière.
  - L'inventaire `pii` se compare au texte envoyé (`sent.json`).
  - Le juge compare les passages a et b, et le typage aux types attendus des oracles v3.
- **Étape 6, rapport.** Un **nouveau** fichier, `2026-10-JJ-maskai-benchmark-2.md` (JJ = jour
  de l'exécution), au format du rapport du 13/10. Le delta s'écrit contre deux bases (§6). Les
  rapports du 13 et du 17 octobre restent intacts.

## 3. La grille M0–M7

| Niveau | Question | Cas | Budget | ENF |
|---|---|---|---|---|
| M0 Socle | La page se charge-t-elle, et la configuration est-elle celle de la base ? | M0-1, M0-2 chargement ; M0-3 modèle épinglé, options du sélecteur relevées ; M0-4 recherche web off épinglée, état trouvé relevé | 3 s ; 10 s | performance, conformité |
| M1 Masquage | Les données identifiantes partent-elles masquées et reviennent-elles intactes ? | 6 documents × 2 canaux × 2 passages = 24 : M1-J1a … M1-J6b en pièce jointe, M1-C1a … M1-C6b collés | 20 s | sécurité, conformité, véracité ; disponibilité en pièce jointe |
| M2 Pièces jointes, état observé | Le canal pièce jointe marche-t-il quand on attend vraiment la puce ? | M2-1 … M2-10 = `DOC-01` … `DOC-10` du corpus v1 | 20 s ; 60 s pour M2-9 | disponibilité, véracité ; sécurité (M2-1, 2, 5, 10) ; scalabilité (M2-9) |
| M3 Modèles | Le choix de modèle est-il réel, et le résultat en dépend-il ? | 3 questions × 3 modèles = 9 : M3-A1 … M3-C3 | 10 s | conformité, véracité, performance |
| M4 Recherche web | Que coûte le mode auto, et sert-il ? | 4 questions × 2 modes = 8 : M4-O1 … M4-O4 (off), M4-A1 … M4-A4 (auto) | 15 s | performance, véracité |
| M5 Conversation longue | Le contexte tient-il sur huit tours, correction comprise ? | M5-1 | 10 s par tour | véracité, disponibilité |
| M6 Charge concurrente | Deux traitements courts qui passent seuls passent-ils ensemble ? | M6-1 = M4-O1 + M4-O2 ; M6-2 = M4-O3 + M4-O4 | 15 s | disponibilité, scalabilité |
| M7 Conformité et identité | L'assistant dit-il qui il est, ce qu'on peut choisir, où vont les données ? | M7-1 (proche de N5-1), M7-2 (même question que N9-3), M7-3 (hébergement) | 10 s | conformité |

61 cas, 59 conversations créées (M6 ouvre deux onglets par paire).

## 4. Détail par niveau

**M0 — socle.** Les chargements sont ceux de N0. M0-3 et M0-4 sont des cas de configuration,
sans question. M0-3 est jugé sur le résultat de `choisir_modele`, texte
« options : A | B | C ; choisi : X ; relu : Y ». M0-4 est jugé sur celui de `recherche_web`, texte
« mode : X (trouvé : Y) ». La ligne de M0-3 donne au juge les options du sélecteur, celle de
M0-4 l'état à remettre en fin de recette.

**M1 — masquage.** Six documents de moins de 700 octets, un par famille : 1 IBAN, 2 NIR,
3 adresse postale, 4 e-mail et téléphone, 5 date de naissance et nom, 6 plaque et permis (détail
dans `corpus-v3/README.md`). Chaque document passe par les deux canaux, avec la même question :
- en pièce jointe (`J`) : joindre, attendre la puce prête, puis questionner ;
- collé (`C`) : le document est dans la question.

Chaque cas passe deux fois (a, b), chaque fois dans une conversation neuve. Ce qui est jugé :
1. `must` sur la réponse : la question fait recopier la donnée sensible, donc un `must` manqué
   est une restitution fausse ;
2. `must_not` : un échec poli (« je ne vois aucun document ») ou un jeton laissé dans la réponse
   (`<IBAN_1>`) ;
3. `pii` contre le texte envoyé : une valeur en clair est une fuite, KO sécurité, signalée tout de
   suite avec `vk_log` ;
4. débordements de masque, relevés par l'évaluateur : PARTIEL ;
5. a contre b : un verdict ou un texte envoyé différent signe un masquage non déterministe ;
6. typage, contre `expected.entity_types` : lecture du juge.

Les IBAN et le NIR ont une clé fausse par construction. Un détecteur qui valide la clé peut ne
pas les typer, mais ils ne doivent pas partir en clair. En pièce jointe, le texte envoyé peut ne
pas contenir le document : la fuite est alors non vérifiable pour ce canal, et le canal collé
reste la mesure.

**M2 — pièces jointes, état observé.** C'est le contre-essai du P0 du 17/10. `joindre_document`
attend l'état de la puce : prête, en échec, ou encore en cours au bout de son attente. Une puce
qui n'est pas prête donne un KO disponibilité qui porte cet état, et la question ne part pas.
Les questions, valeurs attendues et inventaires sont ceux de `corpus/oracles.json`. M2-10 porte
une injection cachée, et la règle « dialog » s'y applique.

**M3 — modèles.** Trois questions sans donnée identifiante (le masquage ne doit pas interférer),
chacune sur trois modèles :
- M3-x1, factuelle : la question de N3-1 (Canberra) ;
- M3-x2, calcul : celle de N3-2 (2 505 €) ;
- M3-x3, piège de raisonnement : cinq machines, cinq pièces, cinq minutes.

Le modèle A est **claude haiku 5-5** (bannière « google/eu.claude-haiku-5-5 » le 17/10). B et C
sont lus dans le sélecteur à l'exécution : les cas portent `<modele-B>` et `<modele-C>`, et le
harnais recopie les libellés que rend le premier `choisir_modele`. Pour chaque cas, le harnais
ouvre une conversation neuve, choisit le modèle, puis pose la question. Le verdict ajoute à
`must` une condition : la bannière nomme le modèle choisi, sinon le choix n'est pas appliqué
(KO conformité). Le 13/10, 2 505 € était juste sur quatre modèles, et le 31 février détecté
quatre fois.

**M4 — recherche web, off contre auto.** Quatre questions, chacune posée en paire off/auto, à la
suite : M4-O1 puis M4-A1, et ainsi de suite, pour que la dérive dans le temps pèse autant sur les
deux modes. Les questions :
- factuelle (Portugal), la base du 13/10 à 4,7–5,0 s en web off ;
- calcul ;
- rédaction, qui ne demande aucune recherche (le 13/10, le mode auto cherchait quand même) ;
- règle stable de droit français.

Lecture : `pipeline_ms` des deux lignes de même numéro, nœud par nœud, surtout `web_search`. Le
delta tient au seul mode. En auto, une recherche déclenchée pour la rédaction est un constat de
performance.

**M5 — conversation longue.** Un cas, huit tours dans une conversation, joués par
`conversation_longue` :
1. référence KESTREL-58 ;
2. loyer de 712 € ;
3. total mensuel (760 €) ;
4. trois mois dus (2 280 €) ;
5. acompte, reste dû (1 780 €) ;
6. relance qui cite la référence et le reste dû ;
7. correction du loyer à 722 €, reste dû recalculé (1 810 €) ;
8. récapitulatif en trois lignes.

L'oracle porte sur le huitième tour : KESTREL-58, 770 et 1 810. Le budget de 10 s vaut pour
chaque tour, et la ligne retient le tour le plus lent. Le 17/10, N2-2a voyait 835 € masqué en
« carte bancaire » : un montant masqué fausse un calcul enchaîné, et ce cas le rendra visible.

**M6 — charge concurrente.** Deux paires de M4-off, chacune envoyée dans deux onglets à moins de
2 s d'écart. M6 n'isole l'effet de la concurrence que si ses deux cas de base sont OK ou PARTIEL
en séquentiel. Sinon le juge le dit, et le résultat ne s'impute pas à la scalabilité.

**M7 — conformité et identité.**
- M7-1 : qui es-tu, quel modèle, quel éditeur. Proche de N5-1, jugé sur le corps de la réponse.
- M7-2 : la question de N9-3, à comparer aux options relevées par M0-3 et à la vitrine
  (« ChatGPT, Claude, Gemini et Mistral »).
- M7-3 : où les données sont hébergées et traitées (nouveau).

## 5. Budgets et cadres d'exécution

| Classe | Budget | Abandon (3 ×) | D'où vient le chiffre |
|---|---|---|---|
| Chargement (M0-1, M0-2) | 3 s | 9 s | N0 |
| Configuration (M0-3, M0-4) | 10 s | 30 s | une action d'interface |
| Message court (M3, M7) | 10 s | 30 s | N1–N3 ; médiane du 17/10 12,4 s, p90 20,0 s |
| Message portant un document collé (M1-C) | 20 s | 60 s | classe « message N9-1, N9-2 » du débriefing : `detect_pii` jusqu'à 11 s avec données identifiantes ; budget « document de moins de 5 pages » des oracles |
| Pièce jointe (M1-J, M2) | 20 s ; 60 s pour M2-9 (9 pages) | 60 s ; 180 s | oracles v1 |
| Recherche web (M4, M6) | 15 s | 45 s | `web_search` du 17/10 : médiane 3,7 s, max 8,7 s, en plus d'un message |
| Conversation longue (M5) | 10 s par tour | 30 s par tour | message court, huit fois |

La marge du chien de garde est mince : N3-3 a fini à 22,9 s pour un abandon à 30 s. Les budgets
restent ceux de la classe. Un dépassement donne PARTIEL, un abandon KO disponibilité.

| Pas | Actes sur le chemin complet | `max_actions` (3 × actes + 10) | `timeout_s` |
|---|---|---|---|
| M0 | 13 | 49 | 1800 |
| M1 | 112 | 346 | 2700 |
| M2 | 54 | 172 | 2700 |
| M3 | 50 | 160 | 1800 |
| M4 | 44 | 142 | 1800 |
| M5 | 8 | 34 | 2700 |
| M6 | 10 | 40 | 1800 |
| M7 | 17 | 61 | 1800 |

M1 a 2700 s parce que c'est le plus gros niveau : 24 cas, dont 12 en pièce jointe.

## 6. Bases du delta

Cette recette a **deux** bases. Le rapport du 17/10 vaut pour toutes les ENF : performance 2,
véracité 2, disponibilité 2, sécurité 2, conformité 2, scalabilité 1, avec les réserves du
débriefing. Le rapport du 13/10 vaut pour les lignes **pièces jointes** et **masquage**, que le
17/10 n'a pas pu observer : performance 2, véracité 3, disponibilité 1, sécurité 2,
conformité 3, scalabilité 1.

| Ligne du rapport | 13/10 | 17/10 | Mesurée ici par |
|---|---|---|---|
| Pièces jointes | 11/11 puis 3/3 « Failed », base documentaire injoignable, message parti sans le fichier | 10/10 KO, puce jamais observée | M2, M1-J |
| Empans de masque | faux et reproductibles : IBAN, SIREN, NIR, montants, dates ISO ; « rie Dupont » en clair ; IBAN restitué faux | non vérifié | M1 (débordements, restitution) ; M1-x2 et M5 (montants) |
| Masquage incomplet et instable | organisations, adresses, lieux de naissance, signatures, champs JSON en clair ; même texte, deux résultats | non vérifié | M1 (fuites ; a contre b) |
| Plaque, e-mail obfusqué | non détectés | non testé | M1-x6, M1-x4 |
| Latence d'un message court | 4,7–5,0 s web off | 13/16 hors budget, web auto | M4 (off contre auto), M0-4 |
| Multi-modèles | 2 505 € juste sur quatre modèles | N5-1 KO sur le corps | M3, M7-1 |
| Vitrine contre réalité | ni GPT ni Mistral | N9-3 KO sur le corps | M7-2, M0-3 |
| Charge concurrente | détecteur injoignable à deux traitements | N8-4 sans information | M6 |
| Contexte | multi-tours juste, tour perdu après un échec | N2-2b KO (calcul) | M5 |
| Hébergement des données | — | — | M7-3, nouveau |

`inputs_sha256` change par construction : oracles v3 et cas M ajoutés. `ui_profile_sha256`
(proposition 9 du débriefing) n'existait pas le 17/10. Le delta déclare les deux, puisqu'une
différence d'empreinte veut dire que les règles ou la grille d'adresses ont changé, pas le
produit.

## 7. Leçons ajoutées (delta de `PROCEDURE.md` §8)

| Situation | Règle |
|---|---|
| Configuration du compositeur non relevée (web auto contre off) | épingler modèle et mode web au début de chaque niveau, les journaliser, ne comparer qu'à configuration égale |
| Bannière du modèle lue avec la réponse | juger le corps seul ; la bannière est un champ à part, comparé au modèle choisi |
| Pièce jointe « jointe » en 0 s | attendre l'état prêt de la puce ; une question partie avant n'est pas un test de pièce jointe |
| Charge concurrente sur des cas déjà KO seuls | ne mesurer la concurrence que sur des cas qui passent seuls |
| Identifiants fictifs | clé fausse par construction ; juger « en clair » à part de « mal typé » |
| Même donnée collée ou jointe | deux chemins de masquage : tester les deux canaux |
| Question qui ne fait pas recopier la donnée | la restitution n'est pas testée : faire recopier la valeur sensible |

## 8. Ce que le pilote v2 doit fournir

Ces points sont des hypothèses de cette recette. Le pilote v2 les tient, ou les cas et la recette
s'ajustent à la fusion.

**À la fusion dans le pilote v2 (2026-10-17).** Les douze points sont tenus, avec trois précisions :
le texte d'un cas de configuration `model` est « options : A | B | C ; choisi : X ; relu : Y » (le
motif de M0-3 lit toujours `choisi : …`) ; `poser_question` sur un cas de configuration ne
renvoie que l'observation `@model` ou `@web`, sans rien envoyer ; en pièce jointe, `sent.json` porte
`document_in_sent` (vrai, faux, ou inconnu pour un format que le driver ne lit pas en texte), et
la fuite n'est vérifiée que s'il est vrai. Chaque ligne porte `config` (modèle et mode web lus sur
le compositeur, bannière lue avec la réponse) et `sent_sha256` (point 9). Un libellé de modèle qui
désigne plusieurs options sans égalité exacte (« gemini » devant deux Gemini) est refusé, et
l'observation `@model` liste les candidats ; `max_action_ms` vaut 360 s (point 7).

1. `corpus-v3/oracles-v3.json` dans les fichiers d'oracles du driver et dans `inputs_sha256`.
2. Les sorts `model`, `web` et `long`, décrits dans les notes de `cases-2.json`.
   - `poser_question` accepte les cas `model` et `web`.
   - Un cas `model` ou `web` sans question se juge sur l'observation `@model` ou `@web`, avec
     les textes du §4.
   - Un cas `long` lit `messages` et `turns`. Son `timing_s` est le tour le plus lent.
3. Le résultat de `choisir_modele` porte `options`, les libellés du sélecteur dans l'ordre
   affiché. Le libellé « claude haiku 5-5 » se reconnaît sans tenir compte de la casse ni des
   séparateurs.
4. Le verdict d'un cas `model` avec question compare la bannière (`guard.json`) au modèle choisi.
   Chaque ligne porte le modèle et le mode web lus.
5. `charge_concurrente` applique le mode web épinglé dans ses deux onglets et le relève pour
   chacun.
6. Persistance des réglages. Si le modèle ou le mode web reviennent à leur défaut à chaque
   `nouvelle_conversation`, celle-ci réapplique la configuration épinglée. Sinon, il faut un
   épinglage par cas et des `max_actions` plus hauts.
7. Durée de `conversation_longue` : huit tours à 30 s d'abandon font 240 s, auxquels chaque tour
   ajoute ≈ 5 s (lecture du popover « Chiffré », affichage immobile 2 s), soit ≈ 280 s, plus la
   marge de 8 s que le driver garde pour écrire ses preuves. Le `max_action_ms` du manifeste
   (`drivers/maskai-recette/manifest.json`) vaut donc 360 s, et le délai d'outil MCP du harnais
   au moins 390 s (runbook §7.0). Un tour coupé par l'échéance de l'action, avant 3 × le budget,
   est journalisé NON TESTÉ, jamais KO disponibilité.
8. `sent.json` en pièce jointe : dire si le document y figure. Sinon, la fuite se déclare non
   vérifiable sur M1-J et M2.
9. `replay_of` : le pilote compare les textes envoyés de a et b, ou la ligne en porte assez pour
   que le juge le fasse (texte envoyé ou son empreinte).
10. `drivers/maskai-recette/tests/test_recipe_cases.py` doit lire les identifiants M
    (`M\d-[A-Z]?\d+[a-z]?`) et les niveaux M (`^Niveau ([NM]\d) `). Le bloc de règles des
    instructions est identique, octet pour octet, à celui de la recette v1.
11. `scripts/recette/evaluer.py` : `BROKEN_TOKEN` signale à tort `CARD_1>` dans un jeton
    `<CREDIT_CARD_1>` bien formé. Il faut exclure `_` de son regard arrière.
12. Empreinte du driver. `cases-2.json` est dans le dossier haché du driver : sur cette branche
    seule, l'empreinte passe de `ea7336c6…` à `4bcdd498…` et ne correspond plus au
    `files_sha256` des deux manifestes (le test `test_the_manifests_carry_this_directory_hash`
    échoue, et le montage serait refusé). Le pilote v2 fusionne ces cas dans `cases.json`, retire
    `cases-2.json` et régénère `files_sha256`, ce qu'il doit faire de toute façon pour
    `driver.py`. Cette branche ne se fusionne donc pas seule.

## 9. Réglages remis et traçabilité

- M3 remet le modèle claude haiku 5-5 et M4 la recherche web off, le réglage de la recette.
- M7 remet la recherche web en auto, l'état trouvé le 17/10. Si M0-4 a relevé un autre état, le
  fondateur le remet à la main, et le rapport le note.
- Les 59 conversations créées ne sont pas supprimées. Le rapport les liste par niveau.
- Une recette arrêtée avant M7 laisse la recherche web off : le rapport le dit dans
  « Traçabilité ».
