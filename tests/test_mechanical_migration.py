"""C-lane mechanical methodology-core migration tests (second loop).

Proves each migration methodology gate mechanically refuses when its invariant is
violated. Deterministic; public fixture only; no provider spend; no A/B private
inputs. Re-runs the origin-to-frontier chain through B's migrated wire.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cortex_v4.control.mechanical_migration import (
    MIGRATION_METHODOLOGY_IDS,
    MechanicalMigrationController,
    MigrationGateError,
    assert_migration_path_allowed,
    classify_migration,
    freeze_migration_slice,
)
from cortex_v4.control.mechanical_migration import load_objective_checker
from cortex_v4.operation.controllers import methodology_origin_oracle

SSC = Path(r"D:\claude\stupidly-simple-cortex")
PUBLIC = SSC / "observations" / "loop-engineering" / "20260805-migration" / "public"
V4 = Path(r"D:\claude\cortex-v4")

EXPECTED_MODULES = [
    "cortex_core.session_preflight",
    "cortex_core.forced_rag_gate",
    "cortex_core.methodology_receipt",
]

MUTANT_CODES = {
    "M-wire-caller-remove": "WIRE_RUNG_MISSING",
    "M-methodology-bypass": "METHODOLOGY_SELECT_REQUIRED",
    "M-corpus-citation-omitted": "CORPUS_CITATION_REQUIRED",
    "M-hidden-holdout-exposed": "HIDDEN_HOLDOUT_REFUSED",
    "M-preflight-gate-skipped": "PREFLIGHT_REQUIRED",
    "M-closeout-receipt-omitted": "RECEIPT_INCOMPLETE",
    "M-prose-methodology-substituted": "STRUCTURED_METHODOLOGY_REQUIRED",
    "M-callgraph-deleted-caller": "WIRE_RUNG_MISSING",
}


def _sha256_file(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _contract() -> dict:
    return json.loads((PUBLIC / "migration-contract.json").read_text(encoding="utf-8"))


def _make_controller() -> MechanicalMigrationController:
    return MechanicalMigrationController(ssc_root=SSC, public_dir=PUBLIC, v4_root=V4)


def _drive_good(ctrl):
    contract = _contract()
    ctrl.select_methodology(contract)
    freeze = ctrl.freeze_slice(contract)
    ctrl.read_corpus()
    ctrl.require_preflight("migration test")
    ctrl.run_origin_to_frontier(contract_hash=freeze["migration_contract_hash"])
    return ctrl


@pytest.fixture(scope="module")
def full_run():
    from cortex_v4.control.mechanical_migration import run_mechanical_migration

    return run_mechanical_migration(
        ssc_root=SSC,
        public_dir=PUBLIC,
        v4_root=V4,
        work_root=V4 / "ops-local" / "loop-engineering" / "workspaces",
    )


@pytest.fixture(scope="module")
def wired():
    return _drive_good(_make_controller())


def test_methodology_ids_selected_and_recorded(full_run):
    ids = full_run.methodology_ids
    assert ids == ["M0", "M1", "M3", "M30", "M33"]
    for mid in ("M0", "M1", "M3", "M30"):
        assert mid in ids
    assert full_run.methodology_receipt["methodology_ids"] == ids
    assert full_run.methodology_receipt["required_methodology_ids"] == ["M0", "M1", "M3", "M30"]


def test_classify_migration_from_contract():
    cls = classify_migration(contract={"methodology_ids": ["M0", "M1", "M3", "M30"]})
    assert cls["task_class"] == "methodology-core-migration"
    assert cls["required_methodology_ids"] == ["M0", "M1", "M3", "M30"]


def test_load_m_procedures_from_ssc_inventory():
    ctrl = _drive_good(_make_controller())
    gate = next(g for g in ctrl.gates if g["gate"] == "methodology_select")
    assert gate["ok"] is True
    assert set(gate["detail"]["methodology_ids"]) == set(MIGRATION_METHODOLOGY_IDS)
    assert gate["detail"]["inventory_count"] >= 34


def test_refuses_unknown_procedure_id():
    ctrl = _make_controller()
    with pytest.raises(MigrationGateError) as exc:
        ctrl.select_methodology({"methodology_ids": ["M99"]})
    assert exc.value.code == "METHODOLOGY_INVENTORY_GAP"


def test_freeze_migration_slice_hashes_and_modules():
    freeze = freeze_migration_slice(PUBLIC)
    assert freeze["allowed_modules"] == EXPECTED_MODULES
    assert freeze["migration_contract_hash"] == _sha256_file(PUBLIC / "migration-contract.json")
    assert freeze["objective_checker_hash"] == _sha256_file(PUBLIC / "objective-checker.py")
    assert freeze["tool_contract_hash"] == _sha256_file(PUBLIC / "tool-contract.json")


def test_freeze_matches_contract_modules():
    freeze = freeze_migration_slice(PUBLIC, contract=_contract())
    assert set(freeze["allowed_modules"]) == set(EXPECTED_MODULES)


def test_refuses_hidden_and_A_B_private_paths():
    cases = [
        f"{SSC}\\observations\\loop-engineering\\20260805-migration\\hidden\\A-private.sealed.json",
        "observations/loop-engineering/20260805-migration/hidden/placeholder",
        f"{SSC}/observations/loop-engineering/20260805-migration/A-ssc-migration/x",
        "observations/loop-engineering/20260805-migration/B-v4-migration/closeout.md",
        "blah/A-private/package.json",
    ]
    for path in cases:
        with pytest.raises(MigrationGateError) as exc:
            assert_migration_path_allowed(path)
        assert exc.value.code == "HIDDEN_HOLDOUT_REFUSED", path
    allowed = assert_migration_path_allowed(PUBLIC / "objective-checker.py")
    assert allowed.name == "objective-checker.py"


def test_preflight_required_before_build():
    ctrl = _make_controller()
    contract = _contract()
    ctrl.select_methodology(contract)
    ctrl.freeze_slice(contract)
    ctrl.read_corpus()
    with pytest.raises(MigrationGateError) as exc:
        ctrl._build_receipt()
    assert exc.value.code == "PREFLIGHT_REQUIRED"


def test_named_caller_records_before_wiring():
    ctrl = _make_controller()
    ctrl.require_named_caller()
    gate = next(g for g in ctrl.gates if g["gate"] == "named_caller")
    assert gate["ok"] is True
    assert "run_methodology_origin_chain" in gate["detail"]["named_caller"]
    assert "MechanicalMigrationController" in gate["detail"]["governing_caller"]


def test_origin_to_frontier_passes_and_refuses_missing_rung():
    ctrl = _drive_good(_make_controller())
    assert ctrl.wire["oracle"]["ok"] is True
    with pytest.raises(MigrationGateError) as exc:
        ctrl.run_origin_to_frontier(disable=("receipt",))
    assert exc.value.code == "WIRE_RUNG_MISSING"


def test_call_graph_wire_gate_records_and_passes():
    ctrl = _drive_good(_make_controller())
    gate = next(g for g in ctrl.gates if g["gate"] == "call_graph_wire")
    assert gate["ok"] is True
    assert set(gate["detail"]["rung_methods_called"]) == {
        "preflight",
        "forced_rag_decide",
        "validate_receipt",
    }
    assert gate["detail"]["missing_rungs"] == []
    assert gate["detail"]["source_sha256"]


def test_behavioral_oracle_strict_on_wired(full_run):
    oracle = methodology_origin_oracle(full_run.wire["steps"])
    assert oracle["ok"] is True
    assert not oracle["errors"]


def test_behavioral_oracle_refuses_missing_rung(full_run):
    steps = dict(full_run.wire["steps"])
    steps.pop("receipt", None)
    oracle = methodology_origin_oracle(steps)
    assert oracle["ok"] is False
    assert any("receipt" in e for e in oracle["errors"])


def test_closeout_refused_without_receipts():
    ctrl = _make_controller()
    with pytest.raises(MigrationGateError) as exc:
        ctrl.refuse_closeout_without_receipts(None)
    assert exc.value.code == "RECEIPT_INCOMPLETE"


def test_closeout_checkable_without_prose(full_run):
    receipt = full_run.methodology_receipt
    assert receipt["closeout_checkable_without_prose"] is True
    assert receipt["wire_oracle_ok"] is True
    assert receipt["required_methodology_ids"] == ["M0", "M1", "M3", "M30"]
    assert receipt["corpus_reference_sha256"]
    assert receipt["provenance"]


def test_full_run_clean_passes(full_run):
    assert full_run.ok is True
    assert full_run.stage == "closeout"
    assert full_run.methodology_receipt
    assert full_run.gates
    assert not full_run.refused


def test_mutants_all_killed(full_run):
    assert full_run.mutant_summary["killed"] == 8
    assert full_run.mutant_summary["survived"] == 0
    ids = {m["id"] for m in full_run.mutants}
    assert ids == {
        "M-wire-caller-remove",
        "M-methodology-bypass",
        "M-corpus-citation-omitted",
        "M-hidden-holdout-exposed",
        "M-preflight-gate-skipped",
        "M-closeout-receipt-omitted",
        "M-prose-methodology-substituted",
        "M-callgraph-deleted-caller",
    }


@pytest.mark.parametrize("kind", ["wire", "methodology", "citation", "hidden", "preflight", "receipt", "prose", "callgraph"])
def test_each_mutant_refuses(kind):
    ctrl = _make_controller()
    spec = next(s for s in ctrl.MUTANT_SPECS if s["kind"] == kind)
    result = ctrl.run_mutant(spec)
    assert result["killed"] is True, result
    assert result["code"] == MUTANT_CODES[spec["id"]]


def test_corpus_boundary_escaping_refused():
    ctrl = _make_controller()
    with pytest.raises(MigrationGateError) as exc:
        ctrl.read_corpus(r"D:\claude\outside\not-in-corpus.md")
    assert exc.value.code in (
        "CORPUS_BOUNDARY_VIOLATION",
        "HIDDEN_HOLDOUT_REFUSED",
        "CORPUS_CITATION_REQUIRED",
    )


def test_objective_checker_detects_named_caller():
    checker = load_objective_checker(PUBLIC)
    result = checker.check(V4)
    assert result["ok"] is True
    norm = {str(p).replace("\\", "/") for p in result["named_callers"]}
    assert "cortex_v4/control/mechanical_migration.py" in norm


def test_source_corpus_not_copied_into_v4():
    assert (V4 / "cortex_v4" / "adapters" / "ssc_methodology.py").is_file()
    assert not (V4 / "cortex_core" / "session_preflight.py").exists()
    assert not (V4 / "docs" / "methodology" / "WORK-METHODOLOGIES.md").exists()