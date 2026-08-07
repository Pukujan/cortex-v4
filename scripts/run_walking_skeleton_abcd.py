"""Compose the migrated slices into one deterministic V4 walking-skeleton replay."""
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

from cortex_v4.operation import run_fixture_operation  # noqa: E402

WORK = ROOT / "observations" / "replays" / "walking-skeleton-abcd-20260805"
DECK = ROOT / "observations" / "decks" / "walking-skeleton-abcd-20260805.json"
TASK = "audit the cortex temporal migration boundary"


def _source_fixture(workspace: Path) -> dict:
    (workspace / "docs").mkdir(parents=True, exist_ok=True)
    preflight = __import__("cortex_core.session_preflight", fromlist=["*"]).run_preflight(
        TASK, workspace=SSC
    )
    context = __import__("cortex_core.knowledge", fromlist=["*"])
    source_path = SSC / "docs" / "methodology" / "WORK-METHODOLOGIES.md"
    file_hash = __import__("hashlib").sha256(source_path.read_bytes()).hexdigest()
    context_hash = __import__("hashlib").sha256(file_hash.encode("ascii")).hexdigest()
    summon = __import__("cortex_core.model_summon", fromlist=["*"])
    spec = summon.resolve_summon("kimi")
    trace = __import__("cortex_core.trace_capture", fromlist=["*"])
    otel = __import__("cortex_core.otel", fromlist=["*"])
    dashboard = __import__("cortex_core.observability_dashboard", fromlist=["*"])
    record = trace.TraceRecord(
        task=TASK, model=spec.model_override or spec.tier, run_id="walking-A",
        task_id="walking-A:task-1", route_id=f"{spec.tier}:{spec.model_override}",
        prompt_id="walking-A:prompt-1", role="executor", output="fixture complete",
        gate_verdict="OBSERVED", extra={"risk_tier": "low"},
    )
    trace.capture(record, workspace=workspace)
    ledger = workspace / "telemetry" / "metrics.jsonl"
    ledger.parent.mkdir(exist_ok=True)
    with otel.gen_ai_span("v4.walking_skeleton", env={"CORTEX_METRICS_LEDGER": str(ledger)},
                          session_id="walking-A", task_id=record.task_id,
                          route_id=record.route_id, model=record.model):
        pass
    prior = os.environ.get("CORTEX_METRICS_LEDGER")
    os.environ["CORTEX_METRICS_LEDGER"] = str(ledger)
    try:
        snapshot = dashboard.collect_observability_snapshot(workspace)
    finally:
        if prior is None:
            os.environ.pop("CORTEX_METRICS_LEDGER", None)
        else:
            os.environ["CORTEX_METRICS_LEDGER"] = prior
    # `context` is intentionally imported above to prove the source search module is loaded;
    # the frozen context oracle is the exact file hash used by this fixture.
    _ = context
    return {
        "context_hash": context_hash,
        "methodology_pack_hash": preflight.pack_hash,
        "seat": spec.seat, "tier": spec.tier, "model_override": spec.model_override,
        "status": "fixture_complete", "observation_overall": snapshot["overall"],
        "source_corpus": str(SSC.resolve()),
    }


def main() -> int:
    import cortex_core.langfuse_sink as langfuse
    import cortex_core.telemetry as telemetry
    import cortex_core.observability_dashboard as dashboard
    old_lf, old_tel, old_dash = langfuse.enabled, telemetry.enabled, dashboard._langfuse_projection
    langfuse.enabled = lambda *a, **k: False
    telemetry.enabled = lambda *a, **k: False
    dashboard._langfuse_projection = lambda env: {
        "configured": False, "reachable": False, "recent_traces": 0,
        "correlated_traces": 0, "plane2_traces": 0, "error": None,
    }
    try:
        a = _source_fixture(WORK / "A")
        b_result = run_fixture_operation(TASK, run_id="walking-B", managed_root=WORK / "B" , corpus_root=SSC)
        b = b_result["receipt"]
        try:
            run_fixture_operation(TASK, run_id="walking-C", managed_root=WORK / "C", corpus_root=SSC,
                                  seat="not-an-owner-seat")
        except KeyError:
            c = {"unknown_seat_refused": True}
        else:
            c = {"unknown_seat_refused": False}
        c["corpus_not_copied"] = not (Path(b_result["managed_run"]) / "corpus").exists()
    finally:
        langfuse.enabled, telemetry.enabled, dashboard._langfuse_projection = old_lf, old_tel, old_dash

    deck = {
        "schema": "cortex.v4.migration_observation.v1",
        "source": "SSC composed walking skeleton",
        "status": "candidate_for_ssc_holdout",
        "axes": {
            "A": {"status": "source_observed", "receipt": a},
            "B": {"status": "v4_composed_observed", "receipt": b},
            "C": {"status": "negative_control", **c},
            "D": {"status": "awaiting_external_ssc_holdout"},
        },
    }
    DECK.parent.mkdir(parents=True, exist_ok=True)
    DECK.write_text(json.dumps(deck, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(deck, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
