"""
Support Bounce Strategy
Knowledge refs: ZONE_CONFLUENCE_REQUIREMENT, ROLE_REVERSAL_CONFIRMATION,
                DYNAMIC_SR_BOUNCE_ENTRY, NO_CHASING_BROKEN_LEVELS
"""

from __future__ import annotations

from data.schemas import (
    FeatureSet, MarketContext, StrategyResult,
    Direction, RiskLevel, TrendDirection,
)
from config.settings import AgentConfig
from strategies.base import BaseStrategy


class SupportBounceStrategy(BaseStrategy):
    strategy_id = "support_bounce"
    strategy_name = "Support Bounce"
    knowledge_refs = [
        "ZONE_CONFLUENCE_REQUIREMENT", "ROLE_REVERSAL_CONFIRMATION",
        "DYNAMIC_SR_BOUNCE_ENTRY", "NO_CHASING_BROKEN_LEVELS",
        "ZONE_BREAK_INVALIDATION",
    ]

    def _evaluate(self, f: FeatureSet, ctx: MarketContext) -> StrategyResult:
        evidence: list[str] = []
        conflicts: list[str] = []
        confidence = 0.0

        # Must be in uptrend or at minimum neutral macro (support bounces work in ranges too)
        if f.macro_bias == TrendDirection.BEARISH and not f.tf_aligned:
            return self._invalid(
                "TREND_DIRECTION_GATE: macro bias bearish without alignment — avoid longs"
            )

        # Must be at a support zone
        if f.nearest_support is None:
            return self._invalid("No identifiable support zone")

        # Find the support zone quality
        support_zone = None
        for zone in f.sr_zones:
            if zone.zone_type in ("support", "both") and zone.strength >= 2:
                support_zone = zone
                break

        if support_zone is None:
            return self._invalid(
                "ZONE_CONFLUENCE_REQUIREMENT: no high-quality support zone (score < 2)"
            )

        evidence.append(
            f"High-quality support zone at {support_zone.level:.2f} "
            f"(strength={support_zone.strength}, touches={support_zone.touches})"
        )
        confidence += 0.20 + support_zone.strength * 0.05

        # Role reversal bonus
        if support_zone.is_role_reversal:
            evidence.append(
                "ROLE_REVERSAL_CONFIRMATION: former resistance now acting as support"
            )
            confidence += 0.10

        # Multi-timeframe support (zone appears on multiple timeframes)
        if len(support_zone.timeframes) >= 2:
            evidence.append(
                f"MULTI_TF_SR_BONUS: support visible on {support_zone.timeframes}"
            )
            confidence += 0.15

        # Candlestick rejection at support
        rejection_patterns = [
            p for p in f.candlestick_patterns
            if p.direction == Direction.LONG
            and p.pattern_name in (
                "hammer", "bullish_engulfing", "morning_star",
                "tweezer_bottom", "marubozu_bullish", "bullish_doji"
            )
        ]
        if rejection_patterns:
            best = max(rejection_patterns, key=lambda p: p.confidence)
            evidence.append(
                f"Rejection candle at support: {best.pattern_name} "
                f"(confidence={best.confidence:.2f})"
            )
            confidence += 0.15 + best.confidence * 0.05
        else:
            conflicts.append("No rejection candlestick pattern at support")
            confidence -= 0.10

        # Volume on rejection
        if f.volume_vs_avg >= 1.5:
            evidence.append(
                f"Volume {f.volume_vs_avg:.1f}× average on rejection — confirms support"
            )
            confidence += 0.10
        else:
            conflicts.append(f"Below-average volume ({f.volume_vs_avg:.1f}×) at support")
            confidence -= 0.10

        # Momentum: RSI not in extreme territory for the zone (avoid catching falling knives)
        if f.rsi < 25:
            conflicts.append(f"RSI {f.rsi:.1f} extremely oversold — potential continuation down")
            confidence -= 0.05

        # ATR divergence: RSI making higher low while price at lower level
        bullish_divergence = any(
            d for d in f.divergences
            if d.divergence_type.value in ("bullish_regular", "bullish_hidden")
        )
        if bullish_divergence:
            evidence.append("Bullish RSI/MACD divergence at support zone")
            confidence += 0.15

        # Entry and stop
        entry = support_zone.zone_high  # Enter as price bounces from top of zone
        stop = support_zone.zone_low * (1 - 0.003)  # Just below zone floor

        risk = abs(entry - stop)
        if risk <= 0:
            return self._invalid("Invalid risk calculation (entry == stop)")

        targets = [
            entry + 2 * risk,   # 2:1 minimum
            f.nearest_resistance if f.nearest_resistance and f.nearest_resistance > entry + risk else entry + 3 * risk,
        ]
        targets = sorted(set(targets))

        if not self._meets_min_rr(entry, stop, targets[0]):
            return self._invalid(
                f"R:R {self._compute_rr(entry, stop, targets[0]):.1f} below minimum "
                f"{self.config.risk.min_risk_reward}"
            )

        confidence = min(confidence, 1.0)
        if confidence < self.sc.min_confidence:
            return self._invalid(f"Confidence {confidence:.2f} below threshold")

        return self._result(
            direction=Direction.LONG,
            confidence=confidence,
            entry=entry,
            stop=stop,
            targets=targets,
            evidence=evidence,
            conflicts=conflicts,
            risk_level=RiskLevel.LOW if support_zone.strength >= 3 else RiskLevel.MEDIUM,
            invalidation=f"Invalidated on close below support zone floor {support_zone.zone_low:.2f}",
            reasoning=[
                f"Support bounce at quality zone {support_zone.level:.2f}",
                f"Zone strength: {support_zone.strength}/4, touches: {support_zone.touches}",
            ],
        )
