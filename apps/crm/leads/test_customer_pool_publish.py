from __future__ import annotations

import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test import TestCase

from console.access import GROUP_BY_ROLE, ROLE_SALES, ROLE_SYSTEM_ADMIN
from marketing.models import CanonicalEvent
from sitecore.models import Cta, PageRoute, Site, SiteLocale

from .customer_import_services import commit_customer_import
from .customer_pool_publish import (
    BULK_PUBLISH_LIMIT,
    preferred_publish_route,
    publish_reviewed_customers_bulk,
)
from .customer_pool_services import CustomerPoolError
from .customer_standard21_import import STANDARD21_HEADERS, preview_standard21_import
from .customer_value_engine import compute_customer_value
from .models import (
    Activity,
    Company,
    CompanyContactPoint,
    CompanyPoolState,
    Contact,
    CrmMutationReceipt,
    CustomerImportBatch,
    CustomerImportRow,
    CustomerPoolRow,
    CustomerSource,
    LeadConversion,
    LeadFormDefinition,
    LeadSubmission,
    Opportunity,
    SalesTeam,
    SalesTeamMember,
    Task,
)


def standard21_row(project_signal: bool = False, **overrides) -> list[str]:
    values = {name: '' for name in STANDARD21_HEADERS}
    values.update(
        {
            'country_name': '肯尼亚',
            'country': 'KE',
            'route_type': '公司总机',
            'route_tier': '企业总机',
            'identity_I': 'I2',
            'evidence_V': 'V3',
            'priority_P': 'P2',
            'source_channel': 'research_public',
        }
    )
    values.update(overrides)
    value = compute_customer_value(
        source_channel=values['source_channel'],
        identity=values['identity_I'],
        evidence_v=values['evidence_V'],
        route_tier=values['route_tier'],
        whatsapp_confirmed=values['whatsapp_confirmed'],
        has_email=bool(values['email'] or values['email_2'] or values['email_3']),
        priority=values['priority_P'],
        project_signal=project_signal,
    )
    values['value'] = str(value)
    return [values[name] for name in STANDARD21_HEADERS]


