import asyncio

from deriv_bot.multi_scan import CATEGORY_LEGS
from main import _study_pick

SYMBOLS = ["R_10", "R_25"]
CANDIDATES = [("DIGITOVER", "4"), ("DIGITUNDER", "4"),
              ("DIGITEVEN", None), ("DIGITODD", None),
              ("CALL", None), ("PUT", None)]


class _FakeAPI:
    """Serves canned tick history per symbol and counts the calls, so tests
    can assert how much extra API traffic the study costs."""

    def __init__(self, prices_by_symbol, pip_size=2):
        self.prices_by_symbol = prices_by_symbol
        self.pip_size = pip_size
        self.calls: list[tuple[str, int]] = []

    async def ticks_history(self, symbol, count=1000, style="ticks"):
        self.calls.append((symbol, count))
        return {"history": {"prices": self.prices_by_symbol[symbol]},
                "pip_size": self.pip_size}


class _FailingAPI:
    async def ticks_history(self, symbol, count=1000, style="ticks"):
        raise ConnectionError("no close frame received or sent")


def _quotes(symbol, legs):
    return [{"symbol": symbol, "contract_type": k, "barrier": b,
             "payout": 9.77, "ask_price": 5.0, "edge_pct": 2.3, "win_prob": 0.5}
            for k, b in legs]


# Two decimals matter: at pip_size=2 a price of 100.1 formats to "100.10",
# whose last digit is 0 — writing `100.{d}` would make every digit 0 and
# quietly turn "uniform" into "always even". Same trap the production code
# guards against in digits_from_ticks.
def _price(digit):
    return float(f"100.{digit:02d}")


def _uniform(n):
    return [_price(i % 10) for i in range(n)]


def _all_even(n):
    return [_price((i * 2) % 10) for i in range(n)]


def _run(api, cfg, results, symbol, legs, after_loss):
    return asyncio.run(_study_pick(
        api, cfg, results, symbol, legs, after_loss=after_loss,
        symbols=SYMBOLS, candidates=CANDIDATES,
    ))


def test_normal_cycle_studies_only_the_rotated_symbol():
    api = _FakeAPI({s: _uniform(200) for s in SYMBOLS})
    legs = CATEGORY_LEGS["even_odd"]
    _run(api, {"enabled": True, "window": 200}, _quotes("R_10", legs), "R_10", legs, False)
    assert [c[0] for c in api.calls] == ["R_10"]  # one call, not ten


def test_deep_review_after_a_loss_studies_every_symbol_with_a_wider_window():
    api = _FakeAPI({s: _uniform(1000) for s in SYMBOLS})
    legs = CATEGORY_LEGS["even_odd"]
    cfg = {"enabled": True, "window": 200, "deep_window": 1000, "deep_after_loss": True}
    _run(api, cfg, _quotes("R_10", legs), "R_10", legs, True)
    assert sorted(c[0] for c in api.calls) == sorted(SYMBOLS)
    assert all(c[1] == 1000 for c in api.calls)  # the wider window


def test_deep_review_can_be_switched_off():
    api = _FakeAPI({s: _uniform(200) for s in SYMBOLS})
    legs = CATEGORY_LEGS["even_odd"]
    cfg = {"enabled": True, "window": 200, "deep_after_loss": False}
    _run(api, cfg, _quotes("R_10", legs), "R_10", legs, True)
    assert [c[0] for c in api.calls] == ["R_10"]


def test_uniform_history_abstains_and_keeps_the_rotation_pick():
    api = _FakeAPI({s: _uniform(200) for s in SYMBOLS})
    legs = CATEGORY_LEGS["even_odd"]
    pick, selector = _run(api, {"enabled": True, "window": 200},
                          _quotes("R_10", legs), "R_10", legs, False)
    assert pick is None
    assert selector == "study-abstain"


def test_a_genuinely_biased_stream_produces_a_study_pick():
    api = _FakeAPI({"R_10": _all_even(200), "R_25": _uniform(200)})
    legs = CATEGORY_LEGS["even_odd"]
    pick, selector = _run(api, {"enabled": True, "window": 200},
                          _quotes("R_10", legs), "R_10", legs, False)
    assert selector == "study"
    assert pick is not None
    assert pick["contract_type"] == "DIGITEVEN"


def test_history_failure_does_not_stop_trading():
    # a dead socket during the study must fall back, never raise
    legs = CATEGORY_LEGS["even_odd"]
    pick, selector = _run(_FailingAPI(), {"enabled": True, "window": 200},
                          _quotes("R_10", legs), "R_10", legs, False)
    assert pick is None
    assert selector == "study-abstain"


def test_rise_fall_legs_are_never_study_picked():
    # CALL/PUT resolve on price; digits say nothing about them
    api = _FakeAPI({"R_10": _all_even(200), "R_25": _uniform(200)})
    legs = CATEGORY_LEGS["rise_fall"]
    pick, selector = _run(api, {"enabled": True, "window": 200},
                          _quotes("R_10", legs), "R_10", legs, False)
    assert pick is None
    assert selector == "study-abstain"


def test_deep_review_fetches_histories_concurrently():
    """A deep review is 10 round trips; awaiting them in sequence multiplied
    the cycle by the link latency (measured as 90-220s gaps against a 45s
    interval). This asserts they overlap rather than queue."""
    import asyncio as _asyncio

    class _SlowAPI(_FakeAPI):
        async def ticks_history(self, symbol, count=1000, style="ticks"):
            await _asyncio.sleep(0.05)
            return await super().ticks_history(symbol, count, style)

    api = _SlowAPI({s: _uniform(200) for s in SYMBOLS})
    legs = CATEGORY_LEGS["even_odd"]
    cfg = {"enabled": True, "window": 200, "deep_window": 200, "deep_after_loss": True}

    loop = _asyncio.new_event_loop()
    try:
        start = loop.time()
        loop.run_until_complete(_study_pick(
            api, cfg, _quotes("R_10", legs), "R_10", legs,
            after_loss=True, symbols=SYMBOLS, candidates=CANDIDATES,
        ))
        elapsed = loop.time() - start
    finally:
        loop.close()

    assert len(api.calls) == len(SYMBOLS)
    # sequential would be >= 0.05 * len(SYMBOLS); concurrent stays near one hop
    assert elapsed < 0.05 * len(SYMBOLS)


def test_one_failing_symbol_does_not_cancel_the_others():
    class _PartlyFailingAPI(_FakeAPI):
        async def ticks_history(self, symbol, count=1000, style="ticks"):
            if symbol == "R_10":
                raise ConnectionError("no close frame received or sent")
            return await super().ticks_history(symbol, count, style)

    api = _PartlyFailingAPI({"R_25": _all_even(200)})
    legs = CATEGORY_LEGS["even_odd"]
    cfg = {"enabled": True, "window": 200, "deep_window": 200, "deep_after_loss": True}
    pick, selector = _run(api, cfg, _quotes("R_25", legs), "R_25", legs, True)
    assert selector == "study"          # R_25 still scored despite R_10 failing
    assert pick is not None
