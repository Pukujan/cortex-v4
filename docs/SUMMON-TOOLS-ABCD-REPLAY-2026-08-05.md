# Model Summon and tool-call A/B/C/D replay — 2026-08-05

This slice moves only the control boundary into V4. The owner-controlled SSC seat table,
dispatch chain, tool registry, and mutation gate remain live in SSC; V4 does not duplicate
them or make a provider request during the migration replay.

- A observes SSC resolution for the `kimi` seat, its dispatch chain, and tool surfaces.
- B asks V4 adapters for the same values.
- C proves unknown seats and hazardous writes are refused.
- D is evaluated by an SSC-side holdout that compares only the behavioral contract, not a
  copied transcript or answer.

The real provider/temporal execution test is separate: temporal A/B/C/D already passes for
the deterministic 120-step worker-loss case. A live provider request is not used as a hidden
fixture because credentials, latency, and model output are not stable migration oracles.

