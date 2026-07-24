# Market Phases
**Source concept**: Murphy / Wyckoff — market cycle analysis
**Category**: Technical Analysis

---

## Core Principle

Markets move through identifiable phases driven by the behavior of large participants (institutions, "smart money") accumulating or distributing positions. Identifying the current phase determines the appropriate strategy: trend-following is profitable in markup/markdown; patience is required in accumulation/distribution.

---

## The Four Market Phases (Wyckoff-Derived)

### Phase 1: Accumulation
**Characteristics:**
- Follows a prolonged downtrend (markdown phase)
- Price trades in a defined range; sellers exhausted
- Volume gradually diminishes during the range
- Periodic tests of the low on declining volume (springs) show sellers unable to push lower
- Institutional buying absorbs sell-side supply quietly

**Tradeable signals:** Primarily range-bound. Long bias near range support; avoid shorts.
**Key tell:** Failed breakdowns (spring) that quickly reverse back into range = smart money testing supply.

### Phase 2: Markup
**Characteristics:**
- Price breaks above the accumulation range on significantly higher volume
- Trending upward: series of HH and HL
- MAs turn up and become properly ordered (fan formation)
- Pullbacks to prior breakout levels are bought
- Volume expands on up-legs, contracts on pullbacks

**Tradeable signals:** Trend-following longs. Buy pullbacks to S/R, EMA bounces, pattern continuations.
**Best environment for the agent:** Highest confidence in long signals here.

### Phase 3: Distribution
**Characteristics:**
- Follows an extended markup phase
- Price trades in a new range at elevated levels
- Volume is high and erratic — big players are selling into retail buying
- Multiple tests of the high on declining momentum (MACD divergence common)
- Price makes marginal new highs that fail to hold (upthrust)

**Tradeable signals:** Avoid new longs. Short bias near range resistance with tight stops.
**Key tell:** Upthrusts (brief spike above resistance that immediately reverses) = smart money distribution.

### Phase 4: Markdown
**Characteristics:**
- Price breaks below distribution range on high volume
- Trending downward: series of LH and LL
- Bounces are sold at prior breakout levels (role reversal)
- MAs turn down and become ordered for downtrend

**Tradeable signals:** Trend-following shorts. Sell bounces to former support (now resistance).
**Best environment for the agent:** Highest confidence in short signals here.

---

## Phase Identification Decision Tree

```
Is price in a defined range after a prolonged move?
├─ Yes, after downtrend → Possible ACCUMULATION
│   Check: volume contracting? Failed breakdowns? → Confirm accumulation
│   Trade: range longs only, tight stops, no trend-following
│
├─ Yes, after uptrend → Possible DISTRIBUTION
│   Check: volume high/erratic? Failed breakouts (upthrusts)? MACD divergence?
│   Trade: range shorts only, avoid new longs
│
└─ No, price is trending?
    ├─ HH + HL, MAs sloping up, volume confirms → MARKUP → trend-following longs
    └─ LH + LL, MAs sloping down, volume confirms → MARKDOWN → trend-following shorts
```

---

## Phase Transitions (High Risk Periods)

Phase transitions are the most dangerous times to trade:
- Accumulation → Markup breakout: wait for confirmed close above range, not the first attempt.
- Markup → Distribution: reduce position size as price extends into extreme territory.
- Distribution → Markdown breakdown: wait for confirmed close below range.
- Markdown → Accumulation: do not try to catch falling knife; wait for range formation.

---

## rules:

```
MARKET_PHASE_RULES:
  - rule: PHASE_DETERMINES_STRATEGY
    description: >
      The identified market phase gates which signal types are eligible:
      - ACCUMULATION: range-bound long entries only (near range support)
      - MARKUP: trend-following long signals at full confidence
      - DISTRIBUTION: range-bound short entries only (near range resistance)
      - MARKDOWN: trend-following short signals at full confidence
      Attempting trend-following signals in accumulation/distribution phases
      is prohibited regardless of other confluence.

  - rule: PHASE_TRANSITION_CAUTION
    description: >
      During phase transitions (breakout/breakdown from a range), apply a 0.20
      confidence penalty until two consecutive closes confirm the new phase.
      The first breakout candle is not sufficient evidence.

  - rule: UPTHRUST_SPRING_RECOGNITION
    description: >
      An upthrust (brief pierce above distribution resistance that reverses within 1-2 bars)
      is a high-quality short setup — mark as SPRING/UPTHRUST_DETECTED in price action module.
      A spring (brief pierce below accumulation support that reverses) is a high-quality long setup.
      Both require confirmation: volume must be above average on the reversal bar.

  - rule: NO_TREND_FOLLOWING_IN_RANGE
    description: >
      When market_phase module identifies ACCUMULATION or DISTRIBUTION,
      the macro_micro_trend module cannot generate a trend-following signal in the
      direction of the prior primary trend. The gate is OFF.

  - rule: DISTRIBUTION_DIVERGENCE_REQUIRED
    description: >
      Distribution phase classification requires MACD or RSI divergence on
      at least one timeframe (1h or higher) in addition to range structure.
      Price alone reaching a high is not distribution evidence.
```
