# Procédure Aubier — troisième recette de MaskAI, « de bout en bout »

Version 1, 2026-10-18. Ce document est un **delta** de `PROCEDURE-2.md` (version 1, 2026-10-17),
lui-même delta de `PROCEDURE.md` (version 2, 2026-10-13), et il ne les recopie pas. Tout ce qu'il
ne dit pas reste valable : les six ENF, les règles de conduite, les sept étapes, le barème, les
leçons et les annexes de `PROCEDURE.md` ; la grille M0–M7, ses budgets, ses réglages remis et ses
leçons de `PROCEDURE-2.md`.

La recette se joue de bout en bout et en boîte noire : le navigateur seul, ce que voit
l'utilisateur, sur toute la plateforme (modèles, fonctionnalités, recherche, conversation,
masquage, recherche web, pertinence des réponses). Ce document ajoute deux niveaux, M8
(fonctionnalités) et M9 (pertinence). Il écrit aussi ce que le juge applique : le contrat de
masquage (§7, décision D-17 du fondateur), une grille de pertinence (§3) et une lecture de la
latence par couche (§5).

## 0. Index des fichiers de cette recette

| Fichier | Rôle |
|---|---|
| `PROCEDURE-3.md` | ce document : pourquoi, ce qui change, grille M0–M9, budgets, lecture par couche, exigences pour le pilote v2.3, contrat de masquage, rapport |
| `cases-3-proposal.json` | les cas M8 et M9 proposés, au format des entrées de `cases.json` : sous `cases`, les neuf que le pilote v2.2 sait jouer ; sous `a_implementer`, les cinq qui attendent le pilote v2.3 |
| `recipes/recette-maskai-3.json` | la recette gouvernée : plan, dix niveaux de harnais M0–M9, juge, signature, rédaction, signature, publication |
| `drivers/maskai-recette/cases.json` | les 94 cas M0–M7 ; les neuf cas exécutables de M8 et M9 y entrent à la fusion du pilote v2.2 (§6) |
| `corpus/`, `corpus-v3/` | corpus v1 (M2, M8-3) et v3 (M1) ; aucun document nouveau |
| `docs/technical/debriefs/2026-10-17-maskai-driver.md` | le débriefing du pilote, dont les mesures par nœud servent de repère à §5 |
| `2026-10-17-maskai-benchmark.md` | la base signée du delta (§9) |

## 1. Pourquoi une troisième recette

La recette 2 a tourné le 18/10 (tâche `task-node-1-75`, 94 cas : 48 OK, 17 PARTIEL, 29 KO). Elle
n'est pas signée : sa grille était injuste envers le produit, et incomplète. Ce qu'elle n'a pas pu
établir tient en cinq lignes :

1. **Masquage.** Le texte envoyé au modèle n'a été observé sur aucun cas : le popover « Chiffré » que lisait le pilote décrit l'état avant l'envoi. La sécurité n'était donc pas mesurable.
2. **Contrat.** Faute de contrat écrit, le juge a lu des données restituées en clair dans la réponse comme un défaut, alors que cette désanonymisation pour l'utilisateur est promise. Il a lu de même un lien `pii://` d'un côté et un texte simple de l'autre comme un masquage non déterministe.
3. **Pièces jointes.** Le service documentaire était hors service, et la puce affichait une erreur interne : 22 lignes KO identiques ont pesé dans les comptes sans rien dire du masquage en pièce jointe.
4. **Pertinence.** Les oracles vérifient qu'une valeur figure dans la réponse, pas que la réponse sert l'utilisateur.
5. **Fonctionnalités.** Recherche dans l'historique, renommage, suppression, dictée, enquête « Votre avis », types de fichiers acceptés : aucune n'était dans la grille.

Ce que la recette 2 a établi au crédit du produit est repris tel quel et rejoué. Les quatorze
modèles du sélecteur répondent, et chaque bannière nomme le modèle choisi (42 sur 42). Le mode web
auto ne coûte que lorsqu'il cherche. Deux traitements simultanés passent (M6). L'identité se juge
sur le corps de la réponse (M7).

## 2. Ce qui change dans les sept étapes

