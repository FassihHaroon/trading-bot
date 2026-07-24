"""
External data connectors — Fear & Greed Index, Macro Event Calendar, and news feeds.
All sources used here are free public APIs — no API keys required.

Sources:
  Fear & Greed : alternative.me/crypto/fear-and-greed-index/
  Macro events : coinmarketcal.com (public endpoint) + CryptoCompare events RSS
  News feeds   : CoinTelegraph, Decrypt, BeInCrypto, CoinDesk RSS (no key needed)
"""

from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FearGreedSnapshot:
    value: int                  # 0–100  (0 = Extreme Fear, 100 = Extreme Greed)
    classification: str         # "Extreme Fear" / "Fear" / "Neutral" / "Greed" / "Extreme Greed"
    timestamp: int              # Unix seconds
    value_yesterday: Optional[int] = None
    value_last_week: Optional[int] = None

    @property
    def signal_bias(self) -> str:
        """
        Counter-trend interpretation (contrarian indicator):
        Extreme Fear  → potential long opportunity
        Extreme Greed → potential short / caution
        """
        if self.value <= 20:
            return "contrarian_long"    # Market is fearful — smart money accumulates
        if self.value >= 80:
            return "contrarian_short"   # Market is greedy — distribution risk
        if self.value <= 40:
            return "mild_fear"
        if self.value >= 60:
            return "mild_greed"
        return "neutral"

    @property
    def trend(self) -> str:
        """Did sentiment improve or worsen since yesterday?"""
        if self.value_yesterday is None:
            return "unknown"
        diff = self.value - self.value_yesterday
        if diff >= 5:
            return "improving"
        if diff <= -5:
            return "deteriorating"
        return "stable"


@dataclass
class MacroEvent:
    title: str
    description: str
    category: str               # "earnings" / "regulation" / "upgrade" / "halving" / "macro"
    impact: str                 # "high" / "medium" / "low"
    scheduled_at: datetime
    coins: list[str] = field(default_factory=list)   # Affected symbols e.g. ["BTC", "ETH"]
    source: str = ""

    @property
    def hours_until(self) -> float:
        now = datetime.now(timezone.utc)
        scheduled = self.scheduled_at.replace(tzinfo=timezone.utc) \
            if self.scheduled_at.tzinfo is None else self.scheduled_at
        return (scheduled - now).total_seconds() / 3600

    @property
    def is_imminent(self) -> bool:
        """Event within 24 hours."""
        return 0 <= self.hours_until <= 24

    @property
    def is_recent(self) -> bool:
        """Event happened within the last 6 hours."""
        return -6 <= self.hours_until <= 0


@dataclass
class NewsItem:
    title: str
    summary: str
    source: str
    published_at: datetime
    url: str
    keywords: list[str] = field(default_factory=list)
    sentiment_score: float = 0.0   # -1.0 (bearish) to +1.0 (bullish)
    relevance_score: float = 0.0   # 0.0–1.0 (how relevant to the tracked symbol)
    impact_estimate: str = "low"   # "high" / "medium" / "low"

    @property
    def age_hours(self) -> float:
        now = datetime.now(timezone.utc)
        pub = self.published_at.replace(tzinfo=timezone.utc) \
            if self.published_at.tzinfo is None else self.published_at
        return (now - pub).total_seconds() / 3600

    @property
    def decayed_sentiment(self) -> float:
        """
        Time-decay: news sentiment fades over 48 hours.
        A 24h-old article has half the weight of a fresh one.
        """
        decay = max(0.0, 1.0 - self.age_hours / 48.0)
        return self.sentiment_score * decay


# ─────────────────────────────────────────────────────────────────────────────
# Fear & Greed Client
# ─────────────────────────────────────────────────────────────────────────────

