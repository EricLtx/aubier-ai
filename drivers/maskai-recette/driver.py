"""maskai-recette : le driver de la recette MaskAI (procédure Aubier, PROCEDURE.md).

Le noyau lance ce processus, lui parle en lignes JSON (protocole 1,
`drivers/vkdriver/protocol.py`) et lui demande des actions métier. Chacune est
compilée ici, déterministe, sans jeton de modèle : un Chrome au profil dédié,
piloté par Playwright, fait ce que la procédure fait à la main. Chaque verdict
va au journal append-only de la recette, chaque preuve sous le dossier que
l'hôte donne pour l'action.

Ce que le driver garantit :
- la liste d'hôtes (`VK_DRIVER_HOSTS`) : toute requête vers un autre hôte est
  refusée et listée comme bloquée ; une navigation hors liste rend `host` ;
- le garde-dialogue : un dialogue JavaScript n'est jamais accepté ni refusé par
  le driver ; chaque action le signale (`dialog`) jusqu'à ce qu'un humain l'ait
  fermé dans Chrome ; une modale inconnue est signalée de même ; une modale
  connue (`KNOWN_MODALS`) est fermée par son bouton documenté ;
- le délai d'abandon : 3 × le budget du cas, journalisé KO disponibilité ;
- la reprise : observations et journal sur disque (`VK_DRIVER_WORK_DIR`) ; un
  cas déjà journalisé rend son verdict sans rien ajouter.

Environnement (posé par l'hôte) : VK_DRIVER_PROFILE_DIR, VK_DRIVER_WORK_DIR,
VK_DRIVER_HEADLESS, VK_DRIVER_CHANNEL, VK_DRIVER_HOSTS ; MASKAI_ENTRY_URL
remplace `ENTRY_URL` (le banc d'essai sur un autre port).
"""
from __future__ import annotations

import argparse
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
RECETTE_DIR = REPO / "docs" / "use-cases" / "recettage-chat-ia"
SCRIPTS_DIR = REPO / "scripts" / "recette"
sys.path.insert(0, str(DRIVER_DIR.parent))

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
VERSION = "0.1.0"

# La page d'entrée de MaskAI : mesurer_chargement la charge, nouvelle_conversation y revient
# quand aucun bouton « nouvelle conversation » n'est trouvé.
ENTRY_URL = "https://app.maskai.fr/new"
# Le banc d'essai (fixture/serve.py, port par défaut) quand la liste d'hôtes est 127.0.0.1.
FIXTURE_ENTRY_URL = "http://127.0.0.1:8765/new"

# Ce que le driver cherche dans la page. Les listes sont essayées dans l'ordre. Les entrées
# marquées « à confirmer » sont des hypothèses sur app.maskai.fr que seul le banc d'essai a
# validées (fixture/index.html) ; les autres viennent des deux exécutions des 12 et 13 octobre.
SELECTORS = {
    # Zone de saisie (annexe B : setter natif, événement input, puis un espace tapé).
    "textarea": "textarea",
    # Champ de fichier caché derrière le trombone.
    "file_input": 'input[type="file"]',
    # Bouton « nouvelle conversation » (sélecteurs Playwright) ; à défaut, retour à ENTRY_URL.
    # À confirmer : l'interface de MaskAI est en anglais (« New chat » ?).
    "new_conversation": ['[aria-label*="Nouvelle"]', 'button:has-text("Nouvelle conversation")',
                         '[aria-label*="New chat" i]', 'button:has-text("New chat")',
                         'a:has-text("New chat")', '[aria-label*="New conversation" i]'],
    # Bouton d'envoi quand Entrée n'envoie pas (CSS). À confirmer.
    "send_button": ['button[aria-label*="Envoyer"]', 'button[aria-label*="Send" i]',
                    'button[type="submit"]'],
    # Bouton d'arrêt visible pendant la génération (CSS, puis libellé). « Stop generating » vu
    # le 13 octobre ; l'attribut aria-label est à confirmer.
    "stop_button": ['button[aria-label*="Stop" i]', 'button[aria-label*="Arrêter" i]'],
    "stop_text": r"stop generating|arrêter",
    # Messages de l'assistant ; le dernier est la réponse (CSS). À confirmer.
    "assistant_message": ['[data-role="assistant"]', '[data-message-author-role="assistant"]',
                          '.message.assistant', '[data-testid*="assistant-message"]'],
    # Puce de pièce jointe (CSS) et ses états lus dans aria-label ou le texte. « Indexing in
    # progress » puis « Failed » vus les 12 et 13 octobre ; la puce elle-même est à confirmer.
    "attach_chip": ['[aria-label="Failed"]', '[aria-label^="Attached"]', '[aria-label^="Indexing"]',
                    '[data-attachment]', '.chip'],
    "attach_failed": r"failed|échec",
    "attach_pending": r"indexing|uploading|in progress|en cours|processing",
    # Conversations de la barre latérale (CSS). À confirmer.
    "conversation_items": ['#conversations li', 'nav a[href*="/c/"]', 'nav a[href*="/chat/"]',
                           '[data-testid*="conversation"]', 'aside li'],
    # Libellé du panneau « texte envoyé au modèle » (« SENT TO THE LLM » vu le 13 octobre).
    "sent_label": r"sent to the llm|envoyé au modèle",
    # Fenêtres modales (CSS).
    "modal": '[role="dialog"], [role="alertdialog"], [aria-modal="true"], .modal',
}
# Modales connues : reconnues par leur sélecteur et fermées par leur bouton documenté.
# À confirmer pour MaskAI (aucune bannière relevée les 12 et 13 octobre).
KNOWN_MODALS = [
    {"name": "cookies", "selector": "#cookies", "dismiss": "#cookies button#accept"},
]
# Motif de l'URL du flux de réponse (annexe A) : /api/chat/stream chez MaskAI.
STREAM_PATTERN = r"chat/stream"

