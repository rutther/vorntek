"""Resolve client IP only across explicitly trusted, append-only proxy hops."""

from ipaddress import ip_address, ip_network

from django.core.exceptions import ImproperlyConfigured


def parse_proxy_networks(values):
    """Fail closed at startup rather than silently trusting an invalid range."""
    networks = []
    try:
        if len(values) > 64:
            raise ValueError('Too many proxy ranges')
        for value in values:
            if not isinstance(value, str) or '%' in value:
                raise ValueError('Proxy ranges must be unscoped IPs/CIDRs')
            network = ip_network(value, strict=False)
            if network.prefixlen == 0:
                raise ValueError('A default route is not a proxy allowlist')
            if network.version == 6 and network.network_address.ipv4_mapped and network.prefixlen >= 96:
                network = ip_network(f'{network.network_address.ipv4_mapped}/{network.prefixlen - 96}')
                if network.prefixlen == 0:
                    raise ValueError('An IPv4 default route is not a proxy allowlist')
            networks.append(network)
    except (TypeError, ValueError) as exc:
        raise ImproperlyConfigured('SITEOS_TRUSTED_PROXY_CIDRS must contain explicit valid proxy IPs/CIDRs, not default routes.') from exc
    return tuple(networks)


def _address(value):
    if not isinstance(value, str) or len(value) > 64 or '%' in value:
        return None
    try:
        address = ip_address(value.strip())
    except ValueError:
        return None
    if address.version == 6:
        return address.ipv4_mapped or address
    return address


def resolve_client_ip(request):
    from django.conf import settings

    peer = _address(request.META.get('REMOTE_ADDR', ''))
    if peer is None:
        return ''
    networks = getattr(settings, 'SITEOS_TRUSTED_PROXY_NETWORKS', ())

    def trusted(address):
        return any(address.version == network.version and address in network for network in networks)

    if not trusted(peer):
        return str(peer)
    chain = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if not chain or (isinstance(chain, str) and not chain.strip()):
        real = _address(request.META.get('HTTP_X_REAL_IP', ''))
        return str(real or peer)
    if not isinstance(chain, str) or len(chain) > 4096:
        return str(peer)
    hops = chain.split(',')
    if len(hops) > 64:
        return str(peer)
    for value in reversed(hops):
        address = _address(value)
        if address is None:
            return str(peer)
        if not trusted(address):
            return str(address)
    # No non-proxy source can be established; never invent a client identity.
    return str(peer)
