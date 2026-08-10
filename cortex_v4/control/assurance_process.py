"""Minimal JSON process boundary for the V4 assurance contract.

This module is not a plugin framework. It is a deliberately small subprocess
surface used to prove that another invocation boundary cannot bypass the same
``AssuranceWorkOrder`` / ``WorkEvent`` / durable-store rules as direct Python
calls.

Protocol: one JSON request on stdin, one JSON response on stdout.
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


def work_order_to_dict(work_order: AssuranceWorkOrder) -> dict[str, Any]:
    return asdict(work_order)


def work_order_from_dict(payload: Mapping[str, Any]) -> AssuranceWorkOrder:
    return AssuranceWorkOrder(
        work_order_id=str(payload["work_order_id"]),
        artifact_id=str(payload["artifact_id"]),
        artifact_version=str(payload["artifact_version"]),
        mutating=bool(payload["mutating"]),
        initial_epoch=int(payload.get("initial_epoch", 0)),
        initial_fence_token=str(payload.get("initial_fence_token", "fence-0")),
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
    return WorkEvent(
        work_order_id=str(payload["work_order_id"]),
        kind=WorkEventKind(str(payload["kind"])),
        actor_id=str(payload["actor_id"]),
        artifact_id=str(payload["artifact_id"]),
        artifact_version=str(payload["artifact_version"]),
        epoch=int(payload["epoch"]),
        fence_token=str(payload["fence_token"]),
        parent_event_cids=tuple(str(value) for value in payload.get("parent_event_cids", ())),
        evidence_refs=tuple(str(value) for value in payload.get("evidence_refs", ())),
        decision=(str(payload["decision"]) if payload.get("decision") is not None else None),
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
    operation = request.get("operation")
    with DurableAssuranceStore(db_path) as store:
        if operation == "register_work_order":
            work_order = work_order_from_dict(request["work_order"])
            store.register_work_order(work_order)
            return {"ok": True, "work_order_id": work_order.work_order_id}

        if operation == "append_event":
            event = event_from_dict(request["event"])
            receipt, snapshot = store.append_event(event)
            return {
                "ok": True,
                "receipt": receipt_to_dict(receipt),
                "snapshot": snapshot_to_dict(snapshot),
            }

        if operation == "snapshot":
            work_order_id = str(request["work_order_id"])
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
            raise ValueError("request must be a JSON object")
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
