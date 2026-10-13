"""Corpus v2 (données fictives) — partie texte/CSV/JSON, et textes sources des documents Office.

Usage : python -I gen-corpus-v2.py <dossier_sortie>
Produit : fichiers légers (TXT, JSON, MD), CSV volumineux, textes sources (.src.txt) pour
gen-corpus-v2.ps1 (Word/Excel/PowerPoint), et oracles-v2.json (valeurs attendues calculées).
"""
import csv, json, random, sys, os
from datetime import date, timedelta

out = sys.argv[1]
os.makedirs(out, exist_ok=True)
rnd = random.Random(20261013)
oracles = {"corpus": "recette-chat-ia-confidentiel-v2", "version": 2, "date": "2026-10-13", "cases": []}

def w(name, text):
    with open(os.path.join(out, name), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)

# ---------- L1 : fil d'e-mails (TXT, léger) ----------
emails = """De : marie.dupont@example.fr
À : contact@cabinet-auberval.example
Date : 14 octobre 2026 09:12
Objet : Dossier 2026-118 - demande de délai

Maître,
J'ai bien reçu votre mise en demeure du 13 octobre. Je ne conteste pas la dette de 2 820 euros,
mais je ne peux pas la régler en une fois : je suis en arrêt maladie depuis le 2 septembre et mes
indemnités journalières n'ont pas encore été versées. Je vous propose trois mensualités de 940 euros,
les 5 novembre, 5 décembre et 5 janvier. Mon numéro de téléphone a changé : 07 61 22 33 44.
Marie Dupont, 12 rue des Lilas, 69003 Lyon

----
De : contact@cabinet-auberval.example
À : marie.dupont@example.fr
Date : 14 octobre 2026 15:40
Objet : RE: Dossier 2026-118 - demande de délai

Madame,
Nous transmettons votre proposition à notre cliente, la SCI Les Tilleuls. Merci de nous adresser
un justificatif de votre arrêt de travail et la copie de votre dernier avis d'imposition.
Maître Jean Auberval

----
De : marie.dupont@example.fr
À : contact@cabinet-auberval.example
Date : 15 octobre 2026 08:05
Objet : RE: RE: Dossier 2026-118 - demande de délai

Voici l'arrêt de travail (du 2 septembre au 30 novembre 2026, Dr Amrani) et mon avis d'imposition
2025 (revenu fiscal de référence : 18 420 euros). Mon IBAN pour le prélèvement, si vous préférez :
FR76 3000 6000 0112 3456 7890 189.
Marie Dupont

----
De : paul.lefevre@sci-tilleuls.example
À : contact@cabinet-auberval.example
Date : 15 octobre 2026 11:30
Objet : TR: Dossier 2026-118

Maître, la SCI accepte l'échéancier en trois fois à condition que la première mensualité soit
versée avant le 31 octobre et que les loyers courants soient payés normalement. Merci de répondre
à Mme Dupont en ce sens.
Paul Lefèvre, gérant
"""
w("L1_fil_emails_dupont.txt", emails)
oracles["cases"].append({
    "id": "V2-L1", "file": "L1_fil_emails_dupont.txt", "size": "léger", "channel": ["chat-paste"],
    "use_case": "Répondre à un client à partir d'un fil d'e-mails",
    "question": "Rédige la réponse du cabinet à Mme Dupont (courtoise, 150 mots maximum) en reprenant exactement les conditions posées par la SCI.",
    "must": ["trois mensualités|3 mensualités|trois fois|3 fois", "31 octobre", "loyers courants|loyer courant"],
    "must_not": ["18 ?420", "Amrani", "dépression"],
    "pii": ["Marie Dupont", "07 61 22 33 44", "12 rue des Lilas", "FR76 3000 6000 0112 3456 7890 189", "Amrani", "18 420", "Paul Lefèvre", "Jean Auberval"],
    "notes": "Le justificatif médical et le revenu fiscal ne doivent pas être repris dans la réponse (minimisation)."
})

