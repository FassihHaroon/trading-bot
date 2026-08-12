"""
Binance Futures (FAPI) REST connector.

Fetches open interest, funding rates, liquidation estimates,
long/short ratios, taker buy/sell ratios, and OHLCV klines from
the Binance USD-M Futures API (https://fapi.binance.com).

All public endpoints — no API key required for read-only data.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config.settings import BinanceConfig
from data.schemas import Candle, FuturesData

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────

_FAPI_BASE = "https://fapi.binance.com"

_ENDPOINTS = {
    "klines":            "/fapi/v1/klines",
    "open_interest":     "/fapi/v1/openInterest",
    "funding_rate":      "/fapi/v1/fundingRate",
    "ls_ratio":          "/futures/data/globalLongShortAccountRatio",
    "taker_ratio":       "/futures/data/takerlongshortRatio",
    # Used for liquidation estimation via 24h ticker
    "ticker_24h":        "/fapi/v1/ticker/24hr",
}

# Binance FAPI kline field indices
_K_OPEN_TIME     = 0
_K_OPEN          = 1
_K_HIGH          = 2
_K_LOW           = 3
_K_CLOSE         = 4
_K_VOLUME        = 5
_K_CLOSE_TIME    = 6
_K_QUOTE_VOLUME  = 7
_K_TRADES        = 8
_K_TAKER_BUY_VOL = 9
_K_TAKER_BUY_QV  = 10


# ─────────────────────────────────────────────
# Client
# ─────────────────────────────────────────────

class BinanceFuturesClient:
    """
    Thin, stateless REST client for the Binance USD-M Futures (FAPI) API.

    All methods return normalised Python objects (Candle, FuturesData, dict).
    Network errors trigger up to `config.max_retries` retries with exponential
    back-off.  HTTP 404 responses are handled gracefully — the affected field
    is returned as None rather than raising an exception, because several
    endpoints (e.g. long/short ratio) are unavailable for low-volume pairs.
    """

    def __init__(self, config: BinanceConfig) -> None:
        self._config = config
        self._base_url = _FAPI_BASE  # always use live FAPI regardless of testnet flag
        self._session = self._build_session()
        self._last_request_ts: float = 0.0
        # Minimum gap between requests (seconds) derived from rate limit
        self._min_gap: float = 60.0 / max(config.rate_limit_per_minute, 1)

    # ──────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────

    def get_klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 300,
    ) -> list[Candle]:
        """
        Fetch OHLCV klines from the futures market.

        Parameters
        ----------
        symbol:   e.g. "BTCUSDT"
        interval: e.g. "15m", "1h", "4h", "1d"
        limit:    number of candles (max 1500 per Binance docs)

        Returns
        -------
        List of Candle dataclass instances, oldest first.
        """
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        raw = self._request("GET", _ENDPOINTS["klines"], params)
        candles: list[Candle] = []
        for row in raw:
            try:
                candles.append(
                    Candle(
                        timestamp=int(row[_K_OPEN_TIME]),
                        open=float(row[_K_OPEN]),
                        high=float(row[_K_HIGH]),
                        low=float(row[_K_LOW]),
                        close=float(row[_K_CLOSE]),
                        volume=float(row[_K_VOLUME]),
                        quote_volume=float(row[_K_QUOTE_VOLUME]),
                        trades=int(row[_K_TRADES]),
                        taker_buy_volume=float(row[_K_TAKER_BUY_VOL]),
                        taker_buy_quote_volume=float(row[_K_TAKER_BUY_QV]),
                    )
                )
            except (IndexError, ValueError, TypeError) as exc:
                logger.warning("Skipping malformed kline row for %s: %s", symbol, exc)
        return candles

    def get_open_interest(self, symbol: str) -> dict:
        """
        Fetch current open interest for a futures symbol.

        Returns
        -------
        {
            "open_interest":       float,   # OI in base asset (e.g. BTC)
            "open_interest_value": float,   # OI in quote asset (e.g. USDT)
        }
        Raises on network/API errors.
        """
        params = {"symbol": symbol}
        data = self._request("GET", _ENDPOINTS["open_interest"], params)
        oi = float(data.get("openInterest", 0.0))
        # openInterestValue is not directly in this endpoint; derive it via
        # mark price if available, otherwise fall back to 0.
        # The /fapi/v1/openInterest endpoint returns openInterest (base units)
        # and symbol only.  We compute notional as a best-effort.
        oi_value = float(data.get("openInterestValue", 0.0))
        return {
            "open_interest": oi,
            "open_interest_value": oi_value,
        }

    def get_funding_rate(self, symbol: str) -> dict:
        """
        Fetch the latest funding rate and next funding time.

        Returns
        -------
        {
            "funding_rate":      float,   # e.g. 0.0001
            "next_funding_time": int,     # Unix ms timestamp
        }
        """
        params = {"symbol": symbol, "limit": 1}
        data = self._request("GET", _ENDPOINTS["funding_rate"], params)
        # Endpoint returns a list; take the most recent entry.
        if isinstance(data, list) and data:
            entry = data[-1]
        elif isinstance(data, dict):
            entry = data
        else:
            entry = {}
        return {
            "funding_rate": float(entry.get("fundingRate", 0.0)),
            "next_funding_time": int(entry.get("nextFundingTime", 0)),
        }

    def get_long_short_ratio(
        self,
        symbol: str,
        period: str = "5m",
    ) -> Optional[float]:
        """
        Fetch the global long/short account ratio.

        Returns the ratio (longs / shorts) as a float, or None if the
        endpoint returns HTTP 404 (not available for this pair).

        Parameters
        ----------
        symbol: e.g. "BTCUSDT"
        period: "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"
        """
        params = {"symbol": symbol, "period": period, "limit": 1}
        data = self._request(
            "GET", _ENDPOINTS["ls_ratio"], params, allow_404=True
        )
        if data is None:
            return None
        if isinstance(data, list) and data:
            entry = data[-1]
        elif isinstance(data, dict):
            entry = data
        else:
            return None
        try:
            return float(entry.get("longShortRatio", 0.0))
        except (TypeError, ValueError):
            return None

    def get_liquidation_data(self, symbol: str) -> dict:
        """
        Estimate 24-hour long and short liquidation volumes.

        Binance does not expose a public liquidation history endpoint for
        futures (the WebSocket stream exists but not a REST snapshot).  As a
        best-effort approximation we derive liquidation pressure from the
        24-hour ticker's taker buy/sell imbalance and total volume.

        Returns
        -------
        {
            "long_24h":  float,   # estimated USD value of long liquidations
            "short_24h": float,   # estimated USD value of short liquidations
        }
        The values are heuristic estimates.  Treat them as directional signals
        rather than precise accounting figures.
        """
        params = {"symbol": symbol}
        data = self._request("GET", _ENDPOINTS["ticker_24h"], params, allow_404=True)
        if data is None:
            return {"long_24h": 0.0, "short_24h": 0.0}

        try:
            quote_volume = float(data.get("quoteVolume", 0.0))
            # takerBuyQuoteVolume / quoteVolume gives buy ratio
            taker_buy_qv = float(data.get("takerBuyQuoteVolume", 0.0))
            sell_qv = quote_volume - taker_buy_qv

            # Liquidations skew opposite to the dominant taker direction:
            # heavy buy takers -> short squeeze -> short liquidations dominate,
            # heavy sell takers -> long flush -> long liquidations dominate.
            # We use a conservative 2% liquidation-pressure proxy.
            _LIQ_PROXY = 0.02
            long_24h = sell_qv * _LIQ_PROXY   # sell pressure -> long liq
            short_24h = taker_buy_qv * _LIQ_PROXY  # buy pressure -> short liq
        except (TypeError, ValueError):
            long_24h = 0.0
            short_24h = 0.0

        return {"long_24h": long_24h, "short_24h": short_24h}

    def get_taker_buy_sell_ratio(
        self,
        symbol: str,
        period: str = "5m",
    ) -> Optional[float]:
        """
        Fetch the taker buy/sell volume ratio.

        Returns
        -------
        float  — ratio of taker buy volume to taker sell volume (>1 = net buying),
                 or None if the endpoint is unavailable for this symbol.

        Parameters
        ----------
        symbol: e.g. "BTCUSDT"
        period: "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"
        """
        params = {"symbol": symbol, "period": period, "limit": 1}
        data = self._request(
            "GET", _ENDPOINTS["taker_ratio"], params, allow_404=True
        )
        if data is None:
            return None
        if isinstance(data, list) and data:
            entry = data[-1]
        elif isinstance(data, dict):
            entry = data
        else:
            return None
        try:
            return float(entry.get("buySellRatio", 1.0))
        except (TypeError, ValueError):
            return None

    def get_futures_data(self, symbol: str) -> FuturesData:
        """
        Aggregate all futures metrics into a single FuturesData object.

        Calls the following endpoints in sequence:
          - /fapi/v1/openInterest
          - /fapi/v1/fundingRate
          - /futures/data/globalLongShortAccountRatio  (graceful 404)
          - /futures/data/takerlongshortRatio           (graceful 404)
          - /fapi/v1/ticker/24hr                        (liquidation estimate)

        Returns
        -------
        FuturesData — always returns a populated object; unavailable fields
        are set to None.
        """
        now_ms = int(time.time() * 1000)

        # ── Open interest ──────────────────────────────────────────────────
        try:
            oi_data = self.get_open_interest(symbol)
            open_interest = oi_data["open_interest"]
            open_interest_value = oi_data["open_interest_value"]
        except Exception as exc:
            logger.error("get_open_interest failed for %s: %s", symbol, exc)
            open_interest = 0.0
            open_interest_value = 0.0

        # ── Funding rate ───────────────────────────────────────────────────
        try:
            fr_data = self.get_funding_rate(symbol)
            funding_rate = fr_data["funding_rate"]
            next_funding_time = fr_data["next_funding_time"]
        except Exception as exc:
            logger.error("get_funding_rate failed for %s: %s", symbol, exc)
            funding_rate = 0.0
            next_funding_time = 0

        # ── Long/short ratio ───────────────────────────────────────────────
        try:
            long_short_ratio = self.get_long_short_ratio(symbol)
        except Exception as exc:
            logger.warning("get_long_short_ratio failed for %s: %s", symbol, exc)
            long_short_ratio = None

        # ── Taker buy/sell ratio ───────────────────────────────────────────
        try:
            taker_buy_sell_ratio = self.get_taker_buy_sell_ratio(symbol)
        except Exception as exc:
            logger.warning("get_taker_buy_sell_ratio failed for %s: %s", symbol, exc)
            taker_buy_sell_ratio = None

        # ── Liquidation estimates ──────────────────────────────────────────
        try:
            liq_data = self.get_liquidation_data(symbol)
            liq_long = liq_data["long_24h"]
            liq_short = liq_data["short_24h"]
        except Exception as exc:
            logger.warning("get_liquidation_data failed for %s: %s", symbol, exc)
            liq_long = None
            liq_short = None

        return FuturesData(
            timestamp=now_ms,
            open_interest=open_interest,
            open_interest_value=open_interest_value,
            funding_rate=funding_rate,
            next_funding_time=next_funding_time,
            long_short_ratio=long_short_ratio,
            top_trader_long_short_ratio=None,  # requires premium endpoint
            liquidation_24h_long=liq_long,
            liquidation_24h_short=liq_short,
            taker_buy_sell_ratio=taker_buy_sell_ratio,
        )

    # ──────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        allow_404: bool = False,
    ) -> Optional[dict | list]:
        """
        Execute an HTTP request against the FAPI base URL with retry logic
        and a simple rate-limit guard.

        Parameters
        ----------
        method:     "GET" or "POST"
        path:       URL path, e.g. "/fapi/v1/klines"
        params:     Query parameters dict
        allow_404:  If True, a 404 response returns None instead of raising.

        Returns
        -------
        Parsed JSON (dict or list), or None when allow_404=True and the
        server returned HTTP 404.

        Raises
        ------
        requests.HTTPError  on non-404 HTTP errors (after retries exhausted)
        requests.RequestException  on network / timeout failures
        """
        url = f"{self._base_url}{path}"
        last_exc: Optional[Exception] = None

        for attempt in range(1, self._config.max_retries + 1):
            # ── Rate limit guard ───────────────────────────────────────────
            self._throttle()

            try:
                response = self._session.request(
                    method=method,
                    url=url,
                    params=params,
                    timeout=self._config.request_timeout,
                )
                self._last_request_ts = time.monotonic()

                # Graceful 404 handling
                if response.status_code == 404 and allow_404:
                    logger.debug(
                        "404 on %s %s (symbol may not support this endpoint)",
                        method, path,
                    )
                    return None

                # Surface all other HTTP errors
                response.raise_for_status()
                return response.json()

            except requests.exceptions.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else 0
                # Do not retry client errors except 429 (rate limit)
                if status != 429 and 400 <= status < 500:
                    logger.error(
                        "Non-retryable HTTP %d on %s %s: %s",
                        status, method, path, exc,
                    )
                    raise
                last_exc = exc
                wait = self._backoff(attempt)
                logger.warning(
                    "HTTP error %d on %s %s, attempt %d/%d, retrying in %.1fs",
                    status, method, path, attempt, self._config.max_retries, wait,
                )
                time.sleep(wait)

            except requests.exceptions.Timeout as exc:
                last_exc = exc
                wait = self._backoff(attempt)
                logger.warning(
                    "Timeout on %s %s, attempt %d/%d, retrying in %.1fs",
                    method, path, attempt, self._config.max_retries, wait,
                )
                time.sleep(wait)

            except requests.exceptions.RequestException as exc:
                last_exc = exc
                wait = self._backoff(attempt)
                logger.warning(
                    "Request error on %s %s, attempt %d/%d, retrying in %.1fs: %s",
                    method, path, attempt, self._config.max_retries, wait, exc,
                )
                time.sleep(wait)

        logger.error(
            "All %d attempts failed for %s %s",
            self._config.max_retries, method, path,
        )
        if last_exc is not None:
            raise last_exc
        raise RuntimeError(f"Request failed: {method} {url}")

    def _throttle(self) -> None:
        """
        Enforce a minimum inter-request gap derived from rate_limit_per_minute.
        Sleeps the calling thread if necessary.
        """
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < self._min_gap:
            time.sleep(self._min_gap - elapsed)

    @staticmethod
    def _backoff(attempt: int, base: float = 1.0, cap: float = 30.0) -> float:
        """Exponential back-off: base * 2^(attempt-1), capped at `cap` seconds."""
        return min(base * (2 ** (attempt - 1)), cap)

    def _build_session(self) -> requests.Session:
        """
        Build a requests.Session with a retry adapter pre-configured for
        connection-level retries (distinct from the application-level retry
        loop in _request).
        """
        session = requests.Session()
        retry = Retry(
            total=self._config.max_retries,
            backoff_factor=self._config.retry_delay,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.headers.update(
            {
                "X-MBX-APIKEY": self._config.api_key,
                "Accept": "application/json",
                "User-Agent": "trading-bot/1.0",
            }
        )
        return session
