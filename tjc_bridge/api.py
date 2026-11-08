"""Authenticated Home Assistant WebSocket client; never retries service calls."""

import asyncio
import ssl
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime

import aiohttp
from tjc_display.history import decode_history

from .config import normalize_url


class APIError(Exception):
    pass


class AuthenticationError(APIError):
    pass


class HomeAssistantAPI:
    def __init__(self, base_url: str, token: str, ca_file: str = ""):
        base = normalize_url(base_url)
        self.url = (
            ("wss" if base.startswith("https:") else "ws")
            + base[base.index(":") :]
            + "/api/websocket"
        )
        self._token = token.strip()
        if not self._token:
            raise AuthenticationError("Enter a Home Assistant long-lived access token.")
        self._ssl = ssl.create_default_context(cafile=ca_file or None)
        self._session = self._socket = self._reader = None
        self._next_id = 0
        self._pending = {}
        self._subscriptions = {}
        self.closed = asyncio.Event()

    async def __aenter__(self):
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=None, connect=10),
            trust_env=False,
        )
        try:
            self._socket = await self._session.ws_connect(
                self.url,
                ssl=self._ssl,
                heartbeat=20,
                max_msg_size=32 * 1024 * 1024,
            )
            greeting = await self._socket.receive_json(timeout=10)
            if greeting.get("type") != "auth_required":
                raise APIError("The address did not return the Home Assistant WebSocket API.")
            await self._socket.send_json({"type": "auth", "access_token": self._token})
            reply = await self._socket.receive_json(timeout=10)
            if reply.get("type") != "auth_ok":
                raise AuthenticationError("Home Assistant rejected the access token.")
            self._reader = asyncio.create_task(self._read(), name="tjc_ha_websocket")
            return self
        except BaseException:
            await self.__aexit__(None, None, None)
            raise

    async def __aexit__(self, *args):
        if self._reader:
            self._reader.cancel()
            with suppress(asyncio.CancelledError):
                await self._reader
        if self._socket:
            with suppress(TimeoutError, aiohttp.ClientError):
                async with asyncio.timeout(2):
                    await self._socket.close()
        if self._session:
            await self._session.close()
        self._fail_pending()

    def _fail_pending(self):
        self.closed.set()
        for future, _commit in self._pending.values():
            if not future.done():
                future.set_exception(
                    APIError("Home Assistant disconnected; action was not retried.")
                )
        self._pending.clear()

    async def _read(self):
        try:
            async for packet in self._socket:
                if packet.type != aiohttp.WSMsgType.TEXT:
                    continue
                message = packet.json()
                identifier = message.get("id")
                if message.get("type") == "event":
                    callback = self._subscriptions.get(identifier)
                    if callback:
                        callback(message.get("event", {}))
                elif message.get("type") == "result":
                    pending = self._pending.get(identifier)
                    if not pending or pending[0].done():
                        continue
                    future, commit = pending
                    if not message.get("success"):
                        error = message.get("error", {})
                        code = str(error.get("code", "error"))
                        detail = str(error.get("message", "Request failed"))
                        future.set_exception(
                            APIError(f"{code}: {detail}".replace(self._token, "[token]"))
                        )
                    else:
                        value = message.get("result")
                        # Commit snapshots inside the reader before later events can
                        # arrive, avoiding an initial-state snapshot/event race.
                        if commit:
                            commit(value)
                        future.set_result(value)
        except (aiohttp.ClientError, ValueError, TypeError):
            pass
        finally:
            self._fail_pending()

    async def _request(self, command: dict, *, commit=None, subscription=None):
        if self.closed.is_set() or self._socket is None:
            raise APIError("Home Assistant is disconnected.")
        self._next_id += 1
        identifier = self._next_id
        future = asyncio.get_running_loop().create_future()
        self._pending[identifier] = future, commit
        if subscription:
            self._subscriptions[identifier] = subscription
        try:
            await self._socket.send_json({**command, "id": identifier})
            return await asyncio.wait_for(future, 20)
        except BaseException:
            self._subscriptions.pop(identifier, None)
            raise
        finally:
            self._pending.pop(identifier, None)

    async def request(self, kind: str, *, commit=None, **data):
        return await self._request({"type": kind, **data}, commit=commit)

    async def subscribe(self, event_type: str, callback: Callable):
        return await self._request(
            {"type": "subscribe_events", "event_type": event_type},
            subscription=callback,
        )

    async def call_service(self, action):
        return await self.request(
            "call_service",
            domain=action.domain,
            service=action.service,
            service_data=action.data,
            target=action.target,
        )

    async def chart_data(self, item, end):
        start = end - item.hours * 3600
        times = {
            "start_time": datetime.fromtimestamp(start, UTC).isoformat(),
            "end_time": datetime.fromtimestamp(end, UTC).isoformat(),
        }
        if item.kind == "statistics-graph":
            raw = await self.request(
                "recorder/statistics_during_period",
                **times,
                statistic_ids=list(item.series),
                period=item.period,
                types=[item.statistic],
            )
            return decode_history(raw, start, end, statistic=item.statistic)
        raw = await self.request(
            "history/history_during_period",
            **times,
            entity_ids=list(item.series),
            include_start_time_state=True,
            significant_changes_only=False,
            minimal_response=True,
            no_attributes=True,
        )
        return decode_history(raw, start, end)

    async def weather_forecast(self, entity_id, forecast_type):
        result = await self.request(
            "call_service",
            domain="weather",
            service="get_forecasts",
            service_data={"type": forecast_type},
            target={"entity_id": entity_id},
            return_response=True,
        )
        return result.get("response", {}) if isinstance(result, dict) else {}
