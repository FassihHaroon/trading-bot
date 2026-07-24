# Avoiding Revenge Trading and Greed
**Source concept**: Douglas — Trading in the Zone
**Category**: Psychology / Risk Discipline

---

## Core Principle

Two emotions cause the majority of catastrophic trading losses: **revenge** (the compulsion to immediately recover a loss by taking a worse trade) and **greed** (the compulsion to take more profit or more risk than the plan allows). Both are expressions of the same underlying problem: letting outcome emotions override the pre-defined process. The agent architecture eliminates both by making them structurally impossible.

---

## Anatomy of Revenge Trading

A revenge trade is a trade entered:
1. Immediately after a loss, without waiting for a new valid setup
2. With increased size ("I need to make it back faster")
3. In a market that no longer meets confluence requirements
4. Without a proper stop ("I can't take another loss right now")

The mechanism is: **pain from loss → impulse to recover → overrides analysis → results in larger loss → escalating cycle**.

Every characteristic of revenge trading is detectable in the system:
- Trade follows a loss with no new market cycle
- Position size exceeds computed risk_amount
- Confluence threshold not met
- Stop not technically placed

---

## Revenge Trading Prevention Architecture

```
Prevention Layer 1: COOLDOWN after consecutive losses
  → After N consecutive losses (default: 3), all signal generation halts
  → Duration: 24 hours (configurable)
  → Cannot be bypassed at runtime

Prevention Layer 2: SAME_MARKET re-entry check
  → If a trade stopped out on instrument X, require a new setup cycle
    (new market structure, new S/R, new confluence assessment) before
    generating another signal on instrument X
  → Minimum time gap: 4 candles on the entry timeframe

Prevention Layer 3: POSITION SIZE LOCK
  → Position size is computed from fixed formula — cannot be manually increased
  → Any request to override computed size is rejected

Prevention Layer 4: CONFLUENCE ENFORCEMENT
  → After a loss, the same confluence requirements apply as before the loss
  → No "I'll be more selective" or "I'll lower the bar just this once"
  → The bar stays identical
```

---

## Anatomy of Greed

Greed in trading manifests as:
1. **Moving take-profit targets further** after price moves in favor ("it can go higher")
2. **Removing or ignoring partial profit rules** ("let it run")
3. **Adding to a winning position beyond the plan** (pyramiding without rules)
4. **Re-entering immediately after a target hit** without waiting for new setup
5. **Oversizing because "this one is obvious"** (covered in probabilistic mindset)

Greed is seductive because it sometimes produces bigger wins — making it self-reinforcing for non-systematic traders. For a system, it produces inconsistency and eventually ruins the risk profile.

---

## Greed Prevention Architecture

```
Rule: HONOR_TAKE_PROFIT_TARGETS
  → Take-profit levels defined at signal generation must not be extended after entry
  → The only exception: trailing stop to "let winners run" must be pre-defined in
    the signal itself, not added discretionarily after entry

Rule: PYRAMIDING_REQUIRES_PRE_PLAN
  → Adding to a winning position (pyramiding) is only allowed if:
    a) The trade signal included a pyramid plan at signal generation
    b) The add-on size maintains total risk at or below original risk_amount
    c) The original stop has moved to at least breakeven
  → Post-entry decisions to "add more" without pre-plan are rejected

Rule: RE-ENTRY_AFTER_TARGET_REQUIRES_NEW_SETUP
  → After a take-profit is hit, a new complete setup cycle must complete
    before another signal on the same instrument is generated
  → Minimum: one complete candle close on the entry timeframe has occurred

Rule: EQUITY_HIGH_OVERCONFIDENCE_CHECK
  → After a new equity high is reached, run an explicit check:
    "Are confluence requirements still being followed?"
  → Log the check result. The check itself prevents overconfidence drift.
```

---

## The Patience Principle

Not being in a trade is a valid position. Markets do not reward impatience — they punish it by offering sub-quality setups to traders who need to always be in the market.

The agent must be comfortable generating `NO_TRADE` as the output for the majority of cycles. The default state is: **waiting for a qualified setup**, not: **always looking for a reason to enter**.

---

## rules:

```
REVENGE_GREED_PREVENTION_RULES:
  - rule: COOLDOWN_AFTER_CONSECUTIVE_LOSSES
    description: >
      After CONSECUTIVE_LOSS_LIMIT consecutive stop-outs, enter COOLDOWN_STATE.
      In COOLDOWN_STATE, no signals are generated and no orders are placed.
      COOLDOWN_DURATION is configurable (default: 24h). Not overridable at runtime.

  - rule: SAME_INSTRUMENT_REENTRY_CYCLE
    description: >
      After a stop-out on instrument X, a new signal on X requires:
      - A new complete market structure cycle (new S/R assessment, new confluence)
      - Minimum 4 entry-timeframe candles elapsed since the stop-out
      A signal generated within these constraints is flagged and rejected.

  - rule: NO_TARGET_EXTENSION_POSTENTRY
    description: >
      Take-profit levels are defined at signal generation and stored immutably.
      Post-entry extension of targets is not permitted.
      Trailing stops (if enabled) must follow a pre-defined rule (e.g., trail by ATR),
      not discretionary judgment.

  - rule: PYRAMIDING_ONLY_WITH_PREPLAN
    description: >
      Position adds are only executed if the original TradeSignal included
      a pyramid_plan object. Unplanned adds are rejected at execution.
      Total risk after any pyramid add must not exceed original risk_amount.

  - rule: NO_TRADE_IS_VALID_OUTPUT
    description: >
      The agent must never enter a trade simply because it has been "idle" for
      a defined period. Inactivity is not a signal. NO_TRADE logged with
      "no qualifying setup" is a completely acceptable and frequent outcome.

  - rule: DAILY_LOSS_CIRCUIT_BREAKER
    description: >
      If daily realized losses exceed DAILY_LOSS_LIMIT (default: 3% of equity),
      halt all signal generation for the remainder of the trading day.
      This is a hard stop — not a soft suggestion. Prevents the spiral of
      loss → revenge → larger loss → account damage.

  - rule: WEEKLY_LOSS_CIRCUIT_BREAKER
    description: >
      If weekly realized losses exceed WEEKLY_LOSS_LIMIT (default: 5% of equity),
      halt all signal generation for the remainder of the week.
      Resume on next Monday's session open with reduced position sizing
      (75% of normal risk_pct) for 3 trading sessions as recalibration.
```
