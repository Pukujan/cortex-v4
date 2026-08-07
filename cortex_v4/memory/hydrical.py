"""Lazy pointer hydration with DictStore / FileResolver (V4 independent)."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable

from .pointers import Pointer, ResolvedPointer, format_pointer, parse_pointer


def _file_fingerprint(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "exists": False}
    st = path.stat()
    out: dict[str, Any] = {
        "path": str(path),
        "exists": True,
        "is_file": path.is_file(),
        "size": st.st_size,
        "mtime": st.st_mtime,
        "sha256": None,
    }
    if path.is_file():
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        out["sha256"] = h.hexdigest()
    return out


@runtime_checkable
class Resolver(Protocol):
    def resolve(self, pointer: Pointer) -> ResolvedPointer | None: ...


@dataclass
class DictStore:
    data: dict[str, Any] = field(default_factory=dict)

    def put(
        self,
        pointer: Pointer | str,
        value: Any,
        *,
        provenance: Mapping[str, Any] | None = None,
    ) -> None:
        key = format_pointer(pointer)
        self.data[key] = (value, dict(provenance or {}))

    def resolve(self, pointer: Pointer) -> ResolvedPointer | None:
        key = str(pointer)
        if key not in self.data:
            return None
        value, prov = self.data[key]
        return ResolvedPointer(pointer=pointer, value=value, provenance=prov)


@dataclass
class FileResolver:
    root: Path
    namespace: str = "file"

    def __post_init__(self) -> None:
        self.root = Path(self.root).resolve()

    def resolve(self, pointer: Pointer) -> ResolvedPointer | None:
        if pointer.namespace != self.namespace:
            return None
        path = (self.root / pointer.key).resolve()
        try:
            path.relative_to(self.root)
        except ValueError:
            raise PermissionError(f"pointer escapes root: {pointer}") from None
        if not path.is_file():
            return None
        text = path.read_text(encoding="utf-8", errors="replace")
        fp = _file_fingerprint(path)
        return ResolvedPointer(
            pointer=pointer,
            value=text,
            provenance={"source": str(path), "fingerprint": fp},
        )


@dataclass
class Hydrator:
    resolvers: list[Resolver] = field(default_factory=list)
    _cache: dict[str, ResolvedPointer] = field(default_factory=dict)

    def register(self, resolver: Resolver) -> None:
        self.resolvers.append(resolver)

    def hydrate(
        self, pointer: Pointer | str, *, use_cache: bool = True
    ) -> ResolvedPointer:
        ptr = pointer if isinstance(pointer, Pointer) else parse_pointer(pointer)
        key = str(ptr)
        if use_cache and key in self._cache:
            return self._cache[key]
        for resolver in self.resolvers:
            hit = resolver.resolve(ptr)
            if hit is not None:
                self._cache[key] = hit
                return hit
        raise KeyError(f"unresolvable pointer: {ptr}")

    def hydrate_many(self, pointers: list[Pointer | str]) -> list[ResolvedPointer]:
        return [self.hydrate(p) for p in pointers]

    def is_stale(self, resolved: ResolvedPointer) -> bool:
        fp = (resolved.provenance or {}).get("fingerprint")
        if not isinstance(fp, dict) or not fp.get("path"):
            return False
        path = Path(fp["path"])
        now = _file_fingerprint(path)
        if now.get("exists") != fp.get("exists"):
            return True
        if now.get("sha256") and fp.get("sha256"):
            return now["sha256"] != fp["sha256"]
        return (now.get("size"), now.get("mtime")) != (fp.get("size"), fp.get("mtime"))

    def clear_cache(self, pointer: Pointer | str | None = None) -> None:
        if pointer is None:
            self._cache.clear()
            return
        self._cache.pop(format_pointer(pointer), None)
