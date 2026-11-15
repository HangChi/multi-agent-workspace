"""Pure harness controls. unittest discovery deliberately avoids backend conftest/DB.

Run: python -m unittest discover -s deploy/verify -p 'test_*.py' -v
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import full_stack_acceptance as full
import httpx
import native_acceptance as native
from test_s3_sandbox_scenarios import mock, tools


class PerformanceControlTests(unittest.TestCase):
    def test_nearest_rank_not_mean_or_interpolated_percentile(self):
        self.assertEqual(full.percentile95(list(range(1, 101))), 95)
        self.assertEqual(full.percentile95([1] * 19 + [100]), 1)
        self.assertEqual(full.percentile95([1] * 18 + [100, 101]), 100)

    def test_exact_twenty_percent_boundary_is_not_loosened(self):
        self.assertTrue(full.performance_verdict([10] * 100, [12] * 100, 0)["passed"])
        self.assertFalse(full.performance_verdict([10] * 100, [12.00001] * 100, 0)["passed"])

    def test_failed_request_cannot_be_excluded_from_verdict(self):
        result = full.performance_verdict([10] * 100, [9] * 100, 1)
        self.assertFalse(result["passed"])
        self.assertEqual(result["request_failures"], 1)

    def test_outliers_and_raw_measurements_are_preserved(self):
        loaded = [10] * 94 + [1000] * 6
        result = full.performance_verdict([10] * 100, loaded, 0)
        self.assertFalse(result["passed"])
        self.assertEqual(result["load_samples_ms"], loaded)
        self.assertEqual(result["load_p95_ms"], 1000)

    def test_empty_nan_infinite_and_negative_samples_fail_closed(self):
        for values in ([], [float("nan")], [float("inf")], [-1]):
            with self.subTest(values=values), self.assertRaises(full.CheckFailure):
                full.percentile95(values)

    def test_execution_window_overlap_has_real_timestamps(self):
        samples = [
            {"created_at": "2026-11-15T00:00:00Z", "finished_at": "2026-11-15T00:00:03Z"},
            {"created_at": "2026-11-15T00:00:01Z", "finished_at": "2026-11-15T00:00:04Z"},
            {"created_at": "2026-11-15T00:00:04Z", "finished_at": "2026-11-15T00:00:05Z"},
        ]
        self.assertEqual(full.interval_overlap(samples), 2)


class GatewayControlTests(unittest.TestCase):
    def reply(self, scenario, output, available=None):
        messages = [{"role": "user", "content": f"[sandbox:{scenario}]"},
                    {"role": "tool", "content": json.dumps(output)}]
        return mock.sandbox_reply(messages, available or tools("run_python"), messages[0]["content"])

    def test_only_exact_advertised_singleton_is_accepted(self):
        messages = [{"role": "user", "content": "[sandbox:python_sum]"}]
        text, calls = mock.sandbox_reply(messages, tools("run_python", "run_shell"), messages[0]["content"])
        self.assertIn("SANDBOX_SCENARIO_ERROR", text)
        self.assertIsNone(calls)

    def test_failure_classification_is_observed_not_assumed(self):
        for category in ("timeout", "oom", "pids", "disk", "policy", "infra"):
            with self.subTest(category=category):
                text, _ = self.reply(category, {"ok": False, "error_category": category})
                self.assertIn(f"SANDBOX_{category.upper()}_OK", text)
                text, _ = self.reply(category, {"ok": True, "exit_code": 0})
                self.assertIn("SANDBOX_SCENARIO_ERROR", text)

    def test_builtins_require_actual_result_not_just_ok_flag(self):
        for scenario in ("calculate", "web_search", "read_url"):
            text, _ = self.reply(scenario, {"ok": True}, tools(scenario))
            self.assertIn("SANDBOX_SCENARIO_ERROR", text)
        text, _ = self.reply("calculate", {"result": 338350}, tools("calculate"))
        self.assertIn("SANDBOX_CALCULATE_OK", text)

    def test_denial_threshold_mock_remains_adversarial_after_every_refusal(self):
        for count in range(6):
            messages = [{"role": "user", "content": "[sandbox:denied_threshold]"}]
            messages.extend({"role": "tool", "content": '{"ok":false,"denied":true}'} for _ in range(count))
            _, calls = mock.sandbox_reply(messages, tools("calculate"), messages[0]["content"])
            self.assertEqual(calls[0]["function"]["name"], "run_shell")

    def test_large_stdout_cannot_make_unbounded_final_answer(self):
        text, _ = self.reply("output", {"ok": True, "exit_code": 0, "stdout": "X" * (3 * 1024 * 1024)})
        self.assertLess(len(text), 512)


class AcceptanceHarnessTests(unittest.TestCase):
    def socket(self, events):
        ws = AsyncMock()
        ws.recv.side_effect = [json.dumps(event) for event in events]
        connection = MagicMock()
        connection.__aenter__ = AsyncMock(return_value=ws)
        connection.__aexit__ = AsyncMock(return_value=False)
        return connection

    def verifier(self, http):
        args = full.parser().parse_args([])
        verifier = full.FullVerifier(args, http)
        verifier.tokens = {"owner": "owner-secret", "admin": "admin-secret", "other": "other-secret"}
        return verifier

    def test_real_login_token_pair_contract_and_member_roles(self):
        async def check():
            def handle(request):
                path = request.url.path
                if path.endswith('/auth/login'):
                    identity = json.loads(request.content)["email"].split('@')[0]
                    self.assertEqual(json.loads(request.content)["password"], "ci-only-secret")
                    return httpx.Response(200, json={"tokens": {"access": identity + '-token'}})
                if path.endswith('/auth/me'):
                    identity = request.headers['authorization'].removeprefix('Bearer ').removesuffix('-token')
                    return httpx.Response(200, json={"id": identity, "role": 'admin' if identity == 'admin' else 'member'})
                if 'authorization' not in request.headers:
                    return httpx.Response(401)
                return httpx.Response(200, json={"items": [{"name": name} for name in
                                                           ('calculate', 'web_search', 'read_url', 'run_python', 'run_shell')]})
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
                verifier = self.verifier(client)
                with patch.dict(os.environ, {"AW_VERIFY_CI": "1", "AW_VERIFY_USER_PASSWORD": "ci-only-secret"}, clear=True):
                    await verifier.setup()
                    self.assertEqual(verifier.tokens['owner'], 'owner-token')
                    self.assertEqual(verifier.tokens['admin'], 'admin-token')
        asyncio.run(check())

    def test_denial_count_missing_audit_and_false_ok_each_fail(self):
        async def check(count=5, audit_count=5, denied=True):
            async with httpx.AsyncClient() as client:
                verifier = self.verifier(client)
                summaries = [{"seq": index + 1, "kind": "tool", "tool_name": "run_shell", "status": "failed"}
                             for index in range(count)]
                run = {"id": "run-id", "executions": [], "artifacts": [], "error": "threshold reached",
                       "steps": summaries}
                verifier.create_run = AsyncMock(return_value='run-id')
                verifier.wait_run = AsyncMock(return_value=run)
                verifier.audit = AsyncMock(return_value=[{}] * audit_count)
                events = [{"type": "ready"}] + [{"type": "step_update", "step": {
                    **step, "output": {"denied": denied, "ok": False}}} for step in summaries]
                events.append({"type": "run_failed"})
                with patch.object(full, "connect", return_value=self.socket(events)):
                    await verifier.denial_threshold()
        asyncio.run(check())
        for args in ((4, 5, True), (6, 5, True), (5, 4, True), (5, 5, False)):
            with self.subTest(args=args), self.assertRaises(full.CheckFailure):
                asyncio.run(check(*args))

    def test_ws_url_uses_root_route_and_encoded_token(self):
        async def check():
            async with httpx.AsyncClient() as client:
                verifier = self.verifier(client)
                verifier.base = 'https://ci.example.com'
                verifier.tokens['owner'] = 'token&must=stay?encoded'
                url = full.urlsplit(verifier.ws_url('run-id'))
                self.assertEqual(url.scheme, 'wss')
                self.assertEqual(url.path, '/ws/runs/run-id')
                self.assertEqual(url.query, 'token=token%26must%3Dstay%3Fencoded')
        asyncio.run(check())

    def test_builtins_exact_results_come_from_ws_not_summary(self):
        async def check(calculation=338350):
            async with httpx.AsyncClient() as client:
                verifier = self.verifier(client)
                async def execute(scenario, grants):
                    output = {'calculate': {'result': calculation},
                              'web_search': {'results': [{'title': '002 controlled search result'}]},
                              'read_url': {'status': 200, 'text': 'Example Domain'}}[scenario]
                    step = {'seq': 2, 'kind': 'tool', 'tool_name': scenario, 'status': 'completed'}
                    run = {'id': scenario, 'steps': [step], 'executions': [],
                           'result': {'answer': f'SANDBOX_{scenario.upper()}_OK'}}
                    verifier.observe_tool_event(scenario, {'type': 'step_update', 'step': {**step, 'output': output}})
                    return run
                verifier.execute = execute
                await verifier.builtins()
        asyncio.run(check())
        for wrong in (338349, True, None, '338350'):
            with self.subTest(wrong=wrong), self.assertRaises(full.CheckFailure):
                asyncio.run(check(wrong))

    def test_failed_execution_observes_explicit_false_even_with_summary_only_steps(self):
        async def check(ok=False):
            async with httpx.AsyncClient() as client:
                verifier = self.verifier(client)
                step = {'seq': 2, 'kind': 'tool', 'tool_name': 'run_python', 'status': 'failed'}
                run = {'id': 'run-id', 'steps': [step]}
                execution = {'id': 'execution-id', 'status': 'infra', 'exit_code': None,
                             'error_category': 'infra', 'error_message': '创建沙箱网络失败: secret-token',
                             'duration_ms': 12}
                verifier.executions = AsyncMock(return_value=[execution])
                verifier.audit = AsyncMock(return_value=[{}])
                verifier.observe_tool_event('run-id', {'type': 'step_update', 'step': {**step, 'output': {'ok': ok}}})
                await verifier.checked_execution(run, category='infra')
                self.assertNotIn('secret-token', json.dumps(verifier.evidence))
        asyncio.run(check())
        for wrong in (True, None, 0, ''):
            with self.subTest(wrong=wrong), self.assertRaises(full.CheckFailure):
                asyncio.run(check(wrong))

    def test_sleep_load_path_never_adds_ws_or_observer_delay(self):
        async def check():
            async with httpx.AsyncClient() as client:
                verifier = self.verifier(client)
                verifier.create_run = AsyncMock(return_value='run-id')
                verifier.wait_run = AsyncMock(return_value={'id': 'run-id'})
                verifier.observed_run = AsyncMock(side_effect=AssertionError('S6 must remain HTTP only'))
                self.assertEqual(await verifier.execute('sleep', ['run_python']), {'id': 'run-id'})
        asyncio.run(check())

    def test_cross_owner_ws_requires_403_not_server_failure(self):
        async def check(status):
            async with httpx.AsyncClient() as client:
                verifier = self.verifier(client)
                verifier.create_run = AsyncMock(return_value='run-id')
                verifier.wait_run = AsyncMock(return_value={'id': 'run-id'})
                verifier.checked_execution = AsyncMock()
                allowed = self.socket([{'type': 'ready'}, {'type': 'sandbox_started'},
                                       {'type': 'sandbox_output', 'chunk': 'real'}, {'type': 'sandbox_finished'}])
                denied = self.socket([])
                denied.__aenter__.side_effect = full.InvalidStatus(SimpleNamespace(status_code=status, reason_phrase='denied'))
                with patch.object(full, 'connect', side_effect=[allowed, denied]):
                    await verifier.websocket()
        asyncio.run(check(403))
        for wrong in (404, 500, 502):
            with self.subTest(status=wrong), self.assertRaises(full.CheckFailure):
                asyncio.run(check(wrong))

    def test_cancel_waits_for_real_failed_tool_result_after_sandbox_finished(self):
        async def check():
            async with httpx.AsyncClient() as client:
                verifier = self.verifier(client)
                step = {'seq': 2, 'kind': 'tool', 'tool_name': 'run_python', 'status': 'failed'}
                run = {'id': 'run-id', 'steps': [step]}
                verifier.create_run = AsyncMock(return_value='run-id')
                verifier.wait_run = AsyncMock(return_value=run)
                verifier.request = AsyncMock()
                verifier.audit = AsyncMock(return_value=[{}])
                verifier.executions = AsyncMock(return_value=[{
                    'id': 'execution-id', 'status': 'cancelled', 'error_category': 'cancelled',
                    'error_message': '已取消', 'exit_code': None, 'duration_ms': 42}])
                events = [{'type': 'ready'}, {'type': 'sandbox_output', 'chunk': 'cancel-started'},
                          {'type': 'sandbox_finished'}, {'type': 'step_update', 'step': {
                              **step, 'status': 'completed', 'output': {'ok': False}}}]
                with patch.object(full, 'connect', return_value=self.socket(events)):
                    await verifier.cancellation()
                self.assertEqual(verifier.tool_steps(run)[0]['output'], {'ok': False})
        asyncio.run(check())

    def test_diagnostic_execution_facts_are_finite_and_drop_raw_error_and_output(self):
        async def check():
            run_id, execution_id = '43178b0f-8d83-46ec-ad3b-afd26ed5fd1a', 'a3e4d9ca-bf99-4670-8c03-145a41285289'
            def handle(request):
                if '/executions/' in request.url.path:
                    return httpx.Response(200, json={'id': execution_id, 'status': 'infra', 'duration_ms': 21,
                        'error_category': 'infra', 'stdout': 'Bearer SECRET', 'stderr': 'X-Amz-Signature=SECRET',
                        'error_message': '创建沙箱网络失败: IPv6 pool is not available SECRET',
                        'code': 'SECRET', 'mem_peak_bytes': 'SECRET'})
                return httpx.Response(200, json={'id': run_id, 'status': 'completed', 'steps': [],
                    'executions': [{'id': execution_id}], 'error': 'SECRET'})
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
                facts = await self.verifier(client).diagnostic_facts([run_id])
                self.assertNotIn('SECRET', json.dumps(facts))
                execution = facts[0]['executions'][0]
                self.assertEqual(execution['error_stage'], 'network_create')
                self.assertEqual(execution['error_code'], 'ipv6_pool_unavailable')
                self.assertEqual(execution['duration_ms'], 21)
        asyncio.run(check())

    def test_setup_failure_report_never_echoes_credentials(self):
        async def check(directory):
            path = Path(directory) / 'safe.json'
            with patch.object(full.FullVerifier, 'setup', side_effect=RuntimeError('secret-token-and-password')):
                status = await full.main(['--output', str(path)])
            self.assertEqual(status, 1)
            self.assertNotIn('secret-token-and-password', path.read_text())
            self.assertEqual(json.loads(path.read_text())['results'][0]['error_type'], 'RuntimeError')
        with tempfile.TemporaryDirectory() as directory:
            asyncio.run(check(directory))


class NativeIsolationTests(unittest.TestCase):
    def test_both_native_bridge_filter_families_must_be_enabled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ipv4, ipv6 = root / 'bridge-nf-call-iptables', root / 'bridge-nf-call-ip6tables'
            for value4, value6 in ((None, None), ('1', None), ('1', '0'), ('0', '1'), ('1', '1')):
                for path, value in ((ipv4, value4), (ipv6, value6)):
                    if value is None:
                        path.unlink(missing_ok=True)
                    else:
                        path.write_text(value + '\n')
                if value4 == value6 == '1':
                    self.assertEqual(native.bridge_filtering_status(root), {
                        'bridge-nf-call-iptables': 1, 'bridge-nf-call-ip6tables': 1,
                    })
                else:
                    with self.assertRaises(native.AcceptanceFailure):
                        native.bridge_filtering_status(root)

    def test_project_name_requires_numeric_ci_run_and_attempt(self):
        self.assertEqual(native.project_name({'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '2'}), 'aw002ci-123-2')
        for environ in ({}, {'GITHUB_RUN_ID': '../aw', 'GITHUB_RUN_ATTEMPT': '1'},
                        {'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '$(touch bad)'}):
            with self.assertRaises(native.AcceptanceFailure):
                native.project_name(environ)

    def test_native_entrypoint_refuses_local_machine_before_docker_or_bootstrap(self):
        with patch.object(native.platform, 'system', return_value='Darwin'), \
                patch.object(native.docker, 'from_env') as client, \
                self.assertRaises(native.AcceptanceFailure):
            native.NativeRunner(Path('/tmp/not-created-by-unit-test'))
        client.assert_not_called()

    def test_runtime_report_drops_output_code_urls_and_exception_secrets(self):
        report = native.sanitized_runtime({'suite': 'controlled', 'results': [{
            'name': 'controlled case', 'passed': False, 'error': 'secret-top-level',
            'runtime': {'status': 'infra', 'stdout': 'secret-stdout', 'code': 'secret-code',
                        'error': 'secret-DSN', 'url': 'http://signed?secret', 'residual': {'containers': 0}}
        }]})
        self.assertNotIn('secret', json.dumps(report))
        self.assertEqual(report['results'][0]['status'], 'infra')

    def test_runtime_failure_evidence_preserves_only_finite_values(self):
        runtime = {'error_stage': 'network_create', 'error_code': 'ipv6_pool_unavailable',
                   'error_type': 'RuntimeError', 'error_chain': ['RuntimeError', 'APIError'],
                   'daemon_status': 500, 'error': 'SECRET', 'stderr': 'SECRET'}
        def report(value):
            return native.sanitized_runtime({'results': [{'name': 'case', 'passed': False, 'runtime': value}]})
        result = report(runtime)['results'][0]
        self.assertNotIn('SECRET', json.dumps(result))
        for key in ('error_stage', 'error_code', 'error_type', 'error_chain', 'daemon_status'):
            self.assertEqual(result[key], runtime[key])
        for key, value in (('error_stage', 'SECRET'), ('error_code', 'SECRET'),
                           ('error_type', 'SECRET'), ('error_chain', ['SECRET']),
                           ('error_chain', ['APIError'] * 9), ('daemon_status', 600),
                           ('daemon_status', 'SECRET')):
            with self.subTest(key=key, value=value), self.assertRaises(native.AcceptanceFailure):
                report({**runtime, key: value})

    def test_empty_or_implicit_runtime_success_is_rejected(self):
        for report in ({}, {'results': []}, {'results': [{'name': 'missing pass'}]}):
            with self.assertRaises(native.AcceptanceFailure):
                native.sanitized_runtime(report)

    def test_file_upload_guard_rejects_secrets_and_signed_urls(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = object.__new__(native.NativeRunner)
            runner.output = Path(directory)
            runner.env = {'AW_VERIFY_PASSWORD': 'generated-ci-password'}
            for data in ({'bad': 'generated-ci-password'}, {'bad': 'Bearer token'}, {'bad': 'X-Amz-Signature=x'}):
                with self.assertRaises(native.AcceptanceFailure):
                    runner.write_json('result.json', data)
            runner.write_json('result.json', {'passed': True})
            self.assertEqual((Path(directory) / 'result.json').stat().st_mode & 0o777, 0o600)

    def test_minimum_required_case_counts_prevent_reduced_suite_false_green(self):
        for suite, required in (("native_io", 2), ("native_escape_matrix", 13),
                                ("native_egress_ip_ipv6", 12), ("native_egress_domains", 18)):
            for count in (required - 1, required):
                with self.subTest(suite=suite, count=count), tempfile.TemporaryDirectory() as directory:
                    runner = object.__new__(native.NativeRunner)
                    runner.work, runner.results, runner.execution_ids = Path(directory), [], set()
                    runner.write_json = lambda *args: None
                    def command(argv, case_count=count, **kwargs):
                        Path(argv[-1]).write_text(json.dumps({'results': [
                            {'name': str(index), 'passed': True} for index in range(case_count)]}))
                        return SimpleNamespace(returncode=0)
                    runner.command = command
                    with contextlib.redirect_stdout(io.StringIO()):
                        runner.run_suite(suite, ['never-executed'], runtime=True)
                    self.assertEqual(runner.results[-1]['passed'], count >= required)

    def test_cleanup_never_adopts_rejected_existing_project(self):
        runner = object.__new__(native.NativeRunner)
        runner.started_stack = False
        runner.client, runner.private = MagicMock(), MagicMock()
        runner.cleanup()
        runner.client.containers.list.assert_not_called()
        runner.client.networks.list.assert_not_called()
        runner.client.volumes.list.assert_not_called()
        runner.client.close.assert_called_once()

    def test_fixture_selection_uses_exact_project_label_only(self):
        runner = object.__new__(native.NativeRunner)
        runner.project = 'aw002ci-123-1'
        runner.client = MagicMock()
        runner.owned_fixture_resources()
        for collection in (runner.client.containers, runner.client.networks, runner.client.volumes):
            self.assertEqual(collection.list.call_args.kwargs['filters'],
                             {'label': 'aw.verify_run=aw002ci-123-1'})

    def test_kernel_cleanup_can_delete_only_exact_own_names(self):
        runner = object.__new__(native.NativeRunner)
        runner.kernel_names = lambda: ({'AWOWN'}, {'awd_own', 'awp_own'})
        runner.kernel_residual = lambda: {'iptables-save': 0, 'ip6tables-save': 0, 'nft_tables': 0}
        recorded = []
        def helper(command):
            if command[0] in ('iptables-save', 'ip6tables-save'):
                return b':AWOWN - [0:0]\n:OTHER - [0:0]\n-A FORWARD -i ownbridge -j AWOWN\n-A FORWARD -i otherbridge -j OTHER\n'
            if command[0] == 'nft':
                return json.dumps({'nftables': [{'table': {'family': 'inet', 'name': name}}
                                                for name in ('awd_own', 'awp_own', 'other_table')]}).encode()
            recorded.extend(json.loads(base64.b64decode(command[-1]))['commands'])
            return b'{}'
        runner.helper = helper
        runner.cleanup_kernel()
        encoded = json.dumps(recorded)
        self.assertIn('AWOWN', encoded)
        self.assertIn('awd_own', encoded)
        self.assertNotIn('OTHER', encoded)
        self.assertNotIn('other_table', encoded)
        self.assertNotIn('otherbridge', encoded)
        self.assertNotIn(['iptables', '-F', 'FORWARD'], recorded)

    def test_private_uuid_journal_rejects_non_uuid_and_no_symlink_follow(self):
        import sandbox_runtime
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ids'
            identifier = 'a3e4d9ca-bf99-4670-8c03-145a41285289'
            with patch.dict(os.environ, {'AW_VERIFY_ID_JOURNAL': str(path)}):
                sandbox_runtime.record_execution_id(identifier)
                self.assertEqual(path.read_text(), identifier + '\n')
                with self.assertRaises(ValueError):
                    sandbox_runtime.record_execution_id('not-a-uuid')
                path.unlink()
                path.symlink_to(Path(directory) / 'unrelated')
                with self.assertRaises(OSError):
                    sandbox_runtime.record_execution_id(identifier)
            self.assertFalse((Path(directory) / 'unrelated').exists())

    def test_runner_failure_still_cleans_owned_stack_and_reports_sanitized_type(self):
        runner = object.__new__(native.NativeRunner)
        runner.results, runner.env, runner.started = [], {}, 0
        runner.prepare = lambda: (_ for _ in ()).throw(RuntimeError('secret-password'))
        cleaned, reports = [], []
        runner.cleanup = lambda: cleaned.append(True)
        runner.write_json = lambda name, content: reports.append(content)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            status = runner.run()
        self.assertEqual(status, 1)
        self.assertEqual(cleaned, [True])
        self.assertNotIn('secret-password', output.getvalue() + json.dumps(reports))
        self.assertFalse(reports[0]['passed'])


if __name__ == '__main__':
    unittest.main()
