import asyncio

import pytest

from deriv_bot.multi_scan import (
    CATEGORY_LEGS, DEFAULT_CANDIDATES, DEFAULT_SYMBOLS, RoundRobin, parse_candidate_specs, scan_best,
)


class _FakeAPI:
    """Returns a canned proposal per (symbol, contract_type, barrier), or
    raises to simulate an unavailable combination."""

    def __init__(self, quotes: dict[tuple[str, str, str | None], tuple[float, float]]):
        self.quotes = quotes
        self.calls: list[dict] = []

    async def proposal(self, **params):
        self.calls.append(params)
        key = (params["underlying_symbol"], params["contract_type"], params.get("barrier"))
        if key not in self.quotes:
            raise RuntimeError("not offered")
        payout, ask = self.quotes[key]
        return {"proposal": {"payout": payout, "ask_price": ask, "id": "x"}}


def test_scan_best_sorts_by_edge_ascending():
    quotes = {
        ("R_100", "DIGITOVER", "0"): (10.87, 10.00),  # 2.17% edge
        ("R_10", "DIGITOVER", "0"): (10.96, 10.00),    # 1.36% edge, cheaper
    }
    api = _FakeAPI(quotes)
    candidates = [("DIGITOVER", "0")]
    results = asyncio.run(scan_best(api, ["R_100", "R_10"], candidates, 10.0, "USD"))
    assert len(results) == 2
    assert results[0]["symbol"] == "R_10"  # cheapest first
    assert results[0]["edge_pct"] < results[1]["edge_pct"]


def test_scan_best_skips_unavailable_combinations():
    quotes = {("R_100", "DIGITEVEN", None): (19.23, 10.00)}
    api = _FakeAPI(quotes)
    candidates = [("DIGITEVEN", None), ("CALL", None)]  # CALL not in quotes -> skipped
    results = asyncio.run(scan_best(api, ["R_100"], candidates, 10.0, "USD"))
    assert len(results) == 1
    assert results[0]["contract_type"] == "DIGITEVEN"


def test_scan_best_computes_correct_win_prob_and_edge():
    quotes = {("R_10", "DIGITMATCH", "5"): (83.33, 10.00)}
    api = _FakeAPI(quotes)
    results = asyncio.run(scan_best(api, ["R_10"], [("DIGITMATCH", "5")], 10.0, "USD"))
    r = results[0]
    assert r["win_prob"] == 0.1
    assert r["edge_pct"] == pytest.approx(16.67, abs=0.1)


def test_scan_best_queries_every_symbol_x_candidate_combo():
    quotes = {
        ("R_10", "DIGITOVER", "0"): (10.96, 10.0),
        ("R_10", "DIGITEVEN", None): (19.23, 10.0),
        ("R_25", "DIGITOVER", "0"): (10.96, 10.0),
        ("R_25", "DIGITEVEN", None): (19.23, 10.0),
    }
    api = _FakeAPI(quotes)
    candidates = [("DIGITOVER", "0"), ("DIGITEVEN", None)]
    asyncio.run(scan_best(api, ["R_10", "R_25"], candidates, 10.0, "USD"))
    assert len(api.calls) == 4  # 2 symbols x 2 candidates


def test_scan_best_empty_when_nothing_available():
    api = _FakeAPI({})
    results = asyncio.run(scan_best(api, ["R_10"], DEFAULT_CANDIDATES, 10.0, "USD"))
    assert results == []


def test_parse_candidate_specs():
    parsed = parse_candidate_specs(["DIGITOVER:0", "DIGITEVEN", "CALL"])
    assert parsed == [("DIGITOVER", "0"), ("DIGITEVEN", None), ("CALL", None)]


def test_parse_candidate_specs_rejects_bad_input():
    with pytest.raises(ValueError):
        parse_candidate_specs(["NOTREAL"])
    with pytest.raises(ValueError):
        parse_candidate_specs(["DIGITOVER"])  # needs a barrier
    with pytest.raises(ValueError):
        parse_candidate_specs(["DIGITOVER:99"])


def test_default_candidates_cover_the_three_requested_categories():
    kinds = {k for k, _ in DEFAULT_CANDIDATES}
    assert kinds == {"DIGITOVER", "DIGITUNDER", "DIGITEVEN", "DIGITODD", "CALL", "PUT"}
    # explicitly barrier 4, not the cheapest-possible barrier
    assert ("DIGITOVER", "4") in DEFAULT_CANDIDATES
    assert ("DIGITUNDER", "4") in DEFAULT_CANDIDATES


def test_default_symbols_cover_both_volatility_families():
    assert set(DEFAULT_SYMBOLS) == {
        "R_10", "R_25", "R_50", "R_75", "R_100",
        "1HZ10V", "1HZ25V", "1HZ50V", "1HZ75V", "1HZ100V",
    }


def test_round_robin_equal_weights_cycles_every_item_in_order():
    rr = RoundRobin(["a", "b", "c"])
    picks = [rr.next() for _ in range(9)]
    assert picks == ["a", "b", "c"] * 3


def test_round_robin_never_repeats_within_one_full_cycle():
    rr = RoundRobin(list(range(10)))
    picks = [rr.next() for _ in range(10)]
    assert sorted(picks) == list(range(10))  # every item exactly once


def test_round_robin_proportional_weights():
    rr = RoundRobin(["a", "b"], weights=[2.0, 1.0])
    picks = [rr.next() for _ in range(30)]
    assert picks.count("a") == 20
    assert picks.count("b") == 10


def test_round_robin_rejects_empty():
    with pytest.raises(ValueError):
        RoundRobin([])


def test_category_and_symbol_rotation_cover_every_combination():
    # Regression test for the actual bug reported: a pure "pick cheapest
    # overall" scan kept picking the same symbol+category every cycle
    # forever. Independent round-robins over 3 categories and N symbols
    # must visit every (category, symbol) pair within lcm(3, N) cycles.
    symbols = DEFAULT_SYMBOLS  # 10 symbols
    category_rr = RoundRobin(list(CATEGORY_LEGS))
    symbol_rr = RoundRobin(symbols)
    seen = set()
    cycles = 30  # lcm(3, 10)
    for _ in range(cycles):
        seen.add((category_rr.next(), symbol_rr.next()))
    assert len(seen) == 3 * len(symbols)
    assert {c for c, _ in seen} == set(CATEGORY_LEGS)
    assert {s for _, s in seen} == set(symbols)


def test_category_legs_cover_the_three_requested_types():
    assert set(CATEGORY_LEGS) == {"over_under", "even_odd", "rise_fall"}
    assert CATEGORY_LEGS["over_under"] == [("DIGITOVER", "4"), ("DIGITUNDER", "4")]
    assert CATEGORY_LEGS["even_odd"] == [("DIGITEVEN", None), ("DIGITODD", None)]
    assert CATEGORY_LEGS["rise_fall"] == [("CALL", None), ("PUT", None)]
