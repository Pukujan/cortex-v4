"""C-lane mechanical methodology-core migration gate (second loop).

Binds the second-loop migration methodology to the V4 controller as runtime
checks rather than prompt wording. The controller re-runs the methodology-core
vertical slice migration (M0/M1/M3/M30 + M33 source-to-destination replay) with
A and B's private answers and diagnoses staying hidden, and mechanically:

  - selects and records the methodology IDs for the migration task;
  - loads the required M-procedures from the SSC manual via the methodology
    adapter (inventory + procedure-text presence, no corpus copy);
  - freezes the migration slice (public contract hashes + allowed module list);
  - enforces the corpus boundary and refuses any path touching hidden/ or
    A/B-private packages (hidden holdouts);
  - requires preflight before build;
  - requires a named caller before a wiring claim;
  - runs the origin-to-frontier wiring chain (M30) and refuses ok when a rung
    is missing;
  - refuses closeout without structured receipts; and
  - runs migration mutants, each of which must fail.

Deterministic, public-fixture-only, no provider spend, no A/B private inputs.
Reuses B's migrated wire (``cortex_v4.operation.controllers``) as the origin-to-
frontier chain; C does not re-implement the slice, it mechanically gates it.
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

from ..adapters.ssc_corpus import SSCCorpusAdapter
from ..adapters.ssc_methodology import SSCMethodologyAdapter
from ..operation.controllers import run_methodology_origin_chain
from .wire_oracle import (
    NAMED_CALLER_MODULE,
    NAMED_FUNCTION,
    WireOracleError,
    call_graph_oracle,
)

# The methodology-core migration vertical slice. M0/M1/M3/M30 come from the public
# migration contract; M33 is the source-to-destination replay rule that governs the
# migration itself (added mechanically for this second-loop migration).
MIGRATION_METHODOLOGY_IDS = ("M0", "M1", "M3", "M30", "M33")
MIGRATION_TASK_CLASS = "methodology-core-migration"

# Public files we freeze. The bounded allowed_module list is read from the contract.
PUBLIC_MIGRATION_FILES = (
    "migration-contract.json",
    "objective-checker.py",
    "tool-contract.json",
)

# The named caller is the B-migrated wire that runs the origin-to-frontier chain,
# plus this controller which governs it. Both are named and required.
NAMED_CALLER = "cortex_v4.operation.controllers.run_methodology_origin_chain"
GOVERNING_CALLER = (
    "cortex_v4.control.mechanical_migration.MechanicalMigrationController"
)

# Required corpus citation: the migration must ground on the one allowed doc.
REQUIRED_CORPUS_REFERENCE = "docs/methodology/WORK-METHODOLOGIES.md"

FORBIDDEN_PATH_MARKERS = (
    "/hidden/",
    "\\hidden\\",
    "hidden/",
    "hidden\\",
    "A-private",
    "A-ssc-migration/",
    "A-ssc-migration\\",
    "B-v4-migration/",
    "B-v4-migration\\",
)

RECEIPT_SCHEMA = "cortex.loop_engineering.migration_receipt.v1"
REQUIRED_RECEIPT_FIELDS = (
    "methodology_ids",
    "migration_contract_hash",
    "evidence_pack_hash",
    "allowed_modules_hash",
    "preflight_pack_hash",
    "named_caller",
    "corpus_reference_sha256",
    "wire_oracle_ok",
    "provenance",
)


class MigrationGateError(RuntimeError):
    """Raised when a mechanical migration methodology gate refuses progress."""

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


def assert_migration_path_allowed(path: str | Path, *, label: str = "path") -> Path:
    """Refuse any path that touches the hidden holdout or A/B private packages."""
    raw = str(path)
    normalized = raw.replace("\\", "/")
    lower = normalized.lower()
    for marker in FORBIDDEN_PATH_MARKERS:
        marker_n = marker.replace("\\", "/").lower()
        if marker_n and marker_n in lower:
            raise MigrationGateError(
                "HIDDEN_HOLDOUT_REFUSED",
                f"{label} touches forbidden boundary marker {marker!r}: {raw}",
            )
    parts = [p.lower() for p in Path(normalized).parts]
    if "hidden" in parts:
        raise MigrationGateError(
            "HIDDEN_HOLDOUT_REFUSED",
            f"{label} has a 'hidden' path segment: {raw}",
        )
    return Path(path)


def classify_migration(
    task: str | Mapping[str, Any] | None = None,
    contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify the migration task and select methodology IDs."""
    _ = task
    contract_ids = (
        [str(m) for m in (contract.get("methodology_ids") or [])]
        if contract
        else list(MIGRATION_METHODOLOGY_IDS[:4])
    )
    methodology_ids: list[str] = []
    for mid in list(contract_ids) + list(MIGRATION_METHODOLOGY_IDS):
        if mid and mid not in methodology_ids:
            methodology_ids.append(mid)
    return {
        "task_class": MIGRATION_TASK_CLASS,
        "methodology_ids": methodology_ids,
        "required_methodology_ids": list(dict.fromkeys(contract_ids)),
    }


