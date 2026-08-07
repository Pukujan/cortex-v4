# V4 observation boundary

V4 keeps its own observation packages separate from the migrated public contract. The V4-A
failure observation, V4-B repair observation, and later hidden holdouts belong here:

```text
observations/
  decks/       human review projections
  runs/        run summaries and event projections
  holdouts/    hidden source-comparison evidence
```

SSC-A answers and diagnosis are not copied here. V4 must independently observe and localize the
replayed failure under M32 before comparing objective behavior with SSC.
