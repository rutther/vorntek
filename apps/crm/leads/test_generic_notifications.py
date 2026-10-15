from django.test import SimpleTestCase
from django.utils import timezone

from leads.models import LeadFormDefinition, LeadSubmission
from leads.notifications import _html_body, _subject, _text_body


class GenericNotificationContentTests(SimpleTestCase):
    def lead(self, fields=None):
        form = LeadFormDefinition(category='general', form_schema={} if fields is None else {'fields': fields})
        return LeadSubmission(id=12, form=form, full_name='Synthetic person', email='person@example.test',
            company='Synthetic organization', message='Synthetic inquiry', submitted_at=timezone.now(),
            payload_json={'product_category': 'disabled-category-value', 'capacity': 'disabled-parameter-value'})

    def test_neutral_defaults_do_not_leak_disabled_industry_fields(self):
        lead = self.lead()
        plain = _text_body(lead, reminder=False)
        html = _html_body(lead, reminder=False)
        subject = _subject(lead, reminder=False)
        for text in (plain, html, subject):
            self.assertNotIn('disabled-category-value', text)
            self.assertNotIn('disabled-parameter-value', text)
            self.assertNotIn('产能目标', text)
        self.assertIn('咨询内容', plain)
        self.assertIn('person@example.test', plain)

    def test_configured_extra_fields_use_labels_choices_and_html_escaping(self):
        lead = self.lead([
            {'name': 'full_name', 'label': '称呼', 'type': 'text'},
            {'name': 'email', 'label': '回复邮箱', 'type': 'email'},
            {'name': 'topic', 'label': '服务主题', 'type': 'select',
             'options': [{'value': 'training', 'label': '培训咨询'}]},
            {'name': 'context', 'label': '咨询背景', 'type': 'textarea'},
        ])
        lead.payload_json = {'topic': 'training', 'context': '<script>synthetic</script>', 'unconfigured': 'do-not-display'}
        plain = _text_body(lead, reminder=False)
        html = _html_body(lead, reminder=False)
        self.assertIn('服务主题：培训咨询', plain)
        self.assertIn('回复邮箱：person@example.test', plain)
        self.assertIn('&lt;script&gt;synthetic&lt;/script&gt;', html)
        self.assertNotIn('<script>', html)
        self.assertNotIn('do-not-display', plain + html)

    def test_disabled_core_field_is_not_reintroduced_by_notification_template(self):
        lead = self.lead([
            {'name': 'full_name', 'label': '称呼', 'type': 'text'},
            {'name': 'email', 'label': '回复邮箱', 'type': 'email'},
            {'name': 'company', 'label': '公司', 'type': 'text', 'enabled': False},
        ])
        self.assertNotIn('Synthetic organization', _text_body(lead, reminder=False))
        self.assertNotIn('Synthetic organization', _subject(lead, reminder=False))
