"""
Backtesting Engine — replays historical MarketSnapshots through the full pipeline.
No lookahead bias: only data available at time T is used to decide at time T.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import Optional

from data.schemas import (
    MarketSnapshot, TradeSignal, Direction,
    BacktestTrade, BacktestResult,
)
from data.connectors.market_data_service import MarketDataService
from engine.decision_engine import DecisionEngine
from config.settings import AgentConfig

logger = logging.getLogger(__name__)


class BacktestEngine:
    """
    Walks through historical data bar-by-bar. Builds MarketSnapshots using
    only past data (lookahead-free), passes each through DecisionEngine,
    simulates fills and exits, collects BacktestTrades.
    """

    def __init__(self, config: AgentConfig):
        self.config = config
        self.decision_engine = DecisionEngine(config)
        self.data_service = MarketDataService(config)
        self.ec = config.execution
        self.rc = config.risk

    # Additional timeframes fetched for MTF analysis — we need them alongside 1h
    _EXTRA_TIMEFRAMES: tuple[str, ...] = ("4h", "1d")

    def run(
        self,
        symbol: str,
        start_timestamp: int,    # Unix ms
        end_timestamp: int,
        primary_timeframe: str = "1h",
        warmup_bars: int = 200,
    ) -> BacktestResult:
        """
        Run backtest from start to end. Returns BacktestResult with full metrics.
        """
        logger.info(
            "Backtest: %s %s → %s on %s",
            symbol, _ts_to_str(start_timestamp), _ts_to_str(end_timestamp), primary_timeframe,
        )

        fetch_start = start_timestamp - _warmup_ms(primary_timeframe, warmup_bars)

        # Fetch primary timeframe candles
        all_candles = self.data_service.get_historical_candles(
            symbol, primary_timeframe, fetch_start, end_timestamp,
        )

        if len(all_candles) < warmup_bars + 10:
            raise ValueError(f"Insufficient candle data: {len(all_candles)} bars")

        # Fetch additional timeframes for MTF analysis (4h, 1d).
        # Each TF uses its own 100-bar warmup (independent of the primary TF warmup)
        # so the MTF extractor has enough data from the very first bar.
        extra_candles: dict[str, list] = {}
        for tf in self._EXTRA_TIMEFRAMES:
            if tf == primary_timeframe:
                continue
            try:
                tf_start = start_timestamp - _warmup_ms(tf, 100)
                candles = self.data_service.get_historical_candles(
                    symbol, tf, tf_start, end_timestamp,
                )
                if candles:
                    extra_candles[tf] = candles
                    logger.info("Fetched %d candles for %s %s", len(candles), symbol, tf)
            except Exception as exc:
                logger.warning("Could not fetch %s candles for backtest: %s", tf, exc)

        trades: list[BacktestTrade] = []
        equity = self.rc.account_equity
        open_trade: Optional[_OpenTrade] = None
        consecutive_losses = 0
        daily_loss = 0.0
        current_day = ""

        # Walk forward bar by bar (starting after warmup)
        for i in range(warmup_bars, len(all_candles)):
            candle = all_candles[i]
            candle_day = _ts_to_str(candle.timestamp)[:10]

            # Daily reset
            if candle_day != current_day:
                current_day = candle_day
                daily_loss = 0.0

            # Check exit for open trade
            if open_trade:
                exit_result = self._check_exit(open_trade, candle)
                if exit_result:
                    bt_trade, pnl = exit_result
                    trades.append(bt_trade)
                    equity += pnl
                    if pnl < 0:
                        daily_loss += abs(pnl)
                        consecutive_losses += 1
                    else:
                        consecutive_losses = 0
                    open_trade = None

            # Skip if already in a trade (no pyramiding in backtest)
            if open_trade:
                continue

            # Skip circuit breakers
            if daily_loss / equity >= self.rc.daily_loss_limit_pct:
                continue
            if consecutive_losses >= self.rc.consecutive_loss_limit:
                # Simple cooldown: skip 24 hours of bars
                consecutive_losses = 0
                continue

            # Build snapshot from available history (no lookahead)
            snapshot = self._build_snapshot_at(symbol, all_candles, i, primary_timeframe, extra_candles)

            # Run decision engine
            try:
                signal: TradeSignal = self.decision_engine.run(snapshot)
            except Exception as exc:
                logger.warning("Decision engine error at bar %d: %s", i, exc)
                continue

            if not signal.is_actionable():
                continue

            # Simulate entry at next bar open (avoid lookahead)
            if i + 1 >= len(all_candles):
                break

            next_candle = all_candles[i + 1]
            fill_price = next_candle.open * (
                1 + self.ec.slippage_pct
                if signal.direction == Direction.LONG
                else 1 - self.ec.slippage_pct
            )
            commission = fill_price * signal.position_size * self.ec.commission_pct

            # Position sizing from equity
            stop_distance = abs(fill_price - signal.stop_price)
            risk_amount = equity * self.rc.max_risk_pct
            position_size = risk_amount / stop_distance if stop_distance > 0 else 0

            if position_size <= 0:
                continue

            open_trade = _OpenTrade(
                entry_timestamp=next_candle.timestamp,
                symbol=symbol,
                direction=signal.direction,
                entry_price=fill_price,
                stop_price=signal.stop_price,
                targets=signal.targets,
                position_size=position_size,
                risk_amount=risk_amount,
                commission_paid=commission,
                strategies_used=[r.strategy_id for r in signal.strategy_results if r.is_valid],
                market_regime=signal.market_context.regime.value if signal.market_context else "unknown",
                confidence=signal.confidence,
            )

        # Close any remaining open trade at last bar
        if open_trade and all_candles:
            last = all_candles[-1]
            pnl = self._compute_pnl(open_trade, last.close)
            bt_trade = BacktestTrade(
                entry_timestamp=open_trade.entry_timestamp,
                exit_timestamp=last.timestamp,
                symbol=symbol,
                direction=open_trade.direction,
                entry_price=open_trade.entry_price,
                exit_price=last.close,
                stop_price=open_trade.stop_price,
                position_size=open_trade.position_size,
                risk_amount=open_trade.risk_amount,
                pnl=pnl,
                r_multiple=pnl / open_trade.risk_amount if open_trade.risk_amount else 0,
                strategies_used=open_trade.strategies_used,
                market_regime=open_trade.market_regime,
                confidence=open_trade.confidence,
                exit_reason="end_of_data",
            )
            trades.append(bt_trade)

        result = self._compute_metrics(
            symbol=symbol,
            timeframe=primary_timeframe,
            start_ts=start_timestamp,
            end_ts=end_timestamp,
            trades=trades,
            initial_equity=self.rc.account_equity,
        )
        self._log_result(result)
        return result

    # ──────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────

    def _build_snapshot_at(
        self, symbol: str, candles: list, bar_index: int, timeframe: str,
        extra_candles: dict | None = None,
    ) -> MarketSnapshot:
        """Build a snapshot using only candles available at bar_index (no lookahead)."""
        current_ts = candles[bar_index].timestamp
        window = candles[max(0, bar_index - 300):bar_index + 1]
        all_tf: dict[str, list] = {timeframe: window}

        # Include additional timeframe candles that precede current_ts
        if extra_candles:
            for tf, tf_candles in extra_candles.items():
                # Binary search: find all candles with timestamp <= current_ts
                lo, hi = 0, len(tf_candles)
                while lo < hi:
                    mid = (lo + hi) // 2
                    if tf_candles[mid].timestamp <= current_ts:
                        lo = mid + 1
                    else:
                        hi = mid
                # lo is now the first index > current_ts; take up to 200 bars before it
                available = tf_candles[max(0, lo - 200):lo]
                if available:
                    all_tf[tf] = available

        return MarketSnapshot(
            symbol=symbol,
            timestamp=current_ts,
            candles=all_tf,
            is_complete=False,  # No order book in backtest
        )

    def _check_exit(
        self, trade: "_OpenTrade", candle
    ) -> Optional[tuple[BacktestTrade, float]]:
        """Check if candle triggers stop or target."""
        exit_price = None
        exit_reason = None

        if trade.direction == Direction.LONG:
            if candle.low <= trade.stop_price:
                exit_price = min(candle.open, trade.stop_price)
                exit_reason = "stop"
            elif trade.targets and candle.high >= trade.targets[0]:
                exit_price = trade.targets[0]
                exit_reason = "target_1"
        else:
            if candle.high >= trade.stop_price:
                exit_price = max(candle.open, trade.stop_price)
                exit_reason = "stop"
            elif trade.targets and candle.low <= trade.targets[0]:
                exit_price = trade.targets[0]
                exit_reason = "target_1"

        if exit_price is None:
            return None

        pnl = self._compute_pnl(trade, exit_price)
        bt_trade = BacktestTrade(
            entry_timestamp=trade.entry_timestamp,
            exit_timestamp=candle.timestamp,
            symbol=trade.symbol,
            direction=trade.direction,
            entry_price=trade.entry_price,
            exit_price=exit_price,
            stop_price=trade.stop_price,
            position_size=trade.position_size,
            risk_amount=trade.risk_amount,
            pnl=pnl,
            r_multiple=pnl / trade.risk_amount if trade.risk_amount else 0,
            strategies_used=trade.strategies_used,
            market_regime=trade.market_regime,
            confidence=trade.confidence,
            exit_reason=exit_reason,
        )
        return bt_trade, pnl

    def _compute_pnl(self, trade: "_OpenTrade", exit_price: float) -> float:
        if trade.direction == Direction.LONG:
            return (exit_price - trade.entry_price) * trade.position_size - trade.commission_paid
        return (trade.entry_price - exit_price) * trade.position_size - trade.commission_paid

    def _compute_metrics(
        self, symbol: str, timeframe: str, start_ts: int, end_ts: int,
        trades: list[BacktestTrade], initial_equity: float,
    ) -> BacktestResult:
        total = len(trades)
        if total == 0:
            return BacktestResult(symbol=symbol, timeframe=timeframe,
                                  start_timestamp=start_ts, end_timestamp=end_ts)

        wins = [t for t in trades if t.pnl > 0]
        losses = [t for t in trades if t.pnl <= 0]
        gross_win = sum(t.pnl for t in wins)
        gross_loss = abs(sum(t.pnl for t in losses))

        # Equity curve
        equity = initial_equity
        equity_curve = [equity]
        peak = equity
        max_dd = 0.0
        monthly: dict[str, float] = {}

        for t in trades:
            equity += t.pnl
            equity_curve.append(equity)
            peak = max(peak, equity)
            dd = (peak - equity) / peak
            max_dd = max(max_dd, dd)
            month = _ts_to_str(t.entry_timestamp)[:7]
            monthly[month] = monthly.get(month, 0) + t.pnl

        returns = [equity_curve[i] / equity_curve[i-1] - 1 for i in range(1, len(equity_curve))]
        sharpe = _sharpe(returns)
        sortino = _sortino(returns)
        expectancy = sum(t.r_multiple for t in trades) / total

        # Strategy breakdown
        strat_stats: dict[str, dict] = {}
        for t in trades:
            for s in t.strategies_used:
                if s not in strat_stats:
                    strat_stats[s] = {"trades": 0, "wins": 0, "pnl": 0.0}
                strat_stats[s]["trades"] += 1
                if t.pnl > 0:
                    strat_stats[s]["wins"] += 1
                strat_stats[s]["pnl"] += t.pnl

        validation = (
            total >= self.ec.backtest_min_trades
            and len(wins) / total >= self.ec.backtest_min_win_rate
            and expectancy >= self.ec.backtest_min_expectancy
        )

        return BacktestResult(
            symbol=symbol, timeframe=timeframe,
            start_timestamp=start_ts, end_timestamp=end_ts,
            total_trades=total,
            winning_trades=len(wins),
            losing_trades=len(losses),
            win_rate=len(wins) / total,
            profit_factor=gross_win / gross_loss if gross_loss > 0 else float("inf"),
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            max_drawdown_pct=max_dd,
            avg_r_multiple=sum(t.r_multiple for t in trades) / total,
            expectancy=expectancy,
            total_return_pct=(equity - initial_equity) / initial_equity,
            trades=trades,
            strategy_breakdown=strat_stats,
            monthly_returns=list(monthly.values()),
            validation_passed=validation,
        )

    def _log_result(self, r: BacktestResult) -> None:
        logger.info(
            "BACKTEST RESULT %s:\n"
            "  Trades: %d  Win Rate: %.1f%%  Profit Factor: %.2f\n"
            "  Sharpe: %.2f  Sortino: %.2f  Max Drawdown: %.1f%%\n"
            "  Expectancy: %.2fR  Total Return: %.1f%%\n"
            "  Validation: %s",
            r.symbol, r.total_trades, r.win_rate * 100, r.profit_factor,
            r.sharpe_ratio, r.sortino_ratio, r.max_drawdown_pct * 100,
            r.expectancy, r.total_return_pct * 100,
            "PASSED" if r.validation_passed else "FAILED",
        )
        # Log individual trades for debugging
        if r.trades:
            logger.info("Trade log (entry_date | direction | entry | exit | R | reason | strategies):")
            for t in r.trades:
                logger.info(
                    "  %s | %s | %.2f -> %.2f | %+.2fR | %s | %s",
                    _ts_to_str(t.entry_timestamp), t.direction.value,
                    t.entry_price, t.exit_price, t.r_multiple,
                    t.exit_reason, ",".join(t.strategies_used),
                )
        # Log strategy breakdown
        if r.strategy_breakdown:
            logger.info("Strategy breakdown:")
            for sid, stats in sorted(r.strategy_breakdown.items()):
                wr = stats["wins"] / stats["trades"] if stats["trades"] else 0
                logger.info("  %s: trades=%d win_rate=%.0f%% pnl=%.2f",
                            sid, stats["trades"], wr * 100, stats["pnl"])


@dataclass
class _OpenTrade:
    entry_timestamp: int
    symbol: str
    direction: Direction
    entry_price: float
    stop_price: float
    targets: list[float]
    position_size: float
    risk_amount: float
    commission_paid: float
    strategies_used: list[str]
    market_regime: str
    confidence: float


def _ts_to_str(ts_ms: int) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _warmup_ms(timeframe: str, bars: int) -> int:
    mult = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
    return mult.get(timeframe, 3600) * bars * 1000


def _sharpe(returns: list[float], rf: float = 0.0) -> float:
    if len(returns) < 2:
        return 0.0
    n = len(returns)
    mean = sum(returns) / n - rf
    var = sum((r - mean) ** 2 for r in returns) / (n - 1)
    std = math.sqrt(var)
    return mean / std * math.sqrt(252) if std > 0 else 0.0


def _sortino(returns: list[float], rf: float = 0.0) -> float:
    if len(returns) < 2:
        return 0.0
    n = len(returns)
    mean = sum(returns) / n - rf
    downside = [r for r in returns if r < 0]
    if not downside:
        return float("inf")
    var = sum(r ** 2 for r in downside) / len(downside)
    std = math.sqrt(var)
    return mean / std * math.sqrt(252) if std > 0 else 0.0
