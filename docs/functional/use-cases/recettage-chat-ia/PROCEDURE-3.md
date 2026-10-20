# Procédure Aubier — troisième recette de MaskAI, « de bout en bout »

Version 2, 2026-10-19, revue le 20/10 (la version 1 est du 18/10 ; la version 2 suit le pilote v2.3,
qui joue M8-4 à M8-8 et livre le §6 ; la revue du 20/10 en a durci les gardes avant le passage réel,
§6). Ce document est un **delta** de `PROCEDURE-2.md` (version 1, 2026-10-17),
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
| `cases-3-proposal.json` | les quatorze cas M8 et M9, au format des entrées de `cases.json`, tous entrés dans `cases.json` : neuf avec le pilote v2.2, cinq avec le pilote v2.3 |
| `recipes/recette-maskai-3.json` | la recette gouvernée : plan, dix niveaux de harnais M0–M9, juge, signature, rédaction, signature, publication |
| `drivers/maskai-recette/cases.json` | les 108 cas M0–M9 (94 + 8 + 6) ; M8-4 à M8-8 y sont entrés avec le pilote v2.3 (§6) |
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
  - Il ne presse jamais le bouton qui révèle les données identifiantes dans la réponse. Depuis le
    neuvième tour, il ne presse, ne survole ni ne focalise jamais non plus les puces des jetons ni le
    résumé des étapes de la carte.
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
  - Une conversation neuve par cas qui pose une question. M8-2 et M8-4 à M8-8 n'en ouvrent pas : ils
    lisent ou modifient la barre latérale, le compositeur ou l'enquête, autour de la conversation de M8-1.
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
| **M8 Fonctionnalités** | Les fonctions autour du message marchent-elles ? | M8-1 … M8-8 (M8-4 … M8-8 avec le pilote v2.3) | 10 s ; 20 s pour M8-3 | disponibilité, conformité ; véracité (M8-3) |
| **M9 Pertinence** | Les réponses servent-elles un gestionnaire de biens ? | M9-1 … M9-6 | 10 s | véracité, performance |

Au total, 108 cas sont joués (94 + 8 + 6) et 100 conversations créées (les 92 de la recette 2,
plus M8-1, M8-3 et les six de M9). M8-6 en supprime une, celle de M8-1 : la seule conversation que
la recette supprime.

### M8 — Fonctionnalités

Ce que l'utilisateur fait autour du message. Un cas par fonction. Les fonctions que le pilote v2.2
ne savait pas jouer ont fait les exigences du pilote v2.3 (§6), livré le 19/10 : leurs cinq cas,
M8-4 à M8-8, gardent une sorte qui existe déjà, nomment l'action qui les joue (champ `action`, la
seule qui le peut) et sont joués.

