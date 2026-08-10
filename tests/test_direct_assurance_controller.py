from __future__ import annotations

import pytest

from cortex_v4.control.assurance import (
    AssuranceError,
    AssuranceWorkOrder,
    MutationPhase,
    WorkEvent,
    WorkEventKind,
)
from cortex_v4.control.assurance_store import DurableAssuranceStore
from cortex_v4.control.direct_assurance_controller import (
    DirectAssuranceController,
    MutationObservation,
    MutationRunStatus,
    ObservationState,
)


class InjectedCrash(RuntimeError):
    pass


class MemoryMutationPort:
    def __init__(self, *, ambiguous=False, raise_after_apply_once=False):
        self.ambiguous = ambiguous
        self.raise_after_apply_once = raise_after_apply_once
        self.applied: set[str] = set()
        self.apply_calls = 0
        self.observe_calls = 0
        self.contexts: list[tuple[str, int, str]] = []

    def observe(self, *, idempotency_key, epoch, fence_token):
        self.observe_calls += 1
        self.contexts.append(("observe", epoch, fence_token))
        if self.ambiguous:
            return MutationObservation(
                ObservationState.AMBIGUOUS,
                "ambiguous-observation",
            )
        if idempotency_key in self.applied:
            return MutationObservation(
                ObservationState.APPLIED,
                f"observed:{idempotency_key}",
            )
        return MutationObservation(ObservationState.ABSENT)

    def apply(self, *, idempotency_key, epoch, fence_token):
        self.apply_calls += 1
        self.contexts.append(("apply", epoch, fence_token))
        self.applied.add(idempotency_key)
        if self.raise_after_apply_once:
            self.raise_after_apply_once = False
            raise RuntimeError("transport died after effect")


def _order(*, mutating=True):
    return AssuranceWorkOrder(
        work_order_id="wo-direct",
        artifact_id="artifact-direct",
        artifact_version="v1",
        mutating=mutating,
        initial_epoch=0,
        initial_fence_token="fence-0",
    )


def _registered_store(tmp_path, *, mutating=True):
    store = DurableAssuranceStore(tmp_path / "direct.db")
    store.register_work_order(_order(mutating=mutating))
    return store


def test_direct_controller_happy_path_commits_only_after_observed_effect(tmp_path):
    with _registered_store(tmp_path) as store:
        port = MemoryMutationPort()
        result = DirectAssuranceController(store).execute_mutation(
            work_order_id="wo-direct",
            port=port,
        )
        assert result.status is MutationRunStatus.COMMITTED
        assert result.snapshot.committed_success is True
        assert port.apply_calls == 1
        assert [event.kind for event in store.load_events("wo-direct")] == [
            WorkEventKind.MUTATION_INTENT,
            WorkEventKind.MUTATION_EFFECT_OBSERVED,
            WorkEventKind.MUTATION_DECISION,
        ]


@pytest.mark.parametrize(
    "crash_point",
    [
        "after_intent_commit",
        "before_recovery_observe",
        "after_recovery_observe",
        "before_effect_apply",
        "after_effect_apply",
        "after_effect_observe",
        "after_effect_observation_commit",
        "after_decision_commit",
    ],
)
def test_recovery_from_each_direct_boundary_never_reapplies_effect(tmp_path, crash_point):
    with _registered_store(tmp_path) as store:
        port = MemoryMutationPort()

        def inject(point):
            if point == crash_point:
                raise InjectedCrash(point)

        with pytest.raises(InjectedCrash):
            DirectAssuranceController(store, failure_injector=inject).execute_mutation(
                work_order_id="wo-direct",
                port=port,
            )

        recovered = DirectAssuranceController(store).execute_mutation(
            work_order_id="wo-direct",
            port=port,
        )
        assert recovered.status is MutationRunStatus.COMMITTED
        assert recovered.snapshot.committed_success is True
        assert port.apply_calls == 1
        assert len(port.applied) == 1
        assert store.event_count("wo-direct") == 3


