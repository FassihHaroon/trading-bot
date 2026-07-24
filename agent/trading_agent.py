"""
Orchestration Agent — main analysis loop.
Pulls MarketSnapshot → runs full pipeline → emits TradeSignal → executes (paper/live).
Integrates self-learning: after each trade close the TradeAnalyzer re-computes weights
and the SignalScorer reloads them for the next cycle.
"""

from __future__ import annotations

import logging
import signal as os_signal
import time
from typing import Optional

from data.schemas import TradeSignal, Direction
from data.connectors.market_data_service import MarketDataService
from engine.decision_engine import DecisionEngine
from execution.paper_trader import PaperTrader
from journal.trade_journal import TradeJournal
from risk.risk_manager import RiskManager
from ai.explainer import TradeExplainer
from analytics.trade_analyzer import TradeAnalyzer
from config.settings import AgentConfig

logger = logging.getLogger(__name__)

# Run the self-learner every N closed trades (not every cycle — expensive)
_LEARN_EVERY_N_TRADES = 5


class TradingAgent:
    """
    Top-level orchestrator. One run_once() call = one analysis cycle.
    Designed to be called on a scheduler (every N seconds) or in a loop.
    """

    def __init__(self, config: AgentConfig):
        self.config = config
        self.symbol = config.symbol

        # Validate live trading guard
        if config.execution.live_trading and not self._live_trading_cleared():
            raise RuntimeError(
                "LIVE_TRADING_GATE: cannot enable live trading — "
                "backtest validation not passed or explicit flag not set"
            )

        mode = "LIVE" if config.execution.live_trading else "PAPER"
        logger.info("TradingAgent initialised: symbol=%s mode=%s", self.symbol, mode)

        self.data_service   = MarketDataService(config)
        self.risk_manager   = RiskManager(config)
        self.decision_engine = DecisionEngine(config, risk_manager=self.risk_manager)
        self.paper_trader   = PaperTrader(config)
        self.journal        = TradeJournal(config)
        self.explainer      = TradeExplainer(config)
        self.analyzer       = TradeAnalyzer()

        # Live executor — only instantiated when live trading is enabled
        self._live_executor = None
        if config.execution.live_trading:
            from execution.live_executor import LiveFuturesExecutor
            self._live_executor = LiveFuturesExecutor(config)
            logger.info("LiveFuturesExecutor ready → https://fapi.binance.com")

        self._running = False
        self._cycle_count = 0
        self._closed_trade_count = 0  # Tracks when to trigger self-learning

        # Run self-learning once at startup if state file already exists
        self._try_learn()

    def run_once(self) -> TradeSignal:
        """
        Execute one full analysis cycle.
        Returns the TradeSignal (including NO_TRADE decisions).
        """
        self._cycle_count += 1
        logger.info("--- Cycle #%d ---", self._cycle_count)

        # 1. Fetch market data
        try:
            snapshot = self.data_service.get_snapshot(self.symbol)
        except Exception as exc:
            logger.error("Data fetch failed: %s", exc, exc_info=True)
            return self._null_signal()

        if not snapshot.is_complete:
            logger.warning("Incomplete snapshot: %s", snapshot.fetch_errors)

        # 2. Run decision pipeline
        try:
            signal = self.decision_engine.run(snapshot)
        except Exception as exc:
            logger.error("Decision engine failed: %s", exc, exc_info=True)
            return self._null_signal()

        # 3. Journal the decision (always — including NO_TRADE)
        self.journal.log_decision(signal)

        # 4. Check for exits on open positions
        current_price = self._get_current_price(snapshot)
        if current_price:
            exit_result = self._check_exits(current_price)
            if exit_result:
                closed_entry, exit_reason = exit_result
                self.journal.log_exit(closed_entry)
                self.risk_manager.record_trade_result(
                    pnl=closed_entry.realized_pnl or 0.0,
                    risk_amount=closed_entry.risk_amount,
                )
                logger.info(
                    "Position closed: %s reason=%s pnl=%.2f",
                    self.symbol, exit_reason, closed_entry.realized_pnl or 0,
                )
                # ── Self-learning trigger ────────────────────────────────
                self._closed_trade_count += 1
                if self._closed_trade_count % _LEARN_EVERY_N_TRADES == 0:
                    self._try_learn()

        # 5. Execute if actionable
        if signal.is_actionable():
            journal_entry = self._execute(signal)
            if journal_entry:
                self.journal.log_entry(journal_entry)
                self.risk_manager.record_trade_open()

            # 6. AI explanation
            explanation = self.explainer.explain_trade(signal)
            if explanation:
                logger.info("AI explanation:\n%s", explanation)

        else:
            if self.config.ai.explain_no_trade:
                explanation = self.explainer.explain_no_trade(signal)
                if explanation:
                    logger.info("NO_TRADE explanation:\n%s", explanation)

        logger.info("Cycle #%d complete: %s", self._cycle_count, signal.to_summary())
        return signal

    def run_loop(self) -> None:
        """
        Continuous analysis loop with configurable interval.
        Handles SIGINT gracefully.
        """
        self._running = True

        def _shutdown(sig, frame):
            logger.info("Shutdown signal received — stopping after current cycle")
            self._running = False

        os_signal.signal(os_signal.SIGINT, _shutdown)
        os_signal.signal(os_signal.SIGTERM, _shutdown)

        logger.info("Warming up data cache for %s...", self.symbol)
        try:
            self.data_service.warmup(self.symbol)
        except Exception as exc:
            logger.warning("Warmup error (continuing): %s", exc)

        interval = self.config.cycle_interval_seconds
        logger.info("Starting analysis loop: interval=%ds", interval)

        while self._running:
            cycle_start = time.monotonic()
            try:
                self.run_once()
            except Exception as exc:
                logger.error("Unhandled cycle error: %s", exc, exc_info=True)

            elapsed = time.monotonic() - cycle_start
            sleep_time = max(0.0, interval - elapsed)
            if sleep_time > 0 and self._running:
                time.sleep(sleep_time)

        logger.info("Agent stopped after %d cycles", self._cycle_count)

    def get_stats(self) -> dict:
        """Return current performance statistics."""
        return {
            "cycle_count": self._cycle_count,
            "symbol": self.symbol,
            "mode": "live" if self.config.execution.live_trading else "paper",
            "agent_state": self.risk_manager.state.state.value,
            "equity": self.risk_manager.state.account_equity,
            "consecutive_losses": self.risk_manager.state.consecutive_losses,
            "daily_loss_pct": self.risk_manager._daily_loss_pct(),
            "journal_stats": self.journal.get_stats(),
            "learned_state_exists": (
                self.analyzer.state_file.exists()
            ),
        }

    # ──────────────────────────────────────────────
    # Execution routing
    # ──────────────────────────────────────────────

    def _execute(self, signal: TradeSignal):
        """Route signal to live executor or paper trader."""
        if self._live_executor and self.config.execution.live_trading:
            logger.info("Routing to LiveFuturesExecutor → REAL ORDER")
            return self._live_executor.execute(signal)
        return self.paper_trader.execute(signal)

    def _check_exits(self, current_price: float):
        """Check exits — paper mode only (live exits are handled by SL/TP orders on exchange)."""
        if self._live_executor and self.config.execution.live_trading:
            # In live mode, the exchange's stop/TP orders close positions automatically.
            # We only poll the position to detect if it was closed.
            pos = self._live_executor.get_position(self.symbol)
            if pos is None:
                # Position was closed (SL or TP hit on exchange)
                # Try to find the open entry in the journal
                open_entries = [
                    e for e in self.journal._entries
                    if e.outcome == "open" and e.symbol == self.symbol
                ]
                if open_entries:
                    entry = open_entries[-1]
                    entry.exit_price = current_price
                    entry.outcome = "win" if current_price > (entry.entry_price or 0) else "loss"
                    pnl_per_unit = (
                        (current_price - (entry.entry_price or 0))
                        if entry.direction == Direction.LONG
                        else ((entry.entry_price or 0) - current_price)
                    )
                    entry.realized_pnl = pnl_per_unit * entry.position_size
                    comm = entry.position_size * current_price * 0.0004
                    entry.realized_pnl -= comm * 2
                    risk = entry.risk_amount or 1.0
                    entry.realized_r = entry.realized_pnl / risk if risk != 0 else 0.0
                    entry.exit_reason = "exchange_sl_tp"
                    return entry, "exchange_sl_tp"
            return None
        return self.paper_trader.check_exits(self.symbol, current_price)

    # ──────────────────────────────────────────────
    # Self-learning
    # ──────────────────────────────────────────────

    def _try_learn(self) -> None:
        """Run the trade analyzer and reload weights in the scoring engine."""
        try:
            logger.info("Self-learning: running trade analyzer...")
            report = self.analyzer.run()

            # Tell the SignalScorer to reload weights from disk
            scorer = getattr(self.decision_engine, "signal_scorer", None)
            if scorer and hasattr(scorer, "reload_weights"):
                scorer.reload_weights()
                logger.info("Self-learning: SignalScorer weights reloaded")

        except Exception as exc:
            logger.warning("Self-learning failed (non-fatal): %s", exc)

    # ──────────────────────────────────────────────
    # Misc helpers
    # ──────────────────────────────────────────────

    def _live_trading_cleared(self) -> bool:
        if not self.config.execution.backtest_required_before_live:
            return True
        from pathlib import Path
        import json
        result_file = Path("logs/backtest/validation_result.json")
        if not result_file.exists():
            logger.error("LIVE_TRADING_GATE: no backtest validation result found")
            return False
        try:
            data = json.loads(result_file.read_text())
            return data.get("validation_passed", False)
        except Exception:
            return False

    def _get_current_price(self, snapshot) -> Optional[float]:
        tf = self.config.data.timeframes[0]
        candles = snapshot.candles.get(tf, [])
        if candles:
            return candles[-1].close
        return None

    def _null_signal(self) -> TradeSignal:
        return TradeSignal(
            symbol=self.symbol,
            timestamp=int(time.time() * 1000),
            direction=Direction.NO_TRADE,
            no_trade_reason="Agent error — see logs",
            is_paper_trade=not self.config.execution.live_trading,
        )
