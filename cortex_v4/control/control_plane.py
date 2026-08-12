"""V4-owned offline execution-policy contract; no SSC or provider runtime imports."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, Mapping


CONTROL_PLANE_VERSION = "cortex-v4-control-plane-v1"


class ControlPlaneError(ValueError):
    """A missing execution-policy invariant; never a model verdict."""


@dataclass(frozen=True)
class ExecutionPolicy:
    work_order_id: str
    task_id: str
    generation: int
    deadline_s: int
    allowed_routes: tuple[str, ...]
    required_checks: tuple[str, ...]
    version: str = CONTROL_PLANE_VERSION

    def validate(self) -> None:
        if self.version != CONTROL_PLANE_VERSION:
            raise ControlPlaneError("unsupported control-plane version")
        if not self.work_order_id or not self.task_id:
            raise ControlPlaneError("work_order_id and task_id are required")
        if self.generation < 0 or self.deadline_s <= 0:
            raise ControlPlaneError("generation must be non-negative and deadline positive")
        if not self.allowed_routes or not self.required_checks:
            raise ControlPlaneError("routes and independent checks are required")

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def classify_terminal_closeout(
    policy: ExecutionPolicy, *, checks: Mapping[str, bool], cancelled: bool = False, late_generation: bool = False
) -> dict[str, object]:
    """Return a mechanical terminal disposition independent of agent prose."""
    policy.validate()
    missing = [check for check in policy.required_checks if check not in checks]
    failed = [check for check in policy.required_checks if checks.get(check) is False]
    if cancelled:
        status, reason = "BLOCKED", "cancelled before mechanical acceptance"
    elif late_generation:
        status, reason = "BLOCKED", "late generation cannot win"
    elif missing:
        status, reason = "FAILED", f"missing independent checks: {', '.join(missing)}"
    elif failed:
        status, reason = "FAILED", f"failed independent checks: {', '.join(failed)}"
    else:
        status, reason = "PASS", "all required independent checks passed"
    return {"version": CONTROL_PLANE_VERSION, "work_order_id": policy.work_order_id, "task_id": policy.task_id,
            "generation": policy.generation, "terminal_status": status, "reason": reason,
            "required_checks": list(policy.required_checks)}


def validate_routes(routes: Iterable[str], *, allowlist: Iterable[str]) -> tuple[str, ...]:
    """Route identity is an explicit policy input, never inherited authority."""
    selected = tuple(routes)
    if not selected or any(route not in frozenset(allowlist) for route in selected):
        raise ControlPlaneError("selected route is outside the explicit allowlist")
    return selected
