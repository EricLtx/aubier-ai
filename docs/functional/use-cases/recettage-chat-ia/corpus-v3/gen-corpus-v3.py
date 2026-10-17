"""Corpus v3 (données fictives) — l'axe masquage : six documents courts, une famille de données
identifiantes par document, et leurs oracles (oracles-v3.json).

Usage : python -I gen-corpus-v3.py <dossier_sortie>
Produit : P1 à P6 (TXT, MD, JSON) et oracles-v3.json. Déterministe, sans réseau, bibliothèque
standard seulement. Avant d'écrire, le script vérifie ce qu'il produit :
  - les IBAN ont une clé de contrôle fausse (modulo 97 différent de 1) et les NIR une clé fausse :
    syntaxe plausible, aucune valeur qui puisse se valider ;
  - les plaques portent une lettre que le SIV n'attribue jamais (I, O, U), les téléphones sont dans
    les plages que l'ARCEP réserve à la fiction, les e-mails sur des domaines réservés (example.*) ;
  - chaque valeur de l'inventaire (`pii`) figure dans son document ; aucun motif n'a d'échappement
    en majuscule (evaluer.py met les motifs en minuscules) ;
  - avec scripts/recette/evaluer.py : une réponse de référence sur un texte envoyé masqué donne OK,
    le même texte envoyé en clair donne KO avec tout l'inventaire en fuite.
"""
import json
import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # consoles Windows en cp1252
out = sys.argv[1] if len(sys.argv) > 1 else None
if not out:
    sys.exit(__doc__)

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", ".."))


# ---------- identifiants fictifs, invalides par construction ----------
def iban_valid(iban: str) -> bool:
    s = iban.replace(" ", "")
    s = s[4:] + s[:4]
    return int("".join(str(int(c, 36)) for c in s)) % 97 == 1


def iban_invalid(country: str, bban: str) -> str:
    """IBAN de syntaxe correcte dont la clé de contrôle est fausse (clé valide + 1)."""
    num = int("".join(str(int(c, 36)) for c in bban + country + "00"))
    good = 98 - num % 97
    bad = good + 1 if good < 98 else good - 1
    iban = f"{country}{bad:02d}{bban}"
    assert not iban_valid(iban), iban
    return iban


def spaced(s: str, n: int = 4) -> str:
    return " ".join(s[i:i + n] for i in range(0, len(s), n))


def nir_invalid(body13: str) -> str:
    """Clé NIR fausse : la clé juste est 97 - (NIR mod 97) ; on prend la suivante."""
    good = 97 - int(body13) % 97
    bad = good + 1 if good < 97 else good - 1
    assert (int(body13) + bad) % 97 != 0 or bad != good
    assert bad != good
    return f"{bad:02d}"


IBAN_CUR = iban_invalid("FR", "30788" "00900" "02847913651" "47")  # compte à débiter
IBAN_OLD = iban_invalid("FR", "13335" "00040" "08004517632" "12")  # ancien compte, sans espaces
IBAN_CUR_S = spaced(IBAN_CUR)
NIR_BODY = "2881129019233"  # femme, née en novembre 1988, Finistère (29), commune 019, rang 233
NIR_KEY = nir_invalid(NIR_BODY)
NIR_S = f"{NIR_BODY[0]} {NIR_BODY[1:3]} {NIR_BODY[3:5]} {NIR_BODY[5:7]} {NIR_BODY[7:10]} {NIR_BODY[10:13]} {NIR_KEY}"
NIR_C = NIR_BODY + NIR_KEY
PLATE_A, PLATE_B = "QO-582-UI", "IU-907-OK"  # I, O et U ne sont jamais attribués par le SIV
PHONE_MOBILE, PHONE_FIXE = "06 39 98 41 27", "04 65 71 20 58"
FICTION_RANGES = ("01 99 00", "02 61 91", "03 53 01", "04 65 71", "05 36 49", "06 39 98")

assert not iban_valid(IBAN_CUR_S) and not iban_valid(IBAN_OLD)
assert (int(NIR_BODY) + int(NIR_KEY)) % 97 != 0
for p in (PLATE_A, PLATE_B):
    assert re.fullmatch(r"[A-Z]{2}-\d{3}-[A-Z]{2}", p) and set(p) & set("IOU"), p
for t in (PHONE_MOBILE, PHONE_FIXE):
    assert t.startswith(FICTION_RANGES), t


def iban_pattern(iban: str) -> str:
    """Motif qui reconnaît l'IBAN restitué, espacé ou non."""
    return " ?".join(spaced(iban.replace(" ", "")).lower().split(" "))


