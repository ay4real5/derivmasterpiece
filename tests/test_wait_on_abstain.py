"""Abstaining must be able to mean WAIT, not "buy the cheapest thing anyway".

The gate decided WHICH contract to buy, never WHETHER to buy. So the bot paid
the margin every 45 seconds regardless of what it had just measured - the
opposite of waiting for a setup.

Measured at min_z=2.0 over two legs: a cycle fires 4.5% of the time, so ~3.6
trades an hour instead of 80. About 22x fewer trades and 22x less bleed.

What it does NOT do is make those trades likelier to win. A z>=2 reading on a
structureless feed is a false positive by construction - roughly 1 draw in 22.
The whole gain is in not paying 2.33% eighty times an hour.
"""
import math

import pytest
import yaml


def test_both_abstain_actions_are_accepted():
    src = open("main.py", encoding="utf-8").read()
    assert '("cheapest", "wait")' in src


def test_the_default_is_cheapest_so_upgrading_changes_nothing():
    """An existing config must not silently start skipping trades."""
    src = open("main.py", encoding="utf-8").read()
    assert 'study_cfg.pop("abstain_action", "cheapest")' in src


def test_an_unknown_action_is_rejected_loudly():
    src = open("main.py", encoding="utf-8").read()
    assert "abstain_action must be 'cheapest' or" in src


def test_wait_skips_the_cycle_rather_than_falling_through():
    """The skip has to `continue`, not just avoid the override - otherwise the
    cheapest-margin pick made earlier in the cycle still gets bought."""
    src = open("main.py", encoding="utf-8").read()
    i = src.index('if studied is None and abstain_action == "wait":')
    block = src[i:i + 700]
    assert "continue" in block
    assert "await asyncio.sleep" in block, "must still honour the cycle interval"


def test_the_abstain_key_is_removed_before_building_the_study_config():
    """study_cfg is passed on to _study_pick; an unexpected key there would
    be silently ignored, which is how a setting ends up doing nothing."""
    src = open("main.py", encoding="utf-8").read()
    assert 'study_cfg.pop("abstain_action"' in src


# --- the arithmetic the setting is justified by ----------------------------

def fire_rate(min_z, legs):
    p_one = 0.5 * math.erfc(min_z / math.sqrt(2))
    return 1 - (1 - p_one) ** legs


def test_the_quoted_fire_rate_is_right():
    assert fire_rate(2.0, 2) == pytest.approx(0.045, abs=0.002)


def test_a_higher_gate_waits_longer():
    assert fire_rate(3.0, 2) < fire_rate(2.0, 2) < fire_rate(1.0, 2)


def test_more_legs_means_more_false_positives():
    """Every extra leg studied is another chance to cross the threshold by
    luck - which is why the deep post-loss review fires more often."""
    assert fire_rate(2.0, 8) > fire_rate(2.0, 2)


def test_waiting_cuts_cost_in_proportion_to_trades_not_accuracy():
    """The honest claim: cost scales with trade count, and nothing here
    changes the per-trade odds."""
    cycles_per_hour = 3600 / 45
    margin, stake = 0.0233, 3.0
    always = cycles_per_hour * stake * margin
    waiting = cycles_per_hour * fire_rate(2.0, 2) * stake * margin
    assert waiting < always / 20
    assert always / waiting == pytest.approx(1 / fire_rate(2.0, 2), rel=1e-6)


def test_the_shipped_config_states_its_choice_explicitly():
    """Either value is defensible; leaving it unset is what is not, because
    the default quietly keeps trading every cycle."""
    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    study = cfg["scan_trade"]["study"]
    assert "abstain_action" in study, (
        "config.yaml should say outright whether abstaining means waiting")
    assert study["abstain_action"] in ("cheapest", "wait")
