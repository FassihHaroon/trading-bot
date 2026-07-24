"""
Pullback / Trend-Continuation Strategy
Knowledge refs: DYNAMIC_SR_BOUNCE_ENTRY, ENTRY_ONLY_ON_MICRO_CONFIRMATION,
                TREND_BENEFIT_OF_DOUBT
"""

from __future__ import annotations

from data.schemas import (
    FeatureSet, MarketContext, StrategyResult,
    Direction, RiskLevel, TrendDirection, StructureState,
)
from strategies.base import BaseStrategy


class PullbackStrategy(BaseStrategy):
    strategy_id = "pullback"
    strategy_name = "Pullback (Trend Continuation)"
    knowledge_refs = [
        "DYNAMIC_SR_BOUNCE_ENTRY", "ENTRY_ONLY_ON_MICRO_CONFIRMATION",
        "TREND_BENEFIT_OF_DOUBT", "MULTI_TF_SR_BONUS",
    ]

    def _evaluate(self, f: FeatureSet, ctx: MarketContext) -> StrategyResult:
        evidence: list[str] = []
        conflicts: list[str] = []
        confidence = 0.0

        # Requires a clear primary trend
        if f.macro_bias == TrendDirection.NEUTRAL:
            return self._invalid("Pullback requires clear macro trend — neutral bias")

        direction = (
            Direction.LONG if f.macro_bias == TrendDirection.BULLISH else Direction.SHORT
        )

        # Structure must show trend continuation (not a reversal structure)
        if direction == Direction.LONG and f.structure_state not in (
            StructureState.HH_HL, StructureState.RANGING
        ):
            return self._invalid("Structure not supporting uptrend pullback")
        if direction == Direction.SHORT and f.structure_state not in (
            StructureState.LH_LL, StructureState.RANGING
        ):
            return self._invalid("Structure not supporting downtrend pullback")

        # ── Find the pullback zone ────────────────────────────────────────
        # Pullback is valid when price has retraced to 20/50 EMA or Fibonacci zone

        pullback_zone = None
        pullback_type = ""

        # Check Fibonacci retracement (0.382–0.618 is ideal)
        ideal_fib_levels = {0.382, 0.5, 0.618}
        for fib in f.fib_retracements:
            if fib.get("level") in ideal_fib_levels and fib.get("fib_confluence"):
                pullback_zone = fib.get("price")
                pullback_type = f"Fibonacci {fib['level']} retracement"
                confidence += 0.20
                break

        # Check EMA confluence (price near 20 or 50 EMA)
        for zone in f.sr_zones:
            if zone.zone_type in ("support" if direction == Direction.LONG else "resistance", "both"):
                if len(zone.timeframes) >= 2:
                    if pullback_zone is None:
                        pullback_zone = zone.level
                        pullback_type = f"Multi-TF S/R zone at {zone.level:.2f}"
                    confidence += 0.15

        if pullback_zone is None:
            return self._invalid("No identifiable pullback zone (no Fib or EMA convergence)")

        evidence.append(f"Pullback to {pullback_type}")
        evidence.append(f"Primary trend: {f.macro_bias.value} — TREND_BENEFIT_OF_DOUBT")

        # ── Micro confirmation (ENTRY_ONLY_ON_MICRO_CONFIRMATION) ────────────
        # Need a rejection candle at the pullback zone
        micro_confirmed = False
        for cp in f.candlestick_patterns:
            if direction == Direction.LONG and cp.direction == Direction.LONG:
                micro_confirmed = True
                evidence.append(
                    f"ENTRY_ONLY_ON_MICRO_CONFIRMATION: {cp.pattern_name} at pullback zone"
                )
                confidence += 0.15 + cp.confidence * 0.05
                break
            if direction == Direction.SHORT and cp.direction == Direction.SHORT:
                micro_confirmed = True
                evidence.append(
                    f"ENTRY_ONLY_ON_MICRO_CONFIRMATION: {cp.pattern_name} at pullback zone"
                )
                confidence += 0.15 + cp.confidence * 0.05
                break

        if not micro_confirmed:
            conflicts.append("No micro confirmation trigger — ENTRY_ONLY_ON_MICRO_CONFIRMATION not met")
            confidence -= 0.15

        # ── Volume drying up on pullback ─────────────────────────────────────
        if f.volume_vs_avg < 1.0:
            evidence.append(f"Volume declining on pullback ({f.volume_vs_avg:.1f}×) — healthy correction")
            confidence += 0.10
        else:
            conflicts.append("High volume on pullback — may be trend reversal, not continuation")
            confidence -= 0.10

        # ── Momentum not at extreme against trade direction ───────────────────
        if direction == Direction.LONG and f.rsi < 30:
            conflicts.append(f"RSI {f.rsi:.1f} deeply oversold during pullback — potential flush")
            confidence -= 0.10
        elif direction == Direction.SHORT and f.rsi > 70:
            conflicts.append(f"RSI {f.rsi:.1f} deeply overbought during pullback — potential squeeze")
            confidence -= 0.10

        # MA fan alignment bonus
        if (direction == Direction.LONG and f.ma_fan_bullish) or \
           (direction == Direction.SHORT and f.ma_fan_bearish):
            evidence.append("MA fan properly ordered — MA_FAN_BONUS (+0.10)")
            confidence += 0.10

        # ── Entry, stop, targets ──────────────────────────────────────────────
        entry = pullback_zone
        if direction == Direction.LONG:
            # Stop below the most recent higher low
            if f.last_swing_low:
                stop = f.last_swing_low.price * (1 - 0.003)
            else:
                stop = self._atr_stop(f, entry, direction)
        else:
            if f.last_swing_high:
                stop = f.last_swing_high.price * (1 + 0.003)
            else:
                stop = self._atr_stop(f, entry, direction)

        risk = abs(entry - stop)
        if risk <= 0:
            return self._invalid("Zero risk distance")

        targets = [
            entry + 2 * risk if direction == Direction.LONG else entry - 2 * risk,
            entry + 3.5 * risk if direction == Direction.LONG else entry - 3.5 * risk,
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
            risk_level=RiskLevel.LOW if micro_confirmed else RiskLevel.MEDIUM,
            invalidation=(
                f"Invalidated if price breaks below last HL "
                f"({f.last_swing_low.price:.2f})" if f.last_swing_low else
                "Invalidated if price moves against trend beyond stop"
            ),
            reasoning=[
                f"Trend continuation pullback {direction.value}",
                f"Pullback zone: {pullback_type}",
            ],
        )
