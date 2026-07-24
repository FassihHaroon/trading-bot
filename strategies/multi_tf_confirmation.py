"""
Multi-Timeframe Confirmation — highest-quality entries requiring 1d + 4h + 1h + 15m alignment.
Knowledge refs: MACRO_MICRO_GATE_MANDATORY, TOP_DOWN_ANALYSIS_ORDER,
                MULTI_TF_SR_BONUS, ENTRY_ONLY_ON_MICRO_CONFIRMATION
"""

from __future__ import annotations

from data.schemas import (
    FeatureSet, MarketContext, StrategyResult,
    Direction, RiskLevel, TrendDirection, StructureState,
)
from strategies.base import BaseStrategy


class MultiTFConfirmationStrategy(BaseStrategy):
    strategy_id = "multi_tf_confirmation"
    strategy_name = "Multi-Timeframe Confirmation"
    knowledge_refs = [
        "MACRO_MICRO_GATE_MANDATORY", "TOP_DOWN_ANALYSIS_ORDER",
        "MULTI_TF_SR_BONUS", "ENTRY_ONLY_ON_MICRO_CONFIRMATION",
        "COUNTER_TREND_MICRO_EXCLUDED",
    ]

    def _evaluate(self, f: FeatureSet, ctx: MarketContext) -> StrategyResult:
        evidence: list[str] = []
        conflicts: list[str] = []
        confidence = 0.0

        # ── Step 1: 1D trend (TOP_DOWN_ANALYSIS_ORDER) ───────────────────────
        daily_bias = f.tf_bias.get("1d", TrendDirection.NEUTRAL)
        if daily_bias == TrendDirection.NEUTRAL:
            return self._invalid(
                "TOP_DOWN_ANALYSIS_ORDER: daily timeframe is neutral — no directional signal"
            )
        direction = Direction.LONG if daily_bias == TrendDirection.BULLISH else Direction.SHORT
        evidence.append(f"1D trend: {daily_bias.value} — sets directional mandate")
        confidence += 0.20

        # ── Step 2: 4H confirms (MACRO_MICRO_GATE_MANDATORY) ─────────────────
        h4_bias = f.tf_bias.get("4h", TrendDirection.NEUTRAL)
        if h4_bias != daily_bias:
            return self._invalid(
                f"MACRO_MICRO_GATE_MANDATORY: 4H ({h4_bias.value}) disagrees with 1D ({daily_bias.value}) — gate FAIL"
            )
        evidence.append(f"4H confirms 1D direction: {h4_bias.value}")
        confidence += 0.20

        # ── Step 3: 1H setup ─────────────────────────────────────────────────
        h1_bias = f.tf_bias.get("1h", TrendDirection.NEUTRAL)
        if h1_bias == daily_bias:
            evidence.append("1H aligned with macro: full 3-TF alignment")
            confidence += 0.15
        elif h1_bias == TrendDirection.NEUTRAL:
            evidence.append("1H neutral — pullback context, acceptable")
            confidence += 0.08
        else:
            conflicts.append(f"1H ({h1_bias.value}) opposing macro — elevated risk")
            confidence -= 0.10

        # ── Structure must be intact ─────────────────────────────────────────
        if direction == Direction.LONG and f.structure_state not in (
            StructureState.HH_HL, StructureState.BROKEN_UP, StructureState.RANGING
        ):
            return self._invalid("Structure not supporting multi-TF long")
        if direction == Direction.SHORT and f.structure_state not in (
            StructureState.LH_LL, StructureState.BROKEN_DOWN, StructureState.RANGING
        ):
            return self._invalid("Structure not supporting multi-TF short")
        evidence.append(f"Market structure: {f.structure_state.value}")
        confidence += 0.10

        # ── Multi-TF S/R confluence bonus ────────────────────────────────────
        multi_tf_zone = next(
            (z for z in f.sr_zones if len(z.timeframes) >= 3 and z.strength >= 3), None
        )
        if multi_tf_zone:
            evidence.append(
                f"MULTI_TF_SR_BONUS: S/R zone on {multi_tf_zone.timeframes} "
                f"(strength={multi_tf_zone.strength}) +0.15"
            )
            confidence += 0.15

        # ── 15M micro confirmation (ENTRY_ONLY_ON_MICRO_CONFIRMATION) ────────
        m15_bias = f.tf_bias.get("15m", TrendDirection.NEUTRAL)
        micro_pattern_found = False

        for cp in f.candlestick_patterns:
            if (direction == Direction.LONG and cp.direction == Direction.LONG) or \
               (direction == Direction.SHORT and cp.direction == Direction.SHORT):
                if cp.timeframe in ("15m", "1h"):
                    evidence.append(
                        f"ENTRY_ONLY_ON_MICRO_CONFIRMATION: {cp.pattern_name} on {cp.timeframe}"
                    )
                    confidence += 0.15 + cp.confidence * 0.05
                    micro_pattern_found = True
                    break

        if not micro_pattern_found:
            # Check if 15m is at least not opposing
            if m15_bias == TrendDirection.NEUTRAL:
                evidence.append("15M neutral — waiting for micro trigger (partial confirmation)")
                confidence += 0.05
            else:
                conflicts.append(
                    "COUNTER_TREND_MICRO_EXCLUDED: 15M opposing macro — no micro entry trigger"
                )
                confidence -= 0.15

        # ── Volume on the entry timeframe ────────────────────────────────────
        if f.volume_vs_avg >= 1.3:
            evidence.append(f"Volume {f.volume_vs_avg:.1f}× average on confirmation timeframe")
            confidence += 0.08

        # MA fan alignment
        if (direction == Direction.LONG and f.ma_fan_bullish) or \
           (direction == Direction.SHORT and f.ma_fan_bearish):
            evidence.append("MA_FAN_BONUS: MAs properly ordered across timeframes")
            confidence += 0.10

        # ── Entry, stop, targets ─────────────────────────────────────────────
        if multi_tf_zone:
            zone = multi_tf_zone
            if direction == Direction.LONG:
                entry = zone.zone_high
                stop = zone.zone_low * (1 - 0.002)
            else:
                entry = zone.zone_low
                stop = zone.zone_high * (1 + 0.002)
        else:
            # Fallback: use last swing point
            if direction == Direction.LONG and f.last_swing_low:
                entry = f.last_swing_low.price * 1.003
                stop = f.last_swing_low.price * (1 - 0.003)
            elif direction == Direction.SHORT and f.last_swing_high:
                entry = f.last_swing_high.price * 0.997
                stop = f.last_swing_high.price * (1 + 0.003)
            else:
                return self._invalid("No price reference for multi-TF entry")

        risk = abs(entry - stop)
        if risk <= 0:
            return self._invalid("Zero risk distance")

        targets = [
            entry + 2.5 * risk if direction == Direction.LONG else entry - 2.5 * risk,
            entry + 4 * risk if direction == Direction.LONG else entry - 4 * risk,
        ]

        if not self._meets_min_rr(entry, stop, targets[0]):
            return self._invalid(f"R:R {self._compute_rr(entry, stop, targets[0]):.1f} below minimum")

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
            risk_level=RiskLevel.LOW if confidence > 0.70 else RiskLevel.MEDIUM,
            invalidation=f"Multi-TF gate fails if 4H/1D alignment breaks",
            reasoning=[
                f"Multi-TF alignment: 1D={daily_bias.value} 4H={h4_bias.value} 1H={h1_bias.value} 15M={m15_bias.value}",
            ],
        )
