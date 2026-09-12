from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase


class PipelineValueSummaryTests(SimpleTestCase):
    def test_pipeline_totals_are_kept_separate_by_currency(self):
        from .sales_queries import pipeline_value_summary

        rows = [
            SimpleNamespace(stage='quotation', value_amount=Decimal('120000'), currency='USD'),
            SimpleNamespace(stage='quotation', value_amount=Decimal('80000'), currency='usd'),
            SimpleNamespace(stage='quotation', value_amount=Decimal('95000'), currency='EUR'),
            SimpleNamespace(stage='quotation', value_amount=None, currency='USD'),
        ]
        self.assertEqual(
            pipeline_value_summary(rows),
            [
                {'currency': 'EUR', 'amount': Decimal('95000')},
                {'currency': 'USD', 'amount': Decimal('200000')},
            ],
        )
