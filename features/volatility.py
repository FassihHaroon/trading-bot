"""
Volatility Analysis Feature Extractor.

Populates the following FeatureSet fields:
  - atr            : float  — 14-period ATR on 1h candles
  - atr_pct        : float  — ATR as a percentage of close (ATR / close * 100)
  - volatility_state: VolatilityLevel — HIGH / NORMAL / LOW
  - atr_percentile : float  — rank of current ATR within 100-bar ATR history (0-100)

Stores in FeatureSet.metadata (dict, created if absent) or dedicated fields:
  - bb_width       : float  — (bb_upper - bb_lower) / bb_middle
  - bb_squeeze     : bool   — BB width < 20th-percentile of last 50 bars

Bollinger Bands: 20-period SMA ± 2 std-dev on 1h closes.
ATR percentile  : rank current 14-period ATR in last 100 ATR values.

Volatility classification:
  HIGH   : atr_percentile > 75
  LOW    : atr_percentile < 25
  NORMAL : 25 <= atr_percentile <= 75
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

import numpy as np

from config.settings import AgentConfig, DEFAULT_CONFIG
from data.schemas import Candle, FeatureSet, MarketSnapshot, VolatilityLevel
from features.base import BaseFeatureExtractor, FeatureExtractionError

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Module-level constants
# ─────────────────────────────────────────────

_PRIMARY_TF = "1h"
_FALLBACK_TFS = ("15m", "4h", "1d")

_ATR_PERIOD = 14
_ATR_HISTORY = 100          # bars used to build the ATR percentile window

_BB_PERIOD = 20
_BB_STD_MULT = 2.0
_BB_SQUEEZE_HISTORY = 50    # bars of BB widths used for squeeze detection
_BB_SQUEEZE_PCT = 20        # squeeze = width < 20th-percentile of that window

_HIGH_PERCENTILE = 75.0
_LOW_PERCENTILE = 25.0


# ─────────────────────────────────────────────
# Pure numeric helpers (no side-effects)
# ─────────────────────────────────────────────

def _extract_arrays(
    candles: List[Candle],
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (highs, lows, closes) as float64 arrays, oldest-first."""
    highs = np.array([c.high for c in candles], dtype=np.float64)
    lows = np.array([c.low for c in candles], dtype=np.float64)
    closes = np.array([c.close for c in candles], dtype=np.float64)
    return highs, lows, closes


def _compute_atr_series(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    period: int = _ATR_PERIOD,
) -> np.ndarray:
    """
    Wilder-smoothed ATR.  Returns an array of the same length as *closes*.
    Indices [0 .. period] are NaN; the first valid ATR sits at index *period*.
    """
    n = len(closes)
    result = np.full(n, np.nan, dtype=np.float64)

    if n < period + 1:
        return result

    prev_closes = closes[:-1]
    curr_highs = highs[1:]
    curr_lows = lows[1:]

    hl = curr_highs - curr_lows
    hc = np.abs(curr_highs - prev_closes)
    lc = np.abs(curr_lows - prev_closes)
    tr = np.maximum(hl, np.maximum(hc, lc))   # length n-1

    # Seed: simple mean of first *period* true ranges
    result[period] = np.mean(tr[:period])

    alpha = 1.0 / period
    for i in range(period + 1, n):
        result[i] = result[i - 1] * (1.0 - alpha) + tr[i - 1] * alpha

    return result


