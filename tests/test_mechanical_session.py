"""Tests for V4 mechanical session control (driver entry).

Deterministic happy path + refuse path + A/B vs adapter. No provider spend.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cortex_v4.control.mechanical_session import (
    MechanicalSessionController,
    SessionGateError,
    ab_compare,
    classify_session_task,
    mechanical_session_oracle,
    run_mechanical_session_chain,
)

SSC = Path(r"D:\claude\stupidly-simple-cortex")


def test_classify_selects_build_and_base_methods():
    c = classify_session_task("Wire OpenCode plugin build and fix mechanical gates")
    assert "M1" in c["methodology_ids"]
    assert "M7" in c["methodology_ids"]
    assert "M3" in c["methodology_ids"]
    assert "build" in c["task_class"]


def test_ungrounded_write_refused():
    ctl = MechanicalSessionController(ssc_root=SSC)
    with pytest.raises(SessionGateError) as exc:
        ctl.gate_tool("sess-ungrounded", "Write", enforce=True)
    assert exc.value.code == "PREFLIGHT_REQUIRED"


def test_shadow_ungrounded_logs_would_have_failed():
    ctl = MechanicalSessionController(ssc_root=SSC)
    out = ctl.gate_tool("sess-shadow", "Write", enforce=False)
    assert out["allowed"] is False
    assert out["would_have_failed"] is True
    assert out["control_layer"].startswith("cortex_v4")


def test_full_chain_oracle_passes():
    result = run_mechanical_session_chain(
        corpus_root=SSC,
        session_id="test-chain-1",
        task="Build V4 mechanical session control for OpenCode wire",
        enforce=True,
    )
    assert result["oracle"]["ok"] is True, result["oracle"]
    assert result["steps"]["preflight"]["pack_hash"]
    assert result["steps"]["gate"]["allowed"] is True
    assert result["named_caller"].endswith("run_mechanical_session_chain")


def test_mutant_missing_preflight_fails_oracle():
    result = run_mechanical_session_chain(
        corpus_root=SSC,
        session_id="test-chain-mutant",
        task="Build V4 mechanical session control",
        enforce=False,
        disable=("preflight",),
    )
    assert result["oracle"]["ok"] is False
    assert any("preflight" in e for e in result["oracle"]["errors"])


def test_ab_mechanical_matches_adapter_and_is_stricter():
    report = ab_compare(
        corpus_root=SSC,
        task="Wire OpenCode sessions through V4 mechanical methodology control layer",
    )
    assert report["checks"]["both_pack_hash"] is True
    assert report["checks"]["both_grounded_allow"] is True
    assert report["checks"]["b_refuses_ungrounded"] is True
    assert report["checks"]["b_oracle_ok"] is True
    assert report["checks"]["b_is_control_layer"] is True
    assert report["ok"] is True, report


def test_oracle_unit_rejects_empty_steps():
    verdict = mechanical_session_oracle({})
    assert verdict["ok"] is False
    assert len(verdict["errors"]) >= 3
