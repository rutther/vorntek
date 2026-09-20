# Database contract / 数据库契约

Django owns managed authentication, content-type, permission and session tables.
Business models use `managed = False`; the authoritative schema is the complete,
ordered and checksum-verified `migrations/0001…0032` SQL chain. Do not rewrite
applied SQL or use a demo reset as an installation/upgrade procedure.

## Fresh installation and upgrade

The root Compose `initialize` service runs these commands after its independent
database is healthy and before the application/workers start:

```sh
python manage.py initialize_database --apply
python manage.py bootstrap_site
python manage.py collectstatic --noinput
```

Without `--apply`, `initialize_database` only inspects/plans. With it, installation
verifies SQL checksums, takes a cooperative PostgreSQL lock, runs Django migrations
and records each SQL migration transactionally. It checks 62 unmanaged models and
866 fields. Repeated initialization preserves existing configuration and data.

Existing unowned databases, partial/gapped legacy ledgers and changed applied
checksums are refused. Missing ledger rows do not mean old SQL is safe to replay.
Production legacy-ledger adoption requires separate verified recovery work; never
delete or invent ledger rows to bypass the check.

## Local synthetic verification

From the repository root with application dependencies and local PostgreSQL tools:

```sh
python scripts/test_postgres_install.py --pg-bin /absolute/path/to/pgsql/bin --http
```

The harness creates a new loopback-only cluster and synthetic test databases. It
does not use a production `.env` or shared DB. See [PostgreSQL evidence](../../../docs/POSTGRES_ACCEPTANCE.md)
and [backup/restore](../../../docs/BACKUP_RESTORE.md) for scope and remaining gates.

The legacy `prepare_candidate_demo.py` retains only pure test helpers; its CLI
refuses execution and cannot reset a schema. For optional synthetic customers in
an empty isolated Vorntek instance, see [demo initialization](../../../docs/DEMO_DATA.md).
It does not create accounts, overwrite customers or activate integrations.

Schema presence does not activate an integration. WhatsApp connection/sending
remains paused, and isolated environments must keep external delivery disabled.
The old host-specific deployment scripts and service wrappers are retired from
this distribution; see [legacy tool disposition](../../../docs/LEGACY_TOOLS.md).
