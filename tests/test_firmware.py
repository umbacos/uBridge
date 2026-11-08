"""Exercise the upload wire protocol without touching a physical serial port."""

import pytest
import serial
from tjc_hmi.firmware import END, upload


class Wire:
    def __init__(self, identity=b"comok TJC4827X243_011C", replies=None):
        self.identity = identity
        self.replies = iter(replies or [b"\x05"] * 3)
        self.writes = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def write(self, data):
        self.writes.append(data)

    def flush(self):
        pass

    def reset_input_buffer(self):
        pass

    def read_until(self, end):
        return self.identity + end

    def read(self, count):
        return next(self.replies, b"")


def test_upload_acknowledges_final_partial_block_and_closes(tmp_path, monkeypatch):
    path = tmp_path / "panel.tft"
    data = b"a" * 5000
    path.write_bytes(data)
    wire = Wire()
    monkeypatch.setattr(serial, "Serial", lambda *a, **k: wire)
    messages = []
    upload(path, "COM4", 115200, progress=messages.append)
    assert wire.writes == [
        END,
        b"connect" + END,
        b"whmi-wri 5000,921600,0" + END,
        data[:4096],
        data[4096:],
    ]
    assert wire.closed and wire.baudrate == 921600
    assert any("5000/5000" in message for message in messages)


@pytest.mark.parametrize(
    "identity,replies,error",
    [
        (b"wrong model", None, "Expected"),
        (b"TJC4827X243_011", [b""], "enter upload"),
        (b"TJC4827X243_011", [b"\x05", b""], "byte 0"),
    ],
)
def test_upload_rejects_wrong_model_and_stops_after_missing_ack(
    tmp_path, monkeypatch, identity, replies, error
):
    path = tmp_path / "panel.tft"
    path.write_bytes(b"a" * 5000)
    wire = Wire(identity, replies)
    monkeypatch.setattr(serial, "Serial", lambda *a, **k: wire)
    with pytest.raises(RuntimeError, match=error):
        upload(path, "COM4", 115200)
    assert wire.closed and b"a" * 904 not in wire.writes
