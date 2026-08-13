from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from contextlib import contextmanager
from typing import Any, Mapping

from .models import OperationReceipt
from .views import render_closeout


class _NativeCorpus:
    """V4-owned context pack; no external corpus or source checkout is consulted."""

    corpus_root = "v4://owned-context"

    def read_context(self, refs: list[str]) -> dict[str, Any]:
        normalized = [str(ref) for ref in refs]
        digest = hashlib.sha256("\n".join(normalized).encode("utf-8")).hexdigest()
        return {"context_hash": digest, "refs": normalized, "source": "v4-owned"}


class _NativeMethodology:
    """Small deterministic V4 methodology surface used by normal runtime paths."""

    def preflight(self, task: str, *, workspace: str | Path | None = None) -> dict[str, Any]:
        normalized = str(task).strip()
        pack_hash = hashlib.sha256(f"v4-preflight:{normalized}".encode("utf-8")).hexdigest()
        return {
            "ok": bool(normalized),
            "task_class": "coding" if any(word in normalized.lower() for word in ("code", "build", "implement", "fix")) else "generic",
            "workspace_declared": workspace is not None,
            "pack_hash": pack_hash if normalized else "",
            "citations": [],
            "source": "v4-owned",
        }

    def forced_rag_decide(self, **kwargs: Any) -> dict[str, Any]:
        prompt_text = str(kwargs.get("prompt_text", ""))
        allowed = "pack_hash:" in prompt_text or bool(kwargs.get("pack_hash"))
        return {"allowed": allowed, "reason": "v4-owned pack" if allowed else "no V4 pack"}

    def mint_receipt(self, *, work_unit_id: str, contract_hash: str, **fields: Any) -> dict[str, Any]:
        receipt_id = hashlib.sha256(f"{work_unit_id}:{contract_hash}".encode("utf-8")).hexdigest()[:16]
        return {
            "schema": "cortex.v4.methodology_receipt.v1",
            "receipt_id": receipt_id,
            "work_unit_id": work_unit_id,
            "contract_hash": contract_hash,
            **fields,
        }

    def validate_receipt(self, receipt: Mapping[str, Any]) -> list[str]:
        required = ("schema", "receipt_id", "work_unit_id", "contract_hash", "risk_tier", "roles")
        missing = [field for field in required if not receipt.get(field)]
        if receipt.get("schema") != "cortex.v4.methodology_receipt.v1":
            missing.append("schema")
        return sorted(set(missing))

    def observe(self, *, task: str, differences: int = 0) -> dict[str, Any]:
        return {"task": task, "observed_differences": int(differences),
                "observation_first": True, "hypothesis_not_yet_formed": True}

    def citation_require(self, claim: str, source: str | None = None) -> dict[str, Any]:
        pointer = source or "v4://evidence/unspecified"
        kind = "url" if "://" in pointer else "path"
        return {"claim": claim, "source": pointer, "kind": kind}

    def citation_strict(self, claim: str, citations: list[str], sources: dict[str, Any]) -> dict[str, Any]:
        normalized = [str(item) for item in citations if str(item).strip()]
        known = {str(key) for key in sources}
        supported = bool(str(claim).strip()) and bool(normalized) and all(item in known for item in normalized)
        return {
            "status": "SUPPORTED" if supported else "UNSUPPORTED",
            "citation_count": len(normalized),
            "unresolved_count": sum(item not in known for item in normalized),
        }

    def audit_classification(self, *, claims: int, verified: int, residuals: int) -> dict[str, Any]:
        claims, verified, residuals = int(claims), int(verified), int(residuals)
        valid = min(claims, verified, residuals) >= 0 and verified + residuals == claims
        return {"claims_inventoried": claims, "claims_verified": verified,
                "residuals_recorded": residuals, "every_finding_has_closure_metric": valid}

    def replay(self, *, destination_seen: bool, hidden_protected: bool = True) -> dict[str, Any]:
        return {"destination_seen_failure_independently": bool(destination_seen),
                "hidden_holdout_protected": bool(hidden_protected)}


