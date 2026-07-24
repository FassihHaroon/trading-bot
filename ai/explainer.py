"""
AI Explainer — uses Google Gemini to generate human-readable trade reports.
Gemini is used for explanation only. It does NOT generate trade signals.
All signals come from the quantitative pipeline.

Free tier limits (as of 2025):
  gemini-2.0-flash : 15 RPM, 1M TPM, 1500 RPD — well within trading bot needs.

Uses the new google-genai SDK (google-generativeai is deprecated).
"""

from __future__ import annotations

import logging
from typing import Optional

from data.schemas import TradeSignal, Direction, BacktestResult
from config.settings import AgentConfig

logger = logging.getLogger(__name__)


class TradeExplainer:
    """
    Wraps Gemini API to explain trade decisions in natural language.
    Explanations are generated AFTER the quantitative system decides — not before.
    The AI role is explanation only, never signal generation.
    """

    def __init__(self, config: AgentConfig):
        self.config = config
        self.ai = config.ai
        self._client = None

        if self.ai.enabled and self.ai.api_key:
            self._init_client()

    def _init_client(self) -> None:
        try:
            from google import genai
            self._client = genai.Client(api_key=self.ai.api_key)
            logger.info("Gemini AI explainer initialized: model=%s", self.ai.model)
        except ImportError:
            logger.warning(
                "google-genai not installed — run: pip install google-genai"
            )
        except Exception as exc:
            logger.warning("Failed to init Gemini client: %s", exc)

    # ── Public methods ────────────────────────────────────────────────────────

    def explain_trade(self, signal: TradeSignal) -> Optional[str]:
        """Generate a plain-English explanation of an approved trade signal."""
        if not self._client or not self.ai.explain_trades:
            return None
        if signal.direction == Direction.NO_TRADE:
            return self.explain_no_trade(signal)
        return self._call(self._build_trade_prompt(signal))

    def explain_no_trade(self, signal: TradeSignal) -> Optional[str]:
        """Explain why no trade was taken this cycle."""
        if not self._client or not self.ai.explain_no_trade:
            return None
        return self._call(self._build_no_trade_prompt(signal))

    def explain_market(self, signal: TradeSignal) -> Optional[str]:
        """Summarize current market conditions."""
        if not self._client:
            return None
        ctx = signal.market_context
        if not ctx:
            return None

        prompt = f"""You are a professional trading analyst. Summarize the current market conditions concisely.

Market Data:
- Symbol: {signal.symbol}
- Regime: {ctx.regime.value}
- Trend direction: {ctx.trend_direction.value}
- Market phase: {ctx.phase.value}
- Volatility: {ctx.volatility_level.value}
- Volume quality: {ctx.volume_quality}
- Context confidence: {ctx.context_confidence:.0%}
- Context reasoning: {'; '.join(ctx.reasoning[:3])}

Write 2-3 sentences for a professional trader. Use probabilistic language \
(e.g. "appears to favor", "suggests", "historically associated with"). \
Do not predict the future or claim certainty."""

        return self._call(prompt)

    def explain_backtest(self, result: BacktestResult) -> Optional[str]:
        """Summarize backtest results in plain English."""
        if not self._model:
            return None

        best_strategy = (
            max(result.strategy_breakdown.items(), key=lambda x: x[1].get("pnl", 0))[0]
            if result.strategy_breakdown else "N/A"
        )

        prompt = f"""You are a quantitative trading analyst. Summarize these backtest results.

Results:
- Symbol: {result.symbol}
- Total trades: {result.total_trades}
- Win rate: {result.win_rate:.1%}
- Profit factor: {result.profit_factor:.2f}
- Sharpe ratio: {result.sharpe_ratio:.2f}
- Sortino ratio: {result.sortino_ratio:.2f}
- Max drawdown: {result.max_drawdown_pct:.1%}
- Expectancy: {result.expectancy:.2f}R per trade
- Total return: {result.total_return_pct:.1%}
- Validation passed: {result.validation_passed}
- Best performing strategy: {best_strategy}

Provide a professional assessment in 3-4 sentences. Identify the key strengths \
and weaknesses. State clearly whether this system should be considered for \
live trading based on the metrics (validation_passed={result.validation_passed})."""

        return self._call(prompt)

    def generate_performance_report(self, report: dict) -> Optional[str]:
        """
        Generate a natural-language performance report from PerformanceAnalytics.generate_report().
        This is the main method used for on-demand AI reports.
        """
        if not self._client:
            return None

        summary = report.get("summary", {})
        streaks = report.get("win_loss_streaks", {})
        failures = report.get("failure_analysis", {})

        # Top 3 strategies by PnL
        strats = report.get("strategy_performance", {})
        top_strats = sorted(strats.items(), key=lambda x: x[1].get("total_pnl", 0), reverse=True)[:3]
        top_strats_str = "\n".join(
            f"  {name}: {d['trades']} trades, {d['win_rate']:.0%} WR, ${d['total_pnl']:.2f} PnL"
            for name, d in top_strats
        )

        prompt = f"""You are a professional quantitative trading analyst reviewing a trading bot's performance report.

OVERALL SUMMARY:
- Total trades: {summary.get('total_trades', 0)}
- Win rate: {summary.get('win_rate', 0):.1%}
- Profit factor: {summary.get('profit_factor', 0):.2f}
- Total PnL: ${summary.get('total_pnl', 0):.2f}
- Average R-multiple: {summary.get('avg_r_multiple', 0):.2f}R
- Best trade: {summary.get('best_trade_r', 0):.2f}R  |  Worst trade: {summary.get('worst_trade_r', 0):.2f}R

TOP STRATEGIES:
{top_strats_str if top_strats_str else "No strategy data yet"}

STREAKS:
- Max win streak: {streaks.get('max_win_streak', 0)}
- Max loss streak: {streaks.get('max_loss_streak', 0)}

FAILURE ANALYSIS:
- Total losses: {failures.get('total_losses', 0)}
- Most common exit reason: {list(failures.get('loss_by_exit_reason', {}).keys())[:1]}

Provide a clear, actionable report in 4-5 sentences covering:
1. Overall system health (profitable / breaking even / losing)
2. Which strategy is performing best and why it likely works
3. The most important risk or weakness to address
4. A concrete recommendation (continue paper trading / adjust parameters / consider live)

Use professional language. Never claim certainty about future performance."""

        return self._call(prompt)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _build_trade_prompt(self, signal: TradeSignal) -> str:
        ctx = signal.market_context
        strategies = [r.strategy_name for r in signal.strategy_results if r.is_valid]
        evidence = []
        for r in signal.strategy_results:
            if r.is_valid:
                evidence.extend(r.supporting_evidence[:2])

        return f"""You are a professional trading analyst. Explain this trade signal to a trader.

IMPORTANT: Use probabilistic language. Never claim certainty. \
This is decision-support, not a guarantee.

Signal Details:
- Symbol: {signal.symbol}
- Direction: {signal.direction.value.upper()}
- Confidence: {signal.confidence:.0%} (probability estimate, not certainty)
- Entry: {signal.entry_price}
- Stop: {signal.stop_price} (STOP_REQUIRED_BEFORE_SIGNAL rule)
- Targets: {signal.targets}
- Risk:Reward: {signal.risk_reward_ratio:.1f}:1
- Position size: {signal.position_size:.4f} (sized by stop distance only)

Market Context:
- Regime: {ctx.regime.value if ctx else 'N/A'}
- Phase: {ctx.phase.value if ctx else 'N/A'}

Strategies in agreement: {', '.join(strategies)}

Supporting evidence:
{chr(10).join(f'- {e}' for e in evidence[:6])}

Knowledge base rules fired: {', '.join(signal.rules_fired[:5])}

Write 3-4 sentences: (1) why this setup was identified, \
(2) what conditions must hold for it to work, \
(3) what would invalidate the trade. \
Use language like "the analysis suggests", "historically this pattern", "if price holds"."""

    def _build_no_trade_prompt(self, signal: TradeSignal) -> str:
        return f"""You are a professional trading analyst. Explain why no trade was taken.

NO_TRADE_IS_VALID_OUTPUT: Not trading when conditions are poor is disciplined execution, not failure.

Symbol: {signal.symbol}
Reason: {signal.no_trade_reason}
Regime: {signal.market_context.regime.value if signal.market_context else 'N/A'}
Context reasoning: {'; '.join((signal.market_context.reasoning or [])[:2]) if signal.market_context else 'N/A'}

Write 2 sentences: (1) the primary reason no trade was taken, \
(2) what conditions would need to change for a valid signal to emerge."""

    def _call(self, prompt: str) -> Optional[str]:
        try:
            from google.genai import types
            response = self._client.models.generate_content(
                model=self.ai.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    max_output_tokens=self.ai.max_tokens,
                ),
            )
            return response.text.strip()
        except Exception as exc:
            logger.warning("Gemini AI call failed: %s", exc)
            return None
