# Upgrade runbook / 更新操作手册

This runbook upgrades one existing Compose installation. It does not authorize a
production deployment. Test the exact source and backup/restore path in an
independent environment before using it with real data.

本手册用于更新一套已有的 Compose 安装，不代表已授权部署生产。对真实数据操作前，
必须先在独立环境验证完全相同的源码、备份和恢复路径。

## 1. Identify and plan / 识别版本并只读检查

Record the current Git commit, `VERSION`, `/healthz/` response and Compose project
name. From the candidate checkout, build the new CRM image and run the read-only
preflight against the existing database:

记录当前 Git 提交、`VERSION`、`/healthz/` 返回值和 Compose 项目名。在候选源码中构建
新 CRM 镜像，再对现有数据库运行只读检查：

```sh
git status --short
cat VERSION
docker compose build crm initialize exports scheduler website
docker compose run --rm --no-deps crm python manage.py release_preflight --strict
```

The command performs `SELECT`/introspection only. It reports the application
version, migration chain/ledger/pending suffix, storage availability and outbound
writer flags. `--strict` refuses non-PostgreSQL databases, unavailable or invalid
ledgers, unknown/downgrade migrations, and enabled outbound writers. It does not
apply SQL or create directories.

该命令只做 `SELECT` 和结构读取，报告应用版本、迁移链/账本/待执行后缀、存储状态及
外部写入开关。`--strict` 会拒绝非 PostgreSQL、不可用或不完整账本、未知/降级迁移，
以及未暂停的外部写入；不会执行 SQL 或创建目录。

## 2. Create and verify a recovery set / 创建并验证恢复集

Follow [BACKUP_RESTORE.md](BACKUP_RESTORE.md). Preserve together:

- a verified PostgreSQL custom-format dump;
- CRM file/runtime volume snapshots;
- the matching secret files, especially the vault key;
- the current source commit, image identifiers and Compose configuration.

Do not continue merely because files exist. Verify manifests/hashes and restore the
set into an independent target before the real change when risk requires it.

按照 [BACKUP_RESTORE.md](BACKUP_RESTORE.md) 同时保存并核验 PostgreSQL 自定义格式
备份、CRM 文件/运行卷、匹配的秘密文件（尤其保险库密钥）、当前源码提交、镜像标识
和 Compose 配置。只有“备份文件存在”不算恢复证据；高风险更新前应先恢复到独立目标。

## 3. Pause writers and recheck / 暂停写入并复查

Stop the public front door and application writers for this Compose project while
leaving PostgreSQL available for the upgrade job:

```sh
docker compose stop website crm exports scheduler
docker compose run --rm --no-deps crm python manage.py release_preflight --strict
```

The configuration must keep `NEWCROWN_ALLOW_EXTERNAL_IO=0`,
`NEWCROWN_RUN_SCHEDULED_TASKS=0` and `SITEOS_WHATSAPP_ALLOW_LIVE_SEND=0` during the
upgrade. Stopping containers does not compensate for unsafe candidate settings.

更新期间须保持上述三个外发/任务开关为 `0`。仅停止容器不能弥补候选配置本身仍允许
外发的问题。

## 4. Apply the append-only chain / 执行追加式迁移链

Run the same initializer used by normal startup:

```sh
docker compose run --rm --no-deps initialize
```

The initializer verifies checksums and the exact recorded prefix, obtains the
database migration lock, applies each pending migration transactionally, records
it only after success, bootstraps missing configuration and collects static files.
Never edit an applied SQL file, delete ledger rows or run `down -v` as an upgrade.

初始化器会校验哈希与账本前缀、取得迁移锁、逐条事务执行待迁移，并仅在成功后记账，
随后补充缺失配置及收集静态文件。不得改写已执行 SQL、删除账本行，或把 `down -v`
当作更新命令。

## 5. Start and verify / 启动与验收

```sh
docker compose up -d
docker compose ps -a
docker compose exec -T crm python manage.py release_preflight --strict
```

Confirm `/healthz/` reports the intended version, the migration pending list is
empty, required containers are healthy, and synthetic inquiry/login/customer
workflows pass. Verify workers and static assets, then perform the browser checks
appropriate to the changed feature.

确认 `/healthz/` 返回目标版本、待迁移列表为空、必要容器健康，并用合成数据验证询盘、
登录和客户流程；同时核对 worker、静态资源及本次功能对应的浏览器路径。

## 6. Failure and rollback / 失败与回退

If preflight or initialization fails, keep writers stopped and retain all output.
Do not insert migration ledger rows by hand. An application-only rollback is valid
only when the older code is proven compatible with the upgraded schema. Otherwise
restore the complete pre-upgrade database/files/secrets set into an independent
Compose project, validate it, then switch traffic according to an authorized plan.

若检查或初始化失败，保持写入暂停并保留输出，不得手工补迁移账本。只有旧代码已证明
兼容新结构时才可只回退应用；否则应把更新前数据库、文件和秘密的完整恢复集恢复到独立
Compose 项目，验收后再按授权方案切换流量。
