from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cortex_v4.execution.opencode_server import OpenCodeAgentSpec, OpenCodeServerClient


class _State:
    next_id = 1
    prompts: list[dict] = []
    message_reads = 0


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def _json(self, status: int, value) -> None:
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path == "/global/health":
            return self._json(200, {"healthy": True, "version": "test"})
        if self.path == "/session/status":
            # Deliberately remain busy to exercise the stale-busy completion fallback.
            return self._json(200, {"ses-1": {"type": "busy"}})
        if self.path.startswith("/session/ses-1/message"):
            _State.message_reads += 1
            if _State.message_reads < 2:
                return self._json(200, [])
            return self._json(
                200,
                [
                    {
                        "info": {
                            "id": "msg-1",
                            "role": "assistant",
                            "time": {"created": 1, "completed": 2},
                        },
                        "parts": [{"type": "text", "text": "done"}],
                    }
                ],
            )
        if self.path == "/session/ses-1/diff":
            return self._json(200, [{"file": "result.txt"}])
        return self._json(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/session":
            session_id = f"ses-{_State.next_id}"
            _State.next_id += 1
            return self._json(200, {"id": session_id, "title": payload.get("title")})
        if self.path.endswith("/prompt_async"):
            _State.prompts.append(payload)
            self.send_response(204)
            self.end_headers()
            return
        if self.path.endswith("/abort"):
            return self._json(200, True)
        return self._json(404, {"error": "not found"})


class OpenCodeServerClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _State.next_id = 1
        _State.prompts = []
        _State.message_reads = 0
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.client = OpenCodeServerClient(
            f"http://127.0.0.1:{cls.server.server_address[1]}", request_timeout_s=2
        )

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(timeout=2)
        cls.server.server_close()

    def test_health_and_async_summon(self):
        self.assertTrue(self.client.health()["healthy"])
        handle = self.client.summon(
            OpenCodeAgentSpec(
                role="builder",
                prompt="write result.txt",
                provider_id="litellm",
                model_id="ckff-model",
            )
        )
        self.assertEqual(handle.session_id, "ses-1")
        self.assertEqual(_State.prompts[-1]["model"]["providerID"], "litellm")
        self.assertEqual(_State.prompts[-1]["model"]["modelID"], "ckff-model")
        self.assertEqual(_State.prompts[-1]["parts"][0]["text"], "write result.txt")

    def test_wait_tolerates_stale_busy_after_completed_message(self):
        result = self.client.wait_for_completion(
            "ses-1", poll_interval_s=0.01, overall_timeout_s=1, stale_busy_polls=2
        )
        self.assertEqual(result["completion_source"], "completed_message_stale_busy")
        self.assertEqual(result["diff"], [{"file": "result.txt"}])

    def test_summon_many_uses_independent_sessions(self):
        handles = self.client.summon_many(
            [
                OpenCodeAgentSpec("a", "task a", "litellm", "m1"),
                OpenCodeAgentSpec("b", "task b", "litellm", "m2"),
            ]
        )
        self.assertEqual([h.session_id for h in handles], ["ses-2", "ses-3"])
        self.assertEqual([h.role for h in handles], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
