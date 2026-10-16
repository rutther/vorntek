"""Opt in to one verified TLS-edge hop; never install/reload a host config.

The edge must replace X-Forwarded-For with its socket client address, and replace
X-Forwarded-Proto with its own protocol. Other peers cannot assert either value.
The application must separately trust the website container's exact peer IP.
"""
import argparse
from ipaddress import ip_address
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]


def render(edge_proxy_ip):
    if '%' in edge_proxy_ip:
        raise ValueError('Scoped proxy addresses are not supported')
    address = ip_address(edge_proxy_ip)
    if address.version == 6 and address.ipv4_mapped:
        address = address.ipv4_mapped
    if address.is_unspecified or address.is_multicast:
        raise ValueError('An exact unicast proxy peer IP is required')
    peer = str(address)
    source = (ROOT / 'deploy/nginx.conf').read_text(encoding='utf-8')
    # Trust only the original socket peer, not the rewritten client address.
    prefix = f'''# Generated opt-in HTTPS edge configuration. Verify the peer before use.
set_real_ip_from {peer};
real_ip_header X-Forwarded-For;
real_ip_recursive off;
geo $realip_remote_addr $vorntek_verified_edge {{
    default 0;
    {peer} 1;
}}
map "$vorntek_verified_edge:$http_x_forwarded_proto" $vorntek_request_scheme {{
    default $scheme;
    "1:https" https;
}}

'''
    old = 'proxy_set_header X-Forwarded-Proto $scheme;'
    if old not in source:
        raise ValueError('Base proxy contract changed; review before generating')
    return prefix + source.replace(old, 'proxy_set_header X-Forwarded-Proto $vorntek_request_scheme;')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--edge-proxy-ip', required=True,
                        help='Verified socket peer of this installation TLS edge; no CIDRs/hostnames')
    parser.add_argument('--output', type=Path,
                        help='Create a NEW configuration file; omitted prints a plan to stdout')
    args = parser.parse_args()
    try:
        rendered = render(args.edge_proxy_ip)
        if args.output:
            with args.output.open('x', encoding='utf-8', newline='\n') as target:
                target.write(rendered)
        else:
            sys.stdout.write(rendered)
    except (ValueError, OSError) as exc:
        parser.exit(2, f'Proxy configuration refused: {exc}\n')


if __name__ == '__main__':
    main()
