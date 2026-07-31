"""Tests for pricebot/runner.py's instrument branching.

Before this file existed, the TOUCH path in `Session._consider` had never
been exercised: no config had ever set `instrument: touch`. It carried two
bugs that a fake-API test now locks in the fix for:

1. It called `_allowed_multipliers` (MULTUP's leverage range) for every
   non-RISE_FALL instrument, including TOUCH, and skipped the trade if that
   came back empty - a multiplier concept with nothing to do with Touch.
2. It overwrote the strategy's own `expected_move_pct` with a
   volatility/hold-time-derived target for every non-RISE_FALL instrument,
   which would have silently discarded a `FixedNoTouch` strategy's fixed
   barrier before `build_proposal` ever saw it.
3. The OPEN log line assumed MULTIPLIER-shaped proposal params
   (`params['multiplier']`) for anything that wasn't RISE_FALL, which would
   raise a KeyError logging a TOUCH trade.
"""
import pytest

from deriv_bot.journal import TradeJournal
from pricebot.runner import Session


def candles(n=30, price=100.0):
    return [{"open": price, "high": price * 1.001, "low": price * 0.999,
             "close": price * (1 + 0.0001 * i)} for i in range(n)]


class FakeAPI:
    """Just enough of DerivAPI for `_consider` to run end to end."""

    def __init__(self):
        self.proposal_calls: list[dict] = []
        self.bought: dict | None = None
        self.contracts_for_calls = 0

    async def candles(self, symbol, granularity=60, count=500):
        return candles()

    async def contracts_for(self, symbol):
        self.contracts_for_calls += 1
        # No MULTUP entry at all - if the TOUCH path ever asks for this,
        # `_allowed_multipliers` returns an empty tuple and the old code
        # skipped the trade.
        return {"contracts_for": {"available": []}}

    async def proposal(self, **params):
        self.proposal_calls.append(params)
        return {"proposal": {"id": "prop1", "ask_price": params["amount"],
                             "payout": params["amount"] * 3.2, "commission": 0.0}}

    async def buy(self, proposal_id, price):
        self.bought = {"id": proposal_id, "price": price}
        return {"buy": {"contract_id": 42}}


def make_session(tmp_path, **pricebot_overrides):
    config = {
        "pricebot": {
            "instrument": "touch",
            "symbols": ["R_50"],
            "stake": 3.0,
            "poll_seconds": 20,
            "duration": 5,
            "duration_unit": "m",
            "staking": {"name": "flat"},
            "strategy": {"name": "fixed_notouch", "barrier_pct": 0.30, "horizon_seconds": 300},
            **pricebot_overrides,
        }
    }
    journal = TradeJournal(str(tmp_path / "journal.csv"))
    api = FakeAPI()
    return Session(api, config, journal), api, journal


@pytest.mark.asyncio
async def test_touch_does_not_consult_the_multiplier_range(tmp_path):
    session, api, journal = make_session(tmp_path)
    await session._consider("R_50")
    journal.close()
    assert api.contracts_for_calls == 0


@pytest.mark.asyncio
async def test_touch_keeps_the_strategys_own_barrier(tmp_path):
    session, api, journal = make_session(tmp_path)
    await session._consider("R_50")
    journal.close()
    assert len(api.proposal_calls) == 1
    assert api.proposal_calls[0]["barrier"] == "+0.3000"
    assert api.proposal_calls[0]["contract_type"] == "NOTOUCH"
    assert api.proposal_calls[0]["duration"] == 5
    assert api.proposal_calls[0]["duration_unit"] == "m"


@pytest.mark.asyncio
async def test_touch_actually_opens_a_position(tmp_path):
    session, api, journal = make_session(tmp_path)
    await session._consider("R_50")
    journal.close()
    assert api.bought is not None
    assert session.open["R_50"] == 42
    assert session.opened == 1


@pytest.mark.asyncio
async def test_touch_skips_a_symbol_with_an_open_position(tmp_path):
    session, api, journal = make_session(tmp_path)
    session.open["R_50"] = 999
    await session._consider("R_50")
    journal.close()
    assert api.proposal_calls == []
