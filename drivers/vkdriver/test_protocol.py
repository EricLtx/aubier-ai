"""The protocol-1 loop as the host sees it: a real interpreter, real pipes."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

DRIVERS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# A driver with four actions: one that echoes, one that refuses, one that
# writes an evidence file, and one that prints on stdout behind the loop's back.
SCRIPT = r"""
import os, sys
sys.path.insert(0, sys.argv[1])
from vkdriver.protocol import serve, DriverRefusal, remaining_s

def echo(args, evidence_dir):
    return {"echo": args, "remaining_ok": 0 < remaining_s() <= 5, "text": "echo"}, []

def refuse(args, evidence_dir):
    raise DriverRefusal("dialog", "Save changes?")

def proof(args, evidence_dir):
    path = os.path.join(evidence_dir, "proof.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("ok")
    return {"text": "proof"}, [{"name": "proof.txt", "mime": "text/plain", "path": path}]

def noisy(args, evidence_dir):
    import subprocess
    print("noise on stdout")
    subprocess.run([sys.executable, "-I", "-c", "print('child noise')"], check=True)
    return {"text": "noisy"}, []

serve("t", "0", {"echo": echo, "refuse": refuse, "proof": proof, "noisy": noisy})
"""


class ProtocolTest(unittest.TestCase):
    def run_driver(self, requests):
        ev = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(ev, ignore_errors=True))
        lines = [json.dumps({"id": i + 1, "action": a, "args": args,
                             "deadline_ms": 5000, "evidence_dir": ev}, ensure_ascii=False)
                 for i, (a, args) in enumerate(requests)]
        p = subprocess.run([sys.executable, "-I", "-c", SCRIPT, DRIVERS],
                           input=("\n".join(lines) + "\n").encode("utf-8"),
                           capture_output=True, timeout=60)
        err = p.stderr.decode("utf-8", "replace")
        self.assertEqual(p.returncode, 0, err)
        out = [json.loads(line) for line in p.stdout.decode("utf-8").splitlines()]
        return out, err, ev

    def test_hello_then_one_answer_per_request(self):
        out, _, _ = self.run_driver([("echo", {"x": "é"}), ("refuse", {}), ("nope", {})])
        self.assertEqual(out[0], {"hello": {"protocol": 1, "driver": "t", "version": "0",
                                            "actions": ["echo", "noisy", "proof", "refuse"]}})
        self.assertEqual(out[1], {"id": 1, "ok": True, "evidence": [],
                                  "result": {"echo": {"x": "é"}, "remaining_ok": True, "text": "echo"}})
        self.assertEqual(out[2]["id"], 2)
        self.assertFalse(out[2]["ok"])
        self.assertEqual(out[2]["reason"], "dialog")
        self.assertEqual(out[2]["detail"], "Save changes?")
        # The harness reads `result` only, so a refusal says so there too.
        self.assertEqual(out[2]["result"]["refused"], "dialog")
        self.assertIn("Save changes?", out[2]["result"]["text"])
        self.assertEqual(out[3]["id"], 3)
        self.assertEqual((out[3]["ok"], out[3]["reason"]), (False, "internal"))
        self.assertEqual(len(out), 4)

    def test_evidence_is_named_by_path(self):
        out, _, ev = self.run_driver([("proof", {})])
        self.assertEqual(out[1]["evidence"],
                         [{"name": "proof.txt", "mime": "text/plain",
                           "path": os.path.join(ev, "proof.txt")}])

    def test_nothing_but_protocol_lines_reach_stdout(self):
        out, err, _ = self.run_driver([("noisy", {}), ("echo", {})])
        self.assertEqual([o.get("id") for o in out[1:]], [1, 2])
        self.assertTrue(out[1]["ok"])
        self.assertIn("noise on stdout", err)
        self.assertIn("child noise", err)  # a child inheriting stdout writes to the log


if __name__ == "__main__":
    unittest.main()
