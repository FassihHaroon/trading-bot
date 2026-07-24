# Trend Theory
**Source concept**: Dow Theory / Murphy — Technical Analysis of the Financial Markets
**Category**: Technical Analysis

---

## Core Principle

A trend is a directional bias in price movement that persists over time. Markets do not move in straight lines — they advance and retreat in a series of waves. A trend is defined by the *direction* of those waves, not by any single bar or candle.

---

## Trend Definition by Wave Structure

| Trend Type | Definition |
|------------|------------|
| **Uptrend** | Series of Higher Highs (HH) and Higher Lows (HL) |
| **Downtrend** | Series of Lower Highs (LH) and Lower Lows (LL) |
| **Sideways/Range** | Neither HH+HL nor LH+LL — price oscillates between a ceiling and a floor |

A trend is **intact** until the wave structure is broken. An uptrend is only threatened when price fails to make a new higher high OR when a prior higher low is violated.

---

## Dow Theory — Three Tiers of Trend

1. **Primary Trend** — The dominant multi-month to multi-year directional move. The "tide." This is the only trend that matters for directional bias in the decision engine.
2. **Secondary Trend** — Corrections against the primary trend, typically retracing 1/3 to 2/3 of the prior primary move. The "wave." Tradeable counter-trend moves but carry higher risk.
3. **Minor Trend** — Day-to-day fluctuations within the secondary trend. The "ripple." Used only for entry timing, never for directional bias.

**Agent rule**: Never use a minor-trend signal to override a primary-trend direction.

---

## Dow Theory — Six Core Tenets (paraphrased)

1. **Averages/price discounts everything** — all known information is reflected in current price.
2. **Three trends exist simultaneously** — primary, secondary, and minor (above).
3. **Primary trends have three phases** — accumulation (smart money), public participation (trend recognition), excess/distribution (overextension and reversal).
4. **Averages must confirm each other** — in multi-asset terms: macro confirmation across related assets strengthens a signal (e.g., BTC trend confirmed by ETH).
5. **Volume must confirm the trend** — volume should expand in the direction of the primary trend and contract during corrections.
6. **A trend is assumed to continue until it gives a definitive reversal signal** — the burden of proof is on the reversal.

---

## Trend Strength Indicators

- **Angle of advance/decline**: Steeper moves are more vulnerable to sharp reversals.
- **Retracement depth**: Shallow retracements (< 38.2%) in an uptrend signal strength. Deep retracements (> 61.8%) signal weakening.
- **Volume behavior**: Rising volume on trend impulses, declining volume on corrections = healthy trend. The reverse signals exhaustion.

---

## Trend Reversal vs. Trend Continuation

| Signal | Reversal | Continuation |
|--------|----------|--------------|
| Wave structure | Prior swing point violated | Prior swing point holds |
| Volume | High volume on reversal bar | Low volume on correction |
| Momentum | Divergence on oscillators | Momentum confirms move |
| Pattern context | Topping/bottoming pattern | Pause/consolidation pattern |

---

## rules:

```
TREND_RULES:
  - rule: TREND_DIRECTION_GATE
    description: >
      All long signals require price to be in an uptrend on the primary timeframe (4h/1d).
      All short signals require price to be in a downtrend on the primary timeframe.
      Sideways markets produce no directional signal unless a clear breakout has occurred.

  - rule: WAVE_STRUCTURE_CONFIRMATION
    description: >
      An uptrend is confirmed only by successive HH and HL. A downtrend by successive LH and LL.
      A single bar breach of a swing point is insufficient — require close beyond the level.

  - rule: TREND_BENEFIT_OF_DOUBT
    description: >
      A trend is considered intact until price gives a definitive structural break.
      Do not anticipate reversals — wait for confirmation.

  - rule: VOLUME_TREND_CONFIRMATION
    description: >
      If trend impulse legs show declining volume over multiple bars, reduce confidence score
      for continuation signals by 0.15.

  - rule: SECONDARY_TREND_RISK_PREMIUM
    description: >
      Signals generated against the primary trend (secondary-trend trades) carry a mandatory
      confidence penalty of 0.20 and require an additional confluence factor to fire.
```
