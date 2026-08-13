from cortex_v4.control.provider_preflight import (
    FactObservation,
    FactRequirement,
    SourceAttempt,
    SourceAttemptStatus,
    SourceAuthority,
    reconcile_provider_facts,
)


def test_preflight_report_preserves_official_source_attempt_receipt():
    attempt = SourceAttempt(
        source_ref="official-provider-docs",
        authority=SourceAuthority.OFFICIAL_PROVIDER,
        attempted_at="2026-08-10T21:50:00Z",
        status=SourceAttemptStatus.RETRIEVED,
        note="versioned provider route documentation",
    )
    report = reconcile_provider_facts(
        observations=[
            FactObservation(
                fact_key="stream_mode",
                value="stream",
                authority=SourceAuthority.OFFICIAL_PROVIDER,
                source_ref="official-provider-docs",
                retrieved_at="2026-08-10T21:50:00Z",
                fresh_until="2026-08-11T21:50:00Z",
            )
        ],
        requirements=[
            FactRequirement("stream_mode", SourceAuthority.OFFICIAL_PROVIDER)
        ],
        source_attempts=[attempt],
        evaluated_at="2026-08-10T21:50:00Z",
    )
    assert report.source_attempts == (attempt,)
    assert report.to_dict()["source_attempts"][0]["source_ref"] == "official-provider-docs"
    assert report.configuration_change_allowed is True
