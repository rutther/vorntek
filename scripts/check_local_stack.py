"""Read-only smoke check for an isolated local website/CRM entrypoint."""
import argparse
import json
import time
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlparse


class LocalRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        require_local(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def require_local(url):
    parsed = urlparse(url)
    if parsed.scheme != 'http' or parsed.hostname not in {'localhost', '127.0.0.1', '::1'}:
        raise ValueError('Only a loopback HTTP target is allowed.')
    if parsed.username or parsed.password:
        raise ValueError('Credentials must not be included in the target URL.')


def check(url):
    require_local(url)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), LocalRedirects())
    results = []
    for route in ('', 'contact/', 'admin/login/', 'healthz/', 'static/console/system-users.js'):
        with opener.open(urljoin(url, route), timeout=10) as response:
            if response.status != 200 or not response.read():
                raise RuntimeError('A local website/CRM/static check failed.')
        results.append(route or '/')
    with opener.open(urljoin(url, 'api/marketing/measurement-config/'), timeout=10) as response:
        data = json.load(response)
    if data.get('meta_pixel_enabled') is not False or data.get('google_tag_enabled') is not False:
        raise RuntimeError('Isolated stack unexpectedly enables external browser measurement.')
    return {'result': 'passed', 'routes': results, 'measurement_disabled': True,
            'scope': 'read-only smoke; not complete business/browser/recovery acceptance'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--wait-seconds', type=int, default=0)
    args = parser.parse_args()
    require_local(args.url)
    if not 0 <= args.wait_seconds <= 300:
        parser.error('--wait-seconds must be between 0 and 300.')
    deadline = time.monotonic() + args.wait_seconds
    while True:
        try:
            print(json.dumps(check(args.url)))
            break
        except (OSError, ValueError, RuntimeError):
            if time.monotonic() >= deadline:
                raise SystemExit('Local stack smoke check failed; inspect service state privately.')
            time.sleep(2)
