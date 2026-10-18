"""python -I drivers/vkforge/__main__.py <stage> …  — the learning pipeline, one stage per call.

The one way vkforge runs (the kernel's spawn, CI, the runbooks). Not `python -I -m vkforge`: -I
keeps the working directory and the script's directory off sys.path, so the package would not be
found. This file puts drivers/ there itself, as drivers/maskai-recette/driver.py does for vkdriver.
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent              # drivers/vkforge
sys.path.insert(0, str(HERE.parent))                 # drivers/: vkforge and vkdriver from the repository

from vkforge import debrief, generate, inventory, mine, ontology, plan, qualify  # noqa: E402
from vkforge.errors import Refusal  # noqa: E402

# The stage table, built here and nowhere else. A stage module defines add_arguments(parser) and
# run(ns) -> int and does nothing at import: run as a script this file is `__main__`, so a stage
# importing `vkforge.__main__` to register itself would fill a second copy of this table.
STAGES = {"inventory": inventory, "bind": ontology, "mine": mine, "plan": plan, "generate": generate,
          "qualify": qualify, "debrief": debrief}


def main(argv=None) -> int:
    # The kernel reads what a stage prints as UTF-8 (`run_stage`). Under -I, with stdout and stderr
    # redirected to files and UTF-8 mode off, Python would write the locale's code page (cp1252 on
    # a French Windows): `§16` or `…` in a refusal would reach the operator mangled.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):  # a test's StringIO has none, and needs none
            stream.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="vkforge")
    sub = p.add_subparsers(dest="stage", required=True)
    for name, module in STAGES.items():
        module.add_arguments(sub.add_parser(name))
    ns = p.parse_args(argv)
    try:
        return STAGES[ns.stage].run(ns)
    except Refusal as e:
        sys.stderr.write(f"vkforge {ns.stage}: {e}\n")   # L-P15: the one refusal line the kernel relays
        return 2


if __name__ == "__main__":
    sys.exit(main())
