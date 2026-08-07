"""Run the Model Summon/tool-call boundary replay without making a model request."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SSC = Path(r"D:\claude\stupidly-simple-cortex")
for path in (ROOT, SSC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from cortex_v4.adapters import SSCSummonAdapter  # noqa: E402

DECK = ROOT / "observations" / "decks" / "summon-tools-abcd-20260805.json"


def main() -> int:
    source = __import__("cortex_core.model_summon", fromlist=["*"])
    source_runtime = __import__("cortex_core.agent_runtime", fromlist=["*"])
    source_spec = source.resolve_summon("kimi").__dict__.copy()
    source_chain = list(source.seat_dispatch_chain("kimi"))
    source_tools = sorted(source_runtime.TOOLS)
    source_mutating = sorted(source_runtime.MUTATING_TOOLS)

    adapter = SSCSummonAdapter(SSC)
    v4_spec = adapter.resolve("kimi")
    v4_chain = adapter.dispatch_chain("kimi")
    v4_tools = adapter.tool_names()
    v4_mutating = adapter.mutating_tool_names()

    c_checks: dict[str, object] = {}
    try:
        adapter.resolve("not-an-owner-seat")
    except KeyError:
        c_checks["unknown_seat_refused"] = True
    else:
        c_checks["unknown_seat_refused"] = False
    c_checks["hazardous_write_refused"] = not adapter.mutation_decision(
        "write_file",
        {"path": "ops-local/.env", "content": "hidden"},
        allow_hard_mutations=False,
    )["allowed"]

    deck = {
        "schema": "cortex.v4.migration_observation.v1",
        "source": "SSC model summon and tool-call controls",
        "status": "candidate_for_ssc_holdout",
        "axes": {
            "A": {
                "status": "source_observed",
                "seat": source_spec,
                "dispatch_chain": source_chain,
                "tools": source_tools,
                "mutating_tools": source_mutating,
            },
            "B": {
                "status": "v4_adapter_observed",
                "seat": v4_spec,
                "dispatch_chain": v4_chain,
                "tools": v4_tools,
                "mutating_tools": v4_mutating,
            },
            "C": {"status": "negative_control", **c_checks},
            "D": {"status": "awaiting_external_ssc_holdout"},
        },
    }
    DECK.parent.mkdir(parents=True, exist_ok=True)
    DECK.write_text(json.dumps(deck, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(deck, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

