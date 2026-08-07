"""V4 live LiteLLM extended-task run (first-loop B/C live residual).

Attaches V4's public-fixture extended-task shape to the real LiteLLM gateway
through the proven SSC summon path. Deterministic V4 control (B/C/D) proves the
control behavior; this script proves the real gateway attachment on a V4-shaped
workspace with the frozen objective checker. It does not change V4 control code.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
import uuid
from pathlib import Path

SSC_ROOT = Path(r"D:\claude\stupidly-simple-cortex")
V4_ROOT = Path(r"D:\claude\cortex-v4")
LANE = SSC_ROOT / "observations" / "loop-engineering" / "20260805-litellm"
OPS = SSC_ROOT / "ops-local" / "loop-engineering" / "20260805-litellm" / "V4-live"

if str(SSC_ROOT) not in sys.path:
    sys.path.insert(0, str(SSC_ROOT))
if str(V4_ROOT) not in sys.path:
    sys.path.insert(0, str(V4_ROOT))


def _desktop_env() -> dict[str, str]:
    env = dict(os.environ)
    path = Path(r"C:\Users\pujan\OneDrive\Desktop\.env")
    if not path.is_file():
        return env
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ")
        if key:
            env[key] = value.strip().strip('"').strip("'")
    return env


def _route_table(run_dir: Path) -> Path:
    path = run_dir / "litellm-route.json"
    path.write_text(json.dumps({
        "schema": "cortex.model_summon.v1",
        "updated": "observation-2026-08-06",
        "seats": {
            "lite-grok": {
                "tier": "litellm-ckff",
                "model_override": "[grok] grok-4.5",
                "stream": True,
                "force_stream": True,
                "min_max_tokens": 16000,
                "status": "ok",
                "role": "v4-live-worker",
            }
        },
    }, indent=2), encoding="utf-8")
    return path


def _fixture(workspace: Path) -> None:
    (workspace / "inputs").mkdir(parents=True, exist_ok=True)
    (workspace / "artifacts").mkdir(parents=True, exist_ok=True)
    (workspace / "inputs" / "brief.md").write_text(
        "# Extended control-layer audit\n\n"
        "Read all three input files, then write the requested artifacts.\n"
        "evidence.json must be a JSON object with at least the fields "
        "route, completed_steps, and telemetry.\n",
        encoding="utf-8",
    )
    (workspace / "inputs" / "control-contract.md").write_text(
        "The run must preserve evidence, identify gaps, and verify every artifact.\n"
        "artifacts/evidence.json requires keys: route, completed_steps, telemetry.\n",
        encoding="utf-8",
    )
    (workspace / "inputs" / "telemetry-contract.md").write_text(
        "Put a top-level telemetry object on evidence.json covering route, attempt, "
        "checkpoint, heartbeat, retry, and terminal state.\n",
        encoding="utf-8",
    )


def _objective_check(workspace: Path) -> dict:
    checker_path = LANE / "public" / "objective-checker.py"
    spec = importlib.util.spec_from_file_location("loop_objective", checker_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.check(workspace)


def main() -> int:
    from cortex_core.model_summon import summon_agent

    run_id = "v4-live-" + uuid.uuid4().hex[:10]
    run_dir = OPS / run_id
    workspace = run_dir / "workspace"
    runtime = run_dir / "runtime"
    workspace.mkdir(parents=True, exist_ok=True)
    runtime.mkdir(parents=True, exist_ok=True)
    _fixture(workspace)
    route = _route_table(run_dir)

    os.environ["CORTEX_AGENT_RUNS_DIR"] = str(runtime)
    os.environ.update(_desktop_env())

    prompt = (
        "Perform this bounded extended user task. Use the available tools and keep working; "
        "do not answer with a plan or narration. Read inputs/brief.md, "
        "inputs/control-contract.md, and inputs/telemetry-contract.md. Then write exactly "
        "artifacts/review.md, artifacts/evidence.json, and artifacts/checks.txt. "
        "The review must summarize the contract. evidence.json must be valid JSON with at "
        "least the fields route, completed_steps, and telemetry (telemetry is a nested "
        "object covering attempt, checkpoint, heartbeat, retry, and terminal_state). "
        "checks.txt must list each artifact path. "
        "Read back each artifact and verify it before the final response."
    )

    started = time.time()
    result = summon_agent(
        "lite-grok",
        prompt,
        max_tokens=16000,
        path=str(route),
        workspace=str(workspace),
        write_set=[str(workspace)],
        _dispatch_tier="litellm-ckff",
        model_override="[grok] grok-4.5",
    )
    objective = _objective_check(workspace)

    deck = {
        "schema": "cortex.observation.deck.v1",
        "run_id": run_id,
        "generated_at": time.time(),
        "authority": "human_observation_only",
        "result": {
            "ok": result.ok,
            "run_id": result.run_id,
            "steps": len(result.steps),
            "tools": list(result.tools_used),
            "final_preview": (result.final or "")[:500],
            "objective_ok": bool(objective["ok"]),
        },
        "route_class": "litellm-ckff/[grok] grok-4.5",
        "duration_s": round(time.time() - started, 2),
        "objective_check": objective,
        "telemetry": {"local_events": True, "remote_receipt": "not_queryable"},
    }
    (OPS / "decks").mkdir(parents=True, exist_ok=True)
    (OPS / "decks" / f"{run_id}.json").write_text(json.dumps(deck, indent=2), encoding="utf-8")

    lane_b = LANE / "B-v4-replay"
    lane_c = LANE / "C-v4-mechanical"
    receipt = {
        "schema": "cortex.loop_engineering.v4_live_receipt.v1",
        "run_id": run_id,
        "fixture_id": "20260805-litellm",
        "result": {"ok": result.ok, "objective_ok": bool(objective["ok"])},
        "workspace": str(workspace),
        "deck": f"ops-local/loop-engineering/20260805-litellm/V4-live/decks/{run_id}.json",
        "route_class": "litellm-ckff/[grok] grok-4.5",
        "objective_check": objective,
        "recorded_at": time.time(),
        "note": "V4-shaped workspace run through the real LiteLLM gateway via SSC summon path.",
    }
    (OPS / f"{run_id}.receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")

    # Reflect into B and C lane evidence (live residual).
    for lane_dir in (lane_b, lane_c):
        manifest_path = lane_dir / "manifest.json"
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["v4_live"] = {
                "status": "PASS" if result.ok and objective["ok"] else "FAIL",
                "run_id": run_id,
                "objective_ok": bool(objective["ok"]),
            }
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(json.dumps({
        "run_id": run_id,
        "ok": result.ok,
        "objective_ok": bool(objective["ok"]),
        "tools": list(result.tools_used),
        "objective": objective,
    }, indent=2))
    return 0 if result.ok and objective["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
