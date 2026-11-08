import asyncio

import pytest
from tjc_hmi.const import FIRMWARE_SIGNATURE
from tjc_hmi.protocol import TERMINATOR
from tjc_hmi.transport import Panel, PanelError


class FakeWire:
    """Full duplex byte streams: commands go through the real production parser."""

    def __init__(self, signature=FIRMWARE_SIGNATURE, atlas_signature=0):
        self.reader = asyncio.StreamReader()
        self.commands = []
        self.signature = signature
        self.closed = False
        self.drop = False
        self.atlas_signature = atlas_signature
        self.writes = []

    async def connect(self, **kwargs):
        return self.reader, self

    def write(self, data):
        assert data.endswith(TERMINATOR)
        self.writes.append(data)
        reply = b""
        for encoded in data[:-3].split(TERMINATOR):
            command = encoded.decode("ascii")
            self.commands.append(command)
            if self.drop:
                continue
            if command in {"get sys2", "get sys1"}:
                number = self.signature if command == "get sys2" else self.atlas_signature
                reply += b"\x71" + number.to_bytes(4, "little") + TERMINATOR
            elif command == "bad":
                reply += b"\x1a" + TERMINATOR
            else:
                reply += b"\x01" + TERMINATOR
        # Fragment response to exercise reader reassembly while a waiter is pending.
        loop = asyncio.get_running_loop()
        loop.call_soon(self.reader.feed_data, reply[:2])
        loop.call_soon(self.reader.feed_data, reply[2:])

    async def drain(self):
        await asyncio.sleep(0)

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


async def wait_connected(panel):
    for _ in range(40):
        if panel.connected:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("Panel did not finish handshake")


async def test_handshake_touch_drawing_and_stop_cleanup():
    wire = FakeWire()
    frames, connections = [], []
    panel = Panel("mock", 115200, frames.append, connections.append, wire.connect)
    panel.start()
    try:
        await wait_connected(panel)
        assert wire.commands[:2] == ["bkcmd=3", "get sys2"]
        assert "sendxy=1" in wire.commands
        await asyncio.gather(panel.draw(["cls 0", "fill 0,0,10,10,1"]), panel.brightness(70))
        assert wire.commands[-3:] in (
            ["cls 0", "fill 0,0,10,10,1", "dim=70"],
            ["dim=70", "cls 0", "fill 0,0,10,10,1"],
        )
        wire.reader.feed_data(b"\x67\x00\xff\x00\x20\x00" + TERMINATOR)
        await asyncio.sleep(0.01)
        assert frames[-1].touch == (255, 32, False)
    finally:
        await panel.stop()
    assert wire.closed and not panel.connected and connections[-1] is False


async def test_wrong_firmware_never_receives_drawing():
    wire = FakeWire(42)
    panel = Panel("mock", 115200, lambda f: None, lambda c: None, wire.connect)
    panel.start()
    try:
        await asyncio.sleep(0.2)
        assert not panel.connected
        with pytest.raises(PanelError):
            await panel.draw(["cls 0"])
        assert wire.commands == ["bkcmd=3", "get sys2"]
    finally:
        await panel.stop()


async def test_command_error_forces_reconnect_instead_of_misattributing_ack():
    wire = FakeWire()
    panel = Panel("mock", 115200, lambda f: None, lambda c: None, wire.connect)
    panel.start()
    try:
        await wait_connected(panel)
        with pytest.raises(PanelError, match="0x1a"):
            await panel.draw(["bad", *["fill 0,0,1,1,0"] * 8, "cls 0"])
        assert "cls 0" not in wire.commands
        assert panel._fatal.is_set()
    finally:
        await panel.stop()


async def test_bounded_batches_ack_every_command_and_negotiate_native_icons():
    from tjc_hmi.icons import atlas

    wire = FakeWire(atlas_signature=atlas()["signature"])
    panel = Panel("mock", 115200, lambda f: None, lambda c: None, wire.connect)
    panel.start()
    try:
        await wait_connected(panel)
        assert panel.native_icons
        start = len(wire.writes)
        commands = ["fill 0,0,1,1,0"] * 64
        await panel.draw(commands)
        assert len(wire.writes) - start == 8
        assert wire.commands[-64:] == commands
        assert all(len(chunk) <= 256 for chunk in wire.writes[start:])
    finally:
        await panel.stop()


async def test_missing_batch_ack_times_out_without_sending_another_chunk():
    wire = FakeWire()
    panel = Panel("mock", 115200, lambda f: None, lambda c: None, wire.connect)
    panel.start()
    try:
        await wait_connected(panel)
        wire.drop = True
        start = len(wire.writes)
        with pytest.raises(TimeoutError):
            await panel.draw(["fill 0,0,1,1,0"] * 20)
        assert len(wire.writes) - start == 1 and panel._fatal.is_set()
    finally:
        await panel.stop()
