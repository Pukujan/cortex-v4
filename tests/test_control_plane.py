from __future__ import annotations

import ast
from pathlib import Path

import pytest

from cortex_v4.control.control_plane import ControlPlaneError, ExecutionPolicy, classify_terminal_closeout, validate_routes


def policy(**changes: object) -> ExecutionPolicy:
    values = {"work_order_id": "wo-control-1", "task_id": "task-control-1", "generation": 2,
              "deadline_s": 30, "allowed_routes": ("route-a",), "required_checks": ("unit", "recovery")}
    values.update(changes)
    return ExecutionPolicy(**values)


def test_control_plane_is_versioned_and_closes_only_after_independent_checks():
    assert classify_terminal_closeout(policy(), checks={"unit": True, "recovery": True})["terminal_status"] == "PASS"
    assert classify_terminal_closeout(policy(), checks={"unit": True})["terminal_status"] == "FAILED"
    assert classify_terminal_closeout(policy(), checks={"unit": True, "recovery": True}, late_generation=True)["terminal_status"] == "BLOCKED"


def test_policy_and_routes_fail_closed():
    with pytest.raises(ControlPlaneError):
        policy(deadline_s=0).validate()
    with pytest.raises(ControlPlaneError):
        validate_routes(["route-b"], allowlist=["route-a"])


def test_v4_normal_control_contract_does_not_import_retired_runtime():
    root = Path(__file__).resolve().parents[1] / "cortex_v4" / "control"
    for path in (root / "control_plane.py", root / "workorder_recovery.py", root / "long_running.py", root / "temporal.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
        names += [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module]
        assert not any(name.startswith("cortex_core") or "ssc_" in name for name in names), (path, names)
