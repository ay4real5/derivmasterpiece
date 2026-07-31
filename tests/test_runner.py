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
import asyncio

import pytest

from deriv_bot.journal import TradeJournal
from pricebot.runner import STRAGGLER_GRACE_SECONDS, Session


def candles(n=30, price=100.0):
    return [{"open": price, "high": price * 1.001, "low": price * 0.999,
             "close": price * (1 + 0.0001 * i)} for i in range(n)]


class FakeAPI:
    """Just enough of DerivAPI for `_consider` and `_watch` to run end to end."""

    def __init__(self, settle_delay: float = 0.0):
        self.proposal_calls: list[dict] = []
        self.bought: dict | None = None
        self.contracts_for_calls = 0
        self.settle_delay = settle_delay

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

    async def wait_for_settlement(self, contract_id, timeout=86400):
        await asyncio.sleep(self.settle_delay)
        return {"profit": 0.2, "contract_type": "NOTOUCH",
                "buy_price": 3.0, "payout": 3.2, "status": "won"}


def make_session(tmp_path, api=None, **pricebot_overrides):
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
    api = api if api is not None else FakeAPI()
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
    # barrier_pct (0.30, a real fraction) scaled by the fake candles' last
    # close - see build_proposal's spot-scaling fix. Computed from the same
    # formula `candles()` above uses, not hardcoded, so this doesn't silently
    # drift out of sync with that helper.
    spot = candles()[-1]["close"]
    assert api.proposal_calls[0]["barrier"] == f"+{0.30 * spot:.4f}"
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
async def test_touch_session_start_reports_its_real_expiry(tmp_path, capsys):
    """Before this, TOUCH fell into the MULTIPLIER-shaped 'hold~Xm' log
    line, which reports hold_seconds - a setting TOUCH doesn't even use.
    tools/check_deploy.py parses this exact line for its `expiry` check and
    read every TOUCH deployment as 'strategy-derived' regardless of the
    config, because it never said "expiry ..." at all."""
    session, _api, journal = make_session(tmp_path)
    await session.run(0.01)
    journal.close()
    out = capsys.readouterr().out
    assert "expiry 5m" in out


@pytest.mark.asyncio
async def test_touch_skips_a_symbol_with_an_open_position(tmp_path):
    session, api, journal = make_session(tmp_path)
    session.open["R_50"] = 999
    await session._consider("R_50")
    journal.close()
    assert api.proposal_calls == []


# --- run() waits for stragglers before exiting -----------------------------
#
# The bug this guards: `_watch` runs as a background task, and `run()` used
# to return the moment its polling loop's deadline passed, with no regard
# for a position opened near the end of the window. The contract still
# settled fine on Deriv's side, but `main()` closes the API connection right
# after `run()` returns, cutting the watcher's subscription off mid-flight -
# so the trade never reached the journal, and the daily-loss cap that reads
# it never found out. Measured live: 4 of 5 real trades were lost this way.

@pytest.mark.asyncio
async def test_run_waits_for_a_position_opened_near_the_end_of_the_window(tmp_path, monkeypatch):
    import pricebot.runner as runner_module
    monkeypatch.setattr(runner_module, "STRAGGLER_GRACE_SECONDS", 2.0)

    api = FakeAPI(settle_delay=0.3)
    session, _api, journal = make_session(tmp_path, api=api, poll_seconds=0.05)
    # A session window barely longer than one poll cycle - the position
    # opens with almost no time left before `run()`'s deadline.
    await session.run(0.1)
    journal.close()

    assert session.settled == 1
    rows = list(open(str(journal.path)).read().splitlines())
    assert len(rows) == 2  # header + the settled trade


@pytest.mark.asyncio
async def test_run_gives_up_after_the_grace_period_not_forever(tmp_path, monkeypatch):
    import pricebot.runner as runner_module
    monkeypatch.setattr(runner_module, "STRAGGLER_GRACE_SECONDS", 0.1)

    api = FakeAPI(settle_delay=10.0)  # far longer than the grace period
    session, _api, journal = make_session(tmp_path, api=api, poll_seconds=0.05)
    await session.run(0.1)
    journal.close()

    # run() must return promptly rather than hang on a slow settlement -
    # profit_table reconciliation (see pricebot/reconcile.py) is the backstop.
    assert session.settled == 0
