"""Docker 28 requires configured subnets for prestart static source endpoints."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import requests
from docker.errors import APIError

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
            attrs={"IPAM": {"Config": self.config}, "EnableIPv6": True,
                   "Labels": kwargs["labels"], "Containers": {}},
            reload=Mock(),
            remove=Mock(side_effect=lambda: self.events.append("remove")),
        )
        if not configured:
            self.reservations.append(network)
        return network


class ExplicitDaemonIpamTests(unittest.TestCase):
    def setUp(self):
        self.networks = Networks()
        self.client = SimpleNamespace(networks=self.networks)
        self.run = SandboxRun(execution_id="native-compatibility", language="python",
                              code="print(1)", client=self.client)
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
            [{key: value for key, value in pool.items() if value is not None} for pool in ipam["Config"]],
            self.networks.config,
        )
        self.assertIsNot(network, self.networks.reservations[0])
        self.networks.reservations[0].remove.assert_called_once()

    def test_only_real_overlap_conflict_retries_with_a_new_reservation(self):
        self.networks.conflicts = 1
        self.run._create_network(self.client)
        self.assertEqual(self.networks.events,
                         ["reserve", "remove", "configured", "reserve", "remove", "configured"])
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
            [{"Subnet": "172.30.0.0/24", "Gateway": "10.0.0.1"}, self.networks.config[1]],
            [{"Subnet": "not-a-subnet", "Gateway": "172.30.0.1"}, self.networks.config[1]],
        ):
            with self.subTest(config=config):
                networks = Networks()
                networks.config = config
                client = SimpleNamespace(networks=networks)
                with self.assertRaises(SandboxInfraError), SandboxRun(
                    execution_id="invalid-ipam", language="python", code="print(1)", client=client,
                ) as run:
                    run._create_network(client)
                self.assertEqual(len(networks.calls), 1)
                networks.reservations[0].remove.assert_called_once()

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
