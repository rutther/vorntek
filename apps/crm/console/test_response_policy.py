"""Real HTTP response policy, including redirects/rejections and streaming."""

from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse, StreamingHttpResponse
from django.test import SimpleTestCase, override_settings
from django.urls import path
from django.views.decorators.http import require_POST


def html(request):
    return HttpResponse('Synthetic private page')


def json_response(request):
    return JsonResponse({'synthetic': True})


def stream(request):
    response = StreamingHttpResponse(iter([b'Synthetic download']))
    response['Content-Disposition'] = 'attachment; filename="synthetic.csv"'
    return response


def denied(request):
    raise PermissionDenied('Synthetic denial')


def redirect(request):
    return HttpResponseRedirect('/admin/html/')


@require_POST
def write_only(request):
    return HttpResponse('Synthetic write')


def existing_policy(request):
    response = HttpResponse('Synthetic stricter response')
    response['Cache-Control'] = 'private, no-store, no-cache, max-age=0, must-revalidate'
    response['Vary'] = 'Accept-Language'
    return response


def public_asset(request):
    response = HttpResponse('Synthetic immutable asset')
    response['Cache-Control'] = 'public, max-age=31536000, immutable'
    return response


urlpatterns = [
    path('admin/html/', html), path('admin/json/', json_response), path('admin/download/', stream),
    path('admin/denied/', denied), path('admin/redirect/', redirect),
    path('admin/write-only/', write_only), path('admin/strict/', existing_policy),
    path('static/public.js', public_asset), path('api/public/', public_asset),
]


@override_settings(ROOT_URLCONF=__name__)
class AdminResponsePolicyTests(SimpleTestCase):
    def _assert_private(self, response):
        directives = {part.strip().lower() for part in response.headers.get('Cache-Control', '').split(',')}
        self.assertTrue({'private', 'no-store', 'max-age=0'}.issubset(directives))
        self.assertNotIn('public', directives)

    def test_admin_html_json_redirect_rejection_and_missing_object_are_not_stored(self):
        for url, status in (
            ('/admin/html/', 200), ('/admin/json/', 200), ('/admin/redirect/', 302),
            ('/admin/denied/', 403), ('/admin/missing/', 404), ('/admin/write-only/', 405),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, status)
                self._assert_private(response)

    def test_streaming_download_stays_streamed_with_disposition_and_cache_policy(self):
        response = self.client.get('/admin/download/')
        self.assertTrue(response.streaming)
        self.assertEqual(response['Content-Disposition'], 'attachment; filename="synthetic.csv"')
        self._assert_private(response)
        self.assertEqual(b''.join(response.streaming_content), b'Synthetic download')

    def test_existing_stricter_policy_and_vary_are_retained(self):
        response = self.client.get('/admin/strict/')
        self._assert_private(response)
        self.assertIn('must-revalidate', response['Cache-Control'])
        self.assertIn('no-cache', response['Cache-Control'])
        self.assertIn('Accept-Language', response['Vary'])

    def test_public_static_and_public_api_keep_their_explicit_cache_policy(self):
        for url in ('/static/public.js', '/api/public/'):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response['Cache-Control'], 'public, max-age=31536000, immutable')

    @override_settings(SECURE_SSL_REDIRECT=True)
    def test_outer_https_redirect_for_admin_is_not_stored(self):
        response = self.client.get('/admin/html/')
        self.assertEqual(response.status_code, 301)
        self._assert_private(response)
