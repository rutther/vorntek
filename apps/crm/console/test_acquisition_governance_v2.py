from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import DatabaseError, connection
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from console.access import GROUP_BY_ROLE, ROLE_MARKETING_OPS, ROLE_SYSTEM_ADMIN
from console.acquisition_versions import lead_form_version_token
from console.acquisition_workspace import build_acquisition_forms_workspace
from console.audit import lead_form_snapshot
from console.lead_forms import FIELD_LIBRARY, LeadFormEditorForm
from console.models import AuditLog
from leads.models import LeadFormDefinition, LeadSubmission, SalesTeam
from marketing.models import CanonicalEvent
from sitecore.models import Cta, PageRoute, Site, SiteLocale


class AcquisitionGovernanceTests(TestCase):
    unmanaged_models = (
        Site,
        SiteLocale,
        PageRoute,
        CanonicalEvent,
        Cta,
        LeadFormDefinition,
        SalesTeam,
        LeadSubmission,
        AuditLog,
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
        self.marketing_group = Group.objects.create(
            name=GROUP_BY_ROLE[ROLE_MARKETING_OPS]
        )
        self.system_group = Group.objects.create(
            name=GROUP_BY_ROLE[ROLE_SYSTEM_ADMIN]
        )
        User = get_user_model()
        self.marketing = User.objects.create_user(
            username='acquisition-marketing', password='Test-password-234'
        )
        self.marketing.groups.add(self.marketing_group)
        self.admin = User.objects.create_user(
            username='acquisition-admin', password='Test-password-234'
        )
        self.admin.groups.add(self.system_group)
        self.site = Site.objects.create(
            code='siteos_demo',
            name='Acquisition site',
            base_url='https://example.invalid',
            default_locale='en',
            enabled=True,
            config_json={},
        )
        self.locale = SiteLocale.objects.create(
            site=self.site,
            locale_code='en',
            label='English',
            direction='ltr',
            is_default=True,
            enabled=True,
            sort_order=10,
        )
        self.form_definition = LeadFormDefinition.objects.create(
            site=self.site,
            locale=self.locale,
            code='project-inquiry',
            name='Project inquiry',
            category='project',
            channel='website',
            scope_type='site',
            scope_value='*',
            status='active',
            capi_enabled=True,
            notify_emails='sales@example.invalid',
            success_message='Thank you.',
            form_schema={
                'fields': [
                    {
                        'name': item['name'],
                        'type': item['type'],
                        'label': item['default_label'],
                        'placeholder': item['default_placeholder'],
                        'enabled': item['default_enabled'],
                        'required': item['default_required'],
                    }
                    for item in FIELD_LIBRARY
                ]
            },
            config_json={},
        )
        self.submission = LeadSubmission.objects.create(
            form=self.form_definition,
            site=self.site,
            locale=self.locale,
            full_name='Existing buyer',
            submitted_at=timezone.now(),
            stage_updated_at=timezone.now(),
        )

    def form_post(self, *, version: str, name: str = 'Updated inquiry') -> dict:
        data = {
            'version': version,
            'code': self.form_definition.code,
            'name': name,
            'category': 'project',
            'locale': str(self.locale.id),
            'channel': 'website',
            'scope_type': 'site',
            'scope_value': '*',
            'status': 'active',
            'capi_enabled': 'on',
            'submission_event_name': 'Lead',
            'contacted_event_name': 'contacted_lead',
            'qualified_event_name': 'qualified_lead',
            'won_event_name': 'converted',
            'notify_emails': 'sales@example.invalid',
            'success_message': 'Thank you.',
            'config_json': '{}',
        }
        for item in FIELD_LIBRARY:
            name_key = item['name']
            data[f'field_{name_key}_enabled'] = 'on'
            if item['default_required']:
                data[f'field_{name_key}_required'] = 'on'
            data[f'field_{name_key}_label'] = item['default_label']
            data[f'field_{name_key}_placeholder'] = item['default_placeholder']
        return data

    def test_workspace_exposes_operational_risk_and_real_submission_count(self):
        request = RequestFactory().get('/admin/marketing/forms/')
        request.user = self.marketing
        with patch(
            'console.acquisition_workspace.email_delivery_configured',
            return_value=False,
        ):
            workspace = build_acquisition_forms_workspace(
                site=self.site,
                request=request,
            )

        self.assertEqual(workspace['stats']['total'], 1)
        self.assertFalse(workspace['stats']['email_ready'])
        self.assertEqual(workspace['rows'][0]['submission_count'], 1)
        self.assertEqual(workspace['rows'][0]['field_count'], len(FIELD_LIBRARY))
        self.assertEqual(workspace['rows'][0]['recipient_count'], 1)
        self.assertEqual(
            workspace['rows'][0]['edit_url'],
            f'{reverse("console:marketing_form_edit", args=[self.form_definition.id])}?locale=en',
        )

    @patch('console.views.default_site_locale')
    def test_marketing_forms_page_has_canonical_navigation_and_one_h1(
        self,
        mocked_default_site_locale,
    ):
        mocked_default_site_locale.return_value = (self.site, self.locale)
        self.client.force_login(self.marketing)

        response = self.client.get(reverse('console:marketing_forms'))

        self.assertEqual(response.status_code, 200)
        rendered = response.content.decode('utf-8')
        self.assertEqual(rendered.count('<h1'), 1)
        self.assertIn('获客表单', rendered)
        self.assertIn(self.form_definition.name, rendered)
        self.assertIn(reverse('console:marketing_form_create'), rendered)
        self.assertIn('营销子导航', rendered)

    def test_existing_submission_locks_public_form_code(self):
        editor = LeadFormEditorForm(
            site=self.site,
            form_definition=self.form_definition,
        )

        self.assertTrue(editor.code_locked)
        self.assertTrue(editor.fields['code'].disabled)
        self.assertIn('公开地址已锁定', editor.fields['code'].help_text)

    @patch('console.views.default_site_locale')
    def test_valid_edit_updates_form_and_records_audit(
        self,
        mocked_default_site_locale,
    ):
        mocked_default_site_locale.return_value = (self.site, self.locale)
        self.client.force_login(self.marketing)
        token = lead_form_version_token(self.form_definition)

        response = self.client.post(
            reverse('console:marketing_form_edit', args=[self.form_definition.id]),
            self.form_post(version=token),
        )

        self.assertEqual(response.status_code, 302)
        self.form_definition.refresh_from_db()
        self.assertEqual(self.form_definition.name, 'Updated inquiry')
        audit = AuditLog.objects.get(action='lead_form_updated')
        self.assertEqual(audit.entity_id, self.form_definition.id)
        self.assertEqual(audit.after_json, lead_form_snapshot(self.form_definition))

    @patch('console.views.default_site_locale')
    def test_stale_edit_is_rejected_without_overwriting_newer_data(
        self,
        mocked_default_site_locale,
    ):
        mocked_default_site_locale.return_value = (self.site, self.locale)
        self.client.force_login(self.marketing)
        stale_token = lead_form_version_token(self.form_definition)
        LeadFormDefinition.objects.filter(pk=self.form_definition.pk).update(
            name='Newer server value'
        )

        response = self.client.post(
            reverse('console:marketing_form_edit', args=[self.form_definition.id]),
            self.form_post(version=stale_token, name='Stale browser value'),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '表单已被其他运营人员更新')
        self.form_definition.refresh_from_db()
        self.assertEqual(self.form_definition.name, 'Newer server value')
        self.assertFalse(AuditLog.objects.filter(action='lead_form_updated').exists())

    @patch('console.views.default_site_locale')
    def test_audit_failure_rolls_back_form_update(
        self,
        mocked_default_site_locale,
    ):
        mocked_default_site_locale.return_value = (self.site, self.locale)
        self.client.force_login(self.marketing)
        token = lead_form_version_token(self.form_definition)

        with patch('console.views.record_audit', side_effect=DatabaseError('audit unavailable')):
            with self.assertRaises(DatabaseError):
                self.client.post(
                    reverse('console:marketing_form_edit', args=[self.form_definition.id]),
                    self.form_post(version=token, name='Must roll back'),
                )

        self.form_definition.refresh_from_db()
        self.assertEqual(self.form_definition.name, 'Project inquiry')
