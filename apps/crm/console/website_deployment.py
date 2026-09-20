"""Database-gated, crash-resumable deployment of a selected website candidate."""
from __future__ import annotations

from pathlib import Path
import re
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from sitecore.models import Site

from .article_delivery import ArticleDeliveryError
from .models import (
    WebsiteDeploymentOperation,
    WebsiteReleaseDeployment,
    WebsiteReleaseSelection,
)
from .website_candidate import website_release_store
from .website_selection import review_website_candidate
from .website_serving_store import WebsiteServingStore


_VERSION = re.compile(r'^[a-f0-9]{64}$')
_ERROR = re.compile(r'^[a-z][a-z0-9_]{0,99}$')


def _text(value, *, code: str, minimum: int, maximum: int) -> str:
    if not isinstance(value, str):
        raise ArticleDeliveryError(code)
    normalized = value.strip()
    if not minimum <= len(normalized) <= maximum:
        raise ArticleDeliveryError(code)
    return normalized


def _token(value) -> UUID:
    try:
        token = value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise ArticleDeliveryError('website_deployment_token_invalid') from None
    if token.version != 4:
        raise ArticleDeliveryError('website_deployment_token_invalid')
    return token


def _version(value, *, allow_empty: bool, code: str) -> str:
    if allow_empty and value == '':
        return ''
    if not isinstance(value, str) or not _VERSION.fullmatch(value):
        raise ArticleDeliveryError(code)
    return value


def _selection_id(value) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ArticleDeliveryError('website_deployment_selection_invalid')
    return value


def website_serving_store() -> WebsiteServingStore:
    root = Path(settings.SITEOS_WEBSITE_SERVING_ROOT)
    if not root.is_absolute() or root == Path(root.anchor):
        raise ArticleDeliveryError('website_serving_target_not_configured')
    return WebsiteServingStore(root)


def current_website_deployment(*, site) -> WebsiteReleaseDeployment | None:
    return (
        WebsiteReleaseDeployment.objects.select_related('selection', 'release')
        .filter(site=site)
        .order_by('-id')
        .first()
    )


def current_prepared_website_deployment(*, site) -> WebsiteDeploymentOperation | None:
    return (
        WebsiteDeploymentOperation.objects.select_related('selection', 'release')
        .filter(site=site, status='prepared')
        .order_by('-id')
        .first()
    )


def _matches(
    operation: WebsiteDeploymentOperation,
    *,
    site,
    selection_id: int,
    expected_deployed_version: str,
    token: UUID,
    actor: str,
    reason: str,
) -> bool:
    return (
        operation.request_token == token
        and operation.site_id == site.pk
        and operation.selection_id == selection_id
        and operation.previous_version == expected_deployed_version
        and operation.deployed_by == actor
        and operation.reason == reason
    )


def _result(receipt: WebsiteReleaseDeployment, *, replayed: bool) -> dict:
    return {
        'deploymentId': receipt.pk,
        'operationId': receipt.operation_id,
        'selectionId': receipt.selection_id,
        'version': receipt.version,
        'previous': receipt.previous_version,
        'changed': receipt.version != receipt.previous_version,
        'replayed': replayed,
    }


def _mark_failed(operation_id: int, code: str) -> None:
    stable = code if isinstance(code, str) and _ERROR.fullmatch(code) else 'website_deployment_failed'
    with transaction.atomic():
        operation = WebsiteDeploymentOperation.objects.select_for_update().get(pk=operation_id)
        if operation.status == 'prepared':
            WebsiteDeploymentOperation.objects.filter(
                pk=operation.pk,
                status='prepared',
            ).update(status='failed', error_code=stable, completed_at=timezone.now())


def _finalize(operation_id: int, *, serving: WebsiteServingStore, source_manifest: dict) -> dict:
    with transaction.atomic():
        operation = (
            WebsiteDeploymentOperation.objects.select_for_update()
            .select_related('selection', 'release')
            .get(pk=operation_id)
        )
        if operation.status == 'activated':
            return _result(
                WebsiteReleaseDeployment.objects.get(operation=operation),
                replayed=True,
            )
        if operation.status != 'prepared':
            raise ArticleDeliveryError('website_deployment_request_failed')
        Site.objects.select_for_update().get(pk=operation.site_id)
        current_selection = (
            WebsiteReleaseSelection.objects.filter(site_id=operation.site_id)
            .order_by('-id')
            .first()
        )
        if current_selection is None or current_selection.pk != operation.selection_id:
            raise ArticleDeliveryError('website_deployment_selection_changed')
        if serving.current() != operation.version:
            raise ArticleDeliveryError('website_deployment_recovery_required')
        serving.verify(operation.version, expected_manifest=source_manifest)
        receipt = WebsiteReleaseDeployment.objects.create(
            operation=operation,
            request_token=operation.request_token,
            site_id=operation.site_id,
            selection_id=operation.selection_id,
            release_id=operation.release_id,
            version=operation.version,
            previous_version=operation.previous_version,
            deployed_by=operation.deployed_by,
            reason=operation.reason,
        )
        WebsiteDeploymentOperation.objects.filter(
            pk=operation.pk,
            status='prepared',
        ).update(status='activated', error_code='', completed_at=timezone.now())
        return _result(receipt, replayed=False)


