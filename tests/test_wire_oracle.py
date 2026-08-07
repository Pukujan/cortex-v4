"""Wire-oracle F1/H1 hardening tests — call graph, not token presence.

Proves the second-layer oracle fixes the F1 residual: the frozen public
objective-checker is token-presence and self-matches the adapter, so it cannot
detect a deleted caller or a dropped rung. This oracle imports the real caller and
proves the three rungs appear as actual calls in the one named function body.
"""
from __future__ import annotations

import ast
import importlib
import textwrap
from types import ModuleType

import pytest

from cortex_v4.control.wire_oracle import (
    RUNG_METHODS,
    WireOracleError,
    _rung_methods_called,
    call_graph_oracle,
    refuse_callgraph_when_rung_missing,
    resolve_named_function,
    static_call_graph,
)


def _callable(module: ModuleType, name: str):
    return getattr(module, name)


@pytest.fixture(scope="module")
def skeleton():
    """A synthetic module mirroring the real caller's call shape, generated in a
    temp location so tests can mutate it without touching the real controllers."""
    import sys
    import tempfile
    from pathlib import Path

    tmp = Path(tempfile.mkdtemp(prefix="wire_oracle_"))
    (tmp / "__init__.py").write_text("", encoding="utf-8")
    src = textwrap.dedent(
        """
        class Adapter:
            def preflight(self, *a, **k): return {}
            def forced_rag_decide(self, *a, **k): return {}
            def validate_receipt(self, *a, **k): return []

        def run_methodology_origin_chain(*a, **k):
            ad = Adapter()
            ad.preflight("t")
            ad.forced_rag_decide()
            ad.validate_receipt({})
            return {}

        def run_dropped_preflight(*a, **k):
            ad = Adapter()
            # token IS present as a string, but preflight is never CALLED
            _ = "preflight forced_rag_decide validate_receipt"
            ad.forced_rag_decide()
            ad.validate_receipt({})
            return {}
        """
    )
    (tmp / "skeleton_mod.py").write_text(src, encoding="utf-8")
    sys.path.insert(0, str(tmp))
    spec = importlib.util.spec_from_file_location("wire_skel", tmp / "skeleton_mod.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    yield mod
    sys.path.remove(str(tmp))


def test_real_named_caller_resolves():
    fn = resolve_named_function()
    assert callable(fn)
    assert fn.__module__ == "cortex_v4.operation.controllers"
    assert fn.__name__ == "run_methodology_origin_chain"


def test_real_call_graph_complete():
    graph = call_graph_oracle()
    assert graph["ok"] is True
    assert set(graph["rung_methods_called"]) == set(RUNG_METHODS)
    assert graph["missing_rungs"] == []
    assert graph["source_sha256"]


def test_refuse_does_not_raise_on_complete_real_wire():
    graph = refuse_callgraph_when_rung_missing()
    assert graph["ok"] is True


def test_caller_missing_module_refused():
    graph = call_graph_oracle("no.such.module.does_not_exist", "f")
    assert graph["ok"] is False
    assert graph["refused"]["code"] == "WIRE_MODULE_MISSING"
    with pytest.raises(WireOracleError) as exc:
        refuse_callgraph_when_rung_missing("no.such.module.does_not_exist", "f")
    assert exc.value.code == "WIRE_RUNG_MISSING"


def test_caller_missing_function_refused():
    graph = call_graph_oracle("cortex_v4.operation.controllers", "definitely_not_here")
    assert graph["ok"] is False
    assert graph["refused"]["code"] == "WIRE_CALLER_MISSING"


def test_classify_rung_mentions_vs_calls(skeleton):
    tree = ast.parse(inspect_text(skeleton))
    fn_good = next(n for n in tree.body if n.name == "run_methodology_origin_chain")
    fn_bad = next(n for n in tree.body if n.name == "run_dropped_preflight")
    good = _rung_methods_called(fn_good)
    bad = _rung_methods_called(fn_bad)
    assert good == set(RUNG_METHODS)
    # The dropped-preflight skeleton has the string tokens but not the CALL.
    assert "preflight" not in bad
    assert "forced_rag_decide" in bad
    assert "validate_receipt" in bad


def test_static_call_graph_ok_on_skeleton_good(skeleton):
    fn = _callable(skeleton, "run_methodology_origin_chain")
    graph = static_call_graph(fn)
    assert graph["ok"] is True
    assert set(graph["rung_methods_called"]) == set(RUNG_METHODS)


def test_static_call_graph_refuses_dropped_preflight(skeleton):
    fn = _callable(skeleton, "run_dropped_preflight")
    graph = static_call_graph(fn)
    assert graph["ok"] is False
    assert "preflight" in graph["missing_rungs"]
    assert set(graph["rung_methods_required"]) == set(RUNG_METHODS)


def test_refuse_raises_on_dropped_rung(skeleton):
    fn = _callable(skeleton, "run_dropped_preflight")
    # monkeypatch the module-level default so refuse_callgraph targets the bad fn
    import cortex_v4.control.wire_oracle as wo

    def _fake_refuse():
        graph = static_call_graph(fn)
        if not graph["ok"]:
            raise WireOracleError(
                "WIRE_RUNG_MISSING",
                "missing static call rung(s): " + ", ".join(graph["missing_rungs"]),
            )
        return graph

    with pytest.raises(WireOracleError) as exc:
        _fake_refuse()
    assert exc.value.code == "WIRE_RUNG_MISSING"
    assert "preflight" in exc.value.message


def test_dropped_rung_marked_missing_in_errors(skeleton):
    fn = _callable(skeleton, "run_dropped_preflight")
    graph = static_call_graph(fn)
    errors = [
        f"missing static call rung: {r}" for r in graph["missing_rungs"]
    ]
    assert any("preflight" in e for e in errors)


def test_no_rung_survives_mutant(skeleton):
    """A well-formed caller must call every rung; every rung must be provable by a
    call (refuses a prose/token mention substitution)."""
    for attr in ("run_methodology_origin_chain",):
        fn = _callable(skeleton, attr)
        graph = static_call_graph(fn)
        assert graph["ok"] is True


def test_wire_addr_required_not_just_present():
    """The oracle is scoped to the ONE named function: a helper that does the calls
    but is not called from this function must not make this function pass."""
    src = textwrap.dedent(
        """
        def _do_all(ad):
            ad.preflight("t")
            ad.forced_rag_decide()
            ad.validate_receipt({})

        def run_methodology_origin_chain():
            # does NOT call _do_all
            return {}
        """
    )
    tree = ast.parse(src)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run_methodology_origin_chain")
    called = _rung_methods_called(fn)
    assert called == set()
    assert set(RUNG_METHODS) - called


def _callable(mod, name):
    return getattr(mod, name)


def inspect_text(mod):
    import inspect as _i

    return _i.getsource(mod)