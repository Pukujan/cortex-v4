"""Run the first methodology/corpus adapter A/B/C replay deck."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SSC = Path(r"D:\claude\stupidly-simple-cortex")
if str(SSC) not in sys.path:
    sys.path.insert(0, str(SSC))
from cortex_v4.adapters import SSCCorpusAdapter, SSCMethodologyAdapter  # noqa: E402

DECK = ROOT / "observations" / "decks" / "methodology-corpus-abcd-20260805.json"


def main() -> int:
    task = "audit the cortex temporal migration boundary"
    source_manual = (SSC / "docs" / "methodology" / "WORK-METHODOLOGIES.md").read_bytes()
    source_hash = hashlib.sha256(source_manual).hexdigest()
    source_m = __import__("cortex_core.session_preflight", fromlist=["*"])
    source_preflight = source_m.run_preflight(task, workspace=SSC)
    source_c = SSCCorpusAdapter(SSC)
    source_context = source_c.read_context(["docs/methodology/WORK-METHODOLOGIES.md"])
    source_search = source_c.search("M32 observation-first", limit=3)

    v4_m = SSCMethodologyAdapter(SSC)
    v4_c = SSCCorpusAdapter(SSC)
    v4_preflight = v4_m.preflight(task, workspace=SSC)
    v4_context = v4_c.read_context(["docs/methodology/WORK-METHODOLOGIES.md"])
    v4_search = v4_c.search("M32 observation-first", limit=3)

    c_checks = {}
    try:
        v4_c.read_context(["../outside-ssc.txt"])
    except PermissionError as exc:
        c_checks["escape_refused"] = True
        c_checks["reason"] = str(exc)
    else:
        c_checks["escape_refused"] = False
    c_checks["invalid_receipt_rejected"] = bool(v4_m.validate_receipt({"schema": "wrong"}))

    deck = {
        "schema": "cortex.v4.migration_observation.v1",
        "source": "SSC canonical corpus and methodology",
        "status": "candidate_for_ssc_holdout",
        "axes": {
            "A": {"status": "source_observed", "manual_sha256": source_hash,
                  "pack_hash": source_preflight.pack_hash,
                  "context_hash": source_context["context_hash"],
                  "procedure_ids": SSCMethodologyAdapter(SSC).procedure_ids(),
                  "search": {"hits": source_search["hits"], "results": [
                      r["relative_path"] for r in source_search["results"]]}},
            "B": {"status": "v4_adapter_observed", "manual_sha256": hashlib.sha256(
                      v4_m.manual_text().encode("utf-8")).hexdigest(),
                  "pack_hash": v4_preflight["pack_hash"],
                  "context_hash": v4_context["context_hash"],
                  "procedure_ids": v4_m.procedure_ids(),
                  "search": {"hits": v4_search["hits"], "results": [
                      r["relative_path"] for r in v4_search["results"]]}},
            "C": {"status": "negative_control", **c_checks},
            "D": {"status": "awaiting_external_ssc_holdout"},
        },
    }
    DECK.parent.mkdir(parents=True, exist_ok=True)
    DECK.write_text(json.dumps(deck, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(deck, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
