"""Mechanical model selection from LiteLLM/CKFF capability facts.

LiteLLM is inventory + telemetry. It does not choose the worker model here.
V4 owns seating and cross-family selection. The router is deterministic and has
no SSC dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass(frozen=True)
class CatalogModel:
    model_id: str
    family: str
    available: bool = True
    tool_calling: bool = True
    coding: bool = True
    cost_rank: int = 100
    quality_rank: int = 100
    latency_rank: int = 100
    reliability: float = 0.0
    route_id: str = ""
    provider_deadline_s: float | None = None
    observed_longest_success_s: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict, compare=False)

    @classmethod
    def from_mapping(cls, row: dict[str, Any]) -> "CatalogModel":
        observed = row.get("observed") if isinstance(row.get("observed"), dict) else {}
        timeout = row.get("timeouts") if isinstance(row.get("timeouts"), dict) else {}
        capabilities = row.get("capabilities") if isinstance(row.get("capabilities"), dict) else {}
        return cls(
            model_id=str(row.get("id") or row.get("model_id") or row.get("model_name") or ""),
            family=str(row.get("family") or "unknown"),
            available=bool(row.get("available", True)),
            tool_calling=bool(capabilities.get("tool_calling", row.get("tool_calling", True))),
            coding=bool(capabilities.get("coding", row.get("coding", True))),
            cost_rank=int(row.get("cost_rank", 100)),
            quality_rank=int(row.get("quality_rank", 100)),
            latency_rank=int(row.get("latency_rank", 100)),
            reliability=float(observed.get("success_rate", row.get("reliability", 0.0)) or 0.0),
            route_id=str(row.get("route_id") or ""),
            provider_deadline_s=_number(timeout.get("provider_deadline_s", row.get("provider_deadline_s"))),
            observed_longest_success_s=_number(
                observed.get("longest_success_s", row.get("observed_longest_success_s"))
            ),
            metadata=dict(row),
        )


@dataclass(frozen=True)
class RoutingRequest:
    role: str
    task_kind: str = "coding"
    seats: int = 1
    require_tools: bool = True
    require_cross_family: bool = False
    desired_task_seconds: float | None = None
    excluded_models: frozenset[str] = frozenset()


@dataclass(frozen=True)
class RoutingPolicy:
    """Weights are ordered preferences, not provider defaults."""

    max_cost_rank: int = 100
    min_reliability: float = 0.0
    prefer_cost: int = 4
    prefer_reliability: int = 4
    prefer_quality: int = 2
    prefer_latency: int = 1


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _task_budget_supported(model: CatalogModel, desired: float | None) -> bool:
    """Do not reject durable sessions just because one request has a shorter limit.

    For a single model turn, the route must have evidence that a request of the desired
    duration is plausible. For long-running OpenCode sessions, callers should leave
    desired_task_seconds unset because the task is multiple bounded model turns.
    """
    if desired is None:
        return True
    evidence = model.observed_longest_success_s or model.provider_deadline_s
    return evidence is None or evidence >= desired


def _eligible(model: CatalogModel, request: RoutingRequest, policy: RoutingPolicy) -> bool:
    if not model.model_id or not model.available:
        return False
    if model.model_id in request.excluded_models:
        return False
    if request.require_tools and not model.tool_calling:
        return False
    if request.task_kind == "coding" and not model.coding:
        return False
    if model.cost_rank > policy.max_cost_rank:
        return False
    if model.reliability < policy.min_reliability:
        return False
    return _task_budget_supported(model, request.desired_task_seconds)


def _score(model: CatalogModel, policy: RoutingPolicy) -> tuple[float, str]:
    score = (
        policy.prefer_cost * model.cost_rank
        + policy.prefer_quality * model.quality_rank
        + policy.prefer_latency * model.latency_rank
        - policy.prefer_reliability * model.reliability * 100.0
    )
    # model_id is a deterministic tie breaker, never a preference/default.
    return (score, model.model_id)


def select_models(
    catalog: Iterable[CatalogModel | dict[str, Any]],
    request: RoutingRequest,
    policy: RoutingPolicy | None = None,
) -> list[CatalogModel]:
    """Return a deterministic V4 seating choice from external capability facts.

    When cross-family is requested, the first seat is the strongest overall candidate
    and later seats come from different families when possible. No model ID is hard-coded.
    """
    policy = policy or RoutingPolicy()
    rows = [row if isinstance(row, CatalogModel) else CatalogModel.from_mapping(row) for row in catalog]
    candidates = sorted(
        (row for row in rows if _eligible(row, request, policy)),
        key=lambda row: _score(row, policy),
    )
    if not candidates:
        return []

    selected: list[CatalogModel] = []
    used_families: set[str] = set()
    for candidate in candidates:
        if len(selected) >= max(1, int(request.seats)):
            break
        if request.require_cross_family and selected and candidate.family in used_families:
            continue
        selected.append(candidate)
        used_families.add(candidate.family)

    if len(selected) < max(1, int(request.seats)) and not request.require_cross_family:
        return selected

    return selected
