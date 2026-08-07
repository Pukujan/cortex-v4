"""Vendor fallback matrix for the first-loop recovery contract.

The public failure-injector recovery_contract requires min_same_model_retries
before fallback, then same-vendor fallback, then cross-vendor fallback. This
module models that rotation deterministically against V4 control so the
matrix is exercised without real provider spend (live multi-vendor attach
remains a separate ENVIRONMENT gate).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class FallbackAttempt:
    attempt: int
    route: dict[str, Any]
    kind: str  # same_model | same_vendor | cross_vendor
    ok: bool
    final: str


@dataclass
class FallbackResult:
    ok: bool
    run_id: str
    lineage_id: str
    attempts: list[FallbackAttempt] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    final_route: dict[str, Any] = field(default_factory=dict)
    boundary: str = ""


class VendorFallbackController:
    """Enforce the recovery_contract fallback ordering against a callable provider.

    ``call(route, attempt, cancel)`` returns (ok, final). Stalls/timeouts on the
    primary model must be retried same-model min_same_model_retries times before
    same-vendor fallback, and cross-vendor only after same-vendor is exhausted.
    """

    def __init__(
        self,
        *,
        min_same_model_retries: int = 3,
        timeout_s: float = 0.02,
        cancel_grace_s: float = 0.05,
        same_vendor_routes: int = 1,
        fallback_enabled: bool = True,
    ):
        self.min_same_model_retries = int(min_same_model_retries)
        self.timeout_s = float(timeout_s)
        self.cancel_grace_s = float(cancel_grace_s)
        self.same_vendor_routes = int(same_vendor_routes)
        self.fallback_enabled = fallback_enabled
        self.events: list[dict] = []

    def _event(self, kind: str, **fields: Any) -> None:
        self.events.append({"event_seq": len(self.events), "ts": time.time(), "kind": kind, **fields})

    def run(
        self,
        call: Callable[[dict[str, Any], int, Any], tuple[bool, str]],
        routes: list[dict[str, Any]],
        *,
        same_vendor_routes: int = 1,
    ) -> FallbackResult:
        lineage_id = uuid.uuid4().hex[:12]
        run_id = uuid.uuid4().hex[:12]
        attempts: list[FallbackAttempt] = []
        same_model_strikes = 0
        same_vendor_strikes = 0
        idx = 0
        route = routes[0]
        cancel = _CancelEvent()
        boundary = ""

        while idx < len(routes):
            self._event("dispatch_attempt", run_id=run_id, attempt=idx + 1,
                        route_class=route.get("route_class"), vendor=route.get("vendor"))
            ok, final = call(route, idx + 1, cancel)
            attempts.append(
                FallbackAttempt(idx + 1, dict(route), _kind(idx, same_model_strikes), ok, final)
            )
            if ok:
                self._event("run_completed", run_id=run_id, attempt=idx + 1,
                            route_class=route.get("route_class"), vendor=route.get("vendor"))
                return FallbackResult(True, run_id, lineage_id, attempts, list(self.events), dict(route), boundary)

            if final in {"stall", "timeout", "stall_then_timeout"}:
                same_model_strikes += 1
                if same_model_strikes < self.min_same_model_retries:
                    self._event("same_model_retry", run_id=run_id, attempt=idx + 1,
                                strike=same_model_strikes)
                    continue
                # Same-model budget exhausted on this route → same-vendor next.
                if not self.fallback_enabled:
                    boundary = "fallback_disabled"
                    self._event("run_failed", run_id=run_id, reason="fallback_disabled")
                    return FallbackResult(False, run_id, lineage_id, attempts, list(self.events), dict(route), boundary)
                same_vendor_strikes += 1
                if same_vendor_strikes <= same_vendor_routes and idx + 1 < len(routes):
                    nxt = routes[idx + 1]
                    if nxt.get("vendor") == route.get("vendor"):
                        self._event("fallback_same_vendor", run_id=run_id, from_route=route.get("route_class"),
                                    to_route=nxt.get("route_class"))
                        idx += 1
                        route = nxt
                        same_model_strikes = 0
                        continue
                # Same-vendor options exhausted → cross-vendor.
                nxt = next(
                    (r for r in routes[idx + 1:] if r.get("vendor") != route.get("vendor")), None
                )
                if nxt is not None:
                    self._event("fallback_cross_vendor", run_id=run_id, from_route=route.get("route_class"),
                                to_route=nxt.get("route_class"), from_vendor=route.get("vendor"),
                                to_vendor=nxt.get("vendor"))
                    idx = routes.index(nxt)
                    route = nxt
                    same_model_strikes = 0
                    same_vendor_strikes = 0
                    continue
                boundary = "all_routes_exhausted"
                self._event("run_failed", run_id=run_id, reason="all_routes_exhausted")
                return FallbackResult(False, run_id, lineage_id, attempts, list(self.events), dict(route), boundary)

            boundary = "non_stall_failure"
            self._event("run_failed", run_id=run_id, reason="non_stall_failure", final=final)
            return FallbackResult(False, run_id, lineage_id, attempts, list(self.events), dict(route), boundary)

        boundary = boundary or "routes_exhausted"
        self._event("run_failed", run_id=run_id, reason="routes_exhausted")
        return FallbackResult(False, run_id, lineage_id, attempts, list(self.events), dict(route), boundary)


def _kind(route_index: int, same_model_strikes: int) -> str:
    if same_model_strikes > 0:
        return "same_model"
    if route_index > 0:
        return "same_vendor"
    return "same_model"


class _CancelEvent:
    def __init__(self) -> None:
        self.set = False

    def is_set(self) -> bool:
        return self.set
