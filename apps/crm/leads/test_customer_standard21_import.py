from __future__ import annotations

import csv
import io
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test import TestCase

from console.access import GROUP_BY_ROLE, ROLE_SALES, ROLE_SYSTEM_ADMIN
from marketing.models import CanonicalEvent
from sitecore.models import Cta, PageRoute, Site, SiteLocale

from console.customer_pool_exports import (
    normalize_standard21_columns,
    standard21_csv_bytes,
)
from .customer_import_services import (
    CustomerImportError,
    commit_customer_import,
    preview_customer_import,
)
from .customer_standard21_import import (
    STANDARD21_HEADERS,
    build_standard21_payloads,
    preview_standard21_import,
    read_standard21_csv,
)
from .models import (
    Company,
    CompanyContactPoint,
    CompanyPoolState,
    Contact,
    CustomerImportBatch,
    CustomerImportRow,
    CustomerPoolRow,
    CustomerSource,
    LeadFormDefinition,
    SalesTeam,
    SalesTeamMember,
    Activity,
    CrmMutationReceipt,
    Task,
    LeadConversion,
    Opportunity,
    LeadSubmission,
)


def csv_bytes(rows: list[dict[str, str]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=STANDARD21_HEADERS, lineterminator='\r\n')
    writer.writeheader()
    for row in rows:
        writer.writerow({name: row.get(name, '') for name in STANDARD21_HEADERS})
    return buffer.getvalue().encode('utf-8')


def row(**overrides) -> dict[str, str]:
    base = {name: '' for name in STANDARD21_HEADERS}
    base.update(
        {
            'phone': '+254700000001',
            'country_name': '肯尼亚',
            'country': 'KE',
            'person_name': 'Test Person',
            'company': 'Test Bottling Ltd',
            'value': '31.0',
            'route_type': '公司总机',
            'route_tier': '企业总机',
            'account_id': 'AF-KE-0001',
            'identity_I': 'I2',
            'evidence_V': 'V3',
            'priority_P': 'P2',
            'source_channel': 'research_public',
        }
    )
    base.update(overrides)
    return base


class Standard21ParsingTests(TestCase):
    def test_value_must_match_unified_engine(self):
        # 10 + I2(8) + V3(2) + 企业总机(6) + P2(5) = 31；文件写 25 应被逐行拒收。
        rows, rejected = read_standard21_csv(csv_bytes([row(value='25.0')]))
        self.assertEqual(rows, [])
        self.assertEqual(len(rejected), 1)
        self.assertIn('value 复算不符', rejected[0]['message'])

    def test_project_signal_delta_is_recovered_not_copied(self):
        # 31 + 项目信号 5 = 36，引擎应还原出 project_signal=True。
        rows, rejected = read_standard21_csv(csv_bytes([row(value='36.0')]))
        self.assertEqual(rejected, [])
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].project_signal)
        self.assertEqual(str(rows[0].value), '36.0')

    def test_row_requires_phone_or_email_and_iso_country(self):
        rows, rejected = read_standard21_csv(
            csv_bytes(
                [
                    row(phone='', email='', account_id='AF-KE-0002'),
                    row(phone='+254700000003', country='KEN', account_id='AF-KE-0003'),
                    row(phone='+254700000004', account_id=''),
                ]
            )
        )
        self.assertEqual(rows, [])
        self.assertEqual(len(rejected), 3)
        self.assertIn('电话和邮箱都为空', rejected[0]['message'])
        self.assertIn('两位 ISO 国家代码', rejected[1]['message'])
        self.assertIn('缺少 account_id', rejected[2]['message'])

    def test_header_must_match_exactly(self):
        payload = csv_bytes([row()]).replace(b'route_tier', b'route_tiers')
        with self.assertRaises(CustomerImportError) as caught:
            read_standard21_csv(payload)
        self.assertEqual(caught.exception.workflow_code, 'standard21_header_mismatch')

    def test_row_key_is_stable_and_disambiguates_repeats(self):
        duplicate = row()
        rows, rejected = read_standard21_csv(csv_bytes([duplicate, dict(duplicate)]))
        self.assertEqual(rejected, [])
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0].row_key, rows[1].row_key)
        again, _ = read_standard21_csv(csv_bytes([duplicate, dict(duplicate)]))
        self.assertEqual([item.row_key for item in again], [item.row_key for item in rows])

    def test_rows_group_into_one_company_per_account(self):
        rows, _ = read_standard21_csv(
            csv_bytes(
                [
                    row(account_id='AF-KE-0009', company='Alpha Ltd', value='31.0'),
                    row(
                        account_id='AF-KE-0009',
                        company='Alpha Limited',
                        person_name='Second Person',
                        phone='+254700000009',
                        value='36.0',
                    ),
                    row(account_id='AF-KE-0010', company='Beta Ltd', phone='+254700000010', value='31.0'),
                ]
            )
        )
        payloads, contact_rows = build_standard21_payloads(rows)
        self.assertEqual(contact_rows, 3)
        self.assertEqual(len(payloads), 2)
        alpha = payloads[0]
        self.assertEqual(alpha['external_key'], 'AF-KE-0009')
        self.assertEqual(alpha['company_name'], 'Alpha Ltd')
        self.assertEqual(str(alpha['value']), '36.0')
        self.assertEqual(len(alpha['pool_rows']), 2)
        self.assertEqual(len(alpha['contacts']), 2)

    def test_email_only_row_is_accepted_and_scored_with_email_bonus(self):
        single = row(phone='', email='Buyer@Example.COM', value='36.0', account_id='AF-KE-0011')
        rows, rejected = read_standard21_csv(csv_bytes([single]))
        self.assertEqual(rejected, [])
        self.assertEqual(str(rows[0].value), '36.0')


