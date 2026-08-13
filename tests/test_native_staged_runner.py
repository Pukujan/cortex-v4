from __future__ import annotations

from pathlib import Path

from cortex_v4.control.run_brain import RunBrain
from cortex_v4.control.staged_runner import StageOutcome, StagedRunner
from cortex_v4.control.task_contract import TaskContract


BASE = "b" * 40


def frozen() -> TaskContract:
    return TaskContract.freeze(
        {
            "objective_id": "objective-runner",
            "exact_base_sha": BASE,
            "task_class": "coding",
            "contract_revision": "contract-v1",
            "generation_fence": "objective-runner:fence",
            "dependency_dag": ["contract", "implementation", "critique", "adjudication"],
            "acceptance_checks": ["objective-check"],
            "route_policy": {"role": "chat-only", "model": "test-model", "endpoint": "chat", "capability": "chat"},
            "types_interfaces_schemas": ["test-contract-v1"],
            "stage_specs": [
                {"stage_id": "contract", "assigned_role": "orchestrator", "depends_on": [], "allowed_write_set": [], "acceptance_checks": ["contract-check"], "stage_deadline_s": 20, "kind": "contract"},
                {"stage_id": "implementation", "assigned_role": "implementation_worker", "depends_on": ["contract"], "allowed_write_set": ["implementation/result.txt"], "acceptance_checks": ["implementation-check"], "stage_deadline_s": 20, "kind": "implementation"},
                {"stage_id": "critique", "assigned_role": "critic", "depends_on": ["implementation"], "allowed_read_refs": ["artifact://implementation/result.txt"], "allowed_write_set": [], "acceptance_checks": ["critique-check"], "stage_deadline_s": 20, "kind": "critique"},
                {"stage_id": "adjudication", "assigned_role": "orchestrator", "depends_on": ["critique"], "allowed_write_set": [], "acceptance_checks": ["adjudication-check"], "stage_deadline_s": 20, "kind": "adjudication", "requires_critique": True},
            ],
        }
    )


def make_brain(tmp_path: Path) -> tuple[TaskContract, RunBrain]:
    task = frozen()
    brain = RunBrain.create(task.as_dict(), tmp_path, run_id="runner-run")
    return task, brain


def test_runner_executes_dag_with_critique_and_real_checkers(tmp_path: Path):
    task, brain = make_brain(tmp_path)
    calls: list[str] = []

    def worker(stage):
        def run(context):
            calls.append(stage.stage_id)
            refs = ()
            if stage.stage_id == "implementation":
                refs = (context.brain.write_artifact("implementation/result.txt", "real mutation", mutation_key="impl-v1"),)
            return StageOutcome(
                classification="success",
                mechanical_check={"passed": True},
                artifact_refs=refs,
                requested_model="test-model",
                actual_model="test-model",
                route_label="fake-local-test-route",
                provider_call=True,
                mutation_count=1 if refs else 0,
            )
        return run

    runner = StagedRunner(
        task,
        brain,
        worker_factory=worker,
        checkers={stage.stage_id: lambda _context, _outcome: {"passed": True, "check": stage.stage_id} for stage in task.stages},
        objective_checker=lambda _brain, _task: {"passed": True, "checker": "independent-objective"},
        require_real_provider=True,
    )
    result = runner.run()
    assert result.status == "PASS"
    assert calls == ["contract", "implementation", "critique", "adjudication"]
    assert brain._lease()["status"] == "completed"


def test_runner_retries_only_failed_stage_and_does_not_replay_completed(tmp_path: Path):
    task, brain = make_brain(tmp_path)
    calls: list[str] = []
    attempts: dict[str, int] = {}

    def worker(stage):
        def run(context):
            calls.append(stage.stage_id)
            attempts[stage.stage_id] = attempts.get(stage.stage_id, 0) + 1
            if stage.stage_id == "critique" and attempts[stage.stage_id] == 1:
                return StageOutcome("failed", {"passed": False}, requested_model="m", actual_model="m", route_label="r", provider_call=True)
            refs = ()
            if stage.stage_id == "implementation":
                refs = (context.brain.write_artifact("implementation/result.txt", "same", mutation_key="impl-v1"),)
            return StageOutcome("success", {"passed": True}, refs, "m", "m", "r", True, mutation_count=1 if refs else 0)
        return run

    checks = {stage.stage_id: lambda _context, _outcome: {"passed": True} for stage in task.stages}
    result = StagedRunner(task, brain, worker_factory=worker, checkers=checks, objective_checker=lambda _b, _t: {"passed": True}, max_stage_attempts=2, require_real_provider=True).run()
    assert result.status == "PASS"
    assert calls == ["contract", "implementation", "critique", "critique", "adjudication"]


def test_runner_rejects_success_without_independent_checker_or_provider(tmp_path: Path):
    task, brain = make_brain(tmp_path)
    def worker(_stage):
        return lambda _context: StageOutcome("success", {"passed": True})
    checks = {stage.stage_id: lambda _context, _outcome: {"passed": True} for stage in task.stages}
    result = StagedRunner(task, brain, worker_factory=worker, checkers=checks, objective_checker=lambda _b, _t: {"passed": True}, max_stage_attempts=1, require_real_provider=True).run()
    assert result.status == "FAILED"
    assert result.closeout and result.closeout["stage_id"] == "contract"
