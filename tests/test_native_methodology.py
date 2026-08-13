from __future__ import annotations

import pytest

from cortex_v4.control.native_methodology import MethodologyPreflightError, NativeV4Methodology
from cortex_v4.control.task_contract import TaskContract


def contract(**route):
    return TaskContract.freeze({
        "objective_id": "methodology-objective",
        "exact_base_sha": "d" * 40,
        "task_class": "coding",
        "contract_revision": "method-v1",
        "generation_fence": "method:fence",
        "dependency_dag": ["implementation", "verify"],
        "acceptance_checks": ["objective"],
        "types_interfaces_schemas": ["contract-v1"],
        "route_policy": {"model": "model-a", "endpoint": "chat", "capability": "chat", **route},
        "stage_specs": [
            {"stage_id": "implementation", "assigned_role": "implementation_worker", "acceptance_checks": ["impl"], "stage_deadline_s": 20, "kind": "implementation"},
            {"stage_id": "verify", "assigned_role": "orchestrator", "depends_on": ["implementation"], "acceptance_checks": ["verify"], "stage_deadline_s": 20, "kind": "closeout"},
        ],
    })


def test_preflight_freezes_classification_dispatch_and_fence():
    methodology = NativeV4Methodology.preflight(contract())
    plan = methodology.plan()
    assert plan.task_class == "coding"
    assert plan.contract_revision == "method-v1"
    assert [item.role for item in plan.dispatch] == ["implementation_worker", "orchestrator"]
    assert all(item.checkpoint_required for item in plan.dispatch)
    assert plan.preflight_checks["dependency_dag_validated"] is True
    assert NativeV4Methodology.plan_hash(plan)


@pytest.mark.parametrize("capability", ["search", "embedding", "rerank", "image"])
def test_coding_preflight_rejects_non_chat_capabilities(capability):
    with pytest.raises(MethodologyPreflightError):
        NativeV4Methodology.preflight(contract(capability=capability))


def test_stage_dispatch_can_override_model_by_role():
    methodology = NativeV4Methodology.preflight(contract(model_by_role={"implementation_worker": "coder-a", "orchestrator": "checker-b"}))
    plan = methodology.plan()
    assert [item.requested_model for item in plan.dispatch] == ["coder-a", "checker-b"]
