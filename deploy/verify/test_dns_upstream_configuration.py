"""The DNS upstream list is a deployment input, not a baked-in constant.

The mediator aborts an execution with `upstream_timeout` when the shared proxy
cannot answer within its per-query budget (1 s per transport, 3 s per query), so
the resolvers an environment can actually reach must be configurable without
rebuilding the image. These tests pin the renderer's contract and the two
Compose files' wiring.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RENDERER = ROOT / "deploy" / "dns-proxy" / "render-upstreams.sh"


class RenderUpstreamsTests(unittest.TestCase):
    def render(self, value: str | None, interface: str | None = None) -> tuple[int, str, str]:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "upstream.conf"
            env = {key: os.environ[key] for key in ("PATH",) if key in os.environ}
            if value is not None:
                env["EGRESS_DNS_UPSTREAMS"] = value
            if interface is not None:
                env["EGRESS_DNS_UPSTREAM_INTERFACE"] = interface
            result = subprocess.run(
                ["sh", str(RENDERER), str(out)], check=False,
                env=env, capture_output=True, text=True, timeout=30,
            )
            written = out.read_text() if out.exists() else ""
            return result.returncode, written, result.stderr

    def test_multiple_resolvers_are_all_queried_at_once(self):
        code, written, _ = self.render("1.1.1.1,8.8.8.8,223.5.5.5")
        self.assertEqual(code, 0)
        self.assertIn("all-servers", written)
        self.assertEqual(
            [line for line in written.splitlines() if line.startswith("server=")],
            ["server=1.1.1.1", "server=8.8.8.8", "server=223.5.5.5"],
        )

    def test_single_resolver_does_not_emit_all_servers(self):
        code, written, _ = self.render("223.5.5.5")
        self.assertEqual(code, 0)
        self.assertNotIn("all-servers", written)
        self.assertEqual(written.strip(), "server=223.5.5.5")

    def test_whitespace_and_empty_entries_are_tolerated(self):
        code, written, _ = self.render(" 1.1.1.1 , , 8.8.8.8 ")
        self.assertEqual(code, 0)
        self.assertEqual(
            [line for line in written.splitlines() if line.startswith("server=")],
            ["server=1.1.1.1", "server=8.8.8.8"],
        )

    def test_unset_variable_keeps_the_documented_defaults(self):
        code, written, _ = self.render(None)
        self.assertEqual(code, 0)
        # Defaults stay the production (mainland) resolvers; CI overrides them.
        self.assertIn("server=223.5.5.5", written)
        self.assertIn("server=119.29.29.29", written)

    def test_invalid_address_fails_closed(self):
        for value in ("1.1.1.1,not-an-ip", "example.com", "1.1.1.1;rm -rf /"):
            with self.subTest(value=value):
                code, written, stderr = self.render(value)
                self.assertNotEqual(code, 0)
                self.assertEqual(written, "")
                self.assertIn("invalid resolver address", stderr)

    def test_resolvers_can_be_pinned_to_the_primary_interface(self):
        """Upstream traffic must not escape through an execution bridge."""
        code, written, _ = self.render("1.1.1.1,8.8.8.8", interface="eth0")
        self.assertEqual(code, 0)
        self.assertIn("all-servers", written)
        self.assertEqual(
            [line for line in written.splitlines() if line.startswith("server=")],
            ["server=1.1.1.1@eth0", "server=8.8.8.8@eth0"],
        )

    def test_interface_pinning_is_optional(self):
        code, written, _ = self.render("1.1.1.1", interface="")
        self.assertEqual(code, 0)
        self.assertEqual(written.strip(), "server=1.1.1.1")

    def test_empty_list_fails_closed(self):
        code, written, _ = self.render(" , ")
        self.assertNotEqual(code, 0)
        self.assertEqual(written, "")


class ComposeUpstreamWiringTests(unittest.TestCase):
    def services(self, filename: str) -> dict:
        environment = {key: os.environ[key] for key in ("PATH", "HOME") if key in os.environ}
        environment.update({
            "AW_VERIFY_PASSWORD": "compose-test-only-password",
            "AW_VERIFY_JWT_SECRET": "compose-test-only-jwt",
            "AW_VERIFY_INVITE_CODE": "compose-test-only-invite",
        })
        import json
        result = subprocess.run(
            ["docker", "compose", "--env-file", "/dev/null", "-f",
             str(ROOT / "deploy" / filename), "--profile", "faults",
             "config", "--format", "json"],
            cwd=ROOT, env=environment, capture_output=True, text=True,
            check=True, timeout=30,
        )
        return json.loads(result.stdout)["services"]

    def test_both_stacks_pass_the_upstream_list_to_the_proxy(self):
        for filename in ("docker-compose.yml", "docker-compose.verify.yml"):
            with self.subTest(filename=filename):
                dns = self.services(filename)["dns-proxy"]
                self.assertIn("EGRESS_DNS_UPSTREAMS", dns["environment"])
                self.assertTrue(dns["environment"]["EGRESS_DNS_UPSTREAMS"])
                # The proxy is attached to a fresh bridge per execution; its own
                # upstream traffic must stay on the interface it boots with.
                self.assertEqual(dns["environment"]["EGRESS_DNS_UPSTREAM_INTERFACE"], "eth0")

    def test_acceptance_stack_defaults_to_globally_reachable_resolvers(self):
        dns = self.services("docker-compose.verify.yml")["dns-proxy"]
        value = dns["environment"]["EGRESS_DNS_UPSTREAMS"]
        # The CI hosts are outside mainland China; at least one default must be
        # a globally anycast resolver so the query does not depend on that path.
        self.assertTrue({"1.1.1.1", "8.8.8.8"} & set(value.split(",")), value)


if __name__ == "__main__":
    unittest.main()
