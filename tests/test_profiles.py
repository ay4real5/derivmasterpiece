import os

import yaml

from deriv_bot.preflight import check_staking
from deriv_bot.profiles import (
    activate,
    describe_changes,
    list_profiles,
    load_profile,
    targets_real_money,
)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --- the opt-in matrix -------------------------------------------------
# The ladder-on-real block is a seatbelt we wrote, not a Deriv rule. It is
# the account holder's decision, so it must be a switch - but the default
# must still be the safe one.

def test_ladder_on_real_is_refused_without_the_opt_in():
    assert check_staking("doubling", demo_mode=False, config={}) is not None
    assert check_staking("doubling", demo_mode=False,
                         config={"i_accept_progressive_staking_on_real": False}) is not None


def test_ladder_on_real_is_allowed_with_the_opt_in():
    assert check_staking("doubling", demo_mode=False,
                         config={"i_accept_progressive_staking_on_real": True}) is None


def test_the_opt_in_must_be_exactly_true():
    # a truthy string must not be enough for a decision this size
    assert check_staking("doubling", demo_mode=False,
                         config={"i_accept_progressive_staking_on_real": "yes"}) is not None


def test_demo_is_unaffected_by_the_opt_in():
    for cfg in ({}, {"i_accept_progressive_staking_on_real": True}):
        assert check_staking("doubling", demo_mode=True, config=cfg) is None


def test_flat_never_needs_the_opt_in():
    assert check_staking("flat", demo_mode=False, config={}) is None


def test_the_refusal_points_at_the_evidence():
    msg = check_staking("doubling", demo_mode=False, config={})
    assert "martingale_sim" in msg
    assert "i_accept_progressive_staking_on_real" in msg


# --- profiles ----------------------------------------------------------

def test_the_four_profiles_exist():
    assert set(list_profiles(REPO)) >= {"demo-ladder", "demo-flat",
                                        "real-flat", "real-ladder"}


def test_demo_profiles_do_not_acknowledge_real_money():
    for name in ("demo-ladder", "demo-flat"):
        p = load_profile(REPO, name)
        assert p.get("i_understand_real_money") is not True
        assert not targets_real_money(p, name)


def test_real_profiles_acknowledge_real_money():
    for name in ("real-flat", "real-ladder"):
        p = load_profile(REPO, name)
        assert p.get("i_understand_real_money") is True
        assert targets_real_money(p, name)


def test_only_the_real_ladder_profile_opts_into_progressive_staking():
    assert load_profile(REPO, "real-ladder").get(
        "i_accept_progressive_staking_on_real") is True
    for name in ("real-flat", "demo-ladder", "demo-flat"):
        assert load_profile(REPO, name).get(
            "i_accept_progressive_staking_on_real") is not True


def test_real_profiles_scale_their_limits_down():
    # 1000 came from a 4600-balance era; a real deposit is far smaller
    for name in ("real-flat", "real-ladder"):
        p = load_profile(REPO, name)
        assert p["risk"]["max_daily_loss"] <= 50
        assert p["stake"] <= 5


def test_every_real_profile_would_pass_its_own_staking_check():
    for name in ("real-flat", "real-ladder"):
        p = load_profile(REPO, name)
        staking = (p.get("staking") or {}).get("name", "flat")
        assert check_staking(staking, demo_mode=False, config=p) is None


def test_activate_writes_the_profile_and_backs_up_the_old_config(tmp_path):
    repo = tmp_path
    (repo / "profiles").mkdir()
    (repo / "config.yaml").write_text("stake: 5.0\n", encoding="utf-8")
    (repo / "profiles" / "x.yaml").write_text("stake: 1.0\n", encoding="utf-8")

    profile, changes = activate(str(repo), "x")

    assert profile["stake"] == 1.0
    assert yaml.safe_load((repo / "config.yaml").read_text())["stake"] == 1.0
    # the switch must always be one copy away from being undone
    assert yaml.safe_load((repo / "config.yaml.bak").read_text())["stake"] == 5.0
    assert any("stake" in c for c in changes)


def test_describe_changes_reports_the_fields_that_matter():
    old = {"stake": 5, "staking": {"name": "doubling"},
           "risk": {"max_daily_loss": 1000}}
    new = {"stake": 1, "staking": {"name": "flat"},
           "risk": {"max_daily_loss": 20},
           "i_understand_real_money": True}
    changes = describe_changes(old, new)
    joined = " ".join(changes)
    assert "stake" in joined and "staking" in joined
    assert "max_daily_loss" in joined and "real_money_ack" in joined


def test_describe_changes_is_empty_for_an_identical_config():
    cfg = {"stake": 5, "staking": {"name": "flat"}}
    assert describe_changes(cfg, cfg) == []
