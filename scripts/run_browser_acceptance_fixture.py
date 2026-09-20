"""Serve an ephemeral synthetic website/CRM fixture for actual browser review.

This is a SQLite UI fixture, not an installation, PostgreSQL, Compose, Nginx or
security acceptance. It creates a throwaway Django test database, binds only to
127.0.0.1, disables every external writer and uses obviously synthetic records.
"""

from __future__ import annotations

import argparse
import csv
import ipaddress
import json
import mimetypes
import os
from pathlib import Path
import secrets
import socket
from socketserver import ThreadingMixIn
import sys
import time
from urllib.parse import unquote
from wsgiref.simple_server import WSGIServer, WSGIRequestHandler, make_server
from wsgiref.util import FileWrapper


ROOT = Path(__file__).resolve().parents[1]
CRM = ROOT / 'apps' / 'crm'
WEBSITE = ROOT / 'apps' / 'website'
RUNTIME = ROOT / '.runtime' / 'browser-acceptance'
BROWSER_USERNAME = 'browser-acceptance-admin'
STANDARD21_HEADERS = (
    'phone', 'email', 'country_name', 'country', 'person_name', 'company',
    'value', 'email_2', 'email_3', 'phone_2', 'phone_3', 'route_type',
    'route_tier', 'account_id', 'whatsapp_confirmed', 'evidence_V',
    'identity_I', 'tech_T', 'priority_P', 'restriction_note', 'source_channel',
)


def validate_runtime(port: int, review_seconds: int) -> None:
    if not 1024 <= port <= 65535:
        raise ValueError('port must be an unprivileged TCP port')
    if not 30 <= review_seconds <= 1800:
        raise ValueError('review duration must be between 30 and 1800 seconds')
    if (CRM / '.env').exists():
        raise ValueError('application .env files are forbidden for this fixture')


def fixture_csv() -> bytes:
    row = {header: '' for header in STANDARD21_HEADERS}
    row.update({
        'phone': '+254700000021',
        'email': 'browser-import@example.invalid',
        'country_name': '肯尼亚',
        'country': 'KE',
        'person_name': 'Synthetic Browser Buyer',
        'company': 'Synthetic Browser Import Ltd',
        'value': '36.0',
        'route_type': '公司总机',
        'route_tier': '企业总机',
        'account_id': 'SYN-BROWSER-0021',
        'evidence_V': 'V3',
        'identity_I': 'I2',
        'priority_P': 'P2',
        'source_channel': 'research_public',
    })
    from io import StringIO
    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=STANDARD21_HEADERS, lineterminator='\r\n')
    writer.writeheader()
    writer.writerow(row)
    return buffer.getvalue().encode('utf-8')


def prepare_environment(port: int) -> None:
    for key in list(os.environ):
        if key.startswith(('PG', 'SITEOS_', 'NEWCROWN_', 'VORNTEK_', 'DJANGO_')):
            del os.environ[key]
    origin = f'http://127.0.0.1:{port}'
    os.environ.update({
        'DJANGO_SETTINGS_MODULE': 'siteos_admin.settings',
        'SITEOS_ADMIN_DEBUG': '1',
        'SITEOS_ADMIN_SECRET_KEY': 'synthetic-browser-acceptance-signing-key',
        'SITEOS_SECRET_VAULT_KEY': '0' * 64,
        'SITEOS_ADMIN_ALLOWED_HOSTS': '127.0.0.1,localhost,testserver',
        'SITEOS_ADMIN_CSRF_TRUSTED_ORIGINS': origin,
        'SITEOS_PUBLIC_FORM_ALLOWED_ORIGINS': origin,
        'SITEOS_ADMIN_PUBLIC_URL': origin + '/admin',
        'NEWCROWN_ALLOW_EXTERNAL_IO': '0',
        'NEWCROWN_RUN_SCHEDULED_TASKS': '0',
        'SITEOS_WHATSAPP_ALLOW_LIVE_SEND': '0',
        'SITEOS_EMAIL_BACKEND': 'django.core.mail.backends.locmem.EmailBackend',
        'SITEOS_DEFAULT_FROM_EMAIL': 'browser-fixture@example.invalid',
    })


