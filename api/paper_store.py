"""
Paper Trade Store — persists open/closed paper positions to disk.
All money is fake. Used to track bot signal performance and feed the self-learner.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_PAPER_FILE = Path("logs/paper_trades.json")
_BINANCE_PRICE_URL = "https://api.binance.com/api/v3/ticker/price"
_COMMISSION = 0.0004   # 0.04% per side (Binance taker)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_live_price(symbol: str) -> Optional[float]:
    """Fetch current mark price from Binance spot (no auth required)."""
    try:
        r = requests.get(_BINANCE_PRICE_URL, params={"symbol": symbol}, timeout=5)
        r.raise_for_status()
        return float(r.json()["price"])
    except Exception as exc:
        logger.warning("price fetch failed for %s: %s", symbol, exc)
        return None


class PaperTradeStore:
    """Thread-safe store for paper trades. Persists to logs/paper_trades.json."""

    def __init__(self, path: Path = _PAPER_FILE):
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._data: dict = self._load()

    # ── Public API ────────────────────────────────────────────────────────────

    def open_trade(self, signal: dict) -> dict:
        """Create a new paper position from a signal dict."""
        trade_id = f"PT_{signal['symbol']}_{int(time.time() * 1000)}"
        entry = signal.get("entry_price") or signal.get("current_price", 0.0)

        trade = {
            "id":             trade_id,
            "symbol":         signal["symbol"],
            "direction":      signal["direction"],
            "entry_price":    entry,
            "current_price":  entry,
            "stop_price":     signal.get("stop_price"),
            "targets":        signal.get("targets", []),
            "position_size":  signal.get("position_size", 0.0),
            "risk_amount":    signal.get("risk_amount", 0.0),
            "confidence":     signal.get("confidence", 0.0),
            "regime":         signal.get("regime", "unknown"),
            "strategies_used": signal.get("strategies_used", []),
            "opened_at":      _now_iso(),
            "closed_at":      None,
            "outcome":        "open",
            "realized_pnl":   None,
            "realized_r":     None,
            "exit_price":     None,
            "exit_reason":    None,
            "unrealized_pnl": 0.0,
            "unrealized_pct": 0.0,
        }
        with self._lock:
            self._data["open"].append(trade)
            self._save()
        logger.info("Paper trade opened: %s %s @ %.4f", trade["direction"], trade["symbol"], entry)
        return trade

    def close_trade(self, trade_id: str, exit_price: float, reason: str) -> Optional[dict]:
        """Close an open position, compute P&L, move to closed list."""
        with self._lock:
            for i, t in enumerate(self._data["open"]):
                if t["id"] == trade_id:
                    trade = dict(self._data["open"].pop(i))
                    trade["exit_price"]  = exit_price
                    trade["closed_at"]   = _now_iso()
                    trade["exit_reason"] = reason

                    # P&L
                    entry = trade["entry_price"] or exit_price
                    size  = trade["position_size"] or 0.0
                    if trade["direction"] == "long":
                        gross = (exit_price - entry) * size
                    else:
                        gross = (entry - exit_price) * size

                    comm = size * exit_price * _COMMISSION * 2
                    pnl  = gross - comm

                    risk = abs(entry - (trade["stop_price"] or entry)) * size
                    r_multiple = pnl / risk if risk > 0 else 0.0

                    trade["realized_pnl"]  = round(pnl, 4)
                    trade["realized_r"]    = round(r_multiple, 4)
                    trade["outcome"]       = "win" if pnl > 0 else ("breakeven" if pnl == 0 else "loss")
                    trade["current_price"] = exit_price
                    trade["unrealized_pnl"] = 0.0
                    trade["unrealized_pct"] = 0.0

                    self._data["closed"].append(trade)
                    self._save()
                    logger.info(
                        "Paper trade closed: %s %s @ %.4f | pnl=%.2f R=%.2f outcome=%s",
                        trade["direction"], trade["symbol"], exit_price,
                        pnl, r_multiple, trade["outcome"],
                    )
                    # Write to journal so self-learner can train on it
                    self._write_to_journal(trade)
                    return trade
        return None

    def update_prices(self) -> list[dict]:
        """
        Refresh current_price + unrealized P&L for all open positions.
        Also auto-closes any position that has hit its SL or TP.
        Returns list of newly closed trades.
        """
        closed_this_round: list[dict] = []
        # snapshot open IDs to avoid mutation during iteration
        with self._lock:
            open_ids = [t["id"] for t in self._data["open"]]

        for tid in open_ids:
            trade = self._find_open(tid)
            if trade is None:
                continue

            price = get_live_price(trade["symbol"])
            if price is None:
                continue

            # Update unrealized P&L
            entry = trade["entry_price"] or price
            size  = trade["position_size"] or 0.0
            if trade["direction"] == "long":
                unreal = (price - entry) * size
            else:
                unreal = (entry - price) * size
            unreal_pct = ((price - entry) / entry * 100) if entry else 0.0
            if trade["direction"] == "short":
                unreal_pct = -unreal_pct

            with self._lock:
                for t in self._data["open"]:
                    if t["id"] == tid:
                        t["current_price"]  = price
                        t["unrealized_pnl"] = round(unreal, 4)
                        t["unrealized_pct"] = round(unreal_pct, 4)
                        break
                self._save()

            # Auto-close on SL
            sl = trade.get("stop_price")
            if sl:
                if trade["direction"] == "long"  and price <= sl:
                    closed = self.close_trade(tid, price, "stop_loss")
                    if closed:
                        closed_this_round.append(closed)
                    continue
                if trade["direction"] == "short" and price >= sl:
                    closed = self.close_trade(tid, price, "stop_loss")
                    if closed:
                        closed_this_round.append(closed)
                    continue

            # Auto-close on first TP hit
            targets = trade.get("targets") or []
            if targets:
                tp1 = targets[0]
                if trade["direction"] == "long"  and price >= tp1:
                    closed = self.close_trade(tid, price, "take_profit_1")
                    if closed:
                        closed_this_round.append(closed)
                    continue
                if trade["direction"] == "short" and price <= tp1:
                    closed = self.close_trade(tid, price, "take_profit_1")
                    if closed:
                        closed_this_round.append(closed)
                    continue

        return closed_this_round

    def manual_close(self, trade_id: str) -> Optional[dict]:
        """Manually close an open trade at the current market price."""
        trade = self._find_open(trade_id)
        if not trade:
            return None
        price = get_live_price(trade["symbol"]) or trade.get("current_price") or trade["entry_price"]
        return self.close_trade(trade_id, price, "manual_close")

    @property
    def open_positions(self) -> list[dict]:
        with self._lock:
            return list(self._data["open"])

    @property
    def closed_trades(self) -> list[dict]:
        with self._lock:
            return list(self._data["closed"])

    def summary(self) -> dict:
        closed = self.closed_trades
        if not closed:
            return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0,
                    "total_pnl": 0, "avg_r": 0, "open_count": len(self.open_positions)}
        wins   = [t for t in closed if t["outcome"] == "win"]
        losses = [t for t in closed if t["outcome"] == "loss"]
        pnls   = [t["realized_pnl"] or 0 for t in closed]
        rs     = [t["realized_r"]   or 0 for t in closed]
        return {
            "total":      len(closed),
            "wins":       len(wins),
            "losses":     len(losses),
            "win_rate":   round(len(wins) / len(closed), 4),
            "total_pnl":  round(sum(pnls), 2),
            "avg_r":      round(sum(rs) / len(rs), 4) if rs else 0,
            "open_count": len(self.open_positions),
        }

    # ── Internals ─────────────────────────────────────────────────────────────

    def _find_open(self, trade_id: str) -> Optional[dict]:
        with self._lock:
            for t in self._data["open"]:
                if t["id"] == trade_id:
                    return dict(t)
        return None

    def _load(self) -> dict:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"open": [], "closed": []}

    def _save(self):
        self._path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")

    def _write_to_journal(self, trade: dict):
        """Append closed trade to trades.jsonl so the self-learner can read it."""
        from pathlib import Path as P
        journal = P("logs/journal/trades.jsonl")
        journal.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "event":         "exit",
            "ts":            trade["closed_at"],
            "entry_id":      trade["id"],
            "symbol":        trade["symbol"],
            "direction":     trade["direction"],
            "entry_price":   trade["entry_price"],
            "exit_price":    trade["exit_price"],
            "stop":          trade["stop_price"],
            "position_size": trade["position_size"],
            "risk_amount":   trade["risk_amount"],
            "realized_pnl":  trade["realized_pnl"],
            "realized_r":    trade["realized_r"],
            "outcome":       trade["outcome"],
            "exit_reason":   trade["exit_reason"],
            "confidence":    trade["confidence"],
            "strategies":    trade["strategies_used"],
            "regime":        trade["regime"],
            "is_paper":      True,
        }
        with journal.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        logger.info("Paper trade written to journal for self-learning: %s", trade["id"])


# Module-level singleton
_store: Optional[PaperTradeStore] = None


def get_store() -> PaperTradeStore:
    global _store
    if _store is None:
        _store = PaperTradeStore()
    return _store
