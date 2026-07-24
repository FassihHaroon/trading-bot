"""
Fibonacci retracement and extension level feature extractor.

Populates FeatureSet.fib_retracements with a list of dicts:
  [{"level": 0.618, "price": 43250.0, "label": "0.618", "type": "retracement",
    "fib_confluence": True}, ...]

Logic:
  1. Find the most significant recent swing high and swing low from 4h candles
     (last 100 bars).  A swing qualifies only if it covers >= 3% price movement
     and spans >= 10 bars.
  2. Retracement levels depend on macro_bias:
       BULLISH  — from swing_low to swing_high; pullback zones measure down from high.
       BEARISH  — from swing_high to swing_low; pullback zones measure up from low.
  3. Extension levels (1.272, 1.414, 1.618) mark projected targets beyond the
     swing origin.
  4. Each level dict contains a fib_confluence flag (True when the current
     price is within 0.5% of the fib price).
  5. Returns an empty list when no qualifying swing is found.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

from features.base import BaseFeatureExtractor
from data.schemas import (
    Candle,
    FeatureSet,
    MarketSnapshot,
    TrendDirection,
)
from config.settings import AgentConfig, DEFAULT_CONFIG

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

_4H_TIMEFRAME_KEYS = ("4h", "4H")
_FALLBACK_TIMEFRAME_KEYS = ("1h", "1H", "1d", "1D")

_LOOKBACK_BARS: int = 100
_MIN_SWING_PCT: float = 3.0     # Minimum price movement for a valid swing (%)
_MIN_SWING_BARS: int = 10       # Minimum bar span between swing high and low

_RETRACEMENT_LEVELS: tuple[float, ...] = (0.236, 0.382, 0.5, 0.618, 0.786)
_EXTENSION_LEVELS: tuple[float, ...] = (1.272, 1.414, 1.618)

_CONFLUENCE_PCT: float = 0.5    # Price must be within this % of a fib level


# ── Helper (module-level, no I/O) ────────────────────────────────────────────

def _pct_distance(price_a: float, price_b: float) -> float:
    """Return the absolute percentage distance between two prices."""
    if price_b == 0.0:
        return 0.0
    return abs(price_a - price_b) / price_b * 100.0


# ── Main extractor ────────────────────────────────────────────────────────────

class FibonacciExtractor(BaseFeatureExtractor):
    """
    Computes Fibonacci retracement and extension levels from the most
    significant recent swing on the 4h timeframe.
    """

    def __init__(self, config: Optional[AgentConfig] = None) -> None:
        import logging as _logging
        self._logger = _logging.getLogger(self.__class__.__name__)
        self.config: AgentConfig = config or DEFAULT_CONFIG

    # ── Public interface ──────────────────────────────────────────────────────

    def extract(self, snapshot: MarketSnapshot, features: FeatureSet) -> None:
        """
        Populate features.fib_retracements in-place.

        Each element is a dict with keys:
          level           float   — Fibonacci ratio (e.g. 0.618)
          price           float   — Corresponding price level
          label           str     — Human-readable label (e.g. "0.618")
          type            str     — "retracement" or "extension"
          fib_confluence  bool    — True when current price is within 0.5%
        """
        candles = self._resolve_candles(snapshot)

        if candles is None:
            features.extraction_errors.append(
                "fibonacci: no 4h (or fallback) candle data available"
            )
            features.fib_retracements = []
            return

        # Use only the last _LOOKBACK_BARS candles
        candles = candles[-_LOOKBACK_BARS:]

        swing_high_price, swing_high_idx = self._find_swing_high(candles)
        swing_low_price, swing_low_idx = self._find_swing_low(candles)

        if swing_high_price is None or swing_low_price is None:
            self._log("No swing high or swing low found in lookback window.")
            features.fib_retracements = []
            return

        swing_range = swing_high_price - swing_low_price
        swing_pct = swing_range / swing_low_price * 100.0
        bar_span = abs(swing_high_idx - swing_low_idx)

        if swing_pct < _MIN_SWING_PCT or bar_span < _MIN_SWING_BARS:
            self._log(
                f"Swing does not qualify: range={swing_pct:.2f}% "
                f"(need {_MIN_SWING_PCT}%), span={bar_span} bars "
                f"(need {_MIN_SWING_BARS})."
            )
            features.fib_retracements = []
            return

        current_price = self._current_price(snapshot, candles)
        macro_bias = features.macro_bias

        levels = self._compute_levels(
            swing_high=swing_high_price,
            swing_low=swing_low_price,
            macro_bias=macro_bias,
            current_price=current_price,
        )

        features.fib_retracements = levels
        self._log(
            f"Computed {len(levels)} Fibonacci levels | "
            f"swing_high={swing_high_price:.4f} swing_low={swing_low_price:.4f} "
            f"bias={macro_bias.value}"
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _resolve_candles(
        self, snapshot: MarketSnapshot
    ) -> Optional[list[Candle]]:
        """Return the best available candle list (prefer 4h, fall back gracefully)."""
        for key in _4H_TIMEFRAME_KEYS:
            candles = snapshot.candles.get(key)
            if candles:
                return candles

        for key in _FALLBACK_TIMEFRAME_KEYS:
            candles = snapshot.candles.get(key)
            if candles:
                self._log(
                    f"4h candles not available; falling back to timeframe '{key}'."
                )
                return candles

        return None

    def _find_swing_high(
        self, candles: list[Candle]
    ) -> tuple[Optional[float], int]:
        """
        Return (price, index) of the highest candle high in the lookback window.
        Index is relative to the sliced candles list.
        """
        if not candles:
            return None, -1
        highs = self._get_highs(candles)
        idx = int(np.argmax(highs))
        return float(highs[idx]), idx

    def _find_swing_low(
        self, candles: list[Candle]
    ) -> tuple[Optional[float], int]:
        """
        Return (price, index) of the lowest candle low in the lookback window.
        """
        if not candles:
            return None, -1
        lows = self._get_lows(candles)
        idx = int(np.argmin(lows))
        return float(lows[idx]), idx

    def _current_price(
        self, snapshot: MarketSnapshot, candles: list[Candle]
    ) -> float:
        """Resolve the best estimate of current price."""
        if snapshot.ticker and snapshot.ticker.last_price:
            return snapshot.ticker.last_price
        if candles:
            return candles[-1].close
        return 0.0

    def _compute_levels(
        self,
        swing_high: float,
        swing_low: float,
        macro_bias: TrendDirection,
        current_price: float,
    ) -> list[dict]:
        """
        Build the full list of retracement + extension level dicts.

        Retracements
        ────────────
        BULLISH: price pulled back into the prior up-move.
          price = swing_high - (swing_high - swing_low) × level

        BEARISH: price bounced into the prior down-move.
          price = swing_low + (swing_high - swing_low) × level

        Extensions (targets beyond the swing origin)
        ────────────────────────────────────────────
        BULLISH: price projected above swing_high.
          price = swing_low + (swing_high - swing_low) × level

        BEARISH: price projected below swing_low.
          price = swing_high - (swing_high - swing_low) × level
        """
        swing_range = swing_high - swing_low
        results: list[dict] = []

        is_bullish = macro_bias == TrendDirection.BULLISH

        # ── Retracement levels ────────────────────────────────────────────────
        for lvl in _RETRACEMENT_LEVELS:
            if is_bullish:
                price = swing_high - swing_range * lvl
            else:
                price = swing_low + swing_range * lvl

            results.append(
                self._make_level(
                    level=lvl,
                    price=price,
                    level_type="retracement",
                    current_price=current_price,
                )
            )

        # ── Extension levels ──────────────────────────────────────────────────
        for lvl in _EXTENSION_LEVELS:
            if is_bullish:
                price = swing_low + swing_range * lvl
            else:
                price = swing_high - swing_range * lvl

            results.append(
                self._make_level(
                    level=lvl,
                    price=price,
                    level_type="extension",
                    current_price=current_price,
                )
            )

        return results

    @staticmethod
    def _make_level(
        level: float,
        price: float,
        level_type: str,
        current_price: float,
    ) -> dict:
        """Construct a single Fibonacci level dict."""
        within_confluence = (
            _pct_distance(current_price, price) <= _CONFLUENCE_PCT
            if current_price > 0
            else False
        )
        return {
            "level": level,
            "price": round(price, 4),
            "label": str(level),
            "type": level_type,
            "fib_confluence": within_confluence,
        }
