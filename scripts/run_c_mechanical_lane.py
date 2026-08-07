"""Populate C-v4-mechanical lane evidence from a real mechanical loop run."""
from __future__ import annotations

import json
from pathlib import Path

from cortex_v4.control.mechanical_loop import MechanicalLoopController

SSC = Path(r"D:\claude\stupidly-simple-cortex")
PUBLIC = SSC / "observations" / "loop-engineering" / "20260805-litellm" / "public"
LANE = SSC / "observations" / "loop-engineering" / "20260805-litellm" / "C-v4-mechanical"
OPS = SSC / "ops-local" / "loop-engineering" / "20260805-litellm" / "C-v4-mechanical"

EXPECTED = {
    "failure-injector.json": "6749d7cba00af5afb7c4300f899bf207707977da73d96c4f7d0ac060a16b59c0",
    "objective-checker.py": "2477e1ab7afc3061951390b17993b48fea7991726ac3a2652cffef5c0a79c629",
    "task-contract.json": "a28ed19634077adef994fb52101c6eaa3c63f6ba7931726c8c0a3773531c57f5",
    "tool-contract.json": "17c7476141306bb9e041e54c658c19cddf078706774f710824a733e9c4ab735e",
}


def dump(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    print("wrote", path)


def main() -> int:
    LANE.mkdir(parents=True, exist_ok=True)
    (OPS / "workspaces").mkdir(parents=True, exist_ok=True)
    (OPS / "receipts").mkdir(parents=True, exist_ok=True)

    controller = MechanicalLoopController(
        ssc_root=SSC,
        public_fixture_dir=PUBLIC,
        work_root=OPS / "workspaces",
        expected_freeze=EXPECTED,
    )
    result = controller.run()
    print("ok=", result.ok)
    print("stage=", result.stage)
    print("refused=", result.refused)
    print("methodology=", result.methodology_ids)
    print("mutants_killed=", sum(1 for m in result.mutants if m["killed"]))
    print("strong_obj=", result.strong_result.get("objective_ok"))
    print("weak_obj=", result.weak_result.get("objective_ok"))
    if not result.ok:
        dump(OPS / "receipts" / "mechanical-run-failed.json", result.to_dict())
        return 1

    dump(LANE / "methodology-receipt.json", result.methodology_receipt)

    dump(
        LANE / "gate-results.json",
        {
            "schema": "cortex.loop_engineering.gate_results.v1",
            "lane": "C-v4-mechanical",
            "fixture_id": "20260805-litellm",
            "stage": "C-mechanical",
            "ok": result.ok,
            "gates": result.gates,
            "summary": {
                "total": len(result.gates),
                "passed": sum(1 for g in result.gates if g.get("ok")),
                "failed": sum(1 for g in result.gates if not g.get("ok")),
            },
        },
    )

    dump(
        LANE / "objective-result.json",
        {
            "schema": "cortex.loop_engineering.objective_result.v1",
            "lane": "C-v4-mechanical",
            "fixture_id": "20260805-litellm",
            "stage": "C-mechanical",
            "status": "recorded",
            "checker": str(PUBLIC / "objective-checker.py"),
            "results": {
                "c_weak_observation": {
                    "workspace": str(OPS / "workspaces" / "c-weak-observation"),
                    "passed": False,
                    "result": result.weak_result.get("objective"),
                    "note": "Expected fail: mechanical observation of recovery-contract violation.",
                },
                "c_strong_recovered": {
                    "workspace": str(OPS / "workspaces" / "c-strong-recovered"),
                    "passed": bool(result.strong_result.get("objective_ok")),
                    "result": result.strong_result.get("objective"),
                    "note": "Mechanical strong path after observation+hypothesis gates.",
                },
            },
            "passed": bool(result.strong_result.get("objective_ok")),
            "primary_workspace": "c_strong_recovered",
            "live_provider_objective": {
                "status": "UNRESOLVED",
                "class": "ENVIRONMENT",
                "note": "No real LiteLLM provider spend this turn; live parity deferred.",
            },
        },
    )

    dump(
        LANE / "mutant-results.json",
        {
            "schema": "cortex.loop_engineering.mutant_results.v1",
            "lane": "C-v4-mechanical",
            "fixture_id": "20260805-litellm",
            "stage": "C-mechanical",
            "status": "recorded",
            "mutants": result.mutants,
            "summary": {
                "total": len(result.mutants),
                "killed": sum(1 for m in result.mutants if m.get("killed")),
                "survived": sum(1 for m in result.mutants if m.get("regression")),
            },
        },
    )

    dump(
        LANE / "deck.json",
        {
            "schema": "cortex.loop_engineering.deck.v1",
            "lane": "C-v4-mechanical",
            "fixture_id": "20260805-litellm",
            "authority": "public_fixture_and_v4_mechanical_gates_only",
            "axes": {
                "classification": {
                    "methodology_ids": result.methodology_ids,
                    "task_class": result.methodology_receipt.get("task_class"),
                },
                "C_weak_observation": {
                    "ok": result.weak_result.get("ok"),
                    "boundary": result.weak_result.get("boundary"),
                    "final": result.weak_result.get("final"),
                    "objective_ok": result.weak_result.get("objective_ok"),
                    "event_kinds": result.weak_result.get("event_kinds"),
                },
                "C_strong_repair": {
                    "ok": result.strong_result.get("ok"),
                    "boundary": result.strong_result.get("boundary"),
                    "generation": result.strong_result.get("generation"),
                    "post_stall_retries": result.strong_result.get("post_stall_retries"),
                    "max_active": result.strong_result.get("max_active"),
                    "completed_steps": result.strong_result.get("completed_steps"),
                    "objective_ok": result.strong_result.get("objective_ok"),
                    "event_kinds": result.strong_result.get("event_kinds"),
                },
                "hypotheses": result.hypotheses,
                "mutants": {
                    m["id"]: {
                        "killed": m["killed"],
                        "ok": m["ok"],
                        "boundary": m.get("boundary"),
                    }
                    for m in result.mutants
                },
                "gates": {g["gate"]: g["ok"] for g in result.gates},
            },
            "freeze": {
                "evidence_pack_hash": result.freeze.get("evidence_pack_hash"),
                "task_contract_hash": result.freeze.get("task_contract_hash"),
                "files": result.freeze.get("files"),
            },
            "residuals": result.residuals,
            "observation_id": result.observation.get("observation_id"),
        },
    )

    dump(OPS / "receipts" / "mechanical-run.json", result.to_dict())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
