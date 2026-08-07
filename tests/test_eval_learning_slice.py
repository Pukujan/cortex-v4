"""Fifth-loop eval/learning slice tests (M4/M5/M9/M12/M16/M17/M19/M20/M24).

Proves the eval/learning origin-to-frontier chain is wired end-to-end through the
named caller, that the strict behavioral oracle refuses every dropped rung, that
the static call-graph oracle refuses a deleted caller or missing rung, and that
the mechanical controller kills all 7 mutants. Deterministic; public fixture only;
no provider spend.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cortex_v4.control.mechanical_eval_learning import (
    EVAL_LEARNING_METHODOLOGY_IDS,
    MechanicalEvalLearningController,
    EvalGateError,
    assert_eval_path_allowed,
    classify_eval,
)
from cortex_v4.control.wire_oracle import (
    EVAL_LEARNING_FUNCTION,
    NAMED_CALLER_MODULE,
    call_graph_oracle,
)
from cortex_v4.operation.controllers import eval_learning_oracle

SSC = Path(r"D:\claude\stupidly-simple-cortex")
PUBLIC = (
    SSC
    / "observations"
    / "loop-engineering"
    / "20260806-eval-learning"
    / "public"
)
V4 = Path(r"D:\claude\cortex-v4")

RUNG_NAME = {
    "oracle": "oracle",
    "calibration": "calibration",
    "metric": "metric",
    "holdout": "holdout",
    "blocked": "blocked",
    "refute": "refutation",
    "convenience": "convenience-audit",
    "qagate": "QA-gate",
}
RUNG = RUNG_NAME
RUNG_LABEL = {
    "oracle": "oracle rung",
    "calibration": "inter-rater kappa",
    "metric": "measured score",
    "holdout": "holdout",
    "blocked": "blocked-state",
    "refute": "refutation",
    "convenience": "convenience-audit",
    "qagate": "QA-gate",
}


def _contract() -> dict:
    return json.loads((PUBLIC / "migration-contract.json").read_text(encoding="utf-8"))


def _make_controller() -> MechanicalEvalLearningController:
    return MechanicalEvalLearningController(ssc_root=SSC, public_dir=PUBLIC, v4_root=V4)


def _clean_call(**kw):
    base = {
        "corpus_root": SSC,
        "gold": ["PASS", "PASS", "FAIL"],
        "pred": ["PASS", "PASS", "FAIL"],
        "labels": ["PASS", "FAIL"],
    }
    base.update(kw)
    from cortex_v4.operation.controllers import run_eval_learning_chain

    return run_eval_learning_chain(**base)


@pytest.fixture(scope="module")
def full_run():
    from cortex_v4.control.mechanical_eval_learning import (
        run_mechanical_eval_learning,
    )

    return run_mechanical_eval_learning(ssc_root=SSC, public_dir=PUBLIC, v4_root=V4)


@pytest.fixture(scope="module")
def wired():
    ctrl = _make_controller()
    ctrl.select_methodology(_contract())
    ctrl.read_corpus()
    ctrl.run_eval_chain()
    return ctrl


def test_methodology_ids_selected(full_run):
    assert full_run.methodology_ids == list(EVAL_LEARNING_METHODOLOGY_IDS)
    assert full_run.methodology_receipt["required_methodology_ids"] == list(
        EVAL_LEARNING_METHODOLOGY_IDS
    )


def test_classify_eval():
    cls = classify_eval(contract={"methodology_ids": ["M4", "M20"]})
    assert cls["task_class"] == "eval-learning-slice"
    assert cls["required_methodology_ids"] == ["M4", "M20"]


def test_chain_clean_passes_oracle():
    r = _clean_call()
    assert r["oracle"]["ok"] is True
    assert not r["oracle"]["errors"]
    assert r["named_caller"] == "cortex_v4.operation.controllers.run_eval_learning_chain"
    steps = r["steps"]
    assert steps["calibration"]["calibrated"] is True
    assert steps["calibration"]["kappa"] >= 0.4
    assert steps["metric"]["ndcg_at_k"] >= 0.5


@pytest.mark.parametrize(
    "rung",
    ["oracle", "calibration", "metric", "holdout", "blocked", "refute", "convenience", "qagate"],
)
def test_each_dropped_rung_fails_oracle(rung):
    r = _clean_call(disable=(rung,))
    assert r["oracle"]["ok"] is False
    labels = (RUNG[rung], RUNG_LABEL[rung], rung)
    assert any(any(lbl in e for lbl in labels) for e in r["oracle"]["errors"])


def test_call_graph_wire_complete():
    g = call_graph_oracle(NAMED_CALLER_MODULE, EVAL_LEARNING_FUNCTION)
    assert g["ok"] is True
    assert set(g["rung_methods_called"]) == {
        "verdict_has_no_judge",
        "cohens_kappa",
        "ndcg",
        "holdout",
        "blocked_state",
        "refutation",
        "convenience_audit",
        "qa_gate",
    }
    assert g["missing_rungs"] == []


def test_call_graph_refuses_deleted_caller():
    g = call_graph_oracle("cortex_v4.operation.does_not_exist", EVAL_LEARNING_FUNCTION)
    assert g["ok"] is False
    assert g["refused"]["code"] == "WIRE_MODULE_MISSING"


def test_controller_gates_recorded(full_run):
    names = {gg["gate"] for gg in full_run.gates}
    assert {
        "methodology_select",
        "corpus_citation",
        "named_caller",
        "eval_origin_to_frontier",
        "call_graph_wire",
        "closeout_receipts",
        "closeout",
    } <= names


def test_closeout_receipt_structured(full_run):
    rec = full_run.methodology_receipt
    assert rec["schema"] == "cortex.loop_engineering.eval_learning_receipt.v1"
    assert rec["wire_oracle_ok"] is True
    assert rec["call_graph_wire_ok"] is True
    assert rec["corpus_reference_sha256"]


def test_mutants_all_killed(full_run):
    assert full_run.mutant_summary["killed"] == 7
    assert full_run.mutant_summary["survived"] == 0
    ids = {m["id"] for m in full_run.mutants}
    assert ids == {
        "EV-wire-rung-dropped",
        "EV-methodology-bypass",
        "EV-citation-omitted",
        "EV-hidden-holdout-exposed",
        "EV-callgraph-deleted-caller",
        "EV-closeout-receipt-omitted",
        "EV-prose-receipt-substituted",
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
    with pytest.raises(EvalGateError) as exc:
        assert_eval_path_allowed(
            SSC
            / "observations"
            / "loop-engineering"
            / "20260805-migration"
            / "hidden"
            / "A-private.sealed.json"
        )
    assert exc.value.code == "HIDDEN_HOLDOUT_REFUSED"


def test_source_tables_not_copied_into_v4():
    assert (V4 / "cortex_core" / "calibration.py").exists() is False
    assert (V4 / "cortex_core" / "ndcg.py").exists() is False
    assert (V4 / "docs" / "methodology" / "WORK-METHODOLOGIES.md").exists() is False