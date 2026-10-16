# Security policy / 安全说明

Report vulnerabilities through
[GitHub private vulnerability reporting](https://github.com/rutther/vorntek/security/advisories/new),
enabled and verified on2026-10-17. Do not post credentials, customer information,
private files or raw operational logs in public issues.

Identify the exact Tag/commit and provide a minimal synthetic reproduction.
Fixes target the latest candidate. Source pre-releases do not promise long-term
support, a response SLA or complete security certification. Current scope and
evidence are in docs/RELEASE_SCOPE.md and docs/IMPLEMENTATION_STATUS.md.

中文：使用上述私密入口报告漏洞，已核实启用。提供准确版本和合成复现，勿在公开Issue
或截图中贴客户资料、密码或运行日志。当前为源码预发行，不承诺长期支持或响应SLA。

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