| Cas | Sorte | État | Ce qui est joué | Jugé sur | ENF |
|---|---|---|---|---|---|
| M8-1 | `model` | joué | un message sans donnée identifiante sur claude haiku 5-5, dont le début sert de titre connu (« Recette trois, fiche pratique : … ») | la bannière nomme le modèle choisi (verdict du pilote) ; le corps répond ; aucun jeton ; la source du texte envoyé | conformité, disponibilité |
| M8-2 | `list` | joué | `lister_conversations` juste après M8-1 | le titre de M8-1 figure dans la barre latérale | disponibilité |
| M8-3 | `attach_question` | joué | la facture scannée en JPEG (`corpus/03b_facture_berthod_scan.jpg`), oracle DOC-03 | l'état de la puce ; les valeurs de DOC-03, comme M2-3 | disponibilité, véracité |
| M8-4 | `list` | joué (v2.3, E2) | recherche ⌘K (Ctrl+K) du titre connu, `lister_conversations` M8-4 | le titre de M8-1 parmi les résultats (l'oracle ne lit que les titres trouvés, après « résultats : ») ; un résultat mène à une conversation (son lien, son identifiant ou son titre listé), jamais un élément qui répète la requête ; une liste que la frappe n'a pas changée est « résultats inchangés par la frappe », KO (revue du 20/10, deuxième tour) | disponibilité |
| M8-5 | `list` | joué (v2.3, E1, E3) | renommer la conversation de M8-1, créée par la recette, `renommer_conversation` M8-5 | le nouveau titre, relu sur cette conversation à son adresse (« titre relu « Recette trois renommée » ») ; jamais lu dans la liste entière, où une autre conversation peut porter ce titre (revue du 20/10) ; le titre relu tenu 2 s, attendu jusqu'à 3 × le budget depuis Entrée, et ce délai est le `timing_s` de la ligne (deuxième tour) | disponibilité (sécurité si une autre conversation disparaît) |
| M8-6 | `list` | joué (v2.3, E1, E3) | supprimer cette même conversation, créée par la recette, et aucune autre, `supprimer_conversation` M8-6 | cette conversation, à son adresse, n'est plus listée (« plus listée », jamais « encore listée ») ; jamais jugé sur la liste entière (revue du 20/10) ; deuxième tour : « plus listée » seulement après une requête d'écriture acceptée (2xx), l'absence tenue 2 s, dans 3 × le budget (`timing_s`) ; une autre conversation disparue pendant l'action : KO sécurité (`others_gone`, leur nombre seul) | disponibilité (sécurité si une autre conversation disparaît) |
| M8-7 | `model` | joué (v2.3, E4) | relire le bouton de dictée avec le sélecteur de modèle, sans jamais le presser | « micro : idle » | disponibilité |
| M8-8 | `message` | joué (v2.3, E5) | ouvrir « Votre avis » depuis son entrée, puis le fermer par sa croix, `ouvrir_avis` M8-8 | « fermé par : croix ; envoyé : non » | disponibilité |

- **Titre connu.** Le 17/10, la barre latérale titrait chaque conversation par son premier message,
  coupé vers 45 caractères. Le début de M8-1 sert donc de titre à M8-2, puis à M8-4 à M8-6. Ce
  titre ne porte aucune donnée identifiante, et ces cas ne citent aucun titre d'une conversation
  antérieure à la recette, hors ce que la barre latérale et la recherche listent. M8-5 et M8-6
  n'agissent que sur la conversation que M8-1 a créée : le pilote la tient dans son registre (E1)
  et la retrouve par son adresse, jamais par son rang ni par son titre, qu'une conversation de la
  première exécution du 18/10 partage.
- **Bannière et « Détails ».** M8-1 vérifie la bannière par le mécanisme des cas `model` (KO
  conformité si elle ne nomme pas le modèle choisi). Pour « Détails », c'est le cas témoin : une
  carte sans donnée identifiante peut ne pas en porter, et le juge note ce qu'il a vu (§7, point 4).
- **Dictée.** Le pilote ne presse jamais ce bouton. Depuis la v2.1, une dictée en cours au début
  d'une action est un refus `precondition`. Depuis la v2.3, son état est relu avec le sélecteur de
  modèle (E4) : c'est M8-7.
- **« Votre avis ».** Le pilote ferme cette enquête par sa croix quand elle apparaît (le 17/10,
  après chaque abandon ; débriefing D2), et chaque ligne compte ces apparitions (`modals_closed`).
  M8-8 l'ouvre exprès depuis son entrée, le bouton « Send feedback » de la barre latérale (E5). Le
  pilote ne clique cette entrée que reconnue par son libellé exact, jamais une note 👍/👎 d'une carte
  de réponse (un envoi) : sinon il refuse avant tout clic, et M8-8 est journalisé NON TESTÉ (revue du
  20/10, deuxième tour : une limite du pilote, jamais un KO de MaskAI) ; une fenêtre qu'il ne reconnaît
  pas après cette entrée aussi, laissée ouverte pour le fondateur.
- **Refus du pilote, NON TESTÉ** (revue du 20/10, deuxième tour). Un cas que le pilote refuse par sa
  propre garde, avant tout clic irréversible, est journalisé NON TESTÉ « refus du pilote : … », sans
  ENF : une entrée non identifiée, un menu ou une confirmation qu'il ne reconnaît pas (la confirmation
  est alors annulée par Échap et la conversation reste en place), une confirmation native du navigateur
  qu'il laisse au fondateur, une fenêtre inconnue après l'entrée de l'enquête. KO reste pour ce que
  MaskAI a fait après que le pilote a fait sa part : entrée cliquée et rien ne s'ouvre, titre validé et
  jamais relu, confirmation cliquée et la conversation encore listée.
