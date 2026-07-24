#!/usr/bin/env python3
"""
Crypto Trading Analysis Agent — main entry point.

Usage:
  python main.py                    # Paper trading loop (default)
  python main.py --once             # Single analysis cycle
  python main.py --backtest         # Run backtest validation
  python main.py --symbol ETHUSDT   # Override symbol
  python main.py --live             # Enable live trading (requires backtest)
"""

from __future__ import annotations

import argparse
import json
import logging
import logging.config
import sys
import time
from pathlib import Path


def setup_logging(log_level: str = "INFO", log_dir: str = "logs") -> None:
    import io
    from logging.handlers import RotatingFileHandler

    Path(log_dir).mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler — wrap stdout in UTF-8 to handle emoji/box-chars on Windows
    console_stream = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    ) if hasattr(sys.stdout, "buffer") else sys.stdout
    console_handler = logging.StreamHandler(console_stream)
    console_handler.setFormatter(fmt)

    # Rotating file handler — always UTF-8
    file_handler = RotatingFileHandler(
        f"{log_dir}/agent.log",
        maxBytes=10_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(log_level)
    root.handlers.clear()
    root.addHandler(console_handler)
    root.addHandler(file_handler)

    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)


def run_once(config) -> None:
    from agent.trading_agent import TradingAgent
    agent = TradingAgent(config)
    signal = agent.run_once()
    print(f"\n{'='*60}")
    print(f"RESULT: {signal.to_summary()}")
    if signal.market_context:
        print(f"REGIME: {signal.market_context.regime.value}")
    print(f"{'='*60}\n")


def run_loop(config) -> None:
    from agent.trading_agent import TradingAgent
    agent = TradingAgent(config)
    try:
        agent.run_loop()
    except KeyboardInterrupt:
        print("\nStopped by user")


def run_backtest(config, start_date: str, end_date: str) -> None:
    from backtest.engine import BacktestEngine
    import datetime

    def parse_date(s: str) -> int:
        dt = datetime.datetime.strptime(s, "%Y-%m-%d")
        return int(dt.timestamp() * 1000)

    engine = BacktestEngine(config)
    result = engine.run(
        symbol=config.symbol,
        start_timestamp=parse_date(start_date),
        end_timestamp=parse_date(end_date),
        primary_timeframe="1h",
    )

    print(f"\n{'='*60}")
    print(f"BACKTEST: {config.symbol}")
    print(f"  Trades:       {result.total_trades}")
    print(f"  Win Rate:     {result.win_rate:.1%}")
    print(f"  Profit Factor:{result.profit_factor:.2f}")
    print(f"  Sharpe:       {result.sharpe_ratio:.2f}")
    print(f"  Sortino:      {result.sortino_ratio:.2f}")
    print(f"  Max Drawdown: {result.max_drawdown_pct:.1%}")
    print(f"  Expectancy:   {result.expectancy:.2f}R")
    print(f"  Total Return: {result.total_return_pct:.1%}")
    print(f"  Validation:   {'PASSED' if result.validation_passed else 'FAILED'}")
    print(f"{'='*60}\n")

    # Save validation result for live trading gate
    out = Path("logs/backtest")
    out.mkdir(parents=True, exist_ok=True)
    (out / "validation_result.json").write_text(
        json.dumps({
            "symbol": result.symbol,
            "total_trades": result.total_trades,
            "win_rate": result.win_rate,
            "profit_factor": result.profit_factor,
            "expectancy": result.expectancy,
            "validation_passed": result.validation_passed,
            "run_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }, indent=2)
    )

    if not result.validation_passed:
        print("Backtest did not pass validation — live trading remains disabled.")
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Crypto Trading Analysis Agent")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    parser.add_argument("--backtest", action="store_true", help="Run backtest")
    parser.add_argument("--backtest-start", default="2024-01-01", help="Backtest start YYYY-MM-DD")
    parser.add_argument("--backtest-end", default="2024-12-31", help="Backtest end YYYY-MM-DD")
    parser.add_argument("--symbol", help="Override trading symbol (e.g. ETHUSDT)")
    parser.add_argument("--live", action="store_true", help="Enable live trading")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    from config.settings import AgentConfig
    config = AgentConfig()

    if args.symbol:
        config.symbol = args.symbol
        config.data.symbol = args.symbol

    if args.live:
        print("⚠  LIVE TRADING REQUESTED — verifying backtest validation...")
        config.execution.live_trading = True

    setup_logging(args.log_level, config.logging.log_dir)
    logger = logging.getLogger(__name__)
    logger.info("Starting Crypto Trading Analysis Agent")
    logger.info("Symbol: %s | Mode: %s", config.symbol,
                "LIVE" if config.execution.live_trading else "PAPER")

    if args.backtest:
        run_backtest(config, args.backtest_start, args.backtest_end)
    elif args.once:
        run_once(config)
    else:
        run_loop(config)


if __name__ == "__main__":
    main()
