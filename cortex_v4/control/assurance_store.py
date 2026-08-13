"""SQLite-backed durable event store for the V4 assurance reference contract.

This store is intentionally narrow. It does not execute external mutations. It
provides the durable transaction boundary that the controller must cross before
and after those effects:

* work-order identity is registered durably;
* every event is validated by replaying the independent reference model inside
  a serialized SQLite write transaction;
* event content IDs are primary keys, making retry-after-commit idempotent;
* WAL + ``synchronous=FULL`` make a committed append recoverable after process
  death on normal local filesystems; and
* stale epoch/fence, authority-skip, artifact-version, causal-parent, and
  mutation-order failures abort the transaction rather than leaving a partial
  event.

A successful return means the event transaction committed. It does not mean an
external effect occurred, was correct, or was independently verified.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from typing import Any

from cortex_v4.control.assurance import (
    AssuranceReferenceModel,
    AssuranceSnapshot,
    AssuranceWorkOrder,
    WorkEvent,
    WorkEventKind,
)


SCHEMA_VERSION = "cortex.v4.assurance_store.v1"


class AssuranceStoreError(RuntimeError):
    """Raised when durable store identity or persistence invariants fail."""


@dataclass(frozen=True)
class StoredEventReceipt:
    schema_version: str
    work_order_id: str
    event_cid: str
    sequence: int
    duplicate: bool


class DurableAssuranceStore:
    """One SQLite file containing durable work orders and content-addressed events."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if str(path) == ":memory:":
            raise AssuranceStoreError("durable store cannot use :memory:")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self.path),
            timeout=30.0,
            isolation_level=None,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        journal_mode = str(self._conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]).lower()
        if journal_mode != "wal":
            self._conn.close()
            raise AssuranceStoreError(f"WAL mode unavailable: {journal_mode}")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._init_schema()

    def __enter__(self) -> "DurableAssuranceStore":
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
            CREATE TABLE IF NOT EXISTS assurance_work_orders (
                work_order_id TEXT PRIMARY KEY,
                artifact_id TEXT NOT NULL,
                artifact_version TEXT NOT NULL,
                mutating INTEGER NOT NULL CHECK (mutating IN (0, 1)),
                initial_epoch INTEGER NOT NULL,
                initial_fence_token TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS assurance_events (
                event_cid TEXT PRIMARY KEY,
                work_order_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                UNIQUE (work_order_id, sequence),
                FOREIGN KEY (work_order_id)
                    REFERENCES assurance_work_orders(work_order_id)
                    ON DELETE RESTRICT
            );

            CREATE INDEX IF NOT EXISTS idx_assurance_events_order
                ON assurance_events(work_order_id, sequence);
            """
        )

    def durability_pragmas(self) -> dict[str, Any]:
        return {
            "journal_mode": str(self._conn.execute("PRAGMA journal_mode").fetchone()[0]).lower(),
            "synchronous": int(self._conn.execute("PRAGMA synchronous").fetchone()[0]),
            "foreign_keys": int(self._conn.execute("PRAGMA foreign_keys").fetchone()[0]),
        }

    @staticmethod
    def _work_order_tuple(work_order: AssuranceWorkOrder) -> tuple[Any, ...]:
        return (
            work_order.work_order_id,
            work_order.artifact_id,
            work_order.artifact_version,
            int(work_order.mutating),
            work_order.initial_epoch,
            work_order.initial_fence_token,
        )

    @staticmethod
    def _work_order_from_row(row: sqlite3.Row) -> AssuranceWorkOrder:
        return AssuranceWorkOrder(
            work_order_id=row["work_order_id"],
            artifact_id=row["artifact_id"],
            artifact_version=row["artifact_version"],
            mutating=bool(row["mutating"]),
            initial_epoch=int(row["initial_epoch"]),
            initial_fence_token=row["initial_fence_token"],
        )

    @staticmethod
    def _event_payload(event: WorkEvent) -> dict[str, Any]:
        return {
            "work_order_id": event.work_order_id,
            "kind": event.kind.value,
            "actor_id": event.actor_id,
            "artifact_id": event.artifact_id,
            "artifact_version": event.artifact_version,
            "epoch": event.epoch,
            "fence_token": event.fence_token,
            "parent_event_cids": list(event.parent_event_cids),
            "evidence_refs": list(event.evidence_refs),
            "decision": event.decision,
        }

    @classmethod
    def _event_json(cls, event: WorkEvent) -> str:
        return json.dumps(
            cls._event_payload(event),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    @staticmethod
    def _event_from_json(payload_json: str) -> WorkEvent:
        payload = json.loads(payload_json)
        return WorkEvent(
            work_order_id=payload["work_order_id"],
            kind=WorkEventKind(payload["kind"]),
            actor_id=payload["actor_id"],
            artifact_id=payload["artifact_id"],
            artifact_version=payload["artifact_version"],
            epoch=int(payload["epoch"]),
            fence_token=payload["fence_token"],
            parent_event_cids=tuple(payload.get("parent_event_cids") or ()),
            evidence_refs=tuple(payload.get("evidence_refs") or ()),
            decision=payload.get("decision"),
        )

    def register_work_order(self, work_order: AssuranceWorkOrder) -> None:
        """Idempotently register one immutable work-order identity."""
        # Constructing the oracle validates required identity fields first.
        AssuranceReferenceModel(work_order)
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            row = self._conn.execute(
                "SELECT * FROM assurance_work_orders WHERE work_order_id = ?",
                (work_order.work_order_id,),
            ).fetchone()
            if row is None:
                self._conn.execute(
                    """
                    INSERT INTO assurance_work_orders (
                        work_order_id, artifact_id, artifact_version, mutating,
                        initial_epoch, initial_fence_token
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    self._work_order_tuple(work_order),
                )
            elif self._work_order_from_row(row) != work_order:
                raise AssuranceStoreError(
                    "work_order_id already registered with different immutable identity"
                )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def load_work_order(self, work_order_id: str) -> AssuranceWorkOrder:
        row = self._conn.execute(
            "SELECT * FROM assurance_work_orders WHERE work_order_id = ?",
            (work_order_id,),
        ).fetchone()
        if row is None:
            raise AssuranceStoreError(f"unknown work order: {work_order_id}")
        return self._work_order_from_row(row)

    def load_events(self, work_order_id: str) -> tuple[WorkEvent, ...]:
        rows = self._conn.execute(
            """
            SELECT payload_json
            FROM assurance_events
            WHERE work_order_id = ?
            ORDER BY sequence ASC
            """,
            (work_order_id,),
        ).fetchall()
        return tuple(self._event_from_json(row["payload_json"]) for row in rows)

    def event_count(self, work_order_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM assurance_events WHERE work_order_id = ?",
            (work_order_id,),
        ).fetchone()
        return int(row[0])

    def snapshot(self, work_order_id: str) -> AssuranceSnapshot:
        work_order = self.load_work_order(work_order_id)
        model = AssuranceReferenceModel.replay(work_order, self.load_events(work_order_id))
        return model.snapshot

    def append_event(self, event: WorkEvent) -> tuple[StoredEventReceipt, AssuranceSnapshot]:
        """Validate and durably append one event in a serialized transaction.

        The reference model is replayed from the events read under the same
        ``BEGIN IMMEDIATE`` transaction that performs the insert. Two concurrent
        writers therefore cannot both validate against the same stale state and
        then commit conflicting next events.
        """
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            row = self._conn.execute(
                "SELECT * FROM assurance_work_orders WHERE work_order_id = ?",
                (event.work_order_id,),
            ).fetchone()
            if row is None:
                raise AssuranceStoreError(f"unknown work order: {event.work_order_id}")
            work_order = self._work_order_from_row(row)

            event_rows = self._conn.execute(
                """
                SELECT event_cid, sequence, payload_json
                FROM assurance_events
                WHERE work_order_id = ?
                ORDER BY sequence ASC
                """,
                (event.work_order_id,),
            ).fetchall()
            events = tuple(self._event_from_json(existing["payload_json"]) for existing in event_rows)
            model = AssuranceReferenceModel.replay(work_order, events)
            serialized = self._event_json(event)

            duplicate = next(
                (existing for existing in event_rows if existing["event_cid"] == event.cid),
                None,
            )
            if duplicate is not None:
                if duplicate["payload_json"] != serialized:
                    raise AssuranceStoreError("event CID collision with different payload")
                sequence = int(duplicate["sequence"])
                snapshot = model.snapshot
                self._conn.commit()
                return (
                    StoredEventReceipt(
                        schema_version=SCHEMA_VERSION,
                        work_order_id=event.work_order_id,
                        event_cid=event.cid,
                        sequence=sequence,
                        duplicate=True,
                    ),
                    snapshot,
                )

            snapshot = model.apply(event)
            sequence = len(event_rows) + 1
            self._conn.execute(
                """
                INSERT INTO assurance_events (
                    event_cid, work_order_id, sequence, payload_json
                ) VALUES (?, ?, ?, ?)
                """,
                (event.cid, event.work_order_id, sequence, serialized),
            )
            self._conn.commit()
            return (
                StoredEventReceipt(
                    schema_version=SCHEMA_VERSION,
                    work_order_id=event.work_order_id,
                    event_cid=event.cid,
                    sequence=sequence,
                    duplicate=False,
                ),
                snapshot,
            )
        except Exception:
            self._conn.rollback()
            raise
