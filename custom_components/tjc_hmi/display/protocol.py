"""Bounded streaming parser for TJC's binary replies (not delimiter splitting)."""

import unicodedata
from dataclasses import dataclass

TERMINATOR = b"\xff\xff\xff"
ERROR_CODES = {
    0x00,
    0x02,
    0x03,
    0x04,
    0x05,
    0x06,
    0x09,
    0x11,
    0x12,
    0x1A,
    0x1B,
    0x1C,
    0x1D,
    0x1E,
    0x1F,
    0x20,
    0x23,
    0x24,
}
FIXED_LENGTHS = dict.fromkeys(ERROR_CODES | {0x01, 0x86, 0x87, 0x88, 0x89}, 1)
FIXED_LENGTHS.update({0x65: 4, 0x66: 2, 0x67: 6, 0x68: 6, 0x71: 5})


@dataclass(frozen=True)
class Frame:
    data: bytes

    @property
    def kind(self) -> int:
        return self.data[0]

    @property
    def number(self) -> int:
        if self.kind != 0x71:
            raise ValueError("Not a numeric reply")
        return int.from_bytes(self.data[1:], "little")

    @property
    def touch(self) -> tuple[int, int, bool]:
        if self.kind not in (0x67, 0x68):
            raise ValueError("Not a coordinate reply")
        return (
            int.from_bytes(self.data[1:3], "big"),
            int.from_bytes(self.data[3:5], "big"),
            self.data[5] == 1,
        )


class FrameParser:
    def __init__(self) -> None:
        self.buffer = bytearray()

    def feed(self, data: bytes) -> list[Frame]:
        self.buffer.extend(data)
        frames = []
        while self.buffer:
            kind = self.buffer[0]
            size = FIXED_LENGTHS.get(kind)
            if size is not None:
                if len(self.buffer) < size + 3:
                    break
                if self.buffer[size : size + 3] != TERMINATOR:
                    del self.buffer[0]  # Noise: look for a valid framed reply.
                    continue
            elif kind == 0x70:
                size = self.buffer.find(TERMINATOR, 1)
                if size == -1:
                    if len(self.buffer) > 1024:
                        del self.buffer[0]
                        continue
                    break
            else:
                del self.buffer[0]
                continue
            frames.append(Frame(bytes(self.buffer[:size])))
            del self.buffer[: size + 3]
        return frames


def display_text(value: object, limit: int = 40) -> str:
    """Font 0 is printable ASCII. Strip command syntax and transliterate accents."""
    text = str(value).replace("°", " deg ").replace("€", " EUR ")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = "".join(c if 32 <= ord(c) < 127 and c not in '"\\' else " " for c in text)
    return " ".join(text.split())[:limit]


def encode(command: str) -> bytes:
    raw = command.encode("ascii")
    if not raw or len(raw) > 240 or any(c < 32 or c > 126 for c in raw):
        raise ValueError("Invalid or oversized serial command")
    return raw + TERMINATOR