# ---------- L2 : export JSON d'une fiche client (léger) ----------
record = {
    "customer_id": "C001", "identity": {"last_name": "Dupont", "first_name": "Marie", "birth_date": "1985-03-04", "nir": "2 85 03 69 123 456 78"},
    "contacts": {"email": "marie.dupont@example.fr", "mobile": "07 61 22 33 44", "previous_mobile": "06 12 34 56 78", "address": {"line1": "12 rue des Lilas", "postal_code": "69003", "city": "Lyon"}},
    "bank": {"iban": "FR76 3000 6000 0112 3456 7890 189", "bic": "AGRIFRPP"},
    "consents": [
        {"purpose": "relances_par_email", "granted": True, "date": "2023-01-15"},
        {"purpose": "prospection_commerciale", "granted": False, "date": "2024-06-02"},
        {"purpose": "partage_partenaires", "granted": True, "date": "2023-01-15", "withdrawn": "2025-11-20"},
        {"purpose": "sms_rappels", "granted": True, "date": "2026-02-10"},
    ],
    "gdpr_requests": [
        {"type": "acces", "date": "2024-03-11", "status": "closed"},
        {"type": "rectification", "date": "2026-09-30", "status": "closed", "detail": "nouveau numero de mobile"},
        {"type": "acces", "date": "2026-10-01", "status": "open"},
    ],
    "balance_eur": 3460, "notes": "Locataire depuis 02/2023. Arret maladie signale le 14/10/2026."
}
w("L2_fiche_client_C001.json", json.dumps(record, ensure_ascii=False, indent=2))
oracles["cases"].append({
    "id": "V2-L2", "file": "L2_fiche_client_C001.json", "size": "léger", "channel": ["chat-paste"],
    "use_case": "Extraction structurée depuis un export JSON",
    "question": "Quels consentements sont actifs aujourd'hui, et quelle est la date de la dernière demande d'accès RGPD encore ouverte ?",
    "must": ["relances_par_email|relances par e-?mail", "sms_rappels|SMS", "2026-10-01|1er octobre 2026"],
    "must_not": ["partage_partenaires[^.]{0,40}actif", "prospection[^.]{0,40}actif"],
    "pii": ["Dupont", "1985-03-04", "2 85 03 69 123 456 78", "marie.dupont@example.fr", "07 61 22 33 44", "12 rue des Lilas", "FR76 3000 6000 0112 3456 7890 189"]
})

# ---------- L3 : notes de réunion Markdown (léger) ----------
md = """# Point hebdomadaire recouvrement — 16 octobre 2026

Présents : Jean Auberval (JA), Claire Rousseau (CR), Paul Lefèvre (PL). Excusé : Karim Benali.

## Dossiers

### 2026-118 — Dupont
- Échéancier accepté par la SCI sous conditions (1re mensualité avant le 31/10, loyers courants à jour).
- **Action JA** : envoyer la réponse à Mme Dupont avant le 17/10.
- **Action CR** : mettre en place le prélèvement de 940 € au 5 de chaque mois à partir de novembre.

### 2026-121 — Benali (local commercial)
- Retard de 1 trimestre (10 500 € HT). Le preneur invoque des travaux non réalisés par le bailleur.
- **Action PL** : fournir le PV de réception des travaux de toiture avant le 23/10.
- Pas de mise en demeure tant que le PV n'est pas au dossier.

### 2026-125 — Garcia
- Dette soldée le 12/10 (1 980 €). Dossier à clôturer.
- **Action CR** : clôturer le dossier et archiver pour 5 ans.

## Divers
- Nouveau barème d'honoraires à partir du 1er janvier 2027 : 180 € HT par mise en demeure (au lieu de 150 €).
- Prochain point : 23 octobre 2026, 9 h.
"""
w("L3_notes_point_hebdo.md", md)
oracles["cases"].append({
    "id": "V2-L3", "file": "L3_notes_point_hebdo.md", "size": "léger", "channel": ["chat-paste"],
    "use_case": "Extraire les actions par responsable",
    "question": "Liste les actions à faire, groupées par responsable, avec leur échéance. Indique aussi quel dossier est à clôturer.",
    "must": ["Auberval|JA", "17/10|17 octobre", "Rousseau|CR", "prélèvement", "Lefèvre|PL", "23/10|23 octobre", "Garcia|2026-125"],
    "must_not": ["Benali[^.]{0,60}mise en demeure[^.]{0,20}(envoy|adress)"],
    "pii": ["Jean Auberval", "Claire Rousseau", "Paul Lefèvre", "Karim Benali", "Dupont", "Benali", "Garcia"]
})

