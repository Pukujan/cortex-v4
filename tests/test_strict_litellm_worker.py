from __future__ import annotations

from types import SimpleNamespace

from cortex_v4.control.litellm_worker import ToolExecution
from cortex_v4.control.strict_litellm_worker import StrictLiteLLMStageWorker
from cortex_v4.transport.litellm import ChatResult


class Receipt:
    def as_dict(self):
        return {
            "requested_model": "m",
            "actual_model": "m",
            "config_profile": "p0-local-staging-zero-retry-v1",
            "transport_retries": 0,
        }


class FakeTransport:
    route_label = "strict"
    def __init__(self):
        self.calls = 0
    def chat(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return ChatResult("", "m", ({
                "id": "read-1",
                "type": "function",
                "function": {"name": "read_file", "arguments": "{}"},
            },), "tool_calls", Receipt())
        if self.calls == 2:
            return ChatResult("", "m", ({
                "id": "write-1",
                "type": "function",
                "function": {"name": "write_file", "arguments": "{}"},
            },), "tool_calls", Receipt())
        return ChatResult("done", "m", (), "stop", Receipt())


class Brain:
    def __init__(self):
        self.heartbeats = []
    def read_brain(self, memory_class):
        assert memory_class == "context"
        return {}
    def heartbeat(self, attempt_id):
        self.heartbeats.append(attempt_id)


def test_read_only_tool_does_not_renew_durable_progress_but_mutation_does():
    brain = Brain()
    stage = SimpleNamespace(
        stage_id="implementation",
        kind="implementation",
        allowed_write_set=("out.txt",),
    )
    context = SimpleNamespace(
        brain=brain,
        attempt_id="attempt-1",
        objective_id="objective",
        contract_revision="v1",
        stage=stage,
        dependency_results={},
    )

    def execute(name, arguments, _context):
        if name == "read_file":
            return ToolExecution("read")
        if name == "write_file":
            return ToolExecution("written", mutation_count=1, artifact_refs=("artifact://out.txt",))
        raise AssertionError(name)

    worker = StrictLiteLLMStageWorker(
        FakeTransport(),
        requested_model="m",
        tools=[
            {"type": "function", "function": {"name": "read_file"}},
            {"type": "function", "function": {"name": "write_file"}},
        ],
        tool_executor=execute,
    )
    outcome = worker(context)
    assert brain.heartbeats == ["attempt-1"]
    assert outcome.tool_call_count == 2
    assert outcome.mutation_count == 1
    assert len(outcome.provider_receipts) == 3
    assert all(item["transport_retries"] == 0 for item in outcome.provider_receipts)
