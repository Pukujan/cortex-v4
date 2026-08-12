"""Deterministic regressions for hosted recovery races found by CORTEX-05."""

from __future__ import annotations

import time
from types import SimpleNamespace

from cortex_v4.control import extended_task as et
from cortex_v4.control import temporal


def test_extended_task_watchdog_tracks_inactivity_not_total_recovery_batch(tmp_path):
    """Healthy checkpoint progress must not be reclassified as another stall."""

    class SlowProgressProvider(et.ExtendedTaskProvider):
        def _do_step(self, step: str) -> None:
            if step in et.POST_STALL_STEPS:
                # Each unit is below the watchdog deadline, but the recovered batch
                # deliberately takes longer than one deadline in aggregate.
                time.sleep(0.015)
            super()._do_step(step)

    workspace = tmp_path / "progress-aware-watchdog"
    et.seed_workspace(workspace)
    provider = SlowProgressProvider(
        workspace,
        et.StallThenTimeoutInjector(on_attempt=et.STALL_ATTEMPT, stall_s=1.0),
    )
    result = et.ExtendedTaskController(
        recovery_enabled=True,
        timeout_s=0.03,
        cancel_grace_s=0.5,
        checkpoint_write=True,
    ).run(provider)

    assert result.ok is True
    timeout_attempts = [
        event["attempt"] for event in result.events if event["kind"] == "timeout_requested"
    ]
    assert timeout_attempts == [et.STALL_ATTEMPT]
    assert result.post_stall_retries == 1
    assert provider.max_active == 1


def test_background_start_never_overwrites_state_written_by_spawned_supervisor(monkeypatch, tmp_path):
    """The parent launcher must not write a stale state snapshot after spawn."""

    def fake_spawn(state, *, supervisor=False):
        assert supervisor is True
        state_path = temporal.Path(state["state_path"])
        live = temporal._read(state_path)
        live.update(
            status="recovering",
            supervisor_pid=31337,
            worker_pid=4242,
            attempt=2,
            recovery_count=1,
            generation=1,
        )
        temporal._atomic(state_path, live)
        return SimpleNamespace(pid=31337)

    monkeypatch.setattr(temporal, "_spawn", fake_spawn)

    created = temporal.start(
        tmp_path,
        task_id="background-start-race",
        total_steps=4,
        max_recoveries=2,
        background=True,
    )
    persisted = temporal._read(temporal.Path(created["state_path"]))

    assert persisted["supervisor_pid"] == 31337
    assert persisted["worker_pid"] == 4242
    assert persisted["attempt"] == 2
    assert persisted["recovery_count"] == 1
    assert persisted["generation"] == 1
    assert persisted["status"] == "recovering"
