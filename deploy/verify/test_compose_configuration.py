"""Resolve acceptance Compose without starting services or reading app secrets."""
from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class ComposeConfigurationTests(unittest.TestCase):
    def resolve(self, filename="docker-compose.verify.yml"):
        # Only disposable test values; never expand the developer's deployment .env.
        environment = {
            key: os.environ[key] for key in ("PATH", "HOME") if key in os.environ
        }
        environment.update({
            "AW_VERIFY_PASSWORD": "compose-test-only-password",
            "AW_VERIFY_JWT_SECRET": "compose-test-only-jwt",
            "AW_VERIFY_INVITE_CODE": "compose-test-only-invite",
        })
        result = subprocess.run(
            ["docker", "compose", "--env-file", "/dev/null", "-f",
             str(ROOT / "deploy" / filename),
             "--profile", "faults", "config", "--format", "json"],
            cwd=ROOT, env=environment, capture_output=True, text=True,
            check=True, timeout=30,
        )
        return json.loads(result.stdout)["services"]

    def test_dns_primary_network_keeps_gateway_priority_over_dynamic_sandbox_networks(self):
        for filename in ("docker-compose.verify.yml", "docker-compose.yml"):
            with self.subTest(filename=filename):
                dns = self.resolve(filename)["dns-proxy"]
                self.assertGreater((dns["networks"]["default"] or {}).get("gw_priority", 0), 0)

    def test_api_pools_connections_but_all_worker_variants_use_null_pool(self):
        services = self.resolve()
        self.assertNotEqual(services["api"]["environment"].get("DB_POOL"), "null")
        for name in ("worker", "worker-infra", "worker-quota"):
            with self.subTest(service=name):
                self.assertEqual(services[name]["environment"]["DB_POOL"], "null")
