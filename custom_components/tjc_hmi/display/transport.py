"""Acknowledged, serialized UART transport with reconnect and boot detection."""

import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass

from .const import FIRMWARE_SIGNATURE
from .icons import atlas
from .protocol import ERROR_CODES, Frame, FrameParser, encode

_LOGGER = logging.getLogger(__name__)


class PanelError(Exception):
    """Display rejected a command or did not respond."""


@dataclass
class _Batch:
    future: asyncio.Future
    total: int
    received: int = 0


class Panel:
    def __init__(
        self,
        port: str,
        baud: int,
        on_frame: Callable[[Frame], None],
        on_connection: Callable[[bool], None],
        connector=None,
    ) -> None:
        self.port, self.baud = port, baud
        self.on_frame, self.on_connection = on_frame, on_connection
        self.connector = connector
        self.writer = None
        self.connected = False
        self._runner: asyncio.Task | None = None
        self._reader_task: asyncio.Task | None = None
        self._pending: tuple[int, asyncio.Future] | None = None
        self._lock = asyncio.Lock()
        self._fatal = asyncio.Event()
        self._batch: _Batch | None = None
        self.native_icons = False
        self._stopping = False

    def start(self) -> None:
        self._stopping = False
        self._runner = asyncio.create_task(self._run(), name="tjc_hmi_uart")

    async def stop(self) -> None:
        self._stopping = True
        if self._runner:
            self._runner.cancel()
            with suppress(asyncio.CancelledError):
                await self._runner
            self._runner = None

    async def _run(self) -> None:
        backoff = 1
        while not self._stopping:
            try:
                self._fatal.clear()
                if self.connector is None:
                    from serial_asyncio_fast import open_serial_connection

                    self.connector = open_serial_connection
                reader, self.writer = await self.connector(url=self.port, baudrate=self.baud)
                self._reader_task = asyncio.create_task(self._read(reader))
                # Set errors/success replies before requesting the application signature.
                # An unsolicited ACK from this bootstrap is intentionally ignored.
                self.writer.write(encode("bkcmd=3"))
                await self.writer.drain()
                await asyncio.sleep(0.15)
                async with self._lock:
                    signature = await self._exchange("get sys2", 0x71)
                    if signature.number != FIRMWARE_SIGNATURE:
                        raise PanelError("Install the bundled tjc_hmi.tft firmware first")
                    resources = await self._exchange("get sys1", 0x71)
                    self.native_icons = resources.number == atlas()["signature"]
                    for command in ("sleep=0", "thsp=0", "ussp=0", "sendxy=1"):
                        await self._exchange(command)
                self.connected = True
                self.on_connection(True)
                backoff = 1
                while not self._fatal.is_set():
                    try:
                        await asyncio.wait_for(self._fatal.wait(), 10)
                    except TimeoutError:
                        # Echo checks both directions even with an unchanged screen.
                        async with self._lock:
                            frame = await self._exchange("get sys2", 0x71)
                            if frame.number != FIRMWARE_SIGNATURE:
                                raise PanelError("Display application changed") from None
            except asyncio.CancelledError:
                raise
            except (OSError, PanelError, TimeoutError, ValueError) as err:
                _LOGGER.warning("TJC serial connection: %s; retry in %ss", err, backoff)
            finally:
                self.connected = False
                self.native_icons = False
                self.on_connection(False)
                if self._reader_task:
                    self._reader_task.cancel()
                    with suppress(asyncio.CancelledError, OSError):
                        await self._reader_task
                if self.writer:
                    self.writer.close()
                    with suppress(Exception):
                        await self.writer.wait_closed()
                    self.writer = None
                if self._pending and not self._pending[1].done():
                    self._pending[1].set_exception(PanelError("Serial disconnected"))
                if self._batch and not self._batch.future.done():
                    self._batch.future.set_exception(PanelError("Serial disconnected"))
            if self._stopping:
                break
            await asyncio.sleep(backoff)
            backoff = min(30, backoff * 2)

    async def _read(self, reader) -> None:
        parser = FrameParser()
        try:
            while data := await reader.read(512):
                for frame in parser.feed(data):
                    if frame.kind == 0x88 and self.connected:
                        self._fatal.set()
                    pending = self._pending
                    if self._batch and (frame.kind == 0x01 or frame.kind in ERROR_CODES):
                        batch = self._batch
                        batch.received += 1
                        if not batch.future.done():
                            if frame.kind in ERROR_CODES:
                                self._fatal.set()
                                batch.future.set_exception(
                                    PanelError(
                                        f"Display error 0x{frame.kind:02x} in batch command "
                                        f"{batch.received}/{batch.total}"
                                    )
                                )
                            elif batch.received == batch.total:
                                batch.future.set_result(None)
                    elif pending and (frame.kind == pending[0] or frame.kind in ERROR_CODES):
                        if not pending[1].done():
                            if frame.kind in ERROR_CODES:
                                pending[1].set_exception(
                                    PanelError(f"Display error 0x{frame.kind:02x}")
                                )
                            else:
                                pending[1].set_result(frame)
                    elif frame.kind in ERROR_CODES:
                        _LOGGER.debug("Unsolicited display status 0x%02x", frame.kind)
                    else:
                        self.on_frame(frame)
        except OSError as err:
            _LOGGER.debug("Serial read ended: %s", err)
        finally:
            self._fatal.set()
            if self._pending and not self._pending[1].done():
                self._pending[1].set_exception(PanelError("Serial reader stopped"))
            if self._batch and not self._batch.future.done():
                self._batch.future.set_exception(PanelError("Serial reader stopped"))

    async def _exchange(self, command: str, kind: int = 0x01) -> Frame:
        if self.writer is None or self._fatal.is_set():
            raise PanelError("Serial unavailable")
        future = asyncio.get_running_loop().create_future()
        self._pending = kind, future
        try:
            self.writer.write(encode(command))
            await self.writer.drain()
            return await asyncio.wait_for(future, 2)
        except (TimeoutError, OSError, PanelError):
            # After a timeout, a late reply must never acknowledge another command.
            self._fatal.set()
            raise
        finally:
            self._pending = None

    async def draw(self, commands: list[str]) -> None:
        encoded = [encode(command) for command in commands]
        async with self._lock:
            if not self.connected:
                raise PanelError("Display is offline")
            index = 0
            while index < len(encoded):
                chunk, size = [], 0
                while index < len(encoded) and len(chunk) < 8:
                    command = encoded[index]
                    if chunk and size + len(command) > 256:
                        break
                    chunk.append(command)
                    size += len(command)
                    index += 1
                if self._fatal.is_set() or self.writer is None:
                    raise PanelError("Serial unavailable")
                future = asyncio.get_running_loop().create_future()
                self._batch = _Batch(future, len(chunk))
                try:
                    self.writer.write(b"".join(chunk))
                    await self.writer.drain()
                    await asyncio.wait_for(future, 3)
                except (OSError, TimeoutError, PanelError):
                    self._fatal.set()
                    raise
                finally:
                    self._batch = None

    async def brightness(self, value: int) -> None:
        if not 0 <= value <= 100:
            raise ValueError("Brightness must be 0..100")
        await self.draw([f"dim={value}"])
