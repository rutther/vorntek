from datetime import timedelta
from types import SimpleNamespace

from django.test import SimpleTestCase
from django.utils import timezone

from leads.industrial import BUSINESS_LINES
from leads.notifications import _html_body, _text_body
from leads.services import LeadCaptureError, clean_submission_input


class IndustrialInquiryTests(SimpleTestCase):
    def payload(self, line='circulatingWaterControl'):
        return {
            'full_name': 'Synthetic tester', 'company': 'Vorntek synthetic buyer',
            'country': 'China', 'email': 'tester@example.invalid', 'phone': '',
            'message': 'Synthetic request; do not contact.',
            'source_url': 'http://localhost:8088/contact/',
            'form_started_at': (timezone.now() - timedelta(seconds=5)).isoformat(),
            'consent': {'contact': True, 'privacy_notice': True, 'marketing': False},
            'extra_fields': {'business_line': line, 'product_category': 'Untrusted label',
                             'application_context': 'Synthetic machine monitoring'},
        }

    def clean(self, payload, category='industrial'):
        return clean_submission_input(payload, form_definition=SimpleNamespace(site_id=7, id=11, category=category))

    def test_all_seven_lines_accept_without_beverage_capacity(self):
        self.assertEqual(len(BUSINESS_LINES), 7)
        for line, label in BUSINESS_LINES.items():
            with self.subTest(line=line):
                cleaned = self.clean(self.payload(line))
                self.assertEqual(cleaned['payload_json']['business_line'], line)
                self.assertEqual(cleaned['payload_json']['product_category'], label)
                self.assertNotIn('capacity', cleaned['payload_json'])
                self.assertFalse(cleaned['consent_json']['marketing'])

    def test_missing_unknown_and_non_scalar_business_lines_are_validation_errors(self):
        for value in ('', None, 'unknown', [], {}, 7):
            with self.subTest(value=value), self.assertRaises(LeadCaptureError):
                self.clean(self.payload(value))

    def test_project_message_required(self):
        for value in ('', ' ', 'x'):
            payload = self.payload()
            payload['message'] = value
            with self.subTest(value=value), self.assertRaises(LeadCaptureError):
                self.clean(payload)

    def test_legacy_forms_still_require_capacity(self):
        payload = self.payload()
        with self.assertRaises(LeadCaptureError):
            self.clean(payload, category='project')
        payload['extra_fields']['capacity'] = 'legacy capacity'
        self.assertEqual(self.clean(payload, category='project')['payload_json']['capacity'], 'legacy capacity')

    def test_notification_displays_application_context_and_escapes_html(self):
        payload = self.payload()
        payload['extra_fields']['application_context'] = '<script>example</script>'
        cleaned = self.clean(payload)
        submission = SimpleNamespace(id=1, submitted_at=timezone.now(), **cleaned)
        plain = _text_body(submission, reminder=False)
        markup = _html_body(submission, reminder=False)
        self.assertIn('应用背景', plain)
        self.assertNotIn('产能目标', plain)
        self.assertIn('&lt;script&gt;example&lt;/script&gt;', markup)
        self.assertNotIn('<script>', markup)
