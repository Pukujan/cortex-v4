# Telemetry and observation-deck A/B/C/D replay — 2026-08-05

This slice migrates the local observation contract only: trace-capture JSONL, OTel local
span receipts, redacted dashboard projection, and human dashboard rendering. SSC remains the
implementation authority and V4 calls it through an adapter.

- A records and projects a fixture through SSC directly.
- B records and projects the same fixture through the V4 adapter.
- C proves that an empty observation set is `BLOCKED` and never a verdict authority.
- D is an SSC-side hidden comparison of the normalized contract and required dashboard markers.

Remote Langfuse/collector reachability is deliberately excluded from this migration oracle;
it is environment evidence for operations, not proof that the V4 boundary is behaviorally
identical.

