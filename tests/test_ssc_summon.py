from __future__ import annotations

from pathlib import Path

from cortex_v4.adapters import SSCSummonAdapter


SSC = Path(r"D:\claude\stupidly-simple-cortex")


def test_summon_adapter_preserves_owner_seat_resolution():
    adapter = SSCSummonAdapter(SSC)
    spec = adapter.resolve("kimi")
    assert spec["seat"] == "kimi"
    # Owner policy routes ordinary seat resolution through the V4 LiteLLM
    # boundary; direct CKFF is reserved for explicit recovery mode.
    assert spec["tier"] == "litellm-ckff"
    assert spec["model_override"] == "kimi-k2.7-code"
    assert adapter.dispatch_chain("kimi")[0] == ("litellm-ckff", "kimi-k2.7-code")


def test_summon_adapter_exposes_same_tool_surface_and_mutation_boundary():
    adapter = SSCSummonAdapter(SSC)
    assert {"read_file", "write_file", "run"}.issubset(adapter.tool_names())
    assert adapter.mutating_tool_names() == ["edit_file", "run", "write_file"]
    allowed = adapter.mutation_decision("read_file", {"path": "README.md"})
    assert allowed["allowed"] is True
    refused = adapter.mutation_decision(
        "write_file",
        {"path": "ops-local/.env", "content": "do not write"},
        allow_hard_mutations=False,
    )
    assert refused["allowed"] is False


def test_summon_adapter_rejects_unknown_seat():
    adapter = SSCSummonAdapter(SSC)
    try:
        adapter.resolve("not-an-owner-seat")
    except KeyError:
        pass
    else:
        raise AssertionError("unknown seat was not refused")

