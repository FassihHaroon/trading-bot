"""
Market Structure Feature Extractor.

Identifies HH/HL (uptrend), LH/LL (downtrend), ranging, break of structure
(BOS), and change of character (CHoCH) from 4h candle swing points.
"""

from __future__ import annotations

from typing import Optional

from data.schemas import (
    Candle,
    FeatureSet,
    MarketSnapshot,
    StructureState,
    SwingPoint,
)


# ---------------------------------------------------------------------------
# Base class contract (mirrors what all feature extractors implement)
# ---------------------------------------------------------------------------

class BaseFeatureExtractor:
    """Minimal abstract base so every extractor has a common interface."""

    def extract(self, snapshot: MarketSnapshot, features: FeatureSet) -> None:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PRIMARY_TF = "4h"
_FALLBACK_TFS = ("1h", "15m", "1d")

# How many bars on each side a pivot must be the extreme of to count as a swing
_SWING_LOOKBACK = 3

# Minimum number of consecutive swing highs / lows required to declare a trend
_TREND_MIN_SWINGS = 3

# How many bars back a BOS is still considered "recent" for BROKEN_UP/DOWN state
_BOS_RECENT_BARS = 3


def _pick_candles(snapshot: MarketSnapshot) -> list[Candle]:
    """Return 4h candles; fall back to the first available timeframe."""
    if _PRIMARY_TF in snapshot.candles and snapshot.candles[_PRIMARY_TF]:
        return snapshot.candles[_PRIMARY_TF]
    for tf in _FALLBACK_TFS:
        if tf in snapshot.candles and snapshot.candles[tf]:
            return snapshot.candles[tf]
    return []


def _find_swing_highs(candles: list[Candle], lookback: int = _SWING_LOOKBACK) -> list[SwingPoint]:
    """
    Identify pivot highs.  Bar i is a swing high if its high is the highest
    among the ``lookback`` bars to its left and right.
    Returns points ordered oldest → newest.
    """
    pivots: list[SwingPoint] = []
    n = len(candles)
    for i in range(lookback, n - lookback):
        pivot_high = candles[i].high
        left_ok  = all(candles[i - j].high <= pivot_high for j in range(1, lookback + 1))
        right_ok = all(candles[i + j].high <= pivot_high for j in range(1, lookback + 1))
        if left_ok and right_ok:
            pivots.append(
                SwingPoint(
                    price=pivot_high,
                    timestamp=candles[i].timestamp,
                    timeframe=_PRIMARY_TF,
                    swing_type="high",
                    strength=lookback,
                )
            )
    return pivots


def _find_swing_lows(candles: list[Candle], lookback: int = _SWING_LOOKBACK) -> list[SwingPoint]:
    """
    Identify pivot lows.  Bar i is a swing low if its low is the lowest
    among the ``lookback`` bars to its left and right.
    Returns points ordered oldest → newest.
    """
    pivots: list[SwingPoint] = []
    n = len(candles)
    for i in range(lookback, n - lookback):
        pivot_low = candles[i].low
        left_ok  = all(candles[i - j].low >= pivot_low for j in range(1, lookback + 1))
        right_ok = all(candles[i + j].low >= pivot_low for j in range(1, lookback + 1))
        if left_ok and right_ok:
            pivots.append(
                SwingPoint(
                    price=pivot_low,
                    timestamp=candles[i].timestamp,
                    timeframe=_PRIMARY_TF,
                    swing_type="low",
                    strength=lookback,
                )
            )
    return pivots


def _is_strictly_ascending(prices: list[float]) -> bool:
    """True when every element is strictly greater than the one before it."""
    return all(prices[i] > prices[i - 1] for i in range(1, len(prices)))


def _is_strictly_descending(prices: list[float]) -> bool:
    """True when every element is strictly less than the one before it."""
    return all(prices[i] < prices[i - 1] for i in range(1, len(prices)))


def _candle_index_for_timestamp(candles: list[Candle], ts: int) -> int:
    """Return the index of the candle whose timestamp equals ts, or -1."""
    for idx, c in enumerate(candles):
        if c.timestamp == ts:
            return idx
    return -1


# ---------------------------------------------------------------------------
# Main extractor
# ---------------------------------------------------------------------------

