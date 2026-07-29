# Bybit: better venue, and the directional edge was my own small-sample error

Third report, after [TICK_ANALYSIS.md](TICK_ANALYSIS.md) (Deriv synthetics are
a random walk) and [REAL_MARKETS.md](REAL_MARKETS.md) (real edge, priced at its
own value).

**Verdict: Bybit is a genuinely better venue and the volatility edge there is
overwhelming — but there is no tradeable directional edge, and my first pass
said there was. That was wrong and this documents why.**

---

## A correction, up front

On 4,000 hourly bars (167 days) I measured the mean-reversion signal's gross
return as positive on all six symbols and reported that "the edge is real and
roughly half the fee."

On 22,000 bars (917 days) of the same data, same code:

| symbol | 167 days | 917 days |
|---|---|---|
| BTCUSDT | +0.0874% (t=2.45) | +0.0150% (t=1.09) |
| ETHUSDT | +0.0529% (t=1.02) | +0.0094% (t=0.65) |
| SOLUSDT | +0.0867% (t=1.66) | **−0.0238%** (t=−0.68) |
| XRPUSDT | +0.0568% (t=1.24) | +0.0166% (t=1.10) |
| BNBUSDT | +0.0286% (t=0.58) | +0.0078% (t=0.44) |
| DOGEUSDT | +0.0112% (t=0.37) | **−0.0614%** (t=−1.14) |

Pooled over 8,143 out-of-sample trades: **+0.0062% ± 0.0081%, t = +0.77.**
Indistinguishable from zero.

**What I got wrong was not the arithmetic, it was the inference.** The t-stats
on the short sample were 0.37 to 2.45 — five of six not significant. I leaned
on "positive on all six symbols" as if that were the evidence. Six independent
coin flips land all-positive about one time in 64: suggestive, and nowhere near
enough to call an edge real. I stated it more firmly than the numbers allowed.

Two independent confirmations that it was noise:

- **Parameter instability.** The best streak length moves when the training
  window moves — SOL 4→5, ETH 4→3, DOGE 3→5. A real parameter does not drift
  like that; a fitted one does.
- **The mechanism is absent** (below).

---

## The volatility filter: the idea, and why it fails

The reasoning was sound. Fees are a **fixed** rate, so if the gross edge scales
with the size of the move, filtering to high-volatility bars should let the same
edge fight a proportionally smaller fee. And volatility is strongly
forecastable, so the filter is buildable.

Pooled out-of-sample trades, split into quintiles by causal volatility forecast:

| quintile | trades | gross/trade | SE | t |
|---|---|---|---|---|
| Q1 (calmest) | 1,793 | +0.0007% | 0.0101% | 0.07 |
| Q2 | 1,610 | +0.0101% | 0.0135% | 0.74 |
| Q3 | 1,515 | +0.0342% | 0.0161% | 2.12 |
| Q4 | 1,503 | −0.0118% | 0.0188% | −0.63 |
| Q5 (wildest) | 1,722 | −0.0005% | 0.0271% | −0.02 |

**Q5 − Q1 = −0.0012%, SE 0.0289%, t = −0.04, p = 0.97.**

Not weakly present — flatly absent. The gross edge does not scale with
volatility, so no threshold anywhere can rescue it. Q3's t = 2.12 is one cell
out of five and does not survive correction.

---

## What genuinely holds up

**Volatility clustering on crypto is enormous**, and nothing above weakens it:

| horizon | r range | survivors |
|---|---|---|
| daily | +0.477 to +0.525 | 6/6 |
| 4-hour | +0.480 to +0.633 | 6/6 |
| 1-hour | +0.521 to +0.593 | 6/6 |

**18 of 18 survive Bonferroni**, split-half consistent on every symbol at every
horizon, p-values down to numerical underflow, SE of 0.016–0.024 against the
0.062 that made the Deriv silver result unresolvable.

**Bybit is structurally the better venue**, independent of any strategy:

| | Deriv | Bybit |
|---|---|---|
| cost per round trip | 3.83% (Rise/Fall), 16.4% (vol) | **~0.11%** |
| counterparty | Deriv, who sets your price | other traders; exchange matches |
| daily history | 260 candles | **2,318** |
| market data API key | required | **none — fully public** |

---

## Where this actually leaves things

Across three venues and roughly 900 statistical tests, the pattern is
consistent and worth stating plainly:

- **Direction is not predictable** anywhere tested, at any horizon, on
  synthetics or real crypto, once fees are charged honestly.
- **Volatility is very predictable** on every real market — r = +0.48 to +0.68,
  never in doubt.
- **The instrument that pays for a volatility view is the bottleneck.** Deriv
  sells it at 16.4%, which is its whole value. Bybit perpetuals are cheap but
  linear, so they pay on direction and cannot express a volatility view at all.

That leaves exactly one untested route: **Bybit's BTC/ETH options.** Options are
the direct volatility instrument, and their mark implied volatility is on the
same public API used here. The question is a single measurement — *does Bybit's
option IV already track recent realised volatility?* — and the honest prior is
**yes**, because unlike Deriv this is a competitive market with professional
market makers whose job is exactly that.

It is worth measuring rather than assuming, and it is one script. But it should
be approached expecting to find the edge already priced, not expecting to find
it lying there.

---

## Reproducing

```bash
python fetch_bybit.py          # public API, no key, no account
python fetch_bybit_long.py     # 22,000 hourly bars per symbol
pytest tests/test_reversal_backtest.py -q
```

`pricebot/reversal_backtest.py` carries the guards this report depended on: the
volatility forecast is asserted causal (truncating the future must not change
any earlier value), bid-ask bounce is ruled out by magnitude rather than
assertion, and the central test asserts a genuine 50.5% edge going net-negative
on 0.1% bars — a positive gross edge and a 0% net win rate coexisting, which is
the failure mode a win-rate table hides.

None of those guards caught the error above, because it was not a coding error.
It was reading six weak positives as one strong result. The fix for that is
more data, which is why every number here is quoted with a t-statistic.
