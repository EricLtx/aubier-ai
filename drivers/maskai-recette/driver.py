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
  driver ouvre et referme lui-même dans la même action (`sent.json`, D4) ;
- au délai d'abandon, le driver clique sur le bouton d'arrêt avant de rendre la
  main : aucune génération laissée en vol (D2), et la ligne dit ce qui était
  parti (D8) ;
- trois actions de plus : choisir_modele et recherche_web règlent le compositeur
  et relisent ce qu'il affiche ; conversation_longue pose n questions dans la
  même conversation. Les cas `model`, `web` et `long` les utilisent ;
- `ui_profile_sha256`, l'empreinte de `SELECTORS` et `KNOWN_MODALS`, est sur
  chaque ligne de journal : un changement de sélecteur s'y voit, ce que
  `inputs_sha256` ne montre pas (D12) ;
- les captures masquent la barre latérale (l'historique) et le bloc du compte ;
- un cas sans `budget_s` prend le budget de sa classe (`CLASS_BUDGET_S`).

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
    # Bouton « nouvelle conversation » (sélecteurs Playwright) ; à défaut, retour à ENTRY_URL.
    # Le premier vu le 17/10 ; les suivants à confirmer.
    "new_conversation": ['a[href="/new"][data-slot="button"]',
                         '[aria-label*="Nouvelle"]', 'button:has-text("Nouvelle conversation")',
                         '[aria-label*="New chat" i]', 'button:has-text("New chat")',
                         'a:has-text("New chat")', '[aria-label*="New conversation" i]'],
    # Bouton d'envoi quand Entrée n'envoie pas (CSS). À confirmer.
    "send_button": ['button[aria-label*="Envoyer"]', 'button[aria-label*="Send" i]',
                    'button[type="submit"]'],
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
    "model_banner": r"\b[a-z0-9][a-z0-9_-]*/[a-z0-9][a-z0-9._:-]*",
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
    # Texte envoyé au modèle. Vu le 17/10 : la pastille « Chiffré / Encrypted — no PII detected… »
    # du compositeur, déclencheur de popover. À confirmer : que son popover montre le texte masqué,
    # son bloc de texte, sa croix. Le déclencheur retenu est celui du compositeur (le plus proche de
    # la zone de saisie), jamais celui d'une carte de réponse (« Détails »).
    "sent_trigger": 'button[aria-haspopup="dialog"][data-slot="popover-trigger"]',
    "sent_popover": '[role="dialog"][data-slot="popover-content"]',
    "sent_text": ['[data-slot*="sent"]', 'pre', 'code', 'blockquote'],
    "popover_close": ('[data-slot="popover-close"], button[aria-label*="Fermer" i], '
                      'button[aria-label*="Close" i]'),
    # Libellé du panneau « texte envoyé au modèle » : « SENT TO THE LLM » vu le 13/10, disparu le
    # 17/10 ; le secours du popover.
    "sent_label": r"sent to the llm|envoy[ée] au mod[èe]le|texte envoy[ée]",
    # Compositeur (v2) : le bouton « Model: <modèle> » qui ouvre une liste d'options, le bouton
    # « Web search: <mode> » et son data-mode (relevés le 17/10 par le fondateur). À confirmer : le
    # rôle de la liste (listbox ou menu), le mode web en cycle ou en menu, les mots des modes.
    "model_button": ['button[aria-label^="Model:"]', 'button[aria-label^="Modèle"]'],
    "model_prefix": r"^\s*(?:model|modèle)\s*:\s*",
    "listbox": '[role="listbox"]',
    "option": '[role="option"]',
    "web_button": ['button[aria-label^="Web search:"]', 'button[aria-label^="Recherche web"]'],
    "web_menu": '[role="menu"], [role="listbox"]',
    "web_menu_item": '[role="menuitemradio"], [role="menuitem"], [role="option"]',
    "web_words": {"off": r"\boff\b|désactiv|jamais|never", "auto": r"\bauto", "on": r"\bon\b|activ|toujours|always"},
    # Zones masquées sur toute capture (Playwright `mask`) : la barre latérale, qui montre l'historique
    # des conversations, et le bloc du compte en bas. Vu le 17/10 : nav[aria-label="Main navigation"]
    # dans un aside, le compte en bas de la barre (les captures publiées du 17/10 le montraient, D10) ;
    # les data-slot sont à confirmer.
    "mask": ['nav[aria-label="Main navigation"]', 'aside', '[data-slot="sidebar"]',
             '[data-slot="sidebar-footer"]', '[data-slot="avatar"]'],
    # Fenêtres modales (CSS).
    "modal": '[role="dialog"], [role="alertdialog"], [aria-modal="true"], .modal',
}
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
]
# Motif de l'URL du flux de réponse (annexe A) : /api/chat/stream chez MaskAI.
STREAM_PATTERN = r"chat/stream"

PER_FILE = 2 * 1024 * 1024        # HARNESS_MAX_FILE_BYTES
PER_ACTION = 4 * 1024 * 1024      # HARNESS_MAX_RUN_BYTES / 4
DOM_CAP = 1024 * 1024
NETWORK_CAP = 1024 * 1024
DEFAULT_BUDGET_S = 60
# Budget de latence par classe de cas, pour un cas sans `budget_s` (cases.json garde les siens) :
# les classes du debrief du 17/10 (§3) ; une pièce jointe sans budget prend celui des lourdes.
CLASS_BUDGET_S = {"load": 3, "message": 10, "model": 10, "web": 10, "long": 10, "list": 10,
                  "attach_question": 60, "concurrent": 60}
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
# Recherche web coupée : une étape web_search plus longue que cela est une recherche faite.
WEB_OFF_MAX_MS = 1000
WEB_MODES = ("off", "auto", "on")
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

