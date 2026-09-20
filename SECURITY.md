# Security policy / 安全说明

The published `v0.1.0-rc.1` is a source pre-release; the current 0.2 work is an
unpushed development candidate. There is no supported production release,
completed security certification, or approved public reporting channel yet. A
private security-reporting contact must be configured before a supported release.
Until then, contact the project owner through an existing private channel; do not
post credentials, customer information, private files or raw logs in public issues.

GitHub private vulnerability reporting was observed disabled through the public
repository API on 2026-09-21. Do not send reports to the repository advisory form
unless the owner has subsequently enabled and verified it.

已公开的是 RC1 源码预发行；当前 0.2 候选尚未推送，也不是经过全面安全审计的受支持
生产发行版。正式支持前必须确定私密漏洞报告渠道；2026-09-21 只读核对时 GitHub
私密漏洞报告仍为关闭状态。请勿在公开 Issue、截图、代码或 CI 产物中暴露客户信息或密钥。

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