class Standard21ImportTests(TestCase):
    unmanaged_models = (
        Site,
        SiteLocale,
        PageRoute,
        CanonicalEvent,
        Cta,
        LeadFormDefinition,
        SalesTeam,
        SalesTeamMember,
        LeadSubmission,
        Company,
        Contact,
        Opportunity,
        LeadConversion,
        Activity,
        Task,
        CrmMutationReceipt,
        CompanyPoolState,
        CompanyContactPoint,
        CustomerSource,
        CustomerImportBatch,
        CustomerImportRow,
        CustomerPoolRow,
    )

    @classmethod
    def setUpClass(cls):
        existing = set(connection.introspection.table_names())
        cls.created_models = []
        try:
            with connection.schema_editor() as editor:
                for model in cls.unmanaged_models:
                    if model._meta.db_table not in existing:
                        editor.create_model(model)
                        cls.created_models.append(model)
                        existing.add(model._meta.db_table)
            super().setUpClass()
        except Exception:
            with connection.schema_editor() as editor:
                for model in reversed(cls.created_models):
                    editor.delete_model(model)
            raise

    @classmethod
    def tearDownClass(cls):
        try:
            super().tearDownClass()
        finally:
            with connection.schema_editor() as editor:
                for model in reversed(cls.created_models):
                    editor.delete_model(model)

    def setUp(self):
        self.site = Site.objects.create(
            code='pool21', name='Pool 21', base_url='https://example.invalid', default_locale='en'
        )
        group = Group.objects.create(name=GROUP_BY_ROLE[ROLE_SALES])
        self.sales = get_user_model().objects.create_user(
            username='pool21-sales', password='test-password'
        )
        self.sales.groups.add(group)
        admin_group = Group.objects.create(name=GROUP_BY_ROLE[ROLE_SYSTEM_ADMIN])
        self.admin = get_user_model().objects.create_user(
            username='pool21-admin', password='test-password'
        )
        self.admin.groups.add(admin_group)
        self.team = SalesTeam.objects.create(site=self.site, code='sales', name='Sales')
        SalesTeamMember.objects.create(team=self.team, user=self.sales, membership_role='member')

    def test_preview_and_commit_write_pool_rows_and_contacts(self):
        payload = csv_bytes(
            [
                row(account_id='AF-KE-1001', company='Gamma Ltd', value='31.0'),
                row(
                    account_id='AF-KE-1001',
                    company='Gamma Ltd',
                    phone='+254700000002',
                    email='sales@example.org',
                    person_name='未公开',
                    restriction_note='源表姓名为占位文本「未公开」, 未采信',
                    value='36.0',
                ),
            ]
        )
        preview = preview_standard21_import(
            site=self.site, actor=self.admin, file_bytes=payload, original_name='pool21.csv'
        )
        self.assertFalse(preview.replayed)
        self.assertEqual(preview.batch.counts_json['accounts'], 1)
        self.assertEqual(preview.batch.counts_json['pool_rows'], 2)
        batch = commit_customer_import(batch_id=preview.batch.pk, site=self.site, actor=self.admin)

        self.assertEqual(
            list(CustomerImportRow.objects.filter(status='failed').values_list('message', flat=True)), []
        )
        self.assertEqual(batch.status, 'succeeded')
        company = Company.objects.get(site=self.site, name='Gamma Ltd')
        self.assertEqual(str(company.value), '36.0')
        self.assertEqual(company.country_code, 'KE')
        self.assertEqual(CompanyPoolState.objects.get(company=company).state, 'review')
        self.assertEqual(CustomerPoolRow.objects.filter(company=company).count(), 2)
        named_row = CustomerPoolRow.objects.get(company=company, person_name='Test Person')
        contact = Contact.objects.get(company=company, full_name='Test Person')
        self.assertEqual(named_row.contact_id, contact.pk)
        self.assertEqual(str(named_row.value), '31.0')
        placeholder_row = CustomerPoolRow.objects.get(
            company=company, restriction_note__contains='未采信'
        )
        self.assertEqual(placeholder_row.person_name, '未公开')
        self.assertIsNone(placeholder_row.contact_id)
        self.assertEqual(Contact.objects.filter(company=company).count(), 1)
        self.assertEqual(CompanyContactPoint.objects.filter(company=company).count(), 3)

    def test_replay_same_file_is_idempotent(self):
        payload = csv_bytes([row(account_id='AF-KE-1002', company='Delta Ltd', value='31.0')])
        first = preview_standard21_import(
            site=self.site, actor=self.admin, file_bytes=payload, original_name='pool21.csv'
        )
        commit_customer_import(batch_id=first.batch.pk, site=self.site, actor=self.admin)
        second = preview_standard21_import(
            site=self.site, actor=self.admin, file_bytes=payload, original_name='pool21.csv'
        )
        self.assertTrue(second.replayed)
        self.assertEqual(second.batch.pk, first.batch.pk)
        self.assertEqual(CustomerPoolRow.objects.count(), 1)
        self.assertEqual(Company.objects.filter(site=self.site).count(), 1)

    def test_export_round_trips_persisted_rows_and_rejects_unsafe_columns(self):
        payload = csv_bytes(
            [
                row(account_id='AF-KE-1010', company='Round Trip Ltd', value='31.0'),
                row(
                    account_id='AF-KE-1010',
                    company='Round Trip Ltd',
                    phone='+254700000010',
                    person_name='Second Person',
                    value='36.0',
                ),
            ]
        )
        preview = preview_standard21_import(
            site=self.site,
            actor=self.admin,
            file_bytes=payload,
            original_name='round-trip.csv',
        )
        commit_customer_import(batch_id=preview.batch.pk, site=self.site, actor=self.admin)

        pool_rows = CustomerPoolRow.objects.filter(site=self.site).order_by(
            'import_batch_id', 'row_number', 'row_key'
        )
        self.assertEqual(standard21_csv_bytes(pool_rows), b'\xef\xbb\xbf' + payload)
        self.assertIsNone(normalize_standard21_columns(['phone', 'phone', 'email']))
        self.assertIsNone(normalize_standard21_columns(['phone']))
        self.assertIsNone(normalize_standard21_columns(['phone', 'email', 'unknown']))

    def test_twenty_one_column_file_is_rejected_by_two_table_template(self):
        payload = csv_bytes([row(account_id='AF-KE-1003')])
        with self.assertRaises(CustomerImportError):
            preview_customer_import(
                site=self.site, actor=self.admin, file_bytes=payload, original_name='pool21.csv'
            )
