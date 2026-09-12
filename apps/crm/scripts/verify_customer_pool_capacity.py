"""Benchmark the local customer-pool page with 20,000 synthetic contact routes.

The verifier refuses non-local or non-test databases and wraps all generated
capacity data in a rolled-back transaction. It therefore leaves the candidate
database unchanged after either success or failure.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse


ADMIN_DIR = Path(__file__).resolve().parents[1]
LOCAL_HOSTS = {'localhost', '127.0.0.1', '::1'}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument('--dsn', required=True)
    parser.add_argument('--target-routes', type=int, default=20_000)
    parser.add_argument('--samples', type=int, default=50)
    parser.add_argument('--p95-limit-ms', type=float, default=1_000.0)
    return parser.parse_args()


def require_safe_test_dsn(dsn: str) -> None:
    parsed = urlparse(dsn)
    if parsed.scheme not in {'postgres', 'postgresql'}:
        raise SystemExit('Capacity verification requires postgres:// or postgresql://.')
    if parsed.hostname not in LOCAL_HOSTS:
        raise SystemExit('Refusing to benchmark a non-local PostgreSQL host.')
    if not parsed.path.lstrip('/').endswith('_test'):
        raise SystemExit('Capacity verification database name must end in _test.')


def percentile(values: list[float], percentile_value: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * percentile_value) - 1)
    return ordered[index]


def main() -> int:
    args = parse_args()
    require_safe_test_dsn(args.dsn)
    if args.target_routes < 20_000:
        raise SystemExit('--target-routes must be at least 20000.')
    if args.samples < 50:
        raise SystemExit('--samples must be at least 50.')

    sys.path.insert(0, str(ADMIN_DIR))
    os.environ['DJANGO_SETTINGS_MODULE'] = 'siteos_admin.settings'
    os.environ['SITEOS_ADMIN_DEBUG'] = '1'
    os.environ['SITEOS_ADMIN_DATABASE_URL'] = args.dsn
    os.environ['SITEOS_ADMIN_DATABASE_SSLMODE'] = 'disable'
    os.environ['SITEOS_ADMIN_SECURE_SSL_REDIRECT'] = '0'

    import django

    django.setup()

    from django.contrib.auth import get_user_model
    from django.db import transaction
    from django.test import Client
    from django.test.utils import CaptureQueriesContext
    from django.urls import reverse

    from leads.models import Company, CompanyContactPoint
    from sitecore.models import Site
    from django.db import connection

    site = Site.objects.get(code='siteos_demo')
    companies = list(
        Company.objects.filter(site=site, name__startswith='Demo Beverage Company')
        .order_by('id')[:200]
    )
    if len(companies) != 200:
        raise SystemExit(f'Expected exactly 200 synthetic demo companies; found {len(companies)}.')

    User = get_user_model()
    actor = User.objects.get(username='local-sales', is_active=True)
    client = Client()
    client.force_login(actor)
    url = reverse('console:customer_pool')

    result: dict[str, object] = {
        'database': urlparse(args.dsn).path.lstrip('/'),
        'company_count': len(companies),
        'target_routes': args.target_routes,
        'samples': args.samples,
        'rolled_back': True,
    }

    company_ids = [company.pk for company in companies]
    with transaction.atomic():
        baseline_routes = CompanyContactPoint.objects.filter(company_id__in=company_ids).count()
        with CaptureQueriesContext(connection) as baseline_queries:
            baseline_response = client.get(url)
        baseline_query_count = len(baseline_queries)
        if baseline_response.status_code != 200:
            raise SystemExit(f'Baseline customer-pool request returned {baseline_response.status_code}.')

        missing = args.target_routes - baseline_routes
        if missing > 0:
            rows: list[CompanyContactPoint] = []
            for sequence in range(missing):
                company = companies[sequence % len(companies)]
                ordinal = baseline_routes + sequence + 1
                value = f'capacity-{company.pk}-{ordinal}@example.invalid'
                rows.append(CompanyContactPoint(
                    site=site,
                    company=company,
                    channel='email',
                    raw_value=value,
                    normalized_value=value,
                    purpose='business',
                    status='unverified',
                    usage_status='unknown',
                    evidence_json={'fixture': True, 'capacity_verifier': True},
                ))
                if len(rows) == 1_000:
                    CompanyContactPoint.objects.bulk_create(rows, batch_size=1_000)
                    rows.clear()
            if rows:
                CompanyContactPoint.objects.bulk_create(rows, batch_size=1_000)

        route_count = CompanyContactPoint.objects.filter(company_id__in=company_ids).count()
        if route_count < args.target_routes:
            raise SystemExit(f'Capacity fixture only produced {route_count} routes.')

        warm_response = client.get(url)
        if warm_response.status_code != 200:
            raise SystemExit(f'Warm customer-pool request returned {warm_response.status_code}.')
        with CaptureQueriesContext(connection) as capacity_queries:
            measured_response = client.get(url)
        capacity_query_count = len(capacity_queries)
        if measured_response.status_code != 200:
            raise SystemExit(f'Measured customer-pool request returned {measured_response.status_code}.')

        durations_ms: list[float] = []
        for _ in range(args.samples):
            started = time.perf_counter()
            response = client.get(url)
            durations_ms.append((time.perf_counter() - started) * 1_000)
            if response.status_code != 200:
                raise SystemExit(f'Timed customer-pool request returned {response.status_code}.')

        p95_ms = percentile(durations_ms, 0.95)
        result.update({
            'baseline_routes': baseline_routes,
            'measured_routes': route_count,
            'baseline_query_count': baseline_query_count,
            'capacity_query_count': capacity_query_count,
            'query_count_constant': capacity_query_count <= baseline_query_count,
            'min_ms': round(min(durations_ms), 3),
            'median_ms': round(percentile(durations_ms, 0.50), 3),
            'p95_ms': round(p95_ms, 3),
            'max_ms': round(max(durations_ms), 3),
            'p95_limit_ms': args.p95_limit_ms,
            'passed': (
                p95_ms <= args.p95_limit_ms
                and capacity_query_count <= baseline_query_count
            ),
        })
        transaction.set_rollback(True)

    persisted_routes = CompanyContactPoint.objects.filter(company_id__in=company_ids).count()
    result['persisted_routes_after_rollback'] = persisted_routes
    result['rollback_verified'] = persisted_routes == result['baseline_routes']
    result['passed'] = bool(result['passed'] and result['rollback_verified'])
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