POLL_JS = "(a) => {" + _VISIBLE + _PICK + """
  const ev = window.__ev || [];
  const msgs = pick(a.assistant);
  const last = msgs.length ? msgs[msgs.length - 1] : null;
  const body = last ? last.querySelector(a.body) : null;
  const re = new RegExp(a.stopText, 'i');
  let stop = a.stop.some((s) => [...document.querySelectorAll(s)].some(visible));
  if (!stop) stop = [...document.querySelectorAll('button')].some((b) => visible(b) && re.test(b.textContent || ''));
  return { n: ev.length, ev: ev.length < a.from ? ev : ev.slice(a.from), count: msgs.length,
           text: body ? body.innerText : (last ? last.innerText : ''), body: !!body, stop };
}"""

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

CLICK_STOP_JS = "(a) => {" + _VISIBLE + """
  const re = new RegExp(a.stopText, 'i');
  for (const s of a.sels) for (const b of document.querySelectorAll(s)) if (visible(b) && !b.disabled) { b.click(); return true; }
  for (const b of document.querySelectorAll('button')) if (visible(b) && !b.disabled && re.test(b.textContent || '')) { b.click(); return true; }
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

CLICK_SEND_JS = "(a) => {" + _VISIBLE + """
  for (const s of a.sels) for (const b of document.querySelectorAll(s)) if (visible(b) && !b.disabled) { b.click(); return true; }
  return false;
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

# Le premier élément visible d'une liste de sélecteurs : le sélecteur, son rang parmi ses
# correspondances, son aria-label, son texte, son data-mode.
CONTROL_JS = "(a) => {" + _VISIBLE + """
  for (const s of a.sels) {
    const els = [...document.querySelectorAll(s)];
    const i = els.findIndex(visible);
    if (i < 0) continue;
    const el = els[i];
    return { sel: s, index: i, label: (el.getAttribute('aria-label') || '').trim(),
             text: (el.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 200),
             mode: el.getAttribute('data-mode') || '', expanded: el.getAttribute('aria-expanded') === 'true' };
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
    """L'empreinte du profil d'interface du driver : SELECTORS et KNOWN_MODALS en JSON canonique
    (clés triées, sans espaces, UTF-8). Sur chaque ligne de journal : deux lignes qui la portent
    ont été lues avec les mêmes adresses de page et les mêmes modales connues (D12)."""
    profile = {"known_modals": KNOWN_MODALS, "selectors": SELECTORS}
    data = json.dumps(profile, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def budget_of(case: dict) -> float:
    """Le budget de latence d'un cas : le sien, sinon celui de sa classe, sinon DEFAULT_BUDGET_S."""
    return case.get("budget_s") or CLASS_BUDGET_S.get(case.get("kind"), DEFAULT_BUDGET_S)