class MarketStructureExtractor(BaseFeatureExtractor):
    """
    Analyses 4h market structure and populates three FeatureSet fields:

    * ``structure_state``  — StructureState enum value
    * ``last_bos``         — "bullish_bos" | "bearish_bos" | None
    * ``last_choch``       — "bullish_choch" | "bearish_choch" | None
    """

    def extract(self, snapshot: MarketSnapshot, features: FeatureSet) -> None:  # noqa: C901
        candles = _pick_candles(snapshot)
        if len(candles) < (_SWING_LOOKBACK * 2 + _TREND_MIN_SWINGS + 1):
            # Not enough data — leave defaults
            features.structure_state = StructureState.RANGING
            features.last_bos = None
            features.last_choch = None
            return

        # ── 1. Detect all swing points ────────────────────────────────────
        all_highs = _find_swing_highs(candles)
        all_lows  = _find_swing_lows(candles)

        # Keep last 6 of each
        recent_highs = all_highs[-6:]
        recent_lows  = all_lows[-6:]

        # Expose to the wider FeatureSet (other extractors may use these)
        features.swing_highs = recent_highs
        features.swing_lows  = recent_lows
        features.last_swing_high = recent_highs[-1] if recent_highs else None
        features.last_swing_low  = recent_lows[-1]  if recent_lows  else None

        # ── 2. Classify trend from last 3 swing highs + last 3 swing lows ─
        high_prices = [sp.price for sp in recent_highs[-_TREND_MIN_SWINGS:]]
        low_prices  = [sp.price for sp in recent_lows[-_TREND_MIN_SWINGS:]]

        hh_hl = (
            len(high_prices) >= _TREND_MIN_SWINGS
            and len(low_prices) >= _TREND_MIN_SWINGS
            and _is_strictly_ascending(high_prices)
            and _is_strictly_ascending(low_prices)
        )
        lh_ll = (
            len(high_prices) >= _TREND_MIN_SWINGS
            and len(low_prices) >= _TREND_MIN_SWINGS
            and _is_strictly_descending(high_prices)
            and _is_strictly_descending(low_prices)
        )

        base_state: StructureState
        if hh_hl:
            base_state = StructureState.HH_HL
        elif lh_ll:
            base_state = StructureState.LH_LL
        else:
            base_state = StructureState.RANGING

        # ── 3. Break of Structure detection ───────────────────────────────
        current_close = candles[-1].close
        last_bos: Optional[str] = None
        bos_bar_index: int = -1          # index in `candles` where BOS occurred

        if lh_ll and recent_highs:
            # Most recent swing high (= most recent Lower High in downtrend)
            most_recent_lh_price = recent_highs[-1].price
            most_recent_lh_ts    = recent_highs[-1].timestamp

            # Scan candles AFTER the most recent LH to find the first close above it
            lh_bar_idx = _candle_index_for_timestamp(candles, most_recent_lh_ts)
            search_from = lh_bar_idx + 1 if lh_bar_idx >= 0 else len(candles) - 1

            for idx in range(search_from, len(candles)):
                if candles[idx].close > most_recent_lh_price:
                    last_bos = "bullish_bos"
                    bos_bar_index = idx
                    break

        elif hh_hl and recent_lows:
            # Most recent swing low (= most recent Higher Low in uptrend)
            most_recent_hl_price = recent_lows[-1].price
            most_recent_hl_ts    = recent_lows[-1].timestamp

            hl_bar_idx = _candle_index_for_timestamp(candles, most_recent_hl_ts)
            search_from = hl_bar_idx + 1 if hl_bar_idx >= 0 else len(candles) - 1

            for idx in range(search_from, len(candles)):
                if candles[idx].close < most_recent_hl_price:
                    last_bos = "bearish_bos"
                    bos_bar_index = idx
                    break

        # ── 4. Change of Character detection ──────────────────────────────
        #
        # CHoCH = the first swing point that contradicts the existing trend
        # sequence.
        #
        # In an uptrend (HH_HL): a new swing high that is LOWER than the
        # previous swing high — the first Lower High signals CHoCH bearish.
        #
        # In a downtrend (LH_LL): a new swing low that is HIGHER than the
        # previous swing low — the first Higher Low signals CHoCH bullish.
        #
        last_choch: Optional[str] = None

        if hh_hl and len(recent_highs) >= 2:
            # Check whether the most recent swing high broke the ascending sequence
            if recent_highs[-1].price < recent_highs[-2].price:
                last_choch = "bearish_choch"

        elif lh_ll and len(recent_lows) >= 2:
            # Check whether the most recent swing low broke the descending sequence
            if recent_lows[-1].price > recent_lows[-2].price:
                last_choch = "bullish_choch"

        # ── 5. Resolve final structure_state ──────────────────────────────
        #
        # Promote to BROKEN_UP / BROKEN_DOWN when a BOS fired within the
        # last _BOS_RECENT_BARS bars.
        #
        final_state = base_state
        if last_bos is not None and bos_bar_index >= 0:
            bars_since_bos = (len(candles) - 1) - bos_bar_index
            if bars_since_bos <= _BOS_RECENT_BARS:
                if last_bos == "bullish_bos":
                    final_state = StructureState.BROKEN_UP
                else:
                    final_state = StructureState.BROKEN_DOWN

        # ── 6. Write results ───────────────────────────────────────────────
        features.structure_state = final_state
        features.last_bos        = last_bos
        features.last_choch      = last_choch
