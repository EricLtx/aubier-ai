"""The driver's test bench: a local stand-in for MaskAI (pilot spec §7, "Driver").

GET on any path serves `index.html` (the page pushes `/conversation/<id>` URLs as
MaskAI does since 2026-10-17). POST `/chat/stream` answers a server-sent-event
stream: the pipeline steps, `{"node": "retrieve"}`, then `{"node": "web_search"}`
when the request's `web_search` is "on", then `{"node": "generate"}`, then the
answer in three `{"delta": ...}` chunks. The answer echoes the question
upper-cased and names the attachments the page declared (the chips that were
ready when the question left). The response carries, in its `X-Sent-Anonymized`
header (percent-encoded UTF-8), the anonymised text the bench sends "to the
model": the message with each name (« M. Prénom Nom »), IBAN and phone number
replaced by a numbered token (`<PERSON_1>`, `<IBAN_1>`, `<PHONE_1>`); the page
shows it in the answer card's « Détails » collapsible (pilot v2.2). Trigger
words in the request body:

  SLOW            sleep 8 s before the first event (abandon deadline tests);
  BODY_DETAILS    append a « Détails » button to the answer (in its body; the
                  driver must never click it; it counts its clicks in
                  window.__body_clicks);
  EXTERNAL_IMG    append <img src="http://example.invalid/x"> to the answer
                  (a third-party host, recorded and not blocked);
  TRIGGER_DIALOG  the page opens a confirm() at the end of the stream (dialog guard);
  NAVIGATE_AWAY   the page then navigates to http://localhost:<port>/away, a
                  host outside a 127.0.0.1-only list (navigation guard);
  UNKNOWN_MODAL   the page shows a [role=dialog] nobody catalogued;
  LATE_DIALOG     the page opens a confirm() 5 s after the answer is drawn,
                  once the action is over (the verdict must still see it);
  LEAK            the « Chiffré » popover and the card's « Détails » show the
                  text unmasked (a leak);
  POPOVER_STATUS  the « Chiffré » popover shows a status line, not the text;
  DETAILS_STATUS  the card's « Détails » shows a status line, not the text;
  SILENT_FAIL     (pilot v2.3, E10) the stream emits `detect_pii` twice, then an
                  `event: error` whose payload names an internal host, a module
                  and a path, and ends; the page draws no answer card, writes a
                  status line under the composer and shows a toast (the night of
                  18–19/10: 12 of 14 models answered nothing after detect_pii);
  SILENT_502      the stream request answers HTTP 502 with a plain-text body that
                  names an internal host; no card, a status line;
  ANSWER_TRACE    (fix round 1, m11) the answer itself ends with an internal error:
                  a Python stack frame and a host name;
  NOCREATE        (page side only) the page creates no conversation for that
                  message, while another row shows up in the sidebar.

The page side (index.html) draws each answer behind the stream and ends about
1.5 s after the server; shows the « Votre avis » survey after a generation left
in flight (and from its « Send feedback » entry); steers the attachment chip by
file name (« fail », « slowidx », « instant », and since v2.3 « faildb »,
« failformat », « failsize », whose failed chips carry a host name and a stack,
a format refusal, a size refusal); gives each sidebar conversation a menu
(rename, delete with a confirmation) and a ⌘K search palette.

Other paths: /busy (requests, then a popup after 3 s; /stats says when the popup
was announced and when its request arrived), /redirect-unreachable (a 302 to an
unreachable host). Any page asked with `maintenance=1` in its query is MaskAI's
maintenance card instead (seen at the first live attempt of recette 2: no
textarea, « MAINTENANCE EN COURS / ON REVIENT TRÈS VITE ! »). The page itself
reads `nodetails=1` (answer cards without the « Détails » collapsible, the
composer popover then being the driver's fallback) and `revealed=1` (the cards'
PII reveal toggle drawn pressed), `controls=1` (the « Détails » trigger with
`aria-controls`) and `nostructure=1` (the trigger without `data-slot`, after a
reveal toggle carrying a hidden « Afficher les détails »).

Usage: python -I serve.py [port]     (default 8765; 0 picks a free port)
The first line on stdout is `listening <port>`.
"""
import html
import json
import os
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PORT = 8765
GONE = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)

