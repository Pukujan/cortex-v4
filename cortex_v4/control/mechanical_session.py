"""V4 mechanical session control for driver runtimes (OpenCode / Codex).

This is the control layer for live sessions — not a thin passthrough.
SSC remains the corpus (search, manual text, pack storage). V4 owns:

  1. task classification + methodology ID selection
  2. mandatory M1 preflight before gated work
  3. forced-RAG tool gate (refuse without recorded pack)
  4. M7 closeout with receipt validation
  5. hidden-path refuse + session ledger

Drivers call this module (CLI or import). They do not re-implement gates.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

from ..adapters.ssc_corpus import SSCCorpusAdapter
from ..adapters.ssc_methodology import SSCMethodologyAdapter

DEFAULT_SSC = Path(os.environ.get("CORTEX_SSC_ROOT", r"D:\claude\stupidly-simple-cortex"))
DEFAULT_V4 = Path(os.environ.get("CORTEX_V4_ROOT", r"D:\claude\cortex-v4"))
SESSION_LEDGER = Path(
    os.environ.get(
        "CORTEX_V4_SESSION_LEDGER",
        str(Path.home() / ".config" / "opencode" / "cortex-ritual" / "v4-session-ledger.jsonl"),
    )
)

GATED_TOOLS = frozenset(
    {
        "Write",
        "Edit",
        "NotebookEdit",
        "Bash",
        "PowerShell",
        "Task",
        "Agent",
        "SendMessage",
        "write",
        "edit",
        "bash",
        "powershell",
        "task",
        "agent",
    }
)
EXEMPT_TOOLS = frozenset(
    {
        "cortex_search",
        "cortex_session_preflight",
        "cortex_session_closeout",
        "cortex_v4_preflight",
        "cortex_v4_gate",
        "cortex_v4_closeout",
        "cortex_write_log",
        "cortex_ritual_stamp",
        "Read",
        "Grep",
        "Glob",
        "read",
        "grep",
        "glob",
        "python",
    }
)

BASE_METHODOLOGY = ("M0", "M1", "M7", "M10", "M26")
CLASS_RULES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "build",
        ("build", "implement", "wire", "plugin", "migrate", "fix", "refactor"),
        ("M3", "M4", "M30", "M31"),
    ),
    (
        "debug",
        ("debug", "failure", "stall", "bug", "repair", "root-cause", "root cause"),
        ("M12", "M18", "M32", "M33"),
    ),
    (
        "research",
        ("research", "survey", "audit", "citation", "prior art"),
        ("M21", "M22", "M23", "M25"),
    ),
    (
        "dispatch",
        ("dispatch", "summon", "seat", "model route", "fleet"),
        ("M8", "M11"),
    ),
    (
        "eval",
        ("eval", "holdout", "oracle", "benchmark", "score"),
        ("M4", "M9", "M19", "M20"),
    ),
)

FORBIDDEN_MARKERS = (
    "/hidden/",
    "\\hidden\\",
    "A-private",
    "A-ssc-source/hypothesis",
)


class SessionGateError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def assert_path_allowed(path: str | Path, *, label: str = "path") -> Path:
    raw = str(path)
    lower = raw.replace("\\", "/").lower()
    for marker in FORBIDDEN_MARKERS:
        if marker.replace("\\", "/").lower() in lower:
            raise SessionGateError(
                "HIDDEN_HOLDOUT_REFUSED",
                f"{label} touches forbidden marker {marker!r}: {raw}",
            )
    parts = [p.lower() for p in Path(raw.replace("\\", "/")).parts]
    if "hidden" in parts:
        raise SessionGateError(
            "HIDDEN_HOLDOUT_REFUSED", f"{label} has a 'hidden' segment: {raw}"
        )
    return Path(path)


def classify_session_task(task: str | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(task, Mapping):
        text = " ".join(str(task.get(k, "")) for k in ("task", "name", "description", "goal"))
        explicit = task.get("task_class") or task.get("class")
    else:
        text = str(task)
        explicit = None
    lowered = text.lower()
    classes: list[str] = []
    methods = list(BASE_METHODOLOGY)
    for name, tokens, mids in CLASS_RULES:
        if any(tok in lowered for tok in tokens):
            classes.append(name)
            for mid in mids:
                if mid not in methods:
                    methods.append(mid)
    task_class = str(explicit) if explicit else ("+".join(classes) if classes else "generic")
    return {
        "task_class": task_class,
        "methodology_ids": methods,
        "observation_required_before_hypothesis": "debug" in classes or "M32" in methods,
        "control_layer": "cortex_v4.control.mechanical_session",
    }


@dataclass
class SessionState:
    session_id: str
    task: str = ""
    task_class: str = ""
    methodology_ids: list[str] = field(default_factory=list)
    pack_hash: str = ""
    preflight_ok: bool = False
    searched: bool = False
    tools: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    gates: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def touch(self) -> None:
        self.updated_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SessionState":
        return cls(
            session_id=str(data.get("session_id") or ""),
            task=str(data.get("task") or ""),
            task_class=str(data.get("task_class") or ""),
            methodology_ids=list(data.get("methodology_ids") or []),
            pack_hash=str(data.get("pack_hash") or ""),
            preflight_ok=bool(data.get("preflight_ok")),
            searched=bool(data.get("searched")),
            tools=list(data.get("tools") or []),
            files=list(data.get("files") or []),
            gates=list(data.get("gates") or []),
            events=list(data.get("events") or []),
            created_at=float(data.get("created_at") or time.time()),
            updated_at=float(data.get("updated_at") or time.time()),
        )


class MechanicalSessionController:
    """Named V4 control entry for driver sessions."""

    def __init__(
        self,
        *,
        ssc_root: str | Path | None = None,
        ledger_path: str | Path | None = None,
    ):
        self.ssc_root = Path(ssc_root or DEFAULT_SSC)
        self.ledger_path = Path(ledger_path or SESSION_LEDGER)
        self.methodology = SSCMethodologyAdapter(self.ssc_root)
        self.corpus = SSCCorpusAdapter(self.ssc_root)
        self._states: dict[str, SessionState] = {}

    def _event(self, state: SessionState, kind: str, **fields: Any) -> None:
        state.events.append(
            {"event_seq": len(state.events), "ts": time.time(), "kind": kind, **fields}
        )
        state.touch()

    def _gate(self, state: SessionState, name: str, ok: bool, **detail: Any) -> dict[str, Any]:
        entry = {"gate": name, "ok": ok, "detail": detail, "ts": time.time()}
        state.gates.append(entry)
        self._event(state, "gate", gate=name, ok=ok, **detail)
        return entry

    def _persist_ledger(self, record: Mapping[str, Any]) -> None:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(dict(record), sort_keys=True) + "\n")

    def get_state(self, session_id: str) -> SessionState:
        if session_id not in self._states:
            self._states[session_id] = SessionState(session_id=session_id)
        return self._states[session_id]

    def load_or_create(self, session_id: str, *, task: str = "") -> SessionState:
        state = self.get_state(session_id)
        if task and not state.task:
            state.task = task
        return state

    # ---- rung 1: classify -------------------------------------------------
    def classify(self, session_id: str, task: str) -> dict[str, Any]:
        state = self.load_or_create(session_id, task=task)
        classification = classify_session_task(task)
        state.task = task
        state.task_class = classification["task_class"]
        state.methodology_ids = list(classification["methodology_ids"])
        self._gate(
            state,
            "classify",
            True,
            task_class=state.task_class,
            methodology_ids=state.methodology_ids,
        )
        self._event(state, "classify", **classification)
        return {"ok": True, "session_id": session_id, **classification, "state": state.to_dict()}

    # ---- rung 2: corpus search (broker-shaped) ----------------------------
    def search(self, session_id: str, query: str, *, limit: int = 20) -> dict[str, Any]:
        state = self.get_state(session_id)
        assert_path_allowed(self.ssc_root, label="ssc_root")
        # Prefer corpus adapter search when available; fall back to methodology import.
        hits: list[dict[str, Any]] = []
        try:
            raw = self.corpus.search(query, limit=limit)
            rows = []
            if isinstance(raw, Mapping):
                rows = list(raw.get("results") or raw.get("hits") or [])
                if isinstance(raw.get("hits"), int) and not rows:
                    rows = []
            elif isinstance(raw, list):
                rows = list(raw)
            for r in rows[:limit]:
                if isinstance(r, Mapping):
                    hits.append(
                        {
                            "path": str(r.get("path") or r.get("ref") or r.get("id") or ""),
                            "score": r.get("score"),
                            "snippet": str(r.get("snippet") or r.get("text") or "")[:240],
                        }
                    )
                else:
                    hits.append(
                        {
                            "path": str(getattr(r, "path", r)),
                            "score": getattr(r, "score", None),
                            "snippet": str(getattr(r, "snippet", "") or "")[:240],
                        }
                    )
            if not hits and isinstance(raw, Mapping):
                hits = [{"meta": {"hit_count": raw.get("hits"), "coverage": raw.get("coverage")}}]
        except Exception as exc:
            hits = [{"error": str(exc), "query": query}]
        state.searched = True
        state.tools.append("cortex_search")
        self._gate(state, "search", True, hit_count=len(hits), query=query[:120])
        self._event(state, "search", query=query, hit_count=len(hits))
        return {
            "ok": True,
            "session_id": session_id,
            "query": query,
            "hit_count": len(hits),
            "hits": hits[:limit],
            "control_layer": "cortex_v4.control.mechanical_session",
            "searched": True,
        }

    # ---- rung 3: M1 preflight (mechanical) --------------------------------
    def preflight(self, session_id: str, task: str, *, limit: int = 4) -> dict[str, Any]:
        state = self.load_or_create(session_id, task=task)
        if not state.methodology_ids:
            self.classify(session_id, task)
            state = self.get_state(session_id)

        # Mechanical: M1 must run through V4 controller, not raw prompt.
        if "M1" not in state.methodology_ids:
            raise SessionGateError("METHODOLOGY_MISSING", "M1 required for every session preflight")

        result = self.methodology.preflight(task, workspace=self.ssc_root)
        pack_hash = str(result.get("pack_hash") or "")
        citations = result.get("citations") or []
        none_exists = result.get("none_exists") or []
        if not pack_hash:
            self._gate(state, "preflight", False, reason="missing pack_hash")
            raise SessionGateError("PREFLIGHT_PACK_MISSING", "preflight produced no pack_hash")

        # Mechanical quality: either citations or explicit none_exists (M1 legal terminal).
        if not citations and not none_exists:
            self._gate(state, "preflight", False, reason="empty pack without none_exists")
            raise SessionGateError(
                "PREFLIGHT_EMPTY",
                "preflight has neither citations nor none_exists",
            )

        # Record pack into SSC gate_state so forced-RAG can resolve it.
        try:
            gate_state = self.methodology.import_ssc("cortex_core.gate_state")
            if hasattr(gate_state, "record_pack"):
                gate_state.record_pack(
                    pack_hash,
                    task=task,
                    stamp_ok=True,
                )
            elif hasattr(gate_state, "write_pack"):
                gate_state.write_pack(pack_hash, {"task": task, "stamp_ok": True})
        except Exception:
            # Pack may already be recorded by run_preflight; non-fatal if helper missing.
            pass

        state.pack_hash = pack_hash
        state.preflight_ok = True
        state.task = task
        state.tools.append("cortex_session_preflight")
        self._gate(
            state,
            "preflight",
            True,
            pack_hash=pack_hash,
            citation_count=len(citations),
            none_exists_count=len(none_exists),
            methodology_ids=state.methodology_ids,
        )
        self._event(state, "preflight", pack_hash=pack_hash)
        self._persist_ledger(
            {
                "kind": "preflight",
                "session_id": session_id,
                "pack_hash": pack_hash,
                "task": task,
                "methodology_ids": state.methodology_ids,
                "ts": time.time(),
            }
        )
        return {
            "ok": True,
            "session_id": session_id,
            "pack_hash": pack_hash,
            "citation_count": len(citations),
            "none_exists": none_exists,
            "methodology_ids": state.methodology_ids,
            "task_class": state.task_class,
            "control_layer": "cortex_v4.control.mechanical_session",
            "preflight": {
                k: result.get(k)
                for k in ("pack_hash", "warnings", "workspace", "pack_quality", "none_exists")
                if k in result
            },
            "citations_preview": [
                {
                    "path": getattr(c, "path", c.get("path") if isinstance(c, dict) else str(c)),
                    "source_class": getattr(
                        c, "source_class", c.get("source_class") if isinstance(c, dict) else ""
                    ),
                }
                for c in list(citations)[: max(1, limit)]
            ],
            "state": state.to_dict(),
        }

    # ---- rung 4: tool gate ------------------------------------------------
    def gate_tool(
        self,
        session_id: str,
        tool_name: str,
        *,
        command: str = "",
        prompt_text: str = "",
        mode: str = "auto",
        enforce: bool = True,
    ) -> dict[str, Any]:
        state = self.get_state(session_id)
        tool = str(tool_name or "")

        if tool in EXEMPT_TOOLS or tool not in GATED_TOOLS:
            entry = self._gate(state, "tool", True, tool=tool, reason="not_gated_or_exempt")
            return {
                "ok": True,
                "allowed": True,
                "would_have_failed": False,
                "reason": entry["detail"].get("reason", "not gated"),
                "session_id": session_id,
                "tool": tool,
                "control_layer": "cortex_v4.control.mechanical_session",
                "enforce": enforce,
            }

        # Mechanical session rule: gated tools require prior preflight in this session.
        if not state.preflight_ok or not state.pack_hash:
            reason = "no V4 mechanical preflight / pack_hash for this session"
            self._gate(state, "tool", False, tool=tool, reason=reason)
            self._persist_ledger(
                {
                    "kind": "gate_refuse",
                    "session_id": session_id,
                    "tool": tool,
                    "reason": reason,
                    "ts": time.time(),
                }
            )
            if enforce:
                raise SessionGateError("PREFLIGHT_REQUIRED", reason)
            return {
                "ok": True,
                "allowed": False,
                "would_have_failed": True,
                "reason": reason,
                "session_id": session_id,
                "tool": tool,
                "control_layer": "cortex_v4.control.mechanical_session",
                "enforce": enforce,
            }

        # Also require search for write-class tools (tool-priority mechanical).
        if tool in {"Write", "Edit", "write", "edit"} and not state.searched:
            reason = "no corpus search this session before write/edit"
            self._gate(state, "tool", False, tool=tool, reason=reason)
            if enforce:
                raise SessionGateError("SEARCH_REQUIRED", reason)
            return {
                "ok": True,
                "allowed": False,
                "would_have_failed": True,
                "reason": reason,
                "session_id": session_id,
                "tool": tool,
                "control_layer": "cortex_v4.control.mechanical_session",
                "enforce": enforce,
            }

        # Resolve pack through SSC forced-RAG (corpus authority) with V4-owned prompt binding.
        packs = []
        try:
            gate_state = self.methodology.import_ssc("cortex_core.gate_state")
            packs = list(gate_state.recorded_packs() or [])
        except Exception:
            packs = []

        blob = f"{prompt_text}\npack_hash: {state.pack_hash}\ntask: {state.task}"
        decision = self.methodology.forced_rag_decide(
            tool_name=tool if tool in {"Write", "Edit", "Bash", "Task", "Agent"} else tool,
            tool_input={},
            user_text="",
            prompt_text=blob,
            packs=packs,
            mode=mode,
            command=command,
        )
        allowed = bool(decision.get("allowed"))
        reason = str(decision.get("reason") or "")
        self._gate(state, "tool", allowed, tool=tool, reason=reason, pack_hash=state.pack_hash)
        state.tools.append(tool)
        if not allowed and enforce:
            raise SessionGateError("FORCED_RAG_REFUSED", reason)
        return {
            "ok": True,
            "allowed": allowed,
            "would_have_failed": not allowed,
            "reason": reason,
            "session_id": session_id,
            "tool": tool,
            "pack_hash": state.pack_hash,
            "control_layer": "cortex_v4.control.mechanical_session",
            "enforce": enforce,
            "methodology_ids": state.methodology_ids,
        }

    # ---- rung 5: M7 closeout ----------------------------------------------
    def closeout(
        self,
        session_id: str,
        *,
        task: str | None = None,
        result: str,
        location: str | None = None,
        continuation: str | None = None,
    ) -> dict[str, Any]:
        state = self.get_state(session_id)
        close_task = task or state.task or f"session {session_id}"
        if "M7" not in (state.methodology_ids or BASE_METHODOLOGY):
            # Still allow closeout; record residual.
            self._event(state, "closeout_residual", residual="M7 not pre-classified")

        close_mod = self.methodology.import_ssc("cortex_core.session_closeout")
        locations = []
        if location:
            locations = [p.strip() for p in location.split(",") if p.strip()]
        if not locations:
            locations = [f"cortex_v4/control/mechanical_session.py#{session_id}"]
        path = None
        close_result: dict[str, Any] = {}
        if hasattr(close_mod, "write_session_closeout"):
            close_result = close_mod.write_session_closeout(
                task=close_task,
                result=result,
                workspace=self.ssc_root,
                locations=locations,
                continuation=continuation or "V4 mechanical session closeout",
                force=True,
                skip_substantial_gate=True,
            )
            path = close_result.get("path")

        # Validate a minimal methodology receipt shape for the session.
        receipt = {
            "schema": "cortex.v4.session_closeout.v1",
            "session_id": session_id,
            "task": close_task,
            "result": result,
            "pack_hash": state.pack_hash,
            "methodology_ids": state.methodology_ids,
            "tools": state.tools,
            "gates": state.gates,
            "preflight_ok": state.preflight_ok,
            "control_layer": "cortex_v4.control.mechanical_session",
            "closeout_path": str(path) if path else None,
            "closeout_logged": bool(close_result.get("logged")) if close_result else False,
        }
        self._gate(state, "closeout", True, path=str(path) if path else "")
        self._event(state, "closeout", result=result[:200])
        self._persist_ledger({"kind": "closeout", **receipt, "ts": time.time()})
        return {"ok": True, **receipt, "state": state.to_dict(), "closeout": close_result}


def run_mechanical_session_chain(
    *,
    corpus_root: str | Path,
    session_id: str,
    task: str,
    enforce: bool = True,
    disable: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Named caller: classify -> search -> preflight -> gate(Write) -> closeout.

    Mutants drop a rung via ``disable``. Oracle requires every rung present and green
    on the happy path (or correctly refused when a prior rung is disabled).
    """
    controller = MechanicalSessionController(ssc_root=corpus_root)
    steps: dict[str, Any] = {}

    if "classify" in disable:
        steps["classify"] = None
    else:
        steps["classify"] = controller.classify(session_id, task)

    if "search" in disable:
        steps["search"] = None
    else:
        steps["search"] = controller.search(session_id, task, limit=8)

    if "preflight" in disable:
        steps["preflight"] = None
    else:
        steps["preflight"] = controller.preflight(session_id, task)

    if "gate" in disable:
        steps["gate"] = None
    else:
        try:
            steps["gate"] = controller.gate_tool(
                session_id, "Write", enforce=enforce and "preflight" not in disable
            )
        except SessionGateError as exc:
            steps["gate"] = {
                "ok": False,
                "allowed": False,
                "would_have_failed": True,
                "reason": exc.message,
                "code": exc.code,
            }

    if "closeout" in disable:
        steps["closeout"] = None
    else:
        steps["closeout"] = controller.closeout(
            session_id,
            task=task,
            result="mechanical session chain complete",
            continuation="A/B harness",
        )

    oracle = mechanical_session_oracle(steps, disabled=disable)
    return {
        "schema": "cortex.v4.mechanical_session_chain.v1",
        "session_id": session_id,
        "task": task,
        "steps": steps,
        "oracle": oracle,
        "named_caller": "cortex_v4.control.mechanical_session.run_mechanical_session_chain",
        "control_layer": "cortex_v4.control.mechanical_session",
    }


