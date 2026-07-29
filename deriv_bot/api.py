"""Minimal asyncio client for Deriv's current API.

Deriv retired the old "Legacy API" (wss://ws.derivws.com/websockets/v3 with a
WebSocket `authorize` message + numeric app_id). The current API instead:

  - serves public market data on a public WebSocket with no auth at all
    (`PUBLIC_WS_ENDPOINT`)
  - requires a REST call, authenticated with a Personal Access Token (PAT) as
    a bearer token plus a `Deriv-App-ID` header, to mint a one-time,
    account-scoped WebSocket URL for trading (`list_accounts` +
    `request_trading_ws_url`) — there is no WS-level `authorize` message
    anymore; the returned URL is already authenticated.

Once connected — to either endpoint — the JSON message protocol itself
(ticks_history, proposal, buy, balance, proposal_open_contract, ...) is
unchanged from the legacy API. Verified directly against the live API while
building this; see git history for the empirical trail if this ever needs
re-deriving.

Reference: https://developers.deriv.com/docs/
"""
from __future__ import annotations

import asyncio
import itertools
import json
import urllib.error
import urllib.request
from typing import Any, AsyncIterator

import websockets

PUBLIC_WS_ENDPOINT = "wss://api.derivws.com/trading/v1/options/ws/public"
REST_BASE = "https://api.derivws.com"


# A tick contract settles in seconds; this only ever fires on a dead socket.
SETTLEMENT_TIMEOUT = 120.0


class DerivAPIError(Exception):
    pass


