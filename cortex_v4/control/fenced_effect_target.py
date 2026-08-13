"""Separate-process fenced/idempotent external-effect test target for V4.

This module models the authority boundary that the V4 event store cannot provide
on behalf of an external system.  The target owns its own durable lease epoch
and fence token and checks them in the same SQLite transaction that records an
effect.

Properties of this test target:

* file-backed SQLite WAL + ``synchronous=FULL``;
* lease advance is monotonic and changes the fence token;
* every observe/apply request must carry the target's current epoch/fence;
* stale callers are rejected before they can create an effect;
* effect insertion is keyed by a stable idempotency key and is atomic with the
  lease check; and
* a newer lease may observe an effect that was legitimately committed by an
  older lease, allowing recovery without replaying it.

This is a conformance test target, not a claim that arbitrary third-party APIs
support fencing or idempotency.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import Any, Mapping

from cortex_v4.control.direct_assurance_controller import (
    MutationObservation,
    ObservationState,
)


class EffectTargetError(RuntimeError):
    """Base error for the fenced effect target."""


class StaleLeaseError(EffectTargetError):
    """Raised when a caller does not own the target's current lease."""


@dataclass(frozen=True)
class EffectApplyReceipt:
    idempotency_key: str
    evidence_ref: str
    duplicate: bool
    applied_epoch: int
    applied_fence_token: str


