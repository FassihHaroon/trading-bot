# Risk Acceptance
**Source concept**: Douglas — Trading in the Zone
**Category**: Psychology / Risk Discipline

---

## Core Principle

The most psychologically destructive force in trading is the refusal to accept that a loss is possible *before* entering a trade. Traders who don't truly accept the risk move stops, hold through invalidation, and average down — not because of strategy, but because they cannot emotionally accept being wrong. The agent must make risk acceptance a mechanical, pre-trade commitment.

---

## What "Accepting the Risk" Means

Risk acceptance is not resignation — it is precision. It means:
1. **Defining the maximum loss before entry** — the exact dollar amount the account will lose if the stop is hit.
2. **Being genuinely indifferent to that loss** — because it was pre-budgeted as a cost of doing business.
3. **Not moving the stop** once the trade is live, regardless of how price action feels.

The stop represents the **point at which the trade thesis is invalidated**. If that point is reached, the trade is over. There is no "give it more room."

---

## Pre-Trade Risk Calculation (Mandatory)

```
Given:
  account_equity     = current portfolio value
  risk_pct           = max risk per trade (e.g., 0.5% or 1.0%)
  entry_price        = planned entry
  stop_price         = invalidation level (technical basis, not arbitrary)

Compute:
  risk_amount        = account_equity × risk_pct
  stop_distance      = |entry_price − stop_price| / entry_price  (as % of entry)
  position_size      = risk_amount / (entry_price × stop_distance)
  
Note:
  position_size is determined by the stop distance, not by "how much I want to make."
  A wider stop = smaller position size.
  A tighter stop = larger position size.
  Never reverse this logic.
```

---

## Stop Placement Rules

Stops must be placed at **technically meaningful invalidation levels**, not:
- At a round % loss (e.g., "I'll stop out if I'm down 2%" — not technical)
- Arbitrarily tight to minimize loss amount (forces oversizing)
- Behind round numbers without structural justification

Valid stop locations:
- Below the most recent swing low (for longs)
- Above the most recent swing high (for shorts)
- Beyond the S/R zone that the trade is based on
- Below/above a key EMA that defines the trade thesis

---

## The Stop Is Not a Suggestion

Once the trade is entered:
- **The stop does not move in the direction of risk** (i.e., making losses larger)
- The stop may be moved in the direction of profit (trailing) according to rules
- A stop moved to protect profit (breakeven or trail) is valid
- A stop moved to avoid a loss because "it looks like it will come back" is prohibited

---

## Accepting the Full Risk Before Entry

The agent must not enter a trade if the computed risk_amount would cause emotional or financial concern. The test:

> "If this trade hits its stop immediately after entry, am I genuinely okay with that loss?"

If the computed risk_amount exceeds what the operator considers acceptable (defined in config), the signal is rejected at the risk manager level before order entry.

---

## Loss as Operating Cost, Not Failure

Losses within pre-defined risk parameters are:
- Expected and statistically necessary for any positive-expectancy edge
- A sign that the stop placement system is working
- Not a reason to adjust strategy, increase size, or seek revenge

The agent logs each loss with its rule compliance status. A loss on a rule-compliant trade is noted as "compliant loss." A loss from a rule violation is noted as a critical error requiring review.

---

## rules:

```
RISK_ACCEPTANCE_RULES:
  - rule: STOP_TECHNICALLY_BASED
    description: >
      Every trade signal must have a stop_price derived from a specific technical
      level (swing point, S/R zone boundary, EMA level) stored in the reasoning trace.
      "X% from entry" is not a valid stop calculation method.

  - rule: POSITION_SIZE_FROM_STOP_DISTANCE
    description: >
      position_size = (account_equity × risk_pct) / (entry_price × stop_distance_pct)
      This is the only valid position size formula. Never compute size from
      target distance, desired profit, or any other variable.

  - rule: NO_STOP_WIDENING
    description: >
      Once a trade is active, the stop_price may only move in the direction
      of profit (toward breakeven or beyond). Moving the stop further from entry
      to give the trade "more room" is a hard prohibition — reject at execution layer.

  - rule: RISK_AMOUNT_PRE_APPROVAL
    description: >
      The computed risk_amount (dollar value at risk) must be displayed in the
      signal output before any execution. If risk_amount exceeds the configured
      max_dollar_risk limit, the trade is rejected.

  - rule: COMPLIANT_LOSS_LOGGING
    description: >
      Each losing trade must be logged with its rule compliance status.
      A compliant loss requires no action. A non-compliant loss (rule was violated)
      triggers an alert and mandatory offline review before next session.

  - rule: RISK_PCT_HARD_CEILING
    description: >
      risk_pct has an absolute hard ceiling defined in configuration (default: 1.0%).
      No signal, regardless of confidence score, may result in a position that
      risks more than this percentage of equity. This ceiling cannot be raised
      at runtime.
```
