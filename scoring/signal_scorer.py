"""
Signal Scoring Engine — aggregates StrategyResults into a single ScoredSignal.
Weights evidence; does not simply count indicators.
Knowledge refs: NO_SINGLE_FACTOR_TRADE, CONFIDENCE_NOT_POSITION_SCALER,
                NO_CERTAINTY_CLAIMS, PROBABILITY_LANGUAGE_IN_TRACE
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path

from data.schemas import (
    StrategyResult, ScoredSignal, MarketContext,
    Direction, FeatureSet, TrendDirection,
)
from config.settings import AgentConfig

logger = logging.getLogger(__name__)

_LEARNED_STATE_FILE = Path("logs/learned_state.json")
_ADAPTIVE_BLEND = 0.50      # 50% base weight + 50% learned weight


class SignalScorer:
    """
    Takes all strategy results and produces one ScoredSignal.
    Applies weighted averaging — not simple counting.
    Enforces confluence requirements before producing a directional signal.
    Blends static base weights with self-learned adaptive weights.
    """

    def __init__(self, config: AgentConfig):
        self.config = config
        self.sc = config.signal
        self._learned_state: dict = {}
        self._confidence_gate_adjustment: float = 0.0

        # Normalise base weights so they sum to 1
        total = sum(self.sc.strategy_weights.values())
        self._base_weights = {
            k: v / total for k, v in self.sc.strategy_weights.items()
        }
        self.weights = dict(self._base_weights)

        # Load adaptive weights from trade analyzer (if available)
        self._reload_learned_weights()

    def _reload_learned_weights(self) -> None:
        """Load the latest learned state from disk and blend with base weights."""
        if not _LEARNED_STATE_FILE.exists():
            return
        try:
            state = json.loads(_LEARNED_STATE_FILE.read_text())
            adaptive = state.get("adaptive_weights", {})
            self._confidence_gate_adjustment = float(state.get("confidence_adjustment", 0.0))
            self._learned_state = state

            if not adaptive:
                return

            # Blend: final = (1-blend)*base + blend*(base * multiplier)
            blended = {}
            for strat, base_w in self._base_weights.items():
                multiplier = adaptive.get(strat, 1.0)
                blended[strat] = base_w * (1 - _ADAPTIVE_BLEND + _ADAPTIVE_BLEND * multiplier)

            # Re-normalise so weights sum to 1
            total = sum(blended.values())
            self.weights = {k: v / total for k, v in blended.items()}

            if self._confidence_gate_adjustment != 0.0:
                logger.info(
                    "SignalScorer: confidence gate adjusted by %+.3f due to recent performance",
                    self._confidence_gate_adjustment,
                )
            logger.info("SignalScorer: loaded adaptive weights from %s", _LEARNED_STATE_FILE)
        except Exception as exc:
            logger.warning("SignalScorer: could not load learned weights: %s", exc)

    def score(
        self,
        strategy_results: list[StrategyResult],
        features: FeatureSet,
        context: MarketContext,
    ) -> ScoredSignal:
        """
        Produce a ScoredSignal from all strategy evaluations.
        """
        symbol = features.symbol
        ts = features.timestamp

        valid = [r for r in strategy_results if r.is_valid and r.direction != Direction.NO_TRADE]
        invalid = [r for r in strategy_results if not r.is_valid]

        # ── Gate 1: Minimum strategy confluence ──────────────────────────────
        if len(valid) < self.sc.min_strategies_for_signal:
            return self._no_trade(
                symbol, ts, strategy_results,
                reason=(
                    f"CONFLUENCE_GATE: only {len(valid)} valid strategies "
                    f"(minimum {self.sc.min_strategies_for_signal})"
                ),
            )

        # ── Gate 2: Directional agreement ────────────────────────────────────
        long_strategies = [r for r in valid if r.direction == Direction.LONG]
        short_strategies = [r for r in valid if r.direction == Direction.SHORT]

        if long_strategies and short_strategies:
            # Conflicting signals
            long_score = sum(self._strategy_weight(r) * r.confidence for r in long_strategies)
            short_score = sum(self._strategy_weight(r) * r.confidence for r in short_strategies)

            if abs(long_score - short_score) < 0.15:
                return self._no_trade(
                    symbol, ts, strategy_results,
                    reason=(
                        f"DIRECTIONAL_CONFLICT: long_score={long_score:.2f} vs "
                        f"short_score={short_score:.2f} — too close, ambiguous signal"
                    ),
                )
            # Take the stronger side
            agreed_strategies = long_strategies if long_score > short_score else short_strategies
            direction = Direction.LONG if long_score > short_score else Direction.SHORT
        elif long_strategies:
            direction = Direction.LONG
            agreed_strategies = long_strategies
        elif short_strategies:
            direction = Direction.SHORT
            agreed_strategies = short_strategies
        else:
            return self._no_trade(symbol, ts, strategy_results, reason="No directional strategies valid")

        # ── Gate 3: Macro/micro gate (if enabled) ────────────────────────────
        if self.sc.macro_micro_gate:
            if not features.tf_aligned:
                return self._no_trade(
                    symbol, ts, strategy_results,
                    reason="MACRO_MICRO_GATE_MANDATORY: 4H/1D timeframes not aligned — hard gate FAIL",
                )
            # Use macro_bias (4h+1d MTF) not context.trend_direction (1h only) —
            # the 1h trend can be "neutral" while the macro is clearly directional.
            if direction == Direction.LONG and features.macro_bias == TrendDirection.BEARISH:
                return self._no_trade(
                    symbol, ts, strategy_results,
                    reason="MACRO_MICRO_GATE: long signal in bearish macro context blocked",
                )
            if direction == Direction.SHORT and features.macro_bias == TrendDirection.BULLISH:
                return self._no_trade(
                    symbol, ts, strategy_results,
                    reason="MACRO_MICRO_GATE: short signal in bullish macro context blocked",
                )

        # ── Weighted confidence calculation ───────────────────────────────────
        regime_str = context.regime.value if context and context.regime else ""
        weighted_sum = 0.0
        weight_total = 0.0
        evidence: list[str] = []
        conflicts: list[str] = []
        knowledge_refs: set[str] = set()

        for result in agreed_strategies:
            w = self._strategy_weight(result, regime=regime_str)
            weighted_sum += w * result.confidence
            weight_total += w
            evidence.extend(result.supporting_evidence[:3])   # Top 3 per strategy
            conflicts.extend(result.conflicting_evidence[:2])
            knowledge_refs.update(result.knowledge_refs)
            logger.debug(
                "Strategy %s: weight=%.2f confidence=%.2f weighted_contribution=%.3f",
                result.strategy_id, w, result.confidence, w * result.confidence,
            )

        aggregate_confidence = weighted_sum / weight_total if weight_total > 0 else 0.0

        # ── Apply context modifier ────────────────────────────────────────────
        context_bonus = self._context_confidence_modifier(context, direction)
        aggregate_confidence = min(aggregate_confidence + context_bonus, 1.0)

        # ── Apply external sentiment modifier ─────────────────────────────────
        # NEWS_CONFIRMATION_REQUIRED (master_rules): news cannot trigger a trade;
        # it can only shift an existing valid signal by ≤ ±10%.
        # MACRO_EVENT_CAUTION: high-impact events within 24h reduce confidence.
        sentiment_delta = self._external_sentiment_modifier(features, direction)
        aggregate_confidence = max(0.0, min(aggregate_confidence + sentiment_delta, 1.0))
        if sentiment_delta != 0.0:
            evidence.append(
                f"news_sentiment_modifier={sentiment_delta:+.3f} "
                f"(score={features.news_sentiment_score:.2f} "
                f"macro_risk={features.macro_event_risk})"
            )

        # ── Final confidence threshold ────────────────────────────────────────
        required_confidence = self._required_confidence(len(agreed_strategies))
        if aggregate_confidence < required_confidence:
            return self._no_trade(
                symbol, ts, strategy_results,
                reason=(
                    f"CONFIDENCE_GATE: aggregate={aggregate_confidence:.2f} < "
                    f"required={required_confidence:.2f} for {len(agreed_strategies)} strategies"
                ),
            )

        # ── Determine levels (consensus across strategies) ────────────────────
        entry = self._consensus_price(
            [r.entry_price for r in agreed_strategies if r.entry_price]
        )
        stop = self._consensus_price(
            [r.stop_price for r in agreed_strategies if r.stop_price]
        )
        # Targets: collect from agreed strategies, then filter to correct side of entry.
        # Without filtering, strategies with different entry references can produce
        # targets that are "wrong" relative to the consensus entry (e.g., a SHORT
        # strategy's target may end up above the consensus entry when entries differ).
        all_targets = [t for r in agreed_strategies for t in r.targets]
        if direction == Direction.LONG:
            valid_targets = [t for t in all_targets if entry is None or t > entry]
            targets = sorted(valid_targets) if valid_targets else []
        else:
            valid_targets = [t for t in all_targets if entry is None or t < entry]
            targets = sorted(valid_targets, reverse=True) if valid_targets else []

        logger.info(
            "Signal: %s %s | confidence=%.2f | strategies=%s",
            direction.value.upper(), symbol, aggregate_confidence,
            [r.strategy_id for r in agreed_strategies],
        )

        return ScoredSignal(
            symbol=symbol,
            timestamp=ts,
            direction=direction,
            aggregate_confidence=aggregate_confidence,
            valid_strategies=agreed_strategies,
            invalid_strategies=invalid,
            supporting_strategies=[r.strategy_id for r in agreed_strategies],
            conflicting_strategies=[
                r.strategy_id for r in strategy_results
                if r.is_valid and r not in agreed_strategies
            ],
            entry_price=entry,
            stop_price=stop,
            targets=targets[:4],  # Max 4 targets
            evidence_summary=list(dict.fromkeys(evidence))[:10],  # Deduplicated
            conflict_summary=list(dict.fromkeys(conflicts))[:5],
            no_trade_reasons=[],
            knowledge_refs=sorted(knowledge_refs),
        )

    # ──────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────

    def _strategy_weight(self, result: StrategyResult, regime: str = "") -> float:
        """Return weight, boosted/reduced by regime-specific learned performance."""
        base = self.weights.get(result.strategy_id, 0.5)
        if regime and self._learned_state:
            regime_weights = self._learned_state.get("regime_weights", {})
            if regime in regime_weights:
                multiplier = regime_weights[regime].get(result.strategy_id, 1.0)
                base = base * (0.5 + 0.5 * multiplier)
        return base

    def _required_confidence(self, strategy_count: int) -> float:
        """
        More agreeing strategies = lower individual confidence threshold.
        Adjusted by trade-analyzer gate (raised when win rate is poor).
        """
        if strategy_count >= 5:
            base = self.sc.confidence_required_for_5_factors
        elif strategy_count >= 4:
            base = self.sc.confidence_required_for_4_factors
        else:
            base = self.sc.confidence_required_for_3_factors
        # Apply learned adjustment (positive = raise the bar, negative = lower it)
        adjusted = base + self._confidence_gate_adjustment
        return max(0.35, min(0.85, adjusted))

    def reload_weights(self) -> None:
        """Re-read learned weights from disk. Call after TradeAnalyzer.run()."""
        self._reload_learned_weights()

    def _context_confidence_modifier(self, context: MarketContext, direction: Direction) -> float:
        """Adjust confidence based on how well the regime supports this trade type."""
        from data.schemas import MarketRegime
        bonus = 0.0
        if direction == Direction.LONG and context.regime == MarketRegime.TRENDING_BULL:
            bonus += 0.05
        elif direction == Direction.SHORT and context.regime == MarketRegime.TRENDING_BEAR:
            bonus += 0.05
        if context.volume_quality == "confirming":
            bonus += 0.03
        elif context.volume_quality == "diverging":
            bonus -= 0.05
        return bonus

    def _external_sentiment_modifier(self, features: FeatureSet, direction: Direction) -> float:
        """
        Compute a confidence delta from news sentiment and macro event risk.

        Rules (NEWS_CONFIRMATION_REQUIRED, MACRO_EVENT_CAUTION):
        - News sentiment is a modifier only, capped at ±max_score_modifier (default 0.10).
        - Aligned news adds confidence; opposing news subtracts it.
        - High-impact macro event within 24h applies a hard penalty.
        - Fear & Greed extreme readings provide a mild contrarian nudge.

        Returns a float delta to add to aggregate_confidence.
        """
        ext = self.config.external
        delta = 0.0

        # News sentiment: only applies when direction matches or opposes sentiment
        news_score = features.news_sentiment_score  # -1.0 to +1.0
        if direction == Direction.LONG and news_score > 0:
            delta += ext.news_max_score_modifier * min(news_score, 1.0)
        elif direction == Direction.LONG and news_score < 0:
            delta += ext.news_max_score_modifier * max(news_score, -1.0)  # negative
        elif direction == Direction.SHORT and news_score < 0:
            delta += ext.news_max_score_modifier * min(abs(news_score), 1.0)
        elif direction == Direction.SHORT and news_score > 0:
            delta -= ext.news_max_score_modifier * min(news_score, 1.0)

        # Macro event risk penalty (MACRO_EVENT_CAUTION)
        macro_risk = features.macro_event_risk
        if macro_risk == "high":
            delta -= ext.macro_high_impact_penalty
            logger.info(
                "MACRO_EVENT_CAUTION: high-impact event within 24h — "
                "applying confidence penalty of %.2f", ext.macro_high_impact_penalty
            )
        elif macro_risk == "medium":
            delta -= ext.macro_high_impact_penalty * 0.5

        # Fear & Greed contrarian nudge (soft rule, small weight)
        fg_bias = features.fear_greed_signal_bias
        if direction == Direction.LONG and fg_bias == "contrarian_long":
            delta += 0.03   # Extreme fear → contrarian long is supported
        elif direction == Direction.LONG and fg_bias == "contrarian_short":
            delta -= 0.03   # Extreme greed → caution for longs
        elif direction == Direction.SHORT and fg_bias == "contrarian_short":
            delta += 0.03
        elif direction == Direction.SHORT and fg_bias == "contrarian_long":
            delta -= 0.03

        # Cap total external delta
        max_delta = ext.news_max_score_modifier + ext.macro_high_impact_penalty + 0.03
        return max(-max_delta, min(delta, max_delta))

    def _consensus_price(self, prices: list[float]) -> float | None:
        if not prices:
            return None
        # Weighted median — outlier-resistant
        return sorted(prices)[len(prices) // 2]

    def _no_trade(
        self,
        symbol: str,
        timestamp: int,
        results: list[StrategyResult],
        reason: str,
    ) -> ScoredSignal:
        logger.info("NO_TRADE: %s | %s", symbol, reason)
        return ScoredSignal(
            symbol=symbol,
            timestamp=timestamp,
            direction=Direction.NO_TRADE,
            aggregate_confidence=0.0,
            valid_strategies=[r for r in results if r.is_valid],
            invalid_strategies=[r for r in results if not r.is_valid],
            no_trade_reasons=[reason],
        )
