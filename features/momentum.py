"""
Momentum indicators feature extractor.

Computes RSI, MACD, Stochastics, and ATR from 1-hour candles and writes
the results directly onto the FeatureSet fields defined in data/schemas.py.
"""

from __future__ import annotations

from typing import List, Optional

import numpy as np

from config.settings import FeatureConfig
from data.schemas import Candle, FeatureSet, MarketSnapshot
from features.base import BaseFeatureExtractor, FeatureExtractionError

# Minimum number of 1h candles required to produce all indicators.
# MACD needs 26 (slow EMA) + 9 (signal) - 1 = 34 candles to yield the first
# signal line value.  ATR and RSI both need 15 (period + 1).  We pick 60 so
# there is enough history for stable Wilder smoothing.
_MIN_CANDLES = 60

# Timeframe key that holds the 1-hour candle list inside MarketSnapshot.candles
_TF_1H = "1h"

# Indicator parameters
_RSI_PERIOD = 14
_MACD_FAST = 12
_MACD_SLOW = 26
_MACD_SIGNAL = 9
_STOCH_K_PERIOD = 14
_STOCH_D_PERIOD = 3
_ATR_PERIOD = 14


class MomentumExtractor(BaseFeatureExtractor):
    """
    Extracts momentum indicators from 1-hour candles and populates the
    momentum section of a FeatureSet.

    Fields written:
        rsi, rsi_trend, rsi_zone,
        macd_line, macd_signal_line, macd_histogram, macd_cross,
        macd_histogram_trend,
        stoch_k, stoch_d, stoch_cross,
        atr, atr_pct
    """

    def __init__(self, config: FeatureConfig) -> None:
        super().__init__(config)

    # ── Public interface ──────────────────────────────────────────────

    def extract(self, snapshot: MarketSnapshot) -> dict:
        """
        Compute momentum features from *snapshot*.

        Returns a plain dict with all momentum keys so the pipeline can
        merge it into an existing FeatureSet, OR call extract_into() to
        write directly onto a FeatureSet.

        Raises FeatureExtractionError when the 1h candle series is absent
        or too short for meaningful computation.
        """
        candles = self._get_1h_candles(snapshot)
        self._validate_candles(candles)

        closes = self._get_closes(candles)
        highs = self._get_highs(candles)
        lows = self._get_lows(candles)

        # ── RSI ───────────────────────────────────────────────────────
        rsi_series = self._rsi(closes, _RSI_PERIOD)
        rsi_val = self._last_valid(rsi_series, "RSI")

        rsi_trend = self._compute_rsi_trend(rsi_series)
        rsi_zone = self._compute_rsi_zone(rsi_val)

        # ── MACD ──────────────────────────────────────────────────────
        ema_fast = self._ema(closes, _MACD_FAST)
        ema_slow = self._ema(closes, _MACD_SLOW)
        macd_line_series = ema_fast - ema_slow

        # Signal line is an EMA of the MACD line.  We must compute it only
        # over positions where macd_line_series is valid (not NaN).
        macd_signal_series = self._ema_on_valid(macd_line_series, _MACD_SIGNAL)
        macd_hist_series = macd_line_series - macd_signal_series

        macd_line_val = self._last_valid(macd_line_series, "MACD line")
        macd_signal_val = self._last_valid(macd_signal_series, "MACD signal")
        macd_hist_val = self._last_valid(macd_hist_series, "MACD histogram")

        macd_cross = self._compute_macd_cross(macd_line_series, macd_signal_series)
        macd_hist_trend = self._compute_macd_histogram_trend(macd_hist_series)

        # ── Stochastics ───────────────────────────────────────────────
        k_series = self._stoch_k(highs, lows, closes, _STOCH_K_PERIOD)
        d_series = self._sma(k_series, _STOCH_D_PERIOD)

        stoch_k_val = self._last_valid(k_series, "Stoch %K")
        stoch_d_val = self._last_valid(d_series, "Stoch %D")
        stoch_cross = self._compute_stoch_cross(k_series, d_series)

        # ── ATR ───────────────────────────────────────────────────────
        atr_series = self._atr(highs, lows, closes, _ATR_PERIOD)
        atr_val = self._last_valid(atr_series, "ATR")
        current_close = float(closes[-1])
        atr_pct = (atr_val / current_close * 100.0) if current_close != 0.0 else 0.0

        return {
            "rsi": round(rsi_val, 4),
            "rsi_trend": rsi_trend,
            "rsi_zone": rsi_zone,
            "macd_line": round(macd_line_val, 8),
            "macd_signal_line": round(macd_signal_val, 8),
            "macd_histogram": round(macd_hist_val, 8),
            "macd_cross": macd_cross,
            "macd_histogram_trend": macd_hist_trend,
            "stoch_k": round(stoch_k_val, 4),
            "stoch_d": round(stoch_d_val, 4),
            "stoch_cross": stoch_cross,
            "atr": round(atr_val, 8),
            "atr_pct": round(atr_pct, 4),
        }

    def extract_into(self, snapshot: MarketSnapshot, feature_set: FeatureSet) -> None:
        """
        Convenience method: compute momentum features and write them
        directly onto *feature_set* in-place.
        """
        result = self.extract(snapshot)
        feature_set.rsi = result["rsi"]
        feature_set.rsi_trend = result["rsi_trend"]
        feature_set.rsi_zone = result["rsi_zone"]
        feature_set.macd_line = result["macd_line"]
        feature_set.macd_signal_line = result["macd_signal_line"]
        feature_set.macd_histogram = result["macd_histogram"]
        feature_set.macd_cross = result["macd_cross"]
        feature_set.macd_histogram_trend = result["macd_histogram_trend"]
        feature_set.stoch_k = result["stoch_k"]
        feature_set.stoch_d = result["stoch_d"]
        feature_set.stoch_cross = result["stoch_cross"]
        feature_set.atr = result["atr"]
        feature_set.atr_pct = result["atr_pct"]

    # ── Private helpers ───────────────────────────────────────────────

    def _get_1h_candles(self, snapshot: MarketSnapshot) -> List[Candle]:
        """Return the 1h candle list from the snapshot, or raise."""
        candles = snapshot.candles.get(_TF_1H)
        if not candles:
            raise FeatureExtractionError(
                self.__class__.__name__,
                f"No '{_TF_1H}' candles found in MarketSnapshot for {snapshot.symbol}",
            )
        return candles

    def _validate_candles(self, candles: List[Candle]) -> None:
        if len(candles) < _MIN_CANDLES:
            raise FeatureExtractionError(
                self.__class__.__name__,
                f"Need >= {_MIN_CANDLES} 1h candles, got {len(candles)}",
            )

    @staticmethod
    def _last_valid(series: np.ndarray, name: str, default: float = 50.0) -> float:
        """Return the last non-NaN value, or a neutral default when all-NaN."""
        valid = series[~np.isnan(series)]
        if len(valid) == 0:
            return default  # Neutral fallback — not enough bars for this indicator
        return float(valid[-1])

    # ── RSI helpers ───────────────────────────────────────────────────

    @staticmethod
    def _compute_rsi_trend(rsi_series: np.ndarray, lookback: int = 3) -> str:
        """
        Classify the short-term RSI direction over the last *lookback* bars.

        "rising"  — RSI is net higher than it was *lookback* bars ago.
        "falling" — RSI is net lower.
        "flat"    — change is within a ±0.5 tolerance band.
        """
        valid_idx = np.where(~np.isnan(rsi_series))[0]
        if len(valid_idx) < lookback + 1:
            return "flat"

        current = rsi_series[valid_idx[-1]]
        previous = rsi_series[valid_idx[-1 - lookback]]
        delta = current - previous

        if delta > 0.5:
            return "rising"
        if delta < -0.5:
            return "falling"
        return "flat"

    @staticmethod
    def _compute_rsi_zone(rsi_val: float) -> str:
        """
        Map an RSI value to its zone.

        NOTE: This simply reports the zone — it does NOT imply a reversal.
        Callers that need to account for trending conditions should check
        rsi_zone together with trend_direction before drawing conclusions.
        """
        if rsi_val > 70.0:
            return "overbought"
        if rsi_val < 30.0:
            return "oversold"
        return "neutral"

    # ── MACD helpers ──────────────────────────────────────────────────

    @staticmethod
    def _ema_on_valid(series: np.ndarray, period: int) -> np.ndarray:
        """
        Compute a standard EMA over a series that may contain leading NaNs.

        The EMA is anchored at the first run of *period* consecutive valid
        (non-NaN) values so that the signal line is not displaced by the
        NaN warm-up gap of the slow EMA.
        """
        n = len(series)
        result = np.full(n, np.nan, dtype=np.float64)

        # Find first non-NaN index
        valid_mask = ~np.isnan(series)
        valid_indices = np.where(valid_mask)[0]
        if len(valid_indices) < period:
            return result

        start = valid_indices[0]
        # Need at least *period* valid values from start
        if len(valid_indices) < period:
            return result

        k = 2.0 / (period + 1)

        # Seed with SMA of first *period* valid values
        seed_end = start + period  # exclusive
        if seed_end > n:
            return result

        result[seed_end - 1] = np.mean(series[start:seed_end])

        for i in range(seed_end, n):
            if np.isnan(series[i]):
                # Propagate last valid EMA (edge case: gap in source)
                result[i] = result[i - 1]
            else:
                result[i] = series[i] * k + result[i - 1] * (1.0 - k)

        return result

    @staticmethod
    def _compute_macd_cross(
        macd_line: np.ndarray,
        signal_line: np.ndarray,
        lookback: int = 2,
    ) -> Optional[str]:
        """
        Detect whether a MACD/signal crossover occurred within the last
        *lookback* completed bars.

        Returns "bullish_cross", "bearish_cross", or None.
        Only the most recent cross within the window is returned.
        """
        # We need at least lookback+1 bars with valid values in both series
        valid_mask = ~np.isnan(macd_line) & ~np.isnan(signal_line)
        valid_idx = np.where(valid_mask)[0]

        if len(valid_idx) < lookback + 1:
            return None

        # Walk backwards through the last *lookback* completed bars
        # (bar[-1] is the current bar, bar[-2] is the previous bar, etc.)
        # A cross at position i means:
        #   macd[i-1] was on one side, macd[i] crossed to the other.
        for j in range(len(valid_idx) - 1, max(len(valid_idx) - 1 - lookback, 0), -1):
            curr_idx = valid_idx[j]
            prev_idx = valid_idx[j - 1]

            diff_curr = macd_line[curr_idx] - signal_line[curr_idx]
            diff_prev = macd_line[prev_idx] - signal_line[prev_idx]

            if diff_prev < 0.0 and diff_curr > 0.0:
                return "bullish_cross"
            if diff_prev > 0.0 and diff_curr < 0.0:
                return "bearish_cross"

        return None

    @staticmethod
    def _compute_macd_histogram_trend(
        hist_series: np.ndarray,
        lookback: int = 3,
        flat_threshold: float = 1e-8,
    ) -> str:
        """
        Classify the current momentum state of the MACD histogram.

        "expanding_bullish"  — histogram is positive and growing (more positive).
        "expanding_bearish"  — histogram is negative and growing (more negative).
        "contracting"        — bars are moving toward zero regardless of sign.
        "flat"               — negligible change.
        """
        valid_idx = np.where(~np.isnan(hist_series))[0]
        if len(valid_idx) < lookback + 1:
            return "flat"

        # Use the last *lookback* valid bars
        recent = hist_series[valid_idx[-(lookback + 1):]]
        if len(recent) < 2:
            return "flat"

        current = recent[-1]
        previous = recent[0]
        delta = current - previous

        if abs(delta) <= flat_threshold:
            return "flat"

        # Determine whether magnitude is growing or shrinking
        abs_delta = abs(current) - abs(previous)
        if abs_delta > flat_threshold:
            # Magnitude expanding
            if current > 0.0:
                return "expanding_bullish"
            return "expanding_bearish"

        # Magnitude contracting (moving toward zero)
        return "contracting"

    # ── Stochastics helpers ───────────────────────────────────────────

    @staticmethod
    def _stoch_k(
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
        period: int = 14,
    ) -> np.ndarray:
        """
        Compute raw (fast) Stochastic %K.

        %K[i] = (close[i] - lowest_low[i-period+1:i+1])
                / (highest_high[i-period+1:i+1] - lowest_low[i-period+1:i+1])
                * 100

        Returns np.nan where the high-low range is zero.
        """
        n = len(closes)
        result = np.full(n, np.nan, dtype=np.float64)

        if n < period:
            return result

        for i in range(period - 1, n):
            window_highs = highs[i - period + 1: i + 1]
            window_lows = lows[i - period + 1: i + 1]
            highest = np.max(window_highs)
            lowest = np.min(window_lows)
            denom = highest - lowest
            if denom == 0.0:
                # Flat market — %K undefined; default to 50
                result[i] = 50.0
            else:
                result[i] = (closes[i] - lowest) / denom * 100.0

        return result

    @staticmethod
    def _compute_stoch_cross(
        k_series: np.ndarray,
        d_series: np.ndarray,
        lookback: int = 2,
    ) -> Optional[str]:
        """
        Detect a %K/%D crossover within the last *lookback* bars.

        Returns "bullish_cross", "bearish_cross", or None.
        """
        valid_mask = ~np.isnan(k_series) & ~np.isnan(d_series)
        valid_idx = np.where(valid_mask)[0]

        if len(valid_idx) < lookback + 1:
            return None

        for j in range(len(valid_idx) - 1, max(len(valid_idx) - 1 - lookback, 0), -1):
            curr_idx = valid_idx[j]
            prev_idx = valid_idx[j - 1]

            diff_curr = k_series[curr_idx] - d_series[curr_idx]
            diff_prev = k_series[prev_idx] - d_series[prev_idx]

            if diff_prev < 0.0 and diff_curr > 0.0:
                return "bullish_cross"
            if diff_prev > 0.0 and diff_curr < 0.0:
                return "bearish_cross"

        return None
