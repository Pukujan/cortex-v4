"""Append-only eventual event log with deep freeze (V4 independent)."""
from __future__ import annotations

import time
import types
from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class Event:
    kind: str
    seq: int
    ts: float
    payload: Any = field(default_factory=tuple)

    def __str__(self) -> str:
        return f"[{self.seq}] {self.kind} {self.payload!r}"


def deep_freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return types.MappingProxyType({k: deep_freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(deep_freeze(v) for v in value)
    return value


class EventLog:
    def __init__(self, *, start_seq: int = 1) -> None:
        self._events: list[Event] = []
        self._next_seq = int(start_seq)

    def append(self, kind: str, payload: Any = None, *, ts: float | None = None) -> Event:
        if not kind or not str(kind).strip():
            raise ValueError("event kind must not be empty")
        event = Event(
            kind=kind,
            seq=self._next_seq,
            ts=time.time() if ts is None else float(ts),
            payload=deep_freeze(payload),
        )
        self._events.append(event)
        self._next_seq += 1
        return event

    def read_events(self, *, after_seq: int = 0) -> tuple[Event, ...]:
        return tuple(e for e in self._events if e.seq > after_seq)

    def __len__(self) -> int:
        return len(self._events)

    def __iter__(self):
        return iter(self._events)

    @property
    def last_seq(self) -> int:
        return self._events[-1].seq if self._events else 0


def create_log(*, start_seq: int = 1) -> EventLog:
    return EventLog(start_seq=start_seq)


def append_event(log: EventLog, event: Mapping[str, Any], **kwargs: Any) -> Event:
    if not isinstance(log, EventLog):
        raise TypeError("log must be an EventLog")
    kind = event.get("kind")
    payload = event.get("payload") if "payload" in event else None
    ts = kwargs.get("ts", event.get("ts") if isinstance(event, dict) else None)
    return log.append(kind, payload, ts=ts)


def read_events(log: EventLog, after_seq: int = 0) -> tuple[Event, ...]:
    return log.read_events(after_seq=after_seq)
