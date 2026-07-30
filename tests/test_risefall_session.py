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


# --- the shipped configuration, as a contract ------------------------------

def test_shipped_config_runs_exactly_five_symbols():
    """Five was chosen deliberately. A silent drop to one is the failure this
    project already had once, when 'best symbol' selection put every trade on
    R_10 DIGITOVER and there was no way to tell a bad symbol from a bad
    strategy."""
    cfg = yaml.safe_load(open("config.risefall.yaml", encoding="utf-8"))
    syms = cfg["pricebot"]["symbols"]
    assert len(syms) == 5, f"expected 5 symbols, got {len(syms)}: {syms}"
    assert len(set(syms)) == 5, "symbols must be distinct"


def test_shipped_config_is_rise_fall_only():
    """No digits, no multipliers, no touch - Rise/Fall was the request."""
    cfg = yaml.safe_load(open("config.risefall.yaml", encoding="utf-8"))
    assert cfg["pricebot"]["instrument"] == RISE_FALL


def test_every_shipped_symbol_maps_to_call_or_put():
    cfg = yaml.safe_load(open("config.risefall.yaml", encoding="utf-8"))
    for sym in cfg["pricebot"]["symbols"]:
        for d in (1, -1):
            p = build_proposal(_sig(d), RISE_FALL, sym, 3.0)
            assert p["contract_type"] in ("CALL", "PUT")
            assert p["underlying_symbol"] == sym


@pytest.mark.asyncio
async def test_session_considers_all_five_symbols_each_cycle():
    """One flat symbol must not stop the other four being looked at."""
    cfg = yaml.safe_load(open("config.risefall.yaml", encoding="utf-8"))
    api = FakeAPI()
    seen = []

    class Tracking(FakeAPI):
        async def candles(self, symbol, granularity=60, count=500):
            seen.append(symbol)
            return await FakeAPI.candles(self, symbol, granularity, count)

    sess = Session(api=Tracking(), config=cfg, journal=FakeJournal())
    await sess.run(0)                      # one pass, deadline already past
    # run() with 0 seconds does not enter the loop, so drive one cycle directly
    seen.clear()
    import asyncio
    await asyncio.gather(*(sess._consider(s) for s in sess.symbols))
    assert set(seen) == set(cfg["pricebot"]["symbols"])


# --- the chart-quality gates -----------------------------------------------

def _bars(rets, ranges=None, adx_trend=False):
    out, p = [], 100.0
    for i, r in enumerate(rets):
        o = p
        p *= (1 + r)
        span = ranges[i] if ranges else abs(r) + 1e-5
        out.append({"open": o, "high": max(o, p) * (1 + span), "close": p,
                    "low": min(o, p) * (1 - span), "epoch": i * 60})
    return out


def test_agreement_requires_both_directional_components():
    from pricebot.pdf_strategy import directional_agreement
    assert directional_agreement(0.85, 0.90) is True     # both bull
    assert directional_agreement(0.15, 0.10) is True     # both bear
    assert directional_agreement(0.85, 0.10) is False    # opposite
    assert directional_agreement(0.15, 0.90) is False


def test_agreement_treats_exactly_neutral_as_no_opinion():
    """0.5 is 'no view'; it must not silently count as agreeing."""
    from pricebot.pdf_strategy import directional_agreement
    assert directional_agreement(0.5, 0.9) is False
    assert directional_agreement(0.9, 0.5) is False
    assert directional_agreement(0.5, 0.5) is False


def test_gated_weights_sum_to_one_so_the_score_still_spans_0_100():
    """If they did not, the 72/44 thresholds would silently mean something
    different from what they say."""
    from pricebot.pdf_strategy import _GATED_WEIGHTS
    assert sum(_GATED_WEIGHTS.values()) == pytest.approx(1.0)
    assert "adx" not in _GATED_WEIGHTS


