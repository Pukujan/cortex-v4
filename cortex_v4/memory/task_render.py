"""Deterministic task -> prompt render; task > prompt > context (V4 independent)."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

REQUIRED_TASK_KEYS = (
    "task_id",
    "goals",
    "constraints",
    "acceptance_criteria",
    "methodology_ids",
)


def validate_task(task: Mapping[str, Any]) -> None:
    if not isinstance(task, Mapping):
        raise TypeError("task must be a mapping")
    keys = set(task.keys())
    if keys == {"prompt"} or (keys <= {"prompt", "text", "message"}):
        raise ValueError(
            "prose-substitution refused: task must be structured, not prompt-only"
        )
    missing = [k for k in REQUIRED_TASK_KEYS if k not in task]
    if missing:
        raise ValueError(f"task missing required keys: {missing}")
    if not str(task["task_id"]).strip():
        raise ValueError("task_id must not be empty")
    for field in ("goals", "acceptance_criteria", "methodology_ids"):
        val = task[field]
        if not isinstance(val, (list, tuple)) or not val:
            raise ValueError(f"task.{field} must be a non-empty list")


def render(task: Mapping[str, Any]) -> str:
    validate_task(task)

    def bullets(title: str, items: Sequence[Any]) -> list[str]:
        lines = [f"## {title}"]
        for item in items:
            lines.append(f"- {item}")
        return lines

    lines = [
        f"# Task {task['task_id']}",
        "",
        *bullets("Goals", list(task["goals"])),
        "",
        *bullets("Constraints", list(task.get("constraints") or [])),
        "",
        *bullets("Acceptance criteria", list(task["acceptance_criteria"])),
        "",
        *bullets("Methodology", list(task["methodology_ids"])),
    ]
    if task.get("corpus_refs"):
        lines.extend(["", *bullets("Corpus refs", list(task["corpus_refs"]))])
    if task.get("hidden_holdout_boundary"):
        lines.extend(
            [
                "",
                "## Hidden holdout boundary",
                str(task["hidden_holdout_boundary"]),
            ]
        )
    if task.get("tool_contract_ref"):
        lines.extend(["", f"## Tool contract\n{task['tool_contract_ref']}"])
    return "\n".join(lines)
