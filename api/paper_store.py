"""
Paper Trade Store — simulated limit order book.
Trades are PENDING until price reaches the entry level.
  LONG  limit: fills when price <= entry_price  (buy the dip)
  SHORT limit: fills when price >= entry_price  (sell the rip)
All money is fake. Closed trades feed the self-learner.
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
_COMMISSION = 0.0004  # 0.04 % per side (Binance taker)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_live_price(symbol: str) -> Optional[float]:
    """Fetch current spot price from Binance (no auth required)."""
    try:
        r = requests.get(_BINANCE_PRICE_URL, params={"symbol": symbol}, timeout=5)
        r.raise_for_status()
        return float(r.json()["price"])
    except Exception as exc:
        logger.warning("price fetch failed for %s: %s", symbol, exc)
        return None


def _fill_direction(entry_price: float, live_price: float) -> str:
    """
    Which way must price move to reach the entry?
      'up'   → entry is above current market → wait for price to RISE  (breakout long / sell-limit short)
      'down' → entry is below current market → wait for price to FALL  (pullback long / breakdown short)
    """
    return "up" if entry_price > live_price else "down"


def _entry_triggered(fill_dir: str, entry_price: float, current_price: float) -> bool:
    """Return True when price has crossed the entry level in the required direction."""
    if fill_dir == "up":
        return current_price >= entry_price   # price rose to entry
    else:
        return current_price <= entry_price   # price fell to entry


class PaperTradeStore:
    """
    Thread-safe store for paper positions.

    Trade lifecycle:
      pending  → (price reaches entry) → open → (SL / TP / manual) → closed
      pending  → (user cancels)        → closed  (outcome='cancelled')
    """

    def __init__(self, path: Path = _PAPER_FILE):
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._data: dict = self._load()

    # ── Public API ────────────────────────────────────────────────────────────

    def open_trade(self, signal: dict) -> dict:
        """
        Place a paper limit order.
        Fetches the live price to decide if it's already filled:
          - If price already at/past entry → status='open' (immediate fill)
          - Otherwise → status='pending'  (waiting for entry)
        """
        trade_id = f"PT_{signal['symbol']}_{int(time.time() * 1000)}"
        entry = signal.get("entry_price") or signal.get("current_price", 0.0)
        live  = get_live_price(signal["symbol"]) or entry

        fill_dir       = _fill_direction(entry, live)
        already_filled = _entry_triggered(fill_dir, entry, live)
        status         = "open"    if already_filled else "pending"
        filled_at      = _now_iso() if already_filled else None
        outcome        = "open"    if already_filled else "pending"

        trade = {
            "id":              trade_id,
            "symbol":          signal["symbol"],
            "direction":       signal["direction"],
            "status":          status,         # "pending" | "open"
            "fill_direction":  fill_dir,       # "up" or "down"
            "entry_price":     entry,
            "placed_live_price": live,         # market price at order placement
            "current_price":   live,
            "stop_price":      signal.get("stop_price"),
            "targets":         signal.get("targets", []),
            "position_size":   signal.get("position_size", 0.0),
            "risk_amount":     signal.get("risk_amount", 0.0),
            "confidence":      signal.get("confidence", 0.0),
            "regime":          signal.get("regime", "unknown"),
            "strategies_used": signal.get("strategies_used", []),
            "placed_at":       _now_iso(),
            "filled_at":       filled_at,
            "closed_at":       None,
            "outcome":         outcome,
            "realized_pnl":    None,
            "realized_r":      None,
            "exit_price":      None,
            "exit_reason":     None,
            "unrealized_pnl":  0.0,
            "unrealized_pct":  0.0,
            "entry_distance":  round((live - entry) / entry * 100, 4) if entry else 0.0,
        }

        with self._lock:
            self._data["open"].append(trade)
            self._save()

        if already_filled:
            logger.info("Paper trade FILLED immediately: %s %s @ %.4f",
                        trade["direction"], trade["symbol"], live)
        else:
            dist = abs(live - entry) / entry * 100
            logger.info(
                "Paper trade PENDING: %s %s — entry=%.4f current=%.4f (%.2f%% away)",
                trade["direction"], trade["symbol"], entry, live, dist,
            )
        return trade

    def cancel_trade(self, trade_id: str) -> Optional[dict]:
        """Cancel a pending order (not yet filled). No P&L recorded."""
        with self._lock:
            for i, t in enumerate(self._data["open"]):
                if t["id"] == trade_id and t["status"] == "pending":
                    trade = dict(self._data["open"].pop(i))
                    trade["closed_at"]   = _now_iso()
                    trade["exit_reason"] = "cancelled"
                    trade["outcome"]     = "cancelled"
                    self._data["closed"].append(trade)
                    self._save()
                    logger.info("Paper order cancelled: %s", trade_id)
                    return trade
        return None

    def close_trade(self, trade_id: str, exit_price: float, reason: str) -> Optional[dict]:
        """Close an OPEN (filled) position, compute P&L, move to closed list."""
        with self._lock:
            for i, t in enumerate(self._data["open"]):
                if t["id"] == trade_id and t["status"] == "open":
                    trade = dict(self._data["open"].pop(i))
                    trade["exit_price"]  = exit_price
                    trade["closed_at"]   = _now_iso()
                    trade["exit_reason"] = reason

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
                    self._write_to_journal(trade)
                    return trade
        return None

    def manual_close(self, trade_id: str) -> Optional[dict]:
        """
        Manually act on any active trade:
          - pending → cancel (no fill, no P&L)
          - open    → close at current market price
        """
        trade = self._find_open(trade_id)
        if not trade:
            return None
        if trade["status"] == "pending":
            return self.cancel_trade(trade_id)
        price = get_live_price(trade["symbol"]) or trade.get("current_price") or trade["entry_price"]
        return self.close_trade(trade_id, price, "manual_close")

    def update_prices(self) -> list[dict]:
        """
        Called every 30 s by the background task:
          1. For PENDING trades — check if entry price was crossed (fill trigger)
          2. For OPEN trades    — refresh P&L; auto-close on SL or TP hit
        Returns list of newly closed trades (so the caller can log them).
        """
        closed_this_round: list[dict] = []

        with self._lock:
            all_ids = [t["id"] for t in self._data["open"]]

        for tid in all_ids:
            trade = self._find_open(tid)
            if trade is None:
                continue

            price = get_live_price(trade["symbol"])
            if price is None:
                continue

            # Always update current price + distance-to-entry for pending
            with self._lock:
                for t in self._data["open"]:
                    if t["id"] == tid:
                        t["current_price"] = price
                        if t["status"] == "pending" and t["entry_price"]:
                            # positive = price is above entry, negative = price is below entry
                            t["entry_distance"] = round(
                                (price - t["entry_price"]) / t["entry_price"] * 100, 4
                            )
                            t["fill_direction"] = t.get("fill_direction") or _fill_direction(
                                t["entry_price"], t.get("placed_live_price", price)
                            )
                        break
                self._save()

            # ── 1. Check PENDING → OPEN fill ──────────────────────────────
            if trade["status"] == "pending":
                fill_dir = trade.get("fill_direction") or _fill_direction(
                    trade["entry_price"], trade.get("placed_live_price", price)
                )
                if _entry_triggered(fill_dir, trade["entry_price"], price):
                    with self._lock:
                        for t in self._data["open"]:
                            if t["id"] == tid:
                                t["status"]    = "open"
                                t["outcome"]   = "open"
                                t["filled_at"] = _now_iso()
                                break
                        self._save()
                    logger.info(
                        "Paper order FILLED: %s %s entry=%.4f triggered @ %.4f",
                        trade["direction"], trade["symbol"],
                        trade["entry_price"], price,
                    )
                # Whether just filled or still pending, no SL/TP check yet
                continue

            # ── 2. OPEN trade — update unrealized P&L ────────────────────
            entry = trade["entry_price"] or price
            size  = trade["position_size"] or 0.0

            if trade["direction"] == "long":
                unreal     = (price - entry) * size
                unreal_pct = (price - entry) / entry * 100 if entry else 0.0
            else:
                unreal     = (entry - price) * size
                unreal_pct = (entry - price) / entry * 100 if entry else 0.0

            with self._lock:
                for t in self._data["open"]:
                    if t["id"] == tid:
                        t["unrealized_pnl"] = round(unreal, 4)
                        t["unrealized_pct"] = round(unreal_pct, 4)
                        break
                self._save()

            # ── 3. Auto-close on Stop Loss ────────────────────────────────
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

            # ── 4. Auto-close on Take Profit 1 ───────────────────────────
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

    def delete_closed(self, trade_id: str) -> bool:
        """Permanently remove a trade from the closed list (e.g. bad paper trades)."""
        with self._lock:
            before = len(self._data["closed"])
            self._data["closed"] = [t for t in self._data["closed"] if t["id"] != trade_id]
            if len(self._data["closed"]) < before:
                self._save()
                logger.info("Closed trade deleted: %s", trade_id)
                return True
        return False

    @property
    def open_positions(self) -> list[dict]:
        """All non-closed trades (pending + open)."""
        with self._lock:
            return list(self._data["open"])

    @property
    def closed_trades(self) -> list[dict]:
        with self._lock:
            return list(self._data["closed"])

    def summary(self) -> dict:
        closed = self.closed_trades
        # Exclude cancelled orders from win/loss stats
        real = [t for t in closed if t["outcome"] not in ("cancelled",)]
        open_list = self.open_positions
        pending = sum(1 for t in open_list if t.get("status") == "pending")
        active  = sum(1 for t in open_list if t.get("status") == "open")

        if not real:
            return {
                "total": 0, "wins": 0, "losses": 0, "win_rate": 0,
                "total_pnl": 0, "avg_r": 0,
                "open_count": active, "pending_count": pending,
            }

        wins   = [t for t in real if t["outcome"] == "win"]
        losses = [t for t in real if t["outcome"] == "loss"]
        pnls   = [t["realized_pnl"] or 0 for t in real]
        rs     = [t["realized_r"]   or 0 for t in real]
        return {
            "total":         len(real),
            "wins":          len(wins),
            "losses":        len(losses),
            "win_rate":      round(len(wins) / len(real), 4),
            "total_pnl":     round(sum(pnls), 2),
            "avg_r":         round(sum(rs) / len(rs), 4) if rs else 0,
            "open_count":    active,
            "pending_count": pending,
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
                data = json.loads(self._path.read_text(encoding="utf-8"))
                # Migrate trades created before the pending-order logic was added
                for t in data.get("open", []):
                    if "status" not in t:
                        # Old trade: was immediately opened by old code, treat as filled
                        t["status"]            = "open"
                        t["filled_at"]         = t.get("opened_at")
                        t["placed_at"]         = t.get("opened_at")
                        t["placed_live_price"] = t.get("entry_price")
                        t["entry_distance"]    = 0.0
                        # fill_direction not relevant for already-open trades, set a safe default
                        t["fill_direction"]    = "down"
                    if "placed_at" not in t:
                        t["placed_at"] = t.get("opened_at")
                    if "fill_direction" not in t:
                        t["fill_direction"] = _fill_direction(
                            t.get("entry_price", 0), t.get("placed_live_price", 0)
                        )
                return data
            except Exception:
                pass
        return {"open": [], "closed": []}

    def _save(self):
        self._path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")

    def _write_to_journal(self, trade: dict):
        """Append a closed (filled) trade to trades.jsonl for self-learning."""
        journal = Path("logs/journal/trades.jsonl")
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
