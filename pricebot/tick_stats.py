"""Does the tick generator deviate from a random walk? Tested properly.

The question this answers is the one that decides whether ANY strategy can
work: if the price process is a pure random walk, no rule computed from past
prices can predict the next one, and the house edge is unbeatable by
arithmetic rather than by opinion. If it deviates measurably, that deviation
is the only thing worth building on.

THE PART MOST SUCH ANALYSES GET WRONG, and the reason this module exists
rather than a handful of ad-hoc checks: MULTIPLE COMPARISONS. Run
autocorrelation at 20 lags across 5 indices and that is 100 tests. At p<0.01
roughly ONE will look significant on pure noise, by construction. Add streak
tests, volatility tests and cross-correlations and the expected count of
false discoveries climbs past two or three. Reporting the winners of that
search as findings is how people convince themselves a random generator has
a pattern.

So every p-value here is reported alongside a Bonferroni-corrected threshold,
and `summarise` states plainly how many "significant" results pure noise
would have produced. A finding only counts if it survives that.

All tests are two-sided and use standard asymptotics:

  autocorrelation   under white noise r_k ~ N(0, 1/n), so z = r*sqrt(n)
  streak reversal   binomial proportion vs 0.5
  Ljung-Box         Q = n(n+2) sum r_k^2/(n-k)  ~  chi-square(h)
  runs test         normal approximation to the number of sign runs
  volatility        autocorrelation of |returns|, same asymptotics

Each is validated in the tests against data with a KNOWN answer - synthetic
white noise must come back null, and a deliberately autocorrelated series
must be detected - because a statistical test nobody has checked is just an
opinion with a p-value attached.
"""
from __future__ import annotations

import math
import statistics
from typing import Any, Sequence

# Two-sided z at which we would call something real before correction.
ALPHA = 0.01


def _norm_sf(z: float) -> float:
    """Two-sided tail probability of the standard normal."""
    return math.erfc(abs(z) / math.sqrt(2.0))


def _chi2_sf(x: float, df: int) -> float:
    """Upper tail of chi-square, via a regularised incomplete gamma.

    Series/continued-fraction expansion rather than a table so the p-values
    are exact enough to trust at the small end.
    """
    if x <= 0 or df <= 0:
        return 1.0
    k = df / 2.0
    x2 = x / 2.0
    if x2 < k + 1:
        # series expansion for the lower incomplete gamma
        term = 1.0 / k
        total = term
        n = 1
        while n < 500:
            term *= x2 / (k + n)
            total += term
            if abs(term) < abs(total) * 1e-15:
                break
            n += 1
        lower = total * math.exp(-x2 + k * math.log(x2) - math.lgamma(k))
        return max(0.0, min(1.0, 1.0 - lower))
    # continued fraction for the upper incomplete gamma
    tiny = 1e-300
    b = x2 + 1.0 - k
    c = 1.0 / tiny
    d = 1.0 / b if b != 0 else 1.0 / tiny
    h = d
    for i in range(1, 500):
        an = -i * (i - k)
        b += 2.0
        d = an * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + an / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-14:
            break
    return max(0.0, min(1.0, h * math.exp(-x2 + k * math.log(x2) - math.lgamma(k))))


def returns(prices: Sequence[float]) -> list[float]:
    """Log returns, skipping non-positive prices rather than raising."""
    out = []
    for a, b in zip(prices, prices[1:]):
        if a > 0 and b > 0:
            out.append(math.log(b / a))
    return out


def autocorrelation(series: Sequence[float], lag: int) -> dict[str, Any]:
    """Sample autocorrelation at `lag`, with a p-value against white noise."""
    n = len(series)
    if n <= lag + 2:
        return {"lag": lag, "r": 0.0, "z": 0.0, "p": 1.0, "n": n}
    mean = statistics.fmean(series)
    denom = sum((v - mean) ** 2 for v in series)
    if denom == 0:
        return {"lag": lag, "r": 0.0, "z": 0.0, "p": 1.0, "n": n}
    num = sum((series[i] - mean) * (series[i + lag] - mean)
              for i in range(n - lag))
    r = num / denom
    z = r * math.sqrt(n)
    return {"lag": lag, "r": r, "z": z, "p": _norm_sf(z), "n": n}


def ljung_box(series: Sequence[float], lags: int = 20) -> dict[str, Any]:
    """Joint test that ALL autocorrelations up to `lags` are zero.

    Worth more than the individual lags precisely because it is ONE test
    rather than twenty - it cannot be gamed by picking the best lag.
    """
    n = len(series)
    if n <= lags + 2:
        return {"Q": 0.0, "df": lags, "p": 1.0, "n": n}
    q = 0.0
    for k in range(1, lags + 1):
        r = autocorrelation(series, k)["r"]
        q += (r * r) / (n - k)
    q *= n * (n + 2)
    return {"Q": q, "df": lags, "p": _chi2_sf(q, lags), "n": n}


