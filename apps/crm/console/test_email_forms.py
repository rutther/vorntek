from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase

from console.email_forms import SmtpSettingsForm
from console.views import smtp_settings


class SmtpSettingsFormTests(SimpleTestCase):
    def form(self, data):
        runtime = SimpleNamespace(
            host='',
            port=25,
            username='',
            from_email='website@vorntek.example',
            use_ssl=False,
            use_tls=False,
        )
        with (
            patch('console.email_forms.smtp_password_exists', return_value=False),
            patch('console.email_forms.load_stored_smtp_settings', return_value={}),
            patch('console.email_forms.load_runtime_email_config', return_value=runtime),
        ):
            return SmtpSettingsForm(data=data, default_email='demo@example.invalid')

    def valid_data(self):
        return {
            'enabled': 'on',
            'host': 'smtp.example.invalid',
            'port': '465',
            'security': 'ssl',
            'username': 'demo@example.invalid',
            'from_email': 'demo@example.invalid',
            'password': 'app-password',
            'test_recipient': 'demo@example.invalid',
            'action': 'save_test',
        }

    def test_first_enabled_save_requires_authorization_password(self):
        data = self.valid_data()
        data['password'] = ''
        form = self.form(data)

        self.assertFalse(form.is_valid())
        self.assertIn('password', form.errors)

    def test_test_action_requires_recipient(self):
        data = self.valid_data()
        data['test_recipient'] = ''
        form = self.form(data)

        self.assertFalse(form.is_valid())
        self.assertIn('test_recipient', form.errors)

    def test_complete_first_configuration_is_valid(self):
        form = self.form(self.valid_data())

        self.assertTrue(form.is_valid(), form.errors)

    @patch('console.email_forms.smtp_password_exists', return_value=False)
    @patch('console.email_forms.load_stored_smtp_settings', return_value={})
    @patch('console.email_forms.load_runtime_email_config')
    def test_unconfigured_defaults_are_disabled_and_use_notification_mailbox(
        self,
        mocked_runtime,
        mocked_stored,
        mocked_password_exists,
    ):
        mocked_runtime.return_value = SimpleNamespace(
            ready=False,
            host='localhost',
            port=25,
            username='',
            from_email='website@vorntek.example',
            use_ssl=False,
            use_tls=False,
        )

        form = SmtpSettingsForm(default_email='demo@example.invalid')

        self.assertFalse(form.initial['enabled'])
        self.assertEqual(form.initial['username'], 'demo@example.invalid')
        self.assertEqual(form.initial['from_email'], 'demo@example.invalid')


class SmtpSettingsViewTests(SimpleTestCase):
    @patch('console.views.render_form_console')
    @patch('console.views.smtp_password_last4', return_value='')
    @patch('console.views.load_runtime_email_config')
    @patch('console.views.SmtpSettingsForm')
    @patch('console.views.default_site_locale')
    @patch('console.views.LeadFormDefinition.objects.filter')
    def test_page_uses_form_notification_recipients(
        self,
        mocked_filter,
        mocked_default_site_locale,
        mocked_form_class,
        mocked_load_runtime,
        mocked_password_last4,
        mocked_render,
    ):
        lead_form = SimpleNamespace(notify_emails='sales@example.com; owner@example.com')
        mocked_filter.return_value.first.return_value = lead_form
        mocked_default_site_locale.return_value = (
            SimpleNamespace(),
            SimpleNamespace(locale_code='en', label='English'),
        )
        mocked_form_class.return_value = SimpleNamespace()
        mocked_load_runtime.return_value = SimpleNamespace(source='vault')
        mocked_render.return_value = HttpResponse('ok')

        request = RequestFactory().get('/admin/leads/email-settings/?locale=en')
        request.user = SimpleNamespace(is_authenticated=True, get_username=lambda: 'admin')

        response = smtp_settings(request)

        self.assertEqual(response.status_code, 200)
        extra_context = mocked_render.call_args.kwargs['extra_context']
        self.assertEqual(
            extra_context['smtp_notification_recipients'],
            ['sales@example.com', 'owner@example.com'],
        )
