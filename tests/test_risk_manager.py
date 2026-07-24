"""
Unit tests for the Risk Manager — most critical module.
"""

import pytest
import time
from unittest.mock import patch, MagicMock

from data.schemas import ScoredSignal, Direction, AgentState
from config.settings import AgentConfig, RiskConfig
from risk.risk_manager import RiskManager, RiskState


@pytest.fixture
def config():
    c = AgentConfig()
    c.risk.account_equity = 10000.0
    c.risk.max_risk_pct = 0.01
    c.risk.min_risk_reward = 2.0
    c.risk.daily_loss_limit_pct = 0.03
    c.risk.weekly_loss_limit_pct = 0.05
    c.risk.consecutive_loss_limit = 3
    c.risk.cooldown_hours = 24
    return c


@pytest.fixture
def risk_manager(config, tmp_path):
    rm = RiskManager(config)
    rm.STATE_FILE = str(tmp_path / "risk_state.json")
    rm.state = RiskState(account_equity=10000.0)
    return rm


@pytest.fixture
def valid_signal():
    return ScoredSignal(
        symbol="BTCUSDT",
        timestamp=int(time.time() * 1000),
        direction=Direction.LONG,
        aggregate_confidence=0.75,
        entry_price=50000.0,
        stop_price=49000.0,    # $1000 stop = 2% stop distance
        targets=[52000.0, 54000.0],
    )


class TestPositionSizing:
    def test_position_size_from_stop_distance(self, risk_manager, valid_signal):
        """POSITION_SIZE_FROM_STOP_DISTANCE: size = risk_amount / (entry × stop_pct)"""
        result = risk_manager.assess(valid_signal)
        assert result.signal_approved
        # risk_amount = 10000 × 0.01 = 100
        # stop_distance = |50000 - 49000| = 1000
        # stop_distance_pct = 1000 / 50000 = 0.02
        # position_size = 100 / (50000 × 0.02) = 0.1
        assert abs(result.position_size - 0.1) < 0.001
        assert abs(result.risk_amount - 100.0) < 0.01

    def test_no_confidence_scaling(self, risk_manager, valid_signal):
        """CONFIDENCE_NOT_POSITION_SCALER: same size regardless of confidence."""
        signal_low = ScoredSignal(**{**valid_signal.__dict__, "aggregate_confidence": 0.50})
        signal_high = ScoredSignal(**{**valid_signal.__dict__, "aggregate_confidence": 0.95})
        result_low = risk_manager.assess(signal_low)
        result_high = risk_manager.assess(signal_high)
        assert result_low.signal_approved and result_high.signal_approved
        assert abs(result_low.position_size - result_high.position_size) < 0.0001

    def test_stop_required(self, risk_manager):
        """STOP_REQUIRED_BEFORE_SIGNAL: rejected without stop."""
        signal = ScoredSignal(
            symbol="BTCUSDT", timestamp=int(time.time() * 1000),
            direction=Direction.LONG, aggregate_confidence=0.8,
            entry_price=50000.0, stop_price=None,
        )
        result = risk_manager.assess(signal)
        assert not result.signal_approved
        assert "STOP_REQUIRED" in result.rejection_reason

    def test_no_trade_rejected(self, risk_manager):
        """NO_TRADE signals pass through without approval."""
        signal = ScoredSignal(
            symbol="BTCUSDT", timestamp=int(time.time() * 1000),
            direction=Direction.NO_TRADE,
        )
        result = risk_manager.assess(signal)
        assert not result.signal_approved


class TestCircuitBreakers:
    def test_daily_loss_limit(self, risk_manager, valid_signal):
        """DAILY_CIRCUIT_BREAKER: trading halts at daily loss limit."""
        import datetime
        today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
        this_week = datetime.datetime.utcnow().strftime("%Y-%W")
        risk_manager.state.last_reset_date = today       # prevent daily reset
        risk_manager.state.last_reset_week = this_week   # prevent weekly reset
        risk_manager.state.daily_realized_loss = 350.0   # 3.5% of 10000 > 3% limit
        result = risk_manager.assess(valid_signal)
        assert not result.signal_approved
        assert "DAILY_CIRCUIT_BREAKER" in result.rejection_reason

    def test_weekly_loss_limit(self, risk_manager, valid_signal):
        """WEEKLY_CIRCUIT_BREAKER: trading halts at weekly loss limit."""
        import datetime
        today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
        this_week = datetime.datetime.utcnow().strftime("%Y-%W")
        risk_manager.state.last_reset_date = today       # prevent daily reset
        risk_manager.state.last_reset_week = this_week   # prevent weekly reset
        risk_manager.state.weekly_realized_loss = 550.0  # 5.5% > 5%
        result = risk_manager.assess(valid_signal)
        assert not result.signal_approved
        assert "WEEKLY_CIRCUIT_BREAKER" in result.rejection_reason

    def test_consecutive_loss_cooldown(self, risk_manager, valid_signal):
        """COOLDOWN_AFTER_CONSECUTIVE_LOSSES: engaged after 3 losses."""
        # Simulate 3 consecutive losses
        for _ in range(3):
            risk_manager.record_trade_result(pnl=-100.0, risk_amount=100.0)

        assert risk_manager.state.consecutive_losses >= 3
        result = risk_manager.assess(valid_signal)
        assert not result.signal_approved
        assert "COOLDOWN" in result.rejection_reason

    def test_win_resets_consecutive_losses(self, risk_manager):
        """A win resets consecutive loss counter."""
        risk_manager.state.consecutive_losses = 2
        risk_manager.record_trade_result(pnl=200.0, risk_amount=100.0)
        assert risk_manager.state.consecutive_losses == 0

    def test_cooldown_not_runtime_overridable(self, risk_manager, valid_signal):
        """Cooldown cannot be bypassed — it's a hard block."""
        risk_manager.state.cooldown_until = time.time() + 86400  # 24h from now
        risk_manager.state.state = AgentState.COOLDOWN
        result = risk_manager.assess(valid_signal)
        assert not result.signal_approved
        # Cannot override by changing confidence
        valid_signal.aggregate_confidence = 0.99
        result2 = risk_manager.assess(valid_signal)
        assert not result2.signal_approved


class TestMinRR:
    def test_insufficient_rr_rejected(self, risk_manager):
        """MIN_RR_REQUIREMENT: R:R below minimum is rejected."""
        signal = ScoredSignal(
            symbol="BTCUSDT", timestamp=int(time.time() * 1000),
            direction=Direction.LONG, aggregate_confidence=0.8,
            entry_price=50000.0, stop_price=49500.0,  # $500 stop
            targets=[50750.0],  # $750 target = 1.5:1 R:R < 2.0 minimum
        )
        result = risk_manager.assess(signal)
        assert not result.signal_approved
        assert "MIN_RR" in result.rejection_reason

    def test_sufficient_rr_approved(self, risk_manager, valid_signal):
        """Valid R:R passes."""
        result = risk_manager.assess(valid_signal)
        assert result.signal_approved
        assert result.risk_reward_ratio >= 2.0


class TestRecalibration:
    def test_recalibration_reduces_risk(self, risk_manager, valid_signal):
        """Post-weekly-CB recalibration uses reduced risk."""
        risk_manager.state.recalibration_sessions_remaining = 3
        result = risk_manager.assess(valid_signal)
        assert result.signal_approved
        # Risk should be 75% of normal
        expected_risk = 10000.0 * 0.01 * 0.75
        assert abs(result.risk_amount - expected_risk) < 1.0
