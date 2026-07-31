import pytest

from deriv_bot.touch_edge import margin_and_win_prob, scan_touch


class FakeAPI:
    """Just enough of DerivAPI for scan_touch to run end to end."""

    def __init__(self, spot: float, pip_size: int = 4):
        self.spot = spot
        self.pip_size = pip_size
        self.proposal_calls: list[dict] = []

    async def list_accounts(self, token):
        return [{"account_type": "demo", "account_id": "DEMO1"}]

    async def request_trading_ws_url(self, token, account_id):
        return "wss://fake"

    async def connect(self, ws_url):
        pass

    async def close(self):
        pass

    async def ticks_history(self, symbol, count=1):
        return {"history": {"prices": [self.spot]}, "pip_size": self.pip_size}

    async def proposal(self, **params):
        self.proposal_calls.append(params)
        # A fixed, arbitrary margin - not the point of this test, which is
        # only that the barrier string sent to Deriv is spot-scaled.
        payout = params["amount"] / 0.5 * 0.98
        return {"proposal": {"payout": payout}}


@pytest.mark.asyncio
async def test_scan_touch_scales_barriers_by_the_symbols_own_spot(monkeypatch):
    """The bug this guards: barrier_pcts are genuine fractions, but Deriv
    wants an absolute offset. Without scaling by spot, the same nominal
    fraction sent to a ~112-priced and a ~780,000-priced symbol produces
    barriers that mean wildly different things - confirmed live."""
    import deriv_bot.touch_edge as touch_edge_module

    fake = FakeAPI(spot=112.0)
    monkeypatch.setattr(touch_edge_module, "DerivAPI", lambda app_id: fake)

    await scan_touch(
        "app123", ["R_50"], 3.0,
        barrier_pcts=(0.30,), durations=((5, "m"),), token="tok",
    )

    assert len(fake.proposal_calls) == 2  # ONETOUCH + NOTOUCH
    for call in fake.proposal_calls:
        assert call["barrier"] == "+33.6000"  # 0.30 * 112.0


@pytest.mark.asyncio
async def test_scan_touch_respects_the_symbols_own_pip_size(monkeypatch):
    """The bug this guards: Deriv rejects a barrier with more decimal places
    than a symbol's own pip_size allows (confirmed live - R_10 caps at 3,
    1HZ10V at 2), so a universal 4-decimal format silently zeroed out entire
    symbols from a scan."""
    import deriv_bot.touch_edge as touch_edge_module

    fake = FakeAPI(spot=9679.64, pip_size=2)
    monkeypatch.setattr(touch_edge_module, "DerivAPI", lambda app_id: fake)

    await scan_touch(
        "app123", ["1HZ10V"], 3.0,
        barrier_pcts=(0.01,), durations=((5, "m"),), token="tok",
    )

    for call in fake.proposal_calls:
        assert len(call["barrier"].split(".")[1]) == 2


@pytest.mark.asyncio
async def test_scan_touch_skips_a_symbol_with_no_quotable_spot(monkeypatch):
    import deriv_bot.touch_edge as touch_edge_module

    class DeadSpotAPI(FakeAPI):
        async def ticks_history(self, symbol, count=1):
            raise RuntimeError("symbol not found")

    fake = DeadSpotAPI(spot=0.0)
    monkeypatch.setattr(touch_edge_module, "DerivAPI", lambda app_id: fake)

    results = await scan_touch(
        "app123", ["DEAD_SYM"], 3.0,
        barrier_pcts=(0.30,), durations=((5, "m"),), token="tok",
    )
    assert results == []
    assert fake.proposal_calls == []


def test_no_margin_no_skill_is_a_fair_bet():
    """Zero margin means the two implied probabilities sum to exactly 1."""
    # payout = stake / true_prob at zero margin
    stake = 10.0
    touch_payout = stake / 0.3
    no_touch_payout = stake / 0.7
    margin, win_prob = margin_and_win_prob(stake, touch_payout, no_touch_payout)
    assert margin == pytest.approx(0.0, abs=1e-9)
    assert win_prob == pytest.approx(0.7)


def test_margin_matches_the_quoted_house_edge():
    """A known margin `m` must come back out as `m` regardless of which
    barrier (i.e. which underlying win probability) it was quoted at — this
    is the whole point of the complementary-pair trick."""
    stake = 3.0
    for true_prob, m in [(0.5, 0.024), (0.1, 0.024), (0.965, 0.024)]:
        # Scaled so stake/payout sums to exactly (1 + m) across the pair,
        # whatever the split between the two sides.
        touch_payout = stake / (true_prob * (1 + m))
        no_touch_payout = stake / ((1 - true_prob) * (1 + m))
        margin, _ = margin_and_win_prob(stake, touch_payout, no_touch_payout)
        assert margin == pytest.approx(m, abs=1e-9)


def test_wider_barrier_raises_notouch_win_prob_at_the_same_margin():
    """The shape changes, the cost doesn't — this is what makes Touch/No
    Touch a payout structure you buy, not a forecast."""
    stake = 3.0
    m = 0.023

    def payouts(true_notouch_prob: float) -> tuple[float, float]:
        touch = stake / (1 - true_notouch_prob) * (1 - m)
        no_touch = stake / true_notouch_prob * (1 - m)
        return touch, no_touch

    narrow_touch, narrow_no_touch = payouts(0.05)
    wide_touch, wide_no_touch = payouts(0.90)

    narrow_margin, narrow_win = margin_and_win_prob(stake, narrow_touch, narrow_no_touch)
    wide_margin, wide_win = margin_and_win_prob(stake, wide_touch, wide_no_touch)

    assert wide_win > narrow_win
    assert narrow_margin == pytest.approx(wide_margin, abs=1e-9)


@pytest.mark.parametrize("stake,touch_payout,no_touch_payout", [
    (0.0, 5.0, 5.0), (3.0, 0.0, 5.0), (3.0, 5.0, 0.0), (-1.0, 5.0, 5.0),
])
def test_rejects_non_positive_inputs(stake, touch_payout, no_touch_payout):
    with pytest.raises(ValueError):
        margin_and_win_prob(stake, touch_payout, no_touch_payout)
