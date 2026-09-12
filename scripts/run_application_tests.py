"""Run the complete SQLite compatibility suite with a clean synthetic environment.

External-I/O branches are enabled only for mocked contract tests. Python socket
connections outside loopback are rejected. This is not an OS sandbox; use an
ephemeral, credential-free CI runner, never production. Real PostgreSQL lifecycle
and Docker acceptance are separate commands.
"""
import argparse
from contextlib import ExitStack
import ipaddress
import os
from pathlib import Path
import secrets
import socket
import sys
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
CRM = ROOT/'apps'/'crm'


def allowed(address):
    if not isinstance(address, tuple) or not address:
        return False
    host = str(address[0]).strip('[]')
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('labels', nargs='*', help='Optional Django test labels; default is the whole suite.')
    args = parser.parse_args()
    if (CRM/'.env').exists():
        parser.error('Refusing to run with an application .env present.')
    for key in list(os.environ):
        if key.startswith(('PG', 'SITEOS_', 'NEWCROWN_', 'VORNTEK_', 'DJANGO_')):
            del os.environ[key]
    os.environ.update({
        'DJANGO_SETTINGS_MODULE': 'siteos_admin.settings', 'SITEOS_ADMIN_DEBUG': '1',
        'SITEOS_ADMIN_SECRET_KEY': secrets.token_hex(32),
        'SITEOS_SECRET_VAULT_KEY': secrets.token_hex(32),
        'NEWCROWN_ALLOW_EXTERNAL_IO': '1', 'NEWCROWN_RUN_SCHEDULED_TASKS': '0',
        'SITEOS_WHATSAPP_ALLOW_LIVE_SEND': '0',
        'SITEOS_EMAIL_BACKEND': 'django.core.mail.backends.locmem.EmailBackend',
        'SITEOS_DEFAULT_FROM_EMAIL': 'local-test@example.invalid',
    })
    os.chdir(CRM)
    sys.path.insert(0, str(CRM))

    def guarded(original):
        def call(sock, address, *extra):
            if not allowed(address):
                raise OSError('Test runner refuses non-loopback socket connections; mock the transport.')
            return original(sock, address, *extra)
        return call

    original_sendto = socket.socket.sendto
    def guarded_sendto(sock, data, *arguments):
        if not arguments or not allowed(arguments[-1]):
            raise OSError('Test runner refuses non-loopback datagrams; mock the transport.')
        return original_sendto(sock, data, *arguments)

    with ExitStack() as stack:
        stack.enter_context(patch.object(socket.socket, 'connect', guarded(socket.socket.connect)))
        stack.enter_context(patch.object(socket.socket, 'connect_ex', guarded(socket.socket.connect_ex)))
        stack.enter_context(patch.object(socket.socket, 'sendto', guarded_sendto))
        import django
        django.setup()
        from django.conf import settings
        from django.test.utils import get_runner
        if settings.DATABASES['default']['ENGINE'] != 'django.db.backends.sqlite3':
            raise RuntimeError('Compatibility tests require isolated SQLite test mode.')
        print('Synthetic SQLite suite; Python non-loopback sockets blocked; no real platform acceptance.', flush=True)
        runner = get_runner(settings)(verbosity=1, interactive=False, keepdb=False)
        failures = runner.run_tests(args.labels or ['.'])
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
