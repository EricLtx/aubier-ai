"""The vkforge suite: `drivers/` on sys.path once, for every test module, whatever the discovery top-level."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
