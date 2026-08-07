# SSC-to-V4 migration manifest

## Current first vertical slice

This is the active move boundary. It is not permission to copy all 195 SSC modules.

| Group | SSC source modules | V4 boundary |
|---|---|---|
| Methodology | `session_preflight`, `forced_rag_gate`, `methodology_receipt` | `cortex_v4.adapters.ssc_methodology` — holdout PASS; M0-M33 inventoried |
| Corpus/RAG | `config`, `search`, `knowledge`, `write_policy` | `cortex_v4.adapters.ssc_corpus` — read/search holdout PASS; corpus stays SSC |
| Summon/tools | `model_summon`, `model_dispatch`, `agent_runtime`, `tool_surface` | `cortex_v4.adapters.ssc_summon` — seat/tool boundary holdout PASS |
| Observation | `trace_capture`, `otel`, `langfuse_sink`, `observability_dashboard` | `cortex_v4.adapters.ssc_observability` — local observation holdout PASS |
| Temporal control | V4-native port of the proven supervisor/cursor contract | `cortex_v4.control.temporal` — 120-step interruption holdout PASS |

That is 15 source modules in the first walking-skeleton boundary, plus V4-native models, schemas,
controllers, views, and the temporal controller. The methodology manual itself remains one SSC
canonical document; M0–M33 are consumed through evidence-pack and receipt adapters, not forked into
33 copies.

## Promotion rule

Each group receives SSC-A, V4-A, V4-B, V4-C, and V4-D evidence. The V4 worker never receives SSC-A
raw answers, private traces, credentials, or closeout prose. SSC remains the RAG corpus and source
authority for search, Brain recall, research, accepted data, methodology records, and closeouts.
