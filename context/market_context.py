"""
Market Context Engine — classifies current market regime and determines
which strategy classes are applicable. Single responsibility: regime detection.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from data.schemas import (
    FeatureSet, MarketContext, MarketRegime, MarketPhase,
    TrendDirection, VolatilityLevel,
)
from config.settings import AgentConfig

logger = logging.getLogger(__name__)


class MarketContextEngine:
    """
    Classifies market regime from a FeatureSet.
    Output drives which strategies are activated — not a trading signal itself.
    """

    # Strategies eligible per regime
    REGIME_STRATEGY_MAP: dict[MarketRegime, list[str]] = {
        MarketRegime.TRENDING_BULL: [
            "trend_following", "pullback", "breakout",
            "support_bounce", "multi_tf_confirmation", "momentum_strategy",
        ],
        MarketRegime.TRENDING_BEAR: [
            "trend_following", "pullback", "breakout",
            "resistance_rejection", "multi_tf_confirmation", "momentum_strategy",
        ],
        MarketRegime.RANGING: [
            "range_trading", "support_bounce", "resistance_rejection",
            "mean_reversion",
        ],
        MarketRegime.CHOPPY: [],   # No strategy eligible — NO_TRADE
        MarketRegime.HIGH_VOLATILITY: [
            "breakout", "momentum_strategy",  # Only high-momentum strategies
        ],
        MarketRegime.LOW_VOLATILITY: [
            "range_trading", "support_bounce", "resistance_rejection",
            "mean_reversion", "breakout",  # Anticipating breakout from squeeze
        ],
    }

    # Strategies unsuitable for each regime
    REGIME_UNSUITABLE_MAP: dict[MarketRegime, list[str]] = {
        MarketRegime.TRENDING_BULL: ["range_trading", "mean_reversion", "reversal"],
        MarketRegime.TRENDING_BEAR: ["range_trading", "mean_reversion", "reversal"],
        MarketRegime.RANGING: ["trend_following", "momentum_strategy", "pullback"],
        MarketRegime.CHOPPY: [
            "trend_following", "pullback", "breakout", "range_trading",
            "mean_reversion", "reversal", "support_bounce", "resistance_rejection",
            "momentum_strategy", "multi_tf_confirmation",
        ],
        MarketRegime.HIGH_VOLATILITY: ["range_trading", "mean_reversion"],
        MarketRegime.LOW_VOLATILITY: ["momentum_strategy"],
    }

    def __init__(self, config: AgentConfig):
        self.config = config

    def classify(self, features: FeatureSet) -> MarketContext:
        """
        Determine market regime from extracted features.
        Returns MarketContext with applicable and unsuitable strategies.
        """
        reasoning: list[str] = []
        regime, confidence = self._determine_regime(features, reasoning)

        ctx = MarketContext(
            symbol=features.symbol,
            timestamp=features.timestamp,
            regime=regime,
            trend_direction=features.trend_direction,
            volatility_level=features.volatility_state,
            volume_quality=self._assess_volume_quality(features),
            phase=features.trend_phase,
            applicable_strategies=self.REGIME_STRATEGY_MAP.get(regime, []).copy(),
            unsuitable_strategies=self.REGIME_UNSUITABLE_MAP.get(regime, []).copy(),
            context_confidence=confidence,
            reasoning=reasoning,
        )

        # Phase overrides — distribution/accumulation restrict trend-following
        self._apply_phase_overrides(ctx, features)

        logger.info(
            "Market context: regime=%s trend=%s volatility=%s confidence=%.2f",
            regime.value, features.trend_direction.value,
            features.volatility_state.value, confidence,
        )
        return ctx

    # ──────────────────────────────────────────────
    # Internal classification logic
    # ──────────────────────────────────────────────

    def _determine_regime(
        self, f: FeatureSet, reasoning: list[str]
    ) -> tuple[MarketRegime, float]:
        score_bull = 0
        score_bear = 0
        score_range = 0
        score_chop = 0
        total_signals = 0

        # 1. Multi-timeframe alignment
        if f.tf_aligned:
            if f.macro_bias == TrendDirection.BULLISH:
                score_bull += 2
                reasoning.append("TF_ALIGNED: 4h+1d both bullish (+2 bull)")
            elif f.macro_bias == TrendDirection.BEARISH:
                score_bear += 2
                reasoning.append("TF_ALIGNED: 4h+1d both bearish (+2 bear)")
        else:
            score_chop += 1
            score_range += 1
            reasoning.append("TF_DISAGREEMENT: 4h/1d conflict (+1 chop, +1 range)")
        total_signals += 2

        # 2. Trend direction from trend extractor
        if f.trend_direction == TrendDirection.BULLISH:
            score_bull += 2
            reasoning.append(f"TREND: bullish strength={f.trend_strength:.2f} (+2 bull)")
        elif f.trend_direction == TrendDirection.BEARISH:
            score_bear += 2
            reasoning.append(f"TREND: bearish strength={f.trend_strength:.2f} (+2 bear)")
        else:
            score_range += 1
            reasoning.append("TREND: neutral (+1 range)")
        total_signals += 2

        # 3. Market structure
        from data.schemas import StructureState
        if f.structure_state == StructureState.HH_HL:
            score_bull += 1
            reasoning.append("STRUCTURE: HH+HL uptrend (+1 bull)")
        elif f.structure_state == StructureState.LH_LL:
            score_bear += 1
            reasoning.append("STRUCTURE: LH+LL downtrend (+1 bear)")
        elif f.structure_state == StructureState.RANGING:
            score_range += 2
            reasoning.append("STRUCTURE: ranging (+2 range)")
        total_signals += 1

        # 4. MA fan
        if f.ma_fan_bullish:
            score_bull += 1
            reasoning.append("MA_FAN: bullish order (+1 bull)")
        elif f.ma_fan_bearish:
            score_bear += 1
            reasoning.append("MA_FAN: bearish order (+1 bear)")
        total_signals += 1

        # 5. Volatility override
        if f.volatility_state == VolatilityLevel.HIGH:
            if max(score_bull, score_bear) > score_range:
                regime = (
                    MarketRegime.TRENDING_BULL
                    if score_bull > score_bear
                    else MarketRegime.TRENDING_BEAR
                )
                reasoning.append("VOLATILITY: high — overriding to HIGH_VOLATILITY")
                return MarketRegime.HIGH_VOLATILITY, 0.65
            score_chop += 1
            reasoning.append("VOLATILITY: high with no trend (+1 chop)")
        elif f.volatility_state == VolatilityLevel.LOW:
            score_range += 1
            reasoning.append("VOLATILITY: low — range or squeeze (+1 range)")
        total_signals += 1

        # 6. Volume quality as tie-breaker
        if f.volume_vs_avg > 1.3 and f.trend_direction != TrendDirection.NEUTRAL:
            if f.trend_direction == TrendDirection.BULLISH:
                score_bull += 1
            else:
                score_bear += 1
            reasoning.append("VOLUME: above average confirming trend (+1 directional)")
        total_signals += 1

        # Determine winner
        scores = {
            "bull": score_bull, "bear": score_bear,
            "range": score_range, "chop": score_chop,
        }
        best = max(scores, key=scores.get)
        best_score = scores[best]
        confidence = min(best_score / max(total_signals, 1), 1.0)

        # Choppy: no clear winner
        if best_score <= 1 and total_signals >= 4:
            reasoning.append("CHOPPY: no dominant regime signal")
            return MarketRegime.CHOPPY, 0.4

        regime_map = {
            "bull": MarketRegime.TRENDING_BULL,
            "bear": MarketRegime.TRENDING_BEAR,
            "range": MarketRegime.RANGING,
            "chop": MarketRegime.CHOPPY,
        }
        return regime_map[best], confidence

    def _assess_volume_quality(self, f: FeatureSet) -> str:
        """Volume confirming or diverging from trend?"""
        if f.trend_direction == TrendDirection.BULLISH:
            if f.volume_trend == "increasing" and f.obv_trend == "up":
                return "confirming"
            if f.volume_trend == "decreasing" or f.obv_trend == "down":
                return "diverging"
        elif f.trend_direction == TrendDirection.BEARISH:
            if f.volume_trend == "increasing" and f.obv_trend == "down":
                return "confirming"
            if f.volume_trend == "decreasing" or f.obv_trend == "up":
                return "diverging"
        return "neutral"

    def _apply_phase_overrides(self, ctx: MarketContext, f: FeatureSet) -> None:
        """Market phase restricts which strategies are valid."""
        if f.trend_phase == MarketPhase.ACCUMULATION:
            # In accumulation: only range-based longs near support
            ctx.applicable_strategies = [
                s for s in ctx.applicable_strategies
                if s in ("range_trading", "support_bounce", "mean_reversion")
            ]
            ctx.unsuitable_strategies.extend(
                ["trend_following", "momentum_strategy", "pullback"]
            )
            ctx.reasoning.append(
                "PHASE_OVERRIDE: accumulation — restricted to range-based longs only"
            )

        elif f.trend_phase == MarketPhase.DISTRIBUTION:
            # In distribution: only range-based shorts near resistance
            ctx.applicable_strategies = [
                s for s in ctx.applicable_strategies
                if s in ("range_trading", "resistance_rejection", "reversal")
            ]
            ctx.unsuitable_strategies.extend(
                ["trend_following", "momentum_strategy"]
            )
            ctx.reasoning.append(
                "PHASE_OVERRIDE: distribution — restricted to range-based shorts only"
            )
