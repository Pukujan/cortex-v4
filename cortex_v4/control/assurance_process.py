"""Minimal JSON process boundary for the V4 assurance contract.

This module is not a plugin framework. It is a deliberately small subprocess
surface used to prove that another invocation boundary cannot bypass the same
``AssuranceWorkOrder`` / ``WorkEvent`` / durable-store rules as direct Python
calls.

Protocol: one JSON request on stdin, one JSON response on stdout. Boundary
values are type-checked rather than coerced so serialization cannot silently
change work-order or event semantics.
"""
from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping

from cortex_v4.control.assurance import (
    AssuranceSnapshot,
    AssuranceWorkOrder,
    WorkEvent,
    WorkEventKind,
)
from cortex_v4.control.assurance_store import DurableAssuranceStore, StoredEventReceipt


class ProcessSurfaceError(RuntimeError):
    def __init__(self, error_type: str, message: str):
        super().__init__(f"{error_type}: {message}")
        self.error_type = error_type
        self.message = message


def _require_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be a JSON object")
    return value


def _require_str(payload: Mapping[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise TypeError(f"{field} must be a non-empty string")
    return value


def _require_bool(payload: Mapping[str, Any], field: str) -> bool:
    value = payload.get(field)
    if type(value) is not bool:
        raise TypeError(f"{field} must be a boolean")
    return value


def _require_int(payload: Mapping[str, Any], field: str, *, default: int | None = None) -> int:
    value = payload.get(field, default)
    if type(value) is not int:
        raise TypeError(f"{field} must be an integer")
    return value


def _require_str_list(payload: Mapping[str, Any], field: str) -> tuple[str, ...]:
    value = payload.get(field, [])
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise TypeError(f"{field} must be a JSON array of strings")
    return tuple(value)


def work_order_to_dict(work_order: AssuranceWorkOrder) -> dict[str, Any]:
    return asdict(work_order)


def work_order_from_dict(payload: Mapping[str, Any]) -> AssuranceWorkOrder:
    payload = _require_mapping(payload, "work_order")
    return AssuranceWorkOrder(
        work_order_id=_require_str(payload, "work_order_id"),
        artifact_id=_require_str(payload, "artifact_id"),
        artifact_version=_require_str(payload, "artifact_version"),
        mutating=_require_bool(payload, "mutating"),
        initial_epoch=_require_int(payload, "initial_epoch", default=0),
        initial_fence_token=(
            _require_str(payload, "initial_fence_token")
            if "initial_fence_token" in payload
            else "fence-0"
        ),
    )


def event_to_dict(event: WorkEvent) -> dict[str, Any]:
    return {
        "work_order_id": event.work_order_id,
        "kind": event.kind.value,
        "actor_id": event.actor_id,
        "artifact_id": event.artifact_id,
        "artifact_version": event.artifact_version,
        "epoch": event.epoch,
        "fence_token": event.fence_token,
        "parent_event_cids": list(event.parent_event_cids),
        "evidence_refs": list(event.evidence_refs),
        "decision": event.decision,
    }


def event_from_dict(payload: Mapping[str, Any]) -> WorkEvent:
    payload = _require_mapping(payload, "event")
    decision = payload.get("decision")
    if decision is not None and not isinstance(decision, str):
        raise TypeError("decision must be a string or null")
    return WorkEvent(
        work_order_id=_require_str(payload, "work_order_id"),
        kind=WorkEventKind(_require_str(payload, "kind")),
        actor_id=_require_str(payload, "actor_id"),
        artifact_id=_require_str(payload, "artifact_id"),
        artifact_version=_require_str(payload, "artifact_version"),
        epoch=_require_int(payload, "epoch"),
        fence_token=_require_str(payload, "fence_token"),
        parent_event_cids=_require_str_list(payload, "parent_event_cids"),
        evidence_refs=_require_str_list(payload, "evidence_refs"),
        decision=decision,
    )


def snapshot_to_dict(snapshot: AssuranceSnapshot) -> dict[str, Any]:
    return {
        "authority_state": snapshot.authority_state.value,
        "mutation_phase": snapshot.mutation_phase.value,
        "mutation_decision": snapshot.mutation_decision,
        "current_epoch": snapshot.current_epoch,
        "current_fence_token": snapshot.current_fence_token,
        "producer_actor_id": snapshot.producer_actor_id,
        "event_cids": list(snapshot.event_cids),
        "committed_success": snapshot.committed_success,
    }


def receipt_to_dict(receipt: StoredEventReceipt) -> dict[str, Any]:
    return asdict(receipt)


def handle_request(db_path: str | Path, request: Mapping[str, Any]) -> dict[str, Any]:
    request = _require_mapping(request, "request")
    operation = request.get("operation")
    if not isinstance(operation, str):
        raise TypeError("operation must be a string")

    with DurableAssuranceStore(db_path) as store:
        if operation == "register_work_order":
            work_order = work_order_from_dict(_require_mapping(request.get("work_order"), "work_order"))
            store.register_work_order(work_order)
            return {"ok": True, "work_order_id": work_order.work_order_id}

        if operation == "append_event":
            event = event_from_dict(_require_mapping(request.get("event"), "event"))
            receipt, snapshot = store.append_event(event)
            return {
                "ok": True,
                "receipt": receipt_to_dict(receipt),
                "snapshot": snapshot_to_dict(snapshot),
            }

        if operation == "snapshot":
            work_order_id = _require_str(request, "work_order_id")
            return {
                "ok": True,
                "snapshot": snapshot_to_dict(store.snapshot(work_order_id)),
            }

        raise ValueError(f"unsupported process operation: {operation!r}")


class AssuranceProcessClient:
    """One-process-per-call client used for matched surface conformance tests."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        python_executable: str = sys.executable,
        timeout_s: float = 10.0,
    ):
        self.db_path = str(db_path)
        self.python_executable = python_executable
        self.timeout_s = timeout_s

    def _call(self, request: Mapping[str, Any]) -> dict[str, Any]:
        completed = subprocess.run(
            [
                self.python_executable,
                "-m",
                "cortex_v4.control.assurance_process",
                self.db_path,
            ],
            input=json.dumps(request, sort_keys=True),
            text=True,
            capture_output=True,
            timeout=self.timeout_s,
            check=False,
        )
        try:
            response = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise ProcessSurfaceError(
                "InvalidProcessResponse",
                completed.stderr.strip() or "worker returned non-JSON output",
            ) from exc
        if not response.get("ok"):
            raise ProcessSurfaceError(
                str(response.get("error_type", "ProcessSurfaceError")),
                str(response.get("message", "unknown process error")),
            )
        return response

    def register_work_order(self, work_order: AssuranceWorkOrder) -> dict[str, Any]:
        return self._call(
            {
                "operation": "register_work_order",
                "work_order": work_order_to_dict(work_order),
            }
        )

    def append_event(self, event: WorkEvent) -> dict[str, Any]:
        return self._call(
            {
                "operation": "append_event",
                "event": event_to_dict(event),
            }
        )

    def snapshot(self, work_order_id: str) -> dict[str, Any]:
        return self._call(
            {
                "operation": "snapshot",
                "work_order_id": work_order_id,
            }
        )["snapshot"]


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error_type": "UsageError",
                    "message": "expected exactly one SQLite database path",
                }
            )
        )
        return 2

    try:
        request = json.loads(sys.stdin.read())
        if not isinstance(request, Mapping):
            raise TypeError("request must be a JSON object")
        response = handle_request(args[0], request)
        print(json.dumps(response, sort_keys=True, separators=(",", ":")))
        return 0
    except Exception as exc:  # noqa: BLE001 - protocol returns typed failure only
        print(
            json.dumps(
                {
                    "ok": False,
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 2


if __name__ == "__main__":  # pragma: no cover - exercised through subprocess tests
    raise SystemExit(main())