class FearGreedClient:
    """Fetches Crypto Fear & Greed Index from alternative.me (free, no key)."""

    BASE_URL = "https://api.alternative.me/fng/"
    TIMEOUT = 8
    CACHE_TTL = 3600  # Refreshes once per hour (index updates daily)

    def __init__(self):
        self._cache: Optional[FearGreedSnapshot] = None
        self._cache_ts: float = 0.0

    def get(self) -> Optional[FearGreedSnapshot]:
        """Return current Fear & Greed snapshot."""
        if self._cache and (time.monotonic() - self._cache_ts) < self.CACHE_TTL:
            return self._cache

        try:
            resp = requests.get(
                self.BASE_URL,
                params={"limit": 7, "format": "json"},  # Last 7 days
                timeout=self.TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json().get("data", [])
            if not data:
                return None

            current = data[0]
            yesterday = data[1] if len(data) > 1 else None
            last_week = data[6] if len(data) > 6 else None

            snapshot = FearGreedSnapshot(
                value=int(current["value"]),
                classification=current["value_classification"],
                timestamp=int(current["timestamp"]),
                value_yesterday=int(yesterday["value"]) if yesterday else None,
                value_last_week=int(last_week["value"]) if last_week else None,
            )
            self._cache = snapshot
            self._cache_ts = time.monotonic()

            logger.info(
                "Fear & Greed: %d (%s) | trend=%s | bias=%s",
                snapshot.value, snapshot.classification,
                snapshot.trend, snapshot.signal_bias,
            )
            return snapshot

        except Exception as exc:
            logger.warning("Fear & Greed fetch failed: %s", exc)
            return self._cache  # Return stale data rather than None


# ─────────────────────────────────────────────────────────────────────────────
# Macro Event Calendar Client
# ─────────────────────────────────────────────────────────────────────────────

class MacroCalendarClient:
    """
    Aggregates macro crypto events from CoinMarketCal public RSS
    and CryptoCompare's event feed (both free, no key required).
    """

    # CoinMarketCal public RSS (no API key for basic feed)
    COINMARKETCAL_RSS = "https://coinmarketcal.com/en/rss/feed"

    # CryptoCompare top events (JSON, no key for basic)
    CRYPTOCOMPARE_EVENTS = "https://min-api.cryptocompare.com/data/v2/news/?categories=Blockchain,Mining,Technology&excludeCategories=Sponsored"

    TIMEOUT = 10
    CACHE_TTL = 900  # 15 minutes

    # Keywords that signal high-impact events
    HIGH_IMPACT_KEYWORDS = {
        "halving", "fork", "upgrade", "mainnet", "etf", "regulation",
        "ban", "hack", "exploit", "sec", "fed", "interest rate",
        "inflation", "cpi", "fomc", "gdp", "jobs report",
    }
    MEDIUM_IMPACT_KEYWORDS = {
        "partnership", "listing", "delisting", "launch", "airdrop",
        "unlock", "vesting", "staking", "bridge", "layer",
    }

    def __init__(self):
        self._cache: list[MacroEvent] = []
        self._cache_ts: float = 0.0

    def get_upcoming_events(self, symbol: str = "BTC", days_ahead: int = 7) -> list[MacroEvent]:
        """Return macro events relevant to symbol within next N days."""
        if self._cache and (time.monotonic() - self._cache_ts) < self.CACHE_TTL:
            return self._filter(self._cache, symbol, days_ahead)

        events: list[MacroEvent] = []
        events.extend(self._fetch_coinmarketcal(symbol))
        events.extend(self._fetch_cryptocompare_events(symbol))

        # Deduplicate by title similarity
        events = self._deduplicate(events)
        self._cache = events
        self._cache_ts = time.monotonic()

        logger.info("Macro calendar: %d events loaded", len(events))
        return self._filter(events, symbol, days_ahead)

    def _fetch_coinmarketcal(self, symbol: str) -> list[MacroEvent]:
        try:
            resp = requests.get(self.COINMARKETCAL_RSS, timeout=self.TIMEOUT)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            events = []
            for item in root.findall(".//item"):
                title = (item.findtext("title") or "").strip()
                desc = (item.findtext("description") or "").strip()
                pub_date_str = item.findtext("pubDate") or ""
                try:
                    pub_date = datetime.strptime(pub_date_str, "%a, %d %b %Y %H:%M:%S %z")
                except ValueError:
                    pub_date = datetime.now(timezone.utc)

                impact = self._classify_impact(title + " " + desc)
                coins = self._extract_coins(title + " " + desc)
                events.append(MacroEvent(
                    title=title,
                    description=desc[:300],
                    category=self._classify_category(title),
                    impact=impact,
                    scheduled_at=pub_date,
                    coins=coins,
                    source="CoinMarketCal",
                ))
            return events
        except Exception as exc:
            logger.debug("CoinMarketCal RSS failed: %s", exc)
            return []

    def _fetch_cryptocompare_events(self, symbol: str) -> list[MacroEvent]:
        try:
            resp = requests.get(self.CRYPTOCOMPARE_EVENTS, timeout=self.TIMEOUT)
            resp.raise_for_status()
            data = resp.json().get("Data", [])
            events = []
            for item in data[:30]:  # Cap at 30 items
                title = item.get("title", "")
                body = item.get("body", "")[:300]
                published = datetime.fromtimestamp(
                    item.get("published_on", time.time()), tz=timezone.utc
                )
                coins = self._extract_coins(title + " " + body)
                events.append(MacroEvent(
                    title=title,
                    description=body,
                    category=self._classify_category(title),
                    impact=self._classify_impact(title + " " + body),
                    scheduled_at=published,
                    coins=coins,
                    source="CryptoCompare",
                ))
            return events
        except Exception as exc:
            logger.debug("CryptoCompare events failed: %s", exc)
            return []

    def _classify_impact(self, text: str) -> str:
        lower = text.lower()
        if any(kw in lower for kw in self.HIGH_IMPACT_KEYWORDS):
            return "high"
        if any(kw in lower for kw in self.MEDIUM_IMPACT_KEYWORDS):
            return "medium"
        return "low"

    def _classify_category(self, text: str) -> str:
        lower = text.lower()
        if any(w in lower for w in ("halving", "fork", "upgrade", "mainnet")):
            return "upgrade"
        if any(w in lower for w in ("etf", "regulation", "sec", "ban", "legal")):
            return "regulation"
        if any(w in lower for w in ("fed", "cpi", "gdp", "fomc", "rate")):
            return "macro"
        return "general"

    def _extract_coins(self, text: str) -> list[str]:
        known = ["BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "AVAX", "MATIC", "DOT", "LINK"]
        found = [c for c in known if c in text.upper()]
        return list(dict.fromkeys(found))[:5]

    def _filter(
        self, events: list[MacroEvent], symbol: str, days_ahead: int
    ) -> list[MacroEvent]:
        coin = symbol.replace("USDT", "").replace("BUSD", "")
        cutoff = days_ahead * 24
        return [
            e for e in events
            if (coin in e.coins or not e.coins or e.impact == "high")
            and -6 <= e.hours_until <= cutoff
        ]

    def _deduplicate(self, events: list[MacroEvent]) -> list[MacroEvent]:
        seen: set[str] = set()
        unique = []
        for e in events:
            key = e.title[:60].lower()
            if key not in seen:
                seen.add(key)
                unique.append(e)
        return unique


# ─────────────────────────────────────────────────────────────────────────────
# News Feed Client
# ─────────────────────────────────────────────────────────────────────────────

class NewsFeedClient:
    """
    Aggregates crypto news from multiple free RSS feeds.
    No API key required.

    Sources: CoinTelegraph, Decrypt, BeInCrypto, CoinDesk, TheBlock
    """

    RSS_FEEDS = [
        ("CoinTelegraph",  "https://cointelegraph.com/rss"),
        ("Decrypt",        "https://decrypt.co/feed"),
        ("BeInCrypto",     "https://beincrypto.com/feed/"),
        ("CoinDesk",       "https://www.coindesk.com/arc/outboundfeeds/rss/"),
        ("TheBlock",       "https://www.theblock.co/rss.xml"),
    ]

    TIMEOUT = 8
    CACHE_TTL = 300   # 5 minutes

    # Sentiment keywords — weighted
    BULLISH_KEYWORDS = {
        "surge": 0.8, "rally": 0.7, "breakout": 0.7, "all-time high": 0.9,
        "adoption": 0.6, "upgrade": 0.5, "bullish": 0.8, "soar": 0.7,
        "gain": 0.5, "recovery": 0.6, "moon": 0.4, "institutional": 0.5,
        "etf approved": 0.9, "buy": 0.4, "accumulate": 0.6, "positive": 0.5,
    }
    BEARISH_KEYWORDS = {
        "crash": 0.9, "dump": 0.8, "plunge": 0.8, "collapse": 0.9,
        "bear": 0.6, "sell-off": 0.8, "regulation": 0.5, "ban": 0.9,
        "hack": 0.9, "exploit": 0.9, "scam": 0.8, "fraud": 0.8,
        "bankruptcy": 0.9, "lawsuit": 0.7, "sec": 0.6, "fear": 0.6,
        "panic": 0.7, "liquidation": 0.6, "loss": 0.5, "warning": 0.5,
    }

    def __init__(self):
        self._cache: list[NewsItem] = []
        self._cache_ts: float = 0.0

    def get_news(self, symbol: str = "BTC", max_age_hours: float = 24.0) -> list[NewsItem]:
        """Fetch and return news items relevant to symbol, within max_age_hours."""
        if self._cache and (time.monotonic() - self._cache_ts) < self.CACHE_TTL:
            return self._filter_news(self._cache, symbol, max_age_hours)

        all_news: list[NewsItem] = []
        for source_name, url in self.RSS_FEEDS:
            items = self._fetch_rss(source_name, url)
            all_news.extend(items)

        # Sort by recency
        all_news.sort(key=lambda n: n.published_at, reverse=True)
        self._cache = all_news
        self._cache_ts = time.monotonic()

        logger.info("News feed: %d articles loaded from %d sources", len(all_news), len(self.RSS_FEEDS))
        return self._filter_news(all_news, symbol, max_age_hours)

    def _fetch_rss(self, source_name: str, url: str) -> list[NewsItem]:
        try:
            resp = requests.get(url, timeout=self.TIMEOUT, headers={
                "User-Agent": "Mozilla/5.0 (compatible; TradingBot/1.0)"
            })
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            items = []
            for item in root.findall(".//item")[:20]:  # Max 20 per source
                title = (item.findtext("title") or "").strip()
                description = (item.findtext("description") or "").strip()[:500]
                link = (item.findtext("link") or "").strip()
                pub_str = item.findtext("pubDate") or ""

                try:
                    published = datetime.strptime(pub_str, "%a, %d %b %Y %H:%M:%S %z")
                except ValueError:
                    try:
                        published = datetime.strptime(pub_str, "%a, %d %b %Y %H:%M:%S %Z")
                        published = published.replace(tzinfo=timezone.utc)
                    except ValueError:
                        published = datetime.now(timezone.utc)

                text = f"{title} {description}".lower()
                sentiment = self._compute_sentiment(text)
                keywords = self._extract_keywords(text)

                items.append(NewsItem(
                    title=title,
                    summary=description,
                    source=source_name,
                    published_at=published,
                    url=link,
                    keywords=keywords,
                    sentiment_score=sentiment,
                    relevance_score=0.0,  # Filled in filter step
                    impact_estimate=self._estimate_impact(text),
                ))
            return items
        except Exception as exc:
            logger.debug("RSS fetch failed for %s: %s", source_name, exc)
            return []

    def _compute_sentiment(self, text: str) -> float:
        """
        Rule-based sentiment score: -1.0 (very bearish) to +1.0 (very bullish).
        Not AI-based — keyword weighted scoring.
        """
        bull_score = sum(
            weight for kw, weight in self.BULLISH_KEYWORDS.items() if kw in text
        )
        bear_score = sum(
            weight for kw, weight in self.BEARISH_KEYWORDS.items() if kw in text
        )
        total = bull_score + bear_score
        if total == 0:
            return 0.0
        return (bull_score - bear_score) / total

    def _estimate_impact(self, text: str) -> str:
        high_signals = {"hack", "exploit", "ban", "etf", "halving", "crash", "sec lawsuit"}
        if any(s in text for s in high_signals):
            return "high"
        medium_signals = {"partnership", "upgrade", "mainnet", "listing", "regulation"}
        if any(s in text for s in medium_signals):
            return "medium"
        return "low"

    def _extract_keywords(self, text: str) -> list[str]:
        tokens = text.split()
        important = [
            t for t in tokens
            if t in {**self.BULLISH_KEYWORDS, **self.BEARISH_KEYWORDS}
        ]
        return list(dict.fromkeys(important))[:8]

    def _filter_news(
        self, items: list[NewsItem], symbol: str, max_age_hours: float
    ) -> list[NewsItem]:
        coin = symbol.replace("USDT", "").replace("BUSD", "").lower()
        result = []
        for item in items:
            if item.age_hours > max_age_hours:
                continue
            text = f"{item.title} {item.summary}".lower()
            # Relevance: does it mention the coin or general market?
            relevance = 0.0
            if coin in text or symbol.lower() in text:
                relevance += 0.6
            if any(w in text for w in ["crypto", "bitcoin", "market", "blockchain"]):
                relevance += 0.3
            if item.impact_estimate == "high":
                relevance += 0.2
            item.relevance_score = min(relevance, 1.0)
            if item.relevance_score >= 0.3:
                result.append(item)
        return sorted(result, key=lambda n: (n.relevance_score, -n.age_hours), reverse=True)
