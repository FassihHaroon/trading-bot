"""
Central configuration — all tunable parameters in one place.
Read at session start; immutable during execution.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional
from dotenv import load_dotenv

load_dotenv()


@dataclass
class BinanceConfig:
    api_key: str = field(default_factory=lambda: os.getenv("BINANCE_API_KEY", ""))
    api_secret: str = field(default_factory=lambda: os.getenv("BINANCE_API_SECRET", ""))
    testnet: bool = field(default_factory=lambda: os.getenv("BINANCE_TESTNET", "true").lower() == "true")
    base_url: str = "https://api.binance.com"
    futures_base_url: str = "https://fapi.binance.com"
    rate_limit_per_minute: int = 1200
    request_timeout: int = 10
    max_retries: int = 3
    retry_delay: float = 1.0


@dataclass
class DataConfig:
    symbol: str = field(default_factory=lambda: os.getenv("SYMBOL", "BTCUSDT"))
    timeframes: list = field(default_factory=lambda: ["15m", "1h", "4h", "1d"])
    lookback_bars: int = 300             # Candles to fetch per timeframe
    orderbook_depth: int = 20
    cache_ttl_seconds: int = 30          # Live data cache TTL
    cache_max_size: int = 1000
    use_futures: bool = True             # Fetch open interest + funding rate
    use_external_data: bool = False      # Fear & greed, economic calendar


@dataclass
class FeatureConfig:
    # Swing point detection
    swing_strength: int = 3             # Bars each side to confirm a swing
    # Moving averages
    ema_short: int = 9
    ema_mid: int = 21
    ema_long: int = 50
    ema_200: int = 200
    sma_200: int = 200
    # Momentum
    rsi_period: int = 14
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    stoch_k: int = 14
    stoch_d: int = 3
    stoch_smooth: int = 3
    # Volatility
    atr_period: int = 14
    bb_period: int = 20
    bb_std: float = 2.0
    # Volume
    volume_ma_period: int = 20
    volume_spike_threshold: float = 1.5  # 1.5x average = spike
    climax_threshold: float = 3.0
    # S/R zone width
    sr_zone_pct: float = 0.0075         # ±0.75% zone around level
    sr_min_touches: int = 2
    # Multi-TF
    macro_timeframes: list = field(default_factory=lambda: ["4h", "1d"])
    micro_timeframes: list = field(default_factory=lambda: ["15m", "1h"])
    # Fibonacci levels
    fib_levels: list = field(default_factory=lambda: [0.236, 0.382, 0.5, 0.618, 0.786])
    # Divergence
    divergence_lookback: int = 50


@dataclass
class RiskConfig:
    account_equity: float = field(default_factory=lambda: float(os.getenv("ACCOUNT_EQUITY", "10000")))
    max_risk_pct: float = field(default_factory=lambda: float(os.getenv("MAX_RISK_PCT", "0.01")))
    max_dollar_risk: Optional[float] = None
    min_risk_reward: float = 2.0
    daily_loss_limit_pct: float = 0.03
    weekly_loss_limit_pct: float = 0.05
    max_open_positions: int = 3
    max_correlated_positions: int = 2
    max_leverage: float = 10.0
    consecutive_loss_limit: int = 3
    cooldown_hours: int = 24
    recalibration_sessions: int = 3
    recalibration_risk_multiplier: float = 0.75


@dataclass
class SignalConfig:
    # Confluence
    min_strategies_for_signal: int = 2     # Strategies that must be valid
    macro_micro_gate: bool = True           # Hard gate — required
    # Confidence thresholds
    min_confidence: float = 0.40
    medium_confidence: float = 0.60
    high_confidence: float = 0.75
    # Strategy-count-dependent confidence thresholds
    # (more agreeing strategies → lower required confidence per-strategy)
    confidence_required_for_3_factors: float = 0.60
    confidence_required_for_4_factors: float = 0.50
    confidence_required_for_5_factors: float = 0.45
    # Strategy weights (must sum ≤ 1.0; normalised automatically)
    strategy_weights: dict = field(default_factory=lambda: {
        "trend_following": 1.0,
        "pullback": 0.9,
        "breakout": 0.85,
        "support_bounce": 0.85,
        "resistance_rejection": 0.85,
        "multi_tf_confirmation": 1.0,
        "momentum_strategy": 0.8,
        "range_trading": 0.75,
        "reversal": 0.7,
        "mean_reversion": 0.65,
    })
    # News conservatism
    news_confirmation_required: bool = True
    # Minimum factor confidence to count
    min_factor_confidence: float = 0.45


@dataclass
class ExecutionConfig:
    live_trading: bool = field(
        default_factory=lambda: os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "true"
    )
    paper_trading: bool = True
    backtest_required_before_live: bool = True
    backtest_min_trades: int = 30
    backtest_min_win_rate: float = 0.45
    backtest_min_expectancy: float = 0.5
    slippage_pct: float = 0.001          # 0.1% slippage for paper trading
    commission_pct: float = 0.0004       # 0.04% Binance taker fee


@dataclass
class ExternalDataConfig:
    use_fear_greed: bool = field(
        default_factory=lambda: os.getenv("USE_FEAR_GREED", "true").lower() == "true"
    )
    use_news_sentiment: bool = field(
        default_factory=lambda: os.getenv("USE_NEWS_SENTIMENT", "true").lower() == "true"
    )
    use_macro_calendar: bool = field(
        default_factory=lambda: os.getenv("USE_MACRO_CALENDAR", "true").lower() == "true"
    )
    news_max_age_hours: float = 24.0     # Ignore articles older than this
    news_min_relevance: float = 0.3      # Minimum relevance score to count
    macro_days_ahead: int = 7            # Calendar look-ahead window
    # Fear & Greed thresholds for contrarian signal
    extreme_fear_threshold: int = 20     # ≤ this → contrarian long signal
    extreme_greed_threshold: int = 80    # ≥ this → contrarian short / caution
    # News impact on signal score (additive modifier, capped)
    news_max_score_modifier: float = 0.10  # Max ±0.10 to signal score
    macro_high_impact_penalty: float = 0.15  # Subtract from confidence when high-impact event imminent


@dataclass
class AIConfig:
    enabled: bool = True
    model: str = "gemini-2.5-flash"            # Gemini free tier model
    max_tokens: int = 1024
    explain_no_trade: bool = True
    explain_trades: bool = True
    api_key: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))


@dataclass
class LogConfig:
    level: str = "INFO"
    log_dir: str = "logs"
    journal_dir: str = "logs/journal"
    backtest_dir: str = "logs/backtest"
    format: str = "json"
    log_no_trade: bool = True            # Log NO_TRADE decisions with reasoning
    log_features: bool = False           # Verbose feature logging (expensive)
    max_log_files: int = 30


@dataclass
class AgentConfig:
    symbol: str = field(default_factory=lambda: os.getenv("SYMBOL", "BTCUSDT"))
    cycle_interval_seconds: int = 60

    binance: BinanceConfig = field(default_factory=BinanceConfig)
    data: DataConfig = field(default_factory=DataConfig)
    features: FeatureConfig = field(default_factory=FeatureConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    signal: SignalConfig = field(default_factory=SignalConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    external: ExternalDataConfig = field(default_factory=ExternalDataConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    logging: LogConfig = field(default_factory=LogConfig)


DEFAULT_CONFIG = AgentConfig()