class CustomerPoolPublishTests(TestCase):
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
            code='pool-publish', name='Pool Publish', base_url='https://example.invalid', default_locale='en'
        )
        self.team = SalesTeam.objects.create(site=self.site, code='sales', name='Sales')
        admin_group = Group.objects.create(name=GROUP_BY_ROLE[ROLE_SYSTEM_ADMIN])
        self.admin = get_user_model().objects.create_user(
            username='publish-admin', password='test-password'
        )
        self.admin.groups.add(admin_group)
        sales_group = Group.objects.create(name=GROUP_BY_ROLE[ROLE_SALES])
        self.sales = get_user_model().objects.create_user(
            username='publish-sales', password='test-password'
        )
        self.sales.groups.add(sales_group)

    def import_rows(self, rows: list[list[str]]):
        lines = [','.join(STANDARD21_HEADERS)]
        for row in rows:
            lines.append(','.join(f'"{value}"' if ',' in value else value for value in row))
        payload = (chr(13) + chr(10)).join(lines).encode('utf-8')
        preview = preview_standard21_import(
            site=self.site, actor=self.admin, file_bytes=payload, original_name='routes.csv'
        )
        commit_customer_import(batch_id=preview.batch.pk, site=self.site, actor=self.admin)

    def company_for(self, account_id: str) -> Company:
        return CustomerPoolRow.objects.filter(account_id=account_id).first().company

    def test_route_tier_decides_the_publish_point(self):
        self.import_rows([
            standard21_row(
                phone='+254700001001', account_id='AF-KE-9001', company='Tier Ltd',
                person_name='Switchboard', route_tier='企业总机', route_type='公司总机',
            ),
            standard21_row(
                phone='+254700001002', account_id='AF-KE-9001', company='Tier Ltd',
                person_name='Director', route_tier='本人直联', route_type='直联手机',
                identity_I='I3', evidence_V='V1',
            ),
            standard21_row(
                phone='+254700001003', account_id='AF-KE-9001', company='Tier Ltd',
                person_name='Assistant', route_tier='指定人物转接', route_type='秘书转接',
            ),
        ])
        route = preferred_publish_route(site=self.site, company=self.company_for('AF-KE-9001'))
        self.assertIsNotNone(route)
        self.assertEqual(route.point.raw_value, '+254700001002')
        self.assertEqual(route.pool_row.route_tier, '本人直联')
        self.assertEqual(route.label, '本人直联 · V1')

    def test_soft_flagged_route_is_demoted_inside_its_tier(self):
        self.import_rows([
            standard21_row(
                phone='+254700002001', account_id='AF-KE-9002', company='Flag Ltd',
                person_name='Backup', route_tier='本人直联', route_type='直联手机',
                identity_I='I3', evidence_V='V1',
                restriction_note='源表标记为历史备用号码，优先拨打上方号码',
            ),
            standard21_row(
                phone='+254700002002', account_id='AF-KE-9002', company='Flag Ltd',
                person_name='Primary', route_tier='本人直联', route_type='直联手机',
                evidence_V='V3',
            ),
        ])
        route = preferred_publish_route(site=self.site, company=self.company_for('AF-KE-9002'))
        self.assertIsNotNone(route)
        self.assertEqual(route.point.raw_value, '+254700002002')
        self.assertNotIn('降级', route.label)

    def test_evidence_then_whatsapp_break_ties_inside_a_tier(self):
        self.import_rows([
            standard21_row(
                phone='+254700003001', account_id='AF-KE-9003', company='Evidence Ltd',
                person_name='Third', route_tier='公开业务手机', route_type='业务手机',
                evidence_V='V3', whatsapp_confirmed='已确认',
            ),
            standard21_row(
                phone='+254700003002', account_id='AF-KE-9003', company='Evidence Ltd',
                person_name='First', route_tier='公开业务手机', route_type='业务手机',
                evidence_V='V1',
            ),
            standard21_row(
                phone='+254700003003', account_id='AF-KE-9003', company='Evidence Ltd',
                person_name='Second', route_tier='公开业务手机', route_type='业务手机',
                evidence_V='V1', whatsapp_confirmed='已确认',
            ),
        ])
        route = preferred_publish_route(site=self.site, company=self.company_for('AF-KE-9003'))
        self.assertEqual(route.point.raw_value, '+254700003003')
        self.assertEqual(route.label, '公开业务手机 · V1 · WhatsApp 已确认')

    def test_hard_restricted_rows_are_never_publish_points(self):
        self.import_rows([
            standard21_row(
                phone='+254700004001', account_id='AF-KE-9004', company='Blocked Ltd',
                person_name='Do Not Export', route_tier='本人直联', route_type='直联手机',
                identity_I='I3', evidence_V='V1', restriction_note='该号码不导出',
            ),
            standard21_row(
                phone='+254700004002', account_id='AF-KE-9004', company='Blocked Ltd',
                person_name='Allowed', route_tier='部门入口', route_type='部门电话',
                restriction_note='源表标记为特殊/历史路线',
            ),
        ])
        route = preferred_publish_route(site=self.site, company=self.company_for('AF-KE-9004'))
        self.assertEqual(route.point.raw_value, '+254700004002')
        self.assertIn('降级', route.label)

        CustomerPoolRow.objects.filter(company=self.company_for('AF-KE-9004')).update(
            restriction_note='该号码不导出'
        )
        CompanyContactPoint.objects.filter(company=self.company_for('AF-KE-9004')).update(
            usage_status='restricted'
        )
        self.assertIsNone(
            preferred_publish_route(site=self.site, company=self.company_for('AF-KE-9004'))
        )

    def test_row_with_phone_and_email_publishes_the_phone(self):
        self.import_rows([
            standard21_row(
                phone='+254700005001', email='buyer@example.invalid', account_id='AF-KE-9005',
                company='Slots Ltd', person_name='Buyer', route_tier='本人直联', route_type='直联手机',
                identity_I='I3', evidence_V='V1',
            ),
        ])
        route = preferred_publish_route(site=self.site, company=self.company_for('AF-KE-9005'))
        self.assertEqual(route.point.channel, 'phone')
        self.assertEqual(route.point.raw_value, '+254700005001')

    def test_company_without_standard_rows_falls_back_to_existing_contact_points(self):
        from .customer_pool_services import create_manual_customer

        create_manual_customer(
            site=self.site,
            actor=self.admin,
            company_name='Legacy Ltd',
            email='legacy@example.invalid',
            phone='',
            source_type='manual',
            source_detail='CRM 手动创建',
            assignment_mode='review',
            idempotency_token='manual-' + uuid.uuid4().hex,
        )
        company = Company.objects.get(site=self.site, name='Legacy Ltd')
        route = preferred_publish_route(site=self.site, company=company)
        self.assertIsNotNone(route)
        self.assertIsNone(route.pool_row)
        self.assertEqual(route.label, '历史资料联系方式')
        self.assertEqual(route.point.raw_value, 'legacy@example.invalid')

    def test_standard_row_without_evidence_mapping_never_falls_back(self):
        company = Company.objects.create(
            site=self.site,
            name='Existing Route Ltd',
            normalized_name='existing route ltd',
            country='KE',
            status='prospect',
            source_channel='manual',
        )
        CompanyPoolState.objects.create(company=company, state='review')
        CompanyContactPoint.objects.create(
            site=self.site,
            company=company,
            channel='phone',
            raw_value='+254700005999',
            normalized_value='+254700005999',
            usage_status='unknown',
            evidence_json={'origin': 'older manual record'},
        )
        CustomerPoolRow.objects.create(
            site=self.site,
            company=company,
            row_key='f' * 64,
            phone='+254700005999',
            country_code='KE',
            company_name=company.name,
            value='36.0',
            account_id='AF-KE-EXISTING',
            restriction_note='该号码不导出',
            source_channel='research_public',
        )

        self.assertIsNone(preferred_publish_route(site=self.site, company=company))

    def test_bulk_publish_keeps_each_company_in_its_own_transaction(self):
        self.import_rows([
            standard21_row(
                phone='+254700006001', account_id='AF-KE-9006', company='Publish Ltd',
                person_name='Buyer', route_tier='本人直联', route_type='直联手机',
                identity_I='I3', evidence_V='V1',
            ),
            standard21_row(
                phone='+254700006002', account_id='AF-KE-9007', company='Blocked Ltd',
                person_name='Buyer', route_tier='本人直联', route_type='直联手机',
                identity_I='I3', evidence_V='V1', restriction_note='该号码不导出',
            ),
        ])
        publishable = self.company_for('AF-KE-9006')
        blocked = self.company_for('AF-KE-9007')
        token = 'bulk-review-' + uuid.uuid4().hex

        result = publish_reviewed_customers_bulk(
            site=self.site,
            actor=self.admin,
            team=self.team,
            company_ids=[publishable.pk, blocked.pk],
            idempotency_token=token,
        )

        self.assertEqual(result.succeeded, 1)
        self.assertEqual(result.failed, 1)
        self.assertEqual([item.status for item in result.items], ['published', 'failed'])
        self.assertEqual(result.items[0].route_label, '本人直联 · V1')
        self.assertEqual(result.items[1].workflow_code, 'no_publishable_route')
        self.assertEqual(CompanyPoolState.objects.get(company=publishable).state, 'available')
        self.assertEqual(CompanyPoolState.objects.get(company=blocked).state, 'review')
        self.assertEqual(Company.objects.get(pk=publishable.pk).team, self.team)
        self.assertEqual(Activity.objects.filter(company=publishable).count(), 1)

        repeated = publish_reviewed_customers_bulk(
            site=self.site,
            actor=self.admin,
            team=self.team,
            company_ids=[publishable.pk],
            idempotency_token=token,
        )
        self.assertEqual(repeated.succeeded, 0)
        self.assertEqual(repeated.items[0].workflow_code, 'customer_pool_conflict')
        self.assertEqual(repeated.items[0].message, '客户核验状态已经变化，请刷新后重试。')
        self.assertEqual(Activity.objects.filter(company=publishable).count(), 1)
        self.assertEqual(CompanyPoolState.objects.get(company=publishable).version, 2)

    def test_bulk_publish_rejects_invalid_selection_before_any_write(self):
        self.import_rows([
            standard21_row(
                phone='+254700007001', account_id='AF-KE-9008', company='Select Ltd',
                person_name='Buyer', route_tier='本人直联', route_type='直联手机',
                identity_I='I3', evidence_V='V1',
            ),
        ])
        company = self.company_for('AF-KE-9008')
        token = 'bulk-review-' + uuid.uuid4().hex

        with self.assertRaises(CustomerPoolError) as caught:
            publish_reviewed_customers_bulk(
                site=self.site, actor=self.admin, team=self.team,
                company_ids=[], idempotency_token=token,
            )
        self.assertEqual(caught.exception.workflow_code, 'bulk_selection_required')

        with self.assertRaises(CustomerPoolError) as caught:
            publish_reviewed_customers_bulk(
                site=self.site, actor=self.admin, team=self.team,
                company_ids=[company.pk] * 2, idempotency_token=token,
            )
        self.assertEqual(caught.exception.workflow_code, 'bulk_selection_duplicate')

        with self.assertRaises(CustomerPoolError) as caught:
            publish_reviewed_customers_bulk(
                site=self.site, actor=self.admin, team=self.team,
                company_ids=[company.pk] * (BULK_PUBLISH_LIMIT + 1), idempotency_token=token,
            )
        self.assertEqual(caught.exception.workflow_code, 'bulk_selection_too_large')

        with self.assertRaises(CustomerPoolError) as caught:
            publish_reviewed_customers_bulk(
                site=self.site, actor=self.admin, team=None,
                company_ids=[company.pk], idempotency_token=token,
            )
        self.assertEqual(caught.exception.workflow_code, 'team_invalid')

        with self.assertRaises(CustomerPoolError) as caught:
            publish_reviewed_customers_bulk(
                site=self.site, actor=self.admin, team=self.team,
                company_ids=[company.pk], idempotency_token='short',
            )
        self.assertEqual(caught.exception.workflow_code, 'idempotency_token_invalid')

        self.assertEqual(CompanyPoolState.objects.get(company=company).state, 'review')
        self.assertFalse(CrmMutationReceipt.objects.exists())
