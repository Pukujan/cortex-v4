from __future__ import annotations

import json
from pathlib import Path

from cortex_v4.operation import run_fixture_operation


SSC = Path(r"D:\claude\stupidly-simple-cortex")


def test_walking_skeleton_composes_verified_boundaries(tmp_path: Path, monkeypatch):
    from cortex_v4.adapters.ssc_import import import_ssc
    import_ssc("cortex_core.langfuse_sink", root=SSC)
    import_ssc("cortex_core.telemetry", root=SSC)
    monkeypatch.setattr("cortex_core.langfuse_sink.enabled", lambda *a, **k: False)
    monkeypatch.setattr("cortex_core.telemetry.enabled", lambda *a, **k: False)
    result = run_fixture_operation(
        "audit the cortex temporal migration boundary",
        run_id="walking-skeleton-test",
        managed_root=tmp_path / "managed" / "runs",
        corpus_root=SSC,
    )
    receipt = result["receipt"]
    assert receipt["status"] == "fixture_complete"
    assert receipt["source_corpus"] == str(SSC.resolve())
    assert result["snapshot"]["local"]["traces"]["correlated_runs"] == 1
    run_dir = Path(result["managed_run"])
    assert (run_dir / "receipt.json").is_file()
    assert json.loads((run_dir / "context.json").read_text())["context_hash"] == receipt["context_hash"]
    assert not (run_dir / "corpus").exists()
