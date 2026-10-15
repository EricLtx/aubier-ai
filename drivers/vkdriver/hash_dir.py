"""`sha256:<hex>` over a driver's directory: the twin of the Rust
`vk_contracts::driver::hash_dir`, so a changed file is a new driver.

Every regular file under the directory is listed by its path relative to it,
with `/` separators; any path with a component equal to `__pycache__`,
`.venv`, `tests` or `fixture`, at any depth, is skipped, and so is any
top-level file named `manifest*.json` (the manifest carries this very hash,
so it cannot be part of it); links and junctions are neither files nor
directories and are skipped; the list is sorted by path; `path\\0bytes\\0` is
fed to SHA-256 for each entry.

Usage: python -I drivers/vkdriver/hash_dir.py <driver dir>
"""
import hashlib
import os
import sys

SKIPPED = {"__pycache__", ".venv", "tests", "fixture"}


def _is_link(entry: os.DirEntry) -> bool:
    return entry.is_symlink() or (hasattr(entry, "is_junction") and entry.is_junction())


def hash_dir(directory: str) -> str:
    files = []

    def walk(path: str, rel: list) -> None:
        with os.scandir(path) as it:
            for entry in it:
                parts = rel + [entry.name]
                if any(p in SKIPPED for p in parts) or _is_link(entry):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    walk(entry.path, parts)
                elif entry.is_file(follow_symlinks=False):
                    top_manifest = (len(parts) == 1 and entry.name.startswith("manifest")
                                    and entry.name.endswith(".json"))
                    if not top_manifest:
                        files.append(("/".join(parts), entry.path))

    walk(directory, [])
    files.sort(key=lambda f: f[0].encode("utf-8"))
    h = hashlib.sha256()
    for rel, path in files:
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        with open(path, "rb") as f:
            h.update(f.read())
        h.update(b"\0")
    return "sha256:" + h.hexdigest()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.stderr.write(__doc__)
        sys.exit(2)
    print(hash_dir(sys.argv[1]))
