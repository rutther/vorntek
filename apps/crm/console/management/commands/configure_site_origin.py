"""Explicit, stale-state-guarded origin change for an existing installation."""
import json
from urllib.parse import urlsplit

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from console.audit import record_audit
from sitecore.models import Site


def checked_origin(value, *, old=False):
    try:
        if not value or len(value) > 500 or any(c.isspace() for c in value):
            raise ValueError
        parsed = urlsplit(value)
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password
                or parsed.path not in ('', '/') or parsed.query or parsed.fragment
                or (parsed.port is not None and not 1 <= parsed.port <= 65535)):
            raise ValueError
        if not old and parsed.scheme != 'https' and parsed.hostname not in {'localhost', '127.0.0.1', '::1'}:
            raise ValueError
    except ValueError as exc:
        raise CommandError('Use an HTTP(S) origin without credentials, path or query; public targets require HTTPS.') from exc
    return value.rstrip('/')


class Command(BaseCommand):
    help = 'Plan an existing site origin update; --apply requires exact current value and paused external delivery.'

    def add_arguments(self, parser):
        parser.add_argument('--site-code', required=True)
        parser.add_argument('--origin', required=True)
        parser.add_argument('--expected-current', required=True)
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        origin = checked_origin(options['origin'])
        checked_origin(options['expected_current'], old=True)
        apply = options['apply']
        if apply and any(getattr(settings, name, False) for name in (
                'NEWCROWN_ALLOW_EXTERNAL_IO', 'NEWCROWN_RUN_SCHEDULED_TASKS', 'SITEOS_WHATSAPP_ALLOW_LIVE_SEND')):
            raise CommandError('External delivery and scheduled work must be disabled before changing the site origin.')
        with transaction.atomic():
            query = Site.objects.select_for_update() if apply else Site.objects
            try:
                site = query.get(code=options['site_code'])
            except Site.DoesNotExist as exc:
                raise CommandError('The requested site does not exist; no site was created.') from exc
            if site.base_url != options['expected_current']:
                raise CommandError('Site origin changed since inspection; no changes were made.')
            result = 'PLAN ONLY'
            if apply and site.base_url != origin:
                before = {'base_url': site.base_url}
                site.base_url = origin
                site.save(update_fields=['base_url', 'updated_at'])
                record_audit(actor='configure_site_origin', action='site.origin.configure', entity_table='site',
                             entity_id=site.pk, before=before, after={'base_url': origin}, source='system')
                result = 'origin_updated'
            elif apply:
                result = 'unchanged'
        self.stdout.write(json.dumps({'result': result, 'site': site.code, 'origin': origin}))
