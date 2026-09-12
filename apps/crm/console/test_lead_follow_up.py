from types import SimpleNamespace
from unittest.mock import Mock

from django.test import SimpleTestCase

from .lead_forms import LeadSubmissionEditorForm


class LeadFollowUpHistoryTests(SimpleTestCase):
    def test_new_entry_is_appended_without_overwriting_existing_history(self):
        existing = [{'note': f'entry-{index}'} for index in range(100)]
        submission = SimpleNamespace(
            assignee=None,
            source_channel='website',
            source_detail='',
            next_follow_up_at=None,
            follow_up_notes='',
            qualification_score=0,
            qualification_json={},
            qualification_notes='',
            qualification_overridden=False,
            config_json={'follow_up_history': existing, 'keep': True},
            save=Mock(),
        )
        form = LeadSubmissionEditorForm(
            data={
                'stage': 'contacted',
                'assignee': '',
                'source_channel': 'meta_ads',
                'source_detail': 'Meta campaign',
                'next_follow_up_at': '',
                'follow_up_notes': 'Current account summary',
                'follow_up_entry': 'Spoke with the customer and requested bottle drawings.',
                'buyer_value': '',
                'buyer_currency': 'USD',
            },
            submission=submission,
        )

        self.assertTrue(form.is_valid(), form.errors)
        form.save_operations(actor=SimpleNamespace(get_username=lambda: 'operator'))

        history = submission.config_json['follow_up_history']
        self.assertEqual(len(history), 100)
        self.assertEqual(history[0]['note'], 'entry-1')
        self.assertEqual(history[-1]['actor'], 'operator')
        self.assertEqual(history[-1]['stage'], 'contacted')
        self.assertIn('requested bottle drawings', history[-1]['note'])
        self.assertTrue(submission.config_json['keep'])
        self.assertIn('config_json', submission.save.call_args.kwargs['update_fields'])
