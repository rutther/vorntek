from __future__ import annotations

import hmac

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from siteos_admin.runtime_config import secret_value


class SecretVaultConfigurationError(ValueError):
    """The vault needs its own key; plaintext and file-backed inputs are supported."""


def secret_vault_master_key() -> str:
    try:
        key = secret_value('SITEOS_SECRET_VAULT_KEY').strip()
    except ImproperlyConfigured as exc:
        raise SecretVaultConfigurationError('无法读取独立密钥库主密钥，请核对 SITEOS_SECRET_VAULT_KEY 配置。') from exc
    if not key:
        raise SecretVaultConfigurationError('未配置独立密钥库主密钥。请设置 SITEOS_SECRET_VAULT_KEY。')
    signing = str(settings.SECRET_KEY).strip() if settings.configured else secret_value('SITEOS_ADMIN_SECRET_KEY').strip()
    if hmac.compare_digest(key.encode('utf-8'), signing.encode('utf-8')):
        raise SecretVaultConfigurationError('SITEOS_SECRET_VAULT_KEY 必须独立于网站签名密钥，不能复用。')
    return key


def store_secret(*, secret_key: str, plaintext: str, updated_by: str) -> None:
    if not secret_key.strip():
        raise ValueError('密钥引用不能为空。')
    if not plaintext.strip():
        raise ValueError('密钥内容不能为空。')
    master_key = secret_vault_master_key()
    last4 = plaintext.strip()[-4:]
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO admin_secret_store (secret_key, secret_cipher, last4, updated_by, created_at, updated_at)
            VALUES (%s, pgp_sym_encrypt(%s, %s, 'cipher-algo=aes256,compress-algo=1'), %s, %s, now(), now())
            ON CONFLICT (secret_key)
            DO UPDATE SET
              secret_cipher = EXCLUDED.secret_cipher,
              last4 = EXCLUDED.last4,
              updated_by = EXCLUDED.updated_by,
              updated_at = now()
            """,
            [secret_key, plaintext.strip(), master_key, last4, updated_by.strip() or 'system'],
        )


def load_secret(secret_key: str) -> str:
    if not secret_key.strip():
        return ''
    master_key = secret_vault_master_key()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pgp_sym_decrypt(secret_cipher, %s) FROM admin_secret_store WHERE secret_key = %s",
            [master_key, secret_key.strip()],
        )
        row = cursor.fetchone()
    return str(row[0] or '').strip() if row else ''


def delete_secret(secret_key: str) -> None:
    if not secret_key.strip():
        return
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM admin_secret_store WHERE secret_key = %s", [secret_key.strip()])


def secret_exists(secret_key: str) -> bool:
    if not secret_key.strip():
        return False
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM admin_secret_store WHERE secret_key = %s", [secret_key.strip()])
        return cursor.fetchone() is not None


def secret_last4(secret_key: str) -> str:
    if not secret_key.strip():
        return ''
    with connection.cursor() as cursor:
        cursor.execute("SELECT last4 FROM admin_secret_store WHERE secret_key = %s", [secret_key.strip()])
        row = cursor.fetchone()
    return str(row[0] or '') if row else ''
