# Security policy / 安全说明

This is a development candidate. There is no supported public release, completed
security certification, or approved public reporting channel yet. A private
security-reporting contact must be configured before the first public release.
Until then, contact the project owner through an existing private channel; do not
post credentials, customer information, private files or raw logs in public issues.

目前仍为开发候选，并非已通过全面安全审计的生产发行版。首次公开前必须确定私密
漏洞报告渠道。请勿在公开 Issue、截图、代码、CI 产物中暴露客户信息或账号密钥。

## Deployment boundaries

- Default Compose is an isolated, loopback-only HTTP setup, not public HTTPS.
  Use an approved SSH tunnel or a separately configured and tested HTTPS entry.
- Create unique admin credentials. Passwords are hashed; creation/reset receipts
  show only the new value once. Never add recoverable old-password listing.
- Keep real `.env`, `.secrets`, database/file snapshots, exports and customer
  material outside version control and public images. Protect filesystem access
  and encrypted off-host recovery material; ignore rules alone are not encryption.
- External measurement/sending and scheduled jobs are off by default. Restored
  export workers can still change files without external sending: stop **all**
  writers and approve queue quarantine before restoring or starting services.
- Treat database archives as executable source-defined code. Trust the producer
  and transfer channel; checksums alone do not establish authenticity.
- Do not blindly adopt a partial legacy SQL ledger, weaken permissions, expose a
  database port, disable TLS verification, or delete shared volumes to fix startup.

## Reporting a suspected issue

Privately provide affected version, a minimal synthetic reproduction, observed
impact and redacted evidence. Do not test against someone else's instance or
customer data without authority. Stop affected publication/deployment; retain
restricted evidence. If a secret has been exposed, revoke/rotate it through the
authorized owner rather than relying on deleting a Git commit or chat message.

Account, socket-guard and recovery tests are useful but limited. Current evidence
and unresolved requirements are recorded in [implementation status](docs/IMPLEMENTATION_STATUS.md),
[account security](docs/ACCOUNT_SECURITY.md), [backup/recovery](docs/BACKUP_RESTORE.md),
and the current candidate's [source security review](docs/review/SECURITY_REVIEW_20260921.md).

## Release requirements

Before public release, configure the real private reporting channel and version
support policy; review dependencies, source/history, image layers, CI outputs and
media rights; validate actual container/network/TLS behavior. No current local
test result waives these gates or establishes a guaranteed response SLA.