class DerivAPI:
    def __init__(self, app_id: str, endpoint: str = PUBLIC_WS_ENDPOINT):
        self.app_id = app_id
        self.endpoint = endpoint
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._req_ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}
        self._subscriptions: dict[int, asyncio.Queue] = {}
        self._reader_task: asyncio.Task | None = None

    async def connect(self, ws_url: str | None = None) -> None:
        """Connects to `ws_url` if given (an account-scoped trading URL from
        `request_trading_ws_url`), otherwise to the public market-data
        endpoint.
        """
        self._ws = await websockets.connect(ws_url or self.endpoint)
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

    def _rest_call(
        self, method: str, path: str, token: str, body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{REST_BASE}{path}"
        headers = {
            "Deriv-App-ID": self.app_id,
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            raise DerivAPIError(f"{method} {path} failed: HTTP {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:
            raise DerivAPIError(f"{method} {path} failed: {exc.reason}") from exc

    async def list_accounts(self, token: str) -> list[dict[str, Any]]:
        """Returns the accounts (demo and real) this token can access, e.g.
        `[{"account_id": "DOT93163621", "account_type": "demo",
        "currency": "USD", "balance": "10000.00", ...}, ...]`.
        """
        resp = await asyncio.to_thread(self._rest_call, "GET", "/trading/v1/options/accounts", token)
        return resp["data"]

    async def request_trading_ws_url(self, token: str, account_id: str) -> str:
        """Mints a one-time, account-scoped WebSocket URL. Connecting to it
        (via `connect(ws_url=...)`) is already authenticated — there's no
        separate `authorize` message to send.
        """
        resp = await asyncio.to_thread(
            self._rest_call, "POST", f"/trading/v1/options/accounts/{account_id}/otp", token, {},
        )
        return resp["data"]["url"]

    async def ticks_history(self, symbol: str, count: int = 1000, style: str = "ticks") -> dict[str, Any]:
        return await self.send({
            "ticks_history": symbol,
            "count": count,
            "end": "latest",
            "style": style,
        })

    async def candles(self, symbol: str, granularity: int = 60,
                      count: int = 1000) -> list[dict[str, Any]]:
        """OHLC candles, for measuring realised volatility.

        `ticks_history` already takes a `style`, but the candle call also
        needs `granularity` (seconds per candle) and returns its data under
        `candles` rather than `history.prices` - different enough to be worth
        its own method rather than a footgun in the caller.

        Volatility is what decides which symbol is cheapest to trade
        multipliers on, and it has to be measured: the synthetic names are
        accurate (R_10 measures 10.1%) but EUR/USD measures 3.9% against a
        textbook 8%.
        """
        resp = await self.send({
            "ticks_history": symbol,
            "count": count,
            "end": "latest",
            "style": "candles",
            "granularity": granularity,
        })
        return resp.get("candles") or []

    async def candle_history(self, symbol: str, granularity: int = 60,
                             count: int = 10000, page_pause: float = 0.2,
                             on_page: Any = None) -> list[dict[str, Any]]:
        """Candles going back further than one request allows.

        Deriv caps a single `ticks_history` at 1000 candles - 17 hours at one
        minute, which is far too little to tell a real edge from luck. Paging
        by passing the oldest epoch back as `end` lifts that: verified to walk
        backwards indefinitely.

        Returned oldest-first, de-duplicated by epoch (page boundaries repeat
        a candle), and truncated to `count`. Stops early when a page comes
        back empty or fails to move the boundary, so a market with limited
        history - or a weekend gap on gold - ends the walk rather than
        looping.

        `page_pause` is deliberate: this is the only place that issues many
        requests in quick succession, and being rate-limited mid-research is
        a slow way to learn about politeness.
        """
        by_epoch: dict[int, dict[str, Any]] = {}
        end: str = "latest"
        while len(by_epoch) < count:
            resp = await self.send({
                "ticks_history": symbol,
                "count": min(1000, count),
                "end": end,
                "style": "candles",
                "granularity": granularity,
            })
            page = resp.get("candles") or []
            if not page:
                break
            before = len(by_epoch)
            for c in page:
                try:
                    by_epoch[int(c["epoch"])] = c
                except (KeyError, TypeError, ValueError):
                    continue
            if len(by_epoch) == before:
                break  # boundary stopped moving; no more history
            end = str(min(by_epoch))
            if on_page:
                on_page(len(by_epoch))
            if page_pause:
                await asyncio.sleep(page_pause)
        ordered = [by_epoch[e] for e in sorted(by_epoch)]
        return ordered[-count:]

    async def tick_history(self, symbol: str, count: int = 100000,
                           page_pause: float = 0.15) -> list[tuple[int, float]]:
        """(epoch, price) ticks, paged past the 1000-per-request cap.

        Raw ticks, not candles: the statistical question is whether one tick
        predicts the next, and a candle has already averaged that away. The
        1HZ symbols emit one tick a second, so 100,000 ticks is roughly 28
        hours of continuous data per symbol.

        Same paging contract as `candle_history` - oldest first, deduplicated
        by epoch, stops when a page is empty or the boundary stops moving.
        """
        by_epoch: dict[int, float] = {}
        end: str = "latest"
        while len(by_epoch) < count:
            resp = await self.send({
                "ticks_history": symbol,
                "count": min(1000, count),
                "end": end,
                "style": "ticks",
            })
            hist = resp.get("history") or {}
            times, prices = hist.get("times") or [], hist.get("prices") or []
            if not times or not prices:
                break
            before = len(by_epoch)
            for t, pr in zip(times, prices):
                try:
                    by_epoch[int(t)] = float(pr)
                except (TypeError, ValueError):
                    continue
            if len(by_epoch) == before:
                break
            end = str(min(by_epoch))
            if page_pause:
                await asyncio.sleep(page_pause)
        return [(e, by_epoch[e]) for e in sorted(by_epoch)][-count:]

    async def contracts_for(self, symbol: str, currency: str = "USD") -> dict[str, Any]:
        """Every contract type Deriv actually offers on `symbol`.

        Never called until now, which is why this bot has only ever traded
        six contract types: `edge.py::_requests` is a hand-written list of
        digits plus CALL/PUT. Deriv also sells Touch/No Touch, Higher/Lower,
        Turbos, Vanillas, Multipliers and Accumulators on these symbols, none
        of which have ever been quoted, let alone priced against the digits.

        Returns each type with its permitted durations and whether it needs a
        barrier - so a scan can be built from what the venue reports rather
        than from an assumption about what it sells.
        """
        # Deliberately just the symbol: the current API rejects `currency`
        # and `product_type` here with "Properties not allowed", unlike the
        # legacy docs. Verified against the live endpoint.
        return await self.send({"contracts_for": symbol})

    async def proposal(self, **params: Any) -> dict[str, Any]:
        return await self.send({"proposal": 1, **params})

    async def buy(self, proposal_id: str, price: float) -> dict[str, Any]:
        return await self.send({"buy": proposal_id, "price": price})

    async def balance(self) -> dict[str, Any]:
        return await self.send({"balance": 1})

    async def _stream_until_settled(self, contract_id: int | str) -> dict[str, Any]:
        async for msg in self.subscribe({"proposal_open_contract": 1, "contract_id": contract_id}):
            contract = msg["proposal_open_contract"]
            if contract.get("is_sold"):
                return contract
        raise DerivAPIError(f"subscription ended before contract {contract_id} settled")

    async def wait_for_settlement(self, contract_id: int | str,
                                  timeout: float = SETTLEMENT_TIMEOUT) -> dict[str, Any]:
        """Streams `proposal_open_contract` updates until the contract settles
        (`is_sold`) and returns the final contract snapshot, including `profit`.
        Deriv ends the stream itself once a contract is sold.

        The timeout is not optional in practice. This connection drops with
        `ConnectionClosedError: no close frame received or sent` — a half-open
        socket, where no close frame ever arrives, so the underlying
        `async for` simply never yields again. Without a deadline a drop
        landing between buy and settlement hangs the bot silently and
        indefinitely: observed as a bought contract with no matching "Settled"
        line and no trades for 16 minutes, with the process still alive so
        nothing restarted it. Tick contracts settle in seconds, so anything
        approaching this timeout means the socket is gone, not that the
        contract is slow.
        """
        try:
            return await asyncio.wait_for(self._stream_until_settled(contract_id), timeout)
        except asyncio.TimeoutError:
            raise DerivAPIError(
                f"contract {contract_id} did not settle within {timeout:.0f}s — "
                "connection is probably half-open"
            ) from None
