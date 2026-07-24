"""
Risk Engine — the final gate before execution.
Implements ALL psychology/risk rules as hard code, not suggestions.
Knowledge refs: POSITION_SIZE_FROM_STOP_DISTANCE, RISK_PCT_HARD_CEILING,
                STOP_REQUIRED_BEFORE_SIGNAL, COOLDOWN_AFTER_CONSECUTIVE_LOSSES,
                DAILY_CIRCUIT_BREAKER, WEEKLY_CIRCUIT_BREAKER, NO_STOP_WIDENING
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from data.schemas import ScoredSignal, RiskAssessment, TradeSignal, Direction, AgentState
from config.settings import AgentConfig

logger = logging.getLogger(__name__)


@dataclass
class RiskState:
    """Persistent risk state — survives across cycles."""
    consecutive_losses: int = 0
    cooldown_until: float = 0.0              # Unix timestamp
    daily_realized_loss: float = 0.0
    daily_realized_pnl: float = 0.0
    weekly_realized_loss: float = 0.0
    circuit_breaker_daily_until: float = 0.0
    circuit_breaker_weekly_until: float = 0.0
    recalibration_sessions_remaining: int = 0
    last_reset_date: str = ""               # "YYYY-MM-DD"
    last_reset_week: str = ""               # "YYYY-WW"
    account_equity: float = 10000.0
    open_positions: int = 0
    state: AgentState = AgentState.ACTIVE

    def to_dict(self) -> dict:
        d = asdict(self)
        d["state"] = self.state.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "RiskState":
        d = d.copy()
        d["state"] = AgentState(d.get("state", "active"))
        return cls(**d)


class RiskManager:
    """
    Validates a ScoredSignal against all risk rules.
    Computes position size from stop distance (never from confidence).
    Enforces circuit breakers with no runtime override.
    """

    STATE_FILE = "logs/risk_state.json"

    def __init__(self, config: AgentConfig):
        self.config = config
        self.rc = config.risk
        self.state = self._load_state()

    # ──────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────

    def assess(self, signal: ScoredSignal) -> RiskAssessment:
        """
        Gate the scored signal through all risk rules.
        Returns RiskAssessment with signal_approved=True/False.
        """
        self._reset_daily_weekly_if_needed()

        # Gate 1: Agent state (cooldown / circuit breakers)
        state_check = self._check_agent_state()
        if state_check:
            return RiskAssessment(
                signal_approved=False,
                rejection_reason=state_check,
                agent_state=self.state.state,
                account_equity=self.state.account_equity,
                daily_loss_used_pct=self._daily_loss_pct(),
                weekly_loss_used_pct=self._weekly_loss_pct(),
                consecutive_losses=self.state.consecutive_losses,
            )

        # NO_TRADE signals pass through (nothing to validate)
        if signal.direction == Direction.NO_TRADE:
            return RiskAssessment(
                signal_approved=False,
                rejection_reason="Signal direction is NO_TRADE",
                agent_state=self.state.state,
                account_equity=self.state.account_equity,
                daily_loss_used_pct=self._daily_loss_pct(),
                weekly_loss_used_pct=self._weekly_loss_pct(),
                consecutive_losses=self.state.consecutive_losses,
            )

        # Gate 2: Stop must exist (STOP_REQUIRED_BEFORE_SIGNAL)
        if signal.stop_price is None or signal.entry_price is None:
            return self._reject("STOP_REQUIRED_BEFORE_SIGNAL: stop_price or entry_price missing")

        # Gate 3: Compute position size (POSITION_SIZE_FROM_STOP_DISTANCE)
        equity = self.state.account_equity
        risk_pct = self._effective_risk_pct()
        risk_amount = equity * risk_pct

        # Hard ceiling check
        if risk_amount > equity * self.rc.max_risk_pct:
            risk_amount = equity * self.rc.max_risk_pct

        stop_distance = abs(signal.entry_price - signal.stop_price)
        if stop_distance <= 0:
            return self._reject("STOP_REQUIRED_BEFORE_SIGNAL: stop_distance is zero")

        stop_distance_pct = stop_distance / signal.entry_price
        position_size = risk_amount / (signal.entry_price * stop_distance_pct)

        # Gate 4: Max dollar risk (RISK_PCT_HARD_CEILING)
        if self.rc.max_dollar_risk and risk_amount > self.rc.max_dollar_risk:
            return self._reject(
                f"RISK_PCT_HARD_CEILING: risk_amount ${risk_amount:.2f} exceeds "
                f"max_dollar_risk ${self.rc.max_dollar_risk:.2f}"
            )

        # Gate 5: Daily loss limit
        if self._daily_loss_pct() >= self.rc.daily_loss_limit_pct:
            self._engage_daily_circuit_breaker()
            return self._reject(
                f"DAILY_CIRCUIT_BREAKER: daily loss {self._daily_loss_pct():.1%} >= "
                f"limit {self.rc.daily_loss_limit_pct:.1%}"
            )

        # Gate 6: Weekly loss limit
        if self._weekly_loss_pct() >= self.rc.weekly_loss_limit_pct:
            self._engage_weekly_circuit_breaker()
            return self._reject(
                f"WEEKLY_CIRCUIT_BREAKER: weekly loss {self._weekly_loss_pct():.1%} >= "
                f"limit {self.rc.weekly_loss_limit_pct:.1%}"
            )

        # Gate 7: Open positions limit
        if self.state.open_positions >= self.rc.max_open_positions:
            return self._reject(
                f"MAX_POSITIONS: {self.state.open_positions} open >= limit {self.rc.max_open_positions}"
            )

        # Gate 8: Minimum R:R (from signal targets)
        rr = self._compute_rr(signal)
        if rr < self.rc.min_risk_reward:
            return self._reject(
                f"MIN_RR_REQUIREMENT: R:R {rr:.1f} < minimum {self.rc.min_risk_reward}"
            )

        logger.info(
            "Risk approved: size=%.4f risk_amount=%.2f (%.1f%%) R:R=%.1f",
            position_size, risk_amount, risk_pct * 100, rr,
        )

        return RiskAssessment(
            signal_approved=True,
            position_size=position_size,
            risk_amount=risk_amount,
            risk_pct=risk_pct,
            stop_distance_pct=stop_distance_pct,
            risk_reward_ratio=rr,
            account_equity=equity,
            agent_state=self.state.state,
            max_position_allowed=position_size,
            daily_loss_used_pct=self._daily_loss_pct(),
            weekly_loss_used_pct=self._weekly_loss_pct(),
            consecutive_losses=self.state.consecutive_losses,
        )

    def record_trade_result(self, pnl: float, risk_amount: float) -> None:
        """
        Called after a trade closes. Updates state for circuit breakers.
        pnl: realized P&L in account currency (negative = loss)
        """
        self.state.account_equity += pnl
        self.state.daily_realized_pnl += pnl

        if pnl < 0:
            loss = abs(pnl)
            self.state.daily_realized_loss += loss
            self.state.weekly_realized_loss += loss
            self.state.consecutive_losses += 1
            logger.warning(
                "Trade loss recorded: pnl=%.2f consecutive_losses=%d",
                pnl, self.state.consecutive_losses,
            )
            # Check consecutive loss cooldown
            if self.state.consecutive_losses >= self.rc.consecutive_loss_limit:
                self._engage_cooldown()
        else:
            # Reset consecutive loss counter on a win
            if self.state.consecutive_losses > 0:
                logger.info("Win resets consecutive loss counter (%d → 0)", self.state.consecutive_losses)
            self.state.consecutive_losses = 0

        if pnl > 0:
            self.state.open_positions = max(0, self.state.open_positions - 1)
        self._save_state()

    def record_trade_open(self) -> None:
        self.state.open_positions += 1
        self._save_state()

    def record_trade_close(self) -> None:
        self.state.open_positions = max(0, self.state.open_positions - 1)
        self._save_state()

    def update_equity(self, new_equity: float) -> None:
        self.state.account_equity = new_equity
        self._save_state()

    # ──────────────────────────────────────────────
    # Internal
    # ──────────────────────────────────────────────

    def _effective_risk_pct(self) -> float:
        """Returns reduced risk during recalibration period."""
        if self.state.recalibration_sessions_remaining > 0:
            pct = self.rc.max_risk_pct * self.rc.recalibration_risk_multiplier
            logger.info(
                "RECALIBRATION: reduced risk %.1f%% (%d sessions remaining)",
                pct * 100, self.state.recalibration_sessions_remaining,
            )
            return pct
        return self.rc.max_risk_pct

    def _check_agent_state(self) -> Optional[str]:
        now = time.time()

        # Cooldown (consecutive losses)
        if self.state.cooldown_until > now:
            remaining = int(self.state.cooldown_until - now) // 60
            return (
                f"COOLDOWN_AFTER_CONSECUTIVE_LOSSES: cooldown active for {remaining} more minutes"
            )

        # Daily circuit breaker
        if self.state.circuit_breaker_daily_until > now:
            return f"CIRCUIT_BREAKER_DAILY: halted until daily reset"

        # Weekly circuit breaker
        if self.state.circuit_breaker_weekly_until > now:
            return f"CIRCUIT_BREAKER_WEEKLY: halted until weekly reset"

        # Reset stale state
        if self.state.cooldown_until <= now and self.state.state == AgentState.COOLDOWN:
            self.state.state = AgentState.ACTIVE
            self._save_state()
            logger.info("Cooldown expired — agent state = ACTIVE")

        return None

    def _engage_cooldown(self) -> None:
        cooldown_secs = self.rc.cooldown_hours * 3600
        self.state.cooldown_until = time.time() + cooldown_secs
        self.state.state = AgentState.COOLDOWN
        self._save_state()
        logger.warning(
            "COOLDOWN engaged: %d consecutive losses → %dh halt",
            self.state.consecutive_losses, self.rc.cooldown_hours,
        )

    def _engage_daily_circuit_breaker(self) -> None:
        # Halt until midnight UTC
        import datetime
        tomorrow = datetime.datetime.utcnow().replace(
            hour=0, minute=0, second=0, microsecond=0
        ) + datetime.timedelta(days=1)
        self.state.circuit_breaker_daily_until = tomorrow.timestamp()
        self.state.state = AgentState.CIRCUIT_BREAKER_DAILY
        self._save_state()
        logger.critical("DAILY_CIRCUIT_BREAKER engaged — trading halted for today")

    def _engage_weekly_circuit_breaker(self) -> None:
        import datetime
        # Halt until next Monday
        now = datetime.datetime.utcnow()
        days_until_monday = (7 - now.weekday()) % 7 or 7
        next_monday = (now + datetime.timedelta(days=days_until_monday)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        self.state.circuit_breaker_weekly_until = next_monday.timestamp()
        self.state.state = AgentState.CIRCUIT_BREAKER_WEEKLY
        self.state.recalibration_sessions_remaining = self.rc.recalibration_sessions
        self._save_state()
        logger.critical("WEEKLY_CIRCUIT_BREAKER engaged — trading halted for rest of week")

    def _reset_daily_weekly_if_needed(self) -> None:
        import datetime
        now = datetime.datetime.utcnow()
        today = now.strftime("%Y-%m-%d")
        week = now.strftime("%Y-%W")

        if self.state.last_reset_date != today:
            self.state.daily_realized_loss = 0.0
            self.state.daily_realized_pnl = 0.0
            self.state.last_reset_date = today
            # Reset daily circuit breaker at day change
            if self.state.state == AgentState.CIRCUIT_BREAKER_DAILY:
                self.state.state = AgentState.ACTIVE
                self.state.circuit_breaker_daily_until = 0.0
            if self.state.recalibration_sessions_remaining > 0:
                self.state.recalibration_sessions_remaining -= 1
            self._save_state()
            logger.info("Daily risk counters reset for %s", today)

        if self.state.last_reset_week != week:
            self.state.weekly_realized_loss = 0.0
            self.state.last_reset_week = week
            if self.state.state == AgentState.CIRCUIT_BREAKER_WEEKLY:
                self.state.state = AgentState.ACTIVE
                self.state.circuit_breaker_weekly_until = 0.0
            self._save_state()
            logger.info("Weekly risk counters reset for week %s", week)

    def _daily_loss_pct(self) -> float:
        if self.state.account_equity <= 0:
            return 0.0
        return self.state.daily_realized_loss / self.state.account_equity

    def _weekly_loss_pct(self) -> float:
        if self.state.account_equity <= 0:
            return 0.0
        return self.state.weekly_realized_loss / self.state.account_equity

    def _compute_rr(self, signal: ScoredSignal) -> float:
        if not signal.targets or not signal.entry_price or not signal.stop_price:
            return 0.0
        risk = abs(signal.entry_price - signal.stop_price)
        reward = abs(signal.targets[0] - signal.entry_price)
        return reward / risk if risk > 0 else 0.0

    def _reject(self, reason: str) -> RiskAssessment:
        logger.warning("Risk rejection: %s", reason)
        return RiskAssessment(
            signal_approved=False,
            rejection_reason=reason,
            agent_state=self.state.state,
            account_equity=self.state.account_equity,
            daily_loss_used_pct=self._daily_loss_pct(),
            weekly_loss_used_pct=self._weekly_loss_pct(),
            consecutive_losses=self.state.consecutive_losses,
        )

    def _load_state(self) -> RiskState:
        path = Path(self.STATE_FILE)
        if path.exists():
            try:
                data = json.loads(path.read_text())
                state = RiskState.from_dict(data)
                state.account_equity = self.rc.account_equity  # Always use config equity
                return state
            except Exception as e:
                logger.warning("Failed to load risk state: %s — using defaults", e)
        state = RiskState(account_equity=self.rc.account_equity)
        return state

    def _save_state(self) -> None:
        path = Path(self.STATE_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.state.to_dict(), indent=2))
