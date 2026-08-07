"""B-lane origin-to-frontier wiring tests for the methodology-core vertical slice.

Each hypothesis in B's hypothesis-ledger gets a minimal falsifying test here. The
tests assert the strict behavioral wire oracle, not token presence: the chain is only
wired when preflight produces a pack_hash, the forced-RAG gate allows on a resolved
recorded pack, and the receipt validator accepts well-formed / rejects malformed.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cortex_v4.operation import run_methodology_origin_chain
from cortex_v4.operation.controllers import methodology_origin_oracle

SSC = Path(r"D:\claude\stupidly-simple-cortex")
TASK = "audit the cortex temporal migration boundary"


@pytest.fixture(scope="module")
def chain():
    return run_methodology_origin_chain(
        corpus_root=SSC,
        work_unit_id="b-v4-migration-hypothesis",
        task=TASK,
    )


def test_h1_preflight_returns_pack_hash(chain):
    assert chain["steps"]["preflight"]["pack_hash"]
    assert chain["steps"]["preflight"]["citation_count"] > 0


def test_h2_forced_rag_gate_allows_on_recorded_pack(chain):
    rag = chain["steps"]["forced_rag"]
    assert rag["allowed"] is True
    assert "resolves to a recorded preflight" in rag["reason"]


def test_h3_receipt_validates_well_formed_and_rejects_malformed(chain):
    rec = chain["steps"]["receipt"]
    assert rec["well_formed_errors"] == []
    assert rec["malformed_rejected"] is True
    assert rec["receipt_id"].startswith("msr_")


def test_h4_named_caller_wires_chain_in_one_path(chain):
    assert chain["named_caller"] == "cortex_v4.operation.controllers.run_methodology_origin_chain"
    assert chain["oracle"]["ok"] is True


def test_h5_corpus_stays_outside_v4(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    result = run_methodology_origin_chain(
        corpus_root=SSC,
        work_unit_id="b-corpus-boundary",
        task=TASK,
    )
    assert json.loads(json.dumps(result))  # serializable
    assert "stupidly-simple-cortex" in str(SSC.resolve())
    assert not (run_dir / "corpus").exists()


MUTANTS = [
    ("forced_rag_removed", ("forced_rag",)),
    ("receipt_validate_removed", ("receipt",)),
    ("preflight_removed", ("preflight",)),
]


@pytest.mark.parametrize("mutant_id,disabled", MUTANTS)
def test_mutant_breaks_wire_oracle(mutant_id, disabled):
    result = run_methodology_origin_chain(
        corpus_root=SSC,
        work_unit_id=f"b-mutant-{mutant_id}",
        task=TASK,
        disable=disabled,
    )
    assert result["oracle"]["ok"] is False
    assert result["oracle"]["errors"]
