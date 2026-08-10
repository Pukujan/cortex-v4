"""Typed V4 mutation-port adapter for the separate fenced effect target.

The raw target process reports typed protocol errors. This adapter translates a
target ``StaleLeaseError`` into the controller-level
``MutationPortLeaseMismatch`` contract so the direct controller can durably
record a blocking recovery disposition instead of treating a split lease state
as an unclassified exception.
"""
from __future__ import annotations

from cortex_v4.control.direct_assurance_controller import (
    MutationObservation,
    MutationPortError,
    MutationPortLeaseMismatch,
)
from cortex_v4.control.fenced_effect_target import (
    EffectTargetProcessError,
    FencedEffectTargetClient,
)


class FencedEffectMutationPort:
    def __init__(self, client: FencedEffectTargetClient):
        self.client = client

    @staticmethod
    def _translate(exc: EffectTargetProcessError) -> MutationPortError:
        if exc.error_type == "StaleLeaseError":
            return MutationPortLeaseMismatch(exc.message)
        return MutationPortError(f"{exc.error_type}: {exc.message}")

    def observe(
        self,
        *,
        idempotency_key: str,
        epoch: int,
        fence_token: str,
    ) -> MutationObservation:
        try:
            return self.client.observe(
                idempotency_key=idempotency_key,
                epoch=epoch,
                fence_token=fence_token,
            )
        except EffectTargetProcessError as exc:
            raise self._translate(exc) from exc

    def apply(
        self,
        *,
        idempotency_key: str,
        epoch: int,
        fence_token: str,
    ) -> None:
        try:
            self.client.apply(
                idempotency_key=idempotency_key,
                epoch=epoch,
                fence_token=fence_token,
            )
        except EffectTargetProcessError as exc:
            raise self._translate(exc) from exc
