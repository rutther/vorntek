"""Client identity comes from explicit proxy trust, never arbitrary headers."""

from ipaddress import ip_network

from django.test import RequestFactory, SimpleTestCase, override_settings
from django.core.exceptions import ImproperlyConfigured

from console.views import request_client_ip


@override_settings(SITEOS_TRUSTED_PROXY_NETWORKS=(ip_network('172.18.0.1/32'), ip_network('10.12.0.0/24'),
                                                 ip_network('fd00::1/128')))
class ClientIPTrustTests(SimpleTestCase):
    def ip(self, peer, xff=None, real=None):
        metadata = {'REMOTE_ADDR': peer}
        if xff is not None:
            metadata['HTTP_X_FORWARDED_FOR'] = xff
        if real is not None:
            metadata['HTTP_X_REAL_IP'] = real
        return request_client_ip(RequestFactory().get('/admin/ip-contract/', **metadata))

    def test_untrusted_peers_ignore_all_forwarded_identity(self):
        for peer in ('198.51.100.31', '127.0.0.1', '172.18.0.3', '10.12.1.5'):
            with self.subTest(peer=peer):
                self.assertEqual(self.ip(peer, '192.0.2.41', '192.0.2.42'), peer)

    @override_settings(SITEOS_TRUSTED_PROXY_NETWORKS=())
    def test_empty_trust_configuration_uses_only_socket_peer(self):
        self.assertEqual(self.ip('172.18.0.1', '192.0.2.41', '192.0.2.42'), '172.18.0.1')

    def test_trusted_proxy_append_ignores_attacker_prefix(self):
        for xff in ('203.0.113.11', '192.0.2.41, 203.0.113.11',
                    'not-an-ip, 192.0.2.41, 203.0.113.11'):
            with self.subTest(xff=xff):
                self.assertEqual(self.ip('172.18.0.1', xff, '192.0.2.42'), '203.0.113.11')

    def test_multihop_walks_only_explicitly_trusted_proxies(self):
        self.assertEqual(self.ip('172.18.0.1', '198.51.100.31, 10.12.0.9'), '198.51.100.31')
        self.assertEqual(self.ip('172.18.0.1', '198.51.100.31, 10.12.1.9'), '10.12.1.9')
        self.assertEqual(self.ip('172.18.0.1', '10.12.0.9'), '172.18.0.1')

    def test_ipv6_and_mapped_peer_are_normalized_before_trust(self):
        self.assertEqual(self.ip('fd00::1', '2001:0db8:0:0:0:0:0:11'), '2001:db8::11')
        self.assertEqual(self.ip('::ffff:172.18.0.1', '::ffff:203.0.113.11'), '203.0.113.11')
        self.assertEqual(self.ip('2001:0db8:0:0:0:0:0:31', '192.0.2.1'), '2001:db8::31')

    def test_invalid_or_oversized_chain_cannot_supply_identity(self):
        for xff in ('not-an-ip', '"203.0.113.11"', '203.0.113.11:80', '[2001:db8::11]',
                    'fe80::1%eth0', '203.0.113.11,', '192.0.2.1,' * 65, 'x' * 4097):
            with self.subTest(xff=xff[:40]):
                self.assertEqual(self.ip('172.18.0.1', xff, '192.0.2.42'), '172.18.0.1')

    def test_real_ip_is_fallback_only_for_trusted_peer_without_chain(self):
        self.assertEqual(self.ip('172.18.0.1', real='2001:0db8::11'), '2001:db8::11')
        self.assertEqual(self.ip('172.18.0.1', real='not-an-ip'), '172.18.0.1')
        self.assertEqual(self.ip('172.18.0.1'), '172.18.0.1')
        self.assertEqual(self.ip('invalid-peer', '203.0.113.11', '203.0.113.12'), '')


class ClientIPConfigurationTests(SimpleTestCase):
    def test_proxy_configuration_rejects_ambiguous_or_global_ranges(self):
        from console.client_ip import parse_proxy_networks
        for values in (['invalid'], ['0.0.0.0/0'], ['::/0'], ['::ffff:0.0.0.0/96'],
                       ['fd00::1%eth0/128'], ['127.0.0.1:80'], ['127.0.0.1'] * 65):
            with self.subTest(values=values[:2]):
                with self.assertRaises(ImproperlyConfigured):
                    parse_proxy_networks(values)

    def test_normalized_proxy_configuration_applies_to_real_resolver(self):
        from console.client_ip import parse_proxy_networks
        networks = parse_proxy_networks(['172.18.0.1', '10.12.0.9/24', 'fd00::1/128', '::ffff:127.0.0.1/128'])
        with override_settings(SITEOS_TRUSTED_PROXY_NETWORKS=networks):
            for peer, expected in (('172.18.0.1', '203.0.113.11'), ('10.12.0.23', '203.0.113.11'),
                                   ('fd00::1', '203.0.113.11'), ('::ffff:127.0.0.1', '203.0.113.11'),
                                   ('10.12.1.23', '10.12.1.23')):
                with self.subTest(peer=peer):
                    request = RequestFactory().get('/admin/ip-contract/', REMOTE_ADDR=peer,
                                                   HTTP_X_FORWARDED_FOR='203.0.113.11')
                    self.assertEqual(request_client_ip(request), expected)
