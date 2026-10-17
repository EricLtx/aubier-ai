"""Documentation tree check (docs/README.md, filing rule 5).

Two checks, both over the files git tracks:

1. Every Markdown file under `docs/` sits in one of the four roots
   (`technical`, `functional`, `strategy`, `archive`) or is `docs/README.md`.
2. Every repository path a tracked text file names in back-ticks or in a
   Markdown link — `docs/...`, `contracts/...`, `scripts/...`, `drivers/...`,
   `recipes/...`, `crates/...` — exists. Not checked: a path with a glob or a
   placeholder (`<...>`, `...`, `*`, `YYYY`), a private path (git-ignored, so
   absent on CI), `docs/superpowers` (named only as the place not to use), and
   anything named by a historical record (`docs/archive/`, recorded demo runs).

Exit 1 with one line per finding; exit 0 when clean. Stdlib only.
"""
import os
import re
import subprocess
import sys

ROOTS = ("technical", "functional", "strategy", "archive")
PATH_RE = re.compile(
    r"(?:`|\]\()((?:docs|contracts|scripts|drivers|recipes|crates)/[A-Za-z0-9._/-]+)(?:`|\))"
)
# Named as the place a document must NOT go; not a path to resolve.
DEPRECATED = {"docs/superpowers"}
# Historical records are not kept in step with the code they name.
RECORDS = ("docs/archive/", "docs/functional/demo/runs/")
PRIVATE_PREFIXES = (
    "docs/strategy/",
    "docs/archive/strategy/",
    "docs/technical/reviews/",
    "docs/archive/technical/reviews/",
)
TEXT_EXT = (".md", ".py", ".json", ".ps1", ".yml", ".toml", ".rs")


def tracked_files(repo: str) -> list:
    out = subprocess.check_output(["git", "ls-files"], cwd=repo, text=True)
    return [line for line in out.splitlines() if line]


def is_private(path: str) -> bool:
    return (path + "/").startswith(PRIVATE_PREFIXES) or "/private/" in path + "/"


def placeholder(path: str) -> bool:
    return any(tok in path for tok in ("<", ">", "...", "*", "YYYY", "{"))


def main() -> int:
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    files = tracked_files(repo)
    findings = []

    for f in files:
        if f.startswith("docs/") and f.endswith(".md") and f != "docs/README.md":
            if f.split("/")[1] not in ROOTS:
                findings.append(f"{f}: outside the documentation tree (docs/README.md)")

    for f in files:
        if not f.endswith(TEXT_EXT) or f.startswith(RECORDS):
            continue
        try:
            with open(os.path.join(repo, f), encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError):
            continue
        for m in PATH_RE.finditer(text):
            p = m.group(1).rstrip("/").split("#")[0]
            if placeholder(p) or is_private(p) or p in DEPRECATED:
                continue
            if not os.path.exists(os.path.join(repo, p)):
                findings.append(f"{f}: names `{p}`, which does not exist")

    for line in findings:
        print(line)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
