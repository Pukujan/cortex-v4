"""Source-ranked provider preflight for Cortex V4.

The preflight has two deliberately separate responsibilities:

1. reconcile provider/config facts without allowing model-authored receipts to
   establish provider truth; and
2. collect the three no-spend LiteLLM metadata endpoints and immediately reduce
   their bodies into the sanitized :mod:`litellm_manifest` representation.

Credential material is accepted only as an input to the collector. It is never
stored in a receipt, manifest, exception message, or returned request object.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
import json
import time
from typing import Any, Callable, Mapping, Sequence
import urllib.error
import urllib.parse
import urllib.request

from cortex_v4.control.litellm_manifest import (
    LiteLLMRouteManifest,
    ManifestError,
    build_route_manifest,
)


class PreflightError(ValueError):
    """Raised when provider preflight evidence is structurally invalid."""


class SourceAuthority(str, Enum):
    OFFICIAL_PROVIDER = "official_provider"
    OBSERVED_RUNTIME = "observed_runtime"
    VERSIONED_GATEWAY_CONFIG = "versioned_gateway_config"
    VERSIONED_REPOSITORY_CONFIG = "versioned_repository_config"
    MODEL_AUTHORED = "model_authored"

    @property
    def rank(self) -> int:
        return {
            SourceAuthority.OFFICIAL_PROVIDER: 100,
            SourceAuthority.OBSERVED_RUNTIME: 90,
            SourceAuthority.VERSIONED_GATEWAY_CONFIG: 80,
            SourceAuthority.VERSIONED_REPOSITORY_CONFIG: 70,
            SourceAuthority.MODEL_AUTHORED: 0,
        }[self]


class SourceAttemptStatus(str, Enum):
    RETRIEVED = "retrieved"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


class FactStatus(str, Enum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    CONTRADICTED = "contradicted"


@dataclass(frozen=True)
class SourceAttempt:
    source_ref: str
    authority: SourceAuthority
    attempted_at: str
    status: SourceAttemptStatus
    note: str | None = None


@dataclass(frozen=True)
class FactObservation:
    fact_key: str
    value: Any
    authority: SourceAuthority
    source_ref: str
    retrieved_at: str
    fresh_until: str | None = None


@dataclass(frozen=True)
class FactRequirement:
    fact_key: str
    minimum_authority: SourceAuthority


@dataclass(frozen=True)
class ReconciledFact:
    fact_key: str
    status: FactStatus
    value: Any | None
    authority: SourceAuthority | None
    source_refs: tuple[str, ...]
    reason: str | None = None


@dataclass(frozen=True)
class ProviderPreflightReport:
    evaluated_at: str
    source_attempts: tuple[SourceAttempt, ...]
    official_provider_source_attempted: bool
    facts: tuple[ReconciledFact, ...]
    configuration_change_allowed: bool
    escalation_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EndpointReceipt:
    path: str
    status: int
    first_byte_ms: float | None
    total_ms: float
    error_type: str | None = None


@dataclass(frozen=True)
class InventoryCollectionReceipt:
    base_host: str
    observed_at: str
    endpoints: tuple[EndpointReceipt, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ObservableProbeReceipt:
    node: str
    upstream_model: str
    status: int
    first_byte_ms: float
    total_ms: float
    retries: int
    stream_mode: str
    observed_at: str

    def validate(self) -> None:
        if not self.node.strip() or not self.upstream_model.strip():
            raise PreflightError("probe node and upstream_model are required")
        if self.status < 0:
            raise PreflightError("probe status must be non-negative")
        if self.first_byte_ms < 0 or self.total_ms < 0:
            raise PreflightError("probe timings must be non-negative")
        if self.total_ms < self.first_byte_ms:
            raise PreflightError("probe total_ms cannot be smaller than first_byte_ms")
        if self.retries < 0:
            raise PreflightError("probe retries must be non-negative")
        if self.stream_mode not in {"stream", "non_stream"}:
            raise PreflightError("probe stream_mode must be stream or non_stream")
        _parse_time(self.observed_at)


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise PreflightError(f"invalid RFC3339/ISO timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise PreflightError("timestamps must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _canonical_value(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _is_fresh(observation: FactObservation, evaluated_at: datetime) -> bool:
    _parse_time(observation.retrieved_at)
    if observation.fresh_until is None:
        return True
    return evaluated_at <= _parse_time(observation.fresh_until)


def reconcile_provider_facts(
    *,
    observations: Sequence[FactObservation],
    requirements: Sequence[FactRequirement],
    source_attempts: Sequence[SourceAttempt],
    evaluated_at: str,
) -> ProviderPreflightReport:
    """Reconcile required facts using only fresh evidence at sufficient authority.

    Conflicts at the highest eligible authority rank fail closed. Model-authored
    observations have rank zero and therefore cannot satisfy any requirement
    whose minimum authority is above ``MODEL_AUTHORED``.
    """
    now = _parse_time(evaluated_at)
    for attempt in source_attempts:
        _parse_time(attempt.attempted_at)

    official_attempted = any(
        attempt.authority is SourceAuthority.OFFICIAL_PROVIDER
        for attempt in source_attempts
    )
    escalation: list[str] = []
    if not official_attempted:
        escalation.append("official/provider source was not attempted")

    by_key: dict[str, list[FactObservation]] = {}
    for observation in observations:
        by_key.setdefault(observation.fact_key, []).append(observation)

    reconciled: list[ReconciledFact] = []
    for requirement in requirements:
        candidates = [
            observation
            for observation in by_key.get(requirement.fact_key, [])
            if observation.authority.rank >= requirement.minimum_authority.rank
            and observation.authority is not SourceAuthority.MODEL_AUTHORED
            and _is_fresh(observation, now)
        ]
        if not candidates:
            reason = (
                f"{requirement.fact_key}: no fresh evidence at or above "
                f"{requirement.minimum_authority.value}"
            )
            reconciled.append(
                ReconciledFact(
                    fact_key=requirement.fact_key,
                    status=FactStatus.UNVERIFIED,
                    value=None,
                    authority=None,
                    source_refs=(),
                    reason=reason,
                )
            )
            escalation.append(reason)
            continue

        top_rank = max(candidate.authority.rank for candidate in candidates)
        top = [candidate for candidate in candidates if candidate.authority.rank == top_rank]
        values: dict[str, list[FactObservation]] = {}
        for candidate in top:
            values.setdefault(_canonical_value(candidate.value), []).append(candidate)

        if len(values) != 1:
            refs = tuple(sorted({candidate.source_ref for candidate in top}))
            reason = f"{requirement.fact_key}: contradictory top-authority evidence"
            reconciled.append(
                ReconciledFact(
                    fact_key=requirement.fact_key,
                    status=FactStatus.CONTRADICTED,
                    value=None,
                    authority=top[0].authority,
                    source_refs=refs,
                    reason=reason,
                )
            )
            escalation.append(reason)
            continue

        representative = top[0]
        reconciled.append(
            ReconciledFact(
                fact_key=requirement.fact_key,
                status=FactStatus.VERIFIED,
                value=representative.value,
                authority=representative.authority,
                source_refs=tuple(sorted({candidate.source_ref for candidate in top})),
            )
        )

    return ProviderPreflightReport(
        evaluated_at=evaluated_at,
        source_attempts=tuple(source_attempts),
        official_provider_source_attempted=official_attempted,
        facts=tuple(reconciled),
        configuration_change_allowed=not escalation,
        escalation_reasons=tuple(escalation),
    )


def _read_json_endpoint(
    *,
    base_url: str,
    path: str,
    bearer_key: str,
    timeout_s: float,
    opener: Callable[..., Any],
) -> tuple[int, Mapping[str, Any], EndpointReceipt]:
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        headers={"Authorization": f"Bearer {bearer_key}"},
        method="GET",
    )
    started = time.monotonic()
    first_byte_ms: float | None = None
    status = 0
    error_type: str | None = None
    body: Mapping[str, Any] = {}
    try:
        with opener(request, timeout=timeout_s) as response:
            status = int(getattr(response, "status", 0))
            first = response.read(1)
            first_byte_ms = round((time.monotonic() - started) * 1000, 3)
            raw = first + response.read()
            decoded = json.loads(raw.decode("utf-8", "replace")) if raw else {}
            body = decoded if isinstance(decoded, Mapping) else {}
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        error_type = "HTTPError"
    except Exception as exc:  # noqa: BLE001 - sanitize to type only
        error_type = type(exc).__name__
    total_ms = round((time.monotonic() - started) * 1000, 3)
    return (
        status,
        body,
        EndpointReceipt(
            path=path,
            status=status,
            first_byte_ms=first_byte_ms,
            total_ms=total_ms,
            error_type=error_type,
        ),
    )


def collect_litellm_manifest(
    *,
    base_url: str,
    bearer_key: str,
    observed_at: str | None = None,
    timeout_s: float = 30.0,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> tuple[LiteLLMRouteManifest, InventoryCollectionReceipt]:
    """Collect the no-spend LiteLLM inventory and return only sanitized outputs.

    The bearer key is used to construct request headers and then discarded. The
    returned objects contain the base hostname, endpoint timing/status receipts,
    and normalized route/model metadata only.
    """
    if not base_url.strip():
        raise PreflightError("base_url is required")
    if not bearer_key:
        raise PreflightError("bearer_key is required")
    if timeout_s <= 0:
        raise PreflightError("timeout_s must be positive")

    when = observed_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    _parse_time(when)
    base_host = urllib.parse.urlparse(base_url).hostname or ""
    if not base_host:
        raise PreflightError("base_url must contain a hostname")

    endpoint_data: dict[str, tuple[int, Mapping[str, Any]]] = {}
    receipts: list[EndpointReceipt] = []
    for path in ("/health/liveliness", "/v1/models", "/model/info"):
        status, body, receipt = _read_json_endpoint(
            base_url=base_url,
            path=path,
            bearer_key=bearer_key,
            timeout_s=timeout_s,
            opener=opener,
        )
        endpoint_data[path] = (status, body)
        receipts.append(receipt)

    health_status, _ = endpoint_data["/health/liveliness"]
    models_status, models_payload = endpoint_data["/v1/models"]
    info_status, info_payload = endpoint_data["/model/info"]
    try:
        manifest = build_route_manifest(
            base_host=base_host,
            observed_at=when,
            health_status=health_status,
            models_status=models_status,
            model_info_status=info_status,
            models_payload=models_payload,
            model_info_payload=info_payload,
        )
    except ManifestError as exc:
        # ManifestError carries statuses/shape information only; do not attach
        # request headers, bearer material, or raw bodies.
        raise PreflightError(str(exc)) from exc

    return manifest, InventoryCollectionReceipt(
        base_host=base_host,
        observed_at=when,
        endpoints=tuple(receipts),
    )