PER_FILE = 2 * 1024 * 1024        # HARNESS_MAX_FILE_BYTES
PER_ACTION = 4 * 1024 * 1024      # HARNESS_MAX_RUN_BYTES / 4
DOM_CAP = 1024 * 1024
NETWORK_CAP = 1024 * 1024
DEFAULT_BUDGET_S = 60
ATTACH_WAIT_S = 60
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
  const re = new RegExp(a.stopText, 'i');
  let stop = a.stop.some((s) => [...document.querySelectorAll(s)].some(visible));
  if (!stop) stop = [...document.querySelectorAll('button')].some((b) => visible(b) && re.test(b.textContent || ''));
  return { n: ev.length, ev: ev.length < a.from ? ev : ev.slice(a.from), count: msgs.length,
           text: last ? last.innerText : '', stop };
}"""

ANSWER_JS = "(a) => {" + _PICK + """
  const msgs = pick(a.assistant);
  const last = msgs.length ? msgs[msgs.length - 1] : null;
  return last ? { text: last.innerText, html: last.outerHTML } : { text: '', html: '' };
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

ATTACH_JS = """(a) => {
  let chips = [];
  for (const s of a.chips) chips.push(...document.querySelectorAll(s));
  for (const el of document.querySelectorAll('[aria-label]')) if ((el.getAttribute('aria-label') || '').includes(a.name)) chips.push(el);
  chips = [...new Set(chips)];
  if (!chips.length) return null;
  chips.sort((x, y) => (x.compareDocumentPosition(y) & Node.DOCUMENT_POSITION_FOLLOWING) ? -1 : 1);
  const el = chips[chips.length - 1];
  return { aria: el.getAttribute('aria-label') || '', text: (el.textContent || '').trim().slice(0, 200) };
}"""

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


