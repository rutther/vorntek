# File recovery acceptance / 文件恢复验收

2026-09-12. Scope: local synthetic files and the independent development candidate.
No real assets, customer attachments, server volumes or production data were copied.

## Observed checks

- 11 filesystem tests use real temporary directories. They cover plan-only,
  content/empty-directory recovery, duplicate-content storage, backup and target
  overwrite refusal, volume/acknowledgement checks, corruption, path traversal,
  platform-name and case collisions, source changes, hardlinks, interrupted
  restoration, matching image-seeded empty directories and nested-destination
  refusal. One test runs all three actual CLI operations in subprocesses and
  verifies output contains only summary results, not file content/names.
- Together with database archive and Compose guards: 25 tests passed. File
  operations are opt-in, networkless and credential-free in source configuration;
  backup source volumes and restore snapshot mounts are read-only as appropriate.
- 29 Django customer-pool/export-path tests passed in 41.319s with isolated SQLite
  test data. A newly generated XLSX stored a relative path, downloaded successfully,
  then downloaded byte-identically after its private root was moved without a
  database update. Existing ownership/cancellation/expiry/field rules still passed.
- Tests confirmed legacy absolute export paths remain bounded to the current
  private root and outside-root/traversal/non-XLSX references are rejected.

## Reproduce

With the candidate's Python application and development dependencies installed:

```sh
python -m unittest discover -s tests -v
cd apps/crm
# Isolated test environment only; no application .env or database connection vars.
SITEOS_ADMIN_DEBUG=1 NEWCROWN_ALLOW_EXTERNAL_IO=0 python manage.py test console.test_export_paths console.test_customer_pool_views --noinput
```

The shell environment line is for a POSIX shell. On Windows, set the same two
environment variables in the test-only PowerShell process. Never redirect these
tests to an existing database. The test runner creates/destroys its own test DB;
filesystem fixtures use unique temporary directories, and generated export tests
clean up only files they create. No HTTP service remains running after this run.

## What this does not prove

- Linux/container volume creation, ownership and actual maintenance image build.
- A consistent production database+file+vault snapshot or authenticated transport.
- Mapping existing server-specific asset/export paths to the new server.
- Recovery of private data, whole-system startup, full PostgreSQL18 business
  acceptance or safe release of restored queues.
- Atomic file rollback, ACL/timestamp preservation, encryption, or protection
  against concurrently hostile modifications to otherwise trusted directories.

See [operational instructions](BACKUP_RESTORE.md). The new-server deployment gate
and separate real-data authorization remain unchanged.
