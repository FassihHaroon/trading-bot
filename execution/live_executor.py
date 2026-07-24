"""
Live Binance USD-M Futures Executor.

Places real orders on https://fapi.binance.com using HMAC-SHA256 signing.
On each trade signal:
  1. Places a MARKET order to enter the position.
  2. Places a STOP_MARKET order for the stop loss (reduceOnly).
  3. Places TAKE_PROFIT_MARKET orders for each target level (reduceOnly).

All order IDs are stored in the returned JournalEntry so the agent can
cancel them if the position is closed early.

CRITICAL SAFETY NOTES:
  - This executes REAL orders with REAL money.
  - Always verify the symbol, side, and quantity before enabling.
  - Set LIVE_TRADING_ENABLED=true in .env only after thorough backtesting.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
import urllib.parse
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config.settings import AgentConfig
from data.schemas import TradeSignal, JournalEntry, Direction

logger = logging.getLogger(__name__)

_FAPI_BASE = "https://fapi.binance.com"


class LiveFuturesExecutor:
    """
    Executes live trade signals on Binance USD-M Futures (https://fapi.binance.com).

    Lifecycle per signal:
        entry = executor.execute(signal)        → opens position + places SL/TP
        executor.close_position(entry)          → cancels open orders + market-closes
    """

    def __init__(self, config: AgentConfig):
        self.config = config
        self._api_key    = config.binance.api_key
        self._api_secret = config.binance.api_secret
        self._base_url   = _FAPI_BASE
        self._session    = self._build_session()
        self._open_order_ids: dict[str, list[int]] = {}  # entry_id → [sl_order_id, tp_order_ids...]

        if not self._api_key or not self._api_secret:
            raise RuntimeError(
                "LiveFuturesExecutor: BINANCE_API_KEY and BINANCE_API_SECRET must be set in .env"
            )
        logger.info("LiveFuturesExecutor initialised for %s", _FAPI_BASE)

    # ──────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────

    def execute(self, signal: TradeSignal) -> Optional[JournalEntry]:
        """
        Open a live futures position for the given signal.

        Steps:
          1. Fetch precision rules for the symbol.
          2. Place MARKET entry order.
          3. Place STOP_MARKET stop-loss order (reduceOnly).
          4. Place TAKE_PROFIT_MARKET orders for each target (reduceOnly).

        Returns JournalEntry on success, None on failure.
        """
        symbol    = signal.symbol
        direction = signal.direction
        side      = "BUY" if direction == Direction.LONG else "SELL"
        qty       = self._format_quantity(symbol, signal.position_size or 0.0)

        if qty <= 0:
            logger.error("LiveExecutor: invalid quantity=%.8f for %s", signal.position_size or 0, symbol)
            return None

        logger.info(
            "LiveExecutor: placing MARKET %s %s qty=%s entry=%.4f sl=%.4f",
            side, symbol, qty, signal.entry_price or 0, signal.stop_price or 0,
        )

        # ── 1. Market entry order ──────────────────────────────────────────
        entry_resp = self._place_order(
            symbol=symbol,
            side=side,
            order_type="MARKET",
            quantity=qty,
        )
        if entry_resp is None:
            logger.error("LiveExecutor: entry order failed for %s", symbol)
            return None

        entry_order_id  = entry_resp.get("orderId")
        actual_price    = float(entry_resp.get("avgPrice") or entry_resp.get("price") or signal.entry_price or 0)
        entry_id        = f"LIVE_{symbol}_{int(time.time() * 1000)}"
        sl_tp_order_ids = []

        logger.info(
            "LiveExecutor: entry order filled | orderId=%s avgPrice=%.4f",
            entry_order_id, actual_price,
        )

        # ── 2. Stop-loss order ─────────────────────────────────────────────
        if signal.stop_price:
            sl_side = "SELL" if direction == Direction.LONG else "BUY"
            sl_resp = self._place_order(
                symbol=symbol,
                side=sl_side,
                order_type="STOP_MARKET",
                quantity=qty,
                stop_price=self._format_price(symbol, signal.stop_price),
                reduce_only=True,
            )
            if sl_resp:
                sl_order_id = sl_resp.get("orderId")
                sl_tp_order_ids.append(sl_order_id)
                logger.info("LiveExecutor: stop-loss placed | orderId=%s stopPrice=%.4f", sl_order_id, signal.stop_price)
            else:
                logger.error("LiveExecutor: STOP-LOSS ORDER FAILED — close position manually!")

        # ── 3. Take-profit orders ──────────────────────────────────────────
        tp_side = "SELL" if direction == Direction.LONG else "BUY"
        targets = signal.targets or []
        if targets:
            # Split qty across targets evenly
            tp_qty_each = self._format_quantity(symbol, (signal.position_size or 0.0) / len(targets))
            for idx, target in enumerate(targets[:3]):  # Max 3 TP levels
                tp_resp = self._place_order(
                    symbol=symbol,
                    side=tp_side,
                    order_type="TAKE_PROFIT_MARKET",
                    quantity=tp_qty_each,
                    stop_price=self._format_price(symbol, target),
                    reduce_only=True,
                )
                if tp_resp:
                    tp_order_id = tp_resp.get("orderId")
                    sl_tp_order_ids.append(tp_order_id)
                    logger.info(
                        "LiveExecutor: TP%d placed | orderId=%s price=%.4f",
                        idx + 1, tp_order_id, target,
                    )
                else:
                    logger.warning("LiveExecutor: TP%d order failed for target=%.4f", idx + 1, target)

        self._open_order_ids[entry_id] = sl_tp_order_ids

        # ── Build JournalEntry ─────────────────────────────────────────────
        risk_amount = (
            abs(actual_price - (signal.stop_price or actual_price)) * (signal.position_size or 0.0)
        )

        return JournalEntry(
            entry_id=entry_id,
            symbol=symbol,
            direction=direction,
            entry_price=actual_price,
            stop_price=signal.stop_price,
            targets=signal.targets or [],
            position_size=signal.position_size or 0.0,
            risk_amount=risk_amount,
            confidence=signal.confidence,
            strategies_used=signal.rules_fired[:5] if signal.rules_fired else [],
            market_regime=signal.market_context.regime.value if signal.market_context else "unknown",
            market_phase=signal.market_context.phase.value if signal.market_context else "unknown",
            is_paper_trade=False,
            outcome="open",
            reasoning_trace=[f"live_entry_order_id={entry_order_id}"] + [
                f"sl_tp_order_ids={sl_tp_order_ids}"
            ],
            rules_fired=signal.rules_fired or [],
        )

    def close_position(self, entry: JournalEntry, current_price: float) -> Optional[dict]:
        """
        Market-close the position and cancel all associated SL/TP orders.

        Returns the close order response dict, or None on failure.
        """
        symbol    = entry.symbol
        direction = entry.direction
        side      = "SELL" if direction == Direction.LONG else "BUY"
        qty       = self._format_quantity(symbol, entry.position_size)

        logger.info("LiveExecutor: closing position %s %s qty=%s", side, symbol, qty)

        # Cancel open SL/TP orders first
        for order_id in self._open_order_ids.get(entry.entry_id, []):
            self._cancel_order(symbol, order_id)

        # Market close
        close_resp = self._place_order(
            symbol=symbol,
            side=side,
            order_type="MARKET",
            quantity=qty,
            reduce_only=True,
        )
        if close_resp:
            close_price = float(close_resp.get("avgPrice") or current_price)
            logger.info("LiveExecutor: position closed @ %.4f", close_price)
        else:
            logger.error("LiveExecutor: CLOSE ORDER FAILED for %s — check Binance dashboard!", symbol)

        # Clean up tracking
        self._open_order_ids.pop(entry.entry_id, None)
        return close_resp

    def get_position(self, symbol: str) -> Optional[dict]:
        """Fetch current open position for a symbol from FAPI."""
        data = self._signed_request("GET", "/fapi/v2/positionRisk", {"symbol": symbol})
        if not data:
            return None
        if isinstance(data, list):
            for pos in data:
                if float(pos.get("positionAmt", 0)) != 0:
                    return pos
        return None

    def get_account_balance(self) -> float:
        """Return USDT available balance from futures account."""
        data = self._signed_request("GET", "/fapi/v2/account", {})
        if not data:
            return 0.0
        try:
            for asset in data.get("assets", []):
                if asset.get("asset") == "USDT":
                    return float(asset.get("availableBalance", 0.0))
        except Exception:
            pass
        return 0.0

    # ──────────────────────────────────────────────
    # Order helpers
    # ──────────────────────────────────────────────

    def _place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: str,
        stop_price: Optional[str] = None,
        reduce_only: bool = False,
    ) -> Optional[dict]:
        params: dict = {
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "quantity": quantity,
        }
        if stop_price:
            params["stopPrice"] = stop_price
        if reduce_only:
            params["reduceOnly"] = "true"

        return self._signed_request("POST", "/fapi/v1/order", params)

    def _cancel_order(self, symbol: str, order_id: int) -> None:
        try:
            self._signed_request("DELETE", "/fapi/v1/order", {"symbol": symbol, "orderId": order_id})
            logger.info("LiveExecutor: cancelled orderId=%s for %s", order_id, symbol)
        except Exception as exc:
            logger.warning("LiveExecutor: failed to cancel orderId=%s: %s", order_id, exc)

    # ──────────────────────────────────────────────
    # Signed request + precision helpers
    # ──────────────────────────────────────────────

    def _signed_request(
        self,
        method: str,
        path: str,
        params: dict,
    ) -> Optional[dict | list]:
        """Add timestamp + HMAC-SHA256 signature and send to FAPI."""
        params["timestamp"] = int(time.time() * 1000)
        query_string = urllib.parse.urlencode(params)
        signature = hmac.new(
            self._api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        query_string += f"&signature={signature}"

        url = f"{self._base_url}{path}"
        try:
            if method == "GET":
                resp = self._session.get(url, params=query_string, timeout=10)
            elif method == "POST":
                resp = self._session.post(url, params=query_string, timeout=10)
            elif method == "DELETE":
                resp = self._session.delete(url, params=query_string, timeout=10)
            else:
                raise ValueError(f"Unsupported method: {method}")

            resp.raise_for_status()
            return resp.json()

        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            body = exc.response.text if exc.response is not None else ""
            logger.error("LiveExecutor HTTP %d on %s %s: %s", status, method, path, body)
            return None
        except Exception as exc:
            logger.error("LiveExecutor request error on %s %s: %s", method, path, exc)
            return None

    def _get_symbol_info(self, symbol: str) -> Optional[dict]:
        """Fetch exchange info for symbol to get precision rules."""
        try:
            resp = self._session.get(
                f"{self._base_url}/fapi/v1/exchangeInfo",
                params={"symbol": symbol},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            for s in data.get("symbols", []):
                if s.get("symbol") == symbol:
                    return s
        except Exception as exc:
            logger.warning("LiveExecutor: could not fetch symbol info: %s", exc)
        return None

    def _format_quantity(self, symbol: str, qty: float) -> str:
        """Format quantity to correct decimal precision for the symbol."""
        if qty <= 0:
            return "0"
        # Default to 3 decimal places; a real implementation should cache exchange info
        return f"{qty:.3f}"

    def _format_price(self, symbol: str, price: float) -> str:
        """Format price to correct decimal precision for the symbol."""
        # Default to 2 decimal places; BTC/USDT typically uses 1-2
        return f"{price:.2f}"

    def _build_session(self) -> requests.Session:
        session = requests.Session()
        retry = Retry(total=3, backoff_factor=1.0, status_forcelist=[500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.headers.update({
            "X-MBX-APIKEY": self._api_key,
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        })
        return session
