# SSC `cortex_core` source inventory

**Observed:** 2026-08-05

The source tree is a flat legacy package with **201 Python source files**. The physical directory
also contains **402 generated `.pyc` files** and one HTML artifact; those are not V4 source. At
the repository level, Git currently reports **190 untracked paths** plus six modified/submodule
entries. It is not a single runtime control layer. It contains the live control path, RAG/corpus
machinery, model fleet, evaluation system, state machinery, adapters, and many experiments.

The full measured migration classification is in SSC's
[`CORTEX-CORE-MIGRATION-SLOTS.md`](D:/claude/stupidly-simple-cortex/docs/design/CORTEX-CORE-MIGRATION-SLOTS.md).
That classification is an inventory, not approval to move the modules.

```text
cortex_core/
├── control and entry points
│   ├── __main__.py                     package entry point
│   ├── mcp.py                          MCP surface and tool dispatch
│   ├── mcp_door.py                     MCP doorway
│   ├── http_server.py                  HTTP entry point
│   ├── websocket_server.py             WebSocket entry point
│   ├── config.py                       workspace and plane resolution
│   ├── onboarding.py                   onboarding/status vocabulary
│   └── plugin.py                       plugin integration
├── methodology and gates
│   ├── session_preflight.py            SEARCH_BRAIN / evidence pack
│   ├── forced_rag_gate.py              forced-RAG boundary
│   ├── methodology_receipt.py          methodology receipts
│   ├── work_unit_freeze.py             frozen work-unit contracts
│   ├── build_routing_gate.py           build-route policy
│   ├── driver_preflight.py             driver activation checks
│   ├── app_gates.py                    deterministic application gates
│   ├── gate_state.py                   gate state
│   ├── consent_gate.py                 consent boundary
│   ├── llm_complete_gate.py            single-shot model-call gate
│   ├── doctor.py                       diagnostics
│   ├── wiring.py                       wiring checks
│   └── workspace_sweep.py              workspace checks
├── model summon and tool execution
│   ├── model_summon.py                 agentic summon loop and seat resolution
│   ├── model_dispatch.py               provider/model call transport
│   ├── model_routes.py                 route data
│   ├── model_tiers.py                  tier vocabulary
│   ├── model_catalog.py                model catalog
│   ├── agent_runtime.py                tool-calling runtime
│   ├── agent_runner.py                 worker execution
│   ├── tool_surface.py                 tool inventory
│   ├── tool_output_rtk.py              output compression
│   ├── llm_parse.py                    model response parsing
│   ├── seating.py                      seat selection and independence
│   ├── fleet_dispatch.py               fleet dispatch
│   ├── fanout.py                        parallel worker fan-out
│   ├── concurrency.py                  concurrency limits
│   ├── agent_resilience.py             retry/checkpoint behavior
│   ├── long_job.py                     long-running work
│   ├── phase_runtime.py                phase execution
│   └── orchestrator.py                 orchestration helpers
├── RAG and corpus
│   ├── search.py                       FTS/hybrid corpus search
│   ├── search_router.py                search routing
│   ├── knowledge.py                    Brain/tenant/KEDB composite recall
│   ├── research.py                     research orchestration
│   ├── research_agent.py               tool-loop research
│   ├── research_external_leg.py        external research leg
│   ├── research_prompts.py             research prompts
│   ├── fetch.py                        bounded external fetch
│   ├── browser_fetch.py                browser-backed fetch
│   ├── ingest.py                       corpus ingest
│   ├── corpus_migrate.py               corpus migration utility
│   ├── corpus_integrity_phase0.py      corpus integrity checks
│   ├── registry.py                     source/artifact registry
│   ├── patterns.py                     known-error patterns
│   ├── ontology.py                     living ontology
│   ├── ontology_seed.py                ontology seeding
│   ├── vector.py                       vector retrieval
│   ├── freshness.py                    freshness and fact validity
│   └── retrieval_health.py              retrieval health
├── state, memory, and evidence
│   ├── state_engine.py                 phase/state machine
│   ├── project_state.py                event reducer
│   ├── project_state_store.py          durable state store
│   ├── project_state_projection.py     state projections
│   ├── project_state_ontology.py       state-to-ontology bridge
│   ├── project_state_cli.py            state CLI
│   ├── memory.py                       memory records
│   ├── memdir.py                       memory directory helpers
│   ├── task_ledger.py                  task ledger
│   ├── task_registry.py                task registry
│   ├── write_policy.py                 write policy
│   ├── receipts.py                     receipts
│   ├── evidence_schema.py              evidence bundle schema
│   ├── results_ledger.py               measured results
│   ├── closeout_reconcile.py           closeout reconciliation
│   ├── audit.py                        audit and closeout records
│   ├── handoff.py                      handoff records
│   └── provenance.py                   provenance helpers
├── telemetry and observation
│   ├── trace_capture.py                trace records and sink fan-out
│   ├── otel.py                         OpenTelemetry spans
│   ├── langfuse_sink.py                Langfuse batches
│   ├── telemetry.py                    telemetry helpers
│   ├── observability_dashboard.py      dynamic observation dashboard
│   ├── dashboard.py                    dashboard helpers
│   ├── metrics.py                      metrics
│   ├── output_contract.py              output-contract records
│   ├── trace_redaction.py              trace redaction
│   └── transcript.py                   transcript records
├── evaluation and arbitration
│   ├── eval.py                         evaluation entry point
│   ├── eval_harvest.py                 evaluation harvesting
│   ├── evaluator.py                    evidence-based evaluator
│   ├── assurance_evaluator.py          assurance-result evaluation
│   ├── graded_eval.py                  graded evaluation
│   ├── oracle_crossval.py              oracle cross-validation
│   ├── oracle_verdict.py               oracle verdicts
│   ├── oracle_report.py                oracle reports
│   ├── judge.py                        judge helpers
│   ├── arbitrate.py                    cross-family arbitration
│   ├── arbitration_rigor.py            arbitration metrics
│   ├── rubric_gate.py                  rubric gate
│   ├── faithfulness.py                 grounding/faithfulness checks
│   ├── task_grade.py                   task grading
│   ├── scorecard.py                    scorecard types
│   ├── scorecards.py                   scorecard storage
│   ├── calibration.py                 model/judge calibration
│   ├── case_authorship.py              case provenance
│   ├── assurance_contracts.py          assurance contracts
│   ├── assurance_result.py             assurance result schema
│   └── attestation.py                  attestation records
├── provider and external adapters
│   ├── keys.py                         API key issuance
│   ├── keys_cli.py                     key CLI
│   ├── keys_dashboard.py               key dashboard
│   ├── model_driver.py                 model driver adapter
│   ├── model_probe.py                  model liveness probe
│   ├── provider_route_automation.py    provider route automation
│   ├── cli_lane.py                     CLI provider lane
│   ├── bridge_client.py                bridge client
│   ├── cdp_bridge.py                   browser/CDP bridge
│   ├── websocket_server.py             WebSocket transport
│   ├── runtime_server.py               runtime server
│   ├── integrations.py                 integration helpers
│   ├── ocr_tts.py                      OCR/TTS ingest
│   ├── vision_ingest.py                vision ingest
│   └── update.py                       update helper
├── contracts and policy
│   ├── app_contract.py                 shared contract vocabulary
│   ├── contract.py                     contract checking
│   ├── govern.py                       governance decision helper
│   ├── authz.py                        authorization
│   ├── capability_router.py            capability route planning
│   ├── promotion.py                    promotion policy
│   ├── promotion_state.py              promotion state
│   ├── provenance_tiers.py              provenance tiers
│   ├── research_sufficiency.py         research readiness
│   ├── research_trust.py               research trust
│   ├── bias.py                         bias helpers
│   ├── bias_firewall.py                bias boundary
│   ├── response_bias.py                response-bias checks
│   ├── format_fairness.py              output fairness
│   └── owner_style.py                  owner-legible output rules
├── build and project utilities
│   ├── build_skills.py                 build-skill generation
│   ├── vague_build.py                  vague-task build path
│   ├── hybrid_build.py                 hybrid build
│   ├── decomposer.py                   task decomposition
│   ├── director.py                     project direction
│   ├── packs.py                        context packs
│   ├── playbooks.py                    playbooks
│   ├── gap_ledger.py                   gap lifecycle
│   ├── package_scoreboard.py            package scoreboard
│   ├── domain_tagger.py                domain tagging
│   ├── domain_place.py                 domain placement experiment
│   ├── control_center.py               local control center
│   ├── repo_audit.py                   repository audit
│   ├── pipeline_map.py                 pipeline map
│   └── workspace_scaffold.py            workspace scaffolding
├── experiments and legacy lanes
│   ├── bakeoff.py / bakeoff_authoring.py / bakeoff_tasks.py
│   ├── pack_experiment.py
│   ├── research_v2_experimental.py
│   ├── self_learning.py
│   ├── free_forever_worker.py
│   ├── response_bias_mcp.py
│   ├── gemini_search_agent.py
│   ├── ckff_rerank.py / ckff_tsv_economics.py
│   ├── goose_config.py
│   ├── safeguard_trips.py
│   ├── study_log_mgmt.py
│   ├── run_deep_dives.py
│   ├── bench_seed.py
│   └── rtk_ab_scenarios.py
└── v3_bridge/
    ├── __init__.py
    ├── contracts.py
    └── runtime.py
```

## Immediate V4 candidates

The first candidate slice is deliberately small:

```text
methodology: session_preflight + forced_rag_gate + methodology_receipt
corpus:      config + search + knowledge + write_policy
summon:      model_summon + model_dispatch + agent_runtime + tool_surface
observation: trace_capture + otel + langfuse_sink + observability_dashboard
models:      run/task/summon/result/observation schemas written fresh for V4
```

Everything else is parked until this slice has an owner-approved manifest and passes its A/B/C
boundary test. No module is selected merely because it appears in this inventory.
