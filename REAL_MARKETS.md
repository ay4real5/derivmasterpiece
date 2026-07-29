# Real markets: the edge is real, and it is the same size as the fee

Follow-up to [TICK_ANALYSIS.md](TICK_ANALYSIS.md), which settled that the
synthetic indices are a pure random walk. The obvious next move was real
markets, where volatility clustering is the most reproduced result in
empirical finance. It is present here, it is large, and Deriv has priced the
one instrument that could express it just above what it is worth.

**Verdict: no trade. But the margin of failure is one standard error, not a
factor of seven** — which is a completely different situation from the
synthetics and worth stating precisely rather than rounding to "no".

---

## 1. The instrument census decides most of it

All 43 real-market symbols, `contracts_for` on every one, zero errors.

| category | symbols | shortest duration | pays for volatility? |
|---|---|---|---|
| callput (Rise/Fall) | 41 | **15m** | no — pays on direction |
| touchnotouch | 28 | **1d** (7d on indices) | **yes** |
| endsinout | 28 | 1d | yes |
| staysinout | 28 | 1d | yes |
| higherlower | 28 | 1d | yes |
| multiplier | 18 | 0 | no — EV is −commission at any volatility |
| vanilla / turbos | **0** | — | — |

Two facts fall straight out:

- **Nothing volatility-sensitive trades intraday on any real market.** The
  floor is one day. No vanillas, no turbos anywhere.
- **Crypto sells multipliers and nothing else.** cryBTCUSD and cryETHUSD have
  no binaries at all. Since a multiplier's expected value is minus the
  commission *regardless of volatility*, a volatility forecast is worth
  exactly zero there. The market with the most attractive dynamics has no
  instrument that pays for knowing them.

So the horizon is not a choice. It is one day, because that is the shortest
volatility contract that exists on a market with real volatility dynamics.

---

## 2. Day-ahead volatility persistence is real

Measured at the one-day horizon — the horizon the contract settles over, which
is the whole point. Parkinson high–low estimator on ~260 daily candles
(Deriv serves exactly one year and no more).

| symbol | r | p | half 1 | half 2 |
|---|---|---|---|---|
| **frxXAGUSD** | **+0.607** | 2.3e-22 | +0.598 | +0.433 |
| **frxXAUUSD** | **+0.569** | 5.1e-20 | +0.433 | +0.521 |
| frxUSDJPY | +0.446 | 5.8e-13 | +0.202 | +0.479 |
| frxEURJPY | +0.390 | 3.0e-10 | +0.319 | +0.413 |
| frxAUDJPY | +0.385 | 4.9e-10 | +0.333 | +0.425 |
| frxGBPJPY | +0.352 | 1.4e-08 | +0.337 | +0.346 |
| frxEURUSD | +0.271 | 1.2e-05 | +0.163 | +0.325 |
| … 9 more, all positive | | | | |

**13 of 16 survive Bonferroni** (p < 6e-04) against 0.16 expected by chance.
Compare the synthetics: 0 of 260 survived.

### Three checks, because a correlation on its own is not a measurement

**An independent estimator agrees.** Realised volatility built from *hourly*
returns shares no input with the daily high/low, so it cannot share an
artefact:

| | Parkinson (daily H/L) | realised vol (hourly) |
|---|---|---|
| frxXAUUSD | +0.569 | **+0.618** |
| frxXAGUSD | +0.607 | **+0.681** |
| frxUSDJPY | +0.446 | +0.466 |
| frxEURUSD | +0.271 | +0.356 |

**Split-half holds.** Both halves agree in sign and size on 12 of 16.

**The feed is clean.** 1 duplicated candle in 259; day-gaps are exactly 1 and
3, which is weekends.

> **A correction to something I said earlier.** I previously quoted gold
> persistence of +0.56 as forecastability. That number came from a long sample
> and grew with the window (−0.069 at 30 days → +0.548 at 316), which is the
> signature of slow regime drift, not a day-ahead forecast. The value above is
> re-measured at the one-day horizon with a p-value, a sample size and a
> split-half behind it. A test for exactly this failure mode is in
> `tests/test_vol_forecast.py::test_split_half_catches_drift_masquerading_as_persistence`.

---

## 3. What the instrument costs — measured, model-free

ONETOUCH and NOTOUCH at the same barrier are complementary: exactly one pays.
So `stake/payout_a + stake/payout_b − 1` **is** the house margin, with no
volatility model, no distribution, no barrier mathematics. It cannot be
argued with.

| instrument | median margin |
|---|---|
| Rise/Fall 1d | **7.13%** |
| Rise/Fall 15m | 16.64% |
| Touch/No Touch 1d | **22.92%** |
| Ends In/Out 1d | 23.79% |

And it gets worse as the barrier widens — measured on silver:

| barrier (× daily vol) | 0.15 | 0.20 | 0.32 | 0.50 | 0.80 |
|---|---|---|---|---|---|
| margin | **16.40%** | 18.50% | 23.38% | 29.91% | 38.44% |

