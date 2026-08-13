from __future__ import annotations

import pytest

from cortex_v4.control.assurance import (
    AssuranceError,
    AssuranceReferenceModel,
    AssuranceWorkOrder,
    AuthorityState,
    MutationPhase,
    WorkEvent,
    WorkEventKind,
)


def _order(*, mutating: bool = False):
    return AssuranceWorkOrder(
        work_order_id="wo-1",
        artifact_id="artifact-1",
        artifact_version="v1",
        mutating=mutating,
        initial_epoch=0,
        initial_fence_token="fence-0",
    )


def _event(
    kind,
    *,
    actor="worker-a",
    epoch=0,
    fence="fence-0",
    parents=(),
    evidence=(),
    decision=None,
    version="v1",
):
    return WorkEvent(
        work_order_id="wo-1",
        kind=kind,
        actor_id=actor,
        artifact_id="artifact-1",
        artifact_version=version,
        epoch=epoch,
        fence_token=fence,
        parent_event_cids=tuple(parents),
        evidence_refs=tuple(evidence),
        decision=decision,
    )


def test_legacy_completed_and_model_verdict_cannot_upgrade_authority():
    model = AssuranceReferenceModel(_order())
    model.apply(_event(WorkEventKind.LEGACY_COMPLETED))
    model.apply(_event(WorkEventKind.MODEL_VERDICT, actor="model-x"))
    assert model.snapshot.authority_state is AuthorityState.PROPOSED
    assert model.snapshot.committed_success is False


def test_authority_ladder_refuses_skips():
    model = AssuranceReferenceModel(_order())
    with pytest.raises(AssuranceError):
        model.apply(_event(WorkEventKind.TEST_PASSED))


def test_mutation_requires_intent_effect_decision_before_committed_success():
    model = AssuranceReferenceModel(_order(mutating=True))
    intent = _event(WorkEventKind.MUTATION_INTENT)
    model.apply(intent)
    assert model.snapshot.mutation_phase is MutationPhase.INTENT_DURABLE
    assert model.snapshot.committed_success is False

    effect = _event(
        WorkEventKind.MUTATION_EFFECT_OBSERVED,
        parents=(intent.cid,),
        evidence=("obs-effect",),
    )
    model.apply(effect)
    assert model.snapshot.mutation_phase is MutationPhase.EFFECT_OBSERVED
    assert model.snapshot.committed_success is False

    decision = _event(
        WorkEventKind.MUTATION_DECISION,
        parents=(effect.cid,),
        evidence=("decision-receipt",),
        decision="commit",
    )
    model.apply(decision)
    assert model.snapshot.mutation_phase is MutationPhase.DECISION_DURABLE
    assert model.snapshot.committed_success is True


def test_effect_before_intent_is_refused():
    model = AssuranceReferenceModel(_order(mutating=True))
    with pytest.raises(AssuranceError):
        model.apply(
            _event(
                WorkEventKind.MUTATION_EFFECT_OBSERVED,
                evidence=("effect",),
            )
        )


def test_takeover_fences_stale_worker():
    model = AssuranceReferenceModel(_order())
    takeover = _event(
        WorkEventKind.LEASE_TAKEOVER,
        actor="controller",
        epoch=1,
        fence="fence-1",
    )
    model.apply(takeover)
    with pytest.raises(AssuranceError):
        model.apply(_event(WorkEventKind.ARTIFACT_PRODUCED, epoch=0, fence="fence-0"))

    model.apply(
        _event(
            WorkEventKind.ARTIFACT_PRODUCED,
            actor="worker-b",
            epoch=1,
            fence="fence-1",
            parents=(takeover.cid,),
        )
    )
    assert model.snapshot.producer_actor_id == "worker-b"


def test_artifact_version_mismatch_is_refused():
    model = AssuranceReferenceModel(_order())
    with pytest.raises(AssuranceError):
        model.apply(_event(WorkEventKind.ARTIFACT_PRODUCED, version="v2"))


