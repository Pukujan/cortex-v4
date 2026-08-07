# V4 temporal A/B/C/D replay

## Result

The migrated temporal control slice passed the SSC-side hidden behavioral holdout. V4 receives no
SSC-A answers or raw traces; the source evaluator reads the V4 deck and returns only the axis
verdicts.

Deck: `observations/decks/v4-temporal-abcd-20260805.json`

## Iterations

| Arm | Purpose | Result |
|---|---|---|
| A | Reproduce the historical retry-overlap failure | Failure reproduced: the old path falsely completed with two active attempts |
| B | Repair the same control behavior | Pass: cancellation is acknowledged before retry and overlap is absent |
| C | Add temporal cursor/supervisor behavior | Pass: a 120-step worker was interrupted, resumed at the durable cursor, and completed 120 artifacts with generation fencing |
| D | Compare against SSC-owned hidden behavioral invariants | PASS from SSC-side evaluator |

This is a control-slice promotion, not a claim that V4 now has the full SSC Model Summon/tool
stack. The next slice connects the existing Model Summon and tool-call layer behind this controller,
then repeats the same hidden replay with the real provider route.
