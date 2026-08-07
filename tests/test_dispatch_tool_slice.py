"""Fourth-loop dispatch/tools slice tests (M8/M18/M28/M29).

Proves the dispatch/tools origin-to-frontier chain is wired end-to-end through the
named caller, that the strict behavioral oracle refuses every dropped rung, that the
static call-graph oracle refuses a deleted caller, and that the mechanical controller
kills all 7 mutants. Deterministic; public fixture only; no provider spend.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cortex_v4.control.mechanical_dispatch_tool import (
    DISPATCH_TOOL_METHODOLOGY_IDS,
    DISPATCH_SEAT,
    MechanicalDispatchToolController,
    DispatchGateError,
    assert_dispatch_path_allowed,
    classify_dispatch,
)
from cortex_v4.control.wire_oracle import (
    DISPATCH_TOOL_FUNCTION,
    NAMED_CALLER_MODULE,
    call_graph_oracle,
)
from cortex_v4.operation.controllers import (
    run_dispatch_tool_chain,
    dispatch_tool_oracle,
)

SSC = Path(r"D:\claude\stupidly-simple-cortex")
PUBLIC = (
    SSC
    / "observations"
    / "loop-engineering"
    / "20260806-dispatch-tools"
    / "public"
)
V4 = Path(r"D:\claude\cortex-v4")


def _contract() -> dict:
    return json.loads((PUBLIC / "migration-contract.json").read_text(encoding="utf-8"))


def _make_controller() -> MechanicalDispatchToolController:
    return MechanicalDispatchToolController(ssc_root=SSC, public_dir=PUBLIC, v4_root=V4)


def _clean_call(**kw):
    base = {"corpus_root": SSC, "seat": DISPATCH_SEAT}
    base.update(kw)
    return run_dispatch_tool_chain(**base)


@pytest.fixture(scope="module")
def full_run():
    from cortex_v4.control.mechanical_dispatch_tool import (
        run_mechanical_dispatch_tool,
    )

    return run_mechanical_dispatch_tool(ssc_root=SSC, public_dir=PUBLIC, v4_root=V4)


@pytest.fixture(scope="module")
def wired():
    ctrl = _make_controller()
    ctrl.select_methodology(_contract())
    ctrl.read_corpus()
    ctrl.run_dispatch_chain()
    return ctrl


def test_methodology_ids_selected(full_run):
    assert full_run.methodology_ids == ["M8", "M18", "M28", "M29"]
    assert full_run.methodology_receipt["required_methodology_ids"] == [
        "M8",
        "M18",
        "M28",
        "M29",
    ]


def test_classify_dispatch():
    cls = classify_dispatch(contract={"methodology_ids": ["M8", "M28"]})
    assert cls["task_class"] == "dispatch-tools-slice"
    assert cls["required_methodology_ids"] == ["M8", "M28"]


def test_chain_clean_passes_oracle():
    r = _clean_call()
    assert r["oracle"]["ok"] is True
    assert not r["oracle"]["errors"]
    assert r["preflight_ok"] is True
    assert r["named_caller"] == "cortex_v4.operation.controllers.run_dispatch_tool_chain"


@pytest.mark.parametrize("rung", ["dispatch", "seating", "matrix", "fanout", "metabolism"])
def test_each_dropped_rung_fails_oracle(rung):
    r = _clean_call(disable=(rung,))
    assert r["oracle"]["ok"] is False
    label = "fan-out" if rung == "fanout" else rung
    assert any(label in e for e in r["oracle"]["errors"])


def test_out_seat_refused():
    # A retired/out seat (fable) must not be dispatchable: resolve raises.
    with pytest.raises(RuntimeError):
        _clean_call(seat="fable")


def test_call_graph_wire_complete():
    g = call_graph_oracle(NAMED_CALLER_MODULE, DISPATCH_TOOL_FUNCTION)
    assert g["ok"] is True
    assert set(g["rung_methods_called"]) == {
        "resolve_summon",
        "selected_rank",
        "seat_matrix",
        "fan_out",
        "metabolism",
    }
    assert g["missing_rungs"] == []


def test_call_graph_refuses_deleted_caller():
    g = call_graph_oracle("cortex_v4.operation.does_not_exist", DISPATCH_TOOL_FUNCTION)
    assert g["ok"] is False
    assert g["refused"]["code"] == "WIRE_MODULE_MISSING"


def test_controller_gates_recorded(full_run):
    names = {g["gate"] for g in full_run.gates}
    assert {
        "methodology_select",
        "corpus_citation",
        "named_caller",
        "dispatch_origin_to_frontier",
        "call_graph_wire",
        "closeout_receipts",
        "closeout",
    } <= names


def test_closeout_receipt_structured(full_run):
    rec = full_run.methodology_receipt
    assert rec["schema"] == "cortex.loop_engineering.dispatch_tool_receipt.v1"
    assert rec["wire_oracle_ok"] is True
    assert rec["call_graph_wire_ok"] is True
    assert rec["seat"] == DISPATCH_SEAT
    assert rec["corpus_reference_sha256"]


def test_mutants_all_killed(full_run):
    assert full_run.mutant_summary["killed"] == 7
    assert full_run.mutant_summary["survived"] == 0
    ids = {m["id"] for m in full_run.mutants}
    assert ids == {
        "DT-wire-rung-dropped",
        "DT-methodology-bypass",
        "DT-citation-omitted",
        "DT-hidden-holdout-exposed",
        "DT-callgraph-deleted-caller",
        "DT-closeout-receipt-omitted",
        "DT-prose-receipt-substituted",
    }


@pytest.mark.parametrize(
    "kind,code",
    [
        ("wire", "WIRE_RUNG_MISSING"),
        ("methodology", "METHODOLOGY_SELECT_REQUIRED"),
        ("citation", "CORPUS_CITATION_REQUIRED"),
        ("hidden", "HIDDEN_HOLDOUT_REFUSED"),
        ("callgraph", "WIRE_RUNG_MISSING"),
        ("receipt", "RECEIPT_INCOMPLETE"),
        ("prose", "STRUCTURED_METHODOLOGY_REQUIRED"),
    ],
)
def test_each_mutant_refuses(full_run, kind, code):
    result = next(m for m in full_run.mutants if m["kind"] == kind)
    assert result["killed"] is True
    assert result["code"] == code


def test_hidden_holdout_refused():
    with pytest.raises(DispatchGateError) as exc:
        assert_dispatch_path_allowed(
            SSC
            / "observations"
            / "loop-engineering"
            / "20260805-migration"
            / "hidden"
            / "A-private.sealed.json"
        )
    assert exc.value.code == "HIDDEN_HOLDOUT_REFUSED"


def test_source_tables_not_copied_into_v4():
    assert (V4 / "cortex_core" / "model_summon.py").exists() is False
    assert (V4 / "data" / "model_summon.json").exists() is False
    assert (V4 / "docs" / "methodology" / "WORK-METHODOLOGIES.md").exists() is False


def test_objective_checker_detects_wire():
    from cortex_v4.control.mechanical_migration import load_objective_checker

    checker = load_objective_checker(PUBLIC)
    result = checker.check(V4)
    assert result["ok"] is True
    assert any(
        "controllers.py" in p.replace("\\", "/") for p in result["named_callers"]
    )