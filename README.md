# Cortex V4

V4 is the durable control and assurance layer for repeatable agent work. It mechanically governs
methodology execution, model/tool access, evidence collection, verification, recovery, and
closeout. SSC is one corpus and implementation reference; V4 must remain operable if SSC is
empty, stale, migrated, or replaced.

The live SSC checkout at `D:\\claude\\stupidly-simple-cortex` remains an infrastructure/model-behavior
corpus and golden implementation reference. V4 does not copy the corpus or rebuild the SSC kernel.
V4 owns the durable process contract: gates, state transitions, authority rules, deadlines,
telemetry, verification, escalation, and promotion criteria.

The current control-plane contract is documented in
[`docs/V4-DURABLE-CONTROL-PLANE-CONTRACT-2026-08-10.md`](docs/V4-DURABLE-CONTROL-PLANE-CONTRACT-2026-08-10.md).

## First slice

The first slice will connect, under an owner-approved boundary:

1. methodology preflight;
2. SSC corpus read/write adapter;
3. existing model-summon and tool-call layer;
4. managed run folder;
5. OTel/Langfuse correlation; and
6. a small MVC observation view.

No production runtime code is moved until the Phase 0 manifest and A/B/C fixture are approved.

The first approved replay slice is now the deterministic long-running control contract in
`cortex_v4.control.long_running`. V4-A intentionally injects the historical retry-overlap
failure; V4-B exercises fenced cancellation and retry. Real LiteLLM provider attachment remains
a separate gate after this replay.

The temporal controller owns a durable cursor and separate worker process, recovers an interrupted
120-step deterministic task, and passed the SSC-side A/B/C/D behavioral holdout. The methodology/
corpus, Model Summon/tool, and local observability adapters also pass their independent SSC-side
holdouts. The composed MVC-style walking skeleton passes its receipt-level holdout.

All M0-M33 procedures are inventoried from the live SSC manual through one adapter; the manual is
not forked into 34 V4 copies. A real provider request remains a separate operational gate: the
deterministic temporal proof and fixture composition do not claim provider-generated prose parity.

Draft agent-harness rules (R1-R5, hypothesis, not frozen) live in the SSC canonical repo only:
`docs/design/AGENT-HARNESS-RULES-2026-08-06.md` — read through the methodology adapter, never
duplicated here.

## Source and target

- Source: `D:\\claude\\stupidly-simple-cortex\\cortex_core`
- Corpus: `D:\\claude\\stupidly-simple-cortex`
- Target: this repository
- Reference only: `D:\\claude\\cortex-v3`

The source inventory and slice-selection record remain in the SSC repository. V4 contains
adapters and a small composition layer; it does not contain a copied SSC corpus.
