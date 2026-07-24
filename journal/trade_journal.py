"""
Trade Journal — logs every decision (including NO_TRADE) with full reasoning trace.
PROCESS_LOGGING_COMPLETE: skipped trades are as important as executed trades.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from data.schemas import TradeSignal, JournalEntry, Direction
from config.settings import AgentConfig

logger = logging.getLogger(__name__)


class TradeJournal:
    """
    Append-only journal. Every cycle writes one record.
    Format: JSONL (one JSON object per line) for easy streaming/analysis.
    """

    def __init__(self, config: AgentConfig):
        self.config = config
        lc = config.logging
        self.journal_dir = Path(lc.journal_dir)
        self.journal_dir.mkdir(parents=True, exist_ok=True)
        self._entries: list[JournalEntry] = []

    # ──────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────

    def log_decision(self, signal: TradeSignal) -> None:
        """
        Log any TradeSignal — including NO_TRADE decisions.
        NO_TRADE_IS_VALID_OUTPUT: skipped trades are fully logged.
        """
        record = self._signal_to_record(signal)
        self._append_jsonl("decisions.jsonl", record)

        if signal.direction != Direction.NO_TRADE:
            logger.info("JOURNAL: logged %s signal for %s", signal.direction.value, signal.symbol)
        elif self.config.logging.log_no_trade:
            logger.info("JOURNAL: logged NO_TRADE for %s — %s", signal.symbol, signal.no_trade_reason)

    def log_entry(self, entry: JournalEntry) -> None:
        """Log a trade entry (when a position is opened)."""
        self._entries.append(entry)
        record = self._entry_to_record(entry, event="entry")
        self._append_jsonl("trades.jsonl", record)
        logger.info("JOURNAL: trade entry %s %s @ %.4f", entry.direction.value, entry.symbol, entry.entry_price)

    def log_exit(self, entry: JournalEntry) -> None:
        """Log a trade exit (when a position closes)."""
        # Update in-memory
        for i, e in enumerate(self._entries):
            if e.entry_id == entry.entry_id:
                self._entries[i] = entry
                break
        record = self._entry_to_record(entry, event="exit")
        self._append_jsonl("trades.jsonl", record)
        logger.info(
            "JOURNAL: trade exit %s %s @ %.4f | pnl=%.2f R=%.2f outcome=%s",
            entry.direction.value, entry.symbol,
            entry.exit_price or 0, entry.realized_pnl or 0,
            entry.realized_r or 0, entry.outcome,
        )

    def get_recent_trades(self, n: int = 50) -> list[JournalEntry]:
        """Return last N closed trades."""
        closed = [e for e in self._entries if e.outcome in ("win", "loss", "breakeven")]
        return closed[-n:]

    def get_stats(self) -> dict:
        """Quick performance statistics from in-memory entries."""
        closed = [e for e in self._entries if e.realized_pnl is not None]
        if not closed:
            return {"total_trades": 0}

        wins = [e for e in closed if e.outcome == "win"]
        losses = [e for e in closed if e.outcome == "loss"]
        total_pnl = sum(e.realized_pnl for e in closed)
        gross_win = sum(e.realized_pnl for e in wins)
        gross_loss = abs(sum(e.realized_pnl for e in losses))

        return {
            "total_trades": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": len(wins) / len(closed),
            "profit_factor": gross_win / gross_loss if gross_loss > 0 else float("inf"),
            "total_pnl": total_pnl,
            "avg_r": sum(e.realized_r or 0 for e in closed) / len(closed),
            "strategy_breakdown": self._strategy_breakdown(closed),
        }

    # ──────────────────────────────────────────────
    # Serialization
    # ──────────────────────────────────────────────

    def _signal_to_record(self, signal: TradeSignal) -> dict:
        return {
            "event": "decision",
            "ts": datetime.now(timezone.utc).isoformat(),
            "symbol": signal.symbol,
            "direction": signal.direction.value,
            "confidence": round(signal.confidence, 4),
            "entry": signal.entry_price,
            "stop": signal.stop_price,
            "targets": signal.targets,
            "position_size": signal.position_size,
            "risk_amount": signal.risk_amount,
            "rr": signal.risk_reward_ratio,
            "is_paper": signal.is_paper_trade,
            "no_trade_reason": signal.no_trade_reason,
            "regime": signal.market_context.regime.value if signal.market_context else None,
            "phase": signal.market_context.phase.value if signal.market_context else None,
            "strategies": [r.strategy_id for r in signal.strategy_results if r.is_valid],
            "rules_fired": signal.rules_fired[:10],
            "knowledge_refs": signal.knowledge_refs[:10],
        }

    def _entry_to_record(self, entry: JournalEntry, event: str) -> dict:
        return {
            "event": event,
            "ts": datetime.now(timezone.utc).isoformat(),
            "entry_id": entry.entry_id,
            "symbol": entry.symbol,
            "direction": entry.direction.value,
            "entry_price": entry.entry_price,
            "exit_price": entry.exit_price,
            "stop": entry.stop_price,
            "position_size": entry.position_size,
            "risk_amount": entry.risk_amount,
            "realized_pnl": entry.realized_pnl,
            "realized_r": entry.realized_r,
            "outcome": entry.outcome,
            "exit_reason": entry.exit_reason,
            "confidence": entry.confidence,
            "strategies": entry.strategies_used,
            "regime": entry.market_regime,
            "phase": entry.market_phase,
            "is_paper": entry.is_paper_trade,
            "reasoning_trace": entry.reasoning_trace,
            "rules_fired": entry.rules_fired,
        }

    def _append_jsonl(self, filename: str, record: dict) -> None:
        filepath = self.journal_dir / filename
        with filepath.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")

    def _strategy_breakdown(self, entries: list[JournalEntry]) -> dict:
        breakdown: dict[str, dict] = {}
        for e in entries:
            for strat in e.strategies_used:
                if strat not in breakdown:
                    breakdown[strat] = {"trades": 0, "wins": 0, "pnl": 0.0}
                breakdown[strat]["trades"] += 1
                if e.outcome == "win":
                    breakdown[strat]["wins"] += 1
                breakdown[strat]["pnl"] += e.realized_pnl or 0
        for strat, stats in breakdown.items():
            stats["win_rate"] = stats["wins"] / stats["trades"] if stats["trades"] > 0 else 0
        return breakdown
