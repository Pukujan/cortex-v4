"""Cross-repo WorkOrder correlation contract for fossil-core #94 / Cortex."""

import pytest

from cortex_v4.control.workorder_correlation import BrokerCorrelation, correlation_from_broker
from cortex_v4.control.workorder_recovery import WorkOrderContractError, WorkOrderRecoveryHarness, fixture_work_order


STARTING_SHA = "ebca9d49f258cbeb14b5b28c4d70779a4c04a30c"


def broker_work_order(**overrides):
    value = {
        "version": "trusted-local-workorder-v1",
        "project_issue_id": 94,
        "work_order_id": "wo-cortex-07-attempt-1",
        "task_id": "CORTEX-07",
        "attempt_id": "cortex-07-attempt-1",
        "generation": 4,
        "repo": "Pukujan/cortex-v4",
        "starting_ref": STARTING_SHA,
        "role": "chatgpt",
        "access_class": "CLOUD_SECRETLESS",
        "extra_broker_fact": "inert",
    }
    value.update(overrides)
    return value


def cortex_order(**overrides):
    order = fixture_work_order()
    values = {
        **order.__dict__,
        "work_order_id": "wo-cortex-07-attempt-1",
        "task_id": "CORTEX-07",
        "base_sha": STARTING_SHA,
        "idempotency_key": "cortex-07-attempt-1",
        **overrides,
    }
    return order.__class__(**values)


def test_broker_correlation_preserves_execution_identity_without_owning_policy():
    correlation = correlation_from_broker(broker_work_order(), cortex_order())

    assert correlation.version == "fossil-trusted-local-correlation-v1"
    assert correlation.project_issue_id == 94
    assert correlation.work_order_id == "wo-cortex-07-attempt-1"
    assert correlation.task_id == "CORTEX-07"
    assert correlation.attempt_id == "cortex-07-attempt-1"
    assert correlation.generation == 4
    assert correlation.repo == "Pukujan/cortex-v4"
    assert correlation.starting_ref == STARTING_SHA
    assert correlation.role == "chatgpt"
    assert correlation.access_class == "CLOUD_SECRETLESS"
    assert "extra_broker_fact" not in correlation.to_dict()


def test_starting_ref_must_equal_cortex_base_sha():
    with pytest.raises(WorkOrderContractError, match="starting_ref.*base_sha"):
        correlation_from_broker(broker_work_order(starting_ref="1" * 40), cortex_order())


def test_conflicting_work_order_or_task_identity_is_rejected():
    with pytest.raises(WorkOrderContractError, match="work_order_id"):
        correlation_from_broker(broker_work_order(work_order_id="wo-other"), cortex_order())
    with pytest.raises(WorkOrderContractError, match="task_id"):
        correlation_from_broker(broker_work_order(task_id="OTHER"), cortex_order())


def test_malformed_attempt_generation_sha_and_version_are_rejected():
    with pytest.raises(WorkOrderContractError, match="attempt_id"):
        correlation_from_broker(broker_work_order(attempt_id=""), cortex_order())
    with pytest.raises(WorkOrderContractError, match="generation"):
        correlation_from_broker(broker_work_order(generation=-1), cortex_order())
    with pytest.raises(WorkOrderContractError, match="40-character"):
        correlation_from_broker(broker_work_order(starting_ref="not-a-sha"), cortex_order(base_sha="not-a-sha"))
    with pytest.raises(WorkOrderContractError, match="unsupported trusted-local WorkOrder version"):
        correlation_from_broker(broker_work_order(version="other"), cortex_order())


def test_correlation_round_trips_through_durable_recovery_ledger(tmp_path):
    order = cortex_order()
    correlation = correlation_from_broker(broker_work_order(), order)
    path = tmp_path / "durable" / "ledger.json"

    first = WorkOrderRecoveryHarness(path)
    first.register(order, correlation=correlation)

    recovered = WorkOrderRecoveryHarness(path)
    assert recovered.execution_correlation() == correlation
    assert isinstance(recovered.execution_correlation(), BrokerCorrelation)


def test_existing_workorder_registration_without_correlation_is_unchanged(tmp_path):
    order = cortex_order()
    harness = WorkOrderRecoveryHarness(tmp_path / "legacy" / "ledger.json")
    harness.register(order)
    harness.register(order)
    assert harness.execution_correlation() is None
