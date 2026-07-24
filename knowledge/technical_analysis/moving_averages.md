# Moving Averages
**Source concept**: Murphy — Technical Analysis of the Financial Markets
**Category**: Technical Analysis

---

## Core Principle

A moving average smooths price data to reveal the underlying trend by reducing noise. It is a lagging indicator — it follows price, never predicts it. Its value lies in trend identification, dynamic S/R, and crossover signals.

---

## Types of Moving Averages

| Type | Description | Best Use |
|------|-------------|----------|
| **SMA** (Simple) | Equal weight to all periods | Long-term trend reference (200 SMA) |
| **EMA** (Exponential) | More weight to recent prices, reacts faster | Short/medium-term trend, entry timing |
| **WMA** (Weighted) | Linear weighting, faster than SMA | Less common; similar use to EMA |
| **VWAP** (Volume-Weighted) | Price weighted by volume, resets daily | Intraday S/R, institutional reference |

---

## Key MA Periods and Their Roles

| Period | Type | Role |
|--------|------|------|
| 9/10 EMA | Very short | Intraday momentum; often used for trailing |
| 20/21 EMA | Short | Short-term trend; first line of defense in uptrend |
| 50 EMA/SMA | Medium | Intermediate trend; major S/R in trending markets |
| 100 SMA | Medium-long | Often S/R on 4h/1d charts |
| 200 EMA/SMA | Long | Defining line between bull and bear market |

---

## MAs as Dynamic Support/Resistance

In a strong uptrend, price often pulls back to the 20 or 50 EMA before resuming. This creates high-probability long entries:
- Price approaches 20 EMA → first test → potential long at rising EMA
- Price breaks through 20 EMA but holds 50 EMA → deeper correction, still within uptrend
- Price breaks 50 EMA and fails to reclaim → potential trend weakening; avoid longs

In a downtrend, rallies are often rejected at the 20 or 50 EMA (now acting as resistance).

---

## MA Crossover Signals

### Golden Cross / Death Cross (Lagging, Trend-Confirming)
- **Golden Cross**: 50 SMA crosses above 200 SMA — long-term bullish shift
- **Death Cross**: 50 SMA crosses below 200 SMA — long-term bearish shift

These are highly lagging signals — by the time they occur, much of the move has already happened. Use them for bias, not for precise entries.

### Short-Term Crossovers (Entry Timing)
- 9 EMA crossing 21 EMA — short-term momentum shift; useful for entry timing on 1h/4h.
- Not to be traded mechanically — require S/R and trend alignment.

---

## MA Slope as Trend Strength Indicator

| MA Slope | Interpretation |
|----------|---------------|
| Steep upward | Strong uptrend, high momentum |
| Gradual upward | Moderate uptrend |
| Flat | Ranging market; MA is unreliable as trend indicator |
| Gradual downward | Moderate downtrend |
| Steep downward | Strong downtrend |

A flat 200 SMA + price chopping around it = avoid trend-following signals; treat as range.

---

## MA Fan Formation

When short-, medium-, and long-term MAs are all sloping in the same direction and properly ordered (9 EMA > 20 EMA > 50 EMA > 200 EMA for uptrend), this is a **MA fan** and represents a high-momentum trend phase. This is the environment where trend-following signals carry highest confidence.

---

## rules:

```
MOVING_AVERAGE_RULES:
  - rule: PRICE_VS_200_EMA_BIAS
    description: >
      Price above 200 EMA on 4h/1d = bullish bias. Long signals receive no penalty.
      Price below 200 EMA on 4h/1d = bearish bias. Short signals receive no penalty.
      Long signals below 200 EMA or short signals above 200 EMA receive a 0.15
      confidence penalty (counter-trend bias).

  - rule: FLAT_MA_EXCLUSION
    description: >
      When the 20 EMA on the primary timeframe has a slope within ±0.1% per bar (flat),
      do not generate trend-following signals. Flat MAs indicate ranging conditions.

  - rule: MA_FAN_BONUS
    description: >
      When EMAs are properly ordered for trend direction (9>20>50>200 for bull,
      reversed for bear) on the 4h or 1d chart, apply a +0.10 confidence bonus
      to trend-aligned signals.

  - rule: CROSSOVER_NOT_SUFFICIENT_ALONE
    description: >
      MA crossovers alone do not generate a trade signal. They are one input to
      the price action module's direction determination. Crossover + S/R + volume
      together contribute one signal to the confluence count.

  - rule: DYNAMIC_SR_BOUNCE_ENTRY
    description: >
      A pullback to a key EMA (20 or 50) in an established trend, followed by a
      rejection candle with above-average volume, is a valid "trend continuation"
      pattern input to the price_action module.

  - rule: GOLDEN_DEATH_CROSS_BIAS_ONLY
    description: >
      Golden/Death cross signals are used only to set macro directional bias.
      They are not trade entry signals. A death cross does not trigger a short;
      it lowers confidence on long signals.
```
