"""Fail-closed tests for the SSC-free normal V4 runtime boundary."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _normal_runtime_files() -> list[Path]:
    control = ROOT / "cortex_v4" / "control"
    operation = ROOT / "cortex_v4" / "operation"
    memory = ROOT / "cortex_v4" / "memory"
    return [
        ROOT / "cortex_v4" / "__init__.py",
        control / "__init__.py",
        *(path for path in control.glob("*.py") if not path.name.startswith("mechanical_")),
        *operation.glob("*.py"),
        *memory.glob("*.py"),
    ]


def test_normal_runtime_imports_without_ssc_or_external_checkout():
    probe = """
import builtins
import sys

real_import = builtins.__import__

def guarded_import(name, *args, **kwargs):
    forbidden = (
        name == 'cortex_core',
        name.startswith('cortex_core.'),
        name == 'cortex_v4.adapters',
        name.startswith('cortex_v4.adapters.'),
        name.startswith('cortex_v4.control.mechanical_'),
    )
    if any(forbidden):
        raise AssertionError('forbidden SSC import: ' + name)
    return real_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
import cortex_v4.control
import cortex_v4.memory
import cortex_v4.operation

assert not any(name == 'cortex_core' or name.startswith('cortex_core.') for name in sys.modules)
assert not any(name == 'cortex_v4.adapters' or name.startswith('cortex_v4.adapters.') for name in sys.modules)
print('normal-v4-import-clean')
"""
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "normal-v4-import-clean" in result.stdout


def test_normal_runtime_has_no_external_ssc_imports_or_checkout_default():
    forbidden_imports = ("cortex_core", "cortex_v4.adapters", "ssc_")
    forbidden_paths = ("D:\\claude\\stupidly-simple-cortex", "/d/claude/stupidly-simple-cortex")
    for path in _normal_runtime_files():
        source = path.read_text(encoding="utf-8")
        assert not any(marker in source for marker in forbidden_paths), path
        tree = ast.parse(source, filename=str(path))
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        assert not any(any(module == prefix or module.startswith(prefix + ".")
                           for prefix in forbidden_imports[:2]) or "ssc_" in module
                       for module in imported), (path, imported)


def test_legacy_ssc_adapter_requires_explicit_opt_in():
    probe = """
from cortex_v4.adapters.ssc_import import ssc_root
try:
    ssc_root()
except PermissionError:
    print('legacy-ssc-blocked')
else:
    raise AssertionError('legacy SSC access unexpectedly enabled')
"""
    env = os.environ.copy()
    env.pop("CORTEX_V4_ALLOW_LEGACY_SSC", None)
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "legacy-ssc-blocked" in result.stdout
