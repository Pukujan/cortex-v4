from __future__ import annotations

from cortex_v4.operation.controllers import _NativeEval


def test_cohens_kappa_depends_on_labels_and_predictions():
    evaluator = _NativeEval()
    assert evaluator.cohens_kappa(["PASS", "FAIL"], ["PASS", "FAIL"], ["PASS", "FAIL"])["kappa"] == 1.0
    result = evaluator.cohens_kappa(["PASS", "PASS", "FAIL"], ["PASS", "FAIL", "FAIL"], ["PASS", "FAIL"])
    assert result["kappa"] < 1.0
    assert result["calibrated"] is False


def test_ndcg_depends_on_retrieval_order_and_relevance():
    evaluator = _NativeEval()
    assert evaluator.ndcg([1, 2, 3], [1, 2, 3], 3)["ndcg_at_k"] == 1.0
    result = evaluator.ndcg([3, 2, 9], [1, 2, 3], 3)
    assert 0.0 < result["ndcg_at_k"] < 1.0
    assert evaluator.ndcg([], [1], 3)["ndcg_at_k"] == 0.0