class FencedEffectTargetStore:
    """Durable target-side lease and effect state."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if str(path) == ":memory:":
            raise EffectTargetError("fenced effect target cannot use :memory:")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), timeout=30.0, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        mode = str(self._conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]).lower()
        if mode != "wal":
            self._conn.close()
            raise EffectTargetError(f"WAL mode unavailable: {mode}")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._init_schema()

    def __enter__(self) -> "FencedEffectTargetStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        if getattr(self, "_conn", None) is not None:
            self._conn.close()
            self._conn = None  # type: ignore[assignment]

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS target_lease (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                epoch INTEGER NOT NULL,
                fence_token TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS target_effects (
                idempotency_key TEXT PRIMARY KEY,
                evidence_ref TEXT NOT NULL,
                applied_epoch INTEGER NOT NULL,
                applied_fence_token TEXT NOT NULL
            );
            """
        )

    def initialize_lease(self, *, epoch: int, fence_token: str) -> None:
        if epoch < 0 or not fence_token:
            raise EffectTargetError("initial lease is invalid")
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            row = self._conn.execute(
                "SELECT epoch, fence_token FROM target_lease WHERE singleton = 1"
            ).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO target_lease(singleton, epoch, fence_token) VALUES (1, ?, ?)",
                    (epoch, fence_token),
                )
            elif int(row["epoch"]) != epoch or row["fence_token"] != fence_token:
                raise EffectTargetError("target lease already initialized differently")
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def advance_lease(self, *, epoch: int, fence_token: str) -> None:
        if epoch < 0 or not fence_token:
            raise EffectTargetError("lease is invalid")
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            row = self._conn.execute(
                "SELECT epoch, fence_token FROM target_lease WHERE singleton = 1"
            ).fetchone()
            if row is None:
                raise EffectTargetError("target lease is not initialized")
            current_epoch = int(row["epoch"])
            current_fence = str(row["fence_token"])
            if epoch <= current_epoch:
                raise EffectTargetError("target lease epoch must increase")
            if fence_token == current_fence:
                raise EffectTargetError("target lease takeover requires a new fence token")
            self._conn.execute(
                "UPDATE target_lease SET epoch = ?, fence_token = ? WHERE singleton = 1",
                (epoch, fence_token),
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def current_lease(self) -> tuple[int, str]:
        row = self._conn.execute(
            "SELECT epoch, fence_token FROM target_lease WHERE singleton = 1"
        ).fetchone()
        if row is None:
            raise EffectTargetError("target lease is not initialized")
        return int(row["epoch"]), str(row["fence_token"])

    @staticmethod
    def _evidence_ref(idempotency_key: str) -> str:
        digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
        return f"target-effect:{digest}"

    @staticmethod
    def _validate_request(idempotency_key: str, epoch: int, fence_token: str) -> None:
        if not idempotency_key:
            raise EffectTargetError("idempotency_key is required")
        if epoch < 0 or not fence_token:
            raise EffectTargetError("request lease is invalid")

    @staticmethod
    def _validate_current_lease(row: sqlite3.Row | None, epoch: int, fence_token: str) -> None:
        if row is None:
            raise EffectTargetError("target lease is not initialized")
        current_epoch = int(row["epoch"])
        current_fence = str(row["fence_token"])
        if epoch != current_epoch:
            raise StaleLeaseError(f"stale target epoch: got {epoch}, current {current_epoch}")
        if fence_token != current_fence:
            raise StaleLeaseError("stale target fence token")

    def observe(
        self,
        *,
        idempotency_key: str,
        epoch: int,
        fence_token: str,
    ) -> MutationObservation:
        self._validate_request(idempotency_key, epoch, fence_token)
        self._conn.execute("BEGIN")
        try:
            lease = self._conn.execute(
                "SELECT epoch, fence_token FROM target_lease WHERE singleton = 1"
            ).fetchone()
            self._validate_current_lease(lease, epoch, fence_token)
            effect = self._conn.execute(
                "SELECT evidence_ref FROM target_effects WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        if effect is None:
            return MutationObservation(ObservationState.ABSENT)
        return MutationObservation(ObservationState.APPLIED, str(effect["evidence_ref"]))

    def apply(
        self,
        *,
        idempotency_key: str,
        epoch: int,
        fence_token: str,
    ) -> EffectApplyReceipt:
        """Atomically check lease authority and insert the idempotent effect."""
        self._validate_request(idempotency_key, epoch, fence_token)
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            lease = self._conn.execute(
                "SELECT epoch, fence_token FROM target_lease WHERE singleton = 1"
            ).fetchone()
            self._validate_current_lease(lease, epoch, fence_token)
            existing = self._conn.execute(
                """
                SELECT evidence_ref, applied_epoch, applied_fence_token
                FROM target_effects
                WHERE idempotency_key = ?
                """,
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                receipt = EffectApplyReceipt(
                    idempotency_key=idempotency_key,
                    evidence_ref=str(existing["evidence_ref"]),
                    duplicate=True,
                    applied_epoch=int(existing["applied_epoch"]),
                    applied_fence_token=str(existing["applied_fence_token"]),
                )
                self._conn.commit()
                return receipt

            evidence_ref = self._evidence_ref(idempotency_key)
            self._conn.execute(
                """
                INSERT INTO target_effects(
                    idempotency_key, evidence_ref, applied_epoch, applied_fence_token
                ) VALUES (?, ?, ?, ?)
                """,
                (idempotency_key, evidence_ref, epoch, fence_token),
            )
            self._conn.commit()
            return EffectApplyReceipt(
                idempotency_key=idempotency_key,
                evidence_ref=evidence_ref,
                duplicate=False,
                applied_epoch=epoch,
                applied_fence_token=fence_token,
            )
        except Exception:
            self._conn.rollback()
            raise

    def effect_count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM target_effects").fetchone()[0])


def _require_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be a JSON object")
    return value


def _require_str(payload: Mapping[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise TypeError(f"{field} must be a non-empty string")
    return value


def _require_int(payload: Mapping[str, Any], field: str) -> int:
    value = payload.get(field)
    if type(value) is not int:
        raise TypeError(f"{field} must be an integer")
    return value


def handle_request(db_path: str | Path, request: Mapping[str, Any]) -> dict[str, Any]:
    request = _require_mapping(request, "request")
    operation = _require_str(request, "operation")
    with FencedEffectTargetStore(db_path) as store:
        if operation == "initialize_lease":
            store.initialize_lease(
                epoch=_require_int(request, "epoch"),
                fence_token=_require_str(request, "fence_token"),
            )
            return {"ok": True}
        if operation == "advance_lease":
            store.advance_lease(
                epoch=_require_int(request, "epoch"),
                fence_token=_require_str(request, "fence_token"),
            )
            return {"ok": True}
        if operation == "observe":
            observation = store.observe(
                idempotency_key=_require_str(request, "idempotency_key"),
                epoch=_require_int(request, "epoch"),
                fence_token=_require_str(request, "fence_token"),
            )
            return {
                "ok": True,
                "observation": {
                    "state": observation.state.value,
                    "evidence_ref": observation.evidence_ref,
                },
            }
        if operation == "apply":
            receipt = store.apply(
                idempotency_key=_require_str(request, "idempotency_key"),
                epoch=_require_int(request, "epoch"),
                fence_token=_require_str(request, "fence_token"),
            )
            return {"ok": True, "receipt": asdict(receipt)}
        if operation == "effect_count":
            return {"ok": True, "effect_count": store.effect_count()}
        raise ValueError(f"unsupported target operation: {operation!r}")


class EffectTargetProcessError(RuntimeError):
    def __init__(self, error_type: str, message: str):
        super().__init__(f"{error_type}: {message}")
        self.error_type = error_type
        self.message = message


class FencedEffectTargetClient:
    """One-process-per-call client for the separate effect target."""

    def __init__(self, db_path: str | Path, *, timeout_s: float = 10.0):
        self.db_path = str(db_path)
        self.timeout_s = timeout_s

    def _call(self, request: Mapping[str, Any]) -> dict[str, Any]:
        completed = subprocess.run(
            [sys.executable, "-m", "cortex_v4.control.fenced_effect_target", self.db_path],
            input=json.dumps(request, sort_keys=True),
            text=True,
            capture_output=True,
            timeout=self.timeout_s,
            check=False,
        )
        try:
            response = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise EffectTargetProcessError(
                "InvalidTargetResponse",
                completed.stderr.strip() or "target returned non-JSON output",
            ) from exc
        if not response.get("ok"):
            raise EffectTargetProcessError(
                str(response.get("error_type", "EffectTargetProcessError")),
                str(response.get("message", "unknown target error")),
            )
        return response

    def initialize_lease(self, *, epoch: int, fence_token: str) -> None:
        self._call({"operation": "initialize_lease", "epoch": epoch, "fence_token": fence_token})

    def advance_lease(self, *, epoch: int, fence_token: str) -> None:
        self._call({"operation": "advance_lease", "epoch": epoch, "fence_token": fence_token})

    def observe(self, *, idempotency_key: str, epoch: int, fence_token: str) -> MutationObservation:
        response = self._call(
            {
                "operation": "observe",
                "idempotency_key": idempotency_key,
                "epoch": epoch,
                "fence_token": fence_token,
            }
        )["observation"]
        return MutationObservation(
            ObservationState(response["state"]),
            response.get("evidence_ref"),
        )

    def apply(self, *, idempotency_key: str, epoch: int, fence_token: str) -> None:
        self._call(
            {
                "operation": "apply",
                "idempotency_key": idempotency_key,
                "epoch": epoch,
                "fence_token": fence_token,
            }
        )

    def effect_count(self) -> int:
        return int(self._call({"operation": "effect_count"})["effect_count"])


class SubprocessFencedMutationPort:
    """Adapter satisfying the direct controller's external mutation port."""

    def __init__(self, client: FencedEffectTargetClient):
        self.client = client

    def observe(self, *, idempotency_key: str, epoch: int, fence_token: str) -> MutationObservation:
        return self.client.observe(
            idempotency_key=idempotency_key,
            epoch=epoch,
            fence_token=fence_token,
        )

    def apply(self, *, idempotency_key: str, epoch: int, fence_token: str) -> None:
        self.client.apply(
            idempotency_key=idempotency_key,
            epoch=epoch,
            fence_token=fence_token,
        )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print(json.dumps({"ok": False, "error_type": "UsageError", "message": "expected one target database path"}))
        return 2
    try:
        request = json.loads(sys.stdin.read())
        response = handle_request(args[0], _require_mapping(request, "request"))
        print(json.dumps(response, sort_keys=True, separators=(",", ":")))
        return 0
    except Exception as exc:  # noqa: BLE001 - process protocol returns typed failures
        print(
            json.dumps(
                {"ok": False, "error_type": type(exc).__name__, "message": str(exc)},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 2


if __name__ == "__main__":  # pragma: no cover - exercised through subprocess tests
    raise SystemExit(main())