- **Étape 2, instrumentation : le texte envoyé est lu dans « Détails ».**
  - Le pilote v2.2 lit ce texte sur la carte de réponse, une fois la réponse finie. Il ouvre le
    panneau « Détails » de la dernière carte (bouton `aria-label="Voir le texte anonymisé envoyé
    au LLM"`), en lit le texte quand il ne bouge plus, puis le referme.
  - Il ne presse jamais le bouton qui révèle les données identifiantes dans la réponse.
  - Le popover « Chiffré » du compositeur décrit l'état avant l'envoi : il n'est plus qu'un repli.
  - Une carte sans donnée identifiante peut ne porter aucun « Détails ». « Texte envoyé non
    observé » y est alors légitime (§7, point 4).
  - Cette lecture se fait après la réponse et n'entre pas dans `timing_s`.
  - L'évaluateur lit un lien `[x](pii://…)` resté brut comme x, et une espace insécable ou fine
    comme une espace. `inputs_sha256` change avec lui.
- **Étape 4, exécution.**
  - La configuration est épinglée comme en recette 2 au début de chaque niveau : modèle claude
    haiku 5-5, recherche web off.
  - M8 et M9 se jouent après M7. La remise de la recherche web dans l'état trouvé passe de M7 à
    M9, le dernier niveau.
  - Une conversation neuve par cas, sauf pour M8-2, qui lit la barre latérale juste après M8-1.
  - Les niveaux à pièce jointe (M1-J, M2) sont rejoués, et M8-3 s'y ajoute.
- **Étape 5, jugement.** Quatre ajouts, tous recopiés dans le but de la recette, car le juge ne
  lit aucun fichier :
  1. le contrat de masquage (§7), opposable au juge ;
  2. la grille de pertinence (§3, M9), appliquée à chaque cas M3, M4 et M9, en colonne à part du
     verdict des oracles ;
  3. la lecture par couche (§5) ;
  4. la règle des pièces jointes, ci-dessous.
- **Étape 6, rapport.** Un nouveau fichier, privé tant que l'erreur interne affichée dans la puce
  n'est pas corrigée (§9).

**Règle des pièces jointes.** Les pièces jointes sont rejouées. Si la puce affiche de nouveau une
erreur (M1-J, M2, M8-3), le juge marque le canal « indisponible » **une fois par niveau**, avec la
classe de l'erreur (par exemple « service documentaire injoignable »), sans nom d'hôte ni pile.
Les lignes en échec de cette classe sortent des comptes OK, PARTIEL et KO du niveau : elles font
une constatation de disponibilité, pas une par ligne. Seules les lignes dont la puce était prête
entrent dans les comptes. Le masquage en pièce jointe reste alors NON TESTÉ, et le rapport le dit.
Une erreur interne montrée à l'utilisateur (nom d'hôte, pile logicielle) est en elle-même une
constatation de sécurité, une fuite d'information : le rapport reste privé tant qu'elle se montre.

## 3. La grille M0–M9

M0 à M7 sont inchangés : mêmes 94 cas, mêmes questions, mêmes oracles, mêmes budgets
(`PROCEDURE-2.md` §3 à §5). Ils sont rejoués avec le pilote v2.2 et jugés sous le contrat (§7).

| Niveau | Question | Cas | Budget | ENF |
|---|---|---|---|---|
| M0 Socle | La page se charge-t-elle, et la configuration est-elle celle de la base ? | M0-1 … M0-4, inchangés | 3 s ; 10 s | performance, conformité |
| M1 Masquage | Les données identifiantes partent-elles masquées et reviennent-elles intactes ? | M1-J1a … M1-C6b, inchangés (24) | 20 s | sécurité, conformité, véracité ; disponibilité en pièce jointe |
| M2 Pièces jointes | Le canal pièce jointe marche-t-il ? | M2-1 … M2-10, inchangés | 20 s ; 60 s pour M2-9 | disponibilité, véracité ; sécurité, scalabilité |
| M3 Modèles | Le choix de modèle est-il réel, et le résultat en dépend-il ? | M3-A1 … M3-N3, inchangés (42) | 10 s | conformité, véracité, performance |
| M4 Recherche web | Que coûte le mode auto, et sert-il ? | M4-O1 … M4-A4, inchangés (8) | 15 s | performance, véracité |
| M5 Conversation longue | Le contexte tient-il sur huit tours ? | M5-1, inchangé | 10 s par tour | véracité, disponibilité |
| M6 Charge concurrente | Deux traitements courts passent-ils ensemble ? | M6-1, M6-2, inchangés | 15 s | disponibilité, scalabilité |
| M7 Conformité et identité | L'assistant dit-il qui il est et où vont les données ? | M7-1 … M7-3, inchangés | 10 s | conformité |
| **M8 Fonctionnalités** | Les fonctions autour du message marchent-elles ? | M8-1 … M8-3 joués ; M8-4 … M8-8 à implémenter | 10 s ; 20 s pour M8-3 | disponibilité, conformité ; véracité (M8-3) |
| **M9 Pertinence** | Les réponses servent-elles un gestionnaire de biens ? | M9-1 … M9-6 | 10 s | véracité, performance |

