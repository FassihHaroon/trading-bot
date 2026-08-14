"""
Bybit V5 spot/linear connector — drop-in replacement for BinanceSpotClient.

Uses Bybit's public V5 API (no API key required for market data).
Implements the same interface as BinanceSpotClient so MarketDataService
only needs an import swap.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config.settings import BinanceConfig
from data.schemas import Candle, OrderBook, OrderBookLevel, Ticker24h

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.bybit.com"
_CATEGORY = "linear"  # USDT perpetuals — price tracks spot closely

# Binance interval string → Bybit interval
_INTERVAL_MAP: dict[str, str] = {
    "1m":  "1",
    "3m":  "3",
    "5m":  "5",
    "15m": "15",
    "30m": "30",
    "1h":  "60",
    "2h":  "120",
    "4h":  "240",
    "6h":  "360",
    "12h": "720",
    "1d":  "D",
    "1w":  "W",
    "1M":  "M",
}


def _bybit_interval(binance_interval: str) -> str:
    mapped = _INTERVAL_MAP.get(binance_interval)
    if mapped is None:
        raise ValueError(f"Unsupported interval: {binance_interval!r}")
    return mapped


def _parse_kline_row(row: list) -> Candle:
    """
    Bybit kline row: [startTime, open, high, low, close, volume, turnover]
    - volume   = base asset volume
    - turnover = quote asset volume (equivalent to Binance quoteVolume)
    - Bybit doesn't expose trade count or taker buy volume per candle.
    """
    vol = float(row[5])
    qvol = float(row[6])
    return Candle(
        timestamp=int(row[0]),
        open=float(row[1]),
        high=float(row[2]),
        low=float(row[3]),
        close=float(row[4]),
        volume=vol,
        quote_volume=qvol,
        trades=0,
        taker_buy_volume=vol / 2.0,        # approximation (no breakdown from Bybit)
        taker_buy_quote_volume=qvol / 2.0,
    )


class BybitSpotClient:
    """
    Drop-in replacement for BinanceSpotClient using the Bybit V5 public API.
    Accepts BinanceConfig for timeout/retry settings; Bybit needs no API key
    for read-only market data.
    """

    def __init__(self, config: BinanceConfig) -> None:
        self._timeout = getattr(config, "request_timeout", 10)
        self._max_retries = getattr(config, "max_retries", 3)
        self._session = self._build_session()

    # ── Public interface ──────────────────────────────────────────────────────

    def get_klines(self, symbol: str, interval: str, limit: int = 300) -> list[Candle]:
        params: dict[str, Any] = {
            "category": _CATEGORY,
            "symbol":   symbol.upper(),
            "interval": _bybit_interval(interval),
            "limit":    min(limit, 1000),
        }
        data = self._request("/v5/market/kline", params)
        rows = data["result"]["list"]
        # Bybit returns newest-first — reverse to oldest-first
        return [_parse_kline_row(r) for r in reversed(rows)]

    def get_historical_klines(
        self,
        symbol: str,
        interval: str,
        start_time: int,
        end_time: int,
        limit: int = 1000,
    ) -> list[Candle]:
        params: dict[str, Any] = {
            "category": _CATEGORY,
            "symbol":   symbol.upper(),
            "interval": _bybit_interval(interval),
            "start":    start_time,
            "end":      end_time,
            "limit":    min(limit, 1000),
        }
        data = self._request("/v5/market/kline", params)
        rows = data["result"]["list"]
        return [_parse_kline_row(r) for r in reversed(rows)]

    def get_orderbook(self, symbol: str, depth: int = 20) -> OrderBook:
        params: dict[str, Any] = {
            "category": _CATEGORY,
            "symbol":   symbol.upper(),
            "limit":    min(depth, 200),
        }
        data = self._request("/v5/market/orderbook", params)
        result = data["result"]
        bids = [OrderBookLevel(price=float(b[0]), quantity=float(b[1])) for b in result["b"]]
        asks = [OrderBookLevel(price=float(a[0]), quantity=float(a[1])) for a in result["a"]]
        return OrderBook(timestamp=int(time.time() * 1000), bids=bids, asks=asks)

    def get_ticker_24h(self, symbol: str) -> Ticker24h:
        params: dict[str, Any] = {"category": _CATEGORY, "symbol": symbol.upper()}
        data = self._request("/v5/market/tickers", params)
        t = data["result"]["list"][0]
        last_price = float(t["lastPrice"])
        prev_price = float(t.get("prevPrice24h") or last_price)
        price_change = last_price - prev_price
        # Bybit returns price24hPcnt as decimal e.g. 0.035 means 3.5%
        price_change_pct = float(t.get("price24hPcnt", 0)) * 100.0
        return Ticker24h(
            timestamp=int(time.time() * 1000),
            symbol=str(t["symbol"]),
            price_change=price_change,
            price_change_pct=price_change_pct,
            last_price=last_price,
            volume=float(t.get("volume24h", 0.0)),
            quote_volume=float(t.get("turnover24h", 0.0)),
            high_24h=float(t.get("highPrice24h", last_price)),
            low_24h=float(t.get("lowPrice24h", last_price)),
            open_price=prev_price,
        )

    def get_recent_trades(self, symbol: str, limit: int = 100) -> list[dict]:
        params: dict[str, Any] = {
            "category": _CATEGORY,
            "symbol":   symbol.upper(),
            "limit":    min(limit, 1000),
        }
        data = self._request("/v5/market/recent-trade", params)
        trades = []
        for t in data["result"]["list"]:
            price = float(t["price"])
            qty   = float(t["size"])
            trades.append({
                "id":             str(t.get("execId", "")),
                "price":          price,
                "qty":            qty,
                "quote_qty":      price * qty,
                "time":           int(t["time"]),
                "is_buyer_maker": t.get("side", "").lower() == "sell",
                "is_best_match":  True,
            })
        return trades

    # ── Internal ──────────────────────────────────────────────────────────────

    def _request(self, path: str, params: dict[str, Any]) -> dict:
        url = f"{_BASE_URL}{path}"
        last_exc: Optional[Exception] = None
        for attempt in range(self._max_retries + 1):
            try:
                resp = self._session.get(url, params=params, timeout=self._timeout)
                resp.raise_for_status()
                body = resp.json()
                ret_code = body.get("retCode", -1)
                if ret_code != 0:
                    raise RuntimeError(
                        f"Bybit API error code={ret_code} msg={body.get('retMsg')}"
                    )
                return body
            except Exception as exc:
                last_exc = exc
                if attempt < self._max_retries:
                    time.sleep(1.0 * (2 ** attempt))
                    continue
        raise last_exc  # type: ignore[misc]

    @staticmethod
    def _build_session() -> requests.Session:
        session = requests.Session()
        adapter = HTTPAdapter(
            max_retries=Retry(total=0),
            pool_connections=4,
            pool_maxsize=8,
        )
        session.mount("https://", adapter)
        session.headers.update({
            "Accept":     "application/json",
            "User-Agent": "trading-bot/1.0",
        })
        return session
