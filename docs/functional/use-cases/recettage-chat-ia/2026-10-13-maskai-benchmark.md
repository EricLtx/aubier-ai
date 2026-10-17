# Benchmark MaskAI — recette fonctionnelle, exécutions 1 et 2 (12–13 octobre 2026)

Produit testé : **MaskAI** (`app.maskai.fr`, « Sovereign AI proxy », v1.0.0, bêta), sur un compte
de test. Recette exécutée par un agent (Claude Code + Chrome piloté) selon la procédure Aubier
(`PROCEDURE.md`). Deux exécutions : le 12 octobre au soir (corpus v1, 10 documents) et le 13
octobre au matin (corpus v2, 5 documents légers et 5 volumineux, passe « fonctions cœur »).
Données **fictives** uniquement ; journal complet de la seconde exécution dans
`journal-v2.jsonl`. Version publique : les détails d'infrastructure exposés par le produit sont
remplacés par des marqueurs.

## 1. Synthèse critique

**Verdict : bêta avec un cœur prometteur, pas prête pour des données réelles.** La promesse
centrale tient sur les cas simples et la transparence est exemplaire (on voit ce qui part au
modèle, on voit chaque étape du pipeline). Mais la recette v2 confirme et aggrave les constats
de la v1 : les défauts ne sont pas des détails d'interface, ils touchent l'intégrité des données
et la tenue à la charge.

| ENF | v1 | v2 | Ce qui fonde la note |
|---|---|---|---|
| Performance | 2/5 | 2/5 | plancher 4,7 s par message (garde AI Act 3,5 s) ; 15 à 97 s dès qu'il y a des données personnelles ; la détection coûte selon le **nombre d'entités** (13 entités : 38 s ; 900 entités : > 90 s), pas selon la taille (30 Ko sans PII : 6,5 s) |
| Véracité | 3/5 | 3/5 | réponses justes sur tous les cas vérifiables (4 modèles, contrat, consentements, actions, contexte multi-tours) ; mais données restituées corrompues par les débordements de masque ; langue non déterministe (« Lisbonne » / « Lisboa ») |
| Disponibilité | — | 1/5 | pièces jointes hors service sur deux jours ; échec **silencieux** à 90 s sur un message dense ; tour perdu mais affiché ; détecteur « unreachable » sous deux traitements concurrents ; un seul traitement de fichier par compte ; tableur de 5 000 lignes bloqué à 86/91 |
| Sécurité | 2/5 | 2/5 | erreur interne exposée (pilote, IP privée, port) ; débordements de masque qui **laissent fuir** des fragments (« rie Dupont », signatures en clair) ; injections HTML/JS et prompt tenues ; second filet « safety » qui rattrape des fuites |
| Conformité | — | 3/5 | catégories spéciales bien détectées (santé, handicap, « BELIEF OR AFFILIATION ») mais nom du syndicat et « congé maternité » en clair ; promesses de la vitrine non tenues (ni GPT ni Mistral) ; réglage de langue ignoré |
| Scalabilité | — | 1/5 | 7 min pour 50 pages, 9,6 min pour 19 pages RH, 15 min puis blocage pour 5 000 lignes ; 4,5 à 30 s par page ; aucune parallélisation par compte |

**Les cinq défauts à corriger avant tout usage sur données réelles**, par ordre d'impact :

1. **Empans de masque faux (reproductible, systématique).** IBAN, SIREN, NIR, montants et
   dates ISO sont classés « téléphone » ou « carte bancaire » et le masque avale 1 à 40
   caractères voisins : il efface des lignes entières d'une liste, casse un JSON, corrompt un
   jeton (`12AB<PHONE_1>PERSON_1>`) et, à la restitution, rend un IBAN faux. Dans un fil
   d'e-mails, le masque qui mange « Ma » de « Marie Dupont » laisse « rie Dupont » **en clair**.
2. **Pièces jointes hors service** pendant les deux jours (base documentaire injoignable),
   message envoyé quand même, erreur interne affichée à l'utilisateur.
