# Handoff — V4 mechanical session control (OpenCode wire) · 2026-08-07

## Verdict

**V4 is now a real session control layer for driver work, not adapter-only.**  
A/B vs the raw SSC adapter path **PASS**. Tests **7/7 PASS**.

- **A (adapter-only):** preflight + forced-RAG through `SSCMethodologyAdapter` directly.
- **B (mechanical session):** classify → search → preflight → gate → closeout owned by
  `cortex_v4.control.mechanical_session`.
- B matches A on grounded allow + pack_hash, and refuses ungrounded Write.
- B selects methodology IDs (M0/M1/M7/… + task class) and stamps `control_layer`.

## What shipped

| Artifact | Role |
|---|---|
| `cortex_v4/control/mechanical_session.py` | Control layer: classify, search, preflight, tool gate, closeout, chain, ab |
| `tests/test_mechanical_session.py` | 7 tests (classify, refuse, shadow, chain, mutant, A/B, oracle) |
| `~/.config/opencode/plugins/cortex-v4-mechanical.ts` | OpenCode wire: tools + shadow gate + system inject |
| `observations/mechanical-session/AB-RESULT-2026-08-07.json` | Live A/B receipt |

## How drivers use it

```text
OpenCode session
  → cortex-v4-mechanical plugin
    → python cortex_v4.control.mechanical_session {preflight|search|gate|closeout}
      → SSC corpus (search / pack store / closeout write)
```

CLI:

```powershell
$env:PYTHONPATH = "D:\claude\cortex-v4"
python -m cortex_v4.control.mechanical_session ab --task "..."
python -m cortex_v4.control.mechanical_session preflight --session-id s1 --task "..."
python -m cortex_v4.control.mechanical_session gate --session-id s1 --tool Write --shadow
```

Enforce (narrow): set `CORTEX_V4_ENFORCE=1`. Default is **shadow** (log would_have_failed).

## A/B numbers (this run)

| Path | pack_hash | grounded allow | ungrounded allow | control_layer | latency |
|---|---|---|---|---|---|
| A adapter | yes | yes | no | ssc_adapter_direct | ~3.9s |
| B mechanical | yes | yes | no (would_have_failed) | cortex_v4.control.mechanical_session | ~67s |

B is slower because it runs full search + classify + closeout chain; correctness bar met.

## Not done yet (honest)

1. OpenCode must be **restarted** to load `cortex-v4-mechanical.ts`.
2. Shadow sample (10 SSC + 5 non-SSC) not re-run under the new plugin.
3. Hard deny on `tool.execute.before` is still version-dependent; enforce is log-strong + env flag.
4. Production signer / live Hades route / evaluator still blocked (unrelated).
5. Codex driver wire not installed this turn (same CLI entry works; plugin is OpenCode-first).

## Next

1. Restart OpenCode → confirm plugin tools appear.
2. Run shadow sessions; inspect `~/.config/opencode/cortex-ritual/v4-shadow-log.jsonl`.
3. If exit criteria pass, set `CORTEX_V4_ENFORCE=1` for SSC worktrees only.
