from django.core.management.base import BaseCommand, CommandError

from leads.whatsapp_templates import sync_whatsapp_templates
from marketing.models import MarketingIntegration


class Command(BaseCommand):
    help = 'Synchronize approved WhatsApp templates from the configured WABA.'

    def add_arguments(self, parser):
        parser.add_argument('--integration-id', type=int)

    def handle(self, *args, **options):
        queryset = MarketingIntegration.objects.select_related('provider').filter(
            provider__code='whatsapp', integration_type='cloud_api', enabled=True,
        )
        if options.get('integration_id'):
            queryset = queryset.filter(pk=options['integration_id'])
        integrations = list(queryset.order_by('id'))
        if not integrations:
            raise CommandError('No enabled WhatsApp Cloud API integration matched.')
        for integration in integrations:
            result = sync_whatsapp_templates(integration=integration)
            self.stdout.write(
                self.style.SUCCESS(
                    f'integration={integration.id} received={result.received} '
                    f'created={result.created} updated={result.updated}'
                )
            )
