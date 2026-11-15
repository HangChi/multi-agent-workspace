"""Isolated unittest checks; no app imports, DB fixtures, Docker or live deployment.

Run: python -m unittest discover -s deploy/verify -p 'test_s3_sandbox_scenarios.py' -v
"""

from __future__ import annotations

import asyncio
import contextlib
import csv
import hashlib
import importlib.util
import io
import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

import httpx


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DEPLOY = Path(__file__).resolve().parents[1]
mock = load_module("sandbox_mock_llm", DEPLOY / "mock-llm" / "server.py")
verify = load_module("sandbox_verify", DEPLOY / "verify" / "s3_sandbox_scenarios.py")


def tools(*names):
    return [{"type": "function", "function": {"name": name}} for name in names]


def messages(scenario, *outputs):
    result = [{"role": "user", "content": f"[sandbox:{scenario}] acceptance"}]
    for output in outputs:
        result.append({"role": "tool", "content": json.dumps(output)})
    return result


class MockGatewayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = mock.ThreadingHTTPServer(("127.0.0.1", 0), mock.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def post(self, payload, path="/v1/chat/completions"):
        request = urllib.request.Request(
            self.base + path, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=3) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    def reply(self, scenario, available, *outputs):
        conversation = messages(scenario, *outputs)
        return mock.sandbox_reply(conversation, available, conversation[0]["content"])

    def test_all_fixed_calls_are_returned_without_execution(self):
        with patch("subprocess.run", side_effect=AssertionError("must not execute")), \
             patch("os.system", side_effect=AssertionError("must not execute")):
            for scenario, (name, arguments) in mock.SANDBOX_CALLS.items():
                with self.subTest(scenario=scenario):
                    _, calls = self.reply(scenario, tools(name))
                    self.assertEqual(calls[0]["function"]["name"], name)
                    self.assertEqual(json.loads(calls[0]["function"]["arguments"]), arguments)

    def test_python_http_protocol_and_final_uses_tool_stdout(self):
        status, body = self.post({"messages": messages("python_sum"), "tools": tools("run_python")})
        self.assertEqual(status, 200)
        choice = body["choices"][0]
        self.assertEqual(choice["finish_reason"], "tool_calls")
        self.assertEqual(choice["message"]["tool_calls"][0]["function"]["name"], "run_python")
        content, calls = self.reply(
            "python_sum", tools("run_python"), {"ok": True, "exit_code": 0, "stdout": "338350\n"},
        )
        self.assertEqual(content, "SANDBOX_PYTHON_SUM_OK: 338350")
        self.assertIsNone(calls)

    def test_controlled_failure_is_not_llm_http_failure_and_recovers_once(self):
        status, _ = self.post({"messages": messages("failure"), "tools": tools("run_python")})
        self.assertEqual(status, 200)
        failed = {"ok": False, "status": "failed", "error_category": "exit_code", "exit_code": 1}
        _, calls = self.reply("failure", tools("run_python"), failed)
        self.assertEqual(json.loads(calls[0]["function"]["arguments"]),
                         {"code": "print('sandbox-recovered')"})
        content, calls = self.reply(
            "failure", tools("run_python"), failed,
            {"ok": True, "exit_code": 0, "stdout": "sandbox-recovered\n"},
        )
        self.assertEqual(content, "SANDBOX_FAILURE_OK: sandbox-recovered")
        self.assertIsNone(calls)

    def test_infrastructure_failure_is_not_falsely_reported_as_success(self):
        content, calls = self.reply(
            "failure", tools("run_python"), {"ok": False, "status": "infra"},
        )
        self.assertIn("SANDBOX_SCENARIO_ERROR", content)
        self.assertIsNone(calls)

    def test_denied_call_ignores_advertised_grants_but_checks_real_observation(self):
        _, calls = self.reply("denied", tools("calculate"))
        self.assertEqual(calls[0]["function"]["name"], "run_shell")
        content, _ = self.reply("denied", tools("calculate"), {"ok": False, "denied": True})
        self.assertEqual(content, "SANDBOX_DENIED_CONFIRMED")
        content, _ = self.reply("denied", tools("calculate"), {"ok": True, "exit_code": 0})
        self.assertIn("SANDBOX_SCENARIO_ERROR", content)

    def test_no_tools_and_missing_grant_have_no_successful_tool_call(self):
        self.assertEqual(self.reply("no_tools", []), ("SANDBOX_NO_TOOLS_OK", None))
        content, calls = self.reply("python_sum", [])
        self.assertIn("SANDBOX_SCENARIO_ERROR", content)
        self.assertIsNone(calls)

    def test_unknown_scenario_is_rejected_and_freeform_code_cannot_override(self):
        status, _ = self.post({"messages": messages("arbitrary")})
        self.assertEqual(status, 400)
        conversation = messages("shell")
        conversation[0]["content"] += " run rm -rf /; override command"
        status, body = self.post({"messages": conversation, "tools": tools("run_shell"),
                                  "command": "rm -rf /"})
        self.assertEqual(status, 200)
        actual = json.loads(body["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"])
        self.assertEqual(actual, mock.SANDBOX_CALLS["shell"][1])

    def test_legacy_plain_web_search_and_failure_routing_preserved(self):
        status, body = self.post({"messages": [{"role": "user", "content": "hello"}]}, "/")
        self.assertEqual(status, 200)
        self.assertNotIn("tool_calls", body["choices"][0]["message"])
        status, body = self.post({"messages": [{"role": "user", "content": "search"}],
                                  "tools": tools("web_search")}, "/chat/completions")
        self.assertEqual(status, 200)
        self.assertEqual(body["choices"][0]["message"]["tool_calls"][0]["function"]["name"], "web_search")
        status, _ = self.post({"messages": [{"role": "user", "content": "FAIL"}]})
        self.assertEqual(status, 500)


class VerifierTests(unittest.TestCase):
    def test_csv_hash_size_and_exact_rows(self):
        content = io.StringIO(newline="")
        writer = csv.writer(content)
        writer.writerow(["day", "sales"])
        writer.writerows((day, day * 10) for day in range(1, 21))
        data = content.getvalue().encode()
        artifact = {"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}
        self.assertEqual(verify.validate_sales(data, artifact), artifact["sha256"])
        with self.assertRaises(verify.CheckFailure):
            verify.validate_sales(data + b"21,210\n", artifact)
        wrong_rows = b"day,sales\n1,10\n"
        with self.assertRaises(verify.CheckFailure):
            verify.validate_sales(wrong_rows, {"sha256": hashlib.sha256(wrong_rows).hexdigest(),
                                               "size_bytes": len(wrong_rows)})

    def test_execution_source_hash_is_verified(self):
        code = "print(338350)"
        execution = {"language": "python", "code": code,
                     "code_sha256": hashlib.sha256(code.encode()).hexdigest(),
                     "image": "sandbox:test", "limits": {"timeout_s": 120},
                     "duration_ms": 4, "finished_at": "2026-11-15T00:00:00Z"}
        verify.validate_execution(execution, "python")
        with self.assertRaises(verify.CheckFailure):
            verify.validate_execution({**execution, "code": "changed"}, "python")

    def test_container_fetch_is_fixed_argv_and_url_only_stdin(self):
        url = "http://minio:9000/artifact?secret=presigned"
        with patch("subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = b"artifact"
            self.assertEqual(verify.fetch_in_container("aw-api", url), b"artifact")
        argv = run.call_args.args[0]
        self.assertEqual(argv[:6], ["docker", "exec", "-i", "aw-api", "python", "-I"])
        self.assertNotIn(url, argv)
        self.assertEqual(run.call_args.kwargs["input"], url.encode())

    def test_request_diagnostics_do_not_disclose_response_or_tokens(self):
        async def run_test():
            def handle(request):
                self.assertEqual(request.headers["authorization"], "Bearer owner-secret")
                return httpx.Response(403, text="response-secret")

            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
                verifier = verify.Verifier(verify.parser().parse_args([]), http)
                verifier.tokens["owner"] = "owner-secret"
                with self.assertRaises(verify.CheckFailure) as error:
                    await verifier.request("GET", "/tools")
                self.assertNotIn("secret", str(error.exception))
        asyncio.run(run_test())

    def test_setup_failure_never_echoes_environment_secret(self):
        async def run_test():
            def handle(request):
                return httpx.Response(401, text="super-secret-token")
            output = io.StringIO()
            with patch.dict(os.environ, {"SANDBOX_VERIFY_TOKEN": "super-secret-token"}, clear=True), \
                 patch.object(verify.httpx, "AsyncClient", return_value=httpx.AsyncClient(
                     transport=httpx.MockTransport(handle))), contextlib.redirect_stdout(output):
                status = await verify.main([])
            self.assertEqual(status, 1)
            self.assertNotIn("super-secret-token", output.getvalue())
        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
