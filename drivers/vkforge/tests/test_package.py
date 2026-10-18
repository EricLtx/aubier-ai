import ast
import contextlib
import hashlib
import io
import os
import pathlib
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[2]  # drivers/
ENTRY = ROOT / "vkforge" / "__main__.py"             # the one invocation: python -I <ENTRY> <stage> …

from vkforge import __main__ as entry  # noqa: E402  (tests/__init__.py put drivers/ on sys.path)
from vkforge import canon  # noqa: E402
from vkforge.errors import Refusal  # noqa: E402

STAGE_NAMES = ["bind", "debrief", "generate", "inventory", "mine", "plan", "qualify"]


class PackageTest(unittest.TestCase):
    def test_the_cli_lists_its_stages_and_refuses_an_unknown_one_from_any_directory(self):
        # From a foreign working directory, under -I: what the kernel spawns (Task 9).
        out = subprocess.run([sys.executable, "-I", str(ENTRY), "--help"], cwd=tempfile.gettempdir(),
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        for stage in STAGE_NAMES:
            self.assertIn(stage, out.stdout)
        bad = subprocess.run([sys.executable, "-I", str(ENTRY), "nope"], cwd=tempfile.gettempdir(),
                             capture_output=True, text=True)
        self.assertNotEqual(bad.returncode, 0)

    def test_the_stage_table_is_built_in_main_and_no_stage_registers_itself(self):
        self.assertEqual(sorted(entry.STAGES), STAGE_NAMES)
        for name, module in entry.STAGES.items():
            self.assertTrue(callable(module.add_arguments) and callable(module.run), name)
            self.assertNotIn("STAGES", vars(module), f"{name}: a stage registers nothing at import")

    def test_a_refusal_is_one_line_in_the_kernel_voice(self):
        e = Refusal("corpus has 3 files", "more documents of one kind")
        self.assertEqual(str(e), "corpus has 3 files; remedy: more documents of one kind")
        self.assertEqual((e.what, e.remedy), ("corpus has 3 files", "more documents of one kind"))

        def refuse(ns):
            raise Refusal("corpus has 3 files", "more documents of one kind")

        stage = types.SimpleNamespace(add_arguments=lambda parser: None, run=refuse)
        err = io.StringIO()
        with mock.patch.dict(entry.STAGES, {"fake": stage}), contextlib.redirect_stderr(err):
            code = entry.main(["fake"])
        self.assertEqual(code, 2)
        self.assertEqual(err.getvalue(), "vkforge fake: corpus has 3 files; remedy: more documents of one kind\n")

    def test_a_refusal_reaches_the_kernel_in_utf8_whatever_the_console_code_page(self):
        """The kernel reads a stage's stderr as UTF-8 (`run_stage`, `from_utf8_lossy`). Spawned with
        -I, stderr a pipe, UTF-8 mode off and no PYTHONIOENCODING, Python would write the locale's
        code page (cp1252 on the node): `main` makes stderr UTF-8 itself (re-review N2)."""
        from vkforge.tests import debrief_fixtures
        run = debrief_fixtures.clean_run(pathlib.Path(tempfile.mkdtemp()) / "run")
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
        out = str(pathlib.Path(tempfile.mkdtemp()) / "out")
        cases = [(["--until", "tâche-é"], "until: no task tâche-é in the run; remedy: ")]
        with (run / "journal.jsonl").open("a", encoding="utf-8") as f:
            f.write('{"ts": "2026-10-20T11:06+02:00", "ca\n')  # a line cut mid-write: REMEDY names §16
        cases.append(([], "copy that file again as runbook recette-maskai §16 does"))
        for extra, said in cases:
            r = subprocess.run([sys.executable, "-I", "-X", "utf8=0", str(ENTRY), "debrief", "--run", str(run),
                                "--out", out, *extra], cwd=tempfile.gettempdir(), capture_output=True, env=env)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn(said, r.stderr.decode("utf-8"))  # strict: a cp1252 byte would raise here

    def test_canonical_bytes_are_sorted_indented_and_hashed_as_written(self):
        obj = {"b": [1, {"é": "ü"}], "a": None}
        data = canon.dumps(obj)
        self.assertEqual(data, '{\n "a": null,\n "b": [\n  1,\n  {\n   "é": "ü"\n  }\n ]\n}\n'.encode("utf-8"))
        path = pathlib.Path(tempfile.mkdtemp()) / "x.json"
        canon.write(obj, path)
        self.assertEqual(path.read_bytes(), data)
        self.assertEqual(canon.sha256(obj), canon.sha256_file(path))
        self.assertEqual(canon.sha256(obj), "sha256:" + hashlib.sha256(data).hexdigest())

    def test_the_package_opens_no_socket(self):
        """No module of the package imports a network module, in any form: `import a, socket`,
        `from socket import x`, `from urllib import request`, `__import__("ssl")`."""
        banned = {"socket", "ssl", "urllib", "http", "requests", "ftplib", "smtplib"}
        for p in (ROOT / "vkforge").rglob("*.py"):
            if "tests" in p.parts:
                continue
            found = []
            for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"), str(p))):
                if isinstance(node, ast.Import):
                    found += [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    found.append(node.module or "")
                elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                      and node.func.id == "__import__" and node.args
                      and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                    found.append(node.args[0].value)
            for name in found:
                self.assertNotIn(name.split(".")[0], banned, f"{p}: imports {name}")


if __name__ == "__main__":
    unittest.main()
