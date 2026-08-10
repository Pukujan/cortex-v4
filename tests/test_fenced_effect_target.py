from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from cortex_v4.control.assurance import AssuranceWorkOrder, MutationPhase, WorkEvent, WorkEventKind
from cortex_v4.control.assurance_store import DurableAssuranceStore
from cortex_v4.control.direct_assurance_controller import (
    DirectAssuranceController,
    MutationRunStatus,
    ObservationState,
)
from cortex_v4.control.fenced_effect_target import (
    EffectTargetProcessError,
    FencedEffectTargetClient,
    SubprocessFencedMutationPort,
)


def _order():
    return AssuranceWorkOrder(
        work_order_id="wo-target",
        artifact_id="artifact-target",
        artifact_version="v1",
        mutating=True,
        initial_epoch=0,
        initial_fence_token="fence-0",
    )


def _takeover():
    return WorkEvent(
        work_order_id="wo-target",
        kind=WorkEventKind.LEASE_TAKEOVER,
        actor_id="lease-controller",
        artifact_id="artifact-target",
        artifact_version="v1",
        epoch=1,
        fence_token="fence-1",
    )


def test_target_rejects_stale_apply_before_external_effect(tmp_path):
    client = FencedEffectTargetClient(tmp_path / "target.db")
    client.initialize_lease(epoch=0, fence_token="fence-0")
    client.advance_lease(epoch=1, fence_token="fence-1")

    with pytest.raises(EffectTargetProcessError) as exc:
        client.apply(
            idempotency_key="effect-1",
            epoch=0,
            fence_token="fence-0",
        )
    assert exc.value.error_type == "StaleLeaseError"
    assert client.effect_count() == 0


def test_target_apply_is_idempotent_and_new_lease_can_observe_old_effect(tmp_path):
    client = FencedEffectTargetClient(tmp_path / "target.db")
    client.initialize_lease(epoch=0, fence_token="fence-0")
    client.apply(idempotency_key="effect-1", epoch=0, fence_token="fence-0")
    client.apply(idempotency_key="effect-1", epoch=0, fence_token="fence-0")
    assert client.effect_count() == 1

    before = client.observe(
        idempotency_key="effect-1",
        epoch=0,
        fence_token="fence-0",
    )
    assert before.state is ObservationState.APPLIED
    client.advance_lease(epoch=1, fence_token="fence-1")
    after = client.observe(
        idempotency_key="effect-1",
        epoch=1,
        fence_token="fence-1",
    )
    assert after == before

    with pytest.raises(EffectTargetProcessError) as exc:
        client.observe(
            idempotency_key="effect-1",
            epoch=0,
            fence_token="fence-0",
        )
    assert exc.value.error_type == "StaleLeaseError"


def test_target_effect_survives_process_death_after_commit(tmp_path):
    target_path = tmp_path / "target-crash.db"
    code = r'''
import os
import sys
from cortex_v4.control.fenced_effect_target import FencedEffectTargetStore

path = sys.argv[1]
store = FencedEffectTargetStore(path)
store.initialize_lease(epoch=0, fence_token="fence-0")
store.apply(idempotency_key="effect-crash", epoch=0, fence_token="fence-0")
os._exit(17)
'''
    env = dict(os.environ)
    root = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-c", code, str(target_path)],
        env=env,
        check=False,
    )
    assert result.returncode == 17

    client = FencedEffectTargetClient(target_path)
    assert client.effect_count() == 1
    observation = client.observe(
        idempotency_key="effect-crash",
        epoch=0,
        fence_token="fence-0",
    )
    assert observation.state is ObservationState.APPLIED


def test_direct_controller_commits_through_separate_fenced_target(tmp_path):
    assurance_path = tmp_path / "assurance.db"
    target_client = FencedEffectTargetClient(tmp_path / "target.db")
    target_client.initialize_lease(epoch=0, fence_token="fence-0")
    port = SubprocessFencedMutationPort(target_client)

    with DurableAssuranceStore(assurance_path) as store:
        store.register_work_order(_order())
        result = DirectAssuranceController(store).execute_mutation(
            work_order_id="wo-target",
            port=port,
        )
        assert result.status is MutationRunStatus.COMMITTED
        assert result.snapshot.committed_success is True
        assert target_client.effect_count() == 1


def test_target_side_takeover_blocks_stale_external_mutation_then_new_lease_recovers(tmp_path):
    assurance_path = tmp_path / "assurance.db"
    target_client = FencedEffectTargetClient(tmp_path / "target.db")
    target_client.initialize_lease(epoch=0, fence_token="fence-0")
    port = SubprocessFencedMutationPort(target_client)

    with DurableAssuranceStore(assurance_path) as store:
        store.register_work_order(_order())
        takeover_done = False

        def inject(point):
            nonlocal takeover_done
            if point != "before_effect_apply" or takeover_done:
                return
            takeover_done = True
            # Advance both independent authorities. The old invocation remains
            # pinned to epoch/fence 0 and must fail at the target before effect.
            target_client.advance_lease(epoch=1, fence_token="fence-1")
            store.append_event(_takeover())

        with pytest.raises(EffectTargetProcessError) as exc:
            DirectAssuranceController(store, failure_injector=inject).execute_mutation(
                work_order_id="wo-target",
                port=port,
            )
        assert exc.value.error_type == "StaleLeaseError"
        assert target_client.effect_count() == 0
        snapshot = store.snapshot("wo-target")
        assert snapshot.current_epoch == 1
        assert snapshot.mutation_phase is MutationPhase.INTENT_DURABLE

        recovered = DirectAssuranceController(store).execute_mutation(
            work_order_id="wo-target",
            port=port,
        )
        assert recovered.status is MutationRunStatus.COMMITTED
        assert recovered.snapshot.current_epoch == 1
        assert target_client.effect_count() == 1


def test_new_lease_observes_effect_committed_before_old_invocation_died(tmp_path):
    assurance_path = tmp_path / "assurance.db"
    target_client = FencedEffectTargetClient(tmp_path / "target.db")
    target_client.initialize_lease(epoch=0, fence_token="fence-0")
    port = SubprocessFencedMutationPort(target_client)

    with DurableAssuranceStore(assurance_path) as store:
        store.register_work_order(_order())

        def crash_after_apply(point):
            if point == "after_effect_apply":
                raise RuntimeError("controller died after target commit")

        with pytest.raises(RuntimeError, match="controller died after target commit"):
            DirectAssuranceController(store, failure_injector=crash_after_apply).execute_mutation(
                work_order_id="wo-target",
                port=port,
            )
        assert target_client.effect_count() == 1
        assert store.snapshot("wo-target").mutation_phase is MutationPhase.INTENT_DURABLE

        target_client.advance_lease(epoch=1, fence_token="fence-1")
        store.append_event(_takeover())

        recovered = DirectAssuranceController(store).execute_mutation(
            work_order_id="wo-target",
            port=port,
        )
        assert recovered.status is MutationRunStatus.COMMITTED
        assert recovered.snapshot.current_epoch == 1
        assert target_client.effect_count() == 1
