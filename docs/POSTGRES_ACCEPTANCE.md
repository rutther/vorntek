# PostgreSQL installation and recovery acceptance

Observed on 2026-09-12. Scope: isolated local PostgreSQL **17.11** on Windows,
using synthetic data and the candidate source. This is not acceptance of the
PostgreSQL 18 container, new server, production data or external platforms.

## Reproduce

Install the application's Python dependencies into an isolated environment and
provide a PostgreSQL binary directory containing `postgres`, `initdb`, `pg_ctl`,
`pg_dump` and `pg_restore` (with `.exe` on Windows). From the repository root:

```sh
python scripts/test_postgres_install.py --pg-bin /absolute/path/to/pgsql/bin
```

Run as an ordinary user, not root. Do not put a production `.env` in `apps/crm`;
the script refuses to run if that file exists. It creates a unique temporary
cluster, binds only `127.0.0.1` on an unused port, generates SCRAM credentials,
creates four isolated test databases, and uses a non-superuser application role.
It does not register a service, modify an existing cluster or contact production.
The database credentials and vault key are synthetic, not copied from any system.

The script stops its own cluster in `finally`, retains test files and prints the
JSON report path. Verify `result=passed` and `cluster_stopped=true`. Retained files
are local evidence, not release artifacts. They do not replace private backup
procedures for real systems.

On this Windows machine, the pre-existing PostgreSQL binary tree under a Chinese
path failed `initdb` with a UTF-8 encoding error. Copying just `bin`, `lib` and
`share` into an independent ASCII temporary runtime resolved it. The source
runtime and existing data directory were not changed. The test harness also uses
file-backed subprocess output to avoid Windows inherited-pipe hangs during
`pg_ctl start`; one initial hung harness was explicitly stopped after its unique
cluster had been shut down. These were harness/environment issues, not a repair
of the existing database.

## Observed results

After adding the scheduler, a fresh run passed **14 checks**, including the
original twelve plus duplicate-scheduler refusal and a paused scheduler leaving
all database rows unchanged. Its cluster stopped successfully. The new synthetic
backup was 383,071 bytes with SHA-256
`9e18347a03444082ddac68161e3884e587b28de1a6e12560ef39e598e15557d2`;
the same table/constraint/index/trigger/sequence counts below matched after restore.

The following details preserve the preceding 12-check run as historical evidence.

All **12 checks passed** in the final run; its cluster was stopped successfully.

| Check | Observed result |
| --- | --- |
| Isolated cluster and least-privilege installation role | Loopback-only; application role has no superuser, create-role or create-database privilege |
| Read-only install plan | Empty database remained empty |
| Fresh installation | All 27 verified SQL files applied; 58 unmanaged models / 799 fields matched |
| Repeated installation/bootstrap | All existing rows unchanged across 70 tables |
| Existing settings | Custom synthetic site name preserved |
| Installer concurrency | Second installer refused while advisory lock held |
| Upgrade | Real SQL 0001–0026 fixture upgraded by applying only 0027; current schema contract passed |
| Failure atomicity | Deliberately failed synthetic migration rolled back its table and ledger entry together |
| Unknown existing database | Existing table without ledger caused refusal before installation |
| Backup/restore | Custom-format dump restored into an independent empty database; all table rows and schema checks matched |
| Ledger gap | Missing migration row caused refusal without changing database state |
| Ledger tampering | Altered applied checksum caused refusal without changing database state |

Recovery compared **70 tables, 441 constraints, 249 indexes, 17 user-defined
triggers and 67 sequences**, including sequence values/called state. An encrypted
synthetic vault entry was readable after restore with the same test key.
Dump SHA-256: `e57b0dc9897c50b1251d40b84266a3b7279fa8f916626752d154113dc84b2349`
(383,089 bytes). Dumps are not committed.

The previous-release fixture intentionally skips the current model checker while
creating its 0026 schema; the actual upgrade uses the unmodified installer and
current checker. Failure injection extends the SQL chain in memory only; no
committed SQL or checksum manifest is edited.

## Operational archive utility follow-up — 2026-09-12

The lifecycle script now also executes the actual `database_archive.py` CLI:
plan/identity guards, private archive creation and integrity check, overwrite
refusal, restore acknowledgements and active-session rejection, full recovery
comparison and synthetic vault decryption, nonempty-target and checksum rejection.
A synthetic RLS policy references a deliberately removed test-only role, causing
real pg_restore failure after object/data processing; the single transaction
rolled back and the target passed the comprehensive empty-database check.

Final run: **20 aggregate checks passed**, PostgreSQL17.11; the unique test cluster
stopped. Private evidence is retained in the operator's local test directory,
outside Git; it is not a path that another installer needs to reproduce.
The prior 12/14-check records above describe earlier runs. This follow-up did not
run `--http`, build containers, capture file volumes or touch real customer data.

## Remaining acceptance

Latest local follow-up used `--http` and the new read-only stack smoke script:
**23 aggregate checks passed**, PostgreSQL17.11; HTTP and database stopped.
Private evidence remains in the operator's local test directory, outside Git.
This adds actual website/login/health/static/config GET checks to the 20-check
database/recovery run and existing synthetic website-to-CRM HTTP conversion test;
it is still not Nginx, Docker or PostgreSQL18 acceptance.

- PostgreSQL 18 Linux container build and the same install/upgrade/recovery checks.
- Actual Compose secret mounting, startup ordering, persistent volumes and network isolation.
- Full database-backed business/permission and browser flows on the packaged system.
- Verified adoption of the partial legacy production ledger; it remains deliberately refused.
- Private database/file/key backup consistency, authenticated recovery and rollback procedures.
- Authorized new-server deployment and authorized restoration of real data.

No PostgreSQL 18 archive was downloaded in this run. The
[PostgreSQL Windows download page](https://www.postgresql.org/download/windows/)
and [EDB binary archive page](https://www.enterprisedb.com/download-postgresql-binaries)
were consulted for an official source; the actual test reused the previously
available PostgreSQL 17.11 runtime.
