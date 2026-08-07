"""V4 boundary for the proven SSC seat and tool-call controls.

This adapter deliberately does not copy the summon table or runtime implementation.  V4
asks SSC for the current seat resolution and tool policy at runtime, so the corpus and
owner-controlled dispatch table remain the source of truth during migration.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .ssc_import import import_ssc, ssc_root


class SSCSummonAdapter:
    def __init__(self, corpus_root: str | Path | None = None):
        self.corpus_root = ssc_root(corpus_root)

    def seats(self) -> list[str]:
        module = import_ssc("cortex_core.model_summon", root=self.corpus_root)
        return list(module.list_seats())

    def resolve(self, seat: str) -> dict[str, Any]:
        module = import_ssc("cortex_core.model_summon", root=self.corpus_root)
        return module.resolve_summon(seat).__dict__.copy()

    def dispatch_chain(self, seat: str) -> list[tuple[str, str | None]]:
        module = import_ssc("cortex_core.model_summon", root=self.corpus_root)
        return list(module.seat_dispatch_chain(seat))

    def tool_names(self) -> list[str]:
        module = import_ssc("cortex_core.agent_runtime", root=self.corpus_root)
        return sorted(module.TOOLS)

    def mutating_tool_names(self) -> list[str]:
        module = import_ssc("cortex_core.agent_runtime", root=self.corpus_root)
        return sorted(module.MUTATING_TOOLS)

    def mutation_decision(
        self,
        tool: str,
        args: Mapping[str, Any] | None = None,
        *,
        allow_hard_mutations: bool = True,
    ) -> dict[str, Any]:
        module = import_ssc("cortex_core.agent_runtime", root=self.corpus_root)
        result = module.mutation_gate(
            tool,
            args or {},
            allow_hard_mutations=allow_hard_mutations,
        )
        return result.__dict__.copy()

