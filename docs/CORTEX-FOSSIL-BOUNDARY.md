# Cortex v4 ↔ FOSSIL Ownership Boundary

**Date:** 2026-08-10  
**Status:** accepted control-plane boundary, pending FOSSIL live wiring

## Contract

**Cortex v4 is the agent execution/control plane. FOSSIL is the persistent knowledge/memory plane.**

Cortex does not become a second knowledge database. FOSSIL does not become an agent orchestrator.

The legacy `stupidly-simple-cortex` runtime is a migration/archive source only and must not remain a live dependency once the FOSSIL adapter is operational.

## Cortex owns

- live agent/session/mission state;
- task classification;
- methodology selection and executable methodology versions;
- preflight and tool/risk gates;
- worker/model selection and dispatch;
- retries, timeouts, decomposition, fan-out and merge strategy;
- task-level context-window/resource budgets;
- the decision to direct-read, request compression/context construction, raise a budget, or decompose;
- operational session ledger/checkpoints;
- closeout generation;
- pending candidate proposals when persistent memory is temporarily unavailable.

Cortex can decide **when memory is needed** and **what constraints a memory request has**. It cannot decide the durable semantic state returned by memory.

## FOSSIL owns

- immutable source evidence;
- stable source/artifact/claim/relation/citation IDs;
- durable claims and relationships;
- provenance;
- lifecycle/current-state semantics;
- disagreement/supersession/retraction/history;
- lineage reconstruction;
- knowledge packs/read-write boundaries;
- redaction/suppression state;
- knowledge-changing durable events;
- proposal validation and durable commit;
- corpus retrieval semantics, including lifecycle/lineage/citation safeguards;
- evidence-safe context construction over FOSSIL-sourced material;
- any lossy compression applied to FOSSIL-sourced evidence through the replaceable ContextProvider/context service;
- rebuildable graph/vector/lexical projections behind FOSSIL service contracts.

Cortex must consume these through an adapter/service boundary rather than recreating them in its own state store.

## Replaceable model/API service boundary

Models, provider APIs, LiteLLM, local model servers, embedding services, rerankers, and future gateways are **replaceable cognitive/transport services**. They must not become hidden control planes.

### Cortex owns logical execution policy

Cortex owns the task-level mechanics that change control flow:

- which methodology step runs next;
- whether another model turn is warranted;
- whole-task deadline;
- per-turn deadline;
- retry budget;
- fallback/escalation budget;
- decomposition/fan-out/merge decisions;
- checkpoint/recovery generation;
- terminal `COMPLETED` / `BLOCKED` / `FAILED` determination after mechanical verification.

A provider or gateway must not independently decide to loop the methodology, retry the whole logical task, declare completion, or silently escalate to a stronger model.

### LiteLLM / provider adapters own transport mechanics only

The transport layer may own bounded protocol-level behavior such as:

- provider endpoint and authentication wiring;
- request/response serialization;
- provider-specific parameter translation;
- connection pooling;
- low-level node/provider failover within an explicitly allowed route;
- one bounded transport retry when the Cortex policy explicitly permits it;
- normalization of provider response metadata and errors;
- reporting requested vs actual provider/model/route identity.

Transport retries/failover must be visible to Cortex. Hidden retry loops are forbidden.

### Shared machine-readable capability registry

Cortex workers must **not guess** how an API or model behaves. A shared versioned capability/limit registry must describe each invokable model/service route.

At minimum, each route/profile should expose when known:

- stable route/profile ID;
- provider/gateway/service kind;
- requested model alias and known actual/upstream identity;
- endpoint/protocol (`chat`, `responses`, `embedding`, `rerank`, local runtime, etc.);
- supported input/output modalities;
- tool/function-calling support and schema constraints;
- structured-output / JSON-mode support;
- streaming support;
- context-window limit;
- maximum output-token limit;
- embedding dimension(s) and normalization/prompt policy where applicable;
- reranker/document-count or payload constraints where applicable;
- request body / attachment / batch-size limits;
- supported parameter names/ranges;
- provider/model concurrency limits;
- shared account/key-pool rate limits and quota scope;
- known timeout floors/ceilings;
- cost/accounting unit where known;
- privacy/retention/ZDR status where known;
- canonical normalized error classes;
- retryability/fallback eligibility by error class;
- required cooldown / `retry-after` behavior when supplied;
- health/availability state and last successful probe;
- exact registry/version/probe provenance.

Unknown values must be represented as `unknown`/unverified rather than guessed from model names or historical behavior.

### Shared limits are centrally coordinated

Provider quotas, key-pool limits, model concurrency and rate limits are shared resources. They must be coordinated centrally by the transport/capability service rather than rediscovered independently by each WorkOrder.

A worker asks for a capability/profile; it does not invent sleeps, concurrency, rate-limit heuristics, alternate endpoints, or provider-specific retry loops.

Conceptually:

```text
Cortex methodology step
  -> summon(capability/profile, task constraints, budget)
  -> dispatcher resolves approved route from shared registry
  -> transport performs one bounded mechanical invocation
  -> normalized result/error + actual identity + limits/usage metadata
  -> Cortex decides next logical state
```

