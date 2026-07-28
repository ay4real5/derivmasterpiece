import pytest

from deriv_bot.preflight import (
    check_account_type,
    check_journal_writable,
    check_limits,
    check_real_money_acknowledged,
    check_staking,
    run_checks,
)

# The shape Deriv's list_accounts actually returns - account_type, and no
# is_virtual field at all. The first version of the check guessed is_virtual
# and defaulted it to False, so every account read as REAL.
DEMO = {"account_id": "DOT93163621", "account_type": "demo", "currency": "USD"}
REAL = {"account_id": "ROT91862437", "account_type": "real", "currency": "USD"}


def test_demo_mode_with_a_real_account_is_refused():
    # one PAT reaches both accounts, so a .env typo is the whole difference
    assert check_account_type(REAL, demo_mode=True) is not None


def test_real_mode_with_a_demo_account_is_refused():
    assert check_account_type(DEMO, demo_mode=False) is not None


def test_matching_account_and_mode_pass():
    assert check_account_type(DEMO, demo_mode=True) is None
    assert check_account_type(REAL, demo_mode=False) is None


def test_martingale_is_refused_on_a_real_account():
    # matches main.py's own guard, but fails before anything is funded
    assert check_staking("doubling", demo_mode=False) is not None
    assert check_staking("smart_recovery", demo_mode=False) is not None
    assert check_staking("flat", demo_mode=False) is None


def test_martingale_is_allowed_on_demo():
    assert check_staking("doubling", demo_mode=True) is None


def test_real_money_needs_the_second_switch():
    assert check_real_money_acknowledged({}, demo_mode=False) is not None
    assert check_real_money_acknowledged({"i_understand_real_money": False},
                                         demo_mode=False) is not None
    assert check_real_money_acknowledged({"i_understand_real_money": "yes"},
                                         demo_mode=False) is not None  # must be True
    assert check_real_money_acknowledged({"i_understand_real_money": True},
                                         demo_mode=False) is None


def test_demo_never_needs_the_acknowledgement():
    assert check_real_money_acknowledged({}, demo_mode=True) is None


def test_a_loss_cap_bigger_than_the_balance_is_refused():
    # the live case: 1000 carried from a 4600 demo onto a 200 deposit
    problems = check_limits({"max_daily_loss": 1000}, balance=200, stake=5)
    assert problems
    assert any("cannot protect" in p or "exceeds the entire" in p for p in problems)


def test_a_proportionate_loss_cap_passes():
    assert check_limits({"max_daily_loss": 20}, balance=200, stake=2) == []


def test_an_oversized_stake_is_refused():
    problems = check_limits({"max_daily_loss": 20}, balance=200, stake=50)
    assert any("5% of the" in p for p in problems)


def test_a_missing_loss_cap_is_refused():
    assert check_limits({}, balance=1000, stake=5)


def test_journal_writable_in_a_normal_directory(tmp_path):
    assert check_journal_writable(str(tmp_path / "trade_journal.csv")) is None


def test_journal_in_a_nonexistent_directory_is_refused(tmp_path):
    bad = tmp_path / "nope" / "deeper" / "j.csv"
    assert check_journal_writable(str(bad)) is not None


def test_run_checks_reports_every_problem_at_once():
    # one run should show all of them, not one per attempt
    config = {"risk": {"max_daily_loss": 1000}, "stake": 50,
              "journal_path": "trade_journal.csv"}
    problems = run_checks(config, demo_mode=False, account=DEMO,
                          balance=200, staking_name="doubling")
    assert len(problems) >= 4  # account type, staking, acknowledgement, limits


def test_run_checks_passes_a_sane_real_money_config(tmp_path):
    config = {"risk": {"max_daily_loss": 20}, "stake": 2,
              "journal_path": str(tmp_path / "j.csv"),
              "i_understand_real_money": True}
    assert run_checks(config, demo_mode=False, account=REAL,
                      balance=200, staking_name="flat") == []


def test_account_type_is_read_from_the_field_deriv_actually_sends():
    # regression: is_virtual does not exist in list_accounts output, so
    # defaulting it to False made every account look REAL
    from deriv_bot.preflight import account_is_demo
    assert account_is_demo({"account_type": "demo"}) is True
    assert account_is_demo({"account_type": "real"}) is False
    assert account_is_demo({"account_type": "DEMO"}) is True


def test_unknown_account_type_is_refused_not_assumed():
    # the dangerous direction: guessing "real" would let DEMO_MODE=false
    # sail past while actually pointed at a demo account
    from deriv_bot.preflight import account_is_demo
    assert account_is_demo({"account_id": "X"}) is None
    assert check_account_type({"account_id": "X"}, demo_mode=False) is not None
    assert check_account_type({"account_id": "X"}, demo_mode=True) is not None


def test_is_virtual_still_honoured_if_it_ever_returns():
    from deriv_bot.preflight import account_is_demo
    assert account_is_demo({"is_virtual": True}) is True
    assert account_is_demo({"is_virtual": False}) is False
