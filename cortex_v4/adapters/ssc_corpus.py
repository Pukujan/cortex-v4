"""Read/search/write-policy boundary over the SSC RAG corpus."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .ssc_import import import_ssc, ssc_root


class SSCCorpusAdapter:
    def __init__(self, corpus_root: str | Path | None = None):
        self.corpus_root = ssc_root(corpus_root)

    def _inside(self, ref: str | Path) -> Path:
        candidate = Path(ref)
        if not candidate.is_absolute():
            candidate = self.corpus_root / candidate
        resolved = candidate.resolve()
        try:
            resolved.relative_to(self.corpus_root)
        except ValueError as exc:
            raise PermissionError(f"corpus reference escapes SSC root: {ref}") from exc
        if not resolved.is_file():
            raise FileNotFoundError(str(resolved))
        return resolved

    def read_context(self, refs: list[str | Path]) -> dict[str, Any]:
        files = []
        for ref in refs:
            path = self._inside(ref)
            content = path.read_text(encoding="utf-8", errors="replace")
            files.append({
                "ref": str(path.relative_to(self.corpus_root)),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "content": content,
            })
        material = "\n".join(f["sha256"] for f in files).encode("ascii")
        return {"corpus_root": str(self.corpus_root), "context_hash": hashlib.sha256(material).hexdigest(),
                "files": files}

    def search(self, query: str, *, limit: int = 20) -> dict[str, Any]:
        module = import_ssc("cortex_core.knowledge", root=self.corpus_root)
        result = module.composite_search(
            query,
            brain_workspace=self.corpus_root,
            tenant_workspace=self.corpus_root,
            limit=limit,
            log_telemetry=False,
        )
        return result

    def write_policy(self, task: str, result: str, *, tests: str = "", scripts: str = "") -> dict[str, Any]:
        module = import_ssc("cortex_core.write_policy", root=self.corpus_root)
        policy, decision = module.evaluate_write(
            self.corpus_root, task, result, tests=tests, scripts=scripts
        )
        return {"policy": policy.__dict__, "decision": decision.__dict__}
