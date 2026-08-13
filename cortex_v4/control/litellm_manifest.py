"""Conservative LiteLLM inventory normalization for the V4 provider preflight.

This module does not make network calls and never accepts credentials.  It turns
already-fetched ``/v1/models`` and ``/model/info`` payloads into a deterministic,
sanitized manifest whose transport topology is kept separate from epistemic
model identity.

The default identity policy is intentionally conservative:

* exact ``litellm_params.model`` strings may identify the same configured
  upstream model across deployments;
* route labels, API hosts, credentials, and repeated deployment rows never
  create independent epistemic identities; and
* independence groups remain UNKNOWN until some other evidence source assigns
  them.

Responses-bridge receipts are normalized separately so requested and actual
models cannot be confused when LiteLLM performs cross-model fallback.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import re
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

SCHEMA = "cortex.v4.litellm_route_manifest.v1"
RECEIPT_SCHEMA = "cortex.v4.litellm_inference_receipt.v1"
UNKNOWN = "UNKNOWN"
_ROUTE_LABEL = re.compile(r"^\s*(\[[^\]]+\])")


class ManifestError(ValueError):
    """Raised when a fresh LiteLLM inventory cannot be admitted."""


def _canonical_sha256(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _route_label(alias: str) -> str | None:
    match = _ROUTE_LABEL.match(alias)
    return match.group(1) if match else None


def _host(api_base: Any) -> str | None:
    if not isinstance(api_base, str) or not api_base:
        return None
    return urlparse(api_base).hostname


@dataclass(frozen=True)
class GatewayReceipt:
    base_host: str
    health_status: int
    models_status: int
    model_info_status: int
    source_digest: str


@dataclass(frozen=True)
class AliasRecord:
    alias_id: str
    public_name: str
    deployment_ids: tuple[str, ...]


@dataclass(frozen=True)
class DeploymentRecord:
    deployment_id: str
    alias_id: str
    public_name: str
    upstream_model_hint: str | None
    transport_host: str | None
    route_label: str | None
    max_input_tokens: int | None
    context_window: int | None


@dataclass(frozen=True)
class EpistemicIdentityRecord:
    epistemic_identity_id: str
    canonical_model_id: str
    supporting_deployment_ids: tuple[str, ...]
    independence_group_id: str | None = None


@dataclass(frozen=True)
class LiteLLMRouteManifest:
    schema_version: str
    observed_at: str
    gateway_receipt: GatewayReceipt
    aliases: tuple[AliasRecord, ...]
    deployments: tuple[DeploymentRecord, ...]
    epistemic_identities: tuple[EpistemicIdentityRecord, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def independent_verifier_count(self) -> int:
        """Count only explicitly evidenced independence groups.

        A route pool of N deployments therefore contributes zero until another
        evidence source assigns an independence group.
        """
        return len(
            {
                identity.independence_group_id
                for identity in self.epistemic_identities
                if identity.independence_group_id
            }
        )


@dataclass(frozen=True)
class RouteAttempt:
    model: str
    status: int | None
    reason: str | None


@dataclass(frozen=True)
class LiteLLMInferenceReceipt:
    schema_version: str
    requested_model: str
    actual_model: str | None
    attempts: tuple[RouteAttempt, ...]
    fallback_allowed: bool
    eligible_for_model_specific_credit: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_route_manifest(
    *,
    base_host: str,
    observed_at: str,
    health_status: int,
    models_status: int,
    model_info_status: int,
    models_payload: Mapping[str, Any],
    model_info_payload: Mapping[str, Any],
) -> LiteLLMRouteManifest:
    """Build one deterministic sanitized route manifest.

    All three metadata endpoints must have succeeded.  The returned manifest
    contains no raw LiteLLM parameter dictionaries, bearer keys, response
    bodies, or arbitrary provider metadata.
    """
    statuses = {
        "health": health_status,
        "models": models_status,
        "model_info": model_info_status,
    }
    failed = {name: status for name, status in statuses.items() if status != 200}
    if failed:
        raise ManifestError(f"inventory gate failed: {failed}")

    model_rows = models_payload.get("data")
    info_rows = model_info_payload.get("data")
    if not isinstance(model_rows, list) or not isinstance(info_rows, list):
        raise ManifestError("inventory payloads must contain list-valued data fields")

    public_names: set[str] = set()
    for row in model_rows:
        if isinstance(row, Mapping):
            model_id = row.get("id")
            if isinstance(model_id, str) and model_id.strip():
                public_names.add(model_id.strip())

    sanitized_rows: list[dict[str, Any]] = []
    for row in info_rows:
        if not isinstance(row, Mapping):
            raise ManifestError("model_info data rows must be objects")
        alias = row.get("model_name")
        if not isinstance(alias, str) or not alias.strip():
            raise ManifestError("model_info row missing model_name")
        alias = alias.strip()
        public_names.add(alias)

        params = row.get("litellm_params")
        params = params if isinstance(params, Mapping) else {}
        info = row.get("model_info")
        info = info if isinstance(info, Mapping) else {}

        upstream = params.get("model")
        upstream = upstream.strip() if isinstance(upstream, str) and upstream.strip() else None
        max_input = info.get("max_input_tokens")
        context_window = info.get("context_window")
        sanitized_rows.append(
            {
                "public_name": alias,
                "upstream_model_hint": upstream,
                "transport_host": _host(params.get("api_base")),
                "route_label": _route_label(alias),
                "max_input_tokens": max_input if isinstance(max_input, int) else None,
                "context_window": context_window if isinstance(context_window, int) else None,
            }
        )

    sanitized_rows.sort(
        key=lambda row: (
            row["public_name"],
            row["upstream_model_hint"] or "",
            row["transport_host"] or "",
            row["max_input_tokens"] if row["max_input_tokens"] is not None else -1,
            row["context_window"] if row["context_window"] is not None else -1,
        )
    )

    deployments: list[DeploymentRecord] = []
    by_alias: dict[str, list[str]] = {name: [] for name in public_names}
    identity_groups: dict[tuple[str, str], list[str]] = {}

    for index, row in enumerate(sanitized_rows):
        projection = {"index": index, **row}
        deployment_id = "dep_" + _canonical_sha256(projection)[:24]
        alias_id = "alias_" + _canonical_sha256(row["public_name"])[:24]
        deployments.append(
            DeploymentRecord(
                deployment_id=deployment_id,
                alias_id=alias_id,
                public_name=row["public_name"],
                upstream_model_hint=row["upstream_model_hint"],
                transport_host=row["transport_host"],
                route_label=row["route_label"],
                max_input_tokens=row["max_input_tokens"],
                context_window=row["context_window"],
            )
        )
        by_alias.setdefault(row["public_name"], []).append(deployment_id)

        # UNKNOWN identities are scoped to the public alias so unrelated
        # unknown routes do not collapse into one fake model.
        identity_key = (
            "exact" if row["upstream_model_hint"] else "unknown",
            row["upstream_model_hint"] or row["public_name"],
        )
        identity_groups.setdefault(identity_key, []).append(deployment_id)

    aliases = tuple(
        AliasRecord(
            alias_id="alias_" + _canonical_sha256(name)[:24],
            public_name=name,
            deployment_ids=tuple(sorted(by_alias.get(name, []))),
        )
        for name in sorted(public_names)
    )

    identities: list[EpistemicIdentityRecord] = []
    for (kind, value), deployment_ids in sorted(identity_groups.items()):
        canonical = value if kind == "exact" else UNKNOWN
        identity_seed = {"kind": kind, "value": value}
        identities.append(
            EpistemicIdentityRecord(
                epistemic_identity_id="model_" + _canonical_sha256(identity_seed)[:24],
                canonical_model_id=canonical,
                supporting_deployment_ids=tuple(sorted(deployment_ids)),
                independence_group_id=None,
            )
        )

    normalized = {
        "base_host": base_host,
        "public_names": sorted(public_names),
        "deployments": sanitized_rows,
    }
    receipt = GatewayReceipt(
        base_host=base_host,
        health_status=health_status,
        models_status=models_status,
        model_info_status=model_info_status,
        source_digest=_canonical_sha256(normalized),
    )
    return LiteLLMRouteManifest(
        schema_version=SCHEMA,
        observed_at=observed_at,
        gateway_receipt=receipt,
        aliases=aliases,
        deployments=tuple(deployments),
        epistemic_identities=tuple(identities),
    )


def normalize_inference_receipt(
    *,
    requested_model: str,
    response_metadata: Mapping[str, Any] | None,
    fallback_allowed: bool,
) -> LiteLLMInferenceReceipt:
    """Normalize bridge provenance without granting authority from HTTP 200 alone."""
    metadata = response_metadata or {}
    actual = metadata.get("bridge_actual_model")
    actual = actual.strip() if isinstance(actual, str) and actual.strip() else None

    attempts_raw = metadata.get("bridge_attempts")
    attempts: list[RouteAttempt] = []
    if isinstance(attempts_raw, Sequence) and not isinstance(attempts_raw, (str, bytes)):
        for raw in attempts_raw:
            if not isinstance(raw, Mapping):
                continue
            model = raw.get("model")
            if not isinstance(model, str) or not model.strip():
                continue
            status = raw.get("status")
            reason = raw.get("reason")
            attempts.append(
                RouteAttempt(
                    model=model.strip(),
                    status=status if isinstance(status, int) else None,
                    reason=reason if isinstance(reason, str) else None,
                )
            )

    if not fallback_allowed and actual is None:
        # Exact-model mode makes a successful response attributable to the
        # requested model when the bridge omits redundant metadata.
        actual = requested_model

    exact_identity_ok = fallback_allowed or actual == requested_model
    eligible = actual is not None and exact_identity_ok
    return LiteLLMInferenceReceipt(
        schema_version=RECEIPT_SCHEMA,
        requested_model=requested_model,
        actual_model=actual,
        attempts=tuple(attempts),
        fallback_allowed=fallback_allowed,
        eligible_for_model_specific_credit=eligible,
    )
