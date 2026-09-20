from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlparse
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.utils import timezone

from console.article_build_input import read_article_build_input
from console.article_delivery import (
    ArticleDeliveryError,
    ArticleLocale,
    ArticleVersion,
    build_article_release,
)
from console.models import (
    WebsiteDeploymentOperation,
    WebsiteReleaseDeployment,
    WebsiteReleaseSelection,
)
from console.website_deployment import deploy_selected_website
from console.website_candidate import article_live_store, website_release_store
from console.website_recovery import reconcile_website_serving_cache
from console.website_selection import select_website_candidate
from console.website_serving_store import WebsiteServingStore
from sitecore.models import Release, Site


class Command(BaseCommand):
    help = (
        'Destructive synthetic-only acceptance for website selection/deployment; '
        'refuses non-empty or non-test installations.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--synthetic-only', action='store_true')
        parser.add_argument('--expect-database', required=True)
        parser.add_argument('--require-bundled-baseline', action='store_true')

    def _refused_database_write(
        self,
        callback,
        message: str,
        *,
        diagnostic: str,
    ) -> None:
        try:
            with transaction.atomic():
                callback()
        except DatabaseError as error:
            if diagnostic not in str(error):
                raise CommandError(
                    'PostgreSQL refused the synthetic write for an unexpected reason.'
                ) from error
            return
        raise CommandError(message)

    def handle(self, *args, **options):
        if (
            not options['synthetic_only']
            or os.getenv('NEWCROWN_SYNTHETIC_ACCEPTANCE') != '1'
        ):
            raise CommandError(
                'Synthetic acceptance requires --synthetic-only and '
                'NEWCROWN_SYNTHETIC_ACCEPTANCE=1.'
            )
        if (
            settings.NEWCROWN_ALLOW_EXTERNAL_IO
            or settings.SITEOS_WHATSAPP_ALLOW_LIVE_SEND
            or settings.NEWCROWN_RUN_SCHEDULED_TASKS
        ):
            raise CommandError(
                'External I/O, WhatsApp live sending, and scheduled tasks must '
                'remain disabled for synthetic acceptance.'
            )
        if connection.vendor != 'postgresql':
            raise CommandError('Synthetic deployment acceptance requires PostgreSQL.')
        actual_database = str(connection.settings_dict.get('NAME') or '')
        if actual_database != options['expect_database']:
            raise CommandError('Connected database does not match --expect-database.')
        site_url = urlparse(os.getenv('NEWCROWN_SITE_URL', ''))
        if (
            site_url.scheme != 'http'
            or site_url.hostname not in {'localhost', '127.0.0.1', '::1'}
            or site_url.username
            or site_url.password
        ):
            raise CommandError('Synthetic acceptance requires a loopback HTTP site URL.')
        if any(
            model.objects.exists()
            for model in (
                Release,
                WebsiteReleaseDeployment,
                WebsiteDeploymentOperation,
                WebsiteReleaseSelection,
            )
        ):
            raise CommandError(
                'Deployment acceptance requires an empty release and website ledger.'
            )

        site = Site.objects.get(code='siteos_demo', enabled=True)
        if urlparse(site.base_url).hostname not in {'localhost', '127.0.0.1', '::1'}:
            raise CommandError('Initialized site is not an isolated loopback installation.')

        serving_root = Path(settings.SITEOS_WEBSITE_SERVING_ROOT)
        source_root = Path(settings.SITEOS_WEBSITE_SOURCE_ROOT)
        for root in (serving_root, source_root):
            if not root.is_absolute() or root == Path(root.anchor):
                raise CommandError('Acceptance storage roots must be explicit absolute paths.')
        if not source_root.is_dir():
            raise CommandError('Website source root is unavailable.')

        article_store = article_live_store(site, initialize=True)
        candidate_store = website_release_store(site, initialize=True)
        if serving_root.exists() and any(serving_root.iterdir()):
            serving_store = WebsiteServingStore(serving_root)
        else:
            serving_store = WebsiteServingStore.initialize(serving_root)
        if options['require_bundled_baseline']:
            pointer = serving_root / 'current'
            if not pointer.is_symlink() or os.readlink(pointer) != 'releases/bundled':
                raise CommandError('Compose serving volume did not copy the bundled baseline.')
        if serving_store.current() != '':
            raise CommandError('Acceptance serving store is not at its initial baseline.')

        locales = (
            ArticleLocale('en', 'English', is_default=True),
            ArticleLocale('zh', '中文'),
        )

        def candidate(marker: str) -> tuple[Release, str]:
            rows = [
                ArticleVersion(
                    content_key='synthetic-acceptance-guide',
                    locale=locale,
                    slug='acceptance-guide',
                    title='Synthetic deployment acceptance',
                    markdown=f'Synthetic deployment marker: **{marker}**.',
                    cover_url='',
                    status='published',
                    route_status='published',
                )
                for locale in ('en', 'zh')
            ]
            article = build_article_release(
                rows,
                locales=locales,
                site_code=site.code,
                site_name='Vorntek synthetic acceptance',
                public_origin='https://example.invalid',
                brand_logo_url='/assets/vorntek/vorntekLogo.png',
                base_route_mode='shared',
            )
            article_version = article_store.stage(article)
            build_input = read_article_build_input(
                article_store,
                version=article_version,
                release_mode=True,
            )
            version = candidate_store.stage(
                base_root=source_root,
                article_input=build_input,
            )
            manifest = candidate_store.verify(version)
            record = Release.objects.create(
                site=site,
                release_key=f'synthetic-website-acceptance:{version}',
                status='built',
                created_by='synthetic-acceptance',
                notes='Synthetic-only website deployment acceptance candidate.',
                snapshot_manifest={
                    'kind': 'websiteCandidate',
                    'scope': 'wholeSite',
                    'version': version,
                    'articleVersion': manifest['articleVersion'],
                    'baseSourceVersion': manifest['baseSourceSha256'],
                    'fileCount': len(manifest['files']),
                },
            )
            return record, version

        candidates = [candidate(marker) for marker in ('one', 'two', 'three')]
        actor = 'synthetic-acceptance'

        def select(record: Release, expected: str, reason: str) -> dict:
            return select_website_candidate(
                site=site,
                record_id=record.pk,
                expected_version=expected,
                request_token=uuid4(),
                actor=actor,
                reason=reason,
            )

        def deploy(selection_id: int, expected: str, token, reason: str) -> dict:
            return deploy_selected_website(
                site=site,
                expected_selection_id=selection_id,
                expected_deployed_version=expected,
                request_token=token,
                actor=actor,
                reason=reason,
            )

        first_record, first_version = candidates[0]
        first_selection = select(
            first_record,
            '',
            'Synthetic acceptance selects the first verified candidate.',
        )
        deploy(
            first_selection['selectionId'],
            '',
            uuid4(),
            'Synthetic acceptance deploys the first verified candidate.',
        )

        second_record, second_version = candidates[1]
        second_selection = select(
            second_record,
            first_version,
            'Synthetic acceptance selects the updated verified candidate.',
        )
        deploy(
            second_selection['selectionId'],
            first_version,
            uuid4(),
            'Synthetic acceptance deploys the updated verified candidate.',
        )

        third_record, third_version = candidates[2]
        third_selection = select(
            third_record,
            second_version,
            'Synthetic acceptance selects a candidate for crash recovery.',
        )
        recovery_token = uuid4()
        recovery_reason = 'Synthetic acceptance verifies crash-after-switch recovery.'
        with patch(
            'console.website_deployment._finalize',
            side_effect=RuntimeError('synthetic crash after pointer switch'),
        ):
            try:
                deploy(
                    third_selection['selectionId'],
                    second_version,
                    recovery_token,
                    recovery_reason,
                )
            except ArticleDeliveryError as error:
                if error.code != 'website_deployment_recovery_required':
                    raise
            else:
                raise CommandError('Synthetic post-switch crash did not preserve recovery state.')
        prepared = WebsiteDeploymentOperation.objects.get(request_token=recovery_token)
        if prepared.status != 'prepared' or serving_store.current() != third_version:
            raise CommandError('Prepared crash-recovery state is inconsistent.')
        resumed = deploy(
            third_selection['selectionId'],
            second_version,
            recovery_token,
            recovery_reason,
        )
        if resumed['operationId'] != prepared.pk:
            raise CommandError('Recovery did not reuse the prepared operation.')

        rollback_selection = select(
            first_record,
            third_version,
            'Synthetic acceptance selects the original candidate for rollback.',
        )
        deploy(
            rollback_selection['selectionId'],
            third_version,
            uuid4(),
            'Synthetic acceptance rolls back to the first verified candidate.',
        )
        if serving_store.current() != first_version:
            raise CommandError('Rollback did not restore the first candidate.')
        deployed_html = candidate_store.read_version_file(
            first_version,
            'articles/acceptance-guide/index.html',
        )
        served_html = (
            serving_root
            / 'releases'
            / first_version
            / 'articles'
            / 'acceptance-guide'
            / 'index.html'
        ).read_bytes()
        if served_html != deployed_html or b'Synthetic deployment marker' not in served_html:
            raise CommandError('Rollback serving bytes do not match the verified candidate.')
        if not (serving_root / 'current').is_symlink() or os.readlink(
            serving_root / 'current'
        ) != f'releases/{first_version}':
            raise CommandError('Serving pointer is not the expected relative release link.')

        receipt = WebsiteReleaseDeployment.objects.order_by('-id').first()
        activated = WebsiteDeploymentOperation.objects.get(pk=receipt.operation_id)
        self._refused_database_write(
            lambda: WebsiteReleaseDeployment.objects.filter(pk=receipt.pk).update(
                reason='tampered receipt'
            ),
            'PostgreSQL allowed deployment receipt mutation.',
            diagnostic='website_release_deployment is append-only',
        )
        self._refused_database_write(
            lambda: WebsiteReleaseDeployment.objects.filter(pk=receipt.pk).delete(),
            'PostgreSQL allowed deployment receipt deletion.',
            diagnostic='website_release_deployment is append-only',
        )
        self._refused_database_write(
            lambda: WebsiteDeploymentOperation.objects.filter(pk=activated.pk).update(
                status='failed',
                error_code='illegal_transition',
                completed_at=timezone.now(),
            ),
            'PostgreSQL allowed an activated operation transition.',
            diagnostic='website_deployment_operation transition refused',
        )

        current_selection = WebsiteReleaseSelection.objects.order_by('-id').first()
        prepared_token = uuid4()
        prepared = WebsiteDeploymentOperation.objects.create(
            request_token=prepared_token,
            site=site,
            selection=current_selection,
            release=current_selection.release,
            version=current_selection.version,
            previous_version=first_version,
            deployed_by=actor,
            reason='Synthetic acceptance checks prepared-operation interlocks.',
        )

        def mismatched_receipt():
            with connection.cursor() as cursor:
                cursor.execute(
                    '''INSERT INTO website_release_deployment
                       (operation_id, request_token, site_id, selection_id, release_id,
                        version, previous_version, deployed_by, reason)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                    [
                        prepared.pk,
                        prepared.request_token,
                        site.pk,
                        prepared.selection_id,
                        prepared.release_id,
                        prepared.version,
                        prepared.previous_version,
                        'mismatched-actor',
                        prepared.reason,
                    ],
                )

        self._refused_database_write(
            mismatched_receipt,
            'PostgreSQL accepted a receipt that did not match its prepared operation.',
            diagnostic='website_release_deployment does not match prepared operation',
        )

        def second_prepared():
            WebsiteDeploymentOperation.objects.create(
                request_token=uuid4(),
                site=site,
                selection=current_selection,
                release=current_selection.release,
                version=current_selection.version,
                previous_version=first_version,
                deployed_by=actor,
                reason='Synthetic acceptance attempts a concurrent deployment.',
            )

        try:
            with transaction.atomic():
                second_prepared()
        except IntegrityError as error:
            cause = getattr(error, '__cause__', None)
            constraint = getattr(getattr(cause, 'diag', None), 'constraint_name', None)
            if constraint != 'uq_website_deploy_prepared_site':
                raise CommandError(
                    'PostgreSQL refused the concurrent deployment for an unexpected reason.'
                ) from error
        else:
            raise CommandError('PostgreSQL allowed two prepared deployments for one site.')
        try:
            select_website_candidate(
                site=site,
                record_id=second_record.pk,
                expected_version=first_version,
                request_token=uuid4(),
                actor=actor,
                reason='Synthetic acceptance attempts selection during deployment.',
            )
        except ArticleDeliveryError as error:
            if error.code != 'website_deployment_in_progress':
                raise
        else:
            raise CommandError('Candidate selection was not blocked by prepared deployment.')
        WebsiteDeploymentOperation.objects.filter(pk=prepared.pk).update(
            status='failed',
            error_code='synthetic_acceptance_complete',
            completed_at=timezone.now(),
        )

        recovery_root = serving_root / 'synthetic-recovery-cache'
        recovery_store = WebsiteServingStore.initialize(recovery_root)
        receipts_before_recovery = WebsiteReleaseDeployment.objects.count()
        with patch(
            'console.website_recovery.website_serving_store',
            return_value=recovery_store,
        ):
            recovery_plan = reconcile_website_serving_cache(
                site=site,
                apply=False,
            )
            recovery = reconcile_website_serving_cache(
                site=site,
                apply=True,
                expected_deployment_id=receipt.pk,
                expected_serving_version='',
            )
        if (
            recovery_plan['action'] != 'rebuild_required'
            or recovery['action'] != 'rebuilt'
            or recovery_store.current() != first_version
            or WebsiteReleaseDeployment.objects.count() != receipts_before_recovery
        ):
            raise CommandError('Derived serving-cache reconstruction failed.')

        result = {
            'result': 'passed',
            'scope': 'synthetic isolated deployment lifecycle',
            'siteCode': site.code,
            'selectionEvents': WebsiteReleaseSelection.objects.count(),
            'deploymentReceipts': WebsiteReleaseDeployment.objects.count(),
            'finalVersion': first_version,
            'finalRoute': '/articles/acceptance-guide/',
            'finalMarker': 'Synthetic deployment marker',
            'relativePointer': os.readlink(serving_root / 'current'),
            'recoveryCacheRebuilt': True,
            'externalIoEnabled': False,
        }
        self.stdout.write(json.dumps(result, sort_keys=True))
