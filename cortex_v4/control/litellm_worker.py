"""Scoped V4 stage worker backed by the native LiteLLM transport seam."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

from ..transport.litellm import LiteLLMTransport
from .staged_runner import StageContext, StageOutcome


class ToolExecutor(Protocol):
    def __call__(self, name: str, arguments: Mapping[str, Any], context: StageContext) -> "ToolExecution": ...


@dataclass(frozen=True)
class ToolExecution:
    output: str
    mutation_count: int = 0
    artifact_refs: tuple[str, ...] = ()


PromptBuilder = Callable[[StageContext, Mapping[str, Any]], str]


class LiteLLMStageWorker:
    """Run one bounded stage through authenticated LiteLLM chat completions.

    The worker is intentionally stage-scoped: it receives only the context pack
    returned by the capability handle and only the tool executor supplied for
    that stage.  It never receives a brain path, API key, or another worker's
    session transcript.
    """

    def __init__(
        self,
        transport: LiteLLMTransport,
        *,
        requested_model: str,
        tools: list[Mapping[str, Any]],
        tool_executor: ToolExecutor,
        prompt_builder: PromptBuilder | None = None,
        max_tool_turns: int = 8,
        endpoint: str = "chat",
    ):
        if not requested_model:
            raise ValueError("requested_model is required")
        if max_tool_turns < 1:
            raise ValueError("max_tool_turns must be positive")
        if endpoint not in {"chat", "responses"}:
            raise ValueError("endpoint must be chat or responses")
        self.transport = transport
        self.requested_model = requested_model
        self.tools = [dict(tool) for tool in tools]
        self.tool_executor = tool_executor
        self.prompt_builder = prompt_builder or self._default_prompt
        self.max_tool_turns = max_tool_turns
        self.endpoint = endpoint

    @staticmethod
    def _default_prompt(context: StageContext, context_pack: Mapping[str, Any]) -> str:
        return (
            "Execute exactly the assigned V4 stage. Return a concise completion note after using "
            "any necessary tools. Do not perform work outside the stage write set.\n"
            f"objective_id={context.objective_id}\n"
            f"contract_revision={context.contract_revision}\n"
            f"stage_id={context.stage.stage_id}\n"
            f"stage_kind={context.stage.kind}\n"
            f"allowed_write_set={list(context.stage.allowed_write_set)}\n"
            f"dependency_results={dict(context.dependency_results)}\n"
            f"scoped_context_keys={sorted(context_pack.keys())}"
        )

    def __call__(self, context: StageContext) -> StageOutcome:
        context_pack = context.brain.read_brain("context")
        prompt = self.prompt_builder(context, context_pack)
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": "You are a bounded Cortex V4 stage worker. Use only the supplied tools and scope.",
            },
            {"role": "user", "content": prompt},
        ]
        response_input: str | list[dict[str, Any]] = prompt
        provider_receipts: list[Mapping[str, Any]] = []
        artifact_refs: list[str] = []
        mutation_count = 0
        tool_call_count = 0
        final_text = ""
        actual_model = ""
        fallback_observed = False
        for _ in range(self.max_tool_turns):
            if self.endpoint == "responses":
                response_tools = self._responses_tools()
                response = self.transport.responses(
                    model=self.requested_model,
                    input=response_input,
                    stream=True,
                    tools=response_tools or None,
                )
            else:
                response = self.transport.chat(
                    model=self.requested_model,
                    messages=messages,
                    stream=True,
                    tools=self.tools or None,
                )
            provider_receipts.append(response.receipt.as_dict())
            actual_model = response.actual_model or actual_model
            fallback_observed = fallback_observed or bool(response.actual_model and response.actual_model != self.requested_model)
            final_text = response.text or final_text
            if not response.tool_calls:
                break
            tool_call_count += len(response.tool_calls)
            if self.endpoint == "chat":
                assistant_tool_calls = [dict(call) for call in response.tool_calls]
                messages.append({"role": "assistant", "content": response.text or None, "tool_calls": assistant_tool_calls})
            elif isinstance(response_input, str):
                response_input = [{"type": "message", "role": "user", "content": response_input}]
            for call in response.tool_calls:
                function = call.get("function") if isinstance(call, Mapping) else None
                if self.endpoint == "responses" and function is None and isinstance(call, Mapping):
                    function = {"name": call.get("name", ""), "arguments": call.get("arguments", "{}")}
                if not isinstance(function, Mapping):
                    raise ValueError("malformed tool call")
                name = str(function.get("name") or "")
                raw_arguments = function.get("arguments", "{}")
                try:
                    arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
                except json.JSONDecodeError as exc:
                    raise ValueError("malformed tool arguments") from exc
                if not isinstance(arguments, Mapping):
                    raise ValueError("tool arguments must be an object")
                execution = self.tool_executor(name, dict(arguments), context)
                if not isinstance(execution, ToolExecution):
                    raise TypeError("tool executor must return ToolExecution")
                mutation_count += execution.mutation_count
                artifact_refs.extend(execution.artifact_refs)
                # Tool mutation/output is durable progress only after the
                # controller records a heartbeat. Reads do not renew the lease.
                context.brain.heartbeat(context.attempt_id)
                if self.endpoint == "chat":
                    messages.append({
                        "role": "tool",
                        "tool_call_id": str(call.get("id") or ""),
                        "name": name,
                        "content": execution.output,
                    })
                else:
                    response_input.extend([
                        {
                            "type": "function_call",
                            "call_id": str(call.get("call_id") or call.get("id") or ""),
                            "name": name,
                            "arguments": raw_arguments if isinstance(raw_arguments, str) else json.dumps(dict(arguments), sort_keys=True),
                        },
                        {
                            "type": "function_call_output",
                            "call_id": str(call.get("call_id") or call.get("id") or ""),
                            "output": execution.output,
                        },
                    ])
        else:
            raise RuntimeError("stage exceeded bounded tool-turn budget")
        if not final_text.strip() and not artifact_refs and mutation_count == 0:
            raise ValueError("model completed without usable stage output or mutation")
        return StageOutcome(
            classification="success",
            mechanical_check={"passed": True, "model_output": bool(final_text.strip()), "tool_activity": bool(tool_call_count)},
            artifact_refs=tuple(dict.fromkeys(artifact_refs)),
            requested_model=self.requested_model,
            actual_model=actual_model,
            route_label=self.transport.route_label,
            provider_call=True,
            tool_call_count=tool_call_count,
            mutation_count=mutation_count,
            worker_lifecycle="fresh",
            fallback_reason="gateway-returned-nonrequested-model" if fallback_observed else None,
            provider_receipts=tuple(dict(item) for item in provider_receipts),
        )

    def _responses_tools(self) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for tool in self.tools:
            function = tool.get("function") if isinstance(tool, Mapping) else None
            if not isinstance(function, Mapping):
                continue
            converted.append({
                "type": "function",
                "name": function.get("name", ""),
                "description": function.get("description", ""),
                "parameters": function.get("parameters") or {},
            })
        return converted
