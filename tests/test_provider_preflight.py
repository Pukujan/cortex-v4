from __future__ import annotations

from dataclasses import asdict
import json
from urllib.parse import urlparse

import pytest

from cortex_v4.control.provider_preflight import (
    FactObservation,
    FactRequirement,
    FactStatus,
    ObservableProbeReceipt,
    PreflightError,
    SourceAttempt,
    SourceAttemptStatus,
    SourceAuthority,
    collect_litellm_manifest,
    reconcile_provider_facts,
)


NOW = "2026-08-10T21:50:00Z"


def _attempt(authority=SourceAuthority.OFFICIAL_PROVIDER, status=SourceAttemptStatus.RETRIEVED):
    return SourceAttempt(
        source_ref="provider-docs",
        authority=authority,
        attempted_at=NOW,
        status=status,
    )


def _obs(
    key,
    value,
    *,
    authority=SourceAuthority.OFFICIAL_PROVIDER,
    fresh_until="2026-08-11T21:50:00Z",
    source="provider-docs",
):
    return FactObservation(
        fact_key=key,
        value=value,
        authority=authority,
        source_ref=source,
        retrieved_at=NOW,
        fresh_until=fresh_until,
    )


def test_official_provider_source_attempt_is_mandatory_for_configuration_change():
    report = reconcile_provider_facts(
        observations=[
            _obs(
                "route_url",
                "https://gateway.example",
                authority=SourceAuthority.OBSERVED_RUNTIME,
                source="runtime-probe",
            )
        ],
        requirements=[
            FactRequirement("route_url", SourceAuthority.OBSERVED_RUNTIME)
        ],
        source_attempts=[],
        evaluated_at=NOW,
    )
    assert report.facts[0].status is FactStatus.VERIFIED
    assert report.official_provider_source_attempted is False
    assert report.configuration_change_allowed is False
    assert "official/provider source was not attempted" in report.escalation_reasons


def test_model_authored_receipt_cannot_establish_provider_truth():
    report = reconcile_provider_facts(
        observations=[
            _obs(
                "upstream_model",
                "gpt-5.6-sol",
                authority=SourceAuthority.MODEL_AUTHORED,
                source="agent-receipt",
            )
        ],
        requirements=[
            FactRequirement("upstream_model", SourceAuthority.VERSIONED_REPOSITORY_CONFIG)
        ],
        source_attempts=[_attempt()],
        evaluated_at=NOW,
    )
    assert report.facts[0].status is FactStatus.UNVERIFIED
    assert report.configuration_change_allowed is False


def test_top_authority_contradiction_fails_closed():
    report = reconcile_provider_facts(
        observations=[
            _obs("route_url", "https://a.example", source="official-a"),
            _obs("route_url", "https://b.example", source="official-b"),
            _obs(
                "route_url",
                "https://runtime.example",
                authority=SourceAuthority.OBSERVED_RUNTIME,
                source="runtime",
            ),
        ],
        requirements=[
            FactRequirement("route_url", SourceAuthority.VERSIONED_REPOSITORY_CONFIG)
        ],
        source_attempts=[_attempt()],
        evaluated_at=NOW,
    )
    fact = report.facts[0]
    assert fact.status is FactStatus.CONTRADICTED
    assert set(fact.source_refs) == {"official-a", "official-b"}
    assert report.configuration_change_allowed is False


def test_stale_authoritative_fact_does_not_verify():
    report = reconcile_provider_facts(
        observations=[
            _obs(
                "stream_mode",
                "stream",
                fresh_until="2026-08-09T21:50:00Z",
            )
        ],
        requirements=[
            FactRequirement("stream_mode", SourceAuthority.OFFICIAL_PROVIDER)
        ],
        source_attempts=[_attempt()],
        evaluated_at=NOW,
    )
    assert report.facts[0].status is FactStatus.UNVERIFIED
    assert report.configuration_change_allowed is False


def test_fresh_authoritative_fact_allows_change_when_all_requirements_resolve():
    report = reconcile_provider_facts(
        observations=[_obs("stream_mode", "stream")],
        requirements=[
            FactRequirement("stream_mode", SourceAuthority.OFFICIAL_PROVIDER)
        ],
        source_attempts=[_attempt()],
        evaluated_at=NOW,
    )
    assert report.facts[0].status is FactStatus.VERIFIED
    assert report.facts[0].value == "stream"
    assert report.configuration_change_allowed is True
    assert report.escalation_reasons == ()


