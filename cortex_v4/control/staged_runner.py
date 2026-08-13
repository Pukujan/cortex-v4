"""Provider-neutral, contract-first V4 stage runner.

The runner owns stage order, retries, fencing, checkpoint boundaries and final
objective adjudication. A worker owns only its assigned stage and must use the
scoped brain handle supplied in the stage context.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol

from .run_brain import BrainError, BrainGenerationError, BrainHandle, RunBrain
from .native_methodology import MethodologyPlan, NativeV4Methodology
from .task_contract import StageSpec, TaskContract, TaskContractError


class StageRunnerError(RuntimeError):
    """A stage or objective could not satisfy the frozen contract."""


@dataclass(frozen=True)
class StageOutcome:
    classification: str
    mechanical_check: Mapping[str, Any]
    artifact_refs: tuple[str, ...] = ()
    requested_model: str = ""
    actual_model: str = ""
    route_label: str = ""
    provider_call: bool = False
    tool_call_count: int = 0
    mutation_count: int = 0
    timeout_layer: str | None = None
    fallback_reason: str | None = None
    worker_lifecycle: str = "fresh"
    receipt_ref: str | None = None
    provider_receipts: tuple[Mapping[str, Any], ...] = ()

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "StageOutcome":
        classification = str(value.get("classification", ""))
        if classification not in {"success", "failed", "blocked"}:
            raise StageRunnerError("stage classification must be success, failed, or blocked")
        check = value.get("mechanical_check")
        if not isinstance(check, Mapping):
            raise StageRunnerError("stage outcome requires a structured mechanical_check")
        return cls(
            classification=classification,
            mechanical_check=dict(check),
            artifact_refs=tuple(str(item) for item in value.get("artifact_refs", ())),
            requested_model=str(value.get("requested_model", "")),
            actual_model=str(value.get("actual_model", "")),
            route_label=str(value.get("route_label", "")),
            provider_call=bool(value.get("provider_call", False)),
            tool_call_count=int(value.get("tool_call_count", 0)),
            mutation_count=int(value.get("mutation_count", 0)),
            timeout_layer=value.get("timeout_layer"),
            fallback_reason=value.get("fallback_reason"),
            worker_lifecycle=str(value.get("worker_lifecycle", "fresh")),
            receipt_ref=value.get("receipt_ref"),
            provider_receipts=tuple(dict(item) for item in value.get("provider_receipts", ()) if isinstance(item, Mapping)),
        )


@dataclass(frozen=True)
class StageContext:
    run_id: str
    objective_id: str
    contract_hash: str
    contract_revision: str
    exact_base_sha: str
    stage: StageSpec
    attempt_id: str
    generation: int
    dependency_results: Mapping[str, Mapping[str, Any]]
    idempotency_key: str
    brain: BrainHandle


class StageWorker(Protocol):
    def __call__(self, context: StageContext) -> StageOutcome | Mapping[str, Any]: ...


class MechanicalChecker(Protocol):
    def __call__(self, context: StageContext, outcome: StageOutcome) -> Mapping[str, Any]: ...


@dataclass
class CampaignResult:
    run_id: str
    status: str
    stage_receipts: list[dict[str, Any]] = field(default_factory=list)
    skipped_stages: list[str] = field(default_factory=list)
    closeout: dict[str, Any] | None = None


class StagedRunner:
    def __init__(
        self,
        contract: TaskContract,
        brain: RunBrain,
        *,
        worker_factory: Callable[[StageSpec], StageWorker],
        checkers: Mapping[str, MechanicalChecker],
        objective_checker: Callable[[RunBrain, TaskContract], Mapping[str, Any]],
        max_stage_attempts: int = 2,
        require_real_provider: bool = False,
        clock: Callable[[], float] = time.time,
        methodology_plan: MethodologyPlan | None = None,
        retry_backoff_s: float = 0.0,
    ):
        if contract.contract_hash != brain.manifest.get("contract_hash"):
            raise StageRunnerError("frozen contract hash does not match run brain")
        if max_stage_attempts < 1:
            raise StageRunnerError("max_stage_attempts must be positive")
        if retry_backoff_s < 0:
            raise StageRunnerError("retry_backoff_s cannot be negative")
        missing = [stage.stage_id for stage in contract.stages if stage.stage_id not in checkers]
        if missing:
            raise StageRunnerError(f"missing independent mechanical checkers: {missing}")
        self.contract = contract
        self.brain = brain
        self.worker_factory = worker_factory
        self.checkers = dict(checkers)
        self.objective_checker = objective_checker
        self.max_stage_attempts = max_stage_attempts
        self.require_real_provider = require_real_provider
        self.clock = clock
        self.retry_backoff_s = float(retry_backoff_s)
        self.methodology_plan = methodology_plan or NativeV4Methodology.preflight(contract).plan()
        if self.methodology_plan.contract_hash != contract.contract_hash:
            raise StageRunnerError("methodology plan does not match frozen contract")
        self.brain.record_receipt(
            "methodology-preflight",
            {
                "methodology_plan": self.methodology_plan.as_dict(),
                "plan_hash": NativeV4Methodology.plan_hash(self.methodology_plan),
            },
        )

    def _existing_result(self, stage: StageSpec) -> dict[str, Any] | None:
        value = self.brain.stage_status(stage.stage_id).get("result")
        if isinstance(value, Mapping) and value.get("classification") == "success":
            check = value.get("mechanical_check")
            if isinstance(check, Mapping) and check.get("passed") is True:
                return dict(value)
        return None

    def _next_generation(self, stage: StageSpec) -> int:
        status = self.brain.stage_status(stage.stage_id)
        return int(status.get("generation", 0)) + 1

    def _dependencies(self, stage: StageSpec, completed: Mapping[str, Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
        missing = [dependency for dependency in stage.depends_on if dependency not in completed]
        if missing:
            raise StageRunnerError(f"stage {stage.stage_id} missing completed dependencies: {missing}")
        return {dependency: dict(completed[dependency]) for dependency in stage.depends_on}

    def _validate_outcome(self, context: StageContext, outcome: StageOutcome) -> StageOutcome:
        if outcome.classification != "success":
            raise StageRunnerError(
                f"stage {context.stage.stage_id} returned {outcome.classification}"
            )
        if outcome.classification == "success" and outcome.mechanical_check.get("passed") is not True:
            raise StageRunnerError(f"stage {context.stage.stage_id} claimed success without a passing check")
        if self.require_real_provider:
            if not outcome.provider_call:
                raise StageRunnerError(f"stage {context.stage.stage_id} has no real provider call")
            if not outcome.actual_model:
                raise StageRunnerError(f"stage {context.stage.stage_id} is missing actual model identity")
            if not outcome.route_label:
                raise StageRunnerError(f"stage {context.stage.stage_id} is missing route label")
        if outcome.mutation_count < 0 or outcome.tool_call_count < 0:
            raise StageRunnerError("negative call/mutation counts are invalid")
        return outcome

    def _record_failure(self, context: StageContext, outcome: StageOutcome, error: str) -> dict[str, Any]:
        return self.brain.write_stage_result(
            self.brain.run_id,
            context.stage.stage_id,
            context.attempt_id,
            context.generation,
            {
                "classification": outcome.classification,
                "mechanical_check": dict(outcome.mechanical_check),
                "error": error,
                "requested_model": outcome.requested_model,
                "actual_model": outcome.actual_model,
                "route_label": outcome.route_label,
                "provider_call": outcome.provider_call,
                "worker_lifecycle": outcome.worker_lifecycle,
                "fallback_reason": outcome.fallback_reason,
                "timeout_layer": outcome.timeout_layer,
                "provider_attempts": [dict(item) for item in outcome.provider_receipts],
            },
            _capability=context.brain._capability,
        )

    def run(self) -> CampaignResult:
        completed: dict[str, Mapping[str, Any]] = {}
        result = CampaignResult(self.brain.run_id, "RUNNING")
        for stage_id in self.contract.dependency_dag:
            stage = self.contract.stage(stage_id)
            existing = self._existing_result(stage)
            if existing is not None:
                completed[stage_id] = existing
                result.skipped_stages.append(stage_id)
                continue
            dependencies = self._dependencies(stage, completed)
            stage_succeeded = False
            last_error = ""
            for retry in range(self.max_stage_attempts):
                generation = self._next_generation(stage)
                attempt_id = f"{stage.stage_id}-attempt-{retry + 1}-{uuid.uuid4().hex[:10]}"
                handle = self.brain.handle(stage.stage_id, stage.assigned_role, generation)
                context = StageContext(
                    run_id=self.brain.run_id,
                    objective_id=self.contract.objective_id,
                    contract_hash=self.contract.contract_hash,
                    contract_revision=self.contract.contract_revision,
                    exact_base_sha=self.contract.exact_base_sha,
                    stage=stage,
                    attempt_id=attempt_id,
                    generation=generation,
                    dependency_results=dependencies,
                    idempotency_key=f"{self.contract.objective_id}:{stage.stage_id}:{generation}",
                    brain=handle,
                )
                started = self.clock()
                try:
                    worker = self.worker_factory(stage)
                    raw = worker(context)
                    outcome = raw if isinstance(raw, StageOutcome) else StageOutcome.from_mapping(raw)
                    outcome = self._validate_outcome(context, outcome)
                    check = dict(self.checkers[stage.stage_id](context, outcome))
                    if check.get("passed") is not True:
                        raise StageRunnerError(f"mechanical checker failed for {stage.stage_id}")
                    outcome = StageOutcome(
                        **{**outcome.__dict__, "mechanical_check": check}
                    )
                    receipt = {
                        "schema": "cortex.v4.stage-receipt.v1",
                        "run_id": self.brain.run_id,
                        "objective_id": self.contract.objective_id,
                        "stage_id": stage.stage_id,
                        "assigned_role": stage.assigned_role,
                        "stage_kind": stage.kind,
                        "task_class": self.contract.task_class,
                        "contract_hash": self.contract.contract_hash,
                        "attempt_id": attempt_id,
                        "generation": generation,
                        "contract_revision": self.contract.contract_revision,
                        "exact_base_sha": self.contract.exact_base_sha,
                        "requested_model": outcome.requested_model,
                        "actual_model": outcome.actual_model,
                        "route_label": outcome.route_label,
                        "duration_s": max(0.0, self.clock() - started),
                        "tool_call_count": outcome.tool_call_count,
                        "mutation_count": outcome.mutation_count,
                        "worker_lifecycle": outcome.worker_lifecycle,
                        "fallback_reason": outcome.fallback_reason,
                        "timeout_layer": outcome.timeout_layer,
                        "retry_number": retry,
                        "artifact_refs": list(outcome.artifact_refs),
                        "mechanical_check": check,
                        "checkpoint_required": True,
                        "generation_fence": f"{self.contract.generation_fence}:{stage.stage_id}",
                        "dispatch": next(item.as_dict() for item in self.methodology_plan.dispatch if item.stage_id == stage.stage_id),
                        "provider_attempts": [dict(item) for item in outcome.provider_receipts],
                    }
                    self.brain.write_stage_result(
                        self.brain.run_id, stage.stage_id, attempt_id, generation,
                        {"classification": "success", **receipt}, _capability=handle._capability,
                    )
                    self.brain.checkpoint(
                        self.brain.run_id, stage.stage_id, attempt_id, generation,
                        {"stage_receipt": receipt, "completed": True}, list(outcome.artifact_refs),
                        _capability=handle._capability,
                    )
                    result.stage_receipts.append(receipt)
                    completed[stage_id] = receipt
                    stage_succeeded = True
                    break
                except Exception as exc:
                    last_error = str(exc)
                    failed = StageOutcome(
                        classification="failed",
                        mechanical_check={"passed": False, "error": last_error},
                        worker_lifecycle="failed",
                        provider_receipts=tuple(
                            [dict(exc.receipt.as_dict())]
                            if getattr(exc, "receipt", None) is not None and hasattr(exc.receipt, "as_dict")
                            else []
                        ),
                    )
                    try:
                        self._record_failure(context, failed, last_error)
                    except BrainError:
                        pass
                    if retry + 1 < self.max_stage_attempts and self.retry_backoff_s:
                        time.sleep(self.retry_backoff_s * (2 ** retry))
            if not stage_succeeded:
                result.status = "FAILED"
                failure_closeout = {
                    "status": "FAILED",
                    "stage_id": stage_id,
                    "error_class": "stage_failure",
                    "objective_check": {"passed": False, "failed_stage": stage_id},
                }
                try:
                    result.closeout = self.brain.finalize(failure_closeout)
                except BrainError:
                    result.closeout = failure_closeout
                return result
        objective = dict(self.objective_checker(self.brain, self.contract))
        if objective.get("passed") is not True:
            result.status = "FAILED"
            failure_closeout = {"status": "FAILED", "objective_check": objective}
            try:
                result.closeout = self.brain.finalize(failure_closeout)
            except BrainError:
                result.closeout = failure_closeout
            return result
        closeout = {
            "status": "PASS",
            "objective_check": objective,
            "stage_ids": list(self.contract.dependency_dag),
            "stage_receipt_count": len(result.stage_receipts),
        }
        result.closeout = self.brain.finalize(closeout)
        result.status = "PASS"
        return result
