from __future__ import annotations

import json
import pytest

from cortex_v4.transport.litellm import LiteLLMError, TimeoutLayers
from cortex_v4.transport.strict_litellm import STRICT_PROFILE, StrictLiteLLMTransport


class Response:
    status = 200
    def __init__(self, payload: bytes = b"", *, chunks=None):
        self.payload = payload
        self.chunks = list(chunks or [])
        self.headers = {"x-request-id": "req-strict"}
        self.read_count = 0
    def read(self, size=None):
        self.read_count += 1
        if self.chunks:
            value = self.chunks.pop(0)
            return value.encode() if isinstance(value, str) else value
        value, self.payload = self.payload, b""
        return value


class Opener:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = 0
        self.timeout = None
    def __call__(self, request, *, timeout):
        self.calls += 1
        self.timeout = timeout
        if self.error:
            raise self.error
        return self.response


def make_transport(opener, *, layers=None):
    return StrictLiteLLMTransport(
        "https://gateway.invalid/v1",
        "secret",
        route_label="strict-staging",
        api_base_label="strict",
        timeout_layers=layers or TimeoutLayers(client_request_s=72, stage_deadline_s=72, litellm_request_s=120),
        opener=opener,
        clock=iter([10.0, 12.0, 14.0, 16.0]).__next__,
    )


def test_strict_success_is_one_transport_call_and_profiles_receipt():
    events = [
        "data: " + json.dumps({"model": "m", "choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}) + "\n\n",
        "data: [DONE]\n\n",
    ]
    response = Response(chunks=events)
    opener = Opener(response=response)
    transport = make_transport(opener)
    result = transport.chat(model="m", messages=[{"role": "user", "content": "secret prompt"}], stream=True)
    assert result.text == "ok"
    assert opener.calls == 1
    assert response.read_count >= 2
    receipt = result.receipt.as_dict()
    assert receipt["config_profile"] == STRICT_PROFILE
    assert receipt["transport_retries"] == 0
    assert receipt["semantic_fallbacks"] is False
    assert "secret prompt" not in json.dumps(receipt)


def test_strict_rejects_model_substitution():
    payload = json.dumps({
        "model": "model-b",
        "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
    }).encode()
    opener = Opener(response=Response(payload))
    transport = make_transport(opener)
    with pytest.raises(LiteLLMError) as exc:
        transport.chat(model="model-a", messages=[{"role": "user", "content": "x"}], stream=False)
    assert exc.value.classification == "model_substitution"
    assert opener.calls == 1
    assert exc.value.receipt.as_dict()["result_classification"] == "model_substitution"


def test_translated_responses_is_noncanonical_without_provider_call():
    opener = Opener(response=Response())
    transport = make_transport(opener)
    with pytest.raises(LiteLLMError) as exc:
        transport.responses(model="m", input="x", stream=True)
    assert exc.value.classification == "noncanonical_endpoint"
    assert opener.calls == 0


def test_timeout_receipt_attributes_shortest_stage_deadline():
    opener = Opener(error=TimeoutError("timed out"))
    layers = TimeoutLayers(
        provider_deadline_s=600,
        litellm_request_s=120,
        client_request_s=90,
        stage_deadline_s=12,
        inactivity_watchdog_s=72,
        campaign_deadline_s=300,
    )
    transport = make_transport(opener, layers=layers)
    with pytest.raises(LiteLLMError) as exc:
        transport.chat(model="m", messages=[{"role": "user", "content": "x"}], stream=False)
    assert opener.timeout == 12
    assert exc.value.receipt.timeout_layer == "stage"
    assert exc.value.receipt.as_dict()["timeout_values"]["stage_deadline_s"] == 12
