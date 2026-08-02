"""The manual-lines bot: only trade at levels the user drew, log every skip."""
import json

import pytest

from pricebot.sr_lines import (
    DEFAULT_PAYOUT, Limits, Line, break_even, decide, load_lines, mark_broken,
    merge_state, validate,
)


def candles(prices, step=60):
    out, prev = [], prices[0]
    for i, p in enumerate(prices):
        out.append({"epoch": i * step, "open": prev, "close": p,
                    "high": max(prev, p), "low": min(prev, p)})
        prev = p
    return out


UP3 = candles([10, 11, 12, 13])       # three bullish closes
DOWN3 = candles([13, 12, 11, 10])


def line(**kw):
    base = dict(name="SR1", symbol="R_50", price_level=100.0, type="support",
                tolerance_pct=0.15)
    base.update(kw)
    return Line(**base)


# --- the bar ---------------------------------------------------------------

def test_break_even_matches_the_live_quote():
    """Deriv quotes 1.92x on a 55s R_50 Rise/Fall."""
    assert break_even(DEFAULT_PAYOUT) == pytest.approx(0.5208, abs=0.0005)


def test_the_proposals_75_percent_assumption_is_not_this_market():
    """It implies a 57.1% bar; the real one is 52.08%."""
    assert break_even(1.75) == pytest.approx(0.5714, abs=0.001)
    assert break_even(DEFAULT_PAYOUT) < 0.53


# --- validation ------------------------------------------------------------

def test_a_missing_field_names_itself():
    with pytest.raises(ValueError, match="price_level"):
        validate({"name": "A", "symbol": "R_50", "type": "support"}, 0)


def test_a_bad_type_is_rejected():
    with pytest.raises(ValueError, match="type must be"):
        validate({"name": "A", "symbol": "R_50", "price_level": 1,
                  "type": "sideways"}, 0)


def test_a_non_numeric_level_is_rejected_not_coerced():
    with pytest.raises(ValueError, match="must be a number"):
        validate({"name": "A", "symbol": "R_50", "price_level": "abc",
                  "type": "support"}, 0)


def test_duplicate_names_are_rejected(tmp_path):
    """Names key the cooldown and daily count; duplicates would share one."""
    p = tmp_path / "lines.json"
    p.write_text(json.dumps([
        {"name": "A", "symbol": "R_50", "price_level": 1, "type": "support"},
        {"name": "A", "symbol": "R_50", "price_level": 2, "type": "resistance"},
    ]), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load_lines(str(p))


def test_a_valid_file_loads(tmp_path):
    p = tmp_path / "lines.json"
    p.write_text(json.dumps([
        {"name": "S1", "symbol": "R_50", "price_level": 91.5,
         "type": "support", "tolerance_pct": 0.15},
    ]), encoding="utf-8")
    got = load_lines(str(p))
    assert len(got) == 1 and got[0].wants_up is True


# --- the zone --------------------------------------------------------------

def test_tolerance_is_a_percentage_of_the_level():
    ln = line(price_level=100.0, tolerance_pct=0.15)
    assert ln.contains(100.0) and ln.contains(100.15) and ln.contains(99.85)
    assert not ln.contains(100.16)


def test_support_buys_rise_and_resistance_buys_fall():
    assert line(type="support").wants_up is True
    assert line(type="resistance").wants_up is False


# --- the decision, and its reasons ----------------------------------------

def test_it_trades_when_everything_lines_up():
    d = decide(100.0, [line()], UP3, now_epoch=1000, limits=Limits())
    assert d.tradeable and d.direction == 1


def test_away_from_every_line_it_says_how_far():
    d = decide(105.0, [line()], UP3, now_epoch=1000, limits=Limits())
    assert not d.tradeable
    assert "not at any line" in d.reason and "away" in d.reason


def test_at_the_line_but_unconfirmed_says_so():
    d = decide(100.0, [line()], DOWN3, now_epoch=1000, limits=Limits())
    assert not d.tradeable
    assert "do not confirm" in d.reason


def test_cooldown_is_reported_with_time_remaining():
    ln = line()
    ln.last_trade_epoch = 1000
    d = decide(100.0, [ln], UP3, now_epoch=1200, limits=Limits(cooldown_seconds=1800))
    assert not d.tradeable and "cooldown" in d.reason and "1600s" in d.reason


def test_the_per_line_daily_cap_is_reported():
    ln = line()
    ln.trades_today = 3
    d = decide(100.0, [ln], UP3, now_epoch=9999, limits=Limits())
    assert not d.tradeable and "used its 3 trades" in d.reason


def test_the_daily_loss_limit_stops_everything():
    d = decide(100.0, [line()], UP3, now_epoch=1000, limits=Limits(max_daily_loss=20),
               day_pnl=-20.0)
    assert not d.tradeable and "daily loss limit" in d.reason


def test_only_one_trade_at_a_time():
    d = decide(100.0, [line()], UP3, now_epoch=1000, limits=Limits(), open_trades=1)
    assert not d.tradeable and "already open" in d.reason


def test_a_poor_payout_is_refused():
    d = decide(100.0, [line()], UP3, now_epoch=1000,
               limits=Limits(min_payout=1.95), payout=1.92)
    assert not d.tradeable and "below the" in d.reason


def test_an_inactive_or_broken_line_is_ignored():
    assert not decide(100.0, [line(active=False)], UP3, 1000, Limits()).tradeable
    ln = line(); ln.broken = True
    assert not decide(100.0, [ln], UP3, 1000, Limits()).tradeable


def test_every_outcome_carries_a_reason():
    """A silent skip is indistinguishable from a broken bot."""
    for price, cs, lim in ((105.0, UP3, Limits()), (100.0, DOWN3, Limits()),
                           (100.0, UP3, Limits(max_daily_loss=0.01))):
        d = decide(price, [line()], cs, 1000, lim, day_pnl=-1.0)
        assert d.reason and len(d.reason) > 10


# --- broken lines ----------------------------------------------------------

def test_price_closing_through_support_kills_it():
    ln = line(price_level=100.0, tolerance_pct=0.15)
    assert mark_broken([ln], 99.0) == [ln]
    assert ln.broken is True


def test_a_line_is_never_both_tradeable_and_broken_at_one_price():
    ln = line(price_level=100.0, tolerance_pct=0.15)
    edge = 100.0 - ln.tolerance_abs()
    mark_broken([ln], edge)
    assert not ln.broken, "inside tolerance must not count as broken"
    assert ln.contains(edge)


# --- reload ----------------------------------------------------------------

def test_editing_the_file_does_not_reset_cooldowns():
    """Without this, every edit hands a spent line a fresh daily allowance."""
    old = line(); old.trades_today = 2; old.last_trade_epoch = 500
    merged = merge_state([old], [line()])
    assert merged[0].trades_today == 2
    assert merged[0].last_trade_epoch == 500


def test_moving_a_level_resets_its_state():
    """A different price is a different level, whatever it is called."""
    old = line(price_level=100.0); old.trades_today = 3
    merged = merge_state([old], [line(price_level=101.0)])
    assert merged[0].trades_today == 0
