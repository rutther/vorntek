# Vorntek

A self-hosted industrial-company website and CRM, with PostgreSQL, Docker Compose,
inquiry capture, customer management, roles, import/export and recovery tooling.

[中文说明](README.zh-CN.md)

**Public pre-release — not a production-readiness certification.** Vorntek now runs on the
authorized isolated test instance with 200 seeded fictional customers plus one
synthetic customer converted during sales acceptance. Initial public
website/inquiry checks passed; full release acceptance is still in progress.
See [deployment evidence](docs/VORNTEK_DEPLOYMENT.md) and [current status](docs/IMPLEMENTATION_STATUS.md).

The complete source and [v0.1.0-rc.1](https://github.com/rutther/vorntek/releases/tag/v0.1.0-rc.1)
are public. Both [release CI jobs](https://github.com/rutther/vorntek/actions/runs/34712023363)
passed; independent clone installation, inquiry/login and restart checks passed.
See [delivery evidence and remaining limits](docs/PUBLIC_DELIVERY.md).
The current development version is recorded in [`VERSION`](VERSION) and returned
by `/healthz/`; it is not a release until the acceptance ledger is complete.

## Included

- English/Chinese website: 15 pages, seven industrial business lines, an original
  AI logo and AI-generated product/workshop concepts.
- One inquiry controller for homepage and contact page, preserving business area,
  requirements and application context in the CRM.
- Staff accounts/roles, leads, customer pool, assignments, tasks, opportunities,
  import templates, strict 21-column customer-pool CSV round trips and background
  XLSX exports.
- Canonical SQL installation, persistent database/files, health checks, guarded
  backup/restore and a default-paused integration scheduler.
- Optional measurement contracts. Meta, Google advertising measurement, email and
  WhatsApp are disabled by default. Real Meta deduplication is not verified here.

Vorntek is a **fictional Chinese company** covering circulating-water control,
liquid control, water-quality sensors, gas sensors, compact propane heaters, small
turbojet engines and industrial analytics. The chart/CSV uses deterministic
synthetic data, not live equipment. AI images are exterior concepts, not engineering
drawings, certified products, tested specifications or safety assurances.

## Isolated quick start

Requirements: Python 3; Docker Engine with Docker Compose and Linux containers.
Use a fresh checkout and independent environment, never a production database.

```sh
python3 scripts/build_vorntek_site.py
python3 scripts/configure.py
docker compose build
docker compose up -d
docker compose ps -a
docker compose exec crm python manage.py createsuperuser
```

Open `http://localhost:8088/` and `http://localhost:8088/admin/`. There is **no default
administrator password**. Initialization checks/applies the SQL chain, creates
minimum site/role/form configuration and collects CRM assets. Base Compose binds
to loopback and restricts application-network egress.

Use fictional data. Public HTTP does not protect passwords or form data. Before
real use, configure HTTPS, secure cookies, origins, access controls, retention and
backups. Do not simply expose the example to the internet.

If Docker's build network cannot resolve package hosts, use the documented,
build-only [DNS troubleshooting procedure](docs/BUILD_NETWORK.md); do not weaken
the application network or change unrelated host services.

## Configuration and upgrades

For an optional 200-customer fictional dataset, preview with
`docker compose exec crm python manage.py seed_vorntek_demo`.
The guarded opt-in write and its limits are in [demo data](docs/DEMO_DATA.md).
Never use the old schema-reset helper.

`scripts/configure.py` creates `.env` and four unique file-backed secrets in
`.secrets/`; it refuses to overwrite configuration. Keep secrets out of Git,
screenshots and public artifacts. On Windows restrict directory ACLs to your
account/container runtime. Preserve the vault key with controlled recovery
material: a database dump alone cannot fully recover the system.

Fresh Compose projects/images use Vorntek names. Existing `NEWCROWN_*` settings,
`newcrown` database/role identifiers and `siteos_demo` remain **technical compatibility
identifiers**, not customer branding. Never rename an existing Compose project or
reset volumes to change branding: that can select a different database.

The industrial form category is `industrial`. Bootstrap only creates missing
configuration, not silently converts existing forms. See the
[rollout checklist](docs/VORNTEK_ROLLOUT.md). Applied SQL is not rewritten. Partial
legacy migration ledgers are refused; never delete ledger rows or run old demo
reset helpers to bypass this guard.

Before updating an existing installation, follow the bilingual
[upgrade runbook](docs/UPGRADE.md). `python manage.py release_preflight --strict`
is read-only and must pass with outbound writers paused before migrations run.

Private article previews require `NEWCROWN_ARTICLE_PUBLIC_ORIGIN` to be the
installation's real HTTPS public origin. The example value is intentionally
non-live. Preview is authenticated, never activates the public site, and its
remaining asset/version limits are documented in the
[article delivery architecture](docs/ARTICLE_DELIVERY.md).

## Development and verification

```sh
python3 -m pip install -r apps/crm/requirements.lock -r requirements-dev.txt
python3 -m unittest discover -s tests -v
node --test scripts/test_measurement.mjs scripts/test_form_status.mjs scripts/test_credential_receipt.mjs scripts/test_vorntek_form.mjs
python3 scripts/run_application_tests.py
```

Node is needed only for JS tests. The application runner uses synthetic SQLite
and blocks non-loopback Python sockets; it does not replace PostgreSQL/browser
acceptance. With separately installed PostgreSQL binaries, run
`python3 scripts/test_postgres_install.py --pg-bin /path/to/bin --http`.

Website source: [catalogue](docs/vorntekDemo/catalog.json) and
`scripts/build_vorntek_site.py`. Regenerate `apps/website/` after changing scripts
or styles to update content-hash cache keys.

## Operations and license

- [Configuration](docs/CONFIGURATION.md): origins, public settings, file-backed
  secrets, HTTPS and default-disabled external channels.
- [Administration](docs/ADMINISTRATION.md): startup, accounts, roles, business
  operation, workers and safe stopping.
- [Development](docs/DEVELOPMENT.md) and
  [troubleshooting](docs/TROUBLESHOOTING.md): reproducible checks and fail-closed diagnosis.
- [Backup/restore](docs/BACKUP_RESTORE.md): database, file volumes and secrets belong
  together; stop writers and pause external queues during recovery.
- [Background tasks](docs/BACKGROUND_TASKS.md): export worker versus integration scheduler.
- [Account security](docs/ACCOUNT_SECURITY.md), [security](SECURITY.md),
  [contributing](CONTRIBUTING.md), [CI](docs/CI.md).

`docker compose down -v` destroys persistent volumes; it is **not an upgrade command**.
The published source and its initial history have been checked; binary-image
redistribution and final release acceptance remain open. No real
customers, credentials, original-company photos or private backups belong in the
public project.

Project code: [MIT](LICENSE). Third-party components retain their own terms:
[notices](THIRD_PARTY_NOTICES.md). AI prompts and hashes are in the
[demo pack](docs/vorntekDemo/README.md). This is not trademark clearance or a claim
of exclusive rights over AI-generated outputs.
