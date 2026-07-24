"""
features/__init__.py
====================
Exposes FeaturePipeline — the single entry-point for all feature extraction.

Usage
-----
    from features import FeaturePipeline
    pipeline = FeaturePipeline(config)
    feature_set = pipeline.extract(snapshot)
"""

from __future__ import annotations

import inspect
import logging
from typing import Any, Dict, List

from config.settings import AgentConfig
from data.schemas import FeatureSet, MarketSnapshot

# ── Individual extractor imports ──────────────────────────────────────────────
from features.trend import TrendExtractor
from features.swing_points import SwingPointExtractor
from features.support_resistance import SupportResistanceExtractor
from features.market_structure import MarketStructureExtractor
from features.volume import VolumeExtractor
from features.momentum import MomentumExtractor
from features.divergence import DivergenceExtractor
from features.candlestick import CandlestickExtractor
from features.chart_patterns import ChartPatternExtractor
from features.multi_timeframe import MultiTimeframeExtractor
from features.volatility import VolatilityExtractor
from features.session import SessionExtractor
from features.liquidity import LiquidityExtractor
from features.fibonacci import FibonacciExtractor
from features.external_data import ExternalDataExtractor

logger = logging.getLogger(__name__)

# Ordered list of (name, extractor_class) pairs that defines the pipeline order.
# external_data runs last so it can be skipped cleanly without affecting price features.
_EXTRACTOR_ORDER: List[tuple[str, type]] = [
    ("trend", TrendExtractor),
    ("swing_points", SwingPointExtractor),
    ("support_resistance", SupportResistanceExtractor),
    ("market_structure", MarketStructureExtractor),
    ("volume", VolumeExtractor),
    ("momentum", MomentumExtractor),
    ("divergence", DivergenceExtractor),
    ("candlestick", CandlestickExtractor),
    ("chart_patterns", ChartPatternExtractor),
    ("multi_timeframe", MultiTimeframeExtractor),
    ("volatility", VolatilityExtractor),
    ("session", SessionExtractor),
    ("liquidity", LiquidityExtractor),
    ("fibonacci", FibonacciExtractor),
    ("external_data", ExternalDataExtractor),
]


class FeaturePipeline:
    """
    Orchestrates all 13 feature extractors and merges their outputs into a
    single :class:`~data.schemas.FeatureSet`.

    Parameters
    ----------
    config:
        Top-level agent configuration.  Each extractor receives this same
        config object so that feature-level hyper-parameters are centralised
        in ``config.features``.
    """

    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self._extractors: List[tuple[str, Any]] = []
        for name, cls in _EXTRACTOR_ORDER:
            instance = self._instantiate(cls, config)
            self._extractors.append((name, instance))

    @staticmethod
    def _instantiate(cls: type, config: AgentConfig) -> Any:
        """
        Instantiate an extractor with the right config type.

        Extractor constructors are heterogeneous:
          - (AgentConfig)  → pass config directly
          - (FeatureConfig) → pass config.features
          - ()             → pass nothing (domain-specific defaults)
          - (lookback, timeframes, ...) → pass nothing

        Detection: inspect first non-self param name + annotation string.
        """
        try:
            params = list(inspect.signature(cls.__init__).parameters.values())
            non_self = [p for p in params if p.name != "self"]
            if not non_self:
                return cls()
            first = non_self[0]
            ann = str(first.annotation)
            if first.name == "config":
                if "FeatureConfig" in ann:
                    return cls(config.features)
                # AgentConfig, Optional[AgentConfig], or unannotated 'config'
                return cls(config)
            # First param is not named 'config' — domain-specific extractor
            return cls()
        except Exception:
            try:
                return cls(config)
            except TypeError:
                return cls()

    # ── Public interface ──────────────────────────────────────────────────────

    def extract(self, snapshot: MarketSnapshot) -> FeatureSet:
        """
        Run all extractors in order, mutating a shared FeatureSet in-place.

        Extractors use the signature extract(snapshot, feature_set) -> None.
        The ExternalDataExtractor uses extract(snapshot) -> dict (different
        pattern), handled via the dict branch below.

        Any extractor that raises is caught, logged, and skipped — its name
        is recorded in FeatureSet.extraction_errors.
        """
        errors: List[str] = []

        # Seed the FeatureSet with identity fields from the snapshot
        feature_set = FeatureSet(
            symbol=snapshot.symbol,
            timestamp=snapshot.timestamp,
            timeframes_available=list(snapshot.candles.keys()),
        )

        for name, extractor in self._extractors:
            try:
                result = extractor.extract(snapshot, feature_set)
                # Some extractors (e.g. ExternalDataExtractor) return a dict
                if isinstance(result, dict):
                    valid_fields = {f.name for f in FeatureSet.__dataclass_fields__.values()}  # type: ignore[attr-defined]
                    for k, v in result.items():
                        if k in valid_fields:
                            setattr(feature_set, k, v)
                # None return = mutated in-place, nothing to do
            except TypeError:
                # Fallback: extractor only takes snapshot (old single-arg style)
                try:
                    result = extractor.extract(snapshot)
                    if isinstance(result, dict):
                        valid_fields = {f.name for f in FeatureSet.__dataclass_fields__.values()}  # type: ignore[attr-defined]
                        for k, v in result.items():
                            if k in valid_fields:
                                setattr(feature_set, k, v)
                except Exception as exc:
                    logger.warning(
                        "Extractor '%s' failed: %s: %s", name, type(exc).__name__, exc,
                        exc_info=True,
                    )
                    errors.append(f"{name}: {type(exc).__name__}: {exc}")
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Extractor '%s' failed: %s: %s", name, type(exc).__name__, exc,
                    exc_info=True,
                )
                errors.append(f"{name}: {type(exc).__name__}: {exc}")

        if errors:
            feature_set.extraction_errors = errors

        return feature_set


__all__ = [
    "FeaturePipeline",
    # Extractor classes — re-exported for convenience
    "TrendExtractor",
    "SwingPointExtractor",
    "SupportResistanceExtractor",
    "MarketStructureExtractor",
    "VolumeExtractor",
    "MomentumExtractor",
    "DivergenceExtractor",
    "CandlestickExtractor",
    "ChartPatternExtractor",
    "MultiTimeframeExtractor",
    "VolatilityExtractor",
    "SessionExtractor",
    "LiquidityExtractor",
    "FibonacciExtractor",
    "ExternalDataExtractor",
]