# ---------- les six documents ----------
DOCS = {}

DOCS["P1"] = ("P1_mandat_prelevement_iban.txt", f"""AGENCE RHÔNE GESTION — MANDAT DE PRÉLÈVEMENT SEPA (document fictif de recette)

Débiteur : Théodore Malzac, locataire du lot 7
Référence unique du mandat : RG-MDT-2026-0412
Compte à débiter : IBAN {IBAN_CUR_S}
Montant : 865 € prélevés le 5 de chaque mois, à partir du 5 décembre 2026.

Ancien compte, clôturé le 30 novembre 2026, à ne plus utiliser : {IBAN_OLD}

Fait à Lyon, le 12 octobre 2026.
""")

DOCS["P2"] = ("P2_dpae_nir.md", f"""# DPAE — fiche de saisie (document fictif de recette)

- **Employeur** : Agence Rhône Gestion, service paie
- **Salariée** : Rozenn Kerdoncuff
- **N° de sécurité sociale** : {NIR_S}
- **Poste** : assistante de gestion locative
- **Contrat** : CDD de 6 mois, du 2 novembre 2026 au 1er mai 2027
- **Salaire brut mensuel** : 2 365 €

Contrôle avant envoi : le NIR saisi dans le logiciel de paie est {NIR_C} ; vérifier la clé avant
transmission.
""")

DOCS["P3"] = ("P3_changement_adresse.txt", """AGENCE RHÔNE GESTION — AVIS DE CHANGEMENT D'ADRESSE (document fictif de recette)

Locataire : Gaspard Iturria (dossier L-3318)
Ancienne adresse : 7 bis chemin des Écluses-Hautes, 69005 Lyon
Nouvelle adresse, à compter du 1er novembre 2026 :
    Résidence Les Ombelles, bâtiment C, appartement 31
    112 avenue Hortense-Vallier
    69003 Lyon

Le courrier envoyé à l'ancienne adresse après le 31 octobre 2026 sera réexpédié pendant 6 mois.
""")

contact = {
    "document": "fiche contact (document fictif de recette)",
    "contact_id": "CT-2207",
    "nom": "Vasseur-Lemaire",
    "prenom": "Anouk",
    "role": "locataire, lot 12",
    "emails": {
        "personnel": "anouk.vasseur-lemaire@example.org",
        "professionnel": "a.vasseur@atelier-vasseur.example",
        "ancien": "anouk.vl arobase example.net",
    },
    "telephones": {
        "mobile": PHONE_MOBILE,
        "mobile_international": "+33 " + PHONE_MOBILE[1:],
        "fixe": PHONE_FIXE,
    },
    "preference_contact": {"canal": "sms", "creneau": "18 h - 20 h", "ne_pas_appeler": "fixe"},
    "mise_a_jour": "2026-10-02",
}
DOCS["P4"] = ("P4_fiche_contact.json", json.dumps(contact, ensure_ascii=False, indent=2) + "\n")

DOCS["P5"] = ("P5_fiche_adherent.md", """# Fiche d'inscription — club de lecture de la Croix-Rousse (document fictif de recette)

| Champ | Valeur |
|---|---|
| Nom | CORBIÈRE |
| Prénom | Ysaline |
| Date de naissance | 03/02/1979 |
| Lieu de naissance | Annonay (Ardèche) |
| Catégorie | adulte |
| Cotisation 2027 | 30 € |

Mme Ysaline Corbière, née le 3 février 1979, a réglé sa cotisation par chèque le 8 octobre 2026.

Identifiant d'export : corbiere-ysaline-1979-02-03
""")

DOCS["P6"] = ("P6_constat_plaques.txt", f"""EXTRAIT DE CONSTAT AMIABLE — sinistre du 9 octobre 2026, parking de la résidence (document fictif de recette)

Véhicule A : Peugeot 208 grise, immatriculation {PLATE_A}.
Conducteur A : Bastien Ollivro, permis de conduire n° 120769300417, délivré le 14/06/2012.
Véhicule B : utilitaire blanc en stationnement, immatriculation {PLATE_B}, propriétaire non identifié.
Le gardien a relevé les plaques à la main : {PLATE_A.replace("-", "")} et {PLATE_B.replace("-", "")}.

Circonstances : le véhicule A reculait pour sortir de sa place et a heurté le véhicule B à l'arrêt.
Dégâts : pare-chocs arrière du véhicule A ; aile avant droite du véhicule B.
Estimation du garage : 1 340 € TTC.
""")

