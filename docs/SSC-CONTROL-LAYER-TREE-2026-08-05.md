# SSC control-layer tree

Observed 2026-08-05 from `D:\\claude\\stupidly-simple-cortex`.

> **Historical inventory.** SSC is retired from normal V4 runtime, corpus, routing,
> observability, and merge authority. This file is source inventory evidence only; it is not a
> current dependency. See `docs/SSC_RETIREMENT_2026-08-12.md`.

This is the agent-control slice of SSC, not the full 201-file `cortex_core` inventory. SSC
does not have a separate `cortex_tools/` package. The tool doorway and execution layer live
inside `cortex_core`.

```text
SSC/
├── AGENTS.md                              owner/project operating contract
├── HANDOFF.md                             current human handoff and authority boundary
├── corrected_model_dispatch.tsv           closed model-seat policy
├── data/
│   └── model_summon.json                  durable summon-seat definitions
├── docs/
│   ├── harness/
│   │   ├── START-HERE.md                  session preflight and search order
│   │   ├── CAPABILITY-STATUS.md           runtime capability status
│   │   └── ...                            harness contracts and evidence rules
│   ├── methodology/
│   │   ├── WORK-METHODOLOGIES.md          executable M-procedures
│   │   └── HYPOTHESIS-LANES-AND-ROLE-SEPARATION-2026-07-29.md
│   ├── style/                             owner-legible output rules
│   └── design/                            migration contracts and slice maps
└── cortex_core/
    ├── entry and doorway
    │   ├── __main__.py
    │   ├── mcp.py                         MCP tools and dispatch surface
    │   ├── mcp_door.py                    MCP doorway/lifecycle
    │   ├── http_server.py
    │   ├── websocket_server.py
    │   ├── runtime_server.py
    │   ├── config.py                       workspace and read/write plane resolution
    │   ├── onboarding.py
    │   └── plugin.py
    ├── methodology and gates
    │   ├── session_preflight.py            SEARCH_BRAIN / evidence pack
    │   ├── forced_rag_gate.py              grounding boundary
    │   ├── methodology_receipt.py          procedure receipt
    │   ├── work_unit_freeze.py             frozen work-unit contract
    │   ├── build_routing_gate.py           build-route policy
    │   ├── driver_preflight.py             driver activation checks
    │   ├── app_gates.py                    deterministic application gates
    │   ├── gate_state.py
    │   ├── consent_gate.py
    │   ├── llm_complete_gate.py            single-shot call guard
    │   ├── write_policy.py                 write boundary
    │   ├── contract.py
    │   ├── app_contract.py
    │   ├── govern.py
    │   ├── authz.py
    │   ├── doctor.py
    │   ├── wiring.py
    │   └── workspace_sweep.py
    ├── model summon and seating
    │   ├── model_summon.py                 agentic summon loop and seat resolution
    │   ├── model_dispatch.py               provider/model transport
    │   ├── model_routes.py                 route data and resolution
    │   ├── model_tiers.py                  tier vocabulary
    │   ├── model_catalog.py                catalog data
    │   ├── seating.py                      seat selection and independence
    │   ├── fleet_dispatch.py               governed fleet dispatch
    │   ├── fanout.py                       parallel worker fan-out
    │   ├── concurrency.py                  concurrency caps
    │   ├── agent_resilience.py              retry/checkpoint behavior
    │   ├── agent_runner.py                 worker execution
    │   ├── agent_runtime.py                agent tool-calling loop
    │   ├── phase_runtime.py                phase execution
    │   ├── long_job.py                     long-running work
    │   └── orchestrator.py
    ├── tool layer
    │   ├── tool_surface.py                 available-tool inventory
    │   ├── tool_output_rtk.py              bounded tool-output handling
    │   ├── llm_parse.py                    model response parsing
    │   ├── capability_router.py            capability-aware route planning
    │   ├── bridge_client.py
    │   ├── cdp_bridge.py
    │   └── integrations.py
    ├── state and work tracking
    │   ├── state_engine.py                 phase/state machine
    │   ├── project_state.py                event reducer
    │   ├── project_state_store.py          durable event store
    │   ├── project_state_projection.py     generated projections
    │   ├── project_state_ontology.py       ontology reconciliation
    │   ├── project_state_cli.py
    │   ├── task_ledger.py
    │   ├── task_registry.py
    │   ├── mission_driver.py
    │   ├── receipts.py
    │   ├── methodology_receipt.py
    │   ├── audit.py
    │   ├── handoff.py
    │   ├── closeout_reconcile.py
    │   └── provenance.py
    ├── RAG boundary used by the control layer
    │   ├── knowledge.py                    composite Brain/tenant/KEDB recall
    │   ├── search.py                       corpus search
    │   ├── search_router.py
    │   ├── registry.py
    │   ├── ontology.py
    │   ├── freshness.py
    │   ├── retrieval_health.py
    │   └── vector.py
    └── telemetry and observation
        ├── trace_capture.py                run/task/route trace records
        ├── trace_redaction.py
        ├── otel.py                         OpenTelemetry spans
        ├── langfuse_sink.py                Langfuse export batches
        ├── telemetry.py
        ├── metrics.py
        ├── observability_dashboard.py
        ├── dashboard.py
        ├── output_contract.py
        └── transcript.py
```

## Practical control path

```text
AGENTS/HANDOFF
    ↓
START-HERE + WORK-METHODOLOGIES
    ↓
session_preflight → forced_rag_gate → methodology_receipt
    ↓
state/work-unit gates → seating/model_summon → agent_runtime/tool_surface
    ↓
knowledge/search read boundary → model/tool work
    ↓
receipts/state/audit/closeout + OTel/Langfuse observation
```

This tree is an extraction map for V4 slices. It is not approval to copy the whole package.
The current V4 boundary remains: keep SSC as the working RAG corpus and migrate only proven
runtime-control slices with A/B/C checks.
