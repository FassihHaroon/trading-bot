"""
Strategy Registry — assembles and runs all strategies against current features + context.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from data.schemas import FeatureSet, MarketContext, StrategyResult
from config.settings import AgentConfig
from strategies.base import BaseStrategy

logger = logging.getLogger(__name__)


class StrategyRegistry:
    """
    Holds all strategy instances. Evaluates all applicable strategies
    and returns their results for the signal scorer.
    """

    def __init__(self, config: AgentConfig):
        self.config = config
        self._strategies: dict[str, BaseStrategy] = {}
        self._load_strategies()

    def _load_strategies(self) -> None:
        from strategies.trend_following import TrendFollowingStrategy
        from strategies.pullback import PullbackStrategy
        from strategies.breakout import BreakoutStrategy
        from strategies.support_bounce import SupportBounceStrategy
        from strategies.resistance_rejection import ResistanceRejectionStrategy
        from strategies.range_trading import RangeTradingStrategy
        from strategies.reversal import ReversalStrategy
        from strategies.mean_reversion import MeanReversionStrategy
        from strategies.momentum_strategy import MomentumStrategy
        from strategies.multi_tf_confirmation import MultiTFConfirmationStrategy

        classes = [
            TrendFollowingStrategy, PullbackStrategy, BreakoutStrategy,
            SupportBounceStrategy, ResistanceRejectionStrategy, RangeTradingStrategy,
            ReversalStrategy, MeanReversionStrategy, MomentumStrategy,
            MultiTFConfirmationStrategy,
        ]
        for cls in classes:
            instance = cls(self.config)
            self._strategies[instance.strategy_id] = instance
            logger.debug("Registered strategy: %s", instance.strategy_id)

    def evaluate_all(
        self,
        features: FeatureSet,
        context: MarketContext,
        max_workers: int = 4,
    ) -> list[StrategyResult]:
        """
        Evaluate all registered strategies. Returns all results (valid and invalid).
        Uses a thread pool for parallel evaluation.
        """
        results: list[StrategyResult] = []

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(strategy.evaluate, features, context): sid
                for sid, strategy in self._strategies.items()
            }
            for future in as_completed(futures):
                sid = futures[future]
                try:
                    result = future.result()
                    results.append(result)
                    logger.debug(
                        "Strategy %s: valid=%s direction=%s confidence=%.2f",
                        sid, result.is_valid,
                        result.direction.value, result.confidence,
                    )
                except Exception as exc:
                    logger.warning("Strategy %s raised: %s", sid, exc)
                    results.append(StrategyResult(
                        strategy_id=sid,
                        strategy_name=sid,
                        is_valid=False,
                        violated_rules=[f"Unhandled exception: {exc}"],
                    ))

        return results

    def get_strategy(self, strategy_id: str) -> BaseStrategy | None:
        return self._strategies.get(strategy_id)

    @property
    def strategy_ids(self) -> list[str]:
        return list(self._strategies.keys())
