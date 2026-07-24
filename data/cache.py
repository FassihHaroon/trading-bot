"""
TTL-based in-memory cache for market data.
Prevents redundant Binance API calls.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class _CacheEntry:
    value: Any
    expires_at: float

    def is_expired(self) -> bool:
        return time.monotonic() >= self.expires_at


class TTLCache:
    """Generic thread-safe key-value cache with per-entry TTL."""

    def __init__(self) -> None:
        self._store: dict[str, _CacheEntry] = {}
        self._lock = threading.Lock()
        self._hits: int = 0
        self._misses: int = 0

    def get(self, key: str) -> Optional[Any]:
        """Return cached value if present and not expired, else None."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                logger.debug("Cache miss: key=%r", key)
                return None
            if entry.is_expired():
                del self._store[key]
                self._misses += 1
                logger.debug("Cache expired: key=%r", key)
                return None
            self._hits += 1
            logger.debug("Cache hit: key=%r", key)
            return entry.value

    def set(self, key: str, value: Any, ttl_seconds: float) -> None:
        """Store value under key with given TTL in seconds."""
        if ttl_seconds <= 0:
            raise ValueError(f"ttl_seconds must be positive, got {ttl_seconds}")
        expires_at = time.monotonic() + ttl_seconds
        with self._lock:
            self._store[key] = _CacheEntry(value=value, expires_at=expires_at)
        logger.debug("Cache set: key=%r, ttl=%.1fs", key, ttl_seconds)

    def invalidate(self, key: str) -> bool:
        """Remove a single key. Returns True if it existed."""
        with self._lock:
            existed = key in self._store
            self._store.pop(key, None)
        if existed:
            logger.debug("Cache invalidated: key=%r", key)
        return existed

    def clear(self) -> None:
        """Remove all entries and reset counters."""
        with self._lock:
            self._store.clear()
            self._hits = 0
            self._misses = 0
        logger.debug("Cache cleared")

    def _evict_expired(self) -> int:
        """Remove all expired entries. Returns number evicted. Must be called under lock."""
        now = time.monotonic()
        expired_keys = [k for k, e in self._store.items() if now >= e.expires_at]
        for k in expired_keys:
            del self._store[k]
        return len(expired_keys)

    def stats(self) -> dict[str, Any]:
        """Return cache statistics."""
        with self._lock:
            evicted = self._evict_expired()
            size = len(self._store)
            hits = self._hits
            misses = self._misses

        total = hits + misses
        hit_rate = hits / total if total > 0 else 0.0
        miss_rate = misses / total if total > 0 else 0.0

        return {
            "size": size,
            "hits": hits,
            "misses": misses,
            "total_requests": total,
            "hit_rate": round(hit_rate, 4),
            "miss_rate": round(miss_rate, 4),
            "evicted_on_stats": evicted,
        }


