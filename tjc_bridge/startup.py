"""Absolute-path entry point for login, independent of its working directory."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tjc_bridge.app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
