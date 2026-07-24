"""
Multi-timeframe alignment analysis feature extractor.

Populates the MTF-related fields of FeatureSet:
  - tf_bias          : dict[str, TrendDirection] for each available timeframe
  - tf_aligned       : True when 4h and 1d agree on direction
  - macro_bias       : dominant direction derived from 4h + 1d

Implements the Three-Timeframe Framework from multiple_timeframe_analysis.md:
  - 1D / 4H  → macro (trend) layer — defines directional bias
  - 1H       → mid (swing) layer — setup refinement
  - 15M      → micro (entry) layer — timing only, never directional

Counter-trend micro warning:
  When macro_bias is BULLISH but 15m reads BEARISH (or vice versa), a warning
  is appended to FeatureSet.extraction_errors as an informational timing signal.
  This is NOT a reversal signal — it flags a potential pullback entry opportunity
  for trades aligned with the macro trend.

Rule references (multiple_timeframe_analysis.md):
  MACRO_MICRO_GATE_MANDATORY  — 1D+4H disagreement blocks signals (enforced upstream)
  COUNTER_TREND_MICRO_EXCLUDED — 15M counter signals are flagged, not traded
  TIMEFRAME_DISAGREEMENT_WAIT — tf_aligned=False signals wait state to strategy engine
"""

from __future__ import annotations

import logging
from typing import Optional

from features.base import BaseFeatureExtractor, FeatureExtractionError
from data.schemas import (
    Candle,
    FeatureSet,
    MarketSnapshot,
    TrendDirection,
)
from config.settings import AgentConfig, DEFAULT_CONFIG

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────
# Bias scoring constants
# ─────────────────────────────────────────────────────────────────

_SCORE_BULLISH = 3
_SCORE_BEARISH = 0
# Scores 1 or 2 map to NEUTRAL

# Minimum candles required before we attempt any indicator on a TF
_MIN_CANDLES = 10

# Bars used for structure (HH+HL / LH+LL) detection
_STRUCTURE_LOOKBACK = 5

# EMA periods
_EMA_20_PERIOD = 20
_EMA_50_PERIOD = 50

# Slope lookback (last N valid EMA values)
_SLOPE_LOOKBACK = 5

# Timeframes treated as macro
_MACRO_TF_PRIMARY = "1d"
_MACRO_TF_SECONDARY = "4h"

# Micro timeframe that may produce a counter-trend warning
_MICRO_TF = "15m"


# ─────────────────────────────────────────────────────────────────
# Pure helpers (no side-effects)
# ─────────────────────────────────────────────────────────────────

def _ema_series(prices: list[float], period: int) -> list[Optional[float]]:
    """
    Compute EMA of *prices* (oldest-first).

    Returns a list of the same length; positions before the first valid
    value are None.  Uses standard smoothing factor k = 2 / (period + 1)
    seeded with the SMA of the first *period* bars.
    """
    n = len(prices)
    if n < period:
        return [None] * n

    result: list[Optional[float]] = [None] * n
    k = 2.0 / (period + 1)
    result[period - 1] = sum(prices[:period]) / period

    for i in range(period, n):
        result[i] = prices[i] * k + result[i - 1] * (1.0 - k)  # type: ignore[operator]

    return result


def _last_valid(series: list[Optional[float]]) -> Optional[float]:
    """Return the rightmost non-None element of *series*, or None."""
    for v in reversed(series):
        if v is not None:
            return v
    return None


def _ema_slope(series: list[Optional[float]], lookback: int = _SLOPE_LOOKBACK) -> float:
    """
    Linear-regression slope of the last *lookback* valid EMA values,
    normalised by their mean so the result is dimensionless.
    Returns 0.0 when fewer than 2 valid points are available.
    """
    vals = [v for v in series if v is not None]
    if len(vals) < 2:
        return 0.0

    segment = vals[-lookback:]
    n = len(segment)
    if n < 2:
        return 0.0

    mean_y = sum(segment) / n
    if mean_y == 0.0:
        return 0.0

    mean_x = (n - 1) / 2.0
    numerator = sum((i - mean_x) * (segment[i] - mean_y) for i in range(n))
    denominator = sum((i - mean_x) ** 2 for i in range(n))

    if denominator == 0.0:
        return 0.0

    return (numerator / denominator) / mean_y


