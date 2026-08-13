from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from cortex_v4.control.assurance import (
    AssuranceError,
    AssuranceWorkOrder,
    MutationPhase,
    WorkEvent,
    WorkEventKind,
)
from cortex_v4.control.assurance_store import (
    AssuranceStoreError,
    DurableAssuranceStore,
)


def _order(*, mutating=True):
    return AssuranceWorkOrder(
        work_order_id="wo-store",
        artifact_id="artifact-store",
        artifact_version="v1",
        mutating=mutating,
        initial_epoch=0,
        initial_fence_token="fence-0",
    )


def _event(
    kind,
    *,
    actor="worker-a",
    epoch=0,
    fence="fence-0",
    evidence=(),
    decision=None,
    parents=(),
):
    return WorkEvent(
        work_order_id="wo-store",
        kind=kind,
        actor_id=actor,
        artifact_id="artifact-store",
        artifact_version="v1",
        epoch=epoch,
        fence_token=fence,
        evidence_refs=tuple(evidence),
        decision=decision,
        parent_event_cids=tuple(parents),
    )


def test_store_requires_file_backed_database():
    with pytest.raises(AssuranceStoreError):
        DurableAssuranceStore(":memory:")


def test_store_uses_wal_full_sync_and_foreign_keys(tmp_path):
    with DurableAssuranceStore(tmp_path / "assurance.db") as store:
        pragmas = store.durability_pragmas()
        assert pragmas["journal_mode"] == "wal"
        assert pragmas["synchronous"] == 2
        assert pragmas["foreign_keys"] == 1


def test_work_order_registration_is_idempotent_but_identity_is_immutable(tmp_path):
    path = tmp_path / "assurance.db"
    order = _order()
    with DurableAssuranceStore(path) as store:
        store.register_work_order(order)
        store.register_work_order(order)
        with pytest.raises(AssuranceStoreError):
            store.register_work_order(
                AssuranceWorkOrder(
                    work_order_id=order.work_order_id,
                    artifact_id=order.artifact_id,
                    artifact_version="v2",
                    mutating=True,
                )
            )


def test_committed_event_survives_close_and_reopen(tmp_path):
    path = tmp_path / "assurance.db"
    event = _event(WorkEventKind.ARTIFACT_PRODUCED, actor="producer")
    with DurableAssuranceStore(path) as store:
        store.register_work_order(_order())
        receipt, snapshot = store.append_event(event)
        assert receipt.duplicate is False
        assert receipt.sequence == 1
        assert snapshot.event_cids == (event.cid,)

    with DurableAssuranceStore(path) as reopened:
        assert reopened.event_count("wo-store") == 1
        assert reopened.load_events("wo-store") == (event,)
        assert reopened.snapshot("wo-store").event_cids == (event.cid,)


def test_retry_after_commit_is_idempotent_across_restart(tmp_path):
    path = tmp_path / "assurance.db"
    event = _event(WorkEventKind.ARTIFACT_PRODUCED, actor="producer")
    with DurableAssuranceStore(path) as store:
        store.register_work_order(_order())
        first, _ = store.append_event(event)
        assert first.duplicate is False

    with DurableAssuranceStore(path) as reopened:
        second, snapshot = reopened.append_event(event)
        assert second.duplicate is True
        assert second.sequence == first.sequence
        assert reopened.event_count("wo-store") == 1
        assert snapshot.event_cids == (event.cid,)


def test_invalid_event_rolls_back_without_partial_append(tmp_path):
    path = tmp_path / "assurance.db"
    with DurableAssuranceStore(path) as store:
        store.register_work_order(_order())
        with pytest.raises(AssuranceError):
            store.append_event(_event(WorkEventKind.TEST_PASSED, actor="tester"))
        assert store.event_count("wo-store") == 0
        assert store.snapshot("wo-store").event_cids == ()


def test_takeover_fence_survives_restart_and_rejects_stale_worker(tmp_path):
    path = tmp_path / "assurance.db"
    takeover = _event(
        WorkEventKind.LEASE_TAKEOVER,
        actor="controller",
        epoch=1,
        fence="fence-1",
    )
    with DurableAssuranceStore(path) as store:
        store.register_work_order(_order())
        store.append_event(takeover)

    with DurableAssuranceStore(path) as reopened:
        with pytest.raises(AssuranceError):
            reopened.append_event(
                _event(
                    WorkEventKind.ARTIFACT_PRODUCED,
                    actor="stale-worker",
                    epoch=0,
                    fence="fence-0",
                )
            )
        fresh, snapshot = reopened.append_event(
            _event(
                WorkEventKind.ARTIFACT_PRODUCED,
                actor="fresh-worker",
                epoch=1,
                fence="fence-1",
                parents=(takeover.cid,),
            )
        )
        assert fresh.duplicate is False
        assert snapshot.current_epoch == 1
        assert snapshot.current_fence_token == "fence-1"
        assert reopened.event_count("wo-store") == 2


def test_mutation_commit_decision_survives_restart(tmp_path):
    path = tmp_path / "assurance.db"
    events = [
        _event(WorkEventKind.MUTATION_INTENT, actor="worker"),
        _event(
            WorkEventKind.MUTATION_EFFECT_OBSERVED,
            actor="observer",
            evidence=("effect-observation",),
        ),
        _event(
            WorkEventKind.MUTATION_DECISION,
            actor="controller",
            evidence=("decision-receipt",),
            decision="commit",
        ),
    ]
    with DurableAssuranceStore(path) as store:
        store.register_work_order(_order())
        for event in events:
            store.append_event(event)

    with DurableAssuranceStore(path) as reopened:
        snapshot = reopened.snapshot("wo-store")
        assert snapshot.mutation_phase is MutationPhase.DECISION_DURABLE
        assert snapshot.committed_success is True
        assert reopened.load_events("wo-store") == tuple(events)


def test_process_death_after_commit_keeps_event_recoverable(tmp_path):
    path = tmp_path / "crash.db"
    code = r'''
import os
import sys
from cortex_v4.control.assurance import AssuranceWorkOrder, WorkEvent, WorkEventKind
from cortex_v4.control.assurance_store import DurableAssuranceStore

path = sys.argv[1]
store = DurableAssuranceStore(path)
store.register_work_order(AssuranceWorkOrder(
    work_order_id="wo-store",
    artifact_id="artifact-store",
    artifact_version="v1",
    mutating=True,
    initial_epoch=0,
    initial_fence_token="fence-0",
))
store.append_event(WorkEvent(
    work_order_id="wo-store",
    kind=WorkEventKind.ARTIFACT_PRODUCED,
    actor_id="producer",
    artifact_id="artifact-store",
    artifact_version="v1",
    epoch=0,
    fence_token="fence-0",
))
os._exit(17)
'''
    env = dict(os.environ)
    root = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-c", code, str(path)],
        env=env,
        check=False,
    )
    assert result.returncode == 17

    with DurableAssuranceStore(path) as reopened:
        assert reopened.event_count("wo-store") == 1
        event = reopened.load_events("wo-store")[0]
        assert event.kind is WorkEventKind.ARTIFACT_PRODUCED
        retry, snapshot = reopened.append_event(event)
        assert retry.duplicate is True
        assert snapshot.event_cids == (event.cid,)
