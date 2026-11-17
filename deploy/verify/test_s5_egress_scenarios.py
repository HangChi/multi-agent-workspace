"""Isolated S5 fixture evidence tests; no Docker, app imports, database or Internet."""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import s5_egress_scenarios as suite
import sandbox_runtime


class FixtureEvidenceTests(unittest.TestCase):
    def fake_client(self):
        client = MagicMock()
        networks = []
        servers = []

        def network_create(name, **kwargs):
            network = MagicMock(name="owned network")
            network.name = name
            networks.append(network)
            return network

        def container_run(*args, **kwargs):
            server = MagicMock(name="owned server")
            network_name = kwargs["network"]
            server.attrs = {"NetworkSettings": {"Networks": {network_name: {
                "IPAddress": ("198.18.254.2", "10.253.254.2", "198.18.253.2")[len(servers)],
                "GlobalIPv6Address": "2001:db8:1234::2",
            }}}}
            server.exec_run.return_value = types.SimpleNamespace(exit_code=0, output=b"")
            servers.append(server)
            return server

        client.networks.create.side_effect = network_create
        client.containers.run.side_effect = container_run
        return client, networks, servers

    def run_main(self, client=None, *, connection_failure=None, probe=None):
        config = types.ModuleType("app.core.config")
        config.settings = types.SimpleNamespace(sbx_image="owned-test-image")
        docker = types.ModuleType("docker")
        docker.types = MagicMock()
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "report.json"
            with patch.dict(sys.modules, {"docker": docker, "app.core.config": config}), \
                 patch.object(sys, "argv", ["s5_egress_scenarios.py", "--output", str(output)]), \
                 patch.object(suite, "docker_client", return_value=client, side_effect=connection_failure), \
                 patch.object(suite, "probe", side_effect=probe), \
                 contextlib.redirect_stdout(io.StringIO()):
                status = suite.main()
            raw = output.read_text()
        return status, json.loads(raw), raw

    def test_docker_connection_failure_still_writes_sanitized_json(self):
        status, document, raw = self.run_main(
            connection_failure=RuntimeError("connection refused Bearer private-secret"),
        )
        self.assertEqual(status, 1)
        self.assertEqual(document["suite"], "s5_egress_scenarios")
        facts = document["results"][0]["runtime"]
        self.assertEqual(facts["error_stage"], "docker_connect")
        self.assertEqual(facts["error_code"], "connection_refused")
        self.assertNotIn("private-secret", raw)
        self.assertNotIn("Bearer", raw)

    def test_fixture_network_failure_still_reports_and_closes_client(self):
        client, _, _ = self.fake_client()
        client.networks.create.side_effect = RuntimeError("Pool overlaps private-secret")
        status, document, raw = self.run_main(client)
        self.assertEqual(status, 1)
        facts = document["results"][0]["runtime"]
        self.assertEqual(facts["error_stage"], "fixture_network_create")
        self.assertEqual(facts["error_code"], "address_pool_overlap")
        self.assertNotIn("private-secret", raw)
        client.close.assert_called_once()

    def test_failed_readiness_preserves_real_precondition_and_removes_owned_resources(self):
        client, networks, servers = self.fake_client()
        original = client.containers.run.side_effect

        def failed_listener(*args, **kwargs):
            server = original(*args, **kwargs)
            server.exec_run.return_value = types.SimpleNamespace(exit_code=1, output=b"private-secret")
            return server

        client.containers.run.side_effect = failed_listener
        status, document, raw = self.run_main(client)
        self.assertEqual(status, 1)
        self.assertEqual(len(document["results"]), 1)
        self.assertEqual(document["results"][0]["runtime"]["error_stage"], "fixture_listener_ready")
        self.assertNotIn("private-secret", raw)
        self.assertEqual(len(servers), 1)
        servers[0].remove.assert_called_once_with(force=True)
        networks[0].remove.assert_called_once_with()
        client.close.assert_called_once()

    def test_successful_full_matrix_reports_after_cleanup_and_retains_ipv6(self):
        client, networks, servers = self.fake_client()
        call = 0

        def probe(_client, _code, **kwargs):
            nonlocal call
            index = call
            call += 1
            address = "2001:db8:1234::2" if index >= 8 else "198.18.254.2"
            port = 8000
            if index == 1:
                address = "10.253.254.2"
            if index == 2:
                address, port = "169.254.169.254", 80
            connected = index in (0, 3, 6, 8)
            access = [{
                "target_ip": address, "port": port,
                "action": "allow" if connected else "deny", "source_ip": "fd00::2",
            }]
            stdout = json.dumps({"connected": connected})
            if index in (6, 7):
                ports = [53616, 53617, 53618]
                access = [{**access[0], "source_port": source} for source in ports]
                stdout = json.dumps({"ports": ports, "results": [connected] * 3})
            return {"exit_code": 0, "stdout": stdout, "egress": access, "residual": {}}

        original_report = suite.report

        def report_after_cleanup(results, output):
            self.assertEqual(len(networks), 3)
            self.assertEqual(len(servers), 3)
            for server in servers:
                server.remove.assert_called_once_with(force=True)
            for network in networks:
                network.remove.assert_called_once_with()
            client.close.assert_called_once()
            return original_report(results, output)

        with patch.object(suite, "report", side_effect=report_after_cleanup):
            status, document, _ = self.run_main(client, probe=probe)
        self.assertEqual(status, 0)
        self.assertEqual(len(document["results"]), 11)
        self.assertTrue(all(item["passed"] for item in document["results"]))
        ipv6 = client.networks.create.call_args_list[-1].kwargs
        self.assertIs(ipv6["enable_ipv6"], True)
        self.assertIn("ipam", ipv6)

    def test_cleanup_errors_do_not_suppress_setup_report_or_other_cleanup(self):
        client, networks, servers = self.fake_client()
        original = client.containers.run.side_effect

        def failing_server(*args, **kwargs):
            server = original(*args, **kwargs)
            server.exec_run.side_effect = RuntimeError("private-secret readiness")
            server.remove.side_effect = RuntimeError("permission denied private-secret")
            return server

        client.containers.run.side_effect = failing_server
        client.close.side_effect = RuntimeError("private-secret close")
        status, document, raw = self.run_main(client)
        self.assertEqual(status, 1)
        self.assertEqual(len(document["results"]), 3)
        self.assertEqual([item["runtime"]["error_stage"] for item in document["results"]],
                         ["fixture_listener_ready", "fixture_cleanup", "fixture_client_close"])
        self.assertNotIn("private-secret", raw)
        networks[0].remove.assert_called_once_with()
        client.close.assert_called_once()
        self.assertEqual(len(servers), 1)

    def test_cleanup_attempts_every_resource_after_independent_failures(self):
        resources = [MagicMock(), MagicMock()]
        networks = [MagicMock(), MagicMock()]
        client = MagicMock()
        resources[1].remove.side_effect = RuntimeError("private-secret")
        networks[1].remove.side_effect = RuntimeError("private-secret")
        facts = suite._cleanup_owned(resources, networks, client)
        self.assertEqual(len(facts), 2)
        for resource in resources:
            resource.remove.assert_called_once_with(force=True)
        for network in networks:
            network.remove.assert_called_once_with()
        client.close.assert_called_once()
        self.assertNotIn("private-secret", json.dumps(facts))

    def test_fixture_stages_are_all_preserved_by_ci_sanitizer_allowlist(self):
        self.assertTrue(suite._FIXTURE_STAGES <= sandbox_runtime.ERROR_STAGES)

    def test_three_connection_case_does_not_pin_source_ports(self):
        """A pinned source port turns a passing run into `exit_code: 1`.

        Observed on CI: the allow case connects successfully from 41001-41003,
        those ports stay in TIME_WAIT, and the immediately following deny case
        then dies with `OSError: [Errno 48] Address already in use` before it
        ever reaches the target - reported only as `exit_code: 1`, `622 ms`
        against the `863 ms` a full three-timeout run takes. The contract is
        "three separate source ports", not those three numbers, so the probe
        must let the kernel choose and must not depend on a fixed port being
        free on the host.
        """
        client, _networks, _servers = self.fake_client()
        codes: list[str] = []
        call = 0

        def probe(_client, code, **kwargs):
            nonlocal call
            index = call
            call += 1
            codes.append(code)
            if index in (6, 7):
                ports = [53616, 53617, 53618]
                connected = index == 6
                return {
                    "exit_code": 0,
                    "stdout": json.dumps({"ports": ports, "results": [connected] * 3}),
                    "egress": [{"target_ip": "198.18.254.2", "port": 8000, "source_ip": "fd00::2",
                                "source_port": port, "action": "allow" if connected else "deny"}
                               for port in ports],
                    "residual": {},
                }
            # Every other case in this suite uses the single-connection shape the
            # existing matrix test already pins; only ports 6 and 7 probe three.
            address = "2001:db8:1234::2" if index >= 8 else "198.18.254.2"
            port = 8000
            if index == 1:
                address = "10.253.254.2"
            if index == 2:
                address, port = "169.254.169.254", 80
            connected = index in (0, 3, 8)
            return {
                "exit_code": 0,
                "stdout": json.dumps({"connected": connected}),
                "egress": [{"target_ip": address, "port": port, "source_ip": "fd00::2",
                            "action": "allow" if connected else "deny"}],
                "residual": {},
            }

        status, _document, _raw = self.run_main(client, probe=probe)
        self.assertEqual(status, 0)
        multi = [code for code in codes if "connect_ex" in code and "range(3)" in code]
        self.assertEqual(len(multi), 2, "both three-connection cases must run the probe")
        for code in multi:
            self.assertIn("SO_REUSEADDR", code)
            self.assertIn("bind(('',0))", code)
            for pinned in ("41001", "41002", "41003"):
                self.assertNotIn(pinned, code, "a pinned source port can collide with TIME_WAIT")

    def test_three_connection_case_rejects_reused_or_unreported_ports(self):
        """Three connections from one port is not what the scenario claims."""
        cases = {
            "duplicate ports": {"ports": [53616, 53616, 53617], "results": [False] * 3},
            "audit names other ports": {"ports": [1, 2, 3], "results": [False] * 3},
            "missing ports": None,
        }
        for label, payload in cases.items():
            with self.subTest(label):
                client, _networks, _servers = self.fake_client()

                def probe(_client, _code, _payload=payload, **kwargs):
                    if _payload is None:
                        stdout = json.dumps({"results": [False] * 3})
                        return {"exit_code": 0, "stdout": stdout, "egress": [], "residual": {}}
                    return {
                        "exit_code": 0,
                        "stdout": json.dumps(_payload),
                        "egress": [{"target_ip": "198.18.254.2", "port": 8000,
                                    "source_ip": "fd00::2", "source_port": port, "action": "deny"}
                                   for port in (51000, 51001, 51002)],
                        "residual": {},
                    }

                status, _document, _raw = self.run_main(client, probe=probe)
                self.assertEqual(status, 1)


