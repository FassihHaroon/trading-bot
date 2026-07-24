"""
Swing high / low detection feature extractor.

A swing high at index i is confirmed when:
  candle[i].high > candle[i-n].high  for all n in 1..strength
  candle[i].high > candle[i+n].high  for all n in 1..strength
  candle[i].close <= candle[i].high  (close-confirmation: not a wide-open wick bar)

A swing low mirrors the above using .low and close >= low.

strength comes from config.features.swing_strength (default 3).
Up to 20 swings per timeframe are kept, sorted by timestamp descending.
SwingPoint.strength records how many bars each side actually confirm the point
(always equals the configured strength for complete swings; partial swings at
the edges of the series are skipped).
"""

from __future__ import annotations

import logging
from typing import Optional

from features.base import BaseFeatureExtractor
from data.schemas import (
    Candle,
    FeatureSet,
    MarketSnapshot,
    SwingPoint,
)
from config.settings import AgentConfig, DEFAULT_CONFIG

logger = logging.getLogger(__name__)

_MAX_SWINGS_PER_TF: int = 20


class SwingPointExtractor(BaseFeatureExtractor):
    """Detects swing highs and lows for every available timeframe."""

    def __init__(self, config: Optional[AgentConfig] = None) -> None:
        self.config: AgentConfig = config or DEFAULT_CONFIG

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def extract(self, snapshot: MarketSnapshot, features: FeatureSet) -> None:
        """
        Populate *features* in-place:
          - swing_highs       : list[SwingPoint]  (all TFs, newest first)
          - swing_lows        : list[SwingPoint]  (all TFs, newest first)
          - last_swing_high   : Optional[SwingPoint]
          - last_swing_low    : Optional[SwingPoint]
        """
        strength: int = self.config.features.swing_strength

        all_highs: list[SwingPoint] = []
        all_lows: list[SwingPoint] = []

        for timeframe, candles in snapshot.candles.items():
            if len(candles) < 2 * strength + 1:
                logger.debug(
                    "Skipping swing detection for %s — not enough candles "
                    "(%d required, %d available).",
                    timeframe,
                    2 * strength + 1,
                    len(candles),
                )
                features.extraction_errors.append(
                    f"swing_points:{timeframe}: insufficient candles "
                    f"(need {2 * strength + 1}, got {len(candles)})"
                )
                continue

            tf_highs = self._detect_swing_highs(candles, strength, timeframe)
            tf_lows = self._detect_swing_lows(candles, strength, timeframe)

            all_highs.extend(tf_highs)
            all_lows.extend(tf_lows)

        # Sort newest first across all timeframes
        all_highs.sort(key=lambda sp: sp.timestamp, reverse=True)
        all_lows.sort(key=lambda sp: sp.timestamp, reverse=True)

        features.swing_highs = all_highs
        features.swing_lows = all_lows
        features.last_swing_high = all_highs[0] if all_highs else None
        features.last_swing_low = all_lows[0] if all_lows else None

    # ------------------------------------------------------------------
    # Internal detection helpers
    # ------------------------------------------------------------------

    def _detect_swing_highs(
        self,
        candles: list[Candle],
        strength: int,
        timeframe: str,
    ) -> list[SwingPoint]:
        """
        Return up to _MAX_SWINGS_PER_TF swing highs, sorted newest first.

        Confirmation criteria for candle at index i:
          1. candle[i].high is strictly greater than the high of every
             candle within [i-strength .. i-1] and [i+1 .. i+strength].
          2. candle[i].close < candle[i].high  (close did not pin at the
             exact high — avoids treating strong momentum candles as pivots;
             a tiny tolerance of 1e-9 is used for float equality).
        """
        results: list[SwingPoint] = []
        n = len(candles)

        for i in range(strength, n - strength):
            pivot_high = candles[i].high

            # Left-side confirmation
            left_ok = all(
                pivot_high > candles[i - k].high
                for k in range(1, strength + 1)
            )
            if not left_ok:
                continue

            # Right-side confirmation
            right_ok = all(
                pivot_high > candles[i + k].high
                for k in range(1, strength + 1)
            )
            if not right_ok:
                continue

            # Close confirmation: close must be below the high
            # (allows for wicks; rejects pure momentum close-at-high bars)
            if candles[i].close >= pivot_high - 1e-9:
                continue

            # Compute actual strength: count how many consecutive bars
            # each side the pivot dominates (may exceed `strength` if
            # surrounding bars are very weak, but we cap at the window).
            confirmed_strength = self._compute_strength(candles, i, strength)

            results.append(
                SwingPoint(
                    price=pivot_high,
                    timestamp=candles[i].timestamp,
                    timeframe=timeframe,
                    swing_type="high",
                    strength=confirmed_strength,
                )
            )

        # Sort newest first, keep only the most recent N
        results.sort(key=lambda sp: sp.timestamp, reverse=True)
        return results[:_MAX_SWINGS_PER_TF]

    def _detect_swing_lows(
        self,
        candles: list[Candle],
        strength: int,
        timeframe: str,
    ) -> list[SwingPoint]:
        """
        Return up to _MAX_SWINGS_PER_TF swing lows, sorted newest first.

        Confirmation criteria for candle at index i:
          1. candle[i].low is strictly less than the low of every candle
             within [i-strength .. i-1] and [i+1 .. i+strength].
          2. candle[i].close > candle[i].low  (close did not pin at the
             exact low).
        """
        results: list[SwingPoint] = []
        n = len(candles)

        for i in range(strength, n - strength):
            pivot_low = candles[i].low

            # Left-side confirmation
            left_ok = all(
                pivot_low < candles[i - k].low
                for k in range(1, strength + 1)
            )
            if not left_ok:
                continue

            # Right-side confirmation
            right_ok = all(
                pivot_low < candles[i + k].low
                for k in range(1, strength + 1)
            )
            if not right_ok:
                continue

            # Close confirmation
            if candles[i].close <= pivot_low + 1e-9:
                continue

            confirmed_strength = self._compute_strength_low(candles, i, strength)

            results.append(
                SwingPoint(
                    price=pivot_low,
                    timestamp=candles[i].timestamp,
                    timeframe=timeframe,
                    swing_type="low",
                    strength=confirmed_strength,
                )
            )

        results.sort(key=lambda sp: sp.timestamp, reverse=True)
        return results[:_MAX_SWINGS_PER_TF]

    # ------------------------------------------------------------------
    # Strength computation
    # ------------------------------------------------------------------

    def _compute_strength(
        self, candles: list[Candle], i: int, window: int
    ) -> int:
        """
        Count consecutive bars on each side where the pivot high dominates,
        then return the minimum of the two sides (so strength reflects the
        weaker side).  Result is clamped to [window, min(window+2, 5)] to
        produce the 3..5 range described in the spec.
        """
        pivot_high = candles[i].high
        n = len(candles)

        left_count = 0
        for k in range(1, n):
            if i - k < 0:
                break
            if candles[i - k].high < pivot_high:
                left_count += 1
            else:
                break

        right_count = 0
        for k in range(1, n):
            if i + k >= n:
                break
            if candles[i + k].high < pivot_high:
                right_count += 1
            else:
                break

        raw = min(left_count, right_count)
        # Clamp: minimum is the configured window, maximum is 5
        return max(window, min(raw, 5))

    def _compute_strength_low(
        self, candles: list[Candle], i: int, window: int
    ) -> int:
        """Mirror of _compute_strength for swing lows."""
        pivot_low = candles[i].low
        n = len(candles)

        left_count = 0
        for k in range(1, n):
            if i - k < 0:
                break
            if candles[i - k].low > pivot_low:
                left_count += 1
            else:
                break

        right_count = 0
        for k in range(1, n):
            if i + k >= n:
                break
            if candles[i + k].low > pivot_low:
                right_count += 1
            else:
                break

        raw = min(left_count, right_count)
        return max(window, min(raw, 5))
