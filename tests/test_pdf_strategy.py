import pytest

from pricebot.pdf_strategy import (
    PdfRiseFall,
    adx_subscore,
    bb_subscore,
    composite_score,
    ema_subscore,
    rsi_subscore,
    score_series,
    signals_from_series,
)


def walk(n, seed=11, start=100.0, step=0.002):
    import random
    r = random.Random(seed)
    out, p = [], start
    for _ in range(n):
        nxt = p * (1 + r.gauss(0, step))
        out.append({"open": p, "high": max(p, nxt) * 1.001,
                    "low": min(p, nxt) * 0.999, "close": nxt})
        p = nxt
    return out


# --- sub-scores follow section 3.2 verbatim ----------------------------

def test_rsi_subscore_is_the_specs_formula():
    assert rsi_subscore(30) == pytest.approx(0.0)
    assert rsi_subscore(50) == pytest.approx(0.5)
    assert rsi_subscore(70) == pytest.approx(1.0)
    assert rsi_subscore(90) == pytest.approx(1.0)   # clamped
    assert rsi_subscore(10) == pytest.approx(0.0)   # clamped


def test_bb_subscore_is_zero_below_and_one_above():
    assert bb_subscore(5, 10, 20) == 0.0
    assert bb_subscore(15, 10, 20) == 0.5
    assert bb_subscore(25, 10, 20) == 1.0


def test_adx_subscore_thresholds():
    assert adx_subscore(15) == 0.0
    assert adx_subscore(25) == 0.5
    assert adx_subscore(40) == 1.0
    assert adx_subscore(None) == 0.0


def test_ema_subscore_is_neutral_in_the_indecision_zone():
    # lines within 0.01% of each other - the spec's convergence rule
    assert ema_subscore(100.0, 100.005, 99, 99) == 0.5


def test_ema_subscore_directions():
    assert ema_subscore(110, 100, 108, 100) > 0.5
    assert ema_subscore(90, 100, 92, 100) < 0.5


# --- the fast path must agree with the slow one -------------------------

def test_score_series_matches_composite_score_candle_by_candle():
    """Without this, the fast path is just an untested second
    implementation - and it is the one the backtest uses."""
    candles = walk(400)
    fast = score_series(candles)
    for i in range(120, len(candles), 17):
        slow = composite_score(candles[: i + 1])
        if slow is None:
            assert fast[i] is None
        else:
            assert fast[i] == pytest.approx(slow["score"], abs=1e-9)


def test_score_series_is_none_before_warmup():
    assert all(s is None for s in score_series(walk(20))[:20])


# --- thresholds and confirmation ---------------------------------------

def test_confirmation_requires_the_previous_candle_too():
    scores = [None, 50.0, 75.0]          # spike with no prior support
    assert signals_from_series(scores)[2] == 0
    scores = [None, 70.0, 75.0]          # prior >= rise_confirm 68
    assert signals_from_series(scores)[2] == 1


def test_confirmation_can_be_switched_off():
    scores = [None, 50.0, 75.0]
    assert signals_from_series(scores, confirm=False)[2] == 1


def test_the_neutral_zone_produces_no_trade():
    for s in (45.0, 55.0, 71.0):
        assert signals_from_series([s, s], confirm=False)[1] == 0


def test_fall_zone_fires_short():
    assert signals_from_series([40.0, 40.0], confirm=False)[1] == -1


def test_strategy_rejects_inverted_thresholds():
    with pytest.raises(ValueError):
        PdfRiseFall(rise_threshold=40, fall_threshold=60)


def test_strategy_returns_none_in_the_neutral_zone():
    s = PdfRiseFall()
    assert s.evaluate(walk(30)) is None      # not warmed up
