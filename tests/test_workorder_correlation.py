"""Cross-repo WorkOrder correlation contract for fossil-core #96 / Cortex #1."""

import pytest

from cortex_v4.control.workorder_correlation import BrokerCorrelation, correlation_from_broker
from cortex_v4.control.workorder_recovery import WorkOrderContractError, WorkOrderRecoveryHarness, fixture_work_order


STARTING_SHA = "0a2aad9fded9755053a21c769cc4546e15366319"


def broker_work_order(**overrides):
    value = {
        "version": "trusted-local-workorder-v1",
        "project_issue_id": 94,
        "work_order_id": "wo-cortex-05-attempt-1",
        "task_id": "CORTEX-05",
        "attempt_id": "cortex-05-attempt-1",
        "generation": 3,
        "repo": "Pukujan/cortex-v4",
        "starting_ref": STARTING_SHA,
        "role": "luna",
        "access_class": "CLOUD_SECRETLESS",
    }
    value.update(overrides)
    return value


def cortex_order(**overrides):
    order = fixture_work_order()
    values = {
        **order.__dict__,
        "work_order_id": "wo-cortex-05-attempt-1",
        "task_id": "CORTEX-05",
        "base_sha": STARTING_SHA,
        "idempotency_key": "cortex-05-attempt-1",
        **overrides,
    }
    return order.__class__(**values)


def test_broker_correlation_preserves_execution_identity_without_owning_policy():
    correlation = correlation_from_broker(broker_work_order(), cortex_order())

    assert correlation.version == "fossil-trusted-local-correlation-v1"
    assert correlation.project_issue_id == 94
    assert correlation.work_order_id == "wo-cortex-05-attempt-1"
    assert correlation.task_id == "CORTEX-05"
    assert correlation.attempt_id == "cortex-05-attempt-1"
    assert correlation.generation == 3
    assert correlation.repo == "Pukujan/cortex-v4"
    assert correlation.starting_ref == STARTING_SHA
    assert correlation.role == "luna"
    assert correlation.access_class == "CLOUD_SECRETLESS"


def test_starting_ref_must_equal_cortex_base_sha():
    with pytest.raises(WorkOrderContractError, match="starting_ref.*base_sha"):
        correlation_from_broker(broker_work_order(starting_ref="1" * 40), cortex_order())


def test_conflicting_work_order_or_task_identity_is_rejected():
    with pytest.raises(WorkOrderContractError, match="work_order_id"):
        correlation_from_broker(broker_work_order(work_order_id="wo-other"), cortex_order())
    with pytest.raises(WorkOrderContractError, match="task_id"):
        correlation_from_broker(broker_work_order(task_id="OTHER"), cortex_order())


def test_malformed_attempt_generation_and_sha_are_rejected():
    with pytest.raises(WorkOrderContractError, match="attempt_id"):
        correlation_from_broker(broker_work_order(attempt_id=""), cortex_order())
    with pytest.raises(WorkOrderContractError, match="generation"):
        correlation_from_broker(broker_work_order(generation=-1), cortex_order())
    with pytest.raises(WorkOrderContractError, match="40-character"):
        correlation_from_broker(broker_work_order(starting_ref="not-a-sha"), cortex_order(base_sha="not-a-sha"))


def test_correlation_round_trips_through_durable_recovery_ledger(tmp_path):
    order = cortex_order()
    correlation = correlation_from_broker(broker_work_order(), order)
    path = tmp_path / "durable" / "ledger.json"

    first = WorkOrderRecoveryHarness(path)
    first.register(order, correlation=correlation)

    recovered = WorkOrderRecoveryHarness(path)
    assert recovered.execution_correlation() == correlation
    assert isinstance(recovered.execution_correlation(), BrokerCorrelation)
