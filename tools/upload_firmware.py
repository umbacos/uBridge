"""Flash the display using the same uploader as the bridge and integration."""

import argparse
from pathlib import Path

from core_loader import load_core

load_core()
from tjc_hmi.firmware import upload  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("firmware", type=Path)
    parser.add_argument("--port", required=True)
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--upload-baud", type=int, choices=[115200, 230400, 921600], default=921600)
    args = parser.parse_args()
    upload(args.firmware, args.port, args.baud, args.upload_baud, print)
