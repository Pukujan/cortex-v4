"""V4 temporal control slice.

This is the migrated lifecycle behavior, independent of SSC's corpus and provider credentials.
The worker contract is intentionally small: a worker reads a durable cursor and writes the next
cursor after each completed unit. The V4 Model Summon adapter can plug into this boundary later.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any


TERMINAL = {"completed", "failed"}


def _root(value: str | Path) -> Path:
    path = Path(value).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _atomic(path: Path, value: dict[str, Any]) -> None:
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


def _read(path: Path) -> dict[str, Any]:
    for _ in range(50):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError(f"invalid temporal record: {path}")
            return value
        except PermissionError:
            time.sleep(0.01)
    raise PermissionError(str(path))


def _event(state: dict[str, Any], kind: str, **fields: Any) -> None:
    # Supervisor and worker are separate processes. Keep their append-only ledgers separate so a
    # worker checkpoint cannot race the supervisor's state event append on Windows; the observation
    # deck merges them by timestamp later.
    path = Path(state["worker_events_path"] if kind in {"checkpoint_written", "worker_completed"}
                else state["events_path"])
    rows = []
    if path.exists():
        rows = [line for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line]
    seq = -1
    if rows:
        try:
            seq = int(json.loads(rows[-1]).get("event_seq", -1))
        except (ValueError, TypeError, json.JSONDecodeError):
            pass
    row = {"event_seq": seq + 1, "ts": time.time(), "kind": kind, **fields}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        result = subprocess.run(["tasklist", "/FI", f"PID eq {int(pid)}", "/NH"],
                                capture_output=True, text=True, timeout=5)
        return str(pid) in result.stdout
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _kill(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(int(pid)), "/T", "/F"],
                       capture_output=True, text=True, timeout=10)
    else:
        try:
            os.kill(int(pid), signal.SIGKILL)
        except ProcessLookupError:
            pass


def create_run(root: str | Path, *, task_id: str | None = None, total_steps: int = 120,
               max_recoveries: int = 2) -> dict[str, Any]:
    root = _root(root)
    task_id = task_id or f"v4-temporal-{uuid.uuid4().hex[:12]}"
    run_dir = root / task_id
    run_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "schema": "cortex.v4.temporal_run.v1",
        "task_id": task_id,
        "run_dir": str(run_dir),
        "cursor_path": str(run_dir / "cursor.json"),
        "state_path": str(run_dir / "state.json"),
        "events_path": str(run_dir / "events.jsonl"),
        "worker_events_path": str(run_dir / "worker-events.jsonl"),
        "workspace": str(run_dir / "workspace"),
        "total_steps": int(total_steps),
        "max_recoveries": int(max_recoveries),
        "status": "queued",
        "worker_pid": None,
        "attempt": 0,
        "recovery_count": 0,
        "generation": 0,
        "created_at": time.time(),
    }
    cursor = {"step": 0, "generation": 0, "status": "created", "updated_at": time.time()}
    _atomic(Path(state["state_path"]), state)
    _atomic(Path(state["cursor_path"]), cursor)
    _event(state, "temporal_created", total_steps=total_steps)
    return state


def _spawn(state: dict[str, Any], *, supervisor: bool = False) -> subprocess.Popen:
    mode = "--supervise" if supervisor else "--worker"
    log = Path(state["run_dir"]) / ("supervisor.log" if supervisor else "worker.log")
    fh = log.open("a", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, "-m", "cortex_v4.control.temporal", mode, state["state_path"]],
        cwd=str(Path(__file__).resolve().parents[2]),
        stdin=subprocess.DEVNULL,
        stdout=fh,
        stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    fh.close()
    return process


def status(state_path: str | Path) -> dict[str, Any]:
    state = _read(Path(state_path))
    cursor = _read(Path(state["cursor_path"]))
    return {**state, "cursor": cursor, "worker_alive": _alive(state.get("worker_pid"))}


def supervise(
    state_path: str | Path,
    *,
    poll_s: float = 0.02,
    supervisor_pid: int | None = None,
) -> dict[str, Any]:
    state_path = Path(state_path)
    state = _read(state_path)
    state["status"] = "running"
    if supervisor_pid is not None:
        state["supervisor_pid"] = int(supervisor_pid)
    _atomic(state_path, state)
    if supervisor_pid is not None:
        _event(state, "supervisor_started", supervisor_pid=int(supervisor_pid))
    _event(state, "supervision_started")
    worker: subprocess.Popen | None = None
    try:
        while True:
            cursor = _read(Path(state["cursor_path"]))
            if cursor["status"] in TERMINAL:
                state["status"] = cursor["status"]
                state["worker_pid"] = None
                _atomic(state_path, state)
                _event(state, "temporal_terminal", cursor_status=cursor["status"], step=cursor["step"])
                return status(state_path)
            if worker is None:
                worker = _spawn(state)
                state["worker_pid"] = worker.pid
                state["status"] = "recovering" if state["recovery_count"] else "running"
                _atomic(state_path, state)
                _event(state, "worker_started", worker_pid=worker.pid, attempt=state["attempt"])
            if worker.poll() is not None:
                code = worker.returncode
                worker = None
                state["worker_pid"] = None
                state["recovery_count"] += 1
                state["attempt"] += 1
                if state["recovery_count"] > state["max_recoveries"]:
                    cursor["status"] = "failed"
                    _atomic(Path(state["cursor_path"]), cursor)
                    state["status"] = "failed"
                    state["last_error"] = f"recovery budget exhausted after worker exit {code}"
                    _atomic(state_path, state)
                    _event(state, "recovery_exhausted", exit_code=code)
                    return status(state_path)
                state["generation"] += 1
                cursor["generation"] = state["generation"]
                _atomic(Path(state["cursor_path"]), cursor)
                _atomic(state_path, state)
                _event(state, "worker_recovery", exit_code=code,
                       recovery_count=state["recovery_count"], generation=state["generation"])
                continue
            time.sleep(max(0.01, poll_s))
    finally:
        if worker is not None and worker.poll() is None:
            _kill(worker.pid)


def _worker(state_path: str | Path) -> int:
    state = _read(Path(state_path))
    cursor_path = Path(state["cursor_path"])
    workspace = Path(state["workspace"])
    workspace.mkdir(parents=True, exist_ok=True)
    cursor = _read(cursor_path)
    for step in range(int(cursor["step"]) + 1, int(state["total_steps"]) + 1):
        artifact = workspace / f"step-{step:03d}.txt"
        content = f"v4 temporal step {step}\n"
        if artifact.exists() and artifact.read_text(encoding="utf-8") != content:
            cursor["status"] = "failed"
            _atomic(cursor_path, cursor)
            return 2
        artifact.write_text(content, encoding="utf-8")
        cursor.update(step=step, status="running", updated_at=time.time())
        _atomic(cursor_path, cursor)
        _event(state, "checkpoint_written", step=step, generation=cursor["generation"])
        time.sleep(0.01)
    cursor.update(status="completed", updated_at=time.time())
    _atomic(cursor_path, cursor)
    _event(state, "worker_completed", step=state["total_steps"])
    return 0


def start(root: str | Path, *, task_id: str | None = None, total_steps: int = 120,
          max_recoveries: int = 2, background: bool = False) -> dict[str, Any]:
    state = create_run(root, task_id=task_id, total_steps=total_steps, max_recoveries=max_recoveries)
    if not background:
        return supervise(state["state_path"])
    process = _spawn(state, supervisor=True)
    # The spawned supervisor owns durable state from this point forward. The parent
    # launcher must not write its pre-spawn snapshot back over worker/recovery fields.
    current = status(state["state_path"])
    return {**current, "supervisor_pid": current.get("supervisor_pid", process.pid)}


def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker")
    parser.add_argument("--supervise")
    args = parser.parse_args()
    if args.worker:
        return _worker(args.worker)
    if args.supervise:
        supervise(args.supervise, supervisor_pid=os.getpid())
        return 0
    parser.error("--worker or --supervise required")
    return 2


if __name__ == "__main__":
    raise SystemExit(_main())
