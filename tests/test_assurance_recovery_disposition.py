from __future__ import annotations

import pytest

from cortex_v4.control.assurance import (
    AssuranceError,
    AssuranceReferenceModel,
    AssuranceWorkOrder,
    MutationPhase,
    WorkEvent,
    WorkEventKind,
)


def _order(*, mutating=True):
    return AssuranceWorkOrder(
        work_order_id="wo-recovery",
        artifact_id="artifact-recovery",
        artifact_version="v1",
        mutating=mutating,
        initial_epoch=0,
        initial_fence_token="fence-0",
    )


def _event(kind, *, decision=None, evidence=()):
    return WorkEvent(
        work_order_id="wo-recovery",
        kind=kind,
        actor_id="recovery-controller",
        artifact_id="artifact-recovery",
        artifact_version="v1",
        epoch=0,
        fence_token="fence-0",
        evidence_refs=tuple(evidence),
        decision=decision,
    )


@pytest.mark.parametrize(
    "disposition",
    ["reobserve", "replay", "compensate", "block", "escalate"],
)
def test_recovery_disposition_is_durable_lineage_without_authority_upgrade(disposition):
    model = AssuranceReferenceModel(_order())
    intent = _event(WorkEventKind.MUTATION_INTENT)
    model.apply(intent)
    disposition_event = _event(
        WorkEventKind.MUTATION_RECOVERY_DISPOSITION,
        decision=disposition,
        evidence=(f"reason:{disposition}",),
    )
    snapshot = model.apply(disposition_event)

    assert snapshot.mutation_phase is MutationPhase.INTENT_DURABLE
    assert snapshot.mutation_decision is None
    assert snapshot.committed_success is False
    assert snapshot.event_cids == (intent.cid, disposition_event.cid)


def test_recovery_disposition_requires_unresolved_durable_intent():
    model = AssuranceReferenceModel(_order())
    with pytest.raises(AssuranceError, match="unresolved durable intent"):
        model.apply(
            _event(
                WorkEventKind.MUTATION_RECOVERY_DISPOSITION,
                decision="escalate",
                evidence=("ambiguous",),
            )
        )


def test_recovery_disposition_requires_evidence_and_known_value():
    model = AssuranceReferenceModel(_order())
    model.apply(_event(WorkEventKind.MUTATION_INTENT))
    with pytest.raises(AssuranceError, match="requires evidence"):
        model.apply(
            _event(
                WorkEventKind.MUTATION_RECOVERY_DISPOSITION,
                decision="escalate",
            )
        )
    with pytest.raises(AssuranceError, match="invalid mutation recovery disposition"):
        model.apply(
            _event(
                WorkEventKind.MUTATION_RECOVERY_DISPOSITION,
                decision="pretend-success",
                evidence=("bad",),
            )
        )


def test_recovery_disposition_is_idempotent_under_exact_replay():
    model = AssuranceReferenceModel(_order())
    model.apply(_event(WorkEventKind.MUTATION_INTENT))
    disposition = _event(
        WorkEventKind.MUTATION_RECOVERY_DISPOSITION,
        decision="escalate",
        evidence=("ambiguous",),
    )
    first = model.apply(disposition)
    second = model.apply(disposition)
    assert first == second
    assert second.event_cids.count(disposition.cid) == 1


def test_recovery_disposition_rejected_for_non_mutating_work():
    model = AssuranceReferenceModel(_order(mutating=False))
    with pytest.raises(AssuranceError, match="mutation event on non-mutating work"):
        model.apply(
            _event(
                WorkEventKind.MUTATION_RECOVERY_DISPOSITION,
                decision="block",
                evidence=("not-mutating",),
            )
        )