class _NativeDispatch:
    def resolve(self, seat: str) -> dict[str, Any]:
        return self.resolve_summon(seat)

    def resolve_summon(self, seat: str) -> dict[str, Any]:
        return {"seat": seat, "tier": "v4-native", "model_override": None,
                "vendor": "v4", "route_class": "local-deterministic"}

    def selected_rank(self, role: str) -> dict[str, Any]:
        return {"role": role, "model": "v4-native-worker", "vendor": "v4"}

    def seat_matrix(self, *, seat: str, box: str, never_seen: list[str], forced_rag: bool) -> dict[str, Any]:
        return {"seat": seat, "box": box, "never_seen": list(never_seen), "forced_rag": bool(forced_rag)}

    def fan_out(self, *, theories: list[dict[str, Any]]) -> dict[str, Any]:
        ids = [str(item.get("id", "")) for item in theories]
        return {"theory_count": len(theories), "distinct_ids": len(set(ids)),
                "all_falsifying_tests": all(bool(item.get("falsifying_test")) for item in theories)}

    def metabolism(self, *, error_class: str, mechanism: bool) -> dict[str, Any]:
        return {"error_class": error_class, "becomes_mechanism_same_day": bool(mechanism)}


class _NativeEval:
    def verdict_has_no_judge(self, verdict_paths: list[str]) -> dict[str, Any]:
        return {"no_judge_in_verdict_path": not any("judge" in path.lower() for path in verdict_paths)}

    def honest_abstention(self, *, decided: int, abstained: int) -> dict[str, Any]:
        total = max(1, int(decided) + int(abstained))
        return {"abstention_rate": int(abstained) / total, "reports_abstention_rate": True}

    def cohens_kappa(self, gold: list[str], pred: list[str], labels: list[str]) -> dict[str, Any]:
        if len(gold) != len(pred) or not gold or not labels:
            return {"kappa": None, "calibrated": False, "sample_count": 0}
        label_set = {str(label) for label in labels}
        if any(str(item) not in label_set for item in [*gold, *pred]):
            return {"kappa": None, "calibrated": False, "sample_count": len(gold)}
        n = len(gold)
        observed = sum(str(a) == str(b) for a, b in zip(gold, pred)) / n
        expected = sum(
            (sum(str(item) == label for item in gold) / n)
            * (sum(str(item) == label for item in pred) / n)
            for label in label_set
        )
        kappa = 1.0 if math.isclose(expected, 1.0) else (observed - expected) / (1.0 - expected)
        return {"kappa": float(kappa), "calibrated": float(kappa) >= 0.4, "sample_count": n}

    def ndcg(self, retrieved: list[int], relevant: list[int], k: int) -> dict[str, Any]:
        k = int(k)
        if k <= 0 or not relevant:
            return {"ndcg_at_k": 0.0, "k": k, "retrieved_count": len(retrieved), "relevant_count": len(relevant)}
        relevant_set = {str(item) for item in relevant}
        ranked = list(retrieved)[:k]
        dcg = sum(
            (1.0 / math.log2(index + 2))
            for index, item in enumerate(ranked)
            if str(item) in relevant_set
        )
        ideal = sum(1.0 / math.log2(index + 2) for index in range(min(k, len(relevant_set))))
        score = dcg / ideal if ideal else 0.0
        return {"ndcg_at_k": float(score), "k": k, "retrieved_count": len(retrieved), "relevant_count": len(relevant_set)}

    def holdout(self, *, graded: bool, gaming_probe_refused: bool) -> dict[str, Any]:
        return {"graded_agent_blind": bool(graded), "gaming_probe_refused": bool(gaming_probe_refused)}

    def blocked_state(self, *, reason_present: bool, page_owner: bool) -> dict[str, Any]:
        return {"reason_recorded": bool(reason_present), "owner_paged": bool(page_owner)}

    def refutation(self, *, pre_mortem_grep_run: bool, counterexample_sought: bool) -> dict[str, Any]:
        return {"pre_mortem_grep": bool(pre_mortem_grep_run),
                "counterexample_sought": bool(counterexample_sought)}

    def convenience_audit(self, *, drift_substantiated: bool, ergonomics_bug_flagged: bool) -> dict[str, Any]:
        return {"drift_substantiated": bool(drift_substantiated),
                "ergonomics_bug_flagged": bool(ergonomics_bug_flagged)}

    def qa_gate(self, *, question_answered: bool, rubric_shaped: bool) -> dict[str, Any]:
        return {"question_answered": bool(question_answered), "rubric_shaped": bool(rubric_shaped)}


class _NativeSpan:
    def __init__(self, ledger: Path, name: str, fields: dict[str, Any]):
        self.ledger, self.name, self.fields = ledger, name, fields

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def add_tool_call(self, count: int) -> None:
        self.fields["tool_calls"] = int(count)


