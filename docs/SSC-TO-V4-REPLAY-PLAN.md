# SSC-to-V4 control-layer replay plan

This is the migration contract for the repaired LiteLLM long-running loop. SSC remains the source
corpus and owns the hidden golden evidence. V4 receives the approved runtime slice and public
contract, then independently recreates and fixes the same failure.

## Source gate

Do not export until SSC has:

- passed the deterministic long-running TDD suite;
- passed the clean real LiteLLM extended task;
- passed the injected timeout/retry observation;
- proved checkpoint generation fencing and no retry overlap;
- proved heartbeat/watchdog evidence and terminal retry-budget behavior;
- killed the required mutants; and
- stored the human observation decks and closeout in SSC's `observations/` and `reviewed/`
  boundaries.

## Export boundary

Export to V4 only:

- the approved control-layer implementation;
- the public checkpoint, cancellation, retry, heartbeat, watchdog, and telemetry contracts;
- the deterministic task fixture and objective checker;
- the observation manifest schema and dashboard projection contract; and
- the public migration tests.

Do not export the hidden SSC-A answers, raw traces, private route credentials, detailed diagnosis,
or final closeout language into the V4 worker prompt or workspace.

## Replay loop

1. Create V4-A from the thin migrated control/tool slice.
2. Inject the same deterministic long-running failure: provider stall, timeout boundary,
   checkpoint boundary, retry budget, and heartbeat conditions.
3. Run M32 independently in V4. V4 must produce its own observation matrix and dashboard before
   seeing SSC's diagnosis.
4. Localize the V4 failure and form falsifiable hypotheses.
5. Repair V4-B with the smallest compatible change.
6. Run the same deterministic TDD, A/B/C, mutation, and objective artifact checks.
7. Compare SSC-A and V4-B on behavior: artifacts, checkpoints, generation fencing, retry
   ownership, heartbeat/watchdog events, terminal truth, and telemetry correlation.
8. Refine V4-C and the methodology for any migration-specific gap.
9. Re-run the hidden holdout and the injected replay after refinement.

## Later regression lane

Retain the failure injector as a separate future test. It must be able to recreate the same issue
from either SSC or V4 and invoke the complete M32 -> M33 sequence without relying on session memory.

Parity is not prose similarity or source-file similarity. It is independent observation, repair,
and objective behavioral equivalence under the same failure envelope.
