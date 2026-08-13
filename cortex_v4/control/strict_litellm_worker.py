"""Strict stage worker for the zero-retry Cortex/LiteLLM P0 profile."""
from __future__ import annotations

import json
from typing import Any, Mapping

from .litellm_worker import LiteLLMStageWorker, ToolExecution
from .staged_runner import StageContext, StageOutcome


class StrictLiteLLMStageWorker(LiteLLMStageWorker):
    """Chat-streaming-only worker with durable-progress heartbeat semantics."""

    def __init__(self, *args: Any, endpoint: str = "chat", **kwargs: Any):
        if endpoint != "chat":
            raise ValueError("strict Cortex P0 supports only canonical chat streaming")
        super().__init__(*args, endpoint="chat", **kwargs)

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
        provider_receipts: list[Mapping[str, Any]] = []
        artifact_refs: list[str] = []
        mutation_count = 0
        tool_call_count = 0
        final_text = ""
        actual_model = ""

        for _ in range(self.max_tool_turns):
            response = self.transport.chat(
                model=self.requested_model,
                messages=messages,
                stream=True,
                tools=self.tools or None,
            )
            provider_receipts.append(response.receipt.as_dict())
            actual_model = response.actual_model or actual_model
            final_text = response.text or final_text
            if not response.tool_calls:
                break

            tool_call_count += len(response.tool_calls)
            assistant_tool_calls = [dict(call) for call in response.tool_calls]
            messages.append({
                "role": "assistant",
                "content": response.text or None,
                "tool_calls": assistant_tool_calls,
            })

            for call in response.tool_calls:
                function = call.get("function") if isinstance(call, Mapping) else None
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

                if execution.mutation_count > 0 or execution.artifact_refs:
                    context.brain.heartbeat(context.attempt_id)

                messages.append({
                    "role": "tool",
                    "tool_call_id": str(call.get("id") or ""),
                    "name": name,
                    "content": execution.output,
                })
        else:
            raise RuntimeError("stage exceeded bounded tool-turn budget")

        if not final_text.strip() and not artifact_refs and mutation_count == 0:
            raise ValueError("model completed without usable stage output or mutation")

        return StageOutcome(
            classification="success",
            mechanical_check={
                "passed": True,
                "model_output": bool(final_text.strip()),
                "tool_activity": bool(tool_call_count),
            },
            artifact_refs=tuple(dict.fromkeys(artifact_refs)),
            requested_model=self.requested_model,
            actual_model=actual_model,
            route_label=self.transport.route_label,
            provider_call=True,
            tool_call_count=tool_call_count,
            mutation_count=mutation_count,
            worker_lifecycle="fresh",
            fallback_reason=None,
            provider_receipts=tuple(dict(item) for item in provider_receipts),
        )
