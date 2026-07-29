"""Paging must not silently corrupt the series a backtest runs on."""
import asyncio

import pytest

from deriv_bot.api import DerivAPI


class _PagingAPI(DerivAPI):
    """Serves 1000-candle pages from a synthetic history, like Deriv does."""

    def __init__(self, total, granularity=60, start_epoch=1_000_000):
        self.history = [
            {"epoch": start_epoch + i * granularity, "open": 100 + i,
             "high": 100 + i, "low": 100 + i, "close": 100 + i}
            for i in range(total)
        ]
        self.calls = 0

    async def send(self, request, timeout=15.0):
        self.calls += 1
        end = request["end"]
        count = request["count"]
        pool = self.history if end == "latest" else [
            c for c in self.history if c["epoch"] < int(end)]
        return {"candles": pool[-count:]}


class _EmptyAPI(DerivAPI):
    def __init__(self):
        pass

    async def send(self, request, timeout=15.0):
        return {"candles": []}


class _StuckAPI(DerivAPI):
    """Keeps returning the same page - a boundary that never moves."""

    def __init__(self):
        self.calls = 0

    async def send(self, request, timeout=15.0):
        self.calls += 1
        return {"candles": [{"epoch": 500, "open": 1, "high": 1, "low": 1, "close": 1}]}


def test_paging_assembles_more_than_one_page():
    api = _PagingAPI(total=5000)
    got = asyncio.run(api.candle_history("X", 60, count=3000, page_pause=0))
    assert len(got) == 3000
    assert api.calls >= 3


def test_result_is_chronological_and_deduplicated():
    api = _PagingAPI(total=5000)
    got = asyncio.run(api.candle_history("X", 60, count=2500, page_pause=0))
    epochs = [c["epoch"] for c in got]
    assert epochs == sorted(epochs)
    assert len(epochs) == len(set(epochs))


def test_it_returns_the_most_recent_candles():
    api = _PagingAPI(total=5000)
    got = asyncio.run(api.candle_history("X", 60, count=1500, page_pause=0))
    assert got[-1]["epoch"] == api.history[-1]["epoch"]


def test_limited_history_stops_rather_than_looping():
    api = _PagingAPI(total=1200)
    got = asyncio.run(api.candle_history("X", 60, count=10000, page_pause=0))
    assert len(got) == 1200


def test_an_empty_response_ends_the_walk():
    got = asyncio.run(_EmptyAPI().candle_history("X", 60, count=5000, page_pause=0))
    assert got == []


def test_a_boundary_that_never_moves_ends_the_walk():
    """Otherwise a misbehaving endpoint spins forever issuing requests."""
    api = _StuckAPI()
    got = asyncio.run(api.candle_history("X", 60, count=10000, page_pause=0))
    assert len(got) == 1
    assert api.calls <= 3
