# SSC runtime retirement for Cortex V4

Issue #1 / fossil-core #86 / queue task `CORTEX-04` retire SSC as a normal Cortex runtime, corpus, methodology, routing, observability, and merge-gate dependency.

The ordinary V4 suite now exercises only V4-owned deterministic control, recovery, fallback, temporal, memory, and public-fixture contracts. The SSC-bound replay tests are deliberately removed rather than skipped: they required a private external checkout and encoded superseded authority boundaries. Git history preserves their migration evidence. Replacement tests cover versioned policy validation, mechanical terminal closeout, route allowlists, independently checked extended-task artifacts, and WorkOrder death/replay/fencing semantics.

No SSC prose, corpus, model seating, provider route, telemetry, secret, or current-state output is copied into V4. A future useful historical checker must be extracted as an exact-byte, independently validated asset with provenance before it may become a separate optional eval.
