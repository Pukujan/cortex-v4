"""Public-fixture extended task controller for the LiteLLM loop-engineering B replay.

Implements the public task-contract workspace shape and failure-injector class against
V4 control. Deterministic only: no provider credentials and no real LiteLLM spend.

Public recovery contract (from failure-injector.json):
  min_same_model_retries, checkpoint resume, generation fencing, heartbeat/watchdog,
  route receipt. The weak path intentionally omits fencing and checkpoint resume so B1
  can observe the control failure independently of any source-runtime diagnosis.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


REQUIRED_INPUTS = (
    "inputs/brief.md",
    "inputs/control-contract.md",
    "inputs/telemetry-contract.md",
)

REQUIRED_ARTIFACTS = (
    "artifacts/review.md",
    "artifacts/evidence.json",
    "artifacts/checks.txt",
)


def validate_public_workspace(workspace: Path) -> dict[str, object]:
    """V4-owned objective checker for the deterministic recovery fixture."""
    workspace = Path(workspace)
    missing = [rel for rel in REQUIRED_INPUTS + REQUIRED_ARTIFACTS if not (workspace / rel).is_file()]
    malformed: list[str] = []
    evidence = workspace / "artifacts" / "evidence.json"
    if evidence.is_file():
        try:
            payload = json.loads(evidence.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or not payload.get("route"):
                malformed.append("artifacts/evidence.json")
        except json.JSONDecodeError:
            malformed.append("artifacts/evidence.json")
    return {"ok": not missing and not malformed, "missing": missing, "malformed": malformed}

# One unit of work per dispatch attempt before the injector fires.
# Attempt numbering is 1-based to match failure-injector.json trigger.on_attempt == 4.
PRE_STALL_STEPS = (
    "read_brief",
    "read_control_contract",
    "read_telemetry_contract",
)
STALL_ATTEMPT = 4
POST_STALL_STEPS = (
    "write_review",
    "write_evidence",
    "write_checks",
    "verify_readback",
)
ALL_STEPS = PRE_STALL_STEPS + POST_STALL_STEPS


def seed_workspace(workspace: Path) -> None:
    """Create the public task-contract input layout under workspace."""
    workspace = Path(workspace)
    (workspace / "inputs").mkdir(parents=True, exist_ok=True)
    (workspace / "artifacts").mkdir(parents=True, exist_ok=True)
    (workspace / "inputs" / "brief.md").write_text(
        "# Brief\nBounded extended control-layer audit through the LiteLLM gateway class.\n",
        encoding="utf-8",
    )
    (workspace / "inputs" / "control-contract.md").write_text(
        "# Control contract\nRequire checkpoint resume, generation fencing, retries, and route receipt.\n",
        encoding="utf-8",
    )
    (workspace / "inputs" / "telemetry-contract.md").write_text(
        "# Telemetry contract\nLocal telemetry and heartbeat/watchdog events are required.\n",
        encoding="utf-8",
    )


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    """Atomic JSON write with Windows PermissionError retry (matches temporal._atomic)."""
    import os

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    try:
        for _ in range(50):
            try:
                os.replace(str(tmp), str(path))
                return
            except PermissionError:
                time.sleep(0.01)
        raise PermissionError(str(path))
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


@dataclass
class ExtendedTaskResult:
    ok: bool
    final: str
    run_id: str
    lineage_id: str
    attempt: int
    generation: int
    completed_steps: list[str] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    route: dict[str, Any] = field(default_factory=dict)
    workspace: str = ""
    boundary: str = ""
    post_stall_retries: int = 0
    max_active: int = 0


class StallThenTimeoutInjector:
    """Public failure-injector class: stall_then_timeout on extended_task_dispatch attempt 4."""

    def __init__(self, *, on_attempt: int = STALL_ATTEMPT, stall_s: float = 10.0):
        self.on_attempt = int(on_attempt)
        self.stall_s = float(stall_s)
        self.fired = False
        self.fire_count = 0

    def should_stall(self, attempt: int) -> bool:
        # Fire once on the configured attempt so later same-model retries can resume.
        return attempt == self.on_attempt and not self.fired

    def stall(self, cancel: threading.Event) -> None:
        self.fired = True
        self.fire_count += 1
        deadline = time.monotonic() + self.stall_s
        while time.monotonic() < deadline:
            if cancel.is_set():
                return
            time.sleep(0.005)


class ExtendedTaskProvider:
    """Deterministic worker: one new step per attempt, stall on attempt 4, checkpoint resume."""

    def __init__(
        self,
        workspace: Path,
        injector: StallThenTimeoutInjector,
        *,
        cooperative: bool = True,
        respect_checkpoint: bool = True,
    ):
        self.workspace = Path(workspace)
        self.injector = injector
        self.cooperative = cooperative
        self.respect_checkpoint = respect_checkpoint
        self.active = 0
        self.max_active = 0
        self.calls: list[int] = []
        self._lock = threading.Lock()
        self._checkpoint_path = self.workspace / ".checkpoint.json"
        self._completed: list[str] = []

    def load_checkpoint(self) -> list[str]:
        if not self.respect_checkpoint or not self._checkpoint_path.is_file():
            return []
        try:
            value = json.loads(self._checkpoint_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        steps = value.get("completed_steps", [])
        return list(steps) if isinstance(steps, list) else []

    def save_checkpoint(self, completed: list[str], *, generation: int, attempt: int) -> None:
        if not getattr(self, "checkpoint_write", True):
            return
        _atomic_json(
            self._checkpoint_path,
            {
                "completed_steps": list(completed),
                "generation": generation,
                "attempt": attempt,
                "updated_at": time.time(),
            },
        )

    def clear_progress(self) -> None:
        """Weak-controller restart: drop durable progress and any partial artifacts."""
        self._completed = []
        if self._checkpoint_path.is_file():
            try:
                self._checkpoint_path.unlink()
            except OSError:
                pass
        artifacts = self.workspace / "artifacts"
        if artifacts.is_dir():
            for path in artifacts.glob("*"):
                if path.is_file():
                    try:
                        path.unlink()
                    except OSError:
                        pass

    def _do_step(self, step: str) -> None:
        if step == "read_brief":
            (self.workspace / "inputs" / "brief.md").read_text(encoding="utf-8")
        elif step == "read_control_contract":
            (self.workspace / "inputs" / "control-contract.md").read_text(encoding="utf-8")
        elif step == "read_telemetry_contract":
            (self.workspace / "inputs" / "telemetry-contract.md").read_text(encoding="utf-8")
        elif step == "write_review":
            text = (
                "# Review\n"
                "Brief: bounded extended control-layer audit.\n"
                "Control: checkpoint resume, fencing, retries, route receipt.\n"
                "Telemetry: local heartbeat/watchdog events required.\n"
            )
            (self.workspace / "artifacts" / "review.md").write_text(text, encoding="utf-8")
        elif step == "write_evidence":
            evidence = {
                "route": {
                    "intent": "LiteLLM gateway class (deterministic double)",
                    "route_class": "scripted-extended-task",
                    "model_seat": "lite-grok-class",
                },
                "completed_steps": list(self._completed) + [step],
                "telemetry": {
                    "local": True,
                    "heartbeat": True,
                    "watchdog": True,
                },
            }
            (self.workspace / "artifacts" / "evidence.json").write_text(
                json.dumps(evidence, indent=2, sort_keys=True), encoding="utf-8"
            )
        elif step == "write_checks":
            checks = "\n".join(REQUIRED_ARTIFACTS) + "\n"
            (self.workspace / "artifacts" / "checks.txt").write_text(checks, encoding="utf-8")
        elif step == "verify_readback":
            for rel in REQUIRED_ARTIFACTS:
                content = (self.workspace / rel).read_text(encoding="utf-8")
                if not content.strip():
                    raise RuntimeError(f"empty artifact on read-back: {rel}")
        else:
            raise RuntimeError(f"unknown step: {step}")

    def run(
        self,
        attempt: int,
        cancel: threading.Event,
        checkpoint: Callable[[str, int], None],
        *,
        generation: int,
        finish_remaining: bool = False,
    ) -> tuple[bool, str, list[str]]:
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.calls.append(attempt)
        try:
            if self.respect_checkpoint:
                self._completed = self.load_checkpoint()

            if self.injector.should_stall(attempt):
                # Progress already exists from attempts 1..3; stall instead of advancing.
                checkpoint("pre_stall", attempt)
                self.injector.stall(cancel)
                if self.cooperative and cancel.is_set():
                    return False, "cancelled-during-stall", list(self._completed)
                return False, "stall_then_timeout", list(self._completed)

            pending = [step for step in ALL_STEPS if step not in self._completed]
            if not pending:
                return True, "extended task complete", list(self._completed)

            # Before stall: one step per attempt. After stall recovery: finish remaining.
            to_run = pending if finish_remaining else pending[:1]
            for step in to_run:
                if cancel.is_set() and self.cooperative:
                    return False, "cancelled", list(self._completed)
                self._do_step(step)
                self._completed.append(step)
                if self.respect_checkpoint:
                    self.save_checkpoint(self._completed, generation=generation, attempt=attempt)
                else:
                    # Still record in-memory progress for the weak path pre-stall attempts,
                    # but do not rely on durable resume after a stall.
                    self.save_checkpoint(self._completed, generation=generation, attempt=attempt)
                checkpoint(step, attempt)

            if all(step in self._completed for step in ALL_STEPS):
                return True, "extended task complete", list(self._completed)
            return False, "step_progress", list(self._completed)
        finally:
            with self._lock:
                self.active -= 1


class ExtendedTaskController:
    """Controller for the public extended task with optional recovery safeguards.

    When ``recovery_enabled`` is False (B1 observation path), the controller does not
    fence generations, does not require checkpoint resume, and fails the recovery
    contract on the injected stall.

    When ``recovery_enabled`` is True (B2 repair path), stall recovery uses cancel +
    generation fencing, durable checkpoint resume, heartbeat/watchdog events, route
    receipts, and at least ``min_same_model_retries`` same-model retries after the stall.
    """

    def __init__(
        self,
        *,
        timeout_s: float = 0.05,
        cancel_grace_s: float = 0.2,
        min_same_model_retries: int = 3,
        recovery_enabled: bool = True,
        generation_fencing: bool | None = None,
        checkpoint_resume: bool | None = None,
        checkpoint_write: bool | None = None,
        terminal_objective_check: bool | None = None,
        heartbeat: bool | None = None,
        watchdog: bool | None = None,
        route_receipt: bool | None = None,
        max_attempts: int = 16,
    ):
        self.timeout_s = timeout_s
        self.cancel_grace_s = cancel_grace_s
        self.min_same_model_retries = int(min_same_model_retries)
        self.recovery_enabled = recovery_enabled
        self.generation_fencing = recovery_enabled if generation_fencing is None else generation_fencing
        self.checkpoint_resume = recovery_enabled if checkpoint_resume is None else checkpoint_resume
        self.checkpoint_write = recovery_enabled if checkpoint_write is None else checkpoint_write
        self.terminal_objective_check = (
            recovery_enabled if terminal_objective_check is None else terminal_objective_check
        )
        self.heartbeat = recovery_enabled if heartbeat is None else heartbeat
        self.watchdog = recovery_enabled if watchdog is None else watchdog
        self.route_receipt = recovery_enabled if route_receipt is None else route_receipt
        self.max_attempts = max_attempts
        self.events: list[dict] = []

    def _event(self, kind: str, **fields: Any) -> None:
        self.events.append({"event_seq": len(self.events), "ts": time.time(), "kind": kind, **fields})

    def _missing_required_control_events(self) -> list[str]:
        """Fail-closed checklist for recovery path (public recovery_contract).

        On recovery_enabled runs the public contract always requires heartbeat,
        watchdog, and route_receipt. Mutants that disable emission must not be
        able to report ok=True merely because the emission flag is off.
        """
        if not self.recovery_enabled:
            return []
        kinds = {event["kind"] for event in self.events}
        missing: list[str] = []
        if "heartbeat" not in kinds:
            missing.append("heartbeat")
        if "watchdog_armed" not in kinds and "watchdog_fired" not in kinds:
            missing.append("watchdog")
        if "route_receipt" not in kinds:
            missing.append("route_receipt")
        return missing

    def _terminal_objective_ok(self, provider: ExtendedTaskProvider) -> bool:
        """Run V4's checked-in deterministic objective contract, never an SSC file."""
        return bool(validate_public_workspace(provider.workspace)["ok"])

    def run(self, provider: ExtendedTaskProvider, *, route: dict[str, Any] | None = None) -> ExtendedTaskResult:
        lineage_id = uuid.uuid4().hex[:12]
        run_id = uuid.uuid4().hex[:12]
        route = dict(
            route
            or {
                "intent": "LiteLLM gateway class (deterministic double)",
                "route_class": "scripted-extended-task",
                "model_seat": "lite-grok-class",
                "vendor": "xai-class",
            }
        )
        generation = 0
        attempt = 1
        post_stall_retries = 0
        stall_seen = False
        last_steps: list[str] = []
        boundary = ""

        self._event("run_created", lineage_id=lineage_id, run_id=run_id)
        provider.respect_checkpoint = True  # always write checkpoints during progress
        # Resume-from-checkpoint after stall is gated by self.checkpoint_resume.

        while attempt <= self.max_attempts:
            cancel = threading.Event()
            state: dict[str, Any] = {"ok": False, "final": "", "steps": []}
            finish_remaining = stall_seen and self.recovery_enabled and self.checkpoint_resume
            activity = threading.Condition()
            progress_seq = 0
            worker_done = False

            self._event(
                "run_started",
                lineage_id=lineage_id,
                run_id=run_id,
                attempt=attempt,
                generation=generation,
            )
            self._event(
                "dispatch_attempt",
                lineage_id=lineage_id,
                run_id=run_id,
                attempt=attempt,
                generation=generation,
                scope="extended_task_dispatch",
            )
            if self.route_receipt:
                self._event(
                    "route_receipt",
                    lineage_id=lineage_id,
                    run_id=run_id,
                    attempt=attempt,
                    route=route,
                )
            if self.heartbeat:
                self._event(
                    "heartbeat",
                    lineage_id=lineage_id,
                    run_id=run_id,
                    attempt=attempt,
                    generation=generation,
                )
            if self.watchdog:
                self._event(
                    "watchdog_armed",
                    lineage_id=lineage_id,
                    run_id=run_id,
                    attempt=attempt,
                    timeout_s=self.timeout_s,
                )

            def checkpoint(step: str, step_attempt: int) -> None:
                nonlocal progress_seq
                state["steps"] = list(provider._completed)
                self._event(
                    "checkpoint_written",
                    lineage_id=lineage_id,
                    run_id=run_id,
                    attempt=step_attempt,
                    generation=generation,
                    step=step,
                    completed_steps=list(provider._completed),
                )
                with activity:
                    progress_seq += 1
                    activity.notify_all()

            def work() -> None:
                nonlocal worker_done
                try:
                    # After a weak clear, respect_checkpoint may still be true for writing,
                    # but load may see empty file.
                    if stall_seen and not self.checkpoint_resume:
                        provider.respect_checkpoint = False
                        provider._completed = []
                    elif self.checkpoint_resume:
                        provider.respect_checkpoint = True
                    ok, final, steps = provider.run(
                        attempt,
                        cancel,
                        checkpoint,
                        generation=generation,
                        finish_remaining=finish_remaining,
                    )
                    state.update(ok=ok, final=final, steps=list(steps))
                finally:
                    with activity:
                        worker_done = True
                        activity.notify_all()

            thread = threading.Thread(target=work, daemon=False)
            thread.start()

            # The watchdog is an inactivity deadline, not a total-attempt deadline.
            # Every durable checkpoint resets the same bounded timeout. This preserves
            # fail-closed stall detection while preventing healthy recovery batches
            # from being cancelled merely because several bounded steps take longer
            # than one timeout in aggregate on a busy runner.
            with activity:
                observed_progress = progress_seq
                inactivity_deadline = time.monotonic() + self.timeout_s
                while not worker_done:
                    remaining = inactivity_deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    activity.wait(timeout=remaining)
                    if worker_done:
                        break
                    if progress_seq != observed_progress:
                        observed_progress = progress_seq
                        inactivity_deadline = time.monotonic() + self.timeout_s
            if worker_done:
                thread.join()

            if not thread.is_alive():
                last_steps = list(state["steps"])
                if state["ok"]:
                    missing_control = self._missing_required_control_events()
                    if missing_control:
                        boundary = f"missing_{missing_control[0]}"
                        self._event(
                            "run_failed",
                            lineage_id=lineage_id,
                            run_id=run_id,
                            attempt=attempt,
                            generation=generation,
                            reason="required_control_events_missing",
                            missing=missing_control,
                        )
                        return ExtendedTaskResult(
                            False,
                            f"required control events missing: {','.join(missing_control)}",
                            run_id,
                            lineage_id,
                            attempt,
                            generation,
                            last_steps,
                            list(self.events),
                            route,
                            str(provider.workspace),
                            boundary,
                            post_stall_retries,
                            provider.max_active,
                        )
                    if self.terminal_objective_check and not self._terminal_objective_ok(provider):
                        boundary = "terminal_artifact_check_failed"
                        self._event(
                            "run_failed",
                            lineage_id=lineage_id,
                            run_id=run_id,
                            attempt=attempt,
                            generation=generation,
                            reason="terminal_artifact_check_failed",
                        )
                        return ExtendedTaskResult(
                            False,
                            "terminal artifact check failed",
                            run_id,
                            lineage_id,
                            attempt,
                            generation,
                            last_steps,
                            list(self.events),
                            route,
                            str(provider.workspace),
                            boundary,
                            post_stall_retries,
                            provider.max_active,
                        )
                    self._event(
                        "dispatch_completed",
                        lineage_id=lineage_id,
                        run_id=run_id,
                        attempt=attempt,
                        generation=generation,
                    )
                    self._event(
                        "run_completed",
                        lineage_id=lineage_id,
                        run_id=run_id,
                        attempt=attempt,
                        generation=generation,
                        completed_steps=last_steps,
                    )
                    return ExtendedTaskResult(
                        True,
                        state["final"],
                        run_id,
                        lineage_id,
                        attempt,
                        generation,
                        last_steps,
                        list(self.events),
                        route,
                        str(provider.workspace),
                        boundary or "clean_or_recovered",
                        post_stall_retries,
                        provider.max_active,
                    )

                if state["final"] == "step_progress":
                    # Normal pre-stall progress: advance attempt, keep generation.
                    attempt += 1
                    run_id = uuid.uuid4().hex[:12]
                    continue

                if state["final"] in {"stall_then_timeout", "cancelled-during-stall"}:
                    stall_seen = True
                    boundary = "stall_then_timeout_observed"
                    self._event(
                        "dispatch_stalled",
                        lineage_id=lineage_id,
                        run_id=run_id,
                        attempt=attempt,
                        generation=generation,
                    )

                if not self.recovery_enabled:
                    boundary = boundary or "weak_controller_no_recovery"
                    self._event(
                        "run_failed",
                        lineage_id=lineage_id,
                        run_id=run_id,
                        attempt=attempt,
                        generation=generation,
                        reason="recovery_disabled",
                    )
                    return ExtendedTaskResult(
                        False,
                        state["final"] or "recovery disabled after failure",
                        run_id,
                        lineage_id,
                        attempt,
                        generation,
                        last_steps,
                        list(self.events),
                        route,
                        str(provider.workspace),
                        boundary,
                        post_stall_retries,
                        provider.max_active,
                    )

                if stall_seen:
                    post_stall_retries += 1
                if self.generation_fencing:
                    generation += 1
                    self._event(
                        "attempt_fenced",
                        lineage_id=lineage_id,
                        run_id=run_id,
                        attempt=attempt,
                        generation=generation,
                    )
                attempt += 1
                run_id = uuid.uuid4().hex[:12]
                self._event(
                    "retry_started",
                    lineage_id=lineage_id,
                    run_id=run_id,
                    attempt=attempt,
                    generation=generation,
                    overlap=False,
                    post_stall_retries=post_stall_retries,
                )
                continue

            # Thread still alive past an inactivity deadline → stall/timeout boundary.
            self._event(
                "timeout_requested",
                lineage_id=lineage_id,
                run_id=run_id,
                attempt=attempt,
                generation=generation,
            )
            if self.watchdog:
                self._event(
                    "watchdog_fired",
                    lineage_id=lineage_id,
                    run_id=run_id,
                    attempt=attempt,
                    generation=generation,
                )
            stall_seen = True
            boundary = "stall_then_timeout_observed"
            last_steps = list(provider.load_checkpoint()) if self.checkpoint_resume else list(last_steps)

            # Mutant: durable checkpoint write disabled → resume has no progress to
            # continue from. Fail closed instead of silently restarting from scratch.
            if self.checkpoint_resume and not provider.load_checkpoint():
                self._event(
                    "run_failed",
                    lineage_id=lineage_id,
                    run_id=run_id,
                    attempt=attempt,
                    generation=generation,
                    reason="checkpoint_write_missing",
                )
                return ExtendedTaskResult(
                    False,
                    "checkpoint write missing on resume",
                    run_id,
                    lineage_id,
                    attempt,
                    generation,
                    last_steps,
                    list(self.events),
                    route,
                    str(provider.workspace),
                    "checkpoint_write_missing",
                    post_stall_retries,
                    provider.max_active,
                )

            if not self.recovery_enabled:
                # B1 weak path: no cancel/fence, optionally wipe checkpoint, fail.
                if not self.checkpoint_resume:
                    provider.clear_progress()
                    boundary = "checkpoint_resume_violated"
                if not self.generation_fencing:
                    # Start an overlapped retry while the stall is still live.
                    attempt += 1
                    run_id = uuid.uuid4().hex[:12]
                    self._event(
                        "retry_started",
                        lineage_id=lineage_id,
                        run_id=run_id,
                        attempt=attempt,
                        generation=generation,
                        overlap=True,
                    )
                    time.sleep(0.02)
                    overlapped = provider.max_active > 1 or provider.active > 0
                    self._event(
                        "run_failed",
                        lineage_id=lineage_id,
                        run_id=run_id,
                        attempt=attempt,
                        reason="unfenced_overlap_or_weak_retry",
                        max_active=provider.max_active,
                        active=provider.active,
                        overlapped=overlapped,
                    )
                    cancel.set()
                    thread.join(timeout=self.cancel_grace_s)
                    return ExtendedTaskResult(
                        False,
                        "unfenced stall recovery failed",
                        run_id,
                        lineage_id,
                        attempt,
                        generation,
                        last_steps,
                        list(self.events),
                        route,
                        str(provider.workspace),
                        boundary,
                        post_stall_retries,
                        provider.max_active,
                    )
                cancel.set()
                thread.join(timeout=self.cancel_grace_s)
                self._event(
                    "run_failed",
                    lineage_id=lineage_id,
                    run_id=run_id,
                    attempt=attempt,
                    reason="recovery_disabled_on_stall",
                )
                return ExtendedTaskResult(
                    False,
                    "recovery disabled on stall",
                    run_id,
                    lineage_id,
                    attempt,
                    generation,
                    last_steps,
                    list(self.events),
                    route,
                    str(provider.workspace),
                    boundary,
                    post_stall_retries,
                    provider.max_active,
                )

            # Mutant: recovery on but fencing off → overlapped retry is a kill.
            if not self.generation_fencing:
                attempt += 1
                run_id = uuid.uuid4().hex[:12]
                self._event(
                    "retry_started",
                    lineage_id=lineage_id,
                    run_id=run_id,
                    attempt=attempt,
                    generation=generation,
                    overlap=True,
                )
                time.sleep(0.02)
                self._event(
                    "run_failed",
                    lineage_id=lineage_id,
                    run_id=run_id,
                    attempt=attempt,
                    reason="missing_generation_fence",
                    max_active=provider.max_active,
                )
                cancel.set()
                thread.join(timeout=self.cancel_grace_s)
                return ExtendedTaskResult(
                    False,
                    "missing generation fence",
                    run_id,
                    lineage_id,
                    attempt,
                    generation,
                    last_steps,
                    list(self.events),
                    route,
                    str(provider.workspace),
                    "generation_fence_missing",
                    post_stall_retries,
                    provider.max_active,
                )

            # Mutant: recovery on but checkpoint resume off → wipe and fail contract.
            if not self.checkpoint_resume:
                provider.clear_progress()
                cancel.set()
                thread.join(timeout=self.cancel_grace_s)
                self._event(
                    "run_failed",
                    lineage_id=lineage_id,
                    run_id=run_id,
                    attempt=attempt,
                    reason="checkpoint_resume_disabled",
                )
                return ExtendedTaskResult(
                    False,
                    "checkpoint resume disabled",
                    run_id,
                    lineage_id,
                    attempt,
                    generation,
                    [],
                    list(self.events),
                    route,
                    str(provider.workspace),
                    "checkpoint_resume_violated",
                    post_stall_retries,
                    provider.max_active,
                )

            # Strong recovery: cancel, fence, resume checkpoint, same-model retry.
            cancel.set()
            thread.join(timeout=self.cancel_grace_s)
            if thread.is_alive():
                self._event(
                    "attempt_fenced",
                    lineage_id=lineage_id,
                    run_id=run_id,
                    attempt=attempt,
                    generation=generation,
                    quarantined=True,
                )
                return ExtendedTaskResult(
                    False,
                    "attempt quarantined",
                    run_id,
                    lineage_id,
                    attempt,
                    generation,
                    last_steps,
                    list(self.events),
                    route,
                    str(provider.workspace),
                    "quarantined_live_attempt",
                    post_stall_retries,
                    provider.max_active,
                )
            self._event(
                "cancel_acknowledged",
                lineage_id=lineage_id,
                run_id=run_id,
                attempt=attempt,
            )
            generation += 1
            self._event(
                "attempt_fenced",
                lineage_id=lineage_id,
                run_id=run_id,
                attempt=attempt,
                generation=generation,
            )
            post_stall_retries += 1
            if post_stall_retries > self.min_same_model_retries + len(POST_STALL_STEPS):
                return ExtendedTaskResult(
                    False,
                    "retry budget exhausted",
                    run_id,
                    lineage_id,
                    attempt,
                    generation,
                    last_steps,
                    list(self.events),
                    route,
                    str(provider.workspace),
                    "retry_budget_exhausted",
                    post_stall_retries,
                    provider.max_active,
                )
            attempt += 1
            run_id = uuid.uuid4().hex[:12]
            self._event(
                "retry_started",
                lineage_id=lineage_id,
                run_id=run_id,
                attempt=attempt,
                generation=generation,
                overlap=False,
                post_stall_retries=post_stall_retries,
                checkpoint_resume=True,
            )

        return ExtendedTaskResult(
            False,
            "retry budget exhausted",
            run_id,
            lineage_id,
            attempt,
            generation,
            last_steps,
            list(self.events),
            route,
            str(provider.workspace),
            boundary or "retry_budget_exhausted",
            post_stall_retries,
            provider.max_active,
        )