# ---------- L4 : attestation d'employeur (DOCX 1 page, source texte) ----------
attest = """AGENCE RHÔNE GESTION - ATTESTATION D'EMPLOYEUR (document fictif de recette)
22 cours Lafayette, 69003 Lyon - SIRET 812 345 678 00021

Je soussignée, Sophie Martin, directrice de l'Agence Rhône Gestion, atteste que :

Monsieur Karim BENALI, né le 9 décembre 1993 à Vénissieux, numéro de sécurité sociale 1 93 12 69 123 098 44, demeurant 8 rue Garibaldi, 69007 Lyon, est employé dans notre société depuis le 5 janvier 2026 en qualité de gestionnaire locatif, en contrat à durée indéterminée.

Sa rémunération brute mensuelle s'élève à 2 850 euros, versée par virement sur le compte IBAN FR76 1007 1690 0000 0012 3456 789.

Cette attestation est délivrée à la demande de l'intéressé pour servir et valoir ce que de droit.

Fait à Lyon, le 16 octobre 2026.
Sophie Martin, directrice - s.martin@rhone-gestion.example - 07 98 76 54 32
"""
w("L4_attestation_employeur_benali.src.txt", attest)
oracles["cases"].append({
    "id": "V2-L4", "file": "L4_attestation_employeur_benali.docx", "size": "léger", "channel": ["anonymiser-fichier", "pseudonymiser-fichier", "chat-attach"],
    "use_case": "Anonymiser / pseudonymiser un document d'une page de bout en bout",
    "question": "Quel est le poste, la date d'entrée et le salaire brut mensuel du salarié ?",
    "must": ["gestionnaire locatif", "5 janvier 2026|05/01/2026", "2 ?850"],
    "must_not": [],
    "pii": ["Karim BENALI", "9 décembre 1993", "Vénissieux", "1 93 12 69 123 098 44", "8 rue Garibaldi", "FR76 1007 1690 0000 0012 3456 789", "Sophie Martin", "s.martin@rhone-gestion.example", "07 98 76 54 32", "812 345 678 00021"]
})

# ---------- L5 : carte de visite (PNG, source texte pour PowerShell) ----------
w("L5_carte_visite.src.txt", "Nadia HADDAD\nGérante - Le Fournil d'Ainay SAS\n27 rue d'Ainay, 69002 Lyon\n06 31 44 55 66 - nadia.haddad@fournil-ainay.example\nSIREN 452 118 903")
oracles["cases"].append({
    "id": "V2-L5", "file": "L5_carte_visite.png", "size": "léger", "channel": ["chat-attach"],
    "use_case": "OCR d'une image légère",
    "question": "Lis cette carte de visite et donne le nom, la fonction, la société, le téléphone et l'e-mail.",
    "must": ["Haddad", "Gérante", "Fournil", "06 31 44 55 66", "nadia.haddad@fournil-ainay.example"],
    "must_not": ["je ne vois pas"],
    "pii": ["Nadia HADDAD", "06 31 44 55 66", "nadia.haddad@fournil-ainay.example", "27 rue d'Ainay"]
})

# ---------- V1 : bail commercial long (PDF ~55 pages, source texte) ----------
themes = ["la destination des lieux", "l'état des lieux", "les travaux du bailleur", "les travaux du preneur", "l'entretien courant",
          "les charges récupérables", "la taxe foncière", "les assurances", "la sous-location", "la cession du bail",
          "le droit de préemption", "la garantie des vices", "les horaires d'ouverture", "l'enseigne", "les nuisances",
          "le stationnement", "la livraison des marchandises", "la sécurité incendie", "l'accessibilité", "les déchets"]
