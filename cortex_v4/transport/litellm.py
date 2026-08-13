"""Small, dependency-free LiteLLM HTTP seam for native V4 stages.

This module deliberately targets the authenticated OpenAI-compatible endpoints
owned by the LiteLLM gateway.  It does not import SSC, expose credentials to a
worker, or persist request/response text.  A stage receives the result in
memory; callers persist only the structured receipt and artifact references.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class LiteLLMError(RuntimeError):
    """A provider response cannot be accepted as a usable model result."""

    def __init__(self, classification: str, message: str, *, receipt: "LiteLLMRequestReceipt | None" = None):
        super().__init__(message)
        self.classification = classification
        self.receipt = receipt


@dataclass(frozen=True)
class TimeoutLayers:
    """Timeout/deadline provenance carried with every provider attempt."""

    provider_deadline_s: float | None = None
    litellm_request_s: float | None = None
    client_request_s: float | None = 90.0
    stage_deadline_s: float | None = None
    inactivity_watchdog_s: float | None = None
    campaign_deadline_s: float | None = None

    def values(self) -> dict[str, float | None]:
        return {
            "provider_deadline_s": self.provider_deadline_s,
            "litellm_request_s": self.litellm_request_s,
            "client_request_s": self.client_request_s,
            "stage_deadline_s": self.stage_deadline_s,
            "inactivity_watchdog_s": self.inactivity_watchdog_s,
            "campaign_deadline_s": self.campaign_deadline_s,
        }

    @property
    def effective_deadline_s(self) -> float:
        active = [float(value) for value in self.values().values() if value is not None and float(value) > 0]
        if not active:
            raise ValueError("at least one positive timeout/deadline is required")
        return min(active)


@dataclass(frozen=True)
class LiteLLMRequestReceipt:
    schema: str
    requested_model: str
    actual_model: str
    route_label: str
    api_base_label: str
    endpoint_kind: str
    stream: bool
    start_at: float
    end_at: float
    duration_s: float
    status_code: int | None
    request_id: str | None
    tool_call_count: int
    usable_output: bool
    result_classification: str
    timeout_layer: str | None
    timeout_values: Mapping[str, float | None]
    capability: str = "chat"

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "requested_model": self.requested_model,
            "actual_model": self.actual_model,
            "route_label": self.route_label,
            "api_base_label": self.api_base_label,
            "endpoint_kind": self.endpoint_kind,
            "stream": self.stream,
            "start_at": self.start_at,
            "end_at": self.end_at,
            "duration_s": self.duration_s,
            "status_code": self.status_code,
            "request_id": self.request_id,
            "tool_call_count": self.tool_call_count,
            "usable_output": self.usable_output,
            "result_classification": self.result_classification,
            "timeout_layer": self.timeout_layer,
            "timeout_values": dict(self.timeout_values),
            "capability": self.capability,
        }


@dataclass(frozen=True)
class ChatResult:
    text: str
    actual_model: str
    tool_calls: tuple[dict[str, Any], ...]
    finish_reason: str | None
    receipt: LiteLLMRequestReceipt


@dataclass(frozen=True)
class ResponsesResult:
    text: str
    actual_model: str
    tool_calls: tuple[dict[str, Any], ...]
    receipt: LiteLLMRequestReceipt


def _body_bytes(response: Any) -> bytes:
    raw = response.read()
    if isinstance(raw, str):
        return raw.encode("utf-8")
    return bytes(raw)


def _response_header(response: Any, name: str) -> str | None:
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    try:
        value = headers.get(name)
    except AttributeError:
        return None
    return str(value) if value else None


def _text_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, Mapping):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


class LiteLLMTransport:
    """Authenticated, receipt-producing LiteLLM client.

    ``opener`` is injectable only for unit tests.  Production callers use the
    standard-library opener and pass the API key directly from process memory.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        route_label: str,
        api_base_label: str | None = None,
        timeout_layers: TimeoutLayers | None = None,
        capability: str = "chat",
        opener: Callable[..., Any] | None = None,
        clock: Callable[[], float] = time.time,
    ):
        base_url = str(base_url).strip().rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("LiteLLM base_url must be an HTTP(S) URL")
        if not api_key:
            raise ValueError("LiteLLM API key is required")
        if not route_label:
            raise ValueError("route_label is required")
        if capability != "chat":
            raise ValueError("native Cortex LiteLLM transport is chat-capability only")
        self.base_url = base_url
        self.api_key = api_key
        self.route_label = route_label
        self.api_base_label = api_base_label or base_url
        self.timeout_layers = timeout_layers or TimeoutLayers()
        self.capability = capability
        self.opener = opener or urlopen
        self.clock = clock

    def _url(self, endpoint: str) -> str:
        endpoint = endpoint.lstrip("/")
        if self.base_url.endswith("/" + endpoint):
            return self.base_url
        return f"{self.base_url}/{endpoint}"

    def _request(self, endpoint: str, payload: Mapping[str, Any], *, stream: bool) -> tuple[Any, float, int | None, str | None]:
        body = json.dumps(dict(payload), separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        request = Request(
            self._url(endpoint),
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream" if stream else "application/json",
            },
        )
        started = self.clock()
        try:
            response = self.opener(request, timeout=self.timeout_layers.effective_deadline_s)
        except HTTPError as exc:
            try:
                exc.close()
            except Exception:
                pass
            receipt = self._receipt(
                requested_model=str(payload.get("model", "")), endpoint_kind=endpoint,
                stream=stream, started=started, status_code=int(exc.code),
                request_id=_response_header(exc, "x-request-id"), classification="http_error",
                actual_model="", tool_call_count=0, usable_output=False, timeout_layer=None,
            )
            raise LiteLLMError("http_error", "LiteLLM returned a non-success status", receipt=receipt) from None
        except (TimeoutError, URLError, OSError) as exc:
            classification = "client_timeout" if isinstance(exc, TimeoutError) or "timed out" in str(exc).lower() else "transport_error"
            receipt = self._receipt(
                requested_model=str(payload.get("model", "")), endpoint_kind=endpoint,
                stream=stream, started=started, status_code=None, request_id=None,
                classification=classification, actual_model="", tool_call_count=0,
                usable_output=False, timeout_layer="client" if classification == "client_timeout" else None,
            )
            raise LiteLLMError(classification, "LiteLLM transport did not complete", receipt=receipt) from None
        status = int(getattr(response, "status", 200))
        request_id = _response_header(response, "x-request-id")
        return response, started, status, request_id

    def _receipt(
        self, *, requested_model: str, endpoint_kind: str, stream: bool,
        started: float, status_code: int | None, request_id: str | None,
        classification: str, actual_model: str, tool_call_count: int,
        usable_output: bool, timeout_layer: str | None,
    ) -> LiteLLMRequestReceipt:
        ended = self.clock()
        return LiteLLMRequestReceipt(
            schema="cortex.v4.litellm-attempt-receipt.v1",
            requested_model=requested_model,
            actual_model=actual_model,
            route_label=self.route_label,
            api_base_label=self.api_base_label,
            endpoint_kind=endpoint_kind,
            stream=stream,
            start_at=started,
            end_at=ended,
            duration_s=max(0.0, ended - started),
            status_code=status_code,
            request_id=request_id,
            tool_call_count=tool_call_count,
            usable_output=usable_output,
            result_classification=classification,
            timeout_layer=timeout_layer,
            timeout_values=self.timeout_layers.values(),
            capability=self.capability,
        )

    def chat(
        self,
        *,
        model: str,
        messages: list[Mapping[str, Any]],
        stream: bool,
        tools: list[Mapping[str, Any]] | None = None,
        tool_choice: Any | None = None,
        temperature: float | None = None,
    ) -> ChatResult:
        if not model or not isinstance(messages, list) or not messages:
            raise ValueError("chat requires model and non-empty messages")
        payload: dict[str, Any] = {"model": model, "messages": [dict(item) for item in messages], "stream": bool(stream)}
        if tools:
            payload["tools"] = [dict(item) for item in tools]
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        if temperature is not None:
            payload["temperature"] = temperature
        response, started, status, request_id = self._request("chat/completions", payload, stream=stream)
        if status < 200 or status >= 300:
            receipt = self._receipt(
                requested_model=model, endpoint_kind="chat/completions", stream=stream,
                started=started, status_code=status, request_id=request_id,
                classification="http_error", actual_model="", tool_call_count=0,
                usable_output=False, timeout_layer=None,
            )
            raise LiteLLMError("http_error", "LiteLLM returned a non-success status", receipt=receipt)
        if stream:
            return self._parse_chat_stream(response, model, started, status, request_id)
        return self._parse_chat_json(response, model, started, status, request_id)

    def _parse_chat_json(self, response: Any, requested_model: str, started: float, status: int, request_id: str | None) -> ChatResult:
        try:
            payload = json.loads(_body_bytes(response).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            receipt = self._receipt(
                requested_model=requested_model, endpoint_kind="chat/completions", stream=False,
                started=started, status_code=status, request_id=request_id,
                classification="malformed_response", actual_model="", tool_call_count=0,
                usable_output=False, timeout_layer=None,
            )
            raise LiteLLMError("malformed_response", "LiteLLM returned malformed JSON", receipt=receipt) from None
        try:
            choice = payload["choices"][0]
            message = choice["message"]
            actual = str(payload.get("model") or "")
            text = _text_content(message.get("content"))
            tool_calls = tuple(dict(item) for item in message.get("tool_calls", []) if isinstance(item, Mapping))
            finish = choice.get("finish_reason")
        except (KeyError, IndexError, TypeError):
            actual, text, tool_calls, finish = "", "", (), None
        usable = bool(text.strip() or tool_calls)
        classification = "success" if usable else "empty_output"
        receipt = self._receipt(
            requested_model=requested_model, endpoint_kind="chat/completions", stream=False,
            started=started, status_code=status, request_id=request_id,
            classification=classification, actual_model=actual, tool_call_count=len(tool_calls),
            usable_output=usable, timeout_layer=None,
        )
        if not usable:
            raise LiteLLMError(classification, "LiteLLM returned no usable chat output", receipt=receipt)
        return ChatResult(text, actual, tool_calls, str(finish) if finish is not None else None, receipt)

    def _parse_chat_stream(self, response: Any, requested_model: str, started: float, status: int, request_id: str | None) -> ChatResult:
        buffer = ""
        text_parts: list[str] = []
        tool_parts: dict[int, dict[str, Any]] = {}
        actual = ""
        finish: str | None = None
        done = False

        while True:
            chunk = response.read(8192)
            if not chunk:
                break
            if isinstance(chunk, bytes):
                buffer += chunk.decode("utf-8", errors="replace")
            else:
                buffer += str(chunk)
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.rstrip("\r")
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw == "[DONE]":
                    done = True
                    continue
                if not raw:
                    continue
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    receipt = self._receipt(
                        requested_model=requested_model, endpoint_kind="chat/completions", stream=True,
                        started=started, status_code=status, request_id=request_id,
                        classification="malformed_stream", actual_model=actual,
                        tool_call_count=len(tool_parts), usable_output=False, timeout_layer=None,
                    )
                    raise LiteLLMError("malformed_stream", "LiteLLM returned malformed stream data", receipt=receipt) from None
                actual = str(event.get("model") or actual)
                for choice in event.get("choices", []) if isinstance(event, Mapping) else []:
                    if not isinstance(choice, Mapping):
                        continue
                    delta = choice.get("delta") or {}
                    if isinstance(delta, Mapping):
                        part = delta.get("content")
                        if isinstance(part, str):
                            text_parts.append(part)
                        for call in delta.get("tool_calls", []) or []:
                            if not isinstance(call, Mapping):
                                continue
                            index = int(call.get("index", len(tool_parts)))
                            target = tool_parts.setdefault(index, {"index": index, "id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                            if call.get("id"):
                                target["id"] = str(call["id"])
                            if call.get("type"):
                                target["type"] = str(call["type"])
                            fn = call.get("function") or {}
                            if isinstance(fn, Mapping):
                                if fn.get("name"):
                                    target["function"]["name"] = str(fn["name"])
                                if fn.get("arguments"):
                                    target["function"]["arguments"] += str(fn["arguments"])
                    if choice.get("finish_reason") is not None:
                        finish = str(choice["finish_reason"])

        if buffer.strip().startswith("data:"):
            # A final event without a trailing blank line is still a valid SSE frame.
            raw = buffer[5:].strip()
            if raw == "[DONE]":
                done = True
            elif raw:
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    event = None
                if isinstance(event, Mapping):
                    actual = str(event.get("model") or actual)
                    for choice in event.get("choices", []) or []:
                        delta = choice.get("delta") or {}
                        if isinstance(delta, Mapping) and isinstance(delta.get("content"), str):
                            text_parts.append(delta["content"])
                        if choice.get("finish_reason") is not None:
                            finish = str(choice["finish_reason"])
        text = "".join(text_parts)
        tool_calls = tuple(tool_parts[index] for index in sorted(tool_parts))
        usable = bool(text.strip() or tool_calls)
        classification = "success" if usable and done else ("incomplete_stream" if not done else "zero_usable_output")
        receipt = self._receipt(
            requested_model=requested_model, endpoint_kind="chat/completions", stream=True,
            started=started, status_code=status, request_id=request_id,
            classification=classification, actual_model=actual, tool_call_count=len(tool_calls),
            usable_output=usable and done, timeout_layer=None,
        )
        if not (usable and done):
            raise LiteLLMError(classification, "LiteLLM stream did not produce a complete usable result", receipt=receipt)
        return ChatResult(text, actual, tool_calls, finish, receipt)

    def responses(
        self,
        *,
        model: str,
        input: str | list[Mapping[str, Any]],
        stream: bool = False,
        tools: list[Mapping[str, Any]] | None = None,
    ) -> ResponsesResult:
        if not model or not input:
            raise ValueError("responses requires model and input")
        payload: dict[str, Any] = {"model": model, "input": input, "stream": bool(stream)}
        if tools:
            payload["tools"] = [dict(item) for item in tools]
        response, started, status, request_id = self._request("responses", payload, stream=stream)
        if status < 200 or status >= 300:
            receipt = self._receipt(
                requested_model=model, endpoint_kind="responses", stream=stream,
                started=started, status_code=status, request_id=request_id,
                classification="http_error", actual_model="", tool_call_count=0,
                usable_output=False, timeout_layer=None,
            )
            raise LiteLLMError("http_error", "LiteLLM returned a non-success status", receipt=receipt)
        if stream:
            return self._parse_responses_stream(response, model, started, status, request_id)
        try:
            data = json.loads(_body_bytes(response).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            data = None
        actual = str(data.get("model") or "") if isinstance(data, Mapping) else ""
        text = str(data.get("output_text") or "") if isinstance(data, Mapping) else ""
        calls: list[dict[str, Any]] = []
        if isinstance(data, Mapping):
            for item in data.get("output", []) or []:
                if isinstance(item, Mapping) and item.get("type") in {"function_call", "computer_call"}:
                    calls.append(dict(item))
        usable = bool(text.strip() or calls)
        classification = "success" if usable else "malformed_response" if data is None else "empty_output"
        receipt = self._receipt(
            requested_model=model, endpoint_kind="responses", stream=False,
            started=started, status_code=status, request_id=request_id,
            classification=classification, actual_model=actual, tool_call_count=len(calls),
            usable_output=usable, timeout_layer=None,
        )
        if not usable:
            raise LiteLLMError(classification, "LiteLLM returned no usable Responses output", receipt=receipt)
        return ResponsesResult(text, actual, tuple(calls), receipt)

    def _parse_responses_stream(self, response: Any, requested_model: str, started: float, status: int, request_id: str | None) -> ResponsesResult:
        buffer = ""
        text_parts: list[str] = []
        calls: list[dict[str, Any]] = []
        actual = ""
        done = False
        while True:
            chunk = response.read(8192)
            if not chunk:
                break
            buffer += chunk.decode("utf-8", errors="replace") if isinstance(chunk, bytes) else str(chunk)
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.rstrip("\r")
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw == "[DONE]":
                    done = True
                    continue
                if not raw:
                    continue
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    receipt = self._receipt(
                        requested_model=requested_model, endpoint_kind="responses", stream=True,
                        started=started, status_code=status, request_id=request_id,
                        classification="malformed_stream", actual_model=actual,
                        tool_call_count=len(calls), usable_output=False, timeout_layer=None,
                    )
                    raise LiteLLMError("malformed_stream", "LiteLLM returned malformed Responses stream data", receipt=receipt) from None
                if not isinstance(event, Mapping):
                    continue
                actual = str(event.get("model") or event.get("response", {}).get("model") or actual)
                event_type = event.get("type")
                if event_type == "response.output_text.delta" and isinstance(event.get("delta"), str):
                    text_parts.append(event["delta"])
                elif event_type in {"response.output_item.added", "response.output_item.done"} and isinstance(event.get("item"), Mapping):
                    item = event["item"]
                    if item.get("type") in {"function_call", "computer_call"}:
                        calls.append(dict(item))
                elif event_type == "response.completed":
                    done = True
        text = "".join(text_parts)
        usable = bool(text.strip() or calls)
        classification = "success" if usable and done else "incomplete_stream" if not done else "zero_usable_output"
        receipt = self._receipt(
            requested_model=requested_model, endpoint_kind="responses", stream=True,
            started=started, status_code=status, request_id=request_id,
            classification=classification, actual_model=actual, tool_call_count=len(calls),
            usable_output=usable and done, timeout_layer=None,
        )
        if not (usable and done):
            raise LiteLLMError(classification, "LiteLLM Responses stream did not complete with usable output", receipt=receipt)
        return ResponsesResult(text, actual, tuple(calls), receipt)
