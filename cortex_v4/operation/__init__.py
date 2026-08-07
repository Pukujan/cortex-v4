"""Thin MVC-style composition for the first V4 operation."""
from __future__ import annotations

from .controllers import (
    run_dispatch_tool_chain,
    run_eval_learning_chain,
    run_fixture_operation,
    run_methodology_origin_chain,
    run_research_audit_chain,
)

__all__ = [
    "run_dispatch_tool_chain",
    "run_eval_learning_chain",
    "run_fixture_operation",
    "run_methodology_origin_chain",
    "run_research_audit_chain",
]