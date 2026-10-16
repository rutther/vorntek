"""Read only site-owned, registered, integrity-checked article image uploads."""
import hashlib
from pathlib import Path, PurePosixPath

from .article_delivery import ArticleDeliveryError

IMAGE_TYPES = {'.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg',
               '.webp':'image/webp','.gif':'image/gif','.avif':'image/avif'}


def read_uploaded_article_image(*, site, artifact):
    if (site is None or not isinstance(artifact,str) or not artifact.startswith('assets/')
            or any(char in artifact for char in '\\%?#') or str(PurePosixPath(artifact)) != artifact
            or any(part in ('','.','..') for part in PurePosixPath(artifact).parts)
            or Path(artifact).suffix.lower() not in IMAGE_TYPES):
        return None
    from sitecore.models import MediaAsset
    from .forms import resolve_storage_path
    from .storage_security import StorageSecurityError
    row = MediaAsset.objects.filter(site=site,public_path='/'+artifact,asset_type='image',status='active').first()
    if row is None:
        return None
    try:
        path = resolve_storage_path(row.storage_path)
        if path.stat().st_size > 25*1024*1024:
            raise ArticleDeliveryError('preview_asset_too_large')
        with path.open('rb') as stream:
            raw = stream.read(25*1024*1024+1)
        if len(raw) > 25*1024*1024:
            raise ArticleDeliveryError('preview_asset_too_large')
    except (OSError,StorageSecurityError) as error:
        raise ArticleDeliveryError('preview_upload_unavailable') from error
    if len(raw) != row.file_size_bytes or hashlib.sha256(raw).hexdigest() != row.sha256:
        raise ArticleDeliveryError('preview_upload_changed')
    return raw, IMAGE_TYPES[Path(artifact).suffix.lower()]
