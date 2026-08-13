# Cortex V4 current runtime contract

**Status:** current staging clarification for PR18 (`8701ca4674f43e4828a443d194aa1a35cebd0ceb`)

This record resolves the difference between the current SSC-retirement boundary and older dated
replay notes. Older replay, migration, and observation documents are retained as historical
evidence; they are not runtime instructions.

## Ownership and capabilities

| Capability | Current owner or path | Cortex V4 rule |
|---|---|---|
| Chat/task execution | Cortex execution policy plus an approved provider adapter | Allowed only when the route is proven chat-capable for the stage contract. |
| External internet research | Explicit research caller/tool | Must be requested by the task contract and must preserve source and provenance evidence. |
| Fossil corpus search | Fossil `search/read/lineage` boundary | Mounted-pack search is internal corpus retrieval, not internet search. |
| Embeddings and reranking | Fossil retrieval/projection services when explicitly configured | Cortex does not call these APIs directly for V4 task execution. |
| Image generation | No normal V4 capability | Reject an image-only route for a V4 task. |

Model names and catalog labels are not capability authorization. A live adapter must resolve the
requested role against a versioned route/model capability record and fail closed when the endpoint,
modality, or task role does not match. `[aws]`, `search`, `image`, or other labels do not establish
provider identity, quality, or permission.

## What is implemented on this branch

The legacy PR18 operation/fixture entrypoints remain deterministic fixture/control proofs. The
isolated native staging path in `cortex_v4.control` now owns a frozen contract, run-scoped brain,
role-scoped capabilities, granular checkpoints, generation fencing, direct LiteLLM chat/Responses
execution, sanitized receipts, recovery, and independent mechanical closeout. It is not the
production default; repeated real-route evidence remains a staging gate.

The `closeout.md` emitted by the legacy fixture operation is a human-readable execution view. The
native run brain emits structured JSON closeout/receipts instead. Neither is a Fossil memory
commit, a canonical correctness verdict, or permission for a model to write durable memory. Fossil
write-back must use its own provenance-bearing propose/validate/commit boundary.

## Observability status

Normal PR18 execution and the native P0 staging path do not export OpenTelemetry or Langfuse spans;
the current runtime contract does not export OpenTelemetry or Langfuse telemetry.
Historical SSC observation adapters are quarantined and are not loaded by the normal import
boundary. Native receipts currently record task/stage/attempt/generation, requested and actual
model, route, endpoint, timeout layers, stream mode, tool count, durable-progress events, worker
lifecycle, and mechanical result. A future exporter may mirror those fields; traces are evidence
about execution, not proof that model output is true.

## Required live-gate checks

Before attaching a real route, the stage contract and capability policy must be frozen. The gate
must separately probe chat streaming/non-streaming behavior, reject embedding/rerank/image routes
for ordinary V4 stages, allow search only for an explicitly authorized external-research stage,
and preserve the requested/actual model and timeout provenance. A successful HTTP response alone
does not satisfy the objective checker.
