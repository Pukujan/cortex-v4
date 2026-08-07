"""C-lane mechanical methodology gate for the LiteLLM loop-engineering first loop.

Enforces M32/M33 (and supporting M-procedures) as runtime checks rather than prompt
wording. Uses the public fixture only; never reads hidden/ or A-private diagnosis.
Deterministic: reuses extended_task recovery paths; no live provider spend.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from ..adapters.ssc_methodology import SSCMethodologyAdapter
from .extended_task import run_extended_task

# Task classes that require observation before any hypothesis stage.
LONG_RUNNING_FAILURE_CLASSES = frozenset(
    {
        "extended-task-control-failure",
        "long-running-provider-stall",
        "litellm-extended-control",
    }
)

DEFAULT_METHODOLOGY_IDS = (
    "M0",
    "M1",
    "M30",
    "M32",
    "M33",
)

PUBLIC_FIXTURE_FILES = (
    "task-contract.json",
    "objective-checker.py",
    "tool-contract.json",
    "failure-injector.json",
)

FORBIDDEN_PATH_MARKERS = (
    "/hidden/",
    "\\hidden\\",
    "hidden/",
    "A-private",
    "A-ssc-source/hypothesis",
    "A-ssc-source\\hypothesis",
)


class MechanicalGateError(RuntimeError):
    """Raised when a mechanical methodology gate refuses progress."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _sha256_text(text: str) -> str:
    return _sha256_bytes(text.encode("utf-8"))


def classify_task(task: str | Mapping[str, Any]) -> dict[str, Any]:
    """Classify a loop-engineering control failure and select methodology IDs."""
    if isinstance(task, Mapping):
        text = " ".join(
            str(task.get(k, ""))
            for k in ("name", "description", "task_id", "contract_id", "class")
        )
        explicit = task.get("task_class") or task.get("class")
    else:
        text = str(task)
        explicit = None

    lowered = text.lower()
    is_extended = any(
        token in lowered
        for token in (
            "extended",
            "long-running",
            "long running",
            "litellm",
            "stall",
            "control-layer",
            "control layer",
            "checkpoint",
        )
    )
    task_class = str(explicit) if explicit else (
        "extended-task-control-failure" if is_extended else "generic"
    )
    methodology_ids = list(DEFAULT_METHODOLOGY_IDS)
    if task_class in LONG_RUNNING_FAILURE_CLASSES or is_extended:
        for mid in ("M32", "M33", "M30"):
            if mid not in methodology_ids:
                methodology_ids.append(mid)
    return {
        "task_class": task_class,
        "methodology_ids": methodology_ids,
        "observation_required_before_hypothesis": (
            task_class in LONG_RUNNING_FAILURE_CLASSES or is_extended
        ),
        "reason": "long-running/cross-runtime control failure" if is_extended else "generic",
    }


def assert_path_allowed(path: str | Path, *, label: str = "path") -> Path:
    """Refuse any path that touches the hidden holdout or A-private packages."""
    raw = str(path)
    normalized = raw.replace("\\", "/")
    lower = normalized.lower()
    for marker in FORBIDDEN_PATH_MARKERS:
        marker_n = marker.replace("\\", "/").lower()
        if marker_n in lower:
            raise MechanicalGateError(
                "HIDDEN_HOLDOUT_REFUSED",
                f"{label} touches forbidden boundary marker {marker!r}: {raw}",
            )
    # Explicit segment check for a path component named hidden
    parts = [p.lower() for p in Path(normalized).parts]
    if "hidden" in parts:
        raise MechanicalGateError(
            "HIDDEN_HOLDOUT_REFUSED",
            f"{label} has a 'hidden' path segment: {raw}",
        )
    return Path(path)


