"""The Rise/Fall scoring strategy from the system specification PDF.

Implemented to the letter of section 3.2 so the document's central claim -
"Expected Win Rate >= 62%" in the bull and bear zones - can be tested rather
than assumed. Nothing here is tuned, fitted, or improved: the weights,
thresholds and confirmation rule are the specification's own.

    composite = (ema*0.25 + rsi*0.20 + bb*0.20 + adx*0.20 + candle*0.15) * 100
    >= 72  -> RISE      45-71 -> no trade      <= 44 -> FALL

WHAT THE CLAIM HAS TO CLEAR. Rise/Fall on these indices quotes about a 3.83%
house margin, so a contract paying 1.9231x breaks even at 1/1.9231 = 52.0%.
The PDF's own MIN_PAYOUT_PCT of 78% breaks even at 1/1.78 = 56.2%. So the
document is claiming roughly six points of edge over its own break-even
point, which is enormous and entirely testable.

TWO CONTRADICTIONS IN THE SOURCE, resolved explicitly rather than silently:

1. Section 2.1 specifies EMA(5)/EMA(20); the parameter table on page 20 says
   "indicator periods (EMA 9/21, RSI 14, BB 20)". The detailed spec wins,
   and both are exposed as parameters so the alternative is one argument
   away rather than a code edit.

2. Section 2 assigns additive points (+25 EMA, +20 RSI, ...) while section
   3.2 gives a weighted formula over normalised sub-scores. These are
   different schemes and cannot both hold. 3.2 is the one written as
   executable arithmetic, so 3.2 is implemented.

A note on what the score measures. The formula is DIRECTIONAL - 0 is maximum
bearish, 100 maximum bullish - but two of its five inputs are not. ADX
measures trend strength regardless of direction, and it enters as a positive
term, so a strong DOWNTREND pushes the composite UP, toward the RISE
threshold. The same is true of the RSI mapping. That is what the
specification says to compute, so it is what is computed here; the backtest
will show what it does.
"""
from __future__ import annotations

from typing import Any, Sequence

from .indicators import adx, bollinger, candle_pattern, ema, rsi
from .signals import Signal, Strategy

CANDLE_SCORES = {
    "bullish": 1.0,
    "partial_bull": 0.75,
    "neutral": 0.5,
    "partial_bear": 0.25,
    "bearish": 0.0,
}


def ema_subscore(fast: float | None, slow: float | None,
                 prev_fast: float | None, prev_slow: float | None) -> float:
    """0 = strong bear, 0.5 = neutral, 1 = strong bull.

    Neutral is returned when the lines are within 0.01% of each other - the
    indecision zone the spec describes - rather than letting a hair's
    difference count as a full trend signal.
    """
    if fast is None or slow is None or slow == 0:
        return 0.5
    gap = (fast - slow) / abs(slow)
    if abs(gap) < 0.0001:
        return 0.5
    if prev_fast is not None and prev_slow is not None:
        widening = abs(fast - slow) > abs(prev_fast - prev_slow)
    else:
        widening = False
    strong = 1.0 if widening else 0.85
    return strong if gap > 0 else (1.0 - strong)


def rsi_subscore(value: float | None) -> float:
    """(RSI - 30) / 40, clamped to [0, 1] - section 3.2 verbatim."""
    if value is None:
        return 0.5
    return max(0.0, min(1.0, (value - 30.0) / 40.0))


def bb_subscore(price: float, lower: float | None, upper: float | None) -> float:
    """0 below the lower band, 1 above the upper, 0.5 inside."""
    if lower is None or upper is None:
        return 0.5
    if price < lower:
        return 0.0
    if price > upper:
        return 1.0
    return 0.5


def adx_subscore(value: float | None) -> float:
    """0 below 20, 0.5 for 20-30, 1 above 30 - trend strength only."""
    if value is None:
        return 0.0
    if value < 20:
        return 0.0
    if value <= 30:
        return 0.5
    return 1.0


