"""Minimal asyncio WebSocket client for the Deriv API.

Correlates requests/responses by `req_id`. One-shot calls (authorize,
ticks_history, proposal, buy, balance) resolve a single Future; `subscribe`
keeps a queue open for streamed pushes (e.g. live ticks) that echo the same
req_id on every message, until `unsubscribe` or `close` is called.

Reference: https://developers.deriv.com/docs/ (WebSocket API). If Deriv
changes the wire protocol, this is the one file that needs updating.
"""
from __future__ import annotations

import asyncio
import itertools
import json
from typing import Any, AsyncIterator

import websockets

DEFAULT_ENDPOINT = "wss://ws.derivws.com/websockets/v3"


class DerivAPIError(Exception):
    pass


class DerivAPI:
    def __init__(self, app_id: int | str, endpoint: str = DEFAULT_ENDPOINT):
        self.app_id = app_id
        self.endpoint = endpoint
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._req_ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}
        self._subscriptions: dict[int, asyncio.Queue] = {}
        self._reader_task: asyncio.Task | None = None

    async def connect(self) -> None:
        url = f"{self.endpoint}?app_id={self.app_id}"
        self._ws = await websockets.connect(url)
        self._reader_task = asyncio.create_task(self._read_loop())

    async def close(self) -> None:
        for req_id in list(self._subscriptions):
            self._subscriptions.pop(req_id, None)
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._ws is not None:
            await self._ws.close()

    async def _read_loop(self) -> None:
        assert self._ws is not None
        async for raw in self._ws:
            msg = json.loads(raw)
            req_id = msg.get("req_id")
            if req_id in self._subscriptions:
                await self._subscriptions[req_id].put(msg)
            elif req_id in self._pending:
                fut = self._pending.pop(req_id)
                if not fut.done():
                    fut.set_result(msg)

    async def send(self, request: dict[str, Any], timeout: float = 15.0) -> dict[str, Any]:
        if self._ws is None:
            raise DerivAPIError("not connected — call connect() first")
        req_id = next(self._req_ids)
        payload = {**request, "req_id": req_id}
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = fut
        await self._ws.send(json.dumps(payload))
        try:
            msg = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError as exc:
            self._pending.pop(req_id, None)
            raise DerivAPIError(f"timed out waiting for response to {request}") from exc
        if "error" in msg:
            raise DerivAPIError(msg["error"].get("message", "unknown Deriv API error"))
        return msg

    async def subscribe(self, request: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        if self._ws is None:
            raise DerivAPIError("not connected — call connect() first")
        req_id = next(self._req_ids)
        queue: asyncio.Queue = asyncio.Queue()
        self._subscriptions[req_id] = queue
        payload = {**request, "subscribe": 1, "req_id": req_id}
        await self._ws.send(json.dumps(payload))
        try:
            while True:
                msg = await queue.get()
                if "error" in msg:
                    raise DerivAPIError(msg["error"].get("message", "unknown Deriv API error"))
                yield msg
        finally:
            self._subscriptions.pop(req_id, None)

    async def authorize(self, token: str) -> dict[str, Any]:
        return await self.send({"authorize": token})

    async def ticks_history(self, symbol: str, count: int = 1000, style: str = "ticks") -> dict[str, Any]:
        return await self.send({
            "ticks_history": symbol,
            "count": count,
            "end": "latest",
            "style": style,
        })

    async def proposal(self, **params: Any) -> dict[str, Any]:
        return await self.send({"proposal": 1, **params})

    async def buy(self, proposal_id: str, price: float) -> dict[str, Any]:
        return await self.send({"buy": proposal_id, "price": price})

    async def balance(self) -> dict[str, Any]:
        return await self.send({"balance": 1})
