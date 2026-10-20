"""maskai-recette : le driver de la recette MaskAI (procédure Aubier, PROCEDURE.md).

Le noyau lance ce processus, lui parle en lignes JSON (protocole 1,
`drivers/vkdriver/protocol.py`) et lui demande des actions métier. Chacune est
compilée ici, déterministe, sans jeton de modèle : un Chrome au profil dédié,
piloté par Playwright, fait ce que la procédure fait à la main. Chaque verdict
va au journal append-only de la recette, chaque preuve sous le dossier que
l'hôte donne pour l'action.

Ce que le driver garantit :
- la liste d'hôtes (`VK_DRIVER_HOSTS`) porte sur les navigations : une
  navigation du cadre principal vers un autre hôte (redirection suivie
  comprise) est défaite, retour à la page d'entrée, et l'action rend `host` ;
  les requêtes de sous-ressources et les WebSockets vers d'autres hôtes ne sont
  pas bloquées (le cache HTTP du navigateur reste actif, les mesures restent
  justes) mais relevées dans `third_party_hosts` ;
- le garde-dialogue : un dialogue JavaScript n'est jamais accepté ni refusé par
  le driver ; chaque action le signale (`dialog`) jusqu'à ce qu'un humain l'ait
  fermé dans Chrome ; une modale inconnue est signalée de même ; une modale
  connue (`KNOWN_MODALS`) est fermée par son bouton documenté ;
- le délai d'abandon : 3 × le budget du cas, journalisé KO disponibilité ;
- la reprise : observations et journal sur disque (`VK_DRIVER_WORK_DIR`) ; un
  cas déjà journalisé rend son verdict sans rien ajouter ;
- la traçabilité du verdict : `inputs_sha256`, l'empreinte de ce qui fait le
  verdict hors du dossier du driver (vkdriver, evaluer.py, journal.py,
  cases.json, oracles, fichiers du corpus), est dans la version du hello et sur
  chaque ligne de journal.

Ce que la v2 ajoute (debrief du 2026-10-17, docs/technical/debriefs/) :
- la réponse est le texte du corps (`.prose-llm`) de la dernière carte, jamais
  la carte entière ; l'en-tête (panneau de garde, bannière du modèle) va à part
  dans `guard.json` (D5) ; une réponse est finie quand le flux du serveur l'est
  (<END>/<ABORT>) ET que son texte à l'écran n'a plus bougé depuis 2 s, bouton
  d'arrêt disparu (D6) ;
- la puce de pièce jointe suit une machine à états explicite (absent →
  uploading → indexing → ready | failed) ; la question ne part que sur une puce
  prête, relue au moment d'envoyer (D7) ;
- le texte envoyé est lu dans le popover « Chiffré » du compositeur, que le
  driver ouvre et referme lui-même dans la même action (`sent.json`, D4 ; depuis
  la v2.2, le repli d'une carte sans bouton « Détails ») ;
- au délai d'abandon, le driver clique sur le bouton d'arrêt avant de rendre la
  main : aucune génération laissée en vol (D2), et la ligne dit ce qui était
  parti (D8) ;
- trois actions de plus : choisir_modele et recherche_web règlent le compositeur
  et relisent ce qu'il affiche ; conversation_longue pose n questions dans la
  même conversation. Les cas `model`, `web` et `long` les utilisent ;
- `ui_profile_sha256`, l'empreinte de `SELECTORS`, `KNOWN_MODALS`, du motif du
  flux et des constantes de fin de réponse, est sur chaque ligne de journal : un
  changement de sélecteur s'y voit, ce que `inputs_sha256` ne montre pas (D12) ;
- les captures masquent la barre latérale (l'historique) et le bloc du compte ;
- un cas sans `budget_s` prend le budget de sa classe (`CLASS_BUDGET_S`) ;
- la seconde recette (PROCEDURE-2.md, grille M0–M7) : cas de configuration jugés sur `@model` et
  `@web`, configuration épinglée réappliquée par nouvelle_conversation si l'interface l'a perdue,
  modèle et mode web lus sur chaque ligne, document joint cherché dans le texte envoyé,
  empreinte du texte envoyé pour comparer les passages a et b (`replay_of`) ;
- relevé du 18/10 (plateforme revenue de maintenance) : le compositeur n'est plus un <form>
  (sélecteurs de structure sans `form` en premier), quatorze modèles tous ouverts (data-value =
  identifiant fournisseur/modèle, texte = libellé affiché), un bouton de dictée (micro,
  `data-status`) que le driver n'active jamais et dont un état autre que `idle` est un refus
  `precondition`, et une page d'erreur d'authentification (/api/auth/error) qui est un refus
  `precondition` « session perdue » plutôt que 15 s d'attente d'une zone de saisie.

Ce que la v2.2 ajoute (décision du fondateur du 18/10, recette 3 « de bout en bout », en boîte
noire : le texte transmis au modèle est observé à chaque cas) :
- le texte envoyé est lu, la réponse finie, dans le panneau « Détails » de la dernière carte de
  réponse : le driver clique le bouton de cette carte-là (jamais un rang parmi les boutons de la
  page, jamais le bouton de révélation des PII ni un bouton du corps de la réponse), lit le
  contenu ouvert quand il ne bouge plus, et le referme d'un second clic s'il l'a bien ouvert
  (`sent.json`, `source: "card-details"`). Le texte envoyé est le texte seul : le bloc de texte
  du panneau, sinon son texte privé du libellé (`details_label`) ; c'est lui que portent
  `sent_sha256` et la comparaison `replay_of`. La capture `answer.png` est prise avant le clic
  (le clic fait défiler l'en-tête de la carte). Le popover « Chiffré » du compositeur décrit
  l'état AVANT l'envoi (« Aucune PII détectée — rien à anonymiser pour l'instant » le 18/10 : la
  recette 2 n'a jamais vu le texte envoyé) ; il n'est plus qu'un repli, pour une carte sans
  bouton « Détails » (`source: "composer-popover"`). Un panneau qui ne s'ouvre pas, qui ne montre
  pas la question, ou qu'une erreur ou le délai de l'hôte empêche de lire reste
  `source: "card-details"` avec `rejected`, sans repli ;
- le bouton de révélation des PII de la carte n'est jamais pressé : son état au moment de la
  lecture est noté (`reveal_pressed`), jamais changé ;
- l'évaluateur lit un lien « [x](pii://…) » resté brut comme x (`evaluer.normalise`), et note
  « lien pii:// rendu à l'utilisateur » quand la réponse en montre un ; `inputs_sha256` change
  avec lui.

Ce que la v2.3 ajoute (décision du fondateur du 19/10 : la recette 3 rejouée avec sa grille entière ;
PROCEDURE-3.md §6, E1 à E11) :
- un registre des conversations que le driver crée (`conversations.json` du dossier de travail, avec
  le cas qui a créé chacune) ; `same_conversation_as` s'y résout ;
- la recherche ⌘K (`lister_conversations {case}` d'un cas qui porte une question), le renommage et la
  suppression (`renommer_conversation`, `supprimer_conversation`, irréversibles) de la seule
  conversation du registre que nomme `same_conversation_as`, retrouvée dans la barre latérale par son
  adresse exacte, jamais par son rang ni par son titre seul, et refusée `precondition` avant tout clic
  si le registre ne la connaît pas ; la suppression passe par la confirmation de l'application, une
  modale `own` ;
- l'état du bouton de dictée relu par choisir_modele (« ; micro : idle ») et sur chaque ligne, jamais
  pressé ; l'enquête « Votre avis » ouverte depuis son entrée et fermée par sa croix (`ouvrir_avis`),
  jamais envoyée ; `modals_closed` sur chaque ligne (les modales connues que la garde a fermées) ;
- la liste `accept` du champ de fichier ; la classe d'erreur d'une puce en échec, sans nom d'hôte ni
  pile (le libellé brut ne va qu'à la preuve `chip.json`) ; le diagnostic d'un échec silencieux (ligne
  d'état du compositeur, alertes, statut HTTP et derniers événements du flux, preuve `failure.json`),
  classé et expurgé sur la ligne ;
- sur la ligne d'une question : `sent_source`, `sent_tokens` (les types des jetons envoyés) et
  `pii_declared` (le compte du panneau de garde) ; la note `replay_of` compare les ensembles masqués des
  passages a et b ;
- une observation partagée (@load, @list, @model, @web) ne juge qu'un cas : celui qui la lit le
  premier, ou un cas sondé avant qu'elle soit faite (N7-1 et N7-2 sur une même liste) ;
- la sonde d'interface `sonder_interface`, en lecture seule (`probe.json`) ; `_judge`, le jugement de
  verdict_oracle, que `scripts/recette/correct.py` rejoue hors ligne pour écrire les lignes de
  correction.

Ce que la revue de la v2.3 durcit (20/10, avant le passage réel ; I1 à I7, m1 à m15 ; PROCEDURE-3 §6) :
- une entrée que le driver n'identifie pas n'est jamais cliquée : la recherche par une entrée de la
  barre latérale dont le texte ou le libellé dit « search » ou « recherch » (jamais le bouton de
  dictée, le compositeur ni une carte), « Votre avis » par ses libellés exacts ; sinon refus
  `precondition` avant tout clic (depuis le deuxième tour : NON TESTÉ, les candidats vus dans la preuve
  seulement) ;
- le registre n'admet qu'une conversation dont la page a pris l'adresse, absente de la barre
  latérale avant la question ou le téléversement ; jamais une ligne apparue dans la barre latérale ;
- le menu d'une conversation : un déclencheur qui annonce un menu (`aria-haspopup`), jamais au-dessus
  de la ligne dans un groupe titré ; des entrées en liste blanche sur leur texte entier, seules de
  leur sorte, aucune qui vise plusieurs conversations ; le champ du titre et la confirmation apparus
  après le clic ; une confirmation qui parle de supprimer une conversation, sinon Échap ;
  « suppression confirmée » notée au registre dès son clic, jamais refait ;
- M8-5 et M8-6 se jugent sur l'état de leur conversation, relu à son adresse (« titre relu « … » »,
  « plus listée »), jamais sur la liste entière ; une observation faite pour un cas ne juge que lui ;
- `internal_error_shown` sur la ligne, dans `attach` et dans les résultats : une erreur interne
  montrée à l'utilisateur (ligne d'état, alerte, toast, puce, réponse) ; l'extrait expurgé quitte la
  ligne pour `failure.json`, et l'expurgation prend aussi l'hôte qui suit ENOTFOUND, lookup, connect
  to, host… et un `nom:port` nu ;
- la note `replay_of` compare les entités masquées des deux textes envoyés (leurs types seuls dans la
  note), à défaut leurs types (« types masqués … »).

Ce que le deuxième tour de la revue change (20/10, `.superpowers/fix-round-2.md`, décisions R2-1 à R2-6) :
- un cas que le pilote refuse par sa propre garde, avant tout clic irréversible, est NON TESTÉ « refus du
  pilote : … », sans ENF (`untested` sur l'observation) ; KO reste pour ce que MaskAI a fait (R2-1) ;
- une autre conversation disparue pendant un renommage ou une suppression (relue après 2 s) : KO sécurité,
  « suppression en cascade » et leur nombre au registre, `cascade_delete` dans le résultat ; leurs adresses
  dans `conversation.json` seulement (R2-2) ;
- menus et confirmations : une entrée lue sans points de suspension ni indice de raccourci ; une
  confirmation jugée sur son texte entier, refusée seulement quand elle vise explicitement plusieurs
  conversations ; « Continuer », « Oui » quand ils sont le seul bouton qui n'annule pas d'une fenêtre
  d'alerte ; la sonde dit le verdict du menu pour M8-5 et M8-6 (R2-3) ;
- aucun texte de la page n'atteint le harnais sans `harness_text` (expurgé, jamais un titre listé) ; les
  candidats d'une entrée, les entrées d'un menu, les avis et les confirmations ne vont qu'aux preuves ;
  `internal_error_shown` se calcule sur les avis d'erreur seuls, lus pour chaque tour et chaque action de
  M8 (R2-4) ;
- le pilote ne répond toujours à aucune boîte : un dialogue relevé pendant l'action, même déjà fermé par
  l'humain, reste celui de l'action (le journal des dialogues) ; la confirmation native d'une suppression
  est laissée à l'humain, NON TESTÉ ; les lectures de la sonde et des entrées sont des appels bornés,
  jamais `locator.count()` ni `is_visible()` (R2-5) ;
- les clics : l'envoi, l'arrêt, le modèle et la recherche web dans le compositeur seul ; le déclencheur
  du menu dans la ligne de la conversation seule ; « nouvelle conversation » par le lien validé ou un
  libellé exact ; « plus listée » après une requête 2xx et l'absence tenue 2 s, attendue 3 × le budget,
  dont le délai est `timing_s` ; « suppression demandée » notée avant le clic ; `correct.py` ne rejoue
  que l'observation de la ligne en vigueur (`line_sha256`) (R2-6).

Le troisième tour (20/10, `.superpowers/review-v2.3-fix2.md`) : une confirmation n'est acceptée que si elle nomme
exactement une conversation, sans nom de conversation au pluriel hors de ce nom (I-2) ; la queue d'une entrée de menu
se lit par une seule règle des deux côtés, `MENU_TAIL` (I-1) ; une entrée ou un bouton acceptés puis introuvables, une
palette non reconnue, des résultats non reconnus sont NON TESTÉ, le registre intact (I-1, M-1, M-3) ; un dialogue
relevé avant le propre refus d'une action reste KO sécurité, et la confirmation native ouverte tard reste celle de
l'application (I-3) ; « plus listée » ne compte qu'une écriture 2xx de l'application (M-5). Le quatrième tour : un
nombre ou un quantificateur devant un nom de conversation au pluriel refuse, sur le texte entier ; un titre court cité
en entier est expurgé ; un abandon avec un dialogue relevé est KO sécurité, le refus garde son `extra`. Le
cinquième : « autre(s) » / « other(s) » comme nom, ou après un nombre ou un déterminant, refuse la confirmation.
Le sixième (le menu vu en direct) : sous un déclencheur qui nomme les actions de la conversation, une seule entrée de
suppression en liste blanche suffit ; renommer sans entrée de renommage est un refus du pilote (NON TESTÉ).

Environnement (posé par l'hôte) : VK_DRIVER_PROFILE_DIR, VK_DRIVER_WORK_DIR,
VK_DRIVER_HEADLESS, VK_DRIVER_CHANNEL, VK_DRIVER_HOSTS ; MASKAI_ENTRY_URL
remplace `ENTRY_URL` (le banc d'essai sur un autre port).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import logging
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

DRIVER_DIR = Path(__file__).resolve().parent
REPO = DRIVER_DIR.parent.parent
RECETTE_DIR = REPO / "docs" / "functional" / "use-cases" / "recettage-chat-ia"
# Les dépendances hors du dossier du driver (règle R-T8a du chantier de paquetage). Installé, le
# driver vit seul sous <racine>/<nom>/<version>/, sans dépôt autour : le paquet porte vkdriver,
# evaluer.py et journal.py sous vendor/ (pack.json les nomme). vendor/ d'abord, le dépôt ensuite
# (le développement, inchangé). inputs_sha256 les nomme par leur chemin dans le dépôt, d'où qu'ils
# soient chargés : les mêmes octets donnent la même empreinte.
VENDOR_DIR = DRIVER_DIR / "vendor"


def _first_dir(candidates: tuple, needs: tuple) -> Path:
    """Le premier dossier qui porte tous les fichiers `needs` ; à défaut le dernier (le dépôt), pour
    que l'erreur d'import nomme le chemin du développement."""
    for d in candidates:
        if all((d / n).is_file() for n in needs):
            return d
    return candidates[-1]


VKDRIVER_DIR = _first_dir((VENDOR_DIR / "vkdriver", DRIVER_DIR.parent / "vkdriver"), ("protocol.py", "hash_dir.py"))
SCRIPTS_DIR = _first_dir((VENDOR_DIR / "recette", REPO / "scripts" / "recette"), ("evaluer.py", "journal.py"))
sys.path.insert(0, str(VKDRIVER_DIR.parent))

from vkdriver.hash_dir import hash_files, list_files  # noqa: E402
from vkdriver.protocol import DriverRefusal, remaining_s, serve  # noqa: E402

try:
    from playwright.sync_api import Error as PWError
    from playwright.sync_api import TimeoutError as PWTimeout
    from playwright.sync_api import sync_playwright
except ImportError:  # the hello still goes out; every browser action says why it cannot run
    sync_playwright = None

    class PWError(Exception):
        pass

    class PWTimeout(PWError):
        pass

NAME = "maskai-recette"
VERSION = "0.2.0"

# La page d'entrée de MaskAI : mesurer_chargement la charge, nouvelle_conversation y revient
# quand aucun bouton « nouvelle conversation » n'est trouvé.
ENTRY_URL = "https://app.maskai.fr/new"
# Le banc d'essai (fixture/serve.py, port par défaut) quand la liste d'hôtes est 127.0.0.1.
FIXTURE_ENTRY_URL = "http://127.0.0.1:8765/new"

# Ce que le driver cherche dans la page. Les listes sont essayées dans l'ordre. Chaque entrée dit ce
# qui en a été vu sur app.maskai.fr (« vu le 13/10 », « vu le 17/10 ») ; « à confirmer » marque une
# hypothèse que seul le banc d'essai (fixture/index.html) a validée, à vérifier au premier passage
# réel (runbook §8.3). Les aria-label sont localisés (anglais ou français selon le profil) : les
# sélecteurs structurels passent en premier.
SELECTORS = {
    # Zone de saisie (annexe B : setter natif, événement input, puis un espace tapé). Vu le 13/10.
    "textarea": "textarea",
    # Champ de fichier caché derrière le trombone. Vu le 13/10.
    "file_input": 'input[type="file"]',
    # Bouton « nouvelle conversation » : le lien vu le 17/10 ; à défaut, dans la barre latérale seulement, un
    # bouton ou un lien dont le libellé ou le texte replié est exactement `new_conversation_labels` (revue de
    # la v2.3, round 2, click-targets-8 : jamais « Effacer et démarrer une nouvelle conversation ») ; sinon
    # retour à ENTRY_URL.
    "new_conversation": ['a[href="/new"][data-slot="button"]'],
    "new_conversation_labels": r"^(?:nouvelle conversation|new chat|new conversation)$",
    # Bouton d'envoi quand Entrée n'envoie pas (CSS), dans le compositeur seul (round 2, click-targets-3 :
    # jamais « Send feedback », jamais dans une fenêtre). À confirmer.
    "send_button": ['button[aria-label*="Envoyer"]', 'button[aria-label*="Send" i]'],
    # Bouton d'arrêt visible pendant la génération (CSS, puis libellé). « Stop generating » lu le
    # 13/10 ; un bouton d'arrêt visible sur la capture N1-3 du 17/10 ; l'aria-label est à confirmer.
    "stop_button": ['button[aria-label*="Stop" i]', 'button[aria-label*="Arrêter" i]'],
    "stop_text": r"stop generating|arrêter",
    # Cartes de réponse ; la dernière est la réponse (CSS). Vu le 17/10 : <article aria-label="MASKAI
    # response"> contenant <div class="pl-6"><div class="prose-llm">. Les suivants à confirmer.
    "assistant_message": ['article:has(.prose-llm)', 'article[aria-label="MASKAI response"]',
                          '[data-role="assistant"]', '[data-message-author-role="assistant"]',
                          '.message.assistant', '[data-testid*="assistant-message"]'],
    # Corps de la réponse dans la carte (CSS). Vu le 17/10. Le reste de la carte est son en-tête.
    "answer_body": ".prose-llm",
    # L'en-tête, lu en texte : vu le 17/10 « Aucune donnée personnelle détectée L'Aiguilleur : PASS
    # maskai google/eu.claude-haiku-5-5 » et « Anonymisé → <PERSON_1> <CREDIT_CARD_1> L'Aiguilleur :
    # PASS·Le Chirurgien : 2 PII détectées Détails maskai google/eu.claude-haiku-5-5 » ; le balisage
    # de ces blocs n'a pas été relevé, d'où des motifs sur le texte. « · en direct » suivait la
    # bannière d'une réponse coupée (N1-3) : marque de génération en cours, hypothèse.
    # Le 18/10 (M3 sur quatorze modèles) : la bannière nomme le modèle sans fournisseur pour les
    # Mistral et les anciens Claude (« maskai claude-haiku-4-5 », « maskai mistral-large-4 ») et
    # avec deux barres pour deepseek (« maskai tensorix/deepseek/deepseek-v4-pro-0813 ») : le jeton
    # entier après « maskai », barres comprises, est le modèle ; la forme à une barre du 17/10
    # (« google/eu.claude-haiku-5-5 ») en est un cas.
    "model_banner": r"\b[a-z][a-z0-9_-]*/[a-z0-9][a-z0-9._:/-]*",
    "brand_model": r"\bmaskai\s+([a-z][a-z0-9][a-z0-9._:/-]*)",
    "brand": r"\bmaskai\s*$",
    "live_marker": r"en direct|\blive\b",
    "guard_pii": r"anonymis|<[A-Z][A-Z_]*_\d+>|\b[1-9]\d*\s*PII\b",
    "guard_clean": r"aucune donn[ée]e personnelle|no personal data",
    "mask_token": r"<[A-Z][A-Z_]*_\d+>",
    # Puce de pièce jointe. Vu le 17/10 : un bouton « Retirer <fichier> » (le v1 le lisait comme une
    # puce « attachée », D7) ; vus les 12 et 13/10 : « Indexing in progress », puis « Failed ». La
    # puce est l'ancêtre de ce bouton qui porte du texte. À confirmer : l'état « prête » (aucun
    # marqueur relevé), l'indicateur d'activité, les sélecteurs de secours de la puce.
    "attach_remove": ['button[aria-label^="Retirer"]', 'button[aria-label^="Remove"]'],
    "attach_chip": ['[data-attachment]', '[data-slot*="attachment"]', '.chip'],
    "attach_busy": '[aria-busy="true"], [role="progressbar"], .animate-spin, [data-state="loading"]',
    "attach_failed": r"failed|échec|erreur|error",
    "attach_uploading": r"upload|téléversement|envoi en cours",
    "attach_pending": r"indexing|in progress|en cours|processing|indexation",
    "attach_ready": r"\bready\b|\bindexed\b|\bprêt|\bindexé",
    # Conversations de la barre latérale (CSS). Le premier vu le 17/10 ; les suivants à confirmer.
    "conversation_items": ['a[href*="/conversation/"]',
                           '#conversations li', 'nav a[href*="/c/"]', 'nav a[href*="/chat/"]',
                           '[data-testid*="conversation"]', 'aside li'],
    # Texte envoyé au modèle (v2.2) : le bouton « Détails » de la carte de réponse, cliqué la réponse
    # finie, dans la dernière carte seulement. Relevé le 18/10 dans le DOM d'une carte stockée par la
    # recette 2 : après le panneau de garde (« L'Aiguilleur : PASS · Le Chirurgien : 5 PII
    # détectées », boutons pii-chip <PHONE_1> …), <button data-slot="collapsible-trigger"
    # data-variant="ghost" data-size="xs" type="button" aria-expanded="false" aria-label="Voir le
    # texte anonymisé envoyé au LLM" data-state="closed"><span>Détails</span><svg …/></button>.
    # Sélecteurs Playwright, la structure d'abord, cherchés dans la carte : le préfixe « article »
    # retiré, `:has-text("…")` lu par le driver (details_triggers). À confirmer au premier passage
    # réel : le contenu ouvert, attendu en collapsible Radix (data-state="open") dans la même
    # carte ; le driver en lit le bloc de texte (`sent_text`), sinon tout le texte privé du libellé
    # (`details_label`), et en garde le texte entier et le HTML (sent.json). Un candidat qui est le
    # bouton de révélation des PII (`pii_reveal`) ou qui est dans le corps de la réponse
    # (`answer_body`) n'est jamais retenu.
    "card_details_trigger": ['article button[data-slot="collapsible-trigger"][aria-label*="anonymis" i]',
                             'article button[data-slot="collapsible-trigger"]:has-text("Détails")',
                             'article button:has-text("Détails")'],
    "card_details_content": '[data-slot="collapsible-content"]',
    # Le libellé en tête du panneau, retiré du texte envoyé (le banc : « Texte anonymisé envoyé au
    # LLM : ») ; à défaut, une première ligne qui finit par « : » et que la question ne contient
    # pas (details_text). Formulation réelle à relever au premier passage (sent.json `label`).
    "details_label": (r"^\s*(?:texte\s+anonymis[ée]\s+envoy[ée]\s+au\s+(?:llm|mod[èe]le)"
                      r"|anonymi[sz]ed\s+text\s+sent\s+to\s+the\s+(?:llm|model))\s*:?\s*"),
    # Le bouton qui révèle les PII dans la réponse, juste après « Détails » (18/10, même carte) :
    # <button type="button" aria-pressed="false" data-state="closed" data-slot="pii-reveal-toggle" …>.
    # Jamais pressé par le driver ; son état au moment de la lecture est noté (`reveal_pressed`).
    "pii_reveal": 'button[data-slot="pii-reveal-toggle"]',
    # Le repli (v2.2), pour une carte sans bouton « Détails ». Vu le 17/10 : la pastille « Chiffré /
    # Encrypted — no PII detected… » du compositeur, déclencheur de popover ; le 18/10, son popover
    # décrit l'état AVANT l'envoi (« Aucune PII détectée — rien à anonymiser pour l'instant »). À
    # confirmer : son bloc de texte, sa croix. Le déclencheur retenu est celui du compositeur (le
    # plus proche de la zone de saisie), jamais un bouton d'une carte de réponse.
    "sent_trigger": 'button[aria-haspopup="dialog"][data-slot="popover-trigger"]',
    "sent_popover": '[role="dialog"][data-slot="popover-content"]',
    # Le bloc de texte du popover et, depuis la v2.2, du panneau « Détails » (CSS, dans l'ordre).
    "sent_text": ['[data-slot*="sent"]', 'pre', 'code', 'blockquote'],
    "popover_close": ('[data-slot="popover-close"], button[aria-label*="Fermer" i], '
                      'button[aria-label*="Close" i]'),
    # Libellé du panneau « texte envoyé au modèle » : « SENT TO THE LLM » vu le 13/10, disparu le
    # 17/10 ; le secours du popover.
    "sent_label": r"sent to the llm|envoy[ée] au mod[èe]le|texte envoy[ée]",
    # Compositeur (v2) : le bouton « Model: <modèle> » qui ouvre une liste d'options, le bouton
    # « Web search: <mode> » et son data-mode (relevés le 17/10 par le fondateur). À confirmer : le
    # rôle de la liste (listbox ou menu), le mode web en cycle ou en menu, les mots des modes.
    # Par structure d'abord (le 17/10 : le bouton du compositeur qui ouvre une liste,
    # aria-haspopup="listbox") ; les libellés, localisés (au premier essai de la recette 2,
    # l'interface française ne les avait pas), en secours.
    # Le 18/10 : le compositeur est un <div>, pas un <form> ; la structure sans `form` en premier.
    # Quatorze options ce jour-là (role="option", data-value = « fournisseur/modèle », texte = libellé
    # affiché : « claude haiku 5-5 » pour google/eu.claude-haiku-5-5) dans une liste cmdk.
    "model_button": ['button[aria-haspopup="listbox"]', 'form button[aria-haspopup="listbox"]',
                     'button[aria-label^="Model:"]', 'button[aria-label^="Modèle"]'],
    "model_prefix": r"^\s*(?:model|modèle)\s*:\s*",
    "listbox": '[role="listbox"]',
    "option": '[role="option"]',
    # Par structure d'abord (le 17/10 : le bouton qui porte data-mode="auto|off|on"), les libellés
    # en secours.
    # Le 18/10 : un cycle de clics auto → on → off → auto, sans menu ; data-mode fait foi (le texte
    # du bouton cumule les libellés le temps de l'animation).
    "web_button": ['button[data-mode]', 'form button[data-mode]',
                   'button[aria-label^="Web search:"]', 'button[aria-label^="Recherche web"]'],
    "web_menu": '[role="menu"], [role="listbox"]',
    "web_menu_item": '[role="menuitemradio"], [role="menuitem"], [role="option"]',
    "web_words": {"off": r"\boff\b|désactiv|jamais|never", "auto": r"\bauto", "on": r"\bon\b|activ|toujours|always"},
    # Zones masquées sur toute capture (Playwright `mask`) : la barre latérale, qui montre l'historique
    # des conversations, et le bloc du compte en bas. Vu le 17/10 : nav[aria-label="Main navigation"]
    # dans un aside, le compte en bas de la barre (les captures publiées du 17/10 le montraient, D10) ;
    # les data-slot sont à confirmer.
    "mask": ['nav[aria-label="Main navigation"]', 'aside', '[data-slot="sidebar"]',
             '[data-slot="sidebar-footer"]', '[data-slot="avatar"]'],
    # Page de maintenance, vue au premier essai réel de la recette 2 : une carte « MAINTENANCE EN
    # COURS / ON REVIENT TRÈS VITE ! / Mask AI FR fait une petite pause … » sur app.maskai.fr, sans
    # zone de saisie. Motif sur le texte de la page replié (casse et accents), retenu seulement
    # quand aucune zone de saisie n'est visible : les mêmes mots dans une réponse ne comptent pas.
    "maintenance": r"maintenance en cours|on revient tr[èe]s vite|maintenance in progress",
    # Bouton de dictée (vu le 18/10 : aria-label « Dictate a message in real time (microphone) »,
    # aria-pressed, data-status="idle"). Jamais activé par le driver ; un état autre que idle au
    # début d'une action (dictée lancée par l'utilisateur) est un refus `precondition`.
    "mic_button": 'button[data-status][aria-pressed]',
    # Page d'erreur d'authentification (vue le 18/10 lors d'une coupure réseau : l'application
    # renvoie sur /api/auth/error, sans zone de saisie) ; motif sur l'URL de la page.
    "auth_error": r"/api/auth/error",
    # Fenêtres modales (CSS).
    "modal": '[role="dialog"], [role="alertdialog"], [aria-modal="true"], .modal',
    # -- v2.3 (relevés des 18 et 19/10 ; « à confirmer » : balisage jamais lu sur la plateforme) --------
    # La barre latérale (vue le 17/10 : nav[aria-label="Main navigation"] dans un aside) : le driver n'y
    # cherche une conversation du registre que par son adresse exacte (CSS).
    "sidebar": 'aside, [data-slot="sidebar"], nav[aria-label="Main navigation"]',
    # Le chemin d'une conversation, motif sur location.pathname (vu le 17/10 : /conversation/<id>).
    "conversation_path": r"^/conversation/[^/?#]+$",
    # L'entrée de la recherche ⌘K en tête de la barre latérale (vue le 18/10 : « Search conversations »
    # et l'indice ⌘K ; balisage à confirmer). La recherche s'ouvre d'abord au clavier (ControlOrMeta+K),
    # par cette entrée en secours (Playwright), et seulement par un candidat identifié (revue de la v2.3,
    # I4) : dans la barre latérale, hors carte de réponse et compositeur, jamais le bouton de dictée, son
    # texte ou son libellé suivant `search_words` ; aria-keyshortcuts seulement au jeton exact.
    "search_entry": ['button:has-text("Search conversations")', '[role="button"]:has-text("Search conversations")',
                     'a:has-text("Search conversations")', 'button:has-text("Rechercher")',
                     '[role="button"]:has-text("Rechercher")', '[aria-keyshortcuts~="Control+K" i]',
                     '[aria-keyshortcuts~="Meta+K" i]'],
    "search_words": r"search|recherch",
    # La palette de recherche, attendue en cmdk comme la liste des modèles (à confirmer) : son champ, ses
    # résultats et son état vide, cherchés dans la palette seule (CSS).
    "search_input": ['[cmdk-input]', '[role="dialog"] input[role="combobox"]', '[role="dialog"] input[type="search"]',
                     '[role="dialog"] input'],
    "search_result": ['[cmdk-item]', '[role="option"]', 'a[href*="/conversation/"]'],
    "search_empty": '[cmdk-empty]',
    # Le menu d'une conversation de la barre latérale (jamais lu ; à confirmer par sonder_interface) : son
    # déclencheur, cherché dans l'élément de cette conversation seule et révélé au survol ; seulement un
    # bouton qui annonce un menu (aria-haspopup, revue I4 et m15 : aucun bouton deviné par son libellé),
    # chaque sélecteur à tous les niveaux avant le suivant ; au-dessus de la ligne, jamais dans un élément
    # qui porte un titre de groupe (`group_heading`, I7). Le menu et ses entrées (CSS) ; les entrées
    # cliquées sont celles des listes blanches (motifs sur le texte replié entier, I7).
    "conversation_menu_trigger": ['button[aria-haspopup="menu"]', '[role="button"][aria-haspopup="menu"]',
                                  '[data-slot*="menu-trigger"][aria-haspopup="menu"]', 'button[aria-haspopup="true"]'],
    # Round 2 (click-targets-2, registry-4) : le déclencheur est cherché niveau par niveau, du lien jusqu'à la
    # ligne de la conversation (`conversation_row`, sans jamais monter au-dessus), et jamais dans un élément qui
    # porte un titre ou une étiquette de groupe hors du lien (shadcn : div[data-sidebar=group-label]).
    "group_heading": ('h1, h2, h3, h4, h5, h6, [role="heading"], [data-sidebar="group-label"], '
                      '[data-slot="sidebar-group-label"]'),
    "conversation_row": 'li, [role="listitem"], [data-sidebar="menu-item"], [data-slot="sidebar-menu-item"]',
    "menu": '[role="menu"]',
    "menu_item": '[role="menuitem"]',
    # Round 2 (robustness-1, R2-3) : sur le texte replié de l'entrée, sans les points de suspension finals ni
    # l'indice de raccourci (kbd, data-slot*=shortcut, « ⌘⌫ ») ; « Modifier le titre » et « Déplacer vers la
    # corbeille » sont des formes d'une seule conversation.
    "menu_rename": (r"^(?:renommer|rename|modifier le titre|edit title)"
                    r"(?: (?:la |cette |this )?(?:conversation|discussion|chat))?$"),
    "menu_delete": (r"^(?:supprimer|delete|deplacer vers la corbeille|move to trash)"
                    r"(?: (?:la |cette |this )?(?:conversation|discussion|chat))?$"),
    # Le champ du nouveau titre après « Renommer » (à confirmer) : l'élément qui a pris le focus, sinon un
    # champ texte de la barre latérale ou d'une fenêtre (CSS) ; jamais la zone de saisie du compositeur ni
    # un champ de recherche. Le bouton qui valide une fenêtre de renommage, par son texte (motif).
    "rename_field": ['aside input[type="text"]', 'aside input:not([type])', '[role="dialog"] input[type="text"]',
                     '[role="dialog"] input:not([type])', 'aside [contenteditable="true"]'],
    "rename_submit": r"enregistrer|save|renommer|rename|valider|confirmer|confirm",
    # L'entrée de « Votre avis » (vue le 18/10 : button[aria-label="Send feedback"], fixé en bas à gauche) ;
    # les suivantes à confirmer. Seulement un libellé exact (revue I5 : aucun bouton deviné, jamais une note
    # 👍/👎 d'une carte), hors carte de réponse et compositeur ; sinon ouvrir_avis refuse `precondition`,
    # avant tout clic, en nommant ce qu'il a vu (CSS).
    "feedback_entry": ['button[aria-label="Send feedback"]', 'button[aria-label="Votre avis"]',
                       'button[aria-label="Envoyer un avis"]', 'button[aria-label="Donner votre avis"]',
                       'button[aria-label="Donnez votre avis"]'],
    "feedback_words": r"feedback|avis|retour",
    # Ce que dit la page quand une question reste sans réponse (E10) : la ligne d'état sous le compositeur
    # (vue le 18/10 : #chat-input-status, role="status", aria-live="polite"), les alertes et les toasts
    # (à confirmer) (CSS).
    "input_status": '#chat-input-status',
    "notices": '[role="alert"], [role="status"], [data-sonner-toast], [data-slot*="toast"]',
}
# Les entrées de SELECTORS qui sont des motifs de texte ou d'URL, pas des adresses dans la page :
# sonder_interface ne les résout pas.
SELECTOR_PATTERNS = ("stop_text", "model_banner", "brand_model", "brand", "live_marker", "guard_pii", "guard_clean",
                     "mask_token", "attach_failed", "attach_uploading", "attach_pending", "attach_ready",
                     "details_label", "sent_label", "model_prefix", "web_words", "maintenance", "auth_error",
                     "conversation_path", "menu_rename", "menu_delete", "rename_submit", "search_words",
                     "feedback_words", "new_conversation_labels")
# Modales connues : reconnues par leur sélecteur et fermées par leur bouton documenté (ou une touche,
# « key:<touche> »), jamais par « OK » ni « Envoyer ». Une modale `own` est une fenêtre que le
# driver ouvre lui-même : connue seulement quand c'est lui qui l'a ouverte ; ouverte par quelqu'un
# d'autre, elle reste une modale inconnue, refusée `dialog`.
KNOWN_MODALS = [
    # Bannière de cookies du banc d'essai. À confirmer pour MaskAI (aucune relevée les 12 et 13/10).
    {"name": "cookies", "selector": "#cookies", "dismiss": "#cookies button#accept"},
    # « Votre avis » (vu le 17/10) : enquête de satisfaction Radix, un formulaire avec le groupe radio
    # suggestion/problème et une croix de fermeture. Reconnue par sa structure (pas par son texte,
    # localisé) et fermée par la croix, jamais par « Envoyer ».
    {"name": "avis",
     "selector": '[role="dialog"][data-slot="dialog-content"]:has([data-slot="radio-group-item"][value="probleme"])',
     "dismiss": '[role="dialog"][data-slot="dialog-content"]:has([data-slot="radio-group-item"][value="probleme"]) button[data-slot="dialog-close"]'},
    # Le popover « Chiffré » du compositeur (v2) : ouvert par le driver pour lire le texte envoyé
    # (sent.json), refermé par Échap dans la même action ; si une action s'est arrêtée avant, la
    # suivante le referme.
    {"name": "envoi", "selector": '[role="dialog"][data-slot="popover-content"]', "dismiss": "key:Escape",
     "own": True},
    # v2.3, à confirmer. La palette de recherche ⌘K, ouverte par le driver (lister_conversations {case},
    # sonder_interface), refermée par Échap dans la même action.
    {"name": "recherche",
     "selector": ('[role="dialog"]:has([cmdk-input]), [role="dialog"]:has(input[role="combobox"]), '
                  '[role="dialog"]:has(input[type="search"])'),
     "dismiss": "key:Escape", "own": True},
    # Une fenêtre de renommage, si l'application en ouvre une (renommer_conversation) : abandonnée par
    # Échap, jamais validée par la garde.
    {"name": "renommer", "selector": '[role="dialog"]:has(input[type="text"]), [role="dialog"]:has(input:not([type]))',
     "dismiss": "key:Escape", "own": True},
    # La confirmation de suppression de l'application (supprimer_conversation) : validée par le seul bouton
    # dont le texte suit `confirm`, dans cette fenêtre, par l'action qui l'a ouverte ; laissée ouverte par
    # une action coupée, la garde suivante l'annule par Échap, jamais ne la valide.
    {"name": "suppression", "selector": '[role="alertdialog"]', "dismiss": "key:Escape",
     "confirm": r"supprimer|delete|confirmer|confirm|effacer|remove|retirer", "own": True},
]
# Les états d'une conversation du registre sur laquelle plus rien ne se clique : supprimée, dont la
# confirmation de suppression a été cliquée (revue de la v2.3, m2 : jamais deux fois), ou dont l'entrée
# « Supprimer » a été cliquée (round 2, registry-6 : noté avant le clic, retiré seulement quand une
# confirmation trouvée a été annulée par Échap et que la conversation est encore listée).
GONE_STATES = ("supprimée", "suppression confirmée", "suppression demandée")
# Motif de l'URL du flux de réponse (annexe A) : /api/chat/stream chez MaskAI.
STREAM_PATTERN = r"chat/stream"

PER_FILE = 2 * 1024 * 1024        # HARNESS_MAX_FILE_BYTES
PER_ACTION = 4 * 1024 * 1024      # HARNESS_MAX_RUN_BYTES / 4
DOM_CAP = 1024 * 1024
NETWORK_CAP = 1024 * 1024
DEFAULT_BUDGET_S = 60
# Budget de latence par classe de cas, pour un cas sans `budget_s` (cases.json garde les siens) :
# les classes de PROCEDURE-2 §5 (pièce jointe 20 s, recherche web 15 s, charge concurrente 15 s) ;
# les cas lourds portent leur propre budget_s (60 s).
CLASS_BUDGET_S = {"load": 3, "message": 10, "model": 10, "web": 15, "long": 10, "list": 10,
                  "attach_question": 20, "concurrent": 15}
PASTED_PII_BUDGET_S = 20
# Une observation de maintenance ne juge un cas que si elle a au plus cet âge : une reprise plus
# tard essaie le cas de nouveau.
MAINTENANCE_FRESH_S = 600
# Les actions qui ont besoin de l'application (la garde vérifie la page de maintenance avant
# elles) ; une capture d'écran, la lecture du flux ou de la dernière réponse n'en ont pas besoin.
NEEDS_APP = ("mesurer_chargement", "nouvelle_conversation", "joindre_document", "poser_question",
             "envoyer_message", "lister_conversations", "charge_concurrente", "choisir_modele",
             "recherche_web", "conversation_longue", "renommer_conversation", "supprimer_conversation",
             "ouvrir_avis")
# La couleur des zones masquées sur les captures.
MASK_COLOR = "#FF00FF"
# Fin de réponse (D6) : après la fin du flux, le corps de la nouvelle réponse immobile
# DISPLAY_STABLE_S et le bouton d'arrêt disparu ; au plus DISPLAY_CAP_S après la fin du flux
# (au-delà : capturée « encore en mouvement », notée, sans abandon) ; NO_ANSWER_WAIT_S pour qu'une
# carte nouvelle apparaisse (sinon : aucune réponse affichée).
DISPLAY_STABLE_S = 2.0
DISPLAY_CAP_S = 30.0
NO_ANSWER_WAIT_S = 5.0
# Abandon : après le clic sur le bouton d'arrêt, attendre sa disparition au plus STOP_WAIT_S.
STOP_WAIT_S = 3.0
# Puce de pièce jointe sans aucun marqueur d'état depuis ATTACH_QUIET_S, sans état d'attente vu
# avant : prête présumée (`ready_by: quiet`, le debrief la distingue).
ATTACH_QUIET_S = 2.0
# Popover du texte envoyé : libellé du déclencheur, puis contenu, immobiles SENT_STABLE_S, chacun au
# plus SENT_SETTLE_S.
SENT_STABLE_S = 0.5
SENT_SETTLE_S = 3.0
# Panneau « Détails » de la carte (v2.2) : son contenu est retenu immobile DETAILS_STABLE_S s'il
# porte la question, DETAILS_OTHER_STABLE_S sinon (un statut, un chargement), au plus SENT_SETTLE_S
# après le clic ; refermé (aria-expanded revenu à false) au plus DETAILS_CLOSE_S après le second.
DETAILS_STABLE_S = 0.3
DETAILS_OTHER_STABLE_S = 1.0
DETAILS_CLOSE_S = 1.5
# Le panneau n'est pas lu quand il reste moins de MARGIN_S + DETAILS_MIN_S avant l'échéance de
# l'hôte : les preuves passent d'abord (`rejected: "délai de l'hôte"`).
DETAILS_MIN_S = 3.0
# Recherche web coupée : une étape web_search plus longue que cela est une recherche faite.
WEB_OFF_MAX_MS = 1000
WEB_MODES = ("off", "auto", "on")
# v2.3 : la palette ⌘K ouverte au plus SEARCH_OPEN_S après la touche ; ses résultats retenus immobiles
# SEARCH_STABLE_S, au plus tôt SEARCH_MIN_S après la frappe, au plus SEARCH_CAP_S. Un menu, un champ, une
# confirmation : au plus UI_WAIT_S. Un extrait d'erreur sur une ligne : EXCERPT_MAX caractères.
SEARCH_OPEN_S = 2.5
SEARCH_STABLE_S = 0.8
SEARCH_MIN_S = 1.2
SEARCH_CAP_S = 5.0
UI_WAIT_S = 3.0
EXCERPT_MAX = 80
# Round 2 (registry-5, verdicts-6) : l'état attendu d'une conversation (absente, titre relu) tenu HOLD_S avant
# d'être écrit ; au plus 3 × le budget du cas depuis le clic. Le texte d'une confirmation est jugé entier,
# jusqu'à CONFIRM_TEXT_CAP caractères : au-delà, il est refusé (click-targets-6). Un texte de la page vers le
# harnais (un dialogue, une fenêtre) : au plus HARNESS_TEXT_MAX caractères, expurgé (leakage-2).
HOLD_S = 2.0
# Round 2 (robustness-6) : la confirmation native de la suppression, laissée à l'humain : NON TESTÉ.
NATIVE_CONFIRMATION = "confirmation native : le pilote ne répond jamais à un dialogue (à annuler dans Chrome)"
CONFIRM_TEXT_CAP = 4000
HARNESS_TEXT_MAX = 120
MARGIN_S = 8                      # laissé sous l'échéance de l'hôte pour écrire les preuves
NFR_ORDER = ["performance", "veracite", "disponibilite", "securite", "conformite", "scalabilite"]

log = logging.getLogger(NAME)
log.addHandler(logging.NullHandler())


def _load_module(name: str, path: Path):
    """Load one of the recette scripts by path, writing no bytecode next to it."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    was, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = was
    return module


evaluer = _load_module("recette_evaluer", SCRIPTS_DIR / "evaluer.py")
journal = _load_module("recette_journal", SCRIPTS_DIR / "journal.py")

# Annexe A, en script d'initialisation : chaque document a son intercepteur avant le code de
# la page. Ajouts : <START> à l'appel, <ABORT> si la requête elle-même échoue.
INTERCEPTOR_JS = r"""
(() => {
  if (window.__wrapped) return;
  window.__ev = [];
  const original = window.fetch;
  window.fetch = async (...args) => {
    const url = (args[0] && args[0].url) || String(args[0]);
    const t0 = Date.now();
    const stream = /__PATTERN__/.test(url);
    if (stream) window.__ev.push([0, '<START>']);
    let response;
    try {
      response = await original(...args);
    } catch (e) {
      if (stream) window.__ev.push([Date.now() - t0, '<ABORT> ' + e.name]);
      throw e;
    }
    if (stream && response.body) {
      const [forApp, forUs] = response.body.tee();
      (async () => {
        try {
          const reader = forUs.getReader();
          const decoder = new TextDecoder();
          for (;;) {
            const { done, value } = await reader.read();
            if (done) { window.__ev.push([Date.now() - t0, '<END>']); break; }
            for (const line of decoder.decode(value, { stream: true }).split('\n')) {
              if (line.trim()) window.__ev.push([Date.now() - t0, line.slice(0, 160)]);
            }
          }
        } catch (e) { window.__ev.push([Date.now() - t0, '<ABORT> ' + e.name]); }
      })();
      return new Response(forApp, { status: response.status, statusText: response.statusText, headers: response.headers });
    }
    return response;
  };
  window.__wrapped = true;
})();
""".replace("__PATTERN__", STREAM_PATTERN.replace("/", r"\/"))

_VISIBLE = """const visible = (el) => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
    && getComputedStyle(el).visibility !== 'hidden';"""
# Round 2 (click-targets-3, -4, -5) : le compositeur, le plus petit ancêtre de la zone de saisie (a.textarea) qui
# porte un bouton de modèle (a.model), à défaut un bouton data-mode, à défaut le formulaire de la zone ; null sans
# zone de saisie. Envoyer, arrêter, choisir le modèle ou la recherche web : dans lui seul.
_COMPOSER = """const composerBox = (a) => {
    const ta = document.querySelector(a.textarea);
    if (!ta) return null;
    const climb = (sel) => { if (!sel) return null; let box = ta.parentElement;
      while (box && box !== document.body && !box.querySelector(sel)) box = box.parentElement;
      return box && box !== document.body && box !== document.documentElement ? box : null; };
    return climb(a.model) || climb('button[data-mode]') || ta.closest('form') || ta.parentElement;
  };"""
_PICK = """const pick = (sels) => { for (const s of sels) { const els = document.querySelectorAll(s);
    if (els.length) return [...els]; } return []; };"""

MODALS_JS = "(a) => {" + _VISIBLE + """
  const known = [];
  for (const k of a.known) { const el = document.querySelector(k.selector); if (visible(el)) known.push(k.name); }
  const unknown = [];
  for (const el of document.querySelectorAll(a.modal)) {
    if (!visible(el) || a.known.some((k) => el.closest(k.selector))) continue;
    unknown.push((el.innerText || el.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ').slice(0, 200));
  }
  return { known, unknown };
}"""

# Ce que dit la page hors des cartes (v2.3, E10) : le texte de la ligne d'état du compositeur (a.status,
# lue même cachée : elle est aria-live) et des alertes et toasts visibles (a.notices), en paires
# [genre, texte].
# Round 2 (leakage-1) : avec a.fresh, une alerte ou un toast déjà là au repère (MARK_NOTICES_JS, posé avant
# l'envoi d'une question ou au début d'une action de M8) n'est jamais compté, quel que soit son texte : un
# toast identique à un toast d'avant est un toast nouveau. La ligne d'état, un seul élément, se compare à
# son texte d'avant (notices0).
_NOTICES = """const notices = (a) => {
    const out = [];
    const before = a.fresh ? window.__vkNoticesBefore : null;
    const st = a.status ? document.querySelector(a.status) : null;
    const stText = st ? (st.innerText || st.textContent || '').trim().replace(/\\s+/g, ' ') : '';
    if (stText) out.push(['status', stText.slice(0, 500)]);
    if (a.notices) for (const el of document.querySelectorAll(a.notices)) {
      if ((st && (el === st || st.contains(el) || el.contains(st))) || !visible(el)) continue;
      if (before && before.has(el)) continue;
      const t = (el.innerText || '').trim().replace(/\\s+/g, ' ');
      if (t) out.push([el.hasAttribute('data-sonner-toast') ? 'toast' : (el.getAttribute('role') || 'toast'), t.slice(0, 500)]);
    }
    return out.slice(0, 20);
  };"""

POLL_JS = "(a) => {" + _VISIBLE + _PICK + _NOTICES + _COMPOSER + """
  const ev = window.__ev || [];
  const msgs = pick(a.assistant);
  const last = msgs.length ? msgs[msgs.length - 1] : null;
  const body = last ? last.querySelector(a.body) : null;
  const re = new RegExp(a.stopText, 'i');
  // round 2, click-targets-4: never the dictation button, nor a toggle (aria-pressed), and in the composer
  const box = composerBox(a) || document;
  const usable = (b) => visible(b) && !(a.mic && b.matches(a.mic)) && !b.hasAttribute('aria-pressed');
  let stop = a.stop.some((s) => [...box.querySelectorAll(s)].some(usable));
  if (!stop) stop = [...box.querySelectorAll('button')].some((b) => usable(b) && re.test(b.textContent || ''));
  return { n: ev.length, ev: ev.length < a.from ? ev : ev.slice(a.from), count: msgs.length,
           text: body ? body.innerText : (last ? last.innerText : ''), body: !!body, stop, notices: notices(a) };
}"""

NOTICES_JS = "(a) => {" + _VISIBLE + _NOTICES + " return notices(a); }"
MARK_NOTICES_JS = """(a) => { window.__vkNoticesBefore = new WeakSet(a.notices ? [...document.querySelectorAll(a.notices)] : []);
  return true; }"""

# La dernière carte : le texte de son corps (a.body), le texte de la carte entière (l'en-tête s'en
# déduit) et son HTML. Sans corps trouvé, le texte est celui de la carte (body: false, noté).
ANSWER_JS = "(a) => {" + _PICK + """
  const msgs = pick(a.assistant);
  const last = msgs.length ? msgs[msgs.length - 1] : null;
  if (!last) return { count: 0, text: '', card: '', html: '', body: false };
  const body = last.querySelector(a.body);
  return { count: msgs.length, text: body ? body.innerText : last.innerText, card: last.innerText,
           html: last.outerHTML, body: !!body };
}"""

# Round 2 (click-targets-4) : dans le compositeur seul, jamais le bouton de dictée ni un interrupteur
# (aria-pressed) ; si le seul candidat est le micro, rien n'est cliqué.
CLICK_STOP_JS = "(a) => {" + _VISIBLE + _COMPOSER + """
  const re = new RegExp(a.stopText, 'i');
  const box = composerBox(a);
  if (!box) return false;
  const usable = (b) => visible(b) && !b.disabled && !(a.mic && b.matches(a.mic)) && !b.hasAttribute('aria-pressed');
  for (const s of a.sels) for (const b of box.querySelectorAll(s)) if (usable(b)) { b.click(); return true; }
  for (const b of box.querySelectorAll('button')) if (usable(b) && re.test(b.textContent || '')) { b.click(); return true; }
  return false;
}"""

# Le déclencheur du popover « Chiffré » : parmi les déclencheurs visibles hors des cartes de
# réponse, celui qui partage l'ancêtre le plus profond avec la zone de saisie (le compositeur).
SENT_TRIGGER_JS = "(a) => {" + _VISIBLE + """
  const ta = document.querySelector(a.textarea);
  const all = [...document.querySelectorAll(a.trigger)];
  let best = -1, bestDepth = -1;
  all.forEach((el, i) => {
    if (!visible(el) || el.closest('article')) return;
    let depth = 0;
    if (ta) {
      let p = el;
      while (p && !p.contains(ta)) p = p.parentElement;
      for (; p; p = p.parentElement) depth++;
    }
    if (depth > bestDepth) { best = i; bestDepth = depth; }
  });
  if (best < 0) return null;
  const el = all[best];
  return { index: best, label: (el.getAttribute('aria-label') || el.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 300),
           expanded: el.getAttribute('aria-expanded') === 'true' };
}"""

# Le contenu du popover ouvert : celui que le déclencheur nomme (aria-controls), à défaut le dernier
# popover visible ; son bloc de texte (a.text) s'il en a un, sinon tout son texte.
SENT_READ_JS = "(a) => {" + _VISIBLE + """
  const trig = [...document.querySelectorAll(a.trigger)][a.index];
  const id = trig ? trig.getAttribute('aria-controls') : null;
  let pop = id ? document.getElementById(id) : null;
  if (!pop || !visible(pop)) { const pops = [...document.querySelectorAll(a.popover)].filter(visible); pop = pops.length ? pops[pops.length - 1] : null; }
  if (!pop) return null;
  let block = null;
  for (const s of a.text) { const el = pop.querySelector(s); if (el && (el.innerText || '').trim()) { block = el; break; } }
  return { text: ((block || pop).innerText || '').trim().slice(0, 20000), block: !!block,
           popover: (pop.innerText || '').trim().slice(0, 20000) };
}"""

SENT_OPEN_JS = "(a) => {" + _VISIBLE + " return [...document.querySelectorAll(a.popover)].some(visible); }"

POPOVER_CLOSE_JS = "(a) => {" + _VISIBLE + """
  for (const pop of document.querySelectorAll(a.popover)) {
    if (!visible(pop)) continue;
    const el = pop.querySelector(a.close);
    if (el) { el.click(); return true; }
  }
  return false;
}"""

# v2.2 : la dernière carte de réponse (le premier sélecteur de a.assistant qui trouve des cartes,
# comme ANSWER_JS), son bouton « Détails » cherché dans elle seule (a.triggers, de details_triggers :
# CSS, et texte contenu pour un `:has-text`). DETAILS_JS rend ce qu'on en lit : la carte, l'état du
# bouton de révélation des PII, le bouton trouvé, son aria-expanded, les rangs des contenus déjà
# ouverts de la carte (`open_idx`), et le contenu ouvert : celui que le bouton nomme (aria-controls)
# s'il est visible, sinon, dans la carte, le premier contenu visible et non fermé qui suit le bouton
# hors des rangs `a.exclude` (ceux qui étaient ouverts avant le clic du driver : un autre panneau de
# la carte n'est jamais pris pour le sien) ; son texte et son HTML, coupés. DETAILS_EL_JS rend le
# bouton lui-même, pour le clic.
_DETAILS_FIND = r"""
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const all = (root, css) => { try { return [...root.querySelectorAll(css)]; } catch (e) { return []; } };
  const find = (a) => {
    for (const s of a.assistant) {
      const cards = all(document, s);
      if (!cards.length) continue;
      const card = cards[cards.length - 1];
      for (const t of a.triggers) {  // never the PII reveal toggle, never a button of the answer's body
        const el = all(card, t.css).find((e) => !e.closest(a.reveal) && !(a.body && e.closest(a.body))
                                               && (t.text === null || norm(e.textContent).includes(t.text)));
        if (el) return { sel: s, count: cards.length, card, trigger: el, triggerSel: t.sel };
      }
      return { sel: s, count: cards.length, card, trigger: null, triggerSel: null };
    }
    return { sel: null, count: 0, card: null, trigger: null, triggerSel: null };
  };"""

DETAILS_JS = "(a) => {" + _VISIBLE + _DETAILS_FIND + r"""
  const f = find(a);
  const out = { count: f.count, card: f.sel, index: f.count - 1, trigger: f.triggerSel, reveal: null };
  if (!f.card) return out;
  const rv = f.card.querySelector(a.reveal);
  if (rv) out.reveal = rv.getAttribute('aria-pressed');
  const el = f.trigger;
  if (!el) return out;
  out.label = (el.getAttribute('aria-label') || el.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 300);
  out.expanded = el.getAttribute('aria-expanded');
  const contents = all(f.card, a.content);
  const isOpen = (x) => visible(x) && x.getAttribute('data-state') !== 'closed';
  out.open_idx = contents.map((x, i) => (isOpen(x) ? i : -1)).filter((i) => i >= 0);
  const id = el.getAttribute('aria-controls');
  let c = id ? document.getElementById(id) : null, how = 'aria-controls';
  if (!c || !visible(c)) {
    const skip = new Set(a.exclude || []);
    const open = contents.filter((x, i) => !skip.has(i) && isOpen(x));
    c = open.find((x) => el.compareDocumentPosition(x) & Node.DOCUMENT_POSITION_FOLLOWING) || open[0] || null;
    how = 'card';
  }
  out.open = !!c;
  if (!c) return out;
  let block = null, blockSel = null;
  for (const s of a.text || []) {
    const b = c.querySelector(s);
    if (b && (b.innerText || '').trim()) { block = b.innerText.trim().slice(0, 20000); blockSel = s; break; }
  }
  Object.assign(out, { how, controls: id, state: c.getAttribute('data-state'), block, block_sel: blockSel,
                       text: (c.innerText || '').trim().slice(0, 20000), html: (c.outerHTML || '').slice(0, 20000) });
  return out;
}"""

DETAILS_EL_JS = "(a) => {" + _DETAILS_FIND + " return [find(a).trigger]; }"

SENT_JS = """(a) => {
  const re = new RegExp(a.label, 'i');
  const labels = [...document.querySelectorAll('body *')].filter((el) => el.children.length === 0 && re.test(el.textContent || ''));
  if (!labels.length) return null;
  const label = labels[labels.length - 1];
  const own = (label.textContent || '').trim();
  let box = label.parentElement;
  for (let i = 0; i < 3 && box && (box.textContent || '').replace(own, '').trim().length < 2; i++) box = box.parentElement;
  return box ? (box.textContent || '').replace(own, '').trim().slice(0, 20000) : null;
}"""

FILL_JS = """(a) => {
  const ta = document.querySelector(a.sel);
  if (!ta) return false;
  ta.focus();
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
  setter.call(ta, a.text);
  ta.dispatchEvent(new Event('input', { bubbles: true }));
  return true;
}"""

VALUE_JS = "(a) => { const ta = document.querySelector(a.sel); return ta ? ta.value : null; }"

# Round 2 (click-targets-3) : le bouton d'envoi du compositeur seul, hors fenêtre, jamais le micro ni l'entrée de
# « Votre avis » (a.never), même dans le compositeur ; désactivé ou absent, rien n'est cliqué (« envoi refusé par
# l'interface »). Rend clicked, disabled ou absent.
CLICK_SEND_JS = "(a) => {" + _VISIBLE + _COMPOSER + """
  const box = composerBox(a);
  if (!box) return 'absent';
  let disabled = false;
  for (const s of a.sels) for (const b of box.querySelectorAll(s)) {
    if (!visible(b) || b.closest('[role="dialog"], [role="alertdialog"]') || (a.mic && b.matches(a.mic))
        || (a.never && b.matches(a.never))) continue;
    if (b.disabled || b.getAttribute('aria-disabled') === 'true') { disabled = true; continue; }
    b.click();
    return 'clicked';
  }
  return disabled ? 'disabled' : 'absent';
}"""

# La puce d'un fichier : l'ancêtre du bouton « Retirer <fichier> » qui porte du texte (sans monter
# jusqu'au conteneur d'une autre puce), à défaut un élément des sélecteurs de secours qui nomme le
# fichier. Rend son texte, ses aria-label, son indicateur d'activité et son data-state.
CHIP_JS = """(a) => {
  const removeSel = a.remove.join(', ');
  let el = null, how = null;
  const buttons = [...document.querySelectorAll(removeSel)].filter((b) => (b.getAttribute('aria-label') || '').includes(a.name));
  if (buttons.length) {
    const b = buttons[buttons.length - 1];
    el = b.parentElement || b;
    while (el.parentElement && !(el.innerText || '').replace(b.innerText || '', '').trim()
           && el.parentElement.querySelectorAll(removeSel).length <= 1) el = el.parentElement;
    how = 'remove';
  }
  if (!el) for (const s of a.chips) for (const c of document.querySelectorAll(s))
    if ((c.textContent || '').includes(a.name) || (c.getAttribute('aria-label') || '').includes(a.name)) { el = c; how = 'chip'; }
  if (!el) return null;
  const labels = [el, ...el.querySelectorAll('[aria-label]')].map((e) => e.getAttribute('aria-label') || '').filter(Boolean);
  return { how, text: (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 300),
           labels: labels.slice(0, 10), busy: el.matches(a.busy) || !!el.querySelector(a.busy),
           attr: el.getAttribute('data-state') || el.getAttribute('data-status') || '' };
}"""

# Le premier élément visible d'une liste de sélecteurs, dans le compositeur seul (round 2, click-targets-5) : le
# sélecteur, son rang parmi ses correspondances dans la page, son aria-label, son texte, son data-mode. Pour la
# recherche web (a.kind « web »), seulement un bouton dont data-mode est un mode (a.modes) ou dont le libellé
# commence par « Web search: » ou « Recherche web » : jamais un interrupteur de thème.
CONTROL_JS = "(a) => {" + _VISIBLE + _COMPOSER + """
  const box = composerBox(a);
  if (!box) return null;
  for (const s of a.sels) {
    for (const el of [...box.querySelectorAll(s)].filter(visible)) {
      const label = (el.getAttribute('aria-label') || '').trim(), mode = el.getAttribute('data-mode') || '';
      if (a.kind === 'web' && !((a.modes || []).includes(mode) || /^\\s*(web search|recherche web)\\b/i.test(label))) continue;
      return { sel: s, index: [...document.querySelectorAll(s)].indexOf(el), label,
               text: (el.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 200),
               mode, expanded: el.getAttribute('aria-expanded') === 'true' };
    }
  }
  return null;
}"""

# Les options de la dernière liste ouverte (a.list) : rang, texte, valeur, visibilité.
OPTIONS_JS = "(a) => {" + _VISIBLE + """
  const lists = [...document.querySelectorAll(a.list)].filter(visible);
  if (!lists.length) return null;
  const list = lists[lists.length - 1];
  return [...list.querySelectorAll(a.option)].map((o, i) => ({ index: i, visible: visible(o),
    text: (o.innerText || o.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 200),
    value: o.getAttribute('data-value') || o.getAttribute('value') || o.getAttribute('data-mode') || '' }));
}"""

# Le texte de la page et la présence d'une zone de saisie visible (page de maintenance).
MAINTENANCE_JS = "(a) => {" + _VISIBLE + """
  const mic = a.mic ? document.querySelector(a.mic) : null;
  return { text: ((document.body && document.body.innerText) || '').slice(0, 20000),
           textarea: [...document.querySelectorAll(a.textarea)].some(visible),
           mic: mic ? (mic.getAttribute('data-status') || null) : null };
}"""

LIST_OPEN_JS = "(a) => {" + _VISIBLE + " return [...document.querySelectorAll(a.list)].some(visible); }"

TITLES_JS = "(a) => {" + _PICK + """
  return pick(a.items).map((el) => (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 120)).filter(Boolean).slice(0, 200);
}"""

TIMING_JS = """() => {
  const n = performance.getEntriesByType('navigation')[0];
  const res = performance.getEntriesByType('resource');
  return { url: location.href, nav: n ? n.toJSON() : null,
           paint: performance.getEntriesByType('paint').map((p) => ({ name: p.name, start: p.startTime })),
           resources: res.length, resources_bytes: res.reduce((s, r) => s + (r.transferSize || 0), 0) };
}"""

# v2.3, E4 : le bouton de dictée, lu (data-status, aria-pressed, libellé), jamais pressé.
MIC_JS = """(a) => { const el = document.querySelector(a.sel);
  return el ? { status: el.getAttribute('data-status'), pressed: el.getAttribute('aria-pressed'),
                label: (el.getAttribute('aria-label') || '').slice(0, 200) } : null; }"""

# v2.3, E2 et E11 : la palette de recherche ouverte. Son premier champ visible (a.inputs), la palette qui
# le porte (la fenêtre, à défaut la racine cmdk), ses résultats visibles (a.results, cherchés dans la
# palette seule) et son état vide. `redact` (la sonde, qui ne tape rien et peut voir des titres de
# conversations antérieures à la recette) : le HTML gardé ne porte ni le texte ni les valeurs des
# résultats.
# Round 2 (click-targets-7) : un champ visible avant la touche ou le clic (MARK_BEFORE_JS) n'est jamais celui de
# la palette, ni un champ dans une fenêtre ou une racine cmdk déjà là avant.
_SEARCH_FIND = """const findSearch = (a) => {
    const before = (x) => !!x && !!window.__vkDialogsBefore && window.__vkDialogsBefore.has(x);
    for (const s of a.inputs) {
      let els = [];
      try { els = [...document.querySelectorAll(s)]; } catch (e) { continue; }
      const el = els.find((x) => visible(x) && !(window.__vkFieldsBefore && window.__vkFieldsBefore.has(x))
                                 && !before(x.closest('[role="dialog"]')) && !before(x.closest('[cmdk-root]')));
      if (el) return { input: el, sel: s, root: el.closest('[role="dialog"]') || el.closest('[cmdk-root]') || el.parentElement };
    }
    return null;
  };"""

# Round 2 (verdicts-4, robustness-7) : un résultat est un élément qui mène à une conversation : un lien
# /conversation/, ou une valeur (data-value, value, id) qui est l'identifiant d'une conversation de la barre
# latérale, ou un texte égal au titre d'une conversation listée ; jamais un élément qui ne fait que répéter la
# requête. `items` : combien d'éléments la palette montrait en tout.
SEARCH_JS = "(a) => {" + _VISIBLE + _SEARCH_FIND + """
  const f = findSearch(a);
  if (!f) return null;
  const conv = a.convPath ? new RegExp(a.convPath) : null;
  const pathOf = (x) => { try { return new URL(x.getAttribute('href') || '', location.href).pathname.replace(/\\/+$/, ''); }
                          catch (e) { return null; } };
  const norm = (s) => (s || '').normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').replace(/\\s+/g, ' ').trim().toLowerCase();
  const side = conv ? [...document.querySelectorAll('a[href]')].filter((x) => !f.root.contains(x) && conv.test(pathOf(x) || '')) : [];
  const ids = new Set(side.map((x) => (pathOf(x) || '').split('/').pop()));
  const titles = new Set(side.map((x) => norm(x.textContent)).filter((t) => t.length >= 3));
  const leads = (el) => {
    const link = el.matches('a[href]') ? el : el.querySelector('a[href]');
    if (link && conv && conv.test(pathOf(link) || '')) return true;
    for (const at of ['data-value', 'value', 'id']) {
      const v = el.getAttribute(at);
      if (v && (ids.has(v) || titles.has(norm(v)))) return true;  // round 3, M-1: a value that is a title
    }
    const t = norm(el.textContent);
    return titles.has(t) || [...titles].some((x) => x.length >= 6 && t.startsWith(x));  // M-1: title, then a snippet
  };
  // round 3, M-1: an item that neither leads to a conversation nor echoes the query is one the pilot cannot read
  const q = norm(f.input.value);
  const echoes = (el) => q.length > 0 && norm(el.textContent).includes(q) && !leads(el);
  let results = [], resultSel = null, items = 0, unknown = 0;
  for (const s of a.results) {
    const els = [...f.root.querySelectorAll(s)].filter(visible);
    if (els.length) {
      resultSel = s;
      items = els.length;
      results = els.filter(leads).map((el) => (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 120)).filter(Boolean);
      unknown = els.filter((el) => !leads(el) && !echoes(el)).length;
      break;
    }
  }
  const empty = a.empty ? [...f.root.querySelectorAll(a.empty)].some(visible) : false;
  const keep = /^(role|class|id|cmdk-item|cmdk-group|data-slot|aria-selected|data-selected|hidden)$/;
  const copy = f.root.cloneNode(true);
  if (a.redact) for (const s of a.results) for (const x of copy.querySelectorAll(s)) {
    x.textContent = '…';
    for (const at of [...x.attributes]) if (!keep.test(at.name)) x.setAttribute(at.name, '…');
  }
  return { input: f.sel, root: f.root.getAttribute('role') || f.root.tagName.toLowerCase(), value: f.input.value || '',
           focused: document.activeElement === f.input, results: a.redact ? [] : results.slice(0, 50),
           result: resultSel, count: results.length, items, unknown, empty, html: (copy.outerHTML || '').slice(0, 5000) };
}"""

SEARCH_INPUT_EL_JS = "(a) => {" + _VISIBLE + _SEARCH_FIND + " const f = findSearch(a); return [f ? f.input : null]; }"

# v2.3, E1, E3 et E11 : une conversation de la barre latérale, trouvée par son adresse exacte (a.href, le
# chemin enregistré au registre), jamais par son rang ni par son titre : un lien de la barre latérale
# (a.sidebar), hors fenêtre, menu et liste, dont le chemin est a.href. Son élément (`scope`) est le plus
# grand ancêtre du lien qui ne porte le lien d'aucune autre conversation, sans monter jusqu'à la barre.
# Son déclencheur de menu (a.triggers) est cherché au plus près du lien d'abord : dans le lien, puis dans
# chaque ancêtre jusqu'à `scope`, jamais au-delà (un menu de groupe, plus haut, n'est jamais pris pour
# le sien).
_ITEM_FIND = r"""
  const pathOf = (el) => { try { return new URL(el.getAttribute('href') || '', location.href).pathname.replace(/\/+$/, ''); }
                           catch (e) { return null; } };
  const convRe = new RegExp(a.convPath);
  const textOf = (el) => (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ');
  const conversations = () => [...document.querySelectorAll('a[href]')].filter((el) => !!el.closest(a.sidebar)
      && !el.closest('[role="dialog"], [role="menu"], [role="listbox"]') && convRe.test(pathOf(el) || ''));
  const findItem = (href) => {
    const all = conversations();
    const mine = all.filter((el) => pathOf(el) === href);
    if (!mine.length) return null;
    const anchor = mine.find(visible) || mine[0];
    const top = anchor.closest(a.sidebar);
    let scope = anchor;
    while (scope.parentElement && scope.parentElement !== top
           && !all.some((x) => pathOf(x) !== href && scope.parentElement.contains(x))) scope = scope.parentElement;
    return { anchor, scope, count: mine.length, all };
  };
  // round 2, registry-4: the row of a conversation, its nearest list item ancestor (a.row) that holds no other
  // conversation's link, else the link's parent; never above it
  const rowOf = (anchor, all) => {
    const others = (all || conversations()).filter((x) => x !== anchor && pathOf(x) !== pathOf(anchor));
    const li = a.row ? anchor.closest(a.row) : null;
    if (li && !others.some((x) => li.contains(x))) return li;
    return anchor.parentElement || anchor;
  };
  const triggerNear = (anchor, scope) => {
    // round 2 (click-targets-2, registry-4): level by level, every selector at a level before the next level,
    // from the link up to its row and never above it; at no level an element that holds a group heading or
    // label outside the link (a trigger there is the group's)
    const row = rowOf(anchor);
    let level = 0;
    for (let node = anchor; node; node = node.parentElement, level++) {
      if (level >= 1 && a.headings && [...node.querySelectorAll(a.headings)].some((h) => !anchor.contains(h))) break;
      for (const s of a.triggers || []) {
        let els = [];
        try { els = [...node.querySelectorAll(s)]; } catch (e) { continue; }
        const el = els.find(visible);
        if (el) return { el, sel: s, level, row: true };
      }
      if (node === row || node === scope) break;
    }
    return null;
  };"""

# Où est la page, et quelles conversations la barre latérale liste (E1) : le chemin de la page, et le
# chemin et le titre de chaque lien de conversation.
CONV_STATE_JS = "(a) => {" + _VISIBLE + _ITEM_FIND + """
  return { path: location.pathname.replace(/\\/+$/, ''), items: conversations().map((el) => [pathOf(el), textOf(el).slice(0, 120)]) };
}"""

# Ce que le driver relève d'une conversation (E3, E11) : trouvée ou non, son élément, son déclencheur de
# menu, son titre. Sans a.href, la première conversation visible (la sonde) ; `redact` : le HTML gardé ne
# porte ni son titre ni son adresse (une conversation antérieure à la recette).
ITEM_JS = "(a) => {" + _VISIBLE + _ITEM_FIND + r"""
  const all = conversations();
  let href = a.href;
  if (!href) { const first = all.find(visible); href = first ? pathOf(first) : null; }
  if (!href) return { found: false, total: all.length };
  const f = findItem(href);
  if (!f) return { found: false, total: all.length };
  const t = triggerNear(f.anchor, f.scope);
  const title = textOf(f.anchor).slice(0, 120);
  const key = title.slice(0, 12).toLowerCase();
  const id = href.split('/').pop();
  const copy = f.scope.cloneNode(true);
  if (a.redact) {
    const walker = document.createTreeWalker(copy, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const v = n.nodeValue.trim();
      if (v.length >= 2 && (title.includes(v) || (key && v.toLowerCase().includes(key)))) n.nodeValue = '…';
    }
    for (const x of [copy, ...copy.querySelectorAll('*')]) for (const at of [...x.attributes])
      if (at.name === 'href' || (id && at.value.includes(id)) || (key && at.value.toLowerCase().includes(key)))
        x.setAttribute(at.name, '…');
  }
  let label = t ? (t.el.getAttribute('aria-label') || '').slice(0, 120) : null;
  if (a.redact && label && key && label.toLowerCase().includes(key)) label = '…';
  return { found: true, href, count: f.count, total: all.length, visible: visible(f.anchor),
           title: a.redact ? null : title, scope: f.scope.tagName.toLowerCase(), scope_is_link: f.scope === f.anchor,
           trigger: t ? t.sel : null, trigger_level: t ? t.level : null, trigger_label: label, trigger_row: t ? t.row : null,
           expanded: t ? t.el.getAttribute('aria-expanded') : null, controls: t ? t.el.getAttribute('aria-controls') : null,
           html: (copy.outerHTML || '').slice(0, 5000) };
}"""

ITEM_EL_JS = "(a) => {" + _VISIBLE + _ITEM_FIND + " const f = findItem(a.href); return [f ? f.anchor : null]; }"

TRIGGER_EL_JS = "(a) => {" + _VISIBLE + _ITEM_FIND + """
  const f = findItem(a.href);
  const t = f ? triggerNear(f.anchor, f.scope) : null;
  return [t ? t.el : null];
}"""

# Le menu ouvert : celui que nomme le déclencheur (a.controls, aria-controls), à défaut le dernier menu
# visible ; ses entrées (a.item), leur texte replié (minuscules, sans accents) pour les motifs.
_MENU_FIND = r"""
  const fold = (s) => (s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/\s+/g, ' ').trim().toLowerCase();
  // round 2, robustness-1: an entry's own text, without its keyboard hint (kbd, data-slot*=shortcut) nor a final
  // ellipsis; the entry is then matched whole, as the Python side does (menu_text)
  // round 3, I-1: the tail rule is Python's own (MENU_TAIL, passed as a.tail): a plain-text hint « ⌘⌫ »,
  // « Ctrl+Suppr », « F2 » or one in a plain span is stripped here as on the Python side
  const itemText = (el) => { const c = el.cloneNode(true);
    for (const k of c.querySelectorAll('kbd, [data-slot*="shortcut"]')) k.remove();
    let t = (c.textContent || '').replace(/\s+/g, ' ').trim();
    const tail = new RegExp(a.tail || '\\s*(?:…|\\.\\.\\.)\\s*$', 'i');
    for (let i = 0; i < 4; i++) { const u = t.replace(tail, '').trim(); if (u === t) break; t = u; }
    return t; };
  const findMenu = (a) => {
    let menu = a.controls ? document.getElementById(a.controls) : null, how = 'aria-controls';
    if (!menu || !visible(menu) || !menu.matches(a.menu)) {
      const menus = [...document.querySelectorAll(a.menu)].filter(visible);
      menu = menus.length ? menus[menus.length - 1] : null;
      how = 'last';
    }
    return menu ? { menu, how } : null;
  };"""

MENU_JS = "(a) => {" + _VISIBLE + _MENU_FIND + _ITEM_FIND + r"""
  const f = findMenu(a);
  if (!f) return null;
  // `redactHref` (the probe, on a conversation the run did not create): the menu keeps no word of its title, in
  // its entries as in its HTML (m10; round 2: a label, a text node or an attribute is replaced whole when it
  // holds the title's first twelve characters, or any word of four letters or more of the title that is no
  // entry word: a cut title, a title split across nodes, upper-cased by CSS, or a time after it in its link)
  let title = null;
  if (a.redactHref) { const it = findItem(a.redactHref); title = it ? textOf(it.anchor) : null; }
  const tf = fold(title || ''), key = tf.slice(0, 12);
  const keyOk = (key.match(/[\p{L}\p{N}]/gu) || []).length >= 3;
  const words = [...new Set((tf.match(/\p{L}{4,}/gu) || []).filter((w) => !(a.entryWords || []).includes(w)))];
  const holds = (s) => { const f2 = fold(s || '');
    return (keyOk && f2.includes(key)) || words.some((w) => new RegExp('(^|[^\\p{L}])' + w + '($|[^\\p{L}])', 'u').test(f2)); };
  const scrub = (s) => (title && s && holds(s)) ? '…' : s;
  const items = [...f.menu.querySelectorAll(a.item)].map((el, i) => {
    const label = scrub((el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ')).slice(0, 80);
    const own = scrub(itemText(el));
    return { index: i, text: fold(own).slice(0, 80), label,
             disabled: el.getAttribute('aria-disabled') === 'true' || el.hasAttribute('data-disabled') };
  });
  let html = f.menu.cloneNode(true);
  if (title) {
    const walker = document.createTreeWalker(html, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) n.nodeValue = scrub(n.nodeValue);
    for (const x of [html, ...html.querySelectorAll('*')]) for (const at of [...x.attributes]) x.setAttribute(at.name, scrub(at.value));
    for (const x of [html, ...html.querySelectorAll('*')]) if (holds(x.textContent || '')) x.textContent = '…';
  }
  return { how: f.how, id: f.menu.id || null, items, html: (html.outerHTML || '').slice(0, 5000) };
}"""

# L'entrée du menu à cliquer : la seule, active, dont le texte replié suit a.pattern ; aucune sinon.
MENU_ITEM_EL_JS = "(a) => {" + _VISIBLE + _MENU_FIND + r"""
  const f = findMenu(a);
  if (!f) return [null];
  const re = new RegExp(a.pattern, 'i');
  const hits = [...f.menu.querySelectorAll(a.item)].filter((el) => re.test(fold(itemText(el)))
      && el.getAttribute('aria-disabled') !== 'true' && !el.hasAttribute('data-disabled'));
  return [hits.length === 1 ? hits[0] : null];
}"""

MENU_OPEN_JS = "(a) => {" + _VISIBLE + " return [...document.querySelectorAll(a.menu)].some(visible); }"

# What is open before a click, remembered in the page (WeakSet, no mark in the DOM): a confirmation or a
# field read after the click must be one the click opened (revue de la v2.3, m1 et m14).
MARK_BEFORE_JS = "(a) => {" + _VISIBLE + """
  window.__vkDialogsBefore = new WeakSet([...document.querySelectorAll('[role="dialog"], [role="alertdialog"], [cmdk-root]')].filter(visible));
  window.__vkFieldsBefore = new WeakSet([...document.querySelectorAll('input, textarea, [contenteditable="true"]')].filter(visible));
  return true;
}"""

# Un candidat d'entrée (recherche ⌘K, « Votre avis ») est-il identifié ? Jamais dans une carte de réponse,
# une fenêtre, un menu ni le compositeur (le plus petit ancêtre de la zone de saisie qui porte aussi le
# bouton de modèle), jamais le bouton de dictée ; dans la barre latérale si `a.sidebar` ; son texte ou son
# libellé suivant `a.words` si donné (revue de la v2.3, I4 et I5).
# Round 2 (click-targets-1, R2-4) : jamais un lien de conversation, rien dans la ligne d'une conversation ni dans
# la liste des conversations ; un lien n'est une entrée que si son adresse n'est pas celle d'une conversation.
_CONV_ROWS = r"""
  const convRe = a.convPath ? new RegExp(a.convPath) : null;
  const pathOf = (x) => { try { return new URL(x.getAttribute('href') || '', location.href).pathname.replace(/\/+$/, ''); }
                          catch (e) { return null; } };
  const convLinks = () => convRe ? [...document.querySelectorAll('a[href]')].filter((x) => convRe.test(pathOf(x) || '')) : [];
  const listKinds = 'ul, ol, [role="list"], [data-sidebar="menu"], [data-slot="sidebar-menu"]';
  const inConversations = (el, links) => {
    for (const l of links) {
      const others = links.filter((x) => x !== l && pathOf(x) !== pathOf(l));
      const li = a.row ? l.closest(a.row) : null;
      const row = li && !others.some((x) => li.contains(x)) ? li : (l.parentElement || l);
      if (row.contains(el)) return true;
      const list = row.parentElement && row.parentElement.matches(listKinds) ? row.parentElement : null;
      if (list && list.contains(el)) return true;
    }
    return false;
  };"""

ENTRY_OK_JS = r"""(el, a) => {""" + _CONV_ROWS + r"""
  if (el.closest('article, [role="dialog"], [role="menu"], [role="listbox"]')) return false;
  if (a.mic && el.matches(a.mic)) return false;
  const link = el.closest('a[href]');
  if (link && convRe && convRe.test(pathOf(link) || '')) return false;
  if (inConversations(el, convLinks())) return false;
  const ta = document.querySelector(a.textarea);
  if (ta) {
    let box = ta.parentElement;
    while (box && !box.querySelector(a.model)) box = box.parentElement;
    if (box && box !== document.body && box !== document.documentElement && box.contains(el)) return false;
  }
  if (a.sidebar && !el.closest(a.sidebar)) return false;
  const words = ((el.innerText || '') + ' ' + (el.getAttribute('aria-label') || '')).toLowerCase();
  return !a.words || new RegExp(a.words, 'i').test(words);
}"""

# The entry itself, found within one bounded call (round 2, robustness-4: locator.count() and is_visible() never
# return with a dialog open): the first visible element of a.cands ({sel, css, text}: `:has-text("…")` read apart,
# as Playwright reads it) that passes ENTRY_OK_JS. ENTRY_JS rends ce qu'on en relève, ENTRY_EL_JS l'élément.
_ENTRY_FIND = r"""
  const ok = (""" + ENTRY_OK_JS + r""");
  const normT = (s) => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const findEntry = (a) => {
    for (const c of a.cands) {
      let els = [];
      try { els = [...document.querySelectorAll(c.css)]; } catch (e) { continue; }
      for (const el of els) {
        if (!visible(el) || (c.text && !normT(el.textContent).includes(c.text)) || !ok(el, a)) continue;
        return { el, sel: c.sel, index: els.indexOf(el) };
      }
    }
    return null;
  };"""
ENTRY_JS = "(a) => {" + _VISIBLE + _ENTRY_FIND + r"""
  const f = findEntry(a);
  return f ? { selector: f.sel, index: f.index, label: (f.el.getAttribute('aria-label') || null),
               text: (f.el.innerText || f.el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 120) } : { selector: null };
}"""
ENTRY_EL_JS = "(a) => {" + _VISIBLE + _ENTRY_FIND + " const f = findEntry(a); return [f ? f.el : null]; }"

# Round 2 (click-targets-8, robustness-4) : le bouton « nouvelle conversation » validé, à défaut un bouton ou un
# lien de la barre latérale dont le libellé ou le texte replié est exactement a.labels ; rien d'autre.
NEW_CONV_EL_JS = "(a) => {" + _VISIBLE + r"""
  for (const s of a.validated) { const el = [...document.querySelectorAll(s)].find(visible); if (el) return [el]; }
  const re = new RegExp(a.labels, 'i');
  const fold = (s) => (s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/\s+/g, ' ').trim().toLowerCase();
  for (const side of document.querySelectorAll(a.sidebar))
    for (const el of side.querySelectorAll('a, button, [role="button"]'))
      if (visible(el) && (re.test(fold(el.getAttribute('aria-label'))) || re.test(fold(el.innerText)))) return [el];
  return [null];
}"""

# Round 2 (robustness-4) : le recensement des sélecteurs de la sonde en un appel borné (a.rows : {key, sel, css,
# text}), et le champ de fichier.
PROBE_SELECTORS_JS = "(a) => {" + _VISIBLE + r"""
  const normT = (s) => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  return a.rows.map((r) => {
    let els = [];
    try { els = [...document.querySelectorAll(r.css)]; } catch (e) { return { key: r.key, selector: r.sel, error: String(e).slice(0, 160) }; }
    if (r.text) els = els.filter((el) => normT(el.textContent).includes(r.text));
    return { key: r.key, selector: r.sel, count: els.length, visible: els.filter(visible).length };
  });
}"""
FILE_INPUT_JS = """(a) => { const els = [...document.querySelectorAll(a.sel)];
  return { count: els.length, accept: els.length ? els[0].getAttribute('accept') : null }; }"""

# Ce que la page montre qui ressemble à une entrée (le refus d'une entrée non identifiée le nomme ; rien
# n'est cliqué) : les boutons et liens visibles dont le texte ou le libellé suit a.words, hors cartes de
# réponse et liens de conversation, et sans le début d'aucun titre listé : un bouton de la ligne d'une
# conversation peut porter son titre dans son libellé (un titre de conversation n'est jamais recopié).
ENTRY_CANDIDATES_JS = "(a) => {" + _VISIBLE + _CONV_ROWS + r"""
  const re = new RegExp(a.words, 'i');
  const links = convLinks();
  const titles = links.map((l) => (l.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 12).toLowerCase())
    .filter((t) => t.length >= 3);
  return [...document.querySelectorAll('button, a, [role="button"]')].filter(visible)
    .filter((el) => !el.closest('article') && !(el.closest('a[href]') && convRe && convRe.test(pathOf(el.closest('a[href]')) || ''))
                    && !inConversations(el, links))  // round 2: never a conversation's row, link or list (R2-4)
    .map((el) => ((el.getAttribute('aria-label') || '') + ' ' + (el.innerText || '')).trim().replace(/\s+/g, ' '))
    .filter((t) => re.test(t) && !titles.some((x) => t.toLowerCase().includes(x)))
    .map((t) => t.slice(0, 60)).slice(0, 10);
}"""

# Le champ du nouveau titre (E3) : l'élément qui a le focus s'il est un champ texte de la barre latérale
# ou d'une fenêtre, sinon le premier de a.fields ; jamais la zone de saisie du compositeur (a.composer),
# jamais un champ de recherche.
_FIELD_FIND = r"""
  // round 2, registry-7: a field in a window only when that window appeared after the click, is no known
  // window of the application (« Votre avis »), and holds that one field and no choice; else a field of the
  // sidebar, outside any window
  const okDialog = (d) => !(window.__vkDialogsBefore && window.__vkDialogsBefore.has(d))
      && !(a.known || []).some((k) => { try { return d.matches(k); } catch (e) { return false; } })
      && d.querySelectorAll('input:not([type="hidden"]), textarea, select, [contenteditable="true"]').length === 1
      && !d.querySelector('[role="radiogroup"], [role="radio"], [data-slot="radio-group-item"]');
  const editable = (el) => !!el && el.matches && visible(el)
      && !(window.__vkFieldsBefore && window.__vkFieldsBefore.has(el))
      && el.matches('input:not([type]), input[type="text"], textarea, [contenteditable="true"]')
      && !el.matches(a.composer) && !el.matches('[cmdk-input], input[type="search"], input[role="combobox"]')
      && !/search|recherch/i.test((el.getAttribute('placeholder') || '') + ' ' + (el.getAttribute('aria-label') || ''))
      && (el.closest('[role="dialog"]') ? okDialog(el.closest('[role="dialog"]')) : !!el.closest(a.sidebar));
  const findField = (a) => {
    const ae = document.activeElement;
    if (editable(ae)) return { el: ae, how: 'focus' };
    for (const s of a.fields) {
      let els = [];
      try { els = [...document.querySelectorAll(s)]; } catch (e) { continue; }
      const el = els.find(editable);
      if (el) return { el, how: s };
    }
    return null;
  };"""

RENAME_FIELD_JS = "(a) => {" + _VISIBLE + _FIELD_FIND + """
  const f = findField(a);
  if (!f) return null;
  return { how: f.how, tag: f.el.tagName.toLowerCase(), type: f.el.getAttribute('type'),
           in_dialog: !!f.el.closest('[role="dialog"]'),
           value: (f.el.value !== undefined ? f.el.value : f.el.textContent || '').slice(0, 200) };
}"""

RENAME_FIELD_EL_JS = "(a) => {" + _VISIBLE + _FIELD_FIND + " const f = findField(a); return [f ? f.el : null]; }"

# Le bouton qui valide une fenêtre de renommage : dans la fenêtre du champ, le seul dont le texte suit
# a.submit.
RENAME_SUBMIT_EL_JS = "(a) => {" + _VISIBLE + _FIELD_FIND + r"""
  const f = findField(a);
  const d = f ? f.el.closest('[role="dialog"]') : null;
  if (!d) return [null];
  const re = new RegExp(a.submit, 'i');
  const fold = (s) => (s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').trim();
  const hits = [...d.querySelectorAll('button')].filter((b) => visible(b) && re.test(fold(b.innerText || b.getAttribute('aria-label'))));
  return [hits.length === 1 ? hits[0] : null];
}"""

# La confirmation de suppression (E3) : la dernière fenêtre d'alerte visible ouverte par le clic, à défaut la
# dernière fenêtre ; le texte de chacun de ses boutons, en ordre. Round 2 (registry-1, click-targets-6) : c'est le
# côté Python qui juge (pick_confirm, confirms_one_deletion) sur le texte entier (`full`, au plus a.cap
# caractères, `cut` s'il en avait plus) ; `text`, coupé, n'est que pour la preuve.
_CONFIRM_FIND = r"""
  const fold = (s) => (s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/\s+/g, ' ').trim();
  const findConfirm = (a) => {
    const fresh = (d) => visible(d) && !(window.__vkDialogsBefore && window.__vkDialogsBefore.has(d));
    const alerts = [...document.querySelectorAll('[role="alertdialog"]')].filter(fresh);
    const boxes = alerts.length ? alerts : [...document.querySelectorAll('[role="dialog"]')].filter(fresh);
    if (!boxes.length) return null;
    const d = boxes[boxes.length - 1];
    const buttons = [...d.querySelectorAll('button')].filter(visible);
    const texts = buttons.map((b) => fold(b.innerText || b.getAttribute('aria-label')).slice(0, 60));
    return { d, texts, buttons };
  };"""

CONFIRM_JS = "(a) => {" + _VISIBLE + _CONFIRM_FIND + """
  const f = findConfirm(a);
  if (!f) return null;
  const whole = fold(f.d.innerText);
  return { role: f.d.getAttribute('role'), buttons: f.texts, text: whole.slice(0, 300),
           full: whole.slice(0, a.cap || 4000), cut: whole.length > (a.cap || 4000),
           html: (f.d.outerHTML || '').slice(0, 5000) };
}"""

# Le bouton à cliquer : celui de rang a.index parmi les boutons visibles, si son texte est encore a.expect.
CONFIRM_EL_JS = "(a) => {" + _VISIBLE + _CONFIRM_FIND + """
  const f = findConfirm(a);
  return [f && f.texts[a.index] === a.expect ? f.buttons[a.index] : null];
}"""

# L'enquête « Votre avis » ouverte (E5) : ses choix et ses boutons, pour la preuve ; rien n'y est cliqué
# que sa croix.
AVIS_JS = "(a) => {" + _VISIBLE + r"""
  const d = [...document.querySelectorAll(a.sel)].find(visible);
  if (!d) return null;
  return { radios: [...d.querySelectorAll('[role="radio"], [data-slot="radio-group-item"]')]
             .map((r) => r.getAttribute('value') || (r.innerText || '').trim()).slice(0, 10),
           buttons: [...d.querySelectorAll('button')].filter(visible)
             .map((b) => (b.innerText || b.getAttribute('aria-label') || '').trim().replace(/\s+/g, ' ').slice(0, 60)).slice(0, 20),
           html: (d.outerHTML || '').slice(0, 5000) };
}"""


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def one_line(s: str, n: int = 200) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def short_url(url: str) -> str:
    """Une URL de résumé : sans sa requête (elle peut porter un jeton), 300 caractères au plus."""
    p = urlsplit(url)
    base = f"{p.scheme}://{p.netloc}{p.path}" if p.scheme in ("http", "https", "ws", "wss") else url
    return (base + ("?…" if p.query else ""))[:300]


def nfr_union(*lists) -> list:
    got = {n for lst in lists for n in (lst or [])}
    return [n for n in NFR_ORDER if n in got]


def clean(value):
    """Les chaînes venues de la page, rendues écrivables en UTF-8 : une moitié de paire de
    substitution (un .slice() JavaScript au milieu d'un émoji) devient U+FFFD."""
    if isinstance(value, str):
        return value.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
    if isinstance(value, list):
        return [clean(v) for v in value]
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    return value


def ui_profile_sha256() -> str:
    """L'empreinte du profil d'interface du driver : SELECTORS et KNOWN_MODALS, avec le motif du
    flux et les constantes de fin de réponse (proposition 9 du debrief, que la recette 2 annonce
    au juge), en JSON canonique (clés triées, sans espaces, UTF-8). Sur chaque ligne de journal :
    deux lignes qui la portent ont été lues avec les mêmes adresses de page, les mêmes modales
    connues et la même règle de fin de réponse (D12)."""
    profile = {"known_modals": KNOWN_MODALS, "selectors": SELECTORS, "stream_pattern": STREAM_PATTERN,
               "end_of_answer": {"display_stable_s": DISPLAY_STABLE_S, "display_cap_s": DISPLAY_CAP_S,
                                 "no_answer_wait_s": NO_ANSWER_WAIT_S}}
    data = json.dumps(profile, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def budget_of(case: dict) -> float:
    """Le budget de latence d'un cas : le sien, sinon celui de sa classe, sinon DEFAULT_BUDGET_S. Un
    message qui porte des données identifiantes collées (un inventaire `pii`) est de la classe
    « document collé » (PROCEDURE-2 §5 : `detect_pii` jusqu'à 11 s le 17/10)."""
    if case.get("budget_s"):
        return case["budget_s"]
    if case.get("kind") == "message" and case.get("pii"):
        return PASTED_PII_BUDGET_S
    return CLASS_BUDGET_S.get(case.get("kind"), DEFAULT_BUDGET_S)


def parse_guard(card: str, body: str, body_found: bool) -> dict:
    """L'en-tête d'une carte de réponse (la carte hors de son corps) en champs : le modèle de la
    bannière, le texte du panneau de garde, PII détectée ou non (None : rien de lisible), les
    jetons de masquage, la marque « en direct » après la bannière. Forme vue le 17/10 :
    « <panneau de garde> maskai <fournisseur>/<modèle>[ · en direct] <corps> »."""
    card_l, body_l = one_line(card, 100_000), one_line(body, 100_000)
    head, sep, tail = card_l.rpartition(body_l) if body_found and body_l else ("", "", card_l)
    header = f"{head} {tail}" if sep else card_l  # the body is the card's last block
    header = one_line(header, 100_000)
    # The model is the token after « maskai » (17/10), else the first « provider/model » whose
    # provider starts with a letter: a date in the header (« le 17/10 ») is never a model.
    m = re.search(SELECTORS["brand_model"], header, re.I)
    start, end = (m.start(), m.end()) if m else (None, None)
    model = m.group(1) if m else None
    if m is None:
        m = re.search(SELECTORS["model_banner"], header)
        if m:
            start, end, model = m.start(), m.end(), m.group(0)
    guard_text, after = header, ""
    if model:
        guard_text = re.sub(SELECTORS["brand"], "", header[:start], flags=re.I).strip()
        after = header[end:]
    if re.search(SELECTORS["guard_pii"], guard_text, re.I):
        pii = True
    elif re.search(SELECTORS["guard_clean"], guard_text, re.I):
        pii = False
    else:
        pii = None
    # v2.3, E7 : le nombre de données identifiantes que le panneau déclare (« Le Chirurgien : 2 PII
    # détectées ») ; 0 pour « aucune donnée personnelle détectée » ; None sans compte lisible.
    count = re.search(r"\b(\d+)\s*PII\b", guard_text, re.I)
    pii_count = int(count.group(1)) if count else (0 if pii is False else None)
    return {"model": model, "guard_text": guard_text[:600] or None, "pii_detected": pii, "pii_count": pii_count,
            "tokens": re.findall(SELECTORS["mask_token"], guard_text)[:50],
            "live": bool(re.search(SELECTORS["live_marker"], after, re.I)), "header": header[:600]}


def chip_state(reading: dict | None, name: str) -> str:
    """L'état brut d'une lecture de puce (CHIP_JS) : absent, failed, uploading, indexing, ready (un
    marqueur explicite) ou unmarked (présente, aucun marqueur). Le nom du fichier est retiré du
    texte avant les motifs : il ne dit rien de l'état."""
    if not reading:
        return "absent"
    words = " ".join([reading.get("text") or "", *(reading.get("labels") or [])])
    for part in (name, Path(name).stem):
        if part:
            words = words.replace(part, " ")
    attr = (reading.get("attr") or "").lower()
    if attr in ("error", "failed") or re.search(SELECTORS["attach_failed"], words, re.I):
        return "failed"
    if attr == "uploading" or re.search(SELECTORS["attach_uploading"], words, re.I):
        return "uploading"
    if (reading.get("busy") or attr in ("loading", "indexing", "pending")
            or re.search(SELECTORS["attach_pending"], words, re.I)):
        return "indexing"
    if attr in ("ready", "done", "indexed") or re.search(SELECTORS["attach_ready"], words, re.I):
        return "ready"
    return "unmarked"


class ChipWatch:
    """La machine à états de la puce, nourrie de lectures successives (chip_state) :
    absent → uploading → indexing → ready | failed. `ready` vient d'un marqueur explicite
    (`ready_by: marker`), d'une puce sans marqueur après un état d'attente vu (`after_pending`),
    ou d'une puce sans marqueur ni attente depuis `quiet_s` (`quiet` : prête présumée, le
    debrief le distingue). Avant `quiet_s`, une puce sans marqueur est `present` : pas encore
    prête (le défaut D7 était de la lire « attachée » en 0,1 s). `failed` est final."""

    FINAL = ("ready", "failed")

    def __init__(self, quiet_s: float = ATTACH_QUIET_S):
        self.quiet_s = quiet_s
        self.state, self.ready_by, self.pending_seen = "absent", None, False
        self.trace = []  # [{"t": s, "state": ...}], à chaque changement d'état
        self._unmarked_since = None

    def feed(self, raw: str, t: float) -> str:
        if self.state == "failed":
            return self.state
        by = None
        if raw in ("uploading", "indexing"):
            self.pending_seen = True
        if raw == "unmarked":
            if self._unmarked_since is None:
                self._unmarked_since = t
            if self.pending_seen:
                state, by = "ready", "after_pending"
            elif t - self._unmarked_since >= self.quiet_s:
                state, by = "ready", "quiet"
            else:
                state = "present"
        else:
            self._unmarked_since = None
            state, by = raw, ("marker" if raw == "ready" else None)
        if state != self.state:
            self.trace.append({"t": round(t, 2), "state": state})
        self.state, self.ready_by = state, by
        return state


def norm_label(s: str) -> str:
    """Un libellé pour comparer : minuscules, `-`, `_`, `.` et blancs réduits à une espace."""
    return re.sub(r"[\s._-]+", " ", (s or "").casefold()).strip()


def model_matches(want: str, seen: str | None) -> bool:
    """Le modèle `seen` (relu sur le bouton ou sur la bannière) est-il `want` ? L'un contient
    l'autre, à la casse et aux séparateurs près (« gemini » et « google/eu.gemini-3-flash »)."""
    w, v = norm_label(want), norm_label(seen or "")
    return bool(w and v) and (w in v or (len(v) >= 4 and v in w))


def choose_option(want: str, options: list) -> tuple[dict | None, list]:
    """L'option à choisir pour `want`, et celles qui convenaient : égalité du texte ou de la valeur
    (la première en cas d'égalités), sinon `want` contenu dans le texte ou la valeur, sinon un texte
    d'au moins quatre caractères contenu dans `want`. Hors égalité, une seule option doit convenir :
    plusieurs, c'est ambigu (« gemini » devant Gemini 3 Flash et Gemini 3 Pro) et rien n'est choisi
    — rend (None, les candidates) ; aucune : (None, [])."""
    w = norm_label(want)
    shown = [o for o in options if o.get("visible", True)] or options
    exact = [o for o in shown if w and w in (norm_label(o["text"]), norm_label(o["value"]))]
    if exact:
        return exact[0], exact
    for test in (lambda o: w in norm_label(o["text"]) or (o["value"] and w in norm_label(o["value"])),
                 lambda o: (len(norm_label(o["text"])) >= 4 and norm_label(o["text"]) in w)
                 or (len(norm_label(o["value"])) >= 4 and norm_label(o["value"]) in w)):
        found = [o for o in shown if w and test(o)]
        if len(found) == 1:
            return found[0], found
        if found:
            return None, found
    return None, []


def model_of(control: dict | None) -> str | None:
    """Le modèle affiché par le bouton du compositeur : son aria-label sans « Model: » ; un libellé
    sans ce préfixe (le bouton trouvé par sa structure, « Choisir le modèle ») ne nomme pas le
    modèle : c'est alors le texte du bouton."""
    if not control:
        return None
    label = control["label"] or ""
    if re.match(SELECTORS["model_prefix"], label, flags=re.I):
        return re.sub(SELECTORS["model_prefix"], "", label, flags=re.I).strip() or control["text"] or None
    return control["text"] or label or None


def web_mode_of(control: dict | None) -> str | None:
    """Le mode de recherche web affiché : data-mode s'il dit off/auto/on, sinon le mot du
    libellé après « : » (« Web search: Auto »)."""
    if not control:
        return None
    if control.get("mode", "").lower() in WEB_MODES:
        return control["mode"].lower()
    return web_word(re.sub(r"^[^:]*:\s*", "", control["label"] or control["text"]))


def web_word(text: str) -> str | None:
    for mode in ("off", "auto", "on"):  # off first: « désactivée » contains « activ »
        if re.search(SELECTORS["web_words"][mode], text or "", re.I):
            return mode
    return None


def web_field(post_body: str | None) -> str | None:
    """Le champ web_search du POST envoyé au flux, s'il en porte un."""
    if not post_body:
        return None
    try:
        value = json.loads(post_body).get("web_search")
        return str(value).lower() if value is not None else None
    except (ValueError, AttributeError):
        m = re.search(r'"web_search"\s*:\s*"(\w+)"', post_body)
        return m.group(1).lower() if m else None


def long_questions(case: dict, turns: int) -> list:
    """Les questions d'un cas `long` : ses `messages`, les `turns` premiers (PROCEDURE-2) ; ou
    `first`, puis `filler` pour chaque tour du milieu ({i} est le rang du tour), puis `last`."""
    messages = case.get("messages")
    if messages:
        if not 2 <= turns <= len(messages):
            raise ValueError(f"case {case['id']}: turns must be 2 to {len(messages)}, the messages it has")
        return list(messages[:turns])
    first, filler, last = case.get("first"), case.get("filler"), case.get("last")
    if turns < 2 or not first or not last or (turns > 2 and not filler):
        raise ValueError(f"case {case['id']}: a long case needs first, last, filler and turns >= 2")
    return [first] + [filler.format(i=i) for i in range(2, turns)] + [last]


def is_placeholder(model: str | None) -> bool:
    """« <modele-B> » : un modèle que le harnais lit dans le sélecteur à l'exécution (PROCEDURE-2)."""
    return bool(model) and model.strip().startswith("<") and model.strip().endswith(">")


def sent_digest(text: str | None) -> str | None:
    """L'empreinte du texte envoyé, à la numérotation des jetons près (<PERSON_2> vaut <PERSON_1>)
    et aux blancs près : deux passages d'un même cas se comparent par elle (replay_of)."""
    if text is None:
        return None
    norm = re.sub(r"\s+", " ", re.sub(r"<([A-Z][A-Z_]*)_\d+>", r"<\1>", text)).strip()
    return "sha256:" + hashlib.sha256(norm.encode("utf-8")).hexdigest()


TEXT_SUFFIXES = (".txt", ".md", ".json", ".csv")


def document_in_sent(path: Path, sent: str | None) -> bool | None:
    """Le document joint figure-t-il dans le texte envoyé (PROCEDURE-2 §8.8) ? True si au moins 30 %
    de ses mots de six lettres ou plus s'y retrouvent ; None quand on ne peut pas le dire (rien
    d'envoyé observé, ou un format que le driver ne lit pas en texte : PDF, DOCX, image…)."""
    if sent is None or path.suffix.lower() not in TEXT_SUFFIXES:
        return None
    words = set(re.findall(r"\w{6,}", evaluer.fold(path.read_text(encoding="utf-8", errors="replace"))))
    if not words:
        return None
    seen = evaluer.fold(sent)
    return sum(w in seen for w in words) * 10 >= len(words) * 3


def turn_summary(i: int, question: str, obs: dict) -> dict:
    guard = obs.get("guard") or {}
    return {"turn": i, "question": question, "answer": (obs.get("answer") or "")[:4000],
            "ttft_s": obs.get("ttft_s"), "total_s": obs.get("total_s"), "display_s": obs.get("display_s"),
            "display_stable": obs.get("display_stable"), "end": obs.get("end"), "model": guard.get("model"),
            "pii_detected": guard.get("pii_detected"), "sent_source": (obs.get("sent_info") or {}).get("source"),
            "sent_to_model": (obs.get("sent_to_model") or "")[:4000] or None, "pipeline_ms": obs.get("pipeline_ms"),
            "error_class": (obs.get("failure") or {}).get("error_class"),
            "internal_error_shown": shown_internal(obs)}


def shown_internal(obs: dict) -> bool:
    """Une question a-t-elle montré une erreur interne à l'utilisateur (revue de la v2.3, I3 et m11) ? Ce
    que la page a dit d'un échec silencieux (la ligne d'état, les alertes, les toasts : `_diagnose`), un avis
    d'erreur de la page pendant la question, même répondue (round 2, leakage-1), ou une réponse qui affiche
    une erreur interne."""
    return (bool((obs.get("failure") or {}).get("internal_error_shown"))
            or bool((obs.get("notices") or {}).get("internal_error_shown"))
            or answer_shows_internal(obs.get("answer")))


def looks_like_sent(shown: str | None, question: str, pii=()) -> bool:
    """Le texte du popover est-il le message envoyé (masqué ou non), et pas un simple statut
    (« Encrypted — no PII detected ») ? Au moins la moitié des mots de quatre lettres ou plus de la
    question doivent s'y retrouver. Les valeurs de l'inventaire `pii` sont retirées de la question
    d'abord : masquées, elles deviennent des jetons et ne peuvent pas compter contre le texte (N9-1
    tenait sinon à 8 mots sur 16). Sinon la fuite de PII n'est pas vérifiable, et le verdict le
    dit, plutôt que de juger un statut comme si c'était le texte envoyé."""
    q = evaluer.fold(question or "")
    for value in pii or ():
        if not str(value).startswith("("):  # « (inventaire descriptif) » is no value
            q = q.replace(evaluer.fold(str(value)), " ")
    words = set(re.findall(r"\w{4,}", q))
    if not shown or not words:
        return False
    seen = evaluer.fold(shown)
    return sum(w in seen for w in words) * 2 >= len(words)


def details_open(seen: dict) -> bool:
    """Le panneau « Détails » lu par DETAILS_JS est-il ouvert ? Son bouton le dit (aria-expanded) ;
    sans aria-expanded, un contenu ouvert dans la carte (hors des rangs exclus) le dit."""
    expanded = seen.get("expanded")
    return expanded == "true" or (expanded not in ("true", "false") and bool(seen.get("open")))


def details_triggers() -> list:
    """Les sélecteurs de `card_details_trigger` tels que le driver les cherche dans une carte : le
    préfixe « article » retiré (la carte est l'article), et `:has-text("…")` lu à part, comme
    Playwright le lit (le texte du bouton le contient, casse et blancs ignorés)."""
    out = []
    for sel in SELECTORS["card_details_trigger"]:
        css, mark, rest = sel.partition(':has-text("')
        text = " ".join(rest[:-2].split()).lower() if mark and rest.endswith('")') else None
        css = css.strip()
        if css.startswith("article "):
            css = css[len("article "):].strip()
        out.append({"sel": sel, "css": css or "*", "text": text})
    return out


def details_text(raw: str | None, block: str | None, question: str) -> tuple[str | None, str | None]:
    """Le texte envoyé que montre le panneau « Détails », sans son libellé : le bloc de texte du
    panneau (`sent_text`) s'il porte au moins la moitié du texte (un jeton en <code> n'est pas le
    message), sinon le texte entier privé de son libellé : `details_label`, à défaut une première
    ligne qui finit par « : », que la question ne contient pas (« Voici un document : » ouvre des
    questions du corpus v3 : jamais retiré) et qui ne porte aucun jeton de masquage (« Bonjour
    <PERSON_1> : » est du texte envoyé, jamais un libellé). Rend (texte, libellé retiré ou None)."""
    if not raw or not raw.strip():
        return None, None
    raw = raw.strip()
    m = re.match(SELECTORS["details_label"], raw, flags=re.I)
    if m is None:
        m = re.match(r"([^\n]{1,120}?)\s*:[ \t]*\n", raw)
        if m and (evaluer.fold(m.group(1)).strip() in evaluer.fold(question or "")
                  or re.search(SELECTORS["mask_token"], m.group(1))):
            m = None
    stripped = raw[m.end():].strip() if m else raw
    block = (block or "").strip()
    text = block if block and len(block) * 2 >= len(stripped) else stripped
    at = raw.find(text)
    label = raw[:at].strip() if at > 0 else (raw[:m.end()].strip() if m and text == stripped else None)
    return text or None, label or None


# v2.3, E7 : un jeton de masquage et son type.
TOKEN_TYPE = r"<([A-Z][A-Z_]*)_\d+>"


def token_inventory(text: str | None) -> dict | None:
    """Les types des jetons de masquage d'un texte envoyé, numérotation repliée comme pour sent_sha256
    (<PERSON_2> compte pour un PERSON), chaque occurrence comptée : {"PERSON": 2, "PHONE": 1} ; {} sans
    jeton ; None quand le texte envoyé n'a pas été observé (E7)."""
    if text is None:
        return None
    counts = {}
    for kind in re.findall(TOKEN_TYPE, text):
        counts[kind] = counts.get(kind, 0) + 1
    return dict(sorted(counts.items()))


def signed_types(plus: dict, minus: dict) -> str:
    """« +X −Y » : les types en plus, puis en moins, au passage b, avec leur nombre au-delà de un."""
    def fmt(sign: str, kind: str, n: int) -> str:
        return sign + kind + (f" ×{n}" if n > 1 else "")
    return " ".join([fmt("+", k, plus[k]) for k in sorted(plus) if plus[k] > 0]
                    + [fmt("−", k, minus[k]) for k in sorted(minus) if minus[k] > 0])


def type_delta(first: dict, mine: dict) -> str:
    """Les types en plus et en moins au passage b (`signed_types`), d'un compte par type à l'autre."""
    d = {k: mine.get(k, 0) - first.get(k, 0) for k in set(first) | set(mine)}
    return signed_types({k: v for k, v in d.items() if v > 0}, {k: -v for k, v in d.items() if v < 0})


def entity_note(first: list, mine: list) -> str:
    """Les entités masquées des passages a (`first`) et b (`mine`), lues par masked_entities, comparées
    (revue de la v2.3, m6 ; le contrat, point 5) : « entités masquées identiques », ou « entités masquées
    différentes : +X −Y », les types des entités masquées en plus, puis en moins, au passage b ; jamais
    leur valeur (une donnée du corpus)."""
    if first == mine:
        return "entités masquées identiques"
    a, b = Counter(first), Counter(mine)
    plus, minus = Counter(), Counter()
    for (kind, _), n in (b - a).items():
        plus[kind] += n
    for (kind, _), n in (a - b).items():
        minus[kind] += n
    return "entités masquées différentes : " + signed_types(plus, minus)


def inventory_note(first: dict | None, mine: dict | None) -> str:
    """Les types masqués des passages a (`first`) et b (`mine`) comparés, quand les entités elles-mêmes ne
    peuvent pas l'être (le texte envoyé du passage a n'est plus là) : « types masqués identiques », ou
    « types masqués différents : +X −Y ». Des types identiques ne prouvent pas des entités identiques
    (revue de la v2.3, m6) : la note dit « types », jamais « ensemble »."""
    if first is None or mine is None:
        which = "a" if first is None else "b"
        return f"types masqués non comparables (texte envoyé non observé au passage {which})"
    if first == mine:
        return "types masqués identiques"
    return "types masqués différents : " + type_delta(first, mine)


def masked_entities(sent: str | None, question: str | None) -> list | None:
    """Les entités que le texte envoyé masque, lues contre la question posée : chaque jeton <TYPE_n> prend
    la portion de la question qu'il remplace, alignée par le texte qui l'entoure (blancs réduits). Rend la
    liste triée des (type, portion repliée), ou None quand l'alignement échoue (le texte envoyé n'est pas la
    question masquée, deux jetons se touchent). Le contrat, point 5 : un ensemble différent d'entités
    masquées, à la numérotation près (revue de la v2.3, m6)."""
    if sent is None or not question:
        return None
    norm = lambda s: re.sub(r"\s+", " ", s)  # noqa: E731
    q = norm(question).strip()
    parts = re.split(r"(<[A-Z][A-Z_]*_\d+>)", norm(sent).strip())
    literals, kinds = parts[0::2], [re.match(r"<([A-Z][A-Z_]*)_\d+>", t).group(1) for t in parts[1::2]]
    if not q.startswith(literals[0]):
        return None
    cursor, out = len(literals[0]), []
    for i, kind in enumerate(kinds):
        nxt = literals[i + 1]
        last = i == len(kinds) - 1
        if last:
            if not q.endswith(nxt) or len(q) - len(nxt) <= cursor:
                return None
            end = len(q) - len(nxt)
        elif not nxt:
            return None  # two tokens side by side: their portions cannot be told apart
        else:
            end = q.find(nxt, cursor + 1)
            if end < 0:
                return None
        out.append((kind, evaluer.fold(q[cursor:end]).strip()))
        cursor = end + len(nxt)
    if not kinds and q != literals[0]:
        return None
    return sorted(out)


# v2.3, E9 et E10 : ce qu'une erreur montrée par la page ne porte jamais jusqu'au journal ni jusqu'au
# harnais : pile logicielle, adresse, chemin, nom d'hôte. Le texte brut ne va qu'aux preuves de l'action.
STACK_RE = re.compile(r"Traceback \(most recent call last\):?|File \"[^\"]*\", line \d+(?:, in [\w.<>]+)?"
                      r"|\bat [\w.$<>\[\]]+ \([^)]*\)|\bat \S+:\d+:\d+")
URL_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.I)
IP_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b")
PATH_RE = re.compile(r"(?:\b[A-Za-z]:)?(?:[\\/][\w.@~-]+){2,}[\\/]?")
QUOTED_HOST_RE = re.compile(r"\b(host(?:\s*name)?|hostname|server(?:\s+at)?|h[ôo]te)\s*(?:[=:]\s*[^\s,;]+|\"[^\"]*\"|'[^']*')",
                            re.I)
HOST_RE = re.compile(r"\b(?:[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?\.)+[a-z][a-z0-9_-]*\b(?::\d+)?", re.I)
# Revue de la v2.3, I2 : les formes ordinaires d'un hôte interne sans point. Un nom suivi d'un port
# (redis:6379), et le mot qui suit une résolution ou une connexion (ENOTFOUND x, lookup x, connect to
# [host] x, host x, server at x), entre guillemets ou non, après « = » ou non.
# Round 2 (leakage-4) : jamais un mot d'erreur ou d'état devant les deux-points (« Erreur:500 », « code:404 »).
NAME_PORT_RE = re.compile(r"(?<![\w.-])(?!(?:erreur|error|err|code|http|https|status|statut|warning|avertissement"
                          r"|ligne|line|page|etape|step|tour|turn|article|art)\b)[a-z][\w-]*:\d{2,5}\b", re.I)
KEYWORD_HOST_RE = re.compile(
    r"\b(ENOTFOUND|EAI_AGAIN|lookup|resolve|connect(?:ing)?\s+to|host(?:\s*name)?|hostname|server\s+at"
    r"|server(?=\s*[=:]))((?:\s+(?:host(?:\s*name)?|hostname|server(?:\s+at)?))?\s*[=:]?\s*)"
    r"(\"[^\"]*\"|'[^']*'|[^\s,;()\"']+)", re.I)


def sanitise(text: str | None, n: int = EXCERPT_MAX, keep=()) -> str:
    """Un texte d'erreur sans pile, adresse, chemin ni nom d'hôte, sur une ligne de `n` caractères au
    plus : chaque élément retiré devient <adresse>, <chemin> ou <hôte>. `keep` : les noms à garder tels
    quels (le nom du fichier joint, qui a la forme d'un nom d'hôte). Au mieux : un nom d'hôte sans point
    ni mot-clé (« host », « server ») passe."""
    s = STACK_RE.sub(" ", text or "")
    s = URL_RE.sub("<adresse>", s)
    s = QUOTED_HOST_RE.sub(lambda m: m.group(1) + " <hôte>", s)
    s = KEYWORD_HOST_RE.sub(lambda m: m.group(1) + m.group(2) + "<hôte>", s)
    s = IP_RE.sub("<hôte>", s)
    s = PATH_RE.sub("<chemin>", s)
    kept = {k.lower() for k in keep if k}
    s = HOST_RE.sub(lambda m: m.group(0) if m.group(0).lower() in kept else "<hôte>", s)
    s = NAME_PORT_RE.sub(lambda m: m.group(0) if m.group(0).lower() in kept else "<hôte>", s)
    return one_line(s, n)


def exposes_internals(text: str | None, keep=()) -> bool:
    """Le texte montre-t-il un détail interne (pile, adresse, chemin, nom d'hôte) ? Ce que le harnais
    apprend d'une puce en échec (`internal_error_shown`), sans en recevoir le texte."""
    whole = 10 ** 6
    return bool(text) and sanitise(text, whole, keep) != one_line(text, whole)


# Revue de la v2.3, m11 : ce qui fait d'une réponse affichée une erreur interne (une pile, ou une erreur de
# résolution ou de connexion) ; avec un détail interne (exposes_internals), la réponse en montre une.
INTERNAL_ERROR_RE = re.compile(r"Traceback \(most recent call last\)|File \"[^\"]+\", line \d+|could not translate host"
                               r"|name or service not known|ENOTFOUND|ECONNREFUSED|EAI_AGAIN|getaddrinfo"
                               r"|OperationalError|connection refused", re.I)


# Round 2 (m11, partial in round 1) : les mots d'une erreur de connexion ou de résolution, ceux d'I2 compris.
CONNECTION_ERROR_RE = re.compile(r"ETIMEDOUT|EHOSTUNREACH|ECONNRESET|ECONNREFUSED|ENOTFOUND|EAI_AGAIN|timed out"
                                 r"|no such host|connecting to|cannot connect|could not connect|failed to connect"
                                 r"|connection (?:error|reset|refused|timed out)|name resolution|HTTPConnectionPool"
                                 r"|getaddrinfo", re.I)


def answer_shows_internal(answer: str | None) -> bool:
    """La réponse affichée montre-t-elle une erreur interne (m11) ? Une pile ou une erreur de connexion,
    avec un hôte, un chemin ou une pile : c'est alors une constatation de sécurité (PROCEDURE-3 §2). Round 2 :
    aussi les mots d'une erreur de connexion (CONNECTION_ERROR_RE) avec un hôte, nommé ou après un mot-clé
    (« redis:6379 », « connect to host x », « lookup x ») ; jamais une prose ordinaire."""
    if not answer:
        return False
    if INTERNAL_ERROR_RE.search(answer) and exposes_internals(answer):
        return True
    return bool(CONNECTION_ERROR_RE.search(answer)) and bool(KEYWORD_HOST_RE.search(answer) or NAME_PORT_RE.search(answer)
                                                             or exposes_internals(answer))


# E9 : la classe d'une puce en échec, lue sur son libellé brut replié (casse, accents), le nom du fichier
# retiré ; la première qui convient.
ATTACH_ERRORS = (
    ("service documentaire injoignable",
     r"could not translate host|name or service not known|nodename nor servname|name resolution|getaddrinfo"
     r"|enotfound|econnrefused|connection refused|could not connect|operationalerror|psycopg|postgres"
     r"|database|base de donnees|injoignable|unreachable"),
    ("fichier trop volumineux", r"too large|trop (?:volumineux|gros|lourd)|size limit|file size|exceeds|depasse|\b413\b|taille"),
    ("format refusé", r"format|unsupported|not supported|non (?:pris en charge|supporte)|file type|type de fichier|mime|extension"),
)


def attach_error_class(label: str | None, name: str = "") -> str:
    """La classe d'erreur d'une puce en échec (E9) : « service documentaire injoignable » (une base ou un
    hôte injoignable), « fichier trop volumineux », « format refusé », sinon « échec de la pièce
    jointe ». Sans nom d'hôte ni pile, par construction."""
    text = label or ""
    stem = Path(name).stem if name else ""
    for part in (name, stem if len(stem) >= 4 else ""):  # whole tokens only: « x.pdf » never eats an « x »
        if part:
            text = re.sub(rf"(?<!\w){re.escape(part)}(?!\w)", " ", text)
    folded = evaluer.fold(text)
    for cls, pattern in ATTACH_ERRORS:
        if re.search(pattern, folded):
            return cls
    return "échec de la pièce jointe"


def chip_shows_internal(label: str | None, name: str = "") -> bool:
    """La puce en échec montre-t-elle une erreur interne (E9, revue I2) ? Un détail interne lisible (pile,
    adresse, chemin, hôte), ou une classe de service injoignable : une base ou un hôte injoignable est
    une erreur interne montrée à l'utilisateur, même quand le libellé ne nomme rien qu'on sache lire."""
    return exposes_internals(label, keep=(name,)) or attach_error_class(label, name) == "service documentaire injoignable"


# Revue de la v2.3, m7 : un texte d'état qui dit une erreur ; une alerte ou un toast en est une d'office.
ERROR_TEXT_RE = (r"erreur|error|echec|failed|failure|impossible|indisponible|unavailable|reessay|retry"
                 r"|probleme|problem|refus|denied|timeout|delai|injoignable|unreachable")


def error_notices(notices: list) -> list:
    """Les avis de la page qui disent une erreur (m7) : une alerte, un toast, ou un texte d'état d'erreur ;
    une mise à jour ordinaire d'une région vivante n'en est pas une."""
    return [n for n in notices or [] if n[0] in ("alert", "toast")
            or re.search(ERROR_TEXT_RE, evaluer.fold(n[1] or ""))]


def text_shows_internal(text: str | None, keep=()) -> bool:
    """Un texte de la page montre-t-il une erreur interne ? Un détail interne (hôte, chemin, pile), `keep`
    gardés tels quels (le nom du fichier joint, les hôtes de la liste du driver : round 2, leakage-4), ou un
    service injoignable (la classe « service documentaire injoignable » d'E9)."""
    return bool(text) and (exposes_internals(text, keep) or bool(re.search(ATTACH_ERRORS[0][1], evaluer.fold(text))))


def notices_internal(notices: list, keep=()) -> bool:
    """Les avis de la page montrent-ils une erreur interne (round 2, leakage-4) ? Seuls les avis d'erreur
    (error_notices) comptent : un texte d'état ordinaire, le nom du fichier joint, l'hôte de l'application ou
    un code HTTP n'en font pas une."""
    return any(text_shows_internal(text, keep) for _, text in error_notices(notices))


def harness_text(text: str | None, titles=(), n: int = HARNESS_TEXT_MAX, keep=()) -> tuple[str, bool]:
    """Un texte de la page qui va au harnais (le message d'un dialogue, le texte d'une fenêtre inconnue ;
    round 2, leakage-2, R2-4) : expurgé (sanitise), et remplacé en entier par « … » quand il porte les douze
    premiers caractères du titre d'une conversation listée ; et s'il montre une erreur interne."""
    raw = text or ""
    internal = text_shows_internal(raw, keep)  # round 3, M-2: the case's file names, the app's hosts kept
    folded = evaluer.fold(raw)
    for title in titles or ():
        whole = evaluer.fold(title or "").strip()
        # round 3, M-2: the title's first twelve characters, or a cut title (« Avis juridi… »): any prefix of it
        # of eight characters or more, three of them letters or digits
        for k in range(min(12, len(whole)), 7, -1):
            key = whole[:k]
            if len(re.findall(r"\w", key)) >= 3 and key in folded:
                return "…", internal
        # round 4, R-1: a title of 3 to 7 characters, quoted whole (word boundaries, any case)
        if 3 <= len(whole) < 8 and len(re.findall(r"\w", whole)) >= 3 and re.search(
                rf"(?<!\w){re.escape(whole)}(?!\w)", folded):
            return "…", internal
    return sanitise(raw, n, keep=tuple(keep)), internal


def sha256_text(text: str) -> str:
    """L'empreinte d'une ligne de journal telle qu'écrite (round 2, robustness-5 : l'observation qu'une ligne
    a jugée porte celle de sa ligne)."""
    return "sha256:" + hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def css_and_text(sel: str) -> dict:
    """Un sélecteur Playwright lu comme une page le lit : la partie CSS, et le texte de `:has-text("…")` à part
    (contenu, casse et blancs ignorés), comme Playwright le lit (round 2, robustness-4 : ces sélecteurs sont
    cherchés dans un appel borné, jamais par locator.count())."""
    css, mark, rest = sel.partition(':has-text("')
    text = " ".join(rest[:-2].split()).lower() if mark and rest.endswith('")') else None
    return {"sel": sel, "css": css.strip() or "*", "text": text}


def stream_digest(events: list) -> dict:
    """Les derniers événements du flux d'un tour (window.__ev, chaque ligne coupée à 160 caractères) :
    leurs noms (<START>, node:detect_pii, event:error, error, data, <END>…), le dernier nœud, et le texte
    brut de la dernière charge utile d'erreur (un `event: error`, ou un objet qui porte error, detail ou
    exception, ou message sous un type error)."""
    names, last_node, error, event = [], None, None, None
    for _, line in events or []:
        line = (line or "").strip()
        if line.startswith("<"):
            names.append(line.split(">")[0] + ">")
            continue
        if line.startswith("event:"):
            event = line[6:].strip()
            names.append("event:" + event)
            continue
        payload = line[5:].strip() if line.startswith("data:") else line
        try:
            obj = json.loads(payload)
        except ValueError:
            obj = None
        text = None
        if isinstance(obj, dict):
            if obj.get("node"):
                last_node = str(obj["node"])
            for key in ("error", "detail", "exception", "message"):
                if obj.get(key) and (key != "message" or event == "error" or obj.get("type") == "error"):
                    text = obj[key] if isinstance(obj[key], str) else json.dumps(obj[key], ensure_ascii=False)
                    break
            if text is None and (event == "error" or obj.get("type") == "error"):
                text = payload
        else:
            m = re.search(r'"(?:error|detail|exception)"\s*:\s*"([^"]*)', payload)
            text = m.group(1) if m else (payload if event == "error" else None)
        if text:
            error = text
            names.append("error")
        elif isinstance(obj, dict) and obj.get("node"):
            names.append("node:" + str(obj["node"]))
        else:
            names.append("data")
        event = None
    compact = []
    for name in names:  # a run of answer chunks is one « data ×n »
        if compact and compact[-1][0] == name:
            compact[-1][1] += 1
        else:
            compact.append([name, 1])
    return {"names": [n if k == 1 else f"{n} ×{k}" for n, k in compact][-12:], "last_node": last_node, "error": error}


def failure_class(http: int | None, error: str | None, notices: list, names: list, seen: bool) -> str:
    """La classe d'un échec silencieux (E10), sans nom d'hôte ni pile par construction : le statut HTTP du
    flux d'abord, puis une base ou un hôte injoignable dans ce que disent le flux ou la page, puis
    l'erreur du flux, l'erreur affichée, le flux coupé, le flux fini sans rien."""
    if http is None and not seen:
        return "aucun flux"
    if http is not None and http >= 400:
        if http >= 500:
            return f"erreur serveur (HTTP {http})"
        if http == 429:
            return "limite de débit (HTTP 429)"
        if http in (401, 403):
            return f"session refusée (HTTP {http})"
        return f"requête refusée (HTTP {http})"
    notices = error_notices(notices)
    said = evaluer.fold(" ".join([error or ""] + [t for _, t in notices]))
    if re.search(ATTACH_ERRORS[0][1], said):
        return "service interne injoignable"
    if error:
        return "erreur signalée par le flux"
    if notices:
        return "erreur affichée par l'application"
    if any(n.startswith("<ABORT>") for n in names):
        return "flux coupé"
    return "flux terminé sans réponse"


# v2.3, E3 : une entrée de menu (texte replié) qui viserait plus d'une conversation.
# Revue de la v2.3, I7 : « tous », « toute », « every », « sélection », « selected » s'y ajoutent.
MENU_TOO_WIDE = (r"\btou(?:t|te|tes|s)\b|\ball\b|\bevery|\bentire|\bbulk|selection|\bselected\b|historique"
                 r"|history|\bvider\b|\bclear\b")

# Round 2 (R2-3, registry-1, I7 partial) : ce qui, dans le texte d'une confirmation de suppression, viserait
# explicitement plus d'une conversation : un quantificateur devant un nom de conversation (« toutes les
# conversations », « all chats », « chaque conversation », « toute conversation », « plusieurs… »), « tout
# l'historique » et « all / clear (your) history » (hors celui de cette conversation), vider, un contenant
# (dossier, projet, espace, groupe, sélection). Un nom au pluriel seul (« vos conversations ») et l'historique
# seul (« de votre historique ») ne visent plus rien : ce sont des formes d'une seule conversation.
_CONV_NOUN = r"(?:conversations?|discussions?|chats?|threads?)"
_THIS_ONE = r"(?!\s+(?:de\s+(?:la|cette)\s+(?:conversation|discussion)|du\s+(?:chat|fil)|of\s+(?:this|the)\s+(?:chat|conversation|thread)))"
CONFIRM_TOO_WIDE = (rf"\btou(?:te)?s\s+(?:les|vos|tes|nos|ces|leurs|mes|ses)\s+(?:\w+\s+)?{_CONV_NOUN}\b"
                    rf"|\btoute\s+{_CONV_NOUN}\b"
                    rf"|\b(?:all|every|each)\s+(?:(?:of\s+)?(?:the|your|my|these|those)\s+)?(?:\w+\s+)?{_CONV_NOUN}\b(?!['’]s\b)"
                    rf"|\b(?:chaque|plusieurs|several)\s+(?:\w+\s+)?{_CONV_NOUN}\b"
                    rf"|\btout\s+l['’]?\s*historique{_THIS_ONE}"
                    rf"|\b(?:all|clear)\s+(?:all\s+)?(?:of\s+)?(?:your\s+|the\s+|my\s+)?(?:chat\s+)?history{_THIS_ONE}"
                    r"|\bvider\b|\b(?:dossiers?|folders?|projets?|projects?|espaces?|workspaces?|groupes?|groups?)\b"
                    r"|selection|\bselected\b|\bbulk\b")
CONFIRM_DELETE_RE = r"\b(?:supprim\w*|suppression|delete\w*|deletion|effac\w*|remov\w*|retir\w*)\b"
# Round 3 (I-2, binding) : une confirmation n'est acceptée que si elle nomme exactement une conversation, sans nom de
# conversation au pluriel hors de ce nom, et sans nombre devant un nom de conversation ; seuls les idiomes de lieu
# (« dans vos conversations », « from your chats », « de votre historique ») ne comptent pas.
CONFIRM_PLURAL_RE = r"\b(?:conversations|discussions|chats|threads)\b"
# Round 4 (coordinator's ruling 1) : un nombre ou un quantificateur devant un nom de conversation au pluriel, jugé sur
# le texte entier, avant que les idiomes de lieu soient mis de côté : « et 3 de vos conversations », « and 2 of your
# chats », « some of your chats », « d'autres / la plupart / certaines / plusieurs de vos conversations ».
_CONV_PLURAL = r"(?:conversations|discussions|chats|threads)"
_PARTITIVE = (r"(?:\s+(?:de|des|du|of|d['’])\s*)?(?:(?:vos|tes|mes|nos|leurs|ces|your|my|our|their|the|these|those)\s+)?"
              r"(?:\w+\s+)?")
# Round 5 (coordinator's ruling) : « autre(s) » / « other(s) » employé comme nom, ou après un nombre ou un
# déterminant, n'importe où dans le texte : « et les deux autres », « et 2 autres », « ainsi que les autres », « une
# autre conversation », « and 2 others », « and the others », « another chat ».
CONFIRM_OTHERS_RE = (r"\b(?:\d+|les|des|deux|trois|quatre|toutes?|tous|une?|ses|vos|tes|nos|ces|leurs|d['’])\s+"
                     r"(?:\w+\s+)?autres?\b"
                     r"|\bautres?\s*(?:[?.!,;:)]|$)"
                     r"|\bothers?\b|\banother\b")
CONFIRM_COUNTED_RE = (rf"\b\d+{_PARTITIVE}{_CONV_PLURAL}\b"
                      rf"|\b(?:some|most|any|several|many|few|others?|the\s+rest|certaine?s|plusieurs|d['’]\s*autres"
                      rf"|autres|la\s+plupart|le\s+reste|une\s+partie){_PARTITIVE}{_CONV_PLURAL}\b")
CONFIRM_NUMBER_RE = r"\b\d+\s+(?:\w+\s+)?(?:conversation|discussion|chat|thread)s?\b"
CONFIRM_PLACE_RE = (r"\b(?:dans|de|des|parmi|from|in|of|out\s+of)\s+(?:la\s+liste\s+de\s+)?"
                    r"(?:vos|votre|tes|ton|ta|mes|mon|ma|your|my|the)\s+"
                    r"(?:conversations|discussions|chats|threads|historique|chat\s+history|history)\b")
CONFIRM_ONE_RE = r"\b(?:conversation|discussion|chat|thread)\b"
# Les boutons d'une confirmation (round 2, registry-1) : une annulation (« Ne pas supprimer », « Don't
# delete », « Garder »…), une confirmation forte (le motif de KNOWN_MODALS « suppression »), et une faible
# (« Continuer », « Oui », « OK »…), retenue seulement quand elle est le seul bouton qui n'annule pas d'une
# fenêtre d'alerte ouverte par le clic et dont le texte passe.
CONFIRM_CANCEL_RE = r"annuler|cancel|^non\b|^no\b|ne pas|don'?t|\bkeep\b|garder|conserver"
CONFIRM_WEAK_RE = r"^(?:continuer|continue|oui|yes|ok|valider)$"


# Ce qui ressemble à une entrée de renommage ou de suppression (texte replié) : l'entrée en liste blanche
# doit être la seule de sa sorte dans le menu (« Supprimer » à côté de « Supprimer définitivement » : refusé).
MENU_RENAME_WORDS = r"renomm|rename|\btitre\b|\btitle\b"
MENU_DELETE_WORDS = r"supprim|delete|effac|erase|trash|corbeille"
# Round 6 (le menu vu en direct le 20/10, M0, 17:57) : le libellé du déclencheur de la ligne qui nomme les actions de
# la conversation, texte entier replié. Sous un tel déclencheur, l'entrée de renommage n'est plus exigée, mais chaque
# entrée doit être en liste blanche (une seule de suppression, au plus une de renommage).
MENU_TRIGGER_LABELS = ("actions de la conversation", "conversation actions", "options de la conversation",
                       "chat actions", "chat options", "plus d'options", "more options")
NO_RENAME_ENTRY = "pas d'entrée de renommage dans le menu de la conversation (entrées dans conversation.json)"
# Round 2 (robustness-1) : la queue d'une entrée qui n'en change pas le sens : des points de suspension, un
# indice de raccourci (« ⌘⌫ », « Ctrl+Suppr »).
# Round 3 (I-1) : une seule règle, que le script de la page reçoit telle quelle (a.tail) ; une touche de fonction
# (« Renommer F2 ») aussi.
MENU_TAIL = (r"\s*(?:…|\.\.\.)\s*$|\s+(?:[⌘⇧⌥⌃⌫⌦↵⏎]+[\w⌫⌦]*|(?:ctrl|cmd|alt|shift|maj)(?:\s*\+\s*[\w⌫⌦]+)+"
             r"|f\d{1,2})\s*$")
MENU_TAIL_RE = re.compile(MENU_TAIL, re.I)
# Round 2 (m10) : les mots d'une entrée de menu, que la sonde ne prend jamais pour un mot de titre.
ENTRY_WORDS = ("renommer", "rename", "supprimer", "delete", "partager", "share", "archiver", "archive", "epingler",
               "conversation", "discussion", "chat", "modifier", "titre", "title", "deplacer", "corbeille", "trash",
               "move", "plus", "more", "options", "option", "exporter", "export", "copier", "copy", "lien", "link",
               "dossier", "folder", "projet", "project", "nouvelle", "nouveau")


def menu_text(text: str | None) -> str:
    """Le texte replié d'une entrée de menu, sans sa queue (MENU_TAIL_RE) : « Supprimer… », « Supprimer ⌘⌫ »
    sont « supprimer » (round 2, robustness-1)."""
    out = evaluer.fold(text or "").strip()
    while True:
        cut = MENU_TAIL_RE.sub("", out).strip()
        if cut == out:
            return out
        out = cut


def menu_entry(items: list, want: str, label: str | None = None) -> tuple[dict | None, str | None]:
    """L'entrée du menu d'une conversation à cliquer pour `want` (« renommer » ou « supprimer »). Les
    entrées cliquées sont en liste blanche, sur leur texte replié entier (`menu_rename`, `menu_delete` :
    « Supprimer », « Supprimer la conversation », « Delete chat »… ; revue de la v2.3, I7 ; round 2 : sans
    points de suspension ni indice de raccourci, et « Modifier le titre », « Déplacer vers la corbeille »). Un
    menu n'est celui d'une conversation que si chacune des deux sortes (renommer, supprimer) y a une seule
    entrée qui lui ressemble, active et en liste blanche, et qu'aucune entrée ne vise plus d'une conversation
    (« tous », « all », « every », « sélection », « historique »…) : sinon rien n'est cliqué. Rend (l'entrée,
    None) ou (None, la raison) ; la raison va au harnais, les entrées jamais (round 2, R2-4)."""
    texts = [menu_text(i.get("text")) for i in items]
    if any(re.search(MENU_TOO_WIDE, t) for t in texts):
        return None, "menu refusé, une entrée viserait plus d'une conversation (entrées dans conversation.json)"
    folded = " ".join(evaluer.fold(label or "").split()).replace("’", "'")
    if folded in MENU_TRIGGER_LABELS:  # round 6: the trigger names the conversation's actions
        renames = [i for i, t in zip(items, texts) if re.search(SELECTORS["menu_rename"], t)]
        deletes = [i for i, t in zip(items, texts) if re.search(SELECTORS["menu_delete"], t)]
        if (len(renames) + len(deletes) != len(items) or len(deletes) != 1 or len(renames) > 1
                or any(i.get("disabled") for i in items)):
            return None, ("pas le menu d'une conversation (sous « actions de la conversation », chaque entrée en liste "
                          "blanche, une seule de suppression, au plus une de renommage ; entrées dans conversation.json)")
        if want == "renommer" and not renames:
            return None, NO_RENAME_ENTRY
        return (renames or deletes)[0] if want == "renommer" else deletes[0], None
    found = {}
    for how, alike_re, exact_re in (("renommer", MENU_RENAME_WORDS, SELECTORS["menu_rename"]),
                                    ("supprimer", MENU_DELETE_WORDS, SELECTORS["menu_delete"])):
        alike = [(i, t) for i, t in zip(items, texts) if re.search(alike_re, t)]
        if len(alike) != 1 or alike[0][0].get("disabled") or not re.search(exact_re, alike[0][1]):
            return None, ("pas le menu d'une conversation (une entrée renommer et une supprimer, exactes et seules "
                          "de leur sorte ; entrées dans conversation.json)")
        found[how] = alike[0][0]
    return found[want], None


def confirms_one_deletion(text: str | None, title: str | None = None) -> bool:
    """La confirmation parle-t-elle de supprimer une conversation, et une seule (revue I7) ? Son texte
    replié doit dire supprimer (delete, effacer, remove…) et nommer une conversation (conversation,
    discussion, chat, thread, ou son titre `title`, relu à son adresse juste avant : round 2), et rien qui
    viserait explicitement plus d'une conversation (CONFIRM_TOO_WIDE) ; sinon elle est annulée par Échap,
    jamais validée."""
    folded = evaluer.fold(text or "")
    if not re.search(CONFIRM_DELETE_RE, folded):
        return False
    own = evaluer.fold(title or "").strip()
    named = bool(re.search(CONFIRM_ONE_RE, folded)) or (len(own) >= 3 and own in folded)
    if (not named or re.search(CONFIRM_TOO_WIDE, folded) or re.search(CONFIRM_COUNTED_RE, folded)
            or re.search(CONFIRM_OTHERS_RE, folded)):  # round 5: « others », in any elliptical form
        return False  # round 4: counted or quantified on the whole text, before any place idiom is set aside
    rest = folded.replace(own, " ") if len(own) >= 3 else folded  # the title may say « conversations »
    rest = re.sub(CONFIRM_PLACE_RE, " ", rest)  # round 3, I-2: where it disappears from is no target
    return not (re.search(CONFIRM_PLURAL_RE, rest) or re.search(CONFIRM_NUMBER_RE, rest))


def pick_confirm(dlg: dict) -> tuple[int | None, str | None]:
    """Le bouton qui confirme (son rang parmi les boutons visibles de la fenêtre), ou None et la raison : le seul
    bouton fort qui n'annule pas ; à défaut, dans une fenêtre d'alerte, le seul bouton qui n'annule pas quand il
    est faible (« Continuer », « Oui »…). Round 2, registry-1."""
    texts = [evaluer.fold(t or "").strip() for t in dlg.get("buttons") or []]
    cancel = [bool(re.search(CONFIRM_CANCEL_RE, t)) for t in texts]
    strong_re = next(k["confirm"] for k in KNOWN_MODALS if k["name"] == "suppression")
    strong = [i for i, t in enumerate(texts) if not cancel[i] and re.search(strong_re, t)]
    if len(strong) == 1:
        return strong[0], None
    if not strong and dlg.get("role") == "alertdialog":
        others = [i for i, t in enumerate(texts) if not cancel[i]]
        if len(others) == 1 and re.search(CONFIRM_WEAK_RE, texts[others[0]]):
            return others[0], None
    return None, "confirmation sans bouton de suppression unique (boutons dans conversation.json)"


def confirmation_refusal(dlg: dict, title: str | None) -> str | None:
    """Pourquoi une confirmation est annulée (None : elle est validée) : un texte trop long pour être lu en
    entier (round 2, click-targets-6), un texte qui ne parle pas de supprimer cette seule conversation, pas de
    bouton de confirmation unique. Jamais son texte : il reste dans conversation.json (R2-4)."""
    if dlg.get("cut"):
        return "texte de confirmation trop long pour être lu en entier : annulée (texte dans conversation.json)"
    if not confirms_one_deletion(dlg.get("full") or dlg.get("text"), title):
        return "confirmation non reconnue : elle ne parle pas de supprimer cette seule conversation (texte dans conversation.json)"
    return pick_confirm(dlg)[1]


def same_title(read: str | None, want: str) -> bool:
    """Le titre relu sur la barre latérale est-il celui demandé ? Au repli près (casse, accents, blancs),
    ou coupé par la barre (« … » final ; vers 45 caractères le 17/10) sur au moins vingt caractères."""
    if not read:
        return False
    r, w = evaluer.fold(read).strip(), evaluer.fold(want).strip()
    if r == w:
        return True
    cut = r.rstrip("…. ").strip()
    return len(cut) >= 20 and w.startswith(cut)


def chip_label(reading: dict | None) -> str:
    if not reading:
        return ""
    return one_line(reading.get("text") or " ".join(reading.get("labels") or []), 120)


def abort_text(p: dict) -> str:
    """Le profil d'un abandon, en clair pour la ligne de journal (D8)."""
    if not p:
        return ""
    parts = ["question partie (POST vu)" if p.get("posted") else "aucun POST vu",
             "flux ouvert" if p.get("stream_started") else "flux non ouvert"]
    if p.get("last_node"):
        parts.append(f"dernier nœud {p['last_node']} à {p['last_node_s']:g} s")
    if p.get("send_refused"):  # round 2, click-targets-3
        parts.append(p["send_refused"])
    if p.get("stop_skipped"):
        parts.append("arrêt non cliqué : le sélecteur d'arrêt désignait un bouton déjà visible avant l'envoi")
    elif p.get("stop_clicked"):
        parts.append("arrêt cliqué" + ("" if p.get("stop_gone") else ", bouton encore visible"))
    else:
        parts.append("pas de bouton d'arrêt visible")
    return ", ".join(parts)


ORACLE_FILES = ("corpus-v2/oracles-v2.json", "corpus/oracles.json", "corpus-v3/oracles-v3.json")
CASES_REL = "drivers/maskai-recette/cases.json"


def load_cases(path: Path = DRIVER_DIR / "cases.json") -> dict:
    """Les cas de la recette : ceux qui portent `oracle` prennent question, must, must_not et
    pii dans les oracles du corpus (une seule source pour le verdict). Une ENF que le journal
    ne connaît pas est refusée ici, au démarrage, plutôt qu'au premier verdict."""
    oracles = {}
    for rel in ORACLE_FILES:
        p = RECETTE_DIR / rel
        if p.exists():
            for c in json.loads(p.read_text(encoding="utf-8"))["cases"]:
                oracles[c["id"]] = c
    cases = {}
    for c in json.loads(path.read_text(encoding="utf-8"))["cases"]:
        c = dict(c)
        if "oracle" in c:
            if c["oracle"] not in oracles:
                raise ValueError(f"case {c['id']}: no oracle {c['oracle']} in the corpus")
            o = oracles[c["oracle"]]
            for key in ("question", "must", "must_not", "pii"):
                c.setdefault(key, o.get(key, []))
        for key in ("must", "must_not", "pii", "nfr"):
            c.setdefault(key, [])
        if c.get("kind") == "long":
            long_questions(c, c.get("turns") or 2)  # refused at start, not at the first turn
        unknown = [n for n in c["nfr"] if n not in journal.NFR]
        if unknown:
            raise ValueError(f"case {c['id']}: unknown NFR {unknown}; the journal knows {journal.NFR}")
        cases[c["id"]] = c
    return cases


def case_file(rel: str) -> Path | None:
    """Le fichier d'un cas, sous le dossier du driver ou sous le dossier de la recette."""
    for root in (DRIVER_DIR, RECETTE_DIR):
        p = (root / rel).resolve()
        if p.is_file() and root.resolve() in p.parents:
            return p
    return None


def calibration_files() -> list:
    """vkdriver, evaluer.py et journal.py, sous leur chemin dans le dépôt, qu'ils viennent de
    vendor/ (paquet installé) ou du dépôt (développement)."""
    files = [("drivers/vkdriver/" + rel, path) for rel, path in list_files(str(VKDRIVER_DIR))]
    files += [("scripts/recette/" + name, SCRIPTS_DIR / name) for name in ("evaluer.py", "journal.py")]
    return sorted(files)


def repo_key(p: Path) -> str:
    """Le chemin d'un fichier du driver ou du corpus tel que le dépôt le nomme, où que soit installé
    le driver."""
    for base, prefix in ((DRIVER_DIR, "drivers/maskai-recette/"),
                         (RECETTE_DIR, "docs/functional/use-cases/recettage-chat-ia/")):
        try:
            return prefix + p.resolve().relative_to(base.resolve()).as_posix()
        except ValueError:
            continue
    raise ValueError(f"{p} is neither under the driver nor under the corpus")


def verdict_inputs(cases: dict, cases_path: Path) -> list:
    """Ce qui fait un verdict sans être dans le dossier haché du driver (Ruling R18) :
    drivers/vkdriver, evaluer.py, journal.py, cases.json, les oracles et les fichiers du corpus
    que les cas joignent, en chemins relatifs à la racine du dépôt."""
    files = {CASES_REL: cases_path}
    files.update(calibration_files())
    for rel in ORACLE_FILES:
        if (RECETTE_DIR / rel).exists():
            files["docs/functional/use-cases/recettage-chat-ia/" + rel] = RECETTE_DIR / rel
    for c in cases.values():
        if c.get("file"):
            p = case_file(c["file"])
            if p is None:
                raise ValueError(f"case {c['id']}: file {c['file']} not found")
            files[repo_key(p)] = p
    return sorted(files.items())


def entry_url(hosts: list, env) -> str:
    if env.get("MASKAI_ENTRY_URL"):
        return env["MASKAI_ENTRY_URL"]
    if urlsplit(ENTRY_URL).hostname in hosts:
        return ENTRY_URL
    if "127.0.0.1" in hosts:
        return FIXTURE_ENTRY_URL
    raise ValueError(f"no entry URL for hosts {hosts}: set MASKAI_ENTRY_URL")


class Evidence:
    """Les preuves d'une action, écrites sous evidence_dir sous les noms que le manifeste
    déclare. Une preuve qui dépasserait 2 Mio, ou 4 Mio pour l'action, n'est pas écrite : elle
    est nommée dans `dropped`, et l'action ne tombe pas pour autant."""

    MIME = {".png": "image/png", ".html": "text/html", ".json": "application/json",
            ".jsonl": "application/x-ndjson"}

    def __init__(self, directory: str):
        self.dir = directory
        self.items, self.dropped, self.total = [], [], 0

    def add(self, name: str, data: bytes) -> None:
        if len(data) > PER_FILE or self.total + len(data) > PER_ACTION:
            self.dropped.append(name)
            log.warning("evidence %s (%d bytes) left out: above the cap", name, len(data))
            return
        path = os.path.join(self.dir, name)
        with open(path, "wb") as f:
            f.write(data)
        self.total += len(data)
        self.items.append({"name": name, "mime": self.MIME.get(Path(name).suffix, "application/octet-stream"),
                           "path": path})

    def json(self, name: str, obj, cap: int | None = None) -> None:
        """`obj` en JSON. Au-dessus de `cap`, la plus longue liste perd sa moitié la plus
        ancienne, tour après tour. Un tour qui ne raccourcit rien (la plus longue liste n'a
        plus qu'un élément) arrête la boucle : le texte est alors coupé sous `cap`, avec une
        marque, et n'est plus du JSON valide."""
        data = json.dumps(obj, ensure_ascii=False, indent=1).encode("utf-8", "replace")
        if cap and len(data) > cap and isinstance(obj, dict):
            obj = dict(obj)
            while len(data) > cap:
                lists = [k for k, v in obj.items() if isinstance(v, list) and v]
                if not lists:
                    break
                key = max(lists, key=lambda k: len(obj[k]))
                before = len(obj[key])
                obj[key] = obj[key][before // 2:]
                if len(obj[key]) == before:
                    break
                obj["truncated"] = True
                data = json.dumps(obj, ensure_ascii=False, indent=1).encode("utf-8", "replace")
        if cap and len(data) > cap:
            marker = f"\n… maskai-recette : tronqué, {len(data)} octets au total\n".encode("utf-8")
            data = data[: max(cap - len(marker), 0)].decode("utf-8", "ignore").encode("utf-8") + marker
        self.add(name, data)

    def html(self, name: str, html: str) -> None:
        data = html.encode("utf-8", "replace")
        if len(data) > DOM_CAP:
            marker = f"\n<!-- maskai-recette : tronqué, {len(data)} octets au total -->\n".encode("utf-8")
            data = data[: DOM_CAP - len(marker)].decode("utf-8", "ignore").encode("utf-8") + marker
        self.add(name, data)

    def screenshot(self, page, name: str) -> None:
        """La partie visible de la page, barre latérale et bloc du compte masqués (SELECTORS["mask"])."""
        self.add(name, screenshot_png(page))


def screenshot_png(page) -> bytes:
    """La partie visible de la page en PNG, barre latérale et bloc du compte masqués."""
    masks = [page.locator(sel) for sel in SELECTORS["mask"]]
    return page.screenshot(full_page=False, type="png", timeout=10_000, mask=masks, mask_color=MASK_COLOR)


class Browser:
    """Le navigateur du driver : un contexte persistant Playwright sur le profil dédié, la
    garde des navigations, le garde-dialogue, et la capture réseau et console (résumés).
    Aucune requête n'est routée : le routage Playwright couperait le cache HTTP et ferait
    passer chaque requête par Python, deux biais sur les mesures (Ruling R16). Entre deux
    actions, `pump` lit les événements de Playwright une dizaine de fois par seconde : un
    client qui cesse de les lire bloque le démarrage des fenêtres et workers nouveaux de la
    page, la fenêtre de connexion comprise (Ruling R19)."""

    def __init__(self, profile_dir: str, channel: str | None, headless: bool, hosts: list):
        self.profile_dir, self.channel, self.headless, self.hosts = profile_dir, channel, headless, hosts
        self.pw = self.context = self._page = None
        self._dead = False
        self._broken = set()   # pages that failed a pump: never serviced through again
        self.dialogs = []      # dialogues JavaScript en attente : jamais acceptés ni refusés ici
        self.dialog_log = []   # tous les dialogues vus, fermés ou non
        self.network = []      # résumés de requêtes, toutes pages
        self.third_party = []  # requêtes et WebSockets vers des hôtes hors liste : relevés, pas bloqués
        self.offlist = []      # navigations du cadre principal vers un hôte hors liste
        self.failed_navs = []  # navigations du cadre principal en échec (hôte injoignable)
        self._error_seen = 0   # seq of the last error-page commit already accounted for
        self.console = []
        self.posts = []        # corps envoyés au flux de réponse (`sent`)
        self.own_modal = None  # la modale `own` que le driver a ouverte et pas encore refermée
        self.seq = 0
        self._open = {}

    # -- vie du navigateur -------------------------------------------------------------
    def started(self) -> bool:
        return self.context is not None and not self._dead

    def start(self) -> None:
        if self.started():
            return
        self.close()
        if sync_playwright is None:
            raise DriverRefusal("internal", "playwright is not installed in this interpreter: run setup.ps1")
        self.pw = sync_playwright().start()
        try:
            self.context = self.pw.chromium.launch_persistent_context(
                user_data_dir=self.profile_dir, channel=self.channel or None, headless=self.headless,
                accept_downloads=False)
        except Exception:
            self.pw.stop()
            self.pw = None
            raise
        ctx = self.context
        ctx.set_default_timeout(10_000)
        ctx.add_init_script(INTERCEPTOR_JS)
        ctx.on("dialog", self._on_dialog)
        ctx.on("dialogclosed", self._on_dialog_closed)
        ctx.on("request", self._on_request)
        ctx.on("response", self._on_response)
        ctx.on("requestfailed", self._on_failed)
        ctx.on("console", self._on_console)
        ctx.on("close", self._on_close)
        ctx.on("page", self._watch_page)
        for p in ctx.pages:
            self._watch_page(p)
        self._page = ctx.pages[0] if ctx.pages else ctx.new_page()
        log.info("browser up: channel=%s headless=%s hosts=%s", self.channel, self.headless, self.hosts)

    def page(self):
        self.start()
        return self._live_page()

    def close(self) -> None:
        for step in (lambda: self.context and self.context.close(), lambda: self.pw and self.pw.stop()):
            try:
                step()
            except Exception:  # noqa: BLE001 — closing what is already gone
                pass
        self.pw = self.context = self._page = None
        self._dead = False
        self._broken = set()
        self.dialogs.clear()
        self.own_modal = None

    def drain(self, ms: int = 20) -> None:
        """Laisser Playwright livrer les événements en attente (dialogues, navigations,
        requêtes)."""
        self.page().wait_for_timeout(ms)

    def settle(self, max_ms: int = 1000) -> None:
        """Livrer tout ce qui attend, jusqu'à un tour sans événement nouveau (au plus
        max_ms) : ce qui s'est passé avant une action ne doit pas lui être compté."""
        deadline = time.monotonic() + max_ms / 1000
        while True:
            before = self.seq
            self.drain()
            if self.seq == before or time.monotonic() >= deadline:
                return

    def _live_page(self):
        """The page to service Playwright through: the driver's own if it is still open, else
        another open page of the context (the founder may close a tab), else a new one.
        Never a relaunch here: that would close every window, the founder's included. But a
        context that cannot open a page any more has lost its browser — Playwright's node
        process died, which no `close` event reports — so the browser is marked dead, and the
        next action relaunches it rather than failing `internal` until a remount."""
        if self._page is None or self._page.is_closed() or self._page in self._broken:
            pages = [p for p in self.context.pages if not p.is_closed() and p not in self._broken]
            if pages:
                self._page = pages[0]
            else:
                try:
                    self._page = self.context.new_page()
                except PWError:
                    self._dead = True
                    raise
        return self._page

    def pump(self) -> None:
        """Entre deux actions (le crochet `idle` du protocole) : servir Playwright. Une page
        fermée ou en panne ne l'arrête pas : la suivante prend le relais. Seule la fermeture
        du contexte (Chrome quitté) arrête le service, et l'action suivante relance."""
        if not self.started():
            return
        try:
            page = self._live_page()
            page.wait_for_timeout(20)
        except PWError as e:
            if self._dead:  # the context closed (`_on_close`), or lost its browser (`_live_page`)
                return
            if self._page is not None and self._page not in self._broken:
                log.info("page unusable between actions, serving through another: %s",
                         one_line(str(e)))
                self._broken.add(self._page)
            self._page = None

    # -- appels dans la page, bornés ---------------------------------------------------
    def call(self, fn_src: str, arg=None, timeout_ms: int = 5000, page=None):
        """Évaluer une fonction dans la page et rendre sa valeur JSON, en au plus timeout_ms.
        Un dialogue en attente gèle la page : `page.evaluate` ne reviendrait jamais, un
        `wait_for_function` borné lève TimeoutError."""
        page = page or self.page()
        handle = page.wait_for_function(
            "(a) => { const v = (" + fn_src + ")(a); return JSON.stringify(v === undefined ? null : v); }",
            arg=arg, timeout=timeout_ms, polling=100)
        try:
            return clean(json.loads(handle.json_value()))
        finally:
            handle.dispose()

    def element(self, fn_src: str, arg=None, timeout_ms: int = 5000, page=None):
        """L'élément que rend une fonction de la page, qui rend [élément ou null] : un ElementHandle
        (à libérer par l'appelant) ou None ; borné comme `call`."""
        page = page or self.page()
        handle = page.wait_for_function(fn_src, arg=arg, timeout=timeout_ms, polling=100)
        try:
            item = handle.get_property("0")
            el = item.as_element()
            if el is None:
                item.dispose()
            return el
        finally:
            handle.dispose()

    def responsive(self, page=None) -> bool:
        try:
            self.call("() => 1", timeout_ms=1500, page=page)
            return True
        except PWTimeout:
            return False

    def page_of(self, request) -> int:
        try:
            return self.context.pages.index(request.frame.page)
        except Exception:  # noqa: BLE001 — service worker, detached frame
            return -1

    # -- liste d'hôtes : les navigations sont gardées, le reste est relevé ---------------
    def allowed(self, url: str) -> bool:
        p = urlsplit(url)
        return p.scheme not in ("http", "https", "ws", "wss") or (p.hostname or "") in self.hosts

    def _watch_page(self, page) -> None:
        page.on("framenavigated", lambda frame: self._on_navigated(page, frame))
        page.on("websocket", lambda ws: self._on_websocket(page, ws))
        page.on("close", self._on_page_closed)

    def _on_page_closed(self, page) -> None:
        """A closed tab takes its pending dialogs with it (they stay in `dialog_log`)."""
        self.dialogs = [d for d in self.dialogs if getattr(d["dialog"], "page", None) is not page]
        self._broken.discard(page)

    def _on_navigated(self, page, frame) -> None:
        if frame.parent_frame is not None:
            return
        if frame.url.startswith("chrome-error://"):
            # An error page committed: the navigation failed. Off the list if the failed
            # request was (an off-list redirect to an unreachable host), or if unknown.
            nav = self.offlist_failure(self._error_seen)
            self._error_seen = self.seq
            if nav is None:
                return
            self._push(self.offlist, {**nav, "page": self._index(page)})
        elif self.allowed(frame.url):
            return
        else:
            self._push(self.offlist, {"url": short_url(frame.url), "host": urlsplit(frame.url).hostname or "",
                                      "page": self._index(page)})
        log.warning("main frame left the host list: %s", self.offlist[-1]["url"])

    def offlist_failure(self, since: int) -> dict | None:
        """La navigation en échec (après `since`) qui a mené à une page d'erreur : hors liste
        si sa requête l'était ; « unreachable » si elle n'est pas connue ; None si son hôte
        est de la liste (l'application injoignable n'est pas une sortie de périmètre)."""
        failed = self.since(self.failed_navs, since)
        if not failed:
            return {"url": "chrome-error://chromewebdata/", "host": "unreachable"}
        url = failed[-1]["url"]
        if self.allowed(url):
            return None
        return {"url": short_url(url), "host": urlsplit(url).hostname or "unreachable"}

    def _on_websocket(self, page, ws) -> None:
        if not self.allowed(ws.url):
            self._push(self.third_party, {"host": urlsplit(ws.url).hostname or "", "url": short_url(ws.url),
                                          "type": "websocket", "page": self._index(page)})

    def _index(self, page) -> int:
        try:
            return self.context.pages.index(page)
        except Exception:  # noqa: BLE001 — a page already closed
            return -1

    def third_party_hosts(self, mark: int) -> list:
        """Le résumé des hôtes hors liste vus depuis `mark` : hôte, nombre, types."""
        hosts = {}
        for e in self.since(self.third_party, mark):
            h = hosts.setdefault(e["host"], {"host": e["host"], "requests": 0, "types": []})
            h["requests"] += 1
            if e["type"] not in h["types"]:
                h["types"].append(e["type"])
        return sorted(hosts.values(), key=lambda h: (-h["requests"], h["host"]))

    # -- garde-dialogue -----------------------------------------------------------------
    def _on_dialog(self, dialog) -> None:
        """Relever le dialogue et le laisser ouvert : seul un humain y répond."""
        self.seq += 1
        record = {"seq": self.seq, "type": dialog.type, "message": (dialog.message or "")[:200], "t": now_iso(),
                  "mono": time.monotonic()}
        self.dialogs.append({**record, "dialog": dialog})
        self.dialog_log.append(record)  # kept after the dialog is closed, for the verdict
        log.warning("dialog left pending: %s %r", dialog.type, (dialog.message or "")[:200])

    def _on_dialog_closed(self, dialog) -> None:
        self.dialogs = [d for d in self.dialogs if d["dialog"] is not dialog]
        log.info("dialog closed by someone else: %s", dialog.type)

    def pending(self) -> dict | None:
        return self.dialogs[-1] if self.dialogs else None

    def settled(self, page=None, ms: int = 100) -> bool:
        """Après un clic ou une touche qui peut ouvrir un dialogue : `ms` millisecondes d'un minuteur de
        Playwright (il court et livre l'événement même quand un dialogue s'ouvre), puis True si aucun
        dialogue n'attend. Avec Playwright 1.64 (relevé sur le banc, revue de la v2.3, m4, et son deuxième
        tour, robustness-4) : un appel à la page en cours quand un dialogue s'ouvre ne revient qu'à sa
        fermeture, son délai compris ; un dialogue déjà ouvert fait lever le délai de Browser.call
        (wait_for_function), de screenshot, click, get_attribute, evaluate, wait_for, set_input_files et goto,
        mais locator.count() et locator.is_visible() ne reviennent jamais : le driver ne s'en sert plus. Après
        un tel clic, on regarde donc avant de relire la page ; un dialogue qui s'ouvre plus tard, pendant une
        lecture, la gèle jusqu'à ce qu'un humain le ferme (runbook §8.4), et le journal des dialogues le garde
        (robustness-2)."""
        try:
            (page or self.page()).wait_for_timeout(ms)
        except PWError:
            pass
        return self.pending() is None

    # -- capture ------------------------------------------------------------------------
    def _push(self, lst: list, entry: dict) -> dict:
        self.seq += 1
        entry = {"seq": self.seq, "t": now_iso(), "mono": time.monotonic(), **entry}
        lst.append(entry)
        if len(lst) > 20_000:
            del lst[:5_000]
        return entry

    def _on_request(self, request) -> None:
        e = self._push(self.network, {"method": request.method, "url": short_url(request.url),
                                      "type": request.resource_type, "page": self.page_of(request)})
        self._open[request] = (e, time.monotonic())
        if not self.allowed(request.url):
            self._push(self.third_party, {"host": urlsplit(request.url).hostname or "", "url": e["url"],
                                          "type": request.resource_type, "page": e["page"]})
        if request.method == "POST" and re.search(STREAM_PATTERN, request.url):
            # `net`: the request's own summary, which the response completes with its status (E10)
            self.posts.append({"seq": e["seq"], "page": e["page"], "body": (request.post_data or "")[:20_000], "net": e})

    def _on_response(self, response) -> None:
        e, t0 = self._open.pop(response.request, ({}, time.monotonic()))
        e["status"] = response.status
        e["ms"] = int((time.monotonic() - t0) * 1000)

    def _on_failed(self, request) -> None:
        e, _ = self._open.pop(request, ({}, 0))
        e["failure"] = request.failure
        try:
            if request.is_navigation_request() and request.frame.parent_frame is None:
                self._push(self.failed_navs, {"url": request.url, "failure": request.failure})
        except Exception:  # noqa: BLE001 — a detached frame
            pass

    def _on_console(self, msg) -> None:
        self._push(self.console, {"type": msg.type, "text": (msg.text or "")[:300]})

    def _on_close(self, _ctx) -> None:
        self._dead = True

    def mark(self) -> int:
        return self.seq

    def since(self, lst: list, mark: int, page: int | None = None) -> list:
        return [e for e in lst if e["seq"] > mark and (page is None or e.get("page") in (page, -1))]

    def capture(self, mark: int) -> dict:
        return {"since": mark, "requests": self.since(self.network, mark),
                "third_party_hosts": self.third_party_hosts(mark),
                "offlist_navigations": self.since(self.offlist, mark),
                "console": self.since(self.console, mark)}


class EntryGone(Exception):
    """Round 3, I-1 (R2-1) : l'entrée acceptée n'est plus là au moment du clic : rien n'est cliqué."""


class AskTimeout(Exception):
    def __init__(self, seconds: float, why: str):
        super().__init__(why)
        self.seconds, self.why = seconds, why
        self.profile = {}  # ce qui était parti, et le clic d'arrêt (Recette._abort)
        self.capped = False  # l'échéance de l'action a coupé l'attente avant 3 × le budget
        self.notices = {}  # round 2, leakage-1 : les avis d'erreur de la page pendant l'attente


class Recette:
    """L'état du driver entre deux actions : le navigateur, les cas, les observations par cas
    (persistées après chaque action) et le journal de la recette."""

    def __init__(self, env=None, cases_path: Path | None = None):
        env = os.environ if env is None else env
        for key in ("VK_DRIVER_WORK_DIR", "VK_DRIVER_PROFILE_DIR"):
            if not env.get(key):
                raise ValueError(f"{key} is not set: the kernel's host sets it")
        hosts = [h.strip().lower() for h in env.get("VK_DRIVER_HOSTS", "").split(",") if h.strip()]
        if not hosts:
            raise ValueError("VK_DRIVER_HOSTS is empty: the driver would refuse every host")
        self.entry = entry_url(hosts, env)
        self.work = Path(env["VK_DRIVER_WORK_DIR"])
        self.work.mkdir(parents=True, exist_ok=True)
        self.browser = Browser(env["VK_DRIVER_PROFILE_DIR"], env.get("VK_DRIVER_CHANNEL") or None,
                               env.get("VK_DRIVER_HEADLESS") == "1", hosts)
        self.cases_path = Path(cases_path) if cases_path else DRIVER_DIR / "cases.json"
        self.cases = load_cases(self.cases_path)
        # Ruling R18: what the verdict depends on outside the hashed driver directory.
        self.inputs_sha256 = hash_files(verdict_inputs(self.cases, self.cases_path))
        self.version = f"{VERSION}+{self.inputs_sha256.removeprefix('sha256:')[:12]}"
        # What the page was read with (SELECTORS, KNOWN_MODALS): on every journal line (D12).
        self.ui_profile_sha256 = ui_profile_sha256()
        self.journal_path = self.work / "journal.jsonl"
        self.cursor_path = self.work / "journal.cursor"
        self.obs_path = self.work / "observations.json"
        self.observations = (json.loads(self.obs_path.read_text(encoding="utf-8"))
                             if self.obs_path.exists() else {})
        # v2.3, E1 : the conversations the driver created, with the case that created each; kept apart
        # from the observations (a resumption sets observations.json aside, never this registry).
        self.registry_path = self.work / "conversations.json"
        self.registry = (json.loads(self.registry_path.read_text(encoding="utf-8")).get("conversations", [])
                         if self.registry_path.exists() else [])
        self.conv = self._fresh_conversation()
        self.flux = {"ev": 0, "mark": 0}
        self._case_in_hand = None
        self._mark = None  # where the action in hand began, in the browser's event sequence
        self._last_asked = None  # {"case", "mark"} of the last poser_question
        self._in_flight = False  # an abandon whose stop click did not end the generation
        # answer.png taken by _collect before the « Détails » click (which scrolls the card header
        # into view); the evidence uses it, once.
        self._answer_shot = None
        # The case a maintenance page is charged to: the one the action names, else the last case
        # verdict_oracle said was still to do (nouvelle_conversation names none).
        self._last_verdict_probe = None
        self._action_case = None
        # The composer configuration last set by choisir_modele / recherche_web: nouvelle_conversation
        # sets it again when the interface lost it (PROCEDURE-2 §8.6).
        self.pinned = {}
        # Round 2 (leakage-2, R2-4): the titles the sidebar listed at its last reading, so that no page text
        # that holds one reaches the harness, even while a dialog freezes the page.
        self._titles = []
        # Round 2 (robustness-6): the dialogs that are the application's own native delete confirmation, left
        # to the human; never an unexpected dialog of a case.
        self._app_dialogs = set()

    def actions(self) -> dict:
        browser = ["mesurer_chargement", "nouvelle_conversation", "joindre_document", "poser_question",
                   "envoyer_message", "lire_reponse", "capturer_flux", "lister_conversations",
                   "charge_concurrente", "capture_ecran", "choisir_modele", "recherche_web",
                   "conversation_longue", "renommer_conversation", "supprimer_conversation", "ouvrir_avis",
                   "sonder_interface"]
        local = ["verdict_oracle", "exporter_journal"]
        return {**{n: self._wrap(getattr(self, n), browser=True) for n in browser},
                **{n: self._wrap(getattr(self, n), browser=False) for n in local}}

    def open(self) -> None:
        """Au montage : ouvrir Chrome sur la page d'entrée, pour que l'opérateur s'y connecte.
        Pas de garde des navigations ici : la connexion peut passer par un autre hôte, et ce
        que l'opérateur fait entre deux actions n'est pas un acte du driver. Une navigation
        hors liste après la fin d'une action n'est qu'une note sur le dernier cas posé, jamais
        un KO (Ruling R20) ; seule celle qui survient pendant une action est refusée."""
        page = self.browser.page()
        self._goto(page, self.entry, check=False)

    # -- l'enveloppe commune ---------------------------------------------------------------
    def _wrap(self, fn, browser: bool):
        def run(args: dict, evidence_dir: str):
            ev = Evidence(evidence_dir)
            self._case_in_hand = None
            self._mark = None
            self._answer_shot = None
            self._action_case = args.get("case") or self._last_verdict_probe
            self._close_window(self._action_case)
            try:
                if browser:
                    self._guard(fn.__name__)  # delivers what happened between actions, before the mark
                    self._mark = self.browser.mark()
                result = fn(args, ev)
                if browser:
                    self._after(ev)
                    if fn.__name__ in NEEDS_APP and self._action_case:  # the app answered: no maintenance
                        (self.observations.get(self._action_case) or {}).pop("maintenance", None)
                if ev.dropped:
                    result["evidence_dropped"] = ev.dropped
                return result, ev.items
            except DriverRefusal as r:
                if browser and r.reason not in ("dialog", "host") and self.browser.started():
                    # round 3, I-3 (robustness-2): a dialog seen during the action, even closed by the human, is
                    # the action's, whatever refusal the action then gave
                    seen = self.browser.pending() or self._new_dialog()
                    if seen and seen.get("seq") not in self._app_dialogs:
                        refusal = self._dialog_refusal(ev, seen)
                        refusal.extra = {**(r.extra or {}), **(refusal.extra or {}), "refused_first": r.reason}
                        refusal.evidence = ev.items
                        raise refusal from r
                r.evidence = r.evidence or ev.items
                raise
            except Exception as e:
                if browser and self.browser.started():
                    if self.browser.pending():
                        raise self._dialog_refusal(ev) from e
                    seen = self._new_dialog()
                    if seen:  # round 2, robustness-2: closed by the human before the driver looked
                        raise self._dialog_refusal(ev, seen) from e
                    if self._offlist():  # the page left the list mid-action and broke a call
                        raise self._host_refusal(self._offlist()[-1], ev) from e
                raise
            finally:
                last = self._last_asked
                if last and self._case_in_hand == last["case"] and "end" not in last:
                    # where poser_question's action ended: what comes later is "after the answer"
                    last.update(end=self.browser.mark(), end_mono=time.monotonic())
                self._save_observations()
        run.__name__ = fn.__name__
        return run

    def _guard(self, action: str | None = None) -> None:
        """Avant toute action dans le navigateur : pas de dialogue en attente, pas de modale
        inconnue ; une modale connue est fermée par son bouton ; et, pour une action qui a besoin
        de l'application, pas de page de maintenance (rechargée une fois avant de conclure)."""
        b = self.browser
        page = b.page()
        b.settle()  # all of the waiting period's events land before the action's mark
        if b.pending():
            if b.responsive():
                log.info("pending dialog gone (closed in Chrome): guard cleared")
                b.dialogs.clear()
            else:
                raise self._dialog_refusal()
        if not self._titles:  # round 2, leakage-2: after a relaunch, the titles no harness text may hold
            self._conversation_state(page)
        # A modal the driver opens itself (`own`) is known only while the driver holds it open.
        known = [k for k in KNOWN_MODALS if not k.get("own") or k["name"] == b.own_modal]
        try:
            modals = b.call(MODALS_JS, {"known": known, "modal": SELECTORS["modal"]}, timeout_ms=3000)
        except PWTimeout:
            if b.pending():
                raise self._dialog_refusal()
            raise
        for name in modals["known"]:
            k = next(k for k in known if k["name"] == name)
            self._dismiss(page, k)
            log.info("known modal %s dismissed (%s)", name, k["dismiss"])
            if k.get("own"):
                b.own_modal = None
            else:
                self._note_closed(name)
        if b.own_modal and b.own_modal not in modals["known"]:
            b.own_modal = None  # closed since, by the page or by hand
        if modals["unknown"]:  # round 2, leakage-2: sanitised, never a listed title (R2-4)
            self._conversation_state(page)  # the titles listed now, which the window may quote
            raise DriverRefusal("dialog", f"modale inconnue : {self._harness_text(modals['unknown'][0], self._action_case)}")
        if action in NEEDS_APP:
            self._check_maintenance(page, reload=True)

    def _check_maintenance(self, page, reload: bool = False) -> None:
        """La page de maintenance de MaskAI (son texte, aucune zone de saisie visible) : refus
        `precondition` « service en maintenance », et une observation pour le cas de l'action, que
        verdict_oracle journalise KO disponibilité. `reload` : une page déjà ancienne est d'abord
        rechargée (le service a pu revenir depuis)."""
        b = self.browser

        def auth_error() -> dict | None:
            """18/10 : l'application a renvoyé la session sur /api/auth/error (coupure réseau), ou la
            page d'entrée a redirigé, après son chargement, vers l'hôte d'authentification hors liste
            (profil non connecté : authentification.maskai.fr). Dans les deux cas, pas de zone de
            saisie à attendre."""
            url = page.url or ""
            if re.search(SELECTORS["auth_error"], url):
                return {"t": now_iso(), "url": short_url(url), "why": "session perdue (page d'erreur d'authentification) ; "
                                                                    "reconnecter MaskAI dans Chrome"}
            if not b.allowed(url):
                host = urlsplit(url).hostname or ""
                return {"t": now_iso(), "url": short_url(url), "host": host,
                        "why": f"non connecté : la page d'entrée renvoie vers {host} ; se connecter à MaskAI dans la "
                               f"fenêtre Chrome du pilote"}
            return None

        seen_auth = auth_error()
        if seen_auth and reload:  # a blip on 18/10 came back on its own: the entry page once more
            self._goto(page, self.entry, check=False)
            try:
                page.wait_for_load_state("networkidle", timeout=5000)  # a redirect after load lands first
            except PWTimeout:
                pass
            seen_auth = auth_error()
        if seen_auth:
            if self._action_case:
                self.observations.setdefault(self._action_case, {})["auth_error"] = seen_auth
            log.warning("not on the app: %s", seen_auth["why"])
            raise DriverRefusal("precondition", seen_auth["why"], extra={"auth_error": seen_auth})

        def state() -> dict | None:
            try:
                return b.call(MAINTENANCE_JS, {"textarea": SELECTORS["textarea"], "mic": SELECTORS["mic_button"]},
                              page=page, timeout_ms=3000)
            except PWTimeout:
                if b.pending():
                    raise
                return None

        def maintenance() -> dict | None:
            got = state()
            if got and got.get("mic") not in (None, "idle"):  # 18/10: a dictation the user started
                raise DriverRefusal("precondition", f"dictée en cours (micro « {got['mic']} ») ; "
                                                    "arrêter la dictée dans Chrome", extra={"mic": got["mic"]})
            if not got or got["textarea"] or not evaluer.find(SELECTORS["maintenance"], got["text"]):
                return None
            return got

        got = maintenance()
        if got and reload:
            self._goto(page, self.entry)
            got = maintenance()
        if not got:
            return
        seen = {"t": now_iso(), "epoch": time.time(), "url": short_url(page.url), "text": one_line(got["text"], 200)}
        if self._action_case:
            self.observations.setdefault(self._action_case, {})["maintenance"] = seen
        log.warning("service in maintenance: %s", seen["text"])
        raise DriverRefusal("precondition", "service en maintenance", extra={"maintenance": seen})

    def _wait_for_composer(self, page, timeout_s: float = 15.0) -> None:
        """La zone de saisie visible, au plus timeout_s ; une page de maintenance arrête l'attente
        tout de suite (« service en maintenance »), au lieu de 15 s puis `internal`."""
        deadline = time.monotonic() + timeout_s
        loc = page.locator(SELECTORS["textarea"]).first
        while True:
            try:
                loc.wait_for(state="visible", timeout=1000)
                return
            except PWTimeout:
                self._check_maintenance(page)
                if time.monotonic() >= deadline:
                    raise

    def _note_closed(self, name: str) -> None:
        """Une modale connue que la garde a fermée (v2.3, E5) : comptée pour le cas en main (celui que
        l'action nomme, à défaut le dernier que verdict_oracle a dit à faire), dont chaque ligne de journal
        porte la liste (`modals_closed`), pour que le juge compte les apparitions spontanées. Une modale
        `own` (le driver l'avait ouverte lui-même) n'en est pas une."""
        if self._action_case:
            self.observations.setdefault("@modals", {}).setdefault(self._action_case, []).append(name)

    @staticmethod
    def _dismiss(page, k: dict) -> None:
        """Fermer une modale connue par son moyen documenté : une touche (« key:Escape ») ou un
        bouton ; jamais « OK » ni « Envoyer »."""
        if k["dismiss"].startswith("key:"):
            page.keyboard.press(k["dismiss"][4:])
            page.wait_for_timeout(200)
        else:
            page.locator(k["dismiss"]).first.click(timeout=5000)

    def _after(self, ev: Evidence | None = None) -> None:
        b = self.browser
        b.drain(50)
        if b.pending():  # round 3, M-7: with the evidence, so that dialog.json holds the raw message
            raise self._dialog_refusal(ev)
        seen = self._new_dialog()
        if seen:  # round 2, robustness-2: a dialog the human closed before the driver looked is still the action's
            raise self._dialog_refusal(ev, seen)
        self._check_host()

    def _new_dialog(self) -> dict | None:
        """Round 2, robustness-2 : le dernier dialogue relevé depuis le début de l'action, fermé ou non, qui n'est
        pas la confirmation native de l'application. Un appel à la page que le dialogue a gelé ne revient qu'à sa
        fermeture : `dialogclosed` arrive d'abord, `pending()` est alors vide, le journal des dialogues le garde."""
        if self._mark is None:
            return None
        seen = [d for d in self.browser.dialog_log if d["seq"] > self._mark and d["seq"] not in self._app_dialogs]
        return seen[-1] if seen else None

    def _dialog_seen(self) -> bool:
        """Round 4, R-2 : un dialogue en attente, ou relevé depuis le début de l'action (fermé peut-être par
        l'humain), qui n'est pas la confirmation native de l'application."""
        d = self.browser.pending() or self._new_dialog()
        return bool(d) and d.get("seq") not in self._app_dialogs

    def _close_window(self, cid: str | None) -> None:
        """Round 2, verdicts-5 : dès qu'un autre cas est en jeu (une action qui le nomme, verdict_oracle d'un autre
        cas, un cas pris en main), ce qui arrive ensuite n'est plus « après la réponse » de la dernière
        question : sa fenêtre se ferme là (`until`)."""
        last = self._last_asked
        if last and cid and cid != last["case"] and "until" not in last:
            last["until"] = self.browser.mark()

    def _offlist(self) -> list:
        """Les navigations hors liste du cadre principal depuis le début de l'action."""
        return [] if self._mark is None else self.browser.since(self.browser.offlist, self._mark)

    def _check_host(self) -> None:
        navs = self._offlist()
        if navs:
            raise self._host_refusal(navs[-1])

    def _host_refusal(self, nav: dict, ev: Evidence | None = None, page=None) -> DriverRefusal:
        """Défaire une navigation hors liste (retour à la page d'entrée) et la refuser ; le cas
        en cours la porte dans son observation, comme un dialogue. L'onglet renvoyé à l'entrée
        est `page` quand l'appelant le nomme (un onglet fermé depuis la navigation décale les
        index), sinon celui de l'index relevé avec la navigation."""
        case = self._case_in_hand or self._action_case  # round 2, verdicts-5: the case the action plays
        if case:
            self.observations.setdefault(case, {})["offlist"] = {"host": nav["host"], "url": nav["url"], "t": now_iso()}
        if page is None:
            pages = self.browser.context.pages
            page = pages[nav["page"]] if 0 <= nav.get("page", -1) < len(pages) else self.browser.page()
        self._back_to_entry(page)
        return DriverRefusal("host", f"navigation hors liste vers {nav['host']} : retour à la page d'entrée",
                             extra={"host": nav["host"], "url": nav["url"]},
                             evidence=ev.items if ev else None)

    def _back_to_entry(self, page) -> None:
        try:
            page.goto(self.entry, wait_until="load", timeout=30_000)
        except PWError as e:
            log.warning("could not return to the entry page: %s", e)
        self._mark = self.browser.mark()  # the way back is not a second offence
        if page is self.browser.page():
            self.conv = self._fresh_conversation()
            self.flux["ev"] = 0

    def _dialog_refusal(self, ev: Evidence | None = None, d: dict | None = None) -> DriverRefusal:
        """Le refus `dialog` d'un dialogue en attente (ou de `d`, relevé puis fermé : robustness-2), noté sous le cas
        en main, à défaut sous celui que l'action joue (round 2, verdicts-5 : M8-2, M8-7). Son message va au
        harnais expurgé, jamais avec un titre listé, et signale une erreur interne (leakage-2) ; le message brut
        ne va qu'à la preuve dialog.json. La confirmation native de l'application (robustness-6) n'est le
        dialogue d'aucun cas : laissée à l'humain."""
        d = d or self.browser.pending()
        items = ev.items if ev else None
        if d and d.get("seq") in self._app_dialogs:
            return DriverRefusal("dialog", "confirmation native de la suppression en attente : à annuler dans Chrome ; "
                                           "le pilote ne répond jamais à un dialogue", evidence=items)
        case = self._case_in_hand or self._action_case
        shown = self._harness_text(d["message"], case) if d else ""
        detail = f"dialogue {d['type']} : {shown}" if d else "dialogue en attente"
        if case:
            self.observations.setdefault(case, {})["dialog"] = {
                "type": d["type"] if d else "", "message": shown, "t": now_iso()}
        if ev is not None and d:
            ev.json("dialog.json", {"type": d["type"], "message": d["message"], "t": d.get("t")})
        return DriverRefusal("dialog", detail, evidence=items)

    def _harness_text(self, text: str | None, cid: str | None = None) -> str:
        """Un texte de la page pour le harnais (harness_text) : expurgé, sans titre listé ; une erreur interne
        qu'il montre est notée sur le cas `cid` (round 2, leakage-2)."""
        shown, internal = harness_text(text, self._titles, keep=self._keep_names())
        if internal and cid:
            self.observations.setdefault(cid, {})["internal_error_shown"] = True
        return shown

    def _untested(self, cid: str, reason: str) -> None:
        """Round 2, R2-1 : le cas refusé par la propre garde du pilote, avant tout clic irréversible : verdict_oracle
        le journalisera NON TESTÉ, sans ENF, « refus du pilote : … », jamais KO contre MaskAI."""
        self.observations.setdefault(cid, {})["untested"] = {"reason": reason, "t": now_iso()}

    def _keep_names(self) -> tuple:
        """Ce qu'un avis de la page peut nommer sans montrer de détail interne (round 2, leakage-4) : les fichiers
        joints de la conversation, les hôtes de la liste du driver."""
        return tuple(a.get("file") for a in self.conv["attached"].values() if a.get("file")) + tuple(self.browser.hosts)

    # -- le registre des conversations (v2.3, E1) ------------------------------------------------
    def _save_registry(self) -> None:
        tmp = self.registry_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"version": 1, "conversations": self.registry}, ensure_ascii=False, indent=1),
                       encoding="utf-8", errors="replace")
        os.replace(tmp, self.registry_path)

    def _registered(self, cid: str) -> dict | None:
        """La conversation que le cas `cid` a créée, au registre (la dernière, si le cas a été rejoué)."""
        found = [e for e in self.registry if e["case"] == cid]
        return found[-1] if found else None

    @staticmethod
    def _conv_arg(href: str | None = None, redact: bool = False) -> dict:
        return {"href": href, "sidebar": SELECTORS["sidebar"], "convPath": SELECTORS["conversation_path"],
                "triggers": SELECTORS["conversation_menu_trigger"], "headings": SELECTORS["group_heading"],
                "row": SELECTORS["conversation_row"], "redact": redact}

    def _conversation_state(self, page) -> dict | None:
        """Le chemin de la page et les conversations de la barre latérale (chemin, titre) ; None si la page
        ne répond pas à temps."""
        try:
            st = self.browser.call(CONV_STATE_JS, self._conv_arg(), page=page, timeout_ms=3000)
        except PWTimeout:
            if self.browser.pending():
                raise
            return None
        if st and st.get("items"):  # round 2, leakage-2: the titles no harness text may hold
            self._titles = [t for _, t in st["items"] if t][:200]
        return st

    def _note_conversation(self, cid: str, before: dict | None, page, via: str) -> dict | None:
        """Après une question, ou un téléversement (revue de la v2.3, m13 : l'application peut créer la
        conversation dès la pièce jointe) : la conversation où la page se trouve, et ce que la ligne du cas
        en porte (`conversation` : adresse, cas qui l'a créée, état), ou None. Elle entre au registre, avec
        le cas `cid`, seulement quand l'action l'a créée : la page était avant sur une page qui n'est pas une
        conversation (une page neuve), elle est maintenant sur /conversation/<id>, et cette adresse n'était
        pas dans la barre latérale avant. Rien d'autre (revue I6) : une ligne apparue dans la barre latérale
        peut être celle d'une autre conversation (la liste qui finissait de charger, une conversation ouverte
        ailleurs par le fondateur), elle n'entre jamais au registre. Sans relevé d'avant l'action non plus :
        le driver ne s'attribue jamais une conversation qu'il n'a pas vue naître."""
        after = self._conversation_state(page)
        if not after:
            return None
        here = after.get("path") or ""
        if before is None or re.match(SELECTORS["conversation_path"], before.get("path") or ""):
            href = (before or {}).get("path") or here  # asked in a conversation already open
            entry = next((e for e in self.registry if e["href"] == href), None)
            if not re.match(SELECTORS["conversation_path"], href):
                return None
            return {"href": href, "created_by": entry["case"] if entry else None,
                    "state": entry["state"] if entry else "hors registre"}
        if not re.match(SELECTORS["conversation_path"], here):
            return None  # no conversation page: nothing is registered, whatever the sidebar shows (I6)
        href = here
        entry = next((e for e in self.registry if e["href"] == href), None)
        if entry is None and href in {p for p, _ in before.get("items") or []}:
            log.warning("conversation %s was listed before %s: not registered", href, cid)
            return {"href": href, "created_by": None, "state": "hors registre"}
        if entry is None:
            title = next((t for p, t in after.get("items") or [] if p == href), None)
            entry = {"href": href, "case": cid, "via": via, "created": now_iso(), "state": "créée", "title": title,
                     "history": [{"t": now_iso(), "state": "créée", "case": cid}]}
            self.registry.append(entry)
            self._save_registry()
            log.info("conversation %s created by %s", href, cid)
        return {"href": entry["href"], "created_by": entry["case"], "state": entry["state"]}

    def _note_conversation_quietly(self, cid: str, before: dict | None, page, via: str) -> dict | None:
        """_note_conversation après un abandon : au mieux, sans jamais masquer le refus qui suit."""
        try:
            return self._note_conversation(cid, before, page, via)
        except PWError as e:
            log.warning("conversation of %s not noted: %s", cid, one_line(str(e), 160))
            return None

    def _take_case(self, cid: str) -> None:
        """Le cas en main d'une action qui le nomme (lister_conversations, renommer_conversation,
        supprimer_conversation, ouvrir_avis) : un dialogue ou une navigation hors liste pendant elle est noté
        sous lui, et le juge le lit là, même quand le cas se juge sur une observation partagée (revue de la
        v2.3, m4) ; ceux d'un essai antérieur, jamais jugé, sont oubliés."""
        self._case_in_hand = cid
        self._close_window(cid)  # round 2, verdicts-5
        own = self.observations.get(cid)
        if isinstance(own, dict):  # a new attempt: nothing of an older, unjudged one stands
            for key in ("dialog", "offlist", "untested", "internal_error_shown"):
                own.pop(key, None)

    def _after_click(self, page) -> None:
        """Un dialogue ouvert par le clic ou la touche qui précède est vu avant tout appel à la page
        (Browser.settled) : PWTimeout, que l'action change en refus `dialog`, noté sous le cas en main."""
        if not self.browser.settled(page):
            raise PWTimeout("un dialogue s'est ouvert au clic")

    def _in_conversation_of(self, cid: str) -> bool:
        """La page est-elle sur la conversation que `cid` a créée ? (E1 : same_conversation_as se résout par
        le registre, par exemple après une relance du driver, quand sa mémoire de la conversation est
        perdue.)"""
        entry = self._registered(cid)
        if entry is None or entry["state"] in GONE_STATES:
            return False
        return urlsplit(self.browser.page().url).path.rstrip("/") == entry["href"]

    # -- outils -----------------------------------------------------------------------------
    @staticmethod
    def _fresh_conversation() -> dict:
        return {"attached": {}, "asked": []}

    def _case(self, cid: str) -> dict:
        case = self.cases.get(cid)
        if case is None:
            raise DriverRefusal("precondition", f"cas inconnu : {cid}")
        return case

    @staticmethod
    def _resolve(rel: str) -> Path:
        p = case_file(rel)
        if p is None:
            raise DriverRefusal("precondition", f"fichier du cas introuvable : {rel}")
        return p

    def _goto(self, page, url: str, check: bool = True) -> None:
        """Naviguer vers une page de la liste ; une redirection suivie hors liste est défaite
        (retour à la page d'entrée) et l'action rend `host`."""
        if not self.browser.allowed(url):
            host = urlsplit(url).hostname or ""
            raise DriverRefusal("host", f"navigation hors liste refusée : {host}",
                                extra={"host": host, "url": short_url(url)})
        before = self.browser.mark()
        try:
            page.goto(url, wait_until="load", timeout=60_000)
        except PWError as e:
            if not check:
                raise
            if "ERR_ABORTED" in str(e):
                # A script on the page sent the frame elsewhere while it was loading (18/10, CI's
                # Chromium: the sign-in redirect fired during goto). Let it land, then read where.
                page.wait_for_timeout(500)
                if not self.browser.allowed(page.url):
                    self._check_maintenance(page)  # refuses « non connecté » (off-list host)
            # A redirect followed to an unreachable host fails (and then commits an error
            # page, chrome-error://, which is no http URL): name the host from the failed
            # request; a failure of a listed host (the app itself down) is not a host refusal.
            navs = self.browser.since(self.browser.offlist, before)
            failed = self.browser.since(self.browser.failed_navs, before)
            nav = navs[-1] if navs else (self.browser.offlist_failure(before)
                                         if failed or page.url.startswith("chrome-error://") else None)
            if nav is None:
                raise
            raise self._host_refusal({**nav, "page": self.browser._index(page)}) from None
        if check and not self.browser.allowed(page.url):
            nav = {"host": urlsplit(page.url).hostname or "", "url": short_url(page.url),
                   "page": self.browser._index(page)}
            raise self._host_refusal(nav)
        if page is self.browser.page():
            self.conv = self._fresh_conversation()
            self.flux["ev"] = 0

    def _ensure_on_app(self, page) -> None:
        if page.url in ("", "about:blank") or not self.browser.allowed(page.url):
            self._goto(page, self.entry)

    def _save_observations(self) -> None:
        tmp = self.obs_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.observations, ensure_ascii=False, indent=1), encoding="utf-8",
                       errors="replace")
        os.replace(tmp, self.obs_path)

    def _deadline(self, wanted_s: float) -> tuple[float, bool]:
        """Combien attendre : wanted_s, borné par ce qui reste avant l'échéance de l'hôte."""
        cap = remaining_s() - MARGIN_S
        return max(1.0, min(wanted_s, cap)), cap < wanted_s

    # -- journal ------------------------------------------------------------------------------
    def _journaled(self, cid: str) -> dict | None:
        lines = [e for e in journal.effective(journal.load(str(self.journal_path)))
                 if e.get("case") == cid and e.get("step") in ("exec", "correction")]
        return lines[-1] if lines else None

    def _journal_add(self, cid: str, verdict: str, timing_s, evidence: str, nfr: list,
                     pipeline_ms: dict | None = None, extra: dict | None = None, step: str = "exec",
                     supersedes: int | None = None) -> str:
        """Une ligne au journal, avec inputs_sha256 et ui_profile_sha256 ; `step` « correction » et
        `supersedes` pour une ligne de correction (scripts/recette/correct.py, E8)."""
        event = journal.add(str(self.journal_path), argparse.Namespace(
            step=step, case=clean(cid), verdict=verdict,
            timing=round(timing_s, 1) if isinstance(timing_s, (int, float)) else None,
            evidence=clean(evidence)[:600],
            pipeline=[f"{k}={int(v)}" for k, v in (pipeline_ms or {}).items()] or None,
            nfr=",".join(nfr) or None, supersedes=supersedes),
            extra={"inputs_sha256": self.inputs_sha256, "ui_profile_sha256": self.ui_profile_sha256,
                   **clean(extra or {})})
        return json.dumps(event, ensure_ascii=False)

    # -- envoyer un message et attendre la réponse -------------------------------------------
    def _send(self, page, text: str, read_sent: bool = True, pii=()) -> dict:
        """Annexe B : setter natif, événement input, un espace tapé ; puis (read_sent) le popover
        « Chiffré » lu et refermé, le focus rendu à la zone de saisie ; puis Entrée (ou le bouton
        d'envoi si Entrée n'a rien envoyé). Rend le tour à surveiller ; avec read_sent, `_collect`
        lira aussi le panneau « Détails » de sa carte (v2.2), le popover n'étant qu'un repli."""
        b = self.browser
        b.call(MARK_NOTICES_JS, {"notices": SELECTORS["notices"]}, page=page)  # round 2, leakage-1
        st = b.call(POLL_JS, self._poll_arg(0), page=page)
        # stop_before: the stop selectors already match something before anything is sent; that is
        # no generation indicator (a guessed selector on a permanent button): ignored, never clicked.
        turn = {"page": page, "n0": st["n"], "count0": st["count"], "posts0": len(b.posts),
                "page_no": b.context.pages.index(page), "stop_before": st["stop"], "text": text, "pii": list(pii or []),
                "read_sent": read_sent, "notices0": st.get("notices") or []}
        if not b.call(FILL_JS, {"sel": SELECTORS["textarea"], "text": text}, page=page):
            self._check_maintenance(page)  # the app went into maintenance since the guard
            raise DriverRefusal("precondition", "pas de zone de saisie dans la page")
        page.bring_to_front()
        page.keyboard.type(" ")
        if read_sent:
            turn["sent_popover"] = self._read_sent_popover(page)
            self._refocus(page, text)
        turn["t_send"] = time.monotonic()
        page.keyboard.press("Enter")
        self._after_click(page)  # round 2: a dialog the key opened is seen before the page is read again
        page.wait_for_timeout(200)
        left = b.call(VALUE_JS, {"sel": SELECTORS["textarea"]}, page=page)
        if left and left.strip() and len(b.posts) == turn["posts0"]:
            sent = b.call(CLICK_SEND_JS, {"sels": SELECTORS["send_button"], "never": ", ".join(SELECTORS["feedback_entry"]),
                                          **self._composer_arg()}, page=page)
            if sent == "clicked":
                self._after_click(page)
            else:  # round 2, click-targets-3: nothing else is ever pressed; the judge reads why
                turn["send_refused"] = ("envoi refusé par l'interface (bouton d'envoi "
                                        + ("désactivé" if sent == "disabled" else "absent du compositeur") + ")")
        return turn

    @staticmethod
    def _composer_arg() -> dict:
        """Ce que les scripts du compositeur demandent pour le trouver (_COMPOSER) et ne jamais presser le micro."""
        return {"textarea": SELECTORS["textarea"], "model": ", ".join(SELECTORS["model_button"]),
                "mic": SELECTORS["mic_button"]}

    def _refocus(self, page, text: str) -> None:
        """Le popover a pris le focus : le rendre à la zone de saisie, le texte intact (sinon le
        réécrire), avant Entrée."""
        page.locator(SELECTORS["textarea"]).first.focus(timeout=5000)
        value = self.browser.call(VALUE_JS, {"sel": SELECTORS["textarea"]}, page=page) or ""
        if text.strip() not in value:
            log.warning("composer lost its text while the sent-text popover was open: written again")
            self.browser.call(FILL_JS, {"sel": SELECTORS["textarea"], "text": text}, page=page)
            page.keyboard.type(" ")

    def _read_sent_popover(self, page) -> dict:
        """Le texte que MaskAI dit envoyer (D4) : le popover « Chiffré » du compositeur, ouvert
        par le driver (modale `own`), lu quand son contenu ne bouge plus, refermé dans la même
        action. Rend ce qui a été lu (`sent.json`) ; un popover qui ne se referme pas est un
        refus `dialog` (la question ne part pas). Depuis la v2.2, le repli d'une carte de réponse
        sans bouton « Détails » (`source: "composer-popover"`) : le popover décrit l'état avant
        l'envoi."""
        b = self.browser
        info = {"source": None, "text": None, "trigger_label": None, "popover_text": None,
                "block_found": False, "closed": None, "seconds": None}
        t0 = time.monotonic()
        arg = {"textarea": SELECTORS["textarea"], "trigger": SELECTORS["sent_trigger"]}
        trig = None
        try:
            trig = b.call(SENT_TRIGGER_JS, arg, page=page)
            if trig is None:
                info["missing"] = "trigger"
                return info
            label, since = trig["label"], time.monotonic()
            while time.monotonic() - t0 < SENT_SETTLE_S:  # the label follows the text a moment later
                page.wait_for_timeout(100)
                again = b.call(SENT_TRIGGER_JS, arg, page=page)
                if again is None:
                    break
                if again["label"] != label:
                    label, since = again["label"], time.monotonic()
                elif time.monotonic() - since >= SENT_STABLE_S:
                    break
            info["trigger_label"] = label
            b.own_modal = "envoi"
            page.locator(SELECTORS["sent_trigger"]).nth(trig["index"]).click(timeout=5000)
            read = {"trigger": SELECTORS["sent_trigger"], "index": trig["index"],
                    "popover": SELECTORS["sent_popover"], "text": SELECTORS["sent_text"]}
            content, since, t1 = None, None, time.monotonic()
            while time.monotonic() - t1 < SENT_SETTLE_S:
                page.wait_for_timeout(100)
                got = b.call(SENT_READ_JS, read, page=page)
                if got is None:
                    continue
                if content is None or got["text"] != content["text"]:
                    content, since = got, time.monotonic()
                elif time.monotonic() - since >= SENT_STABLE_S:
                    break
            if content and content["text"]:
                info.update(source="composer-popover", text=content["text"], popover_text=content["popover"],
                            block_found=content["block"])
            else:
                info["missing"] = "popover"
        except PWTimeout:
            if b.pending():
                b.own_modal = None  # a frozen page: the dialog refusal follows, the popover is not ours to keep
                raise
            info["error"] = "timeout"
        except PWError as e:
            info["error"] = one_line(str(e), 200)
        finally:
            if b.own_modal == "envoi" and not b.pending():
                try:
                    info["closed"] = self._close_sent_popover(page, trig["index"] if trig else None)
                except PWError as e:
                    info["closed"], info["close_error"] = False, one_line(str(e), 200)
            if info.get("error") or info.get("close_error"):
                # After an error the popover's state is unknown: the driver no longer claims it, so a
                # popover still open is an unknown dialog for the next guard, never closed as ours.
                b.own_modal = None
            info["seconds"] = round(time.monotonic() - t0, 2)
        if info["closed"] is False:
            raise DriverRefusal("dialog", "le popover du texte envoyé reste ouvert (Échap, croix et "
                                          "déclencheur essayés) : question non envoyée",
                                extra={"sent": info})
        return info

    def _close_sent_popover(self, page, index: int | None):
        """Refermer le popover que le driver a ouvert : Échap, puis sa croix, puis son déclencheur.
        Rend le moyen qui a suffi, ou False."""
        b = self.browser
        arg = {"popover": SELECTORS["sent_popover"]}

        def gone(limit_s: float = 1.5) -> bool:
            deadline = time.monotonic() + limit_s
            while True:
                if not b.call(SENT_OPEN_JS, arg, page=page, timeout_ms=2000):
                    return True
                if time.monotonic() >= deadline:
                    return False
                page.wait_for_timeout(100)

        if gone(0):
            b.own_modal = None
            return "already"
        steps = [("escape", lambda: page.keyboard.press("Escape")),
                 ("close", lambda: b.call(POPOVER_CLOSE_JS, {**arg, "close": SELECTORS["popover_close"]},
                                          page=page, timeout_ms=2000))]
        if index is not None:
            steps.append(("trigger", lambda: page.locator(SELECTORS["sent_trigger"]).nth(index).click(timeout=5000)))
        for how, step in steps:
            step()
            if gone():
                b.own_modal = None
                return how
        return False

    def _read_card_details(self, page, turn: dict) -> dict:
        """v2.2 — le texte envoyé au modèle, lu sur la dernière carte de réponse une fois la réponse
        finie : son bouton « Détails », cherché dans cette carte seulement, cliqué ; le contenu
        ouvert lu quand il ne bouge plus (DETAILS_STABLE_S s'il porte la question,
        DETAILS_OTHER_STABLE_S sinon, au plus SENT_SETTLE_S) ; le même bouton recliqué pour le
        refermer s'il l'a bien ouvert. Le contenu lu est celui que ce clic a ouvert : un autre
        panneau de la carte, déjà ouvert avant, n'est jamais pris pour lui. Le texte retenu est le
        texte seul (details_text) ; le texte entier du panneau reste dans `content_text`. Le
        bouton de révélation des PII n'est jamais pressé : son état est noté (`reveal_pressed`).
        Un panneau déjà ouvert (aria-expanded à true) est lu tel quel et laissé ouvert.
        Une carte sans bouton « Détails » (ou pas de carte nouvelle) rend `source: None`,
        `missing` et `rejected` : le repli est à l'appelant. Tout autre échec (le panneau ne
        s'ouvre pas, ne montre aucun texte, une erreur, le délai de l'hôte) reste
        `source: "card-details"` avec `rejected` : pas de repli, la carte a son bouton. Un
        dialogue pendant la lecture gèle la page : le panneau reste alors ouvert, le refus
        `dialog` suit."""
        b = self.browser
        info = {"source": "card-details", "text": None, "trigger": None, "content": None, "closed": None,
                "seconds": None}
        if remaining_s() < MARGIN_S + DETAILS_MIN_S:  # the evidence first: the panel is not read
            info.update(rejected="délai de l'hôte", seconds=0.0)
            return info
        arg = {"assistant": SELECTORS["assistant_message"], "triggers": details_triggers(),
               "reveal": SELECTORS["pii_reveal"], "body": SELECTORS["answer_body"],
               "content": SELECTORS["card_details_content"], "text": SELECTORS["sent_text"]}
        question, pii = turn.get("text", ""), turn.get("pii")
        t0, clicked, content = time.monotonic(), False, None
        try:
            seen = b.call(DETAILS_JS, arg, page=page, timeout_ms=3000)
            if seen["count"] <= turn["count0"]:  # no card of this turn's on screen
                info.update(source=None, missing="card", rejected="pas de carte de réponse")
                return info
            info.update(card={"selector": seen["card"], "index": seen["index"]},
                        reveal_pressed={"true": True, "false": False}.get(seen["reveal"]))
            if seen["trigger"] is None:
                info.update(source=None, missing="trigger", rejected="pas de bouton Détails sur la carte")
                return info
            info.update(trigger=seen["trigger"], trigger_label=seen.get("label"))
            if details_open(seen):
                info["already_open"] = True  # opened before the driver came: read as found, left as found
            else:
                arg["exclude"] = seen.get("open_idx") or []  # open before the click: never its panel
                self._click_details(page, arg)
                clicked = True
            since, settled, t1 = None, False, time.monotonic()
            while time.monotonic() - t1 < SENT_SETTLE_S:
                got = b.call(DETAILS_JS, arg, page=page, timeout_ms=2000)
                now = time.monotonic()
                if got.get("text"):
                    if content is None or got["text"] != content["text"]:
                        content, since = got, now
                    else:
                        shown, _ = details_text(got["text"], got.get("block"), question)
                        if now - since >= (DETAILS_STABLE_S if looks_like_sent(shown, question, pii)
                                           else DETAILS_OTHER_STABLE_S):
                            settled = True
                            break
                page.wait_for_timeout(100)
            if content:
                text, label = details_text(content["text"], content.get("block"), question)
                info.update(text=text, label=label, block=content.get("block_sel"),
                            content=(f"#{content['controls']}" if content["how"] == "aria-controls"
                                     else SELECTORS["card_details_content"]),
                            content_state=content["state"], content_settled=settled,
                            content_text=content["text"], content_html=content["html"])
            else:
                info["missing"] = "content"
        except PWTimeout:
            if b.pending():
                raise
            info["error"] = "timeout"
        except PWError as e:
            info["error"] = one_line(str(e), 200)
        finally:
            if clicked and not b.pending():
                try:
                    info["closed"] = self._close_card_details(page, arg)
                except PWError as e:
                    info["closed"], info["close_error"] = False, one_line(str(e), 200)
                if info["closed"] is False:
                    log.warning("the « Détails » panel of the last answer card stays open")
            info["seconds"] = round(time.monotonic() - t0, 2)
        if info["text"] is None and not info.get("rejected"):
            if info.get("error"):
                info["rejected"] = f"panneau « Détails » non lu ({info['error']})"
            elif clicked and content is None and info["closed"] is None:
                info["rejected"] = "le panneau ne s'est pas ouvert"
            else:
                info["rejected"] = "le panneau « Détails » ne montre aucun texte"
        return info

    def _click_details(self, page, arg: dict) -> None:
        """Cliquer le bouton « Détails » de la dernière carte : l'élément que la page désigne dans
        cette carte (DETAILS_EL_JS), jamais un rang parmi les boutons de la page."""
        el = self.browser.element(DETAILS_EL_JS, arg, timeout_ms=3000, page=page)
        if el is None:
            raise PWError("le bouton « Détails » de la carte a disparu")
        try:
            el.click(timeout=3000)
        finally:
            el.dispose()

    def _close_card_details(self, page, arg: dict) -> bool | None:
        """Refermer le panneau que le clic du driver a ouvert, et lui seul : relu d'abord. Ouvert
        (details_open), un second clic sur le même bouton, puis le panneau fermé au plus
        DETAILS_CLOSE_S : True, ou False s'il reste ouvert. Pas ouvert (le premier clic n'a rien
        ouvert) : rien n'est cliqué, None ; un clic à l'aveugle l'ouvrirait."""
        b = self.browser
        if not details_open(b.call(DETAILS_JS, arg, page=page, timeout_ms=2000)):
            return None
        self._click_details(page, arg)
        deadline = time.monotonic() + DETAILS_CLOSE_S
        while True:
            if not details_open(b.call(DETAILS_JS, arg, page=page, timeout_ms=2000)):
                return True
            if time.monotonic() >= deadline:
                return False
            page.wait_for_timeout(100)

    @staticmethod
    def _poll_arg(start: int) -> dict:
        return {"from": start, "assistant": SELECTORS["assistant_message"], "body": SELECTORS["answer_body"],
                "stop": SELECTORS["stop_button"], "stopText": SELECTORS["stop_text"],
                "status": SELECTORS["input_status"], "notices": SELECTORS["notices"], "fresh": True,
                "textarea": SELECTORS["textarea"], "model": ", ".join(SELECTORS["model_button"]),
                "mic": SELECTORS["mic_button"]}

    def _wait(self, turns: list, wait_s: float, check_host: bool = True) -> None:
        """Attendre chaque tour, en deux temps.
        1. Le flux : <END> ou <ABORT> dans window.__ev, ou le bouton d'arrêt vu puis disparu, ou
           (flux non vu) une réponse nouvelle immobile 3 s. Au-delà de wait_s : AskTimeout.
        2. L'affichage (D6) : le corps de la nouvelle réponse immobile DISPLAY_STABLE_S et le
           bouton d'arrêt disparu. Au plus DISPLAY_CAP_S après la fin du flux (et avant
           l'échéance de l'action) : au-delà, le tour finit « encore en mouvement »
           (display_stable false) sans abandon, le produit a répondu. Sans carte nouvelle
           NO_ANSWER_WAIT_S après la fin du flux : le tour finit sans réponse.
        Un onglet qui quitte la liste : refus `host` (check_host), ou son tour finit sur
        <HORS PÉRIMÈTRE> et l'appelant juge (charge_concurrente)."""
        b = self.browser
        for t in turns:
            t.update(done=False, events=[], saw_stop=False, stop_gone=None, last_text=None, changed_at=None,
                     end_at=None, display_stable=None, display_s=None, notices=[])
        start = min(t["t_send"] for t in turns)
        while not all(t["done"] for t in turns):
            if check_host:
                self._check_host()  # the page left the list: no answer is coming here
            else:
                gone = {n["page"] for n in self._offlist()}
                for t in turns:
                    if not t["done"] and t["page_no"] in gone:
                        t.update(done=True, end="<HORS PÉRIMÈTRE>",
                                 total_s=round(time.monotonic() - t["t_send"], 2))
                if all(t["done"] for t in turns):
                    break
            streaming = [t for t in turns if not t["done"] and t["end_at"] is None]
            if streaming and time.monotonic() - start >= wait_s:
                raise AskTimeout(time.monotonic() - start, "pas de fin de réponse")
            for t in (t for t in turns if not t["done"]):
                try:
                    st = b.call(POLL_JS, self._poll_arg(t["n0"]), timeout_ms=2000, page=t["page"])
                except PWTimeout:
                    if b.pending():
                        raise
                    continue
                if st["n"] < t["n0"]:  # the page reloaded: a fresh window.__ev
                    t["n0"] = 0
                    continue
                t["events"] = st["ev"]
                for notice in st.get("notices") or []:  # what the page says meanwhile (E10), toasts that go
                    if notice not in t["notices"] and len(t["notices"]) < 20:
                        t["notices"].append(notice)
                lines = [line for _, line in st["ev"]]
                now = time.monotonic()
                fresh = st["count"] > t["count0"]  # a new answer card is on screen
                stop = st["stop"] and not t.get("stop_before")
                text = st["text"] if fresh else None
                if text != t["last_text"] or t["changed_at"] is None:
                    t["last_text"], t["changed_at"] = text, now
                if t["end_at"] is None:
                    ended = None
                    if any(line.startswith(("<END>", "<ABORT>")) for line in lines):
                        ended = next(x for x in lines if x.startswith(("<END>", "<ABORT>")))
                    elif stop:
                        t["saw_stop"], t["stop_gone"] = True, None
                    elif t["saw_stop"]:
                        t["stop_gone"] = t["stop_gone"] or now
                        if now - t["stop_gone"] >= 1.0:
                            ended = "<ARRÊT DISPARU>"
                    elif (not any(line == "<START>" for line in lines) and fresh and (text or "").strip()
                          and now - t["changed_at"] >= 3.0):
                        ended = "<STABLE>"
                    if ended:
                        t["end"], t["end_at"] = ended, now
                        t["total_s"] = round(now - t["t_send"], 2)
                if t["end_at"] is not None:
                    since_end = now - t["end_at"]
                    if fresh and now - t["changed_at"] >= DISPLAY_STABLE_S and not stop:
                        t.update(done=True, display_stable=True,
                                 display_s=round(max(t["changed_at"] - t["t_send"], 0.0), 2))
                    elif not fresh and since_end >= NO_ANSWER_WAIT_S:
                        t.update(done=True, display_stable=None)
                    elif since_end >= DISPLAY_CAP_S or remaining_s() - MARGIN_S <= 0.5:
                        t.update(done=True, display_stable=False)
            if b.pending():
                raise PWTimeout("dialog pending")
            if not all(t["done"] for t in turns):
                turns[0]["page"].wait_for_timeout(250)

    def _collect(self, turn: dict) -> dict:
        """La réponse d'un tour terminé (affichage déjà immobile) : le texte du corps, l'en-tête
        en champs (guard), le HTML de la carte, les étapes serveur, le texte envoyé. Celui-ci
        (v2.2) : le panneau « Détails » de la carte, lu maintenant ; pour une carte sans ce
        bouton seulement, le popover lu à l'envoi, sinon le panneau du 13/10, sinon rien. Un
        texte lu qui ne porte pas la question (un statut) est écarté (`rejected`)."""
        b, page = self.browser, turn["page"]
        got = b.call(ANSWER_JS, {"assistant": SELECTORS["assistant_message"], "body": SELECTORS["answer_body"]},
                     page=page)
        fresh = got["count"] > turn["count0"]
        if not fresh:  # no new card: the last one on screen is an older answer, never this one's
            got = {"text": "", "card": "", "html": "", "body": False}
        guard = parse_guard(got["card"], got["text"], got["body"])
        steps, ttft = [], None
        for t_ms, line in turn["events"]:
            m = re.search(r'"node"\s*:\s*"([^"]+)"', line)
            if m:
                steps.append({"node": m.group(1), "t_ms": t_ms})
            elif ttft is None and not line.startswith(("<START>", "<END>", "<ABORT>")):
                ttft = round(t_ms / 1000, 2)
        pipeline = {}
        ends = [t for t, line in turn["events"] if line.startswith(("<END>", "<ABORT>"))]
        for i, s in enumerate(steps):
            nxt = steps[i + 1]["t_ms"] if i + 1 < len(steps) else (ends[0] if ends else None)
            if nxt is not None:
                pipeline[s["node"]] = pipeline.get(s["node"], 0) + max(0, nxt - s["t_ms"])
        posts = [p for p in b.posts[turn["posts0"]:] if p["page"] in (turn["page_no"], -1)]
        panel = b.call(SENT_JS, {"label": SELECTORS["sent_label"]}, page=page)
        question, pii = turn.get("text", ""), turn.get("pii")
        popover = dict(turn.get("sent_popover") or {"source": None})
        if popover.get("source") == "composer-popover" and not looks_like_sent(popover.get("text"), question, pii):
            popover.update(source=None, rejected="le popover ne montre pas le texte de la question")
        card = None
        if turn.get("read_sent") and fresh:
            if not b.pending():
                try:  # answer.png now: the « Détails » click scrolls the card header into view
                    self._answer_shot = screenshot_png(page)
                except PWError as e:
                    log.warning("answer screenshot before the « Détails » read failed: %s", one_line(str(e)))
            card = self._read_card_details(page, turn)
        sent_to_model = None
        if card is not None and card.get("missing") not in ("card", "trigger"):
            # The card has its « Détails »: it alone says what left, never the fallback; the popover
            # read before sending (the state before sending) is kept for the debrief.
            sent_info = {**card, "composer_popover": popover}
            if not sent_info.get("rejected") and not looks_like_sent(card["text"], question, pii):
                sent_info["rejected"] = "le panneau « Détails » ne montre pas le texte de la question"
            if not sent_info.get("rejected"):
                sent_to_model = sent_info["text"]
        else:
            # No « Détails » on the card: the composer popover, then the 13/10 panel (the fallback).
            sent_info = {**popover, **({"card": card} if card is not None else {})}
            if popover.get("source") == "composer-popover":
                sent_to_model = popover["text"]
            elif panel is not None:
                sent_to_model = panel
                sent_info.update(source="panel", text=panel)
            elif card is not None and card.get("rejected"):
                if popover.get("rejected"):
                    sent_info["popover_rejected"] = popover["rejected"]
                sent_info["rejected"] = card["rejected"]
        sent_info["panel_text"] = panel
        said = self._turn_notices(turn, page)  # round 2, leakage-1: every turn, answered or not
        errs = error_notices(said)
        notices = ({"errors": len(errs), "internal_error_shown": notices_internal(said, self._keep_names())}
                   if errs else None)
        failure = failure_raw = None
        if not fresh or not (got["text"] or "").strip():  # a silent failure: what the page and the stream said
            failure, failure_raw = self._diagnose(turn, page, fresh, said)
        return {"answer": got["text"].strip(), "html": got["html"], "guard": guard, "body_found": got["body"],
                "new_answer": fresh, "sent": posts[-1]["body"] if posts else None,
                "sent_to_model": sent_to_model, "sent_info": sent_info,
                "ttft_s": ttft, "total_s": turn.get("total_s"), "timing_s": turn.get("total_s"),
                "display_s": turn.get("display_s"), "display_stable": turn.get("display_stable"),
                "server_steps": steps, "pipeline_ms": pipeline, "end": turn.get("end"), "t": now_iso(),
                "failure": failure, "failure_raw": failure_raw, "notices": notices, "notices_raw": errs or None}

    def _turn_notices(self, turn: dict, page) -> list:
        """Ce que la page a dit depuis l'envoi d'un tour (round 2, leakage-1) : les avis vus pendant l'attente et
        maintenant, ceux d'avant l'envoi mis à part. Lu pour chaque tour, répondu ou non, et avant le clic
        d'arrêt d'un abandon (qui peut effacer les toasts)."""
        b = self.browser
        try:
            now = b.call(NOTICES_JS, {"status": SELECTORS["input_status"], "notices": SELECTORS["notices"],
                                      "fresh": True}, page=page, timeout_ms=2000) or []
        except PWTimeout:
            if b.pending():
                raise
            now = []
        seen = list(turn.get("notices") or [])
        for notice in now:
            if notice not in seen:
                seen.append(notice)
        turn["notices"] = seen
        before = turn.get("notices0") or []
        return [n for n in seen if n not in before]

    def _diagnose(self, turn: dict, page, fresh: bool, said: list | None = None) -> tuple[dict, dict]:
        """v2.3, E10 — une question restée sans réponse (pas de carte nouvelle, ou une carte vide) : ce que
        la page et le flux en ont dit. La ligne d'état du compositeur, les alertes et les toasts (vus pendant
        l'attente et maintenant, ceux d'avant l'envoi mis à part), le statut HTTP du flux, ses derniers
        événements et sa dernière charge utile d'erreur. Rend le résumé que porte la ligne (`error_class`, et
        `internal_error_shown` : la ligne d'état, une alerte ou un toast a-t-il montré une erreur interne,
        un hôte, un chemin, une pile ou un service injoignable ; ce que dit le flux, que l'utilisateur ne voit
        pas, n'y compte pas : revue de la v2.3, I3) et la capture brute, qui ne va qu'à la preuve
        `failure.json`, avec l'extrait expurgé de EXCERPT_MAX caractères au plus (revue I2 : l'expurgation
        est une heuristique, l'extrait ne va plus sur la ligne)."""
        b = self.browser
        if said is None:
            said = self._turn_notices(turn, page)
        seen, before = list(turn.get("notices") or []), turn.get("notices0") or []
        posts = [p for p in b.posts[turn["posts0"]:] if p["page"] in (turn["page_no"], -1)]
        net = (posts[-1].get("net") or {}) if posts else {}
        http = net.get("status")
        digest = stream_digest(turn.get("events"))
        cls = failure_class(http, digest["error"], said, digest["names"], bool(posts) or bool(digest["names"]))
        source = (digest["error"] or next((text for kind, text in said if kind == "status"), None)
                  or (said[0][1] if said else ""))
        internal = notices_internal(said, self._keep_names())  # round 2, leakage-4: error notices only
        summary = {"error_class": cls, "internal_error_shown": internal, "http_status": http,
                   "last_node": digest["last_node"], "events": digest["names"], "new_card": fresh}
        raw = {"error_class": cls, "internal_error_shown": internal, "excerpt": sanitise(source) or None,
               "http_status": http, "request_failure": net.get("failure"),
               "events": (turn.get("events") or [])[-40:], "stream": digest, "notices": seen,
               "notices_before": before}
        log.warning("silent failure: %s", cls)
        return summary, raw

    def _stop_generation(self, page) -> dict:
        """Cliquer sur le bouton d'arrêt s'il est visible, puis attendre sa disparition (au plus
        STOP_WAIT_S). Une génération arrêtée n'est pas laissée en vol : l'enquête « Votre avis »
        suivait chaque abandon du 17/10 (D2)."""
        b = self.browser
        try:
            clicked = b.call(CLICK_STOP_JS, {"sels": SELECTORS["stop_button"], "stopText": SELECTORS["stop_text"],
                                             **self._composer_arg()}, page=page, timeout_ms=2000)
        except (PWTimeout, PWError) as e:
            if b.pending():
                raise
            return {"stop_clicked": False, "stop_error": one_line(str(e), 120)}
        gone = None
        if clicked:
            deadline = time.monotonic() + STOP_WAIT_S
            gone = False
            while time.monotonic() < deadline:
                page.wait_for_timeout(200)
                try:
                    if not b.call(POLL_JS, self._poll_arg(0), page=page, timeout_ms=2000)["stop"]:
                        gone = True
                        break
                except PWTimeout:
                    if b.pending():
                        raise
            log.info("abandon: stop clicked, %s", "gone" if gone else "still visible")
        return {"stop_clicked": bool(clicked), "stop_gone": gone}

    def _abort(self, turn: dict) -> dict:
        """Au délai d'abandon : ce qui était parti (POST, <START>, dernier nœud, D8), puis le clic
        sur le bouton d'arrêt (D2)."""
        b = self.browser
        lines = [line for _, line in turn.get("events") or []]
        nodes = []
        for t_ms, line in turn.get("events") or []:
            m = re.search(r'"node"\s*:\s*"([^"]+)"', line)
            if m:
                nodes.append((t_ms, m.group(1)))
        profile = {"posted": any(p["page"] in (turn["page_no"], -1) for p in b.posts[turn["posts0"]:]),
                   "stream_started": "<START>" in lines, "events": len(lines),
                   "last_node": nodes[-1][1] if nodes else None,
                   "last_node_s": round(nodes[-1][0] / 1000, 1) if nodes else None}
        if turn.get("send_refused"):  # round 2, click-targets-3
            profile["send_refused"] = turn["send_refused"]
        if turn.get("stop_before"):  # the stop selectors match a button that was there before: no click
            profile.update(stop_clicked=False, stop_skipped=True)
            log.warning("abandon: stop not clicked, the stop selectors matched before sending")
        else:
            profile.update(self._stop_generation(turn["page"]))
            # clicked but still visible, or nothing to click: maybe still in flight; the next
            # nouvelle_conversation tries again
            self._in_flight = not profile.get("stop_gone")
        return profile

    def _ask(self, text: str, budget_s: float, pii=()) -> dict:
        b = self.browser
        page = b.page()
        self._ensure_on_app(page)
        if not text.strip():  # N1 : l'envoi vide ne doit rien envoyer
            posts0 = len(b.posts)
            page.locator(SELECTORS["textarea"]).first.focus()
            page.keyboard.press("Enter")
            page.wait_for_timeout(2000)
            sent = len(b.posts) > posts0
            return {"answer": "un message vide a été envoyé" if sent else "aucun envoi", "html": "",
                    "sent": "" if sent else None, "sent_to_model": None, "ttft_s": None, "total_s": 0.0,
                    "timing_s": 0.0, "server_steps": [], "pipeline_ms": {}, "t": now_iso()}
        wait_s, capped = self._deadline(3 * budget_s)
        turn = self._send(page, text, pii=pii)
        try:
            self._wait([turn], wait_s)
        except AskTimeout as t:
            t.why = (f"abandon à {t.seconds:.0f} s ("
                     + ("échéance de l'action" if capped else f"3 × budget {budget_s:g} s") + ")")
            t.capped = capped  # cut by the action's deadline before 3 × budget: no verdict on the product
            said = self._turn_notices(turn, page)  # round 2, leakage-1: before the stop click clears the toasts
            t.notices = {"raw": error_notices(said), "internal_error_shown": notices_internal(said, self._keep_names())}
            t.profile = self._abort(turn)
            raise
        obs = self._collect(turn)
        self._check_host()  # a page that left after <END> is not the answer
        return obs

    def _timeout_line(self, cid: str, nfr: list, t: AskTimeout, why: str, extra: dict | None = None) -> str:
        """La ligne d'un abandon. À 3 × le budget : KO disponibilité. Coupé avant par l'échéance de
        l'action (le produit n'a pas eu son temps) : NON TESTÉ, sans ENF, « échéance de l'action ». `extra` :
        les champs de la ligne (config, modals_closed)."""
        if getattr(t, "capped", False):
            return self._journal_add(cid, "NON TESTÉ", t.seconds,
                                     f"{why} : échéance de l'action avant 3 × le budget, pas de verdict sur le "
                                     f"produit ; {abort_text(t.profile)}", [], extra=extra)
        return self._journal_add(cid, "KO", t.seconds, f"{why} : pas de fin de réponse ; {abort_text(t.profile)}",
                                 nfr_union(nfr, ["disponibilite"]), extra=extra)

    def _answer_evidence(self, ev: Evidence, obs: dict, mark: int, failure: dict | None = None,
                         notices: list | None = None) -> None:
        page = self.browser.page()
        if failure:  # E10: the raw capture of a silent failure, in the evidence only
            ev.json("failure.json", failure)
        if notices:  # round 2, leakage-1: the error notices the page showed, raw, in the evidence only
            ev.json("notices.json", notices)
        if obs.get("html"):
            ev.html("dom.html", obs["html"])
        ev.json("guard.json", {**(obs.get("guard") or {}), "body_found": obs.get("body_found"),
                               "display_stable": obs.get("display_stable")})
        ev.json("sent.json", {**(obs.get("sent_info") or {"source": None}),
                              "post_body": (obs.get("sent") or "")[:20_000] or None})
        ev.json("network.json", self.browser.capture(mark), cap=NETWORK_CAP)
        self._answer_png(ev, page)

    def _answer_png(self, ev: Evidence, page) -> None:
        """answer.png : la capture prise avant le clic sur « Détails » (_collect), sinon maintenant."""
        shot, self._answer_shot = self._answer_shot, None
        if shot:
            ev.add("answer.png", shot)
        else:
            ev.screenshot(page, "answer.png")

    @staticmethod
    def _answer_result(obs: dict, label: str) -> dict:
        result = {k: obs.get(k) for k in ("answer", "ttft_s", "total_s", "display_s", "server_steps")}
        guard = obs.get("guard") or {}
        result.update(model=guard.get("model"), pii_detected=guard.get("pii_detected"))
        result["sent"] = (obs.get("sent") or "")[:2000] or None
        result["sent_source"] = (obs.get("sent_info") or {}).get("source")
        if obs.get("sent_to_model") is not None:
            result["sent_to_model"] = obs["sent_to_model"][:2000]
        answer = obs.get("answer") or ""
        internal = shown_internal(obs)
        if answer_shows_internal(answer):  # m11: an answer that shows an internal error, sanitised
            result["answer"] = sanitise(answer, 4000)
        shown = sanitise(answer, 160) if answer_shows_internal(answer) else one_line(answer, 160)
        result["text"] = f"{label} : réponse en {obs.get('total_s')} s — {shown}"
        if obs.get("failure"):  # E10: the class only, never the raw text
            result["error_class"] = obs["failure"]["error_class"]
            result["text"] += f" ; échec silencieux : {obs['failure']['error_class']}"
        result["internal_error_shown"] = internal  # I3: what the user saw, for the harness to report
        if internal:
            result["text"] += " ; erreur interne affichée à l'utilisateur"
        return result

    # =========================================================================================
    # Les douze actions
    # =========================================================================================
    def mesurer_chargement(self, args: dict, ev: Evidence) -> dict:
        """N0 : charger la page d'entrée, attendre le repos réseau, lire la mesure de navigation."""
        b = self.browser
        page = b.page()
        self._goto(page, self.entry)
        try:
            page.wait_for_load_state("networkidle", timeout=10_000)
        except PWTimeout:
            pass  # un flux tenu ouvert empêche le repos réseau : la mesure reste celle du load
        self._check_maintenance(page)  # a maintenance or a login page is no measure of the app (18/10)
        t = b.call(TIMING_JS)
        nav = t["nav"] or {}
        fcp = next((p["start"] for p in t["paint"] if p["name"] == "first-contentful-paint"), None)
        result = {"ttfb_ms": round(nav.get("responseStart", 0)),
                  "dom_content_loaded_ms": round(nav.get("domContentLoadedEventEnd", 0)),
                  "load_ms": round(nav.get("loadEventEnd", 0)),
                  "fcp_ms": round(fcp) if fcp is not None else None,
                  "transfer_bytes": int(nav.get("transferSize", 0) + t["resources_bytes"])}
        result["text"] = (f"chargement de {short_url(t['url'])} : ttfb {result['ttfb_ms']} ms, "
                          f"DOMContentLoaded {result['dom_content_loaded_ms']} ms, load {result['load_ms']} ms, "
                          f"FCP {result['fcp_ms']} ms, {result['transfer_bytes']} octets transférés "
                          f"({t['resources']} ressources)")
        ev.json("timing.json", {**t, "url": short_url(t["url"])})
        ev.screenshot(page, "screen.png")
        self._shared("@load", {"answer": result["text"], "timing_s": result["load_ms"] / 1000, "t": now_iso(),
                               "composer": self._composer_now(page)})
        return result

    def nouvelle_conversation(self, args: dict, ev: Evidence) -> dict:
        """Une conversation neuve par cas (procédure §6, étape 4) ; l'intercepteur réarmé."""
        b = self.browser
        page = b.page()
        self._ensure_on_app(page)
        stopped = None
        try:  # a generation an abandon could not stop is stopped first, never left behind (D2)
            if self._in_flight and b.call(POLL_JS, self._poll_arg(0), page=page, timeout_ms=3000)["stop"]:
                stopped = self._stop_generation(page)
        except PWTimeout:
            if b.pending():
                raise
        self._in_flight = False
        clicked = False
        el = b.element(NEW_CONV_EL_JS, {"validated": SELECTORS["new_conversation"], "sidebar": SELECTORS["sidebar"],
                                        "labels": SELECTORS["new_conversation_labels"]}, page=page, timeout_ms=3000)
        if el is not None:  # round 2: one bounded read (robustness-4), never a button guessed by its words
            try:
                el.click(timeout=5000)
                clicked = True
            except PWTimeout:
                if b.pending():
                    raise
            except PWError:
                pass
            finally:
                el.dispose()
            if clicked:
                self._after_click(page)
        if not clicked:
            self._goto(page, self.entry)
        self._wait_for_composer(page)
        b.call("() => {" + INTERCEPTOR_JS + " window.__ev = []; return true; }")
        self.conv = self._fresh_conversation()
        self.flux["ev"] = 0
        self._last_asked = None
        how = "bouton" if clicked else "retour à la page d'entrée"
        if stopped:
            how += ", génération en vol arrêtée d'abord"
        reapplied = self._reapply_pinned(page)
        if reapplied:
            how += ", configuration épinglée réappliquée (" + ", ".join(f"{k} {v}" for k, v in reapplied.items()) + ")"
        return {"conversation": page.url, "stopped_first": stopped, "reapplied": reapplied or None,
                "text": f"nouvelle conversation ({how}) : {short_url(page.url)}"}

    def _reapply_pinned(self, page) -> dict:
        """La configuration épinglée, remise si l'interface l'a perdue (un modèle ou un mode web
        revenu à son défaut). Rend ce qui a été remis, ou l'échec ; n'arrête jamais l'action."""
        if not self.pinned:
            return {}
        now, done = self._composer_now(page), {}
        try:
            if self.pinned.get("model") and not model_matches(self.pinned["model"], now["model"]):
                done["modèle"] = self._set_model(page, self.pinned["model"])["read_back"]
            if self.pinned.get("web") and now["web"] != self.pinned["web"]:
                done["web"] = self._set_web(page, self.pinned["web"])["read_back"]
        except (DriverRefusal, PWError) as e:
            done["échec"] = one_line(getattr(e, "detail", "") or str(e), 120)
            log.warning("pinned configuration not set again: %s", done["échec"])
        return done

    def joindre_document(self, args: dict, ev: Evidence) -> dict:
        """Joindre le fichier du cas par le champ caché derrière le trombone, puis suivre la puce
        (ChipWatch) jusqu'à prête ou en échec, au plus 3 × le budget du cas : poser_question ne
        part que sur une puce prête (D7)."""
        cid = args["case"]
        case = self._case(cid)
        if not case.get("file"):
            raise DriverRefusal("precondition", f"le cas {cid} n'a pas de fichier à joindre")
        path = self._resolve(case["file"])
        b = self.browser
        page = b.page()
        self._ensure_on_app(page)
        self._case_in_hand = cid
        mark = b.mark()
        conv_before = self._conversation_state(page)  # m13: the upload may create the conversation
        att = self._attach(page, path, budget_of(case))
        # v2.3, E9 : the raw label (a host name and a stack, on 18/10) goes to chip.json only. The line, the
        # observation and the harness get its class and whether it shows an internal error (I2: a failed
        # chip's label, even sanitised, never leaves the evidence); a chip not failed, its sanitised label.
        raw_label = att["label"]
        failed = att["state"] == "failed"
        record = {"state": att["state"], "ready_by": att["ready_by"], "seconds": att["seconds"],
                  "label": None if failed else sanitise(raw_label, 120, keep=(path.name,)), "trace": att["trace"],
                  "accept": att["accept"], "error_class": attach_error_class(raw_label, path.name) if failed else None,
                  "internal_error_shown": chip_shows_internal(raw_label, path.name)}
        self.conv["attached"][cid] = {k: att[k] for k in ("state", "ready_by", "pending_seen")} | {"file": path.name}
        conversation = self._note_conversation_quietly(cid, conv_before, page, "joindre_document")
        self.observations[cid] = {"attach": {**record, "t": now_iso()},
                                  **({"conversation": conversation} if conversation else {})}
        ev.screenshot(page, "attach.png")
        ev.json("network.json", b.capture(mark), cap=NETWORK_CAP)
        ev.json("chip.json", {**record, "label": raw_label, "reading": att["reading"], "uploads": att["uploads"]})
        extra = {**record, "uploads": att["uploads"]}
        if att["state"] == "failed":
            raise DriverRefusal("precondition", "attachment failed", extra=extra)
        if att["state"] != "ready":
            raise DriverRefusal("precondition", f"pièce jointe « {record['label'] or 'sans puce'} » ({att['state']}) "
                                                f"après {att['seconds']} s", extra=extra)
        how = {"marker": "marquée prête", "after_pending": "indexée", "quiet": "sans marqueur, présumée prête"}
        return {**extra, "text": f"{path.name} prêt en {att['seconds']} s ({how.get(att['ready_by'], '')}, "
                                 f"{' → '.join(x['state'] for x in att['trace'])})"
                                 + (f" ; accept : {att['accept']}" if att["accept"] else " ; accept : aucun")}

    def _read_chip(self, page, name: str):
        return self.browser.call(CHIP_JS, {"remove": SELECTORS["attach_remove"], "chips": SELECTORS["attach_chip"],
                                           "busy": SELECTORS["attach_busy"], "name": name}, page=page)

    def _attach(self, page, path: Path, budget_s: float) -> dict:
        """Le fichier dans le champ caché, puis la puce lue toutes les 250 ms jusqu'à un état final
        (ready, failed), au plus 3 × budget_s, borné par l'échéance de l'action."""
        b = self.browser
        mark = b.mark()
        field = page.locator(SELECTORS["file_input"]).first
        accept = field.get_attribute("accept", timeout=10_000)  # E6: the families the page says it takes
        field.set_input_files(str(path), timeout=10_000)
        t0 = time.monotonic()
        limit, _ = self._deadline(3 * budget_s)
        watch, reading = ChipWatch(), None
        while True:
            reading = self._read_chip(page, path.name)
            watch.feed(chip_state(reading, path.name), time.monotonic() - t0)
            if watch.state in ChipWatch.FINAL or time.monotonic() - t0 >= limit:
                break
            page.wait_for_timeout(250)
        uploads = [{k: e.get(k) for k in ("method", "url", "status", "ms")}
                   for e in b.since(b.network, mark) if e["method"] in ("POST", "PUT")]
        return {"state": watch.state, "ready_by": watch.ready_by, "pending_seen": watch.pending_seen,
                "seconds": round(time.monotonic() - t0, 1), "label": chip_label(reading), "trace": watch.trace,
                "reading": reading, "uploads": uploads[-20:], "accept": accept}

    def _chip_ready(self, cid: str) -> tuple[bool, str, str]:
        """La puce du cas relue au moment d'envoyer : prête, ou non (son état brut, son libellé)."""
        att = self.conv["attached"][cid]
        reading = self._read_chip(self.browser.page(), att["file"])
        raw = chip_state(reading, att["file"])
        ready = raw == "ready" or (raw == "unmarked" and (att["state"] == "ready" or att["pending_seen"]))
        return ready, raw, chip_label(reading)

    def poser_question(self, args: dict, ev: Evidence) -> dict:
        """Envoyer la question du cas mot pour mot et attendre la réponse complète, intercepteur
        armé ; abandon à 3 × le budget du cas, journalisé KO disponibilité."""
        cid = args["case"]
        case = self._case(cid)
        if case["kind"] in ("model", "web") and "question" not in case:
            # A configuration case (PROCEDURE-2 §8.2): nothing to send, it is judged on what
            # choisir_modele or recherche_web observed; poser_question hands that back.
            key, action = ("@model", "choisir_modele") if case["kind"] == "model" else ("@web", "recherche_web")
            seen = self.observations.get(key)
            if not seen:
                raise DriverRefusal("precondition", f"cas de configuration {cid} : {action} d'abord, puis verdict_oracle")
            return {"case": cid, "configuration": True, "answer": seen["answer"], "text": f"{cid} : {seen['answer']}"}
        if case["kind"] not in ("message", "attach_question", "model", "web") or "question" not in case:
            hint = (f" : {case['action']}" if case.get("action")
                    else " : conversation_longue" if case["kind"] == "long" else "")
            raise DriverRefusal("precondition", f"le cas {cid} ({case['kind']}) n'a pas de question{hint}")
        # The composer as it is now, read on screen: every line carries the model and web mode read.
        composer = self._composer_now(self.browser.page())
        if case["kind"] in ("model", "web"):
            if (case["kind"] == "model" and not is_placeholder(case["model"])
                    and not model_matches(case["model"], composer["model"])):
                raise DriverRefusal("precondition", f"choisir_modele {case['model']} d'abord "
                                                    f"(modèle du compositeur : {composer['model']})")
            if case["kind"] == "web" and composer["web"] != case["web"]:
                raise DriverRefusal("precondition", f"recherche_web {case['web']} d'abord "
                                                    f"(recherche web : {composer['web']})")
        if case["kind"] == "attach_question":
            if cid not in self.conv["attached"]:
                raise DriverRefusal("precondition", f"joindre_document {cid} d'abord (pièce jointe : aucune)")
            ready, raw, label = self._chip_ready(cid)
            if not ready:  # the real chip, read now: a question on a chip not ready never leaves
                name = self.conv["attached"][cid]["file"]
                shown = {"chip": raw, "label": None if raw == "failed" else sanitise(label, 120, keep=(name,)),
                         "internal_error_shown": chip_shows_internal(label, name)}
                if raw == "failed":  # E9: its class, never its label (I2)
                    shown["error_class"] = attach_error_class(label, name)
                self.observations.setdefault(cid, {})["not_sent"] = {**shown, "t": now_iso()}
                raise DriverRefusal("precondition", f"pièce jointe pas prête ({raw}) : question non envoyée",
                                    extra=shown)
        before = case.get("same_conversation_as")
        if before and before not in self.conv["asked"] and not self._in_conversation_of(before):
            raise DriverRefusal("precondition", f"{cid} se pose dans la conversation de {before}, pas encore posé")
        self._case_in_hand = cid
        # A new attempt: only the attachment survives from the previous observation, so a
        # dialog or a timeout now is never judged on an older answer.
        attach = (self.observations.get(cid) or {}).get("attach")
        self.observations[cid] = {"attach": attach} if attach else {}
        mark = self.browser.mark()
        self._last_asked = {"case": cid, "mark": mark}  # what happens from here on is this case's
        budget = budget_of(case)
        page = self.browser.page()
        self._ensure_on_app(page)
        conv_before = self._conversation_state(page)  # E1: where the question leaves from
        try:
            obs = self._ask(case["question"], budget, pii=case.get("pii"))
        except AskTimeout as t:
            self.observations.setdefault(cid, {})["timeout"] = {"seconds": round(t.seconds, 1), "t": now_iso(),
                                                                **t.profile}
            # m3: the conversation first, so that the timeout line names it
            conversation = self._note_conversation_quietly(cid, conv_before, page, "poser_question")
            seen = {"composer": composer, **({"conversation": conversation} if conversation else {})}
            if conversation:
                self.observations[cid]["conversation"] = conversation
            if t.notices.get("internal_error_shown"):  # round 2, leakage-1: shown while the question waited
                seen["internal_error_shown"] = self.observations[cid]["internal_error_shown"] = True
            if t.notices.get("raw"):
                ev.json("notices.json", t.notices["raw"])
            flag = {"internal_error_shown": True} if seen.get("internal_error_shown") else {}  # round 3, M-9
            if self._dialog_seen():  # round 4, R-2: no timeout line; _wrap makes it `dialog`, the judge KO sécurité
                raise DriverRefusal("timeout", t.why, extra={"abort": t.profile, **flag}) from None
            line = self._timeout_line(cid, case["nfr"], t, t.why, extra=self._line_extra(cid, seen))
            raise DriverRefusal("timeout", t.why, extra={"journal": line, "abort": t.profile, **flag}) from None
        failure = obs.pop("failure_raw", None)
        notices = obs.pop("notices_raw", None)
        conversation = self._note_conversation(cid, conv_before, page, "poser_question")
        self.conv["asked"].append(cid)
        if case["kind"] == "attach_question" and obs.get("sent_info") is not None:
            # In an attachment, the sent text may not hold the document: then a leak cannot be
            # seen on this channel (PROCEDURE-2 §8.8).
            obs["sent_info"]["document_in_sent"] = document_in_sent(self._resolve(case["file"]),
                                                                    obs.get("sent_to_model"))
        self.observations[cid] = {**obs, "html": None, **({"attach": attach} if attach else {}),
                                  "composer": composer, **({"conversation": conversation} if conversation else {})}
        self._answer_evidence(ev, obs, mark, failure, notices)
        return self._answer_result(obs, cid)

    def envoyer_message(self, args: dict, ev: Evidence) -> dict:
        """Comme poser_question, avec un texte libre (essais exploratoires) ; l'observation va
        sous « @message », hors cas."""
        text = args["text"]
        mark = self.browser.mark()
        page = self.browser.page()
        self._ensure_on_app(page)
        conv_before = self._conversation_state(page)
        try:
            obs = self._ask(text, DEFAULT_BUDGET_S)
        except AskTimeout as t:
            line = self._timeout_line(f"message : {one_line(text, 60)}", [], t, t.why)
            self._note_conversation_quietly("@message", conv_before, page, "envoyer_message")
            raise DriverRefusal("timeout", t.why, extra={"journal": line, "abort": t.profile}) from None
        failure = obs.pop("failure_raw", None)
        notices = obs.pop("notices_raw", None)
        conversation = self._note_conversation("@message", conv_before, page, "envoyer_message")
        self.observations["@message"] = {**obs, "html": None, "text": text,
                                         **({"conversation": conversation} if conversation else {})}
        self._answer_evidence(ev, obs, mark, failure, notices)
        return self._answer_result(obs, "message")

    def lire_reponse(self, args: dict, ev: Evidence) -> dict:
        """Le texte du corps et le HTML de la dernière réponse affichée, l'en-tête en champs."""
        got = self.browser.call(ANSWER_JS, {"assistant": SELECTORS["assistant_message"], "body": SELECTORS["answer_body"]})
        if got["html"]:
            ev.html("dom.html", got["html"])
        guard = parse_guard(got["card"], got["text"], got["body"])
        answer = got["text"].strip()
        internal = answer_shows_internal(answer)  # round 2, leakage-5: the raw text stays in dom.html
        guard_text = guard["guard_text"]
        if guard_text and text_shows_internal(guard_text):
            guard_text = sanitise(guard_text, 400)
        return {"answer": sanitise(answer, 4000) if internal else answer, "model": guard["model"], "guard_text": guard_text,
                "pii_detected": guard["pii_detected"], "body_found": got["body"], "internal_error_shown": internal,
                "text": (sanitise(answer, 200) if internal else one_line(answer)) or "aucune réponse affichée"}

    def capturer_flux(self, args: dict, ev: Evidence) -> dict:
        """Ce qui s'est passé depuis le dernier appel : lignes du flux (window.__ev), console,
        requêtes en échec (statut ≥ 400 ou coupées), hôtes tiers contactés (relevés, pas
        bloqués)."""
        b = self.browser
        all_ev = b.call("() => window.__ev || []")
        start = self.flux["ev"] if self.flux["ev"] <= len(all_ev) else 0
        events, self.flux["ev"] = all_ev[start:], len(all_ev)
        mark, self.flux["mark"] = self.flux["mark"], b.mark()
        capture = b.capture(mark)
        failed = [e for e in capture["requests"] if (e.get("status") or 0) >= 400 or e.get("failure")]
        third = capture["third_party_hosts"]
        ev.json("network.json", {**capture, "events": events, "failed": failed}, cap=NETWORK_CAP)

        def clean_line(text):  # round 2, leakage-5: the raw lines stay in network.json
            return sanitise(text, 400) if text_shows_internal(text) else text

        shown_events = [[t, clean_line(line)] for t, line in events[-200:]]
        shown_console = [{**c, "text": clean_line(c.get("text"))} for c in capture["console"][-100:]]
        return {"events": shown_events, "console": shown_console, "failed": failed[-100:],
                "third_party_hosts": third,
                "text": (f"{len(events)} lignes de flux, {len(capture['console'])} messages console, "
                         f"{len(failed)} requêtes en échec, {len(third)} hôtes tiers"
                         + (f" ({', '.join(h['host'] for h in third[:5])})" if third else ""))}

    def _late_events(self) -> None:
        """Ce qui survient depuis le dernier poser_question appartient encore à ce cas : le
        livrer (si le navigateur tourne), puis le porter dans son observation avant tout
        jugement (verdict_oracle, exporter_journal). Ruling R20 :
        - un dialogue, pendant l'action ou après la réponse, rend le cas KO sécurité ; après
          la réponse, il est noté « après la réponse, +N s » ;
        - une navigation hors liste pendant l'action rend le cas KO sécurité ; après la fin
          de l'action, ce n'est qu'une note (une reconnexion de l'opérateur n'est pas un
          défaut du produit)."""
        b, last = self.browser, self._last_asked
        if not last:
            return
        if b.started():
            try:
                b.settle()
            except PWError as e:
                log.warning("late events not delivered: %s", e)
        obs = self.observations.setdefault(last["case"], {})
        end, end_mono, until = last.get("end"), last.get("end_mono"), last.get("until")

        def after(e) -> float | None:
            return round(e["mono"] - end_mono, 1) if end is not None and e["seq"] > end else None

        for d in b.dialog_log:
            if d["seq"] in self._app_dialogs or (until is not None and d["seq"] > until):  # round 2, verdicts-5
                continue
            if d["seq"] > last["mark"] and "dialog" not in obs:
                shown, internal = harness_text(d["message"], self._titles)  # leakage-2
                obs["dialog"] = {"type": d["type"], "message": shown, "t": d["t"]}
                if internal:
                    obs["internal_error_shown"] = True
                if after(d) is not None:
                    obs["dialog"]["after_s"] = after(d)
                if obs.get("verdict"):  # robustness-5: never a reason to correct the line already written
                    obs["dialog"]["after_verdict"] = True
        for n in b.since(b.offlist, last["mark"]):
            if until is not None and n["seq"] > until:
                continue
            key = "offlist" if after(n) is None else "offlist_after"
            if key not in obs:
                obs[key] = {"host": n["host"], "url": n["url"], "t": n["t"]}
                if key == "offlist_after":
                    obs[key]["after_s"] = after(n)

    def verdict_oracle(self, args: dict, ev: Evidence) -> dict:
        """evaluer.py sur la dernière observation du cas, puis une ligne au journal (annexe C).
        Un cas déjà journalisé rend son verdict (replayed) et n'ajoute rien. Le jugement lui-même est
        `_judge`, que scripts/recette/correct.py rejoue hors ligne."""
        cid = args["case"]
        case = self._case(cid)
        self._late_events()
        done = self._journaled(cid)
        if done is not None:
            r = (self.observations.get(cid) or {}).get("verdict") or {}
            return {"case": cid, "verdict": done["verdict"], "missing": r.get("missing", []),
                    "forbidden": r.get("forbidden", []), "leaks": r.get("leaks", []),
                    "span_issues": r.get("span_issues", []), "replayed": True,
                    "text": json.dumps(done, ensure_ascii=False)}
        j = self._judge(case)
        line = self._journal_add(cid, j["verdict"], j["timing_s"], j["evidence"], j["nfr"], j["pipeline_ms"],
                                 j["extra"])
        self._record_verdict(case, j, line)
        ev.add("journal.jsonl", (line + "\n").encode("utf-8", "replace"))
        r = j["r"]
        result = {"case": cid, "verdict": r["verdict"], "missing": r["missing"], "forbidden": r["forbidden"],
                  "leaks": r["leaks"], "span_issues": r["span_issues"], "replayed": False, "text": line}
        if j["extra"].get("internal_error_shown"):  # I3: the harness reports it once per level
            result["internal_error_shown"] = True
        if j["extra"].get("others_gone"):  # round 2, R2-2: surfaced to the human at once
            result["cascade_delete"] = j["extra"]["others_gone"]
        return result

    # -- le jugement (v2.3 : séparé de verdict_oracle, rejouable hors ligne) -------------------------
    def _next_seq(self) -> int:
        """Le numéro suivant de la séquence des observations, gardé avec elles (v2.3)."""
        self.observations["@seq"] = int(self.observations.get("@seq") or 0) + 1
        return self.observations["@seq"]

    def _shared(self, key: str, obs: dict) -> dict:
        """Une observation partagée (@load, @list, @model, @web), numérotée : `_fresh_for` la compare aux
        cas sondés."""
        obs["seq"] = self._next_seq()
        self.observations[key] = obs
        return obs

    def _fresh_for(self, cid: str, obs: dict) -> bool:
        """Une observation partagée juge-t-elle encore `cid` ? Faite pour un cas (`case`, par une action qui
        le nomme), elle ne juge que lui ; un cas qui se joue par sa propre action (`action`) ne se juge que
        sur une observation faite pour lui (revue de la v2.3, m5 : M8-5 jamais sur la recherche de M8-4).
        Sinon, oui tant qu'elle n'a jugé aucun cas ; une fois épuisée (`judged`, ou un verdict sans `judged`,
        celui d'un pilote v2.2), seulement un cas sondé (verdict_oracle refusé faute d'observation) avant
        qu'elle soit faite : N7-1 et N7-2 se jugent sur une même liste, M8-4 jamais sur celle de M8-2, M8-7
        jamais sur le choix de modèle de M8-1."""
        bound = obs.get("case")
        if bound is not None and bound != cid:
            return False
        if bound is None and (self.cases.get(cid) or {}).get("action"):
            return False
        if not obs.get("judged") and not obs.get("verdict"):
            return True
        probe = (self.observations.get("@probes") or {}).get(cid)
        return probe is not None and probe < (obs.get("seq") or 0)

    def _judge(self, case: dict, offline: bool = False) -> dict:
        """Le jugement de verdict_oracle, sans rien écrire : evaluer.py sur l'observation du cas et les
        contrôles de sa sorte. Rend le verdict, la preuve, les ENF, pipeline_ms et les champs de la ligne
        (`extra`), avec l'observation jugée et sa clé. Une observation partagée ne juge qu'un cas
        (`_fresh_for`). Sans observation : refus `precondition`, le cas reste à faire, et il est noté sondé.
        `offline` (scripts/recette/correct.py) : rejouer un cas journalisé sur l'observation gardée (pour
        une observation partagée, l'instantané gardé sous le cas à son verdict, `observed`), sans fraîcheur
        ni âge de page de maintenance ; ValueError quand il n'y a rien à rejouer."""
        cid = case["id"]
        key = {"load": "@load", "list": "@list"}.get(case["kind"], cid)
        if case["kind"] in ("model", "web") and "question" not in case:  # a configuration case
            key = "@model" if case["kind"] == "model" else "@web"
        own = self.observations.get(cid) or {}
        if offline and key != cid:
            obs = own.get("observed")
            if obs is None:
                why = ("pilote antérieur à la v2.3" if own else
                       "aucune observation du cas dans ce fichier d'observations, mis de côté à une reprise ?")
                raise ValueError(f"{cid} : jugé sur {key} sans instantané gardé sous le cas ({why}) : non rejouable "
                                 "hors ligne")
        else:
            obs = self.observations.get(key) or {}
            if key != cid and obs and not self._fresh_for(cid, obs):
                obs = {}  # it judged another case already: never a second time
        asked = "answer" in obs
        attach = obs.get("attach") or {}
        # m4: a dialog or an off-list navigation during the case's own action (a rename, a delete, a search)
        # is kept under the case, never on the shared observation
        dialog = obs.get("dialog") or (own.get("dialog") if key != cid else None)
        offlist = obs.get("offlist") or (own.get("offlist") if key != cid else None)
        if offline and dialog and dialog.get("after_verdict"):  # round 2, robustness-5: added after the verdict
            dialog = None
        untested = own.get("untested")  # round 2, R2-1: the pilot's own guard refused, before any irreversible click
        nfr = case["nfr"]
        not_sent = obs.get("not_sent")
        maint = own.get("maintenance")
        if maint and not offline and time.time() - maint.get("epoch", 0) > MAINTENANCE_FRESH_S:
            maint = None  # an old maintenance never stands for a case tried again later
        if untested and not dialog and not offlist:
            # Round 2, R2-1: what the pilot declined to do is never KO against MaskAI: NON TESTÉ, without NFR.
            r = {"verdict": "NON TESTÉ", "notes": [f"refus du pilote : {untested['reason']}"]}
            nfr = []
            obs = {k: v for k, v in own.items() if k in ("internal_error_shown", "notices")}
        elif maint and not dialog and not offlist:
            # The app showed its maintenance page when this case was tried: KO disponibilité.
            r = {"verdict": "KO", "notes": ["service en maintenance (page de maintenance affichée)"]}
            nfr = nfr_union(nfr, ["disponibilite"])
            obs = {k: v for k, v in obs.items() if k in ("verdict",)}  # judged on the maintenance alone
        elif (not asked and not dialog and not offlist
                and (not_sent or (attach and attach.get("state") not in ("ready", "attached")))):
            # E9: a failed chip is named by its class, never by its raw label (an observation of pilot v2.2
            # kept the raw label: its class is read from it here, and the label never reaches the line)
            name = Path(case.get("file") or "").name
            label = sanitise((not_sent or {}).get("label") or attach.get("label") or "", 120, keep=(name,)) or "sans puce"
            if attach.get("state") == "failed" and not attach.get("error_class"):
                attach = {**attach, "error_class": attach_error_class(attach.get("label"), name), "label": label}
                obs = {**obs, "attach": attach}
            if not_sent and not_sent.get("chip") == "failed" and not not_sent.get("error_class"):
                not_sent = {**not_sent, "error_class": attach_error_class(not_sent.get("label"), name), "label": label}
                obs = {**obs, "not_sent": not_sent}
            if not_sent:
                what = not_sent.get("error_class") or label
                note = (f"question non envoyée (précondition) : pièce jointe {not_sent['chip']} ({what})"
                        + (f" après {attach['seconds']} s" if attach.get("seconds") is not None else ""))
            elif attach.get("state") == "failed":
                note = f"pièce jointe en échec ({attach['error_class']})"
            else:
                note = f"pièce jointe pas prête : {attach.get('state')} ({label}) après {attach.get('seconds')} s"
            r = {"verdict": "KO", "notes": [note]}
            nfr = nfr_union(nfr, ["disponibilite"])
        elif not asked and not dialog and not offlist:
            if offline:
                raise ValueError(f"{cid} : aucune observation jugeable sous {key} dans ce fichier d'observations")
            hint = {"load": "mesurer_chargement", "list": "lister_conversations",
                    "concurrent": "charge_concurrente", "long": "conversation_longue"}.get(case["kind"], "poser_question")
            hint = case.get("action") or {"@model": "choisir_modele", "@web": "recherche_web"}.get(key, hint)
            self._last_verdict_probe = cid  # still to do: a maintenance page next is this case's
            self.observations.setdefault("@probes", {})[cid] = self._next_seq()
            raise DriverRefusal("precondition", f"aucune observation pour {cid} : {hint} d'abord")
        elif asked:
            oracle = {"id": cid, "must": case["must"], "must_not": case["must_not"], "pii": case["pii"]}
            seen = {"answer": obs.get("answer"), "timing_s": obs.get("timing_s")}
            in_doc = (obs.get("sent_info") or {}).get("document_in_sent", True)
            if obs.get("sent_to_model") is not None and in_doc is True:
                seen["sent"] = obs["sent_to_model"]
            r = evaluer.evaluate_case(oracle, seen, budget_of(case))
            nfr = nfr_union(nfr, r.get("nfr") or [])  # a leak to the model is KO sécurité
            if r["verdict"] == "NON TESTÉ" and not dialog and not offlist:  # échec silencieux
                r["verdict"] = "KO"
                failure = obs.get("failure") or {}
                why = [w for w in ("aucune nouvelle carte de réponse" if obs.get("new_answer") is False else None,
                                   f"dernier nœud {failure['last_node']}" if failure.get("last_node") else None) if w]
                r["notes"].append("réponse vide : échec silencieux" + (f" ({' ; '.join(why)})" if why else "")
                                  + (f" : {failure['error_class']}" if failure.get("error_class") else ""))
                nfr = nfr_union(nfr, ["disponibilite"])
            if obs.get("body_found") is False and obs.get("answer"):
                r["notes"].append("corps de réponse introuvable : texte de la carte entière, en-tête compris")
            if answer_shows_internal(obs.get("answer")):  # m11: noted, and the answer below is sanitised
                r["notes"].append("la réponse affiche une erreur interne (expurgée ici)")
            if obs.get("display_stable") is False:
                r["notes"].append("affichage encore en mouvement à la capture")
            if obs.get("sent_to_model") is not None and in_doc is not True:
                r["notes"].append("le texte envoyé ne montre pas le document joint : fuite non vérifiable sur ce canal"
                                  if in_doc is False else
                                  "document joint illisible en texte par le driver : fuite non vérifiable sur ce canal")
            nfr = self._kind_checks(case, obs, r, nfr)
            if case.get("replay_of"):
                self._replay_note(case, obs, r)
        else:
            r = {"verdict": "KO", "notes": []}
        for key_ in ("missing", "forbidden", "leaks", "span_issues", "notes"):
            r.setdefault(key_, [])
        if offlist:  # la page a quitté le périmètre pendant l'action : KO sécurité
            r["verdict"] = "KO"
            r["notes"].insert(0, f"navigation hors périmètre : {offlist['host']}")
            nfr = nfr_union(nfr, ["securite"])
        if dialog:  # un dialogue inattendu (pendant l'action ou après la réponse) : KO sécurité
            r["verdict"] = "KO"
            when = f" (après la réponse, +{dialog['after_s']:g} s)" if "after_s" in dialog else ""
            message = harness_text(dialog["message"], self._titles, keep=self._keep_names())[0]  # round 2, leakage-2: an older raw message
            r["notes"].insert(0, f"dialogue inattendu ({dialog['type']}) : {message}{when}")
            nfr = nfr_union(nfr, ["securite"])
        gone = obs.get("others_gone")
        if gone:  # round 2, R2-2: another conversation left the sidebar during the action: KO sécurité
            r["verdict"] = "KO"
            r["notes"].insert(0, f"{gone} autre(s) conversation(s) disparue(s) pendant l'action")
            nfr = nfr_union(nfr, ["securite"])
        if obs.get("offlist_after"):  # après la fin de l'action : une note, pas un KO (R20)
            o = obs["offlist_after"]
            r["notes"].append(f"navigation hors périmètre après la réponse : {o['host']} (+{o['after_s']:g} s)")
        return {"key": key, "obs": obs, "r": r, "verdict": r["verdict"], "timing_s": obs.get("timing_s"),
                "evidence": self._summary(r, obs), "nfr": nfr, "pipeline_ms": obs.get("pipeline_ms"),
                "extra": self._line_extra(cid, obs, own if key != cid else None)}

    def _record_verdict(self, case: dict, j: dict, line: str | None = None) -> None:
        """Après la ligne : le verdict noté sur l'observation qui l'a fait. Une observation partagée est
        alors épuisée (`judged`), et son instantané gardé sous le cas (`observed`, pour un rejeu hors
        ligne) ; le choix du modèle ou du mode web fait pour un cas `model` ou `web` à question est épuisé
        aussi : un cas de configuration ne se juge jamais sur lui. Round 2 (robustness-5) : le verdict porte
        l'empreinte de sa ligne (`line_sha256`), et correct.py ne rejoue que l'observation qui la porte."""
        cid, key, obs, r = case["id"], j["key"], j["obs"], j["r"]
        record = {k: r[k] for k in ("verdict", "missing", "forbidden", "leaks", "span_issues", "notes")}
        if line:
            record["line_sha256"] = sha256_text(line)
        if key != cid:
            shared = self.observations.get(key)
            if obs and shared is obs:  # the shared observation that judged it, and only that one
                shared["verdict"] = record
                shared["judged"] = cid
            own = self.observations.get(cid) or {}
            kept = {k: own[k] for k in ("dialog", "offlist", "untested", "internal_error_shown")
                    if k in own}  # m4, round 2: replayable offline
            self.observations[cid] = {"verdict": record, "from": key,
                                      "observed": {k: v for k, v in obs.items() if k not in ("verdict", "judged")},
                                      **kept}
            return
        self.observations.setdefault(key, obs)["verdict"] = record
        if case["kind"] in ("model", "web"):
            shared = self.observations.get("@model" if case["kind"] == "model" else "@web")
            if isinstance(shared, dict) and shared.get("answer") and not shared.get("judged"):
                shared["judged"] = cid

    def _line_extra(self, cid: str, obs: dict, own: dict | None = None) -> dict:
        """Ce que porte une ligne en plus du format : la fin d'affichage, le modèle et le mode web lus et,
        depuis la v2.3, l'état du micro (`config`, PROCEDURE-2 §8.4 ; E4), l'empreinte du texte envoyé
        (replay_of). Depuis la v2.3 : sur la ligne d'une question, `sent_source` (nul, avec la raison en
        `sent_rejected`), `sent_tokens` et `pii_declared` (E7) ; la pièce jointe (`attach`, avec sa liste
        `accept`, E6, jamais son libellé brut) ; `error_class`, d'une puce en échec (E9) ou d'un échec
        silencieux (E10 ; son extrait expurgé ne va qu'à failure.json, revue de la v2.3, I2) ;
        `internal_error_shown`, une erreur interne montrée à l'utilisateur par la puce, la page ou la réponse
        (revue I3 et m11), dès qu'il y a de quoi le dire ; la conversation du registre (E1), ou celles d'une
        charge concurrente (`conversations`, m3) ; et toujours `modals_closed`, les modales connues que la
        garde a fermées pendant les actions du cas (E5)."""
        extra = {}
        if obs.get("display_s") is not None:
            extra["display_s"] = obs["display_s"]
        composer, guard = obs.get("composer"), obs.get("guard") or {}
        if composer or guard.get("model"):
            extra["config"] = {"model": (composer or {}).get("model"), "web": (composer or {}).get("web"),
                               "banner": guard.get("model"), "mic": (composer or {}).get("mic")}
        if obs.get("sent_to_model") is not None:
            extra["sent_sha256"] = sent_digest(obs["sent_to_model"])
        if "sent_info" in obs or "guard" in obs:  # a question was asked (E7)
            info = obs.get("sent_info") or {}
            seen = obs.get("sent_to_model") is not None
            extra["sent_source"] = info.get("source") if seen else None
            if not seen:
                extra["sent_rejected"] = (info.get("rejected") or info.get("popover_rejected")
                                          or ("pas de carte de réponse" if obs.get("new_answer") is False
                                              else "texte envoyé non observé"))
            extra["sent_tokens"] = token_inventory(obs.get("sent_to_model"))
            extra["pii_declared"] = guard.get("pii_count")
        attach = obs.get("attach")
        if attach:
            extra["attach"] = {k: attach.get(k) for k in ("state", "ready_by", "seconds", "accept", "error_class",
                                                          "internal_error_shown")}
        failure = obs.get("failure") or {}
        error = ((obs.get("not_sent") or {}).get("error_class") or (attach or {}).get("error_class")
                 or failure.get("error_class"))
        if error:
            extra["error_class"] = error
        flags = [x["internal_error_shown"] for x in (attach or {}, obs.get("not_sent") or {}, failure)
                 if "internal_error_shown" in x]
        flags += [True for t in obs.get("turns") or [] if t.get("internal_error_shown")]
        if answer_shows_internal(obs.get("answer")):
            flags.append(True)
        # round 2: an error notice of the page during the question or the action (leakage-1), a dialog's or a
        # window's text that showed one (leakage-2), on the observation or under the case
        for x in (obs, obs.get("notices") or {}, own or {}):
            if "internal_error_shown" in x:
                flags.append(bool(x["internal_error_shown"]))
        if flags:
            extra["internal_error_shown"] = any(flags)
        if obs.get("conversation"):
            extra["conversation"] = obs["conversation"]
        if obs.get("conversations"):
            extra["conversations"] = obs["conversations"]
        if obs.get("others_gone"):  # round 2, R2-2: the count, never their titles
            extra["others_gone"] = obs["others_gone"]
        extra["modals_closed"] = list((self.observations.get("@modals") or {}).get(cid) or [])
        return extra

    def _replay_note(self, case: dict, obs: dict, r: dict) -> None:
        """Le second passage d'un cas (replay_of) : le verdict du premier, le texte envoyé comparé à la
        numérotation des jetons près, et les entités masquées des deux passages comparées : c'est sur elles
        que le juge applique le point 5 du contrat (non-déterminisme). Revue de la v2.3, m6 : les entités sont
        lues dans les deux textes envoyés contre leurs questions (masked_entities ; le texte du passage a,
        celui de son observation, quand son empreinte est celle de sa ligne) ; sans eux, les types seuls
        (`sent_tokens` des deux lignes, E7), et la note dit « types » : des types identiques ne prouvent pas
        des entités identiques."""
        first = self._journaled(case["replay_of"])
        if first is None:
            r["notes"].append(f"passage a ({case['replay_of']}) pas encore journalisé")
            return
        note = f"passage a ({case['replay_of']}) : {first['verdict']}"
        mine = sent_digest(obs.get("sent_to_model"))
        if mine and first.get("sent_sha256"):
            note += " ; texte envoyé identique" if mine == first["sent_sha256"] else " ; texte envoyé différent"
        a_obs = self.observations.get(case["replay_of"]) or {}
        a_sent = a_obs.get("sent_to_model")
        if a_sent is not None and first.get("sent_sha256") and sent_digest(a_sent) != first["sent_sha256"]:
            a_sent = None  # not the text passage a's line was judged on
        ents_a = masked_entities(a_sent, (self.cases.get(case["replay_of"]) or {}).get("question"))
        ents_b = masked_entities(obs.get("sent_to_model"), case.get("question"))
        if ents_a is not None and ents_b is not None:
            note += " ; " + entity_note(ents_a, ents_b)
        elif "sent_tokens" in first:
            note += " ; " + inventory_note(first["sent_tokens"], token_inventory(obs.get("sent_to_model")))
        else:
            note += " ; types masqués non comparables (passage a journalisé sans ses jetons)"
        r["notes"].append(note)

    @staticmethod
    def _kind_checks(case: dict, obs: dict, r: dict, nfr: list) -> list:
        """Ce que les cas model, web et long vérifient en plus de l'oracle (v2) ; rend les ENF. Un
        cas de configuration (sans question) n'a que son oracle, sur `@model` ou `@web`."""
        kind = case["kind"]
        if kind in ("model", "web") and "question" not in case:
            return nfr
        if kind == "model":
            seen = (obs.get("guard") or {}).get("model")
            chosen = (obs.get("composer") or {}).get("model") or case["model"]
            # the model read on the composer when the question left: the label the harness chose,
            # even for « <modele-B> », a model read in the list at run time
            if not model_matches(chosen, seen):
                r["verdict"] = "KO"
                r["notes"].insert(0, f"réponse signée « {seen or 'sans bannière'} », modèle choisi « {chosen} »")
                nfr = nfr_union(nfr, ["conformite"])
            else:
                r["notes"].append(f"modèle choisi « {chosen} », réponse signée « {seen} »")
        elif kind == "web":
            posted = web_field(obs.get("sent"))
            web_ms = (obs.get("pipeline_ms") or {}).get("web_search")
            if posted is not None and posted != case["web"]:
                r["verdict"] = "KO"
                r["notes"].insert(0, f"le POST porte web_search={posted}, recherche web choisie {case['web']}")
                nfr = nfr_union(nfr, ["conformite"])
            elif case["web"] == "off" and web_ms is not None and web_ms >= WEB_OFF_MAX_MS:
                r["verdict"] = "KO"
                r["notes"].insert(0, f"étape web_search de {web_ms} ms, recherche web coupée")
                nfr = nfr_union(nfr, ["conformite"])
            r["notes"].append(f"recherche web {case['web']} ; POST web_search={posted or 'absent'} ; étape web_search "
                              + (f"{web_ms} ms" if web_ms is not None else "absente"))
        elif kind == "long" and obs.get("turns"):
            turns = obs["turns"]
            empty = [str(t["turn"]) for t in turns if not t.get("answer")]
            if empty:
                r["verdict"] = "KO"
                r["notes"].insert(0, f"tours sans réponse : {', '.join(empty)}")
                nfr = nfr_union(nfr, ["disponibilite"])
            slowest = max(turns, key=lambda t: t["total_s"] or 0)
            r["notes"].append(f"{len(turns)} tours ; tour le plus lent : {slowest['turn']} en {slowest['total_s']} s ; "
                              f"tour 1 en {turns[0]['total_s']} s, tour {len(turns)} en {turns[-1]['total_s']} s")
        return nfr

    @staticmethod
    def _summary(r: dict, obs: dict) -> str:
        parts = []
        if r["missing"]:
            parts.append("manque : " + ", ".join(r["missing"]))
        if r["forbidden"]:
            parts.append("interdit : " + ", ".join(r["forbidden"]))
        if r["leaks"]:
            parts.append("PII en clair vers le modèle : " + ", ".join(r["leaks"]))
        if r["span_issues"]:
            parts.append("débordements : " + ", ".join(r["span_issues"][:4]))
        parts += r["notes"]
        if not parts:
            parts.append("valeurs attendues présentes")
        model = (obs.get("guard") or {}).get("model")
        if model:
            parts.append(f"modèle {model}")
        if obs.get("answer"):
            answer = obs["answer"]
            shown = sanitise(answer, 160) if answer_shows_internal(answer) else one_line(answer, 160)
            parts.append(f"réponse : « {shown} »")
        return " ; ".join(parts)

    def lister_conversations(self, args: dict, ev: Evidence) -> dict:
        """N7 : les titres de la barre latérale. Avec `case` (v2.3, E2) : un cas qui porte une question est
        une recherche ⌘K (_search) ; sans question, la liste, comme sans `case` (N7, M8-2). Un cas `list` qui
        se joue par une autre action (renommer, supprimer) est refusé, avant tout clic."""
        page = self.browser.page()
        self._ensure_on_app(page)
        cid = args.get("case")
        case = self._case(cid) if cid else None
        if case is not None:
            if case["kind"] != "list":
                raise DriverRefusal("precondition", f"le cas {cid} ({case['kind']}) n'est pas un cas de liste")
            self._played_by(case, "lister_conversations")
            self._take_case(cid)
        composer = self._composer_now(page)
        if case is not None and (case.get("question") or "").strip():
            return self._search(page, case, composer, ev)
        titles = self.browser.call(TITLES_JS, {"items": SELECTORS["conversation_items"]})
        text = f"{len(titles)} conversation{'s' if len(titles) != 1 else ''} : " + " | ".join(titles[:30])
        ev.screenshot(page, "screen.png")
        self._shared("@list", {"answer": text, "timing_s": None, "t": now_iso(), "composer": composer,
                               **({"case": cid} if cid else {})})
        return {"titles": titles, "count": len(titles), "text": one_line(text, 300)}

    # -- les fonctions autour du message (v2.3, E2 à E5, E11) -------------------------------------
    @staticmethod
    def _played_by(case: dict, action: str) -> None:
        """Un cas qui nomme son action (`action`, v2.3) ne se joue que par elle : supprimer_conversation sur
        le cas du renommage est refusé, avant tout clic. renommer_conversation, supprimer_conversation et
        ouvrir_avis exigent un cas qui les nomme."""
        named = case.get("action")
        if named == action:
            return
        if named:
            raise DriverRefusal("precondition", f"le cas {case['id']} se joue par {named}, pas par {action}")
        if action in ("renommer_conversation", "supprimer_conversation", "ouvrir_avis"):
            raise DriverRefusal("precondition", f"le cas {case['id']} ne nomme pas l'action {action} : rien n'est touché")

    def _wait_for(self, page, probe, limit_s: float):
        """La première valeur vraie de `probe()`, essayée toutes les 100 ms, au plus `limit_s` ; None sinon.
        Un délai de la page compte pour une valeur fausse, sauf avec un dialogue en attente."""
        deadline = time.monotonic() + limit_s
        while True:
            try:
                got = probe()
            except PWTimeout:
                if self.browser.pending():
                    raise
                got = None
            if got:
                return got
            if time.monotonic() >= deadline:
                return None
            page.wait_for_timeout(100)

    def _restore_page(self, page, url: str, record: dict) -> None:
        """Remettre la page où l'action l'a trouvée, si un clic l'a menée ailleurs (une entrée de recherche
        ou d'avis qui serait un lien) : le cas suivant attend le compositeur."""
        if urlsplit(page.url).path != urlsplit(url).path:
            record["moved_to"] = short_url(page.url)
            self._goto(page, url)

    def _identified_entry(self, page, sels: list, words: str, sidebar: bool) -> tuple[dict | None, list]:
        """L'entrée positivement identifiée (la recherche ⌘K, « Votre avis » ; revue de la v2.3, I4 et I5) : le
        premier élément visible d'un de ses sélecteurs, dans l'ordre, qui passe ENTRY_OK_JS (jamais dans une
        carte de réponse, une fenêtre, un menu ni le compositeur, jamais le bouton de dictée ; dans la barre
        latérale si `sidebar` ; son texte ou son libellé suivant `words` ; round 2, click-targets-1 : jamais un
        lien de conversation, ni rien dans la ligne ou la liste d'une conversation). Lue en un appel borné
        (round 2, robustness-4 : locator.count() et is_visible() ne reviennent jamais avec un dialogue ouvert).
        Rend (l'entrée, []) ou (None, les candidats que la page montre : pour avis.json, search.json et la
        sonde, jamais pour le harnais, R2-4). Rien n'est cliqué ici."""
        b = self.browser
        try:
            got = b.call(ENTRY_JS, self._entry_arg(sels, words, sidebar), page=page, timeout_ms=3000)
        except PWTimeout:
            if b.pending():
                raise
            got = None
        if got and got.get("selector"):
            return got, []
        try:
            seen = b.call(ENTRY_CANDIDATES_JS, {"words": words, "convPath": SELECTORS["conversation_path"],
                                                "row": SELECTORS["conversation_row"]}, page=page, timeout_ms=2000) or []
        except PWTimeout:
            if b.pending():
                raise
            seen = []
        return None, seen

    @staticmethod
    def _entry_arg(sels: list, words: str, sidebar: bool) -> dict:
        return {"cands": [css_and_text(s) for s in sels], "mic": SELECTORS["mic_button"], "textarea": SELECTORS["textarea"],
                "model": ", ".join(SELECTORS["model_button"]), "sidebar": SELECTORS["sidebar"] if sidebar else None,
                "words": words, "convPath": SELECTORS["conversation_path"], "row": SELECTORS["conversation_row"]}

    def _click_entry(self, page, sels: list, words: str, sidebar: bool) -> bool:
        """Cliquer l'entrée identifiée, l'élément même qui a passé l'identification (round 2, click-targets-1 :
        jamais un sélecteur résolu de nouveau) ; False si elle a disparu."""
        el = self.browser.element(ENTRY_EL_JS, self._entry_arg(sels, words, sidebar), page=page, timeout_ms=3000)
        if el is None:
            return False
        try:
            el.click(timeout=5000)
        finally:
            el.dispose()
        self._after_click(page)
        return True

    def _page_notices(self, page, mark: bool = False) -> list:
        """Les avis que la page montre maintenant (ligne d'état, alertes, toasts), en paires [genre, texte] ; ni
        une alerte ni un toast déjà là au repère. `mark` : poser le repère d'abord (au début d'une action)."""
        try:
            if mark:
                self.browser.call(MARK_NOTICES_JS, {"notices": SELECTORS["notices"]}, page=page, timeout_ms=2000)
            return self.browser.call(NOTICES_JS, {"status": SELECTORS["input_status"], "notices": SELECTORS["notices"],
                                                  "fresh": True}, page=page, timeout_ms=2000) or []
        except PWTimeout:
            if self.browser.pending():
                raise
            return []

    def _new_error_notices(self, page, before: list) -> dict | None:
        """Les avis d'erreur apparus pendant une action de M8 (round 2, leakage-1) : leur texte brut pour la preuve,
        et s'ils montrent une erreur interne."""
        said = [n for n in self._page_notices(page) if n not in (before or [])]
        errs = error_notices(said)
        return {"raw": errs, "internal": notices_internal(said, self._keep_names())} if errs else None

    def _open_search(self, page, arg: dict, record: dict) -> bool:
        """La palette ⌘K ouverte par le driver : au clavier (ControlOrMeta+K), à défaut par l'entrée de la
        barre latérale, seulement identifiée (_identified_entry ; revue de la v2.3, I4 : jamais un bouton
        deviné, le bouton de dictée moins que tout autre), cliquée elle-même. Non identifiée, rien n'est
        cliqué : record["entry"] reste None et record["candidates"] dit ce que la page montrait. Round 2
        (click-targets-7) : ce qui était ouvert avant la touche ou le clic n'est jamais la palette. Modale `own`
        « recherche » dès avant la touche ou le clic : laissée ouverte par une action coupée, la garde suivante
        la referme. Rend True si son champ est visible."""
        b = self.browser
        b.call(MARK_BEFORE_JS, {}, page=page)
        b.own_modal = "recherche"
        page.keyboard.press("ControlOrMeta+K")
        self._after_click(page)
        if self._wait_for(page, lambda: b.call(SEARCH_JS, arg, page=page, timeout_ms=2000), SEARCH_OPEN_S):
            record["opened_by"] = "ControlOrMeta+K"
            return True
        b.own_modal = None
        entry, seen = self._identified_entry(page, SELECTORS["search_entry"], SELECTORS["search_words"], sidebar=True)
        record["entry"] = entry
        if entry is None:
            record["candidates"] = seen
            return False
        b.call(MARK_BEFORE_JS, {}, page=page)
        b.own_modal = "recherche"
        if not self._click_entry(page, SELECTORS["search_entry"], SELECTORS["search_words"], True):
            b.own_modal = None
            record["entry"], record["candidates"] = None, []
            return False
        if self._wait_for(page, lambda: b.call(SEARCH_JS, arg, page=page, timeout_ms=2000), SEARCH_OPEN_S):
            record["opened_by"] = entry["selector"]
            return True
        b.own_modal = None
        return False

    def _close_search(self, page, arg: dict) -> bool:
        """Échap, deux fois au plus, jusqu'à ce que la palette n'ait plus de champ visible."""
        b = self.browser
        for _ in range(2):
            page.keyboard.press("Escape")
            if self._wait_for(page, lambda: not b.call(SEARCH_JS, arg, page=page, timeout_ms=2000), 1.5):
                if b.own_modal == "recherche":
                    b.own_modal = None
                return True
        return False

    def _search_results(self, page, arg: dict, before: dict, limit_s: float) -> tuple[dict | None, bool, bool]:
        """Les résultats de la palette (round 2, verdicts-4, robustness-7) : lus quand ils ont changé depuis la
        liste d'avant la frappe (`before` : des conversations récentes, peut-être) et ne bougent plus
        SEARCH_STABLE_S, au plus tôt SEARCH_MIN_S après la frappe ; une liste sans résultat jamais avant
        SEARCH_CAP_S ; au plus limit_s (3 × le budget du cas). Rend (la lecture, retenue immobile, changée) ;
        None : la palette s'est refermée."""
        b = self.browser
        t1 = time.monotonic()
        base = (tuple(before.get("results") or []), bool(before.get("empty")))
        last, since = None, t1
        while True:
            got = b.call(SEARCH_JS, arg, page=page, timeout_ms=2000)
            now = time.monotonic()
            if got is None:
                return None, False, False
            key = (tuple(got["results"]), bool(got["empty"]), got["value"])
            changed = key[:2] != base
            if key != last:
                last, since = key, now
            elif (changed and now - t1 >= SEARCH_MIN_S and now - since >= SEARCH_STABLE_S
                  and (got["results"] or now - t1 >= SEARCH_CAP_S)):
                return got, True, True
            if now - t1 >= limit_s:
                return got, False, changed
            page.wait_for_timeout(150)

    def _search(self, page, case: dict, composer: dict, ev: Evidence) -> dict:
        """v2.3, E2 — la recherche ⌘K d'un cas `list` qui porte une question : la palette ouverte
        (_open_search), la liste d'avant la frappe lue, la question tapée dans son champ et là seulement, les
        résultats lus quand la frappe les a changés et qu'ils ne bougent plus (_search_results), la palette
        refermée par Échap. Un résultat mène à une conversation (SEARCH_JS) : jamais un élément qui répète la
        requête. @list : « recherche « q » : n résultats : t1 | t2 … », ou « résultats inchangés par la frappe »
        (round 2, verdicts-4). Une palette qui ne s'ouvre pas, un champ qui ne prend pas la question :
        l'observation le dit, et l'action refuse `precondition`. Une palette qui ne s'ouvre pas au clavier et
        dont l'entrée n'est pas identifiée (revue de la v2.3, I4) : rien n'est cliqué, le cas est journalisé NON
        TESTÉ « refus du pilote » (round 2, R2-1) ; les candidats ne vont qu'à search.json (R2-4)."""
        b = self.browser
        cid, q = case["id"], case["question"]
        t0 = time.monotonic()
        start = page.url
        arg = {"inputs": SELECTORS["search_input"], "results": SELECTORS["search_result"],
               "empty": SELECTORS["search_empty"], "redact": False, "convPath": SELECTORS["conversation_path"]}
        record = {"case": cid, "query": q, "opened_by": None, "input": None, "results": [], "result": None,
                  "empty": None, "settled": None, "changed": None, "before": None, "closed": None, "seconds": None}
        notices0 = self._page_notices(page, mark=True)
        error, shot, unidentified = None, False, False
        try:
            if not self._open_search(page, arg, record):
                if record.get("entry") is None:
                    unidentified = True
                    seen = record.get("candidates") or []
                    error = ("la recherche ne s'ouvre pas au clavier (ControlOrMeta+K) et son entrée n'est pas "
                             f"identifiée dans la barre latérale : rien n'est cliqué ; {len(seen)} candidat(s) dans "
                             "search.json")
                else:  # round 3, M-3 (R2-1): the identified entry opened nothing the pilot recognises
                    unidentified = True
                    error = ("la recherche ne s'ouvre pas (ni ControlOrMeta+K, ni l'entrée de la barre latérale) : "
                             "aucune palette reconnue")
            else:
                before = b.call(SEARCH_JS, arg, page=page, timeout_ms=2000) or {}
                record["before"] = {"count": before.get("count"), "items": before.get("items"),
                                    "results": before.get("results")}
                field = b.element(SEARCH_INPUT_EL_JS, arg, page=page, timeout_ms=3000)
                if field is None:
                    error = "pas de champ dans la recherche"
                else:
                    try:
                        field.fill(q, timeout=5000)
                    finally:
                        field.dispose()
                    limit, _ = self._deadline(3 * budget_of(case))
                    got, record["settled"], record["changed"] = self._search_results(page, arg, before, limit)
                    if got is None:
                        error = "la recherche s'est refermée avant ses résultats"
                    else:
                        record.update({k: got[k] for k in ("input", "results", "result", "empty", "focused", "value",
                                                           "html", "items", "unknown")})
                        if evaluer.fold(got["value"]).strip() != evaluer.fold(q).strip():
                            error = "le champ de recherche ne porte pas la question"
                        ev.screenshot(page, "screen.png")  # the palette and its results, before it closes
                        shot = True
        except PWTimeout:
            if b.pending() or self._new_dialog():
                raise
            error = "délai d'une lecture de la recherche dépassé"
        finally:
            if record["opened_by"] and not b.pending():
                record["closed"] = self._close_search(page, arg)
            record["seconds"] = round(time.monotonic() - t0, 1)
        self._restore_page(page, start, record)
        notices = self._new_error_notices(page, notices0)
        if notices:
            record["notices"] = notices["raw"]
        n = len(record["results"])
        if not error and n == 0 and (record.get("unknown") or 0) > 0:  # round 3, M-1 (R2-1): the pilot's limit
            unidentified = True
            error = (f"résultats non reconnus ({record['unknown']} élément{'s' if record['unknown'] > 1 else ''}) : "
                     "le pilote ne les relie à aucune conversation")
        if error:
            text = f"recherche « {q} » : {error}"
        elif record.get("changed") is False:
            text = f"recherche « {q} » : résultats inchangés par la frappe ({n} avant et après)"
        else:
            text = (f"recherche « {q} » : {n} résultat{'s' if n > 1 else ''}"
                    + (" : " + " | ".join(record["results"][:30]) if n else ""))
        if not shot:
            ev.screenshot(page, "screen.png")
        ev.json("search.json", record)
        if unidentified:  # I4, R2-1: nothing tried: NON TESTÉ, the candidates in search.json only (R2-4)
            self._untested(cid, error)
            if notices and notices["internal"]:
                self.observations[cid]["internal_error_shown"] = True
            raise DriverRefusal("precondition", f"refus du pilote : {error}",
                                extra={"candidates": len(record.get("candidates") or [])})
        obs = {"answer": text, "timing_s": None, "t": now_iso(), "composer": composer, "case": cid,
               "search": {k: record[k] for k in ("opened_by", "input", "result", "empty", "settled", "changed",
                                                 "closed", "seconds")}}
        if notices and notices["internal"]:
            obs["internal_error_shown"] = True
        self._shared("@list", obs)
        if error:
            raise DriverRefusal("precondition", error, extra={"text": one_line(text, 300)})
        return {"case": cid, "results": record["results"], "count": n, "closed": record["closed"],
                "opened_by": record["opened_by"], "text": one_line(text, 300)}

    def _target(self, case: dict) -> dict:
        """La conversation du registre que le cas nomme par same_conversation_as (E1, E3) : celle que ce cas
        a créée. Sinon un refus `precondition`, avant tout clic ; absente du registre, le cas est journalisé NON
        TESTÉ « refus du pilote » (round 2, R2-1)."""
        cid, owner = case["id"], case.get("same_conversation_as")
        if not owner:
            raise DriverRefusal("precondition", f"le cas {cid} ne nomme pas sa conversation (same_conversation_as)")
        entry = self._registered(owner)
        if entry is None:
            self._refuse_untested(cid, f"la conversation de {owner} n'est pas au registre des conversations créées par "
                                       "la recette : rien n'est touché")
        return entry

    def _refuse_untested(self, cid: str, reason: str, kind: str = "precondition", extra: dict | None = None):
        """Le refus d'une garde du pilote, avant tout clic irréversible (round 2, R2-1) : NON TESTÉ au journal."""
        self._untested(cid, reason)
        raise DriverRefusal(kind, f"refus du pilote : {reason}", extra=extra or {})

    def renommer_conversation(self, args: dict, ev: Evidence) -> dict:
        """v2.3, E3 — renommer la conversation que le cas nomme par same_conversation_as, et elle seule : la
        conversation du registre que ce cas a créée (E1), retrouvée dans la barre latérale par son adresse
        exacte, jamais par son rang ni par son titre seul. Son menu (_open_item_menu), l'entrée « Renommer »
        d'un menu de conversation (menu_entry), la question du cas tapée comme titre dans le champ qui
        s'ouvre (_rename). La barre latérale relue dans @list : « renommage de la conversation de X : titre
        relu « … » ; requête … ; n conversations listées » (leur nombre, jamais leurs titres : round 2, R2-4).
        Refus `precondition` sans aucun clic : un cas qui ne nomme pas cette action, une conversation absente du
        registre, supprimée, ou absente de la barre latérale (round 2 : NON TESTÉ, « refus du pilote : … »).
        Irréversible (reversible: false)."""
        case = self._case(args["case"])
        self._played_by(case, "renommer_conversation")
        if not (case.get("question") or "").strip():
            raise DriverRefusal("precondition", f"le cas {case['id']} n'a pas de titre à donner (question)")
        entry = self._target(case)
        if entry["state"] in GONE_STATES:
            self._refuse_untested(case["id"], f"la conversation de {case['same_conversation_as']} est supprimée "
                                              f"({entry['state']}) : rien à renommer")
        return self._conversation_action(case, entry, "renommer", ev)

    def supprimer_conversation(self, args: dict, ev: Evidence) -> dict:
        """v2.3, E3 — supprimer la conversation que le cas nomme par same_conversation_as, et elle seule (la
        même règle que renommer_conversation) : l'entrée « Supprimer » d'un menu de conversation, puis la
        confirmation de l'application, la modale `own` « suppression », validée par son seul bouton de
        confirmation (_confirm_delete). Le registre la note « supprimée ». Déjà supprimée, ou suppression
        demandée ou confirmée, au registre : rien n'est cliqué ; encore listée, c'est un refus du pilote (round 2 :
        NON TESTÉ), sinon la barre latérale est relue. @list : « suppression de la conversation de X : plus
        listée ; requête … ; n conversations listées » (leur nombre, jamais leurs titres). Irréversible
        (reversible: false)."""
        case = self._case(args["case"])
        self._played_by(case, "supprimer_conversation")
        entry = self._target(case)
        return self._conversation_action(case, entry, "supprimer", ev)

    def _conversation_action(self, case: dict, entry: dict, how: str, ev: Evidence) -> dict:
        """Le renommage ou la suppression de la conversation `entry` du registre, puis la barre latérale relue
        dans @list (le titre relu de cette adresse, ou « plus listée », et le compte des conversations), le
        registre mis à jour, conversation.json. Round 2 :
        - une garde du pilote qui refuse avant tout clic irréversible (pas dans la barre latérale, menu déjà
          ouvert, pas de déclencheur de la ligne, menu non reconnu, confirmation non reconnue et annulée,
          confirmation native) : aucune @list, le cas est journalisé NON TESTÉ « refus du pilote » (R2-1) ;
        - « suppression demandée » est noté au registre avant le clic sur « Supprimer », « suppression
          confirmée » avant le clic de confirmation (registry-6) ;
        - l'état attendu est attendu 3 × le budget du cas depuis le clic, tenu HOLD_S, et son délai va dans
          `timing_s` (verdicts-6) ; « plus listée » exige une requête 2xx (le texte nomme les requêtes) ;
        - une autre conversation disparue (relue après 2 s) rend la ligne KO sécurité, `others_gone` en porte le
          compte, jamais les titres, et le résultat le drapeau `cascade_delete` (R2-2) ;
        - le texte ne liste jamais les titres de la barre latérale, seulement leur nombre (R2-2, R2-4).
        Un échec après le premier clic irréversible : l'observation le dit (le verdict sera KO), et l'action
        refuse `precondition`."""
        b = self.browser
        page = b.page()
        self._ensure_on_app(page)
        cid, owner = case["id"], case["same_conversation_as"]
        self._take_case(cid)
        composer = self._composer_now(page)
        arg = self._conv_arg(entry["href"])
        budget = budget_of(case)
        t0 = time.monotonic()
        record = {"case": cid, "action": how, "conversation": {k: entry.get(k) for k in ("href", "case", "state", "title")},
                  "item": None, "trigger": None, "menu": None, "picked": None, "field": None, "confirmation": None}
        outcome = error = untested = seconds = None
        native = clicked = False
        notices0 = self._page_notices(page, mark=True)
        listed = {p for p, _ in (self._conversation_state(page) or {}).get("items") or []}
        mark = b.mark()
        if how == "supprimer" and entry["state"] in GONE_STATES:  # m2, registry-6: never clicked twice
            if (b.call(ITEM_JS, arg, page=page) or {}).get("found"):  # R2-1: the pilot declines: NON TESTÉ
                untested = f"{entry['state']} au registre et encore listée : la suppression n'est jamais recliquée"
            else:
                outcome = ("déjà supprimée au registre : rien n'est cliqué" if entry["state"] == "supprimée"
                           else f"{entry['state']} au registre : rien n'est cliqué")
        else:
            record["item"] = b.call(ITEM_JS, arg, page=page)
            title = record["item"].get("title")
            if not record["item"].get("found"):
                untested = f"la conversation de {owner} ({entry['href']}) n'est pas dans la barre latérale : rien n'est touché"
            else:
                try:
                    menu = self._open_item_menu(page, arg, record)
                    if menu is None:
                        if record.get("guard"):
                            untested = record["error"]
                        else:
                            error = record.get("error") or "le menu de la conversation ne s'ouvre pas"
                    else:
                        pick, why = menu_entry(menu["items"], how,  # round 6: the trigger's own label
                                               label=(record.get("trigger") or {}).get("trigger_label"))
                        if pick is None:
                            record["closed_menu"] = self._close_menu(page)
                            untested = why
                        else:
                            record["picked"] = pick["label"]
                            # m1, m14: what is open before the click (a dialog, a field) is never what it opened
                            b.call(MARK_BEFORE_JS, {}, page=page)
                            previous = entry["state"]

                            def before():  # registry-6: noted just before the click, never clicked twice
                                nonlocal clicked
                                if how == "supprimer":
                                    self._set_state(entry, "suppression demandée", cid)
                                clicked = True
                            mark, clicked_at = b.mark(), time.monotonic()
                            try:
                                app_native = self._click_menu_entry(page, menu, how, title, before)
                            except EntryGone as gone:  # round 3, I-1 (R2-1): nothing was clicked
                                record["closed_menu"] = self._close_menu(page)
                                untested = str(gone)
                            else:
                                if app_native:
                                    native, untested = True, NATIVE_CONFIRMATION  # robustness-6
                                elif how == "renommer":
                                    outcome, error, seconds = self._rename(page, arg, case["question"].strip(),
                                                                           record, budget, listed)
                                else:
                                    outcome, error, untested, seconds = self._confirm_delete(
                                        page, arg, record, entry, cid, title, previous, clicked_at, budget, listed,
                                        mark)
                                    native = untested == NATIVE_CONFIRMATION  # round 3, I-3: opened late
                except PWTimeout:
                    if b.pending() or self._new_dialog():
                        raise
                    error = "délai d'une lecture de la page dépassé"
                except PWError as e:
                    error = one_line(str(e), 200)
        # leakage-1; never with the native confirmation open (robustness-6): every read would wait for it
        notices = None if native else self._new_error_notices(page, notices0)
        if notices:
            record["notices"] = notices["raw"]
            if notices["internal"]:
                self.observations.setdefault(cid, {})["internal_error_shown"] = True
        if untested:  # R2-1: the pilot declined; nothing for the judge to read but its reason
            record.update(untested=untested, seconds=round(time.monotonic() - t0, 1))
            ev.json("conversation.json", record)
            self._refuse_untested(cid, untested, kind="dialog" if native else "precondition")
        reqs = self._requests_since(mark) if clicked else []
        ok2xx = self._delete_proved(reqs, entry["href"])  # round 3, M-5
        record["requests"] = reqs
        after = b.call(ITEM_JS, arg, page=page)
        record["item_after"] = {k: after.get(k) for k in ("found", "title", "count")}
        now_listed = {p for p, _ in (self._conversation_state(page) or {}).get("items") or []}
        others = sorted((listed - now_listed) - {entry["href"]}) if clicked else []  # R2-2: a click's only
        if others:  # R2-2: read again: a re-render or a virtualised list is no cascade
            page.wait_for_timeout(2000)
            now_listed = {p for p, _ in (self._conversation_state(page) or {}).get("items") or []}
            others = sorted((listed - now_listed) - {entry["href"]})
        if how == "renommer":
            state = (f"titre relu « {after.get('title')} »" if after.get("found")
                     else "conversation absente de la barre latérale")
            if outcome and not error:
                entry.update(state="renommée", title=after.get("title"))
                entry.setdefault("history", []).append({"t": now_iso(), "state": "renommée", "case": cid,
                                                        "title": after.get("title")})
        else:
            if not clicked:  # nothing clicked now: the sidebar as it is
                state = "encore listée" if after.get("found") else "plus listée"
            elif after.get("found"):
                state = "encore listée"
                error = error or "encore listée"
            elif error:
                state = "état incertain"  # registry-5: never « plus listée » after an error
            elif not ok2xx:  # verdicts-6: gone from the screen, no request that succeeded
                state = "disparue sans requête aboutie"
                error = "aucune requête de suppression aboutie (2xx)"
            else:
                state = "plus listée"
            if clicked and state == "plus listée" and entry["state"] != "supprimée":
                self._set_state(entry, "supprimée", cid)
        if others:  # R2-2: the count in the registry and on the line; the addresses in conversation.json only
            record["others_gone"] = others
            entry["cascade"] = len(others)
            self._set_state(entry, entry["state"], cid)
            entry["history"][-1].update(state="suppression en cascade", count=len(others))
            self._save_registry()
            log.warning("%d other conversation(s) left the sidebar during %s", len(others), how)
        self._save_registry()
        titles = b.call(TITLES_JS, {"items": SELECTORS["conversation_items"]}, page=page)
        listing = f"{len(titles)} conversation{'s' if len(titles) != 1 else ''} listée{'s' if len(titles) != 1 else ''}"
        verb = "renommage" if how == "renommer" else "suppression"
        text = (f"{verb} de la conversation de {owner} : {state}" + (f" ({error})" if error else "")
                + (f" ; {self._requests_text(reqs)}" if clicked else "")
                + (" ; sans confirmation" if outcome and "sans confirmation" in outcome else "")
                + (f" ; {len(others)} autre(s) conversation(s) plus listée(s)" if others else "") + f" ; {listing}")
        record.update(outcome=outcome, error=error, seconds=round(time.monotonic() - t0, 1))
        ev.json("conversation.json", record)
        ev.screenshot(page, "screen.png")
        obs = {"answer": text, "timing_s": seconds if outcome and not error else None, "t": now_iso(),
               "composer": composer, "case": cid,
               "conversation": {"href": entry["href"], "created_by": entry["case"], "state": entry["state"]}}
        if others:
            obs["others_gone"] = len(others)
        if notices and notices["internal"]:
            obs["internal_error_shown"] = True
        self._shared("@list", obs)
        flag = {"cascade_delete": len(others)} if others else {}
        if error and not others:  # R2-2: a cascade is never a refusal: the result carries its flag, at once
            raise DriverRefusal("precondition", f"{verb} de la conversation de {owner} : {error}",
                                extra={"text": one_line(text, 300), **flag})
        return {"case": cid, "conversation": entry["href"], "state": entry["state"], "text": one_line(text, 300), **flag}

    def _set_state(self, entry: dict, state: str, cid: str) -> None:
        """L'état d'une conversation du registre, noté aussitôt (round 2, registry-6)."""
        entry["state"] = state
        entry.setdefault("history", []).append({"t": now_iso(), "state": state, "case": cid})
        self._save_registry()

    def _requests_since(self, mark: int) -> list:
        """Les requêtes qui écrivent (ni GET, ni le flux de réponse) depuis `mark` (round 2, verdicts-6)."""
        return [{"method": e["method"], "url": e["url"], "status": e.get("status")}
                for e in self.browser.since(self.browser.network, mark)
                if e["method"] not in ("GET", "HEAD", "OPTIONS") and not re.search(STREAM_PATTERN, e["url"] or "")][-20:]

    @staticmethod
    def _requests_text(reqs: list) -> str:
        if not reqs:
            return "aucune requête"
        parts = [f"{r['method']} {r['status'] if r['status'] is not None else 'sans réponse'}" for r in reqs[-3:]]
        return ("requête " if len(parts) == 1 else "requêtes : ") + ", ".join(parts)

    def _state_now(self, page, href: str, want: str, listed: set, title: str | None = None,
                   mark: int | None = None) -> bool:
        """La conversation de `href` est-elle maintenant dans l'état voulu (round 2, registry-5) ? « gone » : absente
        d'une barre latérale qui liste encore une autre conversation d'avant (une liste vidée n'est pas une
        suppression), ou, toutes les autres d'avant parties aussi, après une requête d'écriture 2xx depuis le clic
        (`mark` : une suppression que le serveur a acceptée, une cascade peut-être, R2-2) ; « renamed » : listée,
        son titre relu."""
        st = self._conversation_state(page)
        if st is None:
            return False
        items = dict(st.get("items") or [])
        if want == "gone":
            others = listed - {href}
            return href not in items and (not others or bool(others & set(items))
                                          or (mark is not None and self._write_2xx_since(mark, href)))
        return href in items and same_title(items[href], title)

    def _write_2xx_since(self, mark: int, href: str | None = None) -> bool:
        return self._delete_proved(self._requests_since(mark), href)

    def _delete_proved(self, reqs: list, href: str | None) -> bool:
        """Round 3, M-5 : une écriture 2xx des hôtes de l'application ; si une requête nomme la conversation (son
        identifiant dans l'URL), c'est elle qui doit avoir abouti. Jamais une écriture d'un autre hôte (mesure
        d'audience, service tiers)."""
        mine = [r for r in reqs if self.browser.allowed(r.get("url") or "")]
        ident = (href or "").rstrip("/").rsplit("/", 1)[-1]
        named = [r for r in mine if ident and ident in (r.get("url") or "")]
        return any(200 <= (r.get("status") or 0) < 300 for r in (named or mine))

    def _await_state(self, page, href: str, want: str, listed: set, start: float, deadline: float,
                     title: str | None = None, mark: int | None = None) -> tuple[bool, float | None]:
        """L'état voulu (_state_now) tenu HOLD_S, attendu jusqu'à `deadline` (round 2, registry-5 et verdicts-6) ;
        rend (atteint, secondes depuis `start` jusqu'à ce qu'il le soit)."""
        since = None
        while True:
            now = time.monotonic()
            if self._state_now(page, href, want, listed, title, mark):
                since = since or now
                if now - since >= HOLD_S:
                    return True, round(since - start, 1)
            else:
                since = None
            if since is None and now >= deadline:
                return False, None
            page.wait_for_timeout(200)

    def _open_item_menu(self, page, arg: dict, record: dict) -> dict | None:
        """Le menu de la conversation que nomme `arg` (son adresse) : son lien survolé (le déclencheur se
        révèle au survol), son déclencheur — dans la ligne de cette conversation seule, jamais au-dessus (round 2,
        registry-4) — cliqué, puis le menu lu (celui que le déclencheur nomme, sinon le dernier visible), qui doit
        être celui de ce déclencheur (aria-expanded). Rend le menu, ou None (la raison dans record["error"] ;
        record["guard"] quand c'est une garde du pilote, avant tout clic irréversible : R2-1)."""
        b = self.browser
        if b.call(MENU_OPEN_JS, {"menu": SELECTORS["menu"]}, page=page) and not self._close_menu(page):
            record.update(error="un menu était déjà ouvert et ne se ferme pas : rien n'est cliqué", guard=True)
            return None  # the menu read after the click must be the one this click opened
        anchor = b.element(ITEM_EL_JS, arg, page=page, timeout_ms=3000)
        if anchor is None:
            record.update(error="le lien de la conversation a disparu", guard=True)
            return None
        try:
            anchor.hover(timeout=5000)
        finally:
            anchor.dispose()

        def with_trigger():  # registry-4: a trigger of the row itself, never above it
            info = b.call(ITEM_JS, arg, page=page, timeout_ms=2000)
            return info if info.get("trigger") and info.get("trigger_row") else None

        info = self._wait_for(page, with_trigger, 1.5)
        if not info:
            record.update(error="pas de bouton de menu dans la ligne de la conversation, même au survol", guard=True)
            return None
        record["trigger"] = {k: info.get(k) for k in ("trigger", "trigger_level", "trigger_label", "expanded", "scope",
                                                      "scope_is_link", "html")}
        trigger = b.element(TRIGGER_EL_JS, arg, page=page, timeout_ms=3000)
        if trigger is None:
            record.update(error="le bouton de menu de la conversation a disparu", guard=True)
            return None
        try:
            trigger.click(timeout=5000)
        finally:
            trigger.dispose()
        self._after_click(page)
        controls = (b.call(ITEM_JS, arg, page=page, timeout_ms=2000) or {}).get("controls")
        # m10: the menu of a conversation the run did not create (the probe) keeps no word of its title
        margs = {"menu": SELECTORS["menu"], "item": SELECTORS["menu_item"], "controls": controls,
                 "sidebar": SELECTORS["sidebar"], "convPath": SELECTORS["conversation_path"],
                 "redactHref": arg["href"] if arg.get("redact") else None, "entryWords": list(ENTRY_WORDS),
                 "tail": MENU_TAIL}
        menu = self._wait_for(page, lambda: b.call(MENU_JS, margs, page=page, timeout_ms=2000), UI_WAIT_S)
        if not menu:
            page.keyboard.press("Escape")
            record["error"] = "le menu de la conversation ne s'ouvre pas"
            return None
        record["menu"] = {k: menu[k] for k in ("how", "id", "items", "html")}
        if (b.call(ITEM_JS, arg, page=page, timeout_ms=2000) or {}).get("expanded") != "true":
            record["closed_menu"] = self._close_menu(page)  # registry-4: never a menu its trigger does not own
            record.update(error="le menu ouvert n'est pas celui du bouton de la conversation (aria-expanded)", guard=True)
            return None
        return {**menu, "arg": margs}

    def _click_menu_entry(self, page, menu: dict, how: str, title: str | None = None, before=None) -> bool:
        """Cliquer l'entrée de renommage ou de suppression du menu ouvert : la seule, active, dont le texte
        suit son motif (MENU_ITEM_EL_JS), jamais un rang. Rend True quand le clic sur « Supprimer » a ouvert la
        confirmation native de l'application (round 2, robustness-6) : jamais répondue, laissée à l'humain."""
        pattern = SELECTORS["menu_rename"] if how == "renommer" else SELECTORS["menu_delete"]
        el = self.browser.element(MENU_ITEM_EL_JS, {**menu["arg"], "pattern": pattern}, page=page, timeout_ms=3000)
        if el is None:  # round 3, I-1 (R2-1): accepted, then not found to click: nothing is clicked
            raise EntryGone("l'entrée du menu acceptée n'est plus là au moment du clic : rien n'est cliqué")
        if before is not None:
            before()
        try:
            el.click(timeout=5000)
        except PWTimeout:  # a native dialog opened inside the click: the click waits for it
            if self._app_confirmation(how, title):
                return True
            raise
        finally:
            el.dispose()
        if self._app_confirmation(how, title, page):
            return True
        self._after_click(page)
        return False

    def _app_confirm_since(self, mark: int | None, title: str | None) -> bool:
        """Round 3, I-3 : la confirmation native de l'application ouverte tard, pendant une lecture de la page, et
        peut-être déjà fermée par l'humain : un `confirm` du journal des dialogues depuis le clic sur « Supprimer »
        qui parle de supprimer cette seule conversation. Notée à part (`_app_dialogs`), jamais répondue."""
        if mark is None:
            return False
        for d in self.browser.since(self.browser.dialog_log, mark):
            if d.get("type") == "confirm" and confirms_one_deletion(d.get("message"), title):
                self._app_dialogs.add(d["seq"])
                return True
        return False

    def _app_confirmation(self, how: str, title: str | None, page=None) -> bool:
        """La suppression demande-t-elle sa confirmation par window.confirm, un texte qui parle de supprimer cette
        seule conversation (round 2, robustness-6) ? C'est la confirmation de l'application, pas un dialogue
        inattendu : notée à part (`_app_dialogs`), jamais répondue."""
        if how != "supprimer":
            return False
        if page is not None:
            self.browser.settled(page)
        d = self.browser.pending()
        if d and d["type"] == "confirm" and confirms_one_deletion(d["message"], title):
            self._app_dialogs.add(d["seq"])
            return True
        return False

    def _close_menu(self, page) -> bool:
        """Échap, deux fois au plus, jusqu'à ce qu'aucun menu ne soit visible."""
        b = self.browser
        for _ in range(2):
            page.keyboard.press("Escape")
            if self._wait_for(page, lambda: not b.call(MENU_OPEN_JS, {"menu": SELECTORS["menu"]}, page=page,
                                                         timeout_ms=2000), 1.5):
                return True
        return False

    def _rename(self, page, arg: dict, title: str, record: dict, budget: float,
                listed: set) -> tuple[str | None, str | None, float | None]:
        """Après « Renommer » : le champ du nouveau titre (le focus, sinon rename_field ; jamais le compositeur ni
        une recherche ; dans une fenêtre, seulement une fenêtre apparue après le clic, inconnue de KNOWN_MODALS,
        à un seul champ et sans choix : round 2, registry-7) rempli du titre et validé par Entrée, à défaut par
        le seul bouton de validation de sa fenêtre ; attendu jusqu'à 3 × le budget (le serveur répond : round 2,
        verdicts-6), puis abandonné par Échap. Puis le titre relu sur la conversation de cette adresse, tenu
        HOLD_S. Rend (« renommée », None, secondes depuis Entrée) ou (None, la raison, None)."""
        b = self.browser
        farg = {"fields": SELECTORS["rename_field"], "sidebar": SELECTORS["sidebar"], "composer": SELECTORS["textarea"],
                "submit": SELECTORS["rename_submit"], "known": [k["selector"] for k in KNOWN_MODALS if not k.get("own")]}
        field = self._wait_for(page, lambda: b.call(RENAME_FIELD_JS, farg, page=page, timeout_ms=2000), UI_WAIT_S)
        if not field:
            return None, "pas de champ pour le nouveau titre après « Renommer »", None
        record["field"] = field
        if field["in_dialog"]:
            b.own_modal = "renommer"
        el = b.element(RENAME_FIELD_EL_JS, farg, page=page, timeout_ms=3000)
        if el is None:
            return None, "le champ du nouveau titre a disparu", None
        try:
            el.fill(title, timeout=5000)
            el.press("Enter", timeout=5000)
        finally:
            el.dispose()
        entered = time.monotonic()
        self._after_click(page)
        limit, _ = self._deadline(3 * budget)
        deadline = entered + limit

        def gone():
            return not b.call(RENAME_FIELD_JS, farg, page=page, timeout_ms=2000)

        done = self._wait_for(page, gone, 2.0)
        if not done and field["in_dialog"]:
            button = b.element(RENAME_SUBMIT_EL_JS, farg, page=page, timeout_ms=3000)
            if button is not None:
                try:
                    button.click(timeout=5000)
                finally:
                    button.dispose()
                self._after_click(page)
                done = self._wait_for(page, gone, 2.0)
        if not done:  # verdicts-6: the field may stay while the server answers
            done = self._wait_for(page, gone, max(0.5, deadline - time.monotonic()))
        if not done:
            page.keyboard.press("Escape")  # abandoned, never left half-typed
            if self._wait_for(page, gone, 1.5) and b.own_modal == "renommer":
                b.own_modal = None
            return None, "le nouveau titre n'a pas été validé", None
        if b.own_modal == "renommer":
            b.own_modal = None
        reached, seconds = self._await_state(page, arg["href"], "renamed", listed, entered, deadline, title)
        if not reached:
            return None, "le nouveau titre n'est pas relu sur la conversation", None
        return "renommée", None, seconds

    def _confirm_delete(self, page, arg: dict, record: dict, entry: dict, cid: str, title: str | None, previous: str,
                        clicked_at: float, budget: float, listed: set, mark: int | None = None):
        """Après « Supprimer » : la confirmation de l'application (une fenêtre d'alerte, à défaut une fenêtre),
        ouverte par ce clic (revue de la v2.3, m1 : une fenêtre déjà là avant n'est jamais prise), jugée sur son
        texte entier (confirmation_refusal : elle parle de supprimer cette seule conversation, son titre pouvant
        tenir lieu du nom ; round 2, R2-3, click-targets-6) et validée par son bouton de confirmation (pick_confirm :
        le seul fort, à défaut le seul faible d'une fenêtre d'alerte, « Continuer », « Oui ») ; sinon annulée par
        Échap, et « suppression demandée » retirée du registre si la conversation est encore listée (registry-6) :
        le cas est alors NON TESTÉ (R2-1). « Suppression confirmée » est noté au registre avant le clic de
        confirmation. Puis la conversation absente de la barre latérale, tenue HOLD_S, attendue 3 × le budget
        depuis le clic (verdicts-6). Sans fenêtre, une conversation partie a été supprimée sans confirmation (le
        texte le dit). Rend (le résultat, l'erreur, la raison d'une garde, les secondes)."""
        b = self.browser
        carg = {"cap": CONFIRM_TEXT_CAP}
        href = arg["href"]
        limit, _ = self._deadline(3 * budget)

        def closed():
            return not b.call(CONFIRM_JS, carg, page=page, timeout_ms=2000)

        def window_or_gone():
            if self._app_confirm_since(mark, title):  # round 3, I-3: the app's confirm, opened late
                return {"native": True}
            d = b.call(CONFIRM_JS, carg, page=page, timeout_ms=2000)
            if d:
                return {"dialog": d}
            return {"gone": True} if self._state_now(page, href, "gone", listed, mark=mark) else None

        got = self._wait_for(page, window_or_gone, UI_WAIT_S)
        if (got or {}).get("native") or (not (got or {}).get("dialog") and self._app_confirm_since(mark, title)):
            record["confirmation"] = "native"
            return None, None, NATIVE_CONFIRMATION, None
        dlg = (got or {}).get("dialog")
        if not dlg:
            record["confirmation"] = "aucune"
            reached, seconds = self._await_state(page, href, "gone", listed, clicked_at, clicked_at + limit, mark=mark)
            if reached:
                return "supprimée sans confirmation", None, None, seconds
            if self._app_confirm_since(mark, title):  # round 3, I-3: inside a read, closed by the human
                record["confirmation"] = "native"
                return None, None, NATIVE_CONFIRMATION, None
            return None, "pas de confirmation de suppression (aucune fenêtre ouverte par le clic)", None, None
        record["confirmation"] = dlg
        if dlg["role"] == "alertdialog":
            b.own_modal = "suppression"
        def cancel():  # cancelled by Escape, never confirmed blind; registry-6: withdrawn if still listed
            page.keyboard.press("Escape")
            if self._wait_for(page, closed, 1.5) and b.own_modal == "suppression":
                b.own_modal = None
            if (b.call(ITEM_JS, arg, page=page, timeout_ms=2000) or {}).get("found"):
                entry["state"] = previous
                entry.setdefault("history", []).append({"t": now_iso(), "state": "suppression annulée", "case": cid})
                self._save_registry()

        why = confirmation_refusal(dlg, title)
        if why:
            self._harness_text(dlg.get("full") or dlg.get("text"), cid)  # leakage-2: an internal error it showed
            cancel()
            return None, None, why, None
        index, _ = pick_confirm(dlg)
        el = b.element(CONFIRM_EL_JS, {**carg, "index": index, "expect": dlg["buttons"][index]}, page=page,
                       timeout_ms=3000)
        if el is None:  # round 3, M-3 (R2-1): accepted, then gone before the click: nothing confirmed
            cancel()
            return None, None, "le bouton de confirmation n'est plus là au moment du clic : annulée, rien n'est confirmé", None
        self._set_state(entry, "suppression confirmée", cid)  # registry-6: before the click, never twice
        confirmed_at = time.monotonic()
        try:
            el.click(timeout=5000)
        finally:
            el.dispose()
        self._after_click(page)
        if self._wait_for(page, closed, UI_WAIT_S) and b.own_modal == "suppression":
            b.own_modal = None
        reached, seconds = self._await_state(page, href, "gone", listed, confirmed_at, confirmed_at + limit, mark=mark)
        if not reached:
            return None, "encore listée après la confirmation", None, None
        return "supprimée", None, None, seconds

    def ouvrir_avis(self, args: dict, ev: Evidence) -> dict:
        """v2.3, E5 — l'enquête « Votre avis », ouverte exprès depuis son entrée (feedback_entry ; vue le
        18/10 : button[aria-label="Send feedback"]), reconnue par sa structure (KNOWN_MODALS « avis ») et
        fermée par sa croix, jamais par « Envoyer » : rien n'y est choisi, écrit ni envoyé. L'observation du
        cas : « avis : ouvert (entrée : X) ; fermé par : croix ; envoyé : non ». L'entrée est identifiée par
        son libellé exact (feedback_entry), hors carte de réponse et compositeur (revue de la v2.3, I5 : une
        note 👍/👎 d'une carte est un envoi) ; non identifiée, rien n'est cliqué, aucune observation (le cas
        reste à faire), refus `precondition` qui nomme les candidats vus. Une enquête qui ne s'ouvre pas :
        observation et refus `precondition` ; une fenêtre non reconnue, ou qui ne se ferme pas par sa
        croix : observation et refus `dialog`, laissée à l'humain."""
        cid = args["case"]
        case = self._case(cid)
        self._played_by(case, "ouvrir_avis")
        b = self.browser
        page = b.page()
        self._ensure_on_app(page)
        self._take_case(cid)
        composer = self._composer_now(page)
        avis = next(k for k in KNOWN_MODALS if k["name"] == "avis")
        margs = {"known": [avis], "modal": SELECTORS["modal"]}
        record = {"case": cid, "entry": None, "opened": False, "closed_by": None, "sent": False}
        refusal = None
        start = page.url
        notices0 = self._page_notices(page, mark=True)
        entry, seen = self._identified_entry(page, SELECTORS["feedback_entry"], SELECTORS["feedback_words"],
                                             sidebar=False)
        record["entry"] = entry
        if entry is None:  # I5, R2-1: never a guessed button; nothing clicked: NON TESTÉ, the candidates in avis.json
            record["candidates"] = seen
            ev.json("avis.json", record)
            expected = ", ".join(re.search(r'aria-label="([^"]+)"', s).group(1) for s in SELECTORS["feedback_entry"])
            self._refuse_untested(cid, f"pas d'entrée « Votre avis » identifiée (libellés attendus : {expected}) : rien "
                                       f"n'est cliqué ; {len(seen)} candidat(s) dans avis.json",
                                  extra={"candidates": len(seen)})
        else:
            name = entry["label"] or entry["text"] or entry["selector"]
            if not self._click_entry(page, SELECTORS["feedback_entry"], SELECTORS["feedback_words"], False):
                ev.json("avis.json", record)
                self._refuse_untested(cid, "l'entrée « Votre avis » a disparu avant le clic : rien n'est cliqué")

            def opened():
                m = b.call(MODALS_JS, margs, page=page, timeout_ms=2000)
                return m if (m["known"] or m["unknown"]) else None

            seen = self._wait_for(page, opened, UI_WAIT_S)
            if not seen:
                text = f"avis : ne s'ouvre pas (entrée : {name}) ; envoyé : non"
                refusal = ("precondition", "l'enquête « Votre avis » ne s'ouvre pas depuis son entrée")
            elif "avis" not in seen["known"]:  # R2-1: a window the pilot does not recognise: NON TESTÉ, left open
                record["unknown"] = seen["unknown"]
                ev.json("avis.json", record)
                self._refuse_untested(cid, "fenêtre non reconnue après l'entrée « Votre avis » : laissée ouverte, à "
                                           "fermer dans Chrome (texte dans avis.json)", kind="dialog")
            else:
                record["opened"] = True
                record["survey"] = b.call(AVIS_JS, {"sel": avis["selector"]}, page=page)
                ev.screenshot(page, "avis.png")
                page.locator(avis["dismiss"]).first.click(timeout=5000)  # its cross, never « Envoyer »
                self._after_click(page)
                shut = self._wait_for(page, lambda: "avis" not in b.call(MODALS_JS, margs, page=page,
                                                                         timeout_ms=2000)["known"], 2.0)
                record["closed_by"] = "croix" if shut else None
                if shut:
                    text = f"avis : ouvert (entrée : {name}) ; fermé par : croix ; envoyé : non"
                else:
                    text = f"avis : ouvert (entrée : {name}) ; fermé par : non fermé ; envoyé : non"
                    refusal = ("dialog", "l'enquête « Votre avis » ne se ferme pas par sa croix : à fermer dans "
                                         "Chrome, jamais par « Envoyer »")
        if not refusal or refusal[0] != "dialog":
            self._restore_page(page, start, record)
        notices = self._new_error_notices(page, notices0)  # leakage-1
        if notices:
            record["notices"] = notices["raw"]
        ev.json("avis.json", record)
        self.observations[cid] = {"answer": text, "timing_s": None, "t": now_iso(), "composer": composer,
                                  "avis": {"entry": (entry or {}).get("selector"), "opened": record["opened"],
                                           "closed_by": record["closed_by"], "sent": False},
                                  **({"internal_error_shown": True} if notices and notices["internal"] else {})}
        if refusal:
            raise DriverRefusal(refusal[0], refusal[1], extra={"text": text})
        return {"case": cid, "opened": True, "closed_by": "croix", "sent": False, "text": text}

    def sonder_interface(self, args: dict, ev: Evidence) -> dict:
        """v2.3, E11 — la sonde d'interface, en lecture seule, que la recette joue en M0, avant que M8 ait
        besoin de ces adresses : chaque sélecteur de SELECTORS résolu sur la page (trouvés, visibles ; les
        motifs de texte exceptés), l'entrée de la recherche ⌘K et la palette (ouverte, son balisage relevé
        sans les titres, refermée par Échap, rien tapé), l'entrée de « Votre avis » (jamais cliquée), le
        bouton de dictée (jamais pressé), la liste `accept` du champ de fichier, et le menu de la PREMIÈRE
        conversation de la barre latérale (survolée, déclencheur cliqué, entrées lues, refermé par Échap ;
        aucune entrée cliquée ; ni son titre ni son adresse gardés). probe.json. Ne refuse rien : ce qui
        échoue est noté dans `errors` (seule la garde commune refuse, un dialogue gelant la page)."""
        b = self.browser
        page = b.page()
        probe = {"t": now_iso(), "url": None, "selectors": None, "search_entry": None, "search": None,
                 "feedback_entry": None, "mic": None, "file_input": None, "menu": None, "menu_verdict": None,
                 "errors": []}

        def step(name, fn):
            try:
                probe[name] = fn()
            except (PWError, DriverRefusal) as e:
                if b.pending():
                    raise
                probe["errors"].append(f"{name} : {one_line(getattr(e, 'detail', '') or str(e), 200)}")

        def url():
            self._ensure_on_app(page)
            return short_url(page.url)

        step("url", url)
        start = page.url
        step("selectors", lambda: self._probe_selectors(page))
        step("search_entry", lambda: self._probe_entry(page, "search"))
        step("feedback_entry", lambda: self._probe_entry(page, "feedback"))
        step("mic", lambda: b.call(MIC_JS, {"sel": SELECTORS["mic_button"]}, page=page))
        step("file_input", lambda: self._file_input(page))
        step("search", lambda: self._probe_search(page))
        step("menu", lambda: self._probe_menu(page))
        step("restored", lambda: self._restore_page(page, start, probe) or "moved_to" in probe)
        menu = probe["menu"] or {}
        verdict = probe["menu_verdict"] = menu.get("verdict")  # robustness-1: what M8-5 and M8-6 will meet
        ev.json("probe.json", probe)
        sel = probe["selectors"] or {}
        found = sorted(k for k, rows in sel.items() if any(r.get("count") for r in rows))
        missing = sorted(k for k, rows in sel.items() if not any(r.get("count") for r in rows))
        entries = len((menu.get("menu") or {}).get("items") or [])
        if verdict is not None:  # R2-4: the verdict and the count, the entries in probe.json only
            refused = verdict.get("renommer") or verdict.get("supprimer")
            if verdict.get("renommer") == NO_RENAME_ENTRY and not verdict.get("supprimer"):  # round 6
                menu_text = "M8-5 : menu sans entrée de renommage ; M8-6 : menu accepté"
            else:
                menu_text = ("M8-5/M8-6 : menu accepté" if not refused else f"M8-5/M8-6 : menu refusé ({refused})")
            menu_text += f", {entries} entrée{'s' if entries != 1 else ''}"
        else:
            menu_text = f"menu de la première conversation : {menu.get('error') or 'non lu'}"
        text = (f"sonde : {len(found)} sélecteurs sur {len(sel)} trouvés"
                + (f" (absents : {', '.join(missing)})" if missing else "")
                + f" ; ⌘K : {'ouverte' if (probe['search'] or {}).get('opened_by') else 'non ouverte'}"
                + f" ; avis : {'entrée trouvée' if (probe['feedback_entry'] or {}).get('selector') else 'entrée non identifiée'}"
                + f" ; micro : {(probe['mic'] or {}).get('status') or 'absent'}"
                + f" ; accept : {(probe['file_input'] or {}).get('accept') or 'aucun'}"
                + f" ; {menu_text}"
                + (f" ; {len(probe['errors'])} erreur(s)" if probe["errors"] else ""))
        return {"found": found, "missing": missing, "menu_entries": entries, "menu_verdict": verdict,
                "errors": probe["errors"], "text": one_line(text, 800)}

    def _probe_selectors(self, page) -> dict:
        """Chaque entrée de SELECTORS qui est une adresse dans la page (SELECTOR_PATTERNS exceptés), chaque
        variante : combien d'éléments elle trouve, combien sont visibles ; une variante refusée porte son
        erreur. En un appel borné (round 2, robustness-4 : un dialogue ouvert fait lever son délai, quand
        locator.count() ne revenait jamais)."""
        rows = []
        for key, value in SELECTORS.items():
            if key in SELECTOR_PATTERNS:
                continue
            for sel in (value if isinstance(value, list) else [value]):
                rows.append({"key": key, **css_and_text(sel)})
        got = self.browser.call(PROBE_SELECTORS_JS, {"rows": rows}, page=page, timeout_ms=5000) or []
        out = {}
        for row in got:
            out.setdefault(row["key"], []).append({k: v for k, v in row.items() if k != "key"})
        return out

    def _probe_entry(self, page, which: str) -> dict:
        """L'entrée de la recherche ⌘K ou de « Votre avis », telle que l'action la prendrait
        (_identified_entry), jamais cliquée ; non identifiée, les candidats que la page montre."""
        sels, words, sidebar = ((SELECTORS["search_entry"], SELECTORS["search_words"], True) if which == "search"
                                else (SELECTORS["feedback_entry"], SELECTORS["feedback_words"], False))
        entry, seen = self._identified_entry(page, sels, words, sidebar)
        return entry or {"selector": None, "candidates": seen}

    def _file_input(self, page) -> dict:
        """Le champ de fichier : combien la page en a, et la liste `accept` du premier (E6) ; en un appel borné
        (round 2, robustness-4)."""
        return self.browser.call(FILE_INPUT_JS, {"sel": SELECTORS["file_input"]}, page=page, timeout_ms=3000)

    def _probe_search(self, page) -> dict:
        """La palette ⌘K de la sonde : ouverte, son champ, sa racine et le nombre de ses résultats avant toute
        frappe relevés, son HTML sans les titres ; refermée par Échap ; rien n'y est tapé."""
        b = self.browser
        arg = {"inputs": SELECTORS["search_input"], "results": SELECTORS["search_result"],
               "empty": SELECTORS["search_empty"], "redact": True, "convPath": SELECTORS["conversation_path"]}
        record = {"opened_by": None}
        try:
            if self._open_search(page, arg, record):
                got = b.call(SEARCH_JS, arg, page=page, timeout_ms=2000) or {}
                record.update({k: got.get(k) for k in ("input", "root", "count", "items", "unknown", "result", "empty",
                                                       "focused", "html")})
        finally:
            if record["opened_by"] and not b.pending():
                record["closed"] = self._close_search(page, arg)
        return record

    def _probe_menu(self, page) -> dict:
        """Le menu de la PREMIÈRE conversation de la barre latérale, pour en confirmer le balisage avant M8 :
        son élément sans titre ni adresse, son déclencheur survolé puis cliqué, ses entrées lues, refermé par
        Échap. Aucune entrée n'est cliquée."""
        b = self.browser
        first = b.call(ITEM_JS, self._conv_arg(None, redact=True), page=page)
        if not first.get("found"):
            return {"found": False, "conversations": first.get("total", 0)}
        record = {"found": True, "conversations": first.get("total"),
                  "item": {k: first.get(k) for k in ("scope", "scope_is_link", "trigger", "trigger_level",
                                                     "trigger_label", "expanded", "html")}}
        menu = self._open_item_menu(page, self._conv_arg(first["href"], redact=True), record)
        if menu is not None:
            record["closed"] = self._close_menu(page)
            # robustness-1: what renommer_conversation and supprimer_conversation would decide on this menu
            label = (record.get("trigger") or {}).get("trigger_label")  # round 6
            record["verdict"] = {how: menu_entry(menu["items"], how, label=label)[1] for how in ("renommer", "supprimer")}
        record["trigger"] = {k: v for k, v in (record.get("trigger") or {}).items() if k != "html"} or None
        return record

    def charge_concurrente(self, args: dict, ev: Evidence) -> dict:
        """N8 : deux traitements lourds en parallèle, dans deux onglets, envoyés à moins de 2 s
        l'un de l'autre ; une ligne de journal « disponibilité » pour le couple."""
        a, bb = args["cases"]
        cases = [self._case(a), self._case(bb)]
        cid = next((c["id"] for c in self.cases.values()
                    if c["kind"] == "concurrent" and sorted(c.get("cases", [])) == sorted([a, bb])),
                   f"charge_concurrente : {a} + {bb}")
        done = self._journaled(cid)
        if done is not None:
            return {"case": cid, "replayed": True, "text": json.dumps(done, ensure_ascii=False)}
        b = self.browser
        mark = b.mark()
        first = b.page()
        self._goto(first, self.entry)  # two fresh conversations
        conversations = []  # m3: the two it creates, on its line
        second = b.context.new_page()
        try:
            self._goto(second, self.entry)
            out, turns = {}, []
            before = {id(first): self._conversation_state(first), id(second): self._conversation_state(second)}
            for page, case in ((first, cases[0]), (second, cases[1])):
                out[case["id"]] = {}
                # The pinned web mode in each tab, read for each (PROCEDURE-2 §8.5).
                if self.pinned.get("web"):
                    try:
                        out[case["id"]]["web"] = self._set_web(page, self.pinned["web"])["read_back"]
                    except DriverRefusal as r:
                        out[case["id"]]["web"] = r.extra.get("read_back")
                else:
                    out[case["id"]]["web"] = web_mode_of(self._control(page, SELECTORS["web_button"], kind="web"))
                if case.get("file"):
                    path = self._resolve(case["file"])
                    att = self._attach(page, path, budget_of(case))
                    out[case["id"]]["attach"] = {k: att[k] for k in ("state", "seconds", "ready_by")} | {
                        "label": (attach_error_class(att["label"], path.name) if att["state"] == "failed"
                                  else sanitise(att["label"], 120, keep=(path.name,))),
                        "internal_error_shown": chip_shows_internal(att["label"], path.name)}
            if all(o.get("attach", {"state": "ready"})["state"] == "ready" for o in out.values()):
                for page, case in ((first, cases[0]), (second, cases[1])):
                    # no popover read here: the two sends must leave within 2 s of each other
                    turns.append({**self._send(page, case["question"], read_sent=False), "case": case["id"]})
                gap = round(abs(turns[1]["t_send"] - turns[0]["t_send"]), 2)
                budget = max(budget_of(c) for c in cases)
                wait_s, capped = self._deadline(3 * budget)
                try:
                    self._wait(turns, wait_s, check_host=False)
                except AskTimeout as t:
                    for turn in turns:
                        if not turn["done"]:
                            out[turn["case"]]["timeout_s"] = round(t.seconds, 1)
                            said = self._turn_notices(turn, turn["page"])  # round 2, leakage-1
                            if notices_internal(said, self._keep_names()):
                                out[turn["case"]]["internal_error_shown"] = True
                            out[turn["case"]]["abort"] = self._abort(turn)
                for turn in turns:
                    if turn.get("end") == "<HORS PÉRIMÈTRE>":
                        out[turn["case"]]["end"] = turn["end"]
                    elif turn["done"]:
                        obs = self._collect(turn)
                        answer = (sanitise(obs["answer"], 300) if answer_shows_internal(obs["answer"])
                                  else one_line(obs["answer"], 300))
                        out[turn["case"]].update(answer=answer, total_s=obs["total_s"],
                                                 end=obs["end"], server_steps=obs["server_steps"])
                        if obs.get("failure"):
                            out[turn["case"]]["error_class"] = obs["failure"]["error_class"]
                        if shown_internal(obs):
                            out[turn["case"]]["internal_error_shown"] = True
                for turn in turns:  # E1: the conversations the two sends created, before the tab closes
                    if turn.get("end") != "<HORS PÉRIMÈTRE>":
                        noted = self._note_conversation_quietly(cid, before.get(id(turn["page"])), turn["page"],
                                                                f"charge_concurrente ({turn['case']})")
                        if noted:
                            conversations.append({**noted, "case": turn["case"]})
                out["gap_s"] = gap
            ev.screenshot(second, "screen.png")
        finally:
            try:
                second.close()
            except PWError:
                pass
        # The journal comes last (Task 4 re-review 3): the screenshot, the second tab closed, a
        # drain, the network summary, then a pending dialog and both tabs' hosts. A dialog left
        # open, or a tab that left the list — even after <END>, even during the screenshot —
        # makes the line KO sécurité and the action a `dialog` or `host` refusal; journaled
        # earlier, either was journaled OK, then refused, and a re-run replayed the OK.
        b.drain(50)
        ev.json("network.json", b.capture(mark), cap=NETWORK_CAP)
        dialog = b.pending() or self._new_dialog()  # round 2, robustness-2: closed by the human, still the action's
        navs = self._offlist()
        off = sorted({n["host"] for n in navs})
        ok = [o for k, o in out.items() if k != "gap_s" and o.get("answer") and not (o.get("end") or "").startswith("<ABORT>")]
        verdict = "OK" if len(ok) == 2 and not off and not dialog else "KO"
        parts = ([f"dialogue inattendu ({dialog['type']}) : {self._harness_text(dialog['message'], cid)}"]  # leakage-2
                 if dialog else [])
        if off:
            parts.append(f"navigation hors périmètre : {', '.join(off)}")
        for c in cases:
            o = out[c["id"]]
            if o.get("attach", {}).get("state", "ready") != "ready":
                parts.append(f"{c['id']} : pièce jointe {o['attach']['state']} ({o['attach']['label']})")
            elif o.get("timeout_s"):
                parts.append(f"{c['id']} : abandon à {o['timeout_s']:.0f} s ({abort_text(o.get('abort') or {})})")
            elif o.get("end") == "<HORS PÉRIMÈTRE>":
                parts.append(f"{c['id']} : onglet sorti du périmètre avant la fin de la réponse")
            else:
                parts.append(f"{c['id']} : {o.get('total_s')} s, {len(o.get('answer') or '')} car.")
            if o.get("web"):
                parts[-1] += f", recherche web {o['web']}"
        if "gap_s" in out:
            parts.append(f"écart d'envoi {out['gap_s']} s")
        timing = max([o.get("total_s") or o.get("timeout_s") or 0 for k, o in out.items() if k != "gap_s"] or [0])
        extra = self._line_extra(cid, {"conversations": conversations} if conversations else {})  # modals_closed too
        if any(o.get("internal_error_shown") or (o.get("attach") or {}).get("internal_error_shown")
               for k, o in out.items() if k != "gap_s"):
            extra["internal_error_shown"] = True
        line = self._journal_add(cid, verdict, timing, " ; ".join(parts),
                                 nfr_union(self._case(cid)["nfr"] if cid in self.cases else [],
                                           ["disponibilite", "scalabilite"],
                                           ["securite"] if off or dialog else []),
                                 extra=extra)
        self.observations["@concurrent"] = {**out, "case": cid, "verdict": verdict, "t": now_iso(),
                                            "conversations": conversations}
        if dialog:
            # First, as `_after` would: a frozen tab cannot be sent back to the entry page.
            raise self._dialog_refusal(ev, dialog)
        if navs:
            # The driver's own tab goes back to the entry page: the navigation's tab index was
            # recorded with the second tab open, and may name another tab now.
            raise self._host_refusal(navs[-1], ev, page=b.page())
        result = {"case": cid, "a": out[a], "b": out[bb], "verdict": verdict, "replayed": False, "text": line}
        if extra.get("internal_error_shown"):  # I3: the harness reports it once per level
            result["internal_error_shown"] = True
        return result

    # -- le compositeur (v2) -----------------------------------------------------------------
    def _control(self, page, sels: list, kind: str | None = None) -> dict | None:
        """Un bouton du compositeur, cherché dans lui seul (round 2, click-targets-5) ; `kind` « web » : seulement
        un bouton de recherche web identifié (data-mode d'un mode, ou son libellé)."""
        return self.browser.call(CONTROL_JS, {"sels": sels, "kind": kind, "modes": list(WEB_MODES),
                                              **self._composer_arg()}, page=page)

    def _composer_now(self, page) -> dict:
        """Le compositeur tel qu'il est : le modèle et le mode web affichés, et depuis la v2.3 (E4) l'état
        du bouton de dictée (`mic`, son data-status, « absent » sans bouton), lu, jamais pressé."""
        return {"model": model_of(self._control(page, SELECTORS["model_button"])),
                "web": web_mode_of(self._control(page, SELECTORS["web_button"], kind="web")), "mic": self._mic(page)}

    def _mic(self, page) -> str:
        """L'état du bouton de dictée (v2.3, E4) : son data-status, ou « absent » ; jamais pressé."""
        got = self.browser.call(MIC_JS, {"sel": SELECTORS["mic_button"]}, page=page)
        return (got or {}).get("status") or "absent"

    def _close_list(self, page, sel: str) -> bool:
        """Une liste encore ouverte est refermée par Échap ; rend True si elle est fermée."""
        b = self.browser
        for attempt in range(2):
            deadline = time.monotonic() + 1.5
            while time.monotonic() < deadline:
                if not b.call(LIST_OPEN_JS, {"list": sel}, page=page, timeout_ms=2000):
                    return True
                page.wait_for_timeout(100)
            if attempt == 0:
                page.keyboard.press("Escape")
        return False

    def _set_model(self, page, want: str) -> dict:
        """Ouvrir la liste du bouton « Model: … », toujours (ses options sont relevées, PROCEDURE-2
        §8.3), cliquer l'option qui correspond (choose_option), relire le modèle sur le bouton. Rend
        l'enregistrement de model.json ; refus `precondition` (l'enregistrement dans `extra`) quand
        aucune option ne correspond ou que la relecture diffère."""
        b = self.browser
        t0 = time.monotonic()
        button = self._control(page, SELECTORS["model_button"])
        if button is None:
            raise DriverRefusal("precondition", "pas de bouton de modèle (« Model: … ») dans le compositeur")
        previous = model_of(button)
        record = {"asked": want, "previous": previous, "options": [], "picked": None, "candidates": [],
                  "read_back": previous, "changed": False, "list_opened": False, "list_closed": None,
                  "mic": self._mic(page)}  # E4: the dictation button, read with the selector, never pressed
        page.locator(button["sel"]).nth(button["index"]).click(timeout=5000)
        self._after_click(page)  # round 2: a dialog the click opened is seen before the page is read again
        arg = {"list": SELECTORS["listbox"], "option": SELECTORS["option"]}
        options, deadline = None, time.monotonic() + 5
        while not options and time.monotonic() < deadline:
            page.wait_for_timeout(150)
            options = b.call(OPTIONS_JS, arg, page=page)
        record["list_opened"] = bool(options)
        record["options"] = [o["text"] or o["value"] for o in options or []][:50]
        pick, candidates = choose_option(want, options or [])
        record["candidates"] = [o["text"] for o in candidates][:10]
        if pick is None:
            record["list_closed"] = self._close_list(page, SELECTORS["listbox"])
            if not options and model_matches(want, previous):  # no list to read, but already shown
                record["seconds"] = round(time.monotonic() - t0, 1)
                return record
            if candidates:
                why = (f"« {want} » désigne plusieurs modèles de la liste : {', '.join(record['candidates'])} ; "
                       f"donne le libellé exact")
                record["ambiguous"] = True
            else:
                why = ("la liste des modèles ne s'ouvre pas" if not options
                       else f"aucun modèle « {want} » dans la liste : {', '.join(record['options'][:10])}")
            record["refused"] = why
            raise DriverRefusal("precondition", why, extra={"options": record["options"],
                                                            "candidates": record["candidates"], "record": record})
        record["picked"] = {"text": pick["text"], "value": pick["value"]}
        page.locator(f'{SELECTORS["listbox"]} >> visible=true').last.locator(
            SELECTORS["option"]).nth(pick["index"]).click(timeout=5000)
        self._after_click(page)
        record["list_closed"] = self._close_list(page, SELECTORS["listbox"])
        deadline = time.monotonic() + 3
        while True:  # read back on the button, once it shows the change
            record["read_back"] = model_of(self._control(page, SELECTORS["model_button"]))
            if (model_matches(want, record["read_back"]) or model_matches(pick["text"], record["read_back"])
                    or time.monotonic() >= deadline):
                break
            page.wait_for_timeout(150)
        record["changed"] = record["read_back"] != previous
        record["seconds"] = round(time.monotonic() - t0, 1)
        if not (model_matches(want, record["read_back"]) or model_matches(pick["text"], record["read_back"])
                or (pick["value"] and model_matches(pick["value"], record["read_back"]))):
            raise DriverRefusal("precondition", f"modèle relu « {record['read_back']} » au lieu de « {want} »",
                                extra={"read_back": record["read_back"], "record": record})
        return record

    @staticmethod
    def model_text(record: dict) -> str:
        """Le texte d'un choix de modèle, celui que juge un cas de configuration `model`
        (PROCEDURE-2 §4) : « options : A | B | C ; choisi : X ; relu : Y ; micro : Z », Z l'état du bouton
        de dictée (v2.3, E4 : son data-status, ou « absent »)."""
        options = ' | '.join(record.get('options') or []) or 'non relevées'
        mic = f" ; micro : {record.get('mic') or 'absent'}"
        if record.get("refused"):  # the judge sees why nothing was chosen, candidates included
            why = (f"ambigu entre : {' | '.join(record['candidates'])}" if record.get("ambiguous")
                   else f"refusé : {record['refused']}")
            return f"options : {options} ; choisi : aucun ; {why} ; relu : {record.get('read_back')}{mic}"
        chosen = (record.get("picked") or {}).get("text") or record.get("read_back")
        return f"options : {options} ; choisi : {chosen} ; relu : {record.get('read_back')}{mic}"

    def choisir_modele(self, args: dict, ev: Evidence) -> dict:
        """Choisir le modèle du compositeur (_set_model) ; model.json, et l'observation `@model` qui
        juge un cas de configuration `model`. Le choix reste et devient la configuration épinglée :
        l'action ne restaure rien (reversible: false)."""
        want = args["model"]
        page = self.browser.page()
        self._ensure_on_app(page)
        try:
            record = self._set_model(page, want)
        except DriverRefusal as r:
            if r.extra.get("record"):
                ev.json("model.json", r.extra["record"])
                # A refused choice is observed too: a configuration case judged on @model then sees
                # it (an ambiguous label lists its candidates), never an older success.
                rec = r.extra["record"]
                self._shared("@model", {"answer": self.model_text(rec), "timing_s": None,
                                        "model": rec.get("read_back"), "options": rec.get("options"), "t": now_iso(),
                                        "composer": {"model": rec.get("read_back"), "web": None, "mic": rec.get("mic")}})
            raise
        ev.json("model.json", record)
        text = self.model_text(record)
        self._shared("@model", {"answer": text, "timing_s": record.get("seconds"), "model": record["read_back"],
                                "options": record["options"], "t": now_iso(), "composer": self._composer_now(page)})
        self.pinned["model"] = (record.get("picked") or {}).get("text") or want
        return {"model": record["read_back"], "previous": record["previous"], "changed": record["changed"],
                "options": record["options"], "picked": (record.get("picked") or {}).get("text"),
                "mic": record["mic"], "text": text}

    def _set_web(self, page, want: str) -> dict:
        """Cliquer le bouton « Web search: … » (data-mode) jusqu'à afficher `want` (un cycle, quatre
        clics au plus) ; s'il ouvre un menu, choisir l'entrée du mode. Rend l'enregistrement de
        web.json ; refus `precondition` (l'enregistrement dans `extra`) si le mode relu diffère."""
        b = self.browser
        t0 = time.monotonic()
        control = self._control(page, SELECTORS["web_button"], kind="web")
        if control is None:
            raise DriverRefusal("precondition", "pas de bouton de recherche web (« Web search: … ») dans le compositeur")
        previous = web_mode_of(control)
        seen, clicks, menu = [previous], 0, False
        while control is not None and web_mode_of(control) != want and clicks < 4:
            page.locator(control["sel"]).nth(control["index"]).click(timeout=5000)
            clicks += 1
            self._after_click(page)  # round 2
            page.wait_for_timeout(200)
            items = b.call(OPTIONS_JS, {"list": SELECTORS["web_menu"], "option": SELECTORS["web_menu_item"]}, page=page)
            if items:  # a menu rather than a cycle: pick the mode's entry
                menu = True
                pick = next((o for o in items if web_word(o["value"]) == want or web_word(o["text"]) == want), None)
                if pick is None:
                    self._close_list(page, SELECTORS["web_menu"])
                    break
                page.locator(f'{SELECTORS["web_menu"]} >> visible=true').last.locator(
                    SELECTORS["web_menu_item"]).nth(pick["index"]).click(timeout=5000)
                self._after_click(page)
                self._close_list(page, SELECTORS["web_menu"])
            control = self._control(page, SELECTORS["web_button"], kind="web")
            seen.append(web_mode_of(control))
        read_back = web_mode_of(control)
        record = {"asked": want, "previous": previous, "read_back": read_back, "clicks": clicks, "menu": menu,
                  "seen": seen, "label": control["label"] if control else None,
                  "seconds": round(time.monotonic() - t0, 1)}
        if read_back != want:
            raise DriverRefusal("precondition", f"recherche web relue « {read_back} » au lieu de « {want} »",
                                extra={"read_back": read_back, "record": record})
        return record

    def recherche_web(self, args: dict, ev: Evidence) -> dict:
        """Régler la recherche web du compositeur (_set_web) ; web.json, et l'observation `@web` qui
        juge un cas de configuration `web` (« mode : X (trouvé : Y) »). Le réglage reste et devient
        la configuration épinglée : l'action ne restaure rien (reversible: false)."""
        want = args["mode"]
        page = self.browser.page()
        self._ensure_on_app(page)
        try:
            record = self._set_web(page, want)
        except DriverRefusal as r:
            if r.extra.get("record"):
                ev.json("web.json", r.extra["record"])
            raise
        ev.json("web.json", record)
        text = f"mode : {record['read_back']} (trouvé : {record['previous']})"
        self._shared("@web", {"answer": text, "timing_s": record["seconds"], "mode": record["read_back"],
                              "t": now_iso(), "composer": self._composer_now(page)})
        self.pinned["web"] = want
        clicks = record["clicks"]
        return {"mode": record["read_back"], "previous": record["previous"], "changed": record["read_back"] != record["previous"],
                "clicks": clicks, "text": f"{text}, {clicks} clic{'s' if clicks > 1 else ''}"}

    def conversation_longue(self, args: dict, ev: Evidence) -> dict:
        """Un cas `long` : `turns` questions dans la conversation en cours (first, filler, last),
        chacune lue comme poser_question (fin de réponse, en-tête, texte envoyé) ; turns.json
        garde chaque tour, verdict_oracle juge le dernier. L'abandon d'un tour est journalisé KO
        disponibilité, arrêt cliqué, comme poser_question."""
        cid, n = args["case"], int(args["turns"])
        case = self._case(cid)
        if case["kind"] != "long":
            raise DriverRefusal("precondition", f"le cas {cid} ({case['kind']}) n'est pas une conversation longue")
        try:
            questions = long_questions(case, n)
        except ValueError as e:
            raise DriverRefusal("precondition", str(e)) from None
        self._case_in_hand = cid
        self.observations[cid] = {}
        page = self.browser.page()
        self._ensure_on_app(page)
        composer = self._composer_now(page)
        conv_before = self._conversation_state(page)
        mark = self.browser.mark()
        self._last_asked = {"case": cid, "mark": mark}
        budget = budget_of(case)
        turns, obs, failure, notices_all = [], {}, None, []
        for i, q in enumerate(questions, 1):
            try:
                obs = self._ask(q, budget, pii=case.get("pii"))
            except AskTimeout as t:
                why = f"{t.why} au tour {i}/{n}"
                timeout = {"seconds": round(t.seconds, 1), "turn": i, "t": now_iso(), **t.profile}
                self.observations[cid] = {"turns": turns, "timeout": timeout}
                # m3: the conversation first, so that the timeout line names it
                conversation = self._note_conversation_quietly(cid, conv_before, page, "conversation_longue")
                if conversation:
                    self.observations[cid]["conversation"] = conversation
                seen = {"composer": composer, "turns": turns, **({"conversation": conversation} if conversation else {})}
                if t.notices.get("internal_error_shown"):  # round 2, leakage-1
                    seen["internal_error_shown"] = self.observations[cid]["internal_error_shown"] = True
                if t.notices.get("raw"):
                    ev.json("notices.json", t.notices["raw"])
                ev.json("turns.json", {"case": cid, "asked_turns": n, "turns": turns, "timeout": timeout})
                if failure:
                    ev.json("failure.json", failure)
                flag = {"internal_error_shown": True} if seen.get("internal_error_shown") else {}  # round 3, M-9
                if self._dialog_seen():  # round 4, R-2: no timeout line; the judge makes it KO sécurité
                    raise DriverRefusal("timeout", why, extra={"abort": t.profile, "turns": len(turns), **flag},
                                        evidence=ev.items) from None
                line = self._timeout_line(cid, case["nfr"], t, why, extra=self._line_extra(cid, seen))
                raise DriverRefusal("timeout", why, extra={"journal": line, "abort": t.profile, "turns": len(turns),
                                                           **flag}, evidence=ev.items) from None
            failure = obs.pop("failure_raw", None) or failure  # the last turn left without answer (E10)
            raw_notices = obs.pop("notices_raw", None)
            if raw_notices:  # round 2, leakage-1: each turn's error notices, in the evidence only
                notices_all.extend(raw_notices)
            turns.append(turn_summary(i, q, obs))
        conversation = self._note_conversation(cid, conv_before, page, "conversation_longue")
        self.conv["asked"].append(cid)
        slowest = max(turns, key=lambda t: t["total_s"] or 0)  # timing_s: the slowest turn (PROCEDURE-2)
        self.observations[cid] = {**obs, "html": None, "turns": turns, "composer": composer,
                                  "timing_s": slowest["total_s"], "slowest_turn": slowest["turn"],
                                  **({"conversation": conversation} if conversation else {})}
        ev.json("turns.json", {"case": cid, "asked_turns": n, "turns": turns, "slowest_turn": slowest["turn"]})
        if failure:
            ev.json("failure.json", failure)
        if notices_all:
            ev.json("notices.json", notices_all)
        self._answer_png(ev, self.browser.page())
        internal = any(t.get("internal_error_shown") for t in turns)  # I3, m11: any turn
        answer = obs.get("answer") or ""
        shown = sanitise(answer, 140) if answer_shows_internal(answer) else one_line(answer, 140)
        return {"case": cid, "turns": len(turns), "answer": sanitise(answer, 4000) if answer_shows_internal(answer) else answer,
                "total_s": [t["total_s"] for t in turns], "internal_error_shown": internal,
                "text": f"{cid} : {len(turns)} tours, le dernier en {obs.get('total_s')} s — {shown}"
                        + (" ; erreur interne affichée à l'utilisateur" if internal else "")}

    def capture_ecran(self, args: dict, ev: Evidence) -> dict:
        """Une capture de la partie visible de la page."""
        page = self.browser.page()
        ev.screenshot(page, "screen.png")
        return {"url": page.url, "text": f"capture de {short_url(page.url)}"}

    def exporter_journal(self, args: dict, ev: Evidence) -> dict:
        """Les lignes du journal ajoutées depuis l'export précédent (curseur dans
        journal.cursor), en preuve et en texte pour le registre."""
        self._late_events()
        lines = []
        if self.journal_path.exists():
            lines = [x for x in self.journal_path.read_text(encoding="utf-8").splitlines() if x.strip()]
        start = int(self.cursor_path.read_text().strip() or 0) if self.cursor_path.exists() else 0
        take, size = [], 0
        for line in lines[start:]:
            size += len(line.encode("utf-8")) + 1
            if size > PER_FILE * 3 // 4:
                break
            take.append(line)
        if take:
            ev.add("journal.jsonl", ("\n".join(take) + "\n").encode("utf-8"))
        self.cursor_path.write_text(str(start + len(take)))
        rest = len(lines) - start - len(take)
        text = "\n".join(take) if take else "journal : aucune ligne nouvelle depuis le dernier export"
        return {"lines": len(take), "remaining": rest, "text": text}


def main() -> int:
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        recette = Recette()
    except (ValueError, OSError) as e:
        log.error("not started: %s", e)
        return 2
    try:
        serve(NAME, recette.version, recette.actions(), on_ready=recette.open, idle=recette.browser.pump)
    finally:
        recette.browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