The dispatcher may select among pre-approved equivalent routes according to explicit policy and current capacity, but it must return the selected route and actual identity. A fallback is an execution fact, never invisible behavior.

This registry is **shared operational configuration**, not methodology text and not FOSSIL semantic truth. It may be backed by config/code/service state, but all Cortex workers should consume the same resolved contract/version for a given run so API/model limits and failure semantics are not independently reimplemented.

### Normalized error contract

Provider-specific errors should be normalized into a small shared taxonomy so methodology code does not parse vendor strings. Examples include:

- `rate_limited`;
- `quota_exhausted`;
- `context_limit_exceeded`;
- `unsupported_feature`;
- `invalid_request`;
- `authentication_failed`;
- `provider_unavailable`;
- `transport_timeout`;
- `model_timeout`;
- `content_policy_rejected`;
- `capacity_exhausted`;
- `response_malformed`;
- `unknown_provider_error`.

Each normalized error carries structured metadata such as retryability, suggested cooldown when known, whether same-route retry is permitted, whether fallback is permitted, and the raw provider reference for debugging. Cortex consumes the normalized contract and owns the logical reaction.

### Methodology remains provider-agnostic

Methodology definitions should describe **what capability is required and what success condition must be met**, not provider-specific invocation recipes.

Bad:

```text
call provider X; if 429 sleep 30s; retry three times; then call provider Y; loop until answer looks complete
```

Preferred:

```text
require capability: bounded_code_worker
success: deterministic verifier passes
budget: 2 turns, 1 same-route retry, 1 approved fallback
```

The shared dispatcher/capability registry supplies the mechanical API details. Cortex supplies the execution policy. The methodology does not need to know vendor quirks.

### Replaceability invariant

Replacing LiteLLM, a provider, a model, or a local model server should require changing the transport adapter/registry and re-running relevant evals—not rewriting Cortex methodology or FOSSIL semantics.

Model price, model confidence, provider status and routing score never create semantic truth or task completion authority.

## Retrieval split

Cortex supplies intent/constraints:

```text
query/task intent
pack/read scope
risk class
latency/resource preference
context budget
direct-read/compression/decomposition allowance
```

FOSSIL executes its approved knowledge retrieval semantics:

```text
retrieval policy
lexical/dense/hybrid/rerank implementation
lifecycle resolution
lineage resolution
pack isolation
exact citation/source resolution
fallback/degraded identity
```

Cortex must not directly query Neo4j/vector indexes as a truth path that bypasses FOSSIL's durable resolution rules.

## Persistent-memory write path

A Cortex closeout, model response, research result, worker consensus, or compressed summary is not automatically memory.

```text
Cortex candidate
 -> FOSSIL proposal
 -> provenance/source/run references
 -> schema/reference/scope validation
 -> evidence/risk/policy gate
 -> durable commit
 -> projection update
```

If FOSSIL is unavailable, Cortex may persist an explicit `pending_uncommitted` proposal. It must not report durable-memory success.

## Working memory vs persistent memory

### Cortex working memory

May include:

- task/session state;
- pack/query request receipts;
- tool history;
- active worker assignments;
- retry/decomposition state;
- selected temporary context returned by FOSSIL;
- compressed Cortex-owned operational/session notes;
- operational checkpoints.

This may expire or be reconstructed.

### FOSSIL persistent memory

Includes evidence, stable semantic identity, claims/relations, provenance, lifecycle, lineage, exact citations, redaction state, and knowledge-changing events.

This must survive Cortex replacement, model replacement, graph deletion/rebuild, index replacement, and machine movement.

## Context construction and compression

Compression has two owners because policy and evidence transformation are different concerns.

### Cortex owns the decision

Cortex chooses:

- the target context budget;
- whether direct-read is acceptable;
- whether FOSSIL context construction/compression is permitted;
- whether a larger-context worker/model is worth using;
- whether the task should decompose instead.

### FOSSIL owns compression of FOSSIL evidence

When source material comes from FOSSIL, evidence-safe selection/context construction and any lossy compression run behind FOSSIL's ContextProvider/context-service boundary.

This keeps these semantics together:

- pack/ACL/sensitivity filtering;
- redaction/suppression;
- stable source/claim/citation identity;
- exact citation spans/hashes;
- lifecycle/lineage distinctions;
- source→compressed mapping;
- protected-span verification.

The hard security rule is **filter first, compress second**. A compressor must not receive material the caller was not authorized to retrieve and then try to redact it afterward.

Rules:

- compressed packets are temporary untrusted context;
- source evidence is never overwritten;
- required stable IDs/citations/numbers/dates/code identifiers can be declared protected;
- preservation failure fails closed;
- if the budget cannot be met safely, FOSSIL returns an explicit outcome such as `larger_context_required`, `direct_source_read_required`, `decompose_required`, or `unsafe_to_compress`;
- Cortex then decides the orchestration response;
- a summary proposed for durable storage becomes a new derived FOSSIL proposal with provenance.