def _compute_bb(
    closes: np.ndarray,
    period: int = _BB_PERIOD,
    num_std: float = _BB_STD_MULT,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Bollinger Bands using a simple moving average and population std-dev.

    Returns (upper, middle, lower) arrays, each the same length as *closes*.
    The first (period - 1) values in each band are NaN.
    """
    n = len(closes)
    upper = np.full(n, np.nan, dtype=np.float64)
    middle = np.full(n, np.nan, dtype=np.float64)
    lower = np.full(n, np.nan, dtype=np.float64)

    if n < period:
        return upper, middle, lower

    for i in range(period - 1, n):
        window = closes[i - period + 1 : i + 1]
        sma = np.mean(window)
        std = np.std(window, ddof=0)      # population std, matching standard BB
        middle[i] = sma
        upper[i] = sma + num_std * std
        lower[i] = sma - num_std * std

    return upper, middle, lower


def _bb_width(upper: float, lower: float, middle: float) -> float:
    """Normalised Bollinger Band width: (upper - lower) / middle."""
    if middle == 0.0:
        return 0.0
    return (upper - lower) / middle


def _atr_percentile(atr_series: np.ndarray, history: int = _ATR_HISTORY) -> float:
    """
    Rank the most-recent valid ATR within the last *history* valid ATR values.

    Returns a value in [0, 100]:
      0   = current ATR is the lowest in history
      100 = current ATR is the highest in history
    """
    valid = atr_series[~np.isnan(atr_series)]
    if len(valid) == 0:
        return 50.0

    window = valid[-history:]
    current = window[-1]

    if len(window) == 1:
        return 50.0

    # Count how many values in the window are strictly less than current
    rank = float(np.sum(window < current))
    pct = rank / (len(window) - 1) * 100.0   # normalise to 0-100
    return round(min(max(pct, 0.0), 100.0), 2)


def _classify_volatility(percentile: float) -> VolatilityLevel:
    """Map ATR percentile to a VolatilityLevel enum value."""
    if percentile > _HIGH_PERCENTILE:
        return VolatilityLevel.HIGH
    if percentile < _LOW_PERCENTILE:
        return VolatilityLevel.LOW
    return VolatilityLevel.NORMAL


def _detect_bb_squeeze(
    bb_upper: np.ndarray,
    bb_lower: np.ndarray,
    bb_middle: np.ndarray,
    squeeze_history: int = _BB_SQUEEZE_HISTORY,
    squeeze_pct: float = _BB_SQUEEZE_PCT,
) -> Tuple[float, bool]:
    """
    Compute the current BB width and whether it constitutes a squeeze.

    A *squeeze* is declared when the current BB width is below the
    *squeeze_pct*-th percentile of the last *squeeze_history* valid widths.

    Returns (current_bb_width, is_squeeze).
    """
    # Build array of valid widths (need all three to be non-NaN at same index)
    n = len(bb_upper)
    widths: List[float] = []
    for i in range(n):
        if (
            not np.isnan(bb_upper[i])
            and not np.isnan(bb_lower[i])
            and not np.isnan(bb_middle[i])
            and bb_middle[i] != 0.0
        ):
            widths.append(_bb_width(bb_upper[i], bb_lower[i], bb_middle[i]))

    if not widths:
        return 0.0, False

    current_width = widths[-1]

    # Use up to the last *squeeze_history* widths (including current) for
    # the percentile threshold.
    window = widths[-squeeze_history:]
    if len(window) < 2:
        return current_width, False

    threshold = float(np.percentile(window, squeeze_pct))
    is_squeeze = current_width < threshold

    return round(current_width, 8), is_squeeze


# ─────────────────────────────────────────────
# VolatilityExtractor
# ─────────────────────────────────────────────

class VolatilityExtractor(BaseFeatureExtractor):
    """
    Computes ATR-based and Bollinger-Band-based volatility metrics and
    writes them into a FeatureSet in-place.

    Preferred timeframe: 1h.  Falls back to 15m, 4h, or 1d when 1h is absent.
    """

    def __init__(self, config: AgentConfig = DEFAULT_CONFIG) -> None:
        super().__init__(config.features)
        self._cfg = config
        self._feat = config.features

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def extract(self, snapshot: MarketSnapshot, feature_set: FeatureSet) -> None:
        """
        Mutate *feature_set* in-place with all volatility fields.

        Never raises — any unrecoverable error is appended to
        feature_set.extraction_errors and sensible defaults are left in place.
        """
        candles = self._select_candles(snapshot)
        if not candles:
            feature_set.extraction_errors.append(
                "VolatilityExtractor: no candles available in any timeframe"
            )
            return

        min_bars = _ATR_PERIOD + 1
        if len(candles) < min_bars:
            feature_set.extraction_errors.append(
                f"VolatilityExtractor: need >= {min_bars} bars, got {len(candles)}"
            )
            return

        try:
            highs, lows, closes = _extract_arrays(candles)
        except Exception as exc:
            feature_set.extraction_errors.append(
                f"VolatilityExtractor: array extraction failed — {exc}"
            )
            return

        # ── ATR series ───────────────────────────────────────────────
        try:
            atr_series = _compute_atr_series(
                highs, lows, closes, period=_ATR_PERIOD
            )
        except Exception as exc:
            feature_set.extraction_errors.append(
                f"VolatilityExtractor: ATR computation failed — {exc}"
            )
            return

        valid_atrs = atr_series[~np.isnan(atr_series)]
        if len(valid_atrs) == 0:
            feature_set.extraction_errors.append(
                "VolatilityExtractor: ATR series contains no valid values"
            )
            return

        current_atr = float(valid_atrs[-1])
        current_close = float(closes[-1])

        # ── ATR percentage ───────────────────────────────────────────
        atr_pct = (current_atr / current_close * 100.0) if current_close != 0.0 else 0.0

        # ── ATR percentile & volatility state ───────────────────────
        try:
            percentile = _atr_percentile(atr_series, history=_ATR_HISTORY)
        except Exception as exc:
            feature_set.extraction_errors.append(
                f"VolatilityExtractor: percentile computation failed — {exc}"
            )
            percentile = 50.0

        vol_state = _classify_volatility(percentile)

        # ── Bollinger Bands ──────────────────────────────────────────
        try:
            bb_upper, bb_middle, bb_lower = _compute_bb(
                closes, period=_BB_PERIOD, num_std=_BB_STD_MULT
            )
            current_bb_width, is_squeeze = _detect_bb_squeeze(
                bb_upper,
                bb_lower,
                bb_middle,
                squeeze_history=_BB_SQUEEZE_HISTORY,
                squeeze_pct=_BB_SQUEEZE_PCT,
            )
        except Exception as exc:
            feature_set.extraction_errors.append(
                f"VolatilityExtractor: Bollinger Band computation failed — {exc}"
            )
            current_bb_width = 0.0
            is_squeeze = False

        # ── Write to FeatureSet ──────────────────────────────────────
        feature_set.atr = round(current_atr, 8)
        feature_set.atr_pct = round(atr_pct, 4)
        feature_set.atr_percentile = percentile
        feature_set.volatility_state = vol_state

        # BB fields go into metadata (FeatureSet has no dedicated BB fields).
        # Create the dict lazily; downstream consumers check for these keys.
        if not hasattr(feature_set, "metadata") or feature_set.metadata is None:  # type: ignore[attr-defined]
            try:
                feature_set.metadata = {}          # type: ignore[attr-defined]
            except AttributeError:
                # FeatureSet is a frozen dataclass variant — log and move on.
                feature_set.extraction_errors.append(
                    "VolatilityExtractor: cannot attach metadata dict to FeatureSet"
                )
                return

        feature_set.metadata["bb_width"] = current_bb_width   # type: ignore[attr-defined]
        feature_set.metadata["bb_squeeze"] = is_squeeze        # type: ignore[attr-defined]

        self._log(
            f"atr={current_atr:.6f} atr_pct={atr_pct:.3f}% "
            f"percentile={percentile:.1f} state={vol_state.value} "
            f"bb_width={current_bb_width:.6f} squeeze={is_squeeze}"
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _select_candles(self, snapshot: MarketSnapshot) -> List[Candle]:
        """
        Return 1h candles when available; fall back through FALLBACK_TFS.
        Returns an empty list when no timeframe has data.
        """
        available = snapshot.candles or {}

        for tf in (_PRIMARY_TF, *_FALLBACK_TFS):
            bars = available.get(tf)
            if bars:
                if tf != _PRIMARY_TF:
                    logger.warning(
                        "VolatilityExtractor: 1h unavailable for %s, "
                        "falling back to %s",
                        snapshot.symbol,
                        tf,
                    )
                return bars

        return []