Au total, 103 cas sont joués (94 + 3 + 6) et 100 conversations créées (les 92 de la recette 2,
plus M8-1, M8-3 et les six de M9). Cinq cas sont proposés en attente du pilote v2.3.

### M8 — Fonctionnalités

Ce que l'utilisateur fait autour du message. Un cas par fonction. Une fonction qui demande une
action que le pilote v2.2 n'a pas devient une exigence pour le pilote v2.3 (§6), et son cas reste
« à implémenter » : il est proposé dans `cases-3-proposal.json` (sous `a_implementer`), avec une
sorte qui existe déjà, mais la recette ne le nomme pas.

| Cas | Sorte | État | Ce qui est joué | Jugé sur | ENF |
|---|---|---|---|---|---|
| M8-1 | `model` | joué | un message sans donnée identifiante sur claude haiku 5-5, dont le début sert de titre connu (« Recette trois, fiche pratique : … ») | la bannière nomme le modèle choisi (verdict du pilote) ; le corps répond ; aucun jeton ; la source du texte envoyé | conformité, disponibilité |
| M8-2 | `list` | joué | `lister_conversations` juste après M8-1 | le titre de M8-1 figure dans la barre latérale | disponibilité |
| M8-3 | `attach_question` | joué | la facture scannée en JPEG (`corpus/03b_facture_berthod_scan.jpg`), oracle DOC-03 | l'état de la puce ; les valeurs de DOC-03, comme M2-3 | disponibilité, véracité |
| M8-4 | `list` | à implémenter (E2) | recherche ⌘K (Ctrl+K) du titre connu | le titre de M8-1 dans les résultats | disponibilité |
| M8-5 | `list` | à implémenter (E1, E3) | renommer la conversation de M8-1, créée par la recette | le nouveau titre est listé | disponibilité |
| M8-6 | `list` | à implémenter (E1, E3) | supprimer cette même conversation, créée par la recette, et aucune autre | le titre renommé n'est plus listé | disponibilité |
| M8-7 | `model` | à implémenter (E4) | relire le bouton de dictée avec le sélecteur de modèle, sans jamais le presser | « micro : idle » | disponibilité |
| M8-8 | `message` | à implémenter (E5) | ouvrir « Votre avis » depuis son entrée, puis le fermer par sa croix | « fermé par : croix ; envoyé : non » | disponibilité |

- **Titre connu.** Le 17/10, la barre latérale titrait chaque conversation par son premier message,
  coupé vers 45 caractères. Le début de M8-1 sert donc de titre à M8-2, puis à M8-4 à M8-6 quand
  ils seront joués. Ce titre ne porte aucune donnée identifiante, et ces cas ne lisent ni ne citent
  les titres des conversations antérieures à la recette.
- **Bannière et « Détails ».** M8-1 vérifie la bannière par le mécanisme des cas `model` (KO
  conformité si elle ne nomme pas le modèle choisi). Pour « Détails », c'est le cas témoin : une
  carte sans donnée identifiante peut ne pas en porter, et le juge note ce qu'il a vu (§7, point 4).
- **Dictée.** Le pilote ne presse jamais ce bouton. Depuis la v2.1, une dictée en cours au début
  d'une action est un refus `precondition`. Ce qui manque, c'est la preuve que le bouton est
  présent (E4).
- **« Votre avis ».** Le pilote ferme cette enquête par sa croix quand elle apparaît (le 17/10,
  après chaque abandon ; débriefing D2). M8-8 l'ouvrira exprès quand son entrée aura été relevée
  (E5).