def freeze_public_fixture(public_dir: Path) -> dict[str, Any]:
    """Hash the four public fixture files and return a freeze pack."""
    public_dir = assert_path_allowed(public_dir, label="public_dir")
    if not public_dir.is_dir():
        raise MechanicalGateError("FIXTURE_MISSING", f"public fixture dir missing: {public_dir}")
    files: dict[str, str] = {}
    for name in PUBLIC_FIXTURE_FILES:
        path = public_dir / name
        if not path.is_file():
            raise MechanicalGateError("FIXTURE_MISSING", f"required public file missing: {path}")
        assert_path_allowed(path, label=name)
        files[name] = _sha256_file(path)
    pack = {
        "schema": "cortex.loop_engineering.mechanical_freeze.v1",
        "public_dir": str(public_dir.resolve()),
        "files": files,
        "task_contract_hash": files["task-contract.json"],
        "tool_contract_hash": files["tool-contract.json"],
        "objective_checker_hash": files["objective-checker.py"],
        "failure_injector_hash": files["failure-injector.json"],
    }
    pack["evidence_pack_hash"] = _sha256_text(
        json.dumps(files, sort_keys=True, separators=(",", ":"))
    )
    return pack


def load_objective_checker(public_dir: Path):
    """Load the frozen public objective-checker module without trusting prose."""
    path = assert_path_allowed(public_dir / "objective-checker.py", label="objective-checker")
    if not path.is_file():
        raise MechanicalGateError("CHECKER_MISSING", f"objective-checker missing: {path}")
    spec = importlib.util.spec_from_file_location(
        f"mechanical_objective_checker_{uuid.uuid4().hex[:8]}", path
    )
    if spec is None or spec.loader is None:
        raise MechanicalGateError("CHECKER_LOAD_FAILED", f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "check"):
        raise MechanicalGateError("CHECKER_LOAD_FAILED", "objective-checker has no check()")
    return mod


@dataclass
class StageReceipt:
    stage: str
    ok: bool
    detail: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


@dataclass
class MechanicalLoopResult:
    ok: bool
    stage: str
    methodology_ids: list[str]
    freeze: dict[str, Any]
    gates: list[dict[str, Any]] = field(default_factory=list)
    observation: dict[str, Any] = field(default_factory=dict)
    hypotheses: list[dict[str, Any]] = field(default_factory=list)
    weak_result: dict[str, Any] = field(default_factory=dict)
    strong_result: dict[str, Any] = field(default_factory=dict)
    objective: dict[str, Any] = field(default_factory=dict)
    mutants: list[dict[str, Any]] = field(default_factory=list)
    methodology_receipt: dict[str, Any] = field(default_factory=dict)
    residuals: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    refused: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "stage": self.stage,
            "methodology_ids": list(self.methodology_ids),
            "freeze": dict(self.freeze),
            "gates": list(self.gates),
            "observation": dict(self.observation),
            "hypotheses": list(self.hypotheses),
            "weak_result": dict(self.weak_result),
            "strong_result": dict(self.strong_result),
            "objective": dict(self.objective),
            "mutants": list(self.mutants),
            "methodology_receipt": dict(self.methodology_receipt),
            "residuals": list(self.residuals),
            "events": list(self.events),
            "refused": self.refused,
        }


