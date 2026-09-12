"""Exercise customer-pool PostgreSQL constraints without leaving test rows."""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from pathlib import Path


ADMIN_DIR = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument('--dsn', required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    sys.path.insert(0, str(ADMIN_DIR))
    from scripts.check_candidate_postgres_contract import require_safe_candidate_dsn

    require_safe_candidate_dsn(args.dsn)
    os.environ['DJANGO_SETTINGS_MODULE'] = 'siteos_admin.settings'
    os.environ['SITEOS_ADMIN_DEBUG'] = '1'
    os.environ['SITEOS_ADMIN_DATABASE_URL'] = args.dsn
    os.environ['SITEOS_ADMIN_DATABASE_SSLMODE'] = 'disable'
    os.environ['SITEOS_ADMIN_SECURE_SSL_REDIRECT'] = '0'

    import django

    django.setup()

    from django.contrib.auth import get_user_model
    from django.db import DatabaseError, connection, transaction

    from leads.models import (
        Company,
        CompanyContactPoint,
        CompanyPoolState,
        Contact,
        CustomerSource,
        SalesTeam,
    )
    from sitecore.models import Site

    marker_prefix = f'pool-contract-{uuid.uuid4().hex}'

    def site(suffix: str) -> Site:
        return Site.objects.create(
            code=f'{marker_prefix}-{suffix}',
            name=f'Customer pool contract {suffix}',
            base_url='https://example.invalid',
            default_locale='en',
        )

    def company(owner=None, *, target_site: Site) -> Company:
        return Company.objects.create(
            site=target_site,
            owner_user=owner,
            name=f'{marker_prefix} company',
            normalized_name=f'{marker_prefix} company',
            source_channel='contract_test',
        )

    def expect_rejection(label: str, scenario) -> None:
        try:
            with transaction.atomic():
                scenario()
                with connection.cursor() as cursor:
                    cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
        except DatabaseError:
            print(f'PASS {label}')
            return
        raise SystemExit(f'FAIL {label}: PostgreSQL accepted an invalid customer-pool state.')

    def available_company_with_owner() -> None:
        target_site = site('owner-on-available')
        user = get_user_model().objects.create_user(username=f'{marker_prefix}-owner')
        target_company = company(user, target_site=target_site)
        CompanyPoolState.objects.create(company=target_company, state='available')

    def owned_company_without_owner() -> None:
        target_site = site('owner-missing')
        target_company = company(target_site=target_site)
        CompanyPoolState.objects.create(company=target_company, state='owned')

    def contact_from_other_site() -> None:
        first_site = site('contact-company')
        second_site = site('contact-person')
        target_company = company(target_site=first_site)
        foreign_contact = Contact.objects.create(
            site=second_site,
            full_name='Synthetic cross-site contact',
        )
        CompanyContactPoint.objects.create(
            site=first_site,
            company=target_company,
            contact=foreign_contact,
            channel='email',
            raw_value='contract@example.invalid',
            normalized_value='contract@example.invalid',
        )

    def source_from_other_site() -> None:
        first_site = site('source-company')
        second_site = site('source-record')
        target_company = company(target_site=first_site)
        CustomerSource.objects.create(
            site=second_site,
            company=target_company,
            source_type='research',
            intake_method='file_import',
        )

    def source_with_other_site_team() -> None:
        first_site = site('team-company')
        second_site = site('team-owner')
        target_company = company(target_site=first_site)
        foreign_team = SalesTeam.objects.create(
            site=second_site,
            code=f'{marker_prefix}-team',
            name='Synthetic cross-site team',
        )
        CustomerSource.objects.create(
            site=first_site,
            company=target_company,
            responsible_team=foreign_team,
            source_type='research',
            intake_method='file_import',
        )

    def duplicate_external_source() -> None:
        target_site = site('duplicate-source')
        target_company = company(target_site=target_site)
        values = dict(
            site=target_site,
            company=target_company,
            source_type='research',
            intake_method='file_import',
            external_record_id=f'{marker_prefix}-external',
        )
        CustomerSource.objects.create(**values)
        CustomerSource.objects.create(**values)

    expect_rejection('available company cannot have an owner', available_company_with_owner)
    expect_rejection('owned company must have an owner', owned_company_without_owner)
    expect_rejection('contact point cannot cross sites', contact_from_other_site)
    expect_rejection('customer source cannot cross sites', source_from_other_site)
    expect_rejection('responsible team cannot cross sites', source_with_other_site_team)
    expect_rejection('external source identity is unique', duplicate_external_source)

    if Site.objects.filter(code__startswith=marker_prefix).exists():
        raise SystemExit('FAIL contract fixtures were not rolled back.')

    print('Customer-pool PostgreSQL write-and-rollback contract passed: 6 rejected invalid states, 0 rows retained.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
