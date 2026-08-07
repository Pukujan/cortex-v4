"""V4 observation/deck boundary backed by SSC's proven local telemetry controls."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

from .ssc_import import import_ssc, ssc_root


class SSCObservabilityAdapter:
    def __init__(self, corpus_root: str | Path | None = None):
        self.corpus_root = ssc_root(corpus_root)

    def capture(self, record: Mapping[str, Any], *, workspace: str | Path) -> bool:
        module = import_ssc("cortex_core.trace_capture", root=self.corpus_root)
        return bool(module.capture(module.TraceRecord(**dict(record)), workspace=workspace))

    @contextmanager
    def span(self, name: str, *, env: Mapping[str, str] | None = None, **kwargs: Any) -> Iterator[Any]:
        module = import_ssc("cortex_core.otel", root=self.corpus_root)
        with module.gen_ai_span(name, env=env, **kwargs) as handle:
            yield handle

    def snapshot(self, *, workspace: str | Path) -> dict[str, Any]:
        module = import_ssc("cortex_core.observability_dashboard", root=self.corpus_root)
        return module.collect_observability_snapshot(workspace)

    def render(self, snapshot: Mapping[str, Any]) -> str:
        module = import_ssc("cortex_core.observability_dashboard", root=self.corpus_root)
        return str(module.render_html(dict(snapshot)))

