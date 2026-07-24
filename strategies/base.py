"""
Base strategy class. All strategies inherit from this.
A strategy evaluates whether it has a valid setup; it does NOT size positions.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from data.schemas import (
    FeatureSet, MarketContext, StrategyResult,
    Direction, RiskLevel, TrendDirection,
)
from config.settings import AgentConfig

logger = logging.getLogger(__name__)


class BaseStrategy(ABC):
    strategy_id: str = ""
    strategy_name: str = ""

    # Knowledge-base rule IDs this strategy references
    knowledge_refs: list[str] = []

    def __init__(self, config: AgentConfig):
        self.config = config
        self.fc = config.features
        self.sc = config.signal

    def evaluate(
        self, features: FeatureSet, context: MarketContext
    ) -> StrategyResult:
        """
        Entry point. Returns StrategyResult with is_valid, direction, confidence.
        Never raises — catches all errors and returns invalid result.
        """
        try:
            # Gate 1: Is this strategy applicable in current context?
            if self.strategy_id in context.unsuitable_strategies:
                return self._invalid(
                    f"Strategy {self.strategy_id} unsuitable for {context.regime.value} regime"
                )

            # Gate 2: Multi-timeframe alignment (hard gate for trend strategies)
            if self._requires_tf_alignment() and not features.tf_aligned:
                return self._invalid(
                    "MACRO_MICRO_GATE: 4h/1d timeframes not aligned"
                )

            return self._evaluate(features, context)

        except Exception as exc:
            logger.warning("Strategy %s error: %s", self.strategy_id, exc)
            return self._invalid(f"Evaluation error: {exc}")

    @abstractmethod
    def _evaluate(self, features: FeatureSet, context: MarketContext) -> StrategyResult:
        """Subclass implements this."""
        ...

    def _requires_tf_alignment(self) -> bool:
        """Override in strategies that need strict TF alignment."""
        return True

    # ──────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────

    def _invalid(self, reason: str) -> StrategyResult:
        return StrategyResult(
            strategy_id=self.strategy_id,
            strategy_name=self.strategy_name,
            is_valid=False,
            direction=Direction.NO_TRADE,
            confidence=0.0,
            violated_rules=[reason],
            knowledge_refs=self.knowledge_refs,
        )

    def _result(
        self,
        direction: Direction,
        confidence: float,
        entry: float,
        stop: float,
        targets: list[float],
        evidence: list[str],
        conflicts: list[str] | None = None,
        risk_level: RiskLevel = RiskLevel.MEDIUM,
        invalidation: str = "",
        reasoning: list[str] | None = None,
    ) -> StrategyResult:
        rr = self._compute_rr(entry, stop, targets[0]) if targets else 0.0
        return StrategyResult(
            strategy_id=self.strategy_id,
            strategy_name=self.strategy_name,
            is_valid=True,
            direction=direction,
            confidence=min(max(confidence, 0.0), 1.0),
            entry_price=entry,
            stop_price=stop,
            targets=targets,
            supporting_evidence=evidence,
            violated_rules=[],
            conflicting_evidence=conflicts or [],
            risk_level=risk_level,
            invalidation_condition=invalidation,
            knowledge_refs=self.knowledge_refs,
            reasoning=reasoning or [],
        )

    def _compute_rr(self, entry: float, stop: float, target: float) -> float:
        risk = abs(entry - stop)
        reward = abs(target - entry)
        return reward / risk if risk > 0 else 0.0

    def _meets_min_rr(self, entry: float, stop: float, target: float) -> bool:
        return self._compute_rr(entry, stop, target) >= self.config.risk.min_risk_reward

    def _volume_ok(self, features: FeatureSet) -> bool:
        return features.volume_vs_avg >= self.fc.volume_spike_threshold

    def _atr_stop(self, features: FeatureSet, price: float, direction: Direction) -> float:
        """ATR-based stop: 1.5× ATR from entry."""
        atr = features.atr
        if direction == Direction.LONG:
            return price - 1.5 * atr
        return price + 1.5 * atr

    def _log(self, msg: str) -> None:
        logger.debug("[%s] %s", self.strategy_id, msg)
