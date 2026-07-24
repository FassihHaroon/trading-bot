# The Five Fundamental Truths
**Source concept**: Douglas — Trading in the Zone
**Category**: Psychology / Risk Discipline

---

## Core Principle

These five statements describe the nature of markets and trading outcomes in a way that, when genuinely internalized, remove the emotional friction that causes most trading errors. They are not motivational — they are operational truths that the agent's logic must encode as hard constraints.

---

## Truth 1: Anything Can Happen

**Statement**: On any given trade, any outcome is possible.

**Implication for the agent**:
- No pattern, no matter how "perfect," guarantees a specific outcome.
- The agent must always assume the worst case (stop being hit) is possible *before* assuming the best case.
- Stop-loss placement is not optional — it is the only real risk management tool.
- This truth is why position sizing must be fixed at pre-defined risk, not scaled to conviction.

**Agent implementation**: Stop-loss and invalidation level must be defined *before* a signal is emitted. No signal is valid without an explicit stop.

---

## Truth 2: Every Moment in the Market is Unique

**Statement**: This exact price configuration has never existed before and never will again. No two trades are truly identical.

**Implication for the agent**:
- Historical patterns are probabilistic guides, not deterministic blueprints.
- "This worked last time" is not a reason to relax risk rules.
- Pattern similarity ≠ pattern identity. The agent must not assume identical outcomes from similar setups.

**Agent implementation**: Each trade signal is evaluated fresh. Recency bias (weighting the last few trade outcomes heavily) is explicitly excluded from signal generation.

---

## Truth 3: An Edge Is Not Guaranteed to Work Every Time

**Statement**: A positive-expectancy edge will produce losses on some percentage of its occurrences.

**Implication for the agent**:
- A loss on a rule-compliant trade is not a signal to change the rules.
- The circuit breaker (daily loss limit) is a protection against catastrophic drawdown, not a signal that the edge has stopped working.
- Adjusting strategy after 1–3 losing trades is explicitly prohibited.

**Agent implementation**: Signal rules are only reviewed and adjusted after a statistically significant sample (minimum 20 rule-compliant trades). Mid-session rule changes triggered by losses are blocked.

---

## Truth 4: Consecutive Losses Are a Normal Part of Any Edge

**Statement**: Even a 70% win-rate system will produce streaks of 4–5+ consecutive losses by pure probability.

**Implication for the agent**:
- A losing streak within expected drawdown parameters is not an emergency.
- The appropriate response to consecutive losses is: verify rule compliance (was each trade executed correctly?), then continue executing the same rules.
- The *wrong* response: revenge trade, size up to "get back to even," or abandon rules.

**Agent implementation**: Cooldown protocol engages after N consecutive losses (configurable, default: 3). During cooldown, no new signals are generated for a defined period. This is not discretion — it is a hard rule that prevents revenge trading.

---

## Truth 5: Every Moment of Market Behavior is Unique, But Edges Define Consistent Patterns

**Statement**: While each moment is unique (Truth 2), certain patterns of collective behavior recur with statistical regularity. The edge exploits this regularity — not any single occurrence of it.

**Implication for the agent**:
- The edge is in the *pattern class*, not in any one instance of the pattern.
- Confidence comes from the statistical record of the pattern class, not from how "obvious" any single setup looks.
- "This one is obvious" is not a reason to size up or relax confluence requirements.

**Agent implementation**: Confluence requirements and minimum thresholds are constant across all instances of a pattern. The word "obvious" has no place in the signal calculation logic.

---

## rules:

```
FIVE_TRUTHS_RULES:
  - rule: STOP_REQUIRED_BEFORE_SIGNAL
    description: >
      Truth 1 implementation: No TradeSignal object is valid without an explicit
      stop_price and invalidation_condition. A signal without a defined stop is
      rejected by the risk manager.

  - rule: NO_RECENCY_BIAS_IN_SCORING
    description: >
      Truth 2 implementation: Confidence scores must not be adjusted upward
      because "the same pattern worked recently." Each setup is scored
      identically by its structural properties, not its recent track record.

  - rule: NO_RULE_CHANGE_AFTER_LOSS
    description: >
      Truth 3 implementation: No signal threshold, weight, or confluence
      requirement may be changed during an active trading session in response
      to a loss. Configuration changes require a separate, deliberate process
      outside the trading session.

  - rule: CONSECUTIVE_LOSS_COOLDOWN
    description: >
      Truth 4 implementation: After CONSECUTIVE_LOSS_LIMIT (default: 3)
      consecutive stop-outs, the agent enters COOLDOWN state.
      In COOLDOWN state, no signals are generated.
      COOLDOWN duration: configurable (default: 24 hours).
      COOLDOWN is not overridable at runtime.

  - rule: NO_OBVIOUS_PREMIUM
    description: >
      Truth 5 implementation: A "high-conviction" setup receives no position
      size premium. Rules are applied identically regardless of subjective
      signal quality. Consistency is the only path to a reliable edge.
```
