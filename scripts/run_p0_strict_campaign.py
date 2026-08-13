#!/usr/bin/env python3
"""Run the strict zero-retry, chat-streaming Cortex V4 P0 campaign."""
from __future__ import annotations

import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from run_p0_live_campaign import (
    READ_TOOL,
    RUN_TESTS_TOOL,
    WRITE_TOOL,
    SafeWorkspace,
    _core_check,
    _emit,
    _env,
    _git_head,
    _host_label,
    _prompt,
    _pytest_check,
    _stage_contract,
)
from cortex_v4.control.strict_litellm_worker import StrictLiteLLMStageWorker
from cortex_v4.control.run_brain import RunBrain
from cortex_v4.control.staged_runner import StageContext, StageOutcome, StagedRunner
from cortex_v4.control.task_contract import StageSpec
from cortex_v4.transport.litellm import TimeoutLayers
from cortex_v4.transport.strict_litellm import STRICT_PROFILE, StrictLiteLLMTransport

STRICT_STAGE_TIMEOUT_S = 72.0
STRICT_LITELLM_REQUEST_S = 120.0


def main() -> int:
    base_url = _env("P0_LITELLM_URL", "LITELLM_URL", "CORTEX_LITELLM_API_BASE")
    api_key = _env("P0_LITELLM_API_KEY", "CORTEX_LITELLM_API_KEY", "LITELLM_MASTER_KEY")
    model = _env("P0_MODEL", "LITELLM_MODEL")
    if not base_url or not api_key or not model:
        _emit({"status": "NOT_COMPLETE", "blocker": "BLOCKED_CREDENTIAL_BOUNDARY", "real_authenticated_run": False})
        return 2

    profile = _env("P0_LITELLM_PROFILE") or STRICT_PROFILE
    if profile != STRICT_PROFILE:
        _emit({
            "status": "NOT_COMPLETE",
            "blocker": "UNAPPROVED_STRICT_PROFILE",
            "required_profile": STRICT_PROFILE,
            "real_authenticated_run": False,
        })
        return 2

    route_label = _env("P0_ROUTE_LABEL") or "current-control-staging"
    provider_deadline_raw = _env("P0_PROVIDER_DEADLINE_S")
    provider_deadline = float(provider_deadline_raw) if provider_deadline_raw else None
    campaign_deadline = float(_env("P0_CAMPAIGN_DEADLINE_S") or "300")
    layers = TimeoutLayers(
        provider_deadline_s=provider_deadline,
        litellm_request_s=STRICT_LITELLM_REQUEST_S,
        client_request_s=STRICT_STAGE_TIMEOUT_S,
        stage_deadline_s=STRICT_STAGE_TIMEOUT_S,
        inactivity_watchdog_s=STRICT_STAGE_TIMEOUT_S,
        campaign_deadline_s=campaign_deadline,
    )
    transport = StrictLiteLLMTransport(
        base_url,
        api_key,
        route_label=route_label,
        api_base_label=_host_label(base_url),
        timeout_layers=layers,
        config_profile=profile,
    )

    workspace_path = Path(tempfile.mkdtemp(prefix="cortex-p0-strict-workspace-"))
    brain_parent = Path(tempfile.mkdtemp(prefix="cortex-p0-strict-brain-"))
    workspace = SafeWorkspace(workspace_path)
    contract = _stage_contract(_git_head(), route_label, model, "chat")
    brain = RunBrain.create(
        contract.as_dict(),
        brain_parent,
        run_id=f"p0-strict-{uuid.uuid4().hex[:12]}",
        active_lease_seconds=int(STRICT_STAGE_TIMEOUT_S * 2),
    )

    def worker_factory(stage: StageSpec):
        tools = (
            [READ_TOOL, WRITE_TOOL] if stage.stage_id == "core" else [WRITE_TOOL]
        ) if stage.stage_id in {"core", "tests"} else [RUN_TESTS_TOOL]
        return StrictLiteLLMStageWorker(
            transport,
            requested_model=model,
            tools=tools,
            tool_executor=workspace.execute,
            prompt_builder=_prompt,
        )

    def checker(context: StageContext, outcome: StageOutcome) -> Mapping[str, Any]:
        if context.stage.stage_id == "core":
            return _core_check(workspace)
        if context.stage.stage_id == "tests":
            result = _pytest_check(workspace)
            result["model_tool_calls"] = outcome.tool_call_count
            return result
        result = _pytest_check(workspace)
        result["model_tool_calls"] = outcome.tool_call_count
        result["required_model_tool"] = outcome.tool_call_count >= 1
        result["passed"] = bool(result.get("passed")) and outcome.tool_call_count >= 1
        return result

    def objective_checker(_brain: RunBrain, _contract) -> Mapping[str, Any]:
        result = _pytest_check(workspace)
        result["core"] = _core_check(workspace)
        result["passed"] = bool(result.get("passed")) and bool(result["core"].get("passed"))
        return result

    runner = StagedRunner(
        contract,
        brain,
        worker_factory=worker_factory,
        checkers={stage.stage_id: checker for stage in contract.stages},
        objective_checker=objective_checker,
        max_stage_attempts=3,
        require_real_provider=True,
        retry_backoff_s=float(_env("P0_RETRY_BACKOFF_S") or "4"),
    )

    try:
        result = runner.run()
    except Exception:
        _emit({
            "status": "NOT_COMPLETE",
            "blocker": "CAMPAIGN_RUNTIME_FAILURE",
            "real_authenticated_run": True,
            "requested_model": model,
            "route_label": route_label,
            "config_profile": profile,
            "stream": True,
            "endpoint": "chat",
            "temporary_brain_retained": True,
        })
        return 1

    provider_attempts = [
        attempt
        for receipt in result.stage_receipts
        for attempt in receipt.get("provider_attempts", [])
        if isinstance(attempt, Mapping)
    ]
    output = {
        "status": "PASS" if result.status == "PASS" else "NOT_COMPLETE",
        "run_status": result.status,
        "run_id": result.run_id,
        "requested_model": model,
        "actual_models": sorted({str(item.get("actual_model")) for item in provider_attempts if item.get("actual_model")}),
        "route_label": route_label,
        "api_base_label": _host_label(base_url),
        "config_profile": profile,
        "transport_retries": 0,
        "semantic_fallbacks": False,
        "stream": True,
        "endpoint": "chat",
        "effective_deadline_s": layers.effective_deadline_s,
        "timeout_layers": layers.values(),
        "stage_ids": [stage.stage_id for stage in contract.stages],
        "stage_receipt_count": len(result.stage_receipts),
        "checkpoint_count": sum(int(brain.stage_status(stage.stage_id).get("checkpoint_count", 0)) for stage in contract.stages),
        "attempt_count": sum(int(brain.stage_status(stage.stage_id).get("attempt_count", 0)) for stage in contract.stages),
        "provider_receipt_count": len(provider_attempts),
        "injected_failure": "worker_death_after_mutation",
        "injected_failure_observed": workspace.injected,
        "objective_checker": result.closeout.get("objective_check") if result.closeout else None,
        "temporary_brain_retained": True,
        "real_authenticated_run": True,
        "completed_real_long_running_objective": result.status == "PASS",
    }
    _emit(output)
    return 0 if result.status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
