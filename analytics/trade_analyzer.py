"""
Self-Learning Trade Analyzer.

After each trade closes this module reads the full journal, computes which
strategies and market regimes are profitable, and writes an adaptive weight
file that SignalScorer loads to improve future signals.

Design principles:
- Rolling window (last N trades) so weights track *current* performance, not stale history.
- Per-regime performance so weights shift based on what's working NOW in this market.
- Losing pattern detection: identifies common features of consecutive losses so the engine
  can tighten confidence gates when a losing pattern is active.
- All learning is saved to logs/learned_state.json (human-readable JSON) — easy to inspect.
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_LEARNED_STATE_FILE = Path("logs/learned_state.json")
_ROLLING_WINDOW = 30          # Trades to consider for adaptive weights
_MIN_TRADES_TO_LEARN = 5      # Need at least N trades before adjusting weights
_MAX_WEIGHT_BOOST = 2.0       # Cap how much a strategy weight can be boosted
_MIN_WEIGHT_FLOOR = 0.2       # Floor: never fully ignore a strategy


class TradeAnalyzer:
    """
    Learns from closed trades and produces adaptive signal weights.

    Usage:
        analyzer = TradeAnalyzer()
        report = analyzer.analyze_journal("logs/journal/trades.jsonl")
        analyzer.save(report)
    """

    def __init__(self, state_file: Path = _LEARNED_STATE_FILE):
        self.state_file = state_file
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

    # ──────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────

    def analyze_journal(self, trades_file: str | Path) -> dict:
        """
        Read the trades journal and produce a learning report.

        Returns a dict with:
          - adaptive_weights: {strategy_id -> multiplier}
          - regime_weights:   {regime -> {strategy_id -> multiplier}}
          - losing_patterns:  list of pattern descriptions (for logs)
          - summary:          quick stats
        """
        trades = self._load_closed_trades(Path(trades_file))
        if not trades:
            logger.info("Trade analyzer: no closed trades found — using base weights")
            return self._empty_report()

        logger.info("Trade analyzer: loaded %d closed trades", len(trades))

        # Use rolling window only
        window = trades[-_ROLLING_WINDOW:]

        adaptive_weights  = self._compute_adaptive_weights(window)
        regime_weights    = self._compute_regime_weights(window)
        losing_patterns   = self._detect_losing_patterns(window)
        summary           = self._build_summary(window, trades)
        confidence_adjustment = self._compute_confidence_adjustment(window)

        report = {
            "updated_at": time.time(),
            "total_trades_analyzed": len(trades),
            "window_trades": len(window),
            "adaptive_weights": adaptive_weights,
            "regime_weights": regime_weights,
            "losing_patterns": losing_patterns,
            "confidence_adjustment": confidence_adjustment,
            "summary": summary,
        }

        self._log_report(report)
        return report

    def save(self, report: dict) -> None:
        """Persist the learning report to disk."""
        try:
            self.state_file.write_text(json.dumps(report, indent=2, default=str))
            logger.info("Trade analyzer: saved learned state → %s", self.state_file)
        except Exception as exc:
            logger.error("Trade analyzer: failed to save state: %s", exc)

    def load(self) -> Optional[dict]:
        """Load the previously saved learning report (returns None if not found)."""
        if not self.state_file.exists():
            return None
        try:
            data = json.loads(self.state_file.read_text())
            age_hours = (time.time() - data.get("updated_at", 0)) / 3600
            logger.info(
                "Trade analyzer: loaded learned state (%.1f hours old, %d trades)",
                age_hours, data.get("total_trades_analyzed", 0),
            )
            return data
        except Exception as exc:
            logger.warning("Trade analyzer: could not load state: %s", exc)
            return None

    def run(self, journal_dir: str | Path = "logs/journal") -> dict:
        """Convenience: analyze + save in one call."""
        trades_file = Path(journal_dir) / "trades.jsonl"
        report = self.analyze_journal(trades_file)
        self.save(report)
        return report

    # ──────────────────────────────────────────────
    # Analysis core
    # ──────────────────────────────────────────────

    def _load_closed_trades(self, path: Path) -> list[dict]:
        """Load only exit records (fully closed trades) from the JSONL journal."""
        if not path.exists():
            return []
        trades = []
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    if record.get("event") == "exit" and record.get("outcome") in ("win", "loss", "breakeven"):
                        trades.append(record)
                except json.JSONDecodeError:
                    continue
        return trades

    def _compute_adaptive_weights(self, window: list[dict]) -> dict:
        """
        Compute a weight multiplier per strategy based on recent win rates.

        Formula:
          win_rate_i  = wins_i / trades_i  (within window)
          multiplier  = clamp(0.5 + win_rate_i, MIN, MAX)

        A strategy with 70% win rate gets multiplier=1.2, boosting its weight.
        A strategy with 20% win rate gets multiplier=0.7, reducing its weight.
        """
        if len(window) < _MIN_TRADES_TO_LEARN:
            return {}

        stats: dict[str, dict] = defaultdict(lambda: {"wins": 0, "trades": 0, "pnl": 0.0})

        for trade in window:
            outcome = trade.get("outcome", "loss")
            pnl = float(trade.get("realized_pnl") or 0.0)
            for strat in trade.get("strategies", []):
                stats[strat]["trades"] += 1
                stats[strat]["pnl"] += pnl
                if outcome == "win":
                    stats[strat]["wins"] += 1

        multipliers = {}
        for strat, s in stats.items():
            if s["trades"] < 3:
                continue  # not enough data for this strategy
            win_rate = s["wins"] / s["trades"]
            # multiplier: 0.5 + win_rate → range [0.5, 1.5] before clamp
            mult = 0.5 + win_rate
            mult = max(_MIN_WEIGHT_FLOOR, min(_MAX_WEIGHT_BOOST, mult))
            multipliers[strat] = round(mult, 4)
            logger.debug(
                "  %s: %d trades, %.0f%% win rate → multiplier=%.3f",
                strat, s["trades"], win_rate * 100, mult,
            )

        return multipliers

    def _compute_regime_weights(self, window: list[dict]) -> dict:
        """
        Per-regime strategy win rates.
        Returns {regime -> {strategy -> multiplier}}.
        """
        if len(window) < _MIN_TRADES_TO_LEARN:
            return {}

        regime_stats: dict[str, dict[str, dict]] = defaultdict(
            lambda: defaultdict(lambda: {"wins": 0, "trades": 0})
        )

        for trade in window:
            regime = trade.get("regime", "unknown") or "unknown"
            outcome = trade.get("outcome", "loss")
            for strat in trade.get("strategies", []):
                regime_stats[regime][strat]["trades"] += 1
                if outcome == "win":
                    regime_stats[regime][strat]["wins"] += 1

        result = {}
        for regime, strats in regime_stats.items():
            result[regime] = {}
            for strat, s in strats.items():
                if s["trades"] < 2:
                    continue
                win_rate = s["wins"] / s["trades"]
                mult = max(_MIN_WEIGHT_FLOOR, min(_MAX_WEIGHT_BOOST, 0.5 + win_rate))
                result[regime][strat] = round(mult, 4)

        return result

    def _detect_losing_patterns(self, window: list[dict]) -> list[str]:
        """
        Identify patterns that appear frequently in losing trades.
        Returns human-readable descriptions for logging / future gates.
        """
        patterns = []
        losses = [t for t in window if t.get("outcome") == "loss"]

        if not losses:
            return ["No losing patterns detected — excellent!"]

        # Pattern 1: regime-specific losing streaks
        regime_loss: dict[str, int] = defaultdict(int)
        regime_total: dict[str, int] = defaultdict(int)
        for t in window:
            r = t.get("regime", "unknown")
            regime_total[r] += 1
            if t.get("outcome") == "loss":
                regime_loss[r] += 1

        for regime, loss_count in regime_loss.items():
            total = regime_total[regime]
            if total >= 3 and loss_count / total >= 0.70:
                patterns.append(
                    f"HIGH_LOSS_REGIME: '{regime}' — {loss_count}/{total} trades lost "
                    f"({loss_count/total*100:.0f}%). Consider reducing position size or avoiding."
                )

        # Pattern 2: strategy-specific losing streaks
        strat_loss: dict[str, int] = defaultdict(int)
        strat_total: dict[str, int] = defaultdict(int)
        for t in window:
            for s in t.get("strategies", []):
                strat_total[s] += 1
                if t.get("outcome") == "loss":
                    strat_loss[s] += 1

        for strat, loss_count in strat_loss.items():
            total = strat_total[strat]
            if total >= 3 and loss_count / total >= 0.70:
                patterns.append(
                    f"HIGH_LOSS_STRATEGY: '{strat}' — {loss_count}/{total} trades lost "
                    f"({loss_count/total*100:.0f}%). Weight will be reduced automatically."
                )

        # Pattern 3: consecutive losses
        consecutive = 0
        max_consecutive = 0
        for t in window:
            if t.get("outcome") == "loss":
                consecutive += 1
                max_consecutive = max(max_consecutive, consecutive)
            else:
                consecutive = 0

        if max_consecutive >= 4:
            patterns.append(
                f"CONSECUTIVE_LOSS_STREAK: {max_consecutive} in a row in last {len(window)} trades. "
                "Consider pausing or tightening confidence thresholds."
            )

        # Pattern 4: poor R:R realization
        realized_rs = [float(t.get("realized_r") or 0) for t in losses]
        if realized_rs:
            avg_loss_r = sum(realized_rs) / len(realized_rs)
            if avg_loss_r < -1.5:
                patterns.append(
                    f"STOP_TOO_FAR: Average loss = {avg_loss_r:.2f}R. "
                    "Stops may be too wide — tighten stop placement or reduce max loss."
                )

        return patterns if patterns else ["No significant losing patterns detected"]

    def _compute_confidence_adjustment(self, window: list[dict]) -> float:
        """
        Compute a global confidence gate adjustment based on recent win rate.

        If win rate is below 40%, raise the required confidence by up to +0.10.
        If win rate is above 60%, lower required confidence by up to -0.05.
        This makes the bot more selective when it's been losing.
        """
        if len(window) < _MIN_TRADES_TO_LEARN:
            return 0.0

        wins = sum(1 for t in window if t.get("outcome") == "win")
        win_rate = wins / len(window)

        if win_rate < 0.40:
            # Scale penalty: 40% → 0.0, 30% → +0.05, 20% → +0.10
            adjustment = (0.40 - win_rate) * 0.5
            return round(min(adjustment, 0.10), 4)
        elif win_rate > 0.60:
            # Slight relaxation when doing well
            return round(-(win_rate - 0.60) * 0.25, 4)
        return 0.0

    def _build_summary(self, window: list[dict], all_trades: list[dict]) -> dict:
        """Build a quick summary dict for the report."""
        wins = [t for t in window if t.get("outcome") == "win"]
        losses = [t for t in window if t.get("outcome") == "loss"]
        pnls = [float(t.get("realized_pnl") or 0) for t in window]
        rs   = [float(t.get("realized_r") or 0) for t in window]

        gross_win  = sum(p for p in pnls if p > 0)
        gross_loss = abs(sum(p for p in pnls if p < 0))

        return {
            "window_size": len(window),
            "total_closed": len(all_trades),
            "win_rate": round(len(wins) / len(window), 4) if window else 0,
            "wins": len(wins),
            "losses": len(losses),
            "total_pnl": round(sum(pnls), 2),
            "avg_r": round(sum(rs) / len(rs), 4) if rs else 0,
            "profit_factor": round(gross_win / gross_loss, 4) if gross_loss > 0 else 999.0,
            "expectancy_r": round(sum(rs) / len(rs), 4) if rs else 0,
        }

    def _log_report(self, report: dict) -> None:
        s = report["summary"]
        logger.info(
            "Trade Analyzer Results: %d trades | Win Rate=%.1f%% | Avg R=%.2f | "
            "Profit Factor=%.2f | PnL=%.2f",
            s["window_size"],
            s["win_rate"] * 100,
            s["avg_r"],
            s["profit_factor"],
            s["total_pnl"],
        )
        for pattern in report["losing_patterns"]:
            logger.warning("PATTERN: %s", pattern)

        if report["adaptive_weights"]:
            logger.info("Adaptive weights: %s", report["adaptive_weights"])

        if report["confidence_adjustment"] != 0.0:
            logger.info(
                "Confidence gate adjustment: %+.3f (win rate triggered)",
                report["confidence_adjustment"],
            )

    def _empty_report(self) -> dict:
        return {
            "updated_at": time.time(),
            "total_trades_analyzed": 0,
            "window_trades": 0,
            "adaptive_weights": {},
            "regime_weights": {},
            "losing_patterns": [],
            "confidence_adjustment": 0.0,
            "summary": {"win_rate": 0, "wins": 0, "losses": 0},
        }
