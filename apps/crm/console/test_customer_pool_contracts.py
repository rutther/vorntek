from pathlib import Path

from django.test import SimpleTestCase

from console.customer_pool_exports import _excel_text
from console.customer_pool_views import _customer_template_workbook


class CustomerPoolTemplateContractTests(SimpleTestCase):
    def test_import_template_has_three_routed_sheets_and_fake_example_only(self):
        workbook = _customer_template_workbook()

        self.assertEqual(workbook.sheetnames, ['填写说明', '企业', '联系方式'])
        self.assertEqual(workbook['企业']['A1'].value, '企业外部键*')
        self.assertEqual(workbook['联系方式']['D1'].value, '联系方式类型*')
        self.assertEqual(workbook['企业']['A2'].value, 'example-company-001')
        self.assertIn('example.invalid', workbook['企业']['F2'].value)
        self.assertEqual(len(workbook['企业'].data_validations.dataValidation), 1)
        self.assertEqual(len(workbook['联系方式'].data_validations.dataValidation), 1)

    def test_export_text_neutralizes_spreadsheet_formulas(self):
        for unsafe in ('=2+2', '+SUM(A1:A2)', '-1+1', '@cmd'):
            with self.subTest(value=unsafe):
                self.assertEqual(_excel_text(unsafe), "'" + unsafe)
        self.assertEqual(_excel_text('Normal company'), 'Normal company')

    def test_customer_pool_page_uses_existing_v2_shell_and_real_controls(self):
        template = Path(__file__).resolve().parent / 'templates' / 'console' / 'v2' / 'pages' / 'customer_pool.html'
        source = template.read_text(encoding='utf-8')

        self.assertIn("{% extends 'console/v2/base.html' %}", source)
        self.assertIn('data-pool-select-all', source)
        self.assertIn('workspace.manual_create_url', source)
        self.assertIn('workspace.export_url', source)
        self.assertIn('workspace.import_url', source)

    def test_customer_pool_migration_keeps_source_and_intake_separate(self):
        migration = Path(__file__).resolve().parents[1] / 'db' / 'migrations' / '0027_customer_pool_core.sql'
        source = migration.read_text(encoding='utf-8')

        self.assertIn('source_type text NOT NULL', source)
        self.assertIn('intake_method text NOT NULL', source)
        self.assertIn("state IN ('available', 'owned', 'review', 'archived')", source)
        self.assertIn('DEFERRABLE INITIALLY DEFERRED', source)