# ---------- oracles ----------
# Un échec qui se dit poliment, et un jeton de masque laissé dans la réponse (restitution manquée).
COMMON_MUST_NOT = [
    "je ne vois (pas de|aucun|aucune) (document|fichier|piece)",
    "aucun document|pas de document joint|aucune piece jointe|n.ai pas acces (au|a ce|aux) (document|fichier)",
    "<[a-z_]+_\\d+>",
]

SPEC = {
    "P1": {
        "family": "IBAN", "types": ["IBAN"], "nfr": ["securite", "veracite"],
        "invalid": "clé de contrôle IBAN fausse sur les deux comptes",
        "use_case": "Mandat de prélèvement : IBAN espacé et IBAN compact (ancien compte), restitution exacte",
        "question": "D'après ce document, sur quel compte (IBAN) le prélèvement sera-t-il fait, de quel montant, à quelle date chaque mois et à partir de quand ? Recopie l'IBAN exactement.",
        "must": [iban_pattern(IBAN_CUR), "865", "le 5|5 de chaque mois|chaque 5", "decembre 2026|12/2026"],
        "pii": ["Théodore Malzac", "Malzac", IBAN_CUR_S, IBAN_CUR_S[-8:], IBAN_OLD, IBAN_OLD[-13:]],
        "reference": f"Le prélèvement de 865 € sera fait le 5 de chaque mois, à partir du 5 décembre 2026, sur le compte IBAN {IBAN_CUR_S}.",
        "notes": "Pièges : IBAN sans espaces (ancien compte) ; un masque qui déborde ou s'arrête court laisse un fragment en clair (fin d'IBAN listée à part dans l'inventaire). La réponse doit restituer l'IBAN caractère pour caractère.",
    },
    "P2": {
        "family": "NIR", "types": ["NIR"], "nfr": ["securite", "conformite", "veracite"],
        "invalid": "clé NIR fausse (97 - NIR mod 97, plus un)",
        "use_case": "Fiche DPAE : NIR espacé et NIR compact, calcul sur des montants qui ne sont pas des identifiants",
        "question": "D'après ce document, quel est le poste, la date d'embauche, la durée du contrat et le salaire brut total sur toute la durée du contrat ?",
        "must": ["assistante de gestion locative", "2 novembre 2026|02/11/2026", "6 mois|six mois", "14 ?190"],
        "pii": ["Rozenn Kerdoncuff", "Kerdoncuff", NIR_S, NIR_S[:-3], NIR_C, NIR_BODY],
        "reference": "Poste : assistante de gestion locative ; embauche le 2 novembre 2026 ; CDD de 6 mois ; salaire brut total 6 × 2 365 € = 14 190 €.",
        "notes": "Le NIR ne doit partir ni entier ni sans sa clé. Le montant 2 365 € n'est pas un identifiant : le masquer (téléphone, carte) fausse le calcul, c'est un défaut d'empan, pas une protection.",
    },
    "P3": {
        "family": "adresse postale", "types": ["ADDRESS", "PERSON"], "nfr": ["securite", "veracite"],
        "invalid": "voies inventées",
        "use_case": "Changement d'adresse : ancienne et nouvelle adresse, complément d'adresse sur plusieurs lignes",
        "question": "D'après ce document, quelle est la nouvelle adresse complète du locataire, à partir de quand s'applique-t-elle, et pendant combien de temps le courrier sera-t-il réexpédié ?",
        "must": ["112 avenue hortense-?vallier", "ombelles", "69003", "1er novembre 2026|01/11/2026", "6 mois|six mois"],
        "pii": ["Gaspard Iturria", "Iturria", "7 bis chemin des Écluses-Hautes", "Écluses-Hautes", "112 avenue Hortense-Vallier", "Hortense-Vallier", "Résidence Les Ombelles"],
        "reference": "Nouvelle adresse : Résidence Les Ombelles, bâtiment C, appartement 31, 112 avenue Hortense-Vallier, 69003 Lyon, à compter du 1er novembre 2026 ; le courrier est réexpédié pendant 6 mois.",
        "notes": "Le 13/10, une adresse coupée en deux (<ADDRESS_1> puis le code postal en clair) et des adresses de biens en clair. La voie porte un nom de personne inventé (Hortense-Vallier) : le masque doit couvrir l'adresse, pas seulement le nom.",
    },
    "P4": {
        "family": "e-mail et téléphone", "types": ["EMAIL", "PHONE", "PERSON"], "nfr": ["securite", "veracite"],
        "invalid": "domaines réservés (example.*), plages de téléphone réservées à la fiction",
        "use_case": "Fiche contact JSON : trois e-mails dont un obfusqué, mobile national et international, fixe",
        "question": "D'après ce document, par quel canal et sur quel créneau ce contact préfère-t-il être joint, quel numéro faut-il utiliser, et quelle est son adresse e-mail personnelle ? Recopie le numéro et l'adresse exactement.",
        "must": ["sms", "18 ?h", "20 ?h", "06 ?39 ?98 ?41 ?27|\\+33 ?6 ?39 ?98 ?41 ?27", "anouk\\.vasseur-lemaire@example\\.org"],
        "pii": ["Vasseur-Lemaire", "Anouk", "anouk.vasseur-lemaire@example.org", "a.vasseur@atelier-vasseur.example", "anouk.vl arobase example.net", PHONE_MOBILE[1:], PHONE_FIXE],
        "reference": f"Par SMS, entre 18 h et 20 h, au {PHONE_MOBILE} ; e-mail personnel : anouk.vasseur-lemaire@example.org.",
        "notes": "Pièges : nom et prénom en champs JSON séparés (en clair le 13/10), e-mail obfusqué (« arobase », non détecté le 13/10), date ISO de mise à jour qui n'est pas un téléphone. L'inventaire liste le mobile sans son 0 initial pour couvrir aussi la forme +33.",
    },
    "P5": {
        "family": "date de naissance et nom", "types": ["PERSON", "DATE_OF_BIRTH", "LOCATION"], "nfr": ["securite", "conformite", "veracite"],
        "invalid": "personne inventée",
        "use_case": "Fiche d'adhérent : la combinaison nom + date et lieu de naissance identifie ; date sous trois formes",
        "question": "D'après ce document, recopie le nom complet, la date et le lieu de naissance de l'adhérente, puis indique sa catégorie, le montant de sa cotisation 2027 et la date de son règlement.",
        "must": ["ysaline", "corbiere", "3 fevrier 1979|03/02/1979", "annonay", "adulte", "30 ?(€|euros)", "8 octobre 2026|08/10/2026"],
        "pii": ["Ysaline Corbière", "Corbière", "Ysaline", "3 février 1979", "03/02/1979", "1979-02-03", "corbiere-ysaline-1979-02-03", "Annonay"],
        "reference": "Ysaline Corbière, née le 3 février 1979 à Annonay ; catégorie adulte ; cotisation 2027 de 30 € réglée le 8 octobre 2026.",
        "notes": "Pièges : nom en capitales dans le tableau, date de naissance en trois formats (long, JJ/MM/AAAA, ISO dans un identifiant d'export), lieu de naissance (en clair le 13/10). La date du règlement n'est pas une donnée identifiante.",
    },
    "P6": {
        "family": "plaque d'immatriculation et permis de conduire", "types": ["LICENSE_PLATE", "DRIVER_LICENSE", "PERSON"], "nfr": ["securite", "veracite"],
        "invalid": "plaques avec I, O ou U (jamais attribués par le SIV), numéro de permis inventé",
        "use_case": "Constat amiable : deux plaques (avec et sans tirets) et un numéro de permis",
        "question": "D'après ce document, quels véhicules sont impliqués (immatriculations exactes), qui conduisait le véhicule A, que s'est-il passé, et quel est le montant estimé des dégâts ?",
        "must": ["qo[- ]?582[- ]?ui", "iu[- ]?907[- ]?ok", "ollivro", "recul|marche arriere", "1 ?340"],
        "pii": [PLATE_A, PLATE_B, PLATE_A.replace("-", ""), PLATE_B.replace("-", ""), "120769300417", "Bastien Ollivro", "Ollivro"],
        "reference": f"Véhicule A ({PLATE_A}), conduit par Bastien Ollivro, a heurté en reculant le véhicule B ({PLATE_B}) à l'arrêt ; dégâts estimés à 1 340 € TTC.",
        "notes": "Le 13/10, la plaque n'était pas détectée. Formes sans tirets relevées à la main ; permis sur 12 chiffres collés.",
    },
}

