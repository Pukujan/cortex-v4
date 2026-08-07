import json
import threading
import time

from cortex_v4.control import LongRunningController, ScriptedProvider, start, status, supervise
from cortex_v4.control.temporal import _kill


def test_v4a_reproduces_the_old_retry_overlap_failure():
    provider = ScriptedProvider(cooperative=False)
    result = LongRunningController(timeout_s=0.02, cancel_grace_s=0.01,
                                   max_retries=1, legacy_overlap=True).run(provider)

    assert result.ok is True  # V4-A falsely succeeds while violating the liveness invariant.
    assert provider.calls == [0, 1]
    assert provider.max_active == 2
    assert any(event.get("overlap") is True for event in result.events)


def test_v4b_repairs_the_same_failure_independently():
    provider = ScriptedProvider(cooperative=True)
    result = LongRunningController(timeout_s=0.02, cancel_grace_s=0.1,
                                   max_retries=1).run(provider)

    assert result.ok is True
    assert provider.calls == [0, 1]
    assert provider.max_active == 1
    assert any(event["kind"] == "cancel_acknowledged" for event in result.events)
    assert any(event["kind"] == "attempt_fenced" and event.get("generation") == 1
               for event in result.events)
    assert not any(event.get("overlap") is True for event in result.events)


def test_v4c_temporal_controller_recovers_a_120_step_worker(tmp_path):
    created = start(tmp_path, task_id="v4-temporal-120", total_steps=120,
                    max_recoveries=2, background=True)
    state_path = created["state_path"]
    holder = {}

    def wait_for_completion():
        holder["result"] = supervise(state_path)

    # The background supervisor from start() is already active. The direct supervisor is not
    # started here; instead observe its worker and interrupt it through the persisted PID.
    deadline = time.time() + 20
    pid = None
    while time.time() < deadline:
        current = status(state_path)
        if current["cursor"]["step"] >= 8 and current.get("worker_pid"):
            pid = current["worker_pid"]
            break
        time.sleep(0.05)
    assert pid is not None
    _kill(int(pid))

    deadline = time.time() + 30
    while time.time() < deadline:
        current = status(state_path)
        if current["status"] == "completed":
            break
        time.sleep(0.05)
    final = status(state_path)
    assert final["status"] == "completed"
    assert final["cursor"]["step"] == 120
    assert final["cursor"]["generation"] == 1
    assert final["recovery_count"] == 1
    assert len(list((tmp_path / "v4-temporal-120" / "workspace").glob("step-*.txt"))) == 120


def test_v4d_temporal_public_behavior_matches_golden_invariants(tmp_path):
    result = start(tmp_path, task_id="v4-temporal-golden", total_steps=120,
                   max_recoveries=1, background=False)
    assert {
        "status": result["status"],
        "cursor_step": result["cursor"]["step"],
        "cursor_generation": result["cursor"]["generation"],
        "recoveries": result["recovery_count"],
        "worker_alive": result["worker_alive"],
    } == {
        "status": "completed",
        "cursor_step": 120,
        "cursor_generation": 0,
        "recoveries": 0,
        "worker_alive": False,
    }
    artifacts = sorted(p.name for p in (tmp_path / "v4-temporal-golden" / "workspace").glob("step-*.txt"))
    assert artifacts[0] == "step-001.txt"
    assert artifacts[-1] == "step-120.txt"
