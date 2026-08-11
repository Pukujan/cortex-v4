"""Persistent OpenCode server execution boundary for Cortex V4.

This adapter deliberately models a long-running coding task as a durable OpenCode
session, not as one long-lived ``opencode run`` subprocess.  The caller only makes
short control-plane HTTP requests while OpenCode owns the model/tool loop.

No SSC imports are allowed in this module.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterable


class OpenCodeServerError(RuntimeError):
    """Raised when the OpenCode control plane returns an invalid response."""


@dataclass(frozen=True)
class OpenCodeAgentSpec:
    role: str
    prompt: str
    provider_id: str
    model_id: str
    agent: str = "build"
    title: str | None = None


@dataclass(frozen=True)
class OpenCodeRunHandle:
    session_id: str
    role: str
    provider_id: str
    model_id: str


class OpenCodeServerClient:
    """Small stdlib-only client for a persistent ``opencode serve`` instance.

    ``request_timeout_s`` is only for individual control-plane HTTP requests. It is
    intentionally *not* the lifetime of an agent task. Long tasks are dispatched via
    ``prompt_async`` and observed through status/messages/diff endpoints.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:4096",
        *,
        request_timeout_s: float = 15.0,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.request_timeout_s = float(request_timeout_s)
        self.username = username
        self.password = password

    def _request(
        self,
        method: str,
        path: str,
        payload: Any | None = None,
        *,
        query: dict[str, str | int] | None = None,
    ) -> Any:
        url = self.base_url + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.username is not None and self.password is not None:
            import base64

            token = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
            headers["Authorization"] = f"Basic {token}"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.request_timeout_s) as response:
                body = response.read()
                if response.status == 204 or not body:
                    return None
                return json.loads(body.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise OpenCodeServerError(
                f"OpenCode {method} {path} failed with HTTP {exc.code}: {body[:800]}"
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise OpenCodeServerError(f"OpenCode {method} {path} failed: {exc}") from exc

    def health(self) -> dict[str, Any]:
        result = self._request("GET", "/global/health")
        if not isinstance(result, dict) or not result.get("healthy"):
            raise OpenCodeServerError(f"unhealthy OpenCode server: {result!r}")
        return result

    def create_session(self, *, title: str | None = None) -> str:
        payload: dict[str, Any] = {}
        if title:
            payload["title"] = title
        result = self._request("POST", "/session", payload)
        if not isinstance(result, dict) or not result.get("id"):
            raise OpenCodeServerError(f"session create returned no id: {result!r}")
        return str(result["id"])

    def prompt_async(
        self,
        session_id: str,
        prompt: str,
        *,
        provider_id: str,
        model_id: str,
        agent: str = "build",
        system: str | None = None,
    ) -> None:
        body: dict[str, Any] = {
            "model": {"providerID": provider_id, "modelID": model_id},
            "agent": agent,
            "parts": [{"type": "text", "text": prompt}],
        }
        if system:
            body["system"] = system
        self._request("POST", f"/session/{session_id}/prompt_async", body)

    def session_status(self, session_id: str) -> dict[str, Any]:
        result = self._request("GET", "/session/status")
        if not isinstance(result, dict):
            raise OpenCodeServerError(f"invalid status response: {result!r}")
        status = result.get(session_id)
        # OpenCode deletes idle sessions from its in-memory status map, so absence is idle.
        return status if isinstance(status, dict) else {"type": "idle"}

    def messages(self, session_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        result = self._request(
            "GET", f"/session/{session_id}/message", query={"limit": int(limit)}
        )
        if not isinstance(result, list):
            raise OpenCodeServerError(f"invalid message response: {result!r}")
        return [row for row in result if isinstance(row, dict)]

    def diff(self, session_id: str) -> list[dict[str, Any]]:
        result = self._request("GET", f"/session/{session_id}/diff")
        if not isinstance(result, list):
            raise OpenCodeServerError(f"invalid diff response: {result!r}")
        return [row for row in result if isinstance(row, dict)]

    def abort(self, session_id: str) -> bool:
        result = self._request("POST", f"/session/{session_id}/abort", {})
        return bool(result)

    def summon(self, spec: OpenCodeAgentSpec) -> OpenCodeRunHandle:
        """Create one durable OpenCode session and dispatch work asynchronously."""
        session_id = self.create_session(title=spec.title or f"v4:{spec.role}")
        self.prompt_async(
            session_id,
            spec.prompt,
            provider_id=spec.provider_id,
            model_id=spec.model_id,
            agent=spec.agent,
        )
        return OpenCodeRunHandle(
            session_id=session_id,
            role=spec.role,
            provider_id=spec.provider_id,
            model_id=spec.model_id,
        )

    def summon_many(self, specs: Iterable[OpenCodeAgentSpec]) -> list[OpenCodeRunHandle]:
        """Dispatch independent agents without coupling their lifetime to the caller."""
        return [self.summon(spec) for spec in specs]

    @staticmethod
    def _last_completed_assistant(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
        for row in reversed(messages):
            info = row.get("info")
            if not isinstance(info, dict) or info.get("role") != "assistant":
                continue
            timing = info.get("time")
            if isinstance(timing, dict) and timing.get("completed") is not None:
                return row
        return None

    def wait_for_completion(
        self,
        session_id: str,
        *,
        poll_interval_s: float = 1.0,
        overall_timeout_s: float | None = None,
        stale_busy_polls: int = 5,
    ) -> dict[str, Any]:
        """Wait for a durable session, tolerating OpenCode's known stale-busy window.

        There is deliberately no short default whole-task timeout. Callers that need an
        SLA may pass ``overall_timeout_s`` explicitly. Completion is confirmed by idle
        status, or by a completed assistant message that remains present across several
        busy polls (OpenCode status can lag message persistence).
        """
        started = time.monotonic()
        busy_with_completed = 0
        while True:
            if overall_timeout_s is not None and time.monotonic() - started > overall_timeout_s:
                raise TimeoutError(
                    f"OpenCode session {session_id} exceeded explicit overall timeout "
                    f"of {overall_timeout_s}s"
                )

            status = self.session_status(session_id)
            kind = str(status.get("type") or "idle")
            current_messages = self.messages(session_id)
            completed = self._last_completed_assistant(current_messages)

            if kind == "idle":
                return {
                    "session_id": session_id,
                    "status": status,
                    "messages": current_messages,
                    "diff": self.diff(session_id),
                    "completion_source": "status_idle",
                }

            if kind == "busy" and completed is not None:
                busy_with_completed += 1
                if busy_with_completed >= int(stale_busy_polls):
                    return {
                        "session_id": session_id,
                        "status": status,
                        "messages": current_messages,
                        "diff": self.diff(session_id),
                        "completion_source": "completed_message_stale_busy",
                    }
            else:
                busy_with_completed = 0

            time.sleep(float(poll_interval_s))
