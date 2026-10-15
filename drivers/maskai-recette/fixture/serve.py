"""The driver's test bench: a local stand-in for MaskAI (pilot spec §7, "Driver").

GET on any path serves `index.html` (the page pushes `/c/<id>` URLs as MaskAI
does). POST `/chat/stream` answers a server-sent-event stream: two pipeline
steps, `{"node": "retrieve"}` then `{"node": "generate"}`, then the answer in
three `{"delta": ...}` chunks. The answer echoes the question upper-cased and
names the attachments the page declared. Trigger words in the request body:

  SLOW            sleep 8 s before the first event (abandon deadline tests);
  EXTERNAL_IMG    append <img src="http://example.invalid/x"> to the answer
                  (a third-party host, recorded and not blocked);
  TRIGGER_DIALOG  the page opens a confirm() after the answer (dialog guard);
  NAVIGATE_AWAY   the page then navigates to http://localhost:<port>/away, a
                  host outside a 127.0.0.1-only list (navigation guard).

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


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002 — the base class's name
        pass

    def do_GET(self):
        if self.path.startswith("/favicon"):
            self.send_response(204)
            self.end_headers()
            return
        with open(os.path.join(HERE, "index.html"), "rb") as f:
            page = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)

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
            for step in ("retrieve", "generate"):
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
