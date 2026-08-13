# Cortex V4 strict LiteLLM P0

Status: staging-only implementation for issue #19 and PR #18.

Paired LiteLLM strict-profile commit: `Pukujan/litellm-ckff-ops@65ed5a5782fce082b831e334ad156a6b211363ab`.

Strict Cortex uses `StrictLiteLLMTransport` and `StrictLiteLLMStageWorker` with profile identity `p0-local-staging-zero-retry-v1`.

The canonical long-running endpoint is Chat Completions with real SSE streaming. Translated Responses streaming is intentionally rejected by the strict transport until that bridge provides genuine upstream incremental streaming.

The strict transport rejects a reported actual model that differs from the requested model. A different semantic model can only be chosen by the Cortex orchestrator as a later attempt/generation.

The paired LiteLLM staging profile has zero LiteLLM retries and zero router retries. Cortex therefore owns retries, backoff, generation fencing, checkpoint recovery, and objective completion.

Strict campaign defaults are a 120-second LiteLLM request ceiling, a 72-second Cortex client/stage/inactivity budget, and a 300-second campaign budget. Provider deadline is recorded only when explicitly supplied.

SSE chunks, model text, and read-only tool calls are not durable progress. The strict worker renews the progress heartbeat only after an accepted mutation or persisted artifact reference.

Every strict provider receipt includes requested/actual model, route/api-base label, `config_profile`, `transport_retries: 0`, `semantic_fallbacks: false`, timeout values, endpoint/stream mode, request reference when available, and result classification. Prompts, credentials, provider bodies, and model output text are excluded.

Use `python scripts/run_p0_strict_campaign.py` with the same staging credential variables as the earlier P0 campaign.

Issue #19 is not fully complete until an authenticated strict staging campaign is run against the paired zero-retry LiteLLM profile and its sanitized proof is attached to PR #18.
