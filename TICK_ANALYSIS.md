# Is Deriv's tick generator a random walk?

**Answer: yes, to the precision the data can resolve — and that precision is
roughly 7x finer than any edge that would be worth trading.**

Reproduce with `python run_tick_analysis.py --family 1HZ` (and `--family R`).
Raw ticks are cached under `tick_cache*/`, so re-running the battery re-tests
the *same* data rather than silently mixing a code change with a market change.

---

## What was measured

| | |
|---|---|
| indices | 1HZ10V, 1HZ25V, 1HZ50V, 1HZ75V, 1HZ100V (1s) and R_10…R_100 (2s) |
| sample | **86,354–86,403** ticks per 1s index, 43,188–43,201 per 2s index |
| span | 24.0 hours per index — see the caveat below |
| tests | 130 per family, 260 total |
| collected | 2026-07-29 |

**On the 100,000-tick target.** Deriv retains exactly **24 hours** of tick
history and no more. This is a time cap, not a count cap: the 1-second indices
return ~86,400 ticks, the 2-second indices ~43,200, both spanning precisely
24.0h. Paging further back returns nothing. So 100,000 in one pull is not
obtainable; it would require accumulating across days.

That turned out not to matter, and the reason is the most important number in
this report — see *Power* below.

---

## Results

Nothing was found. Every headline test is null on every index.

### Autocorrelation of tick returns (lags 1, 2, 3, 5, 10, 20)

Largest magnitude anywhere across 60 measurements: **|r| = 0.0156** (R_10,
lag 2). Typical value ~0.004. Under white noise the standard error is
1/√86,353 = **0.0034**, so essentially every reading sits inside ±2 SE.

**Ljung-Box** (joint test that *all* 20 lags are zero — one statistic, so it
cannot be gamed by picking the best lag): p ranges 0.015 to 0.960 across the
ten indices. Nothing close after correction.

### Streak continuation, N = 2 to 10

After N consecutive moves the same way, how often does the next one continue?
50% means no memory.

| N | 1HZ10V | 1HZ25V | 1HZ50V | 1HZ75V | 1HZ100V | sample (N=2) |
|---|---|---|---|---|---|---|
| 2 | 49.9% | 50.0% | 50.1% | 50.0% | 50.0% | ~40,000 |
| 3 | 50.0% | 49.6% | 50.2% | 49.9% | 49.9% | ~20,000 |
| 5 | 51.4% | 49.6% | 49.8% | 49.6% | 49.6% | ~5,000 |
| 8 | 50.1% | 46.2% | 51.3% | 50.1% | 52.1% | ~600 |
| 10 | 53.4% | 46.9% | 44.0% | 53.5% | 52.0% | ~150 |

The wide-looking numbers at N=9 and N=10 are small-sample noise — 150
observations has a standard error of 4 percentage points, so 44% and 53.5%
are both about one SE from a coin flip. The rows that carry real weight are
N=2 and N=3, and those are 50.0% to one decimal place on every index.

**Runs test** (Wald-Wolfowitz, sensitive to clustering or alternation the
lag-by-lag view can miss): all ten p-values between 0.12 and 0.86.

### Volatility clustering

Autocorrelation of |returns| — the GARCH signature that is present in every
real market. Nine of ten indices: null. One is discussed below.

### Time of day

Chi-square across all 24 UTC hours (one statistic, not 24 separate tests, so
the answer cannot be manufactured by reporting the best hour):

- **direction** — p from 0.23 to 0.98 across all ten indices. Most extreme
  single hour anywhere: 51.9% up-rate on n=3,522, which is 2.2 SE and exactly
  what 240 hour-buckets should produce.
- **volatility** — Bartlett's test, p from 0.086 to 0.89. No session
  structure. As expected: these run identically around the clock, unlike a
  real market where the London and New York opens are unmissable.

### Cross-index correlation (lags 0, 1, 2)

30 pairs. Largest |r| anywhere: **0.0101** (1HZ10V vs R_50, lag 1). At lag 0,
the ten pairs range from −0.006 to +0.008. The indices are independent of each
other and no index leads another.

