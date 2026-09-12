# Account security / 账号安全

2026-09-12 — local candidate evidence, not a production security certification.

## Observed behavior / 当前核验

- Fresh installation has no fixed administrator password; create the first
  administrator with `python manage.py createsuperuser` inside the CRM service.
- Account forms use Django password validation and `set_password`; stored values
  are hashes. Administrators can reset passwords, not recover existing ones.
- Create/reset responses show only the newly supplied password once, with
  `Cache-Control: private, no-store, max-age=0`. Audit snapshots contain neither
  plaintext passwords nor password hashes. A subsequent GET has no receipt.
- Resetting a password invalidates the previous login/session. Disabling an
  account rejects existing sessions and future login. Role checks, last-admin
  protections, optimistic edit versions and audit rollback have regression tests.

账号不是持续明文列表：数据库存哈希，管理员可重置，不能取回旧密码。创建或
重置后的当前响应提供一次性回执；不要把它复制到公开群聊、Git 或运行日志。

## Candidate change / 本次候选修复

The inherited receipt script blanked visible password text on `pagehide`, but
kept a password closure and copy controls. It now clears the retained value,
removes the receipt and disables retained handlers on dismissal or page exit.
Pending clipboard completion no longer updates the removed receipt. Styles and
account permissions are unchanged; the shared extra-script cache key was bumped.

Leaving the page does **not** erase the operating-system clipboard or revoke an
already requested clipboard write. This is UI lifecycle hardening, not guaranteed
JavaScript-memory zeroization or protection from screenshots/browser extensions.

## Verification / 验证

- `node --test scripts/test_credential_receipt.mjs`: five synthetic DOM/clipboard
  checks pass (copy, dismiss, pagehide, pending completion and denied clipboard).
- Together with `test_form_status.mjs` and `test_measurement.mjs`: 14 passed.
- `manage.py test console.test_system_governance_v2 --noinput`: 22 passed in
  26.934s in isolated SQLite test mode, no system-check issues. External I/O off.
- These are source and automated test results, not actual browser BFCache,
  PostgreSQL18/container, TLS, rate-limiting or comprehensive security acceptance.
  Production credentials and servers were not changed or verified by this run.
