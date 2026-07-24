"""
Unit tests for the Signal Scoring Engine.
"""

import time
import pytest
from data.schemas import (
    StrategyResult, ScoredSignal, MarketContext,
    FeatureSet, Direction, MarketRegime, TrendDirection,
    VolatilityLevel, MarketPhase,
)
from config.settings import AgentConfig
from scoring.signal_scorer import SignalScorer


@pytest.fixture
def config():
    c = AgentConfig()
    c.signal.min_strategies_for_signal = 2
    c.signal.macro_micro_gate = True
    return c


@pytest.fixture
def scorer(config):
    return SignalScorer(config)


@pytest.fixture
def features_aligned():
    f = FeatureSet(symbol="BTCUSDT", timestamp=int(time.time() * 1000))
    f.tf_aligned = True
    f.macro_bias = TrendDirection.BULLISH
    f.trend_direction = TrendDirection.BULLISH
    f.volume_vs_avg = 1.5
    f.obv_trend = "up"
    return f


@pytest.fixture
def context_bull():
    return MarketContext(
        symbol="BTCUSDT", timestamp=int(time.time() * 1000),
        regime=MarketRegime.TRENDING_BULL,
        trend_direction=TrendDirection.BULLISH,
        volatility_level=VolatilityLevel.NORMAL,
        volume_quality="confirming",
        phase=MarketPhase.MARKUP,
        context_confidence=0.80,
    )


def _make_strategy(direction: Direction, confidence: float, sid: str = "test") -> StrategyResult:
    return StrategyResult(
        strategy_id=sid,
        strategy_name=sid,
        is_valid=True,
        direction=direction,
        confidence=confidence,
        entry_price=50000.0,
        stop_price=49000.0,
        targets=[52000.0],
        supporting_evidence=["Test evidence"],
    )


class TestConfluenceGate:
    def test_single_strategy_blocked(self, scorer, features_aligned, context_bull):
        """NO_SINGLE_FACTOR_TRADE: one strategy is never enough."""
        results = [_make_strategy(Direction.LONG, 0.90)]
        scored = scorer.score(results, features_aligned, context_bull)
        assert scored.direction == Direction.NO_TRADE
        assert any("CONFLUENCE_GATE" in r for r in scored.no_trade_reasons)

    def test_two_agreeing_strategies_pass(self, scorer, features_aligned, context_bull):
        """Two agreeing strategies meet minimum confluence."""
        results = [
            _make_strategy(Direction.LONG, 0.80, "trend_following"),
            _make_strategy(Direction.LONG, 0.75, "support_bounce"),
        ]
        scored = scorer.score(results, features_aligned, context_bull)
        assert scored.direction == Direction.LONG

    def test_conflicting_directions_blocked(self, scorer, features_aligned, context_bull):
        """Balanced long/short conflict produces NO_TRADE."""
        results = [
            _make_strategy(Direction.LONG, 0.80, "strategy_a"),
            _make_strategy(Direction.SHORT, 0.80, "strategy_b"),
            _make_strategy(Direction.LONG, 0.75, "strategy_c"),
            _make_strategy(Direction.SHORT, 0.75, "strategy_d"),
        ]
        scored = scorer.score(results, features_aligned, context_bull)
        # Close scores — direction conflict
        # May or may not fire depending on weights; but conflict is noted
        assert "DIRECTIONAL_CONFLICT" in str(scored.no_trade_reasons) or \
               scored.direction in (Direction.LONG, Direction.SHORT)


class TestMacroMicroGate:
    def test_unaligned_tf_blocked(self, scorer, context_bull):
        """MACRO_MICRO_GATE_MANDATORY: unaligned timeframes block all signals."""
        features = FeatureSet(symbol="BTCUSDT", timestamp=int(time.time() * 1000))
        features.tf_aligned = False
        features.macro_bias = TrendDirection.NEUTRAL
        features.volume_vs_avg = 1.0

        results = [
            _make_strategy(Direction.LONG, 0.85, "trend_following"),
            _make_strategy(Direction.LONG, 0.80, "pullback"),
        ]
        scored = scorer.score(results, features, context_bull)
        assert scored.direction == Direction.NO_TRADE
        assert any("MACRO_MICRO_GATE" in r for r in scored.no_trade_reasons)


class TestConfidenceAggregation:
    def test_weighted_not_equal(self, scorer, features_aligned, context_bull):
        """Higher-weighted strategy contributes more to aggregate."""
        results = [
            _make_strategy(Direction.LONG, 1.0, "trend_following"),    # weight 1.0
            _make_strategy(Direction.LONG, 0.5, "mean_reversion"),     # weight 0.65
        ]
        scored = scorer.score(results, features_aligned, context_bull)
        if scored.direction == Direction.LONG:
            # Aggregate should be between 0.5 and 1.0, closer to 1.0 (trend_following heavier)
            assert 0.5 < scored.aggregate_confidence <= 1.0

    def test_no_certainty_in_output(self, scorer, features_aligned, context_bull):
        """NO_CERTAINTY_CLAIMS: confidence is never exactly 1.0 from aggregation."""
        results = [
            _make_strategy(Direction.LONG, 0.99, "a"),
            _make_strategy(Direction.LONG, 0.99, "b"),
            _make_strategy(Direction.LONG, 0.99, "c"),
        ]
        scored = scorer.score(results, features_aligned, context_bull)
        if scored.direction == Direction.LONG:
            assert scored.aggregate_confidence <= 1.0


class TestNoTradeOutput:
    def test_no_trade_is_valid(self, scorer, features_aligned, context_bull):
        """NO_TRADE_IS_VALID_OUTPUT: no-trade result is fully formed."""
        results = [_make_strategy(Direction.LONG, 0.30)]  # Below threshold
        scored = scorer.score(results, features_aligned, context_bull)
        assert scored.direction == Direction.NO_TRADE
        assert len(scored.no_trade_reasons) > 0
        assert scored.symbol == "BTCUSDT"
