from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class OperationReceipt:
    run_id: str
    task: str
    context_hash: str
    methodology_pack_hash: str
    seat: str
    tier: str
    model_override: str | None
    status: str
    observation_overall: str
    source_corpus: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

