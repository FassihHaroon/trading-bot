"""
Oscillator Divergence Detection Feature Extractor.

Detects four divergence types across RSI, MACD histogram, and Stochastics
for every timeframe at or above 1h.  Results are appended to
FeatureSet.divergences as DivergenceDetection instances.

Divergence taxonomy
-------------------
Regular Bearish  — price makes HH,  indicator makes LH  → BEARISH_REGULAR
Regular Bullish  — price makes LL,  indicator makes HL  → BULLISH_REGULAR
Hidden  Bullish  — price makes HL,  indicator makes LL  → BULLISH_HIDDEN
Hidden  Bearish  — price makes LH,  indicator makes HH  → BEARISH_HIDDEN

Algorithm per indicator
-----------------------
1. Compute the indicator series for the full candle history.
2. Locate the two most-recent swing highs in price (bearish checks) and
   the two most-recent swing lows (bullish checks) within the lookback
   window, subject to:
     - at least divergence_lookback bars of history are available
     - the two pivot bars are at least MIN_PIVOT_SEPARATION bars apart
3. Compare the indicator values at those exact pivot bars.
4. Assign confidence:
     base 0.5
     +0.2 if volume on the second peak is declining relative to the first
     +0.2 if the second pivot price is within S/R proximity of a key zone
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

import numpy as np

from config.settings import AgentConfig, DEFAULT_CONFIG
from data.schemas import (
    Candle,
    DivergenceDetection,
    DivergenceType,
    FeatureSet,
    MarketSnapshot,
    SRZone,
)
from features.base import BaseFeatureExtractor

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Module-level constants
# ─────────────────────────────────────────────────────────────────────────────

# Only analyse these timeframes (15m excluded — too noisy).
_ELIGIBLE_TIMEFRAMES: frozenset[str] = frozenset({"1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w"})

# Minimum bars between the two pivot points.
MIN_PIVOT_SEPARATION: int = 5

# Swing confirmation window: a local high/low must be the extreme of this
# many bars on each side.  Intentionally kept small so that pivots are
# detectable even with moderate lookback windows.
_SWING_WINDOW: int = 3

# Proximity threshold for "near a key S/R level" (fraction of price).
_SR_PROXIMITY_PCT: float = 0.005   # 0.5 %


# ─────────────────────────────────────────────────────────────────────────────
# Helper: indicator calculations (pure numpy, no external TA library)
# ─────────────────────────────────────────────────────────────────────────────

def _rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    """Wilder-smoothed RSI, same implementation as BaseFeatureExtractor._rsi."""
    n = len(closes)
    result = np.full(n, np.nan)
    if n <= period:
        return result
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = float(np.mean(gains[:period]))
    avg_loss = float(np.mean(losses[:period]))
    alpha = 1.0 / period
    if avg_loss == 0.0:
        result[period] = 100.0
    else:
        result[period] = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    for i in range(period + 1, n):
        avg_gain = avg_gain * (1.0 - alpha) + gains[i - 1] * alpha
        avg_loss = avg_loss * (1.0 - alpha) + losses[i - 1] * alpha
        if avg_loss == 0.0:
            result[i] = 100.0
        else:
            result[i] = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    return result


def _macd_histogram(closes: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9) -> np.ndarray:
    """Returns the MACD histogram (MACD line minus signal line)."""

    def _ema(arr: np.ndarray, p: int) -> np.ndarray:
        out = np.full(len(arr), np.nan)
        if len(arr) < p:
            return out
        k = 2.0 / (p + 1)
        out[p - 1] = float(np.mean(arr[:p]))
        for i in range(p, len(arr)):
            out[i] = arr[i] * k + out[i - 1] * (1.0 - k)
        return out

    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    macd_line = ema_fast - ema_slow
    sig_line = _ema(np.where(np.isnan(macd_line), 0.0, macd_line), signal)
    # Propagate NaN from macd_line
    hist = macd_line - sig_line
    hist[np.isnan(macd_line)] = np.nan
    return hist


def _stochastics(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    k_period: int = 14,
    d_period: int = 3,
    smooth: int = 3,
) -> np.ndarray:
    """
    Returns smoothed %K (the line most commonly compared for divergence).
    %K_raw = (close - lowest_low_k) / (highest_high_k - lowest_low_k) * 100
    Smoothed %K = SMA(k_raw, smooth).
    """
    n = len(closes)
    raw_k = np.full(n, np.nan)
    for i in range(k_period - 1, n):
        ll = float(np.min(lows[i - k_period + 1 : i + 1]))
        hh = float(np.max(highs[i - k_period + 1 : i + 1]))
        denom = hh - ll
        if denom == 0.0:
            raw_k[i] = 50.0
        else:
            raw_k[i] = (closes[i] - ll) / denom * 100.0

    # Smooth %K
    smoothed_k = np.full(n, np.nan)
    if smooth > 1:
        for i in range(n):
            window = raw_k[max(0, i - smooth + 1) : i + 1]
            valid = window[~np.isnan(window)]
            if len(valid) == smooth:
                smoothed_k[i] = float(np.mean(valid))
    else:
        smoothed_k = raw_k.copy()

    return smoothed_k


# ─────────────────────────────────────────────────────────────────────────────
# Helper: swing pivot detection within a 1-D price/indicator array
# ─────────────────────────────────────────────────────────────────────────────

def _find_swing_highs(values: np.ndarray, window: int = _SWING_WINDOW) -> List[int]:
    """
    Return indices of local maxima (swing highs), sorted by index descending
    (most recent first).  A value at index i is a swing high when it is
    strictly greater than every value in the window on each side.
    NaN positions are skipped.
    """
    n = len(values)
    indices: List[int] = []
    for i in range(window, n - window):
        if np.isnan(values[i]):
            continue
        left = values[i - window : i]
        right = values[i + 1 : i + window + 1]
        if np.any(np.isnan(left)) or np.any(np.isnan(right)):
            continue
        if values[i] > np.max(left) and values[i] > np.max(right):
            indices.append(i)
    indices.sort(reverse=True)
    return indices


def _find_swing_lows(values: np.ndarray, window: int = _SWING_WINDOW) -> List[int]:
    """
    Return indices of local minima (swing lows), sorted by index descending
    (most recent first).
    """
    n = len(values)
    indices: List[int] = []
    for i in range(window, n - window):
        if np.isnan(values[i]):
            continue
        left = values[i - window : i]
        right = values[i + 1 : i + window + 1]
        if np.any(np.isnan(left)) or np.any(np.isnan(right)):
            continue
        if values[i] < np.min(left) and values[i] < np.min(right):
            indices.append(i)
    indices.sort(reverse=True)
    return indices


# ─────────────────────────────────────────────────────────────────────────────
# Helper: pivot pair extraction with separation constraint
# ─────────────────────────────────────────────────────────────────────────────

def _pick_two_pivots(
    sorted_indices: List[int],
) -> Optional[Tuple[int, int]]:
    """
    From a list of pivot indices (newest first), pick the two most recent
    that are at least MIN_PIVOT_SEPARATION bars apart.

    Returns (newer_idx, older_idx) or None if no valid pair exists.
    """
    for i in range(len(sorted_indices) - 1):
        for j in range(i + 1, len(sorted_indices)):
            newer = sorted_indices[i]
            older = sorted_indices[j]
            if abs(newer - older) >= MIN_PIVOT_SEPARATION:
                return newer, older
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Helper: confidence scoring
# ─────────────────────────────────────────────────────────────────────────────

def _score_confidence(
    candles: List[Candle],
    pivot1_idx: int,   # newer (second) pivot — the one under scrutiny
    pivot2_idx: int,   # older (first) pivot
    sr_zones: List[SRZone],
    pivot_price: float,
) -> float:
    """
    Base 0.5, with up to 0.4 additional confidence:
      +0.2 if volume at the newer pivot is declining vs the older pivot
      +0.2 if the newer pivot price is near a key S/R level
    """
    confidence = 0.5

    # Volume bonus: newer peak volume < older peak volume
    vol1 = candles[pivot1_idx].volume   # newer
    vol2 = candles[pivot2_idx].volume   # older
    if vol2 > 0 and vol1 < vol2:
        confidence += 0.2

    # S/R proximity bonus
    if sr_zones:
        for zone in sr_zones:
            if zone.level > 0:
                proximity = abs(pivot_price - zone.level) / zone.level
                if proximity <= _SR_PROXIMITY_PCT:
                    confidence += 0.2
                    break

    return min(confidence, 1.0)


# ─────────────────────────────────────────────────────────────────────────────
# Main extractor class
# ─────────────────────────────────────────────────────────────────────────────

class DivergenceExtractor(BaseFeatureExtractor):
    """
    Detects oscillator divergences (RSI, MACD histogram, Stochastics) for
    every eligible timeframe and appends DivergenceDetection objects to
    FeatureSet.divergences.

    Only timeframes >= 1h are processed; 15m is explicitly excluded as too
    noisy.
    """

    def __init__(self, config: Optional[AgentConfig] = None) -> None:
        cfg = config or DEFAULT_CONFIG
        # BaseFeatureExtractor expects a FeatureConfig, not AgentConfig.
        super().__init__(cfg.features)
        self._agent_config = cfg

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def extract(self, snapshot: MarketSnapshot, features: FeatureSet) -> None:
        """
        Detect divergences across all eligible timeframes and write results
        into features.divergences in-place.
        """
        lookback: int = self._agent_config.features.divergence_lookback
        sr_zones: List[SRZone] = features.sr_zones  # may be populated by an earlier extractor

        all_detections: List[DivergenceDetection] = []

        for timeframe, candles in snapshot.candles.items():
            if timeframe not in _ELIGIBLE_TIMEFRAMES:
                self._log(f"Skipping {timeframe} — not in eligible set (15m excluded)")
                continue

            if len(candles) < lookback:
                features.extraction_errors.append(
                    f"divergence:{timeframe}: insufficient candles "
                    f"(need {lookback}, got {len(candles)})"
                )
                continue

            # Restrict to the most recent *lookback* candles for efficiency
            window_candles = candles[-lookback:]

            try:
                detections = self._detect_all(window_candles, timeframe, sr_zones)
                all_detections.extend(detections)
            except Exception as exc:  # noqa: BLE001
                features.extraction_errors.append(
                    f"divergence:{timeframe}: {exc!r}"
                )
                logger.exception("Divergence extraction failed for %s", timeframe)

        features.divergences = all_detections

    # ------------------------------------------------------------------
    # Per-timeframe detection orchestration
    # ------------------------------------------------------------------

    def _detect_all(
        self,
        candles: List[Candle],
        timeframe: str,
        sr_zones: List[SRZone],
    ) -> List[DivergenceDetection]:
        """Run all three indicators and collect divergence signals."""
        closes = self._get_closes(candles)
        highs = self._get_highs(candles)
        lows = self._get_lows(candles)

        rsi_cfg = self._agent_config.features
        rsi_series = _rsi(closes, period=rsi_cfg.rsi_period)
        macd_hist = _macd_histogram(
            closes,
            fast=rsi_cfg.macd_fast,
            slow=rsi_cfg.macd_slow,
            signal=rsi_cfg.macd_signal,
        )
        stoch_k = _stochastics(
            highs,
            lows,
            closes,
            k_period=rsi_cfg.stoch_k,
            d_period=rsi_cfg.stoch_d,
            smooth=rsi_cfg.stoch_smooth,
        )

        results: List[DivergenceDetection] = []

        # Price swing high/low arrays use high[] and low[] respectively
        price_highs = highs
        price_lows = lows

        for indicator_name, indicator_series in [
            ("rsi", rsi_series),
            ("macd", macd_hist),
            ("stochastics", stoch_k),
        ]:
            detections = self._check_indicator(
                candles=candles,
                price_highs=price_highs,
                price_lows=price_lows,
                indicator=indicator_series,
                indicator_name=indicator_name,
                timeframe=timeframe,
                sr_zones=sr_zones,
            )
            results.extend(detections)

        return results

    # ------------------------------------------------------------------
    # Core divergence logic
    # ------------------------------------------------------------------

    def _check_indicator(
        self,
        candles: List[Candle],
        price_highs: np.ndarray,
        price_lows: np.ndarray,
        indicator: np.ndarray,
        indicator_name: str,
        timeframe: str,
        sr_zones: List[SRZone],
    ) -> List[DivergenceDetection]:
        """
        Check all four divergence types for one indicator series.
        Returns a (possibly empty) list of DivergenceDetection objects.
        """
        detections: List[DivergenceDetection] = []

        # ── Bearish checks (use price swing highs) ───────────────────────
        price_high_pivots = _find_swing_highs(price_highs)
        pair_h = _pick_two_pivots(price_high_pivots)

        if pair_h is not None:
            newer_hi, older_hi = pair_h
            p1_price = float(price_highs[newer_hi])   # newer (second) pivot
            p2_price = float(price_highs[older_hi])   # older (first) pivot
            i1_val = float(indicator[newer_hi])
            i2_val = float(indicator[older_hi])

            if not (np.isnan(i1_val) or np.isnan(i2_val)):
                bars_apart = abs(newer_hi - older_hi)

                # Regular Bearish: price HH, indicator LH
                if p1_price > p2_price and i1_val < i2_val:
                    conf = _score_confidence(candles, newer_hi, older_hi, sr_zones, p1_price)
                    detections.append(DivergenceDetection(
                        divergence_type=DivergenceType.BEARISH_REGULAR,
                        indicator=indicator_name,
                        timeframe=timeframe,
                        price_point_1=p2_price,    # older swing high (first)
                        price_point_2=p1_price,    # newer swing high (second)
                        indicator_point_1=i2_val,
                        indicator_point_2=i1_val,
                        confidence=conf,
                        bars_apart=bars_apart,
                    ))

                # Hidden Bearish: price LH, indicator HH
                elif p1_price < p2_price and i1_val > i2_val:
                    conf = _score_confidence(candles, newer_hi, older_hi, sr_zones, p1_price)
                    detections.append(DivergenceDetection(
                        divergence_type=DivergenceType.BEARISH_HIDDEN,
                        indicator=indicator_name,
                        timeframe=timeframe,
                        price_point_1=p2_price,
                        price_point_2=p1_price,
                        indicator_point_1=i2_val,
                        indicator_point_2=i1_val,
                        confidence=conf,
                        bars_apart=bars_apart,
                    ))

        # ── Bullish checks (use price swing lows) ────────────────────────
        price_low_pivots = _find_swing_lows(price_lows)
        pair_l = _pick_two_pivots(price_low_pivots)

        if pair_l is not None:
            newer_lo, older_lo = pair_l
            p1_price = float(price_lows[newer_lo])   # newer (second) pivot
            p2_price = float(price_lows[older_lo])   # older (first) pivot
            i1_val = float(indicator[newer_lo])
            i2_val = float(indicator[older_lo])

            if not (np.isnan(i1_val) or np.isnan(i2_val)):
                bars_apart = abs(newer_lo - older_lo)

                # Regular Bullish: price LL, indicator HL
                if p1_price < p2_price and i1_val > i2_val:
                    conf = _score_confidence(candles, newer_lo, older_lo, sr_zones, p1_price)
                    detections.append(DivergenceDetection(
                        divergence_type=DivergenceType.BULLISH_REGULAR,
                        indicator=indicator_name,
                        timeframe=timeframe,
                        price_point_1=p2_price,
                        price_point_2=p1_price,
                        indicator_point_1=i2_val,
                        indicator_point_2=i1_val,
                        confidence=conf,
                        bars_apart=bars_apart,
                    ))

                # Hidden Bullish: price HL, indicator LL
                elif p1_price > p2_price and i1_val < i2_val:
                    conf = _score_confidence(candles, newer_lo, older_lo, sr_zones, p1_price)
                    detections.append(DivergenceDetection(
                        divergence_type=DivergenceType.BULLISH_HIDDEN,
                        indicator=indicator_name,
                        timeframe=timeframe,
                        price_point_1=p2_price,
                        price_point_2=p1_price,
                        indicator_point_1=i2_val,
                        indicator_point_2=i1_val,
                        confidence=conf,
                        bars_apart=bars_apart,
                    ))

        return detections
