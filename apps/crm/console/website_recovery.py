"""Reconstruct the derived website serving cache from durable release evidence."""
from __future__ import annotations

import re

from django.db import transaction

from sitecore.models import Site

from .article_delivery import ArticleDeliveryError
from .models import WebsiteDeploymentOperation, WebsiteReleaseDeployment
from .website_candidate import website_release_store
from .website_deployment import website_serving_store
from .website_selection import review_website_candidate


_VERSION = re.compile(r'^[a-f0-9]{64}$')


def _deployment_id(value, *, required: bool) -> int | None:
    if value is None and not required:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ArticleDeliveryError('website_reconcile_deployment_invalid')
    return value


def _serving_version(value, *, required: bool) -> str | None:
    if value is None and not required:
        return None
    if value == '':
        return ''
    if not isinstance(value, str) or not _VERSION.fullmatch(value):
        raise ArticleDeliveryError('website_reconcile_serving_version_invalid')
    return value


def _receipt_matches_operation(receipt: WebsiteReleaseDeployment) -> bool:
    operation = receipt.operation
    return (
        operation.status == 'activated'
        and operation.request_token == receipt.request_token
        and operation.site_id == receipt.site_id
        and operation.selection_id == receipt.selection_id
        and operation.release_id == receipt.release_id
        and operation.version == receipt.version
        and operation.previous_version == receipt.previous_version
        and operation.deployed_by == receipt.deployed_by
        and operation.reason == receipt.reason
    )


def reconcile_website_serving_cache(
    *,
    site,
    apply: bool,
    expected_deployment_id: int | None = None,
    expected_serving_version: str | None = None,
) -> dict:
    """Verify or rebuild only the cache represented by the latest receipt.

    No database row is inserted or changed. During apply, the site row stays
    locked across candidate verification, copy and atomic pointer replacement so
    normal selection/deployment services cannot race this maintenance operation.
    """

    expected_id = _deployment_id(expected_deployment_id, required=apply)
    expected_serving = _serving_version(expected_serving_version, required=apply)

    with transaction.atomic():
        if apply:
            locked_site = Site.objects.select_for_update().get(pk=site.pk)
        else:
            locked_site = Site.objects.get(pk=site.pk)
        if WebsiteDeploymentOperation.objects.filter(
            site=locked_site,
            status='prepared',
        ).exists():
            raise ArticleDeliveryError('website_reconcile_deployment_pending')

        receipt = (
            WebsiteReleaseDeployment.objects.select_related(
                'operation',
                'selection',
                'release',
            )
            .filter(site=locked_site)
            .order_by('-id')
            .first()
        )
        if receipt is None:
            raise ArticleDeliveryError('website_reconcile_receipt_missing')
        if expected_id is not None and receipt.pk != expected_id:
            raise ArticleDeliveryError('website_reconcile_deployment_changed')
        if (
            not _receipt_matches_operation(receipt)
            or receipt.selection.site_id != locked_site.pk
            or receipt.selection.release_id != receipt.release_id
            or receipt.selection.version != receipt.version
        ):
            raise ArticleDeliveryError('website_reconcile_ledger_mismatch')

        record, candidate_record = review_website_candidate(
            site=locked_site,
            record_id=receipt.release_id,
        )
        if record.pk != receipt.release_id or candidate_record['version'] != receipt.version:
            raise ArticleDeliveryError('website_reconcile_ledger_mismatch')
        source = website_release_store(locked_site, initialize=False)
        source_manifest = source.verify(receipt.version)
        serving = website_serving_store()
        current = serving.current()

        if expected_serving is not None and current != expected_serving:
            raise ArticleDeliveryError('website_reconcile_serving_changed')
        if current == receipt.version:
            serving.verify(receipt.version, expected_manifest=source_manifest)
            changed = False
            action = 'verified'
        elif apply:
            serving.deploy(source, receipt.version, expected=current)
            serving.verify(receipt.version, expected_manifest=source_manifest)
            changed = True
            action = 'rebuilt'
        else:
            changed = False
            action = 'rebuild_required'

        return {
            'result': 'reconciled' if apply else 'plan_only',
            'siteCode': locked_site.code,
            'deploymentId': receipt.pk,
            'selectionId': receipt.selection_id,
            'targetVersion': receipt.version,
            'observedServingVersion': current,
            'changed': changed,
            'action': action,
            'databaseRowsChanged': False,
        }
