"""Loopback-only website/CRM test server. Never a production server.

The PostgreSQL test harness supplies isolated connection settings and starts this
process. Refuse existing .env files, non-test databases and enabled integrations.
"""
import argparse
import ipaddress
import mimetypes
import os
from pathlib import Path
import socket
from socketserver import ThreadingMixIn
import sys
from urllib.parse import unquote
from wsgiref.simple_server import WSGIServer, WSGIRequestHandler, make_server
from wsgiref.util import FileWrapper


ROOT = Path(__file__).resolve().parents[1]
CRM = ROOT / 'apps' / 'crm'
WEBSITE = ROOT / 'apps' / 'website'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, required=True)
    args = parser.parse_args()
    if (CRM / '.env').exists():
        parser.error('Application .env files are not allowed for the isolated test server.')
    if os.getenv('SITEOS_ADMIN_DATABASE_HOST') != '127.0.0.1' or not os.getenv('SITEOS_ADMIN_DATABASE_NAME', '').endswith('_test'):
        parser.error('Only explicit loopback PostgreSQL test databases are allowed.')
    if os.getenv('SITEOS_ADMIN_DATABASE_URL') or os.getenv('SITEOS_ADMIN_DATABASE_URL_FILE'):
        parser.error('Use the harness connection fields, not a database URL.')
    if os.getenv('NEWCROWN_ALLOW_EXTERNAL_IO') != '0':
        parser.error('External I/O must be explicitly disabled.')

    # Independent test-only TCP guard in addition to application feature flags.
    # Browser networking is not governed by this server-side guard.
    original_connect = socket.socket.connect

    def local_connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            try:
                allowed = ipaddress.ip_address(address[0]).is_loopback
            except ValueError:
                allowed = address[0] == 'localhost'
            if not allowed:
                raise PermissionError('The local test server refuses non-loopback TCP connections.')
        return original_connect(sock, address)

    socket.socket.connect = local_connect
    sys.path.insert(0, str(CRM))
    os.environ['DJANGO_SETTINGS_MODULE'] = 'siteos_admin.settings'
    from django.core.wsgi import get_wsgi_application
    from django.contrib.staticfiles.handlers import StaticFilesHandler
    application = StaticFilesHandler(get_wsgi_application())

    def website_and_crm(environ, start_response):
        route = environ.get('PATH_INFO', '/')
        if route.startswith(('/admin/', '/api/', '/healthz/', '/console/', '/static/')):
            return application(environ, start_response)
        if environ['REQUEST_METHOD'] not in {'GET', 'HEAD'}:
            start_response('405 Method Not Allowed', [('Content-Length', '0')])
            return []
        path = (WEBSITE / unquote(route).lstrip('/')).resolve()
        try:
            relative = path.relative_to(WEBSITE)
            if any(part.startswith('.') for part in relative.parts):
                raise ValueError('hidden path')
        except ValueError:
            start_response('404 Not Found', [('Content-Length', '0')])
            return []
        if path.is_dir():
            path = path / 'index.html'
        if not path.is_file():
            start_response('404 Not Found', [('Content-Length', '0')])
            return []
        content_type = mimetypes.guess_type(path)[0] or 'application/octet-stream'
        start_response('200 OK', [('Content-Type', content_type), ('Content-Length', str(path.stat().st_size)),
                                  ('Cache-Control', 'no-store')])
        return [] if environ['REQUEST_METHOD'] == 'HEAD' else FileWrapper(path.open('rb'))

    class QuietHandler(WSGIRequestHandler):
        def log_message(self, *args):
            pass  # No query strings or synthetic contact details in console logs.

    class ThreadedServer(ThreadingMixIn, WSGIServer):
        daemon_threads = True

    with make_server('127.0.0.1', args.port, website_and_crm, server_class=ThreadedServer, handler_class=QuietHandler) as server:
        print('Isolated website/CRM HTTP test server ready.', flush=True)
        server.serve_forever()


if __name__ == '__main__':
    main()