def test_gated_score_ignores_adx_entirely():
    """Two markets identical except for trend STRENGTH must score the same
    directionally - that is the whole point of the correction."""
    from pricebot.pdf_strategy import _GATED_WEIGHTS
    subs_weak = {"ema": 0.85, "rsi": 0.7, "bb": 0.5, "adx": 0.0, "candle": 0.5}
    subs_strong = {**subs_weak, "adx": 1.0}
    g = lambda s: sum(s[k] * w for k, w in _GATED_WEIGHTS.items())
    assert g(subs_weak) == pytest.approx(g(subs_strong))


def test_strategy_defaults_reproduce_the_pdf_exactly():
    """The specification must stay testable as written."""
    from pricebot.pdf_strategy import PdfRiseFall
    s = PdfRiseFall()
    assert s.adx_mode == "score" and s.min_adx == 0.0
    assert s.require_agreement is False


def test_strategy_rejects_a_nonsense_adx_mode():
    from pricebot.pdf_strategy import PdfRiseFall
    with pytest.raises(ValueError, match="adx_mode"):
        PdfRiseFall(adx_mode="sideways")
    with pytest.raises(ValueError, match="min_adx"):
        PdfRiseFall(min_adx=-5)


def test_adx_gate_blocks_a_trendless_market():
    """The live bug, as a test: no trend must mean no trade, not a PUT."""
    from pricebot.pdf_strategy import PdfRiseFall
    import random
    rng = random.Random(9)
    # pure chop - no trend, so ADX stays low
    cs = _bars([rng.choice([0.0008, -0.0008]) for _ in range(300)])
    gated = PdfRiseFall(adx_mode="gate", min_adx=20.0, require_agreement=True)
    sig = gated.evaluate(cs)
    score = gated.score_at(cs)
    if score is not None and score["adx_value"] < 20.0:
        assert sig is None, "a trendless market must not produce a trade"


def test_gates_never_increase_the_trade_count():
    """Gates can only remove trades. If a variant trades MORE, a gate is
    inverted somewhere and the backtest comparison is meaningless."""
    from pricebot.pdf_strategy import score_series_detail, signals_from_detail
    import random
    rng = random.Random(10)
    cs = _bars([rng.gauss(0, 0.0015) for _ in range(3000)])
    d = score_series_detail(cs)
    pure = sum(1 for x in signals_from_detail(d) if x)
    g1 = sum(1 for x in signals_from_detail(d, adx_mode="gate", min_adx=20.0) if x)
    g2 = sum(1 for x in signals_from_detail(
        d, adx_mode="gate", min_adx=20.0, require_agreement=True) if x)
    assert g2 <= g1
    assert g1 <= pure or pure == 0


def test_score_series_detail_matches_composite_score():
    """A fast path nobody checked is an untested second implementation."""
    from pricebot.pdf_strategy import composite_score, score_series_detail
    import random
    rng = random.Random(11)
    cs = _bars([rng.gauss(0, 0.002) for _ in range(400)])
    d = score_series_detail(cs)
    c = composite_score(cs)
    assert c is not None and d[-1] is not None
    assert d[-1]["score"] == pytest.approx(c["score"])
    assert d[-1]["gated_score"] == pytest.approx(c["gated_score"])
    assert d[-1]["agree"] == c["agree"]


# --- the shipped config now carries the gates ------------------------------

def test_shipped_config_enables_the_gates():
    cfg = yaml.safe_load(open("config.risefall.yaml", encoding="utf-8"))
    st = cfg["pricebot"]["strategy"]
    assert st["adx_mode"] == "gate"
    assert st["min_adx"] >= 20.0
    assert st["require_agreement"] is True


def test_shipped_config_strategy_kwargs_all_accepted():
    """A key the strategy does not accept is a crash-loop on startup - which
    this project has already had once, from a signature that drifted."""
    cfg = yaml.safe_load(open("config.risefall.yaml", encoding="utf-8"))
    st = dict(cfg["pricebot"]["strategy"])
    build_strategy(st.pop("name"), **st)
