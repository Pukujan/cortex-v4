"""Fold working detail into typed decision/failure/follow_up pointers (V4 independent)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .context_controller import ContextController
from .event_log import Event, EventLog
from .pointers import Pointer, make_pointer

FOLD_KINDS = frozenset({"decision", "failure", "follow_up", "evidence", "note"})


@dataclass(frozen=True)
class FoldResult:
    pointer: Pointer
    event: Event
    kind: str
    summary: str


def fold(
    controller: ContextController,
    *,
    kind: str,
    key: str,
    summary: str,
    detail: Mapping[str, Any] | None = None,
    log: EventLog | None = None,
) -> FoldResult:
    if kind not in FOLD_KINDS:
        raise ValueError(
            f"unknown fold kind {kind!r}; expected one of {sorted(FOLD_KINDS)}"
        )
    if not key or not str(key).strip():
        raise ValueError("fold key must be non-empty")
    if not summary or not str(summary).strip():
        raise ValueError("fold summary must be non-empty")

    namespace = "follow_up" if kind == "follow_up" else kind
    pointer = make_pointer(namespace, str(key).strip(), label=summary.strip())
    payload = {
        "pointer": str(pointer),
        "kind": kind,
        "summary": summary.strip(),
        "detail": dict(detail or {}),
    }
    event_log = log if log is not None else controller.event_log
    event = event_log.append("fold", payload)
    if kind == "decision":
        controller.add_text(f"{pointer} {summary.strip()}", protected=True)
    else:
        controller.add_pointer(pointer, label=summary.strip())
    return FoldResult(
        pointer=pointer, event=event, kind=kind, summary=summary.strip()
    )


def fold_decision(
    controller: ContextController,
    *,
    key: str,
    summary: str,
    detail: Mapping[str, Any] | None = None,
) -> FoldResult:
    return fold(controller, kind="decision", key=key, summary=summary, detail=detail)


def fold_failure(
    controller: ContextController,
    *,
    key: str,
    summary: str,
    detail: Mapping[str, Any] | None = None,
) -> FoldResult:
    return fold(controller, kind="failure", key=key, summary=summary, detail=detail)


def fold_follow_up(
    controller: ContextController,
    *,
    key: str,
    summary: str,
    detail: Mapping[str, Any] | None = None,
) -> FoldResult:
    return fold(controller, kind="follow_up", key=key, summary=summary, detail=detail)
