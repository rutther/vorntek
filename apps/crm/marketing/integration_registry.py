"""Shared creation contract for independent maintenance and guided preparation."""
import re

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import MarketingIntegration, MarketingProvider


@transaction.atomic
def ensure_meta_pixel_integration(*, site, provider, public_id, environment, name):
    """Reuse the asset identity, never replace credentials, routing, or activation.

    The provider row lock matches the legacy global provider/type/public_id
    unique index and serializes independent and guided creation on PostgreSQL.
    """
    if provider.code != 'meta':
        raise ValidationError('此新建入口仅支持 Meta 网站接入。')
    if not isinstance(public_id, str) or not re.fullmatch(r'[0-9]{5,30}', public_id):
        raise ValidationError('请填写 Meta 提供的 5—30 位数据集数字 ID。')
    if environment not in {'production', 'test'}:
        raise ValidationError('请选择正式业务或演练测试环境。')
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 200:
        raise ValidationError('请填写不超过 200 字的接入名称。')
    MarketingProvider.objects.select_for_update().get(pk=provider.pk)
    item = MarketingIntegration.objects.filter(provider=provider,
        integration_type='pixel', public_id=public_id).first()
    if item:
        if item.site_id != site.pk or item.environment != environment:
            raise ValidationError('该数据集已属于其他站点或环境，不能直接复用。')
        return item, False
    item = MarketingIntegration(site=site, provider=provider, integration_type='pixel',
        public_id=public_id, name=name.strip(), environment=environment,
        routing_mode='flow_scoped', enabled=False, consent_category='marketing')
    item.full_clean(exclude=['config_json'])
    item.save()
    return item, True