Deriv refuses to quote tighter than ~0.15× — below that the payout falls under
their minimum. So **16.40% is the cheapest volatility exposure available on
the best symbol.**

The cheap instrument, Rise/Fall at 7.13%, pays on *direction* — and direction
has no measurable predictability anywhere in this project.

---

## 4. Is the forecast already in the price?

Deriv's implied volatility, backed out of the quotes, tracks realised
volatility across symbols at **r = +0.93** over a 10.9× spread. The pricer is
plainly volatility-aware at the symbol level.

Whether it updates *through time* — which is what I would be trading — I could
not resolve. Dividing out the symbol-level effect gives corr = −0.078 with
recent volatility, but **n = 12 and p = 0.79**: this test could only have
detected |corr| > 0.74, so it is uninformative, not evidence of a blind
pricer. My implied-vol inversion is also unstable across barriers (silver
spans 5.1×, a volatility smile), which attenuates it further.

**I therefore assumed the pricer is completely blind to time-varying
volatility** — the most generous assumption available — and asked whether the
edge clears the fee even then.

---

## 5. The arithmetic

Simulation in `pricebot/vol_trade.py`. Every assumption favours us: Deriv
prices at the stationary volatility distribution, we are handed the *true*
AR(1) coefficient, and there is no spread, slippage or execution delay. It is
an upper bound.

> **This simulation was wrong the first time and said so loudly.** It priced
> the fair quote at the *median* volatility instead of as the *expectation* of
> the touch probability over the volatility distribution. Those differ by
> Jensen's inequality, and at sd = 0.57 in logs they differ enormously — the
> bug invented a 13% edge from a zero-margin, zero-skill setup. It was caught
> only by two checks with answers known in advance: no margin and no skill
> must return exactly 0, and margin `m` with no skill must return exactly
> `−m/(1+m)`. Both are now tests.

**At the tightest barrier Deriv will quote:**

| symbol | edge can carry | margin charged | gap |
|---|---|---|---|
| frxXAGUSD | 16.18% | 16.40% | **−0.22%** |
| frxXAUUSD | 14.29% | 15.21% | −0.92% |
| frxUSDJPY | 10.66% | 11.43% | −0.77% |

Net expected return, silver, at the real fee: **−0.10% per trade.**

Widening the barrier does not help — the fee rises faster than the edge:

| barrier | 0.15 | 0.20 | 0.32 | 0.50 | 0.80 |
|---|---|---|---|---|---|
| silver edge carries | 16.2% | 16.8% | 18.4% | 20.3% | 22.9% |
| silver fee charged | 16.4% | 18.5% | 23.4% | 29.9% | 38.4% |
| gap | −0.2 | −1.7 | −5.0 | −9.6 | −15.5 |

### The result sits inside one standard error

φ for silver is 0.607 ± 0.062 on n = 257:

| φ | edge carries | vs the 16.40% fee |
|---|---|---|
| 0.482 (−2 SE) | 12.93% | −3.47% |
| 0.544 (−1 SE) | 14.59% | −1.81% |
| **0.607 (point)** | **16.26%** | **−0.14%** |
| 0.669 (+1 SE) | 17.93% | +1.53% |
| 0.732 (+2 SE) | 19.63% | +3.23% |

**One year of data cannot tell whether this trade wins or loses.** The point
estimate is negative. Every modelling choice was tilted favourably. And the
fee is exact while the edge is estimated — an asymmetry that matters, because
you pay the fee on every trade whether or not φ turned out to be what you
thought.

---

## What I'd conclude

**Don't trade this.** Not because it is provably negative — it isn't, the way
the synthetics were — but because the honest reading is a coin flip on
whether the edge exists at all, financed by a fee that is certain. The
synthetics failed by a factor of 7 on the statistics; this fails by 0.2
percentage points on the economics, which means the answer turns entirely on
assumptions I made in our favour and cannot verify.

Two things would actually resolve it, and neither is a strategy:

1. **Does Deriv's quote move with recent volatility?** This is the single
   highest-value unknown, and section 4 could not answer it. It needs Touch/No
   Touch quotes collected daily on silver and gold for a few weeks, correlated
   against realised volatility. That is a small, cheap, decisive experiment —
   and if the quote *does* track recent volatility, the −0.22% gap becomes far
   worse and the question is closed for good.
2. **More than one year of daily data**, which Deriv will not serve. An
   outside source would cut the ±0.062 on φ that the whole result hinges on.

What is now firmly established across both reports: on synthetics no strategy
can work, and on real markets the only genuine edge found is priced at
approximately its own value. The recurring pattern is not that the strategies
have been bad — it is that **frequency is the enemy**. Every fee measured in
this project is charged per contract, and the cheapest thing available
anywhere is Rise/Fall at one day for 7.13%, on a signal that does not exist.
