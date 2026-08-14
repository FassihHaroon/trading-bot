"""
Bybit V5 futures connector — drop-in replacement for BinanceFuturesClient.

Uses Bybit's public V5 API (no API key required for market data).
Implements the same interface as BinanceFuturesClient so MarketDataService
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
from data.schemas import Candle, FuturesData

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.bybit.com"

# Binance period string → Bybit period
_PERIOD_MAP: dict[str, str] = {
    "5m":  "5min",
    "15m": "15min",
    "30m": "30min",
    "1h":  "1h",
    "2h":  "4h",
    "4h":  "4h",
    "6h":  "4h",
    "12h": "1d",
    "1d":  "1d",
}

# Binance interval → Bybit interval (for klines)
_INTERVAL_MAP: dict[str, str] = {
    "1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30",
    "1h": "60", "2h": "120", "4h": "240", "6h": "360", "12h": "720",
    "1d": "D", "1w": "W", "1M": "M",
}


class BybitFuturesClient:
    """
    Drop-in replacement for BinanceFuturesClient using the Bybit V5 public API.
    Accepts BinanceConfig for timeout/retry settings; Bybit needs no API key.
    """

    def __init__(self, config: BinanceConfig) -> None:
        self._timeout = getattr(config, "request_timeout", 10)
        self._max_retries = getattr(config, "max_retries", 3)
        self._session = self._build_session()

    # ── Public interface ──────────────────────────────────────────────────────

    def get_klines(self, symbol: str, interval: str, limit: int = 300) -> list[Candle]:
        from data.connectors.bybit_spot import BybitSpotClient, _parse_kline_row, _bybit_interval
        params: dict[str, Any] = {
            "category": "linear",
            "symbol":   symbol.upper(),
            "interval": _bybit_interval(interval),
            "limit":    min(limit, 1000),
        }
        data = self._request("/v5/market/kline", params)
        rows = data["result"]["list"]
        return [_parse_kline_row(r) for r in reversed(rows)]

    def get_open_interest(self, symbol: str) -> dict:
        """Return open_interest (base units) and open_interest_value (USDT)."""
        params: dict[str, Any] = {"category": "linear", "symbol": symbol.upper()}
        data = self._request("/v5/market/tickers", params)
        t = data["result"]["list"][0]
        return {
            "open_interest":       float(t.get("openInterest", 0.0)),
            "open_interest_value": float(t.get("openInterestValue", 0.0)),
        }

    def get_funding_rate(self, symbol: str) -> dict:
        """Return the latest funding_rate and next_funding_time."""
        params: dict[str, Any] = {
            "category": "linear",
            "symbol":   symbol.upper(),
            "limit":    1,
        }
        data = self._request("/v5/market/funding/history", params)
        entries = data["result"]["list"]
        if not entries:
            return {"funding_rate": 0.0, "next_funding_time": 0}
        entry = entries[0]
        return {
            "funding_rate":      float(entry.get("fundingRate", 0.0)),
            "next_funding_time": int(entry.get("fundingRateTimestamp", 0)),
        }

    def get_long_short_ratio(self, symbol: str, period: str = "5m") -> Optional[float]:
        """
        Fetch the global long/short account ratio.
        Bybit returns buyRatio / sellRatio as decimals summing to 1.
        Returns buyRatio / sellRatio (>1 = more longs), or None on failure.
        """
        bybit_period = _PERIOD_MAP.get(period, "1h")
        params: dict[str, Any] = {
            "category": "linear",
            "symbol":   symbol.upper(),
            "period":   bybit_period,
            "limit":    1,
        }
        try:
            data = self._request("/v5/market/account-ratio", params)
            entries = data["result"]["list"]
            if not entries:
                return None
            e = entries[0]
            buy  = float(e.get("buyRatio",  0.5))
            sell = float(e.get("sellRatio", 0.5))
            return buy / sell if sell > 0 else None
        except Exception as exc:
            logger.debug("get_long_short_ratio unavailable for %s: %s", symbol, exc)
            return None

    def get_liquidation_data(self, symbol: str) -> dict:
        """Bybit doesn't expose REST liquidation history; return zeros."""
        return {"long_24h": 0.0, "short_24h": 0.0}

    def get_taker_buy_sell_ratio(self, symbol: str, period: str = "5m") -> Optional[float]:
        """Bybit doesn't have a direct taker ratio REST endpoint; return None."""
        return None

    def get_futures_data(self, symbol: str) -> FuturesData:
        """Aggregate all futures metrics into a FuturesData object."""
        now_ms = int(time.time() * 1000)

        try:
            oi_data = self.get_open_interest(symbol)
            open_interest       = oi_data["open_interest"]
            open_interest_value = oi_data["open_interest_value"]
        except Exception as exc:
            logger.error("get_open_interest failed for %s: %s", symbol, exc)
            open_interest = 0.0
            open_interest_value = 0.0

        try:
            fr_data = self.get_funding_rate(symbol)
            funding_rate      = fr_data["funding_rate"]
            next_funding_time = fr_data["next_funding_time"]
        except Exception as exc:
            logger.error("get_funding_rate failed for %s: %s", symbol, exc)
            funding_rate = 0.0
            next_funding_time = 0

        try:
            long_short_ratio = self.get_long_short_ratio(symbol)
        except Exception as exc:
            logger.warning("get_long_short_ratio failed for %s: %s", symbol, exc)
            long_short_ratio = None

        return FuturesData(
            timestamp=now_ms,
            open_interest=open_interest,
            open_interest_value=open_interest_value,
            funding_rate=funding_rate,
            next_funding_time=next_funding_time,
            long_short_ratio=long_short_ratio,
            top_trader_long_short_ratio=None,
            liquidation_24h_long=None,
            liquidation_24h_short=None,
            taker_buy_sell_ratio=None,
        )

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