# A busy page, as a signed-in app is between two actions: five requests a second, then
# after 3 s a popup (an SSO window, say). The server notes when the page announced the
# popup and when the popup's own request arrived: Chromium holds a new target until
# Playwright resumes it, so a driver that stops servicing Playwright delays the second.
BUSY_PAGE = b"""<!doctype html><html><body><p>busy</p><script>
setInterval(() => fetch('/ping?' + Math.random()), 200);
setTimeout(async () => { await fetch('/popup-created'); window.open('/popup'); }, 3000);
</script></body></html>"""
STATS = {"created_ms": None, "popup_ms": None}
# The authentication error page seen on 18/10 during a network blip: app.maskai.fr/api/auth/error,
# no composer. Any page asked with `dictating=1` shows the dictation button active (data-status
# "listening"), as a dictation the user started would.
AUTH_ERROR_PAGE = b"""<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>Erreur</title></head>
<body><main><h1>Authentication error</h1><p>Your session could not be verified.</p>
<a href="/">Sign in again</a></main></body></html>
"""

# 18/10, recette 2 on the live node: the driver's own Chrome profile was not signed in, and the entry
# page, once loaded, sent the main frame to authentification.maskai.fr (off the host list) — the
# driver had measured the login page's load as the app's. Here: localhost stands for that host.
LOGIN_REDIRECT_PAGE = b"""<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>MaskAI</title></head>
<body><p>Redirection...</p><script>window.addEventListener('load', function () { setTimeout(function () { location.replace('http://localhost:PORT/login'); }, 300); });</script>
</body></html>
"""
LOGIN_PAGE = b"""<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>Connexion</title></head>
<body><main><h1>Bienvenue !</h1><form><label>Identifiant <input name="loginname"></label></form></main></body></html>
"""

# MaskAI's maintenance card, as the first live attempt of recette 2 met it on app.maskai.fr.
MAINTENANCE_PAGE = """<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>Mask AI</title></head>
<body><main><h1>MAINTENANCE EN COURS</h1><p>ON REVIENT TRÈS VITE !</p>
<p>Mask AI FR fait une petite pause pour s'améliorer. Une question ? contact@maskai.fr</p></main></body></html>
""".encode("utf-8")


# The bench's anonymiser (pilot v2.2): what the card's « Détails » shows as sent to the model. The
# names and IBANs are those of the page's own mask() (the guard panel's tokens), plus phone numbers.
MASKS = (
    (re.compile(r"\b(?:M\.|Mme|Mr)\s+[A-ZÉ][a-zé]+\s+[A-ZÉ][a-zé]+"), "PERSON"),
    (re.compile(r"\bFR\d{2}(?:\s?\d{4}){5}\s?\d{3}\b"), "IBAN"),
    (re.compile(r"(?:\+33\s?|\b0)[1-9](?:[\s.-]?\d{2}){4}\b"), "PHONE"),
)
DETAILS_STATUS = "Aucune PII détectée — rien à anonymiser pour l'instant"
# What the stream says when it fails silently (SILENT_FAIL, v2.3): an internal host, a module, a path.
SILENT_ERROR = ("psycopg2.OperationalError: could not translate host name 'pii-db.internal.maskai.local' to address: "
                "Name or service not known (File '/srv/maskai/guard/detect.py', line 42, in detect)")


def anonymise(text: str) -> str:
    """The message with each name, IBAN and phone number replaced by a numbered token; unmasked
    with LEAK, a status line with DETAILS_STATUS."""
    if "DETAILS_STATUS" in text:
        return DETAILS_STATUS
    if "LEAK" in text:
        return text
    for pattern, kind in MASKS:
        seen = []
        text = pattern.sub(lambda m: f"<{kind}_{seen.append(m) or len(seen)}>", text)
    return text


