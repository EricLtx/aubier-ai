"""The driver's test bench: a local stand-in for MaskAI (pilot spec §7, "Driver").

GET on any path serves `index.html` (the page pushes `/conversation/<id>` URLs as
MaskAI does since 2026-10-17). POST `/chat/stream` answers a server-sent-event
stream: the pipeline steps, `{"node": "retrieve"}`, then `{"node": "web_search"}`
when the request's `web_search` is "on", then `{"node": "generate"}`, then the
answer in three `{"delta": ...}` chunks. The answer echoes the question
upper-cased and names the attachments the page declared (the chips that were
ready when the question left). Trigger words in the request body:

  SLOW            sleep 8 s before the first event (abandon deadline tests);
  EXTERNAL_IMG    append <img src="http://example.invalid/x"> to the answer
                  (a third-party host, recorded and not blocked);
  TRIGGER_DIALOG  the page opens a confirm() at the end of the stream (dialog guard);
  NAVIGATE_AWAY   the page then navigates to http://localhost:<port>/away, a
                  host outside a 127.0.0.1-only list (navigation guard);
  UNKNOWN_MODAL   the page shows a [role=dialog] nobody catalogued;
  LATE_DIALOG     the page opens a confirm() 3.5 s after the answer is drawn,
                  once the action is over (the verdict must still see it);
  LEAK            the « Chiffré » popover shows the text unmasked (a leak);
  POPOVER_STATUS  the « Chiffré » popover shows a status line, not the text.

The page side (index.html) draws each answer behind the stream and ends about
1.5 s after the server; shows the « Votre avis » survey after a generation left
in flight; steers the attachment chip by file name (« fail », « slowidx »,
« instant »).

Other paths: /busy (requests, then a popup after 3 s; /stats says when the popup
was announced and when its request arrived), /redirect-unreachable (a 302 to an
unreachable host). Any page asked with `maintenance=1` in its query is MaskAI's
maintenance card instead (seen at the first live attempt of recette 2: no
textarea, « MAINTENANCE EN COURS / ON REVIENT TRÈS VITE ! »).

Usage: python -I serve.py [port]     (default 8765; 0 picks a free port)
The first line on stdout is `listening <port>`.
"""
import html
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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


def now_ms() -> int:
    return int(time.time() * 1000)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002 — the base class's name
        pass

    def reply(self, status: int, body: bytes = b"", ctype: str = "text/html; charset=utf-8", headers=()):
        self.send_response(status)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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
        attachments = msg.get("attachments") or []
        answer = (html.escape(str(msg.get("message", "")).strip().upper())
                  + " — pièces jointes : " + (", ".join(attachments) or "aucune"))
        if "EXTERNAL_IMG" in body:
            answer += ' <img src="http://example.invalid/x" alt="x">'
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            if "SLOW" in body:
                time.sleep(8)
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