@dataclass
class MarketDataCache:
    """
    Typed cache accessors for market data categories.
    Wraps a single TTLCache instance.
    """

    _cache: TTLCache = field(default_factory=TTLCache, init=False, repr=False)

    # ------------------------------------------------------------------
    # Internal key builders
    # ------------------------------------------------------------------

    @staticmethod
    def _candles_key(symbol: str, timeframe: str) -> str:
        return f"candles::{symbol}::{timeframe}"

    @staticmethod
    def _snapshot_key(symbol: str) -> str:
        return f"snapshot::{symbol}"

    @staticmethod
    def _orderbook_key(symbol: str) -> str:
        return f"orderbook::{symbol}"

    @staticmethod
    def _futures_key(symbol: str) -> str:
        return f"futures::{symbol}"

    # ------------------------------------------------------------------
    # Candles
    # ------------------------------------------------------------------

    def get_candles(self, symbol: str, timeframe: str) -> Optional[Any]:
        """Retrieve cached OHLCV candles for symbol/timeframe."""
        return self._cache.get(self._candles_key(symbol, timeframe))

    def set_candles(
        self,
        symbol: str,
        timeframe: str,
        candles: Any,
        ttl: float,
    ) -> None:
        """Cache OHLCV candles for symbol/timeframe with given TTL (seconds)."""
        self._cache.set(self._candles_key(symbol, timeframe), candles, ttl)

    def invalidate_candles(self, symbol: str, timeframe: str) -> bool:
        return self._cache.invalidate(self._candles_key(symbol, timeframe))

    # ------------------------------------------------------------------
    # Snapshots (ticker / 24h stats)
    # ------------------------------------------------------------------

    def get_snapshot(self, symbol: str) -> Optional[Any]:
        """Retrieve cached market snapshot for symbol."""
        return self._cache.get(self._snapshot_key(symbol))

    def set_snapshot(self, symbol: str, snapshot: Any, ttl: float) -> None:
        """Cache market snapshot for symbol with given TTL (seconds)."""
        self._cache.set(self._snapshot_key(symbol), snapshot, ttl)

    def invalidate_snapshot(self, symbol: str) -> bool:
        return self._cache.invalidate(self._snapshot_key(symbol))

    # ------------------------------------------------------------------
    # Order book
    # ------------------------------------------------------------------

    def get_orderbook(self, symbol: str) -> Optional[Any]:
        """Retrieve cached order book for symbol."""
        return self._cache.get(self._orderbook_key(symbol))

    def set_orderbook(self, symbol: str, orderbook: Any, ttl: float) -> None:
        """Cache order book for symbol with given TTL (seconds)."""
        self._cache.set(self._orderbook_key(symbol), orderbook, ttl)

    def invalidate_orderbook(self, symbol: str) -> bool:
        return self._cache.invalidate(self._orderbook_key(symbol))

    # ------------------------------------------------------------------
    # Futures / open interest / funding
    # ------------------------------------------------------------------

    def get_futures(self, symbol: str) -> Optional[Any]:
        """Retrieve cached futures data for symbol."""
        return self._cache.get(self._futures_key(symbol))

    def set_futures(self, symbol: str, futures_data: Any, ttl: float) -> None:
        """Cache futures data for symbol with given TTL (seconds)."""
        self._cache.set(self._futures_key(symbol), futures_data, ttl)

    def invalidate_futures(self, symbol: str) -> bool:
        return self._cache.invalidate(self._futures_key(symbol))

    # ------------------------------------------------------------------
    # Bulk operations
    # ------------------------------------------------------------------

    def invalidate_symbol(self, symbol: str) -> None:
        """Invalidate all cached entries for a given symbol."""
        for key_fn in (
            self._candles_key,
            self._snapshot_key,
            self._orderbook_key,
            self._futures_key,
        ):
            # candles key requires timeframe; skip via partial match workaround
            pass

        # Directly purge keys that contain the symbol prefix
        with self._cache._lock:
            to_delete = [
                k for k in self._cache._store if f"::{symbol}::" in k or k.endswith(f"::{symbol}")
            ]
            for k in to_delete:
                del self._cache._store[k]
        logger.debug("Invalidated all cache entries for symbol=%r (%d keys)", symbol, len(to_delete))

    def clear(self) -> None:
        """Clear all cached market data."""
        self._cache.clear()

    def stats(self) -> dict[str, Any]:
        """
        Return cache statistics including hit_rate, miss_rate, and size.
        """
        raw = self._cache.stats()
        return {
            "size": raw["size"],
            "hits": raw["hits"],
            "misses": raw["misses"],
            "total_requests": raw["total_requests"],
            "hit_rate": raw["hit_rate"],
            "miss_rate": raw["miss_rate"],
        }


# ---------------------------------------------------------------------------
# Module-level singleton — import and reuse across the bot
# ---------------------------------------------------------------------------

market_cache: MarketDataCache = MarketDataCache()

__all__ = ["TTLCache", "MarketDataCache", "market_cache"]
