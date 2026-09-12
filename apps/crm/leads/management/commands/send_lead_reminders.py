from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from leads.models import LeadSubmission
from leads.notifications import email_delivery_configured, send_submission_notification


class Command(BaseCommand):
    help = 'Retry failed new-lead notifications and remind sales about overdue unprocessed leads.'

    def handle(self, *args, **options):
        if not settings.NEWCROWN_ALLOW_EXTERNAL_IO:
            self.stdout.write('External I/O disabled; reminders skipped without reading leads.')
            return
        if not email_delivery_configured():
            self.stdout.write(self.style.WARNING('SMTP is not configured; notification retries and reminders skipped.'))
            return

        now = timezone.now()
        retry_cutoff = now - timedelta(minutes=10)
        overdue_cutoff = now - timedelta(hours=settings.SITEOS_LEAD_REMINDER_HOURS)
        repeat_cutoff = now - timedelta(hours=settings.SITEOS_LEAD_REMINDER_REPEAT_HOURS)

        retried = 0
        retry_sent = 0
        failed_notifications = (
            LeadSubmission.objects.select_related('form')
            .filter(stage='new', notification_status__in=['pending', 'failed'], submitted_at__lte=retry_cutoff)
            .order_by('submitted_at')[:100]
        )
        for submission in failed_notifications:
            retried += 1
            if send_submission_notification(submission):
                retry_sent += 1

        reminded = 0
        reminder_sent = 0
        overdue = (
            LeadSubmission.objects.select_related('form')
            .filter(stage='new', submitted_at__lte=overdue_cutoff)
            .filter(Q(reminder_sent_at__isnull=True) | Q(reminder_sent_at__lte=repeat_cutoff))
            .order_by('submitted_at')[:100]
        )
        for submission in overdue:
            reminded += 1
            if send_submission_notification(submission, reminder=True):
                reminder_sent += 1

        self.stdout.write(
            self.style.SUCCESS(
                f'notification retries {retry_sent}/{retried}; overdue reminders {reminder_sent}/{reminded}'
            )
        )
