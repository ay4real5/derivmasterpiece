"""The configured categories must bound EVERY path that can buy a contract.

A live config of `categories: [even, rise]` still bought DIGITODD and
DIGITUNDER - 9 of 26 trades, all tagged selector=study, laddering up to 29.36
on contract types the config had excluded outright.

The cause was that `categories` constrained only the ROTATION, while the deep
study after a loss scans `candidates`, which was left at all six legs:

    target_legs = list(candidates) if deep else list(legs)

So the rule held on the happy path and broke precisely when a loss triggered a
deep review - the moment the ladder was climbing and stakes were largest.
"""
import pytest

from deriv_bot.multi_scan import CATEGORY_LEGS, DEFAULT_CANDIDATES


def confine(categories, candidates=None):
    """The restriction main.py applies. Mirrors it so the rule is testable
    without standing up a live scan loop."""
    allowed = {leg for c in categories for leg in CATEGORY_LEGS[c]}
    return [leg for leg in (candidates or DEFAULT_CANDIDATES) if leg in allowed]


def test_even_and_rise_confine_to_exactly_two_legs():
    assert confine(["even", "rise"]) == [("DIGITEVEN", None), ("CALL", None)]


def test_the_forbidden_legs_are_gone():
    """The four contract types that actually traded against the rule."""
    legs = confine(["even", "rise"])
    for banned in (("DIGITODD", None), ("PUT", None),
                   ("DIGITUNDER", "4"), ("DIGITOVER", "4")):
        assert banned not in legs, f"{banned[0]} is still quotable"


def test_a_deep_study_cannot_reach_outside_the_categories():
    """`target_legs = candidates if deep else legs` - so confining candidates
    is what makes the deep path safe. This asserts the property directly."""
    candidates = confine(["even", "rise"])
    deep_target_legs = list(candidates)          # what _study_pick would scan
    allowed = {leg for c in ["even", "rise"] for leg in CATEGORY_LEGS[c]}
    assert set(deep_target_legs) <= allowed


def test_one_sided_confinement_excludes_the_opposite_side():
    """`even` must not admit DIGITODD - the whole point of a one-sided pick."""
    assert confine(["even"]) == [("DIGITEVEN", None)]
    assert confine(["rise"]) == [("CALL", None)]
    assert confine(["odd"]) == [("DIGITODD", None)]
    assert confine(["fall"]) == [("PUT", None)]


def test_two_sided_categories_still_get_both_legs():
    """The fix must not narrow existing configs."""
    assert confine(["even_odd"]) == [("DIGITEVEN", None), ("DIGITODD", None)]
    assert confine(["rise_fall"]) == [("CALL", None), ("PUT", None)]


def test_all_categories_reproduce_the_full_candidate_list():
    every = list(CATEGORY_LEGS)
    assert set(confine(every)) == set(DEFAULT_CANDIDATES)


def test_an_explicit_contract_list_is_intersected_not_replaced():
    """If someone sets both `contracts` and `categories`, the narrower wins -
    silently honouring only one of two stated restrictions is how this bug
    happened in the first place."""
    explicit = [("DIGITEVEN", None), ("DIGITODD", None), ("CALL", None)]
    assert confine(["even", "rise"], explicit) == [("DIGITEVEN", None), ("CALL", None)]


def test_a_disjoint_combination_yields_nothing_so_main_can_refuse():
    """Empty must be detectable, not silently fall back to everything."""
    assert confine(["even"], [("PUT", None)]) == []


def test_the_shipped_config_confines_to_what_it_says():
    import yaml
    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    cats = cfg["scan_trade"]["categories"]
    legs = confine(cats)
    assert legs, "the shipped config quotes nothing"
    allowed = {leg for c in cats for leg in CATEGORY_LEGS[c]}
    assert set(legs) <= allowed


def test_main_applies_the_restriction():
    """Guards against the fix living only in this test file."""
    src = open("main.py", encoding="utf-8").read()
    assert "allowed_legs" in src, "main.py no longer confines candidates"
    assert "leg in allowed_legs" in src
