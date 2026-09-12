# Isolated Linux container acceptance — 2026-09-12

Scope: user-authorized new-server deployment with synthetic data only. No original
production database/files were copied, no public domain switched, and no external
marketing/email/WhatsApp traffic enabled. This is not completion of the entire
open-source and private-migration goal.

## Observed runtime

- Ubuntu 24.04.1, Docker 29.4.3, Compose 5.1.3.
- Python application image built from `python:3.12.12-slim-bookworm`; website from
  `nginx:1.28.0-alpine`; separate database `postgres:18.3-bookworm`.
- Independent project name, networks and four application persistent volumes
  (plus one separate restore-proof volume). Only the
  website front door binds `127.0.0.1:8088`; database and CRM have no host ports.
- Host resources initially about 3.2GiB available RAM and 49GiB available disk.
  Existing unrelated containers were not restarted or replaced.

## Actual installation findings and fixes

1. Host DNS worked but default bridge/build DNS did not. The same Python image's
   lookup succeeded under host networking and failed under bridge networking.
   CRM and maintenance images were built using the unchanged Dockerfiles with
   the build-only command below. No daemon DNS, firewall, or existing network was
   changed. Runtime application networking remains private/internal.

   ```sh
   # Export the same non-secret image tags configured in the project's .env.
   export NEWCROWN_CRM_IMAGE=newcrown-crm:dev
   export NEWCROWN_MAINTENANCE_IMAGE=newcrown-maintenance:dev
   docker build --network=host --progress=plain -f deploy/Dockerfile.crm -t "$NEWCROWN_CRM_IMAGE" .
   docker build --network=host --progress=plain -f deploy/Dockerfile.maintenance -t "$NEWCROWN_MAINTENANCE_IMAGE" .
   docker compose build website
   docker compose up -d --no-build
   ```

   This is an explicit, host-specific build workaround, not the default isolation
   mode. It shares host networking during trusted dependency installation; do not
   use it for untrusted Dockerfiles or mount production secrets into builds.
   Docker documents the [build network setting](https://docs.docker.com/reference/compose-file/build/#network).

2. The generated application password file ends in a newline. PostgreSQL's
   default `btrim(text)` removed spaces, not that newline, while Python stripped
   it. First initialization therefore failed authentication with zero business
   tables present. Fixed the fresh-role SQL to trim space/tab/CR/LF explicitly.
   Only the new synthetic database role was repaired; no existing account was
   changed. A second fresh temporary PostgreSQL18 container using the fixed SQL
   accepted TCP password login as `newcrown_app`; it was then stopped/auto-removed.

## Passed checks

- Fresh schema initialization after the fix: 27 recorded SQL migrations, 70
  public tables, 58 unmanaged models / 799 fields match.
- Website, contact, administrator login, health and CRM static assets return
  successfully through Nginx. Browser measurement config remains disabled.
- `scripts/verify_compose_inquiry.py` rejects existing business data and requires
  explicit synthetic-test acknowledgement. Through Nginx it verified invalid
  submission rejection, one accepted inquiry matching the database, exact replay
  deduplication, and zero marketing-outbox rows. One synthetic inquiry is retained.
- Random-password `admin` account created privately; actual session/CSRF login
  succeeded. No password appears in this report or source.
- Database/application/export/scheduler/website restart completed; synthetic
  inquiry and administrator survived, outbox stayed empty, external I/O stayed off.
- Database/file maintenance entrypoints execute in their actual images.
- Synthetic PostgreSQL18 backup/checksum/restore to a different empty database
  succeeded; restored unmanaged schema checks passed. All 70 public tables had
  matching row counts and deterministic aggregate row-content hashes between
  source and restored database. No row values were included in the report.
- During the same application-writer stop window, runtime files were snapshotted;
  two files restored into a separate empty volume using a network-disabled
  maintenance container. The restore tool verified the resulting file tree.
  This is synthetic recovery evidence, not production data, attachment/media or
  secret-vault recovery acceptance. Private snapshots are not source artifacts.
- Actual CRM container is attached only to the internal network without a default
  gateway; a public-IP TCP connection probe was blocked. This does not establish
  universal DNS/subprocess/browser isolation or external-platform acceptance.

## Final regression and handoff checks

- Full Linux application regression completed: **650 tests, 648 passed, two
  skipped, zero failures**, 1142.033 seconds; Django system checks clean and test
  database destroyed normally. The temporary regression container has exited and
  auto-removed. It ran the frozen source-only archive inside the built CRM image,
  with network disabled and an isolated SQLite test database; these are not 650
  PostgreSQL18 tests. Later deployment-SQL and verification-script overlays were
  checked separately, not silently included in this frozen archive.
- Current local distribution/recovery/source checks: **36 passed** in 0.862s,
  including the password whitespace fix; **14 Node tests passed**. Initial local
  reruns used interpreters without the declared YAML/psycopg dependencies and
  failed at import. The successful run reused the existing application interpreter
  plus pinned PyYAML6.0.2 installed in an ignored candidate-only dependency folder;
  no original application environment or source was changed.
- Re-running the synthetic inquiry helper against the retained row correctly
  refused existing data before any submission. Final database check still showed
  one inquiry, one admin, zero outbox rows and global external I/O disabled.
- Final SSH-tunnel read-only smoke passed all five routes and disabled measurement
  checks. Both homepage and admin login rendered in the actual browser at the
  configured `http://localhost:8088` origin. Access requires the SSH tunnel to stay
  open; this URL reaches the new server, not a local application/mock.

See implementation status and private deployment handoff for image IDs, the exact
source tree, added patch files and access instructions. No public release is made.

## Still not established

Real customer-data restoration, legacy production migration-ledger adoption,
complete sales/export/browser lifecycle on this server, reboot/failure recovery,
production HTTPS, final image/license/content review, public GitHub/CI/release and
installation by an independent reader remain separate requirements.