def streak_continuation(directions: Sequence[int], streak: int) -> dict[str, Any]:
    """After `streak` moves the same way, how often does the next continue?

    50% means no memory. Above means momentum, below means mean reversion.
    Both would be exploitable; neither is expected from a clean generator.
    """
    if streak < 1:
        raise ValueError("streak must be >= 1")
    # Every position where the previous `streak` moves ran the same way, and
    # a decisive move followed. Overlapping windows are counted, which
    # inflates the effective sample slightly; that makes the test if anything
    # more likely to cry significance, so it errs toward finding a pattern
    # rather than missing one.
    cont, total = 0, 0
    i = 0
    n = len(directions)
    while i + streak < n:
        window = directions[i:i + streak]
        if window and all(d == window[0] and d != 0 for d in window):
            nxt = directions[i + streak]
            if nxt != 0:
                total += 1
                if nxt == window[0]:
                    cont += 1
        i += 1
    if total == 0:
        return {"streak": streak, "n": 0, "p_continue": 0.5, "z": 0.0, "p": 1.0}
    p_hat = cont / total
    se = math.sqrt(0.25 / total)
    z = (p_hat - 0.5) / se if se > 0 else 0.0
    return {"streak": streak, "n": total, "p_continue": p_hat, "z": z,
            "p": _norm_sf(z)}


def runs_test(directions: Sequence[int]) -> dict[str, Any]:
    """Wald-Wolfowitz: is the sequence of ups and downs random?

    Detects clustering or alternation that lag-by-lag autocorrelation can
    miss, and it is a single test rather than a family.
    """
    seq = [d for d in directions if d != 0]
    n = len(seq)
    if n < 20:
        return {"runs": 0, "expected": 0.0, "z": 0.0, "p": 1.0, "n": n}
    n1 = sum(1 for d in seq if d > 0)
    n2 = n - n1
    if n1 == 0 or n2 == 0:
        return {"runs": 1, "expected": 1.0, "z": 0.0, "p": 1.0, "n": n}
    runs = 1 + sum(1 for a, b in zip(seq, seq[1:]) if a != b)
    exp = 2.0 * n1 * n2 / n + 1.0
    var = (2.0 * n1 * n2 * (2.0 * n1 * n2 - n)) / (n * n * (n - 1))
    if var <= 0:
        return {"runs": runs, "expected": exp, "z": 0.0, "p": 1.0, "n": n}
    z = (runs - exp) / math.sqrt(var)
    return {"runs": runs, "expected": exp, "z": z, "p": _norm_sf(z), "n": n}


def volatility_clustering(rets: Sequence[float], lag: int = 1) -> dict[str, Any]:
    """Autocorrelation of ABSOLUTE returns - the standard GARCH signature.

    Real markets show this strongly. A generator drawing independent moves
    of constant scale should not.
    """
    out = autocorrelation([abs(r) for r in rets], lag)
    out["measure"] = "abs_return_autocorr"
    return out


def cross_correlation(a: Sequence[float], b: Sequence[float],
                      lag: int = 0) -> dict[str, Any]:
    """Correlation between two series, with `b` shifted forward by `lag`.

    A non-zero result at positive lag means the first index LEADS the
    second, which would be directly tradeable.
    """
    if lag > 0:
        a2, b2 = a[:-lag], b[lag:]
    elif lag < 0:
        a2, b2 = a[-lag:], b[:lag]
    else:
        a2, b2 = a, b
    n = min(len(a2), len(b2))
    if n < 30:
        return {"lag": lag, "r": 0.0, "z": 0.0, "p": 1.0, "n": n}
    a2, b2 = a2[:n], b2[:n]
    ma, mb = statistics.fmean(a2), statistics.fmean(b2)
    sa = math.sqrt(sum((v - ma) ** 2 for v in a2))
    sb = math.sqrt(sum((v - mb) ** 2 for v in b2))
    if sa == 0 or sb == 0:
        return {"lag": lag, "r": 0.0, "z": 0.0, "p": 1.0, "n": n}
    r = sum((x - ma) * (y - mb) for x, y in zip(a2, b2)) / (sa * sb)
    z = r * math.sqrt(n)
    return {"lag": lag, "r": r, "z": z, "p": _norm_sf(z), "n": n}


