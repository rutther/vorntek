import json
from datetime import timedelta
import uuid

from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from console.lead_forms import LeadFormEditorForm
from console.test_file_lifecycle_contract import FileLifecycleFixture
from console.views import public_lead_form_definition
from leads.form_schema import FormSchemaError, effective_form_fields, public_form_fields
from leads.models import LeadSubmission
from leads.services import LeadCaptureError, create_submission
from sitecore.models import SiteLocale


@override_settings(NEWCROWN_ALLOW_EXTERNAL_IO=False, NEWCROWN_RUN_SCHEDULED_TASKS=False)
class SelectFieldEditorTests(FileLifecycleFixture, TestCase):
    def data(self, options):
        return {'code': 'editable_select', 'name': 'Synthetic advice', 'category': 'general',
                'channel': 'website', 'scope_type': 'site', 'scope_value': '*', 'status': 'active',
                'submission_event_name': 'Lead', 'contacted_event_name': 'contacted_lead',
                'qualified_event_name': 'qualified_lead', 'won_event_name': 'converted', 'config_json': '{}',
                'field_email_enabled': 'on', 'field_full_name_enabled': 'on',
                'field_product_category_enabled': 'on', 'field_product_category_required': 'on',
                'field_product_category_options': options}

    def test_enabled_select_without_choices_is_a_normal_editor_error(self):
        editor = LeadFormEditorForm(self.data(''), site=self.site)
        self.assertFalse(editor.is_valid(), 'Do not save a select that a visitor cannot fill')
        self.assertIn('选项', str(editor.errors))

    def test_choices_save_publish_validate_and_persist_with_the_same_values(self):
        editor = LeadFormEditorForm(self.data('[{"value":"advice","label":"个人咨询"},{"value":"team","label":"团队协作"}]'), site=self.site)
        self.assertIn('field_product_category_options', editor.fields)
        self.assertTrue(editor.is_valid(), editor.errors)
        record = editor.save(); record.refresh_from_db()
        field = next(f for f in public_form_fields(record) if f['name'] == 'product_category')
        self.assertEqual(field['options'], [{'value': 'advice', 'label': '个人咨询'}, {'value': 'team', 'label': '团队协作'}])
        response = public_lead_form_definition(RequestFactory().get('/definition/'), record.code)
        self.assertEqual(response.status_code, 200)
        self.assertIn(field, json.loads(response.content)['form']['fields'])
        locale = SiteLocale.objects.create(site=self.site, locale_code='en', label='English', is_default=True, enabled=True)
        payload = {'full_name': 'Synthetic chooser', 'email': 'select-test@example.invalid',
                   'extra_fields': {'product_category': 'advice'}, 'client_event_id': str(uuid.uuid4()),
                   'form_started_at': (timezone.now() - timedelta(seconds=30)).isoformat(),
                   'consent': {'contact': True, 'privacy_notice': True, 'marketing': False}}
        result = create_submission(form_definition=record, locale=locale, payload=payload, client_ip='127.0.0.1', user_agent='Synthetic test')
        saved = LeadSubmission.objects.get(pk=result.submission.pk)
        self.assertEqual(saved.payload_json['product_category'], 'advice')
        payload['extra_fields']['product_category'] = 'unknown-choice'
        with self.assertRaises(LeadCaptureError):
            create_submission(form_definition=record, locale=locale, payload=payload, client_ip='127.0.0.1', user_agent='Synthetic test')

    def test_plain_lines_edit_choices_and_reopen_them_without_losing_options(self):
        editor = LeadFormEditorForm(self.data('个人咨询\n团队协作'), site=self.site)
        self.assertTrue(editor.is_valid(), editor.errors)
        record = editor.save()
        reopened = LeadFormEditorForm(site=self.site, form_definition=record)
        self.assertIn('个人咨询', reopened['field_product_category_options'].value())
        self.assertEqual(next(f for f in public_form_fields(record) if f['name'] == 'product_category')['options'], ['个人咨询', '团队协作'])

    def test_configured_empty_select_is_not_a_public_definition(self):
        from types import SimpleNamespace
        record = SimpleNamespace(category='general', form_schema={'fields': [
            {'name': 'email', 'type': 'email', 'enabled': True},
            {'name': 'request_kind', 'type': 'select', 'enabled': True, 'required': True, 'options': []}]})
        with self.assertRaises(FormSchemaError): effective_form_fields(record)
