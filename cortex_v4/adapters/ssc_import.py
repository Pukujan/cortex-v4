"""Controlled import boundary for proven SSC implementations."""
from __future__ import annotations

import os
import sys
from pathlib import Path


DEFAULT_SSC_ROOT = Path(r"D:\claude\stupidly-simple-cortex")
LEGACY_SSC_OPT_IN = "CORTEX_V4_ALLOW_LEGACY_SSC"


def ssc_root(value: str | Path | None = None) -> Path:
    if os.environ.get(LEGACY_SSC_OPT_IN) != "1":
        raise PermissionError(
            "legacy SSC access is disabled; set CORTEX_V4_ALLOW_LEGACY_SSC=1 "
            "only for an explicitly authorized migration/evaluation run"
        )
    root = Path(value or os.environ.get("SSC_CORPUS_ROOT") or DEFAULT_SSC_ROOT).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"SSC corpus root does not exist: {root}")
    return root


def import_ssc(module: str, *, root: str | Path | None = None):
    path = ssc_root(root)
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
    return __import__(module, fromlist=["*"])
