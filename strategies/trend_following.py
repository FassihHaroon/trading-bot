"""
Trend Following Strategy
Knowledge refs: TREND_DIRECTION_GATE, MA_FAN_BONUS, WAVE_STRUCTURE_CONFIRMATION,
                VOLUME_TREND_CONFIRMATION
"""

from __future__ import annotations

from data.schemas import FeatureSet, MarketContext, StrategyResult, Direction, RiskLevel, TrendDirection, StructureState
from config.settings import AgentConfig
from strategies.base import BaseStrategy


class TrendFollowingStrategy(BaseStrategy):
    strategy_id = "trend_following"
    strategy_name = "Trend Following"
    knowledge_refs = [
        "TREND_DIRECTION_GATE", "WAVE_STRUCTURE_CONFIRMATION",
        "MA_FAN_BONUS", "VOLUME_TREND_CONFIRMATION", "SECONDARY_TREND_RISK_PREMIUM",
    ]

    def _evaluate(self, f: FeatureSet, ctx: MarketContext) -> StrategyResult:
        evidence: list[str] = []
        conflicts: list[str] = []
        confidence = 0.0

        # ── Direction from macro bias ─────────────────────────────────────────
        if f.macro_bias == TrendDirection.BULLISH:
            direction = Direction.LONG
        elif f.macro_bias == TrendDirection.BEARISH:
            direction = Direction.SHORT
        else:
            return self._invalid("TREND_DIRECTION_GATE: macro bias is neutral — no trade")

        # ── Wave structure must confirm ───────────────────────────────────────
        if direction == Direction.LONG:
            if f.structure_state not in (StructureState.HH_HL, StructureState.BROKEN_UP):
                return self._invalid(
                    "WAVE_STRUCTURE_CONFIRMATION: no HH+HL structure for long"
                )
            evidence.append("Structure: HH+HL confirmed uptrend wave")
            confidence += 0.25
        else:
            if f.structure_state not in (StructureState.LH_LL, StructureState.BROKEN_DOWN):
                return self._invalid(
                    "WAVE_STRUCTURE_CONFIRMATION: no LH+LL structure for short"
                )
            evidence.append("Structure: LH+LL confirmed downtrend wave")
            confidence += 0.25

        # ── Price above/below 200 EMA ─────────────────────────────────────────
        if direction == Direction.LONG and f.price_vs_200ema == "above":
            evidence.append("Price above 200 EMA — primary bull bias (PRICE_VS_200_EMA_BIAS)")
            confidence += 0.15
        elif direction == Direction.SHORT and f.price_vs_200ema == "below":
            evidence.append("Price below 200 EMA — primary bear bias (PRICE_VS_200_EMA_BIAS)")
            confidence += 0.15
        else:
            conflicts.append("Price on wrong side of 200 EMA — counter-trend risk (+penalty)")
            confidence -= 0.10

        # ── MA Fan formation ──────────────────────────────────────────────────
        if direction == Direction.LONG and f.ma_fan_bullish:
            evidence.append("MA fan bullish order (9>21>50>200 EMA) — MA_FAN_BONUS")
            confidence += 0.10
        elif direction == Direction.SHORT and f.ma_fan_bearish:
            evidence.append("MA fan bearish order — MA_FAN_BONUS")
            confidence += 0.10

        # ── Volume confirmation ───────────────────────────────────────────────
        if f.volume_trend == "increasing" and f.obv_trend in ("up" if direction == Direction.LONG else "down"):
            evidence.append(f"Volume increasing + OBV confirming trend — VOLUME_TREND_CONFIRMATION")
            confidence += 0.10
        elif f.volume_trend == "decreasing":
            conflicts.append("Volume declining on trend — exhaustion warning (-0.15)")
            confidence -= 0.15

        # ── Momentum alignment ────────────────────────────────────────────────
        if direction == Direction.LONG:
            if f.rsi > 50 and f.macd_histogram > 0:
                evidence.append(f"RSI {f.rsi:.1f} above 50, MACD histogram positive")
                confidence += 0.10
        else:
            if f.rsi < 50 and f.macd_histogram < 0:
                evidence.append(f"RSI {f.rsi:.1f} below 50, MACD histogram negative")
                confidence += 0.10

        # ── Determine entry and stop ──────────────────────────────────────────
        current_price = self._estimate_current_price(f)
        if current_price is None:
            return self._invalid("No current price available")

        # Entry: current market price (trend-following; enter on confirmation not prediction)
        entry = current_price

        # Stop: below last swing low (long) or above last swing high (short)
        if direction == Direction.LONG:
            if f.last_swing_low:
                stop = f.last_swing_low.price * (1 - 0.003)  # 0.3% buffer below swing low
                evidence.append(f"Stop below last swing low at {f.last_swing_low.price:.2f}")
            else:
                stop = self._atr_stop(f, entry, direction)
                evidence.append("Stop: 1.5× ATR (no clear swing low)")
        else:
            if f.last_swing_high:
                stop = f.last_swing_high.price * (1 + 0.003)
                evidence.append(f"Stop above last swing high at {f.last_swing_high.price:.2f}")
            else:
                stop = self._atr_stop(f, entry, direction)

        # Targets: 2:1 and 3:1 R from entry
        risk = abs(entry - stop)
        targets = [
            entry + 2 * risk if direction == Direction.LONG else entry - 2 * risk,
            entry + 3 * risk if direction == Direction.LONG else entry - 3 * risk,
        ]

        if not self._meets_min_rr(entry, stop, targets[0]):
            return self._invalid(
                f"R:R {self._compute_rr(entry, stop, targets[0]):.1f} < minimum {self.config.risk.min_risk_reward}"
            )

        confidence = min(confidence, 1.0)
        if confidence < self.sc.min_confidence:
            return self._invalid(f"Confidence {confidence:.2f} below minimum {self.sc.min_confidence}")

        return self._result(
            direction=direction,
            confidence=confidence,
            entry=entry,
            stop=stop,
            targets=targets,
            evidence=evidence,
            conflicts=conflicts,
            risk_level=RiskLevel.LOW if confidence > 0.75 else RiskLevel.MEDIUM,
            invalidation=(
                f"Trend invalidated if price breaks {'above last LH' if direction == Direction.SHORT else 'below last HL'}"
            ),
            reasoning=[
                f"Trend following {direction.value} on {f.symbol}",
                f"Macro bias: {f.macro_bias.value}, Structure: {f.structure_state.value}",
            ],
        )

    def _estimate_current_price(self, f: FeatureSet) -> float | None:
        """Get most recent close from nearest S/R or momentum data."""
        if f.sr_zones:
            # Use nearest resistance (long) or support (short) as reference
            pass
        # Fallback: use fibonacci or ATR-implied price
        if f.atr > 0 and f.nearest_resistance and f.nearest_support:
            return (f.nearest_resistance + f.nearest_support) / 2
        return None
