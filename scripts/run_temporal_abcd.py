"""Produce the V4 temporal A/B/C/D replay deck.

The deck contains behavior receipts only. SSC's holdout evaluator supplies the final golden
comparison separately; this script never reads the SSC corpus or source observation answers.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cortex_v4.control import LongRunningController, ScriptedProvider
from cortex_v4.control.temporal import _kill, start, status


RUN_ROOT = ROOT / "observations" / "runs"
DECK = ROOT / "observations" / "decks" / "v4-temporal-abcd-20260805.json"


def _wait_for_completed(state_path: str, timeout_s: float = 40.0) -> dict:
    end = time.time() + timeout_s
    while time.time() < end:
        current = status(state_path)
        if current["status"] == "completed":
            return current
        time.sleep(0.05)
    return status(state_path)


def main() -> int:
    a_provider = ScriptedProvider(cooperative=False)
    a = LongRunningController(timeout_s=0.02, cancel_grace_s=0.01,
                              max_retries=1, legacy_overlap=True).run(a_provider)
    b_provider = ScriptedProvider(cooperative=True)
    b = LongRunningController(timeout_s=0.02, cancel_grace_s=0.3,
                              max_retries=1).run(b_provider)

    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    created = start(RUN_ROOT, task_id="v4-temporal-c-120", total_steps=120,
                    max_recoveries=2, background=True)
    state_path = created["state_path"]
    interrupted_pid = None
    deadline = time.time() + 20
    while time.time() < deadline:
        current = status(state_path)
        if current["cursor"]["step"] >= 8 and current.get("worker_pid"):
            interrupted_pid = current["worker_pid"]
            break
        time.sleep(0.05)
    if interrupted_pid is not None:
        _kill(int(interrupted_pid))
    c = _wait_for_completed(state_path)
    workspace = Path(c["workspace"])
    d = {
        "status": "candidate_for_ssc_holdout",
        "axes": {
            "A": {
                "status": "observed_failure",
                "overlap": a_provider.max_active == 2,
                "false_success": a.ok,
            },
            "B": {
                "status": "repaired",
                "overlap": b_provider.max_active > 1,
                "ok": b.ok,
            },
            "C": {
                "status": c["status"],
                "cursor_step": c["cursor"]["step"],
                "generation": c["cursor"]["generation"],
                "recovery_count": c["recovery_count"],
                "artifact_count": len(list(workspace.glob("step-*.txt"))),
                "interrupted_worker": interrupted_pid is not None,
            },
            "D": {
                "status": "awaiting_external_ssc_holdout",
                "comparison": "behavioral invariants only; no SSC answers copied",
            },
        },
        "source_boundary": "SSC-A hidden holdout evaluator",
    }
    DECK.parent.mkdir(parents=True, exist_ok=True)
    DECK.write_text(json.dumps(d, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(d, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
