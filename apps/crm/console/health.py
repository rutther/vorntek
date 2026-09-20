from django.db import connection
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from siteos_admin.release import release_version


@never_cache
@require_GET
def healthz(request):
    version = release_version()
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
    except Exception:
        return JsonResponse(
            {
                'status': 'unavailable',
                'service': 'vorntek-crm',
                'version': version,
                'database': 'unavailable',
            },
            status=503,
        )
    return JsonResponse(
        {'status': 'ok', 'service': 'vorntek-crm', 'version': version, 'database': 'ok'}
    )