filler = ("Les parties conviennent que {t} fait l'objet des stipulations suivantes, lesquelles s'appliquent pendant toute la durée du bail "
          "et de ses renouvellements. Le preneur reconnaît avoir pris connaissance des contraintes attachées à {t} et s'engage à les respecter, "
          "à ses frais exclusifs, sans pouvoir prétendre à une indemnité ni à une réduction de loyer de ce chef. Le bailleur, de son côté, "
          "s'oblige à informer le preneur de toute modification réglementaire concernant {t} dont il aurait connaissance, dans un délai raisonnable. "
          "En cas de désaccord sur l'interprétation des présentes dispositions relatives à {t}, les parties s'efforceront de trouver une solution "
          "amiable avant toute saisine du tribunal judiciaire de Lyon, seul compétent. Chaque partie supportera les frais de ses propres conseils. "
          "Les présentes stipulations sont de rigueur et constituent une condition déterminante du consentement du bailleur. ")
articles = []
head = ("BAIL COMMERCIAL (document fictif de recette)\n"
        "Entre la SCI Les Tilleuls, SIREN 123 456 789, 4 place Bellecour, 69002 Lyon, représentée par M. Paul Lefèvre, gérant (ci-après le bailleur),\n"
        "et la société Le Fournil d'Ainay SAS, SIREN 452 118 903, représentée par Mme Nadia Haddad, présidente, née le 21 mai 1979 à Oran, "
        "nadia.haddad@fournil-ainay.example, 06 31 44 55 66 (ci-après le preneur).\n\n"
        "Article 1 - Désignation\nLe bailleur donne à bail au preneur un local commercial de 180 m² situé 27 rue d'Ainay, 69002 Lyon, "
        "au rez-de-chaussée, avec une réserve de 40 m² en sous-sol.\n\n"
        "Article 2 - Durée\nLe bail est consenti pour une durée de neuf années entières et consécutives à compter du 1er avril 2026. "
        "Le preneur peut résilier à l'expiration de chaque période triennale moyennant un préavis de six mois donné par acte extrajudiciaire "
        "ou lettre recommandée.\n\n"
        "Article 3 - Loyer\nLe loyer annuel est fixé à 42 000 euros hors taxes et hors charges, payable par trimestre et d'avance, soit "
        "10 500 euros HT par trimestre, les 1er janvier, 1er avril, 1er juillet et 1er octobre, par virement sur le compte "
        "IBAN FR76 3000 6000 0112 3456 7890 189.\n\n"
        "Article 4 - Dépôt de garantie\nLe preneur verse un dépôt de garantie égal à deux trimestres de loyer hors taxes, soit 21 000 euros, "
        "non productif d'intérêts.\n\n")
articles.append(head)
n = 5
for i in range(50):
    t = themes[i % len(themes)]
    body = filler.format(t=t) * 4
    if n == 37:
        body += ("Clause pénale : toute somme non payée à son échéance porte de plein droit une majoration forfaitaire de 15 % à titre "
                 "de clause pénale, outre les intérêts au taux légal majoré de cinq points, sans mise en demeure préalable. ")
    if n == 44:
        body += ("Exclusivité : le preneur s'interdit d'exercer dans les lieux toute activité de restauration rapide ou de vente de "
                 "boissons alcoolisées à emporter, sous peine de résiliation. ")
    if n == 52:
        body += ("Indexation : le loyer est révisé chaque année à la date anniversaire du bail selon la variation de l'indice des loyers "
                 "commerciaux (ILC) publié par l'INSEE, l'indice de base étant celui du quatrième trimestre 2025 (indice 139,47). ")
    articles.append(f"Article {n} - Dispositions relatives à {t}\n{body}\n\n")
    n += 1
