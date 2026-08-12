"""Mechanical runner-death and replay evidence for Cortex #11."""

import pytest

from cortex_v4.control.workorder_recovery import (
    BOUNDARIES, Deadlines, WorkOrderContractError, WorkOrderRecoveryHarness, fixture_work_order,
)


def new_harness(tmp_path):
    harness = WorkOrderRecoveryHarness(tmp_path / "durable" / "ledger.json")
    harness.register(fixture_work_order())
    return harness


@pytest.mark.parametrize("boundary", sorted(BOUNDARIES))
def test_fresh_runner_recovers_after_death_at_each_boundary(tmp_path, boundary):
    first = new_harness(tmp_path)
    assert first.run_fixture_attempt(attempt_id="attempt-1", generation=0, death_at=boundary) is False
    # New object intentionally models a new Actions runner/process reading only durable state.
    recovered = WorkOrderRecoveryHarness(tmp_path / "durable" / "ledger.json")
    recovered.run_fixture_attempt(attempt_id="attempt-2", generation=1)
    assert recovered.terminal().status == "PASS"
    assert recovered.terminal().accepted_attempt_id in {"attempt-1", "attempt-2"}


def test_duplicate_replay_is_idempotent_and_late_generation_is_rejected(tmp_path):
    harness = new_harness(tmp_path)
    winning = harness.fixture_attempt(attempt_id="attempt-winning", generation=2)
    assert harness.checkpoint(winning) is True
    assert harness.checkpoint(winning) is False
    late = harness.fixture_attempt(attempt_id="attempt-late", generation=1)
    assert harness.checkpoint(late) is False
    assert harness.adjudicate() is True
    assert harness.terminal().accepted_attempt_id == "attempt-winning"


def test_conflicting_duplicate_and_false_success_are_refused(tmp_path):
    harness = new_harness(tmp_path)
    receipt = harness.fixture_attempt(attempt_id="attempt-1", generation=0)
    assert harness.checkpoint(receipt) is True
    with pytest.raises(WorkOrderContractError, match="conflicting"):
        harness.checkpoint(receipt.__class__(**{**receipt.__dict__, "trace_id": "different"}))
    with pytest.raises(WorkOrderContractError, match="PASS requires"):
        harness.checkpoint(receipt.__class__(**{**receipt.__dict__, "attempt_id": "attempt-bad", "tests_passed": False}))


def test_contract_rejects_unbounded_fanout_and_bad_deadline(tmp_path):
    with pytest.raises(WorkOrderContractError, match="bounded"):
        WorkOrderRecoveryHarness(tmp_path / "ledger.json", max_parallel=5)
    with pytest.raises(WorkOrderContractError, match="turn deadline"):
        Deadlines(whole_task_s=1, turn_s=2, provider_s=1, tool_s=1, queue_s=1).validate()


def test_flat_fanout_is_bounded_and_unique(tmp_path):
    harness = new_harness(tmp_path)
    assert harness.plan_flat_fanout(["research-a", "research-b"]) == ["research-a", "research-b"]
    with pytest.raises(WorkOrderContractError, match="unique"):
        harness.plan_flat_fanout(["same", "same"])
    with pytest.raises(WorkOrderContractError, match="within"):
        harness.plan_flat_fanout(["a", "b", "c", "d", "e"])
