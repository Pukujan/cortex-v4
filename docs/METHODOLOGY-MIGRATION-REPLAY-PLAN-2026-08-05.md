# Methodology migration replay plan

## Current execution status — 2026-08-05

The first methodology/corpus boundary is implemented and passed its SSC-side holdout. The
canonical manual remains in SSC; V4 inventories all M0-M33 procedures and consumes them through
the adapter rather than creating divergent copies. Evidence is recorded in
`observations/decks/methodology-corpus-abcd-20260805.json`.

## Decision

The temporal control slice is the first V4 candidate. The methodology layer moves next in grouped
slices, using SSC as the working corpus and source-golden authority. V4 must independently execute
the methodology against realistic user work and pass hidden behavioral comparison before promotion.

This is not a transcript copy and not a one-model probe. Each lane must demonstrate that the
methodology changes what the runtime does, what evidence it records, and whether it refuses a false
completion.

## Replay shape for every slice

| Arm | Meaning |
|---|---|
| SSC-A | Source methodology performs a realistic task; its report, telemetry, artifacts, and objective outcome are sealed as hidden golden |
| V4-A | Thin migrated wiring with one deliberately injected failure or missing boundary |
| V4-B | V4 independently observes, audits, plans, and repairs the failure |
| V4-C | Methodology/runtime refinement after the first replay |
| V4-D | Hidden holdout comparison against SSC-A behavior and evidence invariants |

The holdout compares task outcome, process ordering, evidence completeness, refusal behavior,
checkpoint/resume behavior, telemetry correlation, and human-readable closeout. It does not compare
source text, wording, model identity, or arbitrary tool-call order.

## Slice order

1. **Methodology core and control contract:** M0, M1, M3, M6, M7, M10, M11, M14, M30, and M31.
   Task: inspect the V4 control boundary, identify missing wiring, freeze the smallest safe slice,
   implement it, verify it, and close it out.
2. **Research and audit:** M21, M22, M23, M25, M32, and M33. Task: observe and audit the same
   migration boundary, research only the unresolved questions, distinguish facts from hypotheses,
   and produce a migration decision with evidence.
3. **Dispatch and tool-call execution:** M8, M18, M28, M29. Task: route the bounded implementation
   through Model Summon, execute contained tools, recover from a worker interruption, and prove no
   seat or tool policy was bypassed.
4. **Evaluation and learning:** M4, M5, M9, M12, M16, M17, M19, M20, and M24. Task: run the hidden
   objective checker, mutation controls, arbitration, blocked-state handling, and closeout; retain
   the failure as a replayable regression.

## Task rule

The task must be a real user-shaped migration task with bounded inputs and a deterministic outcome
oracle—for example, moving one working control/tool slice into the modular monolith, preserving
the SSC corpus boundary, then running the same user journey through the migrated runtime. It must
not be “call every methodology once.” The methodology must be necessary to complete the work.

## Promotion gates

- SSC-A source run is observed first and sealed outside the V4 worker workspace.
- V4-A failure is reproduced before V4-B sees SSC's diagnosis.
- V4-B repairs the smallest boundary and passes the same objective checker.
- V4-C removes migration-specific defects and reruns the failure injector.
- V4-D passes hidden behavioral comparison and telemetry/evidence checks.
- The final closeout records what remains unproven, especially real 100+ LiteLLM execution.

The temporal controller is available for this loop now. The V4 Model Summon/tool adapter must pass
its own control-slice replay before methodology lanes that depend on real provider execution are
promoted.