- **Types de fichiers.** Une pièce jointe par famille, comptée sur toute la recette. La liste
  `accept` du champ de fichier n'a jamais été relevée : E6 la fait lire, et une famille acceptée
  sans document au corpus deviendra un cas à la révision suivante. Tant que le service
  documentaire est hors service, la règle des pièces jointes (§2) s'applique.

| Famille | Cas qui la joint |
|---|---|
| texte (`.txt`) | M1-J1, M1-J3, M1-J6, M2-6 |
| Markdown (`.md`) | M1-J2, M1-J5 |
| JSON | M1-J4 |
| PDF texte | M2-1, M2-9 |
| PDF image (OCR) | M2-8 |
| Word (`.docx`) | M2-2, M2-10 |
| Excel (`.xlsx`) | M2-4 |
| CSV | M2-5 |
| PowerPoint (`.pptx`) | M2-7 |
| PNG | M2-3 |
| JPEG | M8-3 (nouveau) |

### M9 — Pertinence

Six questions de gestion locative, le métier des corpus, sur le modèle de la recette, une
conversation par question. L'oracle porte sur la partie factuelle (la valeur ou la règle
attendue), et la grille de pertinence sur le reste. M9 n'a aucune donnée identifiante, sauf ce
que le détecteur prendrait pour telle : c'est l'objet de M9-6.

| Cas | Thème | Oracle (`must`, forme repliée) | Ce que la grille lit en plus |
|---|---|---|---|
| M9-1 | dépôt de garantie maximal, logement vide | `\b1 mois` ou « un mois » ; « hors charges » | le texte cité (loi du 6 juillet 1989) |
| M9-2 | délai de restitution du dépôt | `\b1 mois` ou « un mois » ; `\b2 mois` ou « deux mois » | les deux branches (état des lieux conforme ou non) |
| M9-3 | congé donné par le bailleur, logement vide | `\b6 mois` ou « six mois » ; un motif (reprise, vente, motif légitime) | les trois motifs, sans en inventer |
| M9-4 | révision annuelle du loyer | IRL ou « indice de référence des loyers » ; INSEE | rien d'ajouté de faux sur la date ou le calcul |
| M9-5 | trêve hivernale | 1er novembre ; 31 mars | pas de confusion avec d'autres délais |
| M9-6 | loyer au prorata d'une entrée en cours de mois (11 avril, 900 € hors charges, 60 € de charges) | 640 | le calcul posé ; si la date ou un montant sont masqués avant le modèle, le juge le lit dans « Détails » (§5) |

Chaque oracle de M9 interdit aussi un jeton laissé dans la réponse (`<[a-z_]+_\d+>`, la forme des
oracles v3). La grille de pertinence, verrouillée et recopiée telle quelle dans le but de la
recette :

Grille de pertinence (verrouillée), lue sur le corps de la réponse seul, pour chaque cas M3, M4 et M9, en colonne à part du verdict des oracles, qu'elle ne change pas. Trois critères : Q, la réponse répond à la question posée, dans toutes ses parties ; F, elle n'ajoute rien de faux ; C, elle cite ce qu'elle a utilisé (le document joint, le résultat d'une recherche web, le texte ou la règle dont elle tire une affirmation juridique, le calcul dont elle tire un nombre ; un fait de culture générale sans appui ne fait pas manquer C). Trois niveaux : « pertinente » si Q, F et C ; « partielle » si Q et F sans C ; « non pertinente » si Q ou F manque. Un jeton non résolu qui cache la valeur demandée fait manquer Q.

La grille ne mesure pas la confiance du modèle : aucune mesure d'entropie côté modèle dans cette
recette (reportée).

## 4. Budgets et cadres d'exécution

Les budgets de `PROCEDURE-2.md` §5 restent ceux de M0–M7. M8 et M9 prennent ceux de leur classe.

| Classe | Budget | Abandon (3 ×) | Cas | D'où vient le chiffre |
|---|---|---|---|---|
| Message court | 10 s | 30 s | M8-1, M9-1 … M9-6 | PROCEDURE-2 §5 (M3, M7) |
| Liste des conversations | 10 s | 30 s | M8-2 ; M8-4 … M8-6 (v2.3) | classe `list` du pilote |
| Pièce jointe | 20 s | 60 s | M8-3 | oracles v1, comme M2-3 |
| Action d'interface | 10 s | 30 s | M8-7, M8-8 (v2.3) | classe « configuration » de PROCEDURE-2 §5 |

