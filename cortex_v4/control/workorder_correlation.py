"""Versioned correlation adapter for fossil trusted-local WorkOrders.

This module preserves execution identity crossing the Fossil broker/Cortex boundary.
It deliberately does not import GitHub queue state, select executors/models, or grant
access. Cortex remains the execution-policy/recovery owner; these fields are opaque
correlation facts once an upstream trusted broker has authorized the WorkOrder.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from .workorder_recovery import WorkOrder, WorkOrderContractError


CORRELATION_VERSION = "fossil-trusted-local-correlation-v1"
BROKER_WORKORDER_VERSION = "trusted-local-workorder-v1"
_SHA = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class BrokerCorrelation:
    project_issue_id: int
    work_order_id: str
    task_id: str
    attempt_id: str
    generation: int
    repo: str
    starting_ref: str
    role: str
    access_class: str
    version: str = CORRELATION_VERSION

    def validate_against(self, order: WorkOrder) -> None:
        if self.version != CORRELATION_VERSION:
            raise WorkOrderContractError("unsupported broker correlation version")
        if not isinstance(self.project_issue_id, int) or isinstance(self.project_issue_id, bool) or self.project_issue_id <= 0:
            raise WorkOrderContractError("project_issue_id must be a positive integer")
        if not isinstance(self.generation, int) or isinstance(self.generation, bool) or self.generation < 0:
            raise WorkOrderContractError("generation must be a non-negative integer")
        for field in ("work_order_id", "task_id", "attempt_id", "repo", "starting_ref", "role", "access_class"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value:
                raise WorkOrderContractError(f"{field} must be a non-empty string")
        if not _SHA.fullmatch(self.starting_ref):
            raise WorkOrderContractError("starting_ref must be an exact 40-character lowercase SHA")
        if self.work_order_id != order.work_order_id:
            raise WorkOrderContractError("broker work_order_id conflicts with Cortex WorkOrder")
        if self.task_id != order.task_id:
            raise WorkOrderContractError("broker task_id conflicts with Cortex WorkOrder")
        if self.starting_ref != order.base_sha:
            raise WorkOrderContractError("broker starting_ref must equal Cortex base_sha")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "BrokerCorrelation":
        if not isinstance(raw, Mapping):
            raise WorkOrderContractError("execution correlation must be an object")
        required = (
            "project_issue_id", "work_order_id", "task_id", "attempt_id", "generation",
            "repo", "starting_ref", "role", "access_class",
        )
        missing = [field for field in required if field not in raw]
        if missing:
            raise WorkOrderContractError(f"execution correlation missing fields: {', '.join(missing)}")
        return cls(
            project_issue_id=raw["project_issue_id"],
            work_order_id=raw["work_order_id"],
            task_id=raw["task_id"],
            attempt_id=raw["attempt_id"],
            generation=raw["generation"],
            repo=raw["repo"],
            starting_ref=raw["starting_ref"],
            role=raw["role"],
            access_class=raw["access_class"],
            version=raw.get("version", CORRELATION_VERSION),
        )


def correlation_from_broker(raw: Mapping[str, Any], order: WorkOrder) -> BrokerCorrelation:
    """Map a validated broker WorkOrder envelope into Cortex correlation facts.

    The adapter verifies identity equality but does not reproduce the broker's trust,
    repo, role, or access authorization policy. It also does not infer or overwrite
    Cortex's idempotency key, deadlines, acceptance test, or mutation destination.
    """
    if not isinstance(raw, Mapping):
        raise WorkOrderContractError("broker WorkOrder must be an object")
    if raw.get("version") != BROKER_WORKORDER_VERSION:
        raise WorkOrderContractError("unsupported trusted-local WorkOrder version")
    correlation = BrokerCorrelation.from_dict(raw)
    correlation.validate_against(order)
    return correlation
