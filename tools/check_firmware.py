"""Check native HMI scripts and the released TFT hash without rewriting resources.

The actual compiler is the vendor's USART HMI editor. This reader deliberately
supports only the directory format of the supplied native project.
"""

import hashlib
import json
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def resources(path: Path) -> dict[str, bytes]:
    data = path.read_bytes()
    count = struct.unpack_from("<I", data)[0]
    if not 1 <= count <= 4096:
        raise ValueError("Unexpected native HMI directory")
    result = {}
    for index in range(count):
        name, offset, size, _flags = struct.unpack_from("<16sIII", data, 4 + index * 28)
        if offset + size > len(data):
            raise ValueError("Truncated HMI resource")
        result[name.split(b"\0", 1)[0].decode("ascii")] = data[offset : offset + size]
    return result


def main():
    entries = resources(ROOT / "firmware/tjc_hmi.HMI")
    program = (ROOT / "firmware/source/Program.s").read_text().strip()
    assert entries["Program.s"].decode("ascii").replace("\r\n", "\n").strip() == program
    for line in (ROOT / "firmware/source/page0_postinitialize.txt").read_text().splitlines():
        assert line.encode("ascii") in entries["0.pa"], f"Missing page initialization: {line}"
    assert entries["0.zi"] == (ROOT / "firmware/hmi20.zi").read_bytes()
    report = json.loads((ROOT / "firmware/build.json").read_text())
    firmware = (ROOT / "firmware/tjc_hmi.tft").read_bytes()
    assert len(firmware) == report["size"]
    assert hashlib.sha256(firmware).hexdigest() == report["sha256"]
    atlas = json.loads((ROOT / "firmware/icon-atlas/manifest.json").read_text())
    assert report["resource_signature"] == atlas["signature"]
    assert f"sys1={atlas['signature']}" in program
    pictures = atlas["pictures"]
    assert report["native_icon_count"] == len(atlas["names"])
    assert report["native_picture_count"] == len(pictures)
    assert len([name for name in entries if name.endswith(".i")]) == len(pictures)
    for picture in pictures:
        picture_id = picture["id"]
        bitmap = (ROOT / "firmware/icon-atlas" / picture["file"]).read_bytes()
        assert hashlib.sha256(bitmap).hexdigest() == picture["sha256"]
        # The vendor keeps the imported BMP after its 27-byte source header.
        assert entries[f"{picture_id}.is"][27:] == bitmap, f"Picture order: {picture_id}"
        compiled = entries[f"{picture_id}.i"]
        assert struct.unpack_from("<HH", compiled, 12) == struct.unpack_from("<II", bitmap, 18)
        assert compiled[24:] in firmware, f"Picture absent from TFT: {picture_id}"
    print(
        f"Firmware passed: scripts, font, {len(pictures)} ordered native pictures, "
        "compiled picture payloads, TFT size and SHA256"
    )


if __name__ == "__main__":
    main()
