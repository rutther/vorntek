"""Exercise the WhatsApp CRM contract against an isolated local PostgreSQL DB.

The verifier writes realistic message and status rows inside one outer
transaction and rolls the whole transaction back.  It therefore proves the
PostgreSQL-only locks, constraints and triggers without leaving fixture data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from datetime import timedelta
from pathlib import Path


ADMIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADMIN_DIR))

from scripts.check_candidate_postgres_contract import require_safe_candidate_dsn


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument('--dsn', default=os.getenv('SITEOS_CANDIDATE_POSTGRES_URL', ''))
    return parser.parse_args()


def fingerprint(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')
    ).hexdigest()


def main() -> int:
    args = parse_args()
    require_safe_candidate_dsn(args.dsn)
    os.environ['DJANGO_SETTINGS_MODULE'] = 'siteos_admin.settings'
    os.environ['SITEOS_ADMIN_DEBUG'] = '1'
    os.environ['SITEOS_ADMIN_DATABASE_URL'] = args.dsn
    os.environ['SITEOS_ADMIN_DATABASE_SSLMODE'] = 'disable'
    os.environ['SITEOS_ADMIN_SECURE_SSL_REDIRECT'] = '0'

    import django

    django.setup()

    from django.db import DatabaseError, transaction
    from django.utils import timezone

    from leads.inbound import record_and_process_event
    from leads.models import (
        ConsentRecord,
        LeadSubmission,
        WhatsAppConversation,
        WhatsAppDeliveryEvent,
        WhatsAppMedia,
        WhatsAppMessage,
        WhatsAppTemplate,
    )
    from leads.whatsapp_cloud import (
        MockWhatsAppCloudClient,
        WhatsAppCloudError,
        WhatsAppIdempotencyConflict,
        WhatsAppPolicyError,
        dispatch_whatsapp_message,
        queue_whatsapp_message,
    )
    from marketing.models import MarketingIntegration

    integration = MarketingIntegration.objects.select_related('provider').get(
        provider__code='whatsapp',
        integration_type='cloud_api',
    )
    now = timezone.now()
    suffix = uuid.uuid4().hex[:12]
    wa_id = f'447700{int(suffix[:8], 16) % 100000000:08d}'
    inbound_id = f'wamid.contract.inbound.{suffix}'
    media_message_id = f'wamid.contract.media.{suffix}'
    media_id = f'media-contract-{suffix}'

    with transaction.atomic():
        integration.enabled = True
        integration.config_json = {
            **(integration.config_json or {}),
            'delivery_mode': 'mock',
            'max_messages_per_minute': 60,
        }
        integration.save(update_fields=['enabled', 'config_json', 'updated_at'])
        inbound_payload = {
            'event_type': 'message',
            'waba_id': str((integration.config_json or {}).get('waba_id') or ''),
            'phone_number_id': integration.public_id,
            'message_id': inbound_id,
            'wa_id': wa_id,
            'contact': {'wa_id': wa_id, 'profile': {'name': 'Contract Test Customer'}},
            'message': {
                'id': inbound_id,
                'from': wa_id,
                'timestamp': str(int(now.timestamp())),
                'type': 'text',
                'text': {'body': 'Need a 24,000 bottles/hour filling line.'},
            },
            'value': {},
        }
        receipt, created = record_and_process_event(
            integration=integration,
            provider_code='whatsapp',
            event_type='message',
            external_event_id=inbound_id,
            payload=inbound_payload,
        )
        assert created and receipt.status == 'processed' and receipt.submission_id
        submission = LeadSubmission.objects.get(pk=receipt.submission_id)
        conversation = WhatsAppConversation.objects.get(
            integration=integration,
            external_contact_id=wa_id,
        )
        inbound_message = WhatsAppMessage.objects.get(external_message_id=inbound_id)
        assert inbound_message.conversation_id == conversation.id
        assert conversation.unread_count == 1
        assert conversation.service_window_expires_at == now.replace(microsecond=0) + timedelta(hours=24)
        assert 'whatsapp_messages' not in (submission.payload_json or {})

        duplicate_receipt, duplicate_created = record_and_process_event(
            integration=integration,
            provider_code='whatsapp',
            event_type='message',
            external_event_id=inbound_id,
            payload=inbound_payload,
        )
        conversation.refresh_from_db()
        assert duplicate_receipt.id == receipt.id and not duplicate_created
        assert conversation.unread_count == 1
        assert WhatsAppMessage.objects.filter(external_message_id=inbound_id).count() == 1

        media_payload = {
            **inbound_payload,
            'message_id': media_message_id,
            'message': {
                'id': media_message_id,
                'from': wa_id,
                'timestamp': str(int((now + timedelta(seconds=1)).timestamp())),
                'type': 'document',
                'document': {
                    'id': media_id,
                    'mime_type': 'application/pdf',
                    'filename': 'bottle-drawing.pdf',
                    'caption': 'Bottle drawing',
                },
            },
        }
        media_receipt, media_created = record_and_process_event(
            integration=integration,
            provider_code='whatsapp',
            event_type='message',
            external_event_id=media_message_id,
            payload=media_payload,
        )
        assert media_created and media_receipt.submission_id == submission.id
        assert WhatsAppMedia.objects.get(external_media_id=media_id).status == 'pending'
        conversation.refresh_from_db()
        assert conversation.unread_count == 2

        queued, queued_created = queue_whatsapp_message(
            conversation=conversation,
            actor=conversation.owner_user,
            idempotency_token=f'contract-text-{suffix}',
            body='We can prepare a technical proposal.',
            now=now + timedelta(seconds=2),
        )
        assert queued_created and queued.status == 'queued'
        duplicate_queue, duplicate_queue_created = queue_whatsapp_message(
            conversation=conversation,
            actor=conversation.owner_user,
            idempotency_token=f'contract-text-{suffix}',
            body='We can prepare a technical proposal.',
            now=now + timedelta(seconds=2),
        )
        assert duplicate_queue.id == queued.id and not duplicate_queue_created
        try:
            queue_whatsapp_message(
                conversation=conversation,
                actor=conversation.owner_user,
                idempotency_token=f'contract-text-{suffix}',
                body='Different body with a reused token.',
                now=now + timedelta(seconds=2),
            )
        except WhatsAppIdempotencyConflict:
            pass
        else:
            raise AssertionError('Idempotency token reuse did not fail closed.')
        sent_by_mock, did_send = dispatch_whatsapp_message(
            message_id=queued.id,
            client=MockWhatsAppCloudClient(),
            now=now + timedelta(seconds=3),
        )
        assert did_send and sent_by_mock.status == 'sent'
        assert sent_by_mock.external_message_id.startswith('wamid.mock.')

        conversation.refresh_from_db()
        conversation.service_window_expires_at = now - timedelta(seconds=1)
        conversation.save(update_fields=['service_window_expires_at', 'updated_at'])
        try:
            queue_whatsapp_message(
                conversation=conversation,
                actor=conversation.owner_user,
                idempotency_token=f'closed-window-{suffix}',
                body='This must be blocked outside the service window.',
                now=now,
            )
        except WhatsAppPolicyError:
            pass
        else:
            raise AssertionError('Free-form send escaped the 24-hour policy.')

        utility_template = WhatsAppTemplate.objects.create(
            integration=integration,
            name=f'contract_follow_up_{suffix}',
            language='en_US',
            category='utility',
            status='approved',
            components_json=[],
        )
        template_message, template_created = queue_whatsapp_message(
            conversation=conversation,
            actor=conversation.owner_user,
            idempotency_token=f'utility-template-{suffix}',
            template=utility_template,
            template_parameters=[],
            now=now,
        )
        assert template_created and template_message.message_type == 'template'

        marketing_template = WhatsAppTemplate.objects.create(
            integration=integration,
            name=f'contract_marketing_{suffix}',
            language='en_US',
            category='marketing',
            status='approved',
            components_json=[],
        )
        try:
            queue_whatsapp_message(
                conversation=conversation,
                actor=conversation.owner_user,
                idempotency_token=f'marketing-template-{suffix}',
                template=marketing_template,
                now=now,
            )
        except WhatsAppPolicyError:
            pass
        else:
            raise AssertionError('Marketing send escaped the consent policy.')

        class RetryableFailureClient:
            def send_message(self, **_kwargs):
                raise WhatsAppCloudError(
                    'Synthetic rate limit.',
                    code='130429',
                    http_status=429,
                    retryable=True,
                )

        retry_message, _ = queue_whatsapp_message(
            conversation=conversation,
            actor=conversation.owner_user,
            idempotency_token=f'retry-template-{suffix}',
            template=utility_template,
            now=now,
        )
        failed_retry, retry_sent = dispatch_whatsapp_message(
            message_id=retry_message.id,
            client=RetryableFailureClient(),
            now=now,
        )
        assert not retry_sent and failed_retry.status == 'failed'
        assert failed_retry.retryable and failed_retry.next_attempt_at is not None

        class AmbiguousFailureClient:
            def send_message(self, **_kwargs):
                raise WhatsAppCloudError(
                    'Synthetic ambiguous timeout.',
                    code='ambiguous_transport',
                    ambiguous=True,
                )

        ambiguous_message, _ = queue_whatsapp_message(
            conversation=conversation,
            actor=conversation.owner_user,
            idempotency_token=f'ambiguous-template-{suffix}',
            template=utility_template,
            now=now,
        )
        failed_ambiguous, ambiguous_sent = dispatch_whatsapp_message(
            message_id=ambiguous_message.id,
            client=AmbiguousFailureClient(),
            now=now,
        )
        assert not ambiguous_sent and failed_ambiguous.status == 'failed'
        assert not failed_ambiguous.retryable

        outbound = WhatsAppMessage.objects.create(
            conversation=conversation,
            direction='outbound',
            message_type='text',
            idempotency_key=hashlib.sha256(f'contract:{suffix}'.encode()).hexdigest(),
            request_fingerprint=hashlib.sha256(f'payload:{suffix}'.encode()).hexdigest(),
            sender_id=integration.public_id,
            recipient_id=wa_id,
            body='We can prepare a technical proposal.',
            status='sent',
            attempts=1,
            sent_at=now,
        )
        outbound.external_message_id = f'wamid.contract.outbound.{suffix}'
        outbound.save(update_fields=['external_message_id', 'updated_at'])

        read_status = {
            'id': outbound.external_message_id,
            'status': 'read',
            'timestamp': str(int((now + timedelta(minutes=2)).timestamp())),
            'recipient_id': wa_id,
        }
        read_event = {
            'event_type': 'status',
            'waba_id': str((integration.config_json or {}).get('waba_id') or ''),
            'phone_number_id': integration.public_id,
            'message_id': outbound.external_message_id,
            'delivery_status': 'read',
            'event_fingerprint': fingerprint(read_status),
            'status': read_status,
            'value': {},
        }
        read_receipt, read_created = record_and_process_event(
            integration=integration,
            provider_code='whatsapp',
            event_type='status',
            external_event_id=f"{outbound.external_message_id}:{read_event['event_fingerprint']}",
            payload=read_event,
        )
        assert read_created and read_receipt.status == 'processed', (
            read_receipt.status,
            read_receipt.last_error,
        )

        old_sent_status = {
            'id': outbound.external_message_id,
            'status': 'sent',
            'timestamp': str(int((now + timedelta(minutes=1)).timestamp())),
            'recipient_id': wa_id,
        }
        old_sent_event = {
            **read_event,
            'delivery_status': 'sent',
            'event_fingerprint': fingerprint(old_sent_status),
            'status': old_sent_status,
        }
        record_and_process_event(
            integration=integration,
            provider_code='whatsapp',
            event_type='status',
            external_event_id=f"{outbound.external_message_id}:{old_sent_event['event_fingerprint']}",
            payload=old_sent_event,
        )
        outbound.refresh_from_db()
        assert outbound.status == 'read'
        assert WhatsAppDeliveryEvent.objects.filter(message=outbound).count() == 2

        try:
            with transaction.atomic():
                WhatsAppDeliveryEvent.objects.filter(message=outbound).delete()
        except DatabaseError:
            pass
        else:
            raise AssertionError('Direct delivery evidence deletion was not rejected.')

        opt_out_id = f'wamid.contract.optout.{suffix}'
        opt_out_payload = {
            **inbound_payload,
            'message_id': opt_out_id,
            'message': {
                'id': opt_out_id,
                'from': wa_id,
                'timestamp': str(int((now + timedelta(minutes=3)).timestamp())),
                'type': 'text',
                'text': {'body': 'STOP'},
            },
        }
        opt_out_receipt, opt_out_created = record_and_process_event(
            integration=integration,
            provider_code='whatsapp',
            event_type='message',
            external_event_id=opt_out_id,
            payload=opt_out_payload,
        )
        assert opt_out_created and opt_out_receipt.status == 'processed'
        conversation.refresh_from_db()
        assert conversation.status == 'blocked' and conversation.opted_out_at is not None
        assert ConsentRecord.objects.filter(
            submission=submission,
            purpose='marketing',
            decision='withdrawn',
            source='whatsapp_opt_out',
        ).exists()
        try:
            queue_whatsapp_message(
                conversation=conversation,
                actor=conversation.owner_user,
                idempotency_token=f'after-opt-out-{suffix}',
                template=utility_template,
                now=now + timedelta(minutes=4),
            )
        except WhatsAppPolicyError:
            pass
        else:
            raise AssertionError('Customer opt-out did not block later sends.')

        # Parent retention deletion must still cascade through append-only
        # evidence. Django relies on the database cascade for delivery events.
        conversation.delete()
        assert not WhatsAppDeliveryEvent.objects.filter(message_id=outbound.id).exists()

        transaction.set_rollback(True)

    print(
        'WhatsApp PostgreSQL contract passed: normalized inbound text/media, '
        'duplicate suppression, 24-hour window, out-of-order delivery evidence, '
        'append-only audit, retention cascade, idempotent mock sending, template '
        'and consent policy, retry classification, and ambiguous-timeout safety.'
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
