"""The Rise/Fall wiring, tested offline.

A live session cannot be a first test: a wrong duration unit or a signal that
never fires is invisible in a log until the money is gone. Every branch the
Rise/Fall path takes is exercised here against a fake API.
"""
import math

import pytest
import yaml

from pricebot.instruments import RISE_FALL, build_proposal
from pricebot.runner import Session
from pricebot.signals import Signal, build_strategy


# --- strategy registration -------------------------------------------------

def test_pdf_rise_fall_is_buildable_by_name():
    s = build_strategy("pdf_rise_fall")
    assert s.name == "pdf_rise_fall"


def test_pdf_rise_fall_accepts_its_config_kwargs():
    s = build_strategy("pdf_rise_fall", rise_threshold=80.0, fall_threshold=30.0,
                       duration_seconds=300, confirm=False)
    assert s.rise_threshold == 80.0 and s.duration_seconds == 300


def test_unknown_strategy_names_pdf_rise_fall_in_the_error():
    with pytest.raises(ValueError, match="pdf_rise_fall"):
        build_strategy("nonsense")


# --- the shipped config actually loads and builds ---------------------------

def test_shipped_config_builds_a_working_session():
    """The config file is part of the code path; a typo in it is a bug."""
    cfg = yaml.safe_load(open("config.risefall.yaml", encoding="utf-8"))
    sess = Session(api=None, config=cfg, journal=None)
    assert sess.instrument == RISE_FALL
    assert sess.strategy.name == "pdf_rise_fall"
    assert sess.symbols and sess.stake > 0


def test_shipped_config_uses_a_flat_stake():
    cfg = yaml.safe_load(open("config.risefall.yaml", encoding="utf-8"))
    pb = cfg["pricebot"]
    assert pb["stake"] == pb["max_stake"], "stake must be flat - no ladder"


def test_session_rejects_an_unknown_instrument():
    with pytest.raises(ValueError, match="unknown instrument"):
        Session(api=None, config={"pricebot": {"instrument": "roulette"}},
                journal=None)


def test_session_defaults_to_multiplier_so_existing_config_is_unchanged():
    cfg = yaml.safe_load(open("config.pricebot.yaml", encoding="utf-8"))
    assert Session(api=None, config=cfg, journal=None).instrument == "multiplier"


# --- the proposal the session will actually send ---------------------------

def _sig(direction=1, horizon=300):
    return Signal(direction=direction, expected_move_pct=0.001,
                  horizon_seconds=horizon, confidence=1.0, reason="test")


def test_rise_fall_proposal_has_no_multiplier_fields():
    """Sending multiplier fields on a CALL is how a proposal gets rejected."""
    p = build_proposal(_sig(), RISE_FALL, "R_10", 3.0)
    assert p["contract_type"] == "CALL"
    assert "multiplier" not in p and "limit_order" not in p


def test_rise_fall_direction_maps_to_call_and_put():
    assert build_proposal(_sig(1), RISE_FALL, "R_10", 3.0)["contract_type"] == "CALL"
    assert build_proposal(_sig(-1), RISE_FALL, "R_10", 3.0)["contract_type"] == "PUT"


def test_rise_fall_duration_is_minutes_not_seconds():
    """The 60x error that would turn a 5-minute trade into a 5-hour one."""
    p = build_proposal(_sig(horizon=300), RISE_FALL, "R_10", 3.0)
    assert p["duration"] == 5 and p["duration_unit"] == "m"


def test_rise_fall_duration_never_rounds_down_to_zero():
    p = build_proposal(_sig(horizon=30), RISE_FALL, "R_10", 3.0)
    assert p["duration"] >= 1


def test_rise_fall_stake_is_passed_through_exactly():
    assert build_proposal(_sig(), RISE_FALL, "R_10", 3.0)["amount"] == 3.0


# --- the session opening a trade end to end --------------------------------

class FakeAPI:
    """Enough of the API to drive one full open, and nothing more."""

    def __init__(self, payout=5.77):
        self.payout = payout
        self.bought = []
        self.proposals = []

    async def candles(self, symbol, granularity=60, count=500):
        # A noisy uptrend. The noise is not decoration: a perfectly constant
        # growth rate has zero measured volatility, and the session correctly
        # treats that as a dead feed and refuses to trade it.
        import random
        rng = random.Random(4)
        out = []
        price = 100.0
        for i in range(count):
            o = price
            price *= 1.0008 * math.exp(rng.gauss(0, 0.0004))
            hi, lo = max(o, price) * 1.0003, min(o, price) * 0.9997
            out.append({"open": o, "high": hi, "low": lo, "close": price,
                        "epoch": 1700000000 + i * granularity})
        return out

    async def proposal(self, **kw):
        self.proposals.append(kw)
        return {"proposal": {"id": "pid", "ask_price": kw["amount"],
                             "payout": self.payout, "commission": None}}

    async def buy(self, pid, price):
        self.bought.append((pid, price))
        return {"buy": {"contract_id": 12345}}

    async def contracts_for(self, symbol):
        raise AssertionError("Rise/Fall must not ask for multiplier ranges")


class FakeJournal:
    def record(self, **kw): pass
    def close(self): pass


@pytest.mark.asyncio
async def test_session_opens_a_rise_fall_trade_without_touching_multipliers():
    cfg = yaml.safe_load(open("config.risefall.yaml", encoding="utf-8"))
    cfg["pricebot"]["symbols"] = ["R_10"]
    api = FakeAPI()
    sess = Session(api=api, config=cfg, journal=FakeJournal())
    await sess._consider("R_10")
    assert sess.opened == 1, "expected one trade to open"
    sent = api.proposals[0]
    assert sent["contract_type"] in ("CALL", "PUT")
    assert sent["duration_unit"] == "m"
    assert "multiplier" not in sent
    assert sent["amount"] == 3.0


@pytest.mark.asyncio
async def test_session_will_not_stack_two_positions_on_one_symbol():
    cfg = yaml.safe_load(open("config.risefall.yaml", encoding="utf-8"))
    cfg["pricebot"]["symbols"] = ["R_10"]
    api = FakeAPI()
    sess = Session(api=api, config=cfg, journal=FakeJournal())
    await sess._consider("R_10")
    await sess._consider("R_10")
    assert sess.opened == 1


@pytest.mark.asyncio
async def test_session_survives_a_symbol_whose_candles_fail():
    class Broken(FakeAPI):
        async def candles(self, *a, **k):
            raise RuntimeError("feed down")
    cfg = yaml.safe_load(open("config.risefall.yaml", encoding="utf-8"))
    cfg["pricebot"]["symbols"] = ["R_10"]
    sess = Session(api=Broken(), config=cfg, journal=FakeJournal())
    await sess._consider("R_10")          # must not raise
    assert sess.opened == 0


# --- the arithmetic that decides whether this can pay ----------------------

def test_break_even_win_rate_is_the_inverse_of_the_payout_multiple():
    from pricebot.rise_fall_backtest import break_even_win_rate
    assert break_even_win_rate(1.9231) == pytest.approx(0.52, abs=0.001)
    assert break_even_win_rate(1.78) == pytest.approx(0.5618, abs=0.001)
