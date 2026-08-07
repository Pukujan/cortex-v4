"""Run local telemetry/observation A/B/C/D without contacting remote telemetry sinks."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SSC = Path(r"D:\claude\stupidly-simple-cortex")
for path in (ROOT, SSC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from cortex_v4.adapters import SSCObservabilityAdapter  # noqa: E402

DECK = ROOT / "observations" / "decks" / "observability-abcd-20260805.json"
WORK = ROOT / "observations" / "replays" / "observability-abcd-20260805"


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


def _contract(snapshot: dict) -> dict:
    return {
        "schema": snapshot["schema"],
        "overall": snapshot["overall"],
        "verdict_authority": snapshot["verdict_authority"],
        "contract": snapshot["contract"],
        "trace_records": snapshot["local"]["traces"]["records"],
        "correlated_runs": snapshot["local"]["traces"]["correlated_runs"],
        "span_receipts": snapshot["otel"]["local_span_receipts"],
    }


def _run_source(workspace: Path) -> tuple[dict, str]:
    (workspace / "docs").mkdir(parents=True, exist_ok=True)
    trace = __import__("cortex_core.trace_capture", fromlist=["*"])
    otel = __import__("cortex_core.otel", fromlist=["*"])
    dashboard = __import__("cortex_core.observability_dashboard", fromlist=["*"])
    trace.capture(trace.TraceRecord(**_record("run-source")), workspace=workspace)
    ledger = workspace / "metrics.jsonl"
    with otel.gen_ai_span("fixture", env={"CORTEX_METRICS_LEDGER": str(ledger)},
                          session_id="run-source", task_id="task-1", route_id="route-fixture") as span:
        span.add_tool_call(2).set_usage(input_tokens=3, output_tokens=4)
    os.environ["CORTEX_METRICS_LEDGER"] = str(ledger)
    snapshot = dashboard.collect_observability_snapshot(workspace)
    return snapshot, dashboard.render_html(snapshot)


def main() -> int:
    # The replay measures local receipts only. Remote Langfuse/OTel are intentionally excluded
    # from this fixture so network state cannot become a false migration oracle.
    import cortex_core.langfuse_sink as langfuse
    import cortex_core.telemetry as telemetry
    import cortex_core.observability_dashboard as dashboard
    langfuse_enabled = langfuse.enabled
    telemetry_enabled = telemetry.enabled
    dashboard_langfuse = dashboard._langfuse_projection
    langfuse.enabled = lambda *a, **k: False
    telemetry.enabled = lambda *a, **k: False
    dashboard._langfuse_projection = lambda env: {
        "configured": False, "reachable": False, "recent_traces": 0,
        "correlated_traces": 0, "plane2_traces": 0, "error": None,
    }
    try:
        a_ws = WORK / "A"
        b_ws = WORK / "B"
        a_snapshot, a_html = _run_source(a_ws)
        adapter = SSCObservabilityAdapter(SSC)
        ledger = b_ws / "metrics.jsonl"
        (b_ws / "docs").mkdir(parents=True, exist_ok=True)
        adapter.capture(_record("run-adapter"), workspace=b_ws)
        with adapter.span("fixture", env={"CORTEX_METRICS_LEDGER": str(ledger)},
                          session_id="run-adapter", task_id="task-1", route_id="route-fixture") as span:
            span.add_tool_call(2).set_usage(input_tokens=3, output_tokens=4)
        os.environ["CORTEX_METRICS_LEDGER"] = str(ledger)
        b_snapshot = adapter.snapshot(workspace=b_ws)
        b_html = adapter.render(b_snapshot)
        c_ws = WORK / "C"
        (c_ws / "docs").mkdir(parents=True, exist_ok=True)
        os.environ["CORTEX_METRICS_LEDGER"] = str(c_ws / "metrics.jsonl")
        empty = adapter.snapshot(workspace=c_ws)
    finally:
        langfuse.enabled = langfuse_enabled
        telemetry.enabled = telemetry_enabled
        dashboard._langfuse_projection = dashboard_langfuse

    deck = {
        "schema": "cortex.v4.migration_observation.v1",
        "source": "SSC local trace, OTel ledger, Langfuse boundary, observation deck",
        "status": "candidate_for_ssc_holdout",
        "axes": {
            "A": {"status": "source_observed", "contract": _contract(a_snapshot),
                  "html_markers": ["Cortex observability deck" in a_html,
                                   "/api/observability" in a_html]},
            "B": {"status": "v4_adapter_observed", "contract": _contract(b_snapshot),
                  "html_markers": ["Cortex observability deck" in b_html,
                                   "/api/observability" in b_html]},
            "C": {"status": "negative_control", "empty_is_blocked": empty["overall"] == "BLOCKED",
                  "verdict_authority_false": empty["verdict_authority"] is False},
            "D": {"status": "awaiting_external_ssc_holdout"},
        },
    }
    DECK.parent.mkdir(parents=True, exist_ok=True)
    DECK.write_text(json.dumps(deck, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(deck, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
