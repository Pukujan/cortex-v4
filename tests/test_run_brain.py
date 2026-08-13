from __future__ import annotations

from pathlib import Path

import pytest

from cortex_v4.control.run_brain import (
    BrainAuthorizationError,
    BrainGenerationError,
    BrainLeaseError,
    RunBrain,
)


class Clock:
    def __init__(self) -> None:
        self.value = 1000.0

    def __call__(self) -> float:
        return self.value


def contract() -> dict:
    return {
        "objective_id": "objective-1",
        "exact_base_sha": "a" * 40,
        "task_class": "coding",
        "contract_revision": "v4-contract-1",
        "generation_fence": "objective-1:fence",
        "dependency_dag": ["contract", "implementation", "tests"],
        "acceptance_checks": ["objective-check"],
        "stage_specs": [
            {
                "stage_id": "contract",
                "assigned_role": "orchestrator",
                "allowed_read_refs": [],
                "allowed_write_set": ["contract.json"],
                "acceptance_checks": ["contract-check"],
                "generation": 0,
            },
            {
                "stage_id": "implementation",
                "assigned_role": "implementation_worker",
                "allowed_read_refs": ["artifact://implementation/input.txt"],
                "allowed_write_set": ["implementation/output.txt"],
                "acceptance_checks": ["unit-test"],
                "generation": 0,
            },
            {
                "stage_id": "tests",
                "assigned_role": "test_author",
                "allowed_read_refs": ["artifact://tests/holdout.txt"],
                "allowed_write_set": ["tests/result.txt"],
                "acceptance_checks": ["test-check"],
                "blind_until_convergence": True,
                "generation": 0,
            },
        ],
    }


def test_scoped_context_does_not_expose_brain_path_or_other_stage(tmp_path: Path):
    brain = RunBrain.create(contract(), tmp_path, run_id="run-1")
    handle = brain.handle("implementation", "implementation_worker", 0)
    context = handle.read_brain()
    assert "run_root" not in context
    assert "implementation" == context["stage_id"]
    assert context["artifact_refs"] == ["artifact://implementation/input.txt"]
    with pytest.raises(BrainAuthorizationError):
        brain.handle("implementation", "test_author", 0)
    with pytest.raises(BrainAuthorizationError):
        handle.read_artifact("artifact://tests/holdout.txt")
    test_author = brain.handle("tests", "test_author", 0)
    with pytest.raises(BrainAuthorizationError):
        test_author.read_artifact("artifact://tests/holdout.txt")


def test_read_does_not_renew_lease_but_checkpoint_and_heartbeat_do(tmp_path: Path):
    clock = Clock()
    brain = RunBrain.create(contract(), tmp_path, run_id="run-2", clock=clock, active_lease_seconds=10)
    handle = brain.handle("implementation", "implementation_worker", 0)
    initial = brain._lease()["lease_expires_at"]
    clock.value += 5
    handle.read_brain()
    assert brain._lease()["lease_expires_at"] == initial
    handle.heartbeat("attempt-1")
    assert brain._lease()["lease_expires_at"] == clock.value + 10
    clock.value += 5
    ref = handle.write_artifact("implementation/output.txt", "ok", mutation_key="m-1")
    checkpoint = handle.checkpoint("attempt-1", {"progress": ["write"]}, [ref])
    assert checkpoint["checkpoint_id"]
    assert brain._lease()["last_progress_at"] == clock.value


def test_checkpoint_is_idempotent_and_fenced(tmp_path: Path):
    brain = RunBrain.create(contract(), tmp_path, run_id="run-3")
    handle = brain.handle("implementation", "implementation_worker", 0)
    value = handle.checkpoint("attempt-1", {"n": 1}, [])
    assert handle.checkpoint("attempt-1", {"n": 1}, []) == value
    with pytest.raises(BrainGenerationError):
        handle.checkpoint("attempt-1", {"n": 2}, [])
    with pytest.raises(BrainGenerationError):
        brain.handle("implementation", "implementation_worker", -1)


def test_late_result_from_previous_generation_is_rejected(tmp_path: Path):
    brain = RunBrain.create(contract(), tmp_path, run_id="run-fence")
    old = brain.handle("implementation", "implementation_worker", 0)
    current = brain.handle("implementation", "implementation_worker", 1)
    current.checkpoint("attempt-2", {"generation": 1}, [])
    with pytest.raises(BrainGenerationError):
        old.checkpoint("attempt-1", {"generation": 0}, [])


def test_opening_replacement_generation_fences_old_worker_immediately(tmp_path: Path):
    brain = RunBrain.create(contract(), tmp_path, run_id="run-open-fence")
    old = brain.handle("implementation", "implementation_worker", 0)
    brain.handle("implementation", "implementation_worker", 1)
    with pytest.raises(BrainGenerationError):
        old.write_artifact("implementation/output.txt", "late", mutation_key="late-mutation")


def test_mutation_is_idempotent_and_conflicts_are_rejected(tmp_path: Path):
    brain = RunBrain.create(contract(), tmp_path, run_id="run-4")
    handle = brain.handle("implementation", "implementation_worker", 0)
    first = handle.write_artifact("implementation/output.txt", b"same", mutation_key="m-1")
    second = handle.write_artifact("implementation/output.txt", b"same", mutation_key="m-1")
    assert first == second
    with pytest.raises(BrainGenerationError):
        handle.write_artifact("implementation/output.txt", b"different", mutation_key="m-1")
    with pytest.raises(BrainAuthorizationError):
        handle.write_artifact("tests/result.txt", b"no", mutation_key="m-2")


def test_quarantine_is_append_only_and_finalize_uses_configured_grace(tmp_path: Path):
    clock = Clock()
    brain = RunBrain.create(contract(), tmp_path, run_id="run-5", clock=clock, retention_seconds=20)
    handle = brain.handle("implementation", "implementation_worker", 0)
    ref = handle.write_artifact("implementation/output.txt", "candidate", mutation_key="m-1")
    proposal = handle.request_memory_quarantine(ref, "stale candidate")
    assert proposal["status"] == "proposed"
    assert (brain.run_root / "artifacts" / "implementation" / "output.txt").is_file()
    handle.checkpoint("attempt-1", {"done": True}, [ref])
    brain.write_stage_result(
        brain.run_id, "implementation", "attempt-1", 0,
        {"classification": "success", "mechanical_check": {"passed": True}},
        _capability=handle._capability,
    )
    final = brain.finalize({"status": "PASS", "objective_check": {"passed": True}, "artifacts": [ref]})
    assert final["status"] == "PASS"
    assert brain._lease()["grace_expires_at"] == clock.value + 20
    with pytest.raises(BrainLeaseError):
        handle.heartbeat("attempt-2")
    assert brain.cleanup(now=clock.value + 19) is False
    assert brain.cleanup(now=clock.value + 20) is True
    assert not brain.run_root.exists()
