"""Flash a compiled TJC TFT over serial. Run explicitly with the intended port."""

import hashlib
import time
from pathlib import Path

MODEL = b"TJC4827X243_011"
END = b"\xff\xff\xff"


def command(panel, value):
    panel.write(value.encode("ascii") + END)
    panel.flush()


def upload(path, port, baud, upload_baud=921600, progress=lambda message: None):
    import serial

    path = Path(path)
    data = path.read_bytes()
    if not 4096 <= len(data) <= 8 * 1024 * 1024:
        raise ValueError("Not a compiled TFT file")
    checksum = hashlib.sha256(data).hexdigest()
    progress(f"Image: {path.name}, {len(data):,} bytes, SHA256 {checksum}")
    if str(port).startswith(("socket://", "rfc2217://")):
        raise ValueError("Firmware upload requires a local serial device")
    with serial.Serial(port, baud, timeout=3, write_timeout=10) as panel:
        panel.dtr = panel.rts = False
        panel.write(END)
        panel.reset_input_buffer()
        command(panel, "connect")
        identity = panel.read_until(END)
        if MODEL not in identity:
            raise RuntimeError(
                f"Expected {MODEL.decode()}, received {identity!r}; upload cancelled"
            )
        progress(identity.removesuffix(END).decode("ascii", errors="backslashreplace"))
        panel.reset_input_buffer()
        command(panel, f"whmi-wri {len(data)},{upload_baud},0")
        time.sleep(0.1)
        panel.baudrate = upload_baud
        if panel.read(1) != b"\x05":
            raise RuntimeError("Display did not enter upload mode")
        for offset in range(0, len(data), 4096):
            panel.write(data[offset : offset + 4096])
            panel.flush()
            if panel.read(1) != b"\x05":
                raise RuntimeError(
                    f"Missing block acknowledgement at byte {offset}; reflash using microSD"
                )
            if offset == 0 or (offset + 4096) % 65536 == 0 or offset + 4096 >= len(data):
                progress(f"Uploaded {min(offset + 4096, len(data))}/{len(data)}")
        progress("\nUpload complete. Allow the display to reboot; application baud is 115200.")
