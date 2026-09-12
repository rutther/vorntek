"""Verify website lead capture through CRM conversion on the local candidate.

The verifier is fail-closed to loopback HTTP and a local PostgreSQL database
ending in ``_test``.  It submits only synthetic contact data and never enables
or dispatches an external integration.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
import uuid
from datetime import timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_HOSTS = {'127.0.0.1', 'localhost', '::1'}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:26121/')
    parser.add_argument('--dsn', default=os.environ.get('SITEOS_TEST_DATABASE_URL', ''))
    args = parser.parse_args()
    if not args.dsn:
        parser.error('Provide SITEOS_TEST_DATABASE_URL or --dsn for the isolated test database.')
    return args


def require_safe_local_targets(base_url: str, dsn: str) -> None:
    http_target = urlparse(base_url)
    database_target = urlparse(dsn)
    if http_target.scheme != 'http' or http_target.hostname not in LOCAL_HOSTS:
        raise SystemExit('Refusing to exercise a non-loopback HTTP server.')
    if database_target.scheme not in {'postgres', 'postgresql'}:
        raise SystemExit('The verification database must use PostgreSQL.')
    if database_target.hostname not in LOCAL_HOSTS:
        raise SystemExit('Refusing to inspect a non-local PostgreSQL host.')
    if not database_target.path.lstrip('/').endswith('_test'):
        raise SystemExit('The verification database name must end in _test.')


def decoded(response: requests.Response) -> str:
    response.encoding = 'utf-8'
    return response.text


def csrf_token(html: str) -> str:
    match = re.search(
        r'name=["\']csrfmiddlewaretoken["\'][^>]*value=["\']([^"\']+)',
        html,
        flags=re.IGNORECASE,
    )
    if match is None:
        raise AssertionError('The expected CSRF form token was not rendered.')
    return match.group(1)


def login(base_url: str, *, username: str, password: str) -> requests.Session:
    session = requests.Session()
    login_url = urljoin(base_url, 'admin/login/')
    page = session.get(login_url, timeout=10)
    page.raise_for_status()
    response = session.post(
        login_url,
        data={
            'username': username,
            'password': password,
            'csrfmiddlewaretoken': csrf_token(decoded(page)),
            'next': '/admin/',
        },
        headers={'Referer': login_url},
        timeout=10,
        allow_redirects=True,
    )
    response.raise_for_status()
    if '/admin/login/' in response.url or 'sessionid' not in session.cookies:
        raise AssertionError(f'Local candidate login failed for {username}.')
    return session


def post_form(
    session: requests.Session,
    url: str,
    *,
    referer: str,
    csrf: str,
    data: dict[str, str] | list[tuple[str, str]],
    accept_json: bool = False,
) -> requests.Response:
    if isinstance(data, dict):
        payload: dict[str, str] | list[tuple[str, str]] = {
            **data,
            'csrfmiddlewaretoken': csrf,
        }
    else:
        payload = [*data, ('csrfmiddlewaretoken', csrf)]
    headers = {'Referer': referer}
    if accept_json:
        headers['Accept'] = 'application/json'
    response = session.post(
        url,
        data=payload,
        headers=headers,
        timeout=15,
        allow_redirects=not accept_json,
    )
    response.raise_for_status()
    return response


def main() -> int:
    args = parse_args()
    require_safe_local_targets(args.base_url, args.dsn)
    password = os.environ.get('SITEOS_CANDIDATE_PASSWORD', '')

    sys.path.insert(0, str(PROJECT_ROOT))
    os.environ['DJANGO_SETTINGS_MODULE'] = 'siteos_admin.settings'
    os.environ['SITEOS_ADMIN_DEBUG'] = '1'
    os.environ['SITEOS_ADMIN_DATABASE_URL'] = args.dsn
    os.environ['SITEOS_ADMIN_DATABASE_SSLMODE'] = 'disable'
    os.environ['SITEOS_ADMIN_SECURE_SSL_REDIRECT'] = '0'

    import django

    django.setup()

    from django.contrib.auth import get_user_model
    from django.utils import timezone

    from leads.models import (
        Activity,
        Company,
        CompanyContactPoint,
        CompanyPoolState,
        Contact,
        CustomerSource,
        LeadConversion,
        LeadEventOutbox,
        LeadFormDefinition,
        LeadSubmission,
        Opportunity,
        Task,
    )
    from marketing.models import MarketingIntegration
    from sitecore.models import Site

    base_url = args.base_url.rstrip('/') + '/'
    site = Site.objects.get(code='siteos_demo')
    admin_user = get_user_model().objects.get(username='local-admin')
    sales_user = get_user_model().objects.get(username='local-sales')
    if not password:
        password = secrets.token_urlsafe(24)
        for local_user in (admin_user, sales_user):
            local_user.set_password(password)
            local_user.save(update_fields=['password'])
    admin = login(base_url, username=admin_user.username, password=password)
    sales = login(base_url, username=sales_user.username, password=password)

    assert not MarketingIntegration.objects.filter(
        site=site,
        enabled=True,
    ).exclude(provider__code='whatsapp').exists(), (
        'External marketing integrations must stay disabled for this local verifier.'
    )

    run_key = uuid.uuid4().hex[:10]
    client_event_id = str(uuid.uuid4())
    company_name = f'Vorntek Synthetic Industrial Buyer {run_key}'
    email = f'website-qa-{run_key}@example.test'
    payload = {
        'website': '',
        'full_name': 'Synthetic Website QA Contact',
        'company': company_name,
        'country': 'Ghana',
        'email': email,
        'phone': '+1 202 555 0101',
        'message': 'Synthetic local inquiry for website-to-CRM verification. Never contact.',
        'source_url': 'https://local-website.test/contact/?utm_source=facebook&utm_medium=cpc',
        'referrer_url': 'https://local-website.test/',
        'source_channel': 'meta_ads',
        'source_detail': 'Synthetic Meta-attributed website form visit',
        'utm': {
            'utm_source': 'facebook',
            'utm_medium': 'cpc',
            'utm_campaign': f'local-http-qa-{run_key}',
        },
        'fbclid': f'synthetic-{run_key}',
        'client_event_id': client_event_id,
        'extra_fields': {
            'product_category': 'Beverage production line',
            'capacity': '12,000 bottles/hour',
            'packaging_format': 'PET bottle',
            'project_type': 'new_line',
            'purchase_timeline': '6-12 months',
            'contact_role': 'Project team',
        },
        'consent': {
            'contact': True,
            'privacy_notice': True,
            'marketing': True,
        },
        'form_started_at': (timezone.now() - timedelta(seconds=5)).isoformat(),
    }
    industrial = LeadFormDefinition.objects.get(site=site, code='project-inquiry').category == 'industrial'
    if industrial:
        payload['extra_fields'] = {
            'business_line': 'circulatingWaterControl',
            'product_category': 'Circulating water control systems',
            'application_context': 'Synthetic water-loop monitoring',
        }
    # Match the endpoint used by the actual packaged homepage/contact scripts.
    submit_url = urljoin(base_url, 'admin/api/leads/forms/project-inquiry/submit/')
    before = {
        'submissions': LeadSubmission.objects.filter(site=site).count(),
        'outbox': LeadEventOutbox.objects.filter(submission__site=site).count(),
        'companies': Company.objects.filter(site=site).count(),
        'contacts': Contact.objects.filter(site=site).count(),
        'opportunities': Opportunity.objects.filter(site=site).count(),
    }

    invalid_payload = {**payload, 'company': ''}
    invalid = requests.post(
        submit_url,
        data=json.dumps(invalid_payload),
        headers={'Content-Type': 'application/json', 'Origin': 'https://local-website.test'},
        timeout=15,
    )
    assert invalid.status_code == 400
    assert not invalid.json()['ok']
    assert LeadSubmission.objects.filter(site=site).count() == before['submissions']

    created = requests.post(
        submit_url,
        data=json.dumps(payload),
        headers={
            'Content-Type': 'application/json',
            'Origin': 'https://local-website.test',
            'User-Agent': 'New-Crown-Local-Website-CRM-Verifier/1.0',
        },
        timeout=15,
    )
    created.raise_for_status()
    created_json = created.json()
    assert created_json['ok'] and not created_json['duplicate']
    submission = LeadSubmission.objects.get(pk=created_json['submission_id'])
    assert submission.company == company_name
    assert submission.email == email
    assert submission.source_channel == 'meta_ads'
    assert submission.utm_json['utm_source'] == 'facebook'
    assert submission.identifiers_json['client_event_id'] == client_event_id
    assert submission.assignee_id is None
    assert submission.team_id is not None
    assert LeadSubmission.objects.filter(site=site).count() == before['submissions'] + 1
    assert LeadEventOutbox.objects.filter(submission__site=site).count() == before['outbox']

    duplicate = requests.post(
        submit_url,
        data=json.dumps(payload),
        headers={
            'Content-Type': 'application/json',
            'Origin': 'https://local-website.test',
            'User-Agent': 'New-Crown-Local-Website-CRM-Verifier/1.0',
        },
        timeout=15,
    )
    duplicate.raise_for_status()
    duplicate_json = duplicate.json()
    assert duplicate_json['ok'] and duplicate_json['duplicate']
    assert duplicate_json['submission_id'] == submission.pk
    assert LeadSubmission.objects.filter(site=site, dedupe_key=submission.dedupe_key).count() == 1

    admin_page_url = urljoin(base_url, 'admin/sales/leads/')
    admin_page = admin.get(admin_page_url, timeout=10)
    admin_page.raise_for_status()
    assign_url = urljoin(base_url, 'admin/api/sales/leads/bulk-assign/')
    assigned = post_form(
        admin,
        assign_url,
        referer=admin_page_url,
        csrf=csrf_token(decoded(admin_page)),
        data=[
            ('record_ids', str(submission.pk)),
            ('assignee_id', str(sales_user.pk)),
        ],
        accept_json=True,
    )
    assigned_json = assigned.json()
    assert assigned_json['ok'] and assigned_json['updated'] == 1
    submission.refresh_from_db()
    assert submission.assignee_id == sales_user.pk

    detail_url = urljoin(base_url, f'admin/sales/leads/{submission.pk}/')
    detail = sales.get(detail_url, timeout=10)
    detail.raise_for_status()
    detail_html = decoded(detail)
    assert company_name in detail_html
    if industrial:
        assert submission.payload_json['business_line'] == 'circulatingWaterControl'
        assert 'capacity' not in submission.payload_json
        assert 'Synthetic water-loop monitoring' in detail_html
        assert '应用背景' in detail_html
    disposition_url = urljoin(base_url, f'admin/api/sales/leads/{submission.pk}/disposition/')
    due_at = timezone.localtime(timezone.now() + timedelta(days=1)).strftime('%Y-%m-%dT%H:%M')
    qualification_data = {
        'expected_updated_at': submission.updated_at.isoformat(),
        'idempotency_token': f'website-http-qualify-{run_key}',
        'continue_action': 'stay', 'current_url': f'/admin/sales/leads/{submission.pk}/',
        'stage': 'qualified',
        'qualification_contactable': 'on', 'qualification_company_verified': 'on',
        'qualification_project_confirmed': 'on', 'qualification_technical_fit': 'on',
        'qualification_next_step_confirmed': 'on',
        'qualification_notes': 'Synthetic local qualification evidence.',
        'activity_type': 'note', 'activity_direction': 'internal',
        'activity_subject': 'Synthetic project review', 'activity_body': 'Do not contact.',
        'create_task': 'on', 'task_type': 'follow_up', 'task_title': 'Synthetic follow-up',
        'task_due_at': due_at, 'task_priority': 'normal',
    }
    qualified = post_form(
        sales,
        disposition_url,
        referer=detail_url,
        csrf=csrf_token(detail_html),
        data=qualification_data if industrial else {
            'expected_updated_at': submission.updated_at.isoformat(),
            'idempotency_token': f'website-http-qualify-{run_key}',
            'continue_action': 'stay',
            'current_url': f'/admin/sales/leads/{submission.pk}/',
            'quick_action': 'qualified',
            'country': submission.country,
            'company': submission.company,
            'product_category': payload['extra_fields']['product_category'],
            'capacity': payload['extra_fields']['capacity'],
            'packaging_format': payload['extra_fields']['packaging_format'],
            'project_type': payload['extra_fields']['project_type'],
            'purchase_timeline': payload['extra_fields']['purchase_timeline'],
            'contact_role': payload['extra_fields']['contact_role'],
            'task_title': 'Synthetic local follow-up task',
            'task_due_at': due_at,
            'task_priority': 'normal',
            'decision_notes': 'Synthetic local qualification evidence for HTTP verification.',
        },
        accept_json=True,
    )
    qualified_json = qualified.json()
    assert qualified_json['ok'] and not qualified_json['replayed']
    submission.refresh_from_db()
    assert submission.stage == 'qualified'
    assert submission.qualification_score == 100
    assert Task.objects.filter(submission=submission, status='open').count() == 1
    assert Activity.objects.filter(submission=submission).exists()

    conversion_page = sales.get(detail_url, timeout=10)
    conversion_page.raise_for_status()
    convert_url = urljoin(base_url, f'admin/api/sales/leads/{submission.pk}/convert/')
    converted = post_form(
        sales,
        convert_url,
        referer=detail_url,
        csrf=csrf_token(decoded(conversion_page)),
        data={'opportunity_name': f'{company_name} - synthetic project'},
        accept_json=True,
    )
    converted_json = converted.json()
    assert converted_json['ok'] and converted_json['created']
    conversion = LeadConversion.objects.select_related(
        'company', 'contact', 'opportunity'
    ).get(submission=submission)
    company = conversion.company
    contact = conversion.contact
    opportunity = conversion.opportunity
    assert company.pk == converted_json['company_id']
    assert contact.pk == converted_json['contact_id']
    assert opportunity.pk == converted_json['opportunity_id']
    assert company.owner_user_id == sales_user.pk
    assert company.team_id == submission.team_id
    state = CompanyPoolState.objects.get(company=company)
    assert state.state == 'owned'
    source = CustomerSource.objects.get(company=company, submission=submission)
    assert source.source_type == 'website_form'
    assert source.intake_method == 'automatic_receive'
    assert source.attribution_json['source_channel'] == 'meta_ads'
    assert source.attribution_json['utm']['utm_source'] == 'facebook'
    assert source.occurred_at == submission.submitted_at
    assert CompanyContactPoint.objects.filter(company=company, channel='email').count() == 1
    assert CompanyContactPoint.objects.filter(company=company, channel='phone').count() == 1
    assert Company.objects.filter(site=site).count() == before['companies'] + 1
    assert Contact.objects.filter(site=site).count() == before['contacts'] + 1
    assert Opportunity.objects.filter(site=site).count() == before['opportunities'] + 1
    assert LeadEventOutbox.objects.filter(submission__site=site).count() == before['outbox']

    conversion_page_again = sales.get(detail_url, timeout=10)
    conversion_page_again.raise_for_status()
    converted_again = post_form(
        sales,
        convert_url,
        referer=detail_url,
        csrf=csrf_token(decoded(conversion_page_again)),
        data={'opportunity_name': f'{company_name} - ignored replay name'},
        accept_json=True,
    )
    converted_again_json = converted_again.json()
    assert converted_again_json['ok'] and not converted_again_json['created']
    assert LeadConversion.objects.filter(submission=submission).count() == 1
    assert CustomerSource.objects.filter(company=company, submission=submission).count() == 1
    assert Opportunity.objects.filter(source_submission=submission).count() == 1

    pool_url = urljoin(base_url, 'admin/sales/customer-pool/')
    visible = sales.get(
        pool_url,
        params={'scope': 'mine', 'q': company_name},
        timeout=10,
    )
    visible.raise_for_status()
    assert company_name in decoded(visible)

    if industrial:
        from leads.industrial import BUSINESS_LINES
        for line, label in BUSINESS_LINES.items():
            case = {**payload, 'company': f'Vorntek synthetic {line} {run_key}',
                    'email': f'{line.lower()}-{run_key}@example.invalid',
                    'client_event_id': str(uuid.uuid4()),
                    'extra_fields': {'business_line': line, 'product_category': label,
                                     'application_context': 'Synthetic application ' + line}}
            response = requests.post(submit_url, json=case, headers={'Origin': 'https://local-website.test'}, timeout=15)
            response.raise_for_status()
            assert response.json()['ok'] and not response.json()['duplicate']
            row = LeadSubmission.objects.get(pk=response.json()['submission_id'])
            assert row.payload_json['business_line'] == line and 'capacity' not in row.payload_json
            assert row.email == case['email']
            view = admin.get(urljoin(base_url, f'admin/sales/leads/{row.pk}/'), timeout=10)
            view.raise_for_status()
            assert case['extra_fields']['application_context'] in decoded(view)
            replay = requests.post(submit_url, json=case, headers={'Origin': 'https://local-website.test'}, timeout=15)
            replay.raise_for_status()
            assert replay.json()['duplicate'] and replay.json()['submission_id'] == row.pk
        assert LeadSubmission.objects.filter(site=site).count() == before['submissions'] + 8
        assert LeadEventOutbox.objects.filter(submission__site=site).count() == before['outbox']
        print('- all seven industrial areas persisted without beverage capacity, appeared in CRM and deduplicated')

    print('Website-to-CRM HTTP E2E passed on local PostgreSQL:')
    print('- invalid submission was rejected without a database row')
    print('- one synthetic website submission was accepted and exact replay was deduplicated')
    print('- administrator assigned the lead and sales qualified it with a follow-up task')
    print('- conversion created one owned company, contact, opportunity, source, and two contact points')
    print('- Meta campaign attribution remained attribution while the customer source remained website_form')
    print('- repeated conversion created no duplicate CRM records')
    print('- the converted company was visible in the sales user customer-pool view')
    print('- external marketing integrations stayed disabled and no outbox row was added')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