def test_missing_causal_parent_is_refused():
    model = AssuranceReferenceModel(_order())
    with pytest.raises(AssuranceError):
        model.apply(
            _event(
                WorkEventKind.ARTIFACT_PRODUCED,
                parents=("evt_missing",),
            )
        )


def test_duplicate_event_replay_is_idempotent():
    model = AssuranceReferenceModel(_order())
    event = _event(WorkEventKind.ARTIFACT_PRODUCED)
    first = model.apply(event)
    second = model.apply(event)
    assert first == second
    assert second.event_cids == (event.cid,)


def test_independent_verifier_must_not_be_producer_and_needs_evidence():
    model = AssuranceReferenceModel(_order())
    events = [
        _event(WorkEventKind.ARTIFACT_PRODUCED, actor="producer"),
        _event(WorkEventKind.ARTIFACT_PERSISTED, actor="store"),
        _event(WorkEventKind.TEST_PASSED, actor="tester"),
        _event(WorkEventKind.EXTERNAL_OBSERVED, actor="observer"),
    ]
    for event in events:
        model.apply(event)

    with pytest.raises(AssuranceError):
        model.apply(
            _event(
                WorkEventKind.INDEPENDENT_VERIFIED,
                actor="producer",
                evidence=("verify-receipt",),
            )
        )
    with pytest.raises(AssuranceError):
        model.apply(_event(WorkEventKind.INDEPENDENT_VERIFIED, actor="verifier"))

    model.apply(
        _event(
            WorkEventKind.INDEPENDENT_VERIFIED,
            actor="verifier",
            evidence=("verify-receipt",),
        )
    )
    assert model.snapshot.authority_state is AuthorityState.INDEPENDENTLY_VERIFIED


def test_mutating_work_cannot_promote_without_commit_decision():
    model = AssuranceReferenceModel(_order(mutating=True))
    for event in [
        _event(WorkEventKind.ARTIFACT_PRODUCED, actor="producer"),
        _event(WorkEventKind.ARTIFACT_PERSISTED, actor="store"),
        _event(WorkEventKind.TEST_PASSED, actor="tester"),
        _event(WorkEventKind.EXTERNAL_OBSERVED, actor="observer"),
        _event(
            WorkEventKind.INDEPENDENT_VERIFIED,
            actor="verifier",
            evidence=("verify",),
        ),
        _event(WorkEventKind.MARK_PROMOTABLE, actor="controller"),
    ]:
        model.apply(event)
    with pytest.raises(AssuranceError):
        model.apply(_event(WorkEventKind.PROMOTE, actor="controller"))


def test_replay_converges_to_same_snapshot():
    order = _order(mutating=True)
    events = [
        _event(WorkEventKind.ARTIFACT_PRODUCED, actor="producer"),
        _event(WorkEventKind.MUTATION_INTENT, actor="worker"),
        _event(
            WorkEventKind.MUTATION_EFFECT_OBSERVED,
            actor="worker",
            evidence=("effect",),
        ),
        _event(
            WorkEventKind.MUTATION_DECISION,
            actor="controller",
            evidence=("decision",),
            decision="commit",
        ),
        _event(WorkEventKind.ARTIFACT_PERSISTED, actor="store"),
        _event(WorkEventKind.TEST_PASSED, actor="tester"),
        _event(WorkEventKind.EXTERNAL_OBSERVED, actor="observer"),
        _event(
            WorkEventKind.INDEPENDENT_VERIFIED,
            actor="verifier",
            evidence=("verify",),
        ),
        _event(WorkEventKind.MARK_PROMOTABLE, actor="controller"),
        _event(WorkEventKind.PROMOTE, actor="controller"),
    ]
    first = AssuranceReferenceModel.replay(order, events)
    second = AssuranceReferenceModel.replay(order, events + [events[-1]])
    assert first.snapshot == second.snapshot
    assert first.snapshot.authority_state is AuthorityState.PROMOTED
    assert first.snapshot.committed_success is True
