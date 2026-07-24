"""
Resistance Rejection Strategy — mirror of support bounce, for short entries.
Knowledge refs: ZONE_CONFLUENCE_REQUIREMENT, NO_CHASING_BROKEN_LEVELS
"""

from __future__ import annotations

from data.schemas import FeatureSet, MarketContext, StrategyResult, Direction, RiskLevel, TrendDirection
from strategies.base import BaseStrategy


class ResistanceRejectionStrategy(BaseStrategy):
    strategy_id = "resistance_rejection"
    strategy_name = "Resistance Rejection"
    knowledge_refs = [
        "ZONE_CONFLUENCE_REQUIREMENT", "ROLE_REVERSAL_CONFIRMATION",
        "NO_CHASING_BROKEN_LEVELS", "ZONE_BREAK_INVALIDATION",
    ]

    def _evaluate(self, f: FeatureSet, ctx: MarketContext) -> StrategyResult:
        evidence: list[str] = []
        conflicts: list[str] = []
        confidence = 0.0

        if f.macro_bias == TrendDirection.BULLISH and not f.tf_aligned:
            return self._invalid("Macro bias bullish — avoid shorts without strong confluence")

        if f.nearest_resistance is None:
            return self._invalid("No identifiable resistance zone")

        resistance_zone = None
        for zone in f.sr_zones:
            if zone.zone_type in ("resistance", "both") and zone.strength >= 2:
                resistance_zone = zone
                break

        if resistance_zone is None:
            return self._invalid("ZONE_CONFLUENCE_REQUIREMENT: no high-quality resistance zone")

        evidence.append(
            f"High-quality resistance zone at {resistance_zone.level:.2f} "
            f"(strength={resistance_zone.strength}, touches={resistance_zone.touches})"
        )
        confidence += 0.20 + resistance_zone.strength * 0.05

        if resistance_zone.is_role_reversal:
            evidence.append("ROLE_REVERSAL_CONFIRMATION: former support now acting as resistance")
            confidence += 0.10

        if len(resistance_zone.timeframes) >= 2:
            evidence.append(f"MULTI_TF_SR_BONUS: resistance visible on {resistance_zone.timeframes}")
            confidence += 0.15

        rejection_patterns = [
            p for p in f.candlestick_patterns
            if p.direction == Direction.SHORT
            and p.pattern_name in (
                "shooting_star", "bearish_engulfing", "evening_star",
                "tweezer_top", "marubozu_bearish", "bearish_doji"
            )
        ]
        if rejection_patterns:
            best = max(rejection_patterns, key=lambda p: p.confidence)
            evidence.append(f"Rejection candle at resistance: {best.pattern_name} (conf={best.confidence:.2f})")
            confidence += 0.15 + best.confidence * 0.05
        else:
            conflicts.append("No rejection candlestick pattern at resistance")
            confidence -= 0.10

        if f.volume_vs_avg >= 1.5:
            evidence.append(f"Volume {f.volume_vs_avg:.1f}× average on rejection")
            confidence += 0.10
        else:
            conflicts.append(f"Below-average rejection volume ({f.volume_vs_avg:.1f}×)")
            confidence -= 0.10

        if f.rsi > 75:
            conflicts.append(f"RSI {f.rsi:.1f} extremely overbought — potential continuation up")
            confidence -= 0.05

        bearish_divergence = any(
            d for d in f.divergences
            if d.divergence_type.value in ("bearish_regular", "bearish_hidden")
        )
        if bearish_divergence:
            evidence.append("Bearish RSI/MACD divergence at resistance zone")
            confidence += 0.15

        # Funding rate: crowded longs at resistance = potential squeeze/reversal
        if f.funding_bias == "crowded_long":
            evidence.append("Funding rate: crowded long — liquidation risk at resistance")
            confidence += 0.10

        entry = resistance_zone.zone_low
        stop = resistance_zone.zone_high * (1 + 0.003)
        risk = abs(stop - entry)
        if risk <= 0:
            return self._invalid("Invalid risk calculation")

        targets = [
            entry - 2 * risk,
            f.nearest_support if f.nearest_support and f.nearest_support < entry - risk else entry - 3 * risk,
        ]
        targets = sorted(set(targets), reverse=True)  # closest target first for short

        if not self._meets_min_rr(entry, stop, targets[0]):
            return self._invalid(f"R:R {self._compute_rr(entry, stop, targets[0]):.1f} below minimum")

        confidence = min(confidence, 1.0)
        if confidence < self.sc.min_confidence:
            return self._invalid(f"Confidence {confidence:.2f} below threshold")

        return self._result(
            direction=Direction.SHORT,
            confidence=confidence,
            entry=entry,
            stop=stop,
            targets=targets,
            evidence=evidence,
            conflicts=conflicts,
            risk_level=RiskLevel.LOW if resistance_zone.strength >= 3 else RiskLevel.MEDIUM,
            invalidation=f"Invalidated on close above resistance zone ceiling {resistance_zone.zone_high:.2f}",
            reasoning=[f"Resistance rejection at quality zone {resistance_zone.level:.2f}"],
        )