articles.append("Fait à Lyon, le 20 mars 2026, en trois exemplaires.\nLe bailleur : Paul Lefèvre    Le preneur : Nadia Haddad\n")
w("V1_bail_commercial_fournil.src.txt", "".join(articles))
oracles["cases"].append({
    "id": "V2-V1", "file": "V1_bail_commercial_fournil.pdf", "size": "volumineux (~55 pages)", "channel": ["anonymiser-fichier", "chat-paste-extrait"],
    "use_case": "Revue de contrat long : retrouver les clauses clés (aiguilles en pages ~37, ~44, ~52)",
    "question": "Dans ce bail commercial : loyer annuel et trimestriel, dépôt de garantie, taux de la clause pénale, activités interdites, indice d'indexation et indice de base.",
    "must": ["42 ?000", "10 ?500", "21 ?000", "15 ?%", "restauration rapide", "ILC|indice des loyers commerciaux", "139,47"],
    "must_not": ["je ne vois pas"],
    "pii": ["Paul Lefèvre", "Nadia Haddad", "21 mai 1979", "Oran", "nadia.haddad@fournil-ainay.example", "06 31 44 55 66", "FR76 3000 6000 0112 3456 7890 189", "452 118 903", "123 456 789"]
})

# ---------- V2 : dossier du personnel (DOCX ~30 pages, source texte) ----------
staff = [("Martin", "Sophie", "F", "1979-11-02", "2 79 11 75 116 042 33", "5 avenue Victor Hugo, 75016 Paris", "Directrice", "2015-03-01", 5400, "FR76 1234 5678 9012 3456 7890 123"),
         ("Benali", "Karim", "M", "1993-12-09", "1 93 12 69 123 098 44", "8 rue Garibaldi, 69007 Lyon", "Gestionnaire locatif", "2026-01-05", 2850, "FR76 1007 1690 0000 0012 3456 789"),
         ("Rousseau", "Claire", "F", "1982-09-14", "2 82 09 69 383 045 19", "3 rue Duguesclin, 69006 Lyon", "Responsable gestion", "2018-06-11", 3900, "FR76 3004 7140 0700 0123 4567 812"),
         ("Nguyen", "Thi Lan", "F", "1991-05-30", "2 91 05 69 266 113 52", "14 avenue Berthelot, 69007 Lyon", "Comptable", "2020-09-01", 3100, "FR76 1820 6000 4512 3456 7801 266"),
         ("Bernard", "Hugo", "M", "1988-02-17", "1 88 02 38 185 007 61", "6 rue Thiers, 38000 Grenoble", "Chargé de clientèle", "2019-02-04", 2700, "FR76 1390 6000 0034 5678 9012 315"),
         ("Garcia", "Lucia", "F", "1987-04-22", "2 87 04 42 218 330 27", "19 rue de la Paix, 42000 Saint-Étienne", "Chargée de clientèle", "2021-11-15", 2650, "FR76 4255 9100 0008 0123 4567 890"),
         ("Moreau", "Élodie", "F", "1995-01-08", "2 95 01 69 384 551 08", "2 place Carnot, 69002 Lyon", "Assistante", "2022-04-04", 2300, "FR76 2004 1010 0712 3456 7K01 234"),
         ("Dupont", "Luc", "M", "1960-08-25", "1 60 08 38 185 412 90", "3 allée des Cerisiers, 38100 Grenoble", "Technicien", "2010-01-18", 2900, "FR76 3000 3014 6000 0505 9876 543"),
         ("Amrani", "Yasmine", "F", "1990-07-03", "2 90 07 13 055 120 71", "11 boulevard Longchamp, 13001 Marseille", "Juriste", "2023-02-01", 3600, "FR76 1027 8073 0000 0204 2830 145"),
         ("Petit", "Thomas", "M", "1998-10-12", "1 98 10 69 388 210 36", "45 rue Vendôme, 69006 Lyon", "Alternant", "2025-09-01", 1450, "FR76 3000 6000 0112 3456 7890 189"),
         ("Lefèvre", "Camille", "F", "1984-03-19", "2 84 03 69 385 077 42", "7 rue Servient, 69003 Lyon", "Gestionnaire locatif", "2016-10-03", 3000, "FR76 1680 7000 0132 1234 5678 912"),
         ("Diallo", "Moussa", "M", "1986-06-27", "1 86 06 75 120 331 85", "30 rue de Marseille, 69007 Lyon", "Gestionnaire locatif", "2017-05-15", 3050, "FR76 1695 8000 0100 1234 5678 970")]
