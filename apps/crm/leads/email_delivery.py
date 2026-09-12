from __future__ import annotations

from dataclasses import dataclass
import json

from django.conf import settings
from django.core.mail import get_connection

from console.secret_store import load_secret, secret_exists, secret_last4


SMTP_CONFIG_SECRET_KEY = 'email:smtp_config'
SMTP_PASSWORD_SECRET_KEY = 'email:smtp_password'
SMTP_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'


@dataclass(frozen=True)
class EmailDeliveryConfig:
    backend: str
    enabled: bool
    host: str
    port: int
    username: str
    password: str
    from_email: str
    use_tls: bool
    use_ssl: bool
    timeout: int
    source: str
    error: str = ''

    @property
    def ready(self) -> bool:
        if not self.enabled or self.error:
            return False
        if self.backend != SMTP_BACKEND:
            return True
        return bool(
            self.host
            and self.port
            and self.username
            and self.password
            and self.from_email
            and not (self.use_tls and self.use_ssl)
        )


def load_stored_smtp_settings() -> dict[str, object]:
    raw = load_secret(SMTP_CONFIG_SECRET_KEY)
    if not raw:
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError('SMTP 配置格式无效。')
    return parsed


def smtp_password_exists() -> bool:
    return secret_exists(SMTP_PASSWORD_SECRET_KEY)


def smtp_password_last4() -> str:
    return secret_last4(SMTP_PASSWORD_SECRET_KEY)


def _stored_config() -> EmailDeliveryConfig | None:
    stored = load_stored_smtp_settings()
    if not stored:
        return None
    security = str(stored.get('security') or 'ssl').strip().lower()
    return EmailDeliveryConfig(
        backend=SMTP_BACKEND,
        enabled=bool(stored.get('enabled', True)),
        host=str(stored.get('host') or '').strip(),
        port=int(stored.get('port') or 0),
        username=str(stored.get('username') or '').strip(),
        password=load_secret(SMTP_PASSWORD_SECRET_KEY),
        from_email=str(stored.get('from_email') or '').strip(),
        use_tls=security == 'tls',
        use_ssl=security == 'ssl',
        timeout=int(stored.get('timeout') or settings.EMAIL_TIMEOUT),
        source='vault',
    )


def _settings_config() -> EmailDeliveryConfig:
    backend = settings.EMAIL_BACKEND
    return EmailDeliveryConfig(
        backend=backend,
        enabled=True,
        host=str(settings.EMAIL_HOST or '').strip(),
        port=int(settings.EMAIL_PORT or 0),
        username=str(settings.EMAIL_HOST_USER or '').strip(),
        password=str(settings.EMAIL_HOST_PASSWORD or ''),
        from_email=str(settings.DEFAULT_FROM_EMAIL or '').strip(),
        use_tls=bool(settings.EMAIL_USE_TLS),
        use_ssl=bool(settings.EMAIL_USE_SSL),
        timeout=int(settings.EMAIL_TIMEOUT or 8),
        source='environment' if backend == SMTP_BACKEND else 'django-backend',
    )


def load_runtime_email_config() -> EmailDeliveryConfig:
    settings_config = _settings_config()
    if settings_config.backend != SMTP_BACKEND or settings_config.ready:
        return settings_config
    try:
        stored = _stored_config()
        if stored is not None:
            return stored
        return settings_config
    except Exception as exc:
        return EmailDeliveryConfig(
            backend=SMTP_BACKEND,
            enabled=False,
            host='',
            port=0,
            username='',
            password='',
            from_email='',
            use_tls=False,
            use_ssl=False,
            timeout=8,
            source='error',
            error=str(exc)[:300],
        )


def email_delivery_configured() -> bool:
    return load_runtime_email_config().ready


def build_email_connection(config: EmailDeliveryConfig | None = None):
    config = config or load_runtime_email_config()
    if config.backend != SMTP_BACKEND:
        return get_connection(config.backend, timeout=config.timeout)
    return get_connection(
        config.backend,
        host=config.host,
        port=config.port,
        username=config.username,
        password=config.password,
        use_tls=config.use_tls,
        use_ssl=config.use_ssl,
        timeout=config.timeout,
    )
