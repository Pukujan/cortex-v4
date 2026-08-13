from cortex_v4.control.litellm_manifest import normalize_inference_receipt


def test_exact_identity_mode_refuses_explicit_substitution():
    receipt = normalize_inference_receipt(
        requested_model="qwen3-coder-next",
        response_metadata={"bridge_actual_model": "gemini-3.5-flash-high"},
        fallback_allowed=False,
    )
    assert receipt.actual_model == "gemini-3.5-flash-high"
    assert receipt.eligible_for_model_specific_credit is False
