# Cortex V4

V4 is a self-contained modular runtime-control layer. Its normal control, memory, operation,
recovery, receipt, and observation paths are V4-owned and do not load SSC, `cortex_core`, an
external corpus, or a fixed local checkout.

The remaining `cortex_v4.adapters` and `cortex_v4.control.mechanical_*` modules are quarantined
historical migration/evaluation tooling. They are opt-in only and are not imported by the normal
V4 entrypoint.

## Historical first slice

The historical first slice connected, under an owner-approved boundary:

1. methodology preflight;
2. SSC corpus read/write adapter;
3. existing model-summon and tool-call layer;
4. managed run folder;
5. OTel/Langfuse correlation; and
6. a small MVC observation view.

That historical slice is not the normal V4 runtime and must not be used as the production path.

The first approved replay slice is now the deterministic long-running control contract in
`cortex_v4.control.long_running`. V4-A intentionally injects the historical retry-overlap
failure; V4-B exercises fenced cancellation and retry. Real LiteLLM provider attachment remains
a separate gate after this replay.

The temporal controller owns a durable cursor and separate worker process, recovers an interrupted
120-step deterministic task, and passes the V4-owned recovery tests. The normal V4 suite does not
claim parity with an external methodology corpus.

All M0-M33 procedures are inventoried from the live SSC manual through one adapter; the manual is
not forked into 34 V4 copies. A real provider request remains a separate operational gate: the
deterministic temporal proof and fixture composition do not claim provider-generated prose parity.

Draft agent-harness rules (R1-R5, hypothesis, not frozen) live in the SSC canonical repo only:
`docs/design/AGENT-HARNESS-RULES-2026-08-06.md` — read through the methodology adapter, never
duplicated here.

## Historical source boundary

The source inventory and slice-selection records remain historical migration evidence. V4 does
not copy or consult that corpus on its normal runtime path. The fail-closed boundary is covered by
`tests/test_ssc_retirement_boundary.py`.
