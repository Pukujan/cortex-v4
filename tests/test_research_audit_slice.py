"""Third-loop research/audit slice tests (M21/M22/M25/M32/M33).

Proves the research/audit origin-to-frontier chain is wired end-to-end through the
named caller, that the strict behavioral oracle refuses every dropped rung, that
the static call-graph oracle refuses a deleted caller or missing rung, and that
the mechanical controller kills all 7 mutants. Deterministic; public fixture only;
no provider spend.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cortex_v4.control.mechanical_research_audit import (
    RESEARCH_AUDIT_METHODOLOGY_IDS,
    MechanicalResearchAuditController,
    ResearchGateError,
    assert_research_path_allowed,
    classify_research,
)
from cortex_v4.control.wire_oracle import (
    NAMED_CALLER_MODULE,
    RESEARCH_AUDIT_FUNCTION,
    call_graph_oracle,
)
from cortex_v4.operation.controllers import research_audit_oracle

SSC = Path(r"D:\claude\stupidly-simple-cortex")
PUBLIC = (
    SSC
    / "observations"
    / "loop-engineering"
    / "20260806-research-audit"
    / "public"
)
V4 = Path(r"D:\claude\cortex-v4")


def _contract() -> dict:
    return json.loads((PUBLIC / "migration-contract.json").read_text(encoding="utf-8"))


def _make_controller() -> MechanicalResearchAuditController:
    return MechanicalResearchAuditController(ssc_root=SSC, public_dir=PUBLIC, v4_root=V4)


def _grounded_call(kwargs=None):
    from cortex_v4.operation.controllers import run_research_audit_chain

    base = dict(
        corpus_root=SSC,
        task="audit the control layer",
        claim="the pack count is 7",
        source="docs/methodology/WORK-METHODOLOGIES.md:1027",
        citations=["S1"],
        sources={"S1": "config 7 modules"},
        claim_count=3,
        verified_count=3,
        residual_count=0,
    )
    if kwargs:
        base.update(kwargs)
    return run_research_audit_chain(**base)


@pytest.fixture(scope="module")
def full_run():
    from cortex_v4.control.mechanical_research_audit import (
        run_mechanical_research_audit,
    )

    return run_mechanical_research_audit(ssc_root=SSC, public_dir=PUBLIC, v4_root=V4)


@pytest.fixture(scope="module")
def wired():
    ctrl = _make_controller()
    contract = _contract()
    ctrl.select_methodology(contract)
    ctrl.read_corpus()
    ctrl.run_research_chain()
    return ctrl


def test_methodology_ids_selected(full_run):
    assert full_run.methodology_ids == ["M21", "M22", "M25", "M32", "M33"]
    assert full_run.methodology_receipt["required_methodology_ids"] == [
        "M21",
        "M22",
        "M25",
        "M32",
        "M33",
    ]


def test_classify_research():
    cls = classify_research(contract={"methodology_ids": ["M21", "M22"]})
    assert cls["task_class"] == "research-audit-slice"
    assert cls["required_methodology_ids"] == ["M21", "M22"]


def test_chain_clean_passes_oracle():
    r = _grounded_call()
    assert r["oracle"]["ok"] is True
    assert not r["oracle"]["errors"]
    assert r["named_caller"] == (
        "cortex_v4.operation.controllers.run_research_audit_chain"
    )


@pytest.mark.parametrize("rung", ["observe", "citation", "audit", "replay"])
def test_each_dropped_rung_fails_oracle(rung):
    r = _grounded_call({"disable": (rung,)})
    assert r["oracle"]["ok"] is False
    assert any(rung in e for e in r["oracle"]["errors"])


def test_citation_uncited_refused():
    from cortex_core.citation import UncitedClaim

    with pytest.raises(UncitedClaim):
        _grounded_call({"source": None, "citations": [], "sources": {}})


def test_call_graph_wire_complete_for_research():
    g = call_graph_oracle(NAMED_CALLER_MODULE, RESEARCH_AUDIT_FUNCTION)
    assert g["ok"] is True
    assert set(g["rung_methods_called"]) == {
        "observe",
        "citation_require",
        "citation_strict",
        "audit_classification",
        "replay",
    }
    assert g["missing_rungs"] == []
    assert g["source_sha256"]


def test_call_graph_refuses_deleted_caller():
    g = call_graph_oracle("cortex_v4.operation.does_not_exist", RESEARCH_AUDIT_FUNCTION)
    assert g["ok"] is False
    assert g["refused"]["code"] == "WIRE_MODULE_MISSING"


def test_controller_gates_recorded(full_run):
    names = {g["gate"] for g in full_run.gates}
    assert {
        "methodology_select",
        "corpus_citation",
        "named_caller",
        "research_origin_to_frontier",
        "call_graph_wire",
        "closeout_receipts",
        "closeout",
    } <= names


def test_closeout_receipt_structured(full_run):
    rec = full_run.methodology_receipt
    assert rec["schema"] == "cortex.loop_engineering.research_audit_receipt.v1"
    assert rec["wire_oracle_ok"] is True
    assert rec["call_graph_wire_ok"] is True
    assert rec["closeout_checkable_without_prose"] is True
    assert rec["corpus_reference_sha256"]


def test_mutants_all_killed(full_run):
    assert full_run.mutant_summary["killed"] == 7
    assert full_run.mutant_summary["survived"] == 0
    ids = {m["id"] for m in full_run.mutants}
    assert ids == {
        "RA-wire-rung-dropped",
        "RA-methodology-bypass",
        "RA-citation-omitted",
        "RA-hidden-holdout-exposed",
        "RA-callgraph-deleted-caller",
        "RA-closeout-receipt-omitted",
        "RA-prose-receipt-substituted",
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
    with pytest.raises(ResearchGateError) as exc:
        assert_research_path_allowed(
            SSC
            / "observations"
            / "loop-engineering"
            / "20260805-migration"
            / "hidden"
            / "A-private.sealed.json"
        )
    assert exc.value.code == "HIDDEN_HOLDOUT_REFUSED"


def test_source_corpus_not_copied():
    assert (V4 / "cortex_core" / "citation.py").exists() is False
    assert (V4 / "docs" / "methodology" / "WORK-METHODOLOGIES.md").exists() is False


def test_objective_checker_detects_wire():
    from cortex_v4.control.mechanical_migration import load_objective_checker

    checker = load_objective_checker(PUBLIC)
    result = checker.check(V4)
    assert result["ok"] is True
    assert any(
        "controllers.py" in p.replace("\\", "/") for p in result["named_callers"]
    )
