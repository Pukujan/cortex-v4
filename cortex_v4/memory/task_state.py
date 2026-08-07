"""Stable task state that never gets compacted (V4 independent)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


def _as_tuple(values: Iterable[str] | None) -> tuple[str, ...]:
    return tuple(str(v).strip() for v in (values or ()) if str(v).strip())


@dataclass(frozen=True)
class TaskState:
    goals: tuple[str, ...] = field(default_factory=tuple)
    constraints: tuple[str, ...] = field(default_factory=tuple)
    accepted_decisions: tuple[str, ...] = field(default_factory=tuple)
    unresolved_questions: tuple[str, ...] = field(default_factory=tuple)
    completion_criteria: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def create(
        cls,
        goals: Iterable[str] | None = None,
        constraints: Iterable[str] | None = None,
        accepted_decisions: Iterable[str] | None = None,
        unresolved_questions: Iterable[str] | None = None,
        completion_criteria: Iterable[str] | None = None,
    ) -> "TaskState":
        if not goals:
            raise ValueError("stable task state requires at least one goal")
        return cls(
            goals=_as_tuple(goals),
            constraints=_as_tuple(constraints),
            accepted_decisions=_as_tuple(accepted_decisions),
            unresolved_questions=_as_tuple(unresolved_questions),
            completion_criteria=_as_tuple(completion_criteria),
        )


def update_task_state(
    state: TaskState,
    *,
    goals: Iterable[str] | None = None,
    constraints: Iterable[str] | None = None,
    accepted_decisions: Iterable[str] | None = None,
    unresolved_questions: Iterable[str] | None = None,
    completion_criteria: Iterable[str] | None = None,
    add_goal: Iterable[str] | None = None,
) -> TaskState:
    if goals is not None:
        new_goals = _as_tuple(goals)
        if not new_goals:
            raise ValueError("required field 'goals' must not be emptied")
    elif add_goal is not None:
        seen = set(state.goals)
        extended = list(state.goals)
        for g in _as_tuple(add_goal):
            if g not in seen:
                extended.append(g)
                seen.add(g)
        new_goals = tuple(extended)
    else:
        new_goals = state.goals

    def pick(current: tuple[str, ...], replacement: Iterable[str] | None) -> tuple[str, ...]:
        return current if replacement is None else _as_tuple(replacement)

    return TaskState(
        goals=new_goals,
        constraints=pick(state.constraints, constraints),
        accepted_decisions=pick(state.accepted_decisions, accepted_decisions),
        unresolved_questions=pick(state.unresolved_questions, unresolved_questions),
        completion_criteria=pick(state.completion_criteria, completion_criteria),
    )


def render_stable(state: TaskState) -> str:
    lines = ["## Stable task state (never compacted)"]

    def block(title: str, items: tuple[str, ...]) -> None:
        lines.append(f"### {title}")
        if not items:
            lines.append("(none)")
        for item in items:
            lines.append(f"- {item}")

    block("Goals", state.goals)
    block("Constraints", state.constraints)
    block("Accepted decisions", state.accepted_decisions)
    block("Unresolved questions", state.unresolved_questions)
    block("Completion criteria", state.completion_criteria)
    return "\n".join(lines)
