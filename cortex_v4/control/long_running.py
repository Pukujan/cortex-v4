"""Thin V4 replay slice for the SSC long-running control contract.

The ``legacy_overlap`` switch is intentionally retained only as V4-A's failure injector. V4-B
uses the default fenced path. This module has no model/provider credentials and is deterministic;
the real LiteLLM route is connected only after this replay passes.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class AttemptResult:
    ok: bool
    final: str
    run_id: str
    attempt: int
    steps: int
    generation: int
    events: list[dict] = field(default_factory=list)


class ScriptedProvider:
    """Deterministic provider double that can model a cooperative or late attempt."""

    def __init__(self, *, first_wait_s: float = 0.12, cooperative: bool = True):
        self.first_wait_s = first_wait_s
        self.cooperative = cooperative
        self.active = 0
        self.max_active = 0
        self.calls: list[int] = []
        self._lock = threading.Lock()

    def run(self, attempt: int, cancel: threading.Event, checkpoint: Callable[[int], None]) -> tuple[bool, str, int]:
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.calls.append(attempt)
        try:
            checkpoint(0)
            if attempt == 0:
                deadline = time.monotonic() + self.first_wait_s
                while time.monotonic() < deadline:
                    if self.cooperative and cancel.is_set():
                        return False, "cancelled", 0
                    time.sleep(0.005)
                return False, "first attempt stalled", 1
            checkpoint(1)
            return True, "extended task complete", 1
        finally:
            with self._lock:
                self.active -= 1


class LongRunningController:
    """V4-A/V4-B controller with explicit legacy failure injection."""

    def __init__(self, *, timeout_s: float = 0.03, cancel_grace_s: float = 0.05,
                 max_retries: int = 1, legacy_overlap: bool = False):
        self.timeout_s = timeout_s
        self.cancel_grace_s = cancel_grace_s
        self.max_retries = max_retries
        self.legacy_overlap = legacy_overlap
        self.events: list[dict] = []

    def _event(self, kind: str, **fields) -> None:
        self.events.append({"event_seq": len(self.events), "kind": kind, **fields})

    def run(self, provider: ScriptedProvider) -> AttemptResult:
        lineage_id = uuid.uuid4().hex[:12]
        run_id = uuid.uuid4().hex[:12]
        attempt = 0
        generation = 0

        while attempt <= self.max_retries:
            cancel = threading.Event()
            state = {"ok": False, "final": "", "steps": 0}
            self._event("run_started", lineage_id=lineage_id, run_id=run_id, attempt=attempt,
                        generation=generation)

            def checkpoint(step: int) -> None:
                state["steps"] = max(state["steps"], step)
                self._event("checkpoint_written", lineage_id=lineage_id, run_id=run_id,
                            attempt=attempt, generation=generation, step=step)

            def work() -> None:
                ok, final, steps = provider.run(attempt, cancel, checkpoint)
                state.update(ok=ok, final=final, steps=max(state["steps"], steps))

            thread = threading.Thread(target=work, daemon=False)
            thread.start()
            thread.join(timeout=self.timeout_s)
            if not thread.is_alive():
                if state["ok"]:
                    self._event("run_completed", lineage_id=lineage_id, run_id=run_id,
                                attempt=attempt, generation=generation)
                    return AttemptResult(True, state["final"], run_id, attempt, state["steps"],
                                         generation, list(self.events))
                if attempt >= self.max_retries:
                    self._event("run_failed", lineage_id=lineage_id, run_id=run_id,
                                attempt=attempt, generation=generation)
                    return AttemptResult(False, state["final"], run_id, attempt, state["steps"],
                                         generation, list(self.events))
                attempt += 1
                run_id = uuid.uuid4().hex[:12]
                continue

            self._event("timeout_requested", lineage_id=lineage_id, run_id=run_id, attempt=attempt)
            if self.legacy_overlap:
                # V4-A injected failure: retry starts while the old attempt is still live.
                if attempt >= self.max_retries:
                    return AttemptResult(False, "retry budget exhausted", run_id, attempt,
                                         state["steps"], generation, list(self.events))
                attempt += 1
                run_id = uuid.uuid4().hex[:12]
                self._event("retry_started", lineage_id=lineage_id, run_id=run_id,
                            attempt=attempt, overlap=True)
                continue

            cancel.set()
            thread.join(timeout=self.cancel_grace_s)
            if thread.is_alive():
                self._event("attempt_fenced", lineage_id=lineage_id, run_id=run_id, attempt=attempt)
                return AttemptResult(False, "attempt quarantined", run_id, attempt, state["steps"],
                                     generation, list(self.events))
            self._event("cancel_acknowledged", lineage_id=lineage_id, run_id=run_id, attempt=attempt)
            generation += 1
            self._event("attempt_fenced", lineage_id=lineage_id, run_id=run_id,
                        attempt=attempt, generation=generation)
            if attempt >= self.max_retries:
                return AttemptResult(False, "retry budget exhausted", run_id, attempt,
                                     state["steps"], generation, list(self.events))
            attempt += 1
            run_id = uuid.uuid4().hex[:12]
            self._event("retry_started", lineage_id=lineage_id, run_id=run_id,
                        attempt=attempt, generation=0, overlap=False)

        return AttemptResult(False, "retry budget exhausted", run_id, attempt, 0, generation,
                             list(self.events))
