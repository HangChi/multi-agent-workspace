"""Failure reports may expose finite diagnostic codes, never exception text."""
from __future__ import annotations

import json
import unittest

import sandbox_runtime as runtime


class SafeFailureEvidenceTests(unittest.TestCase):
    def test_nested_failure_reveals_stage_and_cause_without_private_text(self):
        failure = RuntimeError('创建沙箱网络失败：private-token Bearer private-token')
        failure.__cause__ = OSError('permission denied https://private.invalid/?secret=private-token')
        facts = runtime.failure_evidence(failure)
        self.assertEqual(facts['error_stage'], 'network_create')
        self.assertEqual(facts['error_code'], 'permission_denied')
        self.assertEqual(facts['error_chain'], ['RuntimeError', 'OSError'])
        self.assertNotIn('private', json.dumps(facts))
        self.assertNotIn('Bearer', json.dumps(facts))

    def test_unavailable_ipv6_pool_is_identified_without_copying_daemon_message(self):
        facts = runtime.failure_evidence('no available IPv6 address pool: secret-pool-info')
        self.assertEqual(facts['error_code'], 'ipv6_pool_unavailable')
        self.assertEqual(facts['error_type'], 'ExecutionError')
        self.assertNotIn('secret', json.dumps(facts))

    def test_static_ip_subnet_failure_is_classified_without_private_details(self):
        failure = RuntimeError('创建沙箱容器失败：private-container')
        failure.__cause__ = OSError(
            'user specified IP address is supported only when connecting to '
            'networks with user configured subnets: private-network'
        )
        facts = runtime.failure_evidence(failure)
        self.assertEqual(facts['error_stage'], 'container_create')
        self.assertEqual(facts['error_code'], 'static_ip_requires_subnet')
        self.assertIn(facts['error_code'], runtime.ERROR_CODES)
        self.assertNotIn('private', json.dumps(facts))

    def test_finite_dns_diagnostic_survives_wrapped_and_persisted_failures(self):
        message = '出站记录采集失败 SECRET dns_failure[dns_nft_install,nft_syntax]'
        for failure in (message, RuntimeError(message)):
            with self.subTest(failure_type=type(failure).__name__):
                facts = runtime.failure_evidence(failure)
                self.assertEqual(facts['error_stage'], 'dns_nft_install')
                self.assertEqual(facts['error_code'], 'nft_syntax')
                self.assertNotIn('SECRET', json.dumps(facts))

    def test_unrecognized_dns_diagnostic_tokens_cannot_escape_allowlists(self):
        for token in ('dns_failure[SECRET,permission_denied]',
                      'dns_failure[dns_startup,SECRET]',
                      'dns_failure[' + 'x' * 100 + ',helper_exited]'):
            facts = runtime.failure_evidence(token)
            self.assertIn(facts['error_stage'], runtime.ERROR_STAGES)
            self.assertIn(facts['error_code'], runtime.ERROR_CODES)
            self.assertNotIn('SECRET', json.dumps(facts))

    def test_network_precondition_failures_have_finite_codes(self):
        for message, code in (
            ('DNS proxy has no address on sandbox network', 'dns_endpoint_missing'),
            ('Docker network has no bridge interface', 'bridge_unavailable'),
            ('domain DNS mediator requires execution bridge IPv4 gateway', 'gateway_missing'),
            ('IPv6-enabled network has no observable DNS endpoint for source reservation', 'ipv6_endpoint_missing'),
        ):
            with self.subTest(code=code):
                facts = runtime.failure_evidence(RuntimeError(message + ' SECRET'))
                self.assertEqual(facts['error_stage'], 'policy_install')
                self.assertEqual(facts['error_code'], code)
                self.assertIn(code, runtime.ERROR_CODES)
                self.assertNotIn('SECRET', json.dumps(facts))

    def test_exception_names_and_status_are_allowlisted(self):
        failure = type('secret_exception_type', (RuntimeError,), {})('secret-message')
        failure.status_code = 503
        facts = runtime.failure_evidence(failure)
        self.assertEqual(facts['error_type'], 'OtherError')
        self.assertEqual(facts['daemon_status'], 503)
        self.assertNotIn('secret', json.dumps(facts))
        failure.status_code = 'secret-status'
        self.assertNotIn('daemon_status', runtime.failure_evidence(failure))

    def test_cyclic_exception_chains_are_bounded(self):
        failure = RuntimeError('secret')
        failure.__cause__ = failure
        facts = runtime.failure_evidence(failure)
        self.assertEqual(facts['error_chain'], ['RuntimeError'])
        self.assertIn(facts['error_stage'], runtime.ERROR_STAGES)
        self.assertIn(facts['error_code'], runtime.ERROR_CODES)
        self.assertTrue(set(facts['error_chain']) <= runtime.ERROR_TYPES)


if __name__ == '__main__':
    unittest.main()