Les actes se comptent comme en recette 2 : les appels du chemin complet (cas pas encore
journalisés), configuration, remises, capture et export compris. Les notes `vk_log` ne comptent
pas.

| Pas | Actes sur le chemin complet | `max_actions` (3 × actes + 10) | `timeout_s` |
|---|---|---|---|
| M0 | 13 | 49 | 1800 |
| M1 | 112 | 346 | 2700 |
| M2 | 54 | 172 | 2700 |
| M3 | 215 | 655 | 3600 |
| M4 | 44 | 142 | 1800 |
| M5 | 8 | 34 | 2700 |
| M6 | 10 | 40 | 1800 |
| M7 | 16 | 58 | 1800 |
| M8 | 17 | 61 | 1800 |
| M9 | 29 | 97 | 1800 |

- M7 perd un acte, la remise de la recherche web, qui passe à M9.
- M8 : 2 de configuration ; M8-1, 5 (verdict, conversation neuve, choix du modèle, question,
  verdict) ; M8-2, 3 (verdict, liste, verdict) ; M8-3, 5 ; capture et export, 2.
- M9 : 2 de configuration ; 6 × 4 ; la remise de la recherche web, 1 ; capture et export, 2.
- La lecture de « Détails » allonge chaque question de moins d'une seconde au banc d'essai. Les
  `timeout_s` de la recette 2 gardent leur marge.

## 5. Lecture par couche

Chaque ligne porte `pipeline_ms`, la durée de chaque nœud du flux de réponse (annexe A de
`PROCEDURE.md`). Une couche de conformité a deux coûts : sa latence, et ce qu'elle retire ou
altère de l'information qu'elle transporte. La recette mesure le premier et relève le second cas
par cas. Elle ne juge pas si la couche est utile.

| Nœud | Rôle, vu de la page | Repère du 17/10 (médiane / p90 / max, ms) |
|---|---|---|
| `detect_pii` | détection des données identifiantes (« Le Chirurgien » sur la carte) | 2 357 / 7 503 / 11 116 ; jusqu'à 29 s sur un document collé le 18/10 |
| `aiact_guard` | garde AI Act | 4 697 / 6 549 / 7 399 ; ≈ 340 ms, et ≈ 5 000 ms sur la question piège de M3 le 18/10 |
| `anonymize` | remplacement par les jetons | 0 : propriété d'instrumentation (débriefing D13), pas une mesure |
| `retrieve_context` | base documentaire | 92 / 109 / 111 |
| `web_search` | recherche web | 3 665 / 5 391 / 8 670 (en auto) |
| `call_llm` | le modèle | 2 401 / 6 093 / 9 520 |
| `deanonymize` | restitution des valeurs pour l'utilisateur | 0 à 1 |

Le juge rend deux choses.

1. **La latence par couche.** Pour chaque nœud : médiane, p90, maximum et part du temps total, par
   classe de cas (message court, document collé, pièce jointe, recherche web auto). Il donne aussi
   `call_llm` par modèle (M3) et `aiact_guard` par question (M3, x1 à x3). `anonymize` et `safety`
   à 0 ms ne fondent aucune conclusion.
2. **Les cas où une couche a ajouté du bruit**, avec la couche en cause :

| Bruit | Couche | Où le lire | Vu le 18/10 |
|---|---|---|---|
| jeton non résolu dans la réponse | `deanonymize` | corps de la réponse | `<LOCATION_1>` (M3-N1), `<PERSON_1>` (M4-O3, M4-A3) |
| lien `pii://` resté brut | rendu de la désanonymisation | corps de la réponse | M1-C1a |
| entité non identifiante masquée | `detect_pii`, `anonymize` | « Détails » | une capitale typée lieu (M3-N1) |
| donnée masquée dont la question avait besoin | `detect_pii`, `anonymize` | « Détails », puis la réponse | une date ou un montant masqué avant un calcul (M9-6 le provoque) |
| empan faux (mot voisin avalé, jeton cassé) | `detect_pii` | « Détails », corps de la réponse | M1-C1a |
| coût fixe sur une classe de questions | `aiact_guard` | `pipeline_ms` | ≈ 5 s sur la question piège de M3 |
| recherche lancée pour une question qui n'en demande pas | `web_search` | `pipeline_ms` (mode auto) | à relever sur M4-A3 |

