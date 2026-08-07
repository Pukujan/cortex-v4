"""Fourth-loop mechanical dispatch/tools migration gate (M8/M18/M28/M29).

Binds the dispatch/tools slice (M8 model dispatch, M8a ranked seating, M29 seat
access-control box matrix, M28 multi-theory fan-out, M18 error metabolism) to the
V4 controller as runtime checks rather than prompt wording. Mechanically:

  - selects and records the dispatch/tools methodology IDs;
  - loads the required M-procedures from the SSC manual (inventory + text no copy);
  - enforces the frozen public contract and hidden-holdout boundary;
  - runs the dispatch/tools origin-to-frontier chain
    (dispatch -> seating -> matrix -> fanout -> metabolism -> preflight) through
    the strict behavioral oracle;
  - proves the named caller's call graph statically (wire-oracle, F1/H1 style);
  - refuses ok when a rung is missing (runtime or call-graph); and
  - refuses closeout without structured receipts.

Deterministic, public-fixture-only, no provider spend. It gates the migrated
``run_dispatch_tool_chain`` mechanically, not re-implementing the slice.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from ..adapters.ssc_corpus import SSCCorpusAdapter
from ..adapters.ssc_methodology import SSCMethodologyAdapter
from ..operation.controllers import run_dispatch_tool_chain
from .wire_oracle import (
    DISPATCH_TOOL_FUNCTION,
    NAMED_CALLER_MODULE,
    WireOracleError,
    call_graph_oracle,
)

DISPATCH_TOOL_METHODOLOGY_IDS = ("M8", "M18", "M28", "M29")
TASK_CLASS = "dispatch-tools-slice"

NAMED_CALLER = f"{NAMED_CALLER_MODULE}.{DISPATCH_TOOL_FUNCTION}"
GOVERNING_CALLER = (
    "cortex_v4.control.mechanical_dispatch_tool.MechanicalDispatchToolController"
)

REQUIRED_CORPUS_REFERENCE = "docs/methodology/WORK-METHODOLOGIES.md"
RECEIPT_SCHEMA = "cortex.loop_engineering.dispatch_tool_receipt.v1"
DISPATCH_SEAT = "kimi"

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


class DispatchGateError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def assert_dispatch_path_allowed(path: str | Path, *, label: str = "path") -> Path:
    raw = str(path)
    normalized = raw.replace("\\", "/")
    lower = normalized.lower()
    for marker in FORBIDDEN_PATH_MARKERS:
        marker_n = marker.replace("\\", "/").lower()
        if marker_n and marker_n in lower:
            raise DispatchGateError(
                "HIDDEN_HOLDOUT_REFUSED",
                f"{label} touches forbidden boundary marker {marker!r}: {raw}",
            )
    parts = [p.lower() for p in Path(normalized).parts]
    if "hidden" in parts:
        raise DispatchGateError(
            "HIDDEN_HOLDOUT_REFUSED",
            f"{label} has a 'hidden' path segment: {raw}",
        )
    return Path(path)


def classify_dispatch(
    task: str | Mapping[str, Any] | None = None,
    contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    _ = task
    contract_ids = (
        [str(m) for m in (contract.get("methodology_ids") or [])]
        if contract
        else list(DISPATCH_TOOL_METHODOLOGY_IDS)
    )
    methodology_ids: list[str] = []
    for mid in list(contract_ids) + list(DISPATCH_TOOL_METHODOLOGY_IDS):
        if mid and mid not in methodology_ids:
            methodology_ids.append(mid)
    return {
        "task_class": TASK_CLASS,
        "methodology_ids": methodology_ids,
        "required_methodology_ids": list(dict.fromkeys(contract_ids)),
    }


def _read_contract(public_dir: Path) -> dict[str, Any]:
    path = public_dir / "migration-contract.json"
    if not path.is_file():
        raise DispatchGateError("CONTRACT_MISSING", f"migration contract missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass
class DispatchRunResult:
    ok: bool
    stage: str
    methodology_ids: list[str]
    classification: dict[str, Any] = field(default_factory=dict)
    gates: list[dict[str, Any]] = field(default_factory=list)
    wire: dict[str, Any] = field(default_factory=dict)
    call_graph: dict[str, Any] = field(default_factory=dict)
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
            "gates": list(self.gates),
            "wire": dict(self.wire),
            "call_graph": dict(self.call_graph),
            "methodology_receipt": dict(self.methodology_receipt),
            "mutants": list(self.mutants),
            "mutant_summary": dict(self.mutant_summary),
            "residuals": list(self.residuals),
            "events": list(self.events),
            "refused": self.refused,
        }


class MechanicalDispatchToolController:
    def __init__(
        self,
        *,
        ssc_root: str | Path,
        public_dir: str | Path,
        v4_root: str | Path | None = None,
    ):
        self.ssc_root = Path(ssc_root)
        self.public_dir = assert_dispatch_path_allowed(public_dir, label="public_dir")
        self.v4_root = Path(v4_root) if v4_root else Path(ssc_root)
        self.adapter = SSCMethodologyAdapter(self.ssc_root)
        self.corpus = SSCCorpusAdapter(self.ssc_root)
        self.events: list[dict[str, Any]] = []
        self.gates: list[dict[str, Any]] = []
        self._stage = "init"
        self._classification: dict[str, Any] = {}
        self._citation_sha256_ref: str = ""
        self.wire: dict[str, Any] = {}
        self.call_graph: dict[str, Any] = {}
        self.methodology_receipt: dict[str, Any] = {}
        self.mutants: list[dict[str, Any]] = []
        self.mutant_summary: dict[str, int] = {}

    def _event(self, kind: str, **fields: Any) -> None:
        self.events.append(
            {"event_seq": len(self.events), "ts": time.time(), "kind": kind, **fields}
        )

    def _gate(self, name: str, ok: bool, **detail: Any) -> dict[str, Any]:
        entry = {"gate": name, "ok": bool(ok), "detail": detail, "ts": time.time()}
        self.gates.append(entry)
        self._event("gate", gate=name, ok=bool(ok), **detail)
        return entry

    def select_methodology(self, contract: Mapping[str, Any] | None = None) -> dict[str, Any]:
        classification = classify_dispatch(contract=contract)
        inventory = self.adapter.procedure_ids()
        missing = [m for m in classification["methodology_ids"] if m not in inventory]
        if missing:
            raise DispatchGateError(
                "METHODOLOGY_INVENTORY_GAP",
                f"selected methodology IDs not in SSC inventory: {missing}",
            )
        manual = self.adapter.manual_text()
        for mid in classification["methodology_ids"]:
            if mid not in manual:
                raise DispatchGateError(
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

    def read_corpus(self, ref: str = REQUIRED_CORPUS_REFERENCE) -> dict[str, Any]:
        assert_dispatch_path_allowed(ref, label="corpus_ref")
        try:
            context = self.corpus.read_context([str(ref)])
        except (PermissionError, FileNotFoundError) as exc:
            raise DispatchGateError(
                "CORPUS_BOUNDARY_VIOLATION", f"corpus ref refused: {exc}"
            ) from exc
        files = context.get("files") or []
        if not files:
            raise DispatchGateError(
                "CORPUS_CITATION_REQUIRED",
                f"required corpus citation omitted: {REQUIRED_CORPUS_REFERENCE}",
            )
        self._citation_sha256_ref = files[0].get("sha256", "")
        self._gate(
            "corpus_citation",
            True,
            reference=REQUIRED_CORPUS_REFERENCE,
            corpus_boundary=str(self.corpus.corpus_root),
        )
        self._stage = "corpus_read"
        return context

    def require_named_caller(self) -> None:
        if not NAMED_CALLER:
            raise DispatchGateError("NAMED_CALLER_REQUIRED", "no named caller registered")
        self._gate(
            "named_caller",
            True,
            named_caller=NAMED_CALLER,
            governing_caller=GOVERNING_CALLER,
        )

    def run_dispatch_chain(self, *, disable: tuple[str, ...] = ()) -> dict[str, Any]:
        self.require_named_caller()
        wire = run_dispatch_tool_chain(
            corpus_root=self.ssc_root, seat=DISPATCH_SEAT, disable=disable
        )
        oracle = wire["oracle"]
        self.wire = wire
        self._gate(
            "dispatch_origin_to_frontier",
            ok=bool(oracle.get("ok")),
            oracle=oracle,
            rungs=sorted((wire.get("steps") or {}).keys()),
            preflight_ok=bool(wire.get("preflight_ok")),
        )
        cg = call_graph_oracle(NAMED_CALLER_MODULE, DISPATCH_TOOL_FUNCTION)
        self.call_graph = cg
        self._gate(
            "call_graph_wire",
            ok=bool(cg.get("ok")),
            caller=cg.get("caller"),
            rung_methods_called=cg.get("rung_methods_called"),
            missing_rungs=cg.get("missing_rungs"),
            source_sha256=cg.get("source_sha256"),
        )
        if not cg.get("ok"):
            raise WireOracleError(
                "WIRE_RUNG_MISSING",
                "; ".join(cg.get("missing_rungs") or ["static call rung missing"]),
            )
        self._stage = "wired_chain"
        if not oracle.get("ok"):
            raise DispatchGateError(
                "WIRE_RUNG_MISSING", "; ".join(oracle.get("errors") or [])
            )
        return wire

    def _require_methodology_selected(self) -> None:
        if not self._classification.get("methodology_ids"):
            raise DispatchGateError(
                "METHODOLOGY_SELECT_REQUIRED",
                "methodology selection bypassed: no methodology IDs recorded",
            )

    def _build_receipt(self) -> dict[str, Any]:
        self._require_methodology_selected()
        if not self._citation_sha256_ref:
            raise DispatchGateError(
                "CORPUS_CITATION_REQUIRED",
                "required corpus citation omitted: no grounding ref recorded",
            )
        oracle = self.wire.get("oracle") or {}
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "work_unit_id": f"loop-engineering-dt-{uuid.uuid4().hex[:12]}",
            "stage": self._stage,
            "methodology_ids": list(self._classification.get("methodology_ids") or []),
            "required_methodology_ids": list(
                self._classification.get("required_methodology_ids") or []
            ),
            "named_caller": NAMED_CALLER,
            "governing_caller": GOVERNING_CALLER,
            "seat": DISPATCH_SEAT,
            "corpus_reference": REQUIRED_CORPUS_REFERENCE,
            "corpus_reference_sha256": self._citation_sha256_ref,
            "wire_oracle_ok": bool(oracle.get("ok")),
            "call_graph_wire_ok": bool(self.call_graph.get("ok")),
            "hidden_holdout_enforced": True,
            "closeout_checkable_without_prose": True,
            "ts": time.time(),
        }
        self.methodology_receipt = receipt
        return receipt

    def refuse_closeout_without_receipts(self, receipt: Mapping[str, Any] | None) -> None:
        if not receipt or receipt.get("schema") != RECEIPT_SCHEMA:
            raise DispatchGateError(
                "RECEIPT_INCOMPLETE",
                "no dispatch/tools receipt minted (structured, not prose)",
            )
        if not receipt.get("wire_oracle_ok"):
            raise DispatchGateError("RECEIPT_INCOMPLETE", "wire oracle must be ok")
        if not receipt.get("call_graph_wire_ok"):
            raise DispatchGateError("RECEIPT_INCOMPLETE", "call-graph wire must be ok")
        self._gate("closeout_receipts", True, receipt_keys=sorted(receipt.keys()))
        self._stage = "closeout"
        self._gate("closeout", True, stage=self._stage)

    def _refuse_prose_receipt(self, claim: Any) -> None:
        if not isinstance(claim, Mapping) or claim.get("schema") != RECEIPT_SCHEMA:
            raise DispatchGateError(
                "STRUCTURED_METHODOLOGY_REQUIRED",
                "a prose instruction was substituted for structured dispatch receipt data",
            )

    MUTANT_SPECS = (
        {"id": "DT-wire-rung-dropped", "kind": "wire", "disable": ("matrix",)},
        {"id": "DT-methodology-bypass", "kind": "methodology"},
        {"id": "DT-citation-omitted", "kind": "citation"},
        {"id": "DT-hidden-holdout-exposed", "kind": "hidden"},
        {"id": "DT-callgraph-deleted-caller", "kind": "callgraph"},
        {"id": "DT-closeout-receipt-omitted", "kind": "receipt"},
        {"id": "DT-prose-receipt-substituted", "kind": "prose"},
    )

    def _fresh_good(self) -> "MechanicalDispatchToolController":
        fresh = MechanicalDispatchToolController(
            ssc_root=self.ssc_root, public_dir=self.public_dir, v4_root=self.v4_root
        )
        contract = _read_contract(fresh.public_dir)
        fresh.select_methodology(contract)
        fresh.read_corpus()
        fresh.run_dispatch_chain()
        return fresh

    def run_mutants(self) -> tuple[list[dict[str, Any]], dict[str, int]]:
        results = []
        for spec in self.MUTANT_SPECS:
            kind = spec["kind"]
            code = ""
            refused = False
            try:
                if kind == "wire":
                    fresh = self._fresh_good()
                    fresh.run_dispatch_chain(disable=tuple(spec.get("disable") or ()))
                    code = "wire_rung_not_refused_by_oracle"
                elif kind == "methodology":
                    fresh = self._fresh_good()
                    fresh._classification = {}
                    fresh._build_receipt()
                    code = "methodology_selection_not_refused"
                elif kind == "citation":
                    fresh = self._fresh_good()
                    fresh._citation_sha256_ref = ""
                    fresh._build_receipt()
                    code = "corpus_citation_not_refused"
                elif kind == "hidden":
                    assert_dispatch_path_allowed(
                        self.ssc_root
                        / "observations"
                        / "loop-engineering"
                        / "20260805-migration"
                        / "hidden"
                        / "A-private.sealed.json",
                        label="hidden-holdout",
                    )
                    code = "hidden_holdout_not_refused"
                elif kind == "callgraph":
                    from .wire_oracle import refuse_callgraph_when_rung_missing

                    refuse_callgraph_when_rung_missing(
                        "cortex_v4.operation.does_not_exist",
                        DISPATCH_TOOL_FUNCTION,
                    )
                    code = "callgraph_deleted_caller_not_refused"
                elif kind == "receipt":
                    self.refuse_closeout_without_receipts(None)
                    code = "closeout_receipt_absent"
                elif kind == "prose":
                    self._refuse_prose_receipt(
                        {"prose": "trust me, the dispatch slice is done and wired"}
                    )
                    code = "prose_receipt_not_refused"
                else:
                    raise DispatchGateError("UNKNOWN_MUTANT", f"unknown kind {kind}")
            except DispatchGateError as exc:
                code = exc.code
                refused = True
            except WireOracleError as exc:
                code = exc.code
                refused = True
            results.append(
                {
                    "id": spec["id"],
                    "kind": kind,
                    "refused": refused,
                    "code": code or "",
                    "killed": refused,
                    "regression": not refused,
                }
            )
        self.mutants = results
        killed = sum(1 for r in results if r["killed"])
        survived = [r["id"] for r in results if not r["killed"]]
        self.mutant_summary = {
            "total": len(results),
            "killed": killed,
            "survived": len(survived),
        }
        return results, self.mutant_summary

    def run(self, *, disabled: tuple[str, ...] = ()) -> DispatchRunResult:
        contract = _read_contract(self.public_dir)
        classification = self.select_methodology(contract)
        self.read_corpus()
        self.run_dispatch_chain(disable=disabled)
        receipt = self._build_receipt()
        self.refuse_closeout_without_receipts(receipt)
        mutants, summary = self.run_mutants()
        return DispatchRunResult(
            ok=True,
            stage=self._stage,
            methodology_ids=list(classification["methodology_ids"]),
            classification=classification,
            gates=list(self.gates),
            wire=dict(self.wire),
            call_graph=dict(self.call_graph),
            methodology_receipt=receipt,
            mutants=mutants,
            mutant_summary=summary,
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


def run_mechanical_dispatch_tool(
    *,
    ssc_root: str | Path,
    public_dir: str | Path,
    v4_root: str | Path | None = None,
) -> DispatchRunResult:
    return MechanicalDispatchToolController(
        ssc_root=ssc_root, public_dir=public_dir, v4_root=v4_root
    ).run()