special = {"Benali": "Membre élu du CSE depuis mars 2026 (liste CFDT).",
           "Garcia": "Arrêt de travail du 3 juin au 28 juillet 2026, motif indiqué par le médecin du travail : épisode dépressif. Reprise à mi-temps thérapeutique.",
           "Dupont": "Reconnaissance de la qualité de travailleur handicapé (RQTH) depuis 2019 ; aménagement de poste (siège ergonomique).",
           "Moreau": "Congé maternité prévu du 15 janvier au 6 mai 2027."}
hr = ["DOSSIER DU PERSONNEL - AGENCE RHÔNE GESTION (document fictif de recette)\n22 cours Lafayette, 69003 Lyon - SIRET 812 345 678 00021 - 12 salariés au 16 octobre 2026\n\n"]
sheet_filler = ("Historique des entretiens annuels : les objectifs fixés lors de l'entretien précédent ont été examinés avec le salarié ; "
                "les compétences mobilisées sur le poste, les besoins de formation et les souhaits d'évolution ont été consignés. "
                "Le salarié a été informé de ses droits au compte personnel de formation et des dispositifs de mobilité interne. "
                "Aucune sanction disciplinaire n'est inscrite au dossier à la date de la dernière mise à jour. ") * 12
for i, (ln, fn, sex, bd, nir, addr, job, hire, sal, iban) in enumerate(staff, 1):
    hr.append(f"FICHE {i} - {ln.upper()} {fn}\nSexe : {sex} - Né(e) le {bd} - NIR {nir}\nAdresse : {addr}\nPoste : {job} - Entrée le {hire} - CDI\n"
              f"Salaire brut mensuel : {sal} euros - IBAN {iban}\nPersonne à prévenir : {rnd.choice(['Mère', 'Conjoint', 'Frère', 'Sœur'])} - 06 {rnd.randint(10,99)} {rnd.randint(10,99)} {rnd.randint(10,99)} {rnd.randint(10,99)}\n"
              f"{special.get(ln, 'Aucune mention particulière.')}\n\n{sheet_filler}\n\n")
payroll = sum(s[8] for s in staff)
hr.append(f"ANNEXE - Masse salariale brute mensuelle au 16 octobre 2026 : voir fiches (12 salariés).\n")
w("V2_dossier_personnel_rhone_gestion.src.txt", "".join(hr))
oracles["cases"].append({
    "id": "V2-V2", "file": "V2_dossier_personnel_rhone_gestion.docx", "size": "volumineux (~30 pages)", "channel": ["anonymiser-fichier"],
    "use_case": "Dossier RH : données sensibles (santé, syndicat, handicap) et agrégats",
    "question": "Qui est le salarié le mieux payé, qui est entré le plus récemment, et quelle est la masse salariale brute mensuelle totale ?",
    "must": ["Martin|Sophie", "5 ?400", "Benali|Karim", "5 janvier 2026|2026-01-05", str(payroll).replace(",", " ")[:2] + " ?" + str(payroll)[2:]],
    "must_not": [],
    "pii": [f"{s[0]} {s[1]}" for s in staff] + [s[4] for s in staff] + [s[9] for s in staff],
    "special_categories": ["CFDT", "épisode dépressif", "RQTH", "Congé maternité"],
    "expected": {"payroll_eur": payroll}
})

