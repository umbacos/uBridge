"""Expose the engine without requiring a Home Assistant installation."""

import sys
import types
from pathlib import Path


def load_core():
    root = Path(__file__).resolve().parents[1]
    package = types.ModuleType("tjc_hmi")
    package.__path__ = [str(root / "custom_components" / "tjc_hmi" / "display")]
    sys.modules.setdefault("tjc_hmi", package)
    return root
