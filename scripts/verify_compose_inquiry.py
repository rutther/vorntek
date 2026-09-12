"""One synthetic inquiry through a fresh, explicitly authorized Compose stack.

Run from the host source directory after startup:
docker compose exec -T -e NEWCROWN_SYNTHETIC_ACCEPTANCE=1 crm python - < scripts/verify_compose_inquiry.py
Refuses nonempty business data and enabled external integrations. Never use on
production; successful execution intentionally retains one synthetic inquiry.
"""
import json
import os
from datetime import timedelta
from urllib.parse import urlparse
import uuid

if os.environ.get('NEWCROWN_SYNTHETIC_ACCEPTANCE') != '1':
    raise SystemExit('Explicit synthetic acceptance acknowledgement is required.')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'siteos_admin.settings')
import django
django.setup()
from django.conf import settings
from django.db import connection
from django.utils import timezone
import requests
from leads.models import Company, LeadSubmission, LeadEventOutbox
from marketing.models import MarketingIntegration
from sitecore.models import Site

if settings.NEWCROWN_ALLOW_EXTERNAL_IO or settings.SITEOS_WHATSAPP_ALLOW_LIVE_SEND:
    raise SystemExit('Refusing an environment with external sending enabled.')
if connection.vendor != 'postgresql' or connection.settings_dict['HOST'] != 'db' or connection.settings_dict['NAME'] != 'newcrown':
    raise SystemExit('Expected the independent Compose database.')
origin = os.environ.get('NEWCROWN_SITE_URL', '').rstrip('/')
parsed = urlparse(origin)
if parsed.scheme != 'http' or parsed.hostname not in {'localhost', '127.0.0.1'} or parsed.username or parsed.password:
    raise SystemExit('Expected a loopback test origin without credentials.')
if Site.objects.get(code='siteos_demo').base_url.rstrip('/') != origin:
    raise SystemExit('Site origin does not match the test instance.')
if any(model.objects.exists() for model in (Company, LeadSubmission, LeadEventOutbox)) or MarketingIntegration.objects.filter(enabled=True).exists():
    raise SystemExit('Refusing existing business data or enabled integrations; do not clear them to bypass this check.')

run_id = uuid.uuid4().hex
email = f'compose-{run_id}@example.invalid'
payload = {
    'website': '', 'full_name': 'Synthetic Compose Contact',
    'company': f'SYNTHETIC DEPLOY CHECK {run_id[:8]}', 'country': 'Ghana',
    'email': email, 'phone': '', 'message': 'Synthetic deployment check. Do not contact.',
    'source_url': origin + '/contact/', 'source_channel': 'website',
    'client_event_id': str(uuid.uuid4()),
    'extra_fields': {'business_line': 'circulatingWaterControl',
                     'product_category': 'Circulating water control systems',
                     'application_context': 'Synthetic monitoring only'},
    'consent': {'contact': True, 'privacy_notice': True, 'marketing': False},
    'form_started_at': (timezone.now() - timedelta(seconds=10)).isoformat(),
}
session = requests.Session()
session.trust_env = False
session.headers.update({'Host': parsed.netloc, 'Origin': origin})
endpoint = 'http://website:8080/admin/api/leads/forms/project-inquiry/submit/'
invalid = session.post(endpoint, json={**payload, 'company': ''}, timeout=15)
assert invalid.status_code == 400 and not invalid.json()['ok']
assert not LeadSubmission.objects.exists()
created = session.post(endpoint, json=payload, timeout=15)
created.raise_for_status()
result = created.json()
assert result['ok'] and not result['duplicate']
row = LeadSubmission.objects.get(pk=result['submission_id'])
assert row.email == email and row.company == payload['company']
assert row.payload_json['business_line'] == 'circulatingWaterControl' and 'capacity' not in row.payload_json
replay = session.post(endpoint, json=payload, timeout=15)
replay.raise_for_status()
assert replay.json()['ok'] and replay.json()['duplicate']
assert LeadSubmission.objects.count() == 1 and not LeadEventOutbox.objects.exists()
print(json.dumps({'result': 'passed', 'submission_id': row.pk,
                  'invalid_rejected': True, 'duplicate_deduplicated': True,
                  'database_matches': True, 'outbox_count': 0,
                  'scope': 'synthetic website-to-database smoke, not full sales lifecycle'}))
