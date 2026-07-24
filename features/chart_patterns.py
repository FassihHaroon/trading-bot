"""
Chart Pattern Recognition Feature Extractor.

Detects classical reversal and continuation chart patterns on 1h and 4h
timeframes using the last 200 bars.  Each detected pattern is encoded as a
ChartPatternDetection and appended to FeatureSet.chart_patterns.

Reversal patterns
-----------------
  head_and_shoulders        – bearish reversal
  inverse_head_and_shoulders – bullish reversal
  double_top                – bearish reversal
  double_bottom             – bullish reversal

Continuation patterns
---------------------
  ascending_triangle        – bullish continuation
  descending_triangle       – bearish continuation
  symmetrical_triangle      – neutral / direction of trend
  bull_flag                 – bullish continuation
  bear_flag                 – bearish continuation
  rising_wedge              – bearish (prices rise but converge → breakdown)
  falling_wedge             – bullish (prices fall but converge → breakout)
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

from data.schemas import (
    Candle,
    ChartPatternDetection,
    Direction,
    FeatureSet,
    MarketSnapshot,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Tunables
# ─────────────────────────────────────────────

PATTERN_LOOKBACK: int = 200           # bars to analyse per timeframe
TARGET_TIMEFRAMES: List[str] = ["1h", "4h"]
DOUBLE_TOP_TOLERANCE: float = 0.015   # peaks within 1.5 % of each other
VOLUME_BREAKOUT_MULTIPLIER: float = 1.5  # breakout bar must be > 1.5× 20-bar avg
VOLUME_AVG_BARS: int = 20
MIN_SWING_SEPARATION: int = 5         # bars between pivots (noise filter)
TRIANGLE_MIN_TOUCHES: int = 2         # minimum trendline touches per side
FLAG_POLE_MIN_BARS: int = 5           # minimum bars for a flag pole
FLAG_POLE_MIN_PCT: float = 0.03       # pole must be ≥ 3 % price move
FLAG_CHANNEL_MAX_RETRACE: float = 0.50  # flag body retraces ≤ 50 % of pole
FLAG_CHANNEL_MAX_BARS: int = 20       # flag consolidation no longer than 20 bars


# ─────────────────────────────────────────────
# Internal pivot helpers
# ─────────────────────────────────────────────

def _pivot_highs(highs: np.ndarray, order: int = 3) -> List[int]:
    """Return indices of local high pivots (each surrounded by *order* lower bars)."""
    n = len(highs)
    pivots: List[int] = []
    for i in range(order, n - order):
        window = highs[i - order: i + order + 1]
        if highs[i] == window.max() and highs[i] > highs[i - 1] and highs[i] > highs[i + 1]:
            pivots.append(i)
    return pivots


def _pivot_lows(lows: np.ndarray, order: int = 3) -> List[int]:
    """Return indices of local low pivots (each surrounded by *order* higher bars)."""
    n = len(lows)
    pivots: List[int] = []
    for i in range(order, n - order):
        window = lows[i - order: i + order + 1]
        if lows[i] == window.min() and lows[i] < lows[i - 1] and lows[i] < lows[i + 1]:
            pivots.append(i)
    return pivots


def _volume_avg(volumes: np.ndarray, idx: int, window: int = VOLUME_AVG_BARS) -> float:
    """20-bar average volume ending just before *idx*."""
    start = max(0, idx - window)
    sub = volumes[start:idx]
    return float(sub.mean()) if len(sub) > 0 else 0.0


def _volume_confirmed(volumes: np.ndarray, breakout_idx: int) -> bool:
    """True if the bar at *breakout_idx* has volume > 1.5× its 20-bar avg."""
    avg = _volume_avg(volumes, breakout_idx)
    if avg <= 0:
        return False
    return volumes[breakout_idx] > VOLUME_BREAKOUT_MULTIPLIER * avg


def _linreg_slope_intercept(
    x: np.ndarray, y: np.ndarray
) -> Tuple[float, float]:
    """Returns (slope, intercept) of the least-squares line through (x, y)."""
    if len(x) < 2:
        return 0.0, float(y[0]) if len(y) else 0.0
    xm = x.mean()
    ym = y.mean()
    denom = ((x - xm) ** 2).sum()
    if denom == 0:
        return 0.0, ym
    slope = float(((x - xm) * (y - ym)).sum() / denom)
    intercept = float(ym - slope * xm)
    return slope, intercept


def _price_at(slope: float, intercept: float, x: float) -> float:
    return slope * x + intercept


# ─────────────────────────────────────────────
# Thin base so the module is self-contained
# ─────────────────────────────────────────────

class BaseFeatureExtractor(ABC):
    @abstractmethod
    def extract(self, snapshot: MarketSnapshot, features: FeatureSet) -> None: ...


# ─────────────────────────────────────────────
# ChartPatternExtractor
# ─────────────────────────────────────────────

class ChartPatternExtractor(BaseFeatureExtractor):
    """
    Detects chart patterns on 1h and 4h candles and populates
    FeatureSet.chart_patterns with ChartPatternDetection objects.

    Parameters
    ----------
    lookback : int
        Number of bars to consider per timeframe (default 200).
    timeframes : list[str]
        Timeframe keys to scan (default ["1h", "4h"]).
    """

    def __init__(
        self,
        lookback: int = PATTERN_LOOKBACK,
        timeframes: Optional[List[str]] = None,
    ) -> None:
        self.lookback = lookback
        self.timeframes = timeframes or TARGET_TIMEFRAMES

    # ── Public entry point ────────────────────────────────────────────────

    def extract(self, snapshot: MarketSnapshot, features: FeatureSet) -> None:
        """
        Scan each configured timeframe and append detected patterns to
        features.chart_patterns.  All exceptions are caught and recorded
        in features.extraction_errors so the pipeline never crashes.
        """
        for tf in self.timeframes:
            if tf not in snapshot.candles:
                continue
            candles = snapshot.candles[tf][-self.lookback:]
            if len(candles) < 30:
                continue
            try:
                detections = self._detect_all(candles, tf)
                features.chart_patterns.extend(detections)
            except Exception as exc:
                logger.exception("ChartPatternExtractor [%s] failed: %s", tf, exc)
                features.extraction_errors.append(f"chart_patterns[{tf}]: {exc}")

    # ── Dispatcher ────────────────────────────────────────────────────────

    def _detect_all(
        self, candles: List[Candle], timeframe: str
    ) -> List[ChartPatternDetection]:
        highs = np.array([c.high for c in candles], dtype=np.float64)
        lows = np.array([c.low for c in candles], dtype=np.float64)
        closes = np.array([c.close for c in candles], dtype=np.float64)
        volumes = np.array([c.volume for c in candles], dtype=np.float64)
        ts = [c.timestamp for c in candles]

        results: List[ChartPatternDetection] = []

        # Reversal
        for det in self._detect_head_and_shoulders(
            highs, lows, closes, volumes, ts, timeframe, inverse=False
        ):
            results.append(det)

        for det in self._detect_head_and_shoulders(
            highs, lows, closes, volumes, ts, timeframe, inverse=True
        ):
            results.append(det)

        for det in self._detect_double_top_bottom(
            highs, lows, closes, volumes, ts, timeframe, is_top=True
        ):
            results.append(det)

        for det in self._detect_double_top_bottom(
            highs, lows, closes, volumes, ts, timeframe, is_top=False
        ):
            results.append(det)

        # Continuation
        for det in self._detect_triangles(
            highs, lows, closes, volumes, ts, timeframe
        ):
            results.append(det)

        for det in self._detect_flags(
            highs, lows, closes, volumes, ts, timeframe, bull=True
        ):
            results.append(det)

        for det in self._detect_flags(
            highs, lows, closes, volumes, ts, timeframe, bull=False
        ):
            results.append(det)

        for det in self._detect_wedges(
            highs, lows, closes, volumes, ts, timeframe
        ):
            results.append(det)

        return results

    # ─────────────────────────────────────────────
    # Reversal: Head and Shoulders / Inverse H&S
    # ─────────────────────────────────────────────

    def _detect_head_and_shoulders(
        self,
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
        volumes: np.ndarray,
        timestamps: List[int],
        timeframe: str,
        inverse: bool,
    ) -> List[ChartPatternDetection]:
        """
        Head and Shoulders (inverse=False) or Inverse H&S (inverse=True).

        Standard H&S:
          - Three swing highs: left shoulder < head > right shoulder
          - Head is the highest of the three
          - Left/right shoulders within 5 % of each other
          - Neckline = horizontal through the two troughs between shoulders
          - Right shoulder volume < left shoulder volume
          - Target = head height measured from neckline downward

        Inverse H&S mirrors all of the above using swing lows.
        """
        detections: List[ChartPatternDetection] = []

        if inverse:
            peaks = _pivot_lows(lows, order=3)
            troughs = _pivot_highs(highs, order=3)
            peak_vals = lows
            trough_vals = highs
        else:
            peaks = _pivot_highs(highs, order=3)
            troughs = _pivot_lows(lows, order=3)
            peak_vals = highs
            trough_vals = lows

        # Need at least 3 peaks and 2 troughs
        if len(peaks) < 3 or len(troughs) < 2:
            return detections

        n = len(peaks)
        for i in range(n - 2):
            ls_idx = peaks[i]
            head_idx = peaks[i + 1]
            rs_idx = peaks[i + 2]

            ls_val = peak_vals[ls_idx]
            head_val = peak_vals[head_idx]
            rs_val = peak_vals[rs_idx]

            # Ordering: head must be higher (or lower for inverse) than both shoulders
            if not inverse:
                if not (head_val > ls_val and head_val > rs_val):
                    continue
                # Shoulders within 5 %
                if abs(ls_val - rs_val) / ls_val > 0.05:
                    continue
            else:
                if not (head_val < ls_val and head_val < rs_val):
                    continue
                if abs(ls_val - rs_val) / ls_val > 0.05:
                    continue

            # Find troughs between ls–head and head–rs
            t1_candidates = [
                t for t in troughs if ls_idx < t < head_idx
            ]
            t2_candidates = [
                t for t in troughs if head_idx < t < rs_idx
            ]
            if not t1_candidates or not t2_candidates:
                continue

            t1_idx = t1_candidates[-1]
            t2_idx = t2_candidates[0]

            t1_val = trough_vals[t1_idx]
            t2_val = trough_vals[t2_idx]

            # Neckline = average of the two trough prices
            neckline = (t1_val + t2_val) / 2.0

            # Volume condition: right shoulder volume < left shoulder volume
            ls_vol = volumes[ls_idx]
            rs_vol = volumes[rs_idx]
            vol_ok = rs_vol < ls_vol

            # Pattern height from head to neckline
            if not inverse:
                pattern_height = head_val - neckline
                target = neckline - pattern_height          # project downward
                invalidation = head_val                    # price reclaims head
                direction = Direction.SHORT
                pattern_name = "head_and_shoulders"
            else:
                pattern_height = neckline - head_val
                target = neckline + pattern_height          # project upward
                invalidation = head_val                    # price breaks below head
                direction = Direction.LONG
                pattern_name = "inverse_head_and_shoulders"

            # Confidence: base 0.5, +0.2 if volume confirms shoulder ratio,
            # +0.15 if shoulders are symmetric (< 3 %), +0.15 if neckline flat
            confidence = 0.50
            if vol_ok:
                confidence += 0.20
            if abs(ls_val - rs_val) / ls_val < 0.03:
                confidence += 0.15
            neckline_flatness = abs(t1_val - t2_val) / max(t1_val, t2_val)
            if neckline_flatness < 0.01:
                confidence += 0.15
            confidence = min(confidence, 1.0)

            # Volume confirmed = breakout bar at rs (last known bar near rs_idx)
            vol_confirmed = _volume_confirmed(volumes, rs_idx)

            detections.append(
                ChartPatternDetection(
                    pattern_name=pattern_name,
                    direction=direction,
                    confidence=round(confidence, 3),
                    neckline=round(neckline, 8),
                    target=round(target, 8),
                    invalidation=round(invalidation, 8),
                    volume_confirmed=vol_confirmed,
                    timeframe=timeframe,
                    formed_at_timestamp=timestamps[rs_idx],
                )
            )

        return detections

    # ─────────────────────────────────────────────
    # Reversal: Double Top / Double Bottom
    # ─────────────────────────────────────────────

    def _detect_double_top_bottom(
        self,
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
        volumes: np.ndarray,
        timestamps: List[int],
        timeframe: str,
        is_top: bool,
    ) -> List[ChartPatternDetection]:
        """
        Double Top  (is_top=True):
          - Two peaks within DOUBLE_TOP_TOLERANCE (1.5 %) of each other.
          - A trough separates them.
          - Confirmed: a close below the trough.

        Double Bottom (is_top=False): mirror.
        """
        detections: List[ChartPatternDetection] = []

        if is_top:
            peaks = _pivot_highs(highs, order=3)
            troughs = _pivot_lows(lows, order=3)
            peak_vals = highs
            trough_vals = lows
        else:
            peaks = _pivot_lows(lows, order=3)
            troughs = _pivot_highs(highs, order=3)
            peak_vals = lows
            trough_vals = highs

        if len(peaks) < 2:
            return detections

        for i in range(len(peaks) - 1):
            p1_idx = peaks[i]
            p2_idx = peaks[i + 1]

            # Minimum separation
            if p2_idx - p1_idx < MIN_SWING_SEPARATION:
                continue

            p1_val = peak_vals[p1_idx]
            p2_val = peak_vals[p2_idx]

            # Peaks within tolerance
            pct_diff = abs(p1_val - p2_val) / p1_val
            if pct_diff > DOUBLE_TOP_TOLERANCE:
                continue

            # Find trough between the two peaks
            mid_troughs = [t for t in troughs if p1_idx < t < p2_idx]
            if not mid_troughs:
                continue
            trough_idx = mid_troughs[0]
            trough_val = trough_vals[trough_idx]

            # Confirmation: price must close beyond the trough after p2
            confirmed = False
            confirm_bar = None
            for j in range(p2_idx + 1, len(closes)):
                if is_top:
                    if closes[j] < trough_val:
                        confirmed = True
                        confirm_bar = j
                        break
                else:
                    if closes[j] > trough_val:
                        confirmed = True
                        confirm_bar = j
                        break

            if not confirmed:
                continue

            if is_top:
                pattern_height = ((p1_val + p2_val) / 2.0) - trough_val
                neckline = trough_val
                target = neckline - pattern_height
                invalidation = max(p1_val, p2_val)
                direction = Direction.SHORT
                pattern_name = "double_top"
            else:
                pattern_height = trough_val - ((p1_val + p2_val) / 2.0)
                neckline = trough_val
                target = neckline + pattern_height
                invalidation = min(p1_val, p2_val)
                direction = Direction.LONG
                pattern_name = "double_bottom"

            # Confidence
            confidence = 0.55
            if pct_diff < 0.005:
                confidence += 0.15   # very tight peaks
            if confirm_bar is not None:
                vol_confirmed = _volume_confirmed(volumes, confirm_bar)
                if vol_confirmed:
                    confidence += 0.20
            else:
                vol_confirmed = False
            confidence = min(confidence, 1.0)

            formed_ts = timestamps[confirm_bar] if confirm_bar is not None else timestamps[p2_idx]

            detections.append(
                ChartPatternDetection(
                    pattern_name=pattern_name,
                    direction=direction,
                    confidence=round(confidence, 3),
                    neckline=round(neckline, 8),
                    target=round(target, 8),
                    invalidation=round(invalidation, 8),
                    volume_confirmed=vol_confirmed,
                    timeframe=timeframe,
                    formed_at_timestamp=formed_ts,
                )
            )

        return detections

    # ─────────────────────────────────────────────
    # Continuation: Triangles
    # ─────────────────────────────────────────────

    def _detect_triangles(
        self,
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
        volumes: np.ndarray,
        timestamps: List[int],
        timeframe: str,
    ) -> List[ChartPatternDetection]:
        """
        Detects ascending, descending, and symmetrical triangles.

        Method:
          - Fit a linear regression through the last N pivot highs (resistance line).
          - Fit a linear regression through the last N pivot lows (support line).
          - Classify by slope combination.
          - Require at least TRIANGLE_MIN_TOUCHES on each trendline.
          - Target = height of the pattern at the widest point, projected from breakout.
        """
        detections: List[ChartPatternDetection] = []

        n = len(highs)
        if n < 30:
            return detections

        ph_indices = _pivot_highs(highs, order=3)
        pl_indices = _pivot_lows(lows, order=3)

        if len(ph_indices) < TRIANGLE_MIN_TOUCHES or len(pl_indices) < TRIANGLE_MIN_TOUCHES:
            return detections

        # Use last N pivots
        ph = np.array(ph_indices[-8:])
        pl = np.array(pl_indices[-8:])

        res_slope, res_int = _linreg_slope_intercept(ph.astype(float), highs[ph])
        sup_slope, sup_int = _linreg_slope_intercept(pl.astype(float), lows[pl])

        # Residuals to count touches (bar within 0.5 % of the trendline)
        def _touches(indices: np.ndarray, vals: np.ndarray, slope: float, intercept: float) -> int:
            count = 0
            for idx in indices:
                expected = _price_at(slope, intercept, float(idx))
                if expected > 0 and abs(vals[idx] - expected) / expected < 0.005:
                    count += 1
            return count

        res_touches = _touches(ph, highs, res_slope, res_int)
        sup_touches = _touches(pl, lows, sup_slope, sup_int)

        if res_touches < TRIANGLE_MIN_TOUCHES or sup_touches < TRIANGLE_MIN_TOUCHES:
            return detections

        # Pattern height at earliest point in the window
        window_start = int(min(ph[0], pl[0]))
        res_at_start = _price_at(res_slope, res_int, float(window_start))
        sup_at_start = _price_at(sup_slope, sup_int, float(window_start))
        pattern_height = abs(res_at_start - sup_at_start)

        last_close = closes[-1]

        # Classify
        flat_threshold = abs(res_slope) * 0.3   # slope considered "flat" if very small

        res_flat = abs(res_slope) < flat_threshold or abs(res_slope) < 1e-6
        sup_flat = abs(sup_slope) < flat_threshold or abs(sup_slope) < 1e-6

        if res_flat and sup_slope > 0:
            # Ascending triangle: flat resistance, rising support
            pattern_name = "ascending_triangle"
            direction = Direction.LONG
            breakout_level = _price_at(res_slope, res_int, float(n - 1))
            target = breakout_level + pattern_height
            invalidation = _price_at(sup_slope, sup_int, float(n - 1))
            confidence = 0.60

        elif sup_flat and res_slope < 0:
            # Descending triangle: flat support, falling resistance
            pattern_name = "descending_triangle"
            direction = Direction.SHORT
            breakout_level = _price_at(sup_slope, sup_int, float(n - 1))
            target = breakout_level - pattern_height
            invalidation = _price_at(res_slope, res_int, float(n - 1))
            confidence = 0.60

        elif res_slope < 0 and sup_slope > 0:
            # Symmetrical triangle: converging trendlines
            pattern_name = "symmetrical_triangle"
            # Direction inferred from price position relative to mid
            mid = (_price_at(res_slope, res_int, float(n - 1)) +
                   _price_at(sup_slope, sup_int, float(n - 1))) / 2.0
            direction = Direction.LONG if last_close > mid else Direction.SHORT
            if direction == Direction.LONG:
                breakout_level = _price_at(res_slope, res_int, float(n - 1))
                target = breakout_level + pattern_height
                invalidation = _price_at(sup_slope, sup_int, float(n - 1))
            else:
                breakout_level = _price_at(sup_slope, sup_int, float(n - 1))
                target = breakout_level - pattern_height
                invalidation = _price_at(res_slope, res_int, float(n - 1))
            confidence = 0.50

        else:
            return detections

        # Boost confidence for extra trendline touches
        extra = (res_touches - TRIANGLE_MIN_TOUCHES) + (sup_touches - TRIANGLE_MIN_TOUCHES)
        confidence = min(confidence + extra * 0.05, 1.0)

        # Volume confirmed: last bar volume above threshold
        vol_confirmed = _volume_confirmed(volumes, n - 1)

        detections.append(
            ChartPatternDetection(
                pattern_name=pattern_name,
                direction=direction,
                confidence=round(confidence, 3),
                neckline=None,
                target=round(target, 8),
                invalidation=round(invalidation, 8),
                volume_confirmed=vol_confirmed,
                timeframe=timeframe,
                formed_at_timestamp=timestamps[-1],
            )
        )

        return detections

    # ─────────────────────────────────────────────
    # Continuation: Bull / Bear Flags
    # ─────────────────────────────────────────────

    def _detect_flags(
        self,
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
        volumes: np.ndarray,
        timestamps: List[int],
        timeframe: str,
        bull: bool,
    ) -> List[ChartPatternDetection]:
        """
        Bull Flag (bull=True):
          - Identify a sharp upward pole (≥ FLAG_POLE_MIN_PCT over FLAG_POLE_MIN_BARS).
          - Followed by a tight rectangular pullback channel (≤ 50 % retrace of pole,
            no longer than FLAG_CHANNEL_MAX_BARS bars).
          - Direction: LONG; target = pole height projected from breakout.

        Bear Flag (bull=False): mirror using downward pole.
        """
        detections: List[ChartPatternDetection] = []
        n = len(closes)

        for pole_end in range(FLAG_POLE_MIN_BARS, n - 5):
            pole_start = pole_end - FLAG_POLE_MIN_BARS

            if bull:
                pole_move = (closes[pole_end] - closes[pole_start]) / closes[pole_start]
                if pole_move < FLAG_POLE_MIN_PCT:
                    continue
            else:
                pole_move = (closes[pole_start] - closes[pole_end]) / closes[pole_start]
                if pole_move < FLAG_POLE_MIN_PCT:
                    continue

            pole_height = abs(closes[pole_end] - closes[pole_start])

            # Channel: bars immediately after pole end
            ch_start = pole_end + 1
            ch_end_max = min(ch_start + FLAG_CHANNEL_MAX_BARS, n)

            if ch_start >= n:
                continue

            channel_closes = closes[ch_start:ch_end_max]
            if len(channel_closes) < 3:
                continue

            if bull:
                retrace = (closes[pole_end] - channel_closes.min()) / pole_height
                # Pullback should not exceed 50 % of pole
                if retrace > FLAG_CHANNEL_MAX_RETRACE:
                    continue
                # Channel should be downward sloping or flat
                ch_slope, _ = _linreg_slope_intercept(
                    np.arange(len(channel_closes), dtype=float), channel_closes
                )
                if ch_slope > 0:
                    continue
                target = closes[ch_end_max - 1] + pole_height
                invalidation = channel_closes.min()
                direction = Direction.LONG
                pattern_name = "bull_flag"
            else:
                retrace = (channel_closes.max() - closes[pole_end]) / pole_height
                if retrace > FLAG_CHANNEL_MAX_RETRACE:
                    continue
                ch_slope, _ = _linreg_slope_intercept(
                    np.arange(len(channel_closes), dtype=float), channel_closes
                )
                if ch_slope < 0:
                    continue
                target = closes[ch_end_max - 1] - pole_height
                invalidation = channel_closes.max()
                direction = Direction.SHORT
                pattern_name = "bear_flag"

            # Volume: pole should have higher volume than channel
            pole_vol_avg = volumes[pole_start:pole_end].mean() if pole_end > pole_start else 0
            ch_vol_avg = volumes[ch_start:ch_end_max].mean() if ch_end_max > ch_start else 0
            vol_confirmed = pole_vol_avg > ch_vol_avg

            confidence = 0.55
            if vol_confirmed:
                confidence += 0.20
            # Tighter retrace → higher confidence
            if bull and retrace < 0.25:
                confidence += 0.10
            elif not bull and retrace < 0.25:
                confidence += 0.10
            confidence = min(confidence, 1.0)

            detections.append(
                ChartPatternDetection(
                    pattern_name=pattern_name,
                    direction=direction,
                    confidence=round(confidence, 3),
                    neckline=None,
                    target=round(target, 8),
                    invalidation=round(invalidation, 8),
                    volume_confirmed=bool(vol_confirmed),
                    timeframe=timeframe,
                    formed_at_timestamp=timestamps[ch_end_max - 1],
                )
            )
            # Only report the most recent flag per direction per timeframe
            break

        return detections

    # ─────────────────────────────────────────────
    # Continuation: Rising / Falling Wedges
    # ─────────────────────────────────────────────

    def _detect_wedges(
        self,
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
        volumes: np.ndarray,
        timestamps: List[int],
        timeframe: str,
    ) -> List[ChartPatternDetection]:
        """
        Rising Wedge  – both resistance and support trendlines slope upward
                         and are converging → bearish signal.
        Falling Wedge – both slope downward and are converging → bullish signal.

        Convergence criterion: the gap between the two lines is narrowing
        (resistance slope < support slope for rising; resistance slope >
        support slope for falling).
        """
        detections: List[ChartPatternDetection] = []

        n = len(highs)
        if n < 30:
            return detections

        ph_indices = _pivot_highs(highs, order=3)
        pl_indices = _pivot_lows(lows, order=3)

        if len(ph_indices) < TRIANGLE_MIN_TOUCHES or len(pl_indices) < TRIANGLE_MIN_TOUCHES:
            return detections

        ph = np.array(ph_indices[-6:])
        pl = np.array(pl_indices[-6:])

        res_slope, res_int = _linreg_slope_intercept(ph.astype(float), highs[ph])
        sup_slope, sup_int = _linreg_slope_intercept(pl.astype(float), lows[pl])

        # Pattern height at left edge
        window_start = float(min(ph[0], pl[0]))
        res_at_start = _price_at(res_slope, res_int, window_start)
        sup_at_start = _price_at(sup_slope, sup_int, window_start)
        pattern_height = abs(res_at_start - sup_at_start)

        last_idx = float(n - 1)
        res_now = _price_at(res_slope, res_int, last_idx)
        sup_now = _price_at(sup_slope, sup_int, last_idx)

        # Both lines must slope in the same direction
        both_up = res_slope > 0 and sup_slope > 0
        both_down = res_slope < 0 and sup_slope < 0

        if not both_up and not both_down:
            return detections

        # Convergence: lines must be getting closer
        gap_now = abs(res_now - sup_now)
        gap_then = pattern_height
        if gap_now >= gap_then:
            return detections

        if both_up:
            # Rising wedge → bearish
            pattern_name = "rising_wedge"
            direction = Direction.SHORT
            target = sup_now - pattern_height
            invalidation = res_now
        else:
            # Falling wedge → bullish
            pattern_name = "falling_wedge"
            direction = Direction.LONG
            target = res_now + pattern_height
            invalidation = sup_now

        # Confidence based on convergence ratio
        convergence_ratio = 1.0 - (gap_now / gap_then) if gap_then > 0 else 0.0
        confidence = 0.45 + convergence_ratio * 0.35
        confidence = min(confidence, 0.85)

        vol_confirmed = _volume_confirmed(volumes, n - 1)

        detections.append(
            ChartPatternDetection(
                pattern_name=pattern_name,
                direction=direction,
                confidence=round(confidence, 3),
                neckline=None,
                target=round(target, 8),
                invalidation=round(invalidation, 8),
                volume_confirmed=vol_confirmed,
                timeframe=timeframe,
                formed_at_timestamp=timestamps[-1],
            )
        )

        return detections
