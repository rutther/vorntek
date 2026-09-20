# Backup, restore and rollback / 备份、恢复与回退

2026-09-12 — operational candidate. Local PostgreSQL17 and actual Linux/Compose
PostgreSQL18 synthetic database/runtime-file recovery checks passed; see
[container evidence](CONTAINER_ACCEPTANCE.md). This is not full production recovery
acceptance. This document is not authorization to stop production or copy
customer data. No original server is changed by packaging these commands.

## What is backed up / 备份范围

`scripts/database_archive.py` creates a **database-only logical archive** and a
SHA-256/size manifest. It is not a complete system backup, continuous backup/PITR
solution, or a source-authenticity certificate. A successful dump is not proof of
a successful restore. Cross-major restore is deliberately refused by this tool.

A complete New Crown recovery set additionally requires:

| Item | Candidate storage | Recovery requirement |
| --- | --- | --- |
| Database | `postgres_data` | Logical archive; table/relationship/sequence/constraint verification |
| Assets and attachments | `crm_files` (`/data`) | Separate private file snapshot, hash manifest, correct ownership |
| Generated exports/runtime | `crm_runtime` | Inventory and authorized snapshot, including private article-preview artifacts; never replay restored jobs or treat a preview as a public activation |
| Derived website serving cache | `website_runtime` | Do not archive its controlled symlink with the regular-file tool; reconstruct only with `reconcile_website_serving_cache` from the restored deployment ledger and verified candidate artifacts, then re-verify before serving |
| Secret vault and app secrets | `.secrets` | Separate encrypted/controlled transfer; matching vault key is essential |
| Version/configuration | immutable images, source, private `.env` | Match application/migration version, site URL and storage mapping |
| Collected static assets | `crm_static` | Rebuild from the recorded application version, not from arbitrary old files |

数据库包不包含附件、素材、导出文件、密钥或集群账号。数据库和文件要在同一受控
停写窗口内完成快照，或使用另行验证的一致性方案。下方文件工具已在本地合成
目录和 Linux 容器 runtime 卷验证；生产路径重定位及完整系统恢复仍需独立验收，不能把
本地数据库/文件测试说成整个生产系统已恢复。

## Preconditions / 前置条件

1. Confirm source/target and authorization. Real-data transfer requires separate
   approval. The new server already hosts other services: choose a distinct
   Compose project and a free loopback port; do not reset shared resources.
2. Use the application database owner, **not** a PostgreSQL superuser. The tool
   requires explicit host, port, database and user plus `--expect-database`.
   Connection strings are not accepted as database names. Secrets stay in mounted
   files or a protected libpq passfile, never shell command arguments or Git.
3. Use only a trusted archive obtained through a protected channel. A hash proves
   integrity against its manifest, not who produced it. PostgreSQL archives can
   execute source-defined code during restore.
4. Protect the backup directory on the host. Linux example, from the **confirmed
   project directory**, after reviewing permissions (creates only a new path):

   ```sh
   sudo install -d -m 0700 -o 10001 -g 10001 ./backups
   docker compose --profile maintenance build maintenance
   ```

   The directory is git-/build-ignored, but is not encrypted. Use private encrypted
   off-host storage and a retention policy. Windows requires an equivalent ACL;
   the container mount/ownership workflow has not been verified on Windows.

## Candidate database backup / 候选数据库备份

The utility defaults to plan-only. It does not stop any service itself. Before a
full database+file snapshot, arrange an approved maintenance window, remove the
entrypoint and stop **all** writers: web application, export worker, scheduler,
initializer, one-off commands and any old cron/external process using this DB.
Do not stop the old production stack merely to test the new isolated stack.

For an authorized, exclusively owned candidate Compose project:

```sh
docker compose stop website crm exports scheduler initialize
docker compose ps -a
docker compose run --rm --no-deps maintenance backup --bundle /backups/checkpoint01 --expect-database newcrown
docker compose run --rm --no-deps maintenance backup --bundle /backups/checkpoint01 --expect-database newcrown --apply
docker compose run --rm --no-deps maintenance verify --bundle /backups/checkpoint01
```

The database must already be running. `--no-deps` prevents the maintenance command
from starting application or queue services. Each backup path must be new. A failed
dump can leave a partial private directory **without** a valid manifest: preserve
it for restricted diagnosis, do not call it a backup, and retry into a different
path. Raw PostgreSQL diagnostics are not echoed because they may disclose rows.

