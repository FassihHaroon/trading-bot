"""
features/external_data.py
==========================
14th feature extractor — integrates Fear & Greed Index, News Sentiment, and
Macro Event Calendar into FeatureSet fields.

Knowledge base rules enforced here
------------------------------------
NEWS_CONFIRMATION_REQUIRED (hard constraint, master_rules.json):
    News sentiment CANNOT independently trigger a trade.  It is a modifier only.
    The extractor sets sentiment fields; strategies and the signal scorer may
    use them to adjust confidence — but no strategy has news as a gate.

FEAR_GREED_CONTRARIAN (soft rule):
    Extreme fear (≤ 20) is a mild contrarian long signal.
    Extreme greed (≥ 80) is a mild contrarian short / distribution warning.
    Neither reading alone changes the trade direction.

MACRO_EVENT_CAUTION (soft rule):
    A high-impact event within 24 hours reduces confidence.
    The signal scorer applies config.external.macro_high_impact_penalty.

All three sources degrade gracefully — if the network call fails the
FeatureSet fields stay at their neutral defaults and an error is recorded
in extraction_errors.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from config.settings import AgentConfig
from data.schemas import MarketSnapshot
from data.connectors.external import (
    FearGreedClient,
    MacroCalendarClient,
    NewsFeedClient,
    FearGreedSnapshot,
    MacroEvent,
    NewsItem,
)
from features.base import BaseFeatureExtractor

logger = logging.getLogger(__name__)

# Module-level singletons — one network client per process
_fear_greed_client: Optional[FearGreedClient] = None
_macro_client: Optional[MacroCalendarClient] = None
_news_client: Optional[NewsFeedClient] = None


def _get_clients() -> tuple[FearGreedClient, MacroCalendarClient, NewsFeedClient]:
    global _fear_greed_client, _macro_client, _news_client
    if _fear_greed_client is None:
        _fear_greed_client = FearGreedClient()
    if _macro_client is None:
        _macro_client = MacroCalendarClient()
    if _news_client is None:
        _news_client = NewsFeedClient()
    return _fear_greed_client, _macro_client, _news_client


class ExternalDataExtractor(BaseFeatureExtractor):
    """
    Pulls Fear & Greed, news sentiment, and macro events then maps them to
    FeatureSet fields.  Falls back to neutral defaults on any network error.
    """

    def __init__(self, config: AgentConfig) -> None:
        super().__init__(config)
        self._ext = config.external

    # Snapshot timestamps more than this many seconds in the past are treated as
    # historical (backtest mode) — live external APIs are skipped.
    _HISTORICAL_THRESHOLD_S: int = 24 * 3600

    def extract(self, snapshot: MarketSnapshot, feature_set=None) -> dict[str, Any]:
        # Skip live API calls during backtesting — snapshot timestamps from the
        # past have no meaningful current Fear & Greed or news context, and
        # firing ~9000 network requests for a year-long backtest is impractical.
        snap_ts_s = snapshot.timestamp / 1000.0 if snapshot.timestamp > 1e10 else snapshot.timestamp
        if (time.time() - snap_ts_s) > self._HISTORICAL_THRESHOLD_S:
            return {}

        results: dict[str, Any] = {}
        fg_client, macro_client, news_client = _get_clients()
        symbol = snapshot.symbol

        if self._ext.use_fear_greed:
            results.update(self._extract_fear_greed(fg_client))

        if self._ext.use_news_sentiment:
            results.update(self._extract_news(news_client, symbol))

        if self._ext.use_macro_calendar:
            results.update(self._extract_macro(macro_client, symbol))

        # Derive macro_event_risk from upcoming events
        events: list[dict] = results.get("upcoming_macro_events", [])
        results["macro_event_risk"] = self._classify_macro_risk(events)

        return results

    # ── Fear & Greed ──────────────────────────────────────────────────────────

    def _extract_fear_greed(self, client: FearGreedClient) -> dict[str, Any]:
        try:
            snap: Optional[FearGreedSnapshot] = client.get()
            if snap is None:
                logger.debug("Fear & Greed: no data available")
                return {}
            return {
                "fear_greed_value": snap.value,
                "fear_greed_label": snap.classification,
                "fear_greed_signal_bias": snap.signal_bias,
                "fear_greed_trend": snap.trend,
            }
        except Exception as exc:
            logger.warning("Fear & Greed extraction failed: %s", exc)
            return {}

    # ── News Sentiment ────────────────────────────────────────────────────────

    def _extract_news(self, client: NewsFeedClient, symbol: str) -> dict[str, Any]:
        try:
            articles: list[NewsItem] = client.get_news(
                symbol=symbol,
                max_age_hours=self._ext.news_max_age_hours,
            )
            if not articles:
                return {
                    "news_sentiment_score": 0.0,
                    "news_sentiment_direction": "neutral",
                    "news_article_count": 0,
                    "news_high_impact_count": 0,
                }

            # Weighted average using relevance × time-decay
            total_weight = 0.0
            weighted_score = 0.0
            high_impact = 0

            for art in articles:
                if art.relevance_score < self._ext.news_min_relevance:
                    continue
                weight = art.relevance_score
                weighted_score += art.decayed_sentiment * weight
                total_weight += weight
                if art.impact_estimate == "high":
                    high_impact += 1

            sentiment = weighted_score / total_weight if total_weight > 0 else 0.0
            sentiment = max(-1.0, min(1.0, sentiment))

            if sentiment >= 0.2:
                direction = "bullish"
            elif sentiment <= -0.2:
                direction = "bearish"
            else:
                direction = "neutral"

            logger.info(
                "News sentiment for %s: score=%.3f direction=%s articles=%d high_impact=%d",
                symbol, sentiment, direction, len(articles), high_impact,
            )

            return {
                "news_sentiment_score": round(sentiment, 4),
                "news_sentiment_direction": direction,
                "news_article_count": len(articles),
                "news_high_impact_count": high_impact,
            }
        except Exception as exc:
            logger.warning("News sentiment extraction failed: %s", exc)
            return {}

    # ── Macro Calendar ────────────────────────────────────────────────────────

    def _extract_macro(self, client: MacroCalendarClient, symbol: str) -> dict[str, Any]:
        try:
            events: list[MacroEvent] = client.get_upcoming_events(
                symbol=symbol,
                days_ahead=self._ext.macro_days_ahead,
            )
            serialised = [
                {
                    "title": e.title,
                    "category": e.category,
                    "impact": e.impact,
                    "hours_until": round(e.hours_until, 1),
                    "coins": e.coins,
                    "source": e.source,
                }
                for e in events[:10]  # Cap payload size
            ]
            return {"upcoming_macro_events": serialised}
        except Exception as exc:
            logger.warning("Macro calendar extraction failed: %s", exc)
            return {}

    # ── Risk classification ───────────────────────────────────────────────────

    def _classify_macro_risk(self, events: list[dict]) -> str:
        """
        Derive overall macro event risk from the upcoming-event list.

        high  → any high-impact event within 24 hours
        medium → any medium-impact event within 24 hours OR high-impact within 7 days
        low   → otherwise
        """
        if not events:
            return "low"

        imminent = [e for e in events if 0 <= e.get("hours_until", 999) <= 24]
        if any(e.get("impact") == "high" for e in imminent):
            return "high"
        if any(e.get("impact") == "medium" for e in imminent):
            return "medium"
        upcoming_high = [e for e in events if e.get("impact") == "high"]
        if upcoming_high:
            return "medium"
        return "low"
