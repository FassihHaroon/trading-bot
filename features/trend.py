"""
Trend detection feature extractor.

Populates the trend-related fields of FeatureSet:
  - trend_direction, trend_strength, ma_fan_bullish, ma_fan_bearish,
    price_vs_200ema, ma_slopes, trend_phase

Primary timeframes: 4h and 1d.  Falls back to 1h when 4h is absent.
"""

from __future__ import annotations

import logging
from typing import Optional

from features.base import BaseFeatureExtractor
from data.schemas import (
    Candle,
    FeatureSet,
    MarketPhase,
    MarketSnapshot,
    TrendDirection,
)
from config.settings import AgentConfig, DEFAULT_CONFIG

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────
# Low-level helpers (pure functions, no side-effects)
# ─────────────────────────────────────────────────────────────────

def _ema(prices: list[float], period: int) -> list[float]:
    """Return EMA series aligned with *prices* (same length, NaN-padded as None)."""
    if len(prices) < period:
        return [None] * len(prices)
    k = 2.0 / (period + 1)
    result: list[Optional[float]] = [None] * len(prices)
    # Seed with SMA of first `period` bars
    seed = sum(prices[:period]) / period
    result[period - 1] = seed
    for i in range(period, len(prices)):
        result[i] = prices[i] * k + result[i - 1] * (1 - k)
    return result


def _last_valid(series: list[Optional[float]]) -> Optional[float]:
    """Return the rightmost non-None value in a series."""
    for v in reversed(series):
        if v is not None:
            return v
    return None


def _slope(series: list[Optional[float]], lookback: int = 5) -> float:
    """
    Linear-regression slope of the last *lookback* valid values, normalised
    by the mean so that units cancel (returns a dimensionless rate-of-change).
    Returns 0.0 when there are not enough points.
    """
    vals = [v for v in series if v is not None]
    if len(vals) < 2:
        return 0.0
    segment = vals[-lookback:]
    n = len(segment)
    if n < 2:
        return 0.0
    mean_x = (n - 1) / 2
    mean_y = sum(segment) / n
    if mean_y == 0:
        return 0.0
    num = sum((i - mean_x) * (segment[i] - mean_y) for i in range(n))
    den = sum((i - mean_x) ** 2 for i in range(n))
    if den == 0:
        return 0.0
    return (num / den) / mean_y          # normalised slope


def _closes(candles: list[Candle]) -> list[float]:
    return [c.close for c in candles]


def _highs(candles: list[Candle]) -> list[float]:
    return [c.high for c in candles]


def _lows(candles: list[Candle]) -> list[float]:
    return [c.low for c in candles]


def _volumes(candles: list[Candle]) -> list[float]:
    return [c.volume for c in candles]


# ─────────────────────────────────────────────────────────────────
# Swing structure helpers
# ─────────────────────────────────────────────────────────────────

def _detect_hh_hl(candles: list[Candle], lookback: int = 10) -> bool:
    """
    True when the last *lookback* bars show a higher-high AND higher-low
    pattern compared with the prior equivalent window.
    Uses a simple 3-point pivot: a bar is a pivot-high if its high exceeds
    both neighbours; a pivot-low if its low is below both neighbours.
    """
    bars = candles[-lookback * 2:] if len(candles) >= lookback * 2 else candles
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
        hh = pivot_highs[-1] > pivot_highs[-2]
        hl = pivot_lows[-1] > pivot_lows[-2]
        return hh and hl
    return False


def _detect_lh_ll(candles: list[Candle], lookback: int = 10) -> bool:
    """
    True when the last *lookback* bars show a lower-high AND lower-low pattern.
    """
    bars = candles[-lookback * 2:] if len(candles) >= lookback * 2 else candles
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
        lh = pivot_highs[-1] < pivot_highs[-2]
        ll = pivot_lows[-1] < pivot_lows[-2]
        return lh and ll
    return False


