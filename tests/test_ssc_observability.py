from __future__ import annotations

from pathlib import Path

from cortex_v4.adapters import SSCObservabilityAdapter
from cortex_v4.adapters.ssc_import import import_ssc


SSC = Path(r"D:\claude\stupidly-simple-cortex")


def _record(run_id: str) -> dict:
    return {
        "task": "observe migration slice",
        "model": "fixture-model",
        "run_id": run_id,
        "task_id": "task-1",
        "route_id": "route-fixture",
        "prompt_id": "prompt-1",
        "role": "executor",
        "output": "fixture complete",
        "gate_verdict": "OBSERVED",
        "extra": {"risk_tier": "low"},
    }


def test_observability_adapter_captures_and_projects_local_receipts(tmp_path: Path, monkeypatch):
    # Load the controlled external implementation before monkeypatching its
    # optional remote sinks.  The adapter imports SSC lazily at its boundary.
    import_ssc("cortex_core.langfuse_sink", root=SSC)
    import_ssc("cortex_core.telemetry", root=SSC)
    adapter = SSCObservabilityAdapter(SSC)
    (tmp_path / "docs").mkdir()
    monkeypatch.setattr("cortex_core.langfuse_sink.enabled", lambda *a, **k: False)
    monkeypatch.setattr("cortex_core.telemetry.enabled", lambda *a, **k: False)
    ledger = tmp_path / "metrics.jsonl"
    assert adapter.capture(_record("run-1"), workspace=tmp_path)
    with adapter.span("fixture", env={"CORTEX_METRICS_LEDGER": str(ledger)},
                      session_id="run-1", task_id="task-1", route_id="route-fixture") as span:
        span.add_tool_call(2).set_usage(input_tokens=3, output_tokens=4)
    monkeypatch.setenv("CORTEX_METRICS_LEDGER", str(ledger))
    snapshot = adapter.snapshot(workspace=tmp_path)
    assert snapshot["schema"] == "cortex.observability.dashboard.v1"
    assert snapshot["local"]["traces"]["records"] == 1
    assert snapshot["local"]["traces"]["correlated_runs"] == 1
    assert snapshot["otel"]["local_span_receipts"] == 1
    rendered = adapter.render(snapshot)
    assert "Cortex observability" in rendered
    assert "Local span receipts" in rendered


def test_empty_observation_is_not_a_pass(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("cortex_core.langfuse_sink._cfg", lambda *a, **k: None)
    (tmp_path / "docs").mkdir()
    monkeypatch.delenv("CORTEX_METRICS_LEDGER", raising=False)
    snapshot = SSCObservabilityAdapter(SSC).snapshot(workspace=tmp_path)
    assert snapshot["overall"] == "BLOCKED"
    assert snapshot["verdict_authority"] is False
