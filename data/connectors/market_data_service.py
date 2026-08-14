"""
MarketDataService — facade that orchestrates spot + futures clients and the
snapshot cache to produce a complete, validated MarketSnapshot.

Usage
-----
    from config.settings import AgentConfig
    from data.connectors.market_data_service import MarketDataService

    cfg = AgentConfig()
    svc = MarketDataService(cfg)
    svc.warmup("BTCUSDT")
    snapshot = svc.get_snapshot("BTCUSDT")
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from config.settings import AgentConfig
from data.schemas import (
    Candle,
    FuturesData,
    MarketSnapshot,
    OrderBook,
    Ticker24h,
)
from data.cache import MarketDataCache as SnapshotCache
from data.connectors.bybit_spot import BybitSpotClient as BinanceSpotClient
from data.connectors.bybit_futures import BybitFuturesClient as BinanceFuturesClient

logger = logging.getLogger(__name__)


class MarketDataService:
    """
    Single entry-point for all market data consumed by the analysis pipeline.

    Responsibilities
    ----------------
    - Coordinate BinanceSpotClient and BinanceFuturesClient calls.
    - Fan-out multi-timeframe candle fetches in parallel via ThreadPoolExecutor.
    - Serve fresh MarketSnapshot objects, backed by a TTL cache.
    - Collect per-fetch errors into the snapshot rather than raising, so the
      pipeline can continue with partial data.
    - Provide a paginated historical candle fetcher for the backtesting layer.
    """

    def __init__(self, config: AgentConfig) -> None:
        self._config = config
        self._spot = BinanceSpotClient(config.binance)
        self._futures = BinanceFuturesClient(config.binance) if config.data.use_futures else None
        self._cache = SnapshotCache()
        self._cache_ttl = config.data.cache_ttl_seconds
        logger.info(
            "MarketDataService initialised | timeframes=%s | use_futures=%s | "
            "cache_ttl=%ds",
            config.data.timeframes,
            config.data.use_futures,
            config.data.cache_ttl_seconds,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_snapshot(self, symbol: str) -> MarketSnapshot:
        """
        Return a complete MarketSnapshot for *symbol*.

        Steps
        -----
        1. Check the TTL cache — return immediately on a hit.
        2. Fetch candles for every configured timeframe in parallel.
        3. Fetch the level-2 order book.
        4. Fetch the 24-hour ticker.
        5. Optionally fetch FuturesData (funding rate, open interest, …).
        6. Assemble a MarketSnapshot, annotate errors, mark completeness.
        7. Store the snapshot in the cache.
        8. Return the snapshot with fetch_duration_ms populated.
        """
        cached = self._cache.get_snapshot(symbol)
        if cached is not None:
            logger.debug("Cache hit for %s (age<ttl)", symbol)
            return cached

        t_start = time.monotonic()
        errors: list[str] = []
        now_ms = int(time.time() * 1000)

        candles: dict[str, list[Candle]] = {}
        order_book: Optional[OrderBook] = None
        ticker: Optional[Ticker24h] = None
        futures_data: Optional[FuturesData] = None

        # ── 1. Multi-timeframe candles ────────────────────────────────
        candles, tf_errors = self._fetch_all_timeframes(symbol)
        errors.extend(tf_errors)

        # ── 2. Order book ─────────────────────────────────────────────
        try:
            order_book = self._spot.get_orderbook(
                symbol, depth=self._config.data.orderbook_depth
            )
            logger.debug("Order book fetched for %s (%d bids)", symbol, len(order_book.bids))
        except Exception as exc:
            msg = f"order_book: {exc}"
            logger.warning("Failed to fetch %s — %s", msg, symbol)
            errors.append(msg)

        # ── 3. 24h ticker ─────────────────────────────────────────────
        try:
            ticker = self._spot.get_ticker_24h(symbol)
            logger.debug(
                "Ticker fetched for %s | last=%.4f change=%.2f%%",
                symbol,
                ticker.last_price,
                ticker.price_change_pct,
            )
        except Exception as exc:
            msg = f"ticker_24h: {exc}"
            logger.warning("Failed to fetch %s — %s", msg, symbol)
            errors.append(msg)

        # ── 4. Futures data (optional) ────────────────────────────────
        if self._config.data.use_futures and self._futures is not None:
            try:
                futures_data = self._futures.get_futures_data(symbol)
                logger.debug(
                    "Futures data fetched for %s | OI=%.2f funding=%.6f",
                    symbol,
                    futures_data.open_interest,
                    futures_data.funding_rate,
                )
            except Exception as exc:
                msg = f"futures_data: {exc}"
                logger.warning("Failed to fetch %s — %s", msg, symbol)
                errors.append(msg)

        # ── 5. Assemble snapshot ──────────────────────────────────────
        fetch_duration_ms = (time.monotonic() - t_start) * 1000.0
        is_complete = len(errors) == 0

        snapshot = MarketSnapshot(
            symbol=symbol,
            timestamp=now_ms,
            candles=candles,
            order_book=order_book,
            ticker=ticker,
            futures=futures_data,
            fetch_duration_ms=round(fetch_duration_ms, 2),
            is_complete=is_complete,
            fetch_errors=errors,
        )

        # ── 6. Cache & return ─────────────────────────────────────────
        self._cache.set_snapshot(symbol, snapshot, ttl=self._cache_ttl)
        logger.info(
            "Snapshot assembled for %s | duration=%.1fms | complete=%s | errors=%d",
            symbol,
            fetch_duration_ms,
            is_complete,
            len(errors),
        )
        return snapshot

    def warmup(self, symbol: str) -> None:
        """
        Pre-fetch all data for *symbol* and populate the cache.

        Call this at agent startup to ensure the first trading cycle does not
        incur a cold-cache penalty.
        """
        logger.info("Warming up MarketDataService for %s …", symbol)
        try:
            snapshot = self.get_snapshot(symbol)
            logger.info(
                "Warmup complete for %s | timeframes=%s | complete=%s",
                symbol,
                list(snapshot.candles.keys()),
                snapshot.is_complete,
            )
        except Exception as exc:
            logger.error("Warmup failed for %s: %s", symbol, exc, exc_info=True)

    def get_historical_candles(
        self,
        symbol: str,
        timeframe: str,
        start_ts: int,
        end_ts: int,
    ) -> list[Candle]:
        """
        Fetch an arbitrary date range of candles by paginating the Binance
        klines endpoint.

        Parameters
        ----------
        symbol:
            Trading pair, e.g. ``"BTCUSDT"``.
        timeframe:
            Binance interval string, e.g. ``"1h"``, ``"4h"``, ``"1d"``.
        start_ts:
            Range start as a UNIX timestamp in **milliseconds**.
        end_ts:
            Range end as a UNIX timestamp in **milliseconds** (inclusive).

        Returns
        -------
        list[Candle]
            Candles sorted ascending by timestamp.  Candles whose open
            timestamp falls outside [start_ts, end_ts] are excluded.
        """
        logger.info(
            "Fetching historical candles: symbol=%s tf=%s start=%d end=%d",
            symbol,
            timeframe,
            start_ts,
            end_ts,
        )

        all_candles: list[Candle] = []
        page_start = start_ts
        # Binance allows at most 1 000 candles per request.
        page_limit = 1000

        while page_start < end_ts:
            try:
                page = self._spot.get_historical_klines(
                    symbol=symbol,
                    interval=timeframe,
                    start_time=page_start,
                    end_time=end_ts,
                    limit=page_limit,
                )
            except Exception as exc:
                logger.error(
                    "Historical klines fetch failed for %s %s at page_start=%d: %s",
                    symbol,
                    timeframe,
                    page_start,
                    exc,
                    exc_info=True,
                )
                break

            if not page:
                logger.debug(
                    "Empty page returned for %s %s at page_start=%d — stopping.",
                    symbol,
                    timeframe,
                    page_start,
                )
                break

            all_candles.extend(page)
            last_ts = page[-1].timestamp

            logger.debug(
                "Fetched %d candles for %s %s | page_start=%d last_ts=%d",
                len(page),
                symbol,
                timeframe,
                page_start,
                last_ts,
            )

            if last_ts >= end_ts or len(page) < page_limit:
                # We have reached the end of the requested range, or the
                # exchange returned a partial page (no more data available).
                break

            # Advance past the last returned candle to avoid duplicates.
            page_start = last_ts + 1

        # Deduplicate (safety net for overlapping pagination edges) and sort.
        seen: set[int] = set()
        unique: list[Candle] = []
        for c in all_candles:
            if c.timestamp not in seen:
                seen.add(c.timestamp)
                unique.append(c)

        unique.sort(key=lambda c: c.timestamp)

        logger.info(
            "Historical fetch complete: %s %s | %d candles returned",
            symbol,
            timeframe,
            len(unique),
        )
        return unique

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch_all_timeframes(
        self, symbol: str
    ) -> tuple[dict[str, list[Candle]], list[str]]:
        """
        Fetch candles for every timeframe in ``config.data.timeframes`` using
        a ThreadPoolExecutor so that network round-trips overlap.

        Returns
        -------
        tuple[dict[str, list[Candle]], list[str]]
            ``(candles_by_timeframe, error_messages)``
        """
        timeframes: list[str] = self._config.data.timeframes
        lookback: int = self._config.data.lookback_bars
        candles: dict[str, list[Candle]] = {}
        errors: list[str] = []

        logger.debug(
            "Fetching %d timeframes for %s with lookback=%d",
            len(timeframes),
            symbol,
            lookback,
        )

        with ThreadPoolExecutor(
            max_workers=min(len(timeframes), 8),
            thread_name_prefix="mds-tf",
        ) as pool:
            future_to_tf = {
                pool.submit(
                    self._spot.get_klines,
                    symbol,
                    tf,
                    lookback,
                ): tf
                for tf in timeframes
            }

            for future in as_completed(future_to_tf):
                tf = future_to_tf[future]
                try:
                    result = future.result()
                    candles[tf] = result
                    logger.debug(
                        "Timeframe %s: %d candles fetched for %s",
                        tf,
                        len(result),
                        symbol,
                    )
                except Exception as exc:
                    msg = f"candles[{tf}]: {exc}"
                    logger.warning(
                        "Failed to fetch candles for %s %s: %s", symbol, tf, exc
                    )
                    errors.append(msg)

        return candles, errors
