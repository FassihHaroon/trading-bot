"""
Reversal Strategy — high-conviction trend reversal setups.
Requires 3+ confluence factors: divergence + pattern + volume + S/R.
Knowledge refs: DISTRIBUTION_DIVERGENCE_REQUIRED, PHASE_TRANSITION_CAUTION,
                UPTHRUST_SPRING_RECOGNITION
"""

from __future__ import annotations

from data.schemas import (
    FeatureSet, MarketContext, StrategyResult,
    Direction, RiskLevel, TrendDirection, MarketPhase,
)
from strategies.base import BaseStrategy


class ReversalStrategy(BaseStrategy):
    strategy_id = "reversal"
    strategy_name = "Reversal"
    knowledge_refs = [
        "DISTRIBUTION_DIVERGENCE_REQUIRED", "PHASE_TRANSITION_CAUTION",
        "UPTHRUST_SPRING_RECOGNITION", "CLIMACTIC_VOLUME_CAUTION",
    ]

    # Reversals need 3+ signals to fire — higher bar than other strategies
    MIN_REVERSAL_SIGNALS = 3

    def _requires_tf_alignment(self) -> bool:
        return False  # Reversal explicitly trades against trend

    def _evaluate(self, f: FeatureSet, ctx: MarketContext) -> StrategyResult:
        evidence: list[str] = []
        conflicts: list[str] = []
        confidence = 0.0
        signal_count = 0

        # Must be at extremes of a trend (distribution or markdown top/bottom)
        bearish_reversal_phase = f.trend_phase in (MarketPhase.DISTRIBUTION,)
        bullish_reversal_phase = f.trend_phase in (MarketPhase.ACCUMULATION,)

        if not bearish_reversal_phase and not bullish_reversal_phase:
            return self._invalid(
                "PHASE_TRANSITION_CAUTION: not in distribution or accumulation phase"
            )

        direction = Direction.SHORT if bearish_reversal_phase else Direction.LONG

        # ── Signal 1: Momentum divergence (REQUIRED for reversal) ─────────────
        if direction == Direction.SHORT:
            bearish_div = [
                d for d in f.divergences
                if d.divergence_type.value == "bearish_regular"
            ]
            if bearish_div:
                best = max(bearish_div, key=lambda d: d.confidence)
                evidence.append(
                    f"DISTRIBUTION_DIVERGENCE_REQUIRED: bearish {best.indicator} divergence "
                    f"(conf={best.confidence:.2f})"
                )
                confidence += 0.20 + best.confidence * 0.05
                signal_count += 1
            else:
                return self._invalid(
                    "DISTRIBUTION_DIVERGENCE_REQUIRED: bearish divergence missing — reversal blocked"
                )
        else:
            bullish_div = [
                d for d in f.divergences
                if d.divergence_type.value == "bullish_regular"
            ]
            if bullish_div:
                best = max(bullish_div, key=lambda d: d.confidence)
                evidence.append(f"Bullish divergence at potential bottom: {best.indicator}")
                confidence += 0.20 + best.confidence * 0.05
                signal_count += 1
            else:
                return self._invalid("Bullish divergence required for bottom reversal — not found")

        # ── Signal 2: Reversal candlestick pattern ────────────────────────────
        reversal_candles = [
            p for p in f.candlestick_patterns
            if p.direction == direction
            and p.pattern_name in (
                "bearish_engulfing", "shooting_star", "evening_star",
                "three_black_crows",  # bearish reversals
                "bullish_engulfing", "hammer", "morning_star",
                "three_white_soldiers",  # bullish reversals
            )
        ]
        if reversal_candles:
            best = max(reversal_candles, key=lambda p: p.confidence)
            evidence.append(f"Reversal pattern: {best.pattern_name} (conf={best.confidence:.2f})")
            confidence += 0.15 + best.confidence * 0.05
            signal_count += 1
        else:
            conflicts.append("No reversal candlestick at extreme")
            confidence -= 0.10

        # ── Signal 3: At major S/R zone ───────────────────────────────────────
        zone_type = "resistance" if direction == Direction.SHORT else "support"
        reversal_zone = next(
            (z for z in f.sr_zones if z.zone_type in (zone_type, "both") and z.strength >= 3),
            None
        )
        if reversal_zone:
            evidence.append(
                f"Major {zone_type} zone at {reversal_zone.level:.2f} "
                f"(strength={reversal_zone.strength})"
            )
            confidence += 0.15
            signal_count += 1
        else:
            conflicts.append(f"No major {zone_type} zone (strength ≥ 3)")
            confidence -= 0.05

        # ── Signal 4: Volume climax (optional but powerful) ───────────────────
        if f.volume_climax:
            evidence.append(
                "CLIMACTIC_VOLUME_CAUTION: volume climax detected — potential exhaustion/reversal"
            )
            confidence += 0.15
            signal_count += 1
        elif f.volume_vs_avg > 1.8:
            evidence.append(f"High volume ({f.volume_vs_avg:.1f}×) at extreme")
            confidence += 0.08
            signal_count += 0.5

        # ── Signal 5: Upthrust/Spring ─────────────────────────────────────────
        if direction == Direction.SHORT:
            # Upthrust: brief spike above resistance that reverses quickly
            if f.nearest_resistance and f.at_key_level:
                evidence.append("UPTHRUST_SPRING_RECOGNITION: price at resistance, potential upthrust")
                confidence += 0.10
                signal_count += 0.5
        else:
            if f.nearest_support and f.at_key_level:
                evidence.append("UPTHRUST_SPRING_RECOGNITION: price at support, potential spring")
                confidence += 0.10
                signal_count += 0.5

        # Require minimum signal count for reversal
        if signal_count < self.MIN_REVERSAL_SIGNALS:
            return self._invalid(
                f"Reversal requires {self.MIN_REVERSAL_SIGNALS} signals, got {signal_count:.0f}"
            )

        # Phase transition caution penalty
        confidence -= 0.10  # Always apply cautionary penalty to reversals
        evidence.append("PHASE_TRANSITION_CAUTION: −0.10 penalty applied to all reversals")

        # Entry and stop
        if reversal_zone:
            entry = reversal_zone.zone_low if direction == Direction.SHORT else reversal_zone.zone_high
            stop = reversal_zone.zone_high * (1 + 0.003) if direction == Direction.SHORT \
                else reversal_zone.zone_low * (1 - 0.003)
        else:
            entry = f.nearest_resistance if direction == Direction.SHORT else f.nearest_support
            if not entry:
                return self._invalid("No price reference for entry")
            stop = self._atr_stop(f, entry, direction)

        risk = abs(entry - stop)
        if risk <= 0:
            return self._invalid("Zero risk distance")

        targets = [
            entry - 2 * risk if direction == Direction.SHORT else entry + 2 * risk,
            entry - 4 * risk if direction == Direction.SHORT else entry + 4 * risk,
        ]

        if not self._meets_min_rr(entry, stop, targets[0]):
            return self._invalid(f"R:R {self._compute_rr(entry, stop, targets[0]):.1f} below minimum")

        confidence = min(confidence, 1.0)
        if confidence < self.sc.medium_confidence:  # Reversals need higher bar
            return self._invalid(f"Reversal requires medium confidence minimum ({self.sc.medium_confidence}), got {confidence:.2f}")

        return self._result(
            direction=direction,
            confidence=confidence,
            entry=entry,
            stop=stop,
            targets=targets,
            evidence=evidence,
            conflicts=conflicts,
            risk_level=RiskLevel.HIGH,  # Reversals always high risk
            invalidation="New extreme beyond stop invalidates reversal thesis",
            reasoning=[
                f"Reversal {direction.value} at {f.trend_phase.value} phase extreme",
                f"Signal count: {signal_count:.0f}/{self.MIN_REVERSAL_SIGNALS}",
            ],
        )
