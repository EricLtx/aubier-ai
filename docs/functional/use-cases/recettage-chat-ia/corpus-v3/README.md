# Corpus v3 — l'axe masquage (données fictives)

Six documents courts, une famille de données identifiantes par document, pour le niveau M1 de
la seconde recette (`../PROCEDURE-2.md`). Chaque document se teste deux fois par deux canaux :
en pièce jointe (oracles `V3-J1` à `V3-J6`) et collé dans le message (oracles `V3-C1` à
`V3-C6`, dont la question contient le texte du document). Même question des deux côtés : seul
le canal change. Les oracles sont dans `oracles-v3.json`, au format de `../corpus-v2/oracles-v2.json`,
lisible par `scripts/recette/evaluer.py`.

Régénérer : `python -I gen-corpus-v3.py <dossier>` (bibliothèque standard, déterministe, sans
réseau). Le script vérifie ce qu'il écrit avant de l'écrire (voir « Garanties »).

## Pourquoi ce corpus

Le 17/10, le texte envoyé au modèle n'a été observé sur aucun cas (débriefing du pilote, D4) :
la promesse centrale de MaskAI n'a été ni confirmée ni infirmée. Le 13/10, on l'avait observée
et elle fuyait par famille : IBAN, NIR, montants et dates ISO classés téléphone ou carte,
masques qui débordent, adresses et lieux de naissance en clair, champs JSON `nom`/`prenom` en
clair, e-mail obfusqué et plaque non détectés. Ce corpus isole chaque famille dans un document
de moins de 700 octets : le coût de détection (environ 3 s par entité le 13/10) reste dans le
budget, et une fuite se lit sans ambiguïté dans la liste `leaks` de l'évaluateur.

## Les documents

| Doc | Fichier | Famille | Pièges | ENF |
|---|---|---|---|---|
| P1 | `P1_mandat_prelevement_iban.txt` | IBAN | IBAN espacé et IBAN compact (ancien compte) ; restitution exacte demandée | sécurité, véracité |
| P2 | `P2_dpae_nir.md` | NIR | NIR espacé et NIR compact ; un montant (2 365 €) à multiplier, qui n'est pas un identifiant | sécurité, conformité, véracité |
| P3 | `P3_changement_adresse.txt` | adresse postale | deux adresses, complément sur plusieurs lignes, voie qui porte un nom de personne | sécurité, véracité |
| P4 | `P4_fiche_contact.json` | e-mail et téléphone | nom et prénom en champs séparés, e-mail obfusqué (« arobase »), mobile national et `+33`, date ISO | sécurité, véracité |
| P5 | `P5_fiche_adherent.md` | date de naissance et nom | nom en capitales, date en trois formats (long, JJ/MM/AAAA, ISO dans un identifiant), lieu de naissance | sécurité, conformité, véracité |
| P6 | `P6_constat_plaques.txt` | plaque et permis | plaques avec et sans tirets, permis de 12 chiffres collés | sécurité, véracité |

Chaque question demande de **recopier** la donnée sensible (IBAN, numéro, adresse, nom, plaque)
ou de calculer à côté d'elle. Le modèle ne voit que des jetons. La réponse affichée doit donc
restituer la valeur exacte : un `must` manqué sur une valeur recopiée est un défaut de
restitution (`restituer(masquer(x)) == x`). Un jeton laissé dans la réponse (`<IBAN_1>`) est
interdit par `must_not`.

## Lecture des oracles

- `must` et `must_not` sont des expressions régulières, insensibles à la casse et aux accents.
  Elles ne contiennent aucun échappement en majuscule, parce que `evaluer.py` met le motif en
  minuscules.
- `pii` est l'inventaire de ce qui ne doit jamais partir en clair vers le modèle. Il comprend la
  valeur entière **et** les fragments que le 13/10 a vus fuir : fin d'IBAN, NIR sans sa clé,
  nom de famille seul, nom de voie seul. Ce sont des littéraux sans autre métacaractère que `.`
  et `-` : ils se lisent de la même façon comme valeur (évaluateur v1, comparaison après
  normalisation) ou comme motif (évaluateur étendu du pilote v2, qui les compare à `sent.json`).
- `expected` donne la famille, les types d'entité attendus pour juger le typage, la raison pour
  laquelle les valeurs ne peuvent pas se valider, et les ENF du document.
- En pièce jointe, le texte que MaskAI déclare envoyer (fenêtre « Chiffré ») peut ne contenir
  que la question, et non le document : la fuite est alors « non vérifiable » pour ce canal, pas
  absente. Le canal collé, lui, met tout le document dans le texte envoyé.

## Garanties (vérifiées par le générateur)

- **Aucune valeur ne peut se valider.** Les deux IBAN ont une clé de contrôle fausse (modulo 97
  différent de 1). Le NIR a une clé fausse (la clé juste plus un). Les plaques contiennent I, O
  ou U, que le SIV n'attribue jamais. Les téléphones sont pris dans les plages que l'ARCEP
  réserve à la fiction (`06 39 98`, `04 65 71`). Les e-mails utilisent des domaines réservés
  (`example.org`, `example.net`, `.example`). Les personnes et les voies sont inventées.
- Chaque valeur de `pii` figure dans son document.
- Avec `evaluer.py` : une réponse de référence sur un texte envoyé masqué donne OK. Le même
  texte envoyé en clair donne KO, avec tout l'inventaire en fuite. « Je ne vois aucun document »
  donne KO.

**Conséquence pour le juge.** Un détecteur qui valide la clé peut, à bon droit, ne pas typer
ces valeurs en IBAN ou en NIR. Elles ne doivent pas partir en clair pour autant : une
coordonnée mal recopiée reste une donnée personnelle. Le juge distingue donc trois cas :
« non masqué » (fuite, KO sécurité), « masqué mais mal typé » (défaut de typage) et « masque
qui déborde » (défaut d'empan, débordements relevés par l'évaluateur).
