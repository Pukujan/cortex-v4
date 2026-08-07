"""Call-graph wire oracle for the methodology-core vertical slice (F1/H1).

H1 hardening for the F1 residual: the frozen public objective-checker is
token-presence only — it flags ``ok`` whenever any V4 file mentions the adapter
plus the words preflight/forced_rag/validate_receipt, so deleting the named caller
entirely (or dropping one rung) still returns ``ok:true``. This oracle is the
second layer that proves the wire by IMPORT + CALL GRAPH rather than string
presence:

  * it imports the real named caller and resolves it as a callable (a deleted
    caller raises WIRE_CALLER_MISSING, which the token checker cannot see);
  * it statically parses the caller's AST and requires that the three adapter
    rung methods (preflight, forced_rag_decide, validate_receipt) are genuinely
    CALLED from the named function body — not merely mentioned; and
  * it refuses ``ok`` when any one rung is missing in the import or the call
    graph.

Deterministic, stdlib-only, no provider spend. It does NOT edit or replace the
frozen public objective-checker; that file stays the standalone sanity pass with
its hash frozen. The runtime behavioral oracle lives in
``cortex_v4.operation.methodology_origin_oracle``; this module is the static
call-graph layer that pairs with it.
"""
from __future__ import annotations

import ast
import hashlib
import importlib
import inspect
import json
from typing import Any, Callable, Iterable

NAMED_CALLER_MODULE = "cortex_v4.operation.controllers"
NAMED_FUNCTION = "run_methodology_origin_chain"
ADAPTER_QUALNAME = "cortex_v4.adapters.ssc_methodology.SSCMethodologyAdapter"
RUNG_METHODS: tuple[str, ...] = ("preflight", "forced_rag_decide", "validate_receipt")

RESEARCH_AUDIT_FUNCTION = "run_research_audit_chain"
RESEARCH_AUDIT_RUNGS: tuple[str, ...] = (
    "observe",
    "citation_require",
    "citation_strict",
    "audit_classification",
    "replay",
)

DISPATCH_TOOL_FUNCTION = "run_dispatch_tool_chain"
DISPATCH_TOOL_RUNGS: tuple[str, ...] = (
    "resolve_summon",
    "selected_rank",
    "seat_matrix",
    "fan_out",
    "metabolism",
)

EVAL_LEARNING_FUNCTION = "run_eval_learning_chain"
EVAL_LEARNING_RUNGS: tuple[str, ...] = (
    "verdict_has_no_judge",
    "cohens_kappa",
    "ndcg",
    "holdout",
    "blocked_state",
    "refutation",
    "convenience_audit",
    "qa_gate",
)

SCHEMA = "cortex.v4.call_graph_wire.v1"


