"""
Canonical data schemas for the trading system.
Every layer reads from and writes to these types — no ad-hoc dicts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ─────────────────────────────────────────────
# Enumerations
# ─────────────────────────────────────────────

class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"
    NEUTRAL = "neutral"
    NO_TRADE = "no_trade"


class MarketRegime(str, Enum):
    TRENDING_BULL = "trending_bull"
    TRENDING_BEAR = "trending_bear"
    RANGING = "ranging"
    CHOPPY = "choppy"
    HIGH_VOLATILITY = "high_volatility"
    LOW_VOLATILITY = "low_volatility"


class TrendDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class MarketPhase(str, Enum):
    ACCUMULATION = "accumulation"
    MARKUP = "markup"
    DISTRIBUTION = "distribution"
    MARKDOWN = "markdown"
    UNKNOWN = "unknown"


class VolatilityLevel(str, Enum):
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


class TradingSession(str, Enum):
    ASIAN = "asian"
    LONDON = "london"
    NEW_YORK = "new_york"
    LONDON_NY_OVERLAP = "london_ny_overlap"
    CLOSED = "closed"


class StructureState(str, Enum):
    HH_HL = "hh_hl"          # Higher highs + higher lows (uptrend)
    LH_LL = "lh_ll"          # Lower highs + lower lows (downtrend)
    RANGING = "ranging"       # Neither
    BROKEN_UP = "broken_up"   # Break of structure to the upside
    BROKEN_DOWN = "broken_down"


class DivergenceType(str, Enum):
    BULLISH_REGULAR = "bullish_regular"
    BEARISH_REGULAR = "bearish_regular"
    BULLISH_HIDDEN = "bullish_hidden"
    BEARISH_HIDDEN = "bearish_hidden"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    EXTREME = "extreme"


class AgentState(str, Enum):
    ACTIVE = "active"
    COOLDOWN = "cooldown"
    CIRCUIT_BREAKER_DAILY = "circuit_breaker_daily"
    CIRCUIT_BREAKER_WEEKLY = "circuit_breaker_weekly"
    PAUSED = "paused"


# ─────────────────────────────────────────────
# Raw Market Data
# ─────────────────────────────────────────────

@dataclass
class Candle:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float
    trades: int
    taker_buy_volume: float
    taker_buy_quote_volume: float = 0.0

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def body_size(self) -> float:
        return abs(self.close - self.open)

    @property
    def body_pct(self) -> float:
        return self.body_size / self.open * 100 if self.open else 0.0

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def taker_sell_volume(self) -> float:
        return self.volume - self.taker_buy_volume

    @property
    def buy_sell_ratio(self) -> float:
        return self.taker_buy_volume / self.volume if self.volume > 0 else 0.5


@dataclass
class OrderBookLevel:
    price: float
    quantity: float


@dataclass
class OrderBook:
    timestamp: int
    bids: list[OrderBookLevel]
    asks: list[OrderBookLevel]

    @property
    def best_bid(self) -> float:
        return self.bids[0].price if self.bids else 0.0

    @property
    def best_ask(self) -> float:
        return self.asks[0].price if self.asks else 0.0

    @property
    def mid_price(self) -> float:
        return (self.best_bid + self.best_ask) / 2

    @property
    def spread_pct(self) -> float:
        return (self.best_ask - self.best_bid) / self.best_bid * 100 if self.best_bid else 0.0

    @property
    def bid_volume(self) -> float:
        return sum(lvl.quantity for lvl in self.bids)

    @property
    def ask_volume(self) -> float:
        return sum(lvl.quantity for lvl in self.asks)

    @property
    def imbalance(self) -> float:
        """Positive = bid-heavy (buy pressure), negative = ask-heavy."""
        total = self.bid_volume + self.ask_volume
        return (self.bid_volume - self.ask_volume) / total if total > 0 else 0.0


@dataclass
class FuturesData:
    timestamp: int
    open_interest: float
    open_interest_value: float
    funding_rate: float
    next_funding_time: int
    long_short_ratio: Optional[float] = None
    top_trader_long_short_ratio: Optional[float] = None
    liquidation_24h_long: Optional[float] = None
    liquidation_24h_short: Optional[float] = None
    taker_buy_sell_ratio: Optional[float] = None    # >1 = more buys

    @property
    def funding_bias(self) -> str:
        """Positive funding = longs paying shorts (crowded long)."""
        if self.funding_rate > 0.0005:
            return "crowded_long"
        elif self.funding_rate < -0.0005:
            return "crowded_short"
        return "neutral"


@dataclass
class Ticker24h:
    timestamp: int
    symbol: str
    price_change: float
    price_change_pct: float
    last_price: float
    volume: float
    quote_volume: float
    high_24h: float
    low_24h: float
    open_price: float


@dataclass
class MarketSnapshot:
    """The normalized market state consumed by every analysis layer."""
    symbol: str
    timestamp: int
    candles: dict[str, list[Candle]] = field(default_factory=dict)
    order_book: Optional[OrderBook] = None
    ticker: Optional[Ticker24h] = None
    futures: Optional[FuturesData] = None
    fetch_duration_ms: float = 0.0
    is_complete: bool = True
    fetch_errors: list[str] = field(default_factory=list)


# ─────────────────────────────────────────────
# Feature Extraction Output
# ─────────────────────────────────────────────

@dataclass
class SwingPoint:
    price: float
    timestamp: int
    timeframe: str
    swing_type: str      # "high" or "low"
    strength: int        # How many bars confirm it (e.g., 3 = 3 bars each side)


@dataclass
class SRZone:
    level: float
    zone_high: float
    zone_low: float
    strength: int        # 0–4 quality score
    zone_type: str       # "support", "resistance", "both"
    timeframes: list[str] = field(default_factory=list)
    touches: int = 1
    last_touch_timestamp: int = 0
    is_role_reversal: bool = False


@dataclass
class ChartPatternDetection:
    pattern_name: str    # e.g., "head_and_shoulders", "ascending_triangle"
    direction: Direction
    confidence: float    # 0–1
    neckline: Optional[float] = None
    target: Optional[float] = None
    invalidation: Optional[float] = None
    volume_confirmed: bool = False
    timeframe: str = ""
    formed_at_timestamp: int = 0


@dataclass
class CandlestickPattern:
    pattern_name: str    # e.g., "bullish_engulfing", "doji", "hammer"
    direction: Direction
    confidence: float
    candle_index: int    # 0 = most recent candle
    timeframe: str = ""


@dataclass
class DivergenceDetection:
    divergence_type: DivergenceType
    indicator: str       # "rsi", "macd", "stochastics"
    timeframe: str
    price_point_1: float
    price_point_2: float
    indicator_point_1: float
    indicator_point_2: float
    confidence: float
    bars_apart: int


@dataclass
class FeatureSet:
    """
    Complete feature extraction output.
    All 13 feature modules contribute to this object.
    This is the primary input to the strategy engine.
    """
    symbol: str
    timestamp: int

    # ── Trend ────────────────────────────────
    trend_direction: TrendDirection = TrendDirection.NEUTRAL
    trend_strength: float = 0.0          # 0–1
    trend_phase: MarketPhase = MarketPhase.UNKNOWN
    ma_fan_bullish: bool = False
    ma_fan_bearish: bool = False
    price_vs_200ema: str = "neutral"     # "above", "below", "at"
    ma_slopes: dict[str, float] = field(default_factory=dict)  # {"20ema": 0.3, ...}

    # ── Support / Resistance ─────────────────
    sr_zones: list[SRZone] = field(default_factory=list)
    nearest_support: Optional[float] = None
    nearest_resistance: Optional[float] = None
    at_key_level: bool = False
    level_quality: int = 0               # 0–4

    # ── Swing Points ─────────────────────────
    swing_highs: list[SwingPoint] = field(default_factory=list)
    swing_lows: list[SwingPoint] = field(default_factory=list)
    last_swing_high: Optional[SwingPoint] = None
    last_swing_low: Optional[SwingPoint] = None

    # ── Market Structure ─────────────────────
    structure_state: StructureState = StructureState.RANGING
    last_bos: Optional[str] = None       # "bullish_bos", "bearish_bos"
    last_choch: Optional[str] = None     # Change of character

    # ── Volume ───────────────────────────────
    volume_trend: str = "flat"           # "increasing", "decreasing", "flat"
    volume_vs_avg: float = 1.0           # ratio vs 20-bar average
    obv_trend: str = "flat"
    volume_climax: bool = False
    taker_delta: float = 0.0             # buy_vol − sell_vol (positive = net buying)

    # ── Liquidity ────────────────────────────
    orderbook_imbalance: float = 0.0     # -1 to +1
    bid_ask_spread_pct: float = 0.0
    liquidity_void_above: Optional[float] = None
    liquidity_void_below: Optional[float] = None

    # ── Momentum ─────────────────────────────
    rsi: float = 50.0
    rsi_trend: str = "flat"
    rsi_zone: str = "neutral"            # "overbought", "oversold", "neutral"
    macd_line: float = 0.0
    macd_signal_line: float = 0.0
    macd_histogram: float = 0.0
    macd_cross: Optional[str] = None     # "bullish_cross", "bearish_cross"
    macd_histogram_trend: str = "flat"
    stoch_k: float = 50.0
    stoch_d: float = 50.0
    stoch_cross: Optional[str] = None
    atr: float = 0.0
    atr_pct: float = 0.0

    # ── Divergence ───────────────────────────
    divergences: list[DivergenceDetection] = field(default_factory=list)

    # ── Candlestick Patterns ─────────────────
    candlestick_patterns: list[CandlestickPattern] = field(default_factory=list)

    # ── Chart Patterns ───────────────────────
    chart_patterns: list[ChartPatternDetection] = field(default_factory=list)

    # ── Multi-Timeframe Alignment ─────────────
    tf_bias: dict[str, TrendDirection] = field(default_factory=dict)  # {"1d": "bullish", ...}
    tf_aligned: bool = False             # True when 4h + 1d agree
    macro_bias: TrendDirection = TrendDirection.NEUTRAL

    # ── Volatility ───────────────────────────
    volatility_state: VolatilityLevel = VolatilityLevel.NORMAL
    atr_percentile: float = 50.0         # Where current ATR sits in 100-bar history

    # ── Fibonacci Levels ─────────────────────
    fib_retracements: list[dict] = field(default_factory=list)  # {"level": 0.618, "price": 43200}

    # ── Elliott Wave ─────────────────────────
    elliott_wave_count: Optional[str] = None  # e.g., "wave_3_up"
    elliott_confidence: float = 0.0

    # ── Session ───────────────────────────────
    current_session: TradingSession = TradingSession.CLOSED
    session_high: Optional[float] = None
    session_low: Optional[float] = None

    # ── Futures-specific ─────────────────────
    funding_bias: str = "neutral"
    oi_trend: str = "flat"               # "increasing", "decreasing", "flat"
    liquidation_imbalance: str = "neutral"  # "long_heavy", "short_heavy"

    # ── External Sentiment ───────────────────
    fear_greed_value: int = 50           # 0–100  (alternative.me)
    fear_greed_label: str = "Neutral"    # e.g. "Extreme Fear"
    fear_greed_signal_bias: str = "neutral"  # "contrarian_long", "contrarian_short", "neutral"
    fear_greed_trend: str = "unknown"    # "improving", "deteriorating", "stable"
    news_sentiment_score: float = 0.0    # Weighted-average, -1.0 to +1.0
    news_sentiment_direction: str = "neutral"  # "bullish", "bearish", "neutral"
    news_article_count: int = 0          # Relevant articles found
    news_high_impact_count: int = 0      # Articles flagged as high-impact
    upcoming_macro_events: list[dict] = field(default_factory=list)  # Next 24-hr events
    macro_event_risk: str = "low"        # "high", "medium", "low"

    # ── Computation metadata ─────────────────
    timeframes_available: list[str] = field(default_factory=list)
    extraction_errors: list[str] = field(default_factory=list)


# ─────────────────────────────────────────────
# Market Context Engine Output
# ─────────────────────────────────────────────

@dataclass
class MarketContext:
    symbol: str
    timestamp: int
    regime: MarketRegime = MarketRegime.RANGING
    trend_direction: TrendDirection = TrendDirection.NEUTRAL
    volatility_level: VolatilityLevel = VolatilityLevel.NORMAL
    volume_quality: str = "neutral"      # "confirming", "diverging", "neutral"
    phase: MarketPhase = MarketPhase.UNKNOWN
    applicable_strategies: list[str] = field(default_factory=list)
    unsuitable_strategies: list[str] = field(default_factory=list)
    context_confidence: float = 0.0
    reasoning: list[str] = field(default_factory=list)


# ─────────────────────────────────────────────
# Strategy Engine Output
# ─────────────────────────────────────────────

@dataclass
class StrategyResult:
    strategy_id: str
    strategy_name: str
    is_valid: bool               # Did this strategy find a valid setup?
    direction: Direction = Direction.NEUTRAL
    confidence: float = 0.0
    entry_price: Optional[float] = None
    stop_price: Optional[float] = None
    targets: list[float] = field(default_factory=list)
    supporting_evidence: list[str] = field(default_factory=list)
    violated_rules: list[str] = field(default_factory=list)
    conflicting_evidence: list[str] = field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.MEDIUM
    invalidation_condition: str = ""
    knowledge_refs: list[str] = field(default_factory=list)  # KB rule IDs
    reasoning: list[str] = field(default_factory=list)

    def __post_init__(self):
        assert 0.0 <= self.confidence <= 1.0


# ─────────────────────────────────────────────
# Signal Scoring Engine Output
# ─────────────────────────────────────────────

@dataclass
class ScoredSignal:
    symbol: str
    timestamp: int
    direction: Direction = Direction.NO_TRADE
    aggregate_confidence: float = 0.0
    valid_strategies: list[StrategyResult] = field(default_factory=list)
    invalid_strategies: list[StrategyResult] = field(default_factory=list)
    supporting_strategies: list[str] = field(default_factory=list)
    conflicting_strategies: list[str] = field(default_factory=list)
    entry_price: Optional[float] = None
    stop_price: Optional[float] = None
    targets: list[float] = field(default_factory=list)
    evidence_summary: list[str] = field(default_factory=list)
    conflict_summary: list[str] = field(default_factory=list)
    no_trade_reasons: list[str] = field(default_factory=list)
    knowledge_refs: list[str] = field(default_factory=list)


# ─────────────────────────────────────────────
# Risk Engine Output
# ─────────────────────────────────────────────

@dataclass
class RiskAssessment:
    signal_approved: bool
    rejection_reason: Optional[str] = None
    position_size: float = 0.0
    risk_amount: float = 0.0
    risk_pct: float = 0.0
    stop_distance_pct: float = 0.0
    risk_reward_ratio: float = 0.0
    account_equity: float = 0.0
    agent_state: AgentState = AgentState.ACTIVE
    circuit_breaker_reason: Optional[str] = None
    max_position_allowed: float = 0.0
    daily_loss_used_pct: float = 0.0
    weekly_loss_used_pct: float = 0.0
    consecutive_losses: int = 0


# ─────────────────────────────────────────────
# Final Trade Signal (Decision Engine Output)
# ─────────────────────────────────────────────

@dataclass
class TradeSignal:
    symbol: str
    timestamp: int
    direction: Direction = Direction.NO_TRADE
    confidence: float = 0.0

    # Execution levels
    entry_price: Optional[float] = None
    stop_price: Optional[float] = None
    targets: list[float] = field(default_factory=list)

    # Risk
    position_size: float = 0.0
    risk_amount: float = 0.0
    risk_reward_ratio: float = 0.0

    # Reasoning chain (full trace)
    market_context: Optional[MarketContext] = None
    strategy_results: list[StrategyResult] = field(default_factory=list)
    scored_signal: Optional[ScoredSignal] = None
    risk_assessment: Optional[RiskAssessment] = None
    knowledge_refs: list[str] = field(default_factory=list)
    rules_fired: list[str] = field(default_factory=list)
    no_trade_reason: Optional[str] = None

    # Execution metadata
    is_paper_trade: bool = True
    executed: bool = False
    execution_price: Optional[float] = None
    execution_timestamp: Optional[int] = None

    def is_actionable(self) -> bool:
        return (
            self.direction in (Direction.LONG, Direction.SHORT)
            and self.stop_price is not None
            and self.entry_price is not None
            and self.position_size > 0
        )

    def to_summary(self) -> str:
        if self.direction == Direction.NO_TRADE:
            return f"NO_TRADE | {self.no_trade_reason or 'No qualifying setup'}"
        rr = f"{self.risk_reward_ratio:.1f}R" if self.risk_reward_ratio else "N/A"
        return (
            f"{self.direction.value.upper()} {self.symbol} | "
            f"Entry: {self.entry_price:.2f} | Stop: {self.stop_price:.2f} | "
            f"R:R {rr} | Confidence: {self.confidence:.0%} | "
            f"Strategies: {', '.join(s.strategy_id for s in self.strategy_results if s.is_valid)}"
        )


# ─────────────────────────────────────────────
# Trade Journal Entry
# ─────────────────────────────────────────────

@dataclass
class JournalEntry:
    entry_id: str
    symbol: str
    timestamp_open: int
    timestamp_close: Optional[int] = None
    direction: Direction = Direction.NO_TRADE
    entry_price: Optional[float] = None
    exit_price: Optional[float] = None
    stop_price: Optional[float] = None
    targets: list[float] = field(default_factory=list)
    position_size: float = 0.0
    risk_amount: float = 0.0
    realized_pnl: Optional[float] = None
    realized_r: Optional[float] = None  # P&L in R multiples
    confidence: float = 0.0
    strategies_used: list[str] = field(default_factory=list)
    market_regime: Optional[str] = None
    market_phase: Optional[str] = None
    reasoning_trace: list[str] = field(default_factory=list)
    knowledge_refs: list[str] = field(default_factory=list)
    rules_fired: list[str] = field(default_factory=list)
    rules_violated: list[str] = field(default_factory=list)
    is_paper_trade: bool = True
    outcome: Optional[str] = None  # "win", "loss", "breakeven", "open"
    exit_reason: Optional[str] = None  # "stop", "target_1", "target_2", "manual"
    feature_snapshot: Optional[dict] = None


# ─────────────────────────────────────────────
# Backtesting
# ─────────────────────────────────────────────

@dataclass
class BacktestTrade:
    entry_timestamp: int
    exit_timestamp: int
    symbol: str
    direction: Direction
    entry_price: float
    exit_price: float
    stop_price: float
    position_size: float
    risk_amount: float
    pnl: float
    r_multiple: float
    strategies_used: list[str]
    market_regime: str
    confidence: float
    exit_reason: str


@dataclass
class BacktestResult:
    symbol: str
    timeframe: str
    start_timestamp: int
    end_timestamp: int
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    avg_r_multiple: float = 0.0
    expectancy: float = 0.0              # Expected R per trade
    total_return_pct: float = 0.0
    trades: list[BacktestTrade] = field(default_factory=list)
    strategy_breakdown: dict[str, dict] = field(default_factory=dict)
    regime_breakdown: dict[str, dict] = field(default_factory=dict)
    monthly_returns: list[float] = field(default_factory=list)
    validation_passed: bool = False