def _detect_hh_hl(candles: list[Candle], lookback: int = _STRUCTURE_LOOKBACK) -> bool:
    """
    True when the last *lookback* bars contain at least two pivot highs and
    two pivot lows where the most recent pivot high > prior pivot high AND
    the most recent pivot low > prior pivot low (higher-high + higher-low).

    A pivot high is a bar whose high exceeds both its immediate neighbours.
    A pivot low is a bar whose low is below both its immediate neighbours.
    """
    bars = candles[-(lookback * 2):] if len(candles) >= lookback * 2 else candles
    if len(bars) < 6:
        return False

    pivot_highs: list[float] = []
    pivot_lows: list[float] = []

    for i in range(1, len(bars) - 1):
        if bars[i].high > bars[i - 1].high and bars[i].high > bars[i + 1].high:
            pivot_highs.append(bars[i].high)
        if bars[i].low < bars[i - 1].low and bars[i].low < bars[i + 1].low:
            pivot_lows.append(bars[i].low)

    if len(pivot_highs) >= 2 and len(pivot_lows) >= 2:
        return pivot_highs[-1] > pivot_highs[-2] and pivot_lows[-1] > pivot_lows[-2]

    return False


def _detect_lh_ll(candles: list[Candle], lookback: int = _STRUCTURE_LOOKBACK) -> bool:
    """
    True when the most recent pivot high < prior pivot high AND the most
    recent pivot low < prior pivot low (lower-high + lower-low).
    """
    bars = candles[-(lookback * 2):] if len(candles) >= lookback * 2 else candles
    if len(bars) < 6:
        return False

    pivot_highs: list[float] = []
    pivot_lows: list[float] = []

    for i in range(1, len(bars) - 1):
        if bars[i].high > bars[i - 1].high and bars[i].high > bars[i + 1].high:
            pivot_highs.append(bars[i].high)
        if bars[i].low < bars[i - 1].low and bars[i].low < bars[i + 1].low:
            pivot_lows.append(bars[i].low)

    if len(pivot_highs) >= 2 and len(pivot_lows) >= 2:
        return pivot_highs[-1] < pivot_highs[-2] and pivot_lows[-1] < pivot_lows[-2]

    return False


def _score_tf_bias(candles: list[Candle]) -> int:
    """
    Compute a directional score (0–3) for a single timeframe.

    Scoring criteria (each worth 1 point):
      +1  20 EMA slope is positive
      +1  Most recent close is above the 50 EMA
      +1  Last bars show HH + HL structure

    Returns
    -------
    int
        3  → BULLISH
        0  → BEARISH
        1 or 2 → NEUTRAL
    """
    closes = [c.close for c in candles]
    score = 0

    # ── Criterion 1: 20 EMA slope ────────────────────────────────
    ema20 = _ema_series(closes, _EMA_20_PERIOD)
    slope_20 = _ema_slope(ema20)
    if slope_20 > 0:
        score += 1

    # ── Criterion 2: close vs 50 EMA ─────────────────────────────
    ema50 = _ema_series(closes, _EMA_50_PERIOD)
    last_ema50 = _last_valid(ema50)
    if last_ema50 is not None and closes[-1] > last_ema50:
        score += 1

    # ── Criterion 3: HH + HL structure ───────────────────────────
    if _detect_hh_hl(candles, lookback=_STRUCTURE_LOOKBACK):
        score += 1

    return score


def _score_to_direction(score: int) -> TrendDirection:
    """Map a 0–3 score to a TrendDirection."""
    if score == _SCORE_BULLISH:
        return TrendDirection.BULLISH
    if score == _SCORE_BEARISH:
        return TrendDirection.BEARISH
    return TrendDirection.NEUTRAL


