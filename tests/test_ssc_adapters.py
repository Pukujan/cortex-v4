from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from cortex_v4.adapters import SSCCorpusAdapter, SSCMethodologyAdapter


SSC = Path(r"D:\claude\stupidly-simple-cortex")


def test_methodology_adapter_reads_canonical_manual_and_preflight():
    adapter = SSCMethodologyAdapter(SSC)
    manual = adapter.manual_text()
    assert "M32" in manual
    assert adapter.procedure_ids() == [f"M{i}" for i in range(34)]
    result = adapter.preflight("audit the cortex temporal migration boundary", workspace=SSC)
    assert result["pack_hash"]
    assert result["workspace"]


def test_corpus_adapter_preserves_exact_bytes_and_context_hash():
    adapter = SSCCorpusAdapter(SSC)
    ref = "docs/methodology/WORK-METHODOLOGIES.md"
    result = adapter.read_context([ref])
    expected = hashlib.sha256((SSC / ref).read_bytes()).hexdigest()
    assert result["files"][0]["sha256"] == expected
    assert result["context_hash"]


def test_corpus_adapter_refuses_escape():
    adapter = SSCCorpusAdapter(SSC)
    with pytest.raises(PermissionError):
        adapter.read_context(["../outside-ssc.txt"])


def test_methodology_adapter_rejects_invalid_receipt():
    adapter = SSCMethodologyAdapter(SSC)
    errors = adapter.validate_receipt({"schema": "wrong"})
    assert errors
