#!/usr/bin/env python3
"""Materialize the repo-owned SSC contract fixture for secretless hosted CI."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "contracts" / "ssc-contract-v1.json").read_text(encoding="utf-8"))
FIXTURE = ROOT / MANIFEST["fixture_root"]
SSC_COMPAT = ROOT / MANIFEST["hardcoded_compat_paths"]["ssc"]
V4_COMPAT = ROOT / MANIFEST["hardcoded_compat_paths"]["v4"]

EXTENDED_OBJECTIVE = '''from __future__ import annotations
import json
from pathlib import Path

REQUIRED = (
    "artifacts/review.md",
    "artifacts/evidence.json",
    "artifacts/checks.txt",
)

def check(root):
    root = Path(root)
    missing = []
    malformed = []
    for rel in REQUIRED:
        path = root / rel
        if not path.is_file() or not path.read_text(encoding="utf-8", errors="replace").strip():
            missing.append(rel)
    evidence = root / "artifacts/evidence.json"
    if evidence.is_file():
        try:
            value = json.loads(evidence.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or not isinstance(value.get("route"), dict):
                malformed.append("artifacts/evidence.json")
        except Exception:
            malformed.append("artifacts/evidence.json")
    return {"ok": not missing and not malformed, "missing": missing, "malformed": malformed}
'''

FAILURE_INJECTOR = '''{
  "kind": "stall_then_timeout",
  "recovery_contract": {
    "checkpoint_resume": true,
    "generation_fencing": true,
    "heartbeat": true,
    "min_same_model_retries": 3,
    "route_receipt": true,
    "watchdog": true
  },
  "schema": "cortex.public.failure_injector.v1",
  "scope": "extended_task_dispatch",
  "trigger": {
    "on_attempt": 4
  }
}
'''

TASK_CONTRACT = '''{
  "description": "deterministic public recovery contract",
  "name": "LiteLLM long-running extended control-layer audit",
  "required_artifacts": [
    "artifacts/review.md",
    "artifacts/evidence.json",
    "artifacts/checks.txt"
  ],
  "schema": "cortex.public.task_contract.v1",
  "task_class": "extended-task-control-failure",
  "task_id": "loop-engineering-litellm-extended-task"
}
'''

TOOL_CONTRACT = '''{
  "mutation_boundary": "workspace-only",
  "required_controls": [
    "checkpoint_resume",
    "generation_fencing",
    "heartbeat",
    "watchdog",
    "route_receipt"
  ],
  "schema": "cortex.public.tool_contract.v1"
}
'''

MIGRATION_OBJECTIVE = '''from pathlib import Path

def check(root):
    root = Path(root)
    callers = [
        "cortex_v4/control/mechanical_migration.py",
        "cortex_v4/operation/controllers.py",
    ]
    existing = [p for p in callers if (root / p).is_file()]
    return {"ok": len(existing) == len(callers), "missing": [p for p in callers if p not in existing], "named_callers": existing}
'''

WIRE_OBJECTIVE = '''from pathlib import Path

def check(root):
    root = Path(root)
    caller = "cortex_v4/operation/controllers.py"
    ok = (root / caller).is_file()
    return {"ok": ok, "missing": [] if ok else [caller], "named_callers": [caller] if ok else []}
'''


def write(rel: str, content: str) -> None:
    path = FIXTURE / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def contract(methodology_ids, **extra) -> str:
    value = {"schema": "cortex.public.migration_contract.v1", "methodology_ids": methodology_ids}
    value.update(extra)
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def replace_link(path: Path, target: Path) -> None:
    if path.is_symlink() or path.exists():
        if path.is_symlink() or path.is_file():
            path.unlink()
        else:
            shutil.rmtree(path)
    path.symlink_to(target, target_is_directory=True)


def main() -> int:
    if os.name == "nt":
        raise SystemExit("contract fixture is hosted-CI-only; use the real SSC checkout locally")
    if FIXTURE.exists():
        shutil.rmtree(FIXTURE)
    (FIXTURE / "cortex_core").mkdir(parents=True)
    shutil.copyfile(ROOT / "contracts" / "ssc-fixture-runtime.py", FIXTURE / "cortex_core" / "__init__.py")

    manual = "\n".join(
        ["# Secretless SSC methodology contract", ""]
        + [f"## M{i}. Contract procedure {i}\nFixture behavioral contract for M{i}." for i in range(34)]
    ) + "\n"
    write("docs/methodology/WORK-METHODOLOGIES.md", manual)
    write("data/model_summon.json", '{"schema":"fixture","seats":["kimi","terra"]}\n')
    write("data/model_seating.json", '{"schema":"fixture","roles":["builder","executor"]}\n')
    write("corrected_model_dispatch.tsv", "seat\ttier\tmodel\nkimi\tlitellm-ckff\tkimi-k2.7-code\n")

    loop = "observations/loop-engineering/20260805-litellm/public"
    write(loop + "/objective-checker.py", EXTENDED_OBJECTIVE)
    write(loop + "/failure-injector.json", FAILURE_INJECTOR)
    write(loop + "/task-contract.json", TASK_CONTRACT)
    write(loop + "/tool-contract.json", TOOL_CONTRACT)
    write(
        "ops-local/loop-engineering/20260805-litellm/B-v4-replay/public-fixture/objective-checker.py",
        EXTENDED_OBJECTIVE,
    )

    write(
        "observations/loop-engineering/20260805-migration/public/migration-contract.json",
        contract(
            ["M0", "M1", "M3", "M30"],
            source_slice={
                "allowed_modules": [
                    "cortex_core.session_preflight",
                    "cortex_core.forced_rag_gate",
                    "cortex_core.methodology_receipt",
                ]
            },
        ),
    )
    write("observations/loop-engineering/20260805-migration/public/objective-checker.py", MIGRATION_OBJECTIVE)
    write("observations/loop-engineering/20260805-migration/public/tool-contract.json", TOOL_CONTRACT)

    for slug, ids in (
        ("20260806-dispatch-tools", ["M8", "M18", "M28", "M29"]),
        ("20260806-research-audit", ["M21", "M22", "M25", "M32", "M33"]),
        ("20260806-eval-learning", ["M4", "M5", "M9", "M12", "M16", "M17", "M19", "M20", "M24"]),
    ):
        base = f"observations/loop-engineering/{slug}/public"
        write(base + "/migration-contract.json", contract(ids))
        write(base + "/objective-checker.py", WIRE_OBJECTIVE)
        write(base + "/tool-contract.json", TOOL_CONTRACT)

    replace_link(SSC_COMPAT, FIXTURE)
    replace_link(V4_COMPAT, ROOT)
    print(json.dumps({
        "schema": MANIFEST["schema"],
        "fixture_root": str(FIXTURE),
        "ssc_compat": str(SSC_COMPAT),
        "v4_compat": str(V4_COMPAT),
        "private_source_copied": False,
        "hidden_holdout_copied": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
