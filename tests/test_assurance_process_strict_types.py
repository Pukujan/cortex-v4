from __future__ import annotations

import json
import subprocess
import sys


def _raw_call(db_path, request):
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "cortex_v4.control.assurance_process",
            str(db_path),
        ],
        input=json.dumps(request),
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    return completed.returncode, json.loads(completed.stdout)


def _valid_work_order():
    return {
        "work_order_id": "wo-strict",
        "artifact_id": "artifact-strict",
        "artifact_version": "v1",
        "mutating": False,
        "initial_epoch": 0,
        "initial_fence_token": "fence-0",
    }


def test_process_boundary_rejects_string_boolean_instead_of_coercing_true(tmp_path):
    work_order = _valid_work_order()
    work_order["mutating"] = "false"
    code, response = _raw_call(
        tmp_path / "strict-bool.db",
        {"operation": "register_work_order", "work_order": work_order},
    )
    assert code == 2
    assert response["ok"] is False
    assert response["error_type"] == "TypeError"
    assert response["message"] == "mutating must be a boolean"


def test_process_boundary_rejects_null_identity_instead_of_stringifying_none(tmp_path):
    work_order = _valid_work_order()
    work_order["artifact_id"] = None
    code, response = _raw_call(
        tmp_path / "strict-null.db",
        {"operation": "register_work_order", "work_order": work_order},
    )
    assert code == 2
    assert response["ok"] is False
    assert response["error_type"] == "TypeError"
    assert response["message"] == "artifact_id must be a non-empty string"


def test_process_boundary_rejects_boolean_epoch_as_non_integer_semantics(tmp_path):
    work_order = _valid_work_order()
    work_order["initial_epoch"] = True
    code, response = _raw_call(
        tmp_path / "strict-epoch.db",
        {"operation": "register_work_order", "work_order": work_order},
    )
    assert code == 2
    assert response["ok"] is False
    assert response["error_type"] == "TypeError"
    assert response["message"] == "initial_epoch must be an integer"