class MechanicalLoopController:
    """Runtime enforcer for C-lane M32/M33 mechanical gates."""

    def __init__(
        self,
        *,
        ssc_root: str | Path,
        public_fixture_dir: str | Path,
        work_root: str | Path,
        expected_freeze: Mapping[str, str] | None = None,
    ):
        self.ssc_root = Path(ssc_root)
        self.public_fixture_dir = assert_path_allowed(
            public_fixture_dir, label="public_fixture_dir"
        )
        self.work_root = Path(work_root)
        self.work_root.mkdir(parents=True, exist_ok=True)
        self.expected_freeze = dict(expected_freeze) if expected_freeze else None
        self.adapter = SSCMethodologyAdapter(self.ssc_root)
        self.events: list[dict[str, Any]] = []
        self._observation_receipt: dict[str, Any] | None = None
        self._stage = "init"
        self._gates: list[dict[str, Any]] = []

    def _event(self, kind: str, **fields: Any) -> None:
        self.events.append(
            {"event_seq": len(self.events), "ts": time.time(), "kind": kind, **fields}
        )

    def _gate(self, name: str, ok: bool, **detail: Any) -> dict[str, Any]:
        entry = {"gate": name, "ok": ok, "detail": detail, "ts": time.time()}
        self._gates.append(entry)
        self._event("gate", gate=name, ok=ok, **detail)
        return entry

    def select_methodologies(self, task: str | Mapping[str, Any]) -> dict[str, Any]:
        classification = classify_task(task)
        inventory = self.adapter.procedure_ids()
        missing = [m for m in classification["methodology_ids"] if m not in inventory]
        if missing:
            raise MechanicalGateError(
                "METHODOLOGY_INVENTORY_GAP",
                f"selected methodology IDs not in SSC inventory: {missing}",
            )
        # Load procedure text presence (adapter reads canonical manual).
        manual = self.adapter.manual_text()
        for mid in classification["methodology_ids"]:
            if f"## {mid}." not in manual and f"## {mid} " not in manual:
                # procedure_ids already confirmed; soft-check heading form
                if mid not in manual:
                    raise MechanicalGateError(
                        "METHODOLOGY_LOAD_FAILED",
                        f"procedure {mid} not found in SSC manual text",
                    )
        self._gate(
            "methodology_select",
            True,
            task_class=classification["task_class"],
            methodology_ids=classification["methodology_ids"],
            inventory_count=len(inventory),
        )
        self._stage = "methodology_selected"
        return classification

    def freeze_evidence(self) -> dict[str, Any]:
        freeze = freeze_public_fixture(self.public_fixture_dir)
        if self.expected_freeze:
            for name, digest in self.expected_freeze.items():
                actual = freeze["files"].get(name)
                if actual != digest:
                    raise MechanicalGateError(
                        "FREEZE_MISMATCH",
                        f"{name}: expected {digest}, got {actual}",
                    )
        self._gate(
            "freeze_evidence",
            True,
            evidence_pack_hash=freeze["evidence_pack_hash"],
            task_contract_hash=freeze["task_contract_hash"],
        )
        self._stage = "evidence_frozen"
        return freeze

    def preflight(self, task: str) -> dict[str, Any]:
        result = self.adapter.preflight(task, workspace=self.ssc_root)
        pack_hash = result.get("pack_hash")
        if not pack_hash:
            raise MechanicalGateError("PREFLIGHT_REFUSED", "preflight missing pack_hash")
        self._gate("preflight", True, pack_hash=pack_hash, workspace=result.get("workspace"))
        self._stage = "preflight_ok"
        return result

    def refuse_if_missing_observation(self, *, allowing_hypothesis: bool) -> None:
        """Gate: hypothesis stage requires a prior observation receipt."""
        if allowing_hypothesis and self._observation_receipt is None:
            self._gate("observation_before_hypothesis", False, reason="no observation receipt")
            raise MechanicalGateError(
                "OBSERVATION_REQUIRED",
                "hypothesis stage refused: observation receipt missing (M32)",
            )
        if allowing_hypothesis:
            self._gate(
                "observation_before_hypothesis",
                True,
                observation_id=self._observation_receipt.get("observation_id")
                if self._observation_receipt
                else None,
            )

    def record_observation(self, *, weak_run: dict[str, Any]) -> dict[str, Any]:
        """Record the mechanical observation of the weak/control-failure path."""
        receipt = {
            "schema": "cortex.loop_engineering.observation_receipt.v1",
            "observation_id": uuid.uuid4().hex[:12],
            "stage": "weak_path_observation",
            "ok": False,  # weak path must fail objective
            "boundary": weak_run.get("boundary"),
            "final": weak_run.get("final"),
            "events_present": weak_run.get("event_kinds", []),
            "objective_ok": weak_run.get("objective_ok", False),
            "ts": time.time(),
        }
        if weak_run.get("ok") is True:
            raise MechanicalGateError(
                "OBSERVATION_INVALID",
                "weak path must fail; got ok=True (failure not observed)",
            )
        if weak_run.get("objective_ok") is True:
            raise MechanicalGateError(
                "OBSERVATION_INVALID",
                "weak path must fail objective checker",
            )
        self._observation_receipt = receipt
        self._gate("observation_recorded", True, observation_id=receipt["observation_id"])
        self._stage = "observed"
        self._event("observation_receipt", **receipt)
        return receipt

    def select_hypotheses(self) -> list[dict[str, Any]]:
        """Hypothesis stage — refused unless observation receipt exists."""
        self.refuse_if_missing_observation(allowing_hypothesis=True)
        # Mechanical, public-contract hypotheses only (no A diagnosis import).
        hypotheses = [
            {
                "id": "H-gen-fence",
                "claim": "missing generation fencing allows overlapped retry on stall",
                "falsifier": "mutant generation_fencing=False must fail",
                "status": "selected",
            },
            {
                "id": "H-ckpt-resume",
                "claim": "missing checkpoint resume wipes progress across stall",
                "falsifier": "mutant checkpoint_resume=False must fail objective",
                "status": "selected",
            },
            {
                "id": "H-retry-ownership",
                "claim": "weak controller without recovery owner fails public recovery contract",
                "falsifier": "recovery_enabled=False must fail",
                "status": "selected",
            },
        ]
        self._gate(
            "hypothesis_selected",
            True,
            hypothesis_ids=[h["id"] for h in hypotheses],
        )
        self._stage = "hypotheses_selected"
        return hypotheses

    def run_weak_path(self, workspace: Path) -> dict[str, Any]:
        result = run_extended_task(
            workspace,
            recovery_enabled=False,
            generation_fencing=False,
            checkpoint_resume=False,
            timeout_s=0.04,
            cancel_grace_s=0.05,
            stall_s=2.0,
        )
        checker = load_objective_checker(self.public_fixture_dir)
        check = checker.check(workspace)
        payload = {
            "ok": result.ok,
            "final": result.final,
            "boundary": result.boundary,
            "attempt": result.attempt,
            "generation": result.generation,
            "completed_steps": list(result.completed_steps),
            "max_active": result.max_active,
            "event_kinds": sorted({e["kind"] for e in result.events}),
            "objective_ok": bool(check.get("ok")),
            "objective": check,
            "workspace": str(workspace),
        }
        self._event("weak_path_complete", ok=result.ok, boundary=result.boundary)
        return payload

    def run_strong_path(self, workspace: Path) -> dict[str, Any]:
        result = run_extended_task(
            workspace,
            recovery_enabled=True,
            timeout_s=0.04,
            cancel_grace_s=0.3,
            stall_s=2.0,
            min_same_model_retries=3,
        )
        checker = load_objective_checker(self.public_fixture_dir)
        check = checker.check(workspace)
        payload = {
            "ok": result.ok,
            "final": result.final,
            "boundary": result.boundary,
            "attempt": result.attempt,
            "generation": result.generation,
            "completed_steps": list(result.completed_steps),
            "post_stall_retries": result.post_stall_retries,
            "max_active": result.max_active,
            "event_kinds": sorted({e["kind"] for e in result.events}),
            "objective_ok": bool(check.get("ok")),
            "objective": check,
            "workspace": str(workspace),
            "events": list(result.events),
        }
        if not result.ok or not check.get("ok"):
            self._gate("strong_path_objective", False, objective=check, final=result.final)
            raise MechanicalGateError(
                "STRONG_PATH_FAILED",
                f"strong path must pass objective; ok={result.ok} check={check}",
            )
        required_events = {
            "run_created",
            "checkpoint_written",
            "dispatch_attempt",
            "timeout_requested",
            "attempt_fenced",
            "retry_started",
            "heartbeat",
            "watchdog_armed",
            "watchdog_fired",
            "route_receipt",
            "run_completed",
        }
        missing_events = sorted(required_events - set(payload["event_kinds"]))
        if missing_events:
            self._gate("strong_path_events", False, missing=missing_events)
            raise MechanicalGateError(
                "STRONG_PATH_EVENTS_MISSING",
                f"strong path missing required events: {missing_events}",
            )
        self._gate("strong_path_objective", True, objective=check)
        self._event("strong_path_complete", ok=True, generation=result.generation)
        return payload

    def run_mutants(self, base: Path) -> list[dict[str, Any]]:
        """Same mutant suite as B; any mutant that no longer fails is a regression."""
        specs = [
            {
                "id": "M-gen-fence",
                "remove": "generation_fencing",
                "kwargs": {
                    "recovery_enabled": True,
                    "generation_fencing": False,
                    "checkpoint_resume": True,
                    "timeout_s": 0.04,
                    "cancel_grace_s": 0.05,
                    "stall_s": 2.0,
                },
                "expect_ok": False,
            },
            {
                "id": "M-ckpt-resume",
                "remove": "checkpoint_resume",
                "kwargs": {
                    "recovery_enabled": True,
                    "generation_fencing": True,
                    "checkpoint_resume": False,
                    "timeout_s": 0.04,
                    "cancel_grace_s": 0.3,
                    "stall_s": 2.0,
                },
                "expect_ok": False,
            },
            {
                "id": "M-retry-ownership",
                "remove": "recovery_enabled",
                "kwargs": {
                    "recovery_enabled": False,
                    "generation_fencing": False,
                    "checkpoint_resume": False,
                    "timeout_s": 0.04,
                    "cancel_grace_s": 0.05,
                    "stall_s": 2.0,
                },
                "expect_ok": False,
            },
        ]
        results: list[dict[str, Any]] = []
        checker = load_objective_checker(self.public_fixture_dir)
        for spec in specs:
            ws = base / spec["id"]
            run = run_extended_task(ws, **spec["kwargs"])
            check = checker.check(ws)
            killed = (run.ok is False) and (spec["expect_ok"] is False)
            # Checkpoint mutant must also fail objective.
            if spec["id"] == "M-ckpt-resume" and check.get("ok") is True:
                killed = False
            entry = {
                "id": spec["id"],
                "remove": spec["remove"],
                "ok": run.ok,
                "boundary": run.boundary,
                "final": run.final,
                "objective_ok": bool(check.get("ok")),
                "killed": killed,
                "regression": not killed,
            }
            results.append(entry)
            self._event("mutant_result", **entry)
        survived = [m for m in results if m["regression"]]
        self._gate(
            "mutant_suite",
            ok=len(survived) == 0,
            killed=sum(1 for m in results if m["killed"]),
            survived=[m["id"] for m in survived],
        )
        if survived:
            raise MechanicalGateError(
                "MUTANT_REGRESSION",
                f"mutants no longer fail (hardening regression): {[m['id'] for m in survived]}",
            )
        return results

    def build_methodology_receipt(
        self,
        *,
        classification: Mapping[str, Any],
        freeze: Mapping[str, Any],
        preflight: Mapping[str, Any],
        observation: Mapping[str, Any],
        hypotheses: list[dict[str, Any]],
        weak: Mapping[str, Any],
        strong: Mapping[str, Any],
        mutants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Structured receipt checkable without trusting prose."""
        receipt = {
            "schema": "cortex.loop_engineering.mechanical_methodology_receipt.v1",
            "lane": "C-v4-mechanical",
            "fixture_id": "20260805-litellm",
            "work_unit_id": "loop-engineering-c-v4-mechanical",
            "task_class": classification.get("task_class"),
            "methodology_ids": list(classification.get("methodology_ids") or []),
            "contract_hash": freeze.get("task_contract_hash"),
            "evidence_pack_hash": freeze.get("evidence_pack_hash"),
            "tool_contract_hash": freeze.get("tool_contract_hash"),
            "preflight_pack_hash": preflight.get("pack_hash"),
            "observation_receipt_id": observation.get("observation_id"),
            "observation_before_hypothesis": True,
            "hypothesis_ids": [h["id"] for h in hypotheses],
            "falsifying_tests": [h.get("falsifier") for h in hypotheses],
            "repairs": [
                {
                    "id": "R-strong-recovery",
                    "mechanism": "extended_task recovery_enabled with fence+checkpoint+heartbeat+watchdog+route_receipt",
                    "reobservation": "strong_path objective pass",
                }
            ],
            "weak_path": {
                "ok": weak.get("ok"),
                "boundary": weak.get("boundary"),
                "objective_ok": weak.get("objective_ok"),
            },
            "strong_path": {
                "ok": strong.get("ok"),
                "boundary": strong.get("boundary"),
                "objective_ok": strong.get("objective_ok"),
                "generation": strong.get("generation"),
                "post_stall_retries": strong.get("post_stall_retries"),
                "max_active": strong.get("max_active"),
            },
            "mutants": [
                {"id": m["id"], "killed": m["killed"], "boundary": m.get("boundary")}
                for m in mutants
            ],
            "gates": list(self._gates),
            "hidden_holdout_enforced": True,
            "live_provider": {
                "status": "UNRESOLVED",
                "class": "ENVIRONMENT",
                "note": "No real LiteLLM provider spend in C mechanical lane.",
            },
            "closeout_checkable_without_prose": True,
            "ts": time.time(),
        }
        # Structural self-check (not the M3 stack receipt schema — C has its own).
        required = (
            "methodology_ids",
            "contract_hash",
            "evidence_pack_hash",
            "observation_receipt_id",
            "hypothesis_ids",
            "mutants",
        )
        missing = [k for k in required if not receipt.get(k)]
        if missing:
            raise MechanicalGateError(
                "RECEIPT_INCOMPLETE", f"methodology receipt missing fields: {missing}"
            )
        if "M32" not in receipt["methodology_ids"] or "M33" not in receipt["methodology_ids"]:
            raise MechanicalGateError(
                "RECEIPT_INCOMPLETE", "methodology_ids must include M32 and M33"
            )
        self._gate("methodology_receipt", True, receipt_keys=sorted(receipt.keys()))
        return receipt

    def run(
        self,
        task: str | Mapping[str, Any] | None = None,
        *,
        skip_strong: bool = False,
        skip_mutants: bool = False,
    ) -> MechanicalLoopResult:
        """Full mechanical sequence. Refuses on any gate failure."""
        task = task or {
            "task_id": "loop-engineering-litellm-extended-task",
            "name": "LiteLLM long-running extended control-layer audit",
            "description": "extended task stall recovery through LiteLLM gateway class",
            "task_class": "extended-task-control-failure",
        }
        residuals = [
            {
                "id": "live-litellm-parity",
                "status": "UNRESOLVED",
                "class": "ENVIRONMENT",
                "note": "Deterministic scripted path only; live provider not attached.",
            }
        ]
        try:
            classification = self.select_methodologies(task)
            freeze = self.freeze_evidence()
            task_text = (
                task
                if isinstance(task, str)
                else str(task.get("description") or task.get("name") or "extended task")
            )
            preflight = self.preflight(task_text)

            weak_ws = self.work_root / "c-weak-observation"
            weak = self.run_weak_path(weak_ws)
            observation = self.record_observation(weak_run=weak)

            # Hypothesis stage only after observation.
            hypotheses = self.select_hypotheses()

            strong: dict[str, Any] = {}
            if not skip_strong:
                strong_ws = self.work_root / "c-strong-recovered"
                strong = self.run_strong_path(strong_ws)

            mutants: list[dict[str, Any]] = []
            if not skip_mutants:
                mutants = self.run_mutants(self.work_root / "c-mutants")

            receipt = self.build_methodology_receipt(
                classification=classification,
                freeze=freeze,
                preflight=preflight,
                observation=observation,
                hypotheses=hypotheses,
                weak=weak,
                strong=strong,
                mutants=mutants,
            )
            self._stage = "closeout"
            self._gate("closeout", True, stage=self._stage)
            return MechanicalLoopResult(
                ok=True,
                stage=self._stage,
                methodology_ids=list(classification["methodology_ids"]),
                freeze=freeze,
                gates=list(self._gates),
                observation=observation,
                hypotheses=hypotheses,
                weak_result=weak,
                strong_result=strong,
                objective=strong.get("objective") or {},
                mutants=mutants,
                methodology_receipt=receipt,
                residuals=residuals,
                events=list(self.events),
            )
        except MechanicalGateError as exc:
            self._gate("refused", False, code=exc.code, message=exc.message)
            return MechanicalLoopResult(
                ok=False,
                stage=self._stage,
                methodology_ids=[],
                freeze={},
                gates=list(self._gates),
                residuals=residuals,
                events=list(self.events),
                refused=f"{exc.code}: {exc.message}",
            )


def run_mechanical_loop(
    *,
    ssc_root: str | Path,
    public_fixture_dir: str | Path,
    work_root: str | Path,
    task: str | Mapping[str, Any] | None = None,
    expected_freeze: Mapping[str, str] | None = None,
) -> MechanicalLoopResult:
    """Convenience entry for C-lane mechanical enforcement."""
    controller = MechanicalLoopController(
        ssc_root=ssc_root,
        public_fixture_dir=public_fixture_dir,
        work_root=work_root,
        expected_freeze=expected_freeze,
    )
    return controller.run(task)
