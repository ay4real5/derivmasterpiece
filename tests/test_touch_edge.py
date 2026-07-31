import pytest

from deriv_bot.touch_edge import margin_and_win_prob


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