Capture the file snapshots below and the separately controlled secret recovery
material before ending the agreed freeze. The tools do not coordinate the freeze
or certify database/file consistency when other writers remain active.

## File volume snapshots / 文件卷快照

`file_archive.py` uses private content-addressed objects plus a path/hash manifest,
not general-purpose archive extraction. It preserves regular file bytes and empty
directories, deduplicates identical content, and refuses links/junctions/hardlinks,
nonportable/colliding names and source changes during copying. It does not preserve
ACLs, arbitrary executable modes, original owners or timestamps. Restored data
uses the executing user and restricted file/directory modes; container UID10001
must have access. The manifest can contain private filenames: do not publish it.

After building the maintenance image and stopping all writers as above:

```sh
docker compose run --rm --no-deps filebackup backup --volume crm-files --root /data --bundle /backups/checkpoint01-files
docker compose run --rm --no-deps filebackup backup --volume crm-files --root /data --bundle /backups/checkpoint01-files --writers-stopped --apply
docker compose run --rm --no-deps filebackup backup --volume crm-runtime --root /runtime --bundle /backups/checkpoint01-runtime --writers-stopped --apply
docker compose run --rm --no-deps filebackup verify --volume crm-files --bundle /backups/checkpoint01-files
docker compose run --rm --no-deps filebackup verify --volume crm-runtime --bundle /backups/checkpoint01-runtime
```

`filebackup` mounts source volumes read-only and has no network or secrets.
`filerestore` has writable target volumes, read-only snapshots, no network and no
credentials. Neither has dependencies or starts other services. Retain database,
file/runtime snapshots, source/image version and secret recovery references as one
private recovery set with the same recorded maintenance window.

On a **new isolated target**, before starting application commands, populate only
the image's initial empty owned volume directories using an overridden entrypoint:

```sh
docker compose run --rm --no-deps --entrypoint /bin/true crm
docker compose run --rm --no-deps filerestore restore --volume crm-files --root /data --bundle /backups/checkpoint01-files
docker compose run --rm --no-deps filerestore restore --volume crm-files --root /data --bundle /backups/checkpoint01-files --trusted-snapshot --writers-stopped --apply
docker compose run --rm --no-deps filerestore restore --volume crm-runtime --root /runtime --bundle /backups/checkpoint01-runtime --trusted-snapshot --writers-stopped --apply
```

Linux runtime-file restore into a separately owned empty volume passed. The exact
fresh-volume bootstrap sequence above and complete asset recovery still need acceptance;
permission errors must be investigated without broad `chmod`/`chown` or deletion.
Restore permits only file-empty targets with preexisting empty directories that
match the snapshot. It validates all data objects before writing and verifies the
resulting tree. It never overwrites existing files or starts services.

Unlike database transactions, filesystem restoration is **not atomic**. Interrupted
restores retain `.newcrown-restore-incomplete` plus partial files and reject retry
into that target. Keep services stopped, retain the source snapshot and diagnose;
use a separately provisioned fresh target after approval, not automatic cleanup.
The marker is removed only after successful verification. Paths and objects must
remain exclusively controlled: these checks are not a defense against a hostile
process concurrently replacing parent directories. Hashes do not authenticate the
snapshot producer or encrypt customer data.

New customer exports now store paths relative to the private export root, and a
generated XLSX was tested after moving that root. Legacy absolute paths work only
inside the currently configured root; old-server paths are **not** guessed or
silently rewritten. Existing production export/asset references still require an
authorized mapping/hash audit before their recovery can be accepted. Attachments
already use root-relative paths. Restoring runtime files does not authorize old
export jobs, emails or integrations to run.

## Restore only to a new empty database / 仅恢复到全新空库

Prepare a separate project and new volumes. Configure its application password
independently; securely provide the matching vault key if restoring ciphertext.
The base configuration keeps external sending and scheduling off, but **exports
can run without external sending**, so do not start the application/worker stack.

On that new authorized target, with the trusted bundle privately staged under its
own `backups/checkpoint01` directory:

```sh
docker compose up -d db
docker compose ps -a
docker compose run --rm --no-deps maintenance restore --bundle /backups/checkpoint01 --expect-database newcrown
docker compose run --rm --no-deps maintenance restore --bundle /backups/checkpoint01 --expect-database newcrown --trusted-archive --writers-stopped --apply
```

