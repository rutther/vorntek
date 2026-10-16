"""Two valid publication requests cannot allocate one locale identity twice."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.test import Client, TransactionTestCase, override_settings
from django.urls import reverse

from console.forms import ArticleEditorForm
from sitecore.models import Article, PageRoute, Site, SiteLocale


@skipUnless(connection.vendor == 'postgresql', 'Requires committed identity allocation on independent PostgreSQL connections')
@override_settings(DEBUG=False, ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class ArticleIdentityConcurrencyTests(TransactionTestCase):
    def _fixture_teardown(self):
        # The isolated driver owns and destroys the complete SQL database.
        return None

    def test_two_valid_requests_create_one_identity_and_loser_has_no_partial_route(self):
        site = Site.objects.create(code='siteos_demo', name='Synthetic identity race', default_locale='en')
        SiteLocale.objects.create(site=site, locale_code='en', label='English', enabled=True, is_default=True)
        clients = []
        for index in range(2):
            # Commit authority intentionally serializes writes sharing an actor
            # or role-group row. Two independent authorized publishers exercise
            # the content identity race without deadlocking the test barrier.
            admin = get_user_model().objects.create_superuser(
                username=f'identity-publisher-{index}', password='synthetic-only')
            client = Client(raise_request_exception=False)
            client.force_login(admin)
            clients.append(client)
        barrier = threading.Barrier(2)
        original_save = ArticleEditorForm.save

        def staged_save(form, *args, **kwargs):
            barrier.wait(timeout=8)
            return original_save(form, *args, **kwargs)

        def submit(index):
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    pid = cursor.fetchone()[0]
                    cursor.execute("SET lock_timeout = '8s'")
                response = clients[index].post(reverse('console:article_create'),
                    {'title': f'Synthetic candidate {index}', 'slug': f'identity-candidate-{index}',
                     'status': 'published', 'content_key': 'sharedIdentity',
                     'reading_minutes': '3', 'body_markdown': f'Complete body {index}'})
                return pid, response
            finally:
                close_old_connections()

        with patch.object(ArticleEditorForm, 'save', staged_save), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, (0, 1)))
        self.assertNotEqual(results[0][0], results[1][0])
        self.assertEqual(sorted(response.status_code for _, response in results), [200, 302])
        self.assertEqual(Article.objects.filter(route__site=site, config_json__contentKey='sharedIdentity').count(), 1)
        self.assertEqual(PageRoute.objects.filter(site=site, route_type='article').count(), 1)
        rejected = next(response for _, response in results if response.status_code == 200)
        self.assertContains(rejected, '关联标识')
        print('REAL_ARTICLE_IDENTITY_RACE ' + json.dumps([
            {'pid': pid, 'response': response.status_code} for pid, response in results]))