def guard_non_loopback_sockets() -> None:
    original_connect = socket.socket.connect

    def local_connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            try:
                allowed = ipaddress.ip_address(address[0]).is_loopback
            except (ValueError, TypeError):
                allowed = address[0] == 'localhost'
            if not allowed:
                raise PermissionError('browser fixture refuses non-loopback TCP connections')
        return original_connect(sock, address)

    socket.socket.connect = local_connect


def unmanaged_models():
    from console.models import AuditLog
    from leads.models import (
        Activity, Company, CompanyContactPoint, CompanyPoolState, ConsentRecord,
        Contact, CrmMutationReceipt, CustomerExportJob, CustomerImportBatch,
        CustomerImportRow, CustomerPoolRow, CustomerSource, LeadConversion,
        LeadEventOutbox, LeadFormDefinition, LeadSubmission, Opportunity,
        SalesTeam, SalesTeamMember, SavedView, Task,
    )
    from marketing.models import CanonicalEvent, MarketingIntegration, MarketingProvider
    from sitecore.models import Cta, PageRoute, Site, SiteLocale
    return (
        Site, SiteLocale, PageRoute, Cta, CanonicalEvent, MarketingProvider,
        MarketingIntegration, LeadFormDefinition, SalesTeam, SalesTeamMember,
        SavedView, LeadSubmission, ConsentRecord, Company, Contact, Opportunity,
        LeadConversion, Activity, Task, LeadEventOutbox, AuditLog,
        CrmMutationReceipt, CompanyPoolState, CompanyContactPoint, CustomerSource,
        CustomerImportBatch, CustomerImportRow, CustomerPoolRow, CustomerExportJob,
    )


def create_schema() -> None:
    from django.db import connection
    existing = set(connection.introspection.table_names())
    with connection.schema_editor() as editor:
        for model in unmanaged_models():
            if model._meta.db_table not in existing:
                editor.create_model(model)
                existing.add(model._meta.db_table)


def seed_fixture(port: int, password: str) -> None:
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Group
    from console.access import GROUP_BY_ROLE, ROLE_SALES, ROLE_SYSTEM_ADMIN
    from leads.customer_pool_services import create_manual_customer, publish_reviewed_customer
    from leads.models import LeadFormDefinition, SalesTeam, SalesTeamMember
    from sitecore.models import Site, SiteLocale

    admin_group = Group.objects.create(name=GROUP_BY_ROLE[ROLE_SYSTEM_ADMIN])
    admin = get_user_model().objects.create_user(
        username=BROWSER_USERNAME,
        email='browser-admin@example.invalid',
        password=password,
        is_staff=True,
    )
    admin.groups.add(admin_group)
    sales_group = Group.objects.create(name=GROUP_BY_ROLE[ROLE_SALES])
    sales = get_user_model().objects.create_user(
        username='browser-acceptance-sales',
        email='browser-sales@example.invalid',
        password=None,
    )
    sales.groups.add(sales_group)
    site = Site.objects.create(
        code='siteos_demo', name='Vorntek', base_url=f'http://127.0.0.1:{port}',
        default_locale='en', enabled=True, config_json={},
    )
    SiteLocale.objects.create(
        site=site, locale_code='en', label='English', direction='ltr',
        is_default=True, enabled=True, sort_order=10,
    )
    team = SalesTeam.objects.create(site=site, code='default', name='Default Sales', enabled=True)
    SalesTeamMember.objects.create(team=team, user=sales, membership_role='member')
    LeadFormDefinition.objects.create(
        site=site, locale=None, code='project-inquiry', name='Project inquiry',
        category='industrial', channel='website', scope_type='site', scope_value='*',
        status='active', capi_enabled=False, notify_emails='',
        success_message='Inquiry accepted.', form_schema={},
        config_json={'sales_team_code': 'default'},
    )
    draft = create_manual_customer(
        site=site, actor=admin,
        idempotency_token='browser-fixture-create-available-0001',
        company_name='Synthetic Available Company', country='Kenya',
        source_type='manual', source_detail='Ephemeral browser acceptance fixture',
        email='available@example.invalid', assignment_mode='review',
    )
    publish_reviewed_customer(
        company_id=draft.company.pk, site=site, actor=admin,
        expected_version=draft.pool_state.version,
        idempotency_token='browser-fixture-publish-available-0001',
        contact_point_id=draft.company.contact_points.get().pk, team=team,
    )
    create_manual_customer(
        site=site, actor=admin,
        idempotency_token='browser-fixture-create-review-0001',
        company_name='Synthetic Review Company', country='Ghana',
        source_type='research', source_detail='Ephemeral browser acceptance fixture',
        email='review@example.invalid', assignment_mode='review',
    )