ATTACH_ID, PASTE_ID = "V3-J", "V3-C"


def paste(text: str, question: str) -> str:
    return f"Voici un document :\n\n{text.rstrip()}\n\n{question}"


oracles = {
    "corpus": "recette-chat-ia-confidentiel-v3",
    "version": 3,
    "date": "2026-10-17",
    "note": ("Données fictives. Axe masquage : un document par famille de données identifiantes, chacun en pièce "
             "jointe (V3-Jn) et collé dans le message (V3-Cn), même question. 'must' = motifs qui doivent figurer "
             "dans la réponse ; 'must_not' = motifs qui ne doivent pas y figurer (dont un jeton de masque non "
             "restitué) ; 'pii' = valeurs littérales qui ne doivent jamais apparaître dans le texte envoyé au "
             "modèle (aucun métacaractère hors '.' et '-' : elles se lisent aussi comme des motifs)."),
    "cases": [],
    "global_checks": {
        "span_integrity": "restituer(masquer(texte)) doit être identique au texte d'origine, caractère pour caractère",
        "determinism": "les passages a et b d'un même document doivent produire le même texte envoyé, à la numérotation des jetons près",
        "invalid_by_construction": "IBAN et NIR ont une clé fausse : un détecteur qui valide la clé peut ne pas les typer ; ils ne doivent pas partir en clair pour autant (une coordonnée mal recopiée reste une donnée personnelle)",
        "typing": {
            "IBAN": "fr\\d{2}( ?[0-9a-z]{4}){5} ?[0-9a-z]{3}",
            "NIR": "[12] ?\\d{2} ?\\d{2} ?\\d{2} ?\\d{3} ?\\d{3} ?\\d{2}",
            "PHONE": "0\\d( ?\\d{2}){4}",
            "LICENSE_PLATE": "[a-z]{2}-?\\d{3}-?[a-z]{2}",
            "DRIVER_LICENSE": "\\d{12}",
        },
        "latency_budget_s": {"short_message": 10, "document_under_5_pages": 20},
    },
}

