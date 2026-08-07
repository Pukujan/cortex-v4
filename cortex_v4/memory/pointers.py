"""Typed memory pointers: stable namespace:key IDs for evidence (V4 independent)."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

_PTR = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):([^:\s]+)$")
_UNSET = object()


def _digest(namespace: str, key: str) -> str:
    return hashlib.sha256(f"{namespace}:{key}".encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class Pointer:
    namespace: str
    key: str
    label: str = ""
    _hash: Any = field(default_factory=lambda: _UNSET)

    def __post_init__(self) -> None:
        if not self.namespace or not str(self.namespace).strip():
            raise ValueError("pointer namespace must not be empty")
        if not self.key or not str(self.key).strip():
            raise ValueError("pointer key must not be empty")
        if self._hash is _UNSET:
            object.__setattr__(self, "_hash", _digest(self.namespace, self.key))

    @property
    def hash(self) -> str:
        return self._hash

    def __str__(self) -> str:
        return f"{self.namespace}:{self.key}"

    def __repr__(self) -> str:
        extra = f" ({self.label})" if self.label else ""
        return f"Pointer({self.namespace}:{self.key}{extra})"


@dataclass(frozen=True)
class ResolvedPointer:
    pointer: Pointer
    value: Any
    provenance: Mapping[str, Any] = field(default_factory=dict)

    @property
    def source_path(self) -> str | None:
        prov = self.provenance or {}
        if prov.get("source"):
            return prov.get("source")
        fp = prov.get("fingerprint") or {}
        return fp.get("path") if isinstance(fp, Mapping) else None

    def __str__(self) -> str:
        return f"{self.pointer} -> {self.value!r}"


def make_pointer(namespace: str, key: str, label: str = "") -> Pointer:
    return Pointer(namespace=namespace, key=key, label=label)


def parse_pointer(text: str, *, namespace_required: bool = True) -> Pointer:
    if not isinstance(text, str):
        raise TypeError(f"pointer text must be a str, got {type(text).__name__}")
    stripped = text.strip()
    if not stripped:
        raise ValueError("empty pointer text")
    body = stripped.split(maxsplit=1)[0].strip()
    m = _PTR.match(body)
    if not m:
        raise ValueError(f"malformed pointer: {text!r}")
    if namespace_required and not m.group(1):
        raise ValueError(f"pointer missing namespace: {text!r}")
    return Pointer(namespace=m.group(1), key=m.group(2))


def format_pointer(pointer: Pointer | str) -> str:
    if isinstance(pointer, Pointer):
        return str(pointer)
    return str(parse_pointer(pointer))


def is_pointer(text: str) -> bool:
    if not isinstance(text, str):
        return False
    stripped = text.strip()
    if not stripped:
        return False
    return bool(_PTR.match(stripped.split()[0]))