3. **Échecs silencieux et tours perdus.** Au-delà de ~90 s de détection, le flux est coupé
   sans message ni relance ; le message reste affiché dans l'historique mais n'existe pas
   pour le modèle.
4. **Masquage incomplet et instable.** Organisations, adresses de biens, lieux de naissance,
   noms dans un nom de cabinet, signatures en fin de document, champs structurés
   (`last_name`, `first_name`) partent en clair ; même texte, deux résultats différents ;
   trois compteurs qui se contredisent.
5. **Tenue à la charge.** Un traitement de fichier à la fois ; détecteur injoignable dès deux
   traitements ; 4,5 à 30 s par page ; tableur de 5 000 lignes jamais terminé.

**Ce qui est bon et doit être préservé :** le panneau « texte envoyé au modèle » ; les
événements de pipeline (`detect_pii`, `aiact_guard`, `anonymize`, `safety`,
`retrieve_context`, `web_search`, `call_llm`, `deanonymize`) ; la dé-pseudonymisation (table
réutilisable sur un autre document, jetons inconnus laissés tels quels, correspondances non
utilisées signalées) ; l'OCR dans l'anonymiseur ; la revue avant production ; le contexte
multi-tours qui survit au changement de modèle ; la recherche web qui rapporte honnêtement
des sources contradictoires ; le blocage « Article 5 » verrouillé.

## 2. Méthode