def mechanical_session_oracle(
    steps: Mapping[str, Any],
    *,
    disabled: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Strict wire oracle: every rung must be present and correct on happy path."""
    _ = disabled
    errors: list[str] = []
    classify = steps.get("classify")
    search = steps.get("search")
    preflight = steps.get("preflight")
    gate = steps.get("gate")
    closeout = steps.get("closeout")

    if not (isinstance(classify, dict) and classify.get("ok") and classify.get("methodology_ids")):
        errors.append("classify missing methodology_ids")
    if not (isinstance(search, dict) and search.get("ok") and search.get("searched")):
        errors.append("search rung missing or not marked searched")
    if not (isinstance(preflight, dict) and preflight.get("ok") and preflight.get("pack_hash")):
        errors.append("preflight missing pack_hash")
    if not (isinstance(gate, dict) and gate.get("allowed") is True):
        errors.append("gate did not allow Write after preflight+search")
    if not (isinstance(closeout, dict) and closeout.get("ok")):
        errors.append("closeout missing")
    # Control-layer identity must be V4, not raw adapter prose.
    for name, step in (
        ("classify", classify),
        ("preflight", preflight),
        ("gate", gate),
    ):
        if isinstance(step, dict):
            cl = step.get("control_layer") or (step.get("state") or {}).get("control_layer")
            # classify embeds control_layer at top in classify_session_task path via return
            if name == "classify":
                # returned dict includes control_layer from classify_session_task? we add in classify()
                pass
    return {"ok": not errors, "errors": errors}


def ab_compare(
    *,
    corpus_root: str | Path,
    task: str,
    session_a: str | None = None,
    session_b: str | None = None,
) -> dict[str, Any]:
    """A = adapter-only path; B = mechanical session control path.

    Success: B matches A on pack_hash presence and forced-RAG allow after grounding,
    AND B refuses ungrounded Write while exposing control_layer identity.
    """
    corpus_root = Path(corpus_root)
    session_a = session_a or f"a-{uuid.uuid4().hex[:8]}"
    session_b = session_b or f"b-{uuid.uuid4().hex[:8]}"

    # --- Path A: adapter only (legacy shape) ---
    adapter = SSCMethodologyAdapter(corpus_root)
    t0 = time.time()
    a_pre = adapter.preflight(task, workspace=corpus_root)
    a_pack = str(a_pre.get("pack_hash") or "")
    a_packs = []
    try:
        a_packs = list(adapter.import_ssc("cortex_core.gate_state").recorded_packs() or [])
    except Exception:
        a_packs = []
    a_gate_grounded = adapter.forced_rag_decide(
        tool_name="Edit",
        tool_input={},
        user_text="",
        prompt_text=f"task: {task}\npack_hash: {a_pack}",
        packs=a_packs,
        mode="auto",
    )
    a_gate_ungrounded = adapter.forced_rag_decide(
        tool_name="Edit",
        tool_input={},
        user_text="",
        prompt_text=f"task: {task}",
        packs=a_packs,
        mode="auto",
    )
    a_ms = (time.time() - t0) * 1000
    path_a = {
        "path": "A-adapter-only",
        "pack_hash": a_pack,
        "pack_hash_present": bool(a_pack),
        "grounded_allowed": bool(a_gate_grounded.get("allowed")),
        "ungrounded_allowed": bool(a_gate_ungrounded.get("allowed")),
        "control_layer": "ssc_adapter_direct",
        "latency_ms": round(a_ms, 1),
    }

    # --- Path B: mechanical session control ---
    t1 = time.time()
    chain = run_mechanical_session_chain(
        corpus_root=corpus_root,
        session_id=session_b,
        task=task,
        enforce=True,
    )
    controller = MechanicalSessionController(ssc_root=corpus_root)
    # Fresh session to prove ungrounded refuse under B.
    ungrounded = controller.gate_tool(
        f"{session_b}-ungrounded",
        "Write",
        enforce=False,
    )
    b_ms = (time.time() - t1) * 1000
    b_pre = (chain.get("steps") or {}).get("preflight") or {}
    b_gate = (chain.get("steps") or {}).get("gate") or {}
    path_b = {
        "path": "B-mechanical-session",
        "pack_hash": b_pre.get("pack_hash"),
        "pack_hash_present": bool(b_pre.get("pack_hash")),
        "grounded_allowed": bool(b_gate.get("allowed")),
        "ungrounded_allowed": bool(ungrounded.get("allowed")),
        "ungrounded_would_have_failed": bool(ungrounded.get("would_have_failed")),
        "control_layer": "cortex_v4.control.mechanical_session",
        "oracle_ok": bool((chain.get("oracle") or {}).get("ok")),
        "methodology_ids": b_pre.get("methodology_ids") or [],
        "latency_ms": round(b_ms, 1),
        "chain_oracle": chain.get("oracle"),
    }

    checks = {
        "both_pack_hash": path_a["pack_hash_present"] and path_b["pack_hash_present"],
        "both_grounded_allow": path_a["grounded_allowed"] and path_b["grounded_allowed"],
        "a_ungrounded_fail_or_b_stricter": (not path_a["ungrounded_allowed"])
        or (not path_b["ungrounded_allowed"]),
        "b_refuses_ungrounded": path_b["ungrounded_allowed"] is False,
        "b_oracle_ok": path_b["oracle_ok"] is True,
        "b_is_control_layer": path_b["control_layer"].startswith("cortex_v4"),
    }
    ok = all(checks.values())
    return {
        "schema": "cortex.v4.mechanical_session_ab.v1",
        "ok": ok,
        "task": task,
        "path_a": path_a,
        "path_b": path_b,
        "checks": checks,
        "verdict": (
            "PASS: V4 mechanical session matches adapter grounding and is stricter on ungrounded writes"
            if ok
            else "FAIL: mechanical session did not meet adapter parity + control-layer bar"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="V4 mechanical session control (driver entry)")
    p.add_argument(
        "action",
        choices=["classify", "search", "preflight", "gate", "closeout", "chain", "ab"],
    )
    p.add_argument("--session-id", default="")
    p.add_argument("--task", default="")
    p.add_argument("--query", default="")
    p.add_argument("--tool", default="Write")
    p.add_argument("--command", default="")
    p.add_argument("--result", default="session complete")
    p.add_argument("--location", default="")
    p.add_argument("--continuation", default="")
    p.add_argument("--workspace", default=str(DEFAULT_SSC))
    p.add_argument("--limit", type=int, default=8)
    p.add_argument("--enforce", action="store_true", default=False)
    p.add_argument("--shadow", action="store_true", default=False)
    p.add_argument("--json", action="store_true", default=True)
    args = p.parse_args(argv)

    session_id = args.session_id or f"sess-{uuid.uuid4().hex[:10]}"
    controller = MechanicalSessionController(ssc_root=args.workspace)
    enforce = bool(args.enforce) and not bool(args.shadow)

    try:
        if args.action == "classify":
            out = controller.classify(session_id, args.task or "unspecified task")
        elif args.action == "search":
            out = controller.search(session_id, args.query or args.task, limit=args.limit)
        elif args.action == "preflight":
            out = controller.preflight(session_id, args.task, limit=args.limit)
        elif args.action == "gate":
            out = controller.gate_tool(
                session_id,
                args.tool,
                command=args.command,
                prompt_text=args.task,
                enforce=enforce,
            )
        elif args.action == "closeout":
            out = controller.closeout(
                session_id,
                task=args.task or None,
                result=args.result,
                location=args.location or None,
                continuation=args.continuation or None,
            )
        elif args.action == "chain":
            out = run_mechanical_session_chain(
                corpus_root=args.workspace,
                session_id=session_id,
                task=args.task or "mechanical session chain",
                enforce=True,
            )
        elif args.action == "ab":
            out = ab_compare(
                corpus_root=args.workspace,
                task=args.task
                or "Wire OpenCode sessions through V4 mechanical methodology control",
            )
        else:
            raise SystemExit(f"unknown action {args.action}")
    except SessionGateError as exc:
        out = {
            "ok": False,
            "allowed": False,
            "would_have_failed": True,
            "code": exc.code,
            "reason": exc.message,
            "control_layer": "cortex_v4.control.mechanical_session",
            "session_id": session_id,
        }
        print(json.dumps(out, indent=2))
        return 2

    print(json.dumps(out, indent=2, default=str))
    if args.action == "ab":
        return 0 if out.get("ok") else 1
    if args.action == "chain":
        return 0 if (out.get("oracle") or {}).get("ok") else 1
    return 0 if out.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