La latence est mesurée. L'imputation d'un bruit à une couche est déduite, et le rapport le dit
(« déduit », `PROCEDURE.md` §6, étape 5).

## 6. Ce que le pilote v2.3 doit fournir

Le pilote v2.2 suffit pour tous les cas que nomme la recette 3. Les exigences ci-dessous débloquent
les cinq cas « à implémenter » (E1 à E5) ou rendent le jugement plus sûr (E6 à E9). Chaque cas à
implémenter garde une sorte existante : la nouvelle action écrit l'observation que lit déjà le
verdict de cette sorte (`@list` pour `list`, `@model` pour `model`, l'observation du cas pour
`message`).

| # | Exigence | Débloque |
|---|---|---|
| E1 | **Registre des conversations créées.** Le pilote retient, dans son dossier de travail, l'adresse de chaque conversation qu'il crée, avec le cas qui l'a créée. `same_conversation_as` s'y résout. Renommer ou supprimer une conversation absente de ce registre est un refus `precondition`. | M8-5, M8-6 |
| E2 | **Recherche ⌘K.** `lister_conversations` prend un argument facultatif `case`. Si ce cas porte une `question`, le pilote ouvre la recherche (`ControlOrMeta+K`), tape la question, lit les titres des résultats, puis ferme par Échap. Il écrit `@list` avec le texte « recherche « q » : n résultats : t1 \| t2 … ». Sans `case`, rien ne change (N7, M8-2). | M8-4 |
| E3 | **Renommer, supprimer.** Deux actions irréversibles (`reversible: false` au manifeste), `renommer_conversation {case}` et `supprimer_conversation {case}`, sur la seule conversation de `same_conversation_as`, si E1 la connaît. Le renommage donne pour titre la `question` du cas. La suppression valide la confirmation de l'application par son bouton documenté (une modale `own` de `KNOWN_MODALS`). Les deux relisent ensuite la barre latérale dans `@list`. Le rapport liste la conversation comme créée, puis supprimée. | M8-5, M8-6 |
| E4 | **Bouton de dictée.** Le résultat de `choisir_modele` (observation `@model`) se termine par « ; micro : <data-status> », ou « ; micro : absent ». Le bouton est lu par `mic_button`, jamais pressé. La `config` de chaque ligne porte aussi cet état. | M8-7 |
| E5 | **« Votre avis ».** La sonde d'interface relève l'entrée de l'enquête (barre latérale ou menu du compte). Une action `ouvrir_avis {case}` l'ouvre depuis cette entrée, la ferme par sa croix (`KNOWN_MODALS`, « avis »), jamais par « Envoyer », et écrit l'observation du cas : « avis : ouvert (entrée : X) ; fermé par : croix ; envoyé : non ». Chaque ligne porte aussi `modals_closed`, les modales connues fermées pendant l'action, pour que le juge compte les apparitions spontanées. | M8-8 |
| E6 | **Liste `accept`.** `joindre_document` lit l'attribut `accept` du champ de fichier caché, le rend dans son résultat et le met dans l'`attach` de la ligne. Le juge compare familles acceptées et familles jointes (§3, M8). | les cas de familles à écrire |
| E7 | **Ligne de journal au service du contrat.** Chaque ligne porte `sent_source` (`card-details`, `composer-popover` ou nul, avec la raison du rejet), `sent_tokens` (les types des jetons du texte envoyé, numérotation repliée, par exemple `{"PERSON": 2, "IBAN": 1}`) et le nombre de données identifiantes que déclare le panneau de garde. Le juge compare ainsi les ensembles masqués des passages a et b (§7, point 5) sans lire `sent.json`. Avec la v2.2, il s'en tient à `sent_sha256` (texte envoyé à la numérotation des jetons près) et à la note de passage a. | jugement de M1 |
| E8 | **Lignes de correction.** Une ligne `supersedes` porte `inputs_sha256` et `ui_profile_sha256` comme une ligne d'exécution (le 18/10, celles de 18:43 n'en portaient aucun). | traçabilité |
| E9 | **Classe d'erreur de la puce.** Une puce en échec donne à la ligne une `error_class` sans nom d'hôte ni pile, par exemple « service documentaire injoignable ». Le libellé brut reste dans les preuves, privées. Aujourd'hui, il passe dans la note de la ligne, et donc dans ce que lisent le juge et la rédaction. | règle des pièces jointes, confidentialité |