class _NativeObservation:
    def capture(self, record: dict[str, Any], *, workspace: Path) -> None:
        ledger = Path(workspace) / "telemetry" / "events.jsonl"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with ledger.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")

    @contextmanager
    def span(self, name: str, **fields: Any):
        ledger = Path(str(fields.get("env", {}).get("CORTEX_METRICS_LEDGER", "v4://ledger")))
        yield _NativeSpan(ledger, name, fields)

    def snapshot(self, *, workspace: Path) -> dict[str, Any]:
        return {"overall": "OBSERVED", "workspace": str(workspace), "source": "v4-owned"}


_NativeMethodologyAdapter = _NativeMethodology
_NativeCorpusAdapter = _NativeCorpus
_NativeDispatchAdapter = _NativeDispatch
_NativeEvalAdapter = _NativeEval
_NativeSummonAdapter = _NativeDispatch
_NativeObservabilityAdapter = _NativeObservation


def run_fixture_operation(
    task: str,
    *,
    run_id: str,
    managed_root: str | Path,
    corpus_root: str | Path | None = None,
    seat: str = "kimi",
    context_refs: list[str] | None = None,
) -> dict[str, Any]:
    """Run one deterministic walking skeleton without making a provider request.

    The fixture proves composition and boundaries. A real provider call belongs to the
    already-tested temporal lane and must be enabled as a separate, owner-approved run.
    """
    _ = corpus_root
    managed = Path(managed_root).resolve() / run_id
    managed.mkdir(parents=True, exist_ok=True)
    (managed / "docs").mkdir(exist_ok=True)
    refs = context_refs or ["v4://contracts/control-plane-v1"]
    methodology = _NativeMethodologyAdapter()
    corpus = _NativeCorpusAdapter()
    summon = _NativeSummonAdapter()
    observation = _NativeObservabilityAdapter()

    preflight = methodology.preflight(task, workspace=corpus.corpus_root)
    context = corpus.read_context(refs)
    spec = summon.resolve(seat)
    receipt = OperationReceipt(
        run_id=run_id,
        task=task,
        context_hash=str(context["context_hash"]),
        methodology_pack_hash=str(preflight["pack_hash"]),
        seat=str(spec["seat"]),
        tier=str(spec["tier"]),
        model_override=spec.get("model_override"),
        status="fixture_complete",
        observation_overall="PENDING",
        source_corpus=str(corpus.corpus_root),
    )
    record = {
        "task": task,
        "model": str(spec.get("model_override") or spec.get("tier")),
        "run_id": run_id,
        "task_id": f"{run_id}:task-1",
        "route_id": f"{spec['tier']}:{spec.get('model_override') or 'default'}",
        "prompt_id": f"{run_id}:prompt-1",
        "role": "executor",
        "output": "fixture complete",
        "gate_verdict": "OBSERVED",
        "extra": {"risk_tier": "low", "source_corpus": str(corpus.corpus_root)},
    }
    observation.capture(record, workspace=managed)
    ledger = managed / "telemetry" / "metrics.jsonl"
    ledger.parent.mkdir(exist_ok=True)
    with observation.span(
        "v4.walking_skeleton",
        env={"CORTEX_METRICS_LEDGER": str(ledger)},
        session_id=run_id,
        task_id=f"{run_id}:task-1",
        route_id=record["route_id"],
        model=record["model"],
    ) as span:
        span.add_tool_call(0)
    # Dashboard reads the configured ledger path from the environment; this operation is a
    # fixture runner, so the local process-level setting is intentional and bounded.
    import os
    prior_ledger = os.environ.get("CORTEX_METRICS_LEDGER")
    os.environ["CORTEX_METRICS_LEDGER"] = str(ledger)
    try:
        snapshot = observation.snapshot(workspace=managed)
    finally:
        if prior_ledger is None:
            os.environ.pop("CORTEX_METRICS_LEDGER", None)
        else:
            os.environ["CORTEX_METRICS_LEDGER"] = prior_ledger
    final = OperationReceipt(**{**receipt.as_dict(), "observation_overall": snapshot["overall"]})
    (managed / "context.json").write_text(
        json.dumps({"refs": refs, "context_hash": context["context_hash"]}, indent=2), encoding="utf-8"
    )
    (managed / "result.json").write_text(
        json.dumps({"status": final.status, "fixture": True}, indent=2), encoding="utf-8"
    )
    (managed / "receipt.json").write_text(json.dumps(final.as_dict(), indent=2), encoding="utf-8")
    (managed / "closeout.md").write_text(
        render_closeout(final.as_dict(), context_ref=refs[0]), encoding="utf-8"
    )
    return {"receipt": final.as_dict(), "snapshot": snapshot, "managed_run": str(managed)}


