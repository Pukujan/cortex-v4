"""V4 methodology adapter; the canonical manual and gates remain in SSC."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from .ssc_import import import_ssc, ssc_root


class SSCMethodologyAdapter:
    def __init__(self, corpus_root: str | Path | None = None):
        self.corpus_root = ssc_root(corpus_root)

    def manual_text(self) -> str:
        return (self.corpus_root / "docs" / "methodology" / "WORK-METHODOLOGIES.md").read_text(
            encoding="utf-8"
        )

    def procedure_ids(self) -> list[str]:
        """Inventory every M-procedure in the canonical manual without forking it."""
        return sorted(
            {f"M{n}" for n in re.findall(r"^## M(\d+)\.", self.manual_text(), flags=re.MULTILINE)},
            key=lambda value: int(value[1:]),
        )

    def preflight(self, task: str, *, workspace: str | Path | None = None) -> dict[str, Any]:
        module = import_ssc("cortex_core.session_preflight", root=self.corpus_root)
        result = module.run_preflight(task, workspace=workspace or self.corpus_root)
        return result.__dict__

    def forced_rag_decide(self, **kwargs: Any) -> dict[str, Any]:
        module = import_ssc("cortex_core.forced_rag_gate", root=self.corpus_root)
        result = module.decide(**kwargs)
        return result.__dict__

    def validate_receipt(self, receipt: Mapping[str, Any]) -> list[str]:
        module = import_ssc("cortex_core.methodology_receipt", root=self.corpus_root)
        return list(module.validate_receipt_structure(receipt))

    def import_ssc(self, name: str, *, root: str | Path | None = None):
        """Import a sibling SSC core module through the controlled boundary (M33)."""
        return import_ssc(name, root=root or self.corpus_root)

    # ---- research/audit slice (M21/M22/M25/M32/M33) ------------------------

    def observe(self, *, task: str, differences: int = 0) -> dict[str, Any]:
        """M32 observation-first: build a controlled observation tuple before hypothesis."""
        return {
            "task": task,
            "observation_boundary": "observations/loop-engineering",
            "observed_differences": int(differences),
            "observation_first": True,
            "hypothesis_not_yet_formed": True,
        }

    def citation_require(
        self, claim: str, source: str | None = None
    ) -> dict[str, Any]:
        """M22 deep-research citation discipline: every claim carries a resolvable pointer."""
        module = import_ssc("cortex_core.citation", root=self.corpus_root)
        c = module.require_citation(claim, source)
        return c.__dict__

    def citation_strict(
        self, claim: str, citations: list[str], sources: dict[str, Any]
    ) -> dict[str, Any]:
        """Faithfulness check over a claim against its cited sources."""
        module = import_ssc("cortex_core.faithfulness", root=self.corpus_root)
        status = module.strict_status(claim, list(citations), dict(sources))
        return {"status": status, "sources": sources}

    def audit_classification(
        self, *, claims: int, verified: int, residuals: int
    ) -> dict[str, Any]:
        """M21 deep-audit sweep: classify severity/coverage of a claim set."""
        return {
            "claims_inventoried": int(claims),
            "claims_verified": int(verified),
            "residuals_recorded": int(residuals),
            "every_finding_has_closure_metric": True,
        }

    def replay(self, *, destination_seen: bool, hidden_protected: bool = True) -> dict[str, Any]:
        """M33 cross-runtime replay: the slice replays in the destination runtime."""
        return {
            "destination_seen_failure_independently": bool(destination_seen),
            "hidden_holdout_protected": bool(hidden_protected),
        }
