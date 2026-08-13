from __future__ import annotations

import unittest

from cortex_v4.routing.catalog_router import RoutingPolicy, RoutingRequest, select_models


CATALOG = [
    {
        "id": "cheap-qwen",
        "family": "qwen",
        "available": True,
        "capabilities": {"tool_calling": True, "coding": True},
        "cost_rank": 1,
        "quality_rank": 5,
        "latency_rank": 2,
        "observed": {"success_rate": 0.95, "longest_success_s": 180},
    },
    {
        "id": "cheap-grok",
        "family": "grok",
        "available": True,
        "capabilities": {"tool_calling": True, "coding": True},
        "cost_rank": 2,
        "quality_rank": 3,
        "latency_rank": 2,
        "observed": {"success_rate": 0.99, "longest_success_s": 600},
    },
    {
        "id": "expensive-sol",
        "family": "openai",
        "available": True,
        "capabilities": {"tool_calling": True, "coding": True},
        "cost_rank": 20,
        "quality_rank": 1,
        "latency_rank": 8,
        "observed": {"success_rate": 0.98, "longest_success_s": 600},
    },
]


class CatalogRouterTests(unittest.TestCase):
    def test_no_model_is_a_hard_coded_default(self):
        selected = select_models(CATALOG, RoutingRequest(role="builder"))
        self.assertEqual(selected[0].model_id, "cheap-grok")
        changed = [dict(row) for row in CATALOG]
        changed[0]["observed"] = {"success_rate": 1.0, "longest_success_s": 180}
        selected2 = select_models(changed, RoutingRequest(role="builder"))
        self.assertEqual(selected2[0].model_id, "cheap-qwen")

    def test_cross_family_seating_comes_from_catalog(self):
        selected = select_models(
            CATALOG,
            RoutingRequest(role="triage", seats=2, require_cross_family=True),
        )
        self.assertEqual(len(selected), 2)
        self.assertNotEqual(selected[0].family, selected[1].family)

    def test_cost_ceiling_is_owned_by_v4_policy(self):
        selected = select_models(
            CATALOG,
            RoutingRequest(role="builder"),
            RoutingPolicy(max_cost_rank=2, min_reliability=0.9),
        )
        self.assertTrue(selected)
        self.assertTrue(all(row.cost_rank <= 2 for row in selected))

    def test_long_session_does_not_confuse_task_lifetime_with_one_request(self):
        selected = select_models(
            CATALOG,
            RoutingRequest(role="builder", desired_task_seconds=None),
        )
        self.assertTrue(selected)
        strict_turn = select_models(
            CATALOG,
            RoutingRequest(role="builder", desired_task_seconds=500),
        )
        self.assertTrue(all((row.observed_longest_success_s or 0) >= 500 for row in strict_turn))


if __name__ == "__main__":
    unittest.main()
