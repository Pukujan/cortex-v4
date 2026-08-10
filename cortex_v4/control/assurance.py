"""Deterministic reference state model for Cortex V4 assurance work.

This module is intentionally smaller than the production orchestrator.  It
defines the authority and mutation invariants that other execution surfaces can
be tested against.

Key rules encoded here:

* ``completed`` and model verdicts are metadata, not committed authority;
* authority advances only through the typed ladder, without skips;
* mutating work requires durable intent -> observed effect -> durable decision;
* artifact identity/version plus epoch/fence must match every state-changing
  event;
* lease takeover increments the epoch and fences stale workers;
* event parents must already exist, giving the reference model a causal DAG;
* exact event replay is idempotent by content CID.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import hashlib
import json


class AssuranceError(ValueError):
    """Raised when an event violates the reference-model contract."""


class AuthorityState(str, Enum):
    PROPOSED = "proposed"
    PRODUCED = "produced"
    PERSISTED = "persisted"
    TESTED = "tested"
    EXTERNALLY_OBSERVED = "externally_observed"
    INDEPENDENTLY_VERIFIED = "independently_verified"
    PROMOTABLE = "promotable"
    PROMOTED = "promoted"


class MutationPhase(str, Enum):
    NONE = "none"
    INTENT_DURABLE = "intent_durable"
    EFFECT_OBSERVED = "effect_observed"
    DECISION_DURABLE = "decision_durable"


class WorkEventKind(str, Enum):
    ARTIFACT_PRODUCED = "artifact.produced"
    ARTIFACT_PERSISTED = "artifact.persisted"
    TEST_PASSED = "test.passed"
    EXTERNAL_OBSERVED = "external.observed"
    INDEPENDENT_VERIFIED = "independent.verified"
    MARK_PROMOTABLE = "authority.promotable"
    PROMOTE = "authority.promoted"

    MUTATION_INTENT = "mutation.intent"
    MUTATION_EFFECT_OBSERVED = "mutation.effect_observed"
    MUTATION_DECISION = "mutation.decision"
    LEASE_TAKEOVER = "lease.takeover"

    LEGACY_COMPLETED = "legacy.completed"
    MODEL_VERDICT = "model.verdict"


_AUTHORITY_TRANSITIONS: dict[WorkEventKind, tuple[AuthorityState, AuthorityState]] = {
    WorkEventKind.ARTIFACT_PRODUCED: (AuthorityState.PROPOSED, AuthorityState.PRODUCED),
    WorkEventKind.ARTIFACT_PERSISTED: (AuthorityState.PRODUCED, AuthorityState.PERSISTED),
    WorkEventKind.TEST_PASSED: (AuthorityState.PERSISTED, AuthorityState.TESTED),
    WorkEventKind.EXTERNAL_OBSERVED: (
        AuthorityState.TESTED,
        AuthorityState.EXTERNALLY_OBSERVED,
    ),
    WorkEventKind.INDEPENDENT_VERIFIED: (
        AuthorityState.EXTERNALLY_OBSERVED,
        AuthorityState.INDEPENDENTLY_VERIFIED,
    ),
    WorkEventKind.MARK_PROMOTABLE: (
        AuthorityState.INDEPENDENTLY_VERIFIED,
        AuthorityState.PROMOTABLE,
    ),
    WorkEventKind.PROMOTE: (AuthorityState.PROMOTABLE, AuthorityState.PROMOTED),
}


@dataclass(frozen=True)
class AssuranceWorkOrder:
    work_order_id: str
    artifact_id: str
    artifact_version: str
    mutating: bool
    initial_epoch: int = 0
    initial_fence_token: str = "fence-0"


@dataclass(frozen=True)
class WorkEvent:
    work_order_id: str
    kind: WorkEventKind
    actor_id: str
    artifact_id: str
    artifact_version: str
    epoch: int
    fence_token: str
    parent_event_cids: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    decision: str | None = None

    @property
    def cid(self) -> str:
        projection = {
            "work_order_id": self.work_order_id,
            "kind": self.kind.value,
            "actor_id": self.actor_id,
            "artifact_id": self.artifact_id,
            "artifact_version": self.artifact_version,
            "epoch": self.epoch,
            "fence_token": self.fence_token,
            "parent_event_cids": list(self.parent_event_cids),
            "evidence_refs": list(self.evidence_refs),
            "decision": self.decision,
        }
        raw = json.dumps(
            projection,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return "evt_" + hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class AssuranceSnapshot:
    authority_state: AuthorityState
    mutation_phase: MutationPhase
    mutation_decision: str | None
    current_epoch: int
    current_fence_token: str
    producer_actor_id: str | None
    event_cids: tuple[str, ...]

    @property
    def committed_success(self) -> bool:
        return (
            self.mutation_phase is MutationPhase.DECISION_DURABLE
            and self.mutation_decision == "commit"
        )


class AssuranceReferenceModel:
    """Small deterministic oracle used for conformance and crash/replay tests."""

    def __init__(self, work_order: AssuranceWorkOrder):
        if not work_order.work_order_id:
            raise AssuranceError("work_order_id is required")
        if not work_order.artifact_id or not work_order.artifact_version:
            raise AssuranceError("artifact identity and version are required")
        if work_order.initial_epoch < 0 or not work_order.initial_fence_token:
            raise AssuranceError("initial epoch/fence are invalid")
        self.work_order = work_order
        self.snapshot = AssuranceSnapshot(
            authority_state=AuthorityState.PROPOSED,
            mutation_phase=MutationPhase.NONE,
            mutation_decision=None,
            current_epoch=work_order.initial_epoch,
            current_fence_token=work_order.initial_fence_token,
            producer_actor_id=None,
            event_cids=(),
        )
        self._seen: set[str] = set()

    def _validate_identity(self, event: WorkEvent) -> None:
        if event.work_order_id != self.work_order.work_order_id:
            raise AssuranceError("work order mismatch")
        if event.artifact_id != self.work_order.artifact_id:
            raise AssuranceError("artifact id mismatch")
        if event.artifact_version != self.work_order.artifact_version:
            raise AssuranceError("artifact version mismatch")

    def _validate_parents(self, event: WorkEvent) -> None:
        missing = [parent for parent in event.parent_event_cids if parent not in self._seen]
        if missing:
            raise AssuranceError(f"missing causal parent(s): {missing}")

    def _validate_fence(self, event: WorkEvent) -> None:
        if event.epoch != self.snapshot.current_epoch:
            raise AssuranceError(
                f"stale epoch: got {event.epoch}, current {self.snapshot.current_epoch}"
            )
        if event.fence_token != self.snapshot.current_fence_token:
            raise AssuranceError("stale fence token")

    def apply(self, event: WorkEvent) -> AssuranceSnapshot:
        self._validate_identity(event)
        cid = event.cid
        if cid in self._seen:
            return self.snapshot
        self._validate_parents(event)

        if event.kind is WorkEventKind.LEASE_TAKEOVER:
            if event.epoch <= self.snapshot.current_epoch:
                raise AssuranceError("takeover epoch must increase")
            if not event.fence_token or event.fence_token == self.snapshot.current_fence_token:
                raise AssuranceError("takeover requires a new fence token")
            self.snapshot = replace(
                self.snapshot,
                current_epoch=event.epoch,
                current_fence_token=event.fence_token,
                event_cids=self.snapshot.event_cids + (cid,),
            )
            self._seen.add(cid)
            return self.snapshot

        self._validate_fence(event)

        if event.kind in _AUTHORITY_TRANSITIONS:
            expected, target = _AUTHORITY_TRANSITIONS[event.kind]
            if self.snapshot.authority_state is not expected:
                raise AssuranceError(
                    f"authority transition {event.kind.value} requires "
                    f"{expected.value}, got {self.snapshot.authority_state.value}"
                )

            producer = self.snapshot.producer_actor_id
            if event.kind is WorkEventKind.ARTIFACT_PRODUCED:
                producer = event.actor_id
            if event.kind is WorkEventKind.INDEPENDENT_VERIFIED:
                if producer is None:
                    raise AssuranceError("independent verification requires a producer")
                if event.actor_id == producer:
                    raise AssuranceError("producer cannot independently verify its own artifact")
                if not event.evidence_refs:
                    raise AssuranceError("independent verification requires evidence")
            if event.kind is WorkEventKind.PROMOTE and self.work_order.mutating:
                if not self.snapshot.committed_success:
                    raise AssuranceError("mutating work cannot promote before durable commit decision")

            self.snapshot = replace(
                self.snapshot,
                authority_state=target,
                producer_actor_id=producer,
            )

        elif event.kind is WorkEventKind.MUTATION_INTENT:
            if not self.work_order.mutating:
                raise AssuranceError("mutation event on non-mutating work")
            if self.snapshot.mutation_phase is not MutationPhase.NONE:
                raise AssuranceError("mutation intent already recorded")
            self.snapshot = replace(
                self.snapshot,
                mutation_phase=MutationPhase.INTENT_DURABLE,
            )

        elif event.kind is WorkEventKind.MUTATION_EFFECT_OBSERVED:
            if not self.work_order.mutating:
                raise AssuranceError("mutation event on non-mutating work")
            if self.snapshot.mutation_phase is not MutationPhase.INTENT_DURABLE:
                raise AssuranceError("effect observation requires durable intent")
            if not event.evidence_refs:
                raise AssuranceError("effect observation requires evidence")
            self.snapshot = replace(
                self.snapshot,
                mutation_phase=MutationPhase.EFFECT_OBSERVED,
            )

        elif event.kind is WorkEventKind.MUTATION_DECISION:
            if not self.work_order.mutating:
                raise AssuranceError("mutation event on non-mutating work")
            if self.snapshot.mutation_phase is not MutationPhase.EFFECT_OBSERVED:
                raise AssuranceError("mutation decision requires observed effect")
            if event.decision not in {"commit", "compensate", "block", "escalate"}:
                raise AssuranceError("invalid durable mutation decision")
            if not event.evidence_refs:
                raise AssuranceError("mutation decision requires evidence")
            self.snapshot = replace(
                self.snapshot,
                mutation_phase=MutationPhase.DECISION_DURABLE,
                mutation_decision=event.decision,
            )

        elif event.kind in {WorkEventKind.LEGACY_COMPLETED, WorkEventKind.MODEL_VERDICT}:
            # Explicitly metadata-only: these events are preserved in lineage but
            # cannot upgrade authority or create committed success.
            pass

        else:  # pragma: no cover - exhaustive guard for future enum additions
            raise AssuranceError(f"unsupported event kind: {event.kind}")

        self.snapshot = replace(
            self.snapshot,
            event_cids=self.snapshot.event_cids + (cid,),
        )
        self._seen.add(cid)
        return self.snapshot

    @classmethod
    def replay(
        cls,
        work_order: AssuranceWorkOrder,
        events: list[WorkEvent] | tuple[WorkEvent, ...],
    ) -> "AssuranceReferenceModel":
        model = cls(work_order)
        for event in events:
            model.apply(event)
        return model
