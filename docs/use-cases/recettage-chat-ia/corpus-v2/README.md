# Corpus v2 — documents légers et volumineux (données fictives)

Complète le corpus v1 (`../corpus/`, 10 documents, 9 formats) avec cinq documents légers
et cinq volumineux, dans le même univers fictif (SCI Les Tilleuls, Marie Dupont, cabinet
Auberval, Agence Rhône Gestion, Le Fournil d'Ainay). Les réponses attendues et les
inventaires de données identifiantes sont dans `oracles-v2.json` (lisible par
`scripts/recette/evaluer.py`).

Régénérer : `python -I gen-corpus-v2.py <dossier>` puis
`powershell -File gen-corpus-v2.ps1 -Out <dossier>` (Word, Excel et PowerPoint requis).

| Id | Fichier | Taille | Cas d'usage | Canal visé |
|---|---|---|---|---|
| V2-L1 | `L1_fil_emails_dupont.txt` | léger | répondre à un client à partir d'un fil d'e-mails, sans reprendre les données médicales ni fiscales | chat (collé) |
| V2-L2 | `L2_fiche_client_C001.json` | léger | extraction structurée : consentements actifs, dernière demande RGPD ouverte | chat (collé) |
| V2-L3 | `L3_notes_point_hebdo.md` | léger | actions par responsable et échéances ; ne pas inventer de décision | chat (collé) |
| V2-L4 | `L4_attestation_employeur_benali.docx` | 1 page | anonymiser / pseudonymiser de bout en bout | outils fichiers, pièce jointe |
| V2-L5 | `L5_carte_visite.png` | image | OCR léger | pièce jointe |
| V2-L6 | `L6_note_a_restaurer.txt` | léger | dé-pseudonymisation d'une note annotée avec la table d'un autre fichier | outil « De-pseudonymise » |
| V2-V1 | `V1_bail_commercial_fournil.pdf` | 50 pages | revue de contrat : clauses clés enfouies (art. 37, 44, 52) | anonymiseur, extrait en chat |
| V2-V2 | `V2_dossier_personnel_rhone_gestion.docx` | 19 pages | dossier RH : catégories spéciales (santé, syndicat, handicap, maternité), agrégats | anonymiseur |
| V2-V3 | `V3_grand_livre_2026.xlsx` | 5 000 lignes | analyse comptable, formules | anonymiseur, extrait en chat |
| V2-V4 | `V4_clients_10000.csv` | 10 000 lignes, 1,4 Mo | données personnelles en masse, tenue à la charge | anonymiseur, extrait de 100 lignes en chat |
| V2-V5 | `V5_courriers_scannes_3p.pdf` | 3 pages image | OCR multi-pages, chronologie | anonymiseur, pièce jointe |

Pièges volontaires : signatures répétées en fin de contrat (le masquage doit être cohérent
du début à la fin), IBAN avec une lettre (`7K01`), dates ISO dans le JSON, nom de
syndicat dans une parenthèse, montant qui ressemble à un numéro (`1 980`), e-mail obfusqué
(`dupont[at]`), identifiants sans espaces.

Critères communs : exactitude des valeurs attendues ; aucune donnée de l'inventaire en clair
vers le modèle ; `restituer(masquer(x)) == x` caractère pour caractère ; typage correct
(IBAN, SIREN, NIR) ; déterminisme ; budget de latence 10 s (message court) et 20 s (document
de moins de 5 pages).
