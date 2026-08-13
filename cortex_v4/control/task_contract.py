"""Frozen, contract-first task and stage definitions for the native V4 runner."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping


class TaskContractError(ValueError):
    """The task cannot be frozen as a safe executable contract."""


_ROLES = {"orchestrator", "implementation_worker", "test_author", "critic", "holdout"}
_KINDS = {"contract", "implementation", "test", "critique", "adjudication", "closeout", "generic"}
_MEMORY_CLASSES = {"shared", "contract", "checkpoint", "holdout", "private"}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _required(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TaskContractError(f"{name} is required")
    return value.strip()


@dataclass(frozen=True)
class StageSpec:
    stage_id: str
    assigned_role: str
    depends_on: tuple[str, ...]
    allowed_read_refs: tuple[str, ...]
    allowed_write_set: tuple[str, ...]
    acceptance_checks: tuple[str, ...]
    stage_deadline_s: float
    kind: str = "generic"
    requirements: tuple[str, ...] = ()
    requires_critique: bool = False
    blind_until_convergence: bool = False
    result_visible: bool = True

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "StageSpec":
        stage_id = _required(value.get("stage_id"), "stage_id")
        if "/" in stage_id or "\\" in stage_id or stage_id in {".", ".."}:
            raise TaskContractError("stage_id must be a safe component")
        role = _required(value.get("assigned_role"), f"{stage_id}.assigned_role")
        if role not in _ROLES:
            raise TaskContractError(f"unsupported stage role: {role}")
        kind = str(value.get("kind", "generic"))
        if kind not in _KINDS:
            raise TaskContractError(f"unsupported stage kind: {kind}")
        checks = tuple(_required(item, f"{stage_id}.acceptance_checks") for item in value.get("acceptance_checks", ()))
        if not checks:
            raise TaskContractError(f"{stage_id} requires an independent acceptance check")
        deadline = float(value.get("stage_deadline_s", 0))
        if deadline <= 0:
            raise TaskContractError(f"{stage_id}.stage_deadline_s must be positive")
        return cls(
            stage_id=stage_id,
            assigned_role=role,
            depends_on=tuple(_required(item, f"{stage_id}.depends_on") for item in value.get("depends_on", ())),
            allowed_read_refs=tuple(str(item).replace("\\", "/") for item in value.get("allowed_read_refs", ())),
            allowed_write_set=tuple(str(item).replace("\\", "/") for item in value.get("allowed_write_set", ())),
            acceptance_checks=checks,
            stage_deadline_s=deadline,
            kind=kind,
            requirements=tuple(_required(item, f"{stage_id}.requirements") for item in value.get("requirements", ())),
            requires_critique=bool(value.get("requires_critique", False)),
            blind_until_convergence=bool(value.get("blind_until_convergence", False)),
            result_visible=bool(value.get("result_visible", True)),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage_id": self.stage_id,
            "assigned_role": self.assigned_role,
            "depends_on": list(self.depends_on),
            "allowed_read_refs": list(self.allowed_read_refs),
            "allowed_write_set": list(self.allowed_write_set),
            "acceptance_checks": list(self.acceptance_checks),
            "stage_deadline_s": self.stage_deadline_s,
            "kind": self.kind,
            "requirements": list(self.requirements),
            "requires_critique": self.requires_critique,
            "blind_until_convergence": self.blind_until_convergence,
            "result_visible": self.result_visible,
        }


@dataclass(frozen=True)
class TaskContract:
    objective_id: str
    exact_base_sha: str
    task_class: str
    contract_revision: str
    dependency_dag: tuple[str, ...]
    stages: tuple[StageSpec, ...]
    acceptance_checks: tuple[str, ...]
    generation_fence: str
    route_policy: Mapping[str, Any]
    types_interfaces_schemas: tuple[str, ...] = ()
    memory_class_by_ref: Mapping[str, str] = MappingProxyType({})
    requirements: tuple[str, ...] = ()

    @classmethod
    def freeze(cls, value: Mapping[str, Any]) -> "TaskContract":
        objective_id = _required(value.get("objective_id"), "objective_id")
        base_sha = _required(value.get("exact_base_sha"), "exact_base_sha")
        if not re.fullmatch(r"[0-9a-fA-F]{40}", base_sha):
            raise TaskContractError("exact_base_sha must be a 40-character hexadecimal SHA")
        stages = tuple(StageSpec.from_mapping(item) for item in value.get("stage_specs", ()))
        if not stages:
            raise TaskContractError("at least one stage is required")
        ids = [stage.stage_id for stage in stages]
        if len(ids) != len(set(ids)):
            raise TaskContractError("stage IDs must be unique")
        dag = tuple(str(item) for item in value.get("dependency_dag", ids))
        if set(dag) != set(ids) or len(dag) != len(ids):
            raise TaskContractError("dependency_dag must list every stage exactly once")
        position = {stage_id: index for index, stage_id in enumerate(dag)}
        by_id = {stage.stage_id: stage for stage in stages}
        for stage in stages:
            for dependency in stage.depends_on:
                if dependency not in by_id:
                    raise TaskContractError(f"{stage.stage_id} depends on unknown stage {dependency}")
                if position[dependency] >= position[stage.stage_id]:
                    raise TaskContractError("dependency_dag must be topologically ordered")
        acceptance = tuple(_required(item, "acceptance_checks") for item in value.get("acceptance_checks", ()))
        if not acceptance:
            raise TaskContractError("objective acceptance_checks are required")
        generation_fence = _required(value.get("generation_fence"), "generation_fence")
        memory_classes: dict[str, str] = {}
        raw_memory_classes = value.get("memory_class_by_ref") or {}
        if not isinstance(raw_memory_classes, Mapping):
            raise TaskContractError("memory_class_by_ref must be an object")
        for reference, memory_class in raw_memory_classes.items():
            reference = str(reference)
            memory_class = str(memory_class)
            if not reference or memory_class not in _MEMORY_CLASSES:
                raise TaskContractError("memory_class_by_ref contains an invalid entry")
            memory_classes[reference] = memory_class
        return cls(
            objective_id=objective_id,
            exact_base_sha=base_sha.lower(),
            task_class=_required(value.get("task_class"), "task_class"),
            contract_revision=_required(value.get("contract_revision"), "contract_revision"),
            dependency_dag=dag,
            stages=stages,
            acceptance_checks=acceptance,
            generation_fence=generation_fence,
            route_policy=MappingProxyType(dict(value.get("route_policy") or {})),
            types_interfaces_schemas=tuple(str(item) for item in value.get("types_interfaces_schemas", ())),
            memory_class_by_ref=MappingProxyType(memory_classes),
            requirements=tuple(_required(item, "requirements") for item in value.get("requirements", ())),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "objective_id": self.objective_id,
            "exact_base_sha": self.exact_base_sha,
            "task_class": self.task_class,
            "contract_revision": self.contract_revision,
            "dependency_dag": list(self.dependency_dag),
            "stage_specs": [stage.as_dict() for stage in self.stages],
            "acceptance_checks": list(self.acceptance_checks),
            "generation_fence": self.generation_fence,
            "route_policy": dict(self.route_policy),
            "types_interfaces_schemas": list(self.types_interfaces_schemas),
            "memory_class_by_ref": dict(self.memory_class_by_ref),
            "requirements": list(self.requirements),
        }

    @property
    def contract_hash(self) -> str:
        return _hash(self.as_dict())

    def stage(self, stage_id: str) -> StageSpec:
        for stage in self.stages:
            if stage.stage_id == stage_id:
                return stage
        raise TaskContractError(f"unknown stage: {stage_id}")
