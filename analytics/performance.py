"""
Performance Analytics — reads journal data, generates strategy and regime reports.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class PerformanceAnalytics:
    """
    Reads trade journal JSONL files and produces performance reports.
    """

    def __init__(self, journal_dir: str = "logs/journal"):
        self.journal_dir = Path(journal_dir)

    def generate_report(self) -> dict:
        """Full performance report from journal data."""
        trades = self._load_trades()
        if not trades:
            return {"error": "No closed trades found"}

        closed = [t for t in trades if t.get("event") == "exit"]
        if not closed:
            return {"error": "No closed trades in journal"}

        return {
            "summary": self._summary(closed),
            "strategy_performance": self._by_strategy(closed),
            "regime_performance": self._by_regime(closed),
            "monthly_performance": self._monthly(closed),
            "failure_analysis": self._failure_analysis(closed),
            "win_loss_streaks": self._streaks(closed),
        }

    def _summary(self, trades: list[dict]) -> dict:
        wins = [t for t in trades if t.get("outcome") == "win"]
        losses = [t for t in trades if t.get("outcome") == "loss"]
        pnls = [t.get("realized_pnl", 0) for t in trades]
        rs = [t.get("realized_r", 0) for t in trades if t.get("realized_r") is not None]

        gross_win = sum(p for p in pnls if p > 0)
        gross_loss = abs(sum(p for p in pnls if p < 0))

        return {
            "total_trades": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(trades), 4) if trades else 0,
            "profit_factor": round(gross_win / gross_loss, 3) if gross_loss else float("inf"),
            "total_pnl": round(sum(pnls), 2),
            "avg_r_multiple": round(sum(rs) / len(rs), 3) if rs else 0,
            "best_trade_r": round(max(rs), 2) if rs else 0,
            "worst_trade_r": round(min(rs), 2) if rs else 0,
        }

    def _by_strategy(self, trades: list[dict]) -> dict:
        by_strat: dict[str, list] = defaultdict(list)
        for t in trades:
            for strat in t.get("strategies", []):
                by_strat[strat].append(t)

        result = {}
        for strat, strat_trades in by_strat.items():
            wins = sum(1 for t in strat_trades if t.get("outcome") == "win")
            pnl = sum(t.get("realized_pnl", 0) for t in strat_trades)
            result[strat] = {
                "trades": len(strat_trades),
                "win_rate": round(wins / len(strat_trades), 3),
                "total_pnl": round(pnl, 2),
                "avg_pnl": round(pnl / len(strat_trades), 2),
            }
        # Sort by total PnL descending
        return dict(sorted(result.items(), key=lambda x: x[1]["total_pnl"], reverse=True))

    def _by_regime(self, trades: list[dict]) -> dict:
        by_regime: dict[str, list] = defaultdict(list)
        for t in trades:
            regime = t.get("regime", "unknown")
            by_regime[regime].append(t)

        result = {}
        for regime, regime_trades in by_regime.items():
            wins = sum(1 for t in regime_trades if t.get("outcome") == "win")
            pnl = sum(t.get("realized_pnl", 0) for t in regime_trades)
            result[regime] = {
                "trades": len(regime_trades),
                "win_rate": round(wins / len(regime_trades), 3),
                "total_pnl": round(pnl, 2),
            }
        return result

    def _monthly(self, trades: list[dict]) -> dict:
        monthly: dict[str, float] = defaultdict(float)
        for t in trades:
            ts = t.get("ts", "")[:7]  # "YYYY-MM"
            monthly[ts] += t.get("realized_pnl", 0)
        return {k: round(v, 2) for k, v in sorted(monthly.items())}

    def _failure_analysis(self, trades: list[dict]) -> dict:
        losses = [t for t in trades if t.get("outcome") == "loss"]
        reasons: dict[str, int] = defaultdict(int)
        for t in losses:
            exit_reason = t.get("exit_reason", "unknown")
            reasons[exit_reason] += 1

        strategy_losses: dict[str, int] = defaultdict(int)
        for t in losses:
            for strat in t.get("strategies", []):
                strategy_losses[strat] += 1

        return {
            "total_losses": len(losses),
            "loss_by_exit_reason": dict(reasons),
            "most_losses_by_strategy": dict(
                sorted(strategy_losses.items(), key=lambda x: x[1], reverse=True)[:5]
            ),
        }

    def _streaks(self, trades: list[dict]) -> dict:
        outcomes = [t.get("outcome") for t in trades]
        max_win_streak = max_loss_streak = 0
        current_win = current_loss = 0

        for o in outcomes:
            if o == "win":
                current_win += 1
                current_loss = 0
                max_win_streak = max(max_win_streak, current_win)
            elif o == "loss":
                current_loss += 1
                current_win = 0
                max_loss_streak = max(max_loss_streak, current_loss)

        return {"max_win_streak": max_win_streak, "max_loss_streak": max_loss_streak}

    def _load_trades(self) -> list[dict]:
        filepath = self.journal_dir / "trades.jsonl"
        if not filepath.exists():
            return []
        trades = []
        with filepath.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        trades.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return trades