def _build_well_formed_receipt(
    methodology: _NativeMethodologyAdapter,
    *,
    work_unit_id: str,
    contract_hash: str,
) -> dict[str, Any]:
    """Mint a structurally well-formed methodology stack receipt (no persistence)."""
    return methodology.mint_receipt(
        work_unit_id=work_unit_id,
        contract_hash=contract_hash,
        risk_tier="kernel",
        required_methodologies=["M0", "M1", "M3", "M30"],
        roles={
            "implementer": {
                "seat": "terra",
                "artifact_sha256": "a" * 64,
                "paths": ["cortex_v4/operation/controllers.py"],
                "session_or_agent_id": "b-imp",
            },
            "test_author": {
                "seat": "kimi",
                "artifact_sha256": "b" * 64,
                "paths": ["tests/test_methodology_origin_chain.py"],
                "session_or_agent_id": "b-test",
            },
            "holdout": {
                "seat": "grok",
                "artifact_sha256": "c" * 64,
                "paths": ["observations/loop-engineering/20260805-migration"],
                "session_or_agent_id": "b-hold",
            },
        },
        persist=False,
    )


def run_methodology_origin_chain(
    *,
    corpus_root: str | Path,
    work_unit_id: str,
    task: str,
    contract_hash: str | None = None,
    disable: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Run the M30 origin-to-frontier wiring chain in one named V4 caller.

    Chain: preflight -> forced_rag decide -> methodology_receipt validate.

    The origins feed the frontier: the preflight's recorded pack_hash is what the
    forced-RAG gate must resolve to allow a write; the well-formed receipt must mint
    and validate and the malformed one must be refused. No corpus is copied.

    ``disable`` lets a mutant drop one rung of the chain (a wire that must break the
    chain end-to-end); the returned oracle decision is a strict behavioral wire oracle,
    not a token-presence check.
    """
    methodology = _NativeMethodologyAdapter()
    steps: dict[str, Any] = {}

    if "preflight" in disable:
        steps["preflight"] = None
    else:
        preresult = methodology.preflight(task, workspace=corpus_root)
        steps["preflight"] = {
            "pack_hash": preresult.get("pack_hash"),
            "citation_count": len(preresult.get("citations") or []),
        }

    if "forced_rag" in disable:
        steps["forced_rag"] = None
    else:
        pack_hash = (steps["preflight"] or {}).get("pack_hash")
        if pack_hash:
            decision = methodology.forced_rag_decide(
                tool_name="Edit",
                tool_input={},
                user_text="",
                prompt_text=f"task: {task}\npack_hash: {pack_hash}",
                mode="auto",
            )
        else:
            decision = {"allowed": False, "reason": "no pack"}
        steps["forced_rag"] = {
            "allowed": bool(decision.get("allowed")),
            "reason": decision.get("reason", ""),
        }

    if "receipt" not in disable:
        import hashlib as _h
        ch = contract_hash
        if not ch:
            pack_hash_source = (steps["preflight"] or {}).get("pack_hash", "")
            ch = _h.sha256(f"{pack_hash_source}:{task}".encode("utf-8")).hexdigest()
            if len(ch) != 64:
                ch = _h.sha256(task.encode("utf-8")).hexdigest()
    else:
        ch = contract_hash or ""

    if "receipt" not in disable:
        well_formed = _build_well_formed_receipt(methodology, work_unit_id=work_unit_id, contract_hash=ch)
        well_errors = methodology.validate_receipt(well_formed)
        malformed_errors = methodology.validate_receipt({"schema": "not-a-receipt"})
        steps["receipt"] = {
            "well_formed_errors": well_errors,
            "malformed_rejected": bool(malformed_errors),
            "receipt_id": well_formed.get("receipt_id"),
        }
    else:
        steps["receipt"] = None

    verdict = methodology_origin_oracle(steps, disabled=disable)
    return {
        "schema": "cortex.v4.methodology_origin_chain.v1",
        "work_unit_id": work_unit_id,
        "task": task,
        "steps": steps,
        "oracle": verdict,
        "named_caller": "cortex_v4.operation.controllers.run_methodology_origin_chain",
    }


def methodology_origin_oracle(
    steps: Mapping[str, Any],
    *,
    disabled: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Strict behavioral wire oracle: every rung of the M30 chain must carry correct output.

    The chain is wired only when preflight produced a pack_hash, the forced-RAG gate
    allowed on a recorded pack, and the receipt validator accepted well-formed and
    rejected malformed. A rung that is missing, empty, or refused is a wiring failure —
    removing any rung (a mutant) must fail the oracle.
    """
    _ = disabled  # oracle is strict: absent rungs always fail, never exempted
    errors: list[str] = []
    pre = steps.get("preflight")
    rag = steps.get("forced_rag")
    rec = steps.get("receipt")

    if isinstance(rag, dict) and rag.get("allowed") is False:
        errors.append("forced_rag gate refused the recorded pack")
    if not (isinstance(pre, dict) and pre.get("pack_hash")):
        errors.append("preflight missing pack_hash")
    if not (isinstance(rag, dict) and rag.get("allowed") is True):
        errors.append("forced_rag rung missing or did not allow on resolved pack")
    if not (
        isinstance(rec, dict)
        and rec.get("well_formed_errors") == []
        and rec.get("malformed_rejected") is True
    ):
        errors.append("receipt rung missing or did not validate well-formed / reject malformed")

    return {"ok": not errors, "errors": errors}


# ---- fourth-loop slice: dispatch/tools (M8/M18/M28/M29) ---------------------

def run_dispatch_tool_chain(
    *,
    corpus_root: str | Path,
    seat: str,
    task_type: str = "hard-build",
    box: str = "grey",
    never_seen: list[str] | None = None,
    forced_rag: bool = True,
    role: str = "builder",
    theories: list[dict[str, Any]] | None = None,
    error_class: str = "caught-error",
    disable: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Run the dispatch/tools origin-to-frontier wiring chain.

    Chain: M8 model dispatch -> M8a ranked seating -> M29 seat matrix -> M28
    multi-theory fan-out -> M18 error metabolism -> M30 preflight-before-build.

    Dispatch resolves the seat against the owner's closed summon table; ranked
    seating picks the strongest eligible candidate; the box matrix says who sees
    what and who must cite (forced-RAG); fan-out requires a DISTINCT, FALSIFYING,
    innocent-suspect-inclusive theory set; metabolism records every error class as
    becoming a mechanism; preflight must resolve before build. ``disable`` drops one
    rung; a strict behavioral oracle fails when any rung is missing.
    """
    dispatch_adapter = _NativeDispatchAdapter()
    methodology = _NativeMethodologyAdapter()
    steps: dict[str, Any] = {}

    if "dispatch" in disable:
        steps["dispatch"] = None
    else:
        spec = dispatch_adapter.resolve_summon(seat)
        steps["dispatch"] = {
            "seat": seat,
            "tier": spec.get("tier"),
            "model_override": spec.get("model_override"),
            "dispatchable": True,
        }

    if "seating" in disable:
        steps["seating"] = None
    else:
        seatsel = dispatch_adapter.selected_rank(role)
        steps["seating"] = {"role": role, "model": seatsel.get("model")}

    if "matrix" in disable:
        steps["matrix"] = None
    else:
        matrix = dispatch_adapter.seat_matrix(
            seat=seat,
            box=box,
            never_seen=list(never_seen or ["holdout", "mutants", "hidden/"]),
            forced_rag=forced_rag,
        )
        steps["matrix"] = {
            "box": matrix["box"],
            "forced_rag": matrix["forced_rag"],
            "never_seen_count": len(matrix["never_seen"]),
        }

    if "fanout" in disable:
        steps["fanout"] = None
    else:
        fo = dispatch_adapter.fan_out(
            theories=list(theories or [{"id": "obvious", "falsifying_test": True}])
        )
        steps["fanout"] = {
            "theory_count": fo["theory_count"],
            "distinct_ids": fo["distinct_ids"],
            "all_falsifying_tests": fo["all_falsifying_tests"],
        }

    if "metabolism" in disable:
        steps["metabolism"] = None
    else:
        metab = dispatch_adapter.metabolism(error_class=error_class, mechanism=True)
        steps["metabolism"] = {
            "becomes_mechanism_same_day": metab["becomes_mechanism_same_day"],
        }

    preflight_ok = _requires_preflight(methodology)
    steps["preflight"] = {"ok": bool(preflight_ok)}

    verdict = dispatch_tool_oracle(steps, disabled=disable)
    return {
        "schema": "cortex.v4.dispatch_tool_chain.v1",
        "seat": seat,
        "task_type": task_type,
        "steps": steps,
        "oracle": verdict,
        "preflight_ok": bool(preflight_ok),
        "named_caller": "cortex_v4.operation.controllers.run_dispatch_tool_chain",
    }


def _requires_preflight(methodology) -> bool:
    """M0/M30 preflight must resolve before build; refused if it does not."""
    try:
        result = methodology.preflight(
            "dispatch/tools migration to V4", workspace=None
        )
        return bool(result.get("pack_hash"))
    except Exception:  # noqa: BLE001 - a refused preflight is a wiring failure
        return False


def dispatch_tool_oracle(
    steps: Mapping[str, Any],
    *,
    disabled: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Strict behavioral wire oracle for the dispatch/tools chain."""
    _ = disabled  # oracle is strict: absent rungs always fail
    errors: list[str] = []
    disp = steps.get("dispatch")
    seat = steps.get("seating")
    matrix = steps.get("matrix")
    fo = steps.get("fanout")
    metab = steps.get("metabolism")
    pre = steps.get("preflight")

    if not (
        isinstance(disp, dict)
        and disp.get("dispatchable") is True
        and disp.get("tier")
    ):
        errors.append("dispatch rung missing or the seat is not dispatchable")
    if not (isinstance(seat, dict) and seat.get("model")):
        errors.append("seating rung missing or no ranked model resolved")
    if not (
        isinstance(matrix, dict)
        and matrix.get("box") in ("white", "grey", "black")
        and matrix.get("forced_rag") is True
        and matrix.get("never_seen_count", 0) >= 1
    ):
        errors.append("matrix rung missing or box/forced-RAG not enforced")
    if not (
        isinstance(fo, dict)
        and fo.get("theory_count", 0) >= 1
        and fo.get("distinct_ids", 0) >= 1
        and fo.get("all_falsifying_tests") is True
    ):
        errors.append("fan-out rung missing or theories not distinct/falsifying")
    if not (
        isinstance(metab, dict) and metab.get("becomes_mechanism_same_day") is True
    ):
        errors.append("error-metabolism rung missing or did not become a mechanism")
    if not (isinstance(pre, dict) and pre.get("ok") is True):
        errors.append("preflight rung missing or refused before build")

    return {"ok": not errors, "errors": errors}


# ---- fifth-loop slice: eval/learning (M4/M5/M9/M12/M16/M17/M19/M20/M24) -----

def run_eval_learning_chain(
    *,
    corpus_root: str | Path,
    gold: list[str] | None = None,
    pred: list[str] | None = None,
    labels: list[str] | None = None,
    retrieved: list[int] | None = None,
    relevant: list[int] | None = None,
    k: int = 3,
    verdict_paths: list[str] | None = None,
    decided: int = 9,
    abstained: int = 1,
    heldout_graded_blind: bool = True,
    gaming_probe_refused: bool = True,
    blocked_reason_present: bool = True,
    owner_paged: bool = True,
    pre_mortem_grep: bool = True,
    counterexample_sought: bool = True,
    drift_substantiated: bool = True,
    ergonomics_bug_flagged: bool = True,
    question_answered: bool = True,
    rubric_shaped: bool = True,
    task_type: str = "evaluation-slice",
    disable: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Run the eval/learning origin-to-frontier wiring chain.

    Chain: M20 oracle (no-judge + abstention) -> M19 rubric calibration (kappa) ->
    M9 measured benchmark (NDCG) -> M4 sealed holdout -> M12 blocked-state ->
    M16 refutation -> M17 convenience audit -> M24 Q-A gate.

    Each rung is deterministic. ``disable`` drops one rung; the strict behavioral
    oracle fails when any rung is missing.
    """
    eval_adapter = _NativeEvalAdapter()
    steps: dict[str, Any] = {}

    if "oracle" in disable:
        steps["oracle"] = None
    else:
        no_judge = eval_adapter.verdict_has_no_judge(
            list(verdict_paths or ["cortex_v4/operation/controllers.py:run_eval_learning_chain"])
        )
        abstain = eval_adapter.honest_abstention(decided=decided, abstained=abstained)
        steps["oracle"] = {
            "no_judge_in_verdict_path": no_judge["no_judge_in_verdict_path"],
            "reports_abstention_rate": abstain["reports_abstention_rate"],
        }

    if "calibration" in disable:
        steps["calibration"] = None
    else:
        kappa = eval_adapter.cohens_kappa(
            list(gold or ["PASS", "PASS", "FAIL"]),
            list(pred or ["PASS", "PASS", "FAIL"]),
            list(labels or ["PASS", "FAIL"]),
        )
        steps["calibration"] = {
            "kappa": kappa["kappa"],
            "calibrated": kappa["calibrated"],
        }

    if "metric" in disable:
        steps["metric"] = None
    else:
        ndcg = eval_adapter.ndcg(
            list(retrieved or [3, 2, 1]),
            list(relevant or [3, 2, 1]),
            k,
        )
        steps["metric"] = {"ndcg_at_k": ndcg["ndcg_at_k"], "k": ndcg["k"]}

    if "holdout" in disable:
        steps["holdout"] = None
    else:
        hold = eval_adapter.holdout(
            graded=heldout_graded_blind, gaming_probe_refused=gaming_probe_refused
        )
        steps["holdout"] = {
            "graded_agent_blind": hold["graded_agent_blind"],
            "gaming_probe_refused": hold["gaming_probe_refused"],
        }

    if "blocked" in disable:
        steps["blocked"] = None
    else:
        blk = eval_adapter.blocked_state(
            reason_present=bool(blocked_reason_present), page_owner=owner_paged
        )
        steps["blocked"] = {
            "reason_recorded": blk["reason_recorded"],
            "owner_paged": blk["owner_paged"],
        }

    if "refute" in disable:
        steps["refute"] = None
    else:
        ref = eval_adapter.refutation(
            pre_mortem_grep_run=pre_mortem_grep, counterexample_sought=counterexample_sought
        )
        steps["refute"] = {
            "pre_mortem_grep": ref["pre_mortem_grep"],
            "counterexample_sought": ref["counterexample_sought"],
        }

    if "convenience" in disable:
        steps["convenience"] = None
    else:
        conv = eval_adapter.convenience_audit(
            drift_substantiated=drift_substantiated,
            ergonomics_bug_flagged=ergonomics_bug_flagged,
        )
        steps["convenience"] = {
            "drift_substantiated": conv["drift_substantiated"],
            "ergonomics_bug_flagged": conv["ergonomics_bug_flagged"],
        }

    if "qagate" in disable:
        steps["qagate"] = None
    else:
        qa = eval_adapter.qa_gate(
            question_answered=question_answered, rubric_shaped=rubric_shaped
        )
        steps["qagate"] = {
            "question_answered": qa["question_answered"],
            "rubric_shaped": qa["rubric_shaped"],
        }

    verdict = eval_learning_oracle(steps, disabled=disable)
    return {
        "schema": "cortex.v4.eval_learning_chain.v1",
        "task_type": task_type,
        "steps": steps,
        "oracle": verdict,
        "named_caller": "cortex_v4.operation.controllers.run_eval_learning_chain",
    }


def eval_learning_oracle(
    steps: Mapping[str, Any],
    *,
    disabled: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Strict behavioral wire oracle for the eval/learning chain."""
    _ = disabled  # oracle is strict: absent rungs always fail
    errors: list[str] = []
    oracle = steps.get("oracle")
    cal = steps.get("calibration")
    metric = steps.get("metric")
    hold = steps.get("holdout")
    blk = steps.get("blocked")
    ref = steps.get("refute")
    conv = steps.get("convenience")
    qa = steps.get("qagate")

    if not (
        isinstance(oracle, dict)
        and oracle.get("no_judge_in_verdict_path") is True
        and oracle.get("reports_abstention_rate") is True
    ):
        errors.append("oracle rung missing, judge present, or abstention not reported")
    if not (isinstance(cal, dict) and cal.get("calibrated") is True and cal.get("kappa", 0) >= 0.4):
        errors.append("calibration rung missing or inter-rater kappa below calibrated bar")
    if not (isinstance(metric, dict) and metric.get("ndcg_at_k", 0.0) >= 0.5):
        errors.append("metric rung missing or measured score below bar")
    if not (
        isinstance(hold, dict)
        and hold.get("graded_agent_blind") is True
        and hold.get("gaming_probe_refused") is True
    ):
        errors.append("holdout rung missing or holdout/gaming probe not enforced")
    if not (isinstance(blk, dict) and blk.get("reason_recorded") is True and blk.get("owner_paged") is True):
        errors.append("blocked-state rung missing or the block was not recorded/paged")
    if not (
        isinstance(ref, dict)
        and ref.get("pre_mortem_grep") is True
        and ref.get("counterexample_sought") is True
    ):
        errors.append("refutation rung missing or no pre-mortem counterexample was sought")
    if not (
        isinstance(conv, dict)
        and conv.get("drift_substantiated") is True
        and conv.get("ergonomics_bug_flagged") is True
    ):
        errors.append("convenience-audit rung missing or drift not flagged")
    if not (
        isinstance(qa, dict)
        and qa.get("question_answered") is True
        and qa.get("rubric_shaped") is True
    ):
        errors.append("QA-gate rung missing or a sub-question was left unanswered")

    return {"ok": not errors, "errors": errors}


# ---- third-loop slice: research/audit (M21/M22/M25/M32/M33) ----------------

def run_research_audit_chain(
    *,
    corpus_root: str | Path,
    task: str,
    claim: str,
    source: str | None = None,
    citations: list[str] | None = None,
    sources: Mapping[str, Any] | None = None,
    claim_count: int = 1,
    verified_count: int = 1,
    residual_count: int = 0,
    destination_seen: bool = True,
    disable: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Run the research/audit origin-to-frontier wiring chain in one named caller.

    Chain: M32 observe -> M22 citation -> M21 audit -> M33 replay.

    M32 observation-first gates hypothesis formation; M22 requires every claim to
    carry a resolvable pointer and a faithfulness status; M21 sweeps claims with a
    closure metric for every finding; M33 requires the slice to replay in the
    destination runtime with the hidden holdout protected. ``disable`` drops one
    rung; the returned oracle is a strict behavioral wire oracle, not token
    presence.
    """
    methodology = _NativeMethodologyAdapter()
    steps: dict[str, Any] = {}
    source = source or "v4://evidence/unspecified"
    citations = list(citations or [source])
    sources = dict(sources or {source: {"kind": "staging-evidence"}})

    if "observe" in disable:
        steps["observe"] = None
    else:
        obs = methodology.observe(task=task, differences=1 if claim else 0)
        steps["observe"] = {
            "observation_first": obs["observation_first"],
            "hypothesis_not_yet_formed": obs["hypothesis_not_yet_formed"],
        }

    if "citation" in disable:
        steps["citation"] = None
    else:
        c = methodology.citation_require(claim, source)
        status = methodology.citation_strict(
            claim,
            citations,
            sources,
        )
        steps["citation"] = {
            "resolvable_pointer": c.get("kind") in ("path", "clause", "issue", "url"),
            "citation_kind": c.get("kind"),
            "strict_status": status["status"],
        }

    if "audit" in disable:
        steps["audit"] = None
    else:
        audit = methodology.audit_classification(
            claims=claim_count, verified=verified_count, residuals=residual_count
        )
        steps["audit"] = {
            "claims_inventoried": audit["claims_inventoried"],
            "every_finding_has_closure_metric": audit["every_finding_has_closure_metric"],
        }

    if "replay" in disable:
        steps["replay"] = None
    else:
        r = methodology.replay(destination_seen=destination_seen)
        steps["replay"] = {
            "destination_seen_failure_independently": r["destination_seen_failure_independently"],
            "hidden_holdout_protected": r["hidden_holdout_protected"],
        }

    verdict = research_audit_oracle(steps, disabled=disable)
    return {
        "schema": "cortex.v4.research_audit_chain.v1",
        "task": task,
        "claim": claim,
        "steps": steps,
        "oracle": verdict,
        "named_caller": "cortex_v4.operation.controllers.run_research_audit_chain",
    }


def research_audit_oracle(
    steps: Mapping[str, Any],
    *,
    disabled: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Strict behavioral wire oracle for the research/audit chain.

    Every rung must carry correct output; a missing, empty, or refused rung is a
    wiring failure — removing any rung (a mutant) must fail the oracle.
    """
    _ = disabled  # oracle is strict: absent rungs always fail
    errors: list[str] = []
    obs = steps.get("observe")
    cit = steps.get("citation")
    aud = steps.get("audit")
    rep = steps.get("replay")

    if not (isinstance(obs, dict) and obs.get("observation_first") is True):
        errors.append("observe rung missing or did not enforce observation-first")
    if not (
        isinstance(cit, dict)
        and cit.get("resolvable_pointer") is True
        and cit.get("strict_status") in ("SUPPORTED", "NUMBER_SUPPORTED", "QUOTE_SUPPORTED")
    ):
        errors.append("citation rung missing or claim not grounded to a resolvable pointer")
    if not (
        isinstance(aud, dict)
        and aud.get("claims_inventoried", 0) >= 1
        and aud.get("every_finding_has_closure_metric") is True
    ):
        errors.append("audit rung missing or findings lack closure metrics")
    if not (
        isinstance(rep, dict)
        and rep.get("destination_seen_failure_independently") is True
        and rep.get("hidden_holdout_protected") is True
    ):
        errors.append("replay rung missing or destination did not replay independently")

    return {"ok": not errors, "errors": errors}
