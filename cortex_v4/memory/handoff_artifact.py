"""Structured handoff artifacts: task/phase/owner (V4 independent)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

REQUIRED_KEYS = frozenset({"task", "phase", "owner"})


@dataclass(frozen=True)
class HandoffArtifact:
    task: str
    phase: str
    owner: str
    acceptance_criteria: tuple[str, ...] = field(default_factory=tuple)
    evidence: tuple[str, ...] = field(default_factory=tuple)
    status: str = "pending"
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def as_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "phase": self.phase,
            "owner": self.owner,
            "acceptance_criteria": list(self.acceptance_criteria),
            "evidence": list(self.evidence),
            "status": self.status,
            "created_at": self.created_at,
        }


def build_handoff(
    task: str,
    phase: str,
    owner: str,
    *,
    acceptance_criteria: list[str] | None = None,
    evidence: list[str] | None = None,
    status: str = "pending",
) -> dict[str, Any]:
    payload = {
        "task": task,
        "phase": phase,
        "owner": owner,
        "acceptance_criteria": list(acceptance_criteria or []),
        "evidence": list(evidence or []),
        "status": status,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    validate_handoff(payload)
    return payload


def validate_handoff(payload: Mapping[str, Any]) -> None:
    missing = REQUIRED_KEYS.difference(payload.keys())
    if missing:
        raise ValueError(f"handoff payload missing required keys: {sorted(missing)}")
    if not str(payload["task"]).strip():
        raise ValueError("handoff task must not be empty")
    if not str(payload["phase"]).strip():
        raise ValueError("handoff phase must not be empty")
    if not str(payload["owner"]).strip():
        raise ValueError("handoff owner must not be empty")


def from_dict(payload: Mapping[str, Any]) -> HandoffArtifact:
    validate_handoff(payload)
    return HandoffArtifact(
        task=str(payload["task"]).strip(),
        phase=str(payload["phase"]).strip(),
        owner=str(payload["owner"]).strip(),
        acceptance_criteria=tuple(
            str(x) for x in payload.get("acceptance_criteria", [])
        ),
        evidence=tuple(str(x) for x in payload.get("evidence", [])),
        status=str(payload.get("status", "pending")),
        created_at=str(
            payload.get("created_at") or datetime.now(timezone.utc).isoformat()
        ),
    )
