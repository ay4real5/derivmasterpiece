import pytest

from deriv_bot.selection import best_per_symbol, pick, score, summarise


def q(symbol, kind, barrier, edge, payout=9.77, ask=5.0):
    return {"symbol": symbol, "contract_type": kind, "barrier": barrier,
            "edge_pct": edge, "payout": payout, "ask_price": ask, "win_prob": 0.5}


ROWS = [
    q("R_10", "DIGITOVER", "4", 2.25), q("R_10", "CALL", None, 3.75),
    q("R_25", "DIGITEVEN", None, 2.30), q("R_25", "PUT", None, 3.80),
    q("R_50", "DIGITUNDER", "4", 1.36), q("R_50", "DIGITODD", None, 2.35),
]


def test_stage_one_picks_the_cheapest_leg_per_symbol():
    best = best_per_symbol(ROWS)
    assert set(best) == {"R_10", "R_25", "R_50"}
    assert best["R_10"]["contract_type"] == "DIGITOVER"
    assert best["R_25"]["contract_type"] == "DIGITEVEN"
    assert best["R_50"]["contract_type"] == "DIGITUNDER"


def test_stage_two_picks_the_best_of_the_per_symbol_winners():
    winner, stage1 = pick(ROWS)
    assert winner["symbol"] == "R_50"
    assert winner["edge_pct"] == 1.36
    assert len(stage1) == 3


def test_two_stage_equals_a_single_global_argmax():
    """The property the design rests on: best-per-symbol then best-of-those
    is the same answer as one global minimum over every quote."""
    winner, _ = pick(ROWS)
    assert winner is min(ROWS, key=score)


def test_it_never_pays_more_than_the_cheapest_available():
    # the rotation's actual failure: 44.4% of real trades went out at
    # 3.75-3.80% while 2.25% was quoted in the same cycle
    winner, _ = pick(ROWS)
    assert winner["edge_pct"] == min(r["edge_pct"] for r in ROWS)


def test_veto_excludes_a_leg_without_choosing_one():
    winner, _ = pick(ROWS, veto={("DIGITUNDER", "4")})
    assert winner["contract_type"] != "DIGITUNDER"
    assert winner["edge_pct"] == 2.25          # next cheapest


def test_a_veto_that_empties_the_field_is_ignored():
    # not trading is a decision the caller makes explicitly, never a
    # side-effect of an over-aggressive filter
    veto = {(r["contract_type"], r["barrier"]) for r in ROWS}
    winner, _ = pick(ROWS, veto=veto)
    assert winner is not None


def test_no_quotes_returns_nothing():
    winner, stage1 = pick([])
    assert winner is None
    assert stage1 == {}


def test_summarise_lists_every_symbol_and_marks_the_winner():
    winner, stage1 = pick(ROWS)
    text = summarise(stage1, winner)
    assert "R_10" in text and "R_25" in text and "R_50" in text
    assert "<-" in text


def test_summarise_with_nothing_quoted():
    assert summarise({}, None) == "selection: nothing quoted"