Cortex may still compress **its own** operational/session notes because they are Cortex working memory, not FOSSIL evidence.

The old SSC compressor/protected-span work is prior art only. Its design can inform FOSSIL's ContextProvider after independent tests; neither Cortex nor FOSSIL requires SSC at runtime.

The canonical FOSSIL-side detail is tracked in `Pukujan/fossil-core` PR #72 and integration issue #73.

## Cluster deployment

Gravebuster and the local PC are compute/storage hosts, not semantic owners.

Initial deployment should use one logical FOSSIL durable commit authority. Multiple machines may host projections, indexes, rerankers, model services, caches and replicas.

Do not infer multi-master durable writes from multi-machine deployment. A future multi-writer design requires an explicit concurrency/consensus contract and proof.

## Legacy SSC

After the FOSSIL adapter lands:

- no Cortex session should require SSC search/index/ontology for persistent memory;
- no SSC current-state graph or generated conclusion is authoritative;
- old SSC runtime state is not imported as FOSSIL truth;
- old SSC research prose is historical/unverified material unless independently revalidated.

### Evaluation estate exception

SSC contains a valuable **evaluation estate** separate from its memory/runtime:

- deterministic checker-decided hard gold;
- third-party-derived benchmark slices;
- semi-ground/semi-truth calibration data;
- rubrics;
- checker/oracle code;
- frozen tests;
- checker cores/resolvers;
- promotion/quarantine artifacts;
- manifests/reports.

These should be extracted by exact source commit/path/hash into a standalone evaluation archive. Cortex may later consume that archive by version. It must not query SSC as a live eval service.

Historical SSC indexes/counts are not themselves authoritative; extraction must inventory actual bytes and rerun integrity/reproduction checks.

Additional owner-supplied assets that never reached GitHub must enter the standalone archive as separate immutable source bundles, not overwrite the committed SSC extraction.

### Deduped evaluation views

Cortex should consume versioned **derived** evaluation views, not mutate the raw archive.

The archive must preserve:

- immutable raw rows/assets;
- normalized rows with source-preserving identities;
- exact-duplicate clusters and member provenance;
- near-duplicate/overlap candidates with method/version/threshold metadata;
- mutation/counterexample families that must not be collapsed as ordinary duplicates;
- conflicting labels/authorities;
- train/eval/holdout leakage findings;
- historically exposed holdout status;
- reproducible dedupe/split manifests and hashes.

Similarity or model consensus is not label authority. A filename saying `hard_gold` is not sufficient without checker/provenance revalidation.

## Anti-drift rules

Cortex must never:

- create a competing canonical claim/current-state database;
- let session state override FOSSIL lifecycle/lineage;
- treat retrieval score/model confidence/consensus as truth;
- make retrieved FOSSIL documents executable control policy;
- write directly to FOSSIL graph/vector projections as semantic authority;
- persist compressed FOSSIL context as replacement source evidence;
- bypass FOSSIL ContextProvider/ACL/redaction semantics with a parallel evidence compressor;
- mutate raw evaluation assets while deduplicating or splitting them;
- infer hard gold from duplication, similarity, or filename alone;
- hide failed/pending memory commits.

FOSSIL must never:

- decide which agent/tool runs next;
- own Cortex mission orchestration;
- mutate Cortex session state as knowledge truth;
- couple durable knowledge semantics to Cortex implementation details.

## Adapter target

The replacement for the current SSC adapters should expose a narrow FOSSIL-facing capability set, conceptually:

```text
search
read
lineage
context
propose
validate
commit
```

`context` is where FOSSIL returns evidence-safe bounded context and explicit preservation/decomposition outcomes. Cortex's mechanical controller remains the caller/control layer. The adapter translates requests and receipts; it does not duplicate FOSSIL semantics.

## Required integration proof before declaring migration complete

1. Cortex preflight/search can use FOSSIL without importing SSC runtime modules.
2. Current/history queries preserve FOSSIL lifecycle/lineage behavior.
3. Exact citations/stable IDs survive FOSSIL context construction/compression.
4. Pack scope and redaction constraints cannot be bypassed through Cortex or the ContextProvider.
5. Unsafe compression returns explicit fail-closed/decompose/direct-read outcomes.
6. FOSSIL outage produces explicit pending/uncommitted memory state, never false success.
7. Graph/vector projection failure does not erase persistent memory.
8. Gravebuster/local-PC host movement does not change stable knowledge identity.
9. Old SSC can be unavailable/offline while normal Cortex+FOSSIL sessions still pass.
10. Evaluation assets, if used, resolve from the standalone versioned archive rather than SSC.
11. Deduped eval views round-trip to immutable raw source rows and do not leak holdout families across splits.
12. Replacing a provider/model/gateway does not require methodology changes; only the capability registry/adapter/evals change.
13. Provider-specific limits/errors are consumed through the shared normalized registry/error contract rather than hard-coded in methodology workers.
14. Hidden provider/gateway retry loops are absent or mechanically visible and bounded by Cortex policy.
