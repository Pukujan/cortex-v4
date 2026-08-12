"""Secretless, deterministic WorkOrder recovery proof for Cortex issue #11.

This is deliberately an execution *contract* harness.  It calls no model, accepts no
credential, and makes no executor or persistent-service selection.  The JSON ledger is
the durable input a fresh runner needs to continue after a simulated process death.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal


CONTRACT_VERSION = "work-order-v1"
TERMINAL = {"PASS", "FAILED", "BLOCKED"}
BOUNDARIES = {
    "before_execution", "after_execution", "after_tests", "after_checkpoint", "before_terminal",
    "fan_in",
}


class WorkOrderContractError(ValueError):
    """A malformed receipt or unsafe replay was refused mechanically."""


@dataclass(frozen=True)
class Deadlines:
    whole_task_s: int
    turn_s: int
    provider_s: int
    tool_s: int
    queue_s: int

    def validate(self) -> None:
        if any(value <= 0 for value in asdict(self).values()):
            raise WorkOrderContractError("all deadline fields must be positive")
        if self.turn_s > self.whole_task_s:
            raise WorkOrderContractError("turn deadline exceeds whole task deadline")


@dataclass(frozen=True)
class WorkOrder:
    work_order_id: str
    task_id: str
    base_sha: str
    outcome: str
    acceptance_test: str
    idempotency_key: str
    mutation_destination: str
    deadlines: Deadlines
    version: str = CONTRACT_VERSION
    risk_receipt_ref: str = "fixture://risk/preflight-v1"

    def validate(self) -> None:
        if self.version != CONTRACT_VERSION:
            raise WorkOrderContractError("unsupported WorkOrder version")
        if not all((self.work_order_id, self.task_id, self.base_sha, self.outcome,
                    self.acceptance_test, self.idempotency_key, self.mutation_destination)):
            raise WorkOrderContractError("required WorkOrder identity fields are missing")
        self.deadlines.validate()


@dataclass(frozen=True)
class AttemptReceipt:
    attempt_id: str
    generation: int
    idempotency_key: str
    checkpoint_id: str
    status: Literal["PASS", "FAILED", "BLOCKED"]
    terminal_reason: str
    mutation_ref: str
    tests_passed: bool
    trace_id: str
    evidence_ref: str


@dataclass(frozen=True)
class TerminalReceipt:
    work_order_id: str
    status: Literal["PASS", "FAILED", "BLOCKED"]
    terminal_reason: str
    accepted_attempt_id: str | None
    checkpoint_id: str | None
    trace_id: str


def fixture_work_order() -> WorkOrder:
    return WorkOrder(
        work_order_id="wo-fixture-001", task_id="task-fixture-001", base_sha="fixture-base-sha",
        outcome="write a deterministic fixture marker", acceptance_test="fixture marker is present",
        idempotency_key="idem-fixture-001", mutation_destination="patch/wo-fixture-001",
        deadlines=Deadlines(whole_task_s=300, turn_s=60, provider_s=30, tool_s=20, queue_s=10),
    )


class WorkOrderRecoveryHarness:
    """A restartable ledger plus flat, bounded fixture-attempt reconciler."""

    def __init__(self, ledger_path: Path, *, max_parallel: int = 4):
        if not 1 <= max_parallel <= 4:
            raise WorkOrderContractError("fan-out must be flat and bounded to 1..4")
        self.ledger_path = Path(ledger_path)
        self.max_parallel = max_parallel

    def _load(self) -> dict:
        if not self.ledger_path.exists():
            return {"version": CONTRACT_VERSION, "attempts": [], "terminal": None, "events": []}
        return json.loads(self.ledger_path.read_text(encoding="utf-8"))

    def _save(self, ledger: dict) -> None:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.ledger_path.with_suffix(".tmp")
        temp.write_text(json.dumps(ledger, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        temp.replace(self.ledger_path)

    def _event(self, ledger: dict, kind: str, **fields: object) -> None:
        ledger["events"].append({"event_seq": len(ledger["events"]), "kind": kind, **fields})

    def register(self, order: WorkOrder, *, correlation: object | None = None) -> None:
        order.validate()
        ledger = self._load()
        existing = ledger.get("work_order")
        candidate = {**asdict(order), "deadlines": asdict(order.deadlines)}
        if correlation is not None:
            validate = getattr(correlation, "validate_against", None)
            to_dict = getattr(correlation, "to_dict", None)
            if not callable(validate) or not callable(to_dict):
                raise WorkOrderContractError("execution correlation does not implement the compatibility contract")
            validate(order)
            candidate["execution_correlation"] = to_dict()
        if existing and existing != candidate:
            raise WorkOrderContractError("ledger already belongs to a different WorkOrder")
        if not existing:
            ledger["work_order"] = candidate
            self._event(ledger, "work_order_registered", work_order_id=order.work_order_id)
            self._save(ledger)

    def execution_correlation(self):
        """Recover an optional versioned execution-correlation object after restart."""
        work_order = self._load().get("work_order") or {}
        raw = work_order.get("execution_correlation")
        if raw is None:
            return None
        from .workorder_correlation import BrokerCorrelation

        return BrokerCorrelation.from_dict(raw)

    def plan_flat_fanout(self, task_ids: list[str]) -> list[str]:
        """Return independent task IDs only when the flat matrix stays within the fixed cap."""
        if not task_ids or len(task_ids) > self.max_parallel or len(set(task_ids)) != len(task_ids):
            raise WorkOrderContractError("fan-out task IDs must be unique and within max_parallel")
        ledger = self._load()
        self._event(ledger, "flat_fanout_planned", task_ids=task_ids, max_parallel=self.max_parallel)
        self._save(ledger)
        return list(task_ids)

    def checkpoint(self, receipt: AttemptReceipt) -> bool:
        ledger = self._load()
        order = ledger.get("work_order")
        if not order:
            raise WorkOrderContractError("WorkOrder must be registered before checkpoint")
        if receipt.idempotency_key != order["idempotency_key"]:
            raise WorkOrderContractError("idempotency key mismatch")
        if receipt.status == "PASS" and (not receipt.tests_passed or not receipt.mutation_ref):
            raise WorkOrderContractError("PASS requires mutation and independent mechanical tests")
        for prior in ledger["attempts"]:
            if prior["attempt_id"] == receipt.attempt_id:
                if prior == asdict(receipt):
                    return False
                raise WorkOrderContractError("duplicate attempt ID has conflicting receipt")
        highest = max((item["generation"] for item in ledger["attempts"]), default=-1)
        if receipt.generation < highest:
            self._event(ledger, "late_generation_rejected", attempt_id=receipt.attempt_id,
                        generation=receipt.generation, highest_generation=highest)
            self._save(ledger)
            return False
        ledger["attempts"].append(asdict(receipt))
        self._event(ledger, "checkpoint_written", attempt_id=receipt.attempt_id,
                    generation=receipt.generation, checkpoint_id=receipt.checkpoint_id)
        self._save(ledger)
        return True

    def fixture_attempt(self, *, attempt_id: str, generation: int,
                        status: Literal["PASS", "FAILED", "BLOCKED"] = "PASS") -> AttemptReceipt:
        digest = hashlib.sha256(f"{attempt_id}:{generation}".encode()).hexdigest()[:12]
        return AttemptReceipt(
            attempt_id=attempt_id, generation=generation, idempotency_key=self._load()["work_order"]["idempotency_key"],
            checkpoint_id=f"ckpt-{digest}", status=status,
            terminal_reason="fixture acceptance passed" if status == "PASS" else "fixture retry budget exhausted",
            mutation_ref=f"patch://{attempt_id}" if status == "PASS" else "",
            tests_passed=status == "PASS", trace_id=f"trace-{digest}", evidence_ref=f"fixture://receipt/{digest}",
        )

    def run_fixture_attempt(self, *, attempt_id: str, generation: int,
                            death_at: str | None = None) -> bool:
        """Execute a side-effect-free fixture attempt; death leaves only prior durable state."""
        if death_at is not None and death_at not in BOUNDARIES:
            raise WorkOrderContractError("unknown death boundary")
        ledger = self._load()
        if ledger["terminal"]:
            return False
        self._event(ledger, "attempt_started", attempt_id=attempt_id, generation=generation)
        self._save(ledger)
        if death_at == "before_execution":
            return False
        self._event(ledger, "fixture_execution_completed", attempt_id=attempt_id)
        self._save(ledger)
        if death_at == "after_execution":
            return False
        self._event(ledger, "fixture_mutation_completed", attempt_id=attempt_id,
                    mutation_ref=f"patch://{attempt_id}")
        self._save(ledger)
        self._event(ledger, "fixture_tests_completed", attempt_id=attempt_id, passed=True)
        self._save(ledger)
        if death_at == "after_tests":
            return False
        self.checkpoint(self.fixture_attempt(attempt_id=attempt_id, generation=generation))
        if death_at == "after_checkpoint":
            return False
        return self.adjudicate(death_at=death_at)

    def adjudicate(self, *, death_at: str | None = None) -> bool:
        ledger = self._load()
        if ledger["terminal"]:
            return False
        if death_at == "fan_in":
            self._event(ledger, "fan_in_interrupted")
            self._save(ledger)
            return False
        passes = [item for item in ledger["attempts"] if item["status"] == "PASS" and item["tests_passed"]]
        if not passes:
            status, reason, accepted = "BLOCKED", "no mechanically valid checkpoint", None
        else:
            accepted = max(passes, key=lambda item: (item["generation"], item["attempt_id"]))
            status, reason = "PASS", "mechanical acceptance passed"
        terminal = TerminalReceipt(
            work_order_id=ledger["work_order"]["work_order_id"], status=status, terminal_reason=reason,
            accepted_attempt_id=accepted["attempt_id"] if accepted else None,
            checkpoint_id=accepted["checkpoint_id"] if accepted else None,
            trace_id=accepted["trace_id"] if accepted else "trace-fan-in",
        )
        if death_at == "before_terminal":
            self._event(ledger, "terminal_interrupted")
            self._save(ledger)
            return False
        ledger["terminal"] = asdict(terminal)
        self._event(ledger, "terminal_receipt_written", status=status)
        self._save(ledger)
        return True

    def terminal(self) -> TerminalReceipt | None:
        current = self._load().get("terminal")
        return TerminalReceipt(**current) if current else None