class FixtureReadinessTests(unittest.TestCase):
    def test_ipv4_and_ipv6_readiness_retry_refusal_then_require_real_success(self):
        for host, family in (("127.0.0.1", 2), ("::1", 10)):
            with self.subTest(host=host):
                socket = types.ModuleType("socket")
                socket.AF_INET, socket.AF_INET6 = 2, 10
                connection = MagicMock()
                connection.connect.side_effect = [OSError("not yet listening"), None]
                socket.socket = MagicMock(return_value=connection)
                connection.__enter__.return_value = connection
                clock = types.ModuleType("time")
                clock.sleep = MagicMock()
                server = MagicMock()

                def execute_fixed_readiness(argv, socket=socket, clock=clock):
                    self.assertEqual(argv[:2], ["python", "-c"])
                    # Execute only this module's fixed trusted fixture code with
                    # in-memory sockets: never launch payloads or contact a host.
                    with patch.dict(sys.modules, {"socket": socket, "time": clock}):
                        exec(compile(argv[2], "<trusted fixture readiness>", "exec"), {})  # noqa: S102 - fixed trusted code with mocked sockets
                    return types.SimpleNamespace(exit_code=0)

                server.exec_run.side_effect = execute_fixed_readiness
                suite._wait_for_listener(server, host)
                self.assertEqual(connection.connect.call_count, 2)
                socket.socket.assert_called_with(family)
                connection.settimeout.assert_called_with(0.1)
                clock.sleep.assert_called_once_with(0.02)

    def test_readiness_retry_count_is_bounded_and_does_not_release_raw_output(self):
        server = MagicMock()
        server.exec_run.return_value = types.SimpleNamespace(exit_code=1, output=b"private-secret")
        with self.assertRaisesRegex(RuntimeError, "fixture listener readiness check failed"):
            suite._wait_for_listener(server, "127.0.0.1")
        code = server.exec_run.call_args.args[0][2]
        self.assertIn("range(100)", code)
        for attempts in (0, 101):
            with self.assertRaises(ValueError):
                suite._wait_for_listener(server, "127.0.0.1", attempts=attempts)
        with self.assertRaises(ValueError):
            suite._wait_for_listener(server, "public.example")


if __name__ == "__main__":
    unittest.main()
