"""The vkforge suite: `drivers/` on sys.path once, for every test module, whatever the discovery top-level."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Why a real-Office test skipped: it is a node step, never part of a plain run (ruling PF-19).
NODE_STEP = "a node step (rulings PF-5, PF-19): VK_FORGE_NODE_OFFICE=1, on Windows, the application installed"


def node_office(app: str) -> bool:
    """Whether this run is a node step for `app` (ruling PF-19). A real-Office test starts or touches Word
    or PowerPoint, so it runs only when the operator asks for it — VK_FORGE_NODE_OFFICE=1, which the
    Step 8 and Step 9 commands set and nothing else does — on Windows, the application installed."""
    if sys.platform != "win32" or os.environ.get("VK_FORGE_NODE_OFFICE") != "1":
        return False
    from vkforge import inventory

    return inventory.office_present(app)
