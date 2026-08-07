"""C-lane mechanical methodology gate tests (M32/M33 first loop).

Deterministic only. Public fixture only. No A diagnosis. No provider spend.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cortex_v4.control.mechanical_loop import (
    MechanicalGateError,
    MechanicalLoopController,
    assert_path_allowed,
    classify_task,
    freeze_public_fixture,
    run_mechanical_loop,
)

SSC = Path(r"D:\claude\stupidly-simple-cortex")
PUBLIC = SSC / "observations" / "loop-engineering" / "20260805-litellm" / "public"

EXPECTED_FREEZE = {
    "failure-injector.json": "6749d7cba00af5afb7c4300f899bf207707977da73d96c4f7d0ac060a16b59c0",
    "objective-checker.py": "2477e1ab7afc3061951390b17993b48fea7991726ac3a2652cffef5c0a79c629",
    "task-contract.json": "a28ed19634077adef994fb52101c6eaa3c63f6ba7931726c8c0a3773531c57f5",
    "tool-contract.json": "17c7476141306bb9e041e54c658c19cddf078706774f710824a733e9c4ab735e",
}


def test_methodology_ids_selected_include_m32_m33():
    classification = classify_task(
        {
            "task_id": "loop-engineering-litellm-extended-task",
            "name": "LiteLLM long-running extended control-layer audit",
            "description": "stall recovery through LiteLLM gateway class",
            "task_class": "extended-task-control-failure",
        }
    )
    assert classification["task_class"] == "extended-task-control-failure"
    assert "M32" in classification["methodology_ids"]
    assert "M33" in classification["methodology_ids"]
    assert "M30" in classification["methodology_ids"]
    assert classification["observation_required_before_hypothesis"] is True


def test_refuses_hypothesis_without_observation_receipt(tmp_path):
    controller = MechanicalLoopController(
        ssc_root=SSC,
        public_fixture_dir=PUBLIC,
        work_root=tmp_path / "work",
        expected_freeze=EXPECTED_FREEZE,
    )
    controller.select_methodologies(
        {"task_class": "extended-task-control-failure", "name": "litellm extended"}
    )
    controller.freeze_evidence()
    with pytest.raises(MechanicalGateError) as exc:
        controller.select_hypotheses()
    assert exc.value.code == "OBSERVATION_REQUIRED"


def test_refuses_hidden_path_access():
    with pytest.raises(MechanicalGateError) as exc:
        assert_path_allowed(
            r"D:\claude\stupidly-simple-cortex\observations\loop-engineering\20260805-litellm\hidden\A-private.sealed.json"
        )
    assert exc.value.code == "HIDDEN_HOLDOUT_REFUSED"
    with pytest.raises(MechanicalGateError):
        assert_path_allowed("observations/loop-engineering/20260805-litellm/hidden/foo")
    with pytest.raises(MechanicalGateError):
        assert_path_allowed("something/A-private/package.json")
    # Public path is allowed.
    allowed = assert_path_allowed(PUBLIC / "task-contract.json")
    assert allowed.name == "task-contract.json"


def test_clean_strong_path_passes_objective_checker(tmp_path):
    result = run_mechanical_loop(
        ssc_root=SSC,
        public_fixture_dir=PUBLIC,
        work_root=tmp_path / "c-full",
        expected_freeze=EXPECTED_FREEZE,
    )
    assert result.ok is True, result.refused
    assert result.strong_result.get("ok") is True
    assert result.strong_result.get("objective_ok") is True
    assert result.objective.get("ok") is True
    assert result.weak_result.get("ok") is False
    assert result.weak_result.get("objective_ok") is False
    assert result.observation.get("observation_id")
    assert "M32" in result.methodology_ids and "M33" in result.methodology_ids
    receipt = result.methodology_receipt
    assert receipt["observation_before_hypothesis"] is True
    assert receipt["hidden_holdout_enforced"] is True
    assert receipt["live_provider"]["status"] == "UNRESOLVED"


def test_mutants_killed(tmp_path):
    controller = MechanicalLoopController(
        ssc_root=SSC,
        public_fixture_dir=PUBLIC,
        work_root=tmp_path / "mut-work",
        expected_freeze=EXPECTED_FREEZE,
    )
    controller.select_methodologies(
        {"task_class": "extended-task-control-failure", "name": "litellm extended stall"}
    )
    controller.freeze_evidence()
    # Observation then hypotheses then mutants (full path also covers this).
    weak = controller.run_weak_path(tmp_path / "mut-work" / "weak")
    controller.record_observation(weak_run=weak)
    controller.select_hypotheses()
    mutants = controller.run_mutants(tmp_path / "mut-work" / "mutants")
    assert len(mutants) == 3
    assert all(m["killed"] for m in mutants)
    assert not any(m["regression"] for m in mutants)
    ids = {m["id"] for m in mutants}
    assert ids == {"M-gen-fence", "M-ckpt-resume", "M-retry-ownership"}


def test_freeze_matches_public_receipt():
    freeze = freeze_public_fixture(PUBLIC)
    for name, digest in EXPECTED_FREEZE.items():
        assert freeze["files"][name] == digest
    assert freeze["task_contract_hash"] == EXPECTED_FREEZE["task-contract.json"]
