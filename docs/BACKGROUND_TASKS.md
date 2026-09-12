# Background tasks / 后台任务

The distribution has separate export and scheduler services. Both run in the
isolated Vorntek instance; a real 200-company XLSX job was generated and its HTTP
download matched database rows and the stored file hash. See deployment evidence.

导出与调度容器已在独立测试实例运行；真实合成客户导出已核对。外部集成调度仍暂停。

| Service / 服务 | Work / 工作 | Interval / 周期 | Default / 默认 |
| --- | --- | --- | --- |
| `exports` | Process explicitly queued customer exports / 处理已排队导出 | Poll every 2 seconds / 每2秒轮询 | Active in fresh-install candidate / 空库安装候选启动 |
| `scheduler` | Meta inbound retries / Meta入站重试 | 5 minutes / 5分钟 | Paused / 暂停 |
| `scheduler` | Marketing outbox dispatch / 营销事件派发 | 5 minutes / 5分钟 | Paused / 暂停 |
| `scheduler` | Email retry/reminder / 邮件重试和提醒 | 15 minutes / 15分钟 | Paused / 暂停 |

## Safety and operation / 安全与运行

- Scheduled work requires **both** `NEWCROWN_RUN_SCHEDULED_TASKS` and
  `NEWCROWN_ALLOW_EXTERNAL_IO`. Base Compose sets both to `0`; setting a database
  integration to enabled does not override these environment gates. The base
  network also remains internally isolated. Enabling a real channel requires a
  separately reviewed, authorized deployment configuration, not merely an `.env`
  edit to a variable hard-coded off in base Compose.
- 调度任务需要两个环境开关同时打开；默认均关闭。数据库接入开关不能绕过环境限制。实际启用需经过单独审查和授权，基础Compose仍阻断应用外网。
- The scheduler calls inbound retries with `provider=meta`. Both stale-claim
  recovery and pending/failed selection are provider-filtered. It does not
  schedule WhatsApp dispatch, media, templates or inbound processing.
- 调度器只重试Meta入站，连超时认领回收也按平台过滤；不调度WhatsApp相关任务。
- One PostgreSQL session-level advisory lock owns the schedule for a database.
  A second scheduler fails instead of running concurrently. The owning session
  is checked before jobs; loss/replacement stops further execution. This does
  **not** provide exactly-once external delivery or protect against old cron
  commands bypassing this scheduler. Preserve provider idempotency and remove
  duplicate legacy schedules only in an authorized deployment.
- PostgreSQL会话锁防止同库多个新调度器并行；不是外部事件“恰好一次”保证，也不能约束绕过调度器的旧cron。旧机任务本轮未改。
- Jobs run serially, start after their initial interval, and do not replay every
  missed schedule after downtime. Failure records contain job name and error
  type, not exception text, credentials or command output. Shutdown finishes the
  current job then stops before starting another; the container has a 45-second
  grace period, so interrupted tasks still need their existing retry semantics.
- 任务串行执行，不补跑停机期间每个时间点。日志不写凭据或异常详情；停止时不再启动下一项。长任务被容器强制终止后仍依赖原有安全重试机制。

The scheduler writes `/app/.runtime/scheduler.json`. Its healthcheck requires a
fresh `running` or `paused` heartbeat; `paused` means alive and intentionally
silent. A completed command does not prove every queued event was delivered:
inspect CRM event/notification status. Jobs lasting more than 120 seconds can
temporarily make the heartbeat stale; investigate rather than clearing queues.
Docker health status alone is not an automatic repair or delivery guarantee.

健康检查读取心跳；`paused`代表服务活着但任务暂停，不代表外部发送成功。具体结果须看CRM事件与通知记录，不能靠清队列消除告警。

Read-only inspection after an authorized installation:

```sh
docker compose ps -a
docker compose logs --tail=50 scheduler
docker compose exec -T scheduler python manage.py run_scheduled_tasks --healthcheck
```

## Recovery gate / 恢复门禁

The [external-I/O review](EXTERNAL_IO_BOUNDARY.md) records the global transport
guards, including live WhatsApp media/template clients. It is not a database
write freeze or deployed firewall verification; WhatsApp remains paused.

Before restoring a private backup, **stop web writers and all workers**, including
`exports`. Restored queued export jobs must not resume implicitly. The dedicated
private restore procedure, export-queue release decision and live channel enablement
remain pending; do not restore production data using the fresh-install quickstart.

恢复私有备份前必须停止Web写入与所有worker（含导出）；不能让复制来的历史导出队列自动启动。专用恢复流程和队列放行仍未验收，不得用空库安装步骤直接恢复生产数据。

## Evidence / 验证证据

- Local tests cover pause gates, due times, no catch-up burst, error redaction,
  shutdown between jobs, heartbeat state and isolated provider selection.
- PostgreSQL 17.11 tests confirm duplicate scheduler refusal and a paused run
  preserving all table rows and closing cleanly. PostgreSQL18/container service
  lifecycle, actual timed delivery, restart/failure and browser acceptance remain
  pending. No real messages or platform requests were sent for these tests.

Design references: [PostgreSQL advisory locks](https://www.postgresql.org/docs/18/explicit-locking.html#ADVISORY-LOCKS)
and [Compose service healthchecks](https://docs.docker.com/reference/compose-file/services/#healthcheck).
# Export worker shutdown follow-up

The watch worker handles SIGTERM/SIGINT: finish the current export, do not claim
the next queued job, and wake immediately if idle. Compose gives it 60 seconds to
finish. A forced kill or crash still uses the existing 15-minute stale-processing
recovery rule; this is not unlimited graceful execution or a durable task broker.
The CI stack includes a real container stop/exit-code check. Source changes and
local tests alone do not establish that the deployed worker uses this revision.
