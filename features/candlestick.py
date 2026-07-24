"""
Candlestick Pattern Recognition Feature Extractor.

Detects single-, two-, and three-candle patterns on the last 3 candles of
both the 1h and 4h timeframes and appends CandlestickPattern instances to
FeatureSet.candlestick_patterns.

Confidence model
----------------
  0.50  base confidence for every detected pattern
+ 0.20  if volume confirms the pattern direction
+ 0.20  if the pattern forms at a key S/R level (nearest_support or
        nearest_resistance within LEVEL_PROXIMITY_PCT of the current price)
= 0.90  max per pattern (rounded to 2 dp)
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import List, Optional, Sequence

from data.schemas import (
    Candle,
    CandlestickPattern,
    Direction,
    FeatureSet,
    MarketSnapshot,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Tunable thresholds
# ─────────────────────────────────────────────────────────────────────────────

DOJI_BODY_RATIO: float = 0.10          # body must be < 10 % of candle range
SPINNING_TOP_BODY_RATIO: float = 0.30  # body must be < 30 % of candle range
MARUBOZU_BODY_RATIO: float = 0.90      # body must be > 90 % of candle range
HAMMER_WICK_MULTIPLIER: float = 2.0    # lower wick >= 2× body
HAMMER_UPPER_WICK_RATIO: float = 0.30  # upper wick <= 30 % of body
SHOOTING_STAR_WICK_MULTIPLIER: float = 2.0
SHOOTING_STAR_LOWER_WICK_RATIO: float = 0.30
TWEEZER_PRICE_TOLERANCE: float = 0.002  # highs/lows must be within 0.2 %
LEVEL_PROXIMITY_PCT: float = 0.005      # 0.5 % of price to qualify as "at level"
VOLUME_CONFIRM_RATIO: float = 1.0       # >= 1× avg volume counts as confirmation
VOLUME_AVG_BARS: int = 20               # bars used to compute average volume

BASE_CONFIDENCE: float = 0.50
VOLUME_BONUS: float = 0.20
SR_BONUS: float = 0.20

SUPPORTED_TIMEFRAMES: List[str] = ["1h", "4h"]


# ─────────────────────────────────────────────────────────────────────────────
# Minimal base class (matches the pattern used in volume.py)
# ─────────────────────────────────────────────────────────────────────────────

class BaseFeatureExtractor(ABC):
    """Minimal interface every feature extractor must satisfy."""

    @abstractmethod
    def extract(self, snapshot: MarketSnapshot, features: FeatureSet) -> None:
        """
        Read from *snapshot*, write results into *features* in-place.
        Must never raise — catch all exceptions and append to
        features.extraction_errors instead.
        """


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _safe_ratio(numerator: float, denominator: float) -> float:
    """Return numerator / denominator, or 0.0 when denominator is ~0."""
    return numerator / denominator if abs(denominator) > 1e-12 else 0.0


def _avg_volume(candles: Sequence[Candle], lookback: int = VOLUME_AVG_BARS) -> float:
    """Average volume of the last *lookback* candles (excluding the newest)."""
    # Use all available bars when fewer than lookback are present.
    sample = list(candles)[: -1]          # exclude the most-recent candle
    if not sample:
        return 0.0
    tail = sample[-lookback:]
    return sum(c.volume for c in tail) / len(tail)


def _volume_confirms(
    candles: Sequence[Candle],
    candle_index: int,          # 0 = most recent
    bullish: bool,
    avg_vol: float,
) -> bool:
    """
    Return True when the pattern's key candle has above-average volume
    AND the taker buy/sell split aligns with the pattern direction.
    """
    if avg_vol <= 0:
        return False
    # candle_index 0 == last element in the list
    c = list(candles)[-(1 + candle_index)]
    if c.volume < avg_vol * VOLUME_CONFIRM_RATIO:
        return False
    bsr = c.buy_sell_ratio          # >0.5 means net buying
    if bullish:
        return bsr > 0.5
    else:
        return bsr < 0.5


def _at_sr_level(price: float, features: FeatureSet) -> bool:
    """Return True when *price* is within LEVEL_PROXIMITY_PCT of support or resistance."""
    threshold = price * LEVEL_PROXIMITY_PCT
    if features.nearest_support is not None:
        if abs(price - features.nearest_support) <= threshold:
            return True
    if features.nearest_resistance is not None:
        if abs(price - features.nearest_resistance) <= threshold:
            return True
    return False


def _build_pattern(
    name: str,
    direction: Direction,
    timeframe: str,
    candle_index: int,
    vol_confirmed: bool,
    at_level: bool,
) -> CandlestickPattern:
    confidence = BASE_CONFIDENCE
    if vol_confirmed:
        confidence += VOLUME_BONUS
    if at_level:
        confidence += SR_BONUS
    return CandlestickPattern(
        pattern_name=name,
        direction=direction,
        confidence=round(min(confidence, 1.0), 2),
        candle_index=candle_index,
        timeframe=timeframe,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Single-candle detectors
# Each function receives the candle to test plus contextual information
# (avg_vol, features, timeframe).  Returns a CandlestickPattern or None.
# ─────────────────────────────────────────────────────────────────────────────

def _detect_doji(
    c: Candle,
    candle_index: int,
    timeframe: str,
    all_candles: Sequence[Candle],
    features: FeatureSet,
    avg_vol: float,
) -> Optional[CandlestickPattern]:
    """Doji: body < 10 % of range AND both wicks present."""
    rng = c.range
    if rng <= 0:
        return None
    if _safe_ratio(c.body_size, rng) >= DOJI_BODY_RATIO:
        return None
    if c.upper_wick <= 0 or c.lower_wick <= 0:
        return None
    vol_ok = _volume_confirms(all_candles, candle_index, bullish=True, avg_vol=avg_vol)
    sr_ok = _at_sr_level(c.close, features)
    return _build_pattern("doji", Direction.NEUTRAL, timeframe, candle_index, vol_ok, sr_ok)


def _detect_hammer(
    c: Candle,
    candle_index: int,
    timeframe: str,
    all_candles: Sequence[Candle],
    features: FeatureSet,
    avg_vol: float,
) -> Optional[CandlestickPattern]:
    """
    Hammer (bullish reversal at support):
      lower wick >= 2× body, upper wick <= 30 % of body, at support.
    """
    body = c.body_size
    if body <= 0:
        return None
    if c.lower_wick < HAMMER_WICK_MULTIPLIER * body:
        return None
    if c.upper_wick > HAMMER_UPPER_WICK_RATIO * body:
        return None
    # Require support context
    if features.nearest_support is None:
        return None
    threshold = c.close * LEVEL_PROXIMITY_PCT
    if abs(c.close - features.nearest_support) > threshold:
        return None
    vol_ok = _volume_confirms(all_candles, candle_index, bullish=True, avg_vol=avg_vol)
    return _build_pattern("hammer", Direction.LONG, timeframe, candle_index, vol_ok, True)


def _detect_shooting_star(
    c: Candle,
    candle_index: int,
    timeframe: str,
    all_candles: Sequence[Candle],
    features: FeatureSet,
    avg_vol: float,
) -> Optional[CandlestickPattern]:
    """
    Shooting Star (bearish reversal at resistance):
      upper wick >= 2× body, lower wick <= 30 % of body, at resistance.
    """
    body = c.body_size
    if body <= 0:
        return None
    if c.upper_wick < SHOOTING_STAR_WICK_MULTIPLIER * body:
        return None
    if c.lower_wick > SHOOTING_STAR_LOWER_WICK_RATIO * body:
        return None
    # Require resistance context
    if features.nearest_resistance is None:
        return None
    threshold = c.close * LEVEL_PROXIMITY_PCT
    if abs(c.close - features.nearest_resistance) > threshold:
        return None
    vol_ok = _volume_confirms(all_candles, candle_index, bullish=False, avg_vol=avg_vol)
    return _build_pattern("shooting_star", Direction.SHORT, timeframe, candle_index, vol_ok, True)


def _detect_spinning_top(
    c: Candle,
    candle_index: int,
    timeframe: str,
    all_candles: Sequence[Candle],
    features: FeatureSet,
    avg_vol: float,
) -> Optional[CandlestickPattern]:
    """Spinning Top: body < 30 % of range AND both wicks present."""
    rng = c.range
    if rng <= 0:
        return None
    body_ratio = _safe_ratio(c.body_size, rng)
    # Must be between doji threshold and spinning-top threshold
    if body_ratio >= SPINNING_TOP_BODY_RATIO:
        return None
    if body_ratio < DOJI_BODY_RATIO:
        return None  # that is a doji, not a spinning top
    if c.upper_wick <= 0 or c.lower_wick <= 0:
        return None
    vol_ok = _volume_confirms(all_candles, candle_index, bullish=True, avg_vol=avg_vol)
    sr_ok = _at_sr_level(c.close, features)
    return _build_pattern("spinning_top", Direction.NEUTRAL, timeframe, candle_index, vol_ok, sr_ok)


def _detect_marubozu_bullish(
    c: Candle,
    candle_index: int,
    timeframe: str,
    all_candles: Sequence[Candle],
    features: FeatureSet,
    avg_vol: float,
) -> Optional[CandlestickPattern]:
    """Bullish Marubozu: body > 90 % of range, close near high, bullish candle."""
    rng = c.range
    if rng <= 0:
        return None
    if not c.is_bullish:
        return None
    if _safe_ratio(c.body_size, rng) <= MARUBOZU_BODY_RATIO:
        return None
    vol_ok = _volume_confirms(all_candles, candle_index, bullish=True, avg_vol=avg_vol)
    sr_ok = _at_sr_level(c.close, features)
    return _build_pattern("marubozu_bullish", Direction.LONG, timeframe, candle_index, vol_ok, sr_ok)


def _detect_marubozu_bearish(
    c: Candle,
    candle_index: int,
    timeframe: str,
    all_candles: Sequence[Candle],
    features: FeatureSet,
    avg_vol: float,
) -> Optional[CandlestickPattern]:
    """Bearish Marubozu: body > 90 % of range, close near low, bearish candle."""
    rng = c.range
    if rng <= 0:
        return None
    if c.is_bullish:
        return None
    if _safe_ratio(c.body_size, rng) <= MARUBOZU_BODY_RATIO:
        return None
    vol_ok = _volume_confirms(all_candles, candle_index, bullish=False, avg_vol=avg_vol)
    sr_ok = _at_sr_level(c.close, features)
    return _build_pattern("marubozu_bearish", Direction.SHORT, timeframe, candle_index, vol_ok, sr_ok)


# ─────────────────────────────────────────────────────────────────────────────
# Two-candle detectors
# Receive (prev, curr) = (candles[-2], candles[-1]) by default, but the
# helpers accept explicit indices for flexibility.
# ─────────────────────────────────────────────────────────────────────────────

def _detect_bullish_engulfing(
    prev: Candle,
    curr: Candle,
    timeframe: str,
    all_candles: Sequence[Candle],
    features: FeatureSet,
    avg_vol: float,
) -> Optional[CandlestickPattern]:
    """
    Bullish Engulfing: bearish candle (prev) followed by bullish candle (curr)
    whose body fully engulfs the previous candle's body.
    """
    if prev.is_bullish or not curr.is_bullish:
        return None
    # curr body must engulf prev body
    if curr.open >= prev.open or curr.close <= prev.close:
        return None
    vol_ok = _volume_confirms(all_candles, candle_index=0, bullish=True, avg_vol=avg_vol)
    sr_ok = _at_sr_level(curr.close, features)
    return _build_pattern("bullish_engulfing", Direction.LONG, timeframe, 0, vol_ok, sr_ok)


def _detect_bearish_engulfing(
    prev: Candle,
    curr: Candle,
    timeframe: str,
    all_candles: Sequence[Candle],
    features: FeatureSet,
    avg_vol: float,
) -> Optional[CandlestickPattern]:
    """
    Bearish Engulfing: bullish candle (prev) followed by bearish candle (curr)
    whose body fully engulfs the previous candle's body.
    """
    if not prev.is_bullish or curr.is_bullish:
        return None
    # curr body must engulf prev body (bearish: open > close)
    if curr.open <= prev.close or curr.close >= prev.open:
        return None
    vol_ok = _volume_confirms(all_candles, candle_index=0, bullish=False, avg_vol=avg_vol)
    sr_ok = _at_sr_level(curr.close, features)
    return _build_pattern("bearish_engulfing", Direction.SHORT, timeframe, 0, vol_ok, sr_ok)


def _detect_tweezer_top(
    prev: Candle,
    curr: Candle,
    timeframe: str,
    all_candles: Sequence[Candle],
    features: FeatureSet,
    avg_vol: float,
) -> Optional[CandlestickPattern]:
    """
    Tweezer Top: two candles with very similar highs at resistance.
    """
    avg_high = (prev.high + curr.high) / 2
    if avg_high <= 0:
        return None
    if abs(prev.high - curr.high) / avg_high > TWEEZER_PRICE_TOLERANCE:
        return None
    if features.nearest_resistance is None:
        return None
    threshold = curr.close * LEVEL_PROXIMITY_PCT
    if abs(curr.high - features.nearest_resistance) > threshold:
        return None
    vol_ok = _volume_confirms(all_candles, candle_index=0, bullish=False, avg_vol=avg_vol)
    return _build_pattern("tweezer_top", Direction.SHORT, timeframe, 0, vol_ok, True)


def _detect_tweezer_bottom(
    prev: Candle,
    curr: Candle,
    timeframe: str,
    all_candles: Sequence[Candle],
    features: FeatureSet,
    avg_vol: float,
) -> Optional[CandlestickPattern]:
    """
    Tweezer Bottom: two candles with very similar lows at support.
    """
    avg_low = (prev.low + curr.low) / 2
    if avg_low <= 0:
        return None
    if abs(prev.low - curr.low) / avg_low > TWEEZER_PRICE_TOLERANCE:
        return None
    if features.nearest_support is None:
        return None
    threshold = curr.close * LEVEL_PROXIMITY_PCT
    if abs(curr.low - features.nearest_support) > threshold:
        return None
    vol_ok = _volume_confirms(all_candles, candle_index=0, bullish=True, avg_vol=avg_vol)
    return _build_pattern("tweezer_bottom", Direction.LONG, timeframe, 0, vol_ok, True)


# ─────────────────────────────────────────────────────────────────────────────
# Three-candle detectors
# Receive (c1, c2, c3) = (candles[-3], candles[-2], candles[-1]).
# ─────────────────────────────────────────────────────────────────────────────

def _detect_morning_star(
    c1: Candle,
    c2: Candle,
    c3: Candle,
    timeframe: str,
    all_candles: Sequence[Candle],
    features: FeatureSet,
    avg_vol: float,
) -> Optional[CandlestickPattern]:
    """
    Morning Star (bullish reversal):
      c1: large bearish candle
      c2: small body (doji or spinning top) — indecision
      c3: large bullish candle closing well into c1's body
    """
    # c1 must be a significant bearish candle
    if c1.is_bullish:
        return None
    if c1.range <= 0 or _safe_ratio(c1.body_size, c1.range) < 0.40:
        return None
    # c2 must have a small body (doji or spinning-top territory)
    if c2.range <= 0 or _safe_ratio(c2.body_size, c2.range) > SPINNING_TOP_BODY_RATIO:
        pass  # valid — small body is required
    else:
        # body is NOT small enough
        if _safe_ratio(c2.body_size, c2.range) > SPINNING_TOP_BODY_RATIO:
            return None
    # c2 body must be small compared to c1
    if c2.body_size >= c1.body_size * 0.5:
        return None
    # c3 must be a significant bullish candle
    if not c3.is_bullish:
        return None
    if c3.range <= 0 or _safe_ratio(c3.body_size, c3.range) < 0.40:
        return None
    # c3 must close above the midpoint of c1's body
    c1_mid = (c1.open + c1.close) / 2
    if c3.close <= c1_mid:
        return None
    vol_ok = _volume_confirms(all_candles, candle_index=0, bullish=True, avg_vol=avg_vol)
    sr_ok = _at_sr_level(c3.close, features)
    return _build_pattern("morning_star", Direction.LONG, timeframe, 0, vol_ok, sr_ok)


def _detect_evening_star(
    c1: Candle,
    c2: Candle,
    c3: Candle,
    timeframe: str,
    all_candles: Sequence[Candle],
    features: FeatureSet,
    avg_vol: float,
) -> Optional[CandlestickPattern]:
    """
    Evening Star (bearish reversal):
      c1: large bullish candle
      c2: small body — indecision
      c3: large bearish candle closing well into c1's body
    """
    if not c1.is_bullish:
        return None
    if c1.range <= 0 or _safe_ratio(c1.body_size, c1.range) < 0.40:
        return None
    if c2.body_size >= c1.body_size * 0.5:
        return None
    if c3.is_bullish:
        return None
    if c3.range <= 0 or _safe_ratio(c3.body_size, c3.range) < 0.40:
        return None
    c1_mid = (c1.open + c1.close) / 2
    if c3.close >= c1_mid:
        return None
    vol_ok = _volume_confirms(all_candles, candle_index=0, bullish=False, avg_vol=avg_vol)
    sr_ok = _at_sr_level(c3.close, features)
    return _build_pattern("evening_star", Direction.SHORT, timeframe, 0, vol_ok, sr_ok)


def _detect_three_white_soldiers(
    c1: Candle,
    c2: Candle,
    c3: Candle,
    timeframe: str,
    all_candles: Sequence[Candle],
    features: FeatureSet,
    avg_vol: float,
) -> Optional[CandlestickPattern]:
    """
    Three White Soldiers: three consecutive bullish candles with higher closes
    and each opening within the previous candle's body.
    """
    if not (c1.is_bullish and c2.is_bullish and c3.is_bullish):
        return None
    if not (c2.close > c1.close and c3.close > c2.close):
        return None
    # Each candle should open within the prior candle's body (not gap up)
    if c2.open < c1.open or c2.open > c1.close:
        return None
    if c3.open < c2.open or c3.open > c2.close:
        return None
    vol_ok = _volume_confirms(all_candles, candle_index=0, bullish=True, avg_vol=avg_vol)
    sr_ok = _at_sr_level(c3.close, features)
    return _build_pattern("three_white_soldiers", Direction.LONG, timeframe, 0, vol_ok, sr_ok)


def _detect_three_black_crows(
    c1: Candle,
    c2: Candle,
    c3: Candle,
    timeframe: str,
    all_candles: Sequence[Candle],
    features: FeatureSet,
    avg_vol: float,
) -> Optional[CandlestickPattern]:
    """
    Three Black Crows: three consecutive bearish candles with lower closes
    and each opening within the previous candle's body.
    """
    if c1.is_bullish or c2.is_bullish or c3.is_bullish:
        return None
    if not (c2.close < c1.close and c3.close < c2.close):
        return None
    # Each candle opens within the prior candle's body (bearish: open > close)
    if c2.open > c1.open or c2.open < c1.close:
        return None
    if c3.open > c2.open or c3.open < c2.close:
        return None
    vol_ok = _volume_confirms(all_candles, candle_index=0, bullish=False, avg_vol=avg_vol)
    sr_ok = _at_sr_level(c3.close, features)
    return _build_pattern("three_black_crows", Direction.SHORT, timeframe, 0, vol_ok, sr_ok)


# ─────────────────────────────────────────────────────────────────────────────
# Per-timeframe scanning
# ─────────────────────────────────────────────────────────────────────────────

def _scan_timeframe(
    candles: List[Candle],
    timeframe: str,
    features: FeatureSet,
) -> List[CandlestickPattern]:
    """
    Run all pattern detectors against the last 3 candles of *candles*.
    Returns a (possibly empty) list of detected patterns.
    """
    if len(candles) < 3:
        return []

    # Always work with the last 3 candles; index 0 = most recent
    c1 = candles[-3]   # oldest of the three
    c2 = candles[-2]
    c3 = candles[-1]   # most recent

    avg_vol = _avg_volume(candles)
    results: List[CandlestickPattern] = []

    # ── Single-candle (applied to each of the last 3 candles) ────────────
    for idx, candle in enumerate([c3, c2, c1]):
        for detector in (
            _detect_doji,
            _detect_hammer,
            _detect_shooting_star,
            _detect_spinning_top,
            _detect_marubozu_bullish,
            _detect_marubozu_bearish,
        ):
            pattern = detector(
                candle, idx, timeframe, candles, features, avg_vol
            )
            if pattern is not None:
                results.append(pattern)

    # ── Two-candle (applied to the two most-recent pairs) ─────────────────
    for prev, curr in [(c1, c2), (c2, c3)]:
        candle_idx = 0 if curr is c3 else 1
        for detector in (
            _detect_bullish_engulfing,
            _detect_bearish_engulfing,
            _detect_tweezer_top,
            _detect_tweezer_bottom,
        ):
            pattern = detector(prev, curr, timeframe, candles, features, avg_vol)
            if pattern is not None:
                # Adjust index so it reflects the position of the most recent candle
                results.append(
                    CandlestickPattern(
                        pattern_name=pattern.pattern_name,
                        direction=pattern.direction,
                        confidence=pattern.confidence,
                        candle_index=candle_idx,
                        timeframe=timeframe,
                    )
                )

    # ── Three-candle ───────────────────────────────────────────────────────
    for detector in (
        _detect_morning_star,
        _detect_evening_star,
        _detect_three_white_soldiers,
        _detect_three_black_crows,
    ):
        pattern = detector(c1, c2, c3, timeframe, candles, features, avg_vol)
        if pattern is not None:
            results.append(pattern)

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Public extractor
# ─────────────────────────────────────────────────────────────────────────────

class CandlestickExtractor(BaseFeatureExtractor):
    """
    Detects candlestick patterns on the 1h and 4h timeframes and appends
    the results to FeatureSet.candlestick_patterns.

    Usage
    -----
    extractor = CandlestickExtractor()
    extractor.extract(snapshot, features)
    """

    def extract(self, snapshot: MarketSnapshot, features: FeatureSet) -> None:  # noqa: D102
        try:
            all_patterns: List[CandlestickPattern] = []

            for tf in SUPPORTED_TIMEFRAMES:
                candles = snapshot.candles.get(tf)
                if not candles or len(candles) < 3:
                    features.extraction_errors.append(
                        f"CandlestickExtractor: insufficient candles for {tf} "
                        f"(need 3, got {len(candles) if candles else 0})"
                    )
                    continue

                tf_patterns = _scan_timeframe(list(candles), tf, features)
                all_patterns.extend(tf_patterns)
                logger.debug(
                    "[CandlestickExtractor] %s: detected %d pattern(s): %s",
                    tf,
                    len(tf_patterns),
                    [p.pattern_name for p in tf_patterns],
                )

            features.candlestick_patterns = all_patterns

        except Exception as exc:  # pylint: disable=broad-except
            logger.exception("[CandlestickExtractor] Unexpected error: %s", exc)
            features.extraction_errors.append(
                f"CandlestickExtractor: unexpected error — {exc}"
            )
