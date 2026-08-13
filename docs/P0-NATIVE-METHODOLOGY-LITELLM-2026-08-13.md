# P0 native methodology and LiteLLM staging record

**Status:** implementation/staging evidence only; no production routing, deployment, merge, or
Fossil wiring is authorized by this record.

## Runtime boundary

The native P0 path is:

```text
NativeV4Methodology.preflight
  -> frozen TaskContract
  -> RunBrain (one temporary run workspace)
  -> scoped stage capability
  -> LiteLLM chat or Responses transport
  -> real tools/files/tests
  -> durable checkpoint and generation fence
  -> independent objective checker
  -> structured JSON closeout
```

It does not import `cortex_core`, SSC adapters, an SSC checkout, Fossil, or an SSC corpus. Fossil
read/write remains a later integration boundary. The native path uses chat-capable model calls only:
it has no embedding, reranking, image-generation, or internet-search call path. Search-capable
labels are not permission; internet research requires a separate authorized task contract.

## Contract and brain guarantees

`TaskContract` freezes the objective ID, exact base SHA, task classification, contract revision,
types/interfaces/schemas, dependency DAG, stage ownership, read/write references, model/endpoint
policy, acceptance checks, and generation fence. `NativeV4Methodology` validates the contract and
records a per-stage dispatch decision before `StagedRunner` starts atomic work.

`RunBrain` persists contract, methodology plan, stage state, attempts, artifacts, checkpoints,
receipts, events, and objective closeout under one run ID. Capability handles are checked against
run, role, stage, and exact generation. Implementers cannot read test-author private state; test
authors cannot read holdout material; ordinary workers cannot erase the run or required evidence.
Reads do not renew the active lease. Checkpoints, controller progress, and explicit worker
heartbeats do. A successful run enters an explicit configurable post-closeout grace period,
defaulting to the frozen 24-hour policy; cleanup is controller-owned and append-only
invalidation/quarantine proposals are recorded.

The native closeout is `receipts/closeout.json`. The legacy fixture's `closeout.md` is only an
owner-legible execution view and is not a Fossil commit or correctness authority. Fossil does not
require that Markdown writer.

## LiteLLM seam

`cortex_v4.transport.litellm` supports authenticated `/chat/completions` and `/responses`,
buffered and streamed responses, tool-call deltas, actual-model capture, effective deadline
calculation, and fail-closed handling for non-2xx, malformed, empty, zero-usable, or incomplete
streams. Receipts retain only structured metadata; prompts, headers, keys, provider bodies, and
model response text are not persisted.

`LiteLLMStageWorker` receives a scoped context pack, executes only the stage's approved tool
surface, heartbeats after durable tool progress, and returns a structured stage outcome. A failed
stage retries independently; completed stages are not replayed.

## Observability boundary

The native P0 currently has durable sanitized receipts and event records, not an active
OpenTelemetry or Langfuse exporter. Those systems are useful for execution verification, but a
trace cannot by itself prove that a model's output is correct. The future observability task should
correlate task/stage/attempt/generation, requested/actual model, route, endpoint, stream mode,
timeout layers, tool count, durable-progress time, worker lifecycle, and mechanical checks while
keeping prompts and response text outside the receipt.

## Staging evidence and remaining gate

- Cortex full mechanical suite: 95 tests passing after the native slices and placeholder metric
  repairs.
- LiteLLM bridge/semantic tests: 14 tests passing in the isolated LiteLLM checkout.
- The live provider catalogs exposed `kimi-k2.7-code` on `ckffai.com`, `aws.ckffai.com`, and
  `ckff.dev` under the approved provider credential; they did not expose DeepSeek V4 Flash or
  DeepSeek V4 Flash Free at probe time.
- Short authenticated streamed and non-stream probes succeeded on all three provider routes.
- The deployed public gateway produced an HTTP 200 with an incomplete/zero-usable chat stream;
  the isolated bridge patch keeps the upstream stream open until its response generator closes.
- An isolated LiteLLM process routed to the recommended CKFF endpoint and completed a real
  multi-file coding objective with streamed model/tool calls, a worker death after mutation,
  fencing, three checkpoints, real pytest execution, and an independent objective PASS.

This is not yet the final P0 completion claim. The required repeated-run reliability gate and the
AWS/control granular campaign evidence must be completed after transient CKFF 429/503 provider
conditions are re-probed. No fixture or keyless result substitutes for that gate.
