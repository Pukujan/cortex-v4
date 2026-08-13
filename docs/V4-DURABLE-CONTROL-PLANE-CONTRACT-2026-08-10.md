# V4 Durable Control-Plane Contract

Status: working contract for review. This document defines the V4 direction and required gates;
it is not a claim that every gate is implemented yet.

## Purpose

Cortex V4 is a repeatable, exportable assurance loop for agent work. Its objective is to reduce
human attention cost by making research, construction, verification, recovery, and closeout
observable and mechanically enforceable.

V4 must outlive any particular corpus, model vendor, provider, project, or SSC checkout. SSC is
an evidence corpus and infrastructure/model-behavior reference. It is not V4's control plane.

## Required lifecycle

Every run follows the same bounded lifecycle:

`contract → questions → competing research → plan freeze → implementation → adversarial verification → shadow/pilot → human review → promotion or escalation → closeout`

The orchestrator may not skip a gate because an agent reports completion.

## Control-plane invariants

### 1. Contract before construction

The run records the user objective, non-goals, acceptance criteria, known ambiguities,
verification surfaces, and scope boundary before implementation begins. Ambiguities become frozen
questions or require human review; they are not silently resolved by the orchestrator.

### 2. Research is source-ranked and competitive

Research lanes identify competing theories, alternatives, and the option of not building. Claims
require provenance, citation, source-quality assessment, and cross-vendor attack. Agreement or
model voting alone is not validation.

### 3. External truth is checked before runtime work

Provider, repository, API, and environment facts require a preflight manifest containing source,
retrieval time, freshness, confidence, contradictions, and an observable probe plan. If an
authoritative source cannot be verified, the run records `unverified` and stops or escalates; it
does not invent configuration from a plausible pattern.

### 4. Deadlines and retries have one owner

Provider/node timeout, transport timeout, proxy request timeout, whole-call deadline, agent-turn
deadline, temporal recovery timeout, and queue wait are separate fields. Retry ownership is
explicit. Stacked hidden retries are forbidden because they can turn a bounded task into hours of
repeated work.

### 5. Verification tests the real product surface

Verification is named during research and covers the actual user/system flow: API contracts,
integration wiring, human-like interaction, realistic failure paths, shadow/pilot behavior, and
observable outputs. Code tests are evidence, not proof of completion.

### 6. Closeout is evidence-gated

Closeout requires the input, output, changed files, execution trace, telemetry, verification
results, failure attacks, unresolved risks, and a human-verifiable demo or artifact. A status
message saying “done” is never sufficient.

### 7. Failure loops are bounded and learnable

Each retry or repair records the failed claim, new evidence, changed hypothesis, attempted repair,
and next stopping condition. Repeatedly reproducing the same failure triggers escalation rather
than another identical summon.

## Corpus boundary

The corpus supplies context, history, references, and learned failure data. V4 supplies the
methodology schema, state machine, gate validators, source/authority rules, deadlines, telemetry
contracts, and promotion policy. A corpus migration must not change these invariants without a
versioned V4 contract change.

## Current known gaps

- No canonical provider route manifest reconciles CKFF notices, LiteLLM configuration, SSC routes,
  and observed telemetry.
- Timeout and retry ownership is not yet represented as one enforced policy.
- Research-source verification is not yet a mandatory preflight gate.
- Closeout evidence is not yet enforced independently of agent-authored receipts.
- GitHub tracking is being established; the implementation remains unpromoted until the relevant
  replay and live-provider gates pass.

## Promotion rule

No methodology or runtime capability is promoted as repeatable merely because a lane passes a
synthetic test. Promotion requires independent evidence, adversarial review, a holdout or shadow
run, observable artifacts, and a documented human review decision.