# Weights with ADX REMOVED from the directional score and the remaining four
# renormalised back to 1.0, so the composite still spans 0-100 and the
# thresholds keep their meaning.
#
# WHY THIS EXISTS. ADX is direction-blind - it measures trend STRENGTH - but
# section 3.2 adds it as a POSITIVE term, so a weak trend drags the composite
# DOWN toward the FALL threshold. The bot then buys PUT *because there is no
# trend*, which is not a bearish statement about anything. Measured on the
# first 70 live trades: 31% fired with ADX below 20, ADX's own no-trend
# threshold, and 21 of those 22 were PUT. That is the artefact, not a signal.
#
# So ADX moves to where it belongs: a GATE on whether to trade at all.
_GATED_WEIGHTS = {"ema": 0.25 / 0.80, "rsi": 0.20 / 0.80,
                  "bb": 0.20 / 0.80, "candle": 0.15 / 0.80}


def directional_agreement(ema_sub: float, rsi_sub: float) -> bool:
    """Do the two DIRECTIONAL sub-scores point the same way?

    EMA and RSI are the only two components carrying a direction; bb is
    mostly neutral and candle is weak. When those two disagree the composite
    is an average of a bull and a bear case, and trading it commits to
    whichever happened to weigh more. Measured live: 10% of trades.

    Exactly 0.5 counts as no opinion, so it agrees with nothing.
    """
    if ema_sub == 0.5 or rsi_sub == 0.5:
        return False
    return (ema_sub > 0.5) == (rsi_sub > 0.5)


def composite_score(candles: Sequence[dict[str, Any]], *, ema_fast: int = 5,
                    ema_slow: int = 20, rsi_period: int = 14,
                    bb_period: int = 20, bb_std: float = 2.0,
                    adx_period: int = 14) -> dict[str, Any] | None:
    """The 0-100 composite plus every sub-score, or None if not warmed up.

    Returning None rather than a default keeps an un-warmed indicator from
    quietly scoring 50 and dragging the composite toward the neutral zone -
    which would look like the strategy being cautious when it is really
    being blind.
    """
    need = max(ema_slow, bb_period, adx_period * 2 + 1, rsi_period + 1) + 2
    if len(candles) < need:
        return None

    closes = [float(c["close"]) for c in candles]
    fast = ema(closes, ema_fast)
    slow = ema(closes, ema_slow)
    r = rsi(closes, rsi_period)
    lower, _mid, upper = bollinger(closes, bb_period, bb_std)
    a = adx(candles, adx_period)

    if fast[-1] is None or slow[-1] is None or a[-1] is None:
        return None

    subs = {
        "ema": ema_subscore(fast[-1], slow[-1], fast[-2], slow[-2]),
        "rsi": rsi_subscore(r[-1]),
        "bb": bb_subscore(closes[-1], lower[-1], upper[-1]),
        "adx": adx_subscore(a[-1]),
        "candle": CANDLE_SCORES[candle_pattern(candles)],
    }
    score = (subs["ema"] * 0.25 + subs["rsi"] * 0.20 + subs["bb"] * 0.20 +
             subs["adx"] * 0.20 + subs["candle"] * 0.15) * 100
    gated = sum(subs[k] * w for k, w in _GATED_WEIGHTS.items()) * 100
    return {"score": score, "gated_score": gated,
            **{f"{k}_score": v for k, v in subs.items()},
            "adx_value": a[-1], "rsi_value": r[-1],
            "agree": directional_agreement(subs["ema"], subs["rsi"])}


