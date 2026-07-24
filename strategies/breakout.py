"""
Breakout Strategy
Knowledge refs: VOLUME_CONFIRMATION_REQUIRED, NO_PREMATURE_PATTERN_TRADE,
                PATTERN_INVALIDATION_TRACKING, ZONE_BREAK_INVALIDATION
"""

from __future__ import annotations

from data.schemas import (
    FeatureSet, MarketContext, StrategyResult,
    Direction, RiskLevel, TrendDirection,
)
from strategies.base import BaseStrategy


class BreakoutStrategy(BaseStrategy):
    strategy_id = "breakout"
    strategy_name = "Breakout"
    knowledge_refs = [
        "VOLUME_CONFIRMATION_REQUIRED", "NO_PREMATURE_PATTERN_TRADE",
        "PATTERN_INVALIDATION_TRACKING", "MEASURED_TARGET_AS_TAKE_PROFIT",
    ]

    def _evaluate(self, f: FeatureSet, ctx: MarketContext) -> StrategyResult:
        evidence: list[str] = []
        conflicts: list[str] = []
        confidence = 0.0

        # Look for chart pattern breakouts first
        breakout_patterns = [
            p for p in f.chart_patterns
            if p.volume_confirmed
            and p.pattern_name in (
                "ascending_triangle", "symmetrical_triangle",
                "bull_flag", "bear_flag", "rectangle",
                "descending_triangle", "falling_wedge", "rising_wedge",
            )
        ]

        # Also look for S/R zone breakouts (price broke above resistance)
        sr_breakout = self._detect_sr_breakout(f)

        if not breakout_patterns and not sr_breakout:
            return self._invalid("No confirmed breakout pattern or S/R zone break detected")

        direction = Direction.NO_TRADE
        entry = 0.0
        stop = 0.0
        targets: list[float] = []

        # ── Chart pattern breakout ──────────────────────────────────
        if breakout_patterns:
            best_pattern = max(breakout_patterns, key=lambda p: p.confidence)
            direction = best_pattern.direction
            confidence += best_pattern.confidence * 0.4

            evidence.append(
                f"Chart pattern breakout: {best_pattern.pattern_name} "
                f"(confidence={best_pattern.confidence:.2f}, volume_confirmed=True)"
            )
            if best_pattern.volume_confirmed:
                evidence.append("VOLUME_CONFIRMATION_REQUIRED: breakout volume confirmed")
                confidence += 0.15
            if best_pattern.target:
                targets.append(best_pattern.target)
                evidence.append(f"Measured target: {best_pattern.target:.2f}")
            if best_pattern.neckline:
                stop = best_pattern.invalidation or best_pattern.neckline
            entry = f.nearest_resistance if direction == Direction.LONG else f.nearest_support or 0.0

        # ── S/R zone breakout ────────────────────────────────────────
        elif sr_breakout:
            direction, entry, stop_level, zone_height = sr_breakout
            confidence += 0.35
            evidence.append(
                f"S/R zone breakout: price broke {'above resistance' if direction == Direction.LONG else 'below support'}"
            )
            stop = stop_level
            if not targets:
                targets.append(entry + zone_height * 1.5 if direction == Direction.LONG
                                else entry - zone_height * 1.5)

        if direction == Direction.NO_TRADE or entry <= 0:
            return self._invalid("Could not determine valid breakout entry")

        # ── Macro trend alignment bonus ──────────────────────────────
        if direction == Direction.LONG and f.macro_bias == TrendDirection.BULLISH:
            evidence.append("Breakout aligned with macro bullish trend")
            confidence += 0.10
        elif direction == Direction.SHORT and f.macro_bias == TrendDirection.BEARISH:
            evidence.append("Breakout aligned with macro bearish trend")
            confidence += 0.10
        else:
            conflicts.append("Breakout against macro trend — higher failure risk")
            confidence -= 0.15

        # ── Volume ───────────────────────────────────────────────────
        if f.volume_vs_avg >= 1.5:
            evidence.append(f"Breakout volume {f.volume_vs_avg:.1f}× average")
            confidence += 0.10
        else:
            conflicts.append(
                f"Low breakout volume ({f.volume_vs_avg:.1f}×) — false breakout risk"
            )
            confidence -= 0.20  # VOLUME_CONFIRMATION_REQUIRED penalty

        # ── Stop: just inside the broken level ───────────────────────
        if stop <= 0:
            stop = self._atr_stop(f, entry, direction)
            evidence.append("Stop: 1.5× ATR (pattern invalidation fallback)")

        risk = abs(entry - stop)
        if risk <= 0:
            return self._invalid("Invalid risk (entry == stop)")

        if not targets:
            targets = [
                entry + 2.5 * risk if direction == Direction.LONG else entry - 2.5 * risk
            ]

        if not self._meets_min_rr(entry, stop, targets[0]):
            return self._invalid(f"R:R too low: {self._compute_rr(entry, stop, targets[0]):.1f}")

        confidence = min(confidence, 1.0)
        if confidence < self.sc.min_confidence:
            return self._invalid(f"Confidence {confidence:.2f} below threshold")

        return self._result(
            direction=direction,
            confidence=confidence,
            entry=entry,
            stop=stop,
            targets=targets,
            evidence=evidence,
            conflicts=conflicts,
            risk_level=RiskLevel.MEDIUM,
            invalidation=f"Invalidated if price closes back inside broken zone (stop: {stop:.2f})",
            reasoning=[f"Breakout {direction.value} with volume confirmation"],
        )

    def _detect_sr_breakout(
        self, f: FeatureSet
    ) -> tuple[Direction, float, float, float] | None:
        """Detect if price just broke above resistance or below support."""
        if not f.sr_zones:
            return None
        for zone in f.sr_zones:
            if zone.strength < 2:
                continue
            # Bullish breakout: price above zone_high on this bar
            if f.nearest_resistance and f.nearest_resistance <= zone.zone_high:
                entry = zone.zone_high
                stop = zone.zone_low
                height = zone.zone_high - zone.zone_low
                return Direction.LONG, entry, stop, height
            # Bearish breakout: price below zone_low
            if f.nearest_support and f.nearest_support >= zone.zone_low:
                entry = zone.zone_low
                stop = zone.zone_high
                height = zone.zone_high - zone.zone_low
                return Direction.SHORT, entry, stop, height
        return None
