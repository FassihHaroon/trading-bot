"""
Decision Engine — final integration point.
Takes FeatureSet → runs context → strategies → scoring → risk → produces TradeSignal.
Knowledge refs: NO_CERTAINTY_CLAIMS, MANDATORY_REASONING_TRACE,
                PROCESS_LOGGING_COMPLETE, NO_TRADE_IS_VALID_OUTPUT
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from data.schemas import (
    MarketSnapshot, FeatureSet, MarketContext,
    ScoredSignal, RiskAssessment, TradeSignal, Direction,
)
from config.settings import AgentConfig
from features import FeaturePipeline
from context.market_context import MarketContextEngine
from strategies.registry import StrategyRegistry
from scoring.signal_scorer import SignalScorer
from risk.risk_manager import RiskManager

logger = logging.getLogger(__name__)


class DecisionEngine:
    """
    Orchestrates the full analysis pipeline for one market cycle.
    Produces a TradeSignal with complete reasoning trace.
    """

    def __init__(self, config: AgentConfig, risk_manager: Optional[RiskManager] = None):
        self.config = config
        self.features_pipeline = FeaturePipeline(config)
        self.context_engine = MarketContextEngine(config)
        self.strategy_registry = StrategyRegistry(config)
        self.signal_scorer = SignalScorer(config)
        self.risk_manager = risk_manager or RiskManager(config)

    def run(self, snapshot: MarketSnapshot) -> TradeSignal:
        """
        Full decision cycle. Returns TradeSignal (always — including NO_TRADE).

        Pipeline:
          MarketSnapshot → FeatureSet → MarketContext → [StrategyResult×N]
          → ScoredSignal → RiskAssessment → TradeSignal
        """
        cycle_start = time.monotonic()
        symbol = snapshot.symbol
        ts = snapshot.timestamp

        logger.info("=== Decision cycle: %s @ %d ===", symbol, ts)

        # ── Step 1: Feature Extraction ────────────────────────────────────────
        try:
            features: FeatureSet = self.features_pipeline.extract(snapshot)
            if features.extraction_errors:
                logger.warning("Feature extraction errors: %s", features.extraction_errors)
        except Exception as exc:
            logger.error("Feature pipeline failed: %s", exc, exc_info=True)
            return self._no_trade(symbol, ts, f"Feature extraction failed: {exc}")

        # ── Step 2: Market Context ────────────────────────────────────────────
        try:
            context: MarketContext = self.context_engine.classify(features)
        except Exception as exc:
            logger.error("Context engine failed: %s", exc, exc_info=True)
            return self._no_trade(symbol, ts, f"Context engine failed: {exc}")

        logger.info(
            "Context: regime=%s trend=%s phase=%s confidence=%.2f",
            context.regime.value, context.trend_direction.value,
            context.phase.value, context.context_confidence,
        )

        # ── Step 3: Choppy market gate ────────────────────────────────────────
        from data.schemas import MarketRegime
        if context.regime == MarketRegime.CHOPPY:
            return self._no_trade(
                symbol, ts,
                "CHOPPY_MARKET_GATE: regime is choppy — no strategy eligible (NO_TRADE_IS_VALID_OUTPUT)",
                features=features, context=context,
            )

        # ── Step 4: Strategy Evaluation ───────────────────────────────────────
        try:
            strategy_results = self.strategy_registry.evaluate_all(features, context)
        except Exception as exc:
            logger.error("Strategy registry failed: %s", exc, exc_info=True)
            return self._no_trade(symbol, ts, f"Strategy evaluation failed: {exc}")

        valid_count = sum(1 for r in strategy_results if r.is_valid)
        logger.info("Strategies evaluated: %d valid / %d total", valid_count, len(strategy_results))

        # ── Step 5: Signal Scoring ────────────────────────────────────────────
        try:
            scored: ScoredSignal = self.signal_scorer.score(strategy_results, features, context)
        except Exception as exc:
            logger.error("Signal scorer failed: %s", exc, exc_info=True)
            return self._no_trade(symbol, ts, f"Signal scoring failed: {exc}")

        if scored.direction == Direction.NO_TRADE:
            return self._no_trade(
                symbol, ts,
                f"SCORED_NO_TRADE: {'; '.join(scored.no_trade_reasons)}",
                features=features, context=context, scored=scored,
            )

        # ── Step 6: Risk Assessment ───────────────────────────────────────────
        try:
            risk: RiskAssessment = self.risk_manager.assess(scored)
        except Exception as exc:
            logger.error("Risk manager failed: %s", exc, exc_info=True)
            return self._no_trade(symbol, ts, f"Risk manager failed: {exc}")

        if not risk.signal_approved:
            return self._no_trade(
                symbol, ts,
                f"RISK_REJECTED: {risk.rejection_reason}",
                features=features, context=context, scored=scored, risk=risk,
            )

        # ── Step 7: Assemble TradeSignal ─────────────────────────────────────
        elapsed_ms = (time.monotonic() - cycle_start) * 1000

        signal = TradeSignal(
            symbol=symbol,
            timestamp=ts,
            direction=scored.direction,
            confidence=scored.aggregate_confidence,
            entry_price=scored.entry_price,
            stop_price=scored.stop_price,
            targets=scored.targets,
            position_size=risk.position_size,
            risk_amount=risk.risk_amount,
            risk_reward_ratio=risk.risk_reward_ratio,
            market_context=context,
            strategy_results=strategy_results,
            scored_signal=scored,
            risk_assessment=risk,
            knowledge_refs=scored.knowledge_refs,
            rules_fired=self._collect_rules(strategy_results, scored),
            is_paper_trade=not self.config.execution.live_trading,
        )

        logger.info(
            "SIGNAL APPROVED: %s | cycle=%.0fms\n  %s",
            symbol, elapsed_ms, signal.to_summary(),
        )

        # Trace log for review
        self._log_reasoning_trace(signal, features, context, scored, risk)

        return signal

    # ──────────────────────────────────────────────
    # Internal
    # ──────────────────────────────────────────────

    def _no_trade(
        self,
        symbol: str,
        timestamp: int,
        reason: str,
        features: Optional[FeatureSet] = None,
        context: Optional[MarketContext] = None,
        scored: Optional[ScoredSignal] = None,
        risk: Optional[RiskAssessment] = None,
    ) -> TradeSignal:
        """
        Produces a NO_TRADE signal with full reasoning trace.
        PROCESS_LOGGING_COMPLETE: skipped trades are logged as completely as executed trades.
        """
        logger.info("NO_TRADE: %s | %s", symbol, reason)
        return TradeSignal(
            symbol=symbol,
            timestamp=timestamp,
            direction=Direction.NO_TRADE,
            confidence=0.0,
            market_context=context,
            strategy_results=scored.valid_strategies if scored else [],
            scored_signal=scored,
            risk_assessment=risk,
            no_trade_reason=reason,
            is_paper_trade=True,
        )

    def _collect_rules(
        self, strategy_results: list, scored: ScoredSignal
    ) -> list[str]:
        rules: set[str] = set(scored.knowledge_refs)
        for r in strategy_results:
            if r.is_valid:
                rules.update(r.knowledge_refs)
        return sorted(rules)

    def _log_reasoning_trace(
        self,
        signal: TradeSignal,
        features: FeatureSet,
        context: MarketContext,
        scored: ScoredSignal,
        risk: RiskAssessment,
    ) -> None:
        """
        Human-readable reasoning trace — NO_CERTAINTY_CLAIMS: uses probabilistic language.
        """
        trace_lines = [
            "─" * 60,
            f"TRADE SIGNAL REASONING TRACE",
            f"Symbol: {signal.symbol}  Direction: {signal.direction.value.upper()}",
            f"Confidence: {signal.confidence:.0%} (probabilistic estimate — not certainty)",
            f"",
            f"MARKET CONTEXT:",
            f"  Regime: {context.regime.value}",
            f"  Trend: {context.trend_direction.value}",
            f"  Phase: {context.phase.value}",
            f"  Volatility: {context.volatility_level.value}",
            f"  Volume quality: {context.volume_quality}",
            f"",
            f"STRATEGIES ({len(scored.valid_strategies)} agreed):",
        ]
        for s in scored.valid_strategies:
            trace_lines.append(f"  ✓ {s.strategy_name} — confidence {s.confidence:.0%}")
            for ev in s.supporting_evidence[:2]:
                trace_lines.append(f"    • {ev}")

        trace_lines += [
            f"",
            f"EXECUTION:",
            f"  Entry: {signal.entry_price:.4f}",
            f"  Stop:  {signal.stop_price:.4f}  (technically-based — STOP_TECHNICALLY_BASED)",
            f"  Targets: {[f'{t:.4f}' for t in signal.targets]}",
            f"  R:R: {signal.risk_reward_ratio:.1f}:1",
            f"  Position size: {signal.position_size:.4f}",
            f"  Risk amount: ${signal.risk_amount:.2f} ({risk.risk_pct:.1%} of equity)",
            f"",
            f"KNOWLEDGE REFS: {', '.join(signal.rules_fired[:8])}",
            f"─" * 60,
        ]
        for line in trace_lines:
            logger.info(line)
