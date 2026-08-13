from __future__ import annotations

import pytest

from cortex_v4.control.litellm_manifest import (
    ManifestError,
    UNKNOWN,
    build_route_manifest,
    normalize_inference_receipt,
)


def _payload(alias: str, count: int, upstream: str | None = None):
    models = {"data": [{"id": alias}]}
    rows = []
    for i in range(count):
        params = {"api_base": f"https://route{i}.example/v1"}
        if upstream is not None:
            params["model"] = upstream
        rows.append(
            {
                "model_name": alias,
                "litellm_params": params,
                "model_info": {"max_input_tokens": 240000},
            }
        )
    return models, {"data": rows}


def _build(models, info):
    return build_route_manifest(
        base_host="gateway.example",
        observed_at="2026-08-10T21:00:00Z",
        health_status=200,
        models_status=200,
        model_info_status=200,
        models_payload=models,
        model_info_payload=info,
    )


def test_four_kimi_routes_are_one_alias_and_one_configured_identity():
    models, info = _payload("kimi-k2.7-code", 4, "openai/kimi-k2.7-code")
    manifest = _build(models, info)
    alias = next(a for a in manifest.aliases if a.public_name == "kimi-k2.7-code")
    assert len(alias.deployment_ids) == 4
    assert len(manifest.deployments) == 4
    assert len(manifest.epistemic_identities) == 1
    assert manifest.epistemic_identities[0].canonical_model_id == "openai/kimi-k2.7-code"
    assert manifest.independent_verifier_count == 0


def test_three_sol_routes_do_not_create_three_independent_reviewers():
    models, info = _payload("gpt-5.6-sol", 3, "openai/gpt-5.6-sol")
    manifest = _build(models, info)
    assert len(manifest.deployments) == 3
    assert len(manifest.epistemic_identities) == 1
    assert manifest.independent_verifier_count == 0


def test_fallback_receipt_attributes_credit_to_actual_model():
    receipt = normalize_inference_receipt(
        requested_model="[aws]deepseek-v3.2",
        response_metadata={
            "bridge_actual_model": "qwen3.7-flash",
            "bridge_attempts": [
                {"model": "[aws]deepseek-v3.2", "status": 503, "reason": "provider_or_channel_error"},
                {"model": "qwen3.7-flash", "status": 200, "reason": "success"},
            ],
        },
        fallback_allowed=True,
    )
    assert receipt.requested_model == "[aws]deepseek-v3.2"
    assert receipt.actual_model == "qwen3.7-flash"
    assert [attempt.model for attempt in receipt.attempts] == [
        "[aws]deepseek-v3.2",
        "qwen3.7-flash",
    ]
    assert receipt.eligible_for_model_specific_credit is True


def test_missing_actual_model_under_fallback_gets_no_model_specific_credit():
    receipt = normalize_inference_receipt(
        requested_model="model-a",
        response_metadata={},
        fallback_allowed=True,
    )
    assert receipt.actual_model is None
    assert receipt.eligible_for_model_specific_credit is False


def test_unknown_upstream_identity_fails_closed_instead_of_name_guessing():
    models, info = _payload("[grok] grok-4.5", 2, None)
    manifest = _build(models, info)
    assert len(manifest.epistemic_identities) == 1
    assert manifest.epistemic_identities[0].canonical_model_id == UNKNOWN
    assert manifest.independent_verifier_count == 0


@pytest.mark.parametrize(
    "health,models_status,model_info_status",
    [(503, 200, 200), (200, 500, 200), (200, 200, 0)],
)
def test_failed_inventory_endpoint_refuses_fresh_manifest(
    health, models_status, model_info_status
):
    models, info = _payload("qwen3-coder-next", 1, "openai/qwen3-coder-next")
    with pytest.raises(ManifestError):
        build_route_manifest(
            base_host="gateway.example",
            observed_at="2026-08-10T21:00:00Z",
            health_status=health,
            models_status=models_status,
            model_info_status=model_info_status,
            models_payload=models,
            model_info_payload=info,
        )


def test_exact_identity_mode_can_attribute_requested_model_when_metadata_is_absent():
    receipt = normalize_inference_receipt(
        requested_model="qwen3-coder-next",
        response_metadata={},
        fallback_allowed=False,
    )
    assert receipt.actual_model == "qwen3-coder-next"
    assert receipt.eligible_for_model_specific_credit is True
