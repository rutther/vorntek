from django.conf import settings


def brand_context(request):
    return {'brand_name': settings.VORNTEK_BRAND_NAME}
