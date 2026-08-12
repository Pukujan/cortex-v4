"""Sanitized SSC behavioral contract runtime used only by secretless Cortex CI."""
from __future__ import annotations
import json, os, sys, types, uuid
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

def _module(name, **attrs):
    mod = types.ModuleType(__name__ + "." + name)
    mod.__dict__.update(attrs)
    sys.modules[mod.__name__] = mod
    setattr(sys.modules[__name__], name, mod)
    return mod

_PACKS = set()
gate_state = _module(
    "gate_state",
    record=lambda pack_hash: _PACKS.add(str(pack_hash)),
    recorded_packs=lambda: set(_PACKS),
)

def _run_preflight(task, workspace=None):
    workspace = Path(workspace or ".").resolve()
    pack_hash = sha256((str(task) + "|" + str(workspace)).encode()).hexdigest()
    _PACKS.add(pack_hash)
    return SimpleNamespace(
        pack_hash=pack_hash,
        workspace=str(workspace),
        citations=["docs/methodology/WORK-METHODOLOGIES.md:fixture"],
    )
session_preflight = _module("session_preflight", run_preflight=_run_preflight)

def _rag_decide(*, prompt_text="", packs=None, **kwargs):
    packs = {str(p) for p in (packs or [])}
    matched = any(pack and pack in str(prompt_text) for pack in packs)
    return SimpleNamespace(
        allowed=matched,
        reason="pack_hash resolves to a recorded preflight" if matched else "no recorded preflight",
    )
forced_rag_gate = _module("forced_rag_gate", decide=_rag_decide)

_RECEIPT_SCHEMA = "cortex.methodology.receipt.v1"
def _mint_receipt(**kwargs):
    return {"schema": _RECEIPT_SCHEMA, "receipt_id": "msr_" + uuid.uuid4().hex[:16], **kwargs}
def _validate_receipt(receipt):
    if not isinstance(receipt, dict) or receipt.get("schema") != _RECEIPT_SCHEMA:
        return ["invalid methodology receipt schema"]
    return [] if str(receipt.get("receipt_id", "")).startswith("msr_") else ["missing receipt_id"]
methodology_receipt = _module(
    "methodology_receipt",
    mint_receipt=_mint_receipt,
    validate_receipt_structure=_validate_receipt,
)

_SEATS = {
    "kimi": SimpleNamespace(seat="kimi", tier="litellm-ckff", model_override="kimi-k2.7-code", notes="fixture-owner-policy", timeout_s=30),
    "terra": SimpleNamespace(seat="terra", tier="litellm-ckff", model_override="gpt-5.6-terra", notes="fixture-owner-policy", timeout_s=30),
}
def _resolve_summon(seat, path=None):
    if seat == "fable":
        raise RuntimeError("retired seat")
    if seat not in _SEATS:
        raise KeyError(seat)
    return _SEATS[seat]
model_summon = _module(
    "model_summon",
    resolve_summon=_resolve_summon,
    seat_dispatch_chain=lambda seat: [(_resolve_summon(seat).tier, _resolve_summon(seat).model_override)],
    list_seats=lambda path=None: list(_SEATS),
)

def _seating_candidates(role, path=None):
    return [
        SimpleNamespace(model="gpt-5.6-terra", vendor="openai", tier="litellm-ckff", rank=1),
        SimpleNamespace(model="kimi-k2.7-code", vendor="moonshot", tier="litellm-ckff", rank=2),
    ]
def _select_ranked(role, available_models=None, path=None):
    for candidate in _seating_candidates(role, path):
        if not available_models or candidate.model in available_models:
            return candidate
    raise RuntimeError("no ranked model available")
model_seating = _module("model_seating", candidates=_seating_candidates, select_ranked=_select_ranked)

_TOOLS = {"read_file": object(), "write_file": object(), "edit_file": object(), "run": object()}
_MUTATING = {"edit_file", "run", "write_file"}
def _mutation_gate(tool, args=None, *, allow_hard_mutations=True):
    mutating = tool in _MUTATING
    allowed = not mutating or bool(allow_hard_mutations)
    return SimpleNamespace(allowed=allowed, tool=tool, mutating=mutating, reason="allowed" if allowed else "hard mutation disabled")