def test_observable_probe_requires_complete_timing_and_route_receipt():
    receipt = ObservableProbeReceipt(
        node="ckff-node-1",
        upstream_model="qwen3-coder-next",
        status=200,
        first_byte_ms=120.5,
        total_ms=810.2,
        retries=0,
        stream_mode="stream",
        observed_at=NOW,
    )
    receipt.validate()

    with pytest.raises(PreflightError):
        ObservableProbeReceipt(
            node="ckff-node-1",
            upstream_model="qwen3-coder-next",
            status=200,
            first_byte_ms=900,
            total_ms=800,
            retries=0,
            stream_mode="stream",
            observed_at=NOW,
        ).validate()


class _FakeResponse:
    def __init__(self, status, payload):
        self.status = status
        self._raw = json.dumps(payload).encode("utf-8")
        self._offset = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, n=-1):
        if n is None or n < 0:
            result = self._raw[self._offset :]
            self._offset = len(self._raw)
            return result
        result = self._raw[self._offset : self._offset + n]
        self._offset += len(result)
        return result


def _fake_opener_factory(seen_headers):
    payloads = {
        "/health/liveliness": {"status": "ok"},
        "/v1/models": {"data": [{"id": "kimi-k2.7-code"}]},
        "/model/info": {
            "data": [
                {
                    "model_name": "kimi-k2.7-code",
                    "litellm_params": {
                        "model": "openai/kimi-k2.7-code",
                        "api_base": "https://node.example/v1",
                        "api_key": "RAW-PROVIDER-SECRET-MUST-NOT-SURVIVE",
                    },
                    "model_info": {
                        "max_input_tokens": 240000,
                        "context_window": 262144,
                    },
                }
            ]
        },
    }

    def opener(request, timeout):
        seen_headers.append(request.get_header("Authorization"))
        path = urlparse(request.full_url).path
        return _FakeResponse(200, payloads[path])

    return opener


def test_litellm_collector_sanitizes_credentials_and_raw_provider_secrets():
    seen_headers = []
    bearer = "BEARER-SECRET-MUST-NOT-SURVIVE"
    manifest, receipt = collect_litellm_manifest(
        base_url="https://gateway.example",
        bearer_key=bearer,
        observed_at=NOW,
        opener=_fake_opener_factory(seen_headers),
    )
    assert seen_headers == [f"Bearer {bearer}"] * 3
    assert len(manifest.aliases) == 1
    assert len(manifest.deployments) == 1
    assert receipt.base_host == "gateway.example"
    assert [endpoint.status for endpoint in receipt.endpoints] == [200, 200, 200]

    serialized = json.dumps(
        {"manifest": manifest.to_dict(), "receipt": receipt.to_dict()},
        sort_keys=True,
    )
    assert bearer not in serialized
    assert "RAW-PROVIDER-SECRET-MUST-NOT-SURVIVE" not in serialized
    assert "node.example" in serialized
    assert "openai/kimi-k2.7-code" in serialized


def test_litellm_collector_refuses_failed_metadata_gate_without_leaking_key():
    secret = "DO-NOT-LEAK-THIS-KEY"

    def opener(request, timeout):
        path = urlparse(request.full_url).path
        if path == "/health/liveliness":
            return _FakeResponse(503, {"error": "down"})
        if path == "/v1/models":
            return _FakeResponse(200, {"data": []})
        return _FakeResponse(200, {"data": []})

    with pytest.raises(PreflightError) as exc:
        collect_litellm_manifest(
            base_url="https://gateway.example",
            bearer_key=secret,
            observed_at=NOW,
            opener=opener,
        )
    assert secret not in str(exc.value)
    assert "health" in str(exc.value)


def test_litellm_collector_requires_url_key_and_positive_timeout():
    with pytest.raises(PreflightError):
        collect_litellm_manifest(base_url="", bearer_key="x", observed_at=NOW)
    with pytest.raises(PreflightError):
        collect_litellm_manifest(base_url="https://gateway.example", bearer_key="", observed_at=NOW)
    with pytest.raises(PreflightError):
        collect_litellm_manifest(
            base_url="https://gateway.example",
            bearer_key="x",
            observed_at=NOW,
            timeout_s=0,
        )


def test_collection_receipt_contains_timing_not_request_headers():
    manifest, receipt = collect_litellm_manifest(
        base_url="https://gateway.example",
        bearer_key="secret",
        observed_at=NOW,
        opener=_fake_opener_factory([]),
    )
    assert manifest.gateway_receipt.base_host == "gateway.example"
    for endpoint in receipt.endpoints:
        assert endpoint.total_ms >= 0
        assert endpoint.first_byte_ms is not None
        assert endpoint.first_byte_ms >= 0
    assert "Authorization" not in json.dumps(asdict(receipt))