def load_cases(path: Path = DRIVER_DIR / "cases.json") -> dict:
    """Les cas de la recette : ceux qui portent `oracle` prennent question, must, must_not et
    pii dans les oracles du corpus (une seule source pour le verdict)."""
    oracles = {}
    for rel in ("corpus-v2/oracles-v2.json", "corpus/oracles.json"):
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
        cases[c["id"]] = c
    return cases


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
        data = json.dumps(obj, ensure_ascii=False, indent=1).encode("utf-8")
        if cap and len(data) > cap and isinstance(obj, dict):
            obj = dict(obj)
            while len(data) > cap and any(isinstance(v, list) and v for v in obj.values()):
                key = max((k for k, v in obj.items() if isinstance(v, list)), key=lambda k: len(obj[k]))
                obj[key] = obj[key][len(obj[key]) // 2:]
                obj["truncated"] = True
                data = json.dumps(obj, ensure_ascii=False, indent=1).encode("utf-8")
        self.add(name, data)

    def html(self, name: str, html: str) -> None:
        data = html.encode("utf-8")
        if len(data) > DOM_CAP:
            marker = f"\n<!-- maskai-recette : tronqué, {len(data)} octets au total -->\n".encode("utf-8")
            data = data[: DOM_CAP - len(marker)].decode("utf-8", "ignore").encode("utf-8") + marker
        self.add(name, data)

    def screenshot(self, page, name: str) -> None:
        self.add(name, page.screenshot(full_page=False, type="png", timeout=10_000))


class Browser:
    """Le navigateur du driver : un contexte persistant Playwright sur le profil dédié, la
    liste d'hôtes en routage, le garde-dialogue, et la capture réseau et console (résumés)."""

    def __init__(self, profile_dir: str, channel: str | None, headless: bool, hosts: list):
        self.profile_dir, self.channel, self.headless, self.hosts = profile_dir, channel, headless, hosts
        self.pw = self.context = self._page = None
        self._dead = False
        self.dialogs = []      # dialogues JavaScript en attente : jamais acceptés ni refusés ici
        self.network = []      # résumés de requêtes, toutes pages
        self.blocked = []      # requêtes refusées par la liste d'hôtes
        self.console = []
        self.posts = []        # corps envoyés au flux de réponse (`sent`)
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
                accept_downloads=False, service_workers="block")
        except Exception:
            self.pw.stop()
            self.pw = None
            raise
        ctx = self.context
        ctx.set_default_timeout(10_000)
        ctx.add_init_script(INTERCEPTOR_JS)
        ctx.route("**/*", self._route)
        ctx.route_web_socket(lambda url: not self.allowed(url), self._refuse_socket)
        ctx.on("dialog", self._on_dialog)
        ctx.on("dialogclosed", self._on_dialog_closed)
        ctx.on("request", self._on_request)
        ctx.on("response", self._on_response)
        ctx.on("requestfailed", self._on_failed)
        ctx.on("console", self._on_console)
        ctx.on("close", self._on_close)
        self._page = ctx.pages[0] if ctx.pages else ctx.new_page()
        log.info("browser up: channel=%s headless=%s hosts=%s", self.channel, self.headless, self.hosts)

    def page(self):
        self.start()
        if self._page is None or self._page.is_closed():
            pages = [p for p in self.context.pages if not p.is_closed()]
            self._page = pages[0] if pages else self.context.new_page()
        return self._page

    def close(self) -> None:
        for step in (lambda: self.context and self.context.close(), lambda: self.pw and self.pw.stop()):
            try:
                step()
            except Exception:  # noqa: BLE001 — closing what is already gone
                pass
        self.pw = self.context = self._page = None
        self._dead = False
        self.dialogs.clear()

    def pump(self) -> None:
        """Entre deux actions : laisser Playwright servir le routage et les événements, sans
        quoi les requêtes de la page attendraient la prochaine action."""
        if self.started() and self._page is not None and not self._page.is_closed():
            try:
                self._page.wait_for_timeout(20)
            except PWError as e:  # a crashed page: say it once, the next action relaunches
                log.warning("browser unusable between actions, relaunched at the next one: %s", e)
                self._dead = True

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
            return json.loads(handle.json_value())
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

    # -- liste d'hôtes ------------------------------------------------------------------
    def allowed(self, url: str) -> bool:
        p = urlsplit(url)
        return p.scheme not in ("http", "https", "ws", "wss") or (p.hostname or "") in self.hosts

    def _route(self, route, request) -> None:
        if self.allowed(request.url):
            route.continue_()
            return
        navigation = False
        try:
            navigation = request.is_navigation_request() and request.frame.parent_frame is None
        except Exception:  # noqa: BLE001
            pass
        self._push(self.blocked, {"url": short_url(request.url), "type": request.resource_type,
                                  "navigation": navigation, "page": self.page_of(request)})
        log.info("blocked (host not allowed): %s", short_url(request.url))
        route.abort("blockedbyclient")

    def _refuse_socket(self, ws) -> None:
        self._push(self.blocked, {"url": short_url(ws.url), "type": "websocket", "navigation": False})
        ws.close()

    # -- garde-dialogue -----------------------------------------------------------------
    def _on_dialog(self, dialog) -> None:
        """Relever le dialogue et le laisser ouvert : seul un humain y répond."""
        self.dialogs.append({"type": dialog.type, "message": (dialog.message or "")[:200], "t": now_iso(),
                             "dialog": dialog})
        log.warning("dialog left pending: %s %r", dialog.type, (dialog.message or "")[:200])

    def _on_dialog_closed(self, dialog) -> None:
        self.dialogs = [d for d in self.dialogs if d["dialog"] is not dialog]
        log.info("dialog closed by someone else: %s", dialog.type)

    def pending(self) -> dict | None:
        return self.dialogs[-1] if self.dialogs else None

    # -- capture ------------------------------------------------------------------------
    def _push(self, lst: list, entry: dict) -> dict:
        self.seq += 1
        entry = {"seq": self.seq, "t": now_iso(), **entry}
        lst.append(entry)
        if len(lst) > 20_000:
            del lst[:5_000]
        return entry

    def _on_request(self, request) -> None:
        e = self._push(self.network, {"method": request.method, "url": short_url(request.url),
                                      "type": request.resource_type, "page": self.page_of(request)})
        self._open[request] = (e, time.monotonic())
        if request.method == "POST" and re.search(STREAM_PATTERN, request.url):
            self.posts.append({"seq": e["seq"], "page": e["page"], "body": (request.post_data or "")[:20_000]})

    def _on_response(self, response) -> None:
        e, t0 = self._open.pop(response.request, ({}, time.monotonic()))
        e["status"] = response.status
        e["ms"] = int((time.monotonic() - t0) * 1000)

    def _on_failed(self, request) -> None:
        e, _ = self._open.pop(request, ({}, 0))
        e["failure"] = request.failure

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
                "blocked": self.since(self.blocked, mark), "console": self.since(self.console, mark)}