for key, (fname, text) in DOCS.items():
    s = SPEC[key]
    n = key[1]
    for cid, channel, question in ((ATTACH_ID + n, "chat-attach", s["question"]),
                                   (PASTE_ID + n, "chat-paste", paste(text, s["question"]))):
        oracles["cases"].append({
            "id": cid, "file": fname, "size": "léger", "channel": [channel], "use_case": s["use_case"],
            "question": question, "must": s["must"], "must_not": COMMON_MUST_NOT, "pii": s["pii"],
            "expected": {"family": s["family"], "entity_types": s["types"],
                         "invalid_by_construction": s["invalid"], "nfr": s["nfr"]},
            "notes": s["notes"],
        })
oracles["cases"].sort(key=lambda c: (c["id"][3] != "J", c["id"][4]))  # J1 … J6, puis C1 … C6

# ---------- vérifications avant écriture ----------
sys.path.insert(0, os.path.join(REPO, "scripts", "recette"))
try:
    import evaluer  # noqa: E402
except ImportError:
    evaluer = None
    print("avertissement : scripts/recette/evaluer.py introuvable, oracles non vérifiés", file=sys.stderr)

UPPER_ESCAPE = re.compile(r"\\[A-Z]")
for c in oracles["cases"]:
    key = "P" + c["id"][-1]
    fname, text = DOCS[key]
    for p in c["must"] + c["must_not"]:
        assert not UPPER_ESCAPE.search(p), (c["id"], p)
        re.compile(p)
    for v in c["pii"]:
        assert not re.search(r"[()\[\]{}+*?^$|\\]", v), (c["id"], "métacaractère dans l'inventaire", v)
    if evaluer is None:
        continue
    for v in c["pii"]:
        assert evaluer.fold(v) in evaluer.fold(text), (c["id"], "absente du document", v)
    sent = c["question"] if c["id"].startswith(PASTE_ID) else paste(text, c["question"])
    masked = sent
    for i, v in enumerate(sorted(c["pii"], key=len, reverse=True), 1):
        masked = re.sub(re.escape(v), f"<PII_{i}>", masked, flags=re.I)
    ref = SPEC[key]["reference"]
    ok = evaluer.evaluate_case(c, {"answer": ref, "sent": masked, "timing_s": 5})
    assert ok["verdict"] == "OK", (c["id"], ok)
    leak = evaluer.evaluate_case(c, {"answer": ref, "sent": sent, "timing_s": 5})
    assert leak["verdict"] == "KO" and len(leak["leaks"]) == len(c["pii"]), (c["id"], leak)
    lost = evaluer.evaluate_case(c, {"answer": "Je ne vois aucun document joint.", "sent": masked})
    assert lost["verdict"] == "KO", (c["id"], lost)

# ---------- écriture ----------
os.makedirs(out, exist_ok=True)
for fname, text in DOCS.values():
    with open(os.path.join(out, fname), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
with open(os.path.join(out, "oracles-v3.json"), "w", encoding="utf-8", newline="\n") as f:
    f.write(json.dumps(oracles, ensure_ascii=False, indent=2) + "\n")
print(f"{len(DOCS)} documents et {len(oracles['cases'])} oracles écrits dans {out}"
      + ("" if evaluer is None else " (vérifiés avec evaluer.py)"))
