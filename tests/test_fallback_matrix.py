"""Tests for the first-loop vendor fallback matrix (public recovery_contract).

Only determines fallback ordering: min same-model retries → same-vendor →
cross-vendor. No provider spend.
"""

from __future__ import annotations

from cortex_v4.control.fallback_matrix import VendorFallbackController


def _routes():
    return [
        {"route_class": "xai-model-a", "vendor": "xai", "model": "a"},
        {"route_class": "xai-model-b", "vendor": "xai", "model": "b"},
        {"route_class": "anthropic-model-x", "vendor": "anthropic", "model": "x"},
    ]


def _stall(route, attempt, cancel):
    return (False, "stall_then_timeout")


def _ok(route, attempt, cancel):
    return (True, "_ok")


def test_recovers_on_cross_vendor_route_via_same_vendor_first():
    def call(route, attempt, cancel):
        if route.get("route_class") == "anthropic-model-x":
            return (True, "recovered cross-vendor")
        return (False, "stall_then_timeout")

    result = VendorFallbackController(
        min_same_model_retries=2, same_vendor_routes=1, fallback_enabled=True
    ).run(call, _routes())
    assert result.ok is True
    kinds = [e["kind"] for e in result.events]
    assert "fallback_same_vendor" in kinds or "fallback_cross_vendor" in kinds


def test_cross_vendor_never_precedes_same_vendor():
    result = VendorFallbackController(
        min_same_model_retries=2, same_vendor_routes=1, fallback_enabled=True
    ).run(_stall, _routes())
    # Same-model budget on route 0 → same-vendor route 1 tried before cross-vendor.
    kinds = [e["kind"] for e in result.events]
    assert "fallback_same_vendor" in kinds
    assert "fallback_cross_vendor" in kinds
    assert kinds.index("fallback_same_vendor") < kinds.index("fallback_cross_vendor")


def test_min_same_model_retries_respected_before_fallback():
    result = VendorFallbackController(
        min_same_model_retries=3, same_vendor_routes=1, fallback_enabled=True
    ).run(_stall, _routes())
    same_model = [e for e in result.events if e["kind"] == "same_model_retry"]
    # 3 budget: strikes accumulate 1,2,3 then fallback; same_model failures >= 3.
    fb = [e for e in result.events if e["kind"].startswith("fallback_")]
    assert fb, "a fallback must eventually occur"
    assert len(same_model) >= 3


def test_fallback_disabled_is_killed():
    result = VendorFallbackController(
        min_same_model_retries=2, same_vendor_routes=1, fallback_enabled=False
    ).run(_stall, _routes())
    assert result.ok is False
    assert result.boundary == "fallback_disabled"
    assert not any(e["kind"].startswith("fallback_") for e in result.events)


def test_clean_primary_route_is_final_route():
    result = VendorFallbackController(
        min_same_model_retries=2, same_vendor_routes=1, fallback_enabled=True
    ).run(_ok, _routes())
    assert result.ok is True
    assert result.final_route.get("route_class") == "xai-model-a"
    assert all(e["kind"] != "run_failed" for e in result.events)