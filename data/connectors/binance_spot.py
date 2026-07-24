"""
Binance Spot REST API connector.

Fetches OHLCV (klines), order book, 24-hour ticker, and recent trades.
Includes rate-limit tracking, exponential-backoff retry on 429 / 5xx,
and structured exception types.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config.settings import BinanceConfig
from data.schemas import Candle, OrderBook, OrderBookLevel, Ticker24h

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Custom exceptions
# ─────────────────────────────────────────────


class RateLimitError(Exception):
    """Raised when the Binance rate limit has been hit (HTTP 429 / 418)."""

    def __init__(self, retry_after: float = 60.0) -> None:
        self.retry_after = retry_after
        super().__init__(f"Rate limit exceeded. Retry after {retry_after:.1f}s.")


class APIError(Exception):
    """Raised for any non-rate-limit HTTP error returned by Binance."""

    def __init__(self, status_code: int, code: int | None, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(f"Binance API error {status_code} (code={code}): {message}")


class DataError(Exception):
    """Raised when the response payload cannot be parsed into the expected schema."""


# ─────────────────────────────────────────────
# Rate-limit tracker
# ─────────────────────────────────────────────


class _RateLimiter:
    """
    Simple sliding-window request counter.

    Tracks how many requests have been sent in the current 60-second window
    and sleeps proactively when approaching the configured limit.
    """

    _WINDOW_SECONDS: float = 60.0
    _SAFETY_HEADROOM: float = 0.90  # sleep when we reach 90 % of the limit

    def __init__(self, requests_per_minute: int) -> None:
        self._limit = requests_per_minute
        self._threshold = int(requests_per_minute * self._SAFETY_HEADROOM)
        self._window_start: float = time.monotonic()
        self._count: int = 0

    # ------------------------------------------------------------------
    def check_and_increment(self) -> None:
        """
        Increment the request counter; sleep for the remainder of the
        current window if the safety threshold is reached.
        """
        now = time.monotonic()
        elapsed = now - self._window_start

        if elapsed >= self._WINDOW_SECONDS:
            # Start a fresh window
            self._window_start = now
            self._count = 0

        self._count += 1

        if self._count >= self._threshold:
            sleep_for = self._WINDOW_SECONDS - (time.monotonic() - self._window_start)
            if sleep_for > 0:
                logger.debug(
                    "Rate limiter: %d/%d requests used — sleeping %.2fs to reset window.",
                    self._count,
                    self._limit,
                    sleep_for,
                )
                time.sleep(sleep_for)
            # Reset after sleeping
            self._window_start = time.monotonic()
            self._count = 0


# ─────────────────────────────────────────────
# Main client
# ─────────────────────────────────────────────


class BinanceSpotClient:
    """
    Synchronous Binance Spot REST API client.

    Parameters
    ----------
    config:
        A ``BinanceConfig`` instance (from ``config.settings``).
    """

    _KLINE_FIELDS: int = 12  # Binance returns 12 elements per kline

    def __init__(self, config: BinanceConfig) -> None:
        self._config = config
        self._base_url = (
            "https://testnet.binance.vision"
            if config.testnet
            else config.base_url
        )
        self._rate_limiter = _RateLimiter(config.rate_limit_per_minute)
        self._session = self._build_session(config)
        logger.debug(
            "BinanceSpotClient initialised — base_url=%s testnet=%s",
            self._base_url,
            config.testnet,
        )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get_klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 300,
    ) -> list[Candle]:
        """
        Fetch OHLCV klines (candlestick data) for *symbol* at *interval*.

        Parameters
        ----------
        symbol:
            Trading pair, e.g. ``"BTCUSDT"``.
        interval:
            Binance interval string, e.g. ``"15m"``, ``"1h"``, ``"4h"``, ``"1d"``.
        limit:
            Number of candles to return (max 1000).

        Returns
        -------
        list[Candle]
            Oldest-first list of parsed candles.
        """
        logger.debug("get_klines symbol=%s interval=%s limit=%d", symbol, interval, limit)
        params: dict[str, Any] = {
            "symbol": symbol.upper(),
            "interval": interval,
            "limit": min(limit, 1000),
        }
        raw: list = self._request("GET", "/api/v3/klines", params)
        if not isinstance(raw, list):
            raise DataError(f"Expected list from /api/v3/klines, got {type(raw).__name__}")
        candles = [self._parse_kline(k) for k in raw]
        logger.debug("get_klines returned %d candles", len(candles))
        return candles

    def get_historical_klines(
        self,
        symbol: str,
        interval: str,
        start_time: int,
        end_time: int,
        limit: int = 1000,
    ) -> list[Candle]:
        """
        Fetch historical klines with a specific time range.

        Always uses mainnet (api.binance.com) — testnet has no historical data.
        Historical candlestick data is public; no API key needed.

        Parameters
        ----------
        start_time : int
            Start timestamp in milliseconds (inclusive).
        end_time : int
            End timestamp in milliseconds (inclusive).
        limit : int
            Max candles per request (Binance cap: 1000).
        """
        logger.debug(
            "get_historical_klines symbol=%s interval=%s start=%d end=%d limit=%d",
            symbol, interval, start_time, end_time, limit,
        )
        params: dict[str, Any] = {
            "symbol": symbol.upper(),
            "interval": interval,
            "startTime": start_time,
            "endTime": end_time,
            "limit": min(limit, 1000),
        }
        # Use mainnet URL directly — testnet has no historical data
        url = "https://api.binance.com/api/v3/klines"
        resp = self._session.get(url, params=params, timeout=self._config.request_timeout)
        resp.raise_for_status()
        raw = resp.json()
        if not isinstance(raw, list):
            raise DataError(f"Expected list from /api/v3/klines, got {type(raw).__name__}")
        candles = [self._parse_kline(k) for k in raw]
        logger.debug("get_historical_klines returned %d candles", len(candles))
        return candles

    def get_orderbook(self, symbol: str, depth: int = 20) -> OrderBook:
        """
        Fetch the current order book snapshot for *symbol*.

        Parameters
        ----------
        symbol:
            Trading pair, e.g. ``"BTCUSDT"``.
        depth:
            Number of price levels to retrieve per side (5 / 10 / 20 / 50 /
            100 / 500 / 1000).  Values not in the allowed set are rounded up
            by Binance automatically.

        Returns
        -------
        OrderBook
        """
        logger.debug("get_orderbook symbol=%s depth=%d", symbol, depth)
        params: dict[str, Any] = {"symbol": symbol.upper(), "limit": depth}
        raw: dict = self._request("GET", "/api/v3/depth", params)
        if not isinstance(raw, dict):
            raise DataError(f"Expected dict from /api/v3/depth, got {type(raw).__name__}")
        return self._parse_orderbook(raw)

    def get_ticker_24h(self, symbol: str) -> Ticker24h:
        """
        Fetch the 24-hour rolling-window price change statistics for *symbol*.

        Parameters
        ----------
        symbol:
            Trading pair, e.g. ``"BTCUSDT"``.

        Returns
        -------
        Ticker24h
        """
        logger.debug("get_ticker_24h symbol=%s", symbol)
        params: dict[str, Any] = {"symbol": symbol.upper()}
        raw: dict = self._request("GET", "/api/v3/ticker/24hr", params)
        if not isinstance(raw, dict):
            raise DataError(
                f"Expected dict from /api/v3/ticker/24hr, got {type(raw).__name__}"
            )
        try:
            return Ticker24h(
                timestamp=int(raw["closeTime"]),
                symbol=str(raw["symbol"]),
                price_change=float(raw["priceChange"]),
                price_change_pct=float(raw["priceChangePercent"]),
                last_price=float(raw["lastPrice"]),
                volume=float(raw["volume"]),
                quote_volume=float(raw["quoteVolume"]),
                high_24h=float(raw["highPrice"]),
                low_24h=float(raw["lowPrice"]),
                open_price=float(raw["openPrice"]),
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise DataError(f"Failed to parse Ticker24h: {exc}") from exc

    def get_recent_trades(self, symbol: str, limit: int = 100) -> list[dict]:
        """
        Fetch the most recent trades for *symbol*.

        Parameters
        ----------
        symbol:
            Trading pair, e.g. ``"BTCUSDT"``.
        limit:
            Number of trades to return (max 1000).

        Returns
        -------
        list[dict]
            Each dict contains keys: ``id``, ``price``, ``qty``,
            ``quoteQty``, ``time``, ``isBuyerMaker``, ``isBestMatch``.
        """
        logger.debug("get_recent_trades symbol=%s limit=%d", symbol, limit)
        params: dict[str, Any] = {
            "symbol": symbol.upper(),
            "limit": min(limit, 1000),
        }
        raw: list = self._request("GET", "/api/v3/trades", params)
        if not isinstance(raw, list):
            raise DataError(
                f"Expected list from /api/v3/trades, got {type(raw).__name__}"
            )
        logger.debug("get_recent_trades returned %d trades", len(raw))
        # Normalise numeric strings so callers always get Python numbers.
        normalised: list[dict] = []
        for t in raw:
            try:
                normalised.append(
                    {
                        "id": int(t["id"]),
                        "price": float(t["price"]),
                        "qty": float(t["qty"]),
                        "quote_qty": float(t["quoteQty"]),
                        "time": int(t["time"]),
                        "is_buyer_maker": bool(t["isBuyerMaker"]),
                        "is_best_match": bool(t["isBestMatch"]),
                    }
                )
            except (KeyError, ValueError, TypeError) as exc:
                raise DataError(f"Failed to parse trade record: {exc}") from exc
        return normalised

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """
        Execute an HTTP request against the Binance REST API with:

        * Proactive rate-limit tracking (sleep before hitting the cap).
        * Exponential-backoff retry on HTTP 429, 418, and 5xx responses
          (up to ``config.max_retries`` attempts).

        Parameters
        ----------
        method:
            HTTP verb — ``"GET"`` or ``"POST"``.
        path:
            API path, e.g. ``"/api/v3/klines"``.
        params:
            Query parameters (for GET) or body parameters (for POST).

        Returns
        -------
        Any
            Parsed JSON payload.

        Raises
        ------
        RateLimitError
            When the server returns 429 / 418 and all retries are exhausted.
        APIError
            For any other non-2xx response after retries are exhausted.
        DataError
            If the response body cannot be decoded as JSON.
        """
        url = f"{self._base_url}{path}"
        max_retries = self._config.max_retries
        delay = self._config.retry_delay

        for attempt in range(max_retries + 1):
            self._rate_limiter.check_and_increment()
            logger.debug(
                "_request attempt=%d method=%s url=%s params=%s",
                attempt + 1,
                method,
                url,
                params,
            )
            try:
                response = self._session.request(
                    method=method,
                    url=url,
                    params=params if method.upper() == "GET" else None,
                    json=params if method.upper() != "GET" else None,
                    timeout=self._config.request_timeout,
                )
            except requests.exceptions.Timeout as exc:
                logger.warning("Request timed out (attempt %d): %s", attempt + 1, exc)
                if attempt < max_retries:
                    self._sleep_backoff(delay, attempt)
                    continue
                raise APIError(0, None, f"Request timed out after {max_retries} retries") from exc
            except requests.exceptions.ConnectionError as exc:
                logger.warning("Connection error (attempt %d): %s", attempt + 1, exc)
                if attempt < max_retries:
                    self._sleep_backoff(delay, attempt)
                    continue
                raise APIError(0, None, f"Connection error after {max_retries} retries") from exc

            status = response.status_code

            # ── Rate limit responses ──────────────────────────────
            if status in (429, 418):
                retry_after = float(
                    response.headers.get("Retry-After", delay * (2 ** attempt))
                )
                logger.warning(
                    "Rate limit hit HTTP %d — retry_after=%.1fs (attempt %d/%d)",
                    status,
                    retry_after,
                    attempt + 1,
                    max_retries + 1,
                )
                if attempt < max_retries:
                    time.sleep(retry_after)
                    continue
                raise RateLimitError(retry_after=retry_after)

            # ── Server errors ─────────────────────────────────────
            if 500 <= status < 600:
                logger.warning(
                    "Server error HTTP %d (attempt %d/%d): %s",
                    status,
                    attempt + 1,
                    max_retries + 1,
                    response.text[:200],
                )
                if attempt < max_retries:
                    self._sleep_backoff(delay, attempt)
                    continue
                raise APIError(status, None, f"Server error after {max_retries} retries: {response.text[:200]}")

            # ── Client errors (non-rate-limit) ────────────────────
            if not response.ok:
                try:
                    body = response.json()
                    code = body.get("code")
                    msg = body.get("msg", response.text[:200])
                except Exception:
                    code = None
                    msg = response.text[:200]
                logger.error(
                    "API error HTTP %d code=%s msg=%s",
                    status,
                    code,
                    msg,
                )
                raise APIError(status, code, msg)

            # ── Success ───────────────────────────────────────────
            try:
                data = response.json()
            except ValueError as exc:
                raise DataError(
                    f"Could not decode JSON from {url}: {response.text[:200]}"
                ) from exc

            logger.debug("_request success status=%d", status)
            return data

        # Should never be reached, but satisfies the type checker.
        raise APIError(0, None, "Exhausted retries without a conclusive response")

    def _parse_kline(self, raw: list) -> Candle:
        """
        Normalise a raw Binance kline list into a ``Candle`` dataclass.

        Binance kline element layout (index → field):
        0  open_time (ms)
        1  open
        2  high
        3  low
        4  close
        5  volume
        6  close_time (ms)
        7  quote_asset_volume
        8  number_of_trades
        9  taker_buy_base_asset_volume
        10 taker_buy_quote_asset_volume
        11 ignore
        """
        if not isinstance(raw, (list, tuple)) or len(raw) < self._KLINE_FIELDS:
            raise DataError(
                f"Kline record too short: expected {self._KLINE_FIELDS} fields, got {len(raw) if isinstance(raw, (list, tuple)) else type(raw).__name__}"
            )
        try:
            return Candle(
                timestamp=int(raw[0]),
                open=float(raw[1]),
                high=float(raw[2]),
                low=float(raw[3]),
                close=float(raw[4]),
                volume=float(raw[5]),
                quote_volume=float(raw[7]),
                trades=int(raw[8]),
                taker_buy_volume=float(raw[9]),
                taker_buy_quote_volume=float(raw[10]),
            )
        except (ValueError, TypeError, IndexError) as exc:
            raise DataError(f"Failed to parse kline record: {exc}") from exc

    def _parse_orderbook(self, raw: dict) -> OrderBook:
        """
        Normalise the raw Binance depth payload into an ``OrderBook`` dataclass.

        The raw payload has the form::

            {
                "lastUpdateId": 12345,
                "bids": [["43200.00", "1.500"], ...],
                "asks": [["43201.00", "0.750"], ...],
            }
        """
        try:
            bids = [
                OrderBookLevel(price=float(level[0]), quantity=float(level[1]))
                for level in raw["bids"]
            ]
            asks = [
                OrderBookLevel(price=float(level[0]), quantity=float(level[1]))
                for level in raw["asks"]
            ]
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise DataError(f"Failed to parse order book: {exc}") from exc

        return OrderBook(
            timestamp=int(time.time() * 1000),
            bids=bids,
            asks=asks,
        )

    # ------------------------------------------------------------------
    # Session factory
    # ------------------------------------------------------------------

    @staticmethod
    def _build_session(config: BinanceConfig) -> requests.Session:
        """
        Build a ``requests.Session`` with connection pooling.

        Note: Transport-level retries (via ``urllib3``) are disabled here
        because the ``_request`` method implements its own application-level
        retry logic with rate-limit awareness and exponential backoff.
        """
        session = requests.Session()
        # Mount an adapter with connection pooling but no automatic retries
        # (retry decisions are made inside _request).
        adapter = HTTPAdapter(
            max_retries=Retry(total=0),  # no silent urllib3 retries
            pool_connections=4,
            pool_maxsize=8,
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)

        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if config.api_key:
            headers["X-MBX-APIKEY"] = config.api_key

        session.headers.update(headers)
        return session

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _sleep_backoff(base_delay: float, attempt: int) -> None:
        """Sleep for ``base_delay * 2^attempt`` seconds."""
        sleep_for = base_delay * (2 ** attempt)
        logger.debug("Backoff sleep %.2fs before retry attempt %d", sleep_for, attempt + 2)
        time.sleep(sleep_for)
