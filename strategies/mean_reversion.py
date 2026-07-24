"""
Mean Reversion Strategy — price at statistical extreme, revert to mean.
Works in low-volatility / ranging markets only.
"""

from __future__ import annotations

from data.schemas import (
    FeatureSet, MarketContext, StrategyResult,
    Direction, RiskLevel, MarketRegime,
)
from strategies.base import BaseStrategy


class MeanReversionStrategy(BaseStrategy):
    strategy_id = "mean_reversion"
    strategy_name = "Mean Reversion"
    knowledge_refs = ["FLAT_MA_EXCLUSION", "RSI_TRENDING_ADJUSTMENT", "DIVERGENCE_ALERT_NOT_TRIGGER"]

    def _requires_tf_alignment(self) -> bool:
        return False

    def _evaluate(self, f: FeatureSet, ctx: MarketContext) -> StrategyResult:
        evidence: list[str] = []
        conflicts: list[str] = []
        confidence = 0.0

        # Only in ranging / low-volatility regime
        if ctx.regime not in (MarketRegime.RANGING, MarketRegime.LOW_VOLATILITY):
            return self._invalid(f"Mean reversion invalid in {ctx.regime.value}")

        # Need a BB squeeze or extreme RSI
        direction = Direction.NO_TRADE

        # RSI extreme
        if f.rsi < 25:
            direction = Direction.LONG
            evidence.append(f"RSI deeply oversold: {f.rsi:.1f}")
            confidence += 0.25
        elif f.rsi > 75:
            direction = Direction.SHORT
            evidence.append(f"RSI deeply overbought: {f.rsi:.1f}")
            confidence += 0.25
        else:
            return self._invalid(f"RSI {f.rsi:.1f} not at extreme — mean reversion not triggered")

        # Stochastics confirming
        if direction == Direction.LONG and f.stoch_k < 20:
            evidence.append(f"Stoch %K {f.stoch_k:.1f} oversold confirmation")
            confidence += 0.10
        elif direction == Direction.SHORT and f.stoch_k > 80:
            evidence.append(f"Stoch %K {f.stoch_k:.1f} overbought confirmation")
            confidence += 0.10

        # Divergence (alert — not trigger, but adds confidence)
        if direction == Direction.LONG:
            bull_div = [d for d in f.divergences if "bullish" in d.divergence_type.value]
            if bull_div:
                evidence.append("DIVERGENCE_ALERT_NOT_TRIGGER: bullish divergence present")
                confidence += 0.15
        else:
            bear_div = [d for d in f.divergences if "bearish" in d.divergence_type.value]
            if bear_div:
                evidence.append("DIVERGENCE_ALERT_NOT_TRIGGER: bearish divergence present")
                confidence += 0.15

        # Candlestick confirmation
        for cp in f.candlestick_patterns:
            if cp.direction == direction:
                evidence.append(f"Reversal candle: {cp.pattern_name}")
                confidence += 0.10
                break

        # Conflicting: strong trend against mean reversion
        if f.ma_fan_bullish and direction == Direction.SHORT:
            conflicts.append("MA fan bullish — mean reversion short has trend headwind")
            confidence -= 0.20
        if f.ma_fan_bearish and direction == Direction.LONG:
            conflicts.append("MA fan bearish — mean reversion long has trend headwind")
            confidence -= 0.20

        # Entry + stop (tight — mean reversion fails fast)
        if direction == Direction.LONG:
            entry = f.nearest_support or (f.atr * 0 + 0)  # Need price reference
            stop = entry * (1 - 0.015) if entry else None  # 1.5% stop for mean reversion
        else:
            entry = f.nearest_resistance or 0
            stop = entry * (1 + 0.015) if entry else None

        if not entry or not stop:
            return self._invalid("Cannot determine entry/stop without price reference")

        risk = abs(entry - stop)
        target = (
            entry + 2 * risk if direction == Direction.LONG else entry - 2 * risk
        )

        if not self._meets_min_rr(entry, stop, target):
            return self._invalid("R:R insufficient for mean reversion")

        confidence = min(confidence, 1.0)
        if confidence < self.sc.medium_confidence:
            return self._invalid(f"Mean reversion requires medium confidence, got {confidence:.2f}")

        return self._result(
            direction=direction,
            confidence=confidence,
            entry=entry,
            stop=stop,
            targets=[target],
            evidence=evidence,
            conflicts=conflicts,
            risk_level=RiskLevel.MEDIUM,
            invalidation="New extreme beyond stop — trend continuation, not reversion",
        )
