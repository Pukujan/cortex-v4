"""cortex_v4.memory: independent V4 pointer-paged context/memory controller.

Stdlib-only interior. Host adapters may import this package; this package must
not import cortex_core or SSD-only modules.
"""
from .context_controller import (
    ContextController,
    ContextItem,
    WorkingContext,
    create_controller,
)
from .event_log import Event, EventLog, append_event, create_log, deep_freeze, read_events
from .folder import FoldResult, fold, fold_decision, fold_failure, fold_follow_up
from .hydrical import DictStore, FileResolver, Hydrator
from .pointers import (
    Pointer,
    ResolvedPointer,
    format_pointer,
    is_pointer,
    make_pointer,
    parse_pointer,
)
from .task_render import render as render_task
from .task_render import validate_task
from .task_state import TaskState, render_stable, update_task_state
from .handoff_artifact import build_handoff, validate_handoff

__all__ = [
    "ContextController",
    "ContextItem",
    "DictStore",
    "Event",
    "EventLog",
    "FileResolver",
    "FoldResult",
    "Hydrator",
    "Pointer",
    "ResolvedPointer",
    "TaskState",
    "WorkingContext",
    "append_event",
    "build_handoff",
    "create_controller",
    "create_log",
    "deep_freeze",
    "fold",
    "fold_decision",
    "fold_failure",
    "fold_follow_up",
    "format_pointer",
    "is_pointer",
    "make_pointer",
    "parse_pointer",
    "read_events",
    "render_stable",
    "render_task",
    "update_task_state",
    "validate_handoff",
    "validate_task",
]

__version__ = "0.1.0"
