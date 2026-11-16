"""Pure reporting/cleanup controls; never contact Docker, DNS, a DB or network."""
from __future__ import annotations

import contextlib
import io
import ipaddress
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import s5_domain_runtime as domain
from sandbox_runtime import ERROR_CODES, ERROR_STAGES, ERROR_TYPES

SECRET = "Bearer private-token X-Amz-Signature=private-signature"
EID = "a3e4d9ca-bf99-4670-8c03-145a41285289"


class DomainProbeTests(unittest.TestCase):
    def setUp(self):
        # Fail closed at the process boundary: every test uses fake SDK objects.
        self.client = MagicMock()
        self.client.containers.list.return_value = []
        self.client.networks.list.return_value = []
        self.client.containers.run.return_value = b'{"kernel_residuals":0}'
        self.docker = self.enterContext(patch.object(domain.docker, "from_env", return_value=self.client))
        self.prepare = self.enterContext(patch.object(domain.runtime.NetworkCapture, "prepare"))
        self.journal = self.enterContext(patch.object(domain, "record_execution_id"))
        self.enterContext(patch.object(domain.time, "sleep"))
        self.original_dns = domain.runtime.EGRESS_DNS_PROXY
        self.addCleanup(setattr, domain.runtime, "EGRESS_DNS_PROXY", self.original_dns)

    def probe(self):
        probe = domain.Probe()
        probe.client = self.client
        return probe

    def assert_finite(self, evidence):
        self.assertEqual(set(evidence) - {"daemon_status"},
                         {"error_stage", "error_code", "error_type", "error_chain"})
        self.assertIn(evidence["error_stage"], ERROR_STAGES)
        self.assertIn(evidence["error_code"], ERROR_CODES)
        self.assertIn(evidence["error_type"], ERROR_TYPES)
        self.assertLessEqual(len(evidence["error_chain"]), 8)
        self.assertTrue(all(value in ERROR_TYPES for value in evidence["error_chain"]))
        if "daemon_status" in evidence:
            self.assertIs(type(evidence["daemon_status"]), int)
            self.assertTrue(400 <= evidence["daemon_status"] <= 599)

    def run_main(self, probe):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            with patch.object(domain, "Probe", return_value=probe), contextlib.redirect_stdout(io.StringIO()) as stdout:
                code = domain.main(["--output", str(path)])
            data = path.read_text()
            self.assertNotIn("private-token", data + stdout.getvalue())
            self.assertNotIn("private-signature", data + stdout.getvalue())
            self.assertNotIn('"facts"', data)
            self.assertLessEqual(len(data.encode()), domain.MAX_REPORT_BYTES)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            return code, json.loads(data)

    def test_constructor_never_initializes_docker_outside_guarded_run(self):
        probe = domain.Probe()
        self.assertIsNone(probe.client)
        self.docker.assert_not_called()

    def test_docker_initialization_failure_still_reports_and_attempts_cleanup(self):
        probe = domain.Probe()
        self.docker.side_effect = domain.docker.errors.DockerException(SECRET)
        code, report = self.run_main(probe)
        self.assertEqual(code, 1)
        error = next(item for item in report["results"] if item["name"] == "runtime-exception")
        self.assert_finite(error["runtime"])
        self.assertEqual(error["runtime"]["error_stage"], "docker_connect")
        self.assertTrue(any(item["name"] == "owned-resources-and-kernel-tables-cleaned"
                            for item in report["results"]))
        self.assertTrue(any(item["name"] == "mandatory-cases-incomplete" for item in report["results"]))
        self.client.containers.run.assert_not_called()

    def test_network_fixture_failure_preserves_phase_and_daemon_status(self):
        probe = self.probe()
        error = domain.docker.errors.APIError(SECRET, response=SimpleNamespace(status_code=400,
                                                                                 url="http://private.invalid", reason="bad"))
        self.client.networks.create.side_effect = error
        code, report = self.run_main(probe)
        self.assertEqual(code, 1)
        evidence = next(item["runtime"] for item in report["results"] if item["name"] == "runtime-exception")
        self.assert_finite(evidence)
        self.assertEqual(evidence["error_stage"], "fixture_network_create")
        self.assertEqual(evidence["daemon_status"], 400)
        self.client.close.assert_called_once()

    def test_network_retains_explicit_dual_stack_ipam_and_ownership(self):
        probe = self.probe()
        probe.network(1, target=True)
        options = self.client.networks.create.call_args.kwargs
        self.assertTrue(options["enable_ipv6"])
        pools = options["ipam"]["Config"]
        self.assertEqual(len(pools), 2)
        self.assertIn("198.18.", pools[0]["Subnet"])
        self.assertTrue(pools[1]["Subnet"].endswith("/64"))
        for pool in pools:
            self.assertEqual(pool.get("Gateway"), str(ipaddress.ip_network(pool["Subnet"]).network_address + 1))
        self.assertEqual(options["labels"]["aw.domain.probe"], probe.owner)

    def test_fixture_container_failure_records_only_finite_phase(self):
        probe = self.probe()
        self.client.containers.run.side_effect = RuntimeError(SECRET)
        with self.assertRaises(RuntimeError) as raised:
            probe.container(domain.IMAGE, command=["controlled"])
        evidence = domain.finite_failure(raised.exception, probe.phase)
        self.assertEqual(evidence["error_stage"], "fixture_container_create")
        self.assert_finite(evidence)
        self.assertNotIn("private", json.dumps(evidence))

    def test_readiness_timeout_and_oversized_logs_never_enter_exception_text(self):
        container = MagicMock()
        container.logs.return_value = SECRET.encode()
        with patch.object(domain.time, "monotonic", side_effect=[0, 0, 5]), self.assertRaises(RuntimeError) as raised:
            domain.ready(container, "not-ready")
        self.assertNotIn("private", str(raised.exception))
        container.logs.assert_called_once_with(tail=20)
        container.logs.return_value = b"x" * 65537
        with self.assertRaises(RuntimeError):
            domain.ready(container, "not-ready")

    def execution_fixture(self):
        probe = self.probe()
        probe.network = MagicMock(return_value=SimpleNamespace(name="owned-network"))
        probe.targetnet = SimpleNamespace(name="owned-target-network")
        upstream = SimpleNamespace(name="owned-upstream")
        probe.container = MagicMock(return_value=upstream)
        user = MagicMock()
        user.id = "owned-user"
        self.client.containers.create.return_value = user
        capture = MagicMock()
        capture.source_ip, capture.source_ipv6 = "198.19.1.3", "2001:db8::3"
        self.prepare.return_value = capture
        return probe, user, capture

    def test_every_execution_setup_boundary_attributes_its_actual_phase(self):
        for phase in ("upstream_ready", "prepare", "create", "capture", "start", "user_ready"):
            with self.subTest(phase=phase):
                probe, user, capture = self.execution_fixture()
                self.prepare.side_effect = RuntimeError(SECRET) if phase == "prepare" else None
                self.client.containers.create.side_effect = RuntimeError(SECRET) if phase == "create" else None
                capture.start_capture.side_effect = RuntimeError(SECRET) if phase == "capture" else None
                user.start.side_effect = RuntimeError(SECRET) if phase == "start" else None
                ready = [RuntimeError(SECRET)] if phase == "upstream_ready" else [None, RuntimeError(SECRET)] if phase == "user_ready" else [None, None]
                with patch.object(domain, "ready", side_effect=ready), self.assertRaises(RuntimeError) as raised:
                    probe.execution(2, "198.18.1.2", "2001:db8:1::2")
                expected = {"upstream_ready": "fixture_listener_ready", "prepare": "policy_install",
                            "create": "container_create", "capture": "output_capture", "start": "container_start",
                            "user_ready": "fixture_listener_ready"}[phase]
                evidence = domain.finite_failure(raised.exception, probe.phase)
                self.assertEqual(evidence["error_stage"], expected)
                self.assert_finite(evidence)
                self.assertNotIn("private", json.dumps(evidence))
                self.prepare.reset_mock(side_effect=True)
                self.client.containers.create.reset_mock(side_effect=True)

    def test_execution_uuid_is_journaled_before_prepare_even_if_prepare_fails(self):
        probe, _, _ = self.execution_fixture()
        order = []
        self.journal.side_effect = lambda eid: order.append("journal")
        def prepare(*args):
            order.append("prepare")
            raise RuntimeError(SECRET)
        self.prepare.side_effect = prepare
        with patch.object(domain, "ready"), self.assertRaises(RuntimeError):
            probe.execution(2, "198.18.1.2", "2001:db8:1::2")
        self.assertEqual(order, ["journal", "prepare"])
        self.assertEqual(len(probe.execution_ids), 1)

    def test_query_failure_or_invalid_output_is_private_and_not_success(self):
        probe = self.probe()
        user = MagicMock()
        capture = SimpleNamespace(dns_proxy_ip="198.19.1.1")
        for exit_code, output in ((1, SECRET.encode()), (0, SECRET.encode()),
                                  (0, b"x" * 4097), (0, b'{"rcode":0,"connected":true,"secret":"value"}'),
                                  (0, b'{"rcode":true,"connected":true}')):
            with self.subTest(exit_code=exit_code, length=len(output)):
                user.exec_run.return_value = SimpleNamespace(exit_code=exit_code, output=output)
                with self.assertRaises((RuntimeError, ValueError)) as raised:
                    probe.query(user, capture, "198.18.1.2", "2001:db8:1::2")
                evidence = domain.finite_failure(raised.exception, probe.phase)
                self.assertEqual(evidence["error_stage"], "fixture_probe")
                self.assertNotIn("private", json.dumps(evidence))
        user.exec_run.return_value = SimpleNamespace(exit_code=0, output=b'{"rcode":0,"connected":true}')
        self.assertEqual(probe.query(user, capture, "198.18.1.2", "2001:db8:1::2"),
                         {"rcode": 0, "connected": True})

    def test_direct_ip_query_false_accepts_none_rcode_only_without_dns_lookup(self):
        probe = self.probe()
        user = MagicMock()
        capture = SimpleNamespace(dns_proxy_ip="198.19.1.1")
        user.exec_run.return_value = SimpleNamespace(exit_code=0, output=b'{"rcode":null,"connected":false}')
        self.assertEqual(probe.query(user, capture, "198.18.1.2", "2001:db8:1::2", query=False),
                         {"rcode": None, "connected": False})
        for options in ({}, {"query": True}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                probe.query(user, capture, "198.18.1.2", "2001:db8:1::2", **options)
        for invalid_rcode in (True, -2, 16, "0"):
            user.exec_run.return_value = SimpleNamespace(exit_code=0, output=json.dumps({
                "rcode": invalid_rcode, "connected": False}).encode())
            with self.subTest(rcode=invalid_rcode), self.assertRaises(ValueError):
                probe.query(user, capture, "198.18.1.2", "2001:db8:1::2", query=True)
        for rcode in (-1, 0, 5, 15):
            user.exec_run.return_value = SimpleNamespace(exit_code=0, output=json.dumps({
                "rcode": rcode, "connected": False}).encode())
            self.assertEqual(probe.query(user, capture, "198.18.1.2", "2001:db8:1::2", query=True)["rcode"], rcode)

    def test_success_and_failure_checks_never_copy_query_facts(self):
        probe = self.probe()
        probe.phase = "fixture_probe"
        probe.check("wildcard-bind-before-answer", True, {"query": SECRET})
        with self.assertRaises(AssertionError) as raised:
            probe.check("unbound-direct-ip-denied", False, {"query": SECRET})
        self.assertNotIn("private", str(raised.exception) + json.dumps(probe.results))
        self.assertNotIn("facts", json.dumps(probe.results))
        with self.assertRaises(ValueError):
            probe.check(SECRET, True, {})

    def test_cleanup_attempts_all_owned_resources_after_individual_failures(self):
        probe = self.probe()
        order = []
        def action(label, fail=False):
            def run(**kwargs):
                order.append(label)
                if fail:
                    raise RuntimeError(SECRET)
            return run
        first, last, orphan = MagicMock(), MagicMock(), MagicMock()
        first.id, last.id, orphan.id = "first", "last", "orphan"
        first.remove.side_effect = action("first", True)
        last.remove.side_effect = action("last")
        orphan.remove.side_effect = action("orphan")
        capture, network = MagicMock(), MagicMock()
        network.id = "network"
        capture.cleanup.side_effect = action("capture", True)
        network.remove.side_effect = action("network", True)
        probe.containers, probe.captures, probe.networks = [first, last], [capture], [network]
        probe.execution_ids = [EID]
        self.client.containers.list.side_effect = [[first, last, orphan], []]
        probe.close()
        self.assertEqual(order, ["orphan", "last", "first", "capture", "network"])
        self.client.containers.run.assert_called_once()
        self.client.networks.list.assert_called()
        self.client.close.assert_called_once()
        self.assertTrue(any(item["passed"] is False for item in probe.results))
        self.assertNotIn("private", json.dumps(probe.results))
        for call in self.client.containers.list.call_args_list + self.client.networks.list.call_args_list:
            self.assertEqual(call.kwargs["filters"], {"label": f"aw.domain.probe={probe.owner}"})
        self.assertEqual(domain.runtime.EGRESS_DNS_PROXY, self.original_dns)

    def test_kernel_check_failure_still_lists_resources_and_closes_client(self):
        probe = self.probe()
        self.client.containers.run.side_effect = RuntimeError(SECRET)
        probe.close()
        self.assertGreaterEqual(self.client.containers.list.call_count, 2)
        self.assertGreaterEqual(self.client.networks.list.call_count, 2)
        self.client.close.assert_called_once()
        self.assertNotIn("private", json.dumps(probe.results))
        self.assertTrue(any(item["name"] == "cleanup-exception" for item in probe.results))

    def test_client_close_failure_reports_its_phase_without_losing_report(self):
        probe = self.probe()
        probe.run = MagicMock(side_effect=RuntimeError(SECRET))
        self.client.close.side_effect = RuntimeError(SECRET)
        code, report = self.run_main(probe)
        self.assertEqual(code, 1)
        close_errors = [item["runtime"] for item in report["results"] if item["name"] == "cleanup-exception"]
        self.assertTrue(any(item["error_stage"] == "fixture_client_close" for item in close_errors))

    def test_non_docker_cleanup_exception_still_produces_bounded_report(self):
        probe = self.probe()
        probe.run = MagicMock(side_effect=KeyError(SECRET))
        probe.close = MagicMock(side_effect=AttributeError(SECRET))
        code, report = self.run_main(probe)
        self.assertEqual(code, 1)
        errors = [item for item in report["results"] if item["name"] == "cleanup-exception"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["runtime"]["error_stage"], "fixture_cleanup")
        self.assert_finite(errors[0]["runtime"])

    def test_seventeen_or_empty_cases_cannot_report_success(self):
        self.assertEqual(len(domain.MANDATORY_CASES), 18)
        for count in (0, 17, 18):
            with self.subTest(count=count):
                probe = self.probe()
                names = sorted(domain.MANDATORY_CASES)[:count]
                probe.results = [{"name": name, "passed": True} for name in names]
                probe.run, probe.close = MagicMock(), MagicMock()
                code, report = self.run_main(probe)
                self.assertEqual(code, 0 if count == 18 else 1)
                if count < 18:
                    self.assertTrue(any(item["name"] == "mandatory-cases-incomplete" for item in report["results"]))

    def test_report_size_ceiling_fails_closed(self):
        probe = self.probe()
        probe.run, probe.close = MagicMock(), MagicMock()
        probe.results = [{"name": name, "passed": True} for name in domain.MANDATORY_CASES] * 300
        code, report = self.run_main(probe)
        self.assertEqual(code, 1)
        self.assertEqual(len(report["results"]), 1)
        self.assertIs(report["results"][0]["passed"], False)


if __name__ == "__main__":
    unittest.main()
