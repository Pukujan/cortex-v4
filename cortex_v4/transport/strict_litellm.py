"""Strict Cortex V4 LiteLLM profile for staging P0."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from .litellm import ChatResult, LiteLLMError, LiteLLMTransport

STRICT_PROFILE = "p0-local-staging-zero-retry-v1"


@dataclass(frozen=True)
class StrictReceipt:
    base: Any
    config_profile: str
    transport_retries: int = 0
    semantic_fallbacks: bool = False

    def as_dict(self) -> dict[str, Any]:
        value = dict(self.base.as_dict())
        value.update({
            "config_profile": self.config_profile,
            "transport_retries": self.transport_retries,
            "semantic_fallbacks": self.semantic_fallbacks,
        })
        return value

    def __getattr__(self, name: str) -> Any:
        return getattr(self.base, name)


class StrictLiteLLMTransport(LiteLLMTransport):
    def __init__(self, *args: Any, config_profile: str = STRICT_PROFILE, **kwargs: Any):
        if config_profile != STRICT_PROFILE:
            raise ValueError(f"strict Cortex requires config profile {STRICT_PROFILE}")
        super().__init__(*args, **kwargs)
        self.config_profile = config_profile

    def _tag(self, receipt: Any) -> StrictReceipt:
        return StrictReceipt(receipt, self.config_profile)

    def _effective_timeout_layer(self) -> str:
        positive = {
            key: float(value)
            for key, value in self.timeout_layers.values().items()
            if value is not None and float(value) > 0
        }
        if not positive:
            return "unknown"
        minimum = min(positive.values())
        winners = [key for key, value in positive.items() if value == minimum]
        if len(winners) != 1:
            return "effective_deadline"
        return {
            "provider_deadline_s": "provider",
            "litellm_request_s": "litellm",
            "client_request_s": "client",
            "stage_deadline_s": "stage",
            "inactivity_watchdog_s": "inactivity_watchdog",
            "campaign_deadline_s": "campaign",
        }.get(winners[0], "unknown")

    def chat(self, **kwargs: Any) -> ChatResult:
        try:
            result = super().chat(**kwargs)
        except LiteLLMError as exc:
            receipt = exc.receipt
            if receipt is not None and exc.classification == "client_timeout":
                receipt = replace(receipt, timeout_layer=self._effective_timeout_layer())
            if receipt is not None:
                receipt = self._tag(receipt)
            raise LiteLLMError(exc.classification, str(exc), receipt=receipt) from None

        requested = result.receipt.requested_model
        if result.actual_model and result.actual_model != requested:
            failed = replace(
                result.receipt,
                usable_output=False,
                result_classification="model_substitution",
            )
            raise LiteLLMError(
                "model_substitution",
                "strict Cortex rejected a non-requested actual model",
                receipt=self._tag(failed),
            )
        return ChatResult(
            result.text,
            result.actual_model,
            result.tool_calls,
            result.finish_reason,
            self._tag(result.receipt),
        )

    def responses(self, **_: Any):
        raise LiteLLMError(
            "noncanonical_endpoint",
            "strict Cortex P0 uses Chat Completions streaming; translated Responses streaming is disabled",
            receipt=None,
        )
