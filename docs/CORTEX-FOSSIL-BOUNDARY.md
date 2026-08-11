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
- context-window/resource budgets;
- compression/decomposition decisions;
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
- rebuildable graph/vector/lexical projections behind FOSSIL service contracts.

Cortex must consume these through an adapter/service boundary rather than recreating them in its own state store.

## Retrieval split

Cortex supplies intent/constraints:

```text
query/task intent
pack/read scope
risk class
latency/resource preference
context budget
direct-read/decomposition allowance
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
- selected temporary context;
- compressed context packets;
- operational checkpoints.

This may expire or be reconstructed.

### FOSSIL persistent memory

Includes evidence, stable semantic identity, claims/relations, provenance, lifecycle, lineage, exact citations, redaction state, and knowledge-changing events.

This must survive Cortex replacement, model replacement, graph deletion/rebuild, index replacement, and machine movement.

## Compression

Cortex owns the budget/decomposition decision. Any compressor used by Cortex must respect protected FOSSIL identities/evidence.

Rules:

- compressed packets are temporary untrusted context;
- source evidence is never overwritten;
- required stable IDs/citations/numbers/code identifiers must be preserved when declared protected;
- preservation failure fails closed;
- if the budget cannot be met safely, Cortex decomposes/direct-reads/raises the budget rather than silently dropping evidence;
- a summary proposed for durable storage becomes a new derived FOSSIL proposal with provenance.

The old SSC compressor/protected-span work is prior art only. Cortex may reuse the design after independent tests; it does not require SSC at runtime.

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

## Anti-drift rules

Cortex must never:

- create a competing canonical claim/current-state database;
- let session state override FOSSIL lifecycle/lineage;
- treat retrieval score/model confidence/consensus as truth;
- make retrieved FOSSIL documents executable control policy;
- write directly to FOSSIL graph/vector projections as semantic authority;
- persist compressed context as replacement source evidence;
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

Cortex's mechanical controller remains the caller/control layer. The adapter translates requests and receipts; it does not duplicate FOSSIL semantics.

## Required integration proof before declaring migration complete

1. Cortex preflight/search can use FOSSIL without importing SSC runtime modules.
2. Current/history queries preserve FOSSIL lifecycle/lineage behavior.
3. Exact citations/stable IDs survive Cortex context construction/compression.
4. Pack scope and redaction constraints cannot be bypassed through Cortex.
5. FOSSIL outage produces explicit pending/uncommitted memory state, never false success.
6. Graph/vector projection failure does not erase persistent memory.
7. Gravebuster/local-PC host movement does not change stable knowledge identity.
8. Old SSC can be unavailable/offline while normal Cortex+FOSSIL sessions still pass.
9. Evaluation assets, if used, resolve from the standalone versioned archive rather than SSC.
