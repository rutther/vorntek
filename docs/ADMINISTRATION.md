# Administration / 日常管理

This page covers routine operation of one isolated Compose installation. Database
restore, version upgrade and public deployment are separate high-risk procedures.

本页用于一套隔离 Compose 安装的日常操作。数据库恢复、版本升级和公网部署属于另外
的高风险流程。

## Start and inspect / 启动与检查

```sh
docker compose up -d
docker compose ps -a
curl --fail http://127.0.0.1:8088/healthz/
docker compose exec crm python manage.py release_preflight --strict
```

Initialization must finish successfully before CRM, exports and scheduler start.
`/healthz/` reports the application/source version; it is not a database backup or
business-flow test. `scheduler` being healthy and `paused` means intentionally
silent, not that external delivery succeeded.

初始化必须成功完成，CRM、导出 worker 和 scheduler 才会启动。`/healthz/` 只报告
应用/源码版本，不代表数据库已备份或业务流程已验收。scheduler 健康且 `paused` 表示
按设计静默，不代表外部发送成功。

## First administrator and roles / 首位管理员与角色

```sh
docker compose exec crm python manage.py createsuperuser
```

There is no default password. Use the authenticated system pages to create or
disable users, assign roles/teams and reset credentials. Password receipts are
shown once; do not copy them to tickets or logs. Keep content build, candidate
selection and website deployment capabilities separate unless one reviewed
operator genuinely needs each capability.

系统没有默认密码。通过登录后的系统页面创建/停用账号、分配角色/团队和重置凭据。
密码回执只显示一次，不得复制到工单或日志。内容构建、候选选择和官网部署能力应保持
分离，只有确需承担对应职责的受审操作员才分别授权。

## Business and content operation / 业务与内容操作

- Customer imports are preview-first, bounded and explicit-commit. Review rejected
  rows before applying; do not edit database tables to force an import.
- Customer-pool review/publish acts per company and reports partial failures. A
  failed/restricted route remains for manual review.
- Customer exports run through the `exports` worker. Verify the downloaded file,
  not only the queue status.
- Article preview, whole-site candidate build, candidate selection and website
  deployment are four different states. Only the exact permission-gated deployment
  confirmation changes the serving pointer; there is no background auto-deploy.

- 客户导入先预览、受大小限制且需显式确认；先处理拒绝行，不得直接改表强行导入。
- 公海核验发布逐企业事务执行并报告部分失败；受限路线继续留人工处理。
- 客户导出由 `exports` worker 执行；必须核对实际下载文件，不能只看队列状态。
- 文章预览、整站候选构建、候选选择、官网部署是四种不同状态。只有具备精确权限的
  部署确认会切换 serving 指针，系统没有后台自动部署。

## Workers and safe stopping / Worker 与安全停止

```sh
docker compose logs --tail=100 exports scheduler
docker compose exec -T scheduler python manage.py run_scheduled_tasks --healthcheck
docker compose stop website crm exports scheduler
```

Stop all writers before upgrade or restore. The export worker finishes its current
job on graceful stop when possible; forced termination still relies on stale-job
recovery. Never clear queues or delete volumes to make a health indicator green.

升级或恢复前停止全部写入方。导出 worker 在正常停止时会尽量完成当前任务；强制终止
仍依赖陈旧任务恢复。不得通过清队列或删卷让健康状态“变绿”。

## Recovery and updates / 恢复与更新

Use [backup/restore](BACKUP_RESTORE.md) and the [upgrade runbook](UPGRADE.md).
`docker compose down -v` destroys persistent data and is never a normal stop,
upgrade or rollback command.

恢复使用[备份恢复手册](BACKUP_RESTORE.md)，更新使用[升级手册](UPGRADE.md)。
`docker compose down -v` 会销毁持久化数据，绝不是普通停止、升级或回退命令。
