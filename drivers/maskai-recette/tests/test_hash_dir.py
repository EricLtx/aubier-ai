"""`drivers/vkdriver/hash_dir.py` against the Rust `vk_contracts::driver::hash_dir`:
the same directories as the Rust tests, the same known-answer strings."""
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest

TESTS = os.path.dirname(os.path.abspath(__file__))
DRIVERS = os.path.dirname(os.path.dirname(TESTS))
sys.path.insert(0, DRIVERS)

from vkdriver.hash_dir import hash_dir  # noqa: E402


def put(root: str, rel: str, data: bytes) -> None:
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


class HashDirTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.d, ignore_errors=True))

    def test_known_answer_matches_the_rust_test(self):
        # crates/vk-contracts/src/driver.rs,
        # hash_dir_is_order_independent_and_skips_pycache_tests_fixture
        put(self.d, "b.py", b"B")
        put(self.d, "a.py", b"A")
        put(self.d, "__pycache__/x.pyc", b"junk")
        h1 = hash_dir(self.d)
        put(self.d, "__pycache__/y.pyc", b"more")
        self.assertEqual(h1, hash_dir(self.d))
        self.assertEqual(h1, "sha256:" + hashlib.sha256(b"a.py\0A\0b.py\0B\0").hexdigest())
        self.assertEqual(
            h1, "sha256:d43a7ce0a1426c212ee56313af57afab671634ad6291065de0850990586c3d71")

    def test_per_machine_dirs_at_any_depth_and_top_level_manifests_are_skipped(self):
        put(self.d, "a.py", b"A")
        put(self.d, "sub/m.py", b"M")
        base = hash_dir(self.d)
        put(self.d, "sub/__pycache__/x.pyc", b"junk")
        put(self.d, ".venv/lib/x.py", b"venv")
        put(self.d, "sub/.venv/y.py", b"venv")
        put(self.d, "sub/tests/t.py", b"T")
        put(self.d, "fixture/index.html", b"F")
        put(self.d, "manifest.json", b"{}")
        put(self.d, "manifest-fixture.json", b"{}")
        self.assertEqual(base, hash_dir(self.d), "debris and manifests are not identity")
        put(self.d, "sub/manifest.json", b"{}")
        self.assertNotEqual(base, hash_dir(self.d), "a nested manifest.json is a driver file")

    def test_subdirectories_are_walked_with_forward_slashes(self):
        put(self.d, "lib/m.py", b"M")
        put(self.d, "tests/t.py", b"T")
        self.assertEqual(
            hash_dir(self.d),
            "sha256:3e9af73ad186f7f5c6eec393541f8a539fe8bb924551c7a6c9e54f1cc0b2fe77")

    def test_the_manifests_carry_this_directory_hash(self):
        import json
        driver_dir = os.path.dirname(TESTS)
        repo = os.path.dirname(DRIVERS)

        def read(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)

        live = read(os.path.join(driver_dir, "manifest.json"))
        fixture = read(os.path.join(driver_dir, "manifest-fixture.json"))
        example = read(os.path.join(repo, "contracts", "examples", "driver_manifest", "valid", "maskai.json"))
        self.assertEqual(live["program"]["files_sha256"], hash_dir(driver_dir),
                         "driver files changed: regenerate files_sha256 in both manifests")
        self.assertEqual(fixture["program"]["files_sha256"], live["program"]["files_sha256"])
        self.assertEqual(live["program"]["command"], [".venv/Scripts/python.exe", "-I", "driver.py"])
        self.assertEqual(fixture["actions"], live["actions"])
        self.assertEqual((fixture["hosts"], fixture["program"]["headless"]), (["127.0.0.1"], True))
        self.assertEqual(live["hosts"], ["app.maskai.fr"])
        self.assertEqual((live["max_action_ms"], fixture["max_action_ms"]), (240_000, 240_000))  # R17
        self.assertEqual(example, live, "the contract example is a copy of manifest.json")

    def test_the_command_line_prints_the_hash(self):
        put(self.d, "a.py", b"A")
        put(self.d, "b.py", b"B")
        script = os.path.join(DRIVERS, "vkdriver", "hash_dir.py")
        out = subprocess.run([sys.executable, "-I", script, self.d],
                             capture_output=True, text=True, check=True, timeout=60).stdout
        self.assertEqual(out.strip(), hash_dir(self.d))


if __name__ == "__main__":
    unittest.main()
