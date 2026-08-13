from __future__ import annotations

import json

import pytest

from cortex_v4.transport.litellm import LiteLLMError, LiteLLMTransport, TimeoutLayers


class Response:
    status = 200

    def __init__(self, payload: str | bytes, *, chunks: list[str | bytes] | None = None):
        self.payload = payload.encode() if isinstance(payload, str) else payload
        self.chunks = list(chunks) if chunks is not None else None
        self.headers = {"x-request-id": "req-test"}

    def read(self, size: int | None = None) -> bytes:
        if self.chunks is not None:
            if not self.chunks:
                return b""
            value = self.chunks.pop(0)
            return value.encode() if isinstance(value, str) else value
        value, self.payload = self.payload, b""
        return value


class Opener:
    def __init__(self, response: Response):
        self.response = response
        self.request = None
        self.timeout = None

    def __call__(self, request, *, timeout):
        self.request = request
        self.timeout = timeout
        return self.response


def transport(response: Response) -> tuple[LiteLLMTransport, Opener]:
    opener = Opener(response)
    client = LiteLLMTransport(
        "https://gateway.invalid/v1",
        "secret-is-never-logged",
        route_label="staging-control",
        api_base_label="control",
        timeout_layers=TimeoutLayers(client_request_s=72, stage_deadline_s=54),
        opener=opener,
        clock=iter([10.0, 12.0]).__next__,
    )
    return client, opener


def test_nonstream_requires_usable_output_and_records_actual_model():
    client, opener = transport(Response(json.dumps({
        "id": "chatcmpl-test",
        "model": "actual-test-model",
        "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
    })))
    result = client.chat(model="requested-model", messages=[{"role": "user", "content": "probe"}], stream=False)
    assert result.text == "ok"
    assert result.actual_model == "actual-test-model"
    assert result.receipt.stream is False
    assert result.receipt.requested_model == "requested-model"
    assert result.receipt.duration_s == 2.0
    assert result.receipt.timeout_values["stage_deadline_s"] == 54
    assert result.receipt.capability == "chat"
    assert opener.timeout == 54
    assert opener.request.get_header("Authorization") == "Bearer secret-is-never-logged"


def test_stream_joins_content_and_tool_call_deltas():
    events = [
        "data: " + json.dumps({"model": "actual-stream", "choices": [{"delta": {"content": "hello "}, "finish_reason": None}]} ) + "\n\n",
        "data: " + json.dumps({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call-1", "type": "function", "function": {"name": "write_file", "arguments": '{"path":"a.txt",'}}]}, "finish_reason": None}]} ) + "\n\n",
        "data: " + json.dumps({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"content":"x"}'}}]}, "finish_reason": "tool_calls"}]} ) + "\n\n",
        "data: [DONE]\n\n",
    ]
    client, _ = transport(Response(b"", chunks=events))
    result = client.chat(model="requested-model", messages=[{"role": "user", "content": "task"}], stream=True)
    assert result.text == "hello "
    assert result.actual_model == "actual-stream"
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0]["function"]["name"] == "write_file"
    assert result.receipt.tool_call_count == 1


@pytest.mark.parametrize(
    ("chunks", "classification"),
    [
        (["data: {bad}\n\n"], "malformed_stream"),
        (["data: {\"model\":\"m\",\"choices\":[{\"delta\":{\"content\":\"\"}}]}\n\n", "data: [DONE]\n\n"], "zero_usable_output"),
        (["data: {\"model\":\"m\",\"choices\":[{\"delta\":{\"content\":\"ok\"}}]}\n\n"], "incomplete_stream"),
    ],
)
def test_invalid_streams_fail_closed(chunks, classification):
    client, _ = transport(Response(b"", chunks=chunks))
    with pytest.raises(LiteLLMError) as exc:
        client.chat(model="m", messages=[{"role": "user", "content": "x"}], stream=True)
    assert exc.value.classification == classification
    assert exc.value.receipt is not None
    assert exc.value.receipt.usable_output is False


def test_responses_nonstream_has_same_sanitized_receipt_shape():
    client, _ = transport(Response(json.dumps({
        "model": "responses-model",
        "output_text": "done",
        "output": [],
    })))
    result = client.responses(model="requested-model", input="probe")
    assert result.text == "done"
    assert result.actual_model == "responses-model"
    assert result.receipt.endpoint_kind == "responses"
    assert "probe" not in json.dumps(result.receipt.as_dict())