def _read_contract(public_dir: Path) -> dict[str, Any]:
    path = public_dir / "migration-contract.json"
    if not path.is_file():
        raise MigrationGateError("CONTRACT_MISSING", f"migration contract missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_objective_checker(public_dir: Path):
    """Load the frozen public objective-checker without trusting prose."""
    path = assert_migration_path_allowed(
        public_dir / "objective-checker.py", label="objective-checker"
    )
    if not path.is_file():
        raise MigrationGateError("CHECKER_MISSING", f"objective-checker missing: {path}")
    spec = importlib.util.spec_from_file_location(
        f"migration_objective_checker_{uuid.uuid4().hex[:8]}", path
    )
    if spec is None or spec.loader is None:
        raise MigrationGateError("CHECKER_LOAD_FAILED", f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "check"):
        raise MigrationGateError("CHECKER_LOAD_FAILED", "objective-checker has no check()")
    return mod


def freeze_migration_slice(
    public_dir: Path,
    *,
    contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Freeze the migration slice: public contract hashes + allowed module list."""
    public_dir = assert_migration_path_allowed(public_dir, label="public_dir")
    if not public_dir.is_dir():
        raise MigrationGateError("SLICE_MISSING", f"public dir missing: {public_dir}")
    files: dict[str, str] = {}
    for name in PUBLIC_MIGRATION_FILES:
        path = public_dir / name
        if not path.is_file():
            raise MigrationGateError("SLICE_MISSING", f"required public file missing: {path}")
        assert_migration_path_allowed(path, label=name)
        files[name] = _sha256_file(path)

    contract_obj = dict(contract) if contract else _read_contract(public_dir)
    source_slice = contract_obj.get("source_slice") or {}
    allowed_modules = list(source_slice.get("allowed_modules") or [])
    if not allowed_modules:
        raise MigrationGateError("SLICE_INCOMPLETE", "allowed_modules empty in contract")
    pack = {
        "schema": "cortex.loop_engineering.migration_freeze.v1",
        "public_dir": str(public_dir.resolve()),
        "files": files,
        "migration_contract_hash": files["migration-contract.json"],
        "objective_checker_hash": files["objective-checker.py"],
        "tool_contract_hash": files["tool-contract.json"],
        "allowed_modules": allowed_modules,
    }
    pack["evidence_pack_hash"] = _sha256_text(
        json.dumps(files, sort_keys=True, separators=(",", ":"))
    )
    pack["allowed_modules_hash"] = _sha256_text(
        json.dumps(allowed_modules, sort_keys=True, separators=(",", ":"))
    )
    return pack


@dataclass
class MigrationRunResult:
    ok: bool
    stage: str
    methodology_ids: list[str]
    freeze: dict[str, Any]
    classification: dict[str, Any] = field(default_factory=dict)
    gates: list[dict[str, Any]] = field(default_factory=list)
    preflight_pack_hash: str = ""
    wire: dict[str, Any] = field(default_factory=dict)
    objective: dict[str, Any] = field(default_factory=dict)
    objective_oracle: dict[str, Any] = field(default_factory=dict)
    methodology_receipt: dict[str, Any] = field(default_factory=dict)
    mutants: list[dict[str, Any]] = field(default_factory=list)
    mutant_summary: dict[str, int] = field(default_factory=dict)
    residuals: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    refused: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "stage": self.stage,
            "methodology_ids": list(self.methodology_ids),
            "classification": dict(self.classification),
            "freeze": dict(self.freeze),
            "gates": list(self.gates),
            "preflight_pack_hash": self.preflight_pack_hash,
            "wire": dict(self.wire),
            "objective": dict(self.objective),
            "objective_oracle": dict(self.objective_oracle),
            "methodology_receipt": dict(self.methodology_receipt),
            "mutants": list(self.mutants),
            "mutant_summary": dict(self.mutant_summary),
            "residuals": list(self.residuals),
            "events": list(self.events),
            "refused": self.refused,
        }


def _validate_receipt(receipt: Mapping[str, Any] | None) -> list[str]:
    if not receipt or not isinstance(receipt, Mapping):
        return ["no methodology receipt minted (structured, not prose)"]
    if receipt.get("schema") != RECEIPT_SCHEMA:
        return ["receipt schema must be " + RECEIPT_SCHEMA]
    errs = [k for k in REQUIRED_RECEIPT_FIELDS if not receipt.get(k)]
    if not receipt.get("methodology_ids"):
        errs.append("methodology_ids empty")
    if not receipt.get("provenance"):
        errs.append("provenance (gate receipts) missing")
    if not receipt.get("wire_oracle_ok"):
        errs.append("wire oracle must be ok before closeout")
    return errs


class MechanicalMigrationController:
    """Runtime enforcer for second-loop C migration gates."""

    def __init__(
        self,
        *,
        ssc_root: str | Path,
        public_dir: str | Path,
        v4_root: str | Path | None = None,
        work_root: str | Path | None = None,
        expected_freeze: Mapping[str, str] | None = None,
    ):
        self.ssc_root = Path(ssc_root)
        self.public_dir = assert_migration_path_allowed(public_dir, label="public_dir")
        self.v4_root = Path(v4_root) if v4_root else Path(ssc_root)
        self.work_root = Path(work_root) if work_root else self.v4_root
        self.expected_freeze = dict(expected_freeze) if expected_freeze else None
        self.adapter = SSCMethodologyAdapter(self.ssc_root)
        self.corpus = SSCCorpusAdapter(self.ssc_root)
        self.events: list[dict[str, Any]] = []
        self.gates: list[dict[str, Any]] = []
        self._stage = "init"
        self._classification: dict[str, Any] = {}
        self._freeze: dict[str, Any] = {}
        self._preflight_pack_hash: str = ""
        self._preflight_ref_sha256: str = ""
        self.wire: dict[str, Any] = {}
        self.methodology_receipt: dict[str, Any] = {}
        self.mutants: list[dict[str, Any]] = []
        self.mutant_summary: dict[str, int] = {}
        self._work_unit_id = (
            f"loop-engineering-c-v4-mechanical-migration-{uuid.uuid4().hex[:12]}"
        )

    # ---- internal helpers --------------------------------------------------

    def _event(self, kind: str, **fields: Any) -> None:
        self.events.append(
            {"event_seq": len(self.events), "ts": time.time(), "kind": kind, **fields}
        )

    def _gate(self, name: str, ok: bool, **detail: Any) -> dict[str, Any]:
        entry = {"gate": name, "ok": bool(ok), "detail": detail, "ts": time.time()}
        self.gates.append(entry)
        self._event("gate", gate=name, ok=bool(ok), **detail)
        return entry

    # ---- gate stages -------------------------------------------------------

    def select_methodology(self, contract: Mapping[str, Any] | None = None) -> dict[str, Any]:
        classification = classify_migration(contract=contract)
        inventory = self.adapter.procedure_ids()
        missing = [m for m in classification["methodology_ids"] if m not in inventory]
        if missing:
            raise MigrationGateError(
                "METHODOLOGY_INVENTORY_GAP",
                f"selected methodology IDs not in SSC inventory: {missing}",
            )
        manual = self.adapter.manual_text()
        for mid in classification["methodology_ids"]:
            if mid not in manual:
                raise MigrationGateError(
                    "METHODOLOGY_LOAD_FAILED",
                    f"procedure text for {mid} not present in SSC manual",
                )
        self._classification = classification
        self._event("methodology_selected", **classification)
        self._gate(
            "methodology_select",
            True,
            task_class=classification["task_class"],
            methodology_ids=classification["methodology_ids"],
            inventory_count=len(inventory),
        )
        self._stage = "methodology_selected"
        return classification

    def freeze_slice(self, contract: Mapping[str, Any] | None = None) -> dict[str, Any]:
        freeze = freeze_migration_slice(self.public_dir, contract=contract)
        if self.expected_freeze:
            for name, digest in self.expected_freeze.items():
                actual = freeze["files"].get(name)
                if actual != digest:
                    raise MigrationGateError(
                        "FREEZE_MISMATCH", f"{name}: expected {digest}, got {actual}"
                    )
        self._freeze = freeze
        self._gate(
            "freeze_slice",
            True,
            migration_contract_hash=freeze["migration_contract_hash"],
            allowed_modules=freeze["allowed_modules"],
            evidence_pack_hash=freeze["evidence_pack_hash"],
        )
        self._stage = "slice_frozen"
        return freeze

    def read_corpus(self, ref: str = REQUIRED_CORPUS_REFERENCE) -> dict[str, Any]:
        """Read the one allowed doc, enforcing corpus boundary and required citation."""
        assert_migration_path_allowed(ref, label="corpus_ref")
        try:
            context = self.corpus.read_context([str(ref)])
        except (PermissionError, FileNotFoundError) as exc:
            raise MigrationGateError(
                "CORPUS_BOUNDARY_VIOLATION", f"corpus ref refused: {exc}"
            ) from exc
        files = context.get("files") or []
        if not files:
            raise MigrationGateError(
                "CORPUS_CITATION_REQUIRED",
                f"required corpus citation omitted: {REQUIRED_CORPUS_REFERENCE}",
            )
        self._preflight_ref_sha256 = files[0].get("sha256", "")
        self._gate(
            "corpus_citation",
            True,
            reference=REQUIRED_CORPUS_REFERENCE,
            corpus_boundary=str(self.corpus.corpus_root),
        )
        self._stage = "corpus_read"
        return context

    def require_preflight(self, task: str) -> dict[str, Any]:
        result = self.adapter.preflight(task, workspace=self.ssc_root)
        pack_hash = result.get("pack_hash")
        if not pack_hash:
            raise MigrationGateError("PREFLIGHT_REFUSED", "preflight missing pack_hash")
        self._preflight_pack_hash = pack_hash
        self._gate("preflight", True, pack_hash=pack_hash, workspace=result.get("workspace"))
        self._stage = "preflight_ok"
        return result

    def require_named_caller(self) -> None:
        if not NAMED_CALLER:
            raise MigrationGateError("NAMED_CALLER_REQUIRED", "no named caller registered")
        self._gate(
            "named_caller",
            True,
            named_caller=NAMED_CALLER,
            governing_caller=GOVERNING_CALLER,
        )

    def run_origin_to_frontier(
        self, *, contract_hash: str | None = None, disable: tuple[str, ...] = ()
    ) -> dict[str, Any]:
        self.require_named_caller()
        task = (
            "migrate the methodology-core vertical slice into V4 "
            "(preflight -> forced_rag -> receipt)"
        )
        wire = run_methodology_origin_chain(
            corpus_root=self.ssc_root,
            work_unit_id=f"{self._work_unit_id}:m30",
            task=task,
            contract_hash=contract_hash,
            disable=disable,
        )
        oracle = wire["oracle"]
        self.wire = wire
        self._gate(
            "origin_to_frontier",
            ok=bool(oracle.get("ok")),
            oracle=oracle,
            rungs=sorted((wire.get("steps") or {}).keys()),
        )
        cg = call_graph_oracle(NAMED_CALLER_MODULE, NAMED_FUNCTION)
        self._gate(
            "call_graph_wire",
            ok=bool(cg.get("ok")),
            caller=cg.get("caller"),
            rung_methods_called=cg.get("rung_methods_called"),
            missing_rungs=cg.get("missing_rungs"),
            source_sha256=cg.get("source_sha256"),
        )
        if not cg.get("ok"):
            raise MigrationGateError(
                "WIRE_RUNG_MISSING", "; ".join(cg.get("missing_rungs") or ["static call rung missing"])
            )
        self._stage = "wired_chain"
        if not oracle.get("ok"):
            raise MigrationGateError(
                "WIRE_RUNG_MISSING", "; ".join(oracle.get("errors") or [])
            )
        return wire

    def _require_methodology_selected(self) -> None:
        if not self._classification.get("methodology_ids"):
            raise MigrationGateError(
                "METHODOLOGY_SELECT_REQUIRED",
                "methodology selection bypassed: no methodology IDs recorded",
            )

    def _require_preflight(self) -> None:
        if not self._preflight_pack_hash:
            raise MigrationGateError(
                "PREFLIGHT_REQUIRED",
                "preflight gate skipped: no recorded pack_hash before build",
            )

    def _require_citation_enforced(self) -> None:
        if not self._preflight_ref_sha256:
            raise MigrationGateError(
                "CORPUS_CITATION_REQUIRED",
                f"required corpus citation omitted: {REQUIRED_CORPUS_REFERENCE}",
            )

    def refuse_closeout_without_receipts(self, receipt: Mapping[str, Any] | None) -> None:
        errs = _validate_receipt(receipt)
        if errs:
            raise MigrationGateError("RECEIPT_INCOMPLETE", "; ".join(errs))
        self._gate(
            "closeout_receipts",
            True,
            receipt_keys=sorted((receipt or {}).keys()),
        )
        self._stage = "closeout"
        self._gate("closeout", True, stage=self._stage)

    def _refuse_prose_methodology(self, claim: Any) -> None:
        if not isinstance(claim, Mapping) or claim.get("schema") != RECEIPT_SCHEMA:
            raise MigrationGateError(
                "STRUCTURED_METHODOLOGY_REQUIRED",
                "a prose instruction was substituted for structured methodology data",
            )

    def _build_receipt(self) -> dict[str, Any]:
        self._require_methodology_selected()
        self._require_preflight()
        self._require_citation_enforced()
        oracle = self.wire.get("oracle") or {}
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "work_unit_id": self._work_unit_id,
            "stage": self._stage,
            "methodology_ids": list(self._classification.get("methodology_ids") or []),
            "required_methodology_ids": list(
                self._classification.get("required_methodology_ids") or []
            ),
            "migration_contract_hash": self._freeze.get("migration_contract_hash", ""),
            "evidence_pack_hash": self._freeze.get("evidence_pack_hash", ""),
            "allowed_modules_hash": self._freeze.get("allowed_modules_hash", ""),
            "allowed_modules": self._freeze.get("allowed_modules", []),
            "preflight_pack_hash": self._preflight_pack_hash,
            "named_caller": NAMED_CALLER,
            "governing_caller": GOVERNING_CALLER,
            "corpus_reference": REQUIRED_CORPUS_REFERENCE,
            "corpus_reference_sha256": self._preflight_ref_sha256,
            "wire_oracle_ok": bool(oracle.get("ok")),
            "wire_oracle_errors": list(oracle.get("errors") or []),
            "provenance": list(self.gates),
            "hidden_holdout_enforced": True,
            "closeout_checkable_without_prose": True,
            "ts": time.time(),
        }
        self.methodology_receipt = receipt
        return receipt

    # ---- mutant runner -----------------------------------------------------

    MUTANT_SPECS = (
        {"id": "M-wire-caller-remove", "kind": "wire", "disable": ("receipt",)},
        {"id": "M-methodology-bypass", "kind": "methodology"},
        {"id": "M-corpus-citation-omitted", "kind": "citation"},
        {"id": "M-hidden-holdout-exposed", "kind": "hidden"},
        {"id": "M-preflight-gate-skipped", "kind": "preflight"},
        {"id": "M-closeout-receipt-omitted", "kind": "receipt"},
        {"id": "M-prose-methodology-substituted", "kind": "prose"},
        {"id": "M-callgraph-deleted-caller", "kind": "callgraph"},
    )

    def _fresh_good_controller(self) -> "MechanicalMigrationController":
        """A fresh controller already driven through all gates (good state)."""
        fresh = MechanicalMigrationController(
            ssc_root=self.ssc_root,
            public_dir=self.public_dir,
            v4_root=self.v4_root,
            work_root=self.work_root,
            expected_freeze=self.expected_freeze,
        )
        contract = _read_contract(fresh.public_dir)
        fresh.select_methodology(contract)
        freeze = fresh.freeze_slice(contract)
        fresh.read_corpus(REQUIRED_CORPUS_REFERENCE)
        fresh.require_preflight("methodology-core migration to V4")
        fresh.run_origin_to_frontier(contract_hash=freeze["migration_contract_hash"])
        return fresh

    def run_mutant(self, spec: Mapping[str, Any]) -> dict[str, Any]:
        """Run one mutant. A correctly-hardened controller must refuse it."""
        kind = spec["kind"]
        code = ""
        refused = False
        try:
            if kind == "wire":
                # Remove a caller/wire rung from the origin-to-frontier chain.
                fresh = self._fresh_good_controller()
                fresh.run_origin_to_frontier(disable=tuple(spec.get("disable") or ()))
                code = "wire_rung_not_refused_by_oracle"
            elif kind == "methodology":
                # Bypass methodology selection and attempt receipt minting.
                fresh = self._fresh_good_controller()
                fresh._classification = {}
                fresh._build_receipt()
                code = "methodology_selection_not_refused"
            elif kind == "citation":
                # Omit the required corpus citation and attempt receipt minting.
                fresh = self._fresh_good_controller()
                fresh._preflight_ref_sha256 = ""
                fresh._build_receipt()
                code = "corpus_citation_not_refused"
            elif kind == "hidden":
                # Expose the hidden holdout path.
                assert_migration_path_allowed(
                    self.ssc_root
                    / "observations"
                    / "loop-engineering"
                    / "20260805-migration"
                    / "hidden"
                    / "A-private.sealed.json",
                    label="hidden-holdout",
                )
                code = "hidden_holdout_not_refused"
            elif kind == "preflight":
                # Skip the preflight gate and mint a receipt anyway.
                fresh = self._fresh_good_controller()
                fresh._preflight_pack_hash = ""
                fresh._build_receipt()
                code = "preflight_skip_not_refused"
            elif kind == "receipt":
                # Omit the closeout receipt entirely.
                self.refuse_closeout_without_receipts(None)
                code = "closeout_receipt_absent"
            elif kind == "prose":
                # Substitute a prose instruction for structured methodology data.
                self._refuse_prose_methodology(
                    {"prose": "trust me, the migration is done and wired"}
                )
                code = "prose_methodology_not_refused"
            elif kind == "callgraph":
                # F1 negative control: a deleted caller / dropped rung that the
                # token-presence public checker self-matches must be refused by the
                # static call-graph oracle. Here the named rung is absent from a
                # synthetic callable, so refuse must raise.
                from .wire_oracle import refuse_callgraph_when_rung_missing

                refuse_callgraph_when_rung_missing(
                    "cortex_v4.operation.does_not_exist", "run_methodology_origin_chain"
                )
                code = "callgraph_deleted_caller_not_refused"
            else:
                raise MigrationGateError("UNKNOWN_MUTANT", f"unknown mutant kind {kind}")
        except MigrationGateError as exc:
            code = exc.code
            refused = True
        except WireOracleError as exc:  # from the static call-graph oracle (F1/H1)
            code = exc.code
            refused = True
        return {
            "id": spec["id"],
            "kind": kind,
            "refused": refused,
            "code": code or "",
            "killed": refused,
            "regression": not refused,
        }

    def run_mutants(self) -> tuple[list[dict[str, Any]], dict[str, int]]:
        results = [self.run_mutant(spec) for spec in self.MUTANT_SPECS]
        self.mutants = results
        killed = sum(1 for r in results if r["killed"])
        survived = [r["id"] for r in results if not r["killed"]]
        self.mutant_summary = {
            "total": len(results),
            "killed": killed,
            "survived": len(survived),
        }
        return results, self.mutant_summary

    # ---- full run ----------------------------------------------------------

    def run(
        self, *, disabled: tuple[str, ...] = (), omit_closeout_receipt: bool = False
    ) -> MigrationRunResult:
        contract = _read_contract(self.public_dir)
        classification = self.select_methodology(contract)
        freeze = self.freeze_slice(contract)
        self.read_corpus(REQUIRED_CORPUS_REFERENCE)
        self.require_preflight("methodology-core migration to V4")
        self.run_origin_to_frontier(
            contract_hash=freeze["migration_contract_hash"], disable=disabled
        )
        receipt = self._build_receipt()
        if omit_closeout_receipt:
            self.refuse_closeout_without_receipts(None)
        else:
            self.refuse_closeout_without_receipts(receipt)

        mutants, mut_summary = self.run_mutants()
        return MigrationRunResult(
            ok=True,
            stage=self._stage,
            methodology_ids=list(classification["methodology_ids"]),
            freeze=freeze,
            classification=classification,
            gates=list(self.gates),
            preflight_pack_hash=self._preflight_pack_hash,
            wire=self.wire,
            methodology_receipt=receipt,
            mutants=mutants,
            mutant_summary=mut_summary,
            residuals=[
                {
                    "id": "live-provider-parity",
                    "status": "UNRESOLVED",
                    "class": "ENVIRONMENT",
                    "note": "Deterministic public fixture only; no provider spend.",
                }
            ],
            events=list(self.events),
        )

    def refuse_wire(self, disabled: tuple[str, ...] = ("preflight",)) -> MigrationRunResult:
        """Drive the wire with a rung removed; must refuse with WIRE_RUNG_MISSING."""
        try:
            self.run_origin_to_frontier(disable=disabled)
        except MigrationGateError as exc:
            self._gate("refused", False, code=exc.code, message=exc.message)
            return MigrationRunResult(
                ok=False,
                stage=self._stage,
                methodology_ids=list(self._classification.get("methodology_ids") or []),
                freeze=self._freeze,
                gates=list(self.gates),
                refused=f"{exc.code}: {exc.message}",
            )
        return MigrationRunResult(
            ok=True, stage=self._stage, methodology_ids=[], freeze={}
        )


def run_mechanical_migration(
    *,
    ssc_root: str | Path,
    public_dir: str | Path,
    v4_root: str | Path | None = None,
    work_root: str | Path | None = None,
    expected_freeze: Mapping[str, str] | None = None,
) -> MigrationRunResult:
    controller = MechanicalMigrationController(
        ssc_root=ssc_root,
        public_dir=public_dir,
        v4_root=v4_root,
        work_root=work_root or v4_root,
        expected_freeze=expected_freeze,
    )
    return controller.run()