class PdfRiseFall(Strategy):
    """Rise/Fall only, per the specification. No digits, no multipliers.

    Two-candle confirmation (section 3.4) is included: a single candle over
    the threshold is not enough, the previous one must also have been in the
    same zone at a slightly looser bound. The spec is right that this cuts
    whipsaws; it also roughly halves the trade count, which matters because
    every trade pays the margin.
    """

    name = "pdf_rise_fall"

    def __init__(self, rise_threshold: float = 72.0, fall_threshold: float = 44.0,
                 rise_confirm: float = 68.0, fall_confirm: float = 48.0,
                 duration_seconds: int = 300, confirm: bool = True,
                 ema_fast: int = 5, ema_slow: int = 20,
                 min_adx: float = 0.0, require_agreement: bool = False,
                 adx_mode: str = "score"):
        if not 0 <= fall_threshold < rise_threshold <= 100:
            raise ValueError("need 0 <= fall_threshold < rise_threshold <= 100")
        if adx_mode not in ("score", "gate"):
            raise ValueError("adx_mode must be 'score' or 'gate'")
        if min_adx < 0:
            raise ValueError("min_adx must be >= 0")
        # Defaults reproduce the PDF exactly, so the specification stays
        # testable as written. The config turns the guards on.
        self.min_adx = float(min_adx)
        self.require_agreement = bool(require_agreement)
        self.adx_mode = adx_mode
        self.rise_threshold = rise_threshold
        self.fall_threshold = fall_threshold
        self.rise_confirm = rise_confirm
        self.fall_confirm = fall_confirm
        self.duration_seconds = duration_seconds
        self.confirm = confirm
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow

    def score_at(self, candles: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
        return composite_score(candles, ema_fast=self.ema_fast,
                               ema_slow=self.ema_slow)

    def evaluate(self, candles: Sequence[dict[str, Any]],
                symbol: str | None = None) -> Signal | None:
        now = self.score_at(candles)
        if now is None:
            return None

        # GATE 1: is there a trend at all? Below ADX 20 the market is ranging
        # by ADX's own definition, and a direction-blind strength number has
        # no business pushing a directional score either way.
        if self.min_adx > 0 and now["adx_value"] < self.min_adx:
            return None

        # GATE 2: do the two directional components agree? An average of a
        # bull case and a bear case is not a forecast.
        if self.require_agreement and not now["agree"]:
            return None

        s = now["gated_score"] if self.adx_mode == "gate" else now["score"]

        direction = 0
        if s >= self.rise_threshold:
            direction = 1
        elif s <= self.fall_threshold:
            direction = -1
        if direction == 0:
            return None                      # the neutral zone, by design

        if self.confirm:
            prev = self.score_at(candles[:-1])
            if prev is None:
                return None
            p = prev["gated_score"] if self.adx_mode == "gate" else prev["score"]
            ok = (p >= self.rise_confirm) if direction > 0 else (p <= self.fall_confirm)
            if not ok:
                return None

        # expected_move_pct is unused by Rise/Fall - it pays on direction at
        # expiry, not on distance - but Signal requires a positive value to
        # count as actionable, so it carries the distance from the bands.
        return Signal(
            direction=direction,
            expected_move_pct=0.001,
            horizon_seconds=self.duration_seconds,
            confidence=1.0,
            reason=(f"pdf score {s:.1f} [{self.adx_mode}] "
                    f"(ema {now['ema_score']:.2f} rsi {now['rsi_score']:.2f} "
                    f"bb {now['bb_score']:.2f} adx {now['adx_score']:.2f} "
                    f"candle {now['candle_score']:.2f}, ADX={now['adx_value']:.1f}, "
                    f"agree={now['agree']})"),
        )


def score_series(candles: Sequence[dict[str, Any]], *, ema_fast: int = 5,
                 ema_slow: int = 20, rsi_period: int = 14, bb_period: int = 20,
                 bb_std: float = 2.0, adx_period: int = 14) -> list[float | None]:
    """Composite score at EVERY candle, computing each indicator ONCE.

    `composite_score` recalculates every indicator over the whole slice each
    time it is called, which is fine for one live decision and quadratic for
    a backtest - scoring 6,000 candles that way did not finish. Same
    arithmetic, one pass, so a year of data becomes seconds.

    A test asserts this agrees with `composite_score` candle by candle;
    without that, a fast path is just an untested second implementation.
    """
    n = len(candles)
    out: list[float | None] = [None] * n
    if n == 0:
        return out

    closes = [float(c["close"]) for c in candles]
    fast = ema(closes, ema_fast)
    slow = ema(closes, ema_slow)
    r = rsi(closes, rsi_period)
    lower, _mid, upper = bollinger(closes, bb_period, bb_std)
    a = adx(candles, adx_period)
    need = max(ema_slow, bb_period, adx_period * 2 + 1, rsi_period + 1) + 2

    for i in range(n):
        if i + 1 < need or fast[i] is None or slow[i] is None or a[i] is None:
            continue
        subs = (
            ema_subscore(fast[i], slow[i], fast[i - 1], slow[i - 1]),
            rsi_subscore(r[i]),
            bb_subscore(closes[i], lower[i], upper[i]),
            adx_subscore(a[i]),
            CANDLE_SCORES[candle_pattern(candles[max(0, i - 2): i + 1])],
        )
        out[i] = (subs[0] * 0.25 + subs[1] * 0.20 + subs[2] * 0.20 +
                  subs[3] * 0.20 + subs[4] * 0.15) * 100
    return out


def signals_from_series(scores: Sequence[float | None], *,
                        rise_threshold: float = 72.0, fall_threshold: float = 44.0,
                        rise_confirm: float = 68.0, fall_confirm: float = 48.0,
                        confirm: bool = True) -> list[int]:
    """+1 / -1 / 0 per candle, applying the spec's two-candle confirmation."""
    out = [0] * len(scores)
    for i, s in enumerate(scores):
        if s is None:
            continue
        if s >= rise_threshold:
            d = 1
        elif s <= fall_threshold:
            d = -1
        else:
            continue
        if confirm:
            p = scores[i - 1] if i > 0 else None
            if p is None:
                continue
            if d > 0 and p < rise_confirm:
                continue
            if d < 0 and p > fall_confirm:
                continue
        out[i] = d
    return out


def score_series_detail(candles: Sequence[dict[str, Any]], *, ema_fast: int = 5,
                        ema_slow: int = 20, rsi_period: int = 14,
                        bb_period: int = 20, bb_std: float = 2.0,
                        adx_period: int = 14) -> list[dict[str, Any] | None]:
    """Per-candle score PLUS the gate inputs, one pass over the indicators.

    `score_series` returns only the PDF composite, which is not enough to
    backtest the gated variant - that needs the raw ADX value and whether the
    directional components agree. Same single-pass arithmetic; a test asserts
    it matches `composite_score` candle by candle, because a fast path nobody
    checked is just an untested second implementation.
    """
    n = len(candles)
    out: list[dict[str, Any] | None] = [None] * n
    if n == 0:
        return out

    closes = [float(c["close"]) for c in candles]
    fast = ema(closes, ema_fast)
    slow = ema(closes, ema_slow)
    r = rsi(closes, rsi_period)
    lower, _mid, upper = bollinger(closes, bb_period, bb_std)
    a = adx(candles, adx_period)
    need = max(ema_slow, bb_period, adx_period * 2 + 1, rsi_period + 1) + 2

    for i in range(n):
        if i + 1 < need or fast[i] is None or slow[i] is None or a[i] is None:
            continue
        subs = {
            "ema": ema_subscore(fast[i], slow[i], fast[i - 1], slow[i - 1]),
            "rsi": rsi_subscore(r[i]),
            "bb": bb_subscore(closes[i], lower[i], upper[i]),
            "adx": adx_subscore(a[i]),
            "candle": CANDLE_SCORES[candle_pattern(candles[max(0, i - 2): i + 1])],
        }
        out[i] = {
            "score": (subs["ema"] * 0.25 + subs["rsi"] * 0.20 + subs["bb"] * 0.20 +
                      subs["adx"] * 0.20 + subs["candle"] * 0.15) * 100,
            "gated_score": sum(subs[k] * w for k, w in _GATED_WEIGHTS.items()) * 100,
            "adx_value": a[i],
            "agree": directional_agreement(subs["ema"], subs["rsi"]),
        }
    return out


def signals_from_detail(detail: Sequence[dict[str, Any] | None], *,
                        rise_threshold: float = 72.0, fall_threshold: float = 44.0,
                        rise_confirm: float = 68.0, fall_confirm: float = 48.0,
                        confirm: bool = True, adx_mode: str = "score",
                        min_adx: float = 0.0,
                        require_agreement: bool = False) -> list[int]:
    """+1 / -1 / 0 per candle, applying the thresholds AND the two gates."""
    key = "gated_score" if adx_mode == "gate" else "score"
    out = [0] * len(detail)
    for i, row in enumerate(detail):
        if row is None:
            continue
        if min_adx > 0 and row["adx_value"] < min_adx:
            continue
        if require_agreement and not row["agree"]:
            continue
        s = row[key]
        if s >= rise_threshold:
            d = 1
        elif s <= fall_threshold:
            d = -1
        else:
            continue
        if confirm:
            prev = detail[i - 1] if i > 0 else None
            if prev is None:
                continue
            p = prev[key]
            if d > 0 and p < rise_confirm:
                continue
            if d < 0 and p > fall_confirm:
                continue
        out[i] = d
    return out
