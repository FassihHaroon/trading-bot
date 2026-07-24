"""
Paper Trading Executor — simulates fills without real money.
Default mode; live trading requires explicit flag.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from data.schemas import TradeSignal, JournalEntry, Direction
from config.settings import AgentConfig

logger = logging.getLogger(__name__)


class PaperTrader:
    """
    Simulates order execution for paper trading.
    Applies configurable slippage and commission.
    """

    def __init__(self, config: AgentConfig):
        self.config = config
        self.ec = config.execution
        self.open_positions: dict[str, JournalEntry] = {}  # symbol → JournalEntry

    def execute(self, signal: TradeSignal) -> Optional[JournalEntry]:
        """
        Simulate entering a trade. Returns a JournalEntry for the journal.
        """
        if not signal.is_actionable():
            logger.debug("Signal not actionable — skipping paper execution")
            return None

        if signal.direction == Direction.NO_TRADE:
            return None

        # Simulate slippage
        slippage_factor = 1 + self.ec.slippage_pct if signal.direction == Direction.LONG \
                          else 1 - self.ec.slippage_pct
        exec_price = signal.entry_price * slippage_factor

        # Commission
        commission = exec_price * signal.position_size * self.ec.commission_pct
        logger.info(
            "[PAPER] %s %s: exec_price=%.4f size=%.4f commission=%.4f",
            signal.direction.value.upper(), signal.symbol,
            exec_price, signal.position_size, commission,
        )

        entry_id = f"PAPER_{signal.symbol}_{int(time.time())}"
        entry = JournalEntry(
            entry_id=entry_id,
            symbol=signal.symbol,
            timestamp_open=signal.timestamp,
            direction=signal.direction,
            entry_price=exec_price,
            stop_price=signal.stop_price,
            targets=signal.targets,
            position_size=signal.position_size,
            risk_amount=signal.risk_amount,
            confidence=signal.confidence,
            strategies_used=[
                r.strategy_id for r in signal.strategy_results if r.is_valid
            ],
            market_regime=signal.market_context.regime.value if signal.market_context else None,
            market_phase=signal.market_context.phase.value if signal.market_context else None,
            reasoning_trace=self._build_trace(signal),
            knowledge_refs=signal.knowledge_refs,
            rules_fired=signal.rules_fired,
            is_paper_trade=True,
            outcome="open",
        )

        self.open_positions[signal.symbol] = entry
        return entry

    def check_exits(
        self, symbol: str, current_price: float
    ) -> Optional[tuple[JournalEntry, str]]:
        """
        Check if any open position should be closed.
        Returns (updated_entry, exit_reason) or None.
        """
        entry = self.open_positions.get(symbol)
        if not entry:
            return None

        exit_reason = None
        exit_price = current_price

        if entry.direction == Direction.LONG:
            # Stop hit
            if current_price <= entry.stop_price:
                exit_reason = "stop"
                exit_price = entry.stop_price * (1 - self.ec.slippage_pct)
            # Targets
            elif entry.targets:
                if current_price >= entry.targets[0]:
                    exit_reason = "target_1"
                if len(entry.targets) > 1 and current_price >= entry.targets[1]:
                    exit_reason = "target_2"

        elif entry.direction == Direction.SHORT:
            if current_price >= entry.stop_price:
                exit_reason = "stop"
                exit_price = entry.stop_price * (1 + self.ec.slippage_pct)
            elif entry.targets:
                if current_price <= entry.targets[0]:
                    exit_reason = "target_1"
                if len(entry.targets) > 1 and current_price <= entry.targets[1]:
                    exit_reason = "target_2"

        if exit_reason:
            return self._close_position(symbol, exit_price, exit_reason)

        return None

    def _close_position(
        self, symbol: str, exit_price: float, exit_reason: str
    ) -> tuple[JournalEntry, str]:
        entry = self.open_positions.pop(symbol)
        entry.exit_price = exit_price
        entry.timestamp_close = int(time.time() * 1000)
        entry.exit_reason = exit_reason

        if entry.direction == Direction.LONG:
            pnl = (exit_price - entry.entry_price) * entry.position_size
        else:
            pnl = (entry.entry_price - exit_price) * entry.position_size

        # Subtract commission on exit
        commission = exit_price * entry.position_size * self.ec.commission_pct
        pnl -= commission

        entry.realized_pnl = pnl
        entry.realized_r = pnl / entry.risk_amount if entry.risk_amount > 0 else 0.0
        entry.outcome = "win" if pnl > 0 else "loss" if pnl < 0 else "breakeven"

        logger.info(
            "[PAPER] CLOSED %s %s: exit=%.4f pnl=%.2f R=%.2f outcome=%s",
            entry.direction.value.upper(), symbol,
            exit_price, pnl, entry.realized_r, entry.outcome,
        )
        return entry, exit_reason

    def _build_trace(self, signal: TradeSignal) -> list[str]:
        trace = [f"Direction: {signal.direction.value.upper()}"]
        if signal.market_context:
            trace.append(f"Regime: {signal.market_context.regime.value}")
        trace.append(f"Confidence: {signal.confidence:.0%} (probabilistic estimate)")
        for r in signal.strategy_results:
            if r.is_valid:
                trace.append(f"Strategy: {r.strategy_name}")
                trace.extend(r.supporting_evidence[:2])
        return trace
