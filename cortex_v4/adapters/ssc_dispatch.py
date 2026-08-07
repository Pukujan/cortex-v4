"""V4 model-dispatch adapter (dispatch/tools slice: M8/M18/M28/M29).

Reads the owner's closed dispatch table and seating policy through the controlled
boundary. The canonical tables and gates remain in SSC; V4 never forks them.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .ssc_import import import_ssc, ssc_root


class SSCDispatchAdapter:
    def __init__(self, corpus_root: str | Path | None = None):
        self.corpus_root = ssc_root(corpus_root)

    def summon_table_path(self) -> Path:
        return self.corpus_root / "data" / "model_summon.json"

    def seating_policy_path(self) -> Path:
        return self.corpus_root / "data" / "model_seating.json"

    def dispatch_tsv_path(self) -> Path:
        return self.corpus_root / "corrected_model_dispatch.tsv"

    def resolve_summon(self, seat: str) -> dict[str, Any]:
        """Resolve a seat against the closed summon table (M8)."""
        module = import_ssc("cortex_core.model_summon", root=self.corpus_root)
        res = module.resolve_summon(seat, path=self.summon_table_path())
        return {
            "seat": getattr(res, "seat", seat),
            "tier": getattr(res, "tier", None),
            "model_override": getattr(res, "model_override", None),
            "status": getattr(res, "notes", ""),
            "timeout_s": getattr(res, "timeout_s", None),
        }

    def list_seats(self) -> list[dict[str, Any]]:
        module = import_ssc("cortex_core.model_summon", root=self.corpus_root)
        return module.list_seats(path=self.summon_table_path())

    def selected_rank(self, role: str) -> dict[str, Any]:
        """Ranked seating from the owner policy (M8a)."""
        module = import_ssc("cortex_core.model_seating", root=self.corpus_root)
        candidates = module.candidates(role=role, path=self.seating_policy_path())
        available = {c.model for c in candidates}
        res = module.select_ranked(
            role, available_models=available, path=self.seating_policy_path()
        )
        return {
            "role": role,
            "model": res.model,
            "vendor": res.vendor,
            "tier": getattr(res, "tier", None),
            "rank": getattr(res, "rank", None),
        }

    def seat_matrix(self, *, seat: str, box: str, never_seen: list[str], forced_rag: bool) -> dict[str, Any]:
        """M29 seat access-control matrix: box type + forced-RAG assignment."""
        return {
            "seat": seat,
            "box": box,
            "never_seen": list(never_seen),
            "forced_rag": bool(forced_rag),
        }

    def fan_out(self, *, theories: list[dict[str, Any]]) -> dict[str, Any]:
        """M28 parallel hypothesis fan-out: distinct + falsifying + innocent-suspect."""
        distinct = {t.get("id") for t in theories}
        falsifying = all(t.get("falsifying_test") for t in theories)
        return {
            "theory_count": len(theories),
            "distinct_ids": len(distinct),
            "all_falsifying_tests": bool(falsifying),
        }

    def metabolism(self, *, error_class: str, mechanism: bool) -> dict[str, Any]:
        """M18 error metabolism: every caught error class becomes a mechanism same day."""
        return {
            "error_class": error_class,
            "becomes_mechanism_same_day": bool(mechanism),
        }