Do **not** run `docker compose up -d` on the whole target before or immediately
after restore: initialization creates tables, and workers can process old jobs.
Restore rejects nonempty targets, a version mismatch, failed checksums, a held
cooperative migration lock, or another database session. It uses one transaction
with error-stop, no `--clean`, no database drop and no automatic overwrite.
The advisory lock is cooperative: it cannot stop an unrelated client from
connecting later. The operator must actually isolate the target and stop writers;
`--writers-stopped` is an acknowledgement, not automatic enforcement.

恢复后不自动启动服务、不执行迁移、不修改业务数据、不清队列。还必须核对：

- Every table's data, relationships, constraints, indexes, triggers and sequence
  positions against the recorded source snapshot; matching counts alone are weak.
- Schema ledger and source version. **Existing production has a partial historical
  ledger: automatic adoption remains prohibited.** Do not delete or invent ledger
  entries to bypass `initialize_database`.
- Private files, stored file paths and permissions; vault decryption; restored
  account access; safe URL/measurement settings and all queued export/inbound/
  outbox/email work. Approve a quarantine/release policy before any worker starts.
- A safe local/SSH-tunnel or HTTPS entry, selected business/browser tests, and
  restart persistence. Record snapshot time; this is not ongoing two-server sync.

## Rebuild the derived website cache / 重建官网运行缓存

Do this only after the database and `crm_runtime` candidate files have both been
restored and verified. Keep website, CRM, initializer, export worker and scheduler
stopped; leave only PostgreSQL running. First seed the new `website_runtime` named
volume from the exact candidate CRM image without starting the application:

```sh
docker compose run --rm --no-deps --entrypoint /bin/true crm
```

The reconciliation command defaults to a read-only plan. It verifies the latest
immutable deployment receipt, its activated operation and selection bindings, and
the restored candidate bytes. It reports `deploymentId`, `targetVersion` and the
observed serving version (`bundled` means the image baseline):

```sh
docker compose run --rm --no-deps crm python manage.py reconcile_website_serving_cache \
  --site-code siteos_demo --expect-database newcrown
```

Review that output. Apply only with those exact stale-state values; replace `42`
with the reported ID. A normal fresh derived volume expects `bundled`:

```sh
docker compose run --rm --no-deps crm python manage.py reconcile_website_serving_cache \
  --site-code siteos_demo --expect-database newcrown \
  --expect-deployment-id 42 --expect-serving-version bundled \
  --writers-stopped --apply
```

The command changes no database row and does not manufacture a second deployment
receipt. It holds the site row lock while copying and atomically switching the
cache. It refuses PostgreSQL/database identity mismatches, enabled outbound or
scheduled work, a pending deployment, changed receipt/pointer, invalid ledger
bindings or changed candidate bytes. If it fails after a pointer switch, preserve
the target, run the plan again, and use the newly observed exact state; do not
delete the ledger, candidate store or serving directory to force a retry.

After a successful apply, start the stack, verify the public pages through the
loopback entrypoint, restart CRM and website, and verify again. This source path
has local unit coverage and is included in the new Linux synthetic acceptance
gate, but it must not be called runtime-accepted until that gate succeeds for the
exact release commit.

## Rollback / 回退

Record source commit, image digests, SQL checksums and a **restore-tested** private
recovery set before an upgrade. Do not assume an older image can run an upgraded
schema. If compatibility has not been proved, restore the pre-upgrade set into
another empty isolated target and verify it with the old version before switching
the authorized entrypoint. Preserve the failed target for controlled diagnosis.

Never use `docker compose down -v`, broad directory deletion, ledger removal or
blind down-migrations as rollback. A rollback to an earlier snapshot loses later
writes unless reconciled; state that loss/window and obtain approval. Keep the
current public site untouched during this new-server project.

## Evidence and references / 证据与依据

Local acceptance is recorded in [PostgreSQL acceptance](POSTGRES_ACCEPTANCE.md).
The utility's local tests do not stand in for actual container/production recovery.

The archive/transaction choices follow PostgreSQL's official
[pg_dump documentation](https://www.postgresql.org/docs/18/app-pgdump.html) and
[pg_restore documentation](https://www.postgresql.org/docs/18/app-pgrestore.html).
Separate deployment names follow Docker's
[Compose project-name documentation](https://docs.docker.com/compose/how-tos/project-name/).