- **Suppression en cascade** (deuxième tour). Si une autre conversation que celle de M8-1 quitte la
  barre latérale pendant le renommage ou la suppression (relue après 2 s), la ligne est KO sécurité
  (« n autre(s) conversation(s) disparue(s) pendant l'action ») ; le registre note « suppression en
  cascade » et leur nombre ; le résultat de l'action et celui de `verdict_oracle` portent
  `cascade_delete`, que le harnais note aussitôt ; leurs adresses ne vont qu'à `conversation.json`,
  jamais un titre ailleurs.
- **Troisième tour de la revue (20/10).** La confirmation de suppression n'est acceptée que si elle
  nomme exactement une conversation (son nom ou son titre) et ne porte aucun nom de conversation au
  pluriel hors de ce nom, ni aucun nombre devant un nom de conversation : « … et les conversations
  liées », « Les 3 conversations de ce fil », « related chats » sont refusées ; seuls les idiomes de lieu
  (« dans vos conversations », « from your chats », « de votre historique ») ne comptent pas. Une entrée
  de menu se lit sans son indice de raccourci, même en texte simple (« Supprimer ⌘⌫ », « Renommer F2 »),
  par la même règle des deux côtés du pilote. Sont aussi NON TESTÉ, avec le registre laissé tel qu'il
  était : une entrée ou un bouton de confirmation acceptés puis introuvables au moment du clic, une palette
  qui ne s'ouvre pas après l'entrée identifiée, des résultats que le pilote ne relie à aucune conversation
  (« résultats non reconnus »). Un dialogue relevé pendant une action reste KO sécurité même quand
  l'action finit par son propre refus ; la confirmation native ouverte tard, pendant une lecture, reste
  celle de l'application (NON TESTÉ). « plus listée » ne compte qu'une écriture 2xx des hôtes de
  l'application, d'abord la requête qui nomme la conversation.
- **Quatrième tour (20/10).** Un nombre ou un quantificateur devant un nom de conversation au pluriel
  refuse la confirmation, jugé sur le texte entier avant que les idiomes de lieu soient mis de côté :
  « … et 3 de vos conversations ? », « and 2 of your chats? », « some of your chats », « d'autres,
  certaines, plusieurs, la plupart de vos conversations », « the rest of your chats » sont annulées par
  Échap, NON TESTÉ ; « Cette conversation sera retirée de vos conversations » reste acceptée. Un titre
  de conversation de 3 à 7 caractères (« Devis », « Contrat »), cité en entier par un dialogue ou une
  fenêtre, ne va jamais au harnais ; un titre coupé se reconnaît à un début de 8 caractères ou plus.
  Un abandon pendant lequel un dialogue s'est ouvert n'écrit plus de ligne KO disponibilité : le cas
  est KO sécurité « dialogue inattendu », et le refus garde `internal_error_shown` et le profil
  d'abandon.
- **Types de fichiers.** Une pièce jointe par famille, comptée sur toute la recette. La liste
  `accept` du champ de fichier n'était pas relevée avant le pilote v2.3 : E6 la fait lire. Chaque
  ligne de pièce jointe la porte (`attach.accept`), et la sonde de M0 la relève ; une famille
  acceptée sans document au corpus deviendra un cas à la révision suivante. Tant que le service
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
| M0 | 14 | 52 | 1800 |
| M1 | 112 | 346 | 2700 |
| M2 | 54 | 172 | 2700 |
| M3 | 215 | 655 | 3600 |
| M4 | 44 | 142 | 1800 |
| M5 | 8 | 34 | 2700 |
| M6 | 10 | 40 | 1800 |
| M7 | 16 | 58 | 1800 |
| M8 | 32 | 106 | 1800 |
| M9 | 29 | 97 | 1800 |

- M7 perd un acte, la remise de la recherche web, qui passe à M9.
- M0 : un acte de plus que la recette 2, la sonde d'interface (`sonder_interface`, v2.3, E11),
  après les deux chargements.
- M8 : 2 de configuration ; M8-1, 5 (verdict, conversation neuve, choix du modèle, question,
  verdict) ; M8-2, 3 (verdict, liste, verdict) ; M8-3, 5 ; M8-4 à M8-8, 3 chacun (verdict, l'action
  du cas, verdict) ; capture et export, 2.
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

## 6. Ce que le pilote v2.3 fournit

Livré en v2.3 le 19/10. Les exigences E1 à E5 ont débloqué les cinq cas qui étaient « à
implémenter » ; E6 à E9 rendent le jugement plus sûr ; E10 et E11 s'y sont ajoutées après la
première exécution de la recette 3 (18 et 19/10). Chaque cas débloqué garde une sorte existante : la
nouvelle action écrit l'observation que lit déjà le verdict de cette sorte (`@list` pour `list`,
`@model` pour `model`, l'observation du cas pour `message`).

| # | Exigence | Débloque |
|---|---|---|
| E1 | **Registre des conversations créées.** Le pilote retient, dans son dossier de travail, l'adresse de chaque conversation qu'il crée, avec le cas qui l'a créée. `same_conversation_as` s'y résout. Renommer ou supprimer une conversation absente de ce registre est un refus `precondition`. Revue du 20/10 (I6, m13) : une conversation n'y entre que si la page arrive, après la question ou le téléversement, sur l'adresse d'une conversation qui n'était pas listée avant ; une ligne apparue dans la barre latérale n'y entre jamais, même seule. | M8-5, M8-6 |
| E2 | **Recherche ⌘K.** `lister_conversations` prend un argument facultatif `case`. Si ce cas porte une `question`, le pilote ouvre la recherche (`ControlOrMeta+K`), tape la question, lit les titres des résultats, puis ferme par Échap. Il écrit `@list` avec le texte « recherche « q » : n résultats : t1 \| t2 … ». Sans `case`, rien ne change (N7, M8-2). Revue du 20/10 (I4) : quand le clavier n'ouvre rien, l'entrée de la barre latérale n'est cliquée qu'identifiée (dans la barre latérale, son texte ou son libellé disant « search » ou « recherch », jamais le bouton de dictée, une commande du compositeur ni un bouton d'une carte) ; sinon refus `precondition` avant tout clic. Deuxième tour : le cas est alors journalisé NON TESTÉ « refus du pilote : … », les candidats vus ne vont qu'à `search.json` (le harnais n'en voit que le nombre), et un lien ou une ligne de conversation n'est jamais un candidat ; la palette est celle qui s'ouvre après la touche ou le clic (jamais un champ ni une racine cmdk déjà là) ; la liste d'avant la frappe est lue, et les résultats le sont quand la frappe les a changés (au plus 3 × le budget), chacun menant à une conversation ; sinon « résultats inchangés par la frappe », KO (motif interdit de M8-4). | M8-4 |
| E3 | **Renommer, supprimer.** Deux actions irréversibles (`reversible: false` au manifeste), `renommer_conversation {case}` et `supprimer_conversation {case}`, sur la seule conversation de `same_conversation_as`, si E1 la connaît. Le renommage donne pour titre la `question` du cas. La suppression valide la confirmation de l'application par son bouton documenté (une modale `own` de `KNOWN_MODALS`). Les deux relisent ensuite la barre latérale dans `@list`. Le rapport liste la conversation comme créée, puis supprimée. Revue du 20/10 (I7, m1, m2, m14, m15) : le déclencheur du menu annonce un menu (`aria-haspopup`) et n'est jamais pris au-dessus de la ligne dans un élément qui porte un titre de groupe ; les entrées cliquées sont en liste blanche sur leur texte entier (« Renommer », « Supprimer », « … la conversation », « Delete chat »…), seules de leur sorte, et aucune entrée du menu ne vise plusieurs conversations (« tous », « all », « every », « sélection »…) ; le champ du titre et la confirmation doivent être ouverts par le clic ; la confirmation doit parler de supprimer une conversation, une seule, sinon elle est annulée par Échap ; son clic est noté aussitôt au registre (« suppression confirmée ») et n'est jamais refait. Deuxième tour (R2-1 à R2-3) : le déclencheur est cherché niveau par niveau dans la ligne de la conversation seule (jamais au-dessus, jamais dans un élément qui porte un titre ou une étiquette de groupe, celle de shadcn comprise), et le menu ouvert doit être le sien (`aria-expanded`) ; une entrée se lit sans ses points de suspension ni son indice de raccourci, et « Modifier le titre », « Déplacer vers la corbeille » sont en liste blanche ; la confirmation est jugée sur son texte entier (au-delà de 4 000 caractères, annulée) et n'est refusée que si elle vise explicitement plusieurs conversations (un quantificateur devant un nom de conversation, tout l'historique, vider, un contenant : dossier, projet, espace, groupe, sélection), le titre de la conversation pouvant tenir lieu du nom ; son bouton est le seul bouton de suppression, à défaut le seul « Continuer », « Oui » ou « OK » d'une fenêtre d'alerte (« Ne pas supprimer », « Garder » annulent) ; « suppression demandée » est noté avant le clic sur « Supprimer » (et retiré, « suppression annulée », quand la confirmation est annulée et la conversation encore listée), « suppression confirmée » avant le clic de confirmation ; « plus listée » exige une requête d'écriture 2xx et l'absence tenue 2 s, attendue jusqu'à 3 × le budget ; le champ du titre n'est pris dans une fenêtre que si elle s'est ouverte au clic, n'a qu'un champ et aucun choix, et n'est pas « Votre avis » ; un refus du pilote avant tout clic irréversible est NON TESTÉ ; une cascade est KO sécurité ; une confirmation native (`window.confirm`) n'est jamais répondue : refus `dialog`, M8-6 NON TESTÉ, le fondateur l'annule (runbook §8.4). | M8-5, M8-6 |
| E4 | **Bouton de dictée.** Le résultat de `choisir_modele` (observation `@model`) se termine par « ; micro : <data-status> », ou « ; micro : absent ». Le bouton est lu par `mic_button`, jamais pressé. La `config` de chaque ligne porte aussi cet état. | M8-7 |
| E5 | **« Votre avis ».** La sonde d'interface relève l'entrée de l'enquête (barre latérale ou menu du compte). Une action `ouvrir_avis {case}` l'ouvre depuis cette entrée, la ferme par sa croix (`KNOWN_MODALS`, « avis »), jamais par « Envoyer », et écrit l'observation du cas : « avis : ouvert (entrée : X) ; fermé par : croix ; envoyé : non ». Chaque ligne porte aussi `modals_closed`, les modales connues fermées pendant l'action, pour que le juge compte les apparitions spontanées. Revue du 20/10 (I5) : l'entrée n'est cliquée que reconnue par son libellé exact (`Send feedback`, `Votre avis`…), hors carte de réponse et compositeur ; sinon refus `precondition` avant tout clic. Deuxième tour : le cas est alors journalisé NON TESTÉ « refus du pilote : … », les candidats ne vont qu'à `avis.json` ; l'entrée cliquée est l'élément même qui a passé l'identification ; une fenêtre que le pilote ne reconnaît pas après elle : refus `dialog`, NON TESTÉ, laissée ouverte pour le fondateur. | M8-8 |
| E6 | **Liste `accept`.** `joindre_document` lit l'attribut `accept` du champ de fichier caché, le rend dans son résultat et le met dans l'`attach` de la ligne. Le juge compare familles acceptées et familles jointes (§3, M8). | les cas de familles à écrire |
| E7 | **Ligne de journal au service du contrat.** Chaque ligne porte `sent_source` (`card-details`, `card-chips` depuis le neuvième tour, `composer-popover` ou nul, avec la raison du rejet), `sent_tokens` (les types des jetons du texte envoyé, numérotation repliée, par exemple `{"PERSON": 2, "IBAN": 1}`) et le nombre de données identifiantes que déclare le panneau de garde. La note d'un passage b compare les entités masquées des deux passages, lues dans leurs textes envoyés contre leurs questions : « entités masquées identiques », ou « entités masquées différentes : +X −Y », les types des entités en plus puis en moins, jamais leurs valeurs ; sans l'un des deux textes, les types seuls, et la note dit « types masqués » (revue du 20/10, m6 : des types identiques ne prouvent pas des entités identiques). Le juge applique ainsi le §7, point 5, sans lire `sent.json`. Avec la v2.2, il s'en tient à `sent_sha256` (texte envoyé à la numérotation des jetons près) et à la note de passage a. Neuvième tour (carte du 20/10) : la carte d'une pièce jointe n'a plus de déclencheur ; ses puces (« Token anonymisé <TYPE_n> ») donnent `sent_source: card-chips` et `sent_tokens`, sans texte envoyé (« texte envoyé non observé : fuite de PII non vérifiable » reste, §7 point 4), et la note d'un passage b dit « entités masquées identiques (puces) » ou « différentes : +X −Y (puces) » ; les puces, le bouton de révélation et le résumé des étapes ne sont jamais pressés. | jugement de M1 |
| E8 | **Lignes de correction.** Une ligne `supersedes` porte `inputs_sha256` et `ui_profile_sha256` comme une ligne d'exécution (le 18/10, celles de 18:43 n'en portaient aucun). Revue du 20/10 (m8, m9) : `correct.py --observations FICHIER` rejoue sur les observations mises de côté à une reprise ; une preuve seule changée n'est corrigée qu'avec `--evidence` ; `journal.py stats` compte les lignes en vigueur, corrections comprises. Deuxième tour (robustness-5) : le verdict noté sur l'observation porte l'empreinte de sa ligne (`line_sha256`) ; `correct.py` ne rejoue que l'observation de la ligne en vigueur (sans empreinte, celle qui redonne son délai) : une observation mise de côté à une reprise et jamais jugée est refusée, jamais rejouée en correction ; un dialogue noté après le verdict (`after_verdict`) n'y compte pas. | traçabilité |
| E9 | **Classe d'erreur de la puce.** Une puce en échec donne à la ligne une `error_class` sans nom d'hôte ni pile, par exemple « service documentaire injoignable ». Le libellé brut reste dans les preuves, privées. Avant la v2.3, il passait dans la note de la ligne, et donc dans ce que lisent le juge et la rédaction. Revue du 20/10 (I2, I3) : la ligne porte aussi `internal_error_shown`, dans `attach` et sur la ligne ; une puce en échec qui montre un hôte, un chemin, une pile ou un service injoignable le met à vrai ; son libellé, même expurgé, ne quitte plus les preuves (`chip.json`). | règle des pièces jointes, confidentialité |
| E10 | **Diagnostic de l'échec silencieux.** Une question sans nouvelle carte de réponse, ou avec une carte vide : le pilote relève la ligne d'état du compositeur (`#chat-input-status`), les alertes et les toasts, le statut HTTP du flux et ses derniers événements, avec la charge utile d'une erreur. La ligne porte une `error_class` et `internal_error_shown` : vrai quand la ligne d'état, une alerte ou un toast a montré à l'utilisateur un hôte, un chemin, une pile ou un service injoignable (ce que dit le flux, que l'utilisateur ne voit pas, n'y compte pas). Une réponse qui affiche une erreur interne le met aussi à vrai, et la preuve de sa ligne est expurgée. L'extrait expurgé, 80 caractères au plus, et la capture brute ne vont qu'à la preuve `failure.json` : l'expurgation est une heuristique, la ligne n'en porte rien (revue du 20/10, I2, I3, m11). Seuls une alerte, un toast ou un texte d'état qui dit une erreur font « erreur affichée par l'application » (m7). Deuxième tour (leakage-1, leakage-4, m11) : ces avis d'erreur sont lus pour chaque question, répondue ou non, avant le clic d'arrêt d'un abandon, et après chaque action de M8 ; une alerte ou un toast déjà là avant l'envoi n'est jamais compté, même de même texte ; `internal_error_shown` se calcule sur ces avis d'erreur seuls (jamais « Erreur:500 », le nom du fichier joint ni l'hôte de l'application) ; leur texte brut va à `notices.json`. Une réponse montre aussi une erreur interne par une erreur de connexion qui nomme un hôte (« redis:6379 », « connect ETIMEDOUT presidio-analyzer:5001 »). | disponibilité (la nuit du 18 au 19/10, 12 modèles sur 14 sans réponse après `detect_pii`, « échec silencieux » sans autre indice) |
| E11 | **Sonde d'interface.** `sonder_interface`, en lecture seule, jouée en M0 : chaque sélecteur résolu, l'entrée de la recherche ⌘K et sa palette, l'entrée de « Votre avis », le bouton de dictée, la liste `accept`, et le menu de la première conversation de la barre latérale (ouvert, lu, refermé par Échap, aucune entrée cliquée, ni son titre ni son adresse gardés) ; preuve `probe.json`. Elle ne refuse rien. Revue du 20/10 (I4, m10, m15) : elle relève les entrées telles que les actions les prendraient (identifiées, sinon les candidats vus), ne presse que le déclencheur qui annonce un menu (`aria-haspopup`), et le menu qu'elle garde ne porte aucun mot du titre de cette conversation. Deuxième tour (robustness-1, robustness-4, m10) : elle applique au menu lu la règle des actions et le dit (« M8-5/M8-6 : menu accepté », ou « menu refusé » et la raison ; les entrées dans `probe.json` seulement ; troisième tour : la palette relève aussi son nombre d'éléments, `items`) ; ses lectures sont des appels bornés, qu'un dialogue ouvert fait échouer au lieu de les geler ; le menu gardé remplace en entier tout libellé, texte ou attribut qui porte le début du titre ou un de ses mots de quatre lettres ou plus. Huitième tour : elle découvre l'« Anonymiseur » (sous l'entrée de la recherche), en lecture seule, page relevée puis retour à /new, « anonymiseur : trouvé (<chemin>) » ou « non trouvé » dans son résultat. | confirmer le balisage réel avant que M8 en ait besoin |

**Livraison.** Les neuf cas de M8 et M9 jouables avec le pilote v2.2 sont entrés dans `cases.json`
à sa fusion, les cinq autres avec le pilote v2.3. `drivers/maskai-recette/tests/test_recipe_cases.py`
lit les trois recettes, compte trente blocs de règles, borne chaque grille à ses niveaux (M0–M7 pour
la recette 2, M0–M9 pour la recette 3) et vérifie la forme v2.3 de la recette 3 (la sonde en M0, les
huit cas de M8 dans l'ordre, le renommage et la suppression par M8-5 et M8-6 seuls). `cases.json` est
dans le dossier haché du driver : `files_sha256` est régénéré, et `inputs_sha256` change.
`scripts/recette/correct.py` écrit les lignes de correction (E8) : il rejoue hors ligne le jugement
du driver sur l'observation gardée, à blanc par défaut.

**Choix du pilote v2.3 et de sa revue du 20/10, à relire au premier passage réel :**
- l'oracle de M8-4 ne lit les titres qu'après « résultats : » (`resultats? : .*recette trois`) : la
  ligne recopie la requête en tête, et l'oracle proposé le 18/10 (`recette trois`) passait même sans
  aucun résultat ;
- un menu n'est celui d'une conversation que si ses entrées de renommage et de suppression sont en
  liste blanche, sur leur texte entier, seules de leur sorte (sixième tour, le menu vu en direct le 20/10 :
  sous un déclencheur de la ligne dont le libellé nomme les actions de la conversation, « Actions de la
  conversation », « Conversation actions », « Options de la conversation », « Chat actions », « Chat
  options », « Plus d'options », « More options », chaque entrée doit être en liste blanche, une seule de
  suppression, au plus une de renommage : l'entrée de renommage n'est plus exigée, et M8-5 sans elle est
  NON TESTÉ « refus du pilote : pas d'entrée de renommage … »), et qu'aucune entrée ne vise plus d'une
  conversation (« tous », « all », « every », « sélection », « historique ») : sinon rien n'est
  cliqué. Ce refus peut tomber sur le vrai menu de MaskAI : la sonde de M0 le dit avant M8 (« M8-5/M8-6 :
  menu refusé »), et le runbook (§8.3) ne lance pas M8 sur un menu refusé ; depuis le deuxième tour de la
  revue, une entrée se lit sans ses points de suspension ni son indice de raccourci (« Supprimer… »,
  « Supprimer ⌘⌫ ») ;
- la confirmation de suppression doit parler de supprimer une conversation, une seule, sinon elle est
  annulée par Échap. Depuis le deuxième tour de la revue (R2-3), seule une formule qui vise explicitement
  plusieurs conversations la fait refuser : un quantificateur devant un nom de conversation (« toutes les
  conversations », « chaque conversation », « all chats »), tout l'historique, vider, un contenant
  (dossier, projet, espace, groupe, sélection) ; « de votre historique », « vos conversations » seuls, le
  titre à la place du nom, « Continuer » ou « Oui » comme seul bouton qui n'annule pas d'une fenêtre
  d'alerte sont des formes d'une seule conversation, et « Tous ses messages » y vise cette conversation.
  Depuis le quatrième tour, un nombre ou un quantificateur devant un nom de conversation au pluriel
  (« 3 de vos conversations », « some of your chats », « d'autres / certaines / plusieurs / la plupart de
  vos conversations ») la fait refuser, jugé avant que les idiomes de lieu soient mis de côté ; depuis le
  cinquième, « autre(s) » / « other(s) » comme nom ou après un nombre ou un déterminant aussi (« et les deux
  autres », « and 2 others », « and the others », « une autre conversation »).
  La sonde ne clique aucune entrée : ce texte ne se lit qu'au clic de M8-6, et un refus laisse la
  conversation en place, son texte dans `conversation.json`, et le cas NON TESTÉ ;
- M8-5 et M8-6 se jugent sur l'état de la conversation de M8-1, relu à son adresse (« titre relu
  « … » », « plus listée »), jamais sur la liste entière : avant la revue, une suppression refusée
  était journalisée OK, et un titre « Recette trois renommée » laissé par un essai antérieur rendait
  M8-5 OK sans renommage ;
- une entrée (recherche ⌘K, « Votre avis ») que le pilote n'identifie pas n'est jamais cliquée : refus
  `precondition`, et le cas est journalisé NON TESTÉ « refus du pilote : … » (deuxième tour, R2-1), plutôt
  qu'un KO sur un bouton que le pilote n'a pas su reconnaître ; il en va de même de tout refus de sa
  propre garde avant un clic irréversible (§3, M8) ;
- deux changements d'oracle du deuxième tour, à confirmer par le fondateur : M8-4 refuse « résultats
  inchangés par la frappe » (une liste que la frappe n'a pas changée n'est pas une recherche), et M8-6
  n'est KO « encore listée » que sur l'état de sa conversation (`m8-1 : encore listee`), le texte d'une
  erreur entre parenthèses n'y comptant plus ;
- une confirmation native de la suppression (`window.confirm`) n'est jamais répondue (« seul un humain
  répond à une boîte ») : M8-6 est NON TESTÉ, et la conversation reste au registre « suppression
  demandée », pour que le pilote ne la supprime jamais deux fois ;
- une observation partagée (`@load`, `@list`, `@model`, `@web`) ne juge qu'un cas, celui qui la lit
  le premier, sauf un cas sondé avant qu'elle soit faite (N7-1 et N7-2 sur une même liste) : la sonde
  de M8-4 ne se juge pas sur la liste de M8-2, ni celle de M8-7 sur le choix de modèle de M8-1 ; une
  observation faite pour un cas qui nomme son action (M8-4 à M8-6) ne juge que lui, et un dialogue
  pendant cette action est noté sous le cas, qui le lit (revue du 20/10, m4, m5) ;
- la palette ⌘K, la fenêtre de renommage et la confirmation de suppression sont des modales `own` :
  laissées ouvertes par une action coupée, la garde suivante les ferme par Échap, sans jamais valider ;
- `sent_tokens` compte chaque occurrence d'un type de jeton, à la numérotation près, comme
  `sent_sha256` ; nul quand le texte envoyé n'est pas observé ;
- `internal_error_shown`, sur les lignes et les résultats, dit qu'une erreur interne a été montrée à
  l'utilisateur ; chaque niveau qui pose des questions la note une fois avec `vk_log` (revue du 20/10,
  I3). Les motifs qui la détectent (hôte, `nom:port`, le mot qui suit `ENOTFOUND`, `lookup`, `connect
  to`, `host`…) sont des heuristiques : ils penchent vers le signalement, et la preuve brute tranche.

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

9. **Non-détection.** Une entrée qui porte des données de l'inventaire et dont la carte déclare « aucune donnée personnelle détectée » (donc sans « Détails ») est partie en clair par construction : le juge en déduit la fuite, KO sécurité marqué « déduit », avec la ligne et la carte pour preuve. La non-détection est comptée à part dans le rapport, à côté des fuites lues dans « Détails ».

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
- Seuls ajouts, hors du bloc de règles : en M1, M2 et M8, une erreur interne affichée dans la puce
  se note une fois avec `vk_log`, sans recopier le message ; depuis la revue du 20/10, chaque niveau
  qui pose des questions (M1 à M9) note de même, une fois, un résultat qui porte
  `"internal_error_shown": true`. Depuis le deuxième tour de la revue : en M0, la note de la sonde dit le
  verdict du menu pour M8-5 et M8-6, sans entrée ni titre (« M8-5/M8-6 : menu accepté », « M8-5 : menu
  sans entrée de renommage ; M8-6 : menu accepté » depuis le sixième tour, ou « menu refusé ») ; en M8, un résultat ou un refus qui porte
  `cascade_delete` se note aussitôt avec `vk_log` « SUPPRESSION EN CASCADE », sans titre (R2-2).
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
- Traçabilité : la conversation de M8-1, créée, renommée (M8-5) puis supprimée (M8-6) par la
  recette, et aucune autre (le registre du pilote ; le champ `conversation` des lignes).

**Confidentialité.**
- Le rapport reste privé, sous `docs/functional/use-cases/recettage-chat-ia/private/`, tant que
  l'erreur interne affichée dans la puce n'est pas corrigée.
- La version publique, expurgée, ne porte ni nom d'hôte interne, ni pile logicielle, ni compte, ni
  titre d'une conversation antérieure à la recette, ni valeur du corpus (IBAN, NIR, noms,
  adresses, téléphones, plaques), même fictive.
- La fuite d'erreur interne est signalée à l'éditeur dès l'exécution, sans attendre le rapport.
- Une ligne qui porte `internal_error_shown: true` compte comme un KO sécurité pour la publication
  (revue du 20/10, deuxième tour, leakage-3) : rien ne va dans le dépôt tant qu'elle se montre. Même
  sans elle, seule une liste blanche de fichiers est copiée (rapport, verdict, `timing.json`, journal),
  jamais les preuves brutes du pilote (`chip.json`, `failure.json`, `notices.json`, `dialog.json`,
  `probe.json`, `search.json`, `conversation.json`, `avis.json`, `dom.html`, `network.json`) : runbook
  §10.3.

**Réglages remis et traçabilité.**
- M3 remet le modèle claude haiku 5-5, M4 la recherche web off, et M9 la recherche web dans l'état
  trouvé (auto le 17/10 et le 18/10). Si M0-4 a relevé un autre état, le fondateur le remet à la
  main, et le rapport le note.
- Une recette arrêtée avant M9 laisse la recherche web off : le rapport le dit dans
  « Traçabilité ».
- Des 100 conversations créées, la recette n'en supprime qu'une, celle de M8-1 (M8-6) ; les 99
  autres restent, et le rapport les liste par niveau.
