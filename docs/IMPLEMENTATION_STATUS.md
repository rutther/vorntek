# Implementation status / 实现状态

Observed 2026-09-13. **Public source pre-release; full goal acceptance remains open.**

## Current delivery

- [v0.1.0-rc.1](https://github.com/rutther/vorntek/releases/tag/v0.1.0-rc.1)
  is public at commit `fe353544d6d37d5cbf01ccecbb157cf30edba6fe`.
- Both [release CI jobs](https://github.com/rutther/vorntek/actions/runs/34712023363)
  passed: 44 root checks, browser-script contracts, 669 application tests (two
  skipped), 27 PostgreSQL lifecycle/HTTP checks, and actual Compose build/start/stop.
- A fresh anonymous GitHub clone passed installation with new secrets/volumes,
  admin login, website inquiry-to-CRM matching, optional 200-customer seed and
  full-stack restart persistence. Host-specific build DNS fallback is documented,
  not concealed as a normal-build success.
- Later documentation-only commits have separate CI runs. See
  [precise versions and delivery evidence](PUBLIC_DELIVERY.md); a successful
  release run does not automatically certify every future commit.

## Verified scope and evidence

| Area | Observed result | Evidence / limit |
| --- | --- | --- |
| Website and brand | 15 bilingual pages, seven business lines, eight original AI images, same synthetic data for chart/CSV | [Demo pack](vorntekDemo/README.md), [website acceptance](VORNTEK_ACCEPTANCE.md); fictional concepts, not certified products |
| Forms and CRM | Invalid input refused, successful inquiry persisted once, replay deduplicated; business context visible in authenticated CRM | [HTTP evidence](HTTP_ACCEPTANCE.md), [deployed evidence](VORNTEK_DEPLOYMENT.md); synthetic only |
| Sales and roles | Assignment, qualification, conversion, follow-up task, handover and denied cross-user paths tested over actual HTTP | [Deployed role checks](review/DEPLOYED_ROLES_20260913.md); temporary accounts disabled |
| Accounts | Hashed passwords, restricted admin management, one-time credential receipt, session invalidation and redaction | [Account security](ACCOUNT_SECURITY.md); no fixed default password |
| Database lifecycle | 27 canonical SQL migrations; guarded initialization, upgrade, locks, failure rollback, independent restore | [PostgreSQL acceptance](POSTGRES_ACCEPTANCE.md), [container acceptance](CONTAINER_ACCEPTANCE.md) |
| Recovery | Actual synthetic 70-table restore, schema/sequence checks, vault decryption and independent file-volume recovery | [Recovery guide](BACKUP_RESTORE.md), [image/recovery evidence](review/IMAGE_SECURITY_20260913.md); not original-company data migration |
| Workers and isolation | Export worker handles actual SIGTERM; scheduler healthy but external tasks paused; browser/server measurement off | [Background tasks](BACKGROUND_TASKS.md), [external-I/O boundaries](EXTERNAL_IO_BOUNDARY.md), [CI](CI.md) |
| Export | Authenticated XLSX bytes, hash, sheets and expected records checked | Actual browser save-to-disk remains unconfirmed |
| Source release | MIT project code, retained third-party terms, reviewed source/history, original private assets excluded | [Release review](PUBLIC_RELEASE_REVIEW.md), [notices](../THIRD_PARTY_NOTICES.md); binary redistribution not approved |

The running demonstration has 200 seeded fictional companies plus one converted
synthetic customer and eight inquiries. The independent clone has 200 companies
and one inquiry. These observations describe different databases, not synchronized
instances.

The demonstration's application sources match RC1. Its maintenance configuration
now selects the verified clean image; changing the three maintenance image fields
did not restart existing containers or write business data. The historical f7
release directory was not rewritten or relabeled as an entire RC1 image set.

## Open gates and boundaries

1. Original-company private-data restoration has not been performed. The user's
   later fictional Vorntek requirement needs an explicit scope decision; do not
   import real customer data into the public demonstration.
2. Browser download-to-disk evidence is still missing. Backend file validation
   and clicking a link do not prove a saved file.
3. No prebuilt container images or build caches have been published.
   [Binary redistribution review](review/BINARY_REDISTRIBUTION.md) identifies
   missing component notices/source-provenance work. Do not publish the old
   maintenance image or donor layers.
4. Public HTTP is a synthetic demonstration only. Real operation requires HTTPS,
   secure cookies, access controls, retention and protected backups.
5. Real Meta deduplication is not verified. Advertising, email and WhatsApp stay
   disabled. Historical CMS/preview capability is not an accepted one-click
   production publishing workflow.

中文：源码候选版、实际部署、关键业务、隔离恢复及干净克隆安装已有证据。
真实客户迁移、浏览器文件落盘及预构建镜像交付仍未完成，不能把“软件可运行”
写成原完整目标已结束。

## Historical evidence

Detailed stage reports linked above remain in the repository. The former running
log is preserved in [Git history at 772cdd4](https://github.com/rutther/vorntek/blob/772cdd40c18d8a096488e760354150be3c6540ad/docs/IMPLEMENTATION_STATUS.md).
Its repeated “Latest” headings and old pending states describe earlier snapshots,
not current requirements or present deployment status.
