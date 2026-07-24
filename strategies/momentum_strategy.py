"""
Momentum Strategy — ride strong impulse moves with MA fan + volume.
"""

from __future__ import annotations

from data.schemas import (
    FeatureSet, MarketContext, StrategyResult,
    Direction, RiskLevel, TrendDirection, MarketRegime,
)
from strategies.base import BaseStrategy


class MomentumStrategy(BaseStrategy):
    strategy_id = "momentum_strategy"
    strategy_name = "Momentum"
    knowledge_refs = ["MA_FAN_BONUS", "VOLUME_TREND_CONFIRMATION", "CLIMACTIC_VOLUME_CAUTION"]

    def _evaluate(self, f: FeatureSet, ctx: MarketContext) -> StrategyResult:
        evidence: list[str] = []
        conflicts: list[str] = []
        confidence = 0.0

        # Momentum only works in trending or high-volatility markets
        if ctx.regime not in (
            MarketRegime.TRENDING_BULL, MarketRegime.TRENDING_BEAR, MarketRegime.HIGH_VOLATILITY
        ):
            return self._invalid(f"Momentum invalid in {ctx.regime.value}")

        direction = (
            Direction.LONG if f.macro_bias == TrendDirection.BULLISH else Direction.SHORT
        )
        if f.macro_bias == TrendDirection.NEUTRAL:
            return self._invalid("No directional momentum — macro bias neutral")

        # MA fan required
        if direction == Direction.LONG and f.ma_fan_bullish:
            evidence.append("MA_FAN_BONUS: 9>21>50>200 EMA bullish order")
            confidence += 0.25
        elif direction == Direction.SHORT and f.ma_fan_bearish:
            evidence.append("MA_FAN_BONUS: EMAs bearish order")
            confidence += 0.25
        else:
            return self._invalid("MA_FAN_BONUS: MA fan not properly ordered — momentum not confirmed")

        # Volume expanding in trend direction
        if f.volume_trend == "increasing" and f.volume_vs_avg > 1.3:
            evidence.append(f"Volume expanding {f.volume_vs_avg:.1f}× — VOLUME_TREND_CONFIRMATION")
            confidence += 0.20
        else:
            conflicts.append("Volume not expanding — momentum may be weakening")
            confidence -= 0.15

        # Caution: volume climax = exhaustion signal
        if f.volume_climax:
            conflicts.append("CLIMACTIC_VOLUME_CAUTION: volume climax — possible exhaustion")
            confidence -= 0.20

        # OBV confirming
        if (direction == Direction.LONG and f.obv_trend == "up") or \
           (direction == Direction.SHORT and f.obv_trend == "down"):
            evidence.append("OBV confirming momentum direction")
            confidence += 0.10

        # Taker delta (net buying/selling pressure)
        if direction == Direction.LONG and f.taker_delta > 0:
            evidence.append(f"Positive taker delta (net buying pressure: {f.taker_delta:.0f})")
            confidence += 0.10
        elif direction == Direction.SHORT and f.taker_delta < 0:
            evidence.append(f"Negative taker delta (net selling pressure)")
            confidence += 0.10

        # MACD histogram expanding
        if "expanding" in f.macd_histogram_trend:
            expected = "expanding_bullish" if direction == Direction.LONG else "expanding_bearish"
            if f.macd_histogram_trend == expected:
                evidence.append(f"MACD histogram {f.macd_histogram_trend}")
                confidence += 0.10

        # Entry: momentum entry — requires a small pullback or continuation
        if f.last_swing_low and direction == Direction.LONG:
            entry = f.last_swing_low.price * 1.005  # Slightly above last HL
            stop = f.last_swing_low.price * (1 - 0.003)
        elif f.last_swing_high and direction == Direction.SHORT:
            entry = f.last_swing_high.price * 0.995
            stop = f.last_swing_high.price * (1 + 0.003)
        else:
            return self._invalid("No recent swing point for entry/stop reference")

        risk = abs(entry - stop)
        if risk <= 0:
            return self._invalid("Zero risk")

        targets = [
            entry + 3 * risk if direction == Direction.LONG else entry - 3 * risk,
            entry + 5 * risk if direction == Direction.LONG else entry - 5 * risk,
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
            risk_level=RiskLevel.MEDIUM,
            invalidation=f"Momentum invalidated if MA fan breaks or volume collapses",
        )