def browser_application():
    from django.contrib.staticfiles.handlers import StaticFilesHandler
    from django.core.wsgi import get_wsgi_application
    django_application = StaticFilesHandler(get_wsgi_application())

    def dispatch(environ, start_response):
        route = environ.get('PATH_INFO', '/')
        if route.startswith(('/admin/', '/api/', '/healthz/', '/console/', '/static/')):
            return django_application(environ, start_response)
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
        start_response('200 OK', [
            ('Content-Type', content_type), ('Content-Length', str(path.stat().st_size)),
            ('Cache-Control', 'no-store'),
        ])
        return [] if environ['REQUEST_METHOD'] == 'HEAD' else FileWrapper(path.open('rb'))

    return dispatch


def evidence() -> dict:
    from leads.models import (
        Company, CustomerExportJob, CustomerImportBatch, CustomerPoolRow, LeadSubmission,
    )
    return {
        'format': 'vorntek-browser-fixture-evidence-v1',
        'synthetic': True,
        'database': 'ephemeral SQLite test database',
        'external_io': False,
        'lead_submissions': LeadSubmission.objects.count(),
        'companies': Company.objects.count(),
        'import_batches': CustomerImportBatch.objects.count(),
        'imported_pool_rows': CustomerPoolRow.objects.count(),
        'export_jobs': CustomerExportJob.objects.count(),
        'limits': [
            'not PostgreSQL acceptance', 'not Compose/Nginx acceptance',
            'browser download bytes require separate filesystem verification',
        ],
    }


class QuietHandler(WSGIRequestHandler):
    def log_message(self, *args):
        pass


class ThreadedServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--review-seconds', type=int, default=900)
    args = parser.parse_args()
    try:
        validate_runtime(args.port, args.review_seconds)
    except ValueError as exc:
        parser.error(str(exc))

    prepare_environment(args.port)
    guard_non_loopback_sockets()
    RUNTIME.mkdir(parents=True, exist_ok=True)
    (RUNTIME / 'standard21.csv').write_bytes(fixture_csv())
    password = secrets.token_urlsafe(24)
    credential_path = RUNTIME / 'credentials.json'
    sys.path.insert(0, str(CRM))
    os.chdir(CRM)

    import django
    django.setup()
    from django.test.runner import DiscoverRunner
    runner = DiscoverRunner(verbosity=0, interactive=False)
    old_config = runner.setup_databases()
    report = None
    try:
        credential_path.write_text(
            json.dumps({'username': BROWSER_USERNAME, 'password': password}, indent=2) + '\n',
            encoding='utf-8',
        )
        if os.name == 'posix':
            credential_path.chmod(0o600)
        create_schema()
        seed_fixture(args.port, password)
        with make_server(
            '127.0.0.1', args.port, browser_application(),
            server_class=ThreadedServer, handler_class=QuietHandler,
        ) as server:
            server.timeout = 0.5
            print(f'BROWSER_ACCEPTANCE_READY http://127.0.0.1:{args.port}/', flush=True)
            print(f'Fixture upload: {RUNTIME / "standard21.csv"}', flush=True)
            print(f'Fixture credentials: {credential_path}', flush=True)
            deadline = time.monotonic() + args.review_seconds
            try:
                while time.monotonic() < deadline:
                    server.handle_request()
            except KeyboardInterrupt:
                pass
        report = evidence()
        (RUNTIME / 'evidence.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
        )
        print(json.dumps(report, ensure_ascii=False), flush=True)
    finally:
        credential_path.unlink(missing_ok=True)
        runner.teardown_databases(old_config)
    return 0 if report is not None else 1


if __name__ == '__main__':
    raise SystemExit(main())
