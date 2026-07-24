# Discipline and Consistency
**Source concept**: Douglas — Trading in the Zone
**Category**: Psychology / Risk Discipline

---

## Core Principle

Trading discipline is not willpower — it is systematic design. A trader who relies on willpower to follow rules will eventually fail when stress, fatigue, or a loss streak creates emotional pressure. The solution is not to try harder; it is to encode the rules into a system that cannot be overridden in the moment. The agent architecture is that system.

---

## What Consistency Means in Trading

Consistency does not mean winning every trade. It means:
1. **Rule-consistent execution**: Every trade follows the same entry criteria, confluence requirements, and risk rules — no exceptions.
2. **Sample-consistent evaluation**: Performance is judged over a meaningful sample, not on a trade-by-trade basis.
3. **Context-consistent behavior**: Rules apply equally in winning streaks, losing streaks, high-volatility periods, and boring markets.

---

## The Consistency Principle Applied to the Agent

| Behavior | Consistent Agent | Inconsistent Agent |
|----------|-----------------|-------------------|
| Same setup, different mood | Same signal output | Signal inflated on "good days" |
| Losing streak | Executes next valid signal normally | Skips signals (fear) or oversizes (revenge) |
| Winning streak | Maintains identical risk per trade | Increases size ("on a roll") |
| News event | Waits for price action confirmation | Reacts to headline before price confirms |
| "Obvious" trade | Applies same confluence requirements | Bypasses confluence ("this one is clear") |

---

## Rules-Based Execution vs. Discretionary Override

The agent is **rules-based by design**. There is no discretionary override capability at runtime. This is intentional:

- Discretion applied in a moment of emotional pressure almost always produces worse outcomes than the rules.
- Discretion applied consistently by an experienced operator would require a fundamentally different system architecture (human-in-the-loop).
- The agent is designed to execute the rulebook, not to "feel the market."

The only legitimate form of discretion is **rules revision** — which happens offline, deliberately, with data, between sessions. Never during a session.

---

## Consistency and the Reasoning Trace

Every decision must be traceable to a specific rule. The reasoning trace is not just for human review — it is the accountability mechanism that ensures decisions are rule-driven. If a decision cannot be traced to a rule, the decision is invalid.

**Trace format requirement:**
```
Decision: [LONG/SHORT/NO_TRADE]
Rules fired: [list of rule IDs]
Confidence breakdown: {factor: score} for each module
Gate check: MACRO_MICRO_GATE = [PASS/FAIL]
Confluence count: X of 5 factors agree
Reason for NO_TRADE (if applicable): [threshold/confluence/cooldown/phase/gate]
```

---

## Process Orientation vs. Outcome Orientation

The agent evaluates success by **process quality**, not trade outcome:
- A rule-compliant trade that loses = correct execution
- A rule-violating trade that wins = incorrect execution (and dangerous — it reinforces bad process)

Backtesting validates that the process produces positive expectancy over time. Individual outcomes are noise.

---

## rules:

```
DISCIPLINE_CONSISTENCY_RULES:
  - rule: NO_RUNTIME_RULE_OVERRIDE
    description: >
      No signal parameter, threshold, weight, confluence count, or risk limit
      may be changed during an active trading session. Configuration is read
      at session start and is immutable until the session ends.

  - rule: IDENTICAL_RULES_SAME_PATTERN
    description: >
      Two occurrences of the same pattern type must produce signals scored by
      identical logic. There is no "this time feels different" pathway in the code.

  - rule: MANDATORY_REASONING_TRACE
    description: >
      Every decision output (LONG, SHORT, or NO_TRADE) must include a complete
      reasoning trace with: rules fired, per-module confidence scores, gate status,
      confluence count, and explicit reason for NO_TRADE when applicable.
      Decisions without traces are treated as invalid and not executed.

  - rule: PROCESS_LOGGING_COMPLETE
    description: >
      Skipped trades (NO_TRADE decisions) are logged with full reasoning.
      A skipped trade is as important for process review as an executed trade.
      The log must contain enough detail to reconstruct the decision from raw data.

  - rule: WIN_STREAK_DISCIPLINE
    description: >
      After N consecutive winning trades (default: 5), apply a consistency check:
      verify that rules were followed exactly on each. Do not increase risk during
      winning streaks. A win streak that came from rule violations is not an edge.

  - rule: OFFLINE_ONLY_RULE_REVISION
    description: >
      Rule changes (thresholds, weights, confluence counts) are only valid when
      made in the configuration file before session start. Changes made mid-session
      are logged as a warning and ignored until the next session.
```
