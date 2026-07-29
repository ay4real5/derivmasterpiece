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


def test_rise_fall_is_dropped():
    # within the seven cheap symbols: over_under 2.29%, even_odd 2.30%,
    # rise_fall 3.79% - a 1.5 point penalty on a third of all trades
    assert "rise_fall" not in load_config()["scan_trade"]["categories"]


def test_two_families_remain_so_the_rotation_still_interchanges():
    # the variety the user asked to preserve
    assert len(load_config()["scan_trade"]["categories"]) >= 2


def test_config_symbols_exclude_the_expensive_tier():
    # R_100, 1HZ100V, 1HZ10V price at 3.82-3.91% on both measured days
    symbols = load_config()["scan_trade"]["symbols"]
    for expensive in ("R_100", "1HZ100V", "1HZ10V"):
        assert expensive not in symbols
    assert len(symbols) == 7


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
