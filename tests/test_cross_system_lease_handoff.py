from __future__ import annotations

from cortex_v4.control.assurance import AssuranceWorkOrder, MutationPhase, WorkEvent, WorkEventKind
from cortex_v4.control.assurance_store import DurableAssuranceStore
from cortex_v4.control.direct_assurance_controller import DirectAssuranceController, MutationRunStatus
from cortex_v4.control.fenced_effect_port import FencedEffectMutationPort
from cortex_v4.control.fenced_effect_target import FencedEffectTargetClient


def _order():
    return AssuranceWorkOrder(
        work_order_id="wo-handoff",
        artifact_id="artifact-handoff",
        artifact_version="v1",
        mutating=True,
        initial_epoch=0,
        initial_fence_token="fence-0",
    )


def _takeover():
    return WorkEvent(
        work_order_id="wo-handoff",
        kind=WorkEventKind.LEASE_TAKEOVER,
        actor_id="lease-controller",
        artifact_id="artifact-handoff",
        artifact_version="v1",
        epoch=1,
        fence_token="fence-1",
    )


def test_target_advances_first_then_stale_apply_blocks_durably_until_v4_catches_up(tmp_path):
    target = FencedEffectTargetClient(tmp_path / "target-first.db")
    target.initialize_lease(epoch=0, fence_token="fence-0")
    port = FencedEffectMutationPort(target)

    with DurableAssuranceStore(tmp_path / "v4-target-first.db") as store:
        store.register_work_order(_order())
        advanced = False

        def inject(point):
            nonlocal advanced
            if point == "before_effect_apply" and not advanced:
                advanced = True
                target.advance_lease(epoch=1, fence_token="fence-1")

        blocked = DirectAssuranceController(store, failure_injector=inject).execute_mutation(
            work_order_id="wo-handoff",
            port=port,
        )
        assert blocked.status is MutationRunStatus.BLOCKED
        assert blocked.snapshot.mutation_phase is MutationPhase.INTENT_DURABLE
        assert target.effect_count() == 0
        events = store.load_events("wo-handoff")
        assert [event.kind for event in events] == [
            WorkEventKind.MUTATION_INTENT,
            WorkEventKind.MUTATION_RECOVERY_DISPOSITION,
        ]
        assert events[-1].decision == "block"
        assert events[-1].evidence_refs[0].startswith("port-lease-mismatch:")

        store.append_event(_takeover())
        recovered = DirectAssuranceController(store).execute_mutation(
            work_order_id="wo-handoff",
            port=port,
        )
        assert recovered.status is MutationRunStatus.COMMITTED
        assert recovered.snapshot.current_epoch == 1
        assert target.effect_count() == 1


def test_v4_advances_first_then_target_mismatch_blocks_durably_until_target_catches_up(tmp_path):
    target = FencedEffectTargetClient(tmp_path / "v4-first-target.db")
    target.initialize_lease(epoch=0, fence_token="fence-0")
    port = FencedEffectMutationPort(target)

    with DurableAssuranceStore(tmp_path / "v4-first.db") as store:
        store.register_work_order(_order())
        store.append_event(_takeover())

        blocked = DirectAssuranceController(store).execute_mutation(
            work_order_id="wo-handoff",
            port=port,
        )
        assert blocked.status is MutationRunStatus.BLOCKED
        assert blocked.snapshot.current_epoch == 1
        assert blocked.snapshot.mutation_phase is MutationPhase.INTENT_DURABLE
        assert target.effect_count() == 0
        events = store.load_events("wo-handoff")
        assert [event.kind for event in events] == [
            WorkEventKind.LEASE_TAKEOVER,
            WorkEventKind.MUTATION_INTENT,
            WorkEventKind.MUTATION_RECOVERY_DISPOSITION,
        ]
        assert events[-1].decision == "block"

        target.advance_lease(epoch=1, fence_token="fence-1")
        recovered = DirectAssuranceController(store).execute_mutation(
            work_order_id="wo-handoff",
            port=port,
        )
        assert recovered.status is MutationRunStatus.COMMITTED
        assert recovered.snapshot.current_epoch == 1
        assert target.effect_count() == 1