def hourly_direction(epochs: Sequence[int], directions: Sequence[int]) -> dict[str, Any]:
    """Does the up-rate differ by hour of the UTC day?

    Real markets do this loudly - the London and New York opens are visible
    in any intraday series. A generator running the same draw around the
    clock should not, and if it DID it would be the easiest edge to trade:
    a fixed clock rule with no state to estimate.

    One chi-square across all 24 buckets rather than 24 separate binomial
    tests, so the answer cannot be manufactured by reporting the best hour.
    """
    up = [0] * 24
    total = [0] * 24
    for e, d in zip(epochs, directions):
        if d == 0:
            continue
        h = int((e // 3600) % 24)
        total[h] += 1
        if d > 0:
            up[h] += 1
    n = sum(total)
    if n < 240:
        return {"chi2": 0.0, "df": 0, "p": 1.0, "n": n, "hours": []}
    # Expected up-count per hour under the null that every hour shares one
    # overall rate; using the POOLED rate rather than 0.5 means this tests
    # for a difference BETWEEN hours, not for drift, which is a separate
    # question and separately answered by the runs test.
    rate = sum(up) / n
    chi2, df = 0.0, 0
    hours = []
    for h in range(24):
        if total[h] < 10:
            continue
        df += 1
        exp_up = total[h] * rate
        exp_dn = total[h] * (1.0 - rate)
        if exp_up <= 0 or exp_dn <= 0:
            continue
        dn = total[h] - up[h]
        chi2 += (up[h] - exp_up) ** 2 / exp_up + (dn - exp_dn) ** 2 / exp_dn
        hours.append({"hour": h, "n": total[h], "up_rate": up[h] / total[h]})
    df = max(0, df - 1)
    return {"chi2": chi2, "df": df, "p": _chi2_sf(chi2, df) if df else 1.0,
            "n": n, "rate": rate, "hours": hours}


def hourly_volatility(epochs: Sequence[int], rets: Sequence[float]) -> dict[str, Any]:
    """Does move SIZE vary by hour of the UTC day?

    Separate from direction and far more likely to be real: even a synthetic
    could plausibly be scheduled. It would not by itself give a directional
    edge, but it would say when a barrier contract is mispriced against a
    flat-volatility assumption, so it is worth its own test.

    Bartlett's test for equal variances, which is a single statistic across
    all hours for the same reason as above.
    """
    buckets: dict[int, list[float]] = {}
    for e, r in zip(epochs, rets):
        buckets.setdefault(int((e // 3600) % 24), []).append(r)

    groups = [v for v in buckets.values() if len(v) >= 30]
    k = len(groups)
    if k < 2:
        return {"stat": 0.0, "df": 0, "p": 1.0, "k": k, "hours": []}
    ns = [len(g) for g in groups]
    n = sum(ns)
    variances = [statistics.variance(g) for g in groups]
    if any(v <= 0 for v in variances):
        return {"stat": 0.0, "df": 0, "p": 1.0, "k": k, "hours": []}
    pooled = sum((ni - 1) * v for ni, v in zip(ns, variances)) / (n - k)
    num = (n - k) * math.log(pooled) - sum((ni - 1) * math.log(v)
                                           for ni, v in zip(ns, variances))
    corr = 1.0 + (sum(1.0 / (ni - 1) for ni in ns) - 1.0 / (n - k)) / (3.0 * (k - 1))
    stat = num / corr
    hours = [{"hour": h, "n": len(v), "sd": statistics.pstdev(v)}
             for h, v in sorted(buckets.items()) if len(v) >= 30]
    return {"stat": stat, "df": k - 1, "p": _chi2_sf(stat, k - 1), "k": k,
            "hours": hours}


def summarise(results: Sequence[dict[str, Any]], alpha: float = ALPHA) -> dict[str, Any]:
    """Count the hits, and say how many pure noise would have produced.

    This is the whole point. With `m` tests at threshold `alpha`, noise
    yields `m * alpha` apparent discoveries on average. If the observed
    count is not clearly above that, the honest reading is that nothing was
    found - however exciting the individual p-values look.
    """
    m = len(results)
    if m == 0:
        return {"tests": 0, "hits": 0, "expected_by_chance": 0.0,
                "bonferroni": alpha, "survivors": [], "verdict": "no tests run"}
    corrected = alpha / m
    hits = [r for r in results if r.get("p", 1.0) < alpha]
    survivors = [r for r in results if r.get("p", 1.0) < corrected]
    expected = m * alpha
    if survivors:
        verdict = (f"{len(survivors)} result(s) survive Bonferroni "
                   f"(p < {corrected:.2e}) - worth investigating")
    elif len(hits) > expected * 3:
        verdict = (f"{len(hits)} hits vs {expected:.1f} expected by chance - "
                   f"suggestive, none individually decisive")
    else:
        verdict = (f"{len(hits)} hits vs {expected:.1f} expected by chance - "
                   f"consistent with pure noise")
    return {"tests": m, "hits": len(hits), "expected_by_chance": expected,
            "bonferroni": corrected, "survivors": survivors, "verdict": verdict}