def test_apply_can_raise_after_real_effect_and_recovery_observes_instead_of_replaying(tmp_path):
    with _registered_store(tmp_path) as store:
        port = MemoryMutationPort(raise_after_apply_once=True)
        with pytest.raises(RuntimeError, match="transport died after effect"):
            DirectAssuranceController(store).execute_mutation(
                work_order_id="wo-direct",
                port=port,
            )
        assert store.snapshot("wo-direct").mutation_phase is MutationPhase.INTENT_DURABLE
        assert port.apply_calls == 1
        assert len(port.applied) == 1

        recovered = DirectAssuranceController(store).execute_mutation(
            work_order_id="wo-direct",
            port=port,
        )
        assert recovered.status is MutationRunStatus.COMMITTED
        assert port.apply_calls == 1
        assert store.snapshot("wo-direct").committed_success is True


def test_ambiguous_recovery_observation_blocks_without_applying(tmp_path):
    with _registered_store(tmp_path) as store:
        port = MemoryMutationPort(ambiguous=True)
        result = DirectAssuranceController(store).execute_mutation(
            work_order_id="wo-direct",
            port=port,
        )
        assert result.status is MutationRunStatus.NEEDS_ESCALATION
        assert port.apply_calls == 0
        assert result.snapshot.mutation_phase is MutationPhase.INTENT_DURABLE
        assert store.event_count("wo-direct") == 1


def test_mid_effect_takeover_rejects_old_receipt_and_new_invocation_recovers(tmp_path):
    with _registered_store(tmp_path) as store:
        port = MemoryMutationPort()
        takeover_done = False

        def inject(point):
            nonlocal takeover_done
            if point != "after_effect_apply" or takeover_done:
                return
            takeover_done = True
            store.append_event(
                WorkEvent(
                    work_order_id="wo-direct",
                    kind=WorkEventKind.LEASE_TAKEOVER,
                    actor_id="lease-controller",
                    artifact_id="artifact-direct",
                    artifact_version="v1",
                    epoch=1,
                    fence_token="fence-1",
                )
            )

        with pytest.raises(AssuranceError, match="stale epoch"):
            DirectAssuranceController(store, failure_injector=inject).execute_mutation(
                work_order_id="wo-direct",
                port=port,
            )

        # The stale invocation applied under its original lease but could not
        # attach an effect receipt using the takeover's new fence.
        assert port.apply_calls == 1
        assert ("apply", 0, "fence-0") in port.contexts
        snapshot = store.snapshot("wo-direct")
        assert snapshot.current_epoch == 1
        assert snapshot.mutation_phase is MutationPhase.INTENT_DURABLE

        recovered = DirectAssuranceController(store).execute_mutation(
            work_order_id="wo-direct",
            port=port,
        )
        assert recovered.status is MutationRunStatus.COMMITTED
        assert recovered.snapshot.current_epoch == 1
        assert recovered.snapshot.committed_success is True
        assert port.apply_calls == 1
        assert ("observe", 1, "fence-1") in port.contexts


def test_non_commit_decision_is_durable_but_not_committed_success(tmp_path):
    with _registered_store(tmp_path) as store:
        port = MemoryMutationPort()
        result = DirectAssuranceController(store).execute_mutation(
            work_order_id="wo-direct",
            port=port,
            decision="block",
            decision_evidence_refs=("block-reason",),
        )
        assert result.status is MutationRunStatus.DECIDED
        assert result.snapshot.mutation_decision == "block"
        assert result.snapshot.committed_success is False


def test_direct_mutation_surface_rejects_non_mutating_work_order(tmp_path):
    with _registered_store(tmp_path, mutating=False) as store:
        with pytest.raises(ValueError, match="mutating work order"):
            DirectAssuranceController(store).execute_mutation(
                work_order_id="wo-direct",
                port=MemoryMutationPort(),
            )
