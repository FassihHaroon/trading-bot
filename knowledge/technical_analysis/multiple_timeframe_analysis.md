# Multiple Timeframe Analysis
**Source concept**: Murphy — Technical Analysis of the Financial Markets
**Category**: Technical Analysis

---

## Core Principle

Every timeframe tells part of the story. Analyzing price through multiple lenses — from macro to micro — prevents trading against the dominant trend and improves entry precision. A signal that aligns across multiple timeframes is significantly more reliable than one isolated to a single chart.

---

## The Three-Timeframe Framework

| Role | Timeframe | Purpose |
|------|-----------|---------|
| **Macro (Trend)** | 1D / 4H | Define primary trend direction and market phase. This is the directional bias. |
| **Mid (Swing)** | 1H / 4H | Identify S/R zones, patterns, and trend structure for the actual trade setup. |
| **Micro (Entry)** | 15M / 1H | Precise entry timing — wait for a micro-level confirmation trigger. |

**Agent timeframe mapping:**
- 1D → Primary trend & market phase
- 4H → Setup identification (S/R, pattern, mid-trend structure)
- 1H → Setup confirmation & refinement
- 15M → Entry trigger timing

---

## Top-Down Analysis Process

```
Step 1: Start on the DAILY chart
  → Determine: Is the primary trend up, down, or sideways?
  → Identify: Major S/R zones, market phase (accumulation/markup/distribution/markdown)
  → Output: Directional bias (LONG-ONLY / SHORT-ONLY / NEUTRAL)

Step 2: Drop to 4H chart
  → Confirm: Does mid-timeframe structure agree with daily bias?
  → Identify: The specific setup — pattern, S/R zone being approached, trend line
  → Output: Setup type + zone definition + mid-trend direction

Step 3: Drop to 1H chart
  → Refine: Is there a specific entry pattern forming? (e.g., flag, pullback to EMA)
  → Check: Volume behavior, momentum state (RSI trend, MACD position)
  → Output: Entry zone, stop level, target

Step 4: Use 15M for entry trigger only
  → Wait for: A specific trigger candle (rejection, engulfing, breakout)
  → Do NOT: Make any directional decisions based solely on 15M
```

---

## Timeframe Alignment Requirements

| Alignment State | Signal Quality | Agent Action |
|-----------------|---------------|-------------|
| 1D + 4H + 1H all agree | High | Full confidence, eligible for trade signal |
| 1D + 4H agree, 1H neutral | Medium | Eligible with reduced confidence (−0.10) |
| 1D + 4H disagree | Low | No signal — wait for resolution |
| Any timeframe opposes | Very Low | Signal blocked — macro/micro gate fails |

**The macro/micro gate is a hard requirement**, not a vote in the confluence system. It must pass before any other factor is counted.

---

## Common Multi-Timeframe Trap: The Counter-Trend Micro Signal

A common failure mode: the daily trend is bearish, but the 15m chart shows a bullish RSI divergence and bouncing price. The agent must not generate a long signal — the micro pattern is a counter-trend noise event, not an edge.

**Rule**: Micro-timeframe signals only contribute when they point in the same direction as the macro trend. Counter-direction micro signals are ignored.

---

## Timeframe Convergence for S/R

When a support/resistance zone is visible on *multiple* timeframes simultaneously, it carries significantly more weight:
- S/R on 15M only → Low weight
- S/R on 1H + 15M → Medium weight
- S/R on 4H + 1H + 15M → High weight (multi-timeframe confluence)
- S/R on 1D + 4H + 1H → Very high weight (major level)

---

## rules:

```
MULTIPLE_TIMEFRAME_RULES:
  - rule: MACRO_MICRO_GATE_MANDATORY
    description: >
      The macro/micro trend module is a HARD GATE, not a vote.
      If 1D and 4H trends disagree, NO trade signal is generated regardless of
      how many other factors are confluent. This rule cannot be overridden.

  - rule: TOP_DOWN_ANALYSIS_ORDER
    description: >
      Analysis always flows from higher to lower timeframe.
      Never start analysis on 15M or 1H and then justify it with the daily.
      The daily trend defines what is tradeable; lower timeframes only refine entry.

  - rule: COUNTER_TREND_MICRO_EXCLUDED
    description: >
      Micro-timeframe signals (15M/1H) that contradict the macro trend direction
      (1D/4H) are discarded. They do not contribute to confluence count and are
      not reported as signals.

  - rule: MULTI_TF_SR_BONUS
    description: >
      S/R zones visible on 3+ timeframes simultaneously receive a +0.15
      confidence bonus when the signal involves approaching or bouncing from
      that zone.

  - rule: TIMEFRAME_DISAGREEMENT_WAIT
    description: >
      When 4H and 1D trends contradict, the agent enters a WAIT state.
      It does not attempt to trade the "stronger" timeframe alone.
      Resume when higher-timeframe agreement is restored.

  - rule: ENTRY_ONLY_ON_MICRO_CONFIRMATION
    description: >
      Even with full macro/mid alignment, entry is only triggered when the micro
      timeframe (15M or 1H) shows a specific confirmation trigger (rejection candle,
      pattern breakout close, EMA reclaim). Entering without micro confirmation
      is prohibited.
```