# ─────────────────────────────────────────────────────────────────
# MultiTimeframeExtractor
# ─────────────────────────────────────────────────────────────────

class MultiTimeframeExtractor(BaseFeatureExtractor):
    """
    Multi-timeframe alignment analysis.

    For each timeframe present in snapshot.candles, determines a directional
    bias using three weighted criteria:
      1. 20 EMA slope positive / negative
      2. Close above / below 50 EMA
      3. Recent bars forming HH+HL (bullish structure) or LH+LL (bearish)

    Scoring: each criterion worth 1 point → 3 = BULLISH, 0 = BEARISH, 1-2 = NEUTRAL.

    Macro bias is resolved from the 1d and 4h readings per the rules in
    multiple_timeframe_analysis.md:
      - Both BULLISH  → macro_bias = BULLISH,  tf_aligned = True
      - Both BEARISH  → macro_bias = BEARISH,  tf_aligned = True
      - Any other     → macro_bias = NEUTRAL,  tf_aligned = False

    Counter-trend micro warning:
      If macro_bias = BULLISH and 15m = BEARISH (or the reverse), an
      informational warning is written to FeatureSet.extraction_errors.
      This signals a potential pullback within the macro trend — an entry
      timing opportunity for longs/shorts in line with macro direction, NOT
      a reversal.
    """

    def __init__(self, config: AgentConfig = DEFAULT_CONFIG) -> None:
        import logging as _logging
        self._logger = _logging.getLogger(self.__class__.__name__)
        self.cfg = config
        self.feat = config.features
        # Use EMA period from config where available; fall back to module defaults
        self._ema50_period = self.feat.ema_long        # typically 50
        self._ema20_period = getattr(self.feat, "ema_mid", _EMA_20_PERIOD)  # typically 21≈20

    # ── Public API ────────────────────────────────────────────────

    def extract(self, snapshot: MarketSnapshot, feature_set: FeatureSet) -> None:
        """
        Mutate *feature_set* in-place with multi-timeframe alignment fields:
          - tf_bias
          - tf_aligned
          - macro_bias

        Also appends a counter-trend micro warning to extraction_errors when
        the 15m timeframe opposes the macro direction.

        Parameters
        ----------
        snapshot : MarketSnapshot
            Live market snapshot; snapshot.candles is a dict[str, list[Candle]].
        feature_set : FeatureSet
            Output object to be mutated.
        """
        available = snapshot.candles or {}

        if not available:
            feature_set.extraction_errors.append(
                "MultiTimeframeExtractor: no candle data in snapshot"
            )
            return

        # ── Step 1: compute per-TF bias ───────────────────────────
        tf_bias: dict[str, TrendDirection] = {}

        for tf, candles in available.items():
            if not candles or len(candles) < _MIN_CANDLES:
                self._log(
                    f"Skipping {tf}: only {len(candles) if candles else 0} candles "
                    f"(minimum {_MIN_CANDLES} required)"
                )
                continue

            try:
                score = _score_tf_bias(candles)
                tf_bias[tf] = _score_to_direction(score)
                self._log(
                    f"{tf}: score={score} → {tf_bias[tf].value}"
                )
            except Exception as exc:
                feature_set.extraction_errors.append(
                    f"MultiTimeframeExtractor [{tf}]: {exc}"
                )

        feature_set.tf_bias = tf_bias
        feature_set.timeframes_available = list(tf_bias.keys())

        # ── Step 2: macro bias from 1d + 4h ──────────────────────
        macro_bias, tf_aligned = self._resolve_macro_bias(tf_bias)
        feature_set.macro_bias = macro_bias
        feature_set.tf_aligned = tf_aligned

        self._log(
            f"macro_bias={macro_bias.value}, tf_aligned={tf_aligned}"
        )

        # ── Step 3: counter-trend micro warning ───────────────────
        self._check_counter_trend_micro(macro_bias, tf_bias, feature_set)

    # ── Internal helpers ──────────────────────────────────────────

    @staticmethod
    def _resolve_macro_bias(
        tf_bias: dict[str, TrendDirection],
    ) -> tuple[TrendDirection, bool]:
        """
        Derive macro directional bias and alignment flag from 1d + 4h readings.

        Rules (multiple_timeframe_analysis.md — MACRO_MICRO_GATE_MANDATORY):
          - 1d BULLISH  AND 4h BULLISH  → BULLISH,  aligned=True
          - 1d BEARISH  AND 4h BEARISH  → BEARISH,  aligned=True
          - Any disagreement             → NEUTRAL,  aligned=False
          - Missing data (only one TF)   → that TF's direction, aligned=False

        Returns
        -------
        tuple[TrendDirection, bool]
            (macro_bias, tf_aligned)
        """
        bias_1d = tf_bias.get(_MACRO_TF_PRIMARY)
        bias_4h = tf_bias.get(_MACRO_TF_SECONDARY)

        # Both macro timeframes present
        if bias_1d is not None and bias_4h is not None:
            if bias_1d == bias_4h == TrendDirection.BULLISH:
                return TrendDirection.BULLISH, True
            if bias_1d == bias_4h == TrendDirection.BEARISH:
                return TrendDirection.BEARISH, True
            # Disagreement (includes NEUTRAL cases)
            return TrendDirection.NEUTRAL, False

        # Only one macro TF available — use it but mark as unaligned
        if bias_1d is not None:
            logger.warning(
                "MultiTimeframeExtractor: 4h candles missing; "
                "using 1d alone for macro bias (tf_aligned=False)"
            )
            return bias_1d, False

        if bias_4h is not None:
            logger.warning(
                "MultiTimeframeExtractor: 1d candles missing; "
                "using 4h alone for macro bias (tf_aligned=False)"
            )
            return bias_4h, False

        # Neither macro TF is present
        logger.warning(
            "MultiTimeframeExtractor: neither 1d nor 4h data available; "
            "macro_bias=NEUTRAL"
        )
        return TrendDirection.NEUTRAL, False

    @staticmethod
    def _check_counter_trend_micro(
        macro_bias: TrendDirection,
        tf_bias: dict[str, TrendDirection],
        feature_set: FeatureSet,
    ) -> None:
        """
        Detect and flag a counter-trend micro signal on the 15m timeframe.

        This is an INFORMATIONAL timing flag, not a reversal signal.
        A bearish 15m reading during a bullish macro trend suggests the market
        is in a short-term pullback — a potential long entry opportunity.
        A bullish 15m during a bearish macro suggests a short-term bounce
        within a downtrend — a potential short entry opportunity.

        The flag is appended to feature_set.extraction_errors using the prefix
        "MTF_COUNTER_TREND_MICRO:" so downstream consumers can identify it
        without string-matching the full message.

        Rule reference: COUNTER_TREND_MICRO_EXCLUDED (the signal is flagged here
        for timing use; the strategy engine must not treat it as a directional edge).
        """
        micro_bias = tf_bias.get(_MICRO_TF)

        if micro_bias is None or macro_bias == TrendDirection.NEUTRAL:
            return

        counter = (
            macro_bias == TrendDirection.BULLISH
            and micro_bias == TrendDirection.BEARISH
        ) or (
            macro_bias == TrendDirection.BEARISH
            and micro_bias == TrendDirection.BULLISH
        )

        if counter:
            msg = (
                f"MTF_COUNTER_TREND_MICRO: macro={macro_bias.value}, "
                f"15m={micro_bias.value}. "
                f"This is a pullback/entry-timing signal for "
                f"{'longs' if macro_bias == TrendDirection.BULLISH else 'shorts'} "
                f"aligned with the macro trend — NOT a reversal signal."
            )
            feature_set.extraction_errors.append(msg)
            logger.info(msg)