class WireOracleError(RuntimeError):
    """Raised when the call-graph wire oracle refuses the wiring claim."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _rung_methods_called(func_def: ast.AST, rungs: Iterable[str] = RUNG_METHODS) -> set[str]:
    """Return the rung method names genuinely CALLED inside one AST block.

    Counts ``ast.Call`` nodes whose callee is ``ast.Attribute`` with an ``.attr``
    in the given rung set. A bare mention (string, unused reference, assignment) is
    not a call and does not count.
    """
    allowed = set(rungs)
    return {
        call.func.attr
        for call in ast.walk(func_def)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr in allowed
    }


def resolve_named_function(
    module: str = NAMED_CALLER_MODULE, fn_name: str = NAMED_FUNCTION
) -> Callable[..., Any]:
    """Import the real named caller and return the function object.

    Raises WireOracleError if the module or the named function cannot be resolved
    (a deleted caller). This is the part a token-presence checker cannot do.
    """
    try:
        mod = importlib.import_module(module)
    except Exception as exc:  # noqa: BLE001 - surface as oracle refusal, not crash
        raise WireOracleError(
            "WIRE_MODULE_MISSING", f"cannot import {module}: {exc}"
        ) from exc
    fn = getattr(mod, fn_name, None)
    if not callable(fn):
        raise WireOracleError(
            "WIRE_CALLER_MISSING",
            f"named caller {module}.{fn_name} is not callable or is absent",
        )
    return fn


def static_call_graph(
    fn: Callable[..., Any], rungs: Iterable[str] = RUNG_METHODS
) -> dict[str, Any]:
    """Prove by AST that the one named function calls all of the required rungs."""
    rungs = tuple(rungs)
    source = inspect.getsource(fn)
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:  # pragma: no cover - defensive
        raise WireOracleError("WIRE_AST_PARSE_FAILED", str(exc)) from exc
    func_def = next(
        (
            n
            for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ),
        None,
    )
    if func_def is None:
        raise WireOracleError(
            "WIRE_FN_NOT_FOUND", f"no function def found in {fn.__qualname__}"
        )
    called = _rung_methods_called(func_def, rungs)
    missing = [m for m in rungs if m not in called]
    return {
        "schema": SCHEMA,
        "caller": f"{fn.__module__}.{fn.__qualname__}",
        "rung_methods_called": sorted(called),
        "rung_methods_required": list(rungs),
        "missing_rungs": missing,
        "ok": not missing,
        "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
    }


_CALLER_TARGETS: dict[str, tuple[str, tuple[str, ...]]] = {
    f"{NAMED_CALLER_MODULE}.{NAMED_FUNCTION}": (RUNG_METHODS, "methodology-core"),
    f"{NAMED_CALLER_MODULE}.{RESEARCH_AUDIT_FUNCTION}": (
        RESEARCH_AUDIT_RUNGS,
        "research/audit",
    ),
    f"{NAMED_CALLER_MODULE}.{DISPATCH_TOOL_FUNCTION}": (
        DISPATCH_TOOL_RUNGS,
        "dispatch/tools",
    ),
    f"{NAMED_CALLER_MODULE}.{EVAL_LEARNING_FUNCTION}": (
        EVAL_LEARNING_RUNGS,
        "eval/learning",
    ),
}


def _lookup(module: str, fn_name: str) -> tuple[tuple[str, ...], str]:
    key = f"{module}.{fn_name}"
    rungs, slice_name = _CALLER_TARGETS.get(
        key, (RUNG_METHODS, f"{module}.{fn_name}")
    )
    return rungs, slice_name


def resolve_named_function(
    module: str = NAMED_CALLER_MODULE, fn_name: str = NAMED_FUNCTION
) -> Callable[..., Any]:
    """Import the real named caller and return the function object.

    Raises WireOracleError if the module or the named function cannot be resolved
    (a deleted caller). This is the part a token-presence checker cannot do.
    """
    try:
        mod = importlib.import_module(module)
    except Exception as exc:  # noqa: BLE001 - surface as oracle refusal, not crash
        raise WireOracleError(
            "WIRE_MODULE_MISSING", f"cannot import {module}: {exc}"
        ) from exc
    fn = getattr(mod, fn_name, None)
    if not callable(fn):
        raise WireOracleError(
            "WIRE_CALLER_MISSING",
            f"named caller {module}.{fn_name} is not callable or is absent",
        )
    return fn


def call_graph_oracle(
    module: str = NAMED_CALLER_MODULE, fn_name: str = NAMED_FUNCTION
) -> dict[str, Any]:
    """Import the caller and compute its static call graph, refusing on failure.

    Returns a stable dict (never raises) so a test or runner can print it. A
    missing caller yields ok:false with the caller's required rungs all missing.
    """
    rungs, slice_label = _lookup(module, fn_name)
    req = list(rungs)
    try:
        fn = resolve_named_function(module, fn_name)
    except WireOracleError as exc:
        return {
            "schema": SCHEMA,
            "caller": f"{module}.{fn_name}",
            "slice": slice_label,
            "rung_methods_called": [],
            "rung_methods_required": req,
            "missing_rungs": req,
            "ok": False,
            "source_sha256": "",
            "refused": {"code": exc.code, "message": exc.message},
        }
    graph = static_call_graph(fn, rungs)
    graph["slice"] = slice_label
    graph["errors"] = (
        [f"missing static call rung: {r}" for r in graph["missing_rungs"]]
        if graph["missing_rungs"]
        else []
    )
    return graph


def refuse_callgraph_when_rung_missing(
    module: str = NAMED_CALLER_MODULE, fn_name: str = NAMED_FUNCTION
) -> dict[str, Any]:
    """Evaluate the wire; raise WireOracleError if any rung is missing."""
    graph = call_graph_oracle(module, fn_name)
    if not graph.get("ok"):
        raise WireOracleError(
            "WIRE_RUNG_MISSING",
            "; ".join(graph.get("errors") or graph.get("missing_rungs") or ["missing rung"]),
        )
    return graph


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="call-graph wire oracle for the methodology-core slice (F1/H1)"
    )
    parser.add_argument(
        "--caller",
        default=f"{NAMED_CALLER_MODULE}.{NAMED_FUNCTION}",
        help="dotted module.function to prove",
    )
    args = parser.parse_args(argv)
    module, _, name = args.caller.rpartition(".")
    result = call_graph_oracle(module, name)
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())