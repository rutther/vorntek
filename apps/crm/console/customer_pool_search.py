from __future__ import annotations

import secrets
from time import time


SESSION_KEY = 'customer_pool_sensitive_searches'
SEARCH_TTL_SECONDS = 30 * 60
MAX_SAVED_SEARCHES = 5


def _clean(value: str) -> str:
    return ' '.join(str(value or '').split())[:120]


def store_sensitive_search(request, value: str) -> str:
    """Store contact PII server-side and return an opaque, session-bound handle."""
    query = _clean(value)
    if not query:
        return ''
    now = int(time())
    existing = request.session.get(SESSION_KEY, {})
    if not isinstance(existing, dict):
        existing = {}
    fresh = [
        (token, item)
        for token, item in existing.items()
        if isinstance(item, dict)
        and isinstance(item.get('created_at'), int)
        and now - item['created_at'] <= SEARCH_TTL_SECONDS
        and isinstance(item.get('query'), str)
    ]
    fresh.sort(key=lambda pair: pair[1]['created_at'], reverse=True)
    token = secrets.token_urlsafe(18)
    searches = {saved_token: item for saved_token, item in fresh[: MAX_SAVED_SEARCHES - 1]}
    searches[token] = {'query': query, 'created_at': now}
    request.session[SESSION_KEY] = searches
    request.session.modified = True
    return token


def resolve_sensitive_search(request, token: str) -> str:
    """Resolve an opaque handle only inside the browser session that created it."""
    handle = str(token or '')[:80]
    if not handle:
        return ''
    now = int(time())
    existing = request.session.get(SESSION_KEY, {})
    if not isinstance(existing, dict):
        return ''
    searches = {
        saved_token: item
        for saved_token, item in existing.items()
        if isinstance(item, dict)
        and isinstance(item.get('created_at'), int)
        and now - item['created_at'] <= SEARCH_TTL_SECONDS
        and isinstance(item.get('query'), str)
    }
    if searches != existing:
        request.session[SESSION_KEY] = searches
        request.session.modified = True
    item = searches.get(handle)
    return _clean(item.get('query')) if item else ''
