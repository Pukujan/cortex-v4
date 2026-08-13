# Methodology and corpus A/B/C/D replay

> **Historical record.** This replay captures the pre-retirement SSC adapter boundary. It must not
> be read as permission for normal V4 to use SSC. See `docs/CURRENT-RUNTIME-CONTRACT-2026-08-13.md`.

The first V4 methodology/corpus slice uses SSC as the canonical implementation and RAG store.
V4 adds only a controlled adapter boundary.

| Arm | Result |
|---|---|
| A | Direct SSC preflight and exact corpus read observed |
| B | V4 adapter produced matching manual hash, preflight pack hash, and context hash |
| C | V4 refused an escaping corpus reference and invalid methodology receipt |
| D | SSC-side hidden behavioral evaluator compares A/B/C invariants |

Deck: `observations/decks/methodology-corpus-abcd-20260805.json`.

This slice does not copy the SSC manual, search index, or RAG records into V4. It proves that V4
can consume the canonical SSC methodology and corpus safely before the Model Summon/tool adapter
is attached.
