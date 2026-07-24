# Support and Resistance
**Source concept**: Murphy — Technical Analysis of the Financial Markets
**Category**: Technical Analysis

---

## Core Principle

Support is a price level where buying pressure is sufficient to halt or reverse a decline. Resistance is a level where selling pressure is sufficient to halt or reverse an advance. These levels represent concentrations of prior market activity — memory embedded in price.

---

## How S/R Levels Form

| Mechanism | Description |
|-----------|-------------|
| **Prior swing highs/lows** | The most basic: price reversed here before, memory creates re-tests |
| **Consolidation zones** | Horizontal ranges where price spent significant time — heavy volume at these levels |
| **Round numbers** | Psychological levels (e.g., $50,000 BTC, $3,000 ETH) act as natural clusters |
| **Volume nodes (HVN/LVN)** | High Volume Nodes = S/R zones; Low Volume Nodes = price moves through quickly |
| **Prior breakout levels** | A broken resistance level becomes support on a re-test (role reversal) |

---

## Role Reversal (The Polarity Principle)

When price breaks decisively through a resistance level, that level transforms into support upon re-test — and vice versa. This is one of the most reliable patterns in market structure.

**Conditions for valid role reversal:**
1. The initial break must be decisive (strong candle, high volume, preferably a gap).
2. Price should spend time above/below the level before re-testing it.
3. The re-test should ideally show reduced volume (sellers/buyers exhausted) with a rejection candle.

---

## Level Quality Assessment

Not all S/R levels are equal. Rate each level on a 0–3 scale:

| Score | Criteria |
|-------|----------|
| +1 | Level is a prior swing high/low |
| +1 | Level has been tested 2+ times |
| +1 | Level aligns with a round number or high-volume node |
| +1 | Role reversal confirmed (prior resistance now acting as support or vice versa) |

Levels scoring 3–4 are high-quality confluent zones. Levels scoring 1 are weak and should be treated with low confidence.

---

## S/R Zone vs. S/R Level

Price does not respect exact levels — it respects **zones**. Define zones as ±0.5% to ±1% around the identified level for crypto (higher volatility than equities). A zone is more reliable than a single price point.

---

## Dynamic S/R

Moving averages and trend lines act as dynamic (moving) support and resistance. Key dynamic levels:
- 20 EMA (short-term trend support in strong trends)
- 50 EMA (medium-term; often re-tested in pullbacks)
- 200 EMA/SMA (long-term trend definition)

---

## S/R and Volume Relationship

- **High volume at a level** = strong memory, more reliable S/R
- **Low volume approach to a level** = often breaks through (no conviction defense)
- **High volume rejection at a level** = confirmation the level is holding, strong signal

---

## rules:

```
SUPPORT_RESISTANCE_RULES:
  - rule: ZONE_CONFLUENCE_REQUIREMENT
    description: >
      Only trade S/R zones scoring 2+ on the quality scale (minimum 2 confluence factors).
      Single-touch levels without volume confirmation are noise.

  - rule: ROLE_REVERSAL_CONFIRMATION
    description: >
      Role reversal is only confirmed when price approaches the re-test level,
      shows a rejection pattern (wick, engulfing, inside bar), AND volume on rejection
      exceeds average. Do not assume role reversal until price behavior confirms it.

  - rule: ZONE_DEFINITION
    description: >
      Define S/R as a zone (±0.75% for crypto), not a single price.
      Signal fires when price enters zone and rejection begins — not at the single tick.

  - rule: ZONE_BREAK_INVALIDATION
    description: >
      A zone is invalidated when price closes decisively beyond it on the relevant timeframe
      (not a wick). After invalidation, the zone switches polarity (role reversal check).

  - rule: PSYCHOLOGICAL_LEVEL_BONUS
    description: >
      Round-number levels receive a +0.10 confidence bonus when acting as S/R,
      provided at least one other confluence factor is present.

  - rule: NO_CHASING_BROKEN_LEVELS
    description: >
      If price breaks through a key S/R zone without a pullback and re-test,
      do not chase the move. Wait for the re-test of the broken level.
      Chasing into momentum after a break is explicitly prohibited.
```
