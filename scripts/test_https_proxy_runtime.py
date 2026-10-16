"""Real isolated Linux Nginx/HTTP checks; no Docker or installed config changes."""
import argparse
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest

from render_https_proxy import ROOT, render


class Echo(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({'client': self.headers.get('X-Forwarded-For'),
                           'scheme': self.headers.get('X-Forwarded-Proto')}).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class RealProxyTests(unittest.TestCase):
    nginx_bin = 'nginx'
    baseline = False

    @classmethod
    def setUpClass(cls):
        cls.workspace = tempfile.TemporaryDirectory(prefix='vorntek-proxy-contract-')
        cls.root = Path(cls.workspace.name)
        cls.backend = ThreadingHTTPServer(('127.0.0.1', 0), Echo)
        cls.thread = threading.Thread(target=cls.backend.serve_forever, daemon=True)
        cls.thread.start()
        with socket.socket() as reserved, socket.socket() as edge_reserved:
            reserved.bind(('127.0.0.1', 0))
            cls.port = reserved.getsockname()[1]
            edge_reserved.bind(('127.0.0.1', 0))
            cls.edge_port = edge_reserved.getsockname()[1]
        source = ((ROOT / 'deploy/nginx.conf').read_text() if cls.baseline
                  else render('127.0.0.2'))
        source = source.replace('listen 8080;', f'listen 127.0.0.1:{cls.port};')
        source = source.replace('http://crm:8000', f'http://127.0.0.1:{cls.backend.server_port}')
        source = source.replace('/var/log/nginx/access.log', str(cls.root / 'access.log'))
        # Represents the owned TLS edge: it overwrites, rather than appends,
        # user-supplied headers. No TLS certificate assertion is made here.
        edge = f'''
server {{
    listen 127.0.0.1:{cls.edge_port};
    location / {{
        proxy_bind 127.0.0.2;
        proxy_pass http://127.0.0.1:{cls.port};
        proxy_set_header Host vorntek.example;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto https;
    }}
}}
'''
        config = cls.root / 'nginx.conf'
        config.write_text(f'pid {cls.root}/nginx.pid;\nerror_log {cls.root}/error.log;\n'
                          'events {}\nhttp {\n' + source + edge + '\n}\n')
        cls.process = subprocess.Popen([cls.nginx_bin, '-p', str(cls.root), '-c', str(config),
                                        '-g', 'daemon off;'], stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE)
        try:
            for _ in range(50):
                if cls.process.poll() is not None:
                    raise RuntimeError(cls.process.communicate()[1].decode())
                try:
                    cls.request('127.0.0.3', {})
                    break
                except OSError:
                    time.sleep(.1)
            else:
                raise RuntimeError('Owned Nginx fixture did not become ready')
        except BaseException:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls):
        if cls.process.poll() is None:
            cls.process.terminate()
        cls.process.communicate(timeout=10)
        cls.backend.shutdown()
        cls.backend.server_close()
        cls.thread.join(timeout=5)
        cls.workspace.cleanup()

    @classmethod
    def request(cls, peer, headers, *, via_edge=False):
        conn = http.client.HTTPConnection('127.0.0.1', cls.edge_port if via_edge else cls.port,
                                          timeout=3, source_address=(peer, 0))
        try:
            conn.request('GET', '/api/probe/', headers=headers)
            response = conn.getresponse()
            if response.status != 200:
                raise AssertionError(f'Unexpected proxy status: {response.status}')
            return json.loads(response.read())
        finally:
            conn.close()

    def test_exact_edge_preserves_verified_client_and_https(self):
        self.assertEqual(self.request('127.0.0.2', {
            'X-Forwarded-For': '203.0.113.9', 'X-Forwarded-Proto': 'https',
        }), {'client': '203.0.113.9', 'scheme': 'https'})

    def test_other_peer_cannot_spoof_client_or_secure_protocol(self):
        self.assertEqual(self.request('127.0.0.3', {
            'X-Forwarded-For': '198.51.100.200', 'X-Forwarded-Proto': 'https',
        }), {'client': '127.0.0.3', 'scheme': 'http'})

    def test_invalid_edge_protocol_is_not_promoted_to_https(self):
        self.assertEqual(self.request('127.0.0.2', {
            'X-Forwarded-For': '203.0.113.9', 'X-Forwarded-Proto': 'https,http',
        }), {'client': '203.0.113.9', 'scheme': 'http'})

    def test_edge_overwrites_forgery_and_keeps_distinct_clients_distinct(self):
        for client in ('127.0.0.3', '127.0.0.4'):
            with self.subTest(client=client):
                self.assertEqual(self.request(client, {
                    'X-Forwarded-For': '198.51.100.200, 192.0.2.1',
                    'X-Forwarded-Proto': 'http',
                }, via_edge=True), {'client': client, 'scheme': 'https'})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nginx-bin', default=shutil.which('nginx'))
    parser.add_argument('--baseline', action='store_true', help='Reproduce the old lost-edge contract')
    args = parser.parse_args()
    if not args.nginx_bin:
        parser.error('Install/use a Linux Nginx binary; this check must not silently skip')
    RealProxyTests.nginx_bin = args.nginx_bin
    RealProxyTests.baseline = args.baseline
    outcome = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(RealProxyTests))
    raise SystemExit(not outcome.wasSuccessful())
