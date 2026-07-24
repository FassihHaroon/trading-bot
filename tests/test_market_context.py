"""
Unit tests for the Market Context Engine.
"""

import time
import pytest
from data.schemas import (
    FeatureSet, MarketRegime, TrendDirection,
    VolatilityLevel, MarketPhase, StructureState,
)
from config.settings import AgentConfig
from context.market_context import MarketContextEngine


@pytest.fixture
def engine():
    return MarketContextEngine(AgentConfig())


def _features(**kwargs) -> FeatureSet:
    f = FeatureSet(symbol="BTCUSDT", timestamp=int(time.time() * 1000))
    for k, v in kwargs.items():
        setattr(f, k, v)
    return f


class TestRegimeClassification:
    def test_trending_bull(self, engine):
        f = _features(
            tf_aligned=True, macro_bias=TrendDirection.BULLISH,
            trend_direction=TrendDirection.BULLISH,
            structure_state=StructureState.HH_HL,
            ma_fan_bullish=True, volatility_state=VolatilityLevel.NORMAL,
            volume_trend="increasing", obv_trend="up", volume_vs_avg=1.5,
        )
        ctx = engine.classify(f)
        assert ctx.regime == MarketRegime.TRENDING_BULL
        assert ctx.trend_direction == TrendDirection.BULLISH

    def test_trending_bear(self, engine):
        f = _features(
            tf_aligned=True, macro_bias=TrendDirection.BEARISH,
            trend_direction=TrendDirection.BEARISH,
            structure_state=StructureState.LH_LL,
            ma_fan_bearish=True, volatility_state=VolatilityLevel.NORMAL,
            volume_trend="increasing", obv_trend="down",
        )
        ctx = engine.classify(f)
        assert ctx.regime == MarketRegime.TRENDING_BEAR

    def test_ranging(self, engine):
        f = _features(
            tf_aligned=False, macro_bias=TrendDirection.NEUTRAL,
            trend_direction=TrendDirection.NEUTRAL,
            structure_state=StructureState.RANGING,
            volatility_state=VolatilityLevel.LOW,
        )
        ctx = engine.classify(f)
        assert ctx.regime in (MarketRegime.RANGING, MarketRegime.CHOPPY)

    def test_choppy_no_strategies(self, engine):
        """Choppy regime has no applicable strategies."""
        f = _features(
            tf_aligned=False, macro_bias=TrendDirection.NEUTRAL,
            trend_direction=TrendDirection.NEUTRAL,
            structure_state=StructureState.RANGING,
            volatility_state=VolatilityLevel.NORMAL,
        )
        ctx = engine.classify(f)
        if ctx.regime == MarketRegime.CHOPPY:
            assert len(ctx.applicable_strategies) == 0


class TestPhaseOverrides:
    def test_accumulation_restricts_to_range_longs(self, engine):
        """PHASE_OVERRIDE: accumulation only allows range-based longs."""
        f = _features(
            tf_aligned=True, macro_bias=TrendDirection.BULLISH,
            trend_direction=TrendDirection.BULLISH,
            structure_state=StructureState.RANGING,
            trend_phase=MarketPhase.ACCUMULATION,
            volatility_state=VolatilityLevel.LOW,
        )
        ctx = engine.classify(f)
        assert "trend_following" not in ctx.applicable_strategies
        assert "momentum_strategy" not in ctx.applicable_strategies

    def test_distribution_restricts_to_range_shorts(self, engine):
        """PHASE_OVERRIDE: distribution restricts to range shorts."""
        f = _features(
            tf_aligned=True, macro_bias=TrendDirection.BEARISH,
            trend_direction=TrendDirection.NEUTRAL,
            structure_state=StructureState.RANGING,
            trend_phase=MarketPhase.DISTRIBUTION,
            volatility_state=VolatilityLevel.NORMAL,
        )
        ctx = engine.classify(f)
        assert "trend_following" not in ctx.applicable_strategies


class TestStrategyMaps:
    def test_trending_bull_has_trend_following(self, engine):
        f = _features(
            tf_aligned=True, macro_bias=TrendDirection.BULLISH,
            trend_direction=TrendDirection.BULLISH,
            structure_state=StructureState.HH_HL,
            ma_fan_bullish=True, volatility_state=VolatilityLevel.NORMAL,
        )
        ctx = engine.classify(f)
        if ctx.regime == MarketRegime.TRENDING_BULL:
            assert "trend_following" in ctx.applicable_strategies
            assert "range_trading" not in ctx.applicable_strategies