# ─────────────────────────────────────────────────────────────────
# Trend-strength helper (directional-bar ratio, ADX-like 0–1)
# ─────────────────────────────────────────────────────────────────

def _directional_strength(candles: list[Candle], lookback: int = 20) -> float:
    """
    Ratio of bars that close in the dominant direction to total bars.
    Weighted by the relative bar body size so large decisive candles count more.
    """
    bars = candles[-lookback:]
    if not bars:
        return 0.0

    up_weight = 0.0
    down_weight = 0.0
    for c in bars:
        body = abs(c.close - c.open)
        candle_range = c.high - c.low if c.high != c.low else 1e-9
        weight = body / candle_range        # large-body candles get more weight
        if c.close > c.open:
            up_weight += weight
        elif c.close < c.open:
            down_weight += weight

    total = up_weight + down_weight
    if total == 0:
        return 0.0
    dominant = max(up_weight, down_weight)
    # Scale so a fully one-sided market gives 1.0
    return round(dominant / total, 4)


# ─────────────────────────────────────────────────────────────────
# MACD divergence (used in phase detection)
# ─────────────────────────────────────────────────────────────────

def _bearish_macd_divergence(
    candles: list[Candle],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> bool:
    """
    Detects simple bearish divergence: price makes a new high while the MACD
    histogram's recent peak is lower than its prior peak.
    """
    closes = _closes(candles)
    if len(closes) < slow + signal + 10:
        return False

    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    macd_line = [
        (f - s) if (f is not None and s is not None) else None
        for f, s in zip(ema_fast, ema_slow)
    ]
    valid_macd = [v for v in macd_line if v is not None]
    if len(valid_macd) < signal + 5:
        return False

    # Signal line = EMA of MACD line
    signal_series = _ema(valid_macd, signal)
    histogram = [
        (m - s) if (s is not None) else None
        for m, s in zip(valid_macd, signal_series)
    ]
    hist_vals = [v for v in histogram if v is not None]
    if len(hist_vals) < 10:
        return False

    # Look at last 20 candles for divergence
    recent_hist = hist_vals[-20:]
    recent_closes = closes[-20:]

    # Find two local peaks in histogram
    peaks_hist: list[tuple[int, float]] = []
    for i in range(1, len(recent_hist) - 1):
        if (
            recent_hist[i] is not None
            and recent_hist[i] > (recent_hist[i - 1] or -999)
            and recent_hist[i] > (recent_hist[i + 1] or -999)
        ):
            peaks_hist.append((i, recent_hist[i]))

    if len(peaks_hist) < 2:
        return False

    p1_idx, p1_val = peaks_hist[-2]
    p2_idx, p2_val = peaks_hist[-1]

    price_made_hh = recent_closes[p2_idx] > recent_closes[p1_idx]
    hist_lower = p2_val < p1_val
    return price_made_hh and hist_lower


# ─────────────────────────────────────────────────────────────────
# Phase detection
# ─────────────────────────────────────────────────────────────────

def _detect_phase(
    candles: list[Candle],
    ema50: list[Optional[float]],
    ema200: list[Optional[float]],
    slope_50: float,
    slope_200: float,
    feat_cfg,
) -> MarketPhase:
    """
    Wyckoff-inspired phase detection using MA slopes and volume patterns.

    Accumulation : flat MAs (near 200 EMA) + volume declining at lows
    Markup       : rising MAs + volume on up-legs > down-legs
    Distribution : flat MAs at highs + bearish MACD divergence
    Markdown     : falling MAs
    """
    if not candles:
        return MarketPhase.UNKNOWN

    e50 = _last_valid(ema50)
    e200 = _last_valid(ema200)

    flat_threshold = 0.001          # slope < 0.1 % considered flat
    is_50_flat = abs(slope_50) < flat_threshold
    is_200_flat = abs(slope_200) < flat_threshold
    is_50_rising = slope_50 > flat_threshold
    is_50_falling = slope_50 < -flat_threshold

    last_close = candles[-1].close

    # Volume trend: compare up-bar vol vs down-bar vol in last 20 bars
    recent = candles[-20:]
    up_vol = sum(c.volume for c in recent if c.close >= c.open)
    dn_vol = sum(c.volume for c in recent if c.close < c.open)
    vol_on_up_legs = up_vol > dn_vol

    # Check if price is near 200 EMA (within 3%)
    near_200 = (
        e200 is not None
        and abs(last_close - e200) / e200 < 0.03
    )

    # Check if price is near recent highs (within 3% of 50-bar high)
    highs_50 = _highs(candles[-50:]) if len(candles) >= 50 else _highs(candles)
    recent_high = max(highs_50) if highs_50 else last_close
    near_highs = abs(last_close - recent_high) / recent_high < 0.03

    if is_50_falling and is_200_flat or (is_50_falling and slope_200 < 0):
        return MarketPhase.MARKDOWN

    if is_50_rising and vol_on_up_legs:
        return MarketPhase.MARKUP

    if is_50_flat and is_200_flat and near_highs:
        # Check for MACD divergence as additional confirmation
        if _bearish_macd_divergence(
            candles,
            fast=feat_cfg.macd_fast,
            slow=feat_cfg.macd_slow,
            signal=feat_cfg.macd_signal,
        ):
            return MarketPhase.DISTRIBUTION
        # Even without divergence, flat at highs hints distribution
        return MarketPhase.DISTRIBUTION

    if is_50_flat and near_200 and not vol_on_up_legs:
        return MarketPhase.ACCUMULATION

    # Rising MAs without strong volume bias — still markup
    if is_50_rising:
        return MarketPhase.MARKUP

    if is_50_falling:
        return MarketPhase.MARKDOWN

    return MarketPhase.UNKNOWN


# ─────────────────────────────────────────────────────────────────
# TrendExtractor
# ─────────────────────────────────────────────────────────────────

class TrendExtractor(BaseFeatureExtractor):
    """
    Extracts trend-related features and writes them into a FeatureSet.

    Primary timeframes: 4h and 1d (falls back to 1h if 4h is missing).
    """

    PREFERRED_TF = "4h"
    FALLBACK_TF = "1h"
    SECONDARY_TF = "1d"

    def __init__(self, config: AgentConfig = DEFAULT_CONFIG) -> None:
        self.cfg = config
        self.feat = config.features

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract(self, snapshot: MarketSnapshot, feature_set: FeatureSet) -> None:
        """
        Mutate *feature_set* in-place with all trend fields.
        Swallows individual indicator errors so one failure cannot block others.
        """
        primary_tf, candles = self._select_candles(snapshot)
        if not candles or len(candles) < 10:
            feature_set.extraction_errors.append(
                "TrendExtractor: insufficient candles"
            )
            return

        closes = _closes(candles)

        # ── Build EMA series ─────────────────────────────────────────
        try:
            ema9   = _ema(closes, self.feat.ema_short)
            ema21  = _ema(closes, self.feat.ema_mid)
            ema50  = _ema(closes, self.feat.ema_long)
            ema200 = _ema(closes, self.feat.ema_200)
        except Exception as exc:
            feature_set.extraction_errors.append(f"TrendExtractor EMA: {exc}")
            return

        e9   = _last_valid(ema9)
        e21  = _last_valid(ema21)
        e50  = _last_valid(ema50)
        e200 = _last_valid(ema200)

        last_close = closes[-1]

        # ── MA Slopes ────────────────────────────────────────────────
        slope_lookback = 5
        slope_9   = _slope(ema9,   slope_lookback)
        slope_21  = _slope(ema21,  slope_lookback)
        slope_50  = _slope(ema50,  slope_lookback)
        slope_200 = _slope(ema200, slope_lookback)

        feature_set.ma_slopes = {
            f"{self.feat.ema_short}ema":  round(slope_9,   6),
            f"{self.feat.ema_mid}ema":    round(slope_21,  6),
            f"{self.feat.ema_long}ema":   round(slope_50,  6),
            f"{self.feat.ema_200}ema":    round(slope_200, 6),
        }

        # ── MA Fan ───────────────────────────────────────────────────
        if all(v is not None for v in (e9, e21, e50, e200)):
            feature_set.ma_fan_bullish = e9 > e21 > e50 > e200
            feature_set.ma_fan_bearish = e9 < e21 < e50 < e200
        else:
            feature_set.ma_fan_bullish = False
            feature_set.ma_fan_bearish = False

        # ── Price vs 200 EMA ─────────────────────────────────────────
        if e200 is not None:
            pct_diff = (last_close - e200) / e200
            at_threshold = 0.005            # 0.5 %
            if abs(pct_diff) <= at_threshold:
                feature_set.price_vs_200ema = "at"
            elif pct_diff > 0:
                feature_set.price_vs_200ema = "above"
            else:
                feature_set.price_vs_200ema = "below"
        else:
            feature_set.price_vs_200ema = "at"

        # ── Trend Direction ──────────────────────────────────────────
        feature_set.trend_direction = self._classify_direction(
            candles, last_close, e50, slope_50
        )

        # ── Trend Strength ───────────────────────────────────────────
        feature_set.trend_strength = _directional_strength(candles, lookback=20)

        # ── Market Phase ─────────────────────────────────────────────
        try:
            feature_set.trend_phase = _detect_phase(
                candles, ema50, ema200, slope_50, slope_200, self.feat
            )
        except Exception as exc:
            feature_set.extraction_errors.append(f"TrendExtractor phase: {exc}")
            feature_set.trend_phase = MarketPhase.UNKNOWN

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _select_candles(
        self, snapshot: MarketSnapshot
    ) -> tuple[str, list[Candle]]:
        """
        Return (timeframe_label, candles) giving preference to 4h, then 1h,
        and also incorporating 1d for macro context when possible.
        The returned list is primarily the intraday series used for indicator
        calculation; callers that need 1d do so separately.
        """
        available = snapshot.candles or {}

        # Try preferred intraday TF
        for tf in (self.PREFERRED_TF, self.FALLBACK_TF):
            if tf in available and available[tf]:
                return tf, available[tf]

        # Last resort: any non-empty timeframe
        for tf, bars in available.items():
            if bars:
                logger.warning(
                    "TrendExtractor: using fallback timeframe %s for %s",
                    tf, snapshot.symbol,
                )
                return tf, bars

        return "", []

    def _classify_direction(
        self,
        candles: list[Candle],
        last_close: float,
        e50: Optional[float],
        slope_50: float,
        lookback: int = 10,
    ) -> TrendDirection:
        """
        Uptrend  : close > 50EMA AND 50EMA sloping up AND HH+HL in last *lookback* bars
        Downtrend: close < 50EMA AND 50EMA sloping down AND LH+LL
        Neutral  : everything else
        """
        flat_threshold = 0.0005             # very small slope = flat

        if e50 is None:
            return TrendDirection.NEUTRAL

        above_50 = last_close > e50
        below_50 = last_close < e50
        rising_50 = slope_50 > flat_threshold
        falling_50 = slope_50 < -flat_threshold

        hh_hl = _detect_hh_hl(candles, lookback)
        lh_ll = _detect_lh_ll(candles, lookback)

        if above_50 and rising_50 and hh_hl:
            return TrendDirection.BULLISH
        if below_50 and falling_50 and lh_ll:
            return TrendDirection.BEARISH

        # Partial conditions: two out of three still indicate a lean
        bullish_score = int(above_50) + int(rising_50) + int(hh_hl)
        bearish_score = int(below_50) + int(falling_50) + int(lh_ll)

        if bullish_score >= 2 and bullish_score > bearish_score:
            return TrendDirection.BULLISH
        if bearish_score >= 2 and bearish_score > bullish_score:
            return TrendDirection.BEARISH

        return TrendDirection.NEUTRAL
