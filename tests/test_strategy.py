from deriv_bot.strategy import DigitFrequencyStrategy


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
    import pytest
    with pytest.raises(ValueError):
        DigitFrequencyStrategy(over_under_barrier=10)
