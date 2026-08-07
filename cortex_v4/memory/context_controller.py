"""Bounded working-context controller with fail-closed protected spans (V4 independent)."""
from __future__ import annotations

from dataclasses import dataclass, field

from .event_log import EventLog
from .pointers import Pointer, format_pointer, make_pointer, parse_pointer
from .task_state import TaskState, render_stable


@dataclass(frozen=True)
class ContextItem:
    kind: str
    body: str
    pointer: Pointer | None = None
    protected: bool = False

    def char_len(self) -> int:
        return len(self.body)


@dataclass
class WorkingContext:
    max_chars: int
    items: list[ContextItem] = field(default_factory=list)
    offloaded: list[Pointer] = field(default_factory=list)
    task_state: TaskState | None = None

    def total_chars(self) -> int:
        n = sum(item.char_len() for item in self.items)
        if self.task_state is not None:
            n += len(render_stable(self.task_state))
        return n

    def over_budget(self) -> bool:
        return self.total_chars() > self.max_chars


class ContextController:
    def __init__(
        self,
        *,
        max_chars: int = 4000,
        event_log: EventLog | None = None,
        task_state: TaskState | None = None,
    ) -> None:
        if max_chars < 1:
            raise ValueError("max_chars must be >= 1")
        self._log = event_log if event_log is not None else EventLog()
        self._ctx = WorkingContext(max_chars=max_chars, task_state=task_state)
        self._offload_seq = 0

    @property
    def max_chars(self) -> int:
        return self._ctx.max_chars

    @property
    def event_log(self) -> EventLog:
        return self._log

    @property
    def task_state(self) -> TaskState | None:
        return self._ctx.task_state

    def set_task_state(self, state: TaskState) -> None:
        self._ctx.task_state = state
        self._log.append("task_state_set", {"goals": list(state.goals)})

    def add_text(self, text: str, *, protected: bool = False) -> ContextItem:
        if not isinstance(text, str):
            raise TypeError("text must be str")
        kind = "protected" if protected else "text"
        item = ContextItem(kind=kind, body=text, protected=protected)
        self._ctx.items.append(item)
        self._log.append(
            "context_add",
            {"kind": kind, "chars": len(text), "protected": protected},
        )
        return item

    def add_pointer(self, pointer: Pointer | str, *, label: str = "") -> ContextItem:
        if isinstance(pointer, str):
            pointer = parse_pointer(pointer)
        body = format_pointer(pointer)
        if label:
            body = f"{body} ({label})"
        item = ContextItem(kind="pointer", body=body, pointer=pointer, protected=False)
        self._ctx.items.append(item)
        self._log.append("context_add_pointer", {"pointer": str(pointer)})
        return item

    def protected_spans(self) -> list[str]:
        spans: list[str] = []
        if self._ctx.task_state is not None:
            spans.append(render_stable(self._ctx.task_state))
        for item in self._ctx.items:
            if item.protected and item.body:
                spans.append(item.body)
        return spans

    def compact(self) -> list[Pointer]:
        newly: list[Pointer] = []
        while self._ctx.over_budget():
            idx = self._find_offload_index()
            if idx is None:
                break
            item = self._ctx.items.pop(idx)
            self._offload_seq += 1
            ptr = make_pointer("offload", f"O{self._offload_seq:04d}", label=item.kind)
            self._log.append(
                "context_offload",
                {
                    "pointer": str(ptr),
                    "kind": item.kind,
                    "chars": item.char_len(),
                    "preview": item.body[:120],
                },
            )
            self._ctx.offloaded.append(ptr)
            self._ctx.items.insert(
                idx,
                ContextItem(kind="pointer", body=str(ptr), pointer=ptr, protected=False),
            )
            newly.append(ptr)
        rendered = self.render()
        for span in self.protected_spans():
            if span and span not in rendered:
                raise RuntimeError(f"compaction dropped protected span: {span[:80]!r}")
        return newly

    def _find_offload_index(self) -> int | None:
        for i, item in enumerate(self._ctx.items):
            if (not item.protected) and item.kind != "pointer":
                return i
        return None

    def render(self) -> str:
        parts: list[str] = []
        if self._ctx.task_state is not None:
            parts.append(render_stable(self._ctx.task_state))
        for item in self._ctx.items:
            parts.append(item.body)
        return "\n\n".join(parts)

    def offload_list(self) -> tuple[Pointer, ...]:
        return tuple(self._ctx.offloaded)

    def items(self) -> tuple[ContextItem, ...]:
        return tuple(self._ctx.items)

    def preservation_ok(self) -> tuple[bool, list[str]]:
        rendered = self.render()
        missing = [s for s in self.protected_spans() if s and s not in rendered]
        return (not missing, missing)


def create_controller(
    *,
    max_chars: int = 4000,
    event_log: EventLog | None = None,
    task_state: TaskState | None = None,
) -> ContextController:
    return ContextController(
        max_chars=max_chars, event_log=event_log, task_state=task_state
    )
