"""Load the HA-independent engine without executing the integration entrypoint."""

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType("tjc_hmi")
package.__path__ = [str(ROOT / "custom_components" / "tjc_hmi" / "display")]
sys.modules["tjc_hmi"] = package