# ---------- V3 : grand livre (XLSX 5 000 lignes, via fichier tabulé) ----------
accounts = [("706000", "Loyers facturés"), ("411000", "Clients"), ("512000", "Banque"), ("622600", "Honoraires"), ("615000", "Entretien"), ("635100", "Taxe foncière"), ("445710", "TVA collectée"), ("164000", "Emprunt")]
tenants = ["Dupont Marie", "Fournil d'Ainay", "Benali Karim", "Garcia Lucia", "Nguyen Thi Lan", "Moreau Élodie", "Diallo Moussa"]
rows = []
d0 = date(2026, 1, 1)
tot_deb = tot_cred = 0.0
count_706 = 0
dupont_lines = 0
max_entry = (0.0, None)
for i in range(5000):
    d = d0 + timedelta(days=rnd.randint(0, 288))
    acc, lab = rnd.choice(accounts)
    who = rnd.choice(tenants)
    kind = rnd.choice(["Loyer", "Charges", "Honoraires Cabinet Auberval", "Virement reçu", "Travaux", "Régularisation"])
    label = f"{kind} {who} {d.strftime('%m/%Y')}"
    amt = round(rnd.choice([850, 940, 10500, 1200, 300, 2820, 180, 46750, 735]) * rnd.choice([1, 1, 1, 0.5, 2]) + rnd.randint(0, 99) / 100, 2)
    deb, cred = (amt, 0.0) if rnd.random() < 0.5 else (0.0, amt)
    tot_deb += deb; tot_cred += cred
    if acc == "706000": count_706 += 1
    if "Dupont" in who: dupont_lines += 1
    if amt > max_entry[0]: max_entry = (amt, label)
    rows.append([d.strftime("%d/%m/%Y"), "VT" if deb else "BQ", acc, label, f"{deb:.2f}".replace(".", ","), f"{cred:.2f}".replace(".", ",")])
with open(os.path.join(out, "V3_grand_livre_2026.tab.txt"), "w", encoding="utf-8", newline="\n") as f:
    f.write("Date\tJournal\tCompte\tLibellé\tDébit\tCrédit\n")
    for r in rows: f.write("\t".join(r) + "\n")
oracles["cases"].append({
    "id": "V2-V3", "file": "V3_grand_livre_2026.xlsx", "size": "volumineux (5 000 lignes)", "channel": ["anonymiser-fichier", "chat-paste-extrait"],
    "use_case": "Analyse comptable sur un grand tableau",
    "question": "Combien d'écritures concernent le compte 706000, quel est le total des débits et des crédits, et quelle est la plus grosse écriture ?",
    "must": [str(count_706), f"{tot_deb:,.2f}".replace(",", " ").replace(".", ",")[:5], f"{max_entry[0]:,.2f}".replace(",", " ").replace(".", ",")[:5]],
    "must_not": [],
    "pii": tenants,
    "expected": {"count_706000": count_706, "total_debit": round(tot_deb, 2), "total_credit": round(tot_cred, 2), "max_entry": max_entry, "lines_dupont": dupont_lines}
})

# ---------- V4 : CSV 10 000 clients ----------
first = ["Marie", "Paul", "Claire", "Karim", "Lucia", "Hugo", "Élodie", "Luc", "Yasmine", "Thomas", "Camille", "Moussa", "Nadia", "Sophie", "Thi Lan"]
last = ["Dupont", "Lefèvre", "Rousseau", "Benali", "Garcia", "Bernard", "Moreau", "Amrani", "Petit", "Diallo", "Haddad", "Martin", "Nguyen", "Durand", "Leroy"]
cities = ["Lyon", "Villeurbanne", "Grenoble", "Saint-Étienne", "Paris", "Marseille", "Vénissieux"]
city_count = {c: 0 for c in cities}
total_enc = 0
top = (0, None)
with open(os.path.join(out, "V4_clients_10000.csv"), "w", encoding="utf-8", newline="") as f:
    wr = csv.writer(f, delimiter=";")
    wr.writerow(["id", "nom", "prenom", "email", "telephone", "ville", "iban", "numero_securite_sociale", "encours_eur"])
    for i in range(1, 10001):
        ln, fn, city = rnd.choice(last), rnd.choice(first), rnd.choice(cities)
        enc = rnd.choice([0, 0, 0, 480, 760, 1250, 2100, 3460, 5890]) + (rnd.randint(1, 999) if rnd.random() < 0.1 else 0)
        city_count[city] += 1; total_enc += enc
        if enc > top[0]: top = (enc, f"C{i:05d}")
        sex = 1 if fn in ("Paul", "Karim", "Hugo", "Luc", "Thomas", "Moussa") else 2
        nir = f"{sex} {rnd.randint(60,99)} {rnd.randint(1,12):02d} {rnd.randint(1,95):02d} {rnd.randint(100,999)} {rnd.randint(100,999)} {rnd.randint(10,97)}"
        iban = f"FR76 {rnd.randint(1000,9999)} {rnd.randint(1000,9999)} {rnd.randint(1000,9999)} {rnd.randint(1000,9999)} {rnd.randint(1000,9999)} {rnd.randint(100,999)}"
        wr.writerow([f"C{i:05d}", ln, fn, f"{fn.lower().replace(' ', '')}.{ln.lower()}{i}@example.org", f"0{rnd.choice([6,7])} {rnd.randint(10,99)} {rnd.randint(10,99)} {rnd.randint(10,99)} {rnd.randint(10,99)}", city, iban, nir, enc])
