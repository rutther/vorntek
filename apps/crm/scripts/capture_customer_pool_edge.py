"""Capture responsive CRM evidence with Microsoft Edge and a temporary session.

The script is local-test-only. It creates an authenticated Django session for
an existing demo user, exposes that cookie from a loopback-only redirect, runs
headless Microsoft Edge at the required widths, and removes the session again.
It never needs or prints the demo password.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen
from urllib.parse import urljoin, urlparse


ADMIN_DIR = Path(__file__).resolve().parents[1]
LOCAL_HOSTS = {'localhost', '127.0.0.1', '::1'}
EDGE_CANDIDATES = (
    Path(os.environ.get('PROGRAMFILES(X86)', '')) / 'Microsoft/Edge/Application/msedge.exe',
    Path(os.environ.get('PROGRAMFILES', '')) / 'Microsoft/Edge/Application/msedge.exe',
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument('--dsn', required=True)
    parser.add_argument('--base-url', default='http://127.0.0.1:26121/')
    parser.add_argument('--output-dir', default=str(ADMIN_DIR / '.runtime' / 'customer-pool-edge-qa'))
    return parser.parse_args()


def require_loopback(value: str, *, label: str, test_database: bool = False) -> None:
    parsed = urlparse(value)
    if parsed.hostname not in LOCAL_HOSTS:
        raise SystemExit(f'{label} must use a loopback host.')
    if test_database and not parsed.path.lstrip('/').endswith('_test'):
        raise SystemExit(f'{label} database name must end in _test.')


def find_edge() -> Path:
    discovered = shutil.which('msedge')
    if discovered:
        return Path(discovered)
    for candidate in EDGE_CANDIDATES:
        if candidate.is_file():
            return candidate
    raise SystemExit('Microsoft Edge executable was not found.')


def find_node() -> Path:
    configured = os.environ.get('NODE_BINARY', '').strip()
    if configured and Path(configured).is_file():
        return Path(configured)
    discovered = shutil.which('node')
    if discovered:
        return Path(discovered)
    raise SystemExit('Node.js is required for the built-in Edge CDP verifier.')


def free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
        candidate.bind(('127.0.0.1', 0))
        return int(candidate.getsockname()[1])


def wait_for_page(port: int, target_url: str) -> str:
    endpoint = f'http://127.0.0.1:{port}/json/list'
    expected = urlparse(target_url)
    for _ in range(100):
        try:
            with urlopen(endpoint, timeout=0.5) as response:
                pages = json.loads(response.read().decode('utf-8'))
            for page in pages:
                actual = urlparse(str(page.get('url') or ''))
                if (
                    page.get('type') == 'page'
                    and actual.hostname == expected.hostname
                    and actual.port == expected.port
                    and actual.path.startswith(expected.path)
                ):
                    return str(page['webSocketDebuggerUrl'])
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(0.1)
    raise RuntimeError(f'Edge did not reach the expected local page: {target_url}')


def main() -> int:
    args = parse_args()
    require_loopback(args.dsn, label='DSN', test_database=True)
    require_loopback(args.base_url, label='Base URL')

    sys.path.insert(0, str(ADMIN_DIR))
    os.environ['DJANGO_SETTINGS_MODULE'] = 'siteos_admin.settings'
    os.environ['SITEOS_ADMIN_DEBUG'] = '1'
    os.environ['SITEOS_ADMIN_DATABASE_URL'] = args.dsn
    os.environ['SITEOS_ADMIN_DATABASE_SSLMODE'] = 'disable'
    os.environ['SITEOS_ADMIN_SECURE_SSL_REDIRECT'] = '0'

    import django

    django.setup()

    from django.contrib.auth import get_user_model
    from django.contrib.sessions.models import Session
    from django.test import Client

    actor = get_user_model().objects.get(username='local-admin', is_active=True)
    client = Client()
    client.force_login(actor)
    session_key = client.cookies['sessionid'].value

    targets = {
        'customer-pool': urljoin(args.base_url, 'admin/sales/customer-pool/'),
        'customer-pool-import': urljoin(args.base_url, 'admin/sales/customer-pool/import/'),
        'existing-leads-reference': urljoin(args.base_url, 'admin/sales/leads/'),
    }
    profiles = (
        ('1440', 1440, 1100, 1),
        ('1280', 1280, 1000, 1),
        ('390', 390, 1100, 1),
        ('320', 320, 1100, 1),
        ('zoom-200', 720, 550, 2),
    )
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    edge = find_edge()
    node = find_node()
    cdp_helper = ADMIN_DIR / 'scripts' / 'edge_cdp_capture.mjs'
    keyboard_helper = ADMIN_DIR / 'scripts' / 'edge_cdp_keyboard.mjs'

    class RedirectHandler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib callback name
            target_key = self.path.removeprefix('/').split('?', 1)[0]
            target = targets.get(target_key)
            if target is None:
                self.send_error(404)
                return
            self.send_response(302)
            self.send_header('Set-Cookie', f'sessionid={session_key}; Path=/; HttpOnly; SameSite=Lax')
            self.send_header('Location', target)
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(('127.0.0.1', 0), RedirectHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    captures: list[dict[str, object]] = []
    keyboard_checks: dict[str, object] | None = None
    try:
        for profile_name, width, height, scale in profiles:
            for target_name in targets:
                screenshot_path = output_dir / f'{target_name}-{profile_name}.png'
                with tempfile.TemporaryDirectory(prefix='new-crown-edge-qa-') as profile_dir:
                    debugging_port = free_loopback_port()
                    command = [
                        str(edge),
                        '--headless=new',
                        '--disable-gpu',
                        '--no-first-run',
                        '--no-default-browser-check',
                        f'--user-data-dir={profile_dir}',
                        f'--window-size={width},{height}',
                        f'--force-device-scale-factor={scale}',
                        f'--remote-debugging-port={debugging_port}',
                        '--remote-allow-origins=*',
                        f'http://127.0.0.1:{server.server_port}/{target_name}',
                    ]
                    edge_process = subprocess.Popen(
                        command,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    try:
                        websocket_url = wait_for_page(debugging_port, targets[target_name])
                        completed = subprocess.run(
                            [
                                str(node),
                                str(cdp_helper),
                                websocket_url,
                                str(screenshot_path),
                                str(width),
                                str(height),
                                str(scale),
                            ],
                            capture_output=True,
                            text=True,
                            encoding='utf-8',
                            errors='replace',
                            timeout=30,
                            check=False,
                        )
                        if completed.returncode != 0:
                            raise RuntimeError(completed.stderr.strip() or 'CDP capture failed.')
                        metrics = json.loads(completed.stdout)
                        if profile_name == '1440' and target_name == 'customer-pool':
                            keyboard = subprocess.run(
                                [str(node), str(keyboard_helper), websocket_url],
                                capture_output=True,
                                text=True,
                                encoding='utf-8',
                                errors='replace',
                                timeout=30,
                                check=False,
                            )
                            if keyboard.returncode != 0:
                                raise RuntimeError(
                                    keyboard.stderr.strip() or 'Edge keyboard verification failed.'
                                )
                            keyboard_checks = json.loads(keyboard.stdout)
                    finally:
                        edge_process.terminate()
                        try:
                            edge_process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            edge_process.kill()
                            edge_process.wait(timeout=5)
                if not screenshot_path.is_file():
                    raise SystemExit(f'Edge did not create {screenshot_path.name}.')
                captures.append({
                    'target': target_name,
                    'profile': profile_name,
                    'css_window_width': width,
                    'css_window_height': height,
                    'device_scale_factor': scale,
                    'file': screenshot_path.name,
                    'bytes': screenshot_path.stat().st_size,
                    'metrics': metrics,
                })
    finally:
        server.shutdown()
        server.server_close()
        Session.objects.filter(session_key=session_key).delete()

    manifest = {
        'browser': 'Microsoft Edge',
        'base_url': args.base_url,
        'synthetic_local_database': urlparse(args.dsn).path.lstrip('/'),
        'session_removed': not Session.objects.filter(session_key=session_key).exists(),
        'keyboard_checks': keyboard_checks,
        'captures': captures,
    }
    manifest_path = output_dir / 'manifest.json'
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
