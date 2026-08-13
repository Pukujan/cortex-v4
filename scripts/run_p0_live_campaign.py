#!/usr/bin/env python3
"""Run one isolated, real LiteLLM-backed native V4 coding campaign.

The script is intentionally staging-only.  It creates a disposable coding
workspace and a retained run brain, never touches the checkout being tested,
and prints only sanitized campaign metadata.  Missing credentials produce a
machine-readable credential-boundary result; no fixture is promoted to a
successful real run.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cortex_v4.control.litellm_worker import LiteLLMStageWorker, ToolExecution
from cortex_v4.control.run_brain import RunBrain
from cortex_v4.control.staged_runner import StageContext, StageOutcome, StagedRunner
from cortex_v4.control.task_contract import StageSpec, TaskContract
from cortex_v4.transport.litellm import LiteLLMTransport, TimeoutLayers


class InjectedWorkerDeath(RuntimeError):
    """Controlled failure after a durable file mutation."""


def _env(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def _host_label(value: str) -> str:
    parsed = urlsplit(value)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}" if parsed.scheme and parsed.netloc else "configured-route"


def _git_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip()


def _safe_relative(value: Any) -> str:
    text = str(value).replace("\\", "/")
    path = Path(text)
    if not text or path.is_absolute() or any(part == ".." for part in path.parts):
        raise RuntimeError("unsafe workspace path")
    parts = [part for part in path.parts if part not in {"", "."}]
    if not parts:
        raise RuntimeError("unsafe workspace path")
    return "/".join(parts)


class SafeWorkspace:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.injected = False

    def _file(self, relative: str) -> Path:
        path = (self.root / relative).resolve()
        if self.root not in path.parents:
            raise RuntimeError("workspace path escaped root")
        return path

    def execute(self, name: str, arguments: Mapping[str, Any], context: StageContext) -> ToolExecution:
        if name == "read_file":
            relative = _safe_relative(arguments.get("path"))
            allowed = {str(item).replace("\\", "/") for item in context.stage.allowed_write_set if not str(item).startswith("brain/")}
            if relative not in allowed:
                return ToolExecution("read denied by frozen stage boundary")
            path = self._file(relative)
            if not path.is_file():
                return ToolExecution("file_not_found")
            return ToolExecution(path.read_text(encoding="utf-8"))

        if name == "write_file":
            relative = _safe_relative(arguments.get("path"))
            allowed = {str(item).replace("\\", "/") for item in context.stage.allowed_write_set if not str(item).startswith("brain/")}
            if relative not in allowed:
                raise RuntimeError("tool write is outside the frozen stage write set")
            content = arguments.get("content")
            if not isinstance(content, str) or not content.strip():
                raise RuntimeError("write_file requires non-empty text content")
            if len(content.encode("utf-8")) > 128 * 1024:
                raise RuntimeError("write_file content is too large")
            path = self._file(relative)
            raw = content.encode("utf-8")
            digest = hashlib.sha256(raw).hexdigest()
            prior = path.read_bytes() if path.is_file() else None
            if prior is not None and prior != raw:
                # A fenced replacement may propose different bytes after a
                # worker died.  Retain the already durable file and expose only
                # a structured tool result; never overwrite or duplicate it.
                actual_digest = hashlib.sha256(prior).hexdigest()
                journal_name = f"brain/mutation-journal/{context.stage.stage_id}.json"
                journal_ref = context.brain.write_artifact(
                    journal_name,
                    json.dumps({"path": relative, "sha256": actual_digest, "mutation_key": f"{context.stage.stage_id}:{relative}:{actual_digest}"}, sort_keys=True),
                    mutation_key=f"{context.stage.stage_id}:{relative}:{actual_digest}",
                )
                return ToolExecution("existing fenced mutation retained; overwrite denied", artifact_refs=(journal_ref,))
            mutation_count = 0
            if prior is None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
                mutation_count = 1
            journal_name = f"brain/mutation-journal/{context.stage.stage_id}.json"
            journal_ref = context.brain.write_artifact(
                journal_name,
                json.dumps({"path": relative, "sha256": digest, "mutation_key": f"{context.stage.stage_id}:{relative}:{digest}"}, sort_keys=True),
                mutation_key=f"{context.stage.stage_id}:{relative}:{digest}",
            )
            if mutation_count and context.stage.stage_id == "core" and not self.injected:
                self.injected = True
                raise InjectedWorkerDeath("injected worker death after durable mutation")
            return ToolExecution("write accepted", mutation_count=mutation_count, artifact_refs=(journal_ref,))

        if name == "run_tests":
            if context.stage.stage_id != "verify":
                raise RuntimeError("run_tests is not allowed in this stage")
            try:
                completed = subprocess.run(
                    [sys.executable, "-m", "pytest", "-q", "test_slugger.py"],
                    cwd=self.root,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("mechanical test timeout") from exc
            return ToolExecution(f"pytest_returncode={completed.returncode}")

        raise RuntimeError("tool is not approved for this stage")


WRITE_TOOL = {
    "type": "function",
    "function": {
        "name": "write_file",
        "description": "Write one UTF-8 text file in the exact frozen stage write set.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
            "additionalProperties": False,
        },
    },
}

READ_TOOL = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Read the implementation worker's own allowed workspace file to resume an interrupted mutation.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    },
}

RUN_TESTS_TOOL = {
    "type": "function",
    "function": {
        "name": "run_tests",
        "description": "Run the frozen pytest objective checker.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
}


def _prompt(context: StageContext, _context_pack: Mapping[str, Any]) -> str:
    if context.stage.stage_id == "core":
        return (
            "Use the write_file tool exactly once to create slugger.py. Implement a public "
            "function slugify(value: str) -> str. It must Unicode-normalize with NFKD, "
            "then encode with ASCII ignore and decode, lowercase, replace every run of "
            "non-alphanumeric characters with one hyphen, and strip leading/trailing hyphens. "
            "Use only the "
            "Python standard library. Do not put the code only in your reply: the tool mutation "
            "is required. If slugger.py already exists because this is a fenced retry, use "
            "read_file first and preserve the existing valid mutation; do not overwrite it with "
            "different content. Do not create any other file."
        )
    if context.stage.stage_id == "tests":
        return (
            "Use the write_file tool exactly once to create test_slugger.py. Write independent "
            "pytest tests for the frozen slugify contract: ordinary words, whitespace and "
            "punctuation collapsing, and leading/trailing separators. Use these exact examples: "
            "Hello, World! -> hello-world; A__B -> a-b; ---x--- -> x. Do not add extra "
            "Unicode or transliteration requirements. Do not "
            "read or infer implementation-private state; test only the public behavior. Do not "
            "put tests only in your reply: the tool mutation is required."
        )
    return "Use the run_tests tool exactly once. Report only whether the frozen pytest objective passed."


def _stage_contract(base_sha: str, route_label: str, model: str, endpoint: str) -> TaskContract:
    return TaskContract.freeze({
        "objective_id": "cortex-p0-live-slugger",
        "exact_base_sha": base_sha,
        "task_class": "coding",
        "contract_revision": "cortex-p0-native-v1",
        "generation_fence": "cortex-p0-live-slugger:fence-v1",
        "dependency_dag": ["core", "tests", "verify"],
        "acceptance_checks": ["core-ast", "pytest", "objective"],
        "route_policy": {"execution": "litellm-chat-completions", "endpoint": endpoint, "capability": "chat", "model": model, "stream": True, "route_label": route_label},
        "types_interfaces_schemas": ["cortex.v4.run-brain.v1", "cortex.v4.stage-receipt.v1"],
        "stage_specs": [
            {
                "stage_id": "core",
                "assigned_role": "implementation_worker",
                "allowed_read_refs": [],
                "allowed_write_set": ["slugger.py", "brain/mutation-journal/core.json"],
                "acceptance_checks": ["core-ast"],
                "stage_deadline_s": 72,
                "kind": "implementation",
                "requirements": [
                    "public function slugify(value: str) -> str",
                    "NFKD then ASCII-ignore decode",
                    "lowercase and collapse non-alphanumeric runs to hyphen",
                    "strip leading and trailing hyphens",
                ],
            },
            {
                "stage_id": "tests",
                "assigned_role": "test_author",
                "depends_on": ["core"],
                "allowed_read_refs": [],
                "allowed_write_set": ["test_slugger.py", "brain/mutation-journal/tests.json"],
                "acceptance_checks": ["pytest"],
                "stage_deadline_s": 72,
                "kind": "test",
                "requirements": [
                    "Hello, World! -> hello-world",
                    "A__B -> a-b",
                    "---x--- -> x",
                    "no extra Unicode or transliteration requirements",
                ],
                "blind_until_convergence": True,
            },
            {
                "stage_id": "verify",
                "assigned_role": "orchestrator",
                "depends_on": ["tests"],
                "allowed_read_refs": [],
                "allowed_write_set": [],
                "acceptance_checks": ["objective"],
                "stage_deadline_s": 72,
                "kind": "closeout",
            },
        ],
    })


def _pytest_check(workspace: SafeWorkspace) -> dict[str, Any]:
    if not (workspace.root / "slugger.py").is_file() or not (workspace.root / "test_slugger.py").is_file():
        return {"passed": False, "check": "required_files"}
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "test_slugger.py"],
            cwd=workspace.root,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"passed": False, "check": "pytest_timeout"}
    return {"passed": completed.returncode == 0, "check": "pytest", "returncode": completed.returncode}


def _core_check(workspace: SafeWorkspace) -> dict[str, Any]:
    path = workspace.root / "slugger.py"
    if not path.is_file():
        return {"passed": False, "check": "core_file"}
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        compile(tree, str(path), "exec")
    except (OSError, SyntaxError, UnicodeError):
        return {"passed": False, "check": "core_syntax"}
    return {"passed": any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "slugify" for node in ast.walk(tree)), "check": "core_ast"}


def _emit(value: Mapping[str, Any]) -> None:
    print(json.dumps(dict(value), sort_keys=True, separators=(",", ":")))


def main() -> int:
    base_url = _env("P0_LITELLM_URL", "LITELLM_URL", "CORTEX_LITELLM_API_BASE")
    api_key = _env("P0_LITELLM_API_KEY", "CORTEX_LITELLM_API_KEY", "LITELLM_MASTER_KEY")
    model = _env("P0_MODEL", "LITELLM_MODEL")
    if not base_url or not api_key or not model:
        _emit({"status": "NOT_COMPLETE", "blocker": "BLOCKED_CREDENTIAL_BOUNDARY", "real_authenticated_run": False})
        return 2

    route_label = _env("P0_ROUTE_LABEL") or "current-control-staging"
    endpoint = _env("P0_ENDPOINT") or "responses"
    if endpoint not in {"chat", "responses"}:
        _emit({"status": "NOT_COMPLETE", "blocker": "INVALID_ENDPOINT_SELECTION", "real_authenticated_run": False})
        return 2
    timeout_s = float(_env("P0_STAGE_TIMEOUT_S") or "72")
    layers = TimeoutLayers(
        provider_deadline_s=float(_env("P0_PROVIDER_DEADLINE_S") or "90"),
        litellm_request_s=float(_env("P0_LITELLM_REQUEST_S") or "90"),
        client_request_s=timeout_s,
        stage_deadline_s=timeout_s,
        inactivity_watchdog_s=timeout_s,
        campaign_deadline_s=float(_env("P0_CAMPAIGN_DEADLINE_S") or "300"),
    )
    transport = LiteLLMTransport(
        base_url,
        api_key,
        route_label=route_label,
        api_base_label=_host_label(base_url),
        timeout_layers=layers,
    )
    workspace_path = Path(tempfile.mkdtemp(prefix="cortex-p0-live-workspace-"))
    brain_parent = Path(tempfile.mkdtemp(prefix="cortex-p0-live-brain-"))
    workspace = SafeWorkspace(workspace_path)
    contract = _stage_contract(_git_head(), route_label, model, endpoint)
    brain = RunBrain.create(contract.as_dict(), brain_parent, run_id=f"p0-{uuid.uuid4().hex[:12]}", active_lease_seconds=int(timeout_s * 2))

    def worker_factory(stage: StageSpec):
        tools = ([READ_TOOL, WRITE_TOOL] if stage.stage_id == "core" else [WRITE_TOOL]) if stage.stage_id in {"core", "tests"} else [RUN_TESTS_TOOL]
        return LiteLLMStageWorker(
            transport,
            requested_model=model,
            tools=tools,
            tool_executor=workspace.execute,
            prompt_builder=_prompt,
            endpoint=endpoint,
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

    def objective_checker(_brain: RunBrain, _contract: TaskContract) -> Mapping[str, Any]:
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
            "stream": True,
            "temporary_brain_retained": True,
        })
        return 1

    provider_attempts = [
        attempt
        for receipt in result.stage_receipts
        for attempt in receipt.get("provider_attempts", [])
        if isinstance(attempt, Mapping)
    ]
    status = "PASS" if result.status == "PASS" else "NOT_COMPLETE"
    output = {
        "status": status,
        "run_status": result.status,
        "run_id": result.run_id,
        "requested_model": model,
        "actual_models": sorted({str(item.get("actual_model")) for item in provider_attempts if item.get("actual_model")}),
        "route_label": route_label,
        "api_base_label": _host_label(base_url),
        "stream": True,
        "endpoint": endpoint,
        "effective_deadline_s": layers.effective_deadline_s,
        "stage_ids": [stage.stage_id for stage in contract.stages],
        "stage_receipt_count": len(result.stage_receipts),
        "stage_durations_s": [
            {
                "stage_id": receipt.get("stage_id"),
                "duration_s": receipt.get("duration_s"),
                "retry_number": receipt.get("retry_number"),
            }
            for receipt in result.stage_receipts
        ],
        "checkpoint_count": sum(int(brain.stage_status(stage.stage_id).get("checkpoint_count", 0)) for stage in contract.stages),
        "attempt_count": sum(int(brain.stage_status(stage.stage_id).get("attempt_count", 0)) for stage in contract.stages),
        "injected_failure": "worker_death_after_mutation",
        "injected_failure_observed": workspace.injected,
        "skipped_stages": result.skipped_stages,
        "objective_checker": result.closeout.get("objective_check") if result.closeout else None,
        "temporary_brain_retained": True,
        "real_authenticated_run": True,
        "completed_real_long_running_objective": result.status == "PASS",
    }
    _emit(output)
    return 0 if result.status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
