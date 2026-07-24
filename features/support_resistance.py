"""
Support / Resistance zone detection.

Algorithm:
1. Collect swing highs and lows across all available timeframes.
2. Cluster prices within sr_zone_pct (0.75%) of each other.
3. Score each cluster:
     +1 per swing point in the cluster
     +1 if represented in 2+ timeframes
     +1 if 3+ distinct touches
     +1 if a role-reversal is detected (former resistance now acting as support)
4. Discard clusters with score < 1.
5. Annotate psychological levels (round numbers) with a +1 bonus.
6. Tag zone_type: "support" | "resistance" | "both".
7. Populate FeatureSet with sr_zones, nearest_support, nearest_resistance,
   at_key_level, and level_quality.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from config.settings import FeatureConfig, DEFAULT_CONFIG
from data.schemas import (
    FeatureSet,
    MarketSnapshot,
    SRZone,
    SwingPoint,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_PSYCH_DIVISORS: dict[str, float] = {
    "BTCUSDT": 1_000.0,
    "ETHUSDT": 100.0,
}
_DEFAULT_PSYCH_DIVISOR = 100.0


def _psych_divisor(symbol: str) -> float:
    """Return the round-number increment for psychological level detection."""
    for key, divisor in _PSYCH_DIVISORS.items():
        if key in symbol.upper():
            return divisor
    return _DEFAULT_PSYCH_DIVISOR


def _is_swing_high(candles: list, idx: int, strength: int) -> bool:
    """True if candles[idx].high is greater than the `strength` bars on each side."""
    if idx < strength or idx >= len(candles) - strength:
        return False
    pivot = candles[idx].high
    for offset in range(1, strength + 1):
        if candles[idx - offset].high >= pivot:
            return False
        if candles[idx + offset].high >= pivot:
            return False
    return True


def _is_swing_low(candles: list, idx: int, strength: int) -> bool:
    """True if candles[idx].low is less than the `strength` bars on each side."""
    if idx < strength or idx >= len(candles) - strength:
        return False
    pivot = candles[idx].low
    for offset in range(1, strength + 1):
        if candles[idx - offset].low <= pivot:
            return False
        if candles[idx + offset].low <= pivot:
            return False
    return True


def _collect_swing_points(
    snapshot: MarketSnapshot,
    swing_strength: int,
) -> list[SwingPoint]:
    """Detect swing highs and lows across every timeframe in the snapshot."""
    points: list[SwingPoint] = []
    for tf, candles in snapshot.candles.items():
        if not candles or len(candles) < swing_strength * 2 + 1:
            continue
        for idx in range(len(candles)):
            if _is_swing_high(candles, idx, swing_strength):
                points.append(
                    SwingPoint(
                        price=candles[idx].high,
                        timestamp=candles[idx].timestamp,
                        timeframe=tf,
                        swing_type="high",
                        strength=swing_strength,
                    )
                )
            elif _is_swing_low(candles, idx, swing_strength):
                points.append(
                    SwingPoint(
                        price=candles[idx].low,
                        timestamp=candles[idx].timestamp,
                        timeframe=tf,
                        swing_type="low",
                        strength=swing_strength,
                    )
                )
    return points


@dataclass
class _Cluster:
    """Internal working cluster before conversion to SRZone."""
    prices: list[float] = field(default_factory=list)
    timestamps: list[int] = field(default_factory=list)
    timeframes: list[str] = field(default_factory=list)
    swing_types: list[str] = field(default_factory=list)  # "high" / "low"

    @property
    def level(self) -> float:
        return sum(self.prices) / len(self.prices) if self.prices else 0.0

    @property
    def unique_timeframes(self) -> set[str]:
        return set(self.timeframes)

    @property
    def touches(self) -> int:
        """Approximate distinct touches: count unique timestamps."""
        return len(set(self.timestamps))

    @property
    def last_touch_timestamp(self) -> int:
        return max(self.timestamps) if self.timestamps else 0


def _cluster_points(
    points: list[SwingPoint],
    zone_pct: float,
) -> list[_Cluster]:
    """
    Greedy single-pass clustering.
    A new point is absorbed into the first existing cluster whose level is
    within zone_pct of the point's price; otherwise a new cluster is started.
    """
    clusters: list[_Cluster] = []
    for pt in sorted(points, key=lambda p: p.price):
        absorbed = False
        for cl in clusters:
            if cl.level == 0.0:
                continue
            distance_pct = abs(pt.price - cl.level) / cl.level
            if distance_pct <= zone_pct:
                cl.prices.append(pt.price)
                cl.timestamps.append(pt.timestamp)
                cl.timeframes.append(pt.timeframe)
                cl.swing_types.append(pt.swing_type)
                absorbed = True
                break
        if not absorbed:
            cl = _Cluster()
            cl.prices.append(pt.price)
            cl.timestamps.append(pt.timestamp)
            cl.timeframes.append(pt.timeframe)
            cl.swing_types.append(pt.swing_type)
            clusters.append(cl)
    return clusters


def _detect_role_reversal(
    cluster: _Cluster,
    current_price: float,
    snapshot: MarketSnapshot,
    zone_pct: float,
) -> bool:
    """
    Role reversal: the zone was previously resistance (swing highs inside it),
    price has since crossed above it, and the most recent candle that touched
    the zone came from below (price bounced up from it).

    Criteria:
      - Cluster contains at least one swing high (former resistance).
      - cluster.level is now below current_price.
      - In the most-recent candle data the low came within zone_pct of the
        level and the candle closed above the level (bounce confirmation).
    """
    if cluster.level >= current_price:
        return False

    has_swing_high = "high" in cluster.swing_types
    if not has_swing_high:
        return False

    # Look for a recent bounce: any timeframe whose most recent candles
    # show a low touching the zone and closing above it.
    for tf, candles in snapshot.candles.items():
        if not candles:
            continue
        # Check the last 10 candles for a bounce off the zone.
        recent = candles[-10:]
        for candle in recent:
            low_near_zone = abs(candle.low - cluster.level) / cluster.level <= zone_pct
            closed_above = candle.close > cluster.level
            if low_near_zone and closed_above:
                return True
    return False


def _add_psychological_levels(
    clusters: list[_Cluster],
    price_range_low: float,
    price_range_high: float,
    symbol: str,
    zone_pct: float,
) -> list[_Cluster]:
    """
    Generate synthetic clusters for round-number psychological levels within
    the observed price range.  If a level already overlaps an existing cluster
    it is not duplicated — instead the existing cluster gets a marker that
    triggers a +1 score bonus during scoring.
    """
    divisor = _psych_divisor(symbol)
    first = int(price_range_low / divisor) * divisor
    psych_levels: list[float] = []
    level = float(first)
    while level <= price_range_high * 1.01:
        psych_levels.append(level)
        level += divisor

    existing_levels = {cl.level for cl in clusters}

    for pl in psych_levels:
        if pl <= 0:
            continue
        # Check overlap with existing clusters.
        overlaps = False
        for cl in clusters:
            if abs(pl - cl.level) / pl <= zone_pct:
                # Mark the cluster as touching a psychological level by
                # appending a sentinel timestamp of 0 with a special timeframe.
                if "_psych_" not in cl.timeframes:
                    cl.timeframes.append("_psych_")
                overlaps = True
                break
        if not overlaps:
            new_cl = _Cluster()
            new_cl.prices.append(pl)
            new_cl.timestamps.append(0)
            new_cl.timeframes.append("_psych_")
            new_cl.swing_types.append("psych")
            clusters.append(new_cl)

    return clusters


def _score_cluster(cluster: _Cluster) -> int:
    """
    Score a cluster according to the specification:
      +1 per swing point in the cluster
      +1 if 2+ distinct (real) timeframes represented
      +1 if 3+ touches
      +1 if role reversal (injected externally — checked via is_role_reversal flag)
      +1 if psychological level marker present (sentinel timeframe "_psych_")

    NOTE: role-reversal bonus is applied after this function via the caller.
    """
    real_tfs = {tf for tf in cluster.timeframes if tf != "_psych_"}
    real_points = sum(1 for st in cluster.swing_types if st != "psych")

    score = real_points  # +1 per swing point
    if len(real_tfs) >= 2:
        score += 1
    if cluster.touches >= 3:
        score += 1
    if "_psych_" in cluster.timeframes:
        score += 1
    return score


def _zone_type(level: float, current_price: float, zone_pct: float) -> str:
    """Classify the zone relative to the current price."""
    distance_pct = abs(level - current_price) / current_price
    if distance_pct <= zone_pct:
        return "both"
    if level < current_price:
        return "support"
    return "resistance"


# ─────────────────────────────────────────────────────────────────────────────
# BaseFeatureExtractor (minimal ABC so modules remain self-contained)
# ─────────────────────────────────────────────────────────────────────────────

class BaseFeatureExtractor:
    """
    Lightweight abstract base class.
    Subclasses implement extract(snapshot, feature_set) which mutates
    feature_set in-place and returns it.
    """

    def __init__(self, config: FeatureConfig | None = None) -> None:
        self.cfg = config or DEFAULT_CONFIG.features

    def extract(self, snapshot: MarketSnapshot, feature_set: FeatureSet) -> FeatureSet:  # pragma: no cover
        raise NotImplementedError


# ─────────────────────────────────────────────────────────────────────────────
# Main extractor
# ─────────────────────────────────────────────────────────────────────────────

class SupportResistanceExtractor(BaseFeatureExtractor):
    """
    Detect support and resistance zones from multi-timeframe swing data and
    update the supplied FeatureSet in place.

    Fields populated
    ----------------
    sr_zones            : list[SRZone] sorted by strength descending
    nearest_support     : float — closest zone level below current price
    nearest_resistance  : float — closest zone level above current price
    at_key_level        : bool  — price within sr_zone_pct of any zone scoring 3+
    level_quality       : int   — strength score of the nearest zone (0–4)
    """

    def extract(self, snapshot: MarketSnapshot, feature_set: FeatureSet) -> FeatureSet:
        try:
            self._extract(snapshot, feature_set)
        except Exception as exc:
            logger.exception("SupportResistanceExtractor failed: %s", exc)
            feature_set.extraction_errors.append(f"sr_zones: {exc}")
        return feature_set

    # ------------------------------------------------------------------
    # Internal implementation
    # ------------------------------------------------------------------

    def _extract(self, snapshot: MarketSnapshot, feature_set: FeatureSet) -> None:
        current_price = self._current_price(snapshot)
        if current_price is None:
            logger.warning("SupportResistanceExtractor: no current price available.")
            return

        zone_pct = self.cfg.sr_zone_pct  # e.g. 0.0075

        # 1. Collect swing points.
        swing_points = _collect_swing_points(snapshot, self.cfg.swing_strength)

        if not swing_points:
            logger.debug("No swing points found — skipping S/R detection.")
            return

        # 2. Cluster.
        clusters = _cluster_points(swing_points, zone_pct)

        # Derive price range for psychological level generation.
        all_prices = [pt.price for pt in swing_points]
        price_range_low = min(all_prices) * 0.98
        price_range_high = max(all_prices) * 1.02

        # 3. Add psychological levels.
        clusters = _add_psychological_levels(
            clusters, price_range_low, price_range_high, snapshot.symbol, zone_pct
        )

        # 4. Score and filter; detect role reversals.
        sr_zones: list[SRZone] = []
        for cl in clusters:
            if not cl.prices:
                continue

            score = _score_cluster(cl)
            is_rr = _detect_role_reversal(cl, current_price, snapshot, zone_pct)
            if is_rr:
                score += 1

            if score < 1:
                continue

            level = cl.level
            half_band = level * zone_pct
            ztype = _zone_type(level, current_price, zone_pct)

            real_tfs = [tf for tf in cl.unique_timeframes if tf != "_psych_"]

            zone = SRZone(
                level=level,
                zone_high=level + half_band,
                zone_low=level - half_band,
                strength=min(score, 4),  # cap at 4 per schema comment
                zone_type=ztype,
                timeframes=real_tfs,
                touches=cl.touches,
                last_touch_timestamp=cl.last_touch_timestamp,
                is_role_reversal=is_rr,
            )
            sr_zones.append(zone)

        # 5. Sort by strength descending, then by proximity to current price.
        sr_zones.sort(
            key=lambda z: (-z.strength, abs(z.level - current_price))
        )

        # 6. Populate FeatureSet.
        feature_set.sr_zones = sr_zones

        supports = [z for z in sr_zones if z.zone_type in ("support", "both") and z.level < current_price]
        resistances = [z for z in sr_zones if z.zone_type in ("resistance", "both") and z.level > current_price]

        # nearest_support: highest level that is still below current price
        if supports:
            nearest_sup = max(supports, key=lambda z: z.level)
            feature_set.nearest_support = nearest_sup.level
        else:
            feature_set.nearest_support = None

        # nearest_resistance: lowest level that is still above current price
        if resistances:
            nearest_res = min(resistances, key=lambda z: z.level)
            feature_set.nearest_resistance = nearest_res.level
        else:
            feature_set.nearest_resistance = None

        # at_key_level: within zone_pct of any zone scoring 3+
        feature_set.at_key_level = any(
            z.strength >= 3
            and abs(z.level - current_price) / current_price <= zone_pct
            for z in sr_zones
        )

        # level_quality: strength of the single nearest zone regardless of type
        if sr_zones:
            nearest_zone = min(sr_zones, key=lambda z: abs(z.level - current_price))
            feature_set.level_quality = nearest_zone.strength
        else:
            feature_set.level_quality = 0

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    @staticmethod
    def _current_price(snapshot: MarketSnapshot) -> Optional[float]:
        """Derive current price from the ticker, order book, or latest candle."""
        if snapshot.ticker and snapshot.ticker.last_price:
            return float(snapshot.ticker.last_price)
        if snapshot.order_book:
            mid = snapshot.order_book.mid_price
            if mid > 0:
                return mid
        # Fall back to the close of the most recent candle across any timeframe.
        for tf in ("1m", "3m", "5m", "15m", "1h", "4h", "1d"):
            candles = snapshot.candles.get(tf)
            if candles:
                return float(candles[-1].close)
        for candles in snapshot.candles.values():
            if candles:
                return float(candles[-1].close)
        return None