def parse_guard(card: str, body: str, body_found: bool) -> dict:
    """L'en-tête d'une carte de réponse (la carte hors de son corps) en champs : le modèle de la
    bannière, le texte du panneau de garde, PII détectée ou non (None : rien de lisible), les
    jetons de masquage, la marque « en direct » après la bannière. Forme vue le 17/10 :
    « <panneau de garde> maskai <fournisseur>/<modèle>[ · en direct] <corps> »."""
    card_l, body_l = one_line(card, 100_000), one_line(body, 100_000)
    head, sep, tail = card_l.rpartition(body_l) if body_found and body_l else ("", "", card_l)
    header = f"{head} {tail}" if sep else card_l  # the body is the card's last block
    header = one_line(header, 100_000)
    m = re.search(SELECTORS["model_banner"], header)
    model, guard_text, after = None, header, ""
    if m:
        model = m.group(0)
        guard_text = re.sub(SELECTORS["brand"], "", header[:m.start()], flags=re.I).strip()
        after = header[m.end():]
    if re.search(SELECTORS["guard_pii"], guard_text, re.I):
        pii = True
    elif re.search(SELECTORS["guard_clean"], guard_text, re.I):
        pii = False
    else:
        pii = None
    return {"model": model, "guard_text": guard_text[:600] or None, "pii_detected": pii,
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
    """L'option à choisir pour `want`, et toutes celles qui convenaient : égalité du texte ou de
    la valeur, sinon `want` contenu dans le texte ou la valeur, sinon un texte d'au moins quatre
    caractères contenu dans `want` ; la première dans l'ordre de la liste."""
    w = norm_label(want)
    shown = [o for o in options if o.get("visible", True)] or options
    for test in (lambda o: w in (norm_label(o["text"]), norm_label(o["value"])),
                 lambda o: w in norm_label(o["text"]) or (o["value"] and w in norm_label(o["value"])),
                 lambda o: (len(norm_label(o["text"])) >= 4 and norm_label(o["text"]) in w)
                 or (len(norm_label(o["value"])) >= 4 and norm_label(o["value"]) in w)):
        found = [o for o in shown if w and test(o)]
        if found:
            return found[0], found
    return None, []


def model_of(control: dict | None) -> str | None:
    """Le modèle affiché par le bouton du compositeur : son aria-label sans « Model: »."""
    if not control:
        return None
    return re.sub(SELECTORS["model_prefix"], "", control["label"], flags=re.I).strip() or control["text"] or None


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
    """Les questions d'un cas `long` : `first`, puis `filler` pour chaque tour du milieu ({i} est
    le rang du tour), puis `last`."""
    first, filler, last = case.get("first"), case.get("filler"), case.get("last")
    if turns < 2 or not first or not last or (turns > 2 and not filler):
        raise ValueError(f"case {case['id']}: a long case needs first, last, filler and turns >= 2")
    return [first] + [filler.format(i=i) for i in range(2, turns)] + [last]


def turn_summary(i: int, question: str, obs: dict) -> dict:
    guard = obs.get("guard") or {}
    return {"turn": i, "question": question, "answer": (obs.get("answer") or "")[:4000],
            "ttft_s": obs.get("ttft_s"), "total_s": obs.get("total_s"), "display_s": obs.get("display_s"),
            "display_stable": obs.get("display_stable"), "end": obs.get("end"), "model": guard.get("model"),
            "pii_detected": guard.get("pii_detected"), "sent_source": (obs.get("sent_info") or {}).get("source"),
            "sent_to_model": (obs.get("sent_to_model") or "")[:4000] or None, "pipeline_ms": obs.get("pipeline_ms")}


def looks_like_sent(shown: str | None, question: str) -> bool:
    """Le texte du popover est-il le message envoyé (masqué ou non), et pas un simple statut
    (« Encrypted — no PII detected ») ? Au moins la moitié des mots de quatre lettres ou plus de la
    question doivent s'y retrouver (un mot masqué est remplacé par un jeton, les autres restent).
    Sinon la fuite de PII n'est pas vérifiable, et le verdict le dit, plutôt que de juger un
    statut comme si c'était le texte envoyé."""
    words = set(re.findall(r"\w{4,}", evaluer.fold(question or "")))
    if not shown or not words:
        return False
    seen = evaluer.fold(shown)
    return sum(w in seen for w in words) * 2 >= len(words)


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
    if p.get("stop_skipped"):
        parts.append("arrêt non cliqué : le sélecteur d'arrêt désignait un bouton déjà visible avant l'envoi")
    elif p.get("stop_clicked"):
        parts.append("arrêt cliqué" + ("" if p.get("stop_gone") else ", bouton encore visible"))
    else:
        parts.append("pas de bouton d'arrêt visible")
    return ", ".join(parts)


ORACLE_FILES = ("corpus-v2/oracles-v2.json", "corpus/oracles.json")
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
        masks = [page.locator(sel) for sel in SELECTORS["mask"]]
        self.add(name, page.screenshot(full_page=False, type="png", timeout=10_000, mask=masks,
                                       mask_color=MASK_COLOR))


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
            self.posts.append({"seq": e["seq"], "page": e["page"], "body": (request.post_data or "")[:20_000]})

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


class AskTimeout(Exception):
    def __init__(self, seconds: float, why: str):
        super().__init__(why)
        self.seconds, self.why = seconds, why
        self.profile = {}  # ce qui était parti, et le clic d'arrêt (Recette._abort)


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
        self.conv = self._fresh_conversation()
        self.flux = {"ev": 0, "mark": 0}
        self._case_in_hand = None
        self._mark = None  # where the action in hand began, in the browser's event sequence
        self._last_asked = None  # {"case", "mark"} of the last poser_question
        self._in_flight = False  # an abandon whose stop click did not end the generation

    def actions(self) -> dict:
        browser = ["mesurer_chargement", "nouvelle_conversation", "joindre_document", "poser_question",
                   "envoyer_message", "lire_reponse", "capturer_flux", "lister_conversations",
                   "charge_concurrente", "capture_ecran", "choisir_modele", "recherche_web",
                   "conversation_longue"]
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
            try:
                if browser:
                    self._guard()  # delivers what happened between actions, before the mark
                    self._mark = self.browser.mark()
                result = fn(args, ev)
                if browser:
                    self._after()
                if ev.dropped:
                    result["evidence_dropped"] = ev.dropped
                return result, ev.items
            except DriverRefusal as r:
                r.evidence = r.evidence or ev.items
                raise
            except Exception as e:
                if browser and self.browser.started():
                    if self.browser.pending():
                        raise self._dialog_refusal(ev) from e
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

    def _guard(self) -> None:
        """Avant toute action dans le navigateur : pas de dialogue en attente, pas de modale
        inconnue ; une modale connue est fermée par son bouton."""
        b = self.browser
        page = b.page()
        b.settle()  # all of the waiting period's events land before the action's mark
        if b.pending():
            if b.responsive():
                log.info("pending dialog gone (closed in Chrome): guard cleared")
                b.dialogs.clear()
            else:
                raise self._dialog_refusal()
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
        if b.own_modal and b.own_modal not in modals["known"]:
            b.own_modal = None  # closed since, by the page or by hand
        if modals["unknown"]:
            raise DriverRefusal("dialog", f"modale inconnue : {modals['unknown'][0]}")

    @staticmethod
    def _dismiss(page, k: dict) -> None:
        """Fermer une modale connue par son moyen documenté : une touche (« key:Escape ») ou un
        bouton ; jamais « OK » ni « Envoyer »."""
        if k["dismiss"].startswith("key:"):
            page.keyboard.press(k["dismiss"][4:])
            page.wait_for_timeout(200)
        else:
            page.locator(k["dismiss"]).first.click(timeout=5000)

    def _after(self) -> None:
        b = self.browser
        b.drain(50)
        if b.pending():
            raise self._dialog_refusal()
        self._check_host()

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
        if self._case_in_hand:
            self.observations.setdefault(self._case_in_hand, {})["offlist"] = {
                "host": nav["host"], "url": nav["url"], "t": now_iso()}
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

    def _dialog_refusal(self, ev: Evidence | None = None) -> DriverRefusal:
        d = self.browser.pending()
        detail = f"dialogue {d['type']} : {d['message']}" if d else "dialogue en attente"
        if self._case_in_hand:
            self.observations.setdefault(self._case_in_hand, {})["dialog"] = {
                "type": d["type"] if d else "", "message": d["message"] if d else "", "t": now_iso()}
        return DriverRefusal("dialog", detail, evidence=ev.items if ev else None)

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
        except PWError:
            if not check:
                raise
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
                     pipeline_ms: dict | None = None, extra: dict | None = None) -> str:
        event = journal.add(str(self.journal_path), argparse.Namespace(
            step="exec", case=clean(cid), verdict=verdict,
            timing=round(timing_s, 1) if isinstance(timing_s, (int, float)) else None,
            evidence=clean(evidence)[:600],
            pipeline=[f"{k}={int(v)}" for k, v in (pipeline_ms or {}).items()] or None,
            nfr=",".join(nfr) or None, supersedes=None),
            extra={"inputs_sha256": self.inputs_sha256, "ui_profile_sha256": self.ui_profile_sha256,
                   **(extra or {})})
        return json.dumps(event, ensure_ascii=False)

    # -- envoyer un message et attendre la réponse -------------------------------------------
    def _send(self, page, text: str, read_sent: bool = True) -> dict:
        """Annexe B : setter natif, événement input, un espace tapé ; puis (read_sent) le popover
        « Chiffré » lu et refermé, le focus rendu à la zone de saisie ; puis Entrée (ou le bouton
        d'envoi si Entrée n'a rien envoyé). Rend le tour à surveiller."""
        b = self.browser
        st = b.call(POLL_JS, self._poll_arg(0), page=page)
        # stop_before: the stop selectors already match something before anything is sent; that is
        # no generation indicator (a guessed selector on a permanent button): ignored, never clicked.
        turn = {"page": page, "n0": st["n"], "count0": st["count"], "posts0": len(b.posts),
                "page_no": b.context.pages.index(page), "stop_before": st["stop"], "text": text}
        if not b.call(FILL_JS, {"sel": SELECTORS["textarea"], "text": text}, page=page):
            raise DriverRefusal("precondition", "pas de zone de saisie dans la page")
        page.bring_to_front()
        page.keyboard.type(" ")
        if read_sent:
            turn["sent_popover"] = self._read_sent_popover(page)
            self._refocus(page, text)
        turn["t_send"] = time.monotonic()
        page.keyboard.press("Enter")
        page.wait_for_timeout(300)
        left = b.call(VALUE_JS, {"sel": SELECTORS["textarea"]}, page=page)
        if left and left.strip() and len(b.posts) == turn["posts0"]:
            b.call(CLICK_SEND_JS, {"sels": SELECTORS["send_button"]}, page=page)
        return turn

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
        refus `dialog` (la question ne part pas)."""
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
                info.update(source="popover", text=content["text"], popover_text=content["popover"],
                            block_found=content["block"])
            else:
                info["missing"] = "popover"
        except PWTimeout:
            if b.pending():
                raise
            info["error"] = "timeout"
        except PWError as e:
            info["error"] = one_line(str(e), 200)
        finally:
            if b.own_modal == "envoi" and not b.pending():
                info["closed"] = self._close_sent_popover(page, trig["index"] if trig else None)
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

    @staticmethod
    def _poll_arg(start: int) -> dict:
        return {"from": start, "assistant": SELECTORS["assistant_message"], "body": SELECTORS["answer_body"],
                "stop": SELECTORS["stop_button"], "stopText": SELECTORS["stop_text"]}

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
                     end_at=None, display_stable=None, display_s=None)
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
        en champs (guard), le HTML de la carte, les étapes serveur, le texte envoyé (popover lu à
        l'envoi, sinon le panneau du 13/10, sinon rien)."""
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
        sent_info = dict(turn.get("sent_popover") or {"source": None})
        if sent_info.get("source") == "popover" and not looks_like_sent(sent_info.get("text"), turn.get("text", "")):
            sent_info.update(source=None, rejected="le popover ne montre pas le texte de la question")
        if sent_info.get("source") == "popover":
            sent_to_model = sent_info["text"]
        elif panel is not None:
            sent_to_model = panel
            sent_info.update(source="panel", text=panel)
        else:
            sent_to_model = None
        sent_info["panel_text"] = panel
        return {"answer": got["text"].strip(), "html": got["html"], "guard": guard, "body_found": got["body"],
                "new_answer": fresh, "sent": posts[-1]["body"] if posts else None,
                "sent_to_model": sent_to_model, "sent_info": sent_info,
                "ttft_s": ttft, "total_s": turn.get("total_s"), "timing_s": turn.get("total_s"),
                "display_s": turn.get("display_s"), "display_stable": turn.get("display_stable"),
                "server_steps": steps, "pipeline_ms": pipeline, "end": turn.get("end"), "t": now_iso()}

    def _stop_generation(self, page) -> dict:
        """Cliquer sur le bouton d'arrêt s'il est visible, puis attendre sa disparition (au plus
        STOP_WAIT_S). Une génération arrêtée n'est pas laissée en vol : l'enquête « Votre avis »
        suivait chaque abandon du 17/10 (D2)."""
        b = self.browser
        try:
            clicked = b.call(CLICK_STOP_JS, {"sels": SELECTORS["stop_button"], "stopText": SELECTORS["stop_text"]},
                             page=page, timeout_ms=2000)
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
        if turn.get("stop_before"):  # the stop selectors match a button that was there before: no click
            profile.update(stop_clicked=False, stop_skipped=True)
            log.warning("abandon: stop not clicked, the stop selectors matched before sending")
        else:
            profile.update(self._stop_generation(turn["page"]))
            # clicked but still visible, or nothing to click: maybe still in flight; the next
            # nouvelle_conversation tries again
            self._in_flight = not profile.get("stop_gone")
        return profile

    def _ask(self, text: str, budget_s: float) -> dict:
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
        turn = self._send(page, text)
        try:
            self._wait([turn], wait_s)
        except AskTimeout as t:
            t.why = (f"abandon à {t.seconds:.0f} s ("
                     + ("échéance de l'action" if capped else f"3 × budget {budget_s:g} s") + ")")
            t.profile = self._abort(turn)
            raise
        obs = self._collect(turn)
        self._check_host()  # a page that left after <END> is not the answer
        return obs

    def _answer_evidence(self, ev: Evidence, obs: dict, mark: int) -> None:
        page = self.browser.page()
        if obs.get("html"):
            ev.html("dom.html", obs["html"])
        ev.json("guard.json", {**(obs.get("guard") or {}), "body_found": obs.get("body_found"),
                               "display_stable": obs.get("display_stable")})
        ev.json("sent.json", {**(obs.get("sent_info") or {"source": None}),
                              "post_body": (obs.get("sent") or "")[:20_000] or None})
        ev.json("network.json", self.browser.capture(mark), cap=NETWORK_CAP)
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
        result["text"] = f"{label} : réponse en {obs.get('total_s')} s — {one_line(obs.get('answer') or '', 160)}"
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
        ev.json("timing.json", t)
        ev.screenshot(page, "screen.png")
        self.observations["@load"] = {"answer": result["text"], "timing_s": result["load_ms"] / 1000,
                                      "t": now_iso()}
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
        for sel in SELECTORS["new_conversation"]:
            loc = page.locator(sel).first
            try:
                if loc.count() and loc.is_visible():
                    loc.click(timeout=5000)
                    clicked = True
                    break
            except PWError:
                continue
        if not clicked:
            self._goto(page, self.entry)
        page.locator(SELECTORS["textarea"]).first.wait_for(state="visible", timeout=15_000)
        b.call("() => {" + INTERCEPTOR_JS + " window.__ev = []; return true; }")
        self.conv = self._fresh_conversation()
        self.flux["ev"] = 0
        self._last_asked = None
        how = "bouton" if clicked else "retour à la page d'entrée"
        if stopped:
            how += ", génération en vol arrêtée d'abord"
        return {"conversation": page.url, "stopped_first": stopped,
                "text": f"nouvelle conversation ({how}) : {short_url(page.url)}"}

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
        mark = b.mark()
        att = self._attach(page, path, budget_of(case))
        self.conv["attached"][cid] = {k: att[k] for k in ("state", "ready_by", "pending_seen")} | {"file": path.name}
        self.observations[cid] = {"attach": {k: att[k] for k in ("state", "ready_by", "seconds", "label", "trace")}
                                  | {"t": now_iso()}}
        ev.screenshot(page, "attach.png")
        ev.json("network.json", b.capture(mark), cap=NETWORK_CAP)
        extra = {k: att[k] for k in ("state", "ready_by", "seconds", "label", "trace", "uploads")}
        if att["state"] == "failed":
            raise DriverRefusal("precondition", "attachment failed", extra=extra)
        if att["state"] != "ready":
            raise DriverRefusal("precondition", f"pièce jointe « {att['label'] or 'sans puce'} » ({att['state']}) "
                                                f"après {att['seconds']} s", extra=extra)
        how = {"marker": "marquée prête", "after_pending": "indexée", "quiet": "sans marqueur, présumée prête"}
        return {**extra, "text": f"{path.name} prêt en {att['seconds']} s ({how.get(att['ready_by'], '')}, "
                                 f"{' → '.join(x['state'] for x in att['trace'])})"}

    def _read_chip(self, page, name: str):
        return self.browser.call(CHIP_JS, {"remove": SELECTORS["attach_remove"], "chips": SELECTORS["attach_chip"],
                                           "busy": SELECTORS["attach_busy"], "name": name}, page=page)

    def _attach(self, page, path: Path, budget_s: float) -> dict:
        """Le fichier dans le champ caché, puis la puce lue toutes les 250 ms jusqu'à un état final
        (ready, failed), au plus 3 × budget_s, borné par l'échéance de l'action."""
        b = self.browser
        mark = b.mark()
        page.locator(SELECTORS["file_input"]).first.set_input_files(str(path), timeout=10_000)
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
                "reading": reading, "uploads": uploads[-20:]}

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
        if case["kind"] not in ("message", "attach_question", "model", "web") or "question" not in case:
            hint = " : conversation_longue" if case["kind"] == "long" else ""
            raise DriverRefusal("precondition", f"le cas {cid} ({case['kind']}) n'a pas de question{hint}")
        composer = None
        if case["kind"] in ("model", "web"):  # the composer as it is now, read on screen
            composer = self._composer_now(self.browser.page())
            if case["kind"] == "model" and not model_matches(case["model"], composer["model"]):
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
                self.observations.setdefault(cid, {})["not_sent"] = {"chip": raw, "label": label, "t": now_iso()}
                raise DriverRefusal("precondition", f"pièce jointe pas prête ({raw}) : question non envoyée",
                                    extra={"chip": raw, "label": label})
        before = case.get("same_conversation_as")
        if before and before not in self.conv["asked"]:
            raise DriverRefusal("precondition", f"{cid} se pose dans la conversation de {before}, pas encore posé")
        self._case_in_hand = cid
        # A new attempt: only the attachment survives from the previous observation, so a
        # dialog or a timeout now is never judged on an older answer.
        attach = (self.observations.get(cid) or {}).get("attach")
        self.observations[cid] = {"attach": attach} if attach else {}
        mark = self.browser.mark()
        self._last_asked = {"case": cid, "mark": mark}  # what happens from here on is this case's
        budget = budget_of(case)
        try:
            obs = self._ask(case["question"], budget)
        except AskTimeout as t:
            line = self._journal_add(cid, "KO", t.seconds, f"{t.why} : pas de fin de réponse ; {abort_text(t.profile)}",
                                     nfr_union(case["nfr"], ["disponibilite"]))
            self.observations.setdefault(cid, {})["timeout"] = {"seconds": round(t.seconds, 1), "t": now_iso(),
                                                                **t.profile}
            raise DriverRefusal("timeout", t.why, extra={"journal": line, "abort": t.profile}) from None
        self.conv["asked"].append(cid)
        self.observations[cid] = {**obs, "html": None, **({"attach": attach} if attach else {}),
                                  **({"composer": composer} if composer else {})}
        self._answer_evidence(ev, obs, mark)
        return self._answer_result(obs, cid)

    def envoyer_message(self, args: dict, ev: Evidence) -> dict:
        """Comme poser_question, avec un texte libre (essais exploratoires) ; l'observation va
        sous « @message », hors cas."""
        text = args["text"]
        mark = self.browser.mark()
        try:
            obs = self._ask(text, DEFAULT_BUDGET_S)
        except AskTimeout as t:
            line = self._journal_add(f"message : {one_line(text, 60)}", "KO", t.seconds,
                                     f"{t.why} : pas de fin de réponse ; {abort_text(t.profile)}", ["disponibilite"])
            raise DriverRefusal("timeout", t.why, extra={"journal": line, "abort": t.profile}) from None
        self.observations["@message"] = {**obs, "html": None, "text": text}
        self._answer_evidence(ev, obs, mark)
        return self._answer_result(obs, "message")

    def lire_reponse(self, args: dict, ev: Evidence) -> dict:
        """Le texte du corps et le HTML de la dernière réponse affichée, l'en-tête en champs."""
        got = self.browser.call(ANSWER_JS, {"assistant": SELECTORS["assistant_message"], "body": SELECTORS["answer_body"]})
        if got["html"]:
            ev.html("dom.html", got["html"])
        guard = parse_guard(got["card"], got["text"], got["body"])
        return {"answer": got["text"].strip(), "model": guard["model"], "guard_text": guard["guard_text"],
                "pii_detected": guard["pii_detected"], "body_found": got["body"],
                "text": one_line(got["text"]) or "aucune réponse affichée"}

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
        return {"events": events[-200:], "console": capture["console"][-100:], "failed": failed[-100:],
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
        end, end_mono = last.get("end"), last.get("end_mono")

        def after(e) -> float | None:
            return round(e["mono"] - end_mono, 1) if end is not None and e["seq"] > end else None

        for d in b.dialog_log:
            if d["seq"] > last["mark"] and "dialog" not in obs:
                obs["dialog"] = {"type": d["type"], "message": d["message"], "t": d["t"]}
                if after(d) is not None:
                    obs["dialog"]["after_s"] = after(d)
        for n in b.since(b.offlist, last["mark"]):
            key = "offlist" if after(n) is None else "offlist_after"
            if key not in obs:
                obs[key] = {"host": n["host"], "url": n["url"], "t": n["t"]}
                if key == "offlist_after":
                    obs[key]["after_s"] = after(n)

    def verdict_oracle(self, args: dict, ev: Evidence) -> dict:
        """evaluer.py sur la dernière observation du cas, puis une ligne au journal (annexe C).
        Un cas déjà journalisé rend son verdict (replayed) et n'ajoute rien."""
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
        key = {"load": "@load", "list": "@list"}.get(case["kind"], cid)
        obs = self.observations.get(key) or {}
        asked = "answer" in obs
        attach = obs.get("attach") or {}
        dialog = obs.get("dialog")
        offlist = obs.get("offlist")
        nfr = case["nfr"]
        not_sent = obs.get("not_sent")
        if (not asked and not dialog and not offlist
                and (not_sent or (attach and attach.get("state") not in ("ready", "attached")))):
            label = (not_sent or {}).get("label") or attach.get("label") or "sans puce"
            if not_sent:
                note = (f"question non envoyée (précondition) : pièce jointe {not_sent['chip']} ({label})"
                        + (f" après {attach['seconds']} s" if attach.get("seconds") is not None else ""))
            elif attach.get("state") == "failed":
                note = f"pièce jointe en échec ({label})"
            else:
                note = f"pièce jointe pas prête : {attach.get('state')} ({label}) après {attach.get('seconds')} s"
            r = {"verdict": "KO", "notes": [note]}
            nfr = nfr_union(nfr, ["disponibilite"])
        elif not asked and not dialog and not offlist:
            hint = {"load": "mesurer_chargement", "list": "lister_conversations",
                    "concurrent": "charge_concurrente", "long": "conversation_longue"}.get(case["kind"], "poser_question")
            raise DriverRefusal("precondition", f"aucune observation pour {cid} : {hint} d'abord")
        elif asked:
            oracle = {"id": cid, "must": case["must"], "must_not": case["must_not"], "pii": case["pii"]}
            seen = {"answer": obs.get("answer"), "timing_s": obs.get("timing_s")}
            if obs.get("sent_to_model") is not None:
                seen["sent"] = obs["sent_to_model"]
            r = evaluer.evaluate_case(oracle, seen, budget_of(case))
            nfr = nfr_union(nfr, r.get("nfr") or [])  # a leak to the model is KO sécurité
            if r["verdict"] == "NON TESTÉ" and not dialog and not offlist:  # échec silencieux
                r["verdict"] = "KO"
                r["notes"].append("réponse vide : échec silencieux"
                                  + (" (aucune nouvelle carte de réponse)" if obs.get("new_answer") is False else ""))
                nfr = nfr_union(nfr, ["disponibilite"])
            if obs.get("body_found") is False and obs.get("answer"):
                r["notes"].append("corps de réponse introuvable : texte de la carte entière, en-tête compris")
            if obs.get("display_stable") is False:
                r["notes"].append("affichage encore en mouvement à la capture")
            nfr = self._kind_checks(case, obs, r, nfr)
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
            r["notes"].insert(0, f"dialogue inattendu ({dialog['type']}) : {dialog['message']}{when}")
            nfr = nfr_union(nfr, ["securite"])
        if obs.get("offlist_after"):  # après la fin de l'action : une note, pas un KO (R20)
            o = obs["offlist_after"]
            r["notes"].append(f"navigation hors périmètre après la réponse : {o['host']} (+{o['after_s']:g} s)")
        line = self._journal_add(cid, r["verdict"], obs.get("timing_s"), self._summary(r, obs), nfr,
                                 obs.get("pipeline_ms"),
                                 {"display_s": obs["display_s"]} if obs.get("display_s") is not None else None)
        self.observations.setdefault(key, obs)["verdict"] = {k: r[k] for k in ("verdict", "missing", "forbidden", "leaks", "span_issues", "notes")}
        if key != cid:
            self.observations[cid] = {"verdict": self.observations[key]["verdict"], "from": key}
        ev.add("journal.jsonl", (line + "\n").encode("utf-8", "replace"))
        return {"case": cid, "verdict": r["verdict"], "missing": r["missing"], "forbidden": r["forbidden"],
                "leaks": r["leaks"], "span_issues": r["span_issues"], "replayed": False, "text": line}

    @staticmethod
    def _kind_checks(case: dict, obs: dict, r: dict, nfr: list) -> list:
        """Ce que les cas model, web et long vérifient en plus de l'oracle (v2) ; rend les ENF."""
        kind = case["kind"]
        if kind == "model":
            seen = (obs.get("guard") or {}).get("model")
            chosen = (obs.get("composer") or {}).get("model") or case["model"]
            if not model_matches(case["model"], seen):
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
            r["notes"].append(f"{len(turns)} tours ; tour 1 en {turns[0]['total_s']} s, tour {len(turns)} en "
                              f"{turns[-1]['total_s']} s")
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
            parts.append(f"réponse : « {one_line(obs['answer'], 160)} »")
        return " ; ".join(parts)

    def lister_conversations(self, args: dict, ev: Evidence) -> dict:
        """N7 : les titres de la barre latérale."""
        page = self.browser.page()
        self._ensure_on_app(page)
        titles = self.browser.call(TITLES_JS, {"items": SELECTORS["conversation_items"]})
        text = f"{len(titles)} conversation{'s' if len(titles) != 1 else ''} : " + " | ".join(titles[:30])
        ev.screenshot(page, "screen.png")
        self.observations["@list"] = {"answer": text, "timing_s": None, "t": now_iso()}
        return {"titles": titles, "count": len(titles), "text": one_line(text, 300)}

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
        second = b.context.new_page()
        try:
            self._goto(second, self.entry)
            out, turns = {}, []
            for page, case in ((first, cases[0]), (second, cases[1])):
                out[case["id"]] = {}
                if case.get("file"):
                    att = self._attach(page, self._resolve(case["file"]), budget_of(case))
                    out[case["id"]]["attach"] = {k: att[k] for k in ("state", "label", "seconds", "ready_by")}
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
                            out[turn["case"]]["abort"] = self._abort(turn)
                for turn in turns:
                    if turn.get("end") == "<HORS PÉRIMÈTRE>":
                        out[turn["case"]]["end"] = turn["end"]
                    elif turn["done"]:
                        obs = self._collect(turn)
                        out[turn["case"]].update(answer=one_line(obs["answer"], 300), total_s=obs["total_s"],
                                                 end=obs["end"], server_steps=obs["server_steps"])
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
        dialog = b.pending()
        navs = self._offlist()
        off = sorted({n["host"] for n in navs})
        ok = [o for k, o in out.items() if k != "gap_s" and o.get("answer") and not (o.get("end") or "").startswith("<ABORT>")]
        verdict = "OK" if len(ok) == 2 and not off and not dialog else "KO"
        parts = [f"dialogue inattendu ({dialog['type']}) : {dialog['message']}"] if dialog else []
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
        if "gap_s" in out:
            parts.append(f"écart d'envoi {out['gap_s']} s")
        timing = max([o.get("total_s") or o.get("timeout_s") or 0 for k, o in out.items() if k != "gap_s"] or [0])
        line = self._journal_add(cid, verdict, timing, " ; ".join(parts),
                                 nfr_union(self._case(cid)["nfr"] if cid in self.cases else [],
                                           ["disponibilite", "scalabilite"],
                                           ["securite"] if off or dialog else []))
        self.observations["@concurrent"] = {**out, "case": cid, "verdict": verdict, "t": now_iso()}
        if dialog:
            # First, as `_after` would: a frozen tab cannot be sent back to the entry page.
            raise self._dialog_refusal(ev)
        if navs:
            # The driver's own tab goes back to the entry page: the navigation's tab index was
            # recorded with the second tab open, and may name another tab now.
            raise self._host_refusal(navs[-1], ev, page=b.page())
        return {"case": cid, "a": out[a], "b": out[bb], "verdict": verdict, "replayed": False, "text": line}

    # -- le compositeur (v2) -----------------------------------------------------------------
    def _control(self, page, sels: list) -> dict | None:
        return self.browser.call(CONTROL_JS, {"sels": sels}, page=page)

    def _composer_now(self, page) -> dict:
        return {"model": model_of(self._control(page, SELECTORS["model_button"])),
                "web": web_mode_of(self._control(page, SELECTORS["web_button"]))}

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

    def choisir_modele(self, args: dict, ev: Evidence) -> dict:
        """Choisir le modèle du compositeur : le bouton « Model: … » ouvre une liste ; l'option qui
        correspond à `model` (choose_option) est cliquée, puis le modèle est relu sur le bouton
        (model.json). Le changement reste : l'action ne restaure rien (reversible: false)."""
        want = args["model"]
        b = self.browser
        page = b.page()
        self._ensure_on_app(page)
        button = self._control(page, SELECTORS["model_button"])
        if button is None:
            raise DriverRefusal("precondition", "pas de bouton de modèle (« Model: … ») dans le compositeur")
        previous = model_of(button)
        record = {"asked": want, "previous": previous, "options": [], "picked": None, "candidates": [],
                  "read_back": previous, "changed": False, "list_closed": None}
        if not model_matches(want, previous):
            page.locator(button["sel"]).nth(button["index"]).click(timeout=5000)
            arg = {"list": SELECTORS["listbox"], "option": SELECTORS["option"]}
            options, deadline = None, time.monotonic() + 5
            while not options and time.monotonic() < deadline:
                page.wait_for_timeout(150)
                options = b.call(OPTIONS_JS, arg, page=page)
            record["options"] = [o["text"] or o["value"] for o in options or []][:50]
            pick, candidates = choose_option(want, options or [])
            record["candidates"] = [o["text"] for o in candidates][:10]
            if pick is None:
                record["list_closed"] = self._close_list(page, SELECTORS["listbox"])
                ev.json("model.json", record)
                why = ("la liste des modèles ne s'ouvre pas" if not options
                       else f"aucun modèle « {want} » dans la liste : {', '.join(record['options'][:10])}")
                raise DriverRefusal("precondition", why, extra={"options": record["options"]})
            record["picked"] = {"text": pick["text"], "value": pick["value"]}
            page.locator(f'{SELECTORS["listbox"]} >> visible=true').last.locator(
                SELECTORS["option"]).nth(pick["index"]).click(timeout=5000)
            record["list_closed"] = self._close_list(page, SELECTORS["listbox"])
            deadline = time.monotonic() + 3
            while True:  # read back on the button, once it shows the change
                record["read_back"] = model_of(self._control(page, SELECTORS["model_button"]))
                if model_matches(want, record["read_back"]) or time.monotonic() >= deadline:
                    break
                page.wait_for_timeout(150)
            record["changed"] = record["read_back"] != previous
        ev.json("model.json", record)
        if not model_matches(want, record["read_back"]):
            raise DriverRefusal("precondition", f"modèle relu « {record['read_back']} » au lieu de « {want} »",
                                extra={"read_back": record["read_back"]})
        return {"model": record["read_back"], "previous": previous, "changed": record["changed"],
                "text": f"modèle du compositeur : {record['read_back']} (avant : {previous})"}

    def recherche_web(self, args: dict, ev: Evidence) -> dict:
        """Régler la recherche web du compositeur : le bouton « Web search: … » (data-mode) est
        cliqué jusqu'à afficher `mode` (un cycle, quatre clics au plus) ; s'il ouvre un menu, l'entrée
        du mode est choisie. Le mode est relu sur le bouton (web.json). Le réglage reste : l'action
        ne restaure rien (reversible: false)."""
        want = args["mode"]
        b = self.browser
        page = b.page()
        self._ensure_on_app(page)
        control = self._control(page, SELECTORS["web_button"])
        if control is None:
            raise DriverRefusal("precondition", "pas de bouton de recherche web (« Web search: … ») dans le compositeur")
        previous = web_mode_of(control)
        seen, clicks, menu = [previous], 0, False
        while control is not None and web_mode_of(control) != want and clicks < 4:
            page.locator(control["sel"]).nth(control["index"]).click(timeout=5000)
            clicks += 1
            page.wait_for_timeout(300)
            items = b.call(OPTIONS_JS, {"list": SELECTORS["web_menu"], "option": SELECTORS["web_menu_item"]}, page=page)
            if items:  # a menu rather than a cycle: pick the mode's entry
                menu = True
                pick = next((o for o in items if web_word(o["value"]) == want or web_word(o["text"]) == want), None)
                if pick is None:
                    self._close_list(page, SELECTORS["web_menu"])
                    break
                page.locator(f'{SELECTORS["web_menu"]} >> visible=true').last.locator(
                    SELECTORS["web_menu_item"]).nth(pick["index"]).click(timeout=5000)
                self._close_list(page, SELECTORS["web_menu"])
            control = self._control(page, SELECTORS["web_button"])
            seen.append(web_mode_of(control))
        read_back = web_mode_of(control)
        record = {"asked": want, "previous": previous, "read_back": read_back, "clicks": clicks, "menu": menu,
                  "seen": seen, "label": control["label"] if control else None}
        ev.json("web.json", record)
        if read_back != want:
            raise DriverRefusal("precondition", f"recherche web relue « {read_back} » au lieu de « {want} »",
                                extra={"read_back": read_back})
        return {"mode": read_back, "previous": previous, "changed": read_back != previous, "clicks": clicks,
                "text": f"recherche web : {read_back} (avant : {previous}, {clicks} clic{'s' if clicks > 1 else ''})"}

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
        mark = self.browser.mark()
        self._last_asked = {"case": cid, "mark": mark}
        budget = budget_of(case)
        turns, obs = [], {}
        for i, q in enumerate(questions, 1):
            try:
                obs = self._ask(q, budget)
            except AskTimeout as t:
                why = f"{t.why} au tour {i}/{n}"
                line = self._journal_add(cid, "KO", t.seconds, f"{why} : pas de fin de réponse ; {abort_text(t.profile)}",
                                         nfr_union(case["nfr"], ["disponibilite"]))
                timeout = {"seconds": round(t.seconds, 1), "turn": i, "t": now_iso(), **t.profile}
                self.observations[cid] = {"turns": turns, "timeout": timeout}
                ev.json("turns.json", {"case": cid, "asked_turns": n, "turns": turns, "timeout": timeout})
                raise DriverRefusal("timeout", why, extra={"journal": line, "abort": t.profile, "turns": len(turns)},
                                    evidence=ev.items) from None
            turns.append(turn_summary(i, q, obs))
        self.conv["asked"].append(cid)
        self.observations[cid] = {**obs, "html": None, "turns": turns}
        ev.json("turns.json", {"case": cid, "asked_turns": n, "turns": turns})
        ev.screenshot(self.browser.page(), "answer.png")
        return {"case": cid, "turns": len(turns), "answer": obs.get("answer"),
                "total_s": [t["total_s"] for t in turns],
                "text": f"{cid} : {len(turns)} tours, le dernier en {obs.get('total_s')} s — "
                        f"{one_line(obs.get('answer') or '', 140)}"}

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
