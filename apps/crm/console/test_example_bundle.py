import json
import copy
from datetime import timedelta
from io import StringIO
from pathlib import Path
import tempfile
import unittest
import hashlib
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, connection
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from console.models import ExampleDataBundle
from leads.models import Activity, Company, Contact, LeadFormDefinition, LeadSubmission, Opportunity, OpportunityStageHistory, SalesTeam, Task
from marketing.models import MarketingIntegration, MarketingLoop, MarketingProvider, MarketingRuntimeBinding
from sitecore.models import Article, FreePage, PageRoute, Site, SiteLocale


class ExampleBundlePlanTests(SimpleTestCase):
    def test_default_command_only_plans_the_cross_module_bundle_without_a_database(self):
        output = StringIO()
        call_command('seed_example_bundle', stdout=output)
        plan = json.loads(output.getvalue())
        self.assertEqual(plan['result'], 'PLAN ONLY')
        self.assertEqual(plan['pack'], 'vorntek-general-v1')
        self.assertEqual(set(plan['modules']), {'forms', 'organizations', 'contacts', 'inquiries',
                         'opportunities', 'tasks', 'flows', 'articles', 'pages'})
        self.assertTrue(all(value > 0 for value in plan['modules'].values()))
        self.assertIn('synthetic', plan['notice'].lower())

    def test_apply_requires_explicit_synthetic_target_acknowledgement_before_database_access(self):
        with self.assertRaisesRegex(CommandError, 'synthetic-only'):
            call_command('seed_example_bundle', apply=True)

    @override_settings(NEWCROWN_ALLOW_EXTERNAL_IO=True, NEWCROWN_RUN_SCHEDULED_TASKS=False)
    def test_live_delivery_is_refused_before_database_access(self):
        with self.assertRaisesRegex(CommandError, 'disabled'):
            call_command('seed_example_bundle', apply=True, synthetic_only=True, owner='operator')

    def test_bundle_contract_rejects_real_contact_routes_or_credentials(self):
        from console.example_bundle import DATA_FILE, load_bundle
        source = json.loads(DATA_FILE.read_text(encoding='utf-8'))
        for changes in ('contact', 'website', 'secret'):
            data = copy.deepcopy(source)
            if changes == 'contact':
                data['contacts'][0]['email'] = 'person@real-company.com'
            elif changes == 'website':
                data['organizations'][0]['website'] = 'https://real-company.com'
            else:
                data['api_token'] = 'must-not-enter-an-example-pack'
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'examples.json'
                path.write_text(json.dumps(data), encoding='utf-8')
                from django.core.exceptions import ValidationError
                with patch('console.example_bundle.DATA_FILE', path), self.assertRaises(ValidationError):
                    load_bundle()


@override_settings(NEWCROWN_ALLOW_EXTERNAL_IO=False, NEWCROWN_RUN_SCHEDULED_TASKS=False,
                   SITEOS_WHATSAPP_ALLOW_LIVE_SEND=False)
