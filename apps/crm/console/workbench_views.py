from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.views.decorators.http import require_GET

from .access import primary_role_label
from .navigation import build_navigation
from .payloads import PUBLIC_PREVIEW_BASE_URL, default_site_locale


def _build_role_workbench(**kwargs):
    """Late-bind the query module so URL loading stays independent of rollout order."""

    from .workbench_queries import build_role_workbench

    return build_role_workbench(**kwargs)


def _requested_locale_code(request) -> str | None:
    value = (request.GET.get('locale') or '').strip().lower()
    return value or None


@login_required
@require_GET
def workbench(request):
    """Render the canonical, role-aware v2 workbench.

    Query assembly lives in ``workbench_queries`` so this view remains a thin
    HTTP boundary.  It deliberately does not call the legacy sales JSON API.
    """

    site, locale = default_site_locale(_requested_locale_code(request))
    workbench_payload = _build_role_workbench(
        site=site,
        locale=locale,
        user=request.user,
        params=request.GET,
    )
    return render(
        request,
        'console/v2/pages/workbench.html',
        {
            'workbench': workbench_payload,
            'nav_items': build_navigation('workbench', request.user),
            'console_role_label': primary_role_label(request.user),
            'preview_site_url': (
                f'{PUBLIC_PREVIEW_BASE_URL}/?lang={locale.locale_code}'
            ),
            'create_actions': [],
        },
    )
