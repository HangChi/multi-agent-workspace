"""The native-only resolver diagnostic is bounded and never exports DNS data."""
import json
import socket
import struct
import unittest
from unittest.mock import Mock, patch

import dns_reachability as dns


class DNSReachabilityTests(unittest.TestCase):
    def test_udp_success_validates_transaction_and_never_returns_answer(self):
        conn = Mock()
        conn.recv.return_value = struct.pack('!6H', dns.TRANSACTION_ID, 0x8180, 1, 1, 0, 0) + b'SECRET'
        with patch.object(dns.socket, 'socket') as factory:
            factory.return_value.__enter__.return_value = conn
            result = dns.query('1.1.1.1', stream=False)
        self.assertEqual(result['outcome'], 'answered')
        self.assertNotIn('SECRET', json.dumps(result))
        self.assertEqual(conn.settimeout.call_args_list[0].args, (1.0,))
        self.assertTrue(all(0 < call.args[0] <= 1 for call in conn.settimeout.call_args_list))
        conn.connect.assert_called_once_with(('1.1.1.1', 53))
        conn.sendall.assert_called_once_with(dns.QUESTION)

    def test_timeout_and_socket_error_have_only_finite_categories(self):
        for error, expected in ((TimeoutError('SECRET'), 'timeout'), (OSError('SECRET'), 'socket_error')):
            with self.subTest(expected=expected), patch.object(dns.socket, 'socket') as factory:
                factory.return_value.__enter__.return_value.connect.side_effect = error
                result = dns.query('1.1.1.1', stream=False)
                self.assertEqual(result['outcome'], expected)
                self.assertNotIn('SECRET', json.dumps(result))

    def test_wrong_transaction_refusal_or_short_reply_cannot_report_success(self):
        for header in (b'bad', struct.pack('!6H', 1, 0x8180, 1, 1, 0, 0),
                       struct.pack('!6H', dns.TRANSACTION_ID, 0x8185, 1, 0, 0, 0)):
            with self.subTest(header=header), patch.object(dns.socket, 'socket') as factory:
                factory.return_value.__enter__.return_value.recv.return_value = header
                self.assertEqual(dns.query('1.1.1.1', stream=False)['outcome'], 'invalid_response')

    def test_tcp_handles_partial_frames_with_one_absolute_deadline(self):
        header = struct.pack('!6H', dns.TRANSACTION_ID, 0x8180, 1, 1, 0, 0)
        conn = Mock()
        conn.recv.side_effect = [b'\x00', b'\x0c', header[:4], header[4:]]
        with patch.object(dns.socket, 'socket') as factory:
            factory.return_value.__enter__.return_value = conn
            result = dns.query('1.1.1.1', stream=True)
        self.assertEqual(result['outcome'], 'answered')
        factory.assert_called_once_with(socket.AF_INET, socket.SOCK_STREAM)
        self.assertTrue(all(0 < call.args[0] <= 1 for call in conn.settimeout.call_args_list))
        conn.sendall.assert_called_once_with(struct.pack('!H', len(dns.QUESTION)) + dns.QUESTION)

    def test_probe_can_measure_own_proxy_without_exporting_its_private_address(self):
        with patch.object(dns, 'query', return_value={'outcome': 'answered', 'elapsed_ms': 1}):
            rows = dns.probe('172.30.0.2')
        self.assertEqual(len(rows), 10)
        self.assertNotIn('172.30.0.2', json.dumps(rows))
        self.assertEqual(sum(row['resolver'] == 'own_shared_proxy' for row in rows), 2)
        for address in ('1.1.1.1', '127.0.0.1', 'not-an-ip'):
            with self.subTest(address=address), self.assertRaises(ValueError):
                dns.probe(address)

    def test_proxy_log_projection_counts_only_fixed_markers_without_private_content(self):
        logs = (b'dnsmasq: ignoring query from non-local network SECRET\n'
                b'dnsmasq: 1 172.20.0.1 query[A] pypi.org from SECRET\n'
                b'dnsmasq: 2 172.20.0.1 query[AAAA] pypi.org from SECRET\n'
                b'dnsmasq: forwarded pypi.org to SECRET\n'
                b'dnsmasq: reply pypi.org is NODATA-IPv6 SECRET\n')
        facts = dns.proxy_log_facts(logs)
        self.assertEqual(facts['ignored_non_local'], 1)
        self.assertEqual(facts['query_a'], 1)
        self.assertEqual(facts['query_aaaa'], 1)
        self.assertEqual(facts['forwarded_target'], 1)
        self.assertEqual(facts['reply_target'], 1)
        self.assertNotIn('SECRET', json.dumps(facts))
        self.assertNotIn('pypi', json.dumps(facts))
        with self.assertRaises(ValueError):
            dns.proxy_log_facts(b'x' * (1024 * 1024 + 1))

    def test_post_acceptance_probe_measures_a_and_aaaa_of_fixed_target_only(self):
        with patch.object(dns, 'query', return_value={'outcome': 'answered', 'elapsed_ms': 1}) as query:
            results = dns.probe('172.30.0.2', acceptance_target=True)
        self.assertEqual(len(results), 20)
        self.assertEqual({row['query_type'] for row in results}, {'a', 'aaaa'})
        self.assertTrue(all(b'pypi' in call.kwargs['wire'] for call in query.call_args_list))
        self.assertEqual({call.kwargs['wire'][-4:] for call in query.call_args_list},
                         {b'\x00\x01\x00\x01', b'\x00\x1c\x00\x01'})

    def test_probe_uses_fixed_public_resolvers_both_transports_and_different_domain(self):
        with patch.object(dns, 'query', return_value={'outcome': 'timeout', 'elapsed_ms': 1000}) as query:
            results = dns.probe()
        self.assertEqual(len(results), 8)
        self.assertEqual(query.call_count, 8)
        self.assertEqual({row['resolver'] for row in results}, set(dns.RESOLVERS))
        self.assertEqual({row['transport'] for row in results}, {'udp', 'tcp'})
        self.assertNotIn(b'pypi', dns.QUESTION)
        self.assertIn(b'example', dns.QUESTION)
