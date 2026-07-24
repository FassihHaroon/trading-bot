"""
Liquidity Feature Extractor

Analyses order-book depth, bid/ask structure, and futures liquidation data
to populate the liquidity-related fields on FeatureSet.

Fields written
──────────────
  orderbook_imbalance    : float   [-1, +1]   (positive = bid-heavy)
  bid_ask_spread_pct     : float              (percentage spread)
  liquidity_void_above   : Optional[float]   (price level where ask side thins)
  liquidity_void_below   : Optional[float]   (price level where bid side thins)
  funding_bias           : str               ("crowded_long" | "crowded_short" | "neutral")
  oi_trend               : str               ("increasing" | "decreasing" | "flat")
  liquidation_imbalance  : str               ("long_heavy" | "short_heavy" | "neutral")
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import List, Optional

from data.schemas import (
    Candle,
    FeatureSet,
    FuturesData,
    MarketSnapshot,
    OrderBook,
    OrderBookLevel,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Constants / tunables
# ─────────────────────────────────────────────

# Minimum relative gap between two consecutive price levels on the same side
# of the book that is classified as a "liquidity void" (0.5 % = 0.005).
VOID_GAP_THRESHOLD: float = 0.005

# Number of 1-hour candles that represent "4 hours ago" for the OI comparison.
# Each 1 h candle stores the OI at that bar's close via the futures snapshot;
# we use the 1 h candle metadata where open_interest is stored as a field
# on the Candle when available, or fall back to taker_buy_sell_ratio direction.
OI_LOOKBACK_BARS: int = 4

# Ratio threshold above which longs are considered "dominant" in liquidations.
# i.e., long_liq > LIQUIDATION_DOMINANCE_RATIO × short_liq → "long_heavy"
LIQUIDATION_DOMINANCE_RATIO: float = 2.0


# ─────────────────────────────────────────────
# Minimal base interface (mirrors the pattern used in volume.py to avoid
# a circular import on the shared base module)
# ─────────────────────────────────────────────

class BaseFeatureExtractor(ABC):
    """Minimal interface that every feature extractor must satisfy."""

    @abstractmethod
    def extract(self, snapshot: MarketSnapshot, features: FeatureSet) -> None:
        """
        Read from *snapshot*, write results into *features* in-place.
        Must never raise — catch all exceptions and append to
        features.extraction_errors instead.
        """


# ─────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────

def _find_void_above(
    asks: List[OrderBookLevel],
    mid_price: float,
    gap_threshold: float,
) -> Optional[float]:
    """
    Scan ask levels (ascending price order) for the first gap larger than
    *gap_threshold* (as a fraction of price) that sits above *mid_price*.

    Returns the price of the lower ask level where the gap begins (i.e., the
    top of the dense liquidity before the void), or None if no void is found.

    Parameters
    ----------
    asks            : List[OrderBookLevel]  Ask side, best ask first (ascending).
    mid_price       : float                 (best_bid + best_ask) / 2.
    gap_threshold   : float                 Fractional gap to classify as a void.
    """
    if len(asks) < 2:
        return None

    relevant = [lvl for lvl in asks if lvl.price > mid_price]
    if len(relevant) < 2:
        return None

    for i in range(len(relevant) - 1):
        lower_price = relevant[i].price
        upper_price = relevant[i + 1].price
        if lower_price <= 0:
            continue
        gap_pct = (upper_price - lower_price) / lower_price
        if gap_pct > gap_threshold:
            return lower_price   # price level where void begins above market

    return None


def _find_void_below(
    bids: List[OrderBookLevel],
    mid_price: float,
    gap_threshold: float,
) -> Optional[float]:
    """
    Scan bid levels (descending price order) for the first gap larger than
    *gap_threshold* that sits below *mid_price*.

    Returns the price of the upper bid level where the gap begins (i.e., the
    bottom of the dense liquidity before the void), or None if no void is found.

    Parameters
    ----------
    bids            : List[OrderBookLevel]  Bid side, best bid first (descending).
    mid_price       : float
    gap_threshold   : float
    """
    if len(bids) < 2:
        return None

    relevant = [lvl for lvl in bids if lvl.price < mid_price]
    if len(relevant) < 2:
        return None

    for i in range(len(relevant) - 1):
        upper_price = relevant[i].price
        lower_price = relevant[i + 1].price
        if upper_price <= 0:
            continue
        gap_pct = (upper_price - lower_price) / upper_price
        if gap_pct > gap_threshold:
            return upper_price   # price level where void begins below market

    return None


def _classify_oi_trend(
    candles: List[Candle],
    lookback: int,
) -> str:
    """
    Estimate open-interest trend by comparing the taker buy/sell ratio of
    the most-recent candle versus the candle *lookback* bars ago.

    Rationale: on perpetual futures the taker_buy_sell_ratio rises when
    participants are net opening longs (OI expanding with upward price
    pressure) and falls when they are net opening shorts or closing positions.
    This is the best proxy available from standard OHLCV + taker data alone.

    Returns "increasing", "decreasing", or "flat".
    """
    if len(candles) <= lookback:
        return "flat"

    recent = candles[-1]
    older  = candles[-(lookback + 1)]

    recent_ratio = recent.buy_sell_ratio   # taker_buy_volume / volume
    older_ratio  = older.buy_sell_ratio

    delta = recent_ratio - older_ratio

    if delta > 0.03:          # >3 percentage-point shift toward buying
        return "increasing"
    elif delta < -0.03:       # >3 pp shift toward selling / closing longs
        return "decreasing"
    return "flat"


def _classify_liquidation_imbalance(futures: FuturesData) -> str:
    """
    Classify which side was more heavily liquidated in the past 24 h.

    long_heavy  → longs liquidated ≥ LIQUIDATION_DOMINANCE_RATIO × shorts
                  (many long positions were forced-closed; signals potential
                  exhaustion of leveraged longs, or continuation of downside)
    short_heavy → shorts liquidated ≥ LIQUIDATION_DOMINANCE_RATIO × longs
    neutral     → neither side dominates, or data is unavailable
    """
    long_liq  = futures.liquidation_24h_long
    short_liq = futures.liquidation_24h_short

    if long_liq is None or short_liq is None:
        return "neutral"

    if short_liq > 0 and long_liq >= LIQUIDATION_DOMINANCE_RATIO * short_liq:
        return "long_heavy"
    if long_liq > 0 and short_liq >= LIQUIDATION_DOMINANCE_RATIO * long_liq:
        return "short_heavy"
    return "neutral"


# ─────────────────────────────────────────────
# Main extractor
# ─────────────────────────────────────────────

class LiquidityExtractor(BaseFeatureExtractor):
    """
    Extracts order-book and liquidation liquidity features.

    Order-book features (requires snapshot.order_book):
      orderbook_imbalance   – bid/ask volume imbalance [-1, +1]
      bid_ask_spread_pct    – percentage spread between best bid and best ask
      liquidity_void_above  – first price on the ask side where a gap > 0.5 %
                              appears above mid-price (None if not found)
      liquidity_void_below  – first price on the bid side where a gap > 0.5 %
                              appears below mid-price (None if not found)

    Futures features (requires snapshot.futures):
      funding_bias          – derived from FuturesData.funding_bias property
      oi_trend              – estimated from 1 h candle taker ratios; compares
                              most-recent bar to bar 4 h ago
      liquidation_imbalance – which side faced heavier forced-liquidations

    All fields gracefully default when source data is absent.
    """

    def extract(self, snapshot: MarketSnapshot, features: FeatureSet) -> None:  # noqa: C901
        """
        Populate liquidity-related fields on *features* in-place.

        Errors are caught per-section; partial results are written when
        possible, and error messages are appended to features.extraction_errors.
        """
        self._extract_orderbook(snapshot, features)
        self._extract_futures(snapshot, features)

    # ── Order-book section ────────────────────────────────────────────

    def _extract_orderbook(
        self,
        snapshot: MarketSnapshot,
        features: FeatureSet,
    ) -> None:
        """Populate order-book fields; skips gracefully when book is absent."""
        ob: Optional[OrderBook] = snapshot.order_book

        if ob is None:
            logger.debug(
                "[LiquidityExtractor] order_book is None for %s — "
                "skipping order-book features.",
                snapshot.symbol,
            )
            # Defaults are already set by FeatureSet dataclass initialisation.
            features.orderbook_imbalance   = 0.0
            features.bid_ask_spread_pct    = 0.0
            features.liquidity_void_above  = None
            features.liquidity_void_below  = None
            return

        try:
            features.orderbook_imbalance = float(ob.imbalance)
        except Exception as exc:
            features.extraction_errors.append(
                f"LiquidityExtractor.orderbook_imbalance: {exc}"
            )
            features.orderbook_imbalance = 0.0

        try:
            features.bid_ask_spread_pct = float(ob.spread_pct)
        except Exception as exc:
            features.extraction_errors.append(
                f"LiquidityExtractor.bid_ask_spread_pct: {exc}"
            )
            features.bid_ask_spread_pct = 0.0

        mid = ob.mid_price

        try:
            features.liquidity_void_above = _find_void_above(
                ob.asks, mid, VOID_GAP_THRESHOLD
            )
        except Exception as exc:
            features.extraction_errors.append(
                f"LiquidityExtractor.liquidity_void_above: {exc}"
            )
            features.liquidity_void_above = None

        try:
            features.liquidity_void_below = _find_void_below(
                ob.bids, mid, VOID_GAP_THRESHOLD
            )
        except Exception as exc:
            features.extraction_errors.append(
                f"LiquidityExtractor.liquidity_void_below: {exc}"
            )
            features.liquidity_void_below = None

    # ── Futures section ───────────────────────────────────────────────

    def _extract_futures(
        self,
        snapshot: MarketSnapshot,
        features: FeatureSet,
    ) -> None:
        """Populate futures-derived fields; skips gracefully when absent."""
        fut: Optional[FuturesData] = snapshot.futures

        if fut is None:
            logger.debug(
                "[LiquidityExtractor] futures is None for %s — "
                "skipping futures features.",
                snapshot.symbol,
            )
            features.funding_bias           = "neutral"
            features.oi_trend               = "flat"
            features.liquidation_imbalance  = "neutral"
            return

        # Funding bias ────────────────────────────────────────────────
        try:
            features.funding_bias = fut.funding_bias
        except Exception as exc:
            features.extraction_errors.append(
                f"LiquidityExtractor.funding_bias: {exc}"
            )
            features.funding_bias = "neutral"

        # OI trend — uses 1 h candles when available ──────────────────
        try:
            candles_1h: List[Candle] = snapshot.candles.get("1h", [])
            if candles_1h:
                features.oi_trend = _classify_oi_trend(candles_1h, OI_LOOKBACK_BARS)
            else:
                # No 1 h candles — fall back to taker ratio on whichever
                # timeframe is available, using the same 4-bar comparison.
                primary_tf = next(iter(snapshot.candles), None)
                if primary_tf is not None:
                    features.oi_trend = _classify_oi_trend(
                        snapshot.candles[primary_tf], OI_LOOKBACK_BARS
                    )
                else:
                    features.oi_trend = "flat"
        except Exception as exc:
            features.extraction_errors.append(
                f"LiquidityExtractor.oi_trend: {exc}"
            )
            features.oi_trend = "flat"

        # Liquidation imbalance ───────────────────────────────────────
        try:
            features.liquidation_imbalance = _classify_liquidation_imbalance(fut)
        except Exception as exc:
            features.extraction_errors.append(
                f"LiquidityExtractor.liquidation_imbalance: {exc}"
            )
            features.liquidation_imbalance = "neutral"
