"""Durable, run-scoped Cortex V4 context and progress boundary.

The brain is temporary execution state, not semantic memory.  Workers receive a
capability handle and scoped context packs; they do not receive the brain path.
Only durable progress renews the active lease.  Reading context is deliberately
side-effect free so a stuck worker cannot keep a run alive forever.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping


RETENTION_SECONDS = 24 * 60 * 60
DEFAULT_ACTIVE_LEASE_SECONDS = 15 * 60
ROLES = frozenset({"orchestrator", "implementation_worker", "test_author", "critic", "holdout"})
MEMORY_CLASSES = frozenset({"shared", "contract", "checkpoint", "holdout", "private"})


class BrainError(ValueError):
    """A malformed or unsafe brain operation."""


class BrainAuthorizationError(BrainError):
    """The caller's stage/role/generation is not allowed to access a reference."""


class BrainGenerationError(BrainError):
    """A stale or conflicting attempt tried to write progress."""


class BrainLeaseError(BrainError):
    """The run cannot accept progress under its current lease state."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha(value: Any) -> str:
    if isinstance(value, bytes):
        raw = value
    else:
        raw = _canonical(value).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _now() -> float:
    return time.time()


def _safe_component(value: str, label: str) -> str:
    value = str(value)
    if not value or value in {".", ".."} or "/" in value or "\\" in value or "\x00" in value:
        raise BrainError(f"invalid {label}")
    return value


def _safe_relative(value: str, label: str = "path") -> str:
    value = str(value).replace("\\", "/")
    path = Path(value)
    if not value or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise BrainError(f"invalid {label}")
    return "/".join(path.parts)


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    temp.write_text(_canonical(dict(value)) + "\n", encoding="utf-8")
    try:
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BrainError(f"invalid durable brain record: {path.name}") from exc
    if not isinstance(value, dict):
        raise BrainError(f"durable brain record is not an object: {path.name}")
    return value


@dataclass(frozen=True)
class BrainHandle:
    """A scoped capability; it intentionally contains no filesystem path."""

    run_id: str
    stage_id: str
    role: str
    generation: int
    _capability: str

    def _brain(self) -> "RunBrain":
        brain = _HANDLE_REGISTRY.get(self._capability)
        if brain is None:
            raise BrainAuthorizationError("invalid or expired brain capability")
        return brain

    def read_brain(self, query: str = "context") -> dict[str, Any]:
        return self._brain().read_brain(
            self.run_id, self.stage_id, self.role, self.generation, query, _capability=self._capability
        )

    def read_artifact(self, reference: str) -> bytes:
        return self._brain().read_artifact(
            self.run_id, self.stage_id, self.role, self.generation, reference, _capability=self._capability
        )

    def write_artifact(self, relative_path: str, content: bytes | str, *, mutation_key: str) -> str:
        return self._brain().write_artifact(
            self.run_id, self.stage_id, self.role, self.generation, relative_path, content,
            mutation_key=mutation_key, _capability=self._capability
        )

    def checkpoint(self, attempt_id: str, payload: Mapping[str, Any], artifact_refs: list[str] | None = None) -> dict[str, Any]:
        return self._brain().checkpoint(
            self.run_id, self.stage_id, attempt_id, self.generation, payload, artifact_refs or [],
            _capability=self._capability
        )

    def write_stage_result(self, attempt_id: str, result: Mapping[str, Any]) -> dict[str, Any]:
        return self._brain().write_stage_result(
            self.run_id, self.stage_id, attempt_id, self.generation, result, _capability=self._capability
        )

    def heartbeat(self, attempt_id: str) -> dict[str, Any]:
        return self._brain().heartbeat(
            self.run_id, self.stage_id, attempt_id, self.generation, _capability=self._capability
        )

    def request_memory_quarantine(self, reference: str, reason: str) -> dict[str, Any]:
        return self._brain().request_memory_quarantine(
            self.run_id, self.stage_id, self.role, self.generation, reference, reason, _capability=self._capability
        )


_HANDLE_REGISTRY: dict[str, "RunBrain"] = {}
_HANDLE_METADATA: dict[str, tuple[str, str, int]] = {}


class RunBrain:
    """A durable temporary run workspace with scoped capability operations."""

    def __init__(self, run_root: Path, *, clock: Callable[[], float] = _now):
        self.run_root = Path(run_root).resolve()
        self.clock = clock
        if not self.run_root.is_dir():
            raise BrainError("run brain does not exist")
        self.manifest = _read_json(self.run_root / "manifest.json")
        self.contract = _read_json(self.run_root / "contract.json")
        self.run_id = _safe_component(str(self.manifest.get("run_id")), "run_id")
        self._stages = {str(item["stage_id"]): dict(item) for item in self.contract["stage_specs"]}
        for stage_id, stage in self._stages.items():
            state_path = self.run_root / "stages" / stage_id / "state.json"
            if state_path.is_file():
                state = _read_json(state_path)
                if "generation" in state:
                    stage["generation"] = int(state["generation"])

    @classmethod
    def create(
        cls,
        contract: Mapping[str, Any],
        root: str | Path,
        *,
        retention_seconds: int = RETENTION_SECONDS,
        active_lease_seconds: int = DEFAULT_ACTIVE_LEASE_SECONDS,
        run_id: str | None = None,
        clock: Callable[[], float] = _now,
    ) -> "RunBrain":
        normalized = dict(contract)
        cls._validate_contract(normalized)
        retention_seconds = int(retention_seconds)
        if retention_seconds <= 0:
            raise BrainError("retention policy must be positive")
        if active_lease_seconds <= 0:
            raise BrainError("active lease must be positive")
        run_id = _safe_component(run_id or f"run-{uuid.uuid4().hex}", "run_id")
        root = Path(root).resolve()
        root.mkdir(parents=True, exist_ok=True)
        run_root = (root / run_id).resolve()
        if run_root.exists():
            raise BrainError("run already exists")
        run_root.mkdir()
        for name in ("stages", "artifacts", "receipts"):
            (run_root / name).mkdir()
        now = float(clock())
        contract_hash = _sha(normalized)
        manifest = {
            "schema": "cortex.v4.run-brain.v1",
            "run_id": run_id,
            "contract_hash": contract_hash,
            "created_at": now,
            "status": "active",
            "retention_seconds": retention_seconds,
            "active_lease_seconds": int(active_lease_seconds),
        }
        lease = {
            "status": "active",
            "last_progress_at": now,
            "lease_expires_at": now + active_lease_seconds,
            "grace_expires_at": None,
        }
        _atomic_json(run_root / "contract.json", normalized)
        _atomic_json(run_root / "manifest.json", manifest)
        _atomic_json(run_root / "lease.json", lease)
        (run_root / "events.jsonl").write_text("", encoding="utf-8")
        brain = cls(run_root, clock=clock)
        brain._event("run_created", contract_hash=contract_hash)
        return brain

    @staticmethod
    def _validate_contract(contract: Mapping[str, Any]) -> None:
        required = (
            "objective_id", "exact_base_sha", "task_class", "contract_revision",
            "dependency_dag", "stage_specs", "acceptance_checks", "generation_fence",
        )
        missing = [key for key in required if not contract.get(key)]
        if missing:
            raise BrainError(f"contract missing required fields: {','.join(missing)}")
        if not isinstance(contract["stage_specs"], list) or not contract["stage_specs"]:
            raise BrainError("contract stage_specs must be a non-empty list")
        if not re.fullmatch(r"[0-9a-fA-F]{40}", str(contract.get("exact_base_sha", ""))):
            raise BrainError("exact_base_sha must be a 40-character hexadecimal SHA")
        memory_classes = contract.get("memory_class_by_ref", {})
        if not isinstance(memory_classes, Mapping):
            raise BrainError("memory_class_by_ref must be an object")
        if any(str(value) not in MEMORY_CLASSES for value in memory_classes.values()):
            raise BrainError("memory_class_by_ref contains an unsupported memory class")
        ids: set[str] = set()
        for stage in contract["stage_specs"]:
            if not isinstance(stage, Mapping):
                raise BrainError("stage spec must be an object")
            stage_id = _safe_component(str(stage.get("stage_id", "")), "stage_id")
            if stage_id in ids:
                raise BrainError("stage IDs must be unique")
            ids.add(stage_id)
            if stage.get("assigned_role") not in ROLES:
                raise BrainError(f"unsupported stage role: {stage.get('assigned_role')}")
            if not isinstance(stage.get("allowed_read_refs", []), list):
                raise BrainError("allowed_read_refs must be a list")
            if not isinstance(stage.get("allowed_write_set", []), list):
                raise BrainError("allowed_write_set must be a list")
            if not isinstance(stage.get("acceptance_checks", []), list) or not stage.get("acceptance_checks"):
                raise BrainError(f"stage {stage_id} needs independent acceptance checks")
        graph_ids = set()
        for node in contract["dependency_dag"]:
            if isinstance(node, Mapping):
                graph_ids.add(str(node.get("stage_id", "")))
            else:
                graph_ids.add(str(node))
        if graph_ids and graph_ids != ids:
            raise BrainError("dependency DAG does not match stage specs")

    def _event(self, kind: str, **fields: Any) -> None:
        row = {"seq": self._event_count(), "at": float(self.clock()), "kind": kind, **fields}
        with (self.run_root / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(_canonical(row) + "\n")

    def _event_count(self) -> int:
        try:
            return sum(1 for _ in (self.run_root / "events.jsonl").open(encoding="utf-8"))
        except OSError:
            return 0

    def record_receipt(self, receipt_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Write controller-owned structured run metadata, never model text."""
        receipt_id = _safe_component(receipt_id, "receipt_id")
        if not isinstance(payload, Mapping):
            raise BrainError("receipt payload must be an object")
        forbidden = {"prompt", "messages", "response", "authorization", "api_key", "token", "secret"}
        if any(any(word in str(key).lower() for word in forbidden) for key in payload):
            raise BrainError("receipt contains a forbidden sensitive field")
        record = {"schema": "cortex.v4.run-receipt.v1", "run_id": self.run_id, **dict(payload)}
        path = self.run_root / "receipts" / f"{receipt_id}.json"
        if path.is_file():
            prior = _read_json(path)
            if prior == record:
                return prior
            raise BrainGenerationError("conflicting controller receipt")
        _atomic_json(path, record)
        self._event("receipt_written", receipt_id=receipt_id)
        return record

    def _lease(self) -> dict[str, Any]:
        return _read_json(self.run_root / "lease.json")

    def _save_lease(self, lease: Mapping[str, Any]) -> None:
        _atomic_json(self.run_root / "lease.json", lease)

    def _save_manifest(self, manifest: Mapping[str, Any]) -> None:
        _atomic_json(self.run_root / "manifest.json", manifest)

    def _assert_capability(self, capability: str | None, *, run_id: str, stage_id: str, role: str, generation: int) -> None:
        if not capability or _HANDLE_REGISTRY.get(capability) is not self:
            raise BrainAuthorizationError("invalid brain capability")
        metadata = _HANDLE_METADATA.get(capability)
        if metadata != (stage_id, role, int(generation)):
            raise BrainAuthorizationError("capability scope does not match the requested operation")
        if run_id != self.run_id or stage_id not in self._stages or role not in ROLES:
            raise BrainAuthorizationError("invalid brain scope")
        stage = self._stages[stage_id]
        if role != "orchestrator" and str(stage.get("assigned_role")) != role:
            raise BrainAuthorizationError("role is not assigned to this stage")
        if int(generation) < int(stage.get("generation", 0)):
            raise BrainGenerationError("stale generation")

    def handle(self, stage_id: str, role: str, generation: int) -> BrainHandle:
        stage_id = _safe_component(stage_id, "stage_id")
        role = _safe_component(role, "role")
        if stage_id not in self._stages or role not in ROLES:
            raise BrainAuthorizationError("invalid stage or role")
        assigned = str(self._stages[stage_id].get("assigned_role"))
        if role != "orchestrator" and role != assigned:
            raise BrainAuthorizationError("role is not assigned to this stage")
        current_generation = int(self._stages[stage_id].get("generation", 0))
        if generation < current_generation:
            raise BrainGenerationError("stale generation")
        if generation > current_generation:
            # Opening a replacement attempt is itself the durable fence.  An
            # old worker must be rejected immediately, even if the replacement
            # dies before its first checkpoint.
            stage = dict(self._stages[stage_id])
            stage["generation"] = int(generation)
            self._replace_stage(stage)
            self._event("generation_opened", stage_id=stage_id, role=role, generation=int(generation))
        token = secrets.token_urlsafe(24)
        _HANDLE_REGISTRY[token] = self
        _HANDLE_METADATA[token] = (stage_id, role, int(generation))
        return BrainHandle(self.run_id, stage_id, role, int(generation), token)

    def stage_status(self, stage_id: str) -> dict[str, Any]:
        stage_id = _safe_component(stage_id, "stage_id")
        if stage_id not in self._stages:
            raise BrainError("unknown stage")
        result_path = self.run_root / "stages" / stage_id / "result.json"
        checkpoint_dir = self.run_root / "stages" / stage_id / "checkpoints"
        attempt_dir = self.run_root / "stages" / stage_id / "attempts"
        return {
            "stage_id": stage_id,
            "generation": int(self._stages[stage_id].get("generation", 0)),
            "result": _read_json(result_path) if result_path.is_file() else None,
            "checkpoint_count": len(list(checkpoint_dir.glob("*.json"))) if checkpoint_dir.is_dir() else 0,
            "attempt_count": len(list(attempt_dir.glob("*.json"))) if attempt_dir.is_dir() else 0,
        }

    def _assert_active(self) -> dict[str, Any]:
        lease = self._lease()
        if lease.get("status") != "active":
            raise BrainLeaseError("run is not active")
        return lease

    def _progress(self, *, kind: str, stage_id: str, attempt_id: str, generation: int, capability: str | None) -> dict[str, Any]:
        self._assert_capability(
            capability,
            run_id=self.run_id,
            stage_id=stage_id,
            role=self._stage_role(stage_id, capability),
            generation=generation,
        )
        lease = self._assert_active()
        stage = self._stages[stage_id]
        if generation < int(stage.get("generation", 0)):
            raise BrainGenerationError("stale generation")
        if generation > int(stage.get("generation", 0)):
            stage["generation"] = int(generation)
            self._replace_stage(stage)
        now = float(self.clock())
        lease["last_progress_at"] = now
        lease["lease_expires_at"] = now + int(self.manifest["active_lease_seconds"])
        self._save_lease(lease)
        self._event(kind, stage_id=stage_id, attempt_id=attempt_id, generation=generation)
        return lease

    def _replace_stage(self, stage: Mapping[str, Any]) -> None:
        self._stages[str(stage["stage_id"])] = dict(stage)
        stage_id = str(stage["stage_id"])
        _atomic_json(
            self.run_root / "stages" / stage_id / "state.json",
            {"schema": "cortex.v4.stage-state.v1", "stage_id": stage_id, "generation": int(stage.get("generation", 0))},
        )

    def _scoped_contract(self, stage_id: str, role: str) -> dict[str, Any]:
        stage = dict(self._stages[stage_id])
        if role == "orchestrator":
            return dict(self.contract)
        allowed = {
            "objective_id": self.contract["objective_id"],
            "exact_base_sha": self.contract["exact_base_sha"],
            "task_class": self.contract["task_class"],
            "contract_revision": self.contract["contract_revision"],
            "contract_hash": self.manifest["contract_hash"],
            "generation_fence": self.contract["generation_fence"],
            "stage": stage,
        }
        if role == "test_author" and stage.get("blind_until_convergence"):
            allowed["stage"] = {key: value for key, value in stage.items() if key not in {"producer_session", "private_context"}}
        return allowed

    def _allowed_ref(self, stage_id: str, role: str, reference: str) -> bool:
        stage = self._stages[stage_id]
        refs = {str(item) for item in stage.get("allowed_read_refs", [])}
        if role == "orchestrator":
            return True
        if reference not in refs:
            # A worker may inspect an artifact it is contractually allowed to write,
            # but may not infer access to every file in its stage namespace.
            write_refs = {f"artifact://{str(item).replace('\\', '/')}" for item in stage.get("allowed_write_set", [])}
            if reference not in write_refs:
                return False
        memory_class = self._memory_class(reference)
        if memory_class == "holdout" and role != "holdout":
            return False
        if memory_class == "private" and role != str(stage.get("assigned_role")):
            return False
        return True

    def _memory_class(self, reference: str) -> str:
        explicit = self.contract.get("memory_class_by_ref", {})
        if isinstance(explicit, Mapping) and reference in explicit:
            return str(explicit[reference])
        normalized = str(reference).replace("\\", "/")
        parts = normalized.split("/")
        if any(part == "holdout" or part.startswith("holdout.") or part.startswith("holdout-") for part in parts) or normalized.startswith("memory://holdout/"):
            return "holdout"
        if any(part == "private" or part.startswith("private.") or part.startswith("private-") for part in parts) or normalized.startswith("memory://private/"):
            return "private"
        if normalized.startswith("checkpoint://"):
            return "checkpoint"
        if normalized.startswith("contract://"):
            return "contract"
        return "shared"

    def read_brain(self, run_id: str, stage_id: str, role: str, generation: int, query: str = "context", *, _capability: str | None = None) -> dict[str, Any]:
        self._assert_capability(_capability, run_id=run_id, stage_id=stage_id, role=role, generation=generation)
        query = str(query or "context")
        stage = self._stages[stage_id]
        result: dict[str, Any] = {
            "schema": "cortex.v4.context-pack.v1",
            "run_id": self.run_id,
            "stage_id": stage_id,
            "role": role,
            "generation": int(generation),
            "contract": self._scoped_contract(stage_id, role),
            "artifact_refs": [],
            "checkpoint_refs": [],
            "stage_result": None,
        }
        if query in {"context", "artifacts", "all"}:
            refs = [str(item) for item in stage.get("allowed_read_refs", [])]
            if role != "orchestrator":
                refs = [ref for ref in refs if self._allowed_ref(stage_id, role, ref)]
            result["artifact_refs"] = refs
        if query in {"context", "checkpoints", "all"}:
            checkpoint_dir = self.run_root / "stages" / stage_id / "checkpoints"
            result["checkpoint_refs"] = sorted(
                str(path.relative_to(self.run_root)).replace("\\", "/")
                for path in checkpoint_dir.glob("*.json")
                if path.is_file()
            ) if checkpoint_dir.is_dir() else []
        if query in {"context", "result", "all"}:
            result_path = self.run_root / "stages" / stage_id / "result.json"
            if result_path.is_file() and (role == "orchestrator" or stage.get("result_visible", True)):
                result["stage_result"] = _read_json(result_path)
        return result

    def _artifact_path(self, reference: str) -> Path:
        if not reference.startswith("artifact://"):
            raise BrainError("invalid artifact reference")
        relative = _safe_relative(reference.removeprefix("artifact://"), "artifact reference")
        path = (self.run_root / "artifacts" / relative).resolve()
        if self.run_root not in path.parents:
            raise BrainError("artifact escapes run brain")
        return path

    def read_artifact(self, run_id: str, stage_id: str, role: str, generation: int, reference: str, *, _capability: str | None = None) -> bytes:
        self._assert_capability(_capability, run_id=run_id, stage_id=stage_id, role=role, generation=generation)
        if not self._allowed_ref(stage_id, role, reference):
            raise BrainAuthorizationError("artifact is outside the caller's read boundary")
        path = self._artifact_path(reference)
        try:
            return path.read_bytes()
        except OSError as exc:
            raise BrainError("artifact is not available") from exc

    def write_artifact(self, run_id: str, stage_id: str, role: str, generation: int, relative_path: str, content: bytes | str, *, mutation_key: str, _capability: str | None = None) -> str:
        self._assert_capability(_capability, run_id=run_id, stage_id=stage_id, role=role, generation=generation)
        if role != "orchestrator" and role != str(self._stages[stage_id].get("assigned_role")):
            raise BrainAuthorizationError("role cannot write artifacts outside its assigned stage")
        relative = _safe_relative(relative_path)
        allowed = [str(item).replace("\\", "/") for item in self._stages[stage_id].get("allowed_write_set", [])]
        if allowed and relative not in allowed:
            raise BrainAuthorizationError("artifact is outside the caller's write set")
        if not mutation_key:
            raise BrainError("mutation_key is required")
        raw = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        # The contract's write/read paths are run-relative (for example
        # ``implementation/output.txt``); the stage namespace is part of that
        # path, not an additional directory inserted by the API.
        ref = f"artifact://{relative}"
        path = self._artifact_path(ref)
        prior = path.read_bytes() if path.is_file() else None
        if prior is not None and prior != raw:
            raise BrainGenerationError("conflicting mutation for artifact")
        if prior is None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        ref_record = {"reference": ref, "sha256": _sha(raw), "mutation_key": mutation_key, "stage_id": stage_id, "generation": generation}
        _atomic_json(self.run_root / "artifacts" / f"{stage_id}-{_sha(mutation_key)[:16]}.json", ref_record)
        self._event("artifact_written" if prior is None else "artifact_write_idempotent", reference=ref, mutation_key=mutation_key, stage_id=stage_id, generation=generation)
        return ref

    def checkpoint(self, run_id: str, stage_id: str, attempt_id: str, generation: int, payload: Mapping[str, Any], artifact_refs: list[str], *, _capability: str | None = None) -> dict[str, Any]:
        role = self._stage_role(stage_id, _capability)
        self._assert_capability(_capability, run_id=run_id, stage_id=stage_id, role=role, generation=generation)
        if not attempt_id or not isinstance(payload, Mapping):
            raise BrainError("checkpoint requires attempt_id and payload")
        for ref in artifact_refs:
            if not self._allowed_ref(stage_id, role, str(ref)):
                raise BrainAuthorizationError("checkpoint references an inaccessible artifact")
        record = {"schema": "cortex.v4.checkpoint.v1", "run_id": run_id, "stage_id": stage_id, "role": role, "attempt_id": attempt_id, "generation": int(generation), "payload": dict(payload), "artifact_refs": list(artifact_refs), "contract_revision": self.contract["contract_revision"]}
        checkpoint_id = _sha(record)
        record["checkpoint_id"] = checkpoint_id
        path = self.run_root / "stages" / stage_id / "checkpoints" / f"{attempt_id}-{generation}.json"
        if path.is_file():
            prior = _read_json(path)
            if prior == record:
                return prior
            raise BrainGenerationError("conflicting duplicate checkpoint")
        self._assert_active()
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_json(path, record)
        stage = dict(self._stages[stage_id])
        stage["generation"] = int(generation)
        self._replace_stage(stage)
        lease = self._lease()
        now = float(self.clock())
        lease["last_progress_at"] = now
        lease["lease_expires_at"] = now + int(self.manifest["active_lease_seconds"])
        self._save_lease(lease)
        self._event("checkpoint_written", stage_id=stage_id, attempt_id=attempt_id, generation=generation, checkpoint_id=checkpoint_id)
        return record

    def write_stage_result(self, run_id: str, stage_id: str, attempt_id: str, generation: int, result: Mapping[str, Any], *, _capability: str | None = None) -> dict[str, Any]:
        role = self._stage_role(stage_id, _capability)
        self._assert_capability(_capability, run_id=run_id, stage_id=stage_id, role=role, generation=generation)
        if not isinstance(result, Mapping) or not result.get("classification"):
            raise BrainError("stage result requires a result classification")
        if result.get("classification") == "success" and not result.get("mechanical_check"):
            raise BrainError("successful stage result requires mechanical_check evidence")
        self._assert_active()
        path = self.run_root / "stages" / stage_id / "result.json"
        record = {"schema": "cortex.v4.stage-result.v1", "run_id": run_id, "stage_id": stage_id, "role": role, "attempt_id": attempt_id, "generation": int(generation), **dict(result)}
        if path.is_file():
            prior = _read_json(path)
            if prior == record:
                return prior
            if int(prior.get("generation", -1)) >= generation:
                raise BrainGenerationError("late or conflicting stage result")
        stage = dict(self._stages[stage_id])
        stage["generation"] = int(generation)
        self._replace_stage(stage)
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_json(path, record)
        attempt_id = _safe_component(attempt_id, "attempt_id")
        attempt_path = self.run_root / "stages" / stage_id / "attempts" / f"{attempt_id}-{generation}.json"
        if attempt_path.is_file():
            prior_attempt = _read_json(attempt_path)
            if prior_attempt != record:
                raise BrainGenerationError("conflicting duplicate stage attempt")
        else:
            _atomic_json(attempt_path, record)
        self._event("stage_result_written", stage_id=stage_id, attempt_id=attempt_id, generation=generation, classification=result.get("classification"))
        return record

    def heartbeat(self, run_id: str, stage_id: str, attempt_id: str, generation: int, *, _capability: str | None = None) -> dict[str, Any]:
        role = self._stage_role(stage_id, _capability)
        self._assert_capability(_capability, run_id=run_id, stage_id=stage_id, role=role, generation=generation)
        return self._progress(kind="heartbeat", stage_id=stage_id, attempt_id=attempt_id, generation=generation, capability=_capability)

    def _stage_role(self, stage_id: str, capability: str | None) -> str:
        if capability not in _HANDLE_REGISTRY or _HANDLE_REGISTRY.get(capability) is not self:
            raise BrainAuthorizationError("invalid brain capability")
        metadata = _HANDLE_METADATA.get(capability)
        if metadata is None or metadata[0] != stage_id:
            raise BrainAuthorizationError("capability stage does not match the requested operation")
        return metadata[1]

    def request_memory_quarantine(self, run_id: str, stage_id: str, role: str, generation: int, reference: str, reason: str, *, _capability: str | None = None) -> dict[str, Any]:
        self._assert_capability(_capability, run_id=run_id, stage_id=stage_id, role=role, generation=generation)
        if not reason or not self._allowed_ref(stage_id, role, reference):
            raise BrainAuthorizationError("quarantine must name an allowed reference and reason")
        record = {"schema": "cortex.v4.memory-quarantine-proposal.v1", "run_id": run_id, "stage_id": stage_id, "role": role, "reference": reference, "reason": reason, "status": "proposed", "at": float(self.clock())}
        path = self.run_root / "receipts" / f"quarantine-{_sha(record)[:16]}.json"
        _atomic_json(path, record)
        self._event("memory_quarantine_proposed", stage_id=stage_id, reference=reference, role=role)
        return record

    def finalize(self, closeout: Mapping[str, Any]) -> dict[str, Any]:
        lease = self._assert_active()
        status = str(closeout.get("status", ""))
        objective = closeout.get("objective_check")
        if status not in {"PASS", "FAILED", "BLOCKED"} or not isinstance(objective, Mapping):
            raise BrainError("closeout requires terminal status and objective_check")
        if status == "PASS" and objective.get("passed") is not True:
            raise BrainError("PASS requires an independently passing objective check")
        if status == "PASS" and not any((self.run_root / "stages" / stage / "checkpoints").glob("*.json") for stage in self._stages):
            raise BrainError("PASS requires durable stage checkpoints")
        now = float(self.clock())
        final = {"schema": "cortex.v4.run-closeout.v1", "run_id": self.run_id, "contract_hash": self.manifest["contract_hash"], "completed_at": now, **dict(closeout)}
        _atomic_json(self.run_root / "receipts" / "closeout.json", final)
        lease["status"] = "completed"
        lease["last_progress_at"] = now
        lease["lease_expires_at"] = None
        lease["grace_expires_at"] = now + int(self.manifest.get("retention_seconds", RETENTION_SECONDS))
        self._save_lease(lease)
        manifest = dict(self.manifest)
        manifest["status"] = "completed"
        self._save_manifest(manifest)
        self.manifest = manifest
        self._event("run_finalized", status=status, grace_expires_at=lease["grace_expires_at"])
        return final

    def cleanup(self, *, now: float | None = None) -> bool:
        lease = self._lease()
        now = float(self.clock() if now is None else now)
        expiry = lease.get("grace_expires_at")
        if lease.get("status") != "completed" or expiry is None or now < float(expiry):
            return False
        if (
            self.run_root.is_symlink()
            or self.run_root.name != self.run_id
            or self.run_root.parent == self.run_root
            or not (self.run_root / "manifest.json").is_file()
            or str(self.manifest.get("run_id")) != self.run_id
        ):
            raise BrainError("invalid cleanup target")
        shutil.rmtree(self.run_root)
        for token, brain in list(_HANDLE_REGISTRY.items()):
            if brain is self:
                _HANDLE_REGISTRY.pop(token, None)
                _HANDLE_METADATA.pop(token, None)
        return True


def create_run(contract: Mapping[str, Any], root: str | Path, **kwargs: Any) -> RunBrain:
    return RunBrain.create(contract, root, **kwargs)