Grille N0–N9 et six exigences non fonctionnelles (`PROCEDURE.md`). Instrumentation : mesures
de navigation, requêtes réseau, intercepteur du flux SSE pour horodater chaque étape serveur,
texte « SENT TO THE LLM » relevé à chaque test de masquage. Pièces jointes déposées dans le
champ de fichier caché derrière le trombone (le sélecteur natif n'est pas pilotable).
Corpus v1 : `corpus/` ; corpus v2 : `corpus-v2/` (oracles lisibles par
`scripts/recette/evaluer.py`). Chaque test de la v2 est une ligne de `journal-v2.jsonl`.

## 3. Performance

| Mesure | v1 | v2 |
|---|---|---|
| Chargement de l'application (FCP) | 0,34 s | — |
| Message court sans PII, web Off | — | **4,7–5,0 s** (détection 0,3–0,5 s, garde AI Act 3,5 s, modèle 0,7 s) |
| Message court, web On | 22 s (Auto, 5 sources) | 7,9 s ; 26 s avec recherche effective (12,7 s de recherche) |
| 1 Ko de notes, 10 PII | — | 49,6 s |
| 1,4 Ko de courrier, 15 PII | 57 s | 45–58 s |
| 1,5 Ko de JSON, 19 PII | — | 87,8 s |
| 4,5 Ko d'extrait de contrat, 12 PII, Opus | — | 88 s (modèle 43 s) |
| 13,5 Ko de CSV, ~900 PII | — | **coupé à 90 s, sans message** |
| 30 Ko de texte sans PII | — | 26 s (détection 6,5 s) |
| Anonymiseur : DOCX 1 p. / PDF 2 p. / PDF scanné 3 p. | — / 44 s / — | 27 s / — / 54 s |
| Anonymiseur : PDF 50 p. / DOCX RH 19 p. / XLSX 5 000 l. | — | 7 min / 9,6 min / bloqué à 86/91 après 15 min |

Lecture : la garde « AI Act » est le plancher incompressible (3,5 à 12 s par message, même
pour « capitale du Portugal ») ; la détection PII coûte ~3 s par entité détectée et explose
sur les tableaux ; la recherche web en mode Auto se déclenche sur des rédactions sans en avoir
besoin (5 à 8 s perdus, 0 source). Les fragments de réponse arrivent d'un bloc en fin de
`call_llm` : pas de streaming perceptible.

## 4. Véracité

Toutes les réponses vérifiables sont justes : Canberra 1913 ; 2 505 € sur quatre modèles et
le « 31 février » détecté quatre fois ; résumé de courrier (2 550 €, 15 jours, tribunal) ;
consentements actifs et demande RGPD ouverte extraits d'un JSON ; actions par responsable ;
revue de contrat (7/7 valeurs dont les trois clauses enfouies) ; rappel exact d'un barème,
d'un code et de la première consigne après trois tours et un changement de modèle ;
code en fin d'un texte de 30 Ko et nombre de paragraphes exacts ; date du jour et sources.

Écarts : « impayé depuis le mars 2026 » et « Vous êtes tenu » (coût des jetons opaques) ;
une condition inversée dans un résumé (« commandement avant le 15 juillet » au lieu de
« si aucun règlement avant le 15 juillet ») ; Gemini Flash-Lite cite un délai périmé ;
« Lisboa » une fois sur deux ; tutoiement ponctuel ; LaTeX brut avec Gemini ; une donnée
masquée ne peut être transformée (majuscules, initiales) sans que l'utilisateur soit prévenu.
Restitution : fidèle sur les valeurs intactes, fausse sur les valeurs débordées (IBAN
`…7890 189C`, e-mail avec point final, parenthèse perdue sur `(1 980 €`).

## 5. Disponibilité

- Pièces jointes : 11/11 fichiers (v1) puis 3/3 (v2) en échec, même erreur de base de
  données ; « Indexing in progress » puis « Failed » ; le message part sans le fichier.
- Message dense (CSV 13,5 Ko) : flux coupé à 90 s, aucune erreur affichée, pas de bouton
  « Try again », zone de saisie rendue ; le tour suivant montre que le message n'a jamais
  atteint le modèle alors qu'il reste dans l'historique.
- Premier message de la v1 : « Connection error » après 34 s, relance OK.
- Anonymiseur : « The personal-data detector is unreachable » quand un chat dense tourne
  en parallèle ; « Another anonymisation is running » : un seul traitement par compte ;
  tableur de 5 000 lignes bloqué sans message, arrêt manuel.
- Génération d'artefact : le bouton « Stop generating » coupe proprement ; la production
  d'un PDF anonymisé est servie en ~1 s.

## 6. Sécurité

- **Erreur interne exposée** sous le message : pilote de base de données, adresse IP privée
  et port (`(psycopg2.OperationalError) connection to server at "<IP privée>", port <port>
  failed`). Codes HTTP 200 malgré l'échec ; un 500 sur `/api/user/envelopes`.
- **Fuites par débordement** : « rie Dupont » après un IBAN mal borné ; signatures « Paul
  Lefèvre » et « Nadia Haddad » en clair en fin d'extrait alors qu'elles sont masquées en
  tête ; `Dupont`/`Marie` en clair dans un JSON ; « CABINET AUBERVAL » et son adresse en
  clair. L'étape `safety` a renvoyé une fois « Patched leaked PII » : un second filet existe
  et a fonctionné sur un cas.
- Injections : `<img onerror>` et `<script>` rendus en texte ; instruction cachée dans un
  document ignorée par le modèle ; mais ni la garde ni le filet ne signalent l'injection.
- Restitution des pseudonymes : fichier restauré « contains real personal data, in the
  clear » clairement annoncé ; bon.

## 7. Conformité

- Catégories spéciales : `HEALTH` (arrêt maladie, épisode dépressif, RQTH) et `BELIEF OR
  AFFILIATION` détectées ; mais « (liste CFDT) » reste en clair et « Congé maternité » aussi
  (dates masquées) ; les salaires et le revenu fiscal de référence restent en clair.
- Minimisation : la réponse à la locataire n'a repris ni le justificatif médical ni le
  revenu fiscal (bon).
- Promesses : « ChatGPT, Claude, Gemini et Mistral » sur la vitrine, aucun modèle OpenAI ni
  Mistral utilisable ; réglage « Français » sans effet sur l'interface ; « ⌘K » sous Windows.
- Transparence : « SENT TO THE LLM », événements de pipeline, « Article 5 » verrouillé,
  usage par modèle dans les réglages : au-dessus du marché.

## 8. Scalabilité

| Document | Résultat |
|---|---|
| PDF 50 pages (contrat) | 7 min, linéaire (~8 s/page) ; organisations, adresse du local et lieu de naissance en clair |
| DOCX 19 pages (RH, 92 valeurs) | 9,6 min (~30 s/page) ; 12 entités fantômes « ×0 » |
| XLSX 5 000 lignes | 4,5 s/bloc jusqu'à 85/91 puis blocage ; 15 min, aucun fichier |
| CSV 10 000 lignes | non soumis : l'extrait de 100 lignes a déjà coupé le chat à 90 s |
| PDF scanné 3 pages | 54 s, OCR effectif, fichier produit en 1 s |
| Texte 30 Ko sans PII en chat | 26 s |

Un cabinet qui traite des dossiers de 100 pages ou des exports de 10 000 lignes ne peut pas
utiliser le produit aujourd'hui.

## 9. Recommandations priorisées

**P0 — intégrité et disponibilité**
- Empans : calculer la fin d'entité sur le texte source, jamais sur une version tokenisée ;
  test de non-régression `restituer(masquer(x)) == x` sur `corpus/` et `corpus-v2/` (les
  oracles et `evaluer.py` sont prêts).
- Détecteurs français typés avec clé de contrôle (IBAN, SIREN/SIRET, NIR) ; dates ISO ;
  champs structurés (clés JSON/CSV `nom`, `prenom`, `last_name`…).
- Rétablir la base documentaire ; bloquer l'envoi si la pièce jointe a échoué ; ne jamais
  afficher d'erreur interne ; codes HTTP justes.
- Délai d'attente explicite côté client (message, relance), et ne jamais afficher dans
  l'historique un tour qui n'a pas été accepté par le serveur.

**P1 — performance et charge**
- Détection PII par lots et en parallèle (coût par entité) ; garde AI Act en parallèle de la
  détection, ou mise en cache par intention ; vrai streaming.
- File de traitements par compte avec position affichée, plutôt qu'un refus « another
  anonymisation is running » ; reprise après blocage ; budget par page affiché.
- Recherche web Auto : ne pas la déclencher sur les intentions de rédaction ou de résumé.

**P2 — conformité et usage**
- Propager un masque à ses variantes (nom dans un nom de cabinet, signatures, « Mme X » vs
  « X ») ; masquer syndicat, maternité, salaires au moins en option ; unifier les compteurs.
- Jetons typés plus riches (genre, mois) ; prévenir que les données masquées ne sont pas
  transformables ; langue de réponse forcée ; supprimer les entités fantômes « ×0 » et le
  libellé `WORD/DOCUMENT.XML`.
- Aligner la vitrine et le sélecteur de modèles ; appliquer le réglage de langue.

## 10. Ce que cette recette apporte à Aubier

- Une procédure rejouable (`PROCEDURE.md`), une compétence agent
  (`.claude/skills/recettage-chat-ia`), deux corpus avec oracles, un journal append-only et
  un évaluateur (`scripts/recette/`) : le savoir-faire est devenu du capital exécutable.
- Trois leçons de méthode entrées dans la procédure : tester la charge concurrente exprès,
  fixer un délai d'abandon par test, vérifier qu'un tour affiché a bien atteint le modèle.
- Le rejeu sur la prochaine version de MaskAI tient en trois commandes : générer les
  observations, `evaluer.py`, `journal.py stats`, puis n'écrire que le delta.

## 11. Traçabilité

Conversations créées (non supprimées) : 26 en v1 et v2, toutes titrées par leur premier
message. Fichiers produits côté produit : un DOCX pseudonymisé, un TXT restauré, un PDF
anonymisé ; aucune revue d'anonymisation appliquée pour les documents longs. Réglages remis
(thème clair, recherche web). Non testé : dictée, édition/régénération de message, partage,
affichage mobile, CSV de 10 000 lignes dans l'anonymiseur, pièce jointe en chat (canal hors
service), Bedrock et modèles indisponibles.
