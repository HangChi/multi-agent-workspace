"""The proxy's own log window is the only evidence that separates "the query
never arrived" from "the proxy could not resolve it" when a mediated execution
fails with `dns_runtime`/`upstream_timeout`. These controls pin that the capture
stays bounded and address-masked, so publishing it cannot leak topology.
"""
from __future__ import annotations

import unittest

import native_acceptance


class SummarizeDnsProxyLogTests(unittest.TestCase):
    def test_keeps_only_lines_that_carry_a_resolution_outcome(self):
        text = (
            "dns-proxy-1  | dnsmasq: query[A] pypi.org from 172.30.0.3\n"
            "dns-proxy-1  | dnsmasq: started, version 2.90 cachesize 1000\n"
            "dns-proxy-1  | dnsmasq: reply pypi.org is 151.101.0.223\n"
            "dns-proxy-1  | dnsmasq: reading /etc/dnsmasq.conf\n"
            "dns-proxy-1  | dnsmasq: no servers found in /etc/resolv.conf, will retry"
        )
        payload = native_acceptance.summarize_dns_proxy_log(text, 42)
        self.assertEqual(payload["total_lines"], 5)
        self.assertEqual(payload["window_seconds"], 42)
        self.assertEqual(len(payload["kept_lines"]), 3)
        self.assertFalse(payload["truncated"])
        self.assertTrue(payload["kept_lines"][0].startswith("dnsmasq: query[A] pypi.org"))
        self.assertIn("no servers found", payload["kept_lines"][-1])

    def test_container_prefix_is_dropped_and_addresses_are_masked(self):
        payload = native_acceptance.summarize_dns_proxy_log(
            "dns-proxy-1  | dnsmasq: query[A] example.com from 172.30.0.7\n"
            "dns-proxy-1  | dnsmasq: reply example.com is 93.184.216.34",
            10,
        )
        joined = "\n".join(payload["kept_lines"])
        self.assertNotIn("dns-proxy-1", joined)
        self.assertNotIn("172.30.0.7", joined)
        self.assertNotIn("93.184.216.34", joined)
        self.assertIn("query[A] example.com from <ip>", joined)

    def test_the_window_is_capped_and_marked_truncated(self):
        text = "\n".join(f"dnsmasq: query[A] host{i}.example from 10.0.0.{i % 250}"
                         for i in range(500))
        payload = native_acceptance.summarize_dns_proxy_log(text, 60)
        self.assertEqual(len(payload["kept_lines"]), 200)
        self.assertTrue(payload["truncated"])
        # The tail is what matters when the failure just happened.
        self.assertIn("host499.example", payload["kept_lines"][-1])


if __name__ == "__main__":
    unittest.main()
