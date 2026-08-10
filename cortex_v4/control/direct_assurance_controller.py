"""Direct mutation controller over the durable V4 assurance event store.

The controller implements one deliberately narrow mutation protocol:

1. durably append ``mutation.intent``;
2. observe before applying, so recovery never blindly repeats an ambiguous
   effect;
3. apply through an idempotent, fenced mutation port only when observation says
   the effect is absent;
4. independently observe the resulting effect and durably append the receipt;
5. durably append the decision; and
6. expose committed success only after that decision transaction commits.

Each invocation pins the epoch/fence it started with. It never refreshes into a
new lease after takeover. The store can therefore reject a stale event receipt,
while a separate recovery invocation may observe the idempotent external effect
under the newer lease.

The store prevents stale events from committing. Preventing a stale worker from
mutating the external system itself requires the supplied port to enforce the
provided epoch/fence token atomically with the effect. That boundary is explicit
rather than inferred from process-local state.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Protocol

from cortex_v4.control.assurance import (
    AssuranceSnapshot,
    AssuranceWorkOrder,
    MutationPhase,
    WorkEvent,
    WorkEventKind,
)
from cortex_v4.control.assurance_store import DurableAssuranceStore


class ObservationState(str, Enum):
    ABSENT = "absent"
    APPLIED = "applied"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class MutationObservation:
    state: ObservationState
    evidence_ref: str | None = None


class IdempotentFencedMutationPort(Protocol):
    """External mutation boundary required by the direct controller.

    Implementations must make ``idempotency_key`` stable across retries and must
    reject stale ``epoch``/``fence_token`` values at the external authority
    boundary if stale-worker exclusion is required for the target system.
    """

    def observe(
        self,
        *,
        idempotency_key: str,
        epoch: int,
        fence_token: str,
    ) -> MutationObservation: ...

    def apply(
        self,
        *,
        idempotency_key: str,
        epoch: int,
        fence_token: str,
    ) -> None: ...


class MutationRunStatus(str, Enum):
    COMMITTED = "committed"
    DECIDED = "decided"
    NEEDS_ESCALATION = "needs_escalation"


@dataclass(frozen=True)
class DirectMutationResult:
    status: MutationRunStatus
    snapshot: AssuranceSnapshot
    idempotency_key: str
    applied_now: bool
    observation: MutationObservation | None


FailureInjector = Callable[[str], None]


class DirectAssuranceController:
    """One direct execution surface whose state is entirely store-derived."""

    def __init__(
        self,
        store: DurableAssuranceStore,
        *,
        actor_id: str = "direct-controller",
        observer_actor_id: str = "direct-observer",
        decision_actor_id: str = "direct-decision",
        failure_injector: FailureInjector | None = None,
    ):
        self.store = store
        self.actor_id = actor_id
        self.observer_actor_id = observer_actor_id
        self.decision_actor_id = decision_actor_id
        self.failure_injector = failure_injector

    def _checkpoint(self, name: str) -> None:
        if self.failure_injector is not None:
            self.failure_injector(name)

    @staticmethod
    def _mutation_event(
        work_order: AssuranceWorkOrder,
        kind: WorkEventKind,
        *,
        actor_id: str,
        epoch: int,
        fence_token: str,
        parents=(),
        evidence=(),
        decision=None,
    ) -> WorkEvent:
        return WorkEvent(
            work_order_id=work_order.work_order_id,
            kind=kind,
            actor_id=actor_id,
            artifact_id=work_order.artifact_id,
            artifact_version=work_order.artifact_version,
            epoch=epoch,
            fence_token=fence_token,
            parent_event_cids=tuple(parents),
            evidence_refs=tuple(evidence),
            decision=decision,
        )

    @staticmethod
    def _find_event(events: tuple[WorkEvent, ...], kind: WorkEventKind) -> WorkEvent | None:
        for event in events:
            if event.kind is kind:
                return event
        return None

    def execute_mutation(
        self,
        *,
        work_order_id: str,
        port: IdempotentFencedMutationPort,
        decision: str = "commit",
        decision_evidence_refs: tuple[str, ...] = ("controller-decision",),
    ) -> DirectMutationResult:
        work_order = self.store.load_work_order(work_order_id)
        if not work_order.mutating:
            raise ValueError("direct mutation controller requires a mutating work order")

        events = self.store.load_events(work_order_id)
        snapshot = self.store.snapshot(work_order_id)
        intent = self._find_event(events, WorkEventKind.MUTATION_INTENT)

        if snapshot.mutation_phase is MutationPhase.DECISION_DURABLE:
            if intent is None:
                raise RuntimeError("durable mutation decision exists without intent")
            status = (
                MutationRunStatus.COMMITTED
                if snapshot.mutation_decision == "commit"
                else MutationRunStatus.DECIDED
            )
            return DirectMutationResult(
                status=status,
                snapshot=snapshot,
                idempotency_key=intent.cid,
                applied_now=False,
                observation=None,
            )

        # This invocation owns only the lease context it observed on entry. It
        # must never adopt a newer fence after a concurrent takeover.
        invocation_epoch = snapshot.current_epoch
        invocation_fence = snapshot.current_fence_token

        if snapshot.mutation_phase is MutationPhase.NONE:
            if intent is not None:
                raise RuntimeError("mutation intent event exists but snapshot phase is NONE")
            intent = self._mutation_event(
                work_order,
                WorkEventKind.MUTATION_INTENT,
                actor_id=self.actor_id,
                epoch=invocation_epoch,
                fence_token=invocation_fence,
            )
            _, snapshot = self.store.append_event(intent)
            self._checkpoint("after_intent_commit")
        elif intent is None:
            raise RuntimeError("mutation phase requires a durable intent event")

        idempotency_key = intent.cid
        applied_now = False
        observation: MutationObservation | None = None

        snapshot = self.store.snapshot(work_order_id)
        if snapshot.mutation_phase is MutationPhase.INTENT_DURABLE:
            # Recovery always observes before replaying an external mutation.
            self._checkpoint("before_recovery_observe")
            observation = port.observe(
                idempotency_key=idempotency_key,
                epoch=invocation_epoch,
                fence_token=invocation_fence,
            )
            self._checkpoint("after_recovery_observe")

            if observation.state is ObservationState.AMBIGUOUS:
                return DirectMutationResult(
                    status=MutationRunStatus.NEEDS_ESCALATION,
                    snapshot=self.store.snapshot(work_order_id),
                    idempotency_key=idempotency_key,
                    applied_now=False,
                    observation=observation,
                )

            if observation.state is ObservationState.ABSENT:
                self._checkpoint("before_effect_apply")
                # Strong stale-worker exclusion at this boundary depends on the
                # external port enforcing epoch/fence together with the effect.
                port.apply(
                    idempotency_key=idempotency_key,
                    epoch=invocation_epoch,
                    fence_token=invocation_fence,
                )
                applied_now = True
                self._checkpoint("after_effect_apply")
                observation = port.observe(
                    idempotency_key=idempotency_key,
                    epoch=invocation_epoch,
                    fence_token=invocation_fence,
                )
                self._checkpoint("after_effect_observe")

            if observation.state is not ObservationState.APPLIED or not observation.evidence_ref:
                return DirectMutationResult(
                    status=MutationRunStatus.NEEDS_ESCALATION,
                    snapshot=self.store.snapshot(work_order_id),
                    idempotency_key=idempotency_key,
                    applied_now=applied_now,
                    observation=observation,
                )

            effect_event = self._mutation_event(
                work_order,
                WorkEventKind.MUTATION_EFFECT_OBSERVED,
                actor_id=self.observer_actor_id,
                epoch=invocation_epoch,
                fence_token=invocation_fence,
                parents=(intent.cid,),
                evidence=(observation.evidence_ref,),
            )
            _, snapshot = self.store.append_event(effect_event)
            self._checkpoint("after_effect_observation_commit")

        snapshot = self.store.snapshot(work_order_id)
        if snapshot.mutation_phase is MutationPhase.EFFECT_OBSERVED:
            events = self.store.load_events(work_order_id)
            effect_event = self._find_event(events, WorkEventKind.MUTATION_EFFECT_OBSERVED)
            if effect_event is None:
                raise RuntimeError("effect-observed phase exists without effect receipt")
            decision_event = self._mutation_event(
                work_order,
                WorkEventKind.MUTATION_DECISION,
                actor_id=self.decision_actor_id,
                epoch=invocation_epoch,
                fence_token=invocation_fence,
                parents=(effect_event.cid,),
                evidence=decision_evidence_refs,
                decision=decision,
            )
            _, snapshot = self.store.append_event(decision_event)
            self._checkpoint("after_decision_commit")

        final = self.store.snapshot(work_order_id)
        status = (
            MutationRunStatus.COMMITTED
            if final.committed_success
            else MutationRunStatus.DECIDED
        )
        return DirectMutationResult(
            status=status,
            snapshot=final,
            idempotency_key=idempotency_key,
            applied_now=applied_now,
            observation=observation,
        )