class AskTimeout(Exception):
    def __init__(self, seconds: float, why: str):
        super().__init__(why)
        self.seconds, self.why = seconds, why


class Recette:
    """L'état du driver entre deux actions : le navigateur, les cas, les observations par cas
    (persistées après chaque action) et le journal de la recette."""

    def __init__(self, env=None):
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
        self.cases = load_cases()
        self.journal_path = self.work / "journal.jsonl"
        self.cursor_path = self.work / "journal.cursor"
        self.obs_path = self.work / "observations.json"
        self.observations = (json.loads(self.obs_path.read_text(encoding="utf-8"))
                             if self.obs_path.exists() else {})
        self.conv = self._fresh_conversation()
        self.flux = {"ev": 0, "mark": 0}
        self._case_in_hand = None

    def actions(self) -> dict:
        browser = ["mesurer_chargement", "nouvelle_conversation", "joindre_document", "poser_question",
                   "envoyer_message", "lire_reponse", "capturer_flux", "lister_conversations",
                   "charge_concurrente", "capture_ecran"]
        local = ["verdict_oracle", "exporter_journal"]
        return {**{n: self._wrap(getattr(self, n), browser=True) for n in browser},
                **{n: self._wrap(getattr(self, n), browser=False) for n in local}}

    def open(self) -> None:
        """Au montage : ouvrir Chrome sur la page d'entrée, pour que l'opérateur s'y connecte."""
        page = self.browser.page()
        self._goto(page, self.entry)

    # -- l'enveloppe commune ---------------------------------------------------------------
    def _wrap(self, fn, browser: bool):
        def run(args: dict, evidence_dir: str):
            ev = Evidence(evidence_dir)
            self._case_in_hand = None
            try:
                mark = self.browser.mark()
                if browser:
                    self._guard()
                result = fn(args, ev)
                if browser:
                    self._after(mark)
                if ev.dropped:
                    result["evidence_dropped"] = ev.dropped
                return result, ev.items
            except DriverRefusal as r:
                r.evidence = r.evidence or ev.items
                raise
            except Exception as e:
                if browser and self.browser.started() and self.browser.pending():
                    raise self._dialog_refusal(ev) from e
                raise
            finally:
                self._save_observations()
        run.__name__ = fn.__name__
        return run

    def _guard(self) -> None:
        """Avant toute action dans le navigateur : pas de dialogue en attente, pas de modale
        inconnue ; une modale connue est fermée par son bouton."""
        b = self.browser
        page = b.page()
        b.pump()
        if b.pending():
            if b.responsive():
                log.info("pending dialog gone (closed in Chrome): guard cleared")
                b.dialogs.clear()
            else:
                raise self._dialog_refusal()
        try:
            modals = b.call(MODALS_JS, {"known": KNOWN_MODALS, "modal": SELECTORS["modal"]}, timeout_ms=3000)
        except PWTimeout:
            if b.pending():
                raise self._dialog_refusal()
            raise
        for name in modals["known"]:
            k = next(k for k in KNOWN_MODALS if k["name"] == name)
            page.locator(k["dismiss"]).first.click(timeout=5000)
            log.info("known modal %s dismissed by its button", name)
        if modals["unknown"]:
            raise DriverRefusal("dialog", f"modale inconnue : {modals['unknown'][0]}")

    def _after(self, mark: int) -> None:
        b = self.browser
        b.pump()
        if b.pending():
            raise self._dialog_refusal()
        navs = [e for e in b.since(b.blocked, mark) if e.get("navigation")]
        if navs:
            raise DriverRefusal("host", f"navigation hors liste refusée : {navs[-1]['url']}")

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
        for root in (DRIVER_DIR, RECETTE_DIR):
            p = (root / rel).resolve()
            if p.is_file() and root.resolve() in p.parents:
                return p
        raise DriverRefusal("precondition", f"fichier du cas introuvable : {rel}")

    def _goto(self, page, url: str) -> None:
        if not self.browser.allowed(url):
            raise DriverRefusal("host", f"navigation hors liste refusée : {short_url(url)}")
        try:
            page.goto(url, wait_until="load", timeout=60_000)
        except PWError as e:
            if "ERR_BLOCKED_BY_CLIENT" in str(e):
                raise DriverRefusal("host", f"navigation hors liste refusée : {short_url(url)}") from e
            raise
        if page is self.browser.page():
            self.conv = self._fresh_conversation()
            self.flux["ev"] = 0

    def _ensure_on_app(self, page) -> None:
        if page.url in ("", "about:blank") or not self.browser.allowed(page.url):
            self._goto(page, self.entry)

    def _save_observations(self) -> None:
        tmp = self.obs_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.observations, ensure_ascii=False, indent=1), encoding="utf-8")
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
                     pipeline_ms: dict | None = None) -> str:
        event = journal.add(str(self.journal_path), argparse.Namespace(
            step="exec", case=cid, verdict=verdict,
            timing=round(timing_s, 1) if isinstance(timing_s, (int, float)) else None,
            evidence=evidence[:600],
            pipeline=[f"{k}={int(v)}" for k, v in (pipeline_ms or {}).items()] or None,
            nfr=",".join(nfr) or None, supersedes=None))
        return json.dumps(event, ensure_ascii=False)

    # -- envoyer un message et attendre la réponse -------------------------------------------
    def _send(self, page, text: str) -> dict:
        """Annexe B : setter natif, événement input, un espace tapé, puis Entrée (ou le bouton
        d'envoi si Entrée n'a rien envoyé). Rend le tour à surveiller."""
        b = self.browser
        st = b.call(POLL_JS, self._poll_arg(0), page=page)
        turn = {"page": page, "n0": st["n"], "count0": st["count"], "posts0": len(b.posts),
                "page_no": b.context.pages.index(page)}
        if not b.call(FILL_JS, {"sel": SELECTORS["textarea"], "text": text}, page=page):
            raise DriverRefusal("precondition", "pas de zone de saisie dans la page")
        page.bring_to_front()
        page.keyboard.type(" ")
        turn["t_send"] = time.monotonic()
        page.keyboard.press("Enter")
        page.wait_for_timeout(300)
        left = b.call(VALUE_JS, {"sel": SELECTORS["textarea"]}, page=page)
        if left and left.strip() and len(b.posts) == turn["posts0"]:
            b.call(CLICK_SEND_JS, {"sels": SELECTORS["send_button"]}, page=page)
        return turn

    @staticmethod
    def _poll_arg(start: int) -> dict:
        return {"from": start, "assistant": SELECTORS["assistant_message"],
                "stop": SELECTORS["stop_button"], "stopText": SELECTORS["stop_text"]}

    def _wait(self, turns: list, wait_s: float) -> None:
        """Attendre chaque tour : <END> ou <ABORT> dans window.__ev, ou le bouton d'arrêt
        disparu, ou (flux non vu) une réponse nouvelle stable 3 s. Lève AskTimeout au délai."""
        b = self.browser
        for t in turns:
            t.update(done=False, events=[], saw_stop=False, stop_gone=None, stable=None, last_text=None)
        start = min(t["t_send"] for t in turns)
        while not all(t["done"] for t in turns):
            if time.monotonic() - start >= wait_s:
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
                if any(line.startswith(("<END>", "<ABORT>")) for line in lines):
                    t["done"], t["end"] = True, next(x for x in lines if x.startswith(("<END>", "<ABORT>")))
                elif st["stop"]:
                    t["saw_stop"], t["stop_gone"] = True, None
                elif t["saw_stop"]:
                    t["stop_gone"] = t["stop_gone"] or now
                    t["done"] = now - t["stop_gone"] >= 1.0
                elif not any(line == "<START>" for line in lines) and st["count"] > t["count0"] and st["text"].strip():
                    if st["text"] != t["last_text"]:
                        t["last_text"], t["stable"] = st["text"], now
                    t["done"] = now - t["stable"] >= 3.0
                if t["done"]:
                    t["total_s"] = round(now - t["t_send"], 2)
            if b.pending():
                raise PWTimeout("dialog pending")
            turns[0]["page"].wait_for_timeout(250)

    def _collect(self, turn: dict) -> dict:
        """La réponse d'un tour terminé : texte stabilisé, HTML, étapes serveur, texte envoyé."""
        b, page = self.browser, turn["page"]
        arg = {"assistant": SELECTORS["assistant_message"]}
        answer = b.call(ANSWER_JS, arg, page=page)
        for _ in range(10):
            page.wait_for_timeout(300)
            again = b.call(ANSWER_JS, arg, page=page)
            if again["text"] == answer["text"]:
                break
            answer = again
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
        sent_to_model = b.call(SENT_JS, {"label": SELECTORS["sent_label"]}, page=page)
        return {"answer": answer["text"].strip(), "html": answer["html"],
                "sent": posts[-1]["body"] if posts else None, "sent_to_model": sent_to_model,
                "ttft_s": ttft, "total_s": turn.get("total_s"), "timing_s": turn.get("total_s"),
                "server_steps": steps, "pipeline_ms": pipeline, "end": turn.get("end"), "t": now_iso()}

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
            raise
        return self._collect(turn)

    def _answer_evidence(self, ev: Evidence, obs: dict, mark: int) -> None:
        page = self.browser.page()
        if obs.get("html"):
            ev.html("dom.html", obs["html"])
        ev.json("network.json", self.browser.capture(mark), cap=NETWORK_CAP)
        ev.screenshot(page, "answer.png")

    @staticmethod
    def _answer_result(obs: dict, label: str) -> dict:
        result = {k: obs.get(k) for k in ("answer", "ttft_s", "total_s", "server_steps")}
        result["sent"] = (obs.get("sent") or "")[:2000] or None
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
        how = "bouton" if clicked else "retour à la page d'entrée"
        return {"conversation": page.url, "text": f"nouvelle conversation ({how}) : {short_url(page.url)}"}

    def joindre_document(self, args: dict, ev: Evidence) -> dict:
        """Joindre le fichier du cas par le champ caché derrière le trombone, puis lire l'état
        de la puce avant tout envoi (« Failed » : la pièce jointe a échoué)."""
        cid = args["case"]
        case = self._case(cid)
        if not case.get("file"):
            raise DriverRefusal("precondition", f"le cas {cid} n'a pas de fichier à joindre")
        path = self._resolve(case["file"])
        b = self.browser
        page = b.page()
        self._ensure_on_app(page)
        mark = b.mark()
        state, chip, seconds = self._attach(page, path)
        self.conv["attached"][cid] = state
        self.observations[cid] = {"attach": {"state": state, "seconds": seconds, "label": chip, "t": now_iso()}}
        ev.screenshot(page, "attach.png")
        ev.json("network.json", b.capture(mark), cap=NETWORK_CAP)
        if state == "failed":
            raise DriverRefusal("precondition", "attachment failed",
                                extra={"state": state, "seconds": seconds, "label": chip})
        if state != "attached":
            raise DriverRefusal("precondition", f"pièce jointe « {chip or 'sans puce'} » après {seconds} s",
                                extra={"state": state, "seconds": seconds, "label": chip})
        return {"state": state, "seconds": seconds, "label": chip,
                "text": f"{path.name} joint en {seconds} s ({chip})"}

    def _attach(self, page, path: Path) -> tuple[str, str, float]:
        b = self.browser
        page.locator(SELECTORS["file_input"]).first.set_input_files(str(path), timeout=10_000)
        t0 = time.monotonic()
        limit, _ = self._deadline(ATTACH_WAIT_S)
        state, label = "missing", ""
        while True:
            chip = b.call(ATTACH_JS, {"chips": SELECTORS["attach_chip"], "name": path.name}, page=page)
            if chip:
                label = one_line(chip["aria"] or chip["text"], 120)
                both = f"{chip['aria']} {chip['text']}"
                if re.search(SELECTORS["attach_failed"], both, re.I):
                    state = "failed"
                elif re.search(SELECTORS["attach_pending"], both, re.I):
                    state = "pending"
                else:
                    state = "attached"
            if state in ("attached", "failed") or time.monotonic() - t0 >= limit:
                return state, label, round(time.monotonic() - t0, 1)
            page.wait_for_timeout(250)

    def poser_question(self, args: dict, ev: Evidence) -> dict:
        """Envoyer la question du cas mot pour mot et attendre la réponse complète, intercepteur
        armé ; abandon à 3 × le budget du cas, journalisé KO disponibilité."""
        cid = args["case"]
        case = self._case(cid)
        if case["kind"] not in ("message", "attach_question") or "question" not in case:
            raise DriverRefusal("precondition", f"le cas {cid} ({case['kind']}) n'a pas de question")
        if case["kind"] == "attach_question" and self.conv["attached"].get(cid) != "attached":
            state = self.conv["attached"].get(cid) or "aucune"
            raise DriverRefusal("precondition", f"joindre_document {cid} d'abord (pièce jointe : {state})")
        before = case.get("same_conversation_as")
        if before and before not in self.conv["asked"]:
            raise DriverRefusal("precondition", f"{cid} se pose dans la conversation de {before}, pas encore posé")
        self._case_in_hand = cid
        # A new attempt: only the attachment survives from the previous observation, so a
        # dialog or a timeout now is never judged on an older answer.
        attach = (self.observations.get(cid) or {}).get("attach")
        self.observations[cid] = {"attach": attach} if attach else {}
        mark = self.browser.mark()
        budget = case.get("budget_s") or DEFAULT_BUDGET_S
        try:
            obs = self._ask(case["question"], budget)
        except AskTimeout as t:
            line = self._journal_add(cid, "KO", t.seconds, t.why + " : pas de fin de réponse",
                                     nfr_union(case["nfr"], ["disponibilite"]))
            self.observations.setdefault(cid, {})["timeout"] = {"seconds": round(t.seconds, 1), "t": now_iso()}
            raise DriverRefusal("timeout", t.why, extra={"journal": line}) from None
        self.conv["asked"].append(cid)
        self.observations[cid] = {**obs, "html": None, **({"attach": attach} if attach else {})}
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
                                     t.why + " : pas de fin de réponse", ["disponibilite"])
            raise DriverRefusal("timeout", t.why, extra={"journal": line}) from None
        self.observations["@message"] = {**obs, "html": None, "text": text}
        self._answer_evidence(ev, obs, mark)
        return self._answer_result(obs, "message")

    def lire_reponse(self, args: dict, ev: Evidence) -> dict:
        """Le texte et le HTML de la dernière réponse affichée."""
        answer = self.browser.call(ANSWER_JS, {"assistant": SELECTORS["assistant_message"]})
        if answer["html"]:
            ev.html("dom.html", answer["html"])
        return {"answer": answer["text"].strip(), "text": one_line(answer["text"]) or "aucune réponse affichée"}

    def capturer_flux(self, args: dict, ev: Evidence) -> dict:
        """Ce qui s'est passé depuis le dernier appel : lignes du flux (window.__ev), console,
        requêtes en échec (statut ≥ 400 ou coupées), requêtes bloquées par la liste d'hôtes."""
        b = self.browser
        all_ev = b.call("() => window.__ev || []")
        start = self.flux["ev"] if self.flux["ev"] <= len(all_ev) else 0
        events, self.flux["ev"] = all_ev[start:], len(all_ev)
        mark, self.flux["mark"] = self.flux["mark"], b.mark()
        capture = b.capture(mark)
        failed = [e for e in capture["requests"] if (e.get("status") or 0) >= 400 or e.get("failure")]
        ev.json("network.json", {**capture, "events": events, "failed": failed}, cap=NETWORK_CAP)
        return {"events": events[-200:], "console": capture["console"][-100:], "failed": failed[-100:],
                "blocked": capture["blocked"][-100:],
                "text": (f"{len(events)} lignes de flux, {len(capture['console'])} messages console, "
                         f"{len(failed)} requêtes en échec, {len(capture['blocked'])} bloquées")}

    def verdict_oracle(self, args: dict, ev: Evidence) -> dict:
        """evaluer.py sur la dernière observation du cas, puis une ligne au journal (annexe C).
        Un cas déjà journalisé rend son verdict (replayed) et n'ajoute rien."""
        cid = args["case"]
        case = self._case(cid)
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
        nfr = case["nfr"]
        if obs.get("dialog") and not obs.get("answer"):
            r = {"verdict": "KO", "notes": [f"dialogue inattendu ({obs['dialog']['type']}) : {obs['dialog']['message']}"]}
        elif not asked and attach and attach.get("state") != "attached":
            r = {"verdict": "KO", "notes": [f"pièce jointe en échec ({attach.get('label') or 'sans puce'})"]}
            nfr = nfr_union(nfr, ["disponibilite"])
        elif not asked:
            hint = {"load": "mesurer_chargement", "list": "lister_conversations",
                    "concurrent": "charge_concurrente"}.get(case["kind"], "poser_question")
            raise DriverRefusal("precondition", f"aucune observation pour {cid} : {hint} d'abord")
        else:
            oracle = {"id": cid, "must": case["must"], "must_not": case["must_not"], "pii": case["pii"]}
            seen = {"answer": obs.get("answer"), "timing_s": obs.get("timing_s")}
            if obs.get("sent_to_model") is not None:
                seen["sent"] = obs["sent_to_model"]
            r = evaluer.evaluate_case(oracle, seen, case.get("budget_s"))
            if r["verdict"] == "NON TESTÉ":  # une réponse vide après un envoi : échec silencieux
                r["verdict"] = "KO"
                r["notes"].append("réponse vide : échec silencieux")
                nfr = nfr_union(nfr, ["disponibilite"])
        for key_ in ("missing", "forbidden", "leaks", "span_issues", "notes"):
            r.setdefault(key_, [])
        line = self._journal_add(cid, r["verdict"], obs.get("timing_s"), self._summary(r, obs), nfr,
                                 obs.get("pipeline_ms"))
        self.observations.setdefault(key, obs)["verdict"] = {k: r[k] for k in ("verdict", "missing", "forbidden", "leaks", "span_issues", "notes")}
        if key != cid:
            self.observations[cid] = {"verdict": self.observations[key]["verdict"], "from": key}
        ev.add("journal.jsonl", (line + "\n").encode("utf-8"))
        return {"case": cid, "verdict": r["verdict"], "missing": r["missing"], "forbidden": r["forbidden"],
                "leaks": r["leaks"], "span_issues": r["span_issues"], "replayed": False, "text": line}

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
                    state, chip, seconds = self._attach(page, self._resolve(case["file"]))
                    out[case["id"]]["attach"] = {"state": state, "label": chip, "seconds": seconds}
            if all(o.get("attach", {"state": "attached"})["state"] == "attached" for o in out.values()):
                for page, case in ((first, cases[0]), (second, cases[1])):
                    turns.append({**self._send(page, case["question"]), "case": case["id"]})
                gap = round(abs(turns[1]["t_send"] - turns[0]["t_send"]), 2)
                budget = max(c.get("budget_s") or DEFAULT_BUDGET_S for c in cases)
                wait_s, capped = self._deadline(3 * budget)
                try:
                    self._wait(turns, wait_s)
                except AskTimeout as t:
                    for turn in turns:
                        if not turn["done"]:
                            out[turn["case"]]["timeout_s"] = round(t.seconds, 1)
                for turn in turns:
                    if turn["done"]:
                        obs = self._collect(turn)
                        out[turn["case"]].update(answer=one_line(obs["answer"], 300), total_s=obs["total_s"],
                                                 end=obs["end"], server_steps=obs["server_steps"])
                out["gap_s"] = gap
            ok = [o for k, o in out.items() if k != "gap_s" and o.get("answer") and not (o.get("end") or "").startswith("<ABORT>")]
            verdict = "OK" if len(ok) == 2 else "KO"
            parts = []
            for c in cases:
                o = out[c["id"]]
                if o.get("attach", {}).get("state", "attached") != "attached":
                    parts.append(f"{c['id']} : pièce jointe {o['attach']['state']} ({o['attach']['label']})")
                elif o.get("timeout_s"):
                    parts.append(f"{c['id']} : abandon à {o['timeout_s']:.0f} s")
                else:
                    parts.append(f"{c['id']} : {o.get('total_s')} s, {len(o.get('answer') or '')} car.")
            if "gap_s" in out:
                parts.append(f"écart d'envoi {out['gap_s']} s")
            timing = max([o.get("total_s") or o.get("timeout_s") or 0 for k, o in out.items() if k != "gap_s"] or [0])
            line = self._journal_add(cid, verdict, timing, " ; ".join(parts),
                                     nfr_union(self._case(cid)["nfr"] if cid in self.cases else [],
                                               ["disponibilite", "scalabilite"]))
            self.observations["@concurrent"] = {**out, "case": cid, "verdict": verdict, "t": now_iso()}
            ev.json("network.json", b.capture(mark), cap=NETWORK_CAP)
            ev.screenshot(second, "screen.png")
            return {"case": cid, "a": out[a], "b": out[bb], "verdict": verdict, "replayed": False, "text": line}
        finally:
            try:
                second.close()
            except PWError:
                pass

    def capture_ecran(self, args: dict, ev: Evidence) -> dict:
        """Une capture de la partie visible de la page."""
        page = self.browser.page()
        ev.screenshot(page, "screen.png")
        return {"url": page.url, "text": f"capture de {short_url(page.url)}"}

    def exporter_journal(self, args: dict, ev: Evidence) -> dict:
        """Les lignes du journal ajoutées depuis l'export précédent (curseur dans
        journal.cursor), en preuve et en texte pour le registre."""
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
        serve(NAME, VERSION, recette.actions(), on_ready=recette.open, idle=recette.browser.pump)
    finally:
        recette.browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
