# Cortex V4

V4 is a self-contained modular runtime-control layer. Its normal control, memory, operation,
recovery, receipt, and observation paths are V4-owned and do not load SSC, `cortex_core`, an
external corpus, or a fixed local checkout.

The current runtime boundary is recorded in
[`docs/CURRENT-RUNTIME-CONTRACT-2026-08-13.md`](docs/CURRENT-RUNTIME-CONTRACT-2026-08-13.md).
That document is the current interpretation of this README; dated replay and migration records
below remain historical evidence unless they are explicitly marked current.

The native P0 staging implementation and its evidence are recorded in
[`docs/P0-NATIVE-METHODOLOGY-LITELLM-2026-08-13.md`](docs/P0-NATIVE-METHODOLOGY-LITELLM-2026-08-13.md).

## Current capability boundary

- Normal V4 execution is a chat/task-control contract. It does not directly call embedding,
  reranking, image-generation, or internet-search endpoints.
- External internet research is a separately authorized task capability. A model label containing
  `search` is not permission to use it for ordinary coding or Fossil corpus lookup.
- `fossil.search` means search over packs mounted for the caller. It is not an internet-search
  service. Fossil owns its retrieval and projection choices; Cortex receives context or an
  explicit no-context result rather than calling Fossil's embedding/reranking endpoints itself.
- Image generation is not a V4 capability. A live provider adapter must reject an image-only
  model for a V4 task instead of treating the model name as a generic chat route.
- The legacy PR18 operation/fixture entrypoints remain deterministic proofs. The native
  run-brain/staged-runner/LiteLLM path is implemented for isolated staging, but it is not the
  production default and its repeated real-run gate remains open.

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
Its OTel/Langfuse references describe quarantined historical adapters, not an active V4
exporter or trace-verification pipeline.

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