def deploy_selected_website(
    *,
    site,
    expected_selection_id: int,
    expected_deployed_version: str,
    request_token,
    actor: str,
    reason: str,
) -> dict:
    """Deploy only the current selection with a resumable two-phase receipt.

    A committed ``prepared`` operation blocks candidate selection and every
    different deployment request. If the process dies after the filesystem
    switch but before the receipt transaction, replaying the same UUID verifies
    the already-switched bytes and completes the immutable receipt.
    """

    selection_id = _selection_id(expected_selection_id)
    expected = _version(
        expected_deployed_version,
        allow_empty=True,
        code='website_deployment_expected_invalid',
    )
    token = _token(request_token)
    normalized_actor = _text(
        actor,
        code='website_deployment_actor_invalid',
        minimum=1,
        maximum=150,
    )
    normalized_reason = _text(
        reason,
        code='website_deployment_reason_invalid',
        minimum=8,
        maximum=500,
    )

    with transaction.atomic():
        Site.objects.select_for_update().get(pk=site.pk)
        replay = (
            WebsiteDeploymentOperation.objects.select_related('selection', 'release')
            .filter(request_token=token)
            .first()
        )
        if replay is not None:
            if not _matches(
                replay,
                site=site,
                selection_id=selection_id,
                expected_deployed_version=expected,
                token=token,
                actor=normalized_actor,
                reason=normalized_reason,
            ):
                raise ArticleDeliveryError('website_deployment_request_reused')
            if replay.status == 'activated':
                return _result(
                    WebsiteReleaseDeployment.objects.get(operation=replay),
                    replayed=True,
                )
            if replay.status == 'failed':
                raise ArticleDeliveryError('website_deployment_request_failed')
            operation = replay
        else:
            if WebsiteDeploymentOperation.objects.filter(
                site=site,
                status='prepared',
            ).exists():
                raise ArticleDeliveryError('website_deployment_in_progress')
            selection = (
                WebsiteReleaseSelection.objects.select_related('release')
                .filter(site=site)
                .order_by('-id')
                .first()
            )
            if selection is None or selection.pk != selection_id:
                raise ArticleDeliveryError('website_deployment_selection_changed')
            current = current_website_deployment(site=site)
            current_version = current.version if current is not None else ''
            if current_version != expected:
                raise ArticleDeliveryError('website_deployment_changed')
            if current_version == selection.version:
                raise ArticleDeliveryError('website_candidate_already_deployed')
            record, manifest = review_website_candidate(
                site=site,
                record_id=selection.release_id,
            )
            if record.pk != selection.release_id or manifest['version'] != selection.version:
                raise ArticleDeliveryError('website_deployment_selection_mismatch')
            operation = WebsiteDeploymentOperation.objects.create(
                request_token=token,
                site=site,
                selection=selection,
                release=record,
                version=selection.version,
                previous_version=current_version,
                deployed_by=normalized_actor,
                reason=normalized_reason,
            )

    serving = None
    try:
        source = website_release_store(site, initialize=False)
        source_manifest = source.verify(operation.version)
        serving = website_serving_store()
        observed = serving.current()
        if observed == operation.previous_version:
            serving.deploy(source, operation.version, expected=operation.previous_version)
        elif observed == operation.version:
            serving.verify(operation.version, expected_manifest=source_manifest)
        else:
            raise ArticleDeliveryError('website_deployment_recovery_required')
        return _finalize(
            operation.pk,
            serving=serving,
            source_manifest=source_manifest,
        )
    except Exception as error:
        if serving is None:
            code = error.code if isinstance(error, ArticleDeliveryError) else 'website_deployment_failed'
            _mark_failed(operation.pk, code)
            if isinstance(error, ArticleDeliveryError):
                raise
            raise ArticleDeliveryError('website_deployment_failed') from error
        try:
            current = serving.current()
        except Exception:
            current = None
        if current == operation.version:
            # The pointer may already have switched. Keep ``prepared`` so the
            # same request token can safely resume and issue the receipt.
            raise ArticleDeliveryError('website_deployment_recovery_required') from error
        if current == operation.previous_version:
            code = error.code if isinstance(error, ArticleDeliveryError) else 'website_deployment_failed'
            _mark_failed(operation.pk, code)
            if isinstance(error, ArticleDeliveryError):
                raise
            raise ArticleDeliveryError('website_deployment_failed') from error
        raise ArticleDeliveryError('website_deployment_recovery_required') from error
