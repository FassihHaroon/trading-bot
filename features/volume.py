"""
Volume Analysis Feature Extractor
Populates volume-related fields on FeatureSet and futures-derived fields
(funding_bias, oi_trend, liquidation_imbalance) when FuturesData is present.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import List, Optional

from data.schemas import Candle, FeatureSet, FuturesData, MarketSnapshot

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Base class (thin interface — avoids circular imports when a shared
# base module does not yet exist in the project)
# ─────────────────────────────────────────────

class BaseFeatureExtractor(ABC):
    """Minimal interface every feature extractor must satisfy."""

    @abstractmethod
    def extract(self, snapshot: MarketSnapshot, features: FeatureSet) -> None:
        """
        Read from *snapshot*, write results into *features* in-place.
        Must never raise — catch all exceptions and append to
        features.extraction_errors instead.
        """


# ─────────────────────────────────────────────
# Constants / tunables
# ─────────────────────────────────────────────

VOLUME_AVG_BARS: int = 20          # window for the baseline average
VOLUME_RECENT_BARS: int = 5        # "recent" window for trend + taker delta
CLIMAX_THRESHOLD: float = 3.0      # ×average to declare a volume climax
CLIMAX_LOOKBACK: int = 3           # how many trailing bars to inspect for climax
OBV_TREND_BARS: int = 20           # OBV slope window (same as avg window)

# Futures OI thresholds (relative change between two snapshots is not
# available here, so we classify using the raw value change proxy via
# the taker_buy_sell_ratio as a directional hint).
OI_RISING_RATIO: float = 1.05      # taker ratio above this → OI rising with buys
OI_FALLING_RATIO: float = 0.95     # taker ratio below this → OI falling with sells


# ─────────────────────────────────────────────
# Helper utilities
# ─────────────────────────────────────────────

def _volumes(candles: List[Candle]) -> List[float]:
    return [c.volume for c in candles]


def _closes(candles: List[Candle]) -> List[float]:
    return [c.close for c in candles]


def _mean(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _compute_obv(candles: List[Candle]) -> List[float]:
    """
    Running OBV.
    +volume when close > prev_close
    -volume when close < prev_close
     0      when close == prev_close
    Returns a list of the same length as *candles*; first element is 0.
    """
    obv: List[float] = [0.0]
    for i in range(1, len(candles)):
        prev_close = candles[i - 1].close
        cur_close  = candles[i].close
        cur_vol    = candles[i].volume
        if cur_close > prev_close:
            obv.append(obv[-1] + cur_vol)
        elif cur_close < prev_close:
            obv.append(obv[-1] - cur_vol)
        else:
            obv.append(obv[-1])
    return obv


def _linear_slope(values: List[float]) -> float:
    """
    Least-squares slope of *values* (index as x).
    Returns a positive number for upward trend, negative for downward.
    """
    n = len(values)
    if n < 2:
        return 0.0
    x_mean = (n - 1) / 2.0
    y_mean = _mean(values)
    numerator   = sum((i - x_mean) * (values[i] - y_mean) for i in range(n))
    denominator = sum((i - x_mean) ** 2 for i in range(n))
    return numerator / denominator if denominator != 0 else 0.0


def _classify_slope(slope: float, tolerance: float = 0.0) -> str:
    """Map a slope to 'up'/'down'/'flat'."""
    if slope > tolerance:
        return "up"
    if slope < -tolerance:
        return "down"
    return "flat"


# ─────────────────────────────────────────────
# Main extractor
# ─────────────────────────────────────────────

class VolumeExtractor(BaseFeatureExtractor):
    """
    Extracts volume-based features from the primary timeframe candles
    (the first key in snapshot.candles) and optional FuturesData.

    Fields written to FeatureSet
    ─────────────────────────────
    Volume:
      volume_trend          – "increasing" | "decreasing" | "flat"
      volume_vs_avg         – current bar volume / 20-bar average
      obv_trend             – "up" | "down" | "flat"
      volume_climax         – True if any bar in last CLIMAX_LOOKBACK bars
                              exceeds CLIMAX_THRESHOLD × 20-bar average
      taker_delta           – cumulative (taker_buy_volume − taker_sell_volume)
                              over the last VOLUME_RECENT_BARS bars

    Futures (only when snapshot.futures is not None):
      funding_bias          – from FuturesData.funding_bias property
      oi_trend              – "increasing" | "decreasing" | "flat"
      liquidation_imbalance – "long_heavy" | "short_heavy" | "neutral"
    """

    def __init__(
        self,
        primary_timeframe: Optional[str] = None,
        climax_threshold: float = CLIMAX_THRESHOLD,
    ) -> None:
        """
        Parameters
        ----------
        primary_timeframe:
            Key to use from snapshot.candles.  When None the extractor picks
            the first available key.
        climax_threshold:
            Multiplier above the 20-bar average that flags a volume climax.
        """
        self.primary_timeframe = primary_timeframe
        self.climax_threshold  = climax_threshold

    # ── public entry point ───────────────────────────────────────────────

    def extract(self, snapshot: MarketSnapshot, features: FeatureSet) -> None:
        try:
            self._extract_volume(snapshot, features)
        except Exception as exc:
            logger.exception("VolumeExtractor._extract_volume failed: %s", exc)
            features.extraction_errors.append(f"volume: {exc}")

        try:
            self._extract_futures(snapshot, features)
        except Exception as exc:
            logger.exception("VolumeExtractor._extract_futures failed: %s", exc)
            features.extraction_errors.append(f"volume_futures: {exc}")

    # ── internal helpers ─────────────────────────────────────────────────

    def _select_candles(self, snapshot: MarketSnapshot) -> Optional[List[Candle]]:
        if not snapshot.candles:
            return None
        if self.primary_timeframe and self.primary_timeframe in snapshot.candles:
            return snapshot.candles[self.primary_timeframe]
        # Fall back to first available timeframe
        return next(iter(snapshot.candles.values()))

    def _extract_volume(
        self, snapshot: MarketSnapshot, features: FeatureSet
    ) -> None:
        candles = self._select_candles(snapshot)
        if not candles or len(candles) < 2:
            features.extraction_errors.append(
                "volume: insufficient candles for volume analysis"
            )
            return

        vols  = _volumes(candles)
        n     = len(vols)

        # ── 20-bar average ───────────────────────────────────────────────
        avg_window = min(VOLUME_AVG_BARS, n)
        avg_vol    = _mean(vols[-avg_window:])

        # ── volume_vs_avg ────────────────────────────────────────────────
        current_vol = vols[-1]
        features.volume_vs_avg = (
            current_vol / avg_vol if avg_vol > 0 else 1.0
        )

        # ── volume_trend ─────────────────────────────────────────────────
        # Compare mean of last VOLUME_RECENT_BARS vs mean of the prior window
        recent_window = min(VOLUME_RECENT_BARS, n)
        prior_start   = max(0, n - VOLUME_AVG_BARS)
        prior_end     = max(0, n - recent_window)

        recent_avg = _mean(vols[-recent_window:])
        prior_vols = vols[prior_start:prior_end]
        prior_avg  = _mean(prior_vols) if prior_vols else avg_vol

        if prior_avg > 0:
            ratio = recent_avg / prior_avg
            if ratio > 1.05:
                features.volume_trend = "increasing"
            elif ratio < 0.95:
                features.volume_trend = "decreasing"
            else:
                features.volume_trend = "flat"
        else:
            features.volume_trend = "flat"

        # ── OBV trend ────────────────────────────────────────────────────
        obv_window = min(OBV_TREND_BARS, n)
        obv_candles = candles[-obv_window:]
        obv_series  = _compute_obv(obv_candles)
        obv_slope   = _linear_slope(obv_series)
        # Use a zero tolerance so any non-zero slope classifies directionally
        features.obv_trend = _classify_slope(obv_slope, tolerance=0.0)

        # ── volume_climax ────────────────────────────────────────────────
        lookback  = min(CLIMAX_LOOKBACK, n)
        threshold = self.climax_threshold * avg_vol
        features.volume_climax = any(
            v > threshold for v in vols[-lookback:]
        )

        # ── taker_delta ───────────────────────────────────────────────────
        delta_window = min(VOLUME_RECENT_BARS, n)
        recent_candles = candles[-delta_window:]
        features.taker_delta = sum(
            c.taker_buy_volume - c.taker_sell_volume for c in recent_candles
        )

    def _extract_futures(
        self, snapshot: MarketSnapshot, features: FeatureSet
    ) -> None:
        futures: Optional[FuturesData] = snapshot.futures
        if futures is None:
            return

        # ── funding_bias ──────────────────────────────────────────────────
        features.funding_bias = futures.funding_bias  # uses the property on FuturesData

        # ── oi_trend ──────────────────────────────────────────────────────
        # Without a historical OI series we approximate direction from
        # the taker_buy_sell_ratio: a ratio consistently above 1 while OI
        # is rising indicates continued buying; below 1 → selling pressure.
        if futures.taker_buy_sell_ratio is not None:
            ratio = futures.taker_buy_sell_ratio
            if ratio >= OI_RISING_RATIO:
                features.oi_trend = "increasing"
            elif ratio <= OI_FALLING_RATIO:
                features.oi_trend = "decreasing"
            else:
                features.oi_trend = "flat"
        else:
            features.oi_trend = "flat"

        # ── liquidation_imbalance ─────────────────────────────────────────
        long_liq  = futures.liquidation_24h_long  or 0.0
        short_liq = futures.liquidation_24h_short or 0.0
        total_liq = long_liq + short_liq

        if total_liq == 0.0:
            features.liquidation_imbalance = "neutral"
        else:
            long_pct = long_liq / total_liq
            if long_pct > 0.60:
                features.liquidation_imbalance = "long_heavy"
            elif long_pct < 0.40:
                features.liquidation_imbalance = "short_heavy"
            else:
                features.liquidation_imbalance = "neutral"