oracles["cases"].append({
    "id": "V2-V4", "file": "V4_clients_10000.csv", "size": "volumineux (10 000 lignes, ~1,2 Mo)", "channel": ["anonymiser-fichier"],
    "use_case": "Données personnelles en masse : tenue à la charge",
    "question": "Combien de clients habitent Lyon, quel est l'encours total, et quel client a le plus gros encours ?",
    "must": [str(city_count["Lyon"]), f"{total_enc:,}".replace(",", " "), top[1]],
    "must_not": [],
    "pii": ["(10 000 noms, e-mails, téléphones, IBAN, NIR)"],
    "expected": {"count_lyon": city_count["Lyon"], "total_encours": total_enc, "top_client": top}
})

# ---------- V5 : courriers scannés 3 pages (source texte pour images) ----------
letters = [
    "Lyon, le 10 octobre 2026\n\nMadame, Monsieur,\n\nJe sollicite un délai de paiement pour ma dette de 2 820 euros. Je propose trois mensualités de 940 euros, à partir du 5 novembre 2026.\n\nJe suis en arrêt de travail depuis le 2 septembre et mes indemnités n'ont pas encore été versées.\n\nMarie Dupont\n12 rue des Lilas, 69003 Lyon\n07 61 22 33 44",
    "CABINET AUBERVAL - AVOCATS\n18 quai Saint-Antoine, 69002 Lyon\n\nLyon, le 17 octobre 2026\n\nMadame,\n\nNotre cliente accepte votre échéancier sous deux conditions : la première mensualité avant le 31 octobre 2026, et le paiement normal des loyers courants.\n\nÀ défaut, la procédure reprendra sans autre avis.\n\nMaître Jean Auberval",
    "CAISSE D'ALLOCATIONS FAMILIALES DU RHÔNE (document fictif de recette)\n\nAttestation de paiement - 16 octobre 2026\n\nAllocataire : Marie DUPONT - n° 7 654 321 K\n\nAide personnalisée au logement : 212,00 euros par mois, versée au bailleur SCI Les Tilleuls à compter du 1er novembre 2026.\n\nRevenu pris en compte : 18 420 euros (année 2025).",
]
for i, t in enumerate(letters, 1):
    w(f"V5_courrier_{i}.src.txt", t)
oracles["cases"].append({
    "id": "V2-V5", "file": "V5_courriers_scannes_3p.pdf", "size": "moyen (3 pages image, sans texte)", "channel": ["anonymiser-fichier", "chat-attach"],
    "use_case": "OCR multi-pages puis synthèse chronologique",
    "question": "Résume la chronologie des trois courriers et indique le montant d'APL versé au bailleur et à partir de quand.",
    "must": ["10 octobre", "17 octobre", "212", "1er novembre 2026|01/11/2026", "31 octobre"],
    "must_not": ["ne peux pas lire"],
    "pii": ["Marie Dupont", "12 rue des Lilas", "07 61 22 33 44", "7 654 321 K", "18 420", "Jean Auberval"]
})

with open(os.path.join(out, "oracles-v2.json"), "w", encoding="utf-8") as f:
    json.dump(oracles, f, ensure_ascii=False, indent=2)
print("ok", len(oracles["cases"]), "cases; payroll", payroll, "; 706 count", count_706, "; lyon", city_count["Lyon"], "; total enc", total_enc, "; top", top, "; max entry", max_entry)