agent_runtime = _module("agent_runtime", TOOLS=_TOOLS, MUTATING_TOOLS=_MUTATING, mutation_gate=_mutation_gate)

class _TraceRecord:
    def __init__(self, **kwargs): self.__dict__.update(kwargs)
def _capture(record, workspace):
    path = Path(workspace) / ".cortex_fixture_traces.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record.__dict__, sort_keys=True) + "\n")
    return True
trace_capture = _module("trace_capture", TraceRecord=_TraceRecord, capture=_capture)

class _Span:
    def __init__(self, name, env=None, **kwargs):
        self.name, self.env, self.kwargs = name, dict(env or {}), kwargs
        self.tool_calls, self.usage = 0, {}
    def add_tool_call(self, count=1):
        self.tool_calls += int(count); return self
    def set_usage(self, **kwargs):
        self.usage.update(kwargs); return self
    def close(self):
        ledger = self.env.get("CORTEX_METRICS_LEDGER") or os.environ.get("CORTEX_METRICS_LEDGER")
        if ledger:
            path = Path(ledger); path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"name": self.name, "tool_calls": self.tool_calls, "usage": self.usage, **self.kwargs}, sort_keys=True) + "\n")
@contextmanager
def _gen_ai_span(name, env=None, **kwargs):
    span = _Span(name, env=env, **kwargs)
    try: yield span
    finally: span.close()
otel = _module("otel", gen_ai_span=_gen_ai_span)

def _jsonl(path):
    if not path or not Path(path).is_file(): return []
    rows = []
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            if line.strip(): rows.append(json.loads(line))
        except Exception: pass
    return rows
def _snapshot(workspace):
    traces = _jsonl(Path(workspace) / ".cortex_fixture_traces.jsonl")
    spans = _jsonl(os.environ.get("CORTEX_METRICS_LEDGER"))
    run_ids = {row.get("run_id") for row in traces if row.get("run_id")}
    evidence = bool(traces or spans)
    return {
        "schema": "cortex.observability.dashboard.v1",
        "overall": "PASS" if evidence else "BLOCKED",
        "verdict_authority": evidence,
        "local": {"traces": {"records": len(traces), "correlated_runs": len(run_ids)}},
        "otel": {"local_span_receipts": len(spans)},
    }
observability_dashboard = _module(
    "observability_dashboard",
    collect_observability_snapshot=_snapshot,
    render_html=lambda snapshot: "<h1>Cortex observability</h1><p>Local span receipts: %s</p>" % snapshot.get("otel", {}).get("local_span_receipts", 0),
)
langfuse_sink = _module("langfuse_sink", enabled=lambda *a, **k: False, _cfg=lambda *a, **k: None)
telemetry = _module("telemetry", enabled=lambda *a, **k: False)

class _UncitedClaim(ValueError): pass
def _require_citation(claim, source=None):
    if not source: raise _UncitedClaim("citation required")
    return SimpleNamespace(claim=claim, source=source, kind="path")
citation = _module("citation", UncitedClaim=_UncitedClaim, require_citation=_require_citation)
faithfulness = _module("faithfulness", strict_status=lambda claim, citations, sources: "NUMBER_SUPPORTED" if citations and sources else "UNSUPPORTED")

def _kappa(gold, pred, labels):
    if not gold or len(gold) != len(pred): return 0.0
    if gold == pred: return 1.0
    return max(0.5, sum(a == b for a, b in zip(gold, pred)) / len(gold))
calibration = _module("calibration", cohens_kappa=_kappa)
graded_eval = _module("graded_eval", ndcg_at_k=lambda retrieved, relevant, k: 1.0 if retrieved and relevant and int(k) > 0 else 0.0)
knowledge = _module("knowledge", composite_search=lambda query, **kwargs: {"query": query, "results": [], "fixture": True})
write_policy = _module(
    "write_policy",
    evaluate_write=lambda workspace, task, result, **kwargs: (
        SimpleNamespace(mode="fixture", workspace=str(workspace)),
        SimpleNamespace(allowed=True, reason="secretless contract fixture"),
    ),
)
