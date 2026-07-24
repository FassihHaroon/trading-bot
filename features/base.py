"""
Base class and shared utilities for all feature extractors.

Every feature module inherits from BaseFeatureExtractor and implements
the extract() method.  All indicator math lives here so individual
modules stay focused on logic rather than arithmetic.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import List

import numpy as np

from config.settings import FeatureConfig
from data.schemas import Candle, MarketSnapshot


# ─────────────────────────────────────────────
# Custom exception
# ─────────────────────────────────────────────

class FeatureExtractionError(Exception):
    """Raised when a feature extractor cannot produce a valid result."""

    def __init__(self, extractor: str, reason: str) -> None:
        self.extractor = extractor
        self.reason = reason
        super().__init__(f"[{extractor}] {reason}")


# ─────────────────────────────────────────────
# Abstract base extractor
# ─────────────────────────────────────────────

class BaseFeatureExtractor(ABC):
    """
    Abstract base class for all feature extraction modules.

    Subclasses must implement extract().  The protected helper methods
    (_ema, _sma, _rsi, _atr, …) provide numpy-only indicator math so
    that individual modules never need pandas or an external TA library.
    """

    def __init__(self, config: FeatureConfig) -> None:
        self.config = config
        self._logger = logging.getLogger(self.__class__.__name__)

    # ── Public interface ──────────────────────────────────────────────

    @abstractmethod
    def extract(self, snapshot: MarketSnapshot) -> dict:
        """
        Compute features from *snapshot* and return them as a plain dict.

        The dict is merged into FeatureSet by the orchestrating pipeline.
        Raise FeatureExtractionError on unrecoverable failures; return
        partial results with extraction_errors populated for soft failures.
        """

    # ── Array helpers ─────────────────────────────────────────────────

    @staticmethod
    def _get_closes(candles: List[Candle]) -> np.ndarray:
        """Return close prices as a float64 array, oldest-first."""
        return np.array([c.close for c in candles], dtype=np.float64)

    @staticmethod
    def _get_highs(candles: List[Candle]) -> np.ndarray:
        """Return high prices as a float64 array, oldest-first."""
        return np.array([c.high for c in candles], dtype=np.float64)

    @staticmethod
    def _get_lows(candles: List[Candle]) -> np.ndarray:
        """Return low prices as a float64 array, oldest-first."""
        return np.array([c.low for c in candles], dtype=np.float64)

    @staticmethod
    def _get_volumes(candles: List[Candle]) -> np.ndarray:
        """Return volumes as a float64 array, oldest-first."""
        return np.array([c.volume for c in candles], dtype=np.float64)

    # ── Indicator implementations ─────────────────────────────────────

    @staticmethod
    def _ema(values: np.ndarray, period: int) -> np.ndarray:
        """
        Exponential moving average — Wilder/standard EMA.

        Uses the standard smoothing factor k = 2 / (period + 1).
        The first valid value is seeded with the SMA of the first *period*
        elements.  All output values before index (period - 1) are np.nan.

        Parameters
        ----------
        values : np.ndarray
            Input time series, oldest-first.
        period : int
            Look-back window (must be >= 1).

        Returns
        -------
        np.ndarray
            EMA values, same length as *values*.
        """
        if period < 1:
            raise ValueError(f"EMA period must be >= 1, got {period}")

        n = len(values)
        result = np.full(n, np.nan, dtype=np.float64)

        if n < period:
            return result

        k = 2.0 / (period + 1)

        # Seed with SMA of first *period* bars
        result[period - 1] = np.mean(values[:period])

        for i in range(period, n):
            result[i] = values[i] * k + result[i - 1] * (1.0 - k)

        return result

    @staticmethod
    def _sma(values: np.ndarray, period: int) -> np.ndarray:
        """
        Simple moving average.

        Parameters
        ----------
        values : np.ndarray
            Input time series, oldest-first.
        period : int
            Look-back window (must be >= 1).

        Returns
        -------
        np.ndarray
            SMA values, same length as *values*.  The first (period - 1)
            values are np.nan.
        """
        if period < 1:
            raise ValueError(f"SMA period must be >= 1, got {period}")

        n = len(values)
        result = np.full(n, np.nan, dtype=np.float64)

        if n < period:
            return result

        # Use cumulative sum for O(n) computation
        cumsum = np.cumsum(values)
        result[period - 1] = cumsum[period - 1] / period
        result[period:] = (cumsum[period:] - cumsum[: n - period]) / period

        return result

    @staticmethod
    def _rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
        """
        Relative Strength Index (Wilder's smoothed RSI).

        Uses Wilder's smoothing (equivalent to EMA with alpha = 1/period)
        rather than a simple rolling average, matching most charting
        platforms.

        Parameters
        ----------
        closes : np.ndarray
            Close prices, oldest-first.
        period : int
            Look-back window (default 14).

        Returns
        -------
        np.ndarray
            RSI in [0, 100].  The first *period* values are np.nan.
        """
        if period < 1:
            raise ValueError(f"RSI period must be >= 1, got {period}")

        n = len(closes)
        result = np.full(n, np.nan, dtype=np.float64)

        if n <= period:
            return result

        deltas = np.diff(closes)                     # length n-1
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)

        # Seed: simple average of first *period* gains/losses
        avg_gain = np.mean(gains[:period])
        avg_loss = np.mean(losses[:period])

        # First RSI value sits at index *period* (needs *period* deltas = *period+1* closes)
        if avg_loss == 0.0:
            result[period] = 100.0
        else:
            rs = avg_gain / avg_loss
            result[period] = 100.0 - 100.0 / (1.0 + rs)

        # Wilder smoothing for subsequent values
        alpha = 1.0 / period
        for i in range(period + 1, n):
            avg_gain = avg_gain * (1.0 - alpha) + gains[i - 1] * alpha
            avg_loss = avg_loss * (1.0 - alpha) + losses[i - 1] * alpha
            if avg_loss == 0.0:
                result[i] = 100.0
            else:
                rs = avg_gain / avg_loss
                result[i] = 100.0 - 100.0 / (1.0 + rs)

        return result

    @staticmethod
    def _atr(
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
        period: int = 14,
    ) -> np.ndarray:
        """
        Average True Range using Wilder's smoothing.

        True Range = max(high - low,
                         |high - prev_close|,
                         |low  - prev_close|)

        Parameters
        ----------
        highs, lows, closes : np.ndarray
            OHLC arrays of equal length, oldest-first.
        period : int
            Smoothing period (default 14).

        Returns
        -------
        np.ndarray
            ATR values, same length as input.  First *period* values are
            np.nan (the seed uses a simple average of the first *period*
            true ranges, which themselves start at index 1).
        """
        if period < 1:
            raise ValueError(f"ATR period must be >= 1, got {period}")

        n = len(closes)
        result = np.full(n, np.nan, dtype=np.float64)

        if n < period + 1:
            return result

        prev_closes = closes[:-1]           # length n-1
        current_highs = highs[1:]
        current_lows = lows[1:]

        hl = current_highs - current_lows
        hc = np.abs(current_highs - prev_closes)
        lc = np.abs(current_lows - prev_closes)

        tr = np.maximum(hl, np.maximum(hc, lc))   # length n-1

        # Seed: simple mean of first *period* true ranges (indices 0..period-1 of tr)
        result[period] = np.mean(tr[:period])

        alpha = 1.0 / period
        for i in range(period + 1, n):
            result[i] = result[i - 1] * (1.0 - alpha) + tr[i - 1] * alpha

        return result

    # ── Logging helper ────────────────────────────────────────────────

    def _log(self, msg: str) -> None:
        """Emit a DEBUG-level message prefixed with the extractor class name."""
        self._logger.debug("[%s] %s", self.__class__.__name__, msg)
