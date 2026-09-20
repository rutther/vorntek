"""Bound provider errors before they cross an audit or operator UI boundary."""

from __future__ import annotations

import re


_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r'''(?i)(?<![\w])(["']?(?:authorization|access[_ -]?token|refresh[_ -]?token|'''
    r'''app[_ -]?secret|client[_ -]?secret|api[_ -]?key|token|secret|password|cookie)["']?)'''
    r'''(?![\w])(\s*[:=]\s*)("[^"]*"|'[^']*'|[^,;}\s]+)'''
)
_BEARER_RE = re.compile(r'(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+')
_SENSITIVE_FIELD_RE = re.compile(
    r'''(?i)(["']?(?:email|phone|full_name|first_name|last_name|client_ip|payload|user_data)["']?'''
    r'''\s*[:=]\s*)("[^"]*"|'[^']*'|[^,;}\s]+)'''
)
_EMAIL_RE = re.compile(r'(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b')
_PHONE_RE = re.compile(r'(?<![\w#])(?:\+?\d[\d\s().-]{7,}\d)(?!\w)')
_WHITESPACE_RE = re.compile(r'\s+')


def redact_event_error(value, *, limit: int = 180) -> str:
    """Return an operations-safe summary, never raw credentials or contact data."""

    text = _WHITESPACE_RE.sub(' ', str(value or '')).strip()
    if not text:
        return '系统没有留下可读错误，请查看接入状态或人工核对。'
    text = _BEARER_RE.sub('Bearer [已隐藏]', text)
    text = _SENSITIVE_ASSIGNMENT_RE.sub(
        lambda match: f'{match.group(1)}{match.group(2)}[已隐藏]',
        text,
    )
    text = _SENSITIVE_FIELD_RE.sub(
        lambda match: f'{match.group(1)}[字段已隐藏]',
        text,
    )
    text = _EMAIL_RE.sub('[邮箱已隐藏]', text)
    text = _PHONE_RE.sub('[电话已隐藏]', text)
    return text if len(text) <= limit else f'{text[: max(limit - 1, 1)].rstrip()}…'
