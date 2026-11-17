"""Docker 28 requires configured subnets for prestart static source endpoints."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests
from docker.errors import APIError

from app.executors.egress_network import NetworkPolicyError
from app.executors.sandbox_client import SandboxInfraError, SandboxRun


def daemon_error(message: str, status: int = 400) -> APIError:
    response = requests.Response()
    response.status_code = status
    return APIError(message, response=response, explanation=message)


class Networks:
    def __init__(self):
        self.calls = []
        self.events = []
        self.reservations = []
        self.conflicts = 0
        self.error = None
        self.config = [
            {"Subnet": "172.30.0.0/24", "Gateway": "172.30.0.1"},
            {"Subnet": "fd00:1234::/64", "Gateway": "fd00:1234::1"},
        ]

    def create(self, name, **kwargs):
        self.calls.append((name, kwargs))
        configured = "ipam" in kwargs
        self.events.append("configured" if configured else "reserve")
        if configured and self.conflicts:
            self.conflicts -= 1
            raise daemon_error("Pool overlaps with other one on this address space")
        if configured and self.error:
            raise self.error
        network = SimpleNamespace(
            name=name,
            attrs={
                "IPAM": {"Config": self.config},
                "EnableIPv6": True,
                "Labels": kwargs["labels"],
                "Containers": {},
            },
            reload=Mock(),
            remove=Mock(side_effect=lambda: self.events.append("remove")),
        )
        if not configured:
            self.reservations.append(network)
        return network


class ExplicitDaemonIpamTests(unittest.TestCase):
    def setUp(self):
        self.networks = Networks()
        self.lock = SimpleNamespace(remove=Mock())
        self.client = SimpleNamespace(
            networks=self.networks,
            containers=SimpleNamespace(create=Mock(return_value=self.lock)),
        )
        self.run = SandboxRun(
            execution_id="native-compatibility",
            language="python",
            code="print(1)",
            client=self.client,
        )
        self.addCleanup(self.run.cleanup)

    def test_daemon_selected_dual_stack_pools_become_explicit_before_endpoints(self):
        network = self.run._create_network(self.client)
        self.assertEqual(self.networks.events, ["reserve", "remove", "configured"])
        self.assertEqual(len(self.networks.calls), 2)
        for name, options in self.networks.calls:
            self.assertEqual(name, self.run.network_name)
            self.assertEqual(options["driver"], "bridge")
            self.assertIs(options["internal"], False)
            self.assertIs(options["enable_ipv6"], True)
            self.assertEqual(options["labels"]["aw.execution"], "native-compatibility")
        ipam = self.networks.calls[-1][1]["ipam"]
        self.assertEqual(
            [
                {key: value for key, value in pool.items() if value is not None}
                for pool in ipam["Config"]
            ],
            self.networks.config,
        )
        self.assertIsNot(network, self.networks.reservations[0])
        self.networks.reservations[0].remove.assert_called_once()

    def test_only_real_overlap_conflict_retries_with_a_new_reservation(self):
        self.networks.conflicts = 1
        self.run._create_network(self.client)
        self.assertEqual(
            self.networks.events,
            ["reserve", "remove", "configured", "reserve", "remove", "configured"],
        )
        self.assertEqual(len(self.networks.reservations), 2)

    def test_persistent_overlap_is_bounded_and_never_falls_back_to_dynamic(self):
        self.networks.conflicts = 100
        with self.assertRaises(SandboxInfraError):
            self.run._create_network(self.client)
        self.assertLessEqual(len(self.networks.calls), 16)
        self.assertEqual(len(self.networks.calls) % 2, 0)
        for reservation in self.networks.reservations:
            reservation.remove.assert_called_once()

    def test_other_daemon_error_is_not_retried(self):
        self.networks.error = daemon_error("operation not permitted", 403)
        with self.assertRaises(SandboxInfraError):
            self.run._create_network(self.client)
        self.assertEqual(len(self.networks.calls), 2)

    def test_missing_ipv6_or_invalid_gateway_fails_closed_and_cleans_reservation(self):
        for config in (
            self.networks.config[:1],
            [
                {"Subnet": "172.30.0.0/24", "Gateway": "10.0.0.1"},
                self.networks.config[1],
            ],
            [
                {"Subnet": "not-a-subnet", "Gateway": "172.30.0.1"},
                self.networks.config[1],
            ],
        ):
            with self.subTest(config=config):
                networks = Networks()
                networks.config = config
                client = SimpleNamespace(
                    networks=networks,
                    containers=SimpleNamespace(create=Mock(return_value=self.lock)),
                )
                with (
                    self.assertRaises(SandboxInfraError),
                    SandboxRun(
                        execution_id="invalid-ipam",
                        language="python",
                        code="print(1)",
                        client=client,
                    ) as run,
                ):
                    run._create_network(client)
                self.assertEqual(len(networks.calls), 1)
                networks.reservations[0].remove.assert_called_once()

    def test_atomic_daemon_lock_covers_the_entire_pool_reservation_gap(self):
        held = False
        original_create = self.networks.create

        def acquire(**options):
            nonlocal held
            self.assertEqual(options["name"], "aw-sandbox-ipam-lock-v1")
            self.assertEqual(options["network_mode"], "none")
            self.assertEqual(options["labels"]["aw.role"], "ipam-lock")
            self.assertEqual(options["labels"]["aw.execution"], "native-compatibility")
            held = True
            return self.lock

        def release(**options):
            nonlocal held
            self.assertEqual(options, {"force": True})
            held = False

        def create(name, **options):
            self.assertTrue(
                held, "daemon-selected pools must not race another sandbox allocator"
            )
            return original_create(name, **options)

        self.client.containers.create.side_effect = acquire
        self.lock.remove.side_effect = release
        self.networks.create = create
        self.run._create_network(self.client)
        self.assertFalse(held)
        self.client.containers.create.assert_called_once()
        self.lock.remove.assert_called_once_with(force=True)

    def test_existing_lock_is_never_adopted_or_deleted_and_wait_is_bounded(self):
        self.client.containers.create.side_effect = daemon_error(
            "container name already in use", 409
        )
        with (
            patch("app.executors.sandbox_client.time.sleep"),
            self.assertRaises(SandboxInfraError),
        ):
            self.run._create_network(self.client)
        self.assertLessEqual(self.client.containers.create.call_count, 64)
        self.assertEqual(self.networks.calls, [])
        self.lock.remove.assert_not_called()

    def test_lock_conflict_can_clear_without_retrying_other_errors(self):
        self.client.containers.create.side_effect = [
            daemon_error("name in use", 409),
            self.lock,
        ]
        with patch("app.executors.sandbox_client.time.sleep"):
            self.run._create_network(self.client)
        self.assertEqual(self.client.containers.create.call_count, 2)
        self.lock.remove.assert_called_once_with(force=True)
        self.networks.calls.clear()
        self.client.containers.create.reset_mock(side_effect=True)
        self.client.containers.create.side_effect = daemon_error(
            "permission denied", 403
        )
        with self.assertRaises(SandboxInfraError):
            self.run._create_network(self.client)
        self.client.containers.create.assert_called_once()
        self.assertEqual(self.networks.calls, [])

    def test_failed_release_is_retried_only_for_our_exact_lock_handle(self):
        self.lock.remove.side_effect = [
            daemon_error("temporary removal failure", 500),
            None,
        ]
        with self.assertRaises(SandboxInfraError):
            self.run._create_network(self.client)
        self.assertIs(self.run._network_lock, self.lock)
        self.run.cleanup()
        self.assertEqual(self.lock.remove.call_count, 2)
        self.assertIsNone(self.run._network_lock)

    def test_persisted_audit_error_preserves_only_canonical_finite_diagnostic(self):
        token = "dns_failure[packet_audit,capture_incomplete]"
        for message, expected in (
            (token, True),
            ("SECRET " + token, False),
            ("dns_failure[SECRET,SECRET]", False),
        ):
            with self.subTest(message=message):
                self.run._egress = SimpleNamespace(
                    collect=Mock(side_effect=NetworkPolicyError(message)),
                    cleanup=Mock(),
                )
                with self.assertRaises(SandboxInfraError) as raised:
                    self.run._collect_egress()
                self.assertEqual(token in str(raised.exception), expected)
                self.assertNotIn("SECRET", str(raised.exception))
                self.assertIn("出站记录采集失败", str(raised.exception))

    def test_removal_failure_does_not_recreate_or_adopt_another_network(self):
        original_create = self.networks.create

        def create(name, **kwargs):
            network = original_create(name, **kwargs)
            network.remove.side_effect = daemon_error("operation not permitted", 403)
            return network

        self.networks.create = create
        with self.assertRaises(SandboxInfraError):
            self.run._create_network(self.client)
        self.assertEqual(len(self.networks.calls), 1)
        self.assertIs(self.run._network, self.networks.reservations[0])


if __name__ == "__main__":
    unittest.main()
