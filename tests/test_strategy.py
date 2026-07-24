import pytest

from deriv_bot.strategy import (
    STRATEGIES,
    DigitFrequencyStrategy,
    EvenOddFrequencyStrategy,
    LowEdgeStrategy,
    StreakReversalStrategy,
    build_strategy,
)


def test_no_signal_before_window_fills():
    s = DigitFrequencyStrategy(window=10, threshold=0.03, over_under_barrier=4)
    for d in [0, 1, 2, 3, 4, 5, 6, 7, 8]:
        assert s.on_tick(d) is None


def test_signals_over_when_low_digits_overrepresented():
    s = DigitFrequencyStrategy(window=10, threshold=0.1, over_under_barrier=4)
    digits = [0, 1, 2, 3, 4, 0, 1, 2, 3, 9]  # 9/10 digits are <= 4
    signal = None
    for d in digits:
        signal = s.on_tick(d)
    assert signal is not None
    assert signal.contract_type == "DIGITOVER"
    assert signal.barrier == "4"


def test_signals_under_when_high_digits_overrepresented():
    s = DigitFrequencyStrategy(window=10, threshold=0.1, over_under_barrier=4)
    digits = [9, 8, 7, 6, 5, 9, 8, 7, 6, 0]  # only 1/10 digits are <= 4
    signal = None
    for d in digits:
        signal = s.on_tick(d)
    assert signal is not None
    assert signal.contract_type == "DIGITUNDER"


def test_no_signal_when_within_threshold():
    s = DigitFrequencyStrategy(window=10, threshold=0.2, over_under_barrier=4)
    digits = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]  # exactly the expected 50/50 split
    signal = None
    for d in digits:
        signal = s.on_tick(d)
    assert signal is None


def test_rejects_out_of_range_barrier():
    with pytest.raises(ValueError):
        DigitFrequencyStrategy(over_under_barrier=10)


def test_even_odd_signals_odd_when_even_overrepresented():
    s = EvenOddFrequencyStrategy(window=10, threshold=0.1)
    digits = [0, 2, 4, 6, 8, 0, 2, 4, 6, 1]  # 9/10 digits are even
    signal = None
    for d in digits:
        signal = s.on_tick(d)
    assert signal is not None
    assert signal.contract_type == "DIGITODD"
    assert signal.barrier is None


def test_even_odd_signals_even_when_odd_overrepresented():
    s = EvenOddFrequencyStrategy(window=10, threshold=0.1)
    digits = [1, 3, 5, 7, 9, 1, 3, 5, 7, 0]  # 9/10 digits are odd
    signal = None
    for d in digits:
        signal = s.on_tick(d)
    assert signal is not None
    assert signal.contract_type == "DIGITEVEN"


def test_even_odd_no_signal_within_threshold():
    s = EvenOddFrequencyStrategy(window=10, threshold=0.2)
    digits = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]  # exactly 50/50
    signal = None
    for d in digits:
        signal = s.on_tick(d)
    assert signal is None


def test_streak_reversal_fires_after_streak_length():
    s = StreakReversalStrategy(streak_len=3, over_under_barrier=4)
    assert s.on_tick(1) is None  # under, streak=1
    assert s.on_tick(2) is None  # under, streak=2
    signal = s.on_tick(3)  # under, streak=3 -> fires
    assert signal is not None
    assert signal.contract_type == "DIGITOVER"
    assert signal.barrier == "4"


def test_streak_reversal_resets_on_side_change():
    s = StreakReversalStrategy(streak_len=3, over_under_barrier=4)
    s.on_tick(1)  # under
    s.on_tick(9)  # over, streak resets to 1
    assert s.on_tick(2) is None  # under, streak=1 (not 3)


def test_streak_reversal_rejects_out_of_range_barrier():
    with pytest.raises(ValueError):
        StreakReversalStrategy(over_under_barrier=10)


def test_build_strategy_by_name():
    s = build_strategy("digit_frequency", window=10, threshold=0.1, over_under_barrier=4)
    assert isinstance(s, DigitFrequencyStrategy)


def test_build_strategy_rejects_unknown_name():
    with pytest.raises(ValueError):
        build_strategy("not_a_real_strategy")


def test_registry_contains_all_strategies():
    assert set(STRATEGIES) == {
        "digit_frequency", "even_odd_frequency", "streak_reversal", "low_edge",
    }


def test_low_edge_fires_on_cadence_with_fixed_contract():
    s = LowEdgeStrategy(every=3)
    signals = [s.on_tick(d) for d in [5, 2, 8, 1, 9, 0]]
    assert [sig is not None for sig in signals] == [False, False, True, False, False, True]
    assert signals[2].contract_type == "DIGITOVER"
    assert signals[2].barrier == "0"


def test_low_edge_rejects_bad_cadence():
    with pytest.raises(ValueError):
        LowEdgeStrategy(every=0)


def test_low_edge_configurable_contract():
    s = LowEdgeStrategy(every=1, contract_type="DIGITDIFF", barrier=7)
    signal = s.on_tick(3)
    assert signal.contract_type == "DIGITDIFF"
    assert signal.barrier == "7"


def test_low_edge_even_odd_drops_barrier():
    s = LowEdgeStrategy(every=1, contract_type="DIGITEVEN", barrier=4)
    assert s.on_tick(3).barrier is None


def test_low_edge_rejects_unknown_contract_and_bad_barrier():
    with pytest.raises(ValueError):
        LowEdgeStrategy(contract_type="CALL")
    with pytest.raises(ValueError):
        LowEdgeStrategy(contract_type="DIGITMATCH", barrier=10)
    with pytest.raises(ValueError):
        LowEdgeStrategy(contract_type="DIGITMATCH", barrier=None)
