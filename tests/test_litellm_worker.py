from __future__ import annotations

from pathlib import Path

from cortex_v4.control.litellm_worker import LiteLLMStageWorker, ToolExecution
from cortex_v4.control.run_brain import RunBrain
from cortex_v4.control.staged_runner import StageContext
from cortex_v4.control.task_contract import TaskContract
from cortex_v4.transport.litellm import ChatResult, LiteLLMRequestReceipt


def task() -> TaskContract:
    return TaskContract.freeze({
        "objective_id": "worker-objective",
        "exact_base_sha": "c" * 40,
        "task_class": "coding",
        "contract_revision": "worker-v1",
        "generation_fence": "worker:fence",
        "dependency_dag": ["implementation"],
        "acceptance_checks": ["objective"],
        "stage_specs": [{
            "stage_id": "implementation",
            "assigned_role": "implementation_worker",
            "allowed_write_set": ["implementation/out.txt"],
            "acceptance_checks": ["implementation"],
            "stage_deadline_s": 20,
            "kind": "implementation",
        }],
    })


def receipt() -> LiteLLMRequestReceipt:
    return LiteLLMRequestReceipt(
        schema="test", requested_model="requested", actual_model="actual",
        route_label="route", api_base_label="base", endpoint_kind="chat/completions",
        stream=True, start_at=1, end_at=2, duration_s=1, status_code=200,
        request_id="r", tool_call_count=0, usable_output=True,
        result_classification="success", timeout_layer=None, timeout_values={"client_request_s": 20},
    )


class FakeTransport:
    route_label = "route"

    def __init__(self):
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs["messages"])
        if len(self.calls) == 1:
            return ChatResult(
                text="",
                actual_model="actual",
                tool_calls=({"id": "call-1", "type": "function", "function": {"name": "write_file", "arguments": '{"content":"ok"}'}},),
                finish_reason="tool_calls",
                receipt=receipt(),
            )
        return ChatResult("finished", "actual", (), "stop", receipt())


def test_worker_uses_scoped_brain_and_executes_tools(tmp_path: Path):
    contract = task()
    brain = RunBrain.create(contract.as_dict(), tmp_path, run_id="worker-run")
    handle = brain.handle("implementation", "implementation_worker", 0)
    context = StageContext(
        run_id=brain.run_id,
        objective_id=contract.objective_id,
        contract_hash=contract.contract_hash,
        contract_revision=contract.contract_revision,
        exact_base_sha=contract.exact_base_sha,
        stage=contract.stage("implementation"),
        attempt_id="attempt-1",
        generation=0,
        dependency_results={},
        idempotency_key="worker-objective:implementation:0",
        brain=handle,
    )

    def execute(name, arguments, stage_context):
        assert name == "write_file"
        ref = stage_context.brain.write_artifact("implementation/out.txt", arguments["content"], mutation_key="out-v1")
        return ToolExecution("written", mutation_count=1, artifact_refs=(ref,))

    worker = LiteLLMStageWorker(
        FakeTransport(),
        requested_model="requested",
        tools=[{"type": "function", "function": {"name": "write_file"}}],
        tool_executor=execute,
    )
    outcome = worker(context)
    assert outcome.provider_call is True
    assert outcome.actual_model == "actual"
    assert outcome.tool_call_count == 1
    assert outcome.mutation_count == 1
    assert outcome.artifact_refs == ("artifact://implementation/out.txt",)
    assert len(outcome.provider_receipts) == 2