def run_extended_task(
    workspace: Path,
    *,
    recovery_enabled: bool = True,
    generation_fencing: bool | None = None,
    checkpoint_resume: bool | None = None,
    checkpoint_write: bool | None = None,
    terminal_objective_check: bool | None = None,
    heartbeat: bool | None = None,
    watchdog: bool | None = None,
    route_receipt: bool | None = None,
    timeout_s: float = 0.05,
    cancel_grace_s: float = 0.25,
    min_same_model_retries: int = 3,
    stall_s: float = 5.0,
    seed: bool = True,
) -> ExtendedTaskResult:
    """Convenience entry: seed workspace, wire injector, run controller."""
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    if seed:
        seed_workspace(workspace)
    injector = StallThenTimeoutInjector(on_attempt=STALL_ATTEMPT, stall_s=stall_s)
    fencing = recovery_enabled if generation_fencing is None else generation_fencing
    resume = recovery_enabled if checkpoint_resume is None else checkpoint_resume
    provider = ExtendedTaskProvider(
        workspace,
        injector,
        cooperative=bool(fencing),
        respect_checkpoint=True,
    )
    # Stash resume policy on provider for clarity; controller owns the gate.
    provider._resume_policy = resume  # type: ignore[attr-defined]
    provider.checkpoint_write = recovery_enabled if checkpoint_write is None else checkpoint_write  # type: ignore[attr-defined]
    controller = ExtendedTaskController(
        timeout_s=timeout_s,
        cancel_grace_s=cancel_grace_s,
        min_same_model_retries=min_same_model_retries,
        recovery_enabled=recovery_enabled,
        generation_fencing=generation_fencing,
        checkpoint_resume=checkpoint_resume,
        checkpoint_write=checkpoint_write,
        terminal_objective_check=terminal_objective_check,
        heartbeat=heartbeat,
        watchdog=watchdog,
        route_receipt=route_receipt,
    )
    return controller.run(provider)
