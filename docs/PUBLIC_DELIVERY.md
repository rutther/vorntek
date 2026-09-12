# Public source delivery / 源码交付记录

Observed 2026-09-13. This records a **pre-release**, not completion of all migration,
browser or production-readiness requirements.

## Version and public evidence

- [Repository](https://github.com/rutther/vorntek)
- [v0.1.0-rc.1](https://github.com/rutther/vorntek/releases/tag/v0.1.0-rc.1), commit
  `fe353544d6d37d5cbf01ccecbb157cf30edba6fe`, tree
  `459827218ec0b80d287bfaf8aacc864189841bb3`: 444 tracked source files.
- [Successful CI run](https://github.com/rutther/vorntek/actions/runs/34712023363):
  both jobs completed successfully. Application: dependency consistency, 44 root
  tests, JS contracts, 669 application tests in 351.985s (667 passed, two skipped).
  PostgreSQL/Compose: 27 lifecycle/HTTP checks, image builds, startup/health,
  disabled measurement, maintenance CLI checks and real export-worker SIGTERM.
- The initial CI run failed before standalone PG startup. Reproduction found a
  distro-owned Unix socket path; the corrected POSIX test directory passed the
  complete subsequent CI. Windows startup behavior is unchanged.
- Fresh anonymous clone and local/remote tag resolution matched the Git objects.
  The complete three-commit release history scan returned the same nine reviewed
  non-secret test/storage identifiers, with no new alert. The eight-file fix
  diff scan returned zero alerts. See [review details](review/IMAGE_SECURITY_20260913.md).

## Independent clone installation

A fresh public clone at `a13278a9b55189735415bd60e2ab726b6b864015` was installed
with a new Compose project, fresh secrets, independent volumes and loopback-only
port. Its website/CRM application source matches the release tag; the later tag
changes are tests/documentation. Nothing from the running instance's private
configuration, users or data was copied into this installation.

1. Website generation left tracked sources unchanged; configuration created four
   unique secrets without printing them. Compose validation passed.
2. Default build failed on the test host's Docker build-network DNS. The documented
   [build-only host-network fallback](BUILD_NETWORK.md) built all three images;
   this is recorded separately from normal-build success on the fresh CI runner.
3. Initialization exited zero. Database/CRM/scheduler became healthy; website and
   export worker ran. Five smoke routes passed and measurement remained disabled.
4. Actual synthetic inquiry: invalid input refused without a row; valid input
   persisted; replay deduplicated; authenticated CRM detail contained the same
   synthetic address. No outbox event was created.
5. The normal Django `createsuperuser` management command created an administrator
   with a random private password. Session/CSRF login and protected CRM pages passed.
6. Optional guarded initialization created 200 fictional customers. After the
   whole test stack restarted, 200 companies, one inquiry, one account and the
   credential remained; outbox stayed zero and all health checks recovered.

Runtime proof and passwords stay private. The clone test is not a production
restore. Existing deployed instance and unrelated shared-host services were not
restarted by these clone checks. The running public demo still uses the previously
recorded f7 image set; its application sources match the release, but its old
maintenance tag has not yet been replaced. Do not mislabel its whole image set as RC1.

## Limits and next gates

- No real customer data restoration has been authorized/completed for the fictional
  demonstration; an explicit user scope decision is still required.
- Browser download-to-disk confirmation remains open. Authenticated XLSX bytes and
  records were checked, but that does not prove a browser saved the file.
- No container binaries or build caches were published. Source licenses/notices
  are retained; binary redistribution obligations are a separate gate if images
  will be published. Do not publish old maintenance donor layers with the test key.
- Real Meta browser/server deduplication is unverified; advertising, email,
  WhatsApp and measurement remain disabled. Do not market mock results as live proof.
- Public HTTP is synthetic-only. Real usage requires HTTPS, secure cookies,
  appropriate permissions, retention and protected recovery material.

中文：源码和首个候选版本已公开，CI 两项作业均通过；干净克隆已实测安装、询盘、
账号登录、演示数据及重启保留。当前不是原公司真实数据库迁移完成，也不是所有浏览器
流程、二进制镜像分发或生产安全上线验收完成。既有实例与独立克隆的版本、数据和配置
必须分别记录，不能相互冒充；回退继续遵守 [备份恢复手册](BACKUP_RESTORE.md)。
