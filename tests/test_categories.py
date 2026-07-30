"""The rotation's contract families are now config-driven."""
import pytest
import yaml

from deriv_bot.multi_scan import CATEGORY_LEGS


def load_config():
    with open("config.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def test_config_categories_are_valid():
    cats = load_config()["scan_trade"]["categories"]
    assert cats, "categories must not be empty"
    assert all(c in CATEGORY_LEGS for c in cats)


def test_a_cheap_family_is_always_in_the_rotation():
    """rise_fall costs 3.99% against even_odd's 2.39% - measured live from
    complementary DIGITEVEN/DIGITODD quotes, which partition exactly.

    This used to assert rise_fall was DROPPED. That froze one day's decision
    into a test, and when the 2026-07-28 configuration was deliberately
    restored the test failed for doing its job wrongly - it was guarding a
    choice, not an invariant. Including the expensive family is allowed; making
    it the ONLY family is what would be a mistake nobody chose.

    Checked by LEG, not by category name. The rewrite above still named the
    cheap categories, and that broke again the moment one-sided variants
    arrived: `even` quotes the same 2.39% as `even_odd` but is a different
    string, so a name-based check called it expensive. Twice burned - the cost
    belongs to the contract, so ask the contract.
    """
    cats = load_config()["scan_trade"]["categories"]
    legs = [leg for c in cats for leg in CATEGORY_LEGS[c]]
    # DIGIT* contracts quote ~2.39%; CALL/PUT quote 3.99% on the same symbol.
    assert any(ct.startswith("DIGIT") for ct, _ in legs), (
        f"only CALL/PUT legs configured: {cats} - every trade would pay the "
        f"3.99% tier when 2.39% is available")


def test_two_families_remain_so_the_rotation_still_interchanges():
    # the variety the user asked to preserve
    assert len(load_config()["scan_trade"]["categories"]) >= 2


def test_config_symbols_exclude_the_expensive_tier():
    """R_100, 1HZ100V and 1HZ10V quote 3.99% against the others' 2.39% -
    measured on three separate days now, same split every time.

    Note the split does NOT follow the 1HZ/R_ family line: these three are a
    mix of both. Excluding a whole family is not the same as excluding the
    expensive tier, which is why this names symbols rather than a prefix.
    """
    symbols = load_config()["scan_trade"]["symbols"]
    for expensive in ("R_100", "1HZ100V", "1HZ10V"):
        assert expensive not in symbols


def test_more_than_one_symbol_stays_in_rotation():
    """Concentration is its own failure mode: 'best symbol' selection once put
    every trade on R_10 DIGITOVER, which removes any spread of the losing runs
    AND makes a bad symbol indistinguishable from a bad strategy.

    A count rather than an exact list - the set is a judgement call that has
    changed several times, the need for more than one has not.
    """
    symbols = load_config()["scan_trade"]["symbols"]
    assert len(symbols) >= 2, f"only {len(symbols)} symbol(s): {symbols}"
    assert len(symbols) == len(set(symbols)), "duplicate symbols"


def test_every_configured_category_has_legs():
    for cat in load_config()["scan_trade"]["categories"]:
        assert CATEGORY_LEGS[cat], f"{cat} has no legs defined"


def test_the_ladder_still_fits_the_daily_cap():
    # the mistake that cost real drawdown: a 509.95 ladder under a 300 cap
    cfg = load_config()
    ladder = sum(cfg["staking"]["sequence"])
    assert ladder < cfg["risk"]["max_daily_loss"], (
        f"ladder {ladder} exceeds max_daily_loss "
        f"{cfg['risk']['max_daily_loss']} - it could never complete")


def test_the_cli_entrypoints_accept_the_offset():
    """Regression: the body of cmd_scan_trade was updated to pass
    daily_pnl_offset but its signature was not, so every child crashed with
    TypeError one second after launch and the supervisor restarted it into
    the same crash. The crash_loop alert caught it."""
    import inspect

    import main
    for name in ("cmd_scan_trade", "cmd_live", "_run_scan_trade", "_run_live"):
        params = inspect.signature(getattr(main, name)).parameters
        assert "daily_pnl_offset" in params, f"{name} cannot accept the offset"
