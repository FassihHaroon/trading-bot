# Chart Patterns
**Source concept**: Murphy — Technical Analysis of the Financial Markets
**Category**: Technical Analysis

---

## Core Principle

Chart patterns are recurring price formations that reflect shifts in supply/demand balance. They are categorized as **reversal** (signaling the end of a trend) or **continuation** (signaling a pause within a trend). Pattern validity depends heavily on volume confirmation — a pattern without volume is unreliable.

---

## Reversal Patterns

### Head and Shoulders (Bearish Reversal)
- **Structure**: Left shoulder (rally + pullback) → Head (higher rally + pullback) → Right shoulder (lower rally, symmetric with left) → Neckline break
- **Volume rule**: Volume typically highest on left shoulder, decreases on head and right shoulder. Volume should expand significantly on neckline breakdown.
- **Measured target**: Height of head above neckline, projected downward from neckline break.
- **Invalidation**: Price closes back above neckline after breakdown.

### Inverse Head and Shoulders (Bullish Reversal)
- Mirror image of above. Volume should expand on the breakout above neckline.

### Double Top (Bearish Reversal)
- Two roughly equal peaks separated by a trough. Second peak ideally forms on lower volume.
- Confirmed on break below the trough between the two peaks.
- **Invalidation**: New high above the second peak.

### Double Bottom (Bullish Reversal)
- Two roughly equal troughs. Volume expansion on breakout above the peak between the two lows.

### Rounding Top / Bottom
- Gradual, curved shift in sentiment. Less common in crypto (more common in slow-moving equities).

---

## Continuation Patterns

### Triangles
| Type | Shape | Bias |
|------|-------|------|
| Ascending | Flat top, rising bottom | Bullish |
| Descending | Flat bottom, falling top | Bearish |
| Symmetrical | Converging: lower highs + higher lows | Neutral until breakout |

- Volume should contract through triangle formation and expand sharply on breakout.
- The longer the compression, the more significant the breakout.
- Breakout in the direction of the preceding trend is more reliable.

### Flags and Pennants
- Short, tight consolidation against the prior impulse trend.
- Flag: rectangular consolidation (slight counter-trend slope).
- Pennant: small symmetrical triangle after an impulse.
- **Volume**: High on pole, very low during consolidation, high again on breakout.
- Measured target: Length of pole added to breakout point.

### Rectangle / Trading Range
- Price oscillates between a defined high and low. Breakout direction sets new trend.
- Not tradeable inside the range as a trend signal; useful only as breakout setup.

### Wedges
- Rising wedge (bearish): Price rises but in a tightening channel sloping up — bearish sign.
- Falling wedge (bullish): Price falls in a tightening channel sloping down — bullish sign.

---

## Volume Confirmation Matrix

| Pattern Stage | Expected Volume |
|--------------|-----------------|
| Formation | Declining (most patterns) |
| Breakout | Expanding (2x+ average) |
| Throwback/re-test | Declining |
| Resumption | Expanding |

Breakouts on declining or average volume are considered **low-probability** and receive a confidence penalty.

---

## Pattern Reliability Ranking (for confidence scoring)

| Pattern | Reliability (general) |
|---------|----------------------|
| Head & Shoulders | High |
| Inverse H&S | High |
| Double Top/Bottom | Medium-High |
| Rectangle breakout | Medium |
| Triangle breakout | Medium |
| Flag/Pennant | Medium-High (with volume) |
| Wedge breakout | Medium |

---

## rules:

```
CHART_PATTERN_RULES:
  - rule: VOLUME_CONFIRMATION_REQUIRED
    description: >
      No pattern signal fires without volume confirmation on the breakout candle.
      Minimum volume threshold: 1.5x the 20-bar average volume on the breakout bar.
      Patterns without volume confirmation are flagged as "unconfirmed" and excluded
      from the signal aggregation.

  - rule: PATTERN_INVALIDATION_TRACKING
    description: >
      Each detected pattern must have a defined invalidation level stored in the signal.
      If price reclaims the invalidation level, the pattern signal is cancelled immediately
      regardless of confluence score.

  - rule: MEASURED_TARGET_AS_TAKE_PROFIT
    description: >
      Pattern measured targets are used as the primary take-profit reference, not arbitrary
      multiples. The target may be scaled (e.g., 50% at first target, remainder at full).

  - rule: NO_PREMATURE_PATTERN_TRADE
    description: >
      Do not trade an anticipated pattern breakout before the breakout occurs.
      Wait for the candle to close beyond the pattern boundary.

  - rule: PATTERN_TIMEFRAME_MINIMUM
    description: >
      Patterns forming on 15m or lower timeframes receive a 0.15 confidence penalty.
      Patterns on 1h+ timeframes are treated at full confidence.
      Patterns on 4h/1d are given a 0.10 confidence bonus.

  - rule: CONFLICTING_PATTERNS
    description: >
      If a bullish and bearish pattern coexist on the same timeframe, treat as neutral
      (no signal from price action module) until one resolves.
```
