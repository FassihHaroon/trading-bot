"""
FastAPI backend for the trading bot dashboard.
Pure analysis only — no trades are opened or closed ever.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ── Ensure project root is on the path ──────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import AgentConfig
from data.connectors.market_data_service import MarketDataService
from data.schemas import Direction
from engine.decision_engine import DecisionEngine
from risk.risk_manager import RiskManager
from ai.explainer import TradeExplainer

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Popular symbols ───────────────────────────────────────────────────────────
POPULAR_SYMBOLS: List[str] = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "MATICUSDT",
    "LINKUSDT", "LTCUSDT", "UNIUSDT", "NEARUSDT", "ATOMUSDT",
    "INJUSDT", "SUIUSDT", "ARBUSDT", "OPUSDT", "APTUSDT",
]

DEFAULT_SCANNER_SYMBOLS: List[str] = POPULAR_SYMBOLS[:15]

# ── Cache ─────────────────────────────────────────────────────────────────────
CACHE_TTL_SECONDS = 60
_analysis_cache: Dict[str, Dict[str, Any]] = {}   # symbol -> {result, expires_at}

# ── Thread pool ───────────────────────────────────────────────────────────────
_executor = ThreadPoolExecutor(max_workers=4)

# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="Trading Bot Dashboard API",
    description="Read-only analysis API for the trading bot dashboard.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Singletons (lazy-initialised) ────────────────────────────────────────────
_main_config: Optional[AgentConfig] = None
_scanner_config: Optional[AgentConfig] = None


def _get_main_config() -> AgentConfig:
    global _main_config
    if _main_config is None:
        _main_config = AgentConfig()
        # Guarantee live trading is never enabled
        _main_config.execution.live_trading = False
    return _main_config


def _get_scanner_config() -> AgentConfig:
    """Scanner config with external data disabled for speed."""
    global _scanner_config
    if _scanner_config is None:
        cfg = AgentConfig()
        cfg.execution.live_trading = False
        cfg.data.use_external_data = False
        _scanner_config = cfg
    return _scanner_config


# ── Pydantic models ───────────────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    symbol: str


class StrategyDetail(BaseModel):
    id: str
    valid: bool
    confidence: float
    evidence: List[str]


class AnalysisResult(BaseModel):
    symbol: str
    direction: str
    confidence: float
    regime: Optional[str]
    trend: Optional[str]
    phase: Optional[str]
    entry_price: Optional[float]
    stop_price: Optional[float]
    targets: List[float]
    risk_reward: float
    position_size: float
    risk_amount: float
    strategies_used: List[str]
    no_trade_reason: Optional[str]
    is_actionable: bool
    ai_explanation: Optional[str]
    strategy_details: List[StrategyDetail]
    analyzed_at: str
    cached: bool


# ── Core analysis function ────────────────────────────────────────────────────

def _run_analysis(symbol: str, config: AgentConfig, cached: bool = False) -> Dict[str, Any]:
    """
    Run the full analysis pipeline for a single symbol.
    This is a blocking call and must be run in a thread pool.
    Never opens or closes any trade.
    """
    market_svc = MarketDataService(config)
    snapshot = market_svc.get_snapshot(symbol)

    risk_manager = RiskManager(config)
    engine = DecisionEngine(config, risk_manager=risk_manager)
    signal = engine.run(snapshot)

    explainer = TradeExplainer(config)
    ai_explanation: Optional[str] = None
    try:
        if signal.is_actionable():
            ai_explanation = explainer.explain_trade(signal)
        else:
            ai_explanation = explainer.explain_no_trade(signal)
    except Exception as exc:
        logger.warning("AI explainer failed for %s: %s", symbol, exc)
        ai_explanation = None

    # Market context fields
    regime: Optional[str] = None
    trend: Optional[str] = None
    phase: Optional[str] = None
    if signal.market_context is not None:
        try:
            regime = signal.market_context.regime.value
        except Exception:
            pass
        try:
            trend = signal.market_context.trend_direction.value
        except Exception:
            pass
        try:
            phase = signal.market_context.phase.value
        except Exception:
            pass

    # Strategy details
    strategy_details: List[Dict[str, Any]] = []
    strategies_used: List[str] = []
    for sr in (signal.strategy_results or []):
        detail: Dict[str, Any] = {
            "id": sr.strategy_id,
            "valid": sr.is_valid,
            "confidence": sr.confidence,
            "evidence": list(sr.supporting_evidence or []),
        }
        strategy_details.append(detail)
        if sr.is_valid:
            strategies_used.append(sr.strategy_id)

    result: Dict[str, Any] = {
        "symbol": signal.symbol,
        "direction": signal.direction.value,
        "confidence": round(signal.confidence, 4),
        "regime": regime,
        "trend": trend,
        "phase": phase,
        "entry_price": signal.entry_price,
        "stop_price": signal.stop_price,
        "targets": list(signal.targets or []),
        "risk_reward": round(signal.risk_reward_ratio, 4),
        "position_size": round(signal.position_size, 6),
        "risk_amount": round(signal.risk_amount, 4),
        "strategies_used": strategies_used,
        "no_trade_reason": signal.no_trade_reason,
        "is_actionable": signal.is_actionable(),
        "ai_explanation": ai_explanation,
        "strategy_details": strategy_details,
        "analyzed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cached": cached,
    }
    return result


def _get_or_run_analysis(symbol: str, config: AgentConfig) -> Dict[str, Any]:
    """Return cached result if still valid, otherwise run fresh analysis."""
    symbol = symbol.upper()
    now = time.monotonic()
    cached_entry = _analysis_cache.get(symbol)
    if cached_entry and now < cached_entry["expires_at"]:
        result = dict(cached_entry["result"])
        result["cached"] = True
        return result

    result = _run_analysis(symbol, config, cached=False)
    _analysis_cache[symbol] = {
        "result": result,
        "expires_at": now + CACHE_TTL_SECONDS,
    }
    return result


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


@app.get("/api/symbols")
def get_symbols() -> Dict[str, Any]:
    return {"symbols": POPULAR_SYMBOLS}


@app.get("/api/popular-coins")
def get_popular_coins() -> Dict[str, Any]:
    return {"symbols": POPULAR_SYMBOLS}


@app.post("/api/analyze", response_model=AnalysisResult)
def analyze(request: AnalyzeRequest) -> Dict[str, Any]:
    symbol = request.symbol.strip().upper()
    if not symbol:
        raise HTTPException(status_code=400, detail="symbol is required")
    try:
        config = _get_main_config()
        future = _executor.submit(_get_or_run_analysis, symbol, config)
        result = future.result(timeout=120)
        return result
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Analysis failed for %s", symbol)
        raise HTTPException(status_code=500, detail=f"Analysis failed: {exc}") from exc


@app.get("/api/scanner")
def scanner(
    symbols: str = Query(
        default="",
        description="Comma-separated list of symbols. Defaults to top 15 coins.",
    )
) -> Dict[str, Any]:
    if symbols.strip():
        symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    else:
        symbol_list = list(DEFAULT_SCANNER_SYMBOLS)

    config = _get_scanner_config()

    futures_map = {
        _executor.submit(_get_or_run_analysis, sym, config): sym
        for sym in symbol_list
    }

    results: List[Dict[str, Any]] = []
    for future in as_completed(futures_map, timeout=180):
        sym = futures_map[future]
        try:
            result = future.result()
            # Skip NO_TRADE results for the scanner view
            if result.get("direction") == Direction.NO_TRADE.value:
                continue
            results.append(result)
        except Exception as exc:
            logger.warning("Scanner skipped %s: %s", sym, exc)

    # Sort by confidence descending
    results.sort(key=lambda r: r.get("confidence", 0.0), reverse=True)

    return {
        "count": len(results),
        "scanned": len(symbol_list),
        "results": results,
        "scanned_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


@app.get("/api/signals/recent")
def recent_signals(limit: int = Query(default=100, ge=1, le=500)) -> Dict[str, Any]:
    journal_path = PROJECT_ROOT / "logs" / "journal" / "decisions.jsonl"
    if not journal_path.exists():
        return {"count": 0, "signals": []}

    try:
        lines = journal_path.read_text(encoding="utf-8").splitlines()
        # Take the last `limit` non-empty lines
        recent_lines = [ln for ln in lines if ln.strip()][-limit:]
        signals: List[Any] = []
        for ln in recent_lines:
            try:
                signals.append(json.loads(ln))
            except json.JSONDecodeError:
                # Preserve raw line if not valid JSON
                signals.append({"raw": ln})
        return {"count": len(signals), "signals": signals}
    except Exception as exc:
        logger.exception("Failed to read journal")
        raise HTTPException(status_code=500, detail=f"Could not read journal: {exc}") from exc
