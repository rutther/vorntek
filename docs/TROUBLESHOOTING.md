# Troubleshooting / 故障排查

Diagnose read-only first. Preserve failed containers, logs and private snapshots;
do not delete volumes, migration rows or files to make the next attempt appear clean.

先只读诊断，保留失败容器、日志和私有快照；不得通过删除卷、迁移记录或文件来让下一次
尝试看起来“干净”。

## Safe first checks / 安全首查

```sh
docker compose config --quiet
docker compose ps -a
docker compose logs --tail=100 initialize crm website exports scheduler
curl --fail http://127.0.0.1:8088/healthz/
docker compose run --rm --no-deps crm python manage.py release_preflight --strict
```

Share only redacted status. Default access logs omit query strings and client
metadata, but error logs and private database/file evidence can still contain
sensitive material. Never paste `.env`, `.secrets`, dumps or raw customer rows.

只共享脱敏状态。默认访问日志不记录查询参数和客户端元数据，但错误日志、数据库和文件
证据仍可能敏感；不得粘贴 `.env`、`.secrets`、dump 或客户原始行。

## Common failures / 常见故障

| Symptom | Read-only diagnosis | Safe action |
| --- | --- | --- |
| `initialize` failed | Inspect its container log and run preflight; identify checksum, gap, unknown migration or database availability | Correct source/configuration; never edit ledger rows or applied SQL |
| Browser returns 400/CSRF failure | Compare the exact browser origin/Host with `NEWCROWN_SITE_URL`, allowed hosts and trusted origins | Correct the reviewed configuration together; do not add `*` |
| 413 request too large | Identify the exact route and application file contract | Use the documented route limit; do not raise the global 2 MiB default |
| 502/504 through Nginx | Check `crm` health and initialization dependency state | Repair the failed service; do not proxy around readiness |
| Storage/preflight unavailable | Inspect volume mounts, owner markers and UID10001 access without listing private content | Correct the exact mount/ownership in an isolated target; avoid broad recursive permission changes |
| Scheduler is `paused` | Check both external-I/O flags and the scheduler heartbeat | This is the safe default; enabling a channel needs separate review |
| Restore refuses target | Verify target emptiness, database identity, snapshot manifest and stopped writers | Provision a new empty target; do not use `--clean`, overwrite or delete the incomplete marker |
| Build cannot resolve package hosts | Follow [build-network troubleshooting](BUILD_NETWORK.md) | Use only the documented build-time DNS workaround |

## Request limits / 请求上限

Nginx accepts 2 MiB by default. Exact authenticated upload routes have bounded
multipart headroom for the application contracts: asset 129 MiB, CRM attachment
51 MiB, paused WhatsApp attachment 26 MiB, article ZIP 17 MiB, customer import
11 MiB and article cover 9 MiB. The application still validates file type, file
bytes, archive entries and expanded size. These are `client_max_body_size`
boundaries; a 413 on any other path is expected.

Nginx 默认只接受 2 MiB。只有明确的登录后上传路由按应用合同保留 multipart 余量：
素材 129 MiB、CRM 附件 51 MiB、暂停的 WhatsApp 附件 26 MiB、文章 ZIP 17 MiB、
客户导入 11 MiB、文章封面 9 MiB。应用仍检查类型、实际字节、压缩包条目和展开大小；
这些是 `client_max_body_size` 边界，其他路径出现 413 属于预期拒绝。

These values are a source-level contract in the current candidate; exact Nginx
syntax and request behavior still require Linux container validation. Do not cite
the static check as runtime acceptance.

这些数值目前属于候选源码合同；Nginx 精确语法及请求行为仍需 Linux 容器验收，不能把
静态检查当作运行验收。

## Escalation evidence / 升级处理证据

Record source commit, `VERSION`, manifest hash, Compose project, container state,
failing command, sanitized error type and whether any write began. For upgrade or
recovery failures follow [UPGRADE.md](UPGRADE.md) and [BACKUP_RESTORE.md](BACKUP_RESTORE.md).
Production, real-data and external-platform actions remain separately authorized.

记录源码提交、`VERSION`、清单哈希、Compose 项目、容器状态、失败命令、脱敏错误类型
以及是否已经开始写入。升级/恢复失败按对应手册处理；生产、真实数据和外部平台动作仍需
单独授权。