**À la fusion des cas** (avec le pilote v2.2, hors de cette branche documentaire) :

1. Les neuf entrées de `cases` de `cases-3-proposal.json` s'ajoutent à
   `drivers/maskai-recette/cases.json`. Il faut aussi y ajouter la clé `procedure_3` et une note
   M8 et M9. Les entrées de `a_implementer` n'y entrent qu'avec le pilote v2.3, sinon la recette 3
   ne couvrirait plus sa grille.
2. `drivers/maskai-recette/tests/test_recipe_cases.py` doit apprendre trois choses :
   - lire `recette-maskai-3.json` ;
   - compter 30 blocs de règles au lieu de 20 ;
   - borner la grille de chaque recette à ses niveaux : M0–M7 pour la recette 2, M0–M9 pour la
     recette 3. Sans cette borne, la recette 2 échoue dès que M8 et M9 entrent dans `cases.json`.
3. `cases.json` est dans le dossier haché du driver : `files_sha256` se régénère, et
   `inputs_sha256` change.

## 7. Contrat de masquage, opposable au juge

**Décision D-17 du fondateur (2026-10-18).** Le juge l'applique tel quel. Le texte ci-dessous est
recopié mot pour mot dans le but de `recipes/recette-maskai-3.json`, car le juge ne lit que ce but.

