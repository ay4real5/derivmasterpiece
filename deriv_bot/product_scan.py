"""What every product Deriv sells actually costs, measured without a model.

`contracts_for("R_10")` reports 65 contracts across 15 categories. This bot
has only ever traded two of them - digits and callput - because
`edge.py::_requests` is a hand-written list. Turbos, Vanillas, Touch/No
Touch, Multipliers, Accumulators and eight more have never been priced.

THE TRAP THIS MODULE EXISTS TO AVOID. For digits the win probability is exact
arithmetic: DIGITOVER:4 wins on five of ten equally likely digits. For every
price-based product it is not - a touch probability depends on a volatility
model. Feed in the wrong volatility and the computed "edge" comes out wrong,
possibly negative, which looks exactly like the loophole one is hunting for.
A fake edge is worse than no edge, because it gets traded.

So nothing here estimates a probability. Cost is measured only from
COMPLEMENTARY PAIRS, where the outcome is certain by construction: buy both
sides of a proposition where exactly one must win, size them so both
outcomes pay the same, and whatever is missing from the guaranteed payout is
the venue's cut. No assumption about volatility, drift, or anything else.

    cost% = (total staked - guaranteed payout) / total staked

WHAT THIS MEASURES, AND WHAT IT DOES NOT. Pair cost is not the same number
as a single contract's edge, and conflating the two would be a serious
mistake. On R_10, DIGITOVER:0 alone charges 1.30% and DIGITMATCH:0 alone
charges 16.67%, but the PAIR of them costs 3.09% - a stake-weighted blend,
because most of the money goes on the cheap leg. Single-leg edge answers
"what does this bet cost me?" and needs the true win probability; pair cost
answers "what does the venue charge to cover this proposition?" and needs no
model whatsoever.

For comparing products against each other, pair cost is the honest metric:
it is the only one available for Turbos or Touch/No Touch without inventing
a volatility model, and it is measured identically for every product.

The check that this can be trusted: applied to DIGITOVER:4 + DIGITUNDER:5 it
returns 2.33%, the figure already known from exact digit arithmetic. A method
that reproduces answers we can verify is one worth using where we cannot.
"""
from __future__ import annotations

from typing import Any, Iterable

# Pairs where exactly one side must win, so the combination is risk-free and
# its cost is pure venue margin. Ties matter: CALL/PUT both lose on an exact
# flat, so CALLE/PUTE (which include equality) are the honest callput pair.
COMPLEMENTARY: list[tuple[str, str, str]] = [
    ("digits", "DIGITEVEN", "DIGITODD"),
    ("digits", "DIGITOVER", "DIGITUNDER"),        # barriers b and b+1
    ("digits", "DIGITMATCH", "DIGITDIFF"),        # same barrier
    ("callputequal", "CALLE", "PUTE"),
    ("touchnotouch", "ONETOUCH", "NOTOUCH"),
    ("staysinout", "RANGE", "UPORDOWN"),
    ("endsinout", "EXPIRYRANGE", "EXPIRYMISS"),
    ("higherlower", "HIGHER", "LOWER"),
    ("asian", "ASIANU", "ASIAND"),
]

# Non-binary: no complementary pair, so cost is the long+short round trip -
# the market exposure cancels and what remains is spread plus commission.
ROUNDTRIP: list[tuple[str, str, str]] = [
    ("turbos", "TURBOSLONG", "TURBOSSHORT"),
    ("vanilla", "VANILLALONGCALL", "VANILLALONGPUT"),
    ("multiplier", "MULTUP", "MULTDOWN"),
]


def payout_ratio(quote: dict[str, Any]) -> float:
    """Payout per 1.0 staked. Both numbers come straight from the quote."""
    ask = float(quote["ask_price"])
    if ask <= 0:
        raise ValueError("ask_price must be positive")
    return float(quote["payout"]) / ask


def pair_weights(quote_a: dict[str, Any], quote_b: dict[str, Any]) -> tuple[float, float]:
    """Relative stakes that make both outcomes pay identically.

    Returns (stake_a, stake_b) normalised so stake_b == 1.0. With A paying
    1.097x and B paying 8.333x, far more goes on A - the point is that the
    result no longer depends on WHICH side wins, so what is left is the
    venue's cut and nothing else.
    """
    ra, rb = payout_ratio(quote_a), payout_ratio(quote_b)
    return rb / ra, 1.0


def pair_cost(quote_a: dict[str, Any], quote_b: dict[str, Any]) -> dict[str, Any]:
    """Guaranteed cost of covering both sides of a complementary pair.

    A positive `cost_pct` is the venue's margin. Zero or negative would be a
    mispricing - genuinely free money, and the one thing in this search that
    could be. Treat any such result as a bug in the pairing until it has been
    reproduced minutes apart.
    """
    a, b = pair_weights(quote_a, quote_b)
    ra = payout_ratio(quote_a)
    staked = a + b
    guaranteed = a * ra          # equals b * rb by construction
    return {
        "stake_a": a,
        "stake_b": b,
        "staked": staked,
        "guaranteed_payout": guaranteed,
        "profit": guaranteed - staked,
        "cost_pct": (staked - guaranteed) / staked * 100.0,
    }


def roundtrip_cost(long_quote: dict[str, Any], short_quote: dict[str, Any]) -> dict[str, Any]:
    """Cost of holding a long and a short of equal size on a non-binary
    product. The directional exposure cancels; the residual is the spread
    plus any commission, expressed against the total staked.

    Deriv reports these differently per product (some carry an explicit
    `commission`, some bury it in the barrier), so this reads whatever the
    quote provides and states which it used.
    """
    stake_l = float(long_quote["ask_price"])
    stake_s = float(short_quote["ask_price"])
    commission = float(long_quote.get("commission") or 0) + \
        float(short_quote.get("commission") or 0)
    staked = stake_l + stake_s
    return {
        "staked": staked,
        "commission": commission,
        "cost_pct": (commission / staked * 100.0) if staked else float("nan"),
        "basis": "explicit commission" if commission else "no commission field reported",
    }


def digit_pair_barriers(kind_a: str, kind_b: str, barrier: int) -> tuple[str | None, str | None]:
    """Barriers that make a digit pair genuinely complementary.

    DIGITOVER:b wins above b, so its complement is DIGITUNDER:b+1, not
    DIGITUNDER:b - those two overlap on nothing and leave digit b uncovered.
    """
    if (kind_a, kind_b) == ("DIGITOVER", "DIGITUNDER"):
        return str(barrier), str(barrier + 1)
    if (kind_a, kind_b) == ("DIGITMATCH", "DIGITDIFF"):
        return str(barrier), str(barrier)
    return None, None


def categories_available(contracts_for_response: dict[str, Any]) -> dict[str, set[str]]:
    """category -> contract types, from what the venue actually reports."""
    out: dict[str, set[str]] = {}
    for entry in contracts_for_response.get("contracts_for", {}).get("available", []):
        out.setdefault(entry.get("contract_category", "?"), set()).add(entry["contract_type"])
    return out


def pairs_to_price(available: dict[str, set[str]]) -> list[tuple[str, str, str]]:
    """Only the pairs this venue actually offers, so an unsupported product
    is skipped rather than producing a confusing error."""
    out = []
    for cat, a, b in COMPLEMENTARY:
        types = available.get(cat, set())
        if a in types and b in types:
            out.append((cat, a, b))
    return out
