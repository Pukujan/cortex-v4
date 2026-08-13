from __future__ import annotations

import pytest

from cortex_v4.control.assurance import (
    AssuranceError,
    AssuranceWorkOrder,
    WorkEvent,
    WorkEventKind,
)
from cortex_v4.control.assurance_process import (
    AssuranceProcessClient,
    ProcessSurfaceError,
    snapshot_to_dict,
)
from cortex_v4.control.assurance_store import DurableAssuranceStore


def _order():
    return AssuranceWorkOrder(
        work_order_id="wo-process",
        artifact_id="artifact-process",
        artifact_version="v1",
        mutating=False,
        initial_epoch=0,
        initial_fence_token="fence-0",
    )


def _event(kind, *, actor, evidence=(), epoch=0, fence="fence-0"):
    return WorkEvent(
        work_order_id="wo-process",
        kind=kind,
        actor_id=actor,
        artifact_id="artifact-process",
        artifact_version="v1",
        epoch=epoch,
        fence_token=fence,
        evidence_refs=tuple(evidence),
    )


def _authority_sequence():
    return [
        _event(WorkEventKind.ARTIFACT_PRODUCED, actor="producer"),
        _event(WorkEventKind.ARTIFACT_PERSISTED, actor="store"),
        _event(WorkEventKind.TEST_PASSED, actor="tester"),
        _event(WorkEventKind.EXTERNAL_OBSERVED, actor="observer"),
        _event(
            WorkEventKind.INDEPENDENT_VERIFIED,
            actor="verifier",
            evidence=("verification-receipt",),
        ),
        _event(WorkEventKind.MARK_PROMOTABLE, actor="controller"),
        _event(WorkEventKind.PROMOTE, actor="controller"),
    ]


def test_direct_and_process_surfaces_converge_on_same_authority_snapshot(tmp_path):
    order = _order()
    events = _authority_sequence()

    direct_path = tmp_path / "direct.db"
    with DurableAssuranceStore(direct_path) as direct:
        direct.register_work_order(order)
        direct_receipts = []
        for event in events:
            receipt, _ = direct.append_event(event)
            direct_receipts.append(receipt)
        direct_snapshot = snapshot_to_dict(direct.snapshot(order.work_order_id))

    process_path = tmp_path / "process.db"
    client = AssuranceProcessClient(process_path)
    client.register_work_order(order)
    process_receipts = [client.append_event(event)["receipt"] for event in events]
    process_snapshot = client.snapshot(order.work_order_id)

    assert process_snapshot == direct_snapshot
    assert [receipt["event_cid"] for receipt in process_receipts] == [
        receipt.event_cid for receipt in direct_receipts
    ]
    assert [receipt["sequence"] for receipt in process_receipts] == list(
        range(1, len(events) + 1)
    )


def test_process_surface_rejects_same_invalid_authority_skip_as_direct_store(tmp_path):
    order = _order()
    invalid = _event(WorkEventKind.TEST_PASSED, actor="tester")

    direct_path = tmp_path / "direct-invalid.db"
    with DurableAssuranceStore(direct_path) as direct:
        direct.register_work_order(order)
        with pytest.raises(AssuranceError):
            direct.append_event(invalid)
        assert direct.event_count(order.work_order_id) == 0

    process_path = tmp_path / "process-invalid.db"
    client = AssuranceProcessClient(process_path)
    client.register_work_order(order)
    with pytest.raises(ProcessSurfaceError) as exc:
        client.append_event(invalid)
    assert exc.value.error_type == "AssuranceError"
    snapshot = client.snapshot(order.work_order_id)
    assert snapshot["authority_state"] == "proposed"
    assert snapshot["event_cids"] == []


def test_process_surface_duplicate_replay_is_idempotent(tmp_path):
    order = _order()
    event = _event(WorkEventKind.ARTIFACT_PRODUCED, actor="producer")
    client = AssuranceProcessClient(tmp_path / "process-duplicate.db")
    client.register_work_order(order)

    first = client.append_event(event)["receipt"]
    second = client.append_event(event)["receipt"]
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert first["event_cid"] == second["event_cid"]
    assert first["sequence"] == second["sequence"] == 1
    assert client.snapshot(order.work_order_id)["event_cids"] == [event.cid]


def test_process_surface_preserves_takeover_fencing(tmp_path):
    order = _order()
    client = AssuranceProcessClient(tmp_path / "process-fence.db")
    client.register_work_order(order)
    takeover = _event(
        WorkEventKind.LEASE_TAKEOVER,
        actor="lease-controller",
        epoch=1,
        fence="fence-1",
    )
    client.append_event(takeover)

    stale = _event(
        WorkEventKind.ARTIFACT_PRODUCED,
        actor="stale-worker",
        epoch=0,
        fence="fence-0",
    )
    with pytest.raises(ProcessSurfaceError) as exc:
        client.append_event(stale)
    assert exc.value.error_type == "AssuranceError"
    assert "stale epoch" in exc.value.message

    fresh = _event(
        WorkEventKind.ARTIFACT_PRODUCED,
        actor="fresh-worker",
        epoch=1,
        fence="fence-1",
    )
    client.append_event(fresh)
    snapshot = client.snapshot(order.work_order_id)
    assert snapshot["current_epoch"] == 1
    assert snapshot["current_fence_token"] == "fence-1"
    assert snapshot["producer_actor_id"] == "fresh-worker"
