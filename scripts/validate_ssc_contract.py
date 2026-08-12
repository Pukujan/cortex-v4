#!/usr/bin/env python3
"""Validate the narrow SSC behavioral contract consumed by Cortex."""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "contracts" / "ssc-contract-v1.json").read_text(encoding="utf-8"))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit("SSC_CONTRACT_FAIL: " + message)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    require(root.is_dir(), f"root missing: {root}")
    sys.path.insert(0, str(root))

    checked = {}
    for name, symbols in MANIFEST["required_modules"].items():
        module = importlib.import_module(name)
        missing = [symbol for symbol in symbols if not hasattr(module, symbol)]
        require(not missing, f"{name} missing symbols {missing}")
        checked[name] = list(symbols)

    summon = importlib.import_module("cortex_core.model_summon")
    spec = summon.resolve_summon("kimi")
    expected = MANIFEST["behavior"]["kimi"]
    for field, value in expected.items():
        require(getattr(spec, field, None) == value, f"kimi {field} drift")
    require(
        tuple(summon.seat_dispatch_chain("kimi")[0]) == (expected["tier"], expected["model_override"]),
        "kimi dispatch chain drift",
    )

    runtime = importlib.import_module("cortex_core.agent_runtime")
    require(sorted(runtime.MUTATING_TOOLS) == MANIFEST["behavior"]["mutating_tools"], "mutation surface drift")
    require(runtime.mutation_gate("read_file", {}).allowed is True, "read_file unexpectedly refused")
    require(
        runtime.mutation_gate("write_file", {}, allow_hard_mutations=False).allowed is False,
        "hard mutation fail-close drift",
    )

    manual = (root / "docs/methodology/WORK-METHODOLOGIES.md").read_text(encoding="utf-8")
    for mid in MANIFEST["behavior"]["methodology_ids"]:
        require(f"## {mid}." in manual, f"methodology {mid} absent")

    preflight = importlib.import_module("cortex_core.session_preflight").run_preflight(
        "contract probe", workspace=root
    )
    require(bool(getattr(preflight, "pack_hash", "")), "preflight missing pack_hash")

    print(json.dumps({
        "schema": MANIFEST["schema"],
        "root": str(root),
        "module_count": len(checked),
        "behavior": "PASS",
        "private_source_copied": False,
        "hidden_holdout_copied": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
