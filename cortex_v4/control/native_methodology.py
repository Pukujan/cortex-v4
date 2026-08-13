"""The executable SSC-free V4 methodology preflight.

This is deliberately a small control object, not a second memory system.  It
freezes and checks the contract, records how each seat is dispatched, and
requires every stage to produce mechanical evidence before the runner can
advance.  It never treats a model response as canonical methodology state.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from .task_contract import StageSpec, TaskContract, TaskContractError


class MethodologyPreflightError(ValueError):
    """The task cannot enter native V4 execution."""


@dataclass(frozen=True)
class DispatchDecision:
    stage_id: str
    role: str
    kind: str
    requested_model: str
    endpoint: str
    capability: str
    checkpoint_required: bool
    generation_fence: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage_id": self.stage_id,
            "role": self.role,
            "kind": self.kind,
            "requested_model": self.requested_model,
            "endpoint": self.endpoint,
            "capability": self.capability,
            "checkpoint_required": self.checkpoint_required,
            "generation_fence": self.generation_fence,
        }


@dataclass(frozen=True)
class MethodologyPlan:
    objective_id: str
    task_class: str
    contract_revision: str
    contract_hash: str
    exact_base_sha: str
    dispatch: tuple[DispatchDecision, ...]
    preflight_checks: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "cortex.v4.methodology-plan.v1",
            "objective_id": self.objective_id,
            "task_class": self.task_class,
            "contract_revision": self.contract_revision,
            "contract_hash": self.contract_hash,
            "exact_base_sha": self.exact_base_sha,
            "dispatch": [decision.as_dict() for decision in self.dispatch],
            "preflight_checks": dict(self.preflight_checks),
        }


class NativeV4Methodology:
    """Freeze a real contract and derive auditable per-stage dispatch."""

    def __init__(self, contract: TaskContract):
        self.contract = contract

    @classmethod
    def preflight(cls, value: TaskContract | Mapping[str, Any]) -> "NativeV4Methodology":
        try:
            contract = value if isinstance(value, TaskContract) else TaskContract.freeze(value)
        except TaskContractError as exc:
            raise MethodologyPreflightError(str(exc)) from exc
        if contract.task_class not in {"coding", "research", "evaluation", "operations", "generic"}:
            raise MethodologyPreflightError("unsupported task classification")
        if not contract.stages:
            raise MethodologyPreflightError("at least one executable stage is required")
        # The DAG is authoritative for order; StageSpec declaration order may
        # differ, but every node must be represented exactly once.
        stage_ids = {stage.stage_id for stage in contract.stages}
        if set(contract.dependency_dag) != stage_ids:
            raise MethodologyPreflightError("stage DAG does not cover the frozen contract")
        route_policy = dict(contract.route_policy)
        capability = str(route_policy.get("capability", "chat"))
        endpoint = str(route_policy.get("endpoint", "chat"))
        if contract.task_class == "coding" and capability != "chat":
            raise MethodologyPreflightError("native coding stages are chat-capability only")
        if endpoint not in {"chat", "responses"}:
            raise MethodologyPreflightError("native V4 endpoint must be chat or responses")
        if any(token in capability.lower() for token in {"search", "embedding", "rerank", "image"}):
            raise MethodologyPreflightError("search, embedding, rerank, and image lanes are not native coding capabilities")
        if not str(contract.generation_fence).strip():
            raise MethodologyPreflightError("generation fencing identity is required")
        if any(not stage.acceptance_checks for stage in contract.stages):
            raise MethodologyPreflightError("every stage needs an independent acceptance check")
        return cls(contract)

    def plan(self) -> MethodologyPlan:
        route = dict(self.contract.route_policy)
        requested_default = str(route.get("model", ""))
        model_by_role = route.get("model_by_role", {})
        if model_by_role is None:
            model_by_role = {}
        if not isinstance(model_by_role, Mapping):
            raise MethodologyPreflightError("route_policy.model_by_role must be an object")
        endpoint = str(route.get("endpoint", "chat"))
        capability = str(route.get("capability", "chat"))
        decisions: list[DispatchDecision] = []
        for stage_id in self.contract.dependency_dag:
            stage = self.contract.stage(stage_id)
            requested_model = str(model_by_role.get(stage.assigned_role, requested_default))
            if not requested_model:
                raise MethodologyPreflightError(f"no requested model dispatch for stage {stage_id}")
            decisions.append(DispatchDecision(
                stage_id=stage.stage_id,
                role=stage.assigned_role,
                kind=stage.kind,
                requested_model=requested_model,
                endpoint=endpoint,
                capability=capability,
                checkpoint_required=True,
                generation_fence=f"{self.contract.generation_fence}:{stage.stage_id}",
            ))
        checks = {
            "contract_frozen": True,
            "exact_base_sha": bool(self.contract.exact_base_sha),
            "task_classified": bool(self.contract.task_class),
            "dependency_dag_validated": True,
            "types_interfaces_schemas_present": bool(self.contract.types_interfaces_schemas),
            "stage_acceptance_checks_present": all(bool(stage.acceptance_checks) for stage in self.contract.stages),
            "generation_fencing_present": True,
            "checkpoint_required": True,
        }
        if not all(checks.values()):
            raise MethodologyPreflightError("methodology preflight did not pass")
        return MethodologyPlan(
            objective_id=self.contract.objective_id,
            task_class=self.contract.task_class,
            contract_revision=self.contract.contract_revision,
            contract_hash=self.contract.contract_hash,
            exact_base_sha=self.contract.exact_base_sha,
            dispatch=tuple(decisions),
            preflight_checks=checks,
        )

    @staticmethod
    def plan_hash(plan: MethodologyPlan) -> str:
        return hashlib.sha256(json.dumps(plan.as_dict(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
