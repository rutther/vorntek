import os
from urllib.parse import urlparse

from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from console.access import GROUP_BY_ROLE
from leads.models import LeadFormDefinition, SalesTeam
from sitecore.models import Site, SiteLocale


class Command(BaseCommand):
    help = 'Create minimum site/form/role configuration; never overwrite existing business settings.'

    @transaction.atomic
    def handle(self, *args, **options):
        url = os.getenv('NEWCROWN_SITE_URL', 'http://localhost:8088').rstrip('/')
        parsed = urlparse(url)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
            raise CommandError('NEWCROWN_SITE_URL must be an HTTP(S) origin without credentials.')
        # Keep the existing single-site lookup contract. Changing this requires a separate migration.
        site, created = Site.objects.get_or_create(code='siteos_demo', defaults={
            'name': os.getenv('VORNTEK_SITE_NAME', os.getenv('NEWCROWN_SITE_NAME', 'Vorntek')), 'base_url': url, 'default_locale': 'en',
            'config_json': {
                'articleDelivery': {
                    'baseRouteMode': 'shared',
                    'brandLogoUrl': '/assets/vorntek/vorntekLogo.png',
                }
            },
        })
        for code, label, direction in [('en', 'English', 'ltr'), ('zh', '中文', 'ltr')]:
            SiteLocale.objects.get_or_create(site=site, locale_code=code, defaults={
                'label': label, 'direction': direction, 'is_default': code == 'en', 'sort_order': 10 if code == 'en' else 20,
            })
        SalesTeam.objects.get_or_create(site=site, code='default', defaults={'name': 'Sales'})
        for group_name in GROUP_BY_ROLE.values():
            Group.objects.get_or_create(name=group_name)
        LeadFormDefinition.objects.get_or_create(site=site, code='project-inquiry', defaults={
            'name': 'Vorntek project inquiry', 'category': 'industrial', 'capi_enabled': False,
            'notify_emails': '', 'success_message': 'Inquiry received. Our team will contact you.',
        })
        self.stdout.write('Site initialized.' if created else 'Existing site preserved; required missing configuration ensured.')