class ExampleBundleLifecycleTests(TestCase):
    @classmethod
    def setUpClass(cls):
        existing = set(connection.introspection.table_names())
        cls.created_models = []
        try:
            with connection.schema_editor() as editor:
                for model in apps.get_models():
                    if (not model._meta.managed and not model._meta.proxy
                            and model._meta.db_table not in existing):
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
        self.actor = get_user_model().objects.create_superuser(
            'example-admin', 'operator@example.invalid', 'example-test-only')
        self.site = Site.objects.create(code='siteos_demo', name='Acceptance site',
            base_url='https://preview.example.invalid', config_json={'keep_this':True})
        for code in ('en', 'zh'):
            SiteLocale.objects.create(site=self.site, locale_code=code, label=code, is_default=code == 'en')
        MarketingProvider.objects.get_or_create(code='meta', defaults={'name':'Meta', 'enabled':True})
        self.nonseed = Company.objects.create(site=self.site, name='Existing organization',
                                              normalized_name='existing organization')

    def command(self, **options):
        output = StringIO()
        call_command('seed_example_bundle', apply=True, synthetic_only=True, site=self.site.code,
                     owner=self.actor.username, stdout=output, **options)
        return json.loads(output.getvalue())

    def business_counts(self):
        return {model._meta.label: model.objects.count() for model in
                (Company, Contact, LeadFormDefinition, LeadSubmission, Opportunity, Task,
                 MarketingLoop, Article, FreePage)}

    def test_real_rows_relations_and_states_are_imported_without_live_bindings(self):
        result = self.command()
        self.assertEqual(result['result'], 'IMPORTED')
        self.assertEqual(LeadFormDefinition.objects.filter(code__startswith='demo-').count(), 3)
        self.assertEqual(LeadSubmission.objects.count(), 9)
        self.assertEqual(set(LeadSubmission.objects.values_list('notification_status', flat=True)), {'disabled'})
        self.assertEqual(Opportunity.objects.count(), 4)
        self.assertEqual(Task.objects.count(), 6)
        self.assertEqual(MarketingLoop.objects.count(), 3)
        self.assertEqual(Article.objects.count(), 4)
        self.assertEqual(FreePage.objects.count(), 2)
        self.assertEqual(MarketingRuntimeBinding.objects.count(), 0)
        self.assertEqual(MarketingIntegration.objects.count(), 0)
        personal = Opportunity.objects.get(name='示例 · 个人支持协作')
        self.assertIsNone(personal.company_id)
        self.assertEqual(personal.primary_contact.email, 'personal-demo@example.invalid')
        self.assertEqual(personal.primary_contact.full_name, '')
        self.assertEqual(set(Task.objects.values_list('status', flat=True)),
                         {'open', 'in_progress', 'completed', 'canceled'})
        self.assertTrue(LeadSubmission.objects.filter(trashed_at__isnull=False).exists())
        trashed = LeadSubmission.objects.get(trashed_at__isnull=False)
        self.assertEqual(Activity.objects.filter(submission=trashed,
                         metadata_json__workflow='lead_trash_v2').count(), 1)
        self.assertEqual(ExampleDataBundle.objects.get().state, 'active')

    def test_repeated_import_adds_nothing_and_preserves_later_edits(self):
        self.command()
        lead = LeadSubmission.objects.first()
        lead.message = 'Later operator work must survive repeat import'
        lead.save(update_fields=['message', 'updated_at'])
        before = self.business_counts()
        result = self.command()
        self.assertEqual(result['result'], 'ALREADY INSTALLED')
        self.assertEqual(self.business_counts(), before)
        lead.refresh_from_db()
        self.assertEqual(lead.message, 'Later operator work must survive repeat import')

    def test_example_articles_can_use_the_real_bilingual_publishing_contract(self):
        from console.article_delivery import snapshot_cms_articles
        self.command()
        Article.objects.update(status='published', published_at=timezone.now())
        PageRoute.objects.filter(route_type='article').update(status='published', published_at=timezone.now())
        release = snapshot_cms_articles(site=self.site, public_origin=self.site.base_url)
        self.assertEqual(len(release['articles']), 4)
        self.assertEqual(release['missingTranslations'], [])
        for article in release['articles']:
            self.assertEqual(set(article['alternates']), {'en','zh','x-default'})
        with self.assertRaisesRegex(CommandError, 'modified'):
            self.command(reset=True)

    def test_reset_refuses_modified_rows_without_partial_deletion(self):
        self.command()
        lead = LeadSubmission.objects.first()
        lead.message = 'New business information'
        lead.save(update_fields=['message', 'updated_at'])
        before = self.business_counts()
        with self.assertRaisesRegex(CommandError, 'modified'):
            self.command(reset=True)
        self.assertEqual(self.business_counts(), before)
        self.assertEqual(ExampleDataBundle.objects.get().state, 'active')

    def test_reset_refuses_new_nonseed_references_without_deleting_them(self):
        self.command()
        company = Company.objects.exclude(pk=self.nonseed.pk).first()
        team = SalesTeam.objects.get(site=self.site, code='demo_examples')
        later = Task.objects.create(site=self.site, company=company, team=team, owner_user=self.actor,
            title='Later real operation', due_at=timezone.now() + timedelta(days=1))
        before = self.business_counts()
        with self.assertRaisesRegex(CommandError, 'referenced'):
            self.command(reset=True)
        self.assertTrue(Task.objects.filter(pk=later.pk).exists())
        self.assertEqual(self.business_counts(), before)

    def test_reset_detects_foreign_keys_from_a_new_unmodeled_table(self):
        self.command()
        company = Company.objects.exclude(pk=self.nonseed.pk).first()
        with connection.cursor() as cursor:
            cursor.execute('CREATE TABLE example_test_future_fk (id integer PRIMARY KEY, company_id bigint REFERENCES crm_company(id))')
            try:
                cursor.execute('INSERT INTO example_test_future_fk (id, company_id) VALUES (%s, %s)', [1, company.pk])
                before = self.business_counts()
                with self.assertRaisesRegex(CommandError, 'referenced'):
                    self.command(reset=True)
                cursor.execute('SELECT company_id FROM example_test_future_fk WHERE id = 1')
                self.assertEqual(cursor.fetchone()[0], company.pk)
                self.assertEqual(self.business_counts(), before)
            finally:
                cursor.execute('DROP TABLE example_test_future_fk')

    def test_pristine_reset_leaves_accounts_config_and_preexisting_business_intact(self):
        original_password = self.actor.password
        original_config = self.site.config_json
        self.command()
        before = self.business_counts()
        identities = list(LeadSubmission.objects.values_list('pk', flat=True))
        history = {model._meta.label:list(model.objects.order_by('pk').values())
                   for model in (Activity, OpportunityStageHistory)}
        task = Task.objects.get(title='示例 · 核对项目范围')
        original_due = task.due_at
        tomorrow = timezone.now() + timedelta(days=1)
        with patch('console.example_bundle.timezone.now', return_value=tomorrow):
            result = self.command(reset=True)
        self.assertEqual(result['result'], 'RESET')
        self.assertEqual(self.business_counts(), before)
        self.assertTrue(Company.objects.filter(pk=self.nonseed.pk).exists())
        self.assertEqual(list(LeadSubmission.objects.values_list('pk', flat=True)), identities)
        for model in (Activity, OpportunityStageHistory):
            self.assertEqual(list(model.objects.order_by('pk').values()), history[model._meta.label])
        task.refresh_from_db()
        self.assertGreater(task.due_at, original_due + timedelta(hours=23))
        self.actor.refresh_from_db()
        self.site.refresh_from_db()
        self.assertEqual(self.actor.password, original_password)
        self.assertTrue(self.actor.is_superuser)
        self.assertEqual(self.site.config_json, original_config)
        self.assertEqual(ExampleDataBundle.objects.get().state, 'active')
        self.assertEqual(self.command()['result'], 'ALREADY INSTALLED')
        self.assertEqual(ExampleDataBundle.objects.count(), 1)

    def test_import_failure_rolls_back_the_entire_bundle(self):
        before = self.business_counts()
        with patch('console.example_bundle._create_content', side_effect=RuntimeError('injected import failure')):
            with self.assertRaisesRegex(RuntimeError, 'injected import failure'):
                self.command()
        self.assertEqual(self.business_counts(), before)
        self.assertEqual(ExampleDataBundle.objects.count(), 0)
        self.assertFalse(SalesTeam.objects.filter(code='demo_examples').exists())

    @unittest.skipUnless(connection.vendor == 'postgresql', 'Requires the real encrypted PostgreSQL vault')
    def test_import_and_reset_preserve_existing_encrypted_credentials(self):
        from console.secret_store import load_secret, store_secret
        store_secret(secret_key='synthetic-preexisting-credential',
            plaintext='synthetic-vault-sentinel-not-a-live-key', updated_by='acceptance')
        self.command()
        self.command(reset=True)
        self.assertEqual(load_secret('synthetic-preexisting-credential'),
                         'synthetic-vault-sentinel-not-a-live-key')

    def test_conflicting_existing_form_is_preserved_and_no_partial_examples_are_created(self):
        form = LeadFormDefinition.objects.create(site=self.site, code='demo-personal',
            name='Preexisting configuration', category='general', config_json={'keep_this':True})
        before = self.business_counts()
        with self.assertRaisesRegex(CommandError, 'conflict'):
            self.command()
        form.refresh_from_db()
        self.assertEqual(form.name, 'Preexisting configuration')
        self.assertEqual(form.config_json, {'keep_this':True})
        self.assertEqual(self.business_counts(), before)
        self.assertEqual(ExampleDataBundle.objects.count(), 0)

    def test_missing_owner_or_provider_is_refused_without_partial_examples(self):
        from console.example_bundle import apply_bundle
        before = self.business_counts()
        with self.assertRaisesRegex(CommandError, 'existing'):
            apply_bundle(site_code=self.site.code, owner_name='missing-owner')
        self.assertEqual(self.business_counts(), before)
        MarketingProvider.objects.filter(code='meta').update(enabled=False)
        with self.assertRaisesRegex(CommandError, 'provider'):
            self.command()
        self.assertEqual(self.business_counts(), before)

    @unittest.skipUnless(connection.vendor == 'postgresql', 'Requires the real PostgreSQL initializer')
    def test_failed_real_initializer_step_leaves_no_partial_table_or_ledger_entry(self):
        from django.conf import settings
        from siteos_admin.schema_install import Migration, load_chain
        chain = load_chain(settings.BASE_DIR / 'db/migrations')
        sql = 'CREATE TABLE example_initializer_fault_probe(id integer PRIMARY KEY); SELECT 1/0;'
        failed = Migration('0044_initializer_fault_probe.sql', hashlib.sha256(sql.encode()).hexdigest(), sql)
        with patch('console.management.commands.initialize_database.load_chain', return_value=chain+[failed]):
            with self.assertRaises(DatabaseError):
                call_command('initialize_database', apply=True, stdout=StringIO())
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('public.example_initializer_fault_probe')")
            self.assertIsNone(cursor.fetchone()[0])
            cursor.execute('SELECT count(*) FROM siteos_schema_migration WHERE name = %s', [failed.name])
            self.assertEqual(cursor.fetchone()[0], 0)
        call_command('initialize_database', apply=True, stdout=StringIO())