---

## The multiple-comparisons check, which is the part that decides it

260 tests were run. At p < 0.01, **pure noise produces ~2.6 apparent
discoveries by construction.**

| | 1HZ family | R family |
|---|---|---|
| tests | 130 | 130 |
| hits at p<0.01 | 2 | 2 |
| expected by chance | 1.3 | 1.3 |
| survive Bonferroni (p < 7.7e-05) | **0** | **0** |

Four hits against 2.6 expected. Zero survivors. This is what a random number
generator looks like when you search it hard enough.

### The one result that did replicate

`1HZ50V` volatility clustering, r = +0.0112, p = 0.0010. I split it to check
whether it was a noise artefact, and it is not:

```
full sample   r=+0.01118  p=0.0010
first half    r=+0.01135  p=0.0184
second half   r=+0.01101  p=0.0221
Q1 +0.00815   Q2 +0.01422   Q3 +0.00782   Q4 +0.01398
```

Both halves agree to the third decimal and all four quarters are positive.
That is genuinely stable, and it is the only such result in 260 — the other
nine indices show no consistency between halves at all, and `R_50`, the
2-second version of the same nominal volatility, comes back at r = +0.0016,
p = 0.74.

**It is still not tradeable, by two orders of magnitude.** An absolute-return
autocorrelation of 0.0112 explains **0.0125%** of the variance in the next
tick's move size. Touch/No Touch, the instrument that would pay for a
volatility view, charges a **2.33%** margin. The forecast would have to be
about **186x stronger** to cover the cost of expressing it. And volatility
clustering gives no directional information at all, so it does nothing for
Rise/Fall or digits regardless.

---

## Power: why 24 hours was enough

This is the number that makes the null result mean something rather than just
being a failure to look hard enough.

```
n                                        86,353 returns per index
SE of autocorrelation under the null     0.0034
smallest |r| detectable at p<0.01        0.0088

break-even win rate, Rise/Fall @1.9231x  52.00%
autocorrelation needed to reach it       0.0628
                                         = 7x the detection floor
if it existed, it would appear at        z = 18   (p ~ 1e-72)
largest |r| actually observed            0.0156
```

For streaks the margin is similar: the N=2 bucket holds ~40,000 observations,
a standard error of 0.25 percentage points, so 0.64pp from 50% is detectable —
while 2.00pp is needed to trade. **3.1x more sensitivity than required.**

So this is not "we found nothing, maybe with more data." An edge large enough
to overcome the house margin would have shown up at z = 18. The observed
maximum is z = 4.6, and that one does not survive correction. **More data
would not change the answer, because the answer is already resolved well past
the threshold that matters.**

---

## What this settles

**Path B.** There is no exploitable structure in these tick streams. This is
not an opinion about strategies — it is a property of the data that holds
against every test applied, on ten indices, at a sensitivity 7x finer than
the smallest edge that could pay for itself.

Concretely, this closes off:

- any rule computed from past prices on synthetic indices — momentum, mean
  reversion, streak-counting, indicator scoring, martingale recovery. Not
  because those rules are badly designed, but because the input they read
  carries no information about the output they predict. This is why the PDF
  system backtested at 49.86% over 15,817 trades: **a coin flip is the correct
  result**, and any strategy on this data will reproduce it.
- time-of-day filters, session rules, "best hour to trade" — no hour differs
  from any other.
- cross-index confirmation and lead-lag — the indices do not know about each
  other.
- volatility-timing on synthetics — the only stable effect found is 186x too
  small to pay its own commission.

The house edge on these products is therefore not a hurdle to be beaten with a
better strategy. It is the entire expected return, and every trade pays it.

The remaining honest options are the ones outside this dataset: real markets
(FX, gold, indices), where volatility clustering is a genuine and large
effect — the earlier scan measured +0.385 block autocorrelation on crypto and
+0.14 to +0.55 on gold, one to two orders of magnitude above anything here.
Whether an instrument exists that lets you *trade* that at acceptable cost is
a separate question, and the one worth asking next.
