"""Default sensitive-response policy for the admin surface, including errors."""

from django.utils.cache import add_never_cache_headers


class AdminResponsePolicyMiddleware:
    """Outside auth/security middleware so redirects and early denials are covered."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        path = request.path_info
        if path in {'/admin', '/console'} or path.startswith(('/admin/', '/console/')):
            # Merge, rather than replace, stricter per-view cache directives.
            # Do not consume streaming bodies or alter public/static policies.
            add_never_cache_headers(response)
        return response