def now_ms() -> int:
    return int(time.time() * 1000)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002 — the base class's name
        pass

    def reply(self, status: int, body: bytes = b"", ctype: str = "text/html; charset=utf-8", headers=()):
        try:
            self.send_response(status)
            for k, v in headers:
                self.send_header(k, v)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except GONE:
            pass  # the tab went away mid-reply (the busy page's pings as it closes), as in do_POST

    def do_GET(self):
        path = self.path.split("?")[0]
        if path.startswith("/favicon") or path == "/ping":
            self.reply(204)
        elif path == "/busy":
            self.reply(200, BUSY_PAGE)
        elif path == "/popup-created":
            STATS.update(created_ms=now_ms(), popup_ms=None)
            self.reply(204)
        elif path == "/popup":
            STATS["popup_ms"] = now_ms()
            self.reply(200, b"<p>popup</p>")
        elif path == "/stats":
            self.reply(200, json.dumps(STATS).encode(), "application/json")
        elif path == "/redirect-unreachable":
            self.reply(302, headers=[("Location", "http://unreachable.invalid/")])
        elif "maintenance=1" in self.path.partition("?")[2]:
            self.reply(200, MAINTENANCE_PAGE)
        elif path == "/api/auth/error":  # 18/10: the session bounced here, no composer
            self.reply(200, AUTH_ERROR_PAGE)
        elif path == "/login":  # the authentication host's page: no composer, another host
            self.reply(200, LOGIN_PAGE)
        elif "login=1" in self.path.partition("?")[2]:  # the entry page of a profile not signed in:
            # loaded, then sent by script to the authentication host (localhost is not 127.0.0.1)
            port = self.server.server_address[1]
            self.reply(200, LOGIN_REDIRECT_PAGE.replace(b"PORT", str(port).encode()))
        else:
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                self.reply(200, f.read())

    def do_POST(self):
        if not self.path.startswith("/chat/stream"):
            self.send_error(404)
            return
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8")
        try:
            msg = json.loads(body)
        except ValueError:
            msg = {"message": body}
        if "SILENT_502" in body:  # v2.3, E10: no stream at all, a gateway error naming an internal host
            self.reply(502, b"Bad Gateway: upstream pii-guard.internal:8443 unavailable", "text/plain; charset=utf-8")
            return
        attachments = msg.get("attachments") or []
        answer = (html.escape(str(msg.get("message", "")).strip().upper())
                  + " — pièces jointes : " + (", ".join(attachments) or "aucune"))
        if "EXTERNAL_IMG" in body:
            answer += ' <img src="http://example.invalid/x" alt="x">'
        if "ANSWER_TRACE" in body:
            answer += (' Traceback (most recent call last): File "/srv/maskai/answer/render.py", line 7, in run '
                       'psycopg2.OperationalError: could not translate host name "db-answer.internal" to address')
        if "BODY_DETAILS" in body:
            answer += (' <button type="button" onclick="window.__body_clicks = (window.__body_clicks || 0) + 1">'
                       "Détails</button>")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Sent-Anonymized", quote(anonymise(str(msg.get("message", ""))), safe=""))
        self.end_headers()
        try:
            if "SLOW" in body:
                time.sleep(8)
            if "SILENT_FAIL" in body:  # v2.3, E10: detect_pii twice, an error event, the end; no answer
                for _ in range(2):
                    self.event({"node": "detect_pii"})
                    time.sleep(0.05)
                self.wfile.write(("event: error\ndata: " + json.dumps({"error": SILENT_ERROR}) + "\n\n").encode("utf-8"))
                self.wfile.flush()
                return
            steps = ["retrieve"] + (["web_search"] if msg.get("web_search") == "on" else []) + ["generate"]
            for step in steps:
                self.event({"node": step})
                time.sleep(0.05)
            third = -(-len(answer) // 3)
            for i in range(0, len(answer), third):
                self.event({"delta": answer[i:i + third]})
                time.sleep(0.05)
        except GONE:
            pass  # the page went away (an abandoned answer, a closed browser)

    def event(self, obj):
        self.wfile.write(("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8"))
        self.wfile.flush()


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    print(f"listening {server.server_address[1]}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