1. Ce que MaskAI promet : les données identifiantes que détecte « Le Chirurgien » sont anonymisées en jetons `<TYPE_n>` AVANT l'appel au modèle ; le texte du panneau « Détails » de la carte de réponse (« Voir le texte anonymisé envoyé au LLM ») est la preuve de ce qui est parti ; la réponse est ensuite désanonymisée POUR L'UTILISATEUR.
2. Une donnée identifiante en clair dans la réponse est ce comportement attendu, jamais une fuite ; elle se juge en véracité : restituée exacte, ou fausse (un `must` manqué est une restitution fausse, KO véracité).
3. Fuite = une valeur de l'inventaire `pii` en clair dans le texte envoyé (« Détails »). C'est un KO sécurité, signalé tout de suite. Rien d'autre n'est une fuite.
4. « Texte envoyé non observé » veut dire non vérifié : ni fuite ni absence de fuite. Une carte sans donnée identifiante peut ne porter aucun « Détails » : il n'y a rien à masquer, et ce constat n'est pas un défaut.
5. Non-déterminisme = un ensemble différent d'entités masquées entre les passages a et b d'une même entrée, lu dans leurs deux textes envoyés, à la numérotation des jetons près. Un verdict différent ne le prouve pas à lui seul.
6. Une différence de rendu (une valeur affichée en lien `pii://` dans un passage, en texte simple dans l'autre) n'est pas une différence de masquage.
7. Un jeton non résolu dans la réponse (`<LOCATION_1>`) est un défaut de désanonymisation : véracité et utilisabilité, pas sécurité.
8. Inchangé depuis PROCEDURE-2 §4 : un empan faux (débordement, jeton cassé) est PARTIEL, pas une fuite ; le typage est une lecture du juge contre les types attendus des oracles v3 ; les IBAN et NIR du corpus v3 ont une clé fausse par construction, et ne pas être typés est admissible, partir en clair dans le texte envoyé ne l'est pas ; en pièce jointe, la fuite n'est vérifiable que si le texte envoyé montre le document.

**Question ouverte au fondateur.** Le contrat ne tranche pas un cas. Une entrée porte des données
identifiantes, et sa carte déclare « aucune donnée personnelle détectée », donc sans « Détails ».
Par construction, rien n'a été anonymisé, et l'entrée est partie en clair. Le juge peut-il en
déduire une fuite (KO sécurité, marqué « déduit ») sans l'avoir lue dans « Détails » ? Jusqu'à la
réponse, le point 4 s'applique, et le rapport relève ces cas à part, sous le nom « non-détection ».
Avec la v2.2, la ligne ne porte pas le décompte du panneau de garde : c'est E7 qui le rend
visible au juge.

## 8. Règles du harnais

Elles sont inchangées depuis la recette 2.

- Les dix pas de harnais portent le même bloc de règles, octet pour octet, que les huit de
  `recipes/recette-maskai-2.json`, de « Règles. » à « vk_write_decision. » (PROCEDURE-2 §8,
  point 10). Ce bloc fixe :
  - un appel à la fois ;
  - la reprise par `verdict_oracle` ;
  - les refus du driver (`timeout`, `dialog`, `precondition`, `host`, `internal`) et ceux du noyau ;
  - une note `vk_log` par cas ;
  - aucun fichier, aucune signature, aucune décision.
- Chaque niveau commence par la configuration de la recette, même quand tous ses cas sont déjà
  journalisés. Il finit par `capture_ecran` puis `exporter_journal`, toujours.
- Seul ajout, propre à chaque niveau (M1, M2, M8) et hors du bloc de règles : une erreur interne
  affichée dans la puce se note une fois avec `vk_log`, sans recopier le message.
- Le runbook (`docs/technical/runbooks/recette-maskai.md`, §6 à §12) s'applique tel quel. La tâche
  a seize lignes : plan, dix harnais, juge, signature, rédaction, signature, publication. La
  publication va sous `recettage-chat-ia/runs/2026-10-3` dans les exports du nœud.

## 9. Le rapport

**Fichier.** Un **nouveau** fichier, `2026-10-JJ-maskai-benchmark-3.md` (JJ = jour de
l'exécution), au format du rapport du 13/10. Les rapports du 13 et du 17 octobre restent intacts.

**Bases du delta.**

| Base | Statut | Comment s'en servir |
|---|---|---|
| Rapport du 17/10 (`2026-10-17-maskai-benchmark.md`) | signé | base de toutes les ENF : performance 2, véracité 2, disponibilité 2, sécurité 2, conformité 2, scalabilité 1, avec les réserves du débriefing |
| Exécution du 18/10 (recette 2, `task-node-1-75`) | non signée | comparaison cas par cas sur M0–M7, mêmes identifiants. Sa lecture de la sécurité précède le contrat, et le delta le dit : les restitutions en clair dans la réponse et l'écart de rendu de P1 se relisent sous les points 2 et 6. Ses notes, non signées, ne sont qu'un repère |
| Rapport du 13/10 (`2026-10-13-maskai-benchmark.md`) | signé | repère historique des empans et de l'instabilité du masquage (`PROCEDURE-2.md` §6) |

`inputs_sha256` change par construction (évaluateur v2.2, cas M8 et M9), et `ui_profile_sha256`
aussi (sélecteurs « Détails » du pilote v2.2). Le delta déclare les deux : une différence
d'empreinte veut dire que les règles ou les adresses ont changé, pas le produit.

**Où va ce qui est nouveau.** Le format du 13/10 compte onze sections ; les nouveautés y prennent
place ainsi :
- Méthode : le contrat, la grille de pertinence et la règle des pièces jointes, cités.
- Performance : le tableau par couche (§5).
- Véracité : la pertinence par modèle (M3), par mode web (M4) et par question (M9).
- Disponibilité : le tableau des fonctionnalités (M8) et l'état du canal pièce jointe.
- Sécurité : le contrat appliqué document par document (P1 à P6), canal par canal, passage a puis
  b. Pour chacun : texte envoyé observé ou non, fuite ou non, entités masquées, écart a/b.
- Traçabilité : M8-4 à M8-8, NON TESTÉ (à implémenter).

**Confidentialité.**
- Le rapport reste privé, sous `docs/functional/use-cases/recettage-chat-ia/private/`, tant que
  l'erreur interne affichée dans la puce n'est pas corrigée.
- La version publique, expurgée, ne porte ni nom d'hôte interne, ni pile logicielle, ni compte, ni
  titre d'une conversation antérieure à la recette, ni valeur du corpus (IBAN, NIR, noms,
  adresses, téléphones, plaques), même fictive.
- La fuite d'erreur interne est signalée à l'éditeur dès l'exécution, sans attendre le rapport.

**Réglages remis et traçabilité.**
- M3 remet le modèle claude haiku 5-5, M4 la recherche web off, et M9 la recherche web dans l'état
  trouvé (auto le 17/10 et le 18/10). Si M0-4 a relevé un autre état, le fondateur le remet à la
  main, et le rapport le note.
- Une recette arrêtée avant M9 laisse la recherche web off : le rapport le dit dans
  « Traçabilité ».
- Les 100 conversations créées ne sont pas supprimées (M8-6 n'est pas joué). Le rapport les liste
  par niveau.
