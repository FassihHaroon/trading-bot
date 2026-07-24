"""
Trading Session Analysis Feature Extractor

Determines the current Forex/crypto trading session from the snapshot
timestamp and computes the session high/low from 15-minute candle data.

Session windows (UTC):
    Asian            00:00 – 08:00
    London           08:00 – 12:00
    London/NY Overlap 12:00 – 16:00  ← preferred trading session (highest volume)
    New York         16:00 – 21:00
    Closed           21:00 – 00:00

Fields written to FeatureSet
─────────────────────────────
    current_session  – TradingSession enum value
    session_high     – max(high) of 15m candles that fall inside the current
                       session window; None when no candles are available
    session_low      – min(low)  of 15m candles that fall inside the current
                       session window; None when no candles are available

Metadata
────────
    The London/NY Overlap is flagged in extraction_errors with a special
    INFO-level marker so downstream consumers can detect the preferred session
    without inspecting the enum directly.  A dedicated module-level constant
    PREFERRED_SESSION is also exported.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import List, Optional, Tuple

from data.schemas import Candle, FeatureSet, MarketSnapshot, TradingSession

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Public constants
# ─────────────────────────────────────────────

#: The session with the highest combined volume and the tightest spreads.
PREFERRED_SESSION: TradingSession = TradingSession.LONDON_NY_OVERLAP

#: Key used to look up 15-minute candles inside MarketSnapshot.candles.
CANDLE_TIMEFRAME: str = "15m"

# Session boundaries expressed as (start_hour_inclusive, end_hour_exclusive) UTC.
# Hours are integers in [0, 24).
_SESSION_WINDOWS: List[Tuple[int, int, TradingSession]] = [
    (0,  8,  TradingSession.ASIAN),
    (8,  12, TradingSession.LONDON),
    (12, 16, TradingSession.LONDON_NY_OVERLAP),
    (16, 21, TradingSession.NEW_YORK),
    (21, 24, TradingSession.CLOSED),
]

# ─────────────────────────────────────────────
# Base class (thin local interface — mirrors volume.py pattern)
# ─────────────────────────────────────────────

class BaseFeatureExtractor(ABC):
    """Minimal interface every feature extractor must satisfy."""

    @abstractmethod
    def extract(self, snapshot: MarketSnapshot, features: FeatureSet) -> None:
        """
        Read from *snapshot*, write results into *features* in-place.
        Must never raise — catch all exceptions and append to
        features.extraction_errors instead.
        """


# ─────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────

def _utc_hour_from_ms(timestamp_ms: int) -> int:
    """
    Return the UTC hour (0-23) for a Unix millisecond timestamp.

    Parameters
    ----------
    timestamp_ms:
        Unix epoch in milliseconds (as stored in MarketSnapshot.timestamp
        and Candle.timestamp).
    """
    return (timestamp_ms // 1_000 // 3_600) % 24


def _session_for_hour(utc_hour: int) -> TradingSession:
    """
    Map a UTC hour to the corresponding TradingSession.

    Parameters
    ----------
    utc_hour:
        Integer in [0, 23].
    """
    for start, end, session in _SESSION_WINDOWS:
        if start <= utc_hour < end:
            return session
    # Defensive fallback — should never be reached for valid UTC hours.
    return TradingSession.CLOSED


def _session_start_ms(snapshot_ts_ms: int, session: TradingSession) -> int:
    """
    Return the Unix-ms timestamp of the most recent session open relative
    to *snapshot_ts_ms*.

    The session start is the UTC hour that begins the session on the same
    calendar day as the snapshot.  For the CLOSED session (21:00-24:00) the
    start is always on the same day; no cross-day correction is needed here
    because _filter_session_candles applies the window symmetrically.

    Parameters
    ----------
    snapshot_ts_ms:
        Snapshot Unix timestamp in milliseconds.
    session:
        The session whose start we need.
    """
    for start_hour, _end_hour, s in _SESSION_WINDOWS:
        if s is session:
            # Truncate snapshot to midnight UTC, then add the session start offset.
            midnight_ms = (snapshot_ts_ms // (86_400 * 1_000)) * (86_400 * 1_000)
            return midnight_ms + start_hour * 3_600 * 1_000
    return snapshot_ts_ms


def _session_end_ms(snapshot_ts_ms: int, session: TradingSession) -> int:
    """
    Return the Unix-ms timestamp of the session close (exclusive) relative
    to *snapshot_ts_ms*.
    """
    for _start_hour, end_hour, s in _SESSION_WINDOWS:
        if s is session:
            midnight_ms = (snapshot_ts_ms // (86_400 * 1_000)) * (86_400 * 1_000)
            if end_hour == 24:
                # Use start of the next day.
                return midnight_ms + 86_400 * 1_000
            return midnight_ms + end_hour * 3_600 * 1_000
    return snapshot_ts_ms


def _filter_session_candles(
    candles: List[Candle],
    session_start_ms: int,
    session_end_ms: int,
) -> List[Candle]:
    """
    Return only candles whose timestamp falls within
    [session_start_ms, session_end_ms).

    Parameters
    ----------
    candles:
        All available 15m candles, in any order.
    session_start_ms:
        Inclusive lower bound (Unix ms).
    session_end_ms:
        Exclusive upper bound (Unix ms).
    """
    return [
        c for c in candles
        if session_start_ms <= c.timestamp < session_end_ms
    ]


# ─────────────────────────────────────────────
# Main extractor
# ─────────────────────────────────────────────

class SessionExtractor(BaseFeatureExtractor):
    """
    Extracts trading-session features from MarketSnapshot.

    Fields written to FeatureSet
    ─────────────────────────────
    current_session  – TradingSession enum
    session_high     – Optional[float]  max high of in-session 15m candles
    session_low      – Optional[float]  min low  of in-session 15m candles

    Metadata note
    ─────────────
    When the current session is the London/NY Overlap (the preferred session),
    an informational string is appended to FeatureSet.extraction_errors with
    the prefix "INFO:session:" so downstream consumers can detect it without
    comparing the enum.  This does NOT indicate an error; it is a deliberate
    annotation.
    """

    def __init__(self, candle_timeframe: str = CANDLE_TIMEFRAME) -> None:
        """
        Parameters
        ----------
        candle_timeframe:
            Key to use when looking up candles in snapshot.candles.
            Defaults to "15m".
        """
        self.candle_timeframe = candle_timeframe

    # ── Public entry point ────────────────────────────────────────────────

    def extract(self, snapshot: MarketSnapshot, features: FeatureSet) -> None:
        """
        Populate session-related fields on *features* in-place.

        Never raises.  All errors are caught and appended to
        features.extraction_errors.
        """
        try:
            self._extract_session(snapshot, features)
        except Exception as exc:
            logger.exception("SessionExtractor._extract_session failed: %s", exc)
            features.extraction_errors.append(f"session: {exc}")

    # ── Internal logic ────────────────────────────────────────────────────

    def _extract_session(
        self, snapshot: MarketSnapshot, features: FeatureSet
    ) -> None:
        timestamp_ms: int = snapshot.timestamp

        # ── Step 1: Determine current session from UTC hour ───────────────
        utc_hour: int = _utc_hour_from_ms(timestamp_ms)
        session: TradingSession = _session_for_hour(utc_hour)
        features.current_session = session

        logger.debug(
            "SessionExtractor: timestamp_ms=%d  utc_hour=%d  session=%s",
            timestamp_ms,
            utc_hour,
            session.value,
        )

        # ── Step 2: Flag preferred session in metadata ────────────────────
        if session is PREFERRED_SESSION:
            features.extraction_errors.append(
                "INFO:session:london_ny_overlap is the preferred trading session "
                "(highest volume, tightest spreads)"
            )

        # ── Step 3: Retrieve 15m candles ──────────────────────────────────
        candles_15m: Optional[List[Candle]] = snapshot.candles.get(
            self.candle_timeframe
        )

        if not candles_15m:
            # No 15m data — session high/low remain None (FeatureSet defaults).
            logger.debug(
                "SessionExtractor: no '%s' candles in snapshot — "
                "session_high and session_low will be None.",
                self.candle_timeframe,
            )
            return

        # ── Step 4: Filter candles to the current session window ──────────
        start_ms: int = _session_start_ms(timestamp_ms, session)
        end_ms: int = _session_end_ms(timestamp_ms, session)

        session_candles: List[Candle] = _filter_session_candles(
            candles_15m, start_ms, end_ms
        )

        logger.debug(
            "SessionExtractor: session window [%d, %d)  candles_in_window=%d",
            start_ms,
            end_ms,
            len(session_candles),
        )

        if not session_candles:
            # Session has not started yet or no data for this window.
            return

        # ── Step 5: Compute session high and low ──────────────────────────
        features.session_high = max(c.high for c in session_candles)
        features.session_low  = min(c.low  for c in session_candles)

        logger.debug(
            "SessionExtractor: session_high=%.6f  session_low=%.6f",
            features.session_high,
            features.session_low,
        )
