"""The protocol-1 loop every Python device driver runs (pilot spec §4.2).

The host starts the driver as a process and speaks JSON lines on its stdio.
The driver's first line on stdout is its hello, and nothing may come before
it. Then, for each request line `{"id", "action", "args", "deadline_ms",
"evidence_dir"}`, the driver answers exactly one line carrying the same id:
`{"id", "ok": true, "result", "evidence"}`, or `{"id", "ok": false, "reason",
"detail", "result"}` when it refuses (`dialog`, `timeout`, `host`,
`precondition`, `internal`). Evidence travels by path, never inline: the
action writes its files under `evidence_dir` and names them.

Stdout belongs to the protocol. `serve` keeps a private copy of it and points
file descriptor 1 at stderr, which the host appends to `driver.log`: a stray
`print`, a library warning or a child process that inherits our stdout lands
in the log instead of corrupting the protocol stream. Every line is valid
UTF-8: a lone surrogate (a JavaScript string cut in the middle of a pair)
becomes `?` rather than an encoding error that would leave a request
unanswered.

The harness only reads `result`, so a refusal also says itself there:
`{"refused": reason, "detail": detail, "text": "..."}`.
"""
import json
import os
import sys
import time
import traceback

PROTOCOL = 1

# Monotonic instant at which the host gives up on the request in hand.
_deadline_at = None


def remaining_s() -> float:
    """Seconds left before the host's deadline for the request being served;
    infinite outside a request or when the host sent no deadline."""
    if _deadline_at is None:
        return float("inf")
    return max(0.0, _deadline_at - time.monotonic())


class DriverRefusal(Exception):
    """An action that will not, or could not, do what it was asked, for a
    reason the protocol names: dialog, timeout, host, precondition. `extra`
    goes into the refusal's `result` beside the reason; `evidence` names the
    files already written (a screenshot of what made the action refuse)."""

    def __init__(self, reason: str, detail: str = "", extra: dict | None = None,
                 evidence: list | None = None):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail
        self.extra = extra or {}
        self.evidence = evidence or []


def _log(msg: str) -> None:
    sys.stderr.write(msg.rstrip("\n") + "\n")
    sys.stderr.flush()


def _protocol_stdout():
    """Our private copy of stdout, UTF-8 and `\\n`-terminated; descriptor 1
    and `sys.stdout` now lead to stderr."""
    fd = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return os.fdopen(fd, "w", encoding="utf-8", errors="replace", newline="\n")


def _refusal(rid, reason: str, detail: str, extra: dict | None = None,
             evidence: list | None = None) -> dict:
    detail = detail[:200]
    result = {"refused": reason, "detail": detail, "text": f"refusé ({reason}) : {detail}"}
    result.update(extra or {})
    return {"id": rid, "ok": False, "reason": reason, "detail": detail, "result": result,
            "evidence": evidence or []}


def serve(name: str, version: str, actions: dict, on_ready=None) -> None:
    """Run the loop until stdin closes.

    actions: {name: fn(args: dict, evidence_dir: str) -> (result: dict, evidence: list[dict])};
    fn may raise DriverRefusal. on_ready() runs once, right after the hello."""
    global _deadline_at
    out = _protocol_stdout()

    def say(obj) -> None:
        line = json.dumps(obj, ensure_ascii=False)
        try:
            out.write(line + "\n")
        except UnicodeEncodeError:  # belt and braces: the stream already replaces
            out.write(line.encode("utf-8", "replace").decode("utf-8") + "\n")
        out.flush()

    say({"hello": {"protocol": PROTOCOL, "driver": name, "version": version,
                   "actions": sorted(actions)}})
    if on_ready is not None:
        try:
            on_ready()
        except Exception:  # noqa: BLE001 — the driver stays up; each action reports
            _log("on_ready: " + traceback.format_exc())
    for raw in sys.stdin.buffer:
        line = raw.decode("utf-8", "replace").strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            rid = req["id"]
        except (ValueError, KeyError, TypeError):
            _log(f"not a request line, ignored: {line[:200]}")
            continue
        fn = actions.get(req.get("action"))
        if fn is None:
            say(_refusal(rid, "internal", f"no such action: {req.get('action')}"))
            continue
        deadline_ms = req.get("deadline_ms")
        _deadline_at = (time.monotonic() + deadline_ms / 1000
                        if isinstance(deadline_ms, (int, float)) else None)
        try:
            result, evidence = fn(req.get("args") or {}, req.get("evidence_dir") or "")
            result.setdefault("text", req["action"])
            answer = {"id": rid, "ok": True, "result": result, "evidence": evidence}
        except DriverRefusal as r:
            answer = _refusal(rid, r.reason, r.detail, r.extra, r.evidence)
        except KeyboardInterrupt:
            raise
        except BaseException as e:  # noqa: BLE001 — SystemExit included: the host must always get an answer
            _log(traceback.format_exc())
            answer = _refusal(rid, "internal", f"{type(e).__name__}: {e}")
        finally:
            _deadline_at = None
        try:
            say(answer)
        except (BrokenPipeError, OSError):
            return
