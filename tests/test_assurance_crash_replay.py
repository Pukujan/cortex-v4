from cortex_v4.control.assurance import (
    AssuranceReferenceModel,
    AssuranceWorkOrder,
    AuthorityState,
    WorkEvent,
    WorkEventKind,
)


def _event(kind, *, actor, evidence=(), decision=None):
    return WorkEvent(
        work_order_id="wo-crash",
        kind=kind,
        actor_id=actor,
        artifact_id="artifact-crash",
        artifact_version="v1",
        epoch=0,
        fence_token="fence-0",
        evidence_refs=tuple(evidence),
        decision=decision,
    )


def test_reference_model_converges_after_restart_at_every_event_boundary():
    order = AssuranceWorkOrder(
        work_order_id="wo-crash",
        artifact_id="artifact-crash",
        artifact_version="v1",
        mutating=True,
        initial_epoch=0,
        initial_fence_token="fence-0",
    )
    events = [
        _event(WorkEventKind.ARTIFACT_PRODUCED, actor="producer"),
        _event(WorkEventKind.MUTATION_INTENT, actor="worker"),
        _event(
            WorkEventKind.MUTATION_EFFECT_OBSERVED,
            actor="observer",
            evidence=("effect-observation",),
        ),
        _event(
            WorkEventKind.MUTATION_DECISION,
            actor="controller",
            evidence=("decision-receipt",),
            decision="commit",
        ),
        _event(WorkEventKind.ARTIFACT_PERSISTED, actor="store"),
        _event(WorkEventKind.TEST_PASSED, actor="tester"),
        _event(WorkEventKind.EXTERNAL_OBSERVED, actor="observer"),
        _event(
            WorkEventKind.INDEPENDENT_VERIFIED,
            actor="verifier",
            evidence=("verification-receipt",),
        ),
        _event(WorkEventKind.MARK_PROMOTABLE, actor="controller"),
        _event(WorkEventKind.PROMOTE, actor="controller"),
    ]

    baseline = AssuranceReferenceModel.replay(order, events).snapshot
    assert baseline.authority_state is AuthorityState.PROMOTED
    assert baseline.committed_success is True

    for cut in range(len(events) + 1):
        # "Crash" by discarding the live object. Recovery rebuilds from the
        # durable prefix, then resumes from the first unapplied event.
        recovered = AssuranceReferenceModel.replay(order, events[:cut])
        for event in events[cut:]:
            recovered.apply(event)
        assert recovered.snapshot == baseline
