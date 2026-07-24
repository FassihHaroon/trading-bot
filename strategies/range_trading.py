"""
Range Trading Strategy — buy low/sell high within a defined range.
Knowledge refs: NO_TREND_FOLLOWING_IN_RANGE, FLAT_MA_EXCLUSION
"""

from __future__ import annotations

from data.schemas import (
    FeatureSet, MarketContext, StrategyResult,
    Direction, RiskLevel, MarketRegime, StructureState,
)
from strategies.base import BaseStrategy


class RangeTradingStrategy(BaseStrategy):
    strategy_id = "range_trading"
    strategy_name = "Range Trading"
    knowledge_refs = [
        "NO_TREND_FOLLOWING_IN_RANGE", "FLAT_MA_EXCLUSION",
        "ZONE_CONFLUENCE_REQUIREMENT",
    ]

    def _requires_tf_alignment(self) -> bool:
        return False  # Range trading doesn't need aligned trends

    def _evaluate(self, f: FeatureSet, ctx: MarketContext) -> StrategyResult:
        evidence: list[str] = []
        conflicts: list[str] = []
        confidence = 0.0

        # Must be in ranging or accumulation/distribution
        if ctx.regime not in (MarketRegime.RANGING, MarketRegime.LOW_VOLATILITY):
            return self._invalid(f"Range trading invalid in {ctx.regime.value} regime")

        if f.structure_state != StructureState.RANGING:
            conflicts.append("Market structure not clearly ranging")
            confidence -= 0.10

        # Identify range boundaries
        if f.nearest_support is None or f.nearest_resistance is None:
            return self._invalid("Cannot identify range boundaries (need both S and R)")

        range_size = f.nearest_resistance - f.nearest_support
        if range_size <= 0:
            return self._invalid("Invalid range (resistance below support)")

        range_pct = range_size / f.nearest_support * 100
        if range_pct < 2.0:
            return self._invalid(f"Range too small ({range_pct:.1f}%) — insufficient R:R")

        evidence.append(
            f"Range defined: {f.nearest_support:.2f}–{f.nearest_resistance:.2f} "
            f"({range_pct:.1f}% wide)"
        )
        confidence += 0.25

        # Determine direction based on price position in range
        midpoint = (f.nearest_support + f.nearest_resistance) / 2

        # Find best S/R zones
        support_zone = next(
            (z for z in f.sr_zones if z.zone_type in ("support", "both") and z.strength >= 2),
            None
        )
        resistance_zone = next(
            (z for z in f.sr_zones if z.zone_type in ("resistance", "both") and z.strength >= 2),
            None
        )

        # If price near support — long
        near_support = (
            support_zone and
            abs(f.nearest_support - support_zone.level) / support_zone.level < 0.01
        )
        near_resistance = (
            resistance_zone and
            abs(f.nearest_resistance - resistance_zone.level) / resistance_zone.level < 0.01
        )

        if near_support and support_zone:
            direction = Direction.LONG
            entry = support_zone.zone_high
            stop = support_zone.zone_low * (1 - 0.002)
            target = f.nearest_resistance * (1 - 0.005)  # 0.5% before resistance
            evidence.append(f"Price near range support at {support_zone.level:.2f}")
            confidence += support_zone.strength * 0.05

        elif near_resistance and resistance_zone:
            direction = Direction.SHORT
            entry = resistance_zone.zone_low
            stop = resistance_zone.zone_high * (1 + 0.002)
            target = f.nearest_support * (1 + 0.005)
            evidence.append(f"Price near range resistance at {resistance_zone.level:.2f}")
            confidence += resistance_zone.strength * 0.05

        else:
            return self._invalid("Price not at range boundary — wait for zone approach")

        risk = abs(entry - stop)
        if risk <= 0 or not self._meets_min_rr(entry, stop, target):
            return self._invalid("Insufficient R:R within range constraints")

        # Stochastics work better in ranges
        if direction == Direction.LONG and f.stoch_k < 25:
            evidence.append(f"Stochastics oversold ({f.stoch_k:.1f}) — range buy signal")
            confidence += 0.10
        elif direction == Direction.SHORT and f.stoch_k > 75:
            evidence.append(f"Stochastics overbought ({f.stoch_k:.1f}) — range sell signal")
            confidence += 0.10

        # Candlestick rejection
        for cp in f.candlestick_patterns:
            if (direction == Direction.LONG and cp.direction == Direction.LONG) or \
               (direction == Direction.SHORT and cp.direction == Direction.SHORT):
                evidence.append(f"Rejection pattern at boundary: {cp.pattern_name}")
                confidence += 0.10
                break

        # Volume should be low (accumulation) not climactic
        if f.volume_climax:
            conflicts.append("Volume climax at range boundary — potential breakout risk")
            confidence -= 0.15

        confidence = min(confidence, 1.0)
        if confidence < self.sc.min_confidence:
            return self._invalid(f"Confidence {confidence:.2f} below threshold")

        targets = [target]
        return self._result(
            direction=direction,
            confidence=confidence,
            entry=entry,
            stop=stop,
            targets=targets,
            evidence=evidence,
            conflicts=conflicts,
            risk_level=RiskLevel.LOW,
            invalidation=f"Invalidated on range breakout beyond stop {stop:.2f}",
            reasoning=[f"Range trade {direction.value} at boundary"],
        )
