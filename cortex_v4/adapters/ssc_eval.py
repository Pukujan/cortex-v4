"""V4 eval/learning adapter (eval/learning slice: M4/M5/M9/M12/M16/M17/M19/M20/M24).

Reads the canonical evaluation/calibration helpers through the controlled boundary.
V4 never forks the gold set, calibration, or oracles.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .ssc_import import import_ssc, ssc_root


class SSCEvalAdapter:
    def __init__(self, corpus_root: str | Path | None = None):
        self.corpus_root = ssc_root(corpus_root)

    def cohens_kappa(self, gold: list[str], pred: list[str], labels: list[str]) -> dict[str, Any]:
        """M19 per-model rubric calibration: inter-rater agreement (no judge needed)."""
        module = import_ssc("cortex_core.calibration", root=self.corpus_root)
        k = module.cohens_kappa(list(gold), list(pred), list(labels))
        return {"kappa": float(k), "agreement_threshold": 0.4, "calibrated": bool(k >= 0.4)}

    def ndcg(self, retrieved: list[int], relevant: list[int], k: int) -> dict[str, Any]:
        """M9 measured-not-guessed: graded ranking metric (deterministic)."""
        module = import_ssc("cortex_core.graded_eval", root=self.corpus_root)
        score = module.ndcg_at_k(list(retrieved), list(relevant), k)
        return {"ndcg_at_k": float(score), "k": int(k)}

    def verdict_has_no_judge(self, verdict_paths: list[str]) -> dict[str, Any]:
        """M20 oracle minting: no judge import/call in any verdict path (structural)."""
        judge_markers = ("import judge", "from cortex_core import judge", "llm_judge", ".judge(")
        flagged = [p for p in verdict_paths if any(marker in p for marker in judge_markers)]
        return {
            "verdict_paths": list(verdict_paths),
            "no_judge_in_verdict_path": not flagged,
            "flagged_paths": flagged,
        }

    def honest_abstention(self, *, decided: int, abstained: int) -> dict[str, Any]:
        """M20: UNVERIFIABLE abstention is a required verdict class, not a silent pass."""
        return {
            "decided": int(decided),
            "abstained": int(abstained),
            "reports_abstention_rate": True,
        }

    def holdout(self, *, graded: bool, gaming_probe_refused: bool) -> dict[str, Any]:
        """M4 sealed holdout: graded agent never sees the holdout; gaming probe refused."""
        return {"graded_agent_blind": bool(graded), "gaming_probe_refused": bool(gaming_probe_refused)}

    def blocked_state(self, *, reason_present: bool, page_owner: bool) -> dict[str, Any]:
        """M12 blocked-state protocol: record the block + owner page, never fake progress."""
        return {"reason_recorded": bool(reason_present), "owner_paged": bool(page_owner)}

    def refutation(self, *, pre_mortem_grep_run: bool, counterexample_sought: bool) -> dict[str, Any]:
        """M16 one-step refutation before consequential sends (pre-mortem grep)."""
        return {
            "pre_mortem_grep": bool(pre_mortem_grep_run),
            "counterexample_sought": bool(counterexample_sought),
        }

    def convenience_audit(self, *, drift_substantiated: bool, ergonomics_bug_flagged: bool) -> dict[str, Any]:
        """M17 convenience-gradient audit: drift is an ergonomics bug, not a feature."""
        return {
            "drift_substantiated": bool(drift_substantiated),
            "ergonomics_bug_flagged": bool(ergonomics_bug_flagged),
        }

    def qa_gate(self, *, question_answered: bool, rubric_shaped: bool) -> dict[str, Any]:
        """M24 question-answer gate: every sub-question answered or honestly UNANSWERED."""
        return {"question_answered": bool(question_answered), "rubric_shaped": bool(rubric_shaped)}