# Volume and Momentum
**Source concept**: Murphy — Technical Analysis of the Financial Markets
**Category**: Technical Analysis

---

## Core Principle

Volume is the fuel of price movement. Momentum measures the rate of change in price. Both are leading/confirming indicators — they can reveal the strength or weakness of a trend before price alone makes it obvious. Divergence between price and momentum/volume is one of the most actionable signals in technical analysis.

---

## Volume Analysis

### Volume and Trend Relationship

| Market Phase | Expected Volume Behavior |
|-------------|--------------------------|
| Strong uptrend | Higher volume on up-days, lower volume on down-days |
| Strong downtrend | Higher volume on down-days, lower volume on bounces |
| Distribution (topping) | High volume at peaks, rallies on declining volume |
| Accumulation (bottoming) | Selling volume decreasing, quiet absorption of supply |
| Breakout | Volume spikes well above average |
| Failed breakout | Low volume on the break, reversal on high volume |

### Volume Signals

- **Climactic volume**: Extremely high volume at a high or low can signal exhaustion (blowoff top, selling climax).
- **Volume dry-up**: Volume contracting sharply at a support level before a bounce signals smart money absorption.
- **On Balance Volume (OBV)**: Cumulative running total of volume. Rising OBV with rising price = confirmation. Divergence = warning.

---

## RSI (Relative Strength Index)

**Calculation concept**: Ratio of average up-closes to average down-closes over N periods (default: 14).
**Scale**: 0–100. Values above 70 traditionally indicate overbought; below 30 oversold.

### RSI in Trending Markets (critical adjustment)
- In a strong uptrend, RSI often oscillates between 40–80 without reaching 30.
- In a strong downtrend, RSI often oscillates between 20–60 without reaching 70.
- **Never use RSI overbought/oversold as a reversal signal in isolation in a strong trend.**
- Use RSI levels as context, not triggers.

### RSI Divergence (High Priority Signal)

| Type | Price | RSI | Implication |
|------|-------|-----|-------------|
| Bearish divergence | Higher high | Lower high | Momentum weakening — potential reversal |
| Bullish divergence | Lower low | Higher low | Momentum strengthening — potential reversal |
| Hidden bullish | Higher low | Lower low | Trend continuation (uptrend) |
| Hidden bearish | Lower high | Higher high | Trend continuation (downtrend) |

**Regular divergence** = potential trend reversal signal.
**Hidden divergence** = trend continuation signal.

---

## MACD (Moving Average Convergence/Divergence)

**Concept**: Shows the relationship between two exponential moving averages (typically 12 and 26 EMA). The MACD line minus a signal line (9 EMA of MACD) produces the histogram.

### Key MACD Signals

| Signal | Description | Reliability |
|--------|-------------|-------------|
| Zero-line cross | MACD crosses above/below zero | Trend confirmation (lagging) |
| Signal-line cross | MACD crosses above/below signal line | Entry timing (moderate) |
| Histogram divergence | Histogram peaks diverge from price peaks | Early warning (higher value) |
| MACD divergence | MACD pattern diverges from price | Momentum shift warning |

MACD histogram divergence (the histogram making lower highs while price makes higher highs) is more actionable than zero-line crosses alone.

---

## Stochastics

**Concept**: Measures where current close sits within the recent high-low range (typically 14 bars). %K line and %D smoothed line. Scale 0–100.

### Stochastic Rules
- More useful in ranging markets than trending markets.
- In trending markets, stochastics can stay overbought/oversold for extended periods — not a reversal signal alone.
- Stochastic divergence (like RSI divergence) is the higher-value signal.
- %K crossing %D from extreme zone (below 20 up or above 80 down) is an entry signal in range-bound conditions.

---

## Momentum Divergence Framework

Divergence between price and any momentum indicator (RSI, MACD, Stochastics) is a **warning signal**, not a trade signal by itself. It requires:

1. A minimum of 2 clear pivot points on both price and indicator to form divergence.
2. Confirmation via price action (rejection candle, S/R interaction, pattern breakout).
3. At least one other confluence factor from another module.

---

## rules:

```
VOLUME_MOMENTUM_RULES:
  - rule: VOLUME_CONFIRMATION_MINIMUM
    description: >
      Any signal from the price action module must be accompanied by volume at or above
      the 20-bar average. Signals on below-average volume receive a 0.20 confidence penalty.

  - rule: RSI_TRENDING_ADJUSTMENT
    description: >
      In a confirmed primary uptrend, RSI overbought (>70) does NOT generate a short signal.
      In a confirmed primary downtrend, RSI oversold (<30) does NOT generate a long signal.
      RSI extremes are only actionable at major S/R zones or when divergence is present.

  - rule: DIVERGENCE_AS_ALERT_NOT_TRIGGER
    description: >
      Divergence on any oscillator (RSI, MACD, Stochastics) is an alert that raises
      awareness. It does not independently trigger a signal. Confirmation via price
      action and S/R is required before divergence contributes to a confluence count.

  - rule: CLIMACTIC_VOLUME_CAUTION
    description: >
      Extremely high volume (3x+ 20-bar average) at a market extreme triggers a
      "potential exhaustion" flag. Do not initiate new trend-following positions
      at climactic volume. Wait for re-test at lower volatility.

  - rule: OBV_TREND_ALIGNMENT
    description: >
      OBV trend must align with price trend for full confidence on trend-following signals.
      OBV diverging from price direction applies a 0.15 confidence penalty to trend signals.

  - rule: MACD_HISTOGRAM_DIVERGENCE_PRIORITY
    description: >
      MACD histogram divergence is weighted above simple signal-line crosses.
      Histogram divergence spanning at least 3 bars is required to count as valid.
```
