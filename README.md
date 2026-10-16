# Vorntek

A self-hosted website and business-management system for industrial B2B teams, with
PostgreSQL, Docker Compose, configurable inquiries, CRM, marketing workflows,
content delivery, roles and recovery tooling.

[中文说明](README.zh-CN.md)

The replaceable industrial company example includes six equipment concepts,
application pages, engineering guides, product models and inquiry forms. The
application supports configurable business fields rather than fixed industry
categories. See [release scope](docs/RELEASE_SCOPE.md) and
[current verification status](docs/IMPLEMENTATION_STATUS.md).

The public [v0.1.0-rc.1](https://github.com/rutther/vorntek/releases/tag/v0.1.0-rc.1),
[release CI](https://github.com/rutther/vorntek/actions/runs/34712023363) and
[earlier delivery evidence](docs/PUBLIC_DELIVERY.md) describe the earlier release,
not this source version or any current server state. The current source version is
recorded in [`VERSION`](VERSION) and returned by `/healthz/`. Immutable downloads,
checksums and run evidence are published on the matching GitHub Release; the
[verification status](docs/IMPLEMENTATION_STATUS.md) separates local, CI and
post-download evidence.

## Included

- English/Chinese website with configurable example content and catalogue entries;
  no fixed industry or seven-category requirement.
- Shared inquiry handling with configurable fields, requirements and backend
  validation. Personal, organization and support scenarios use real CRM storage.
- Staff accounts/roles, leads, customer pool, assignments, tasks, opportunities,
  import templates, strict 21-column customer-pool CSV round trips and background
  XLSX exports.
- Canonical SQL installation, persistent database/files, health checks, guarded
  backup/restore and a default-paused integration scheduler.
- Platform connections, acquisition plans, multiple workflow instances and Pixels,
  flow diagrams, step illustrations, configuration and separate runtime evidence.
- Private article previews, frozen website candidates, explicit deployment and
  website-pointer recovery independent of draft content edits.
- Optional measurement contracts. Meta, Google advertising measurement, email and
  WhatsApp are disabled by default. Real Meta deduplication is not verified here.

Vorntek content and optional business examples are **fictional and synthetic**,
not a real company history, customer portfolio, product certification or fixed
industry model. Installation does not require importing example business data.
Examples are editable records, not hard-coded success states or live equipment data.

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

Open `http://localhost:8088/` for the industrial website and
`http://localhost:8088/admin/` for the empty business workspace. There is **no default
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

For an optional industrial workspace, run the plan first, then explicitly import
using the administrator you created (replace `YOUR_ADMIN`):

```sh
docker compose exec crm python manage.py seed_industrial_examples
docker compose exec crm python manage.py seed_industrial_examples --apply --synthetic-only --owner YOUR_ADMIN
```

This adds actual media, six disabled 3D profiles and English/Chinese/Arabic
engineering article drafts. It does not publish articles, enable integrations or
replace business records. Follow [the industrial example guide](docs/INDUSTRIAL_EXAMPLE.md)
for the normal preview, selection and deployment steps. Optional cross-module
business examples are separate from this company content.

Use the optional, independently versioned [synthetic example packs](docs/EXAMPLE_DATA.md)
for coherent inquiries, companies, contacts, opportunities, tasks, workflow
instances, content and files. The default plan commands do not write data:
`docker compose exec crm python manage.py seed_example_bundle` and
`docker compose exec crm python manage.py seed_content_examples`.
Explicit import requires a synthetic target and an existing administrator. Repeat
import preserves later edits; bounded reset is not a purge. The older
[200-customer demo](docs/DEMO_DATA.md) is historical/optional, not a product default.
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

New installations create a `general` inquiry form. The historical form code
`project-inquiry` is a compatibility identifier, not an industry requirement.
Bootstrap only creates missing configuration, not silently converts existing forms.
Configure existing field requirements explicitly through the form editor. See the
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

For a TLS edge in front of the website container, use the explicit, verified-peer
[HTTPS proxy procedure](docs/HTTPS_PROXY.md). The default HTTP configuration does
not trust incoming forwarding headers; enabling secure cookies alone is not a
complete two-proxy deployment.

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
The optional example catalogue is industry-neutral and independent of legacy
backend business identifiers. It supports arbitrary entry counts and IDs, including
an empty catalogue. `--catalog PATH --output DIRECTORY` builds a separate complete
static site; generation does not delete other destination files. See the current
[example content notes](docs/vorntekDemo/README.md).

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
The published source and its initial history have been checked; this candidate's
release checks are tracked separately. Prebuilt container images are not part of
this source distribution. No real
customers, credentials, original-company photos or private backups belong in the
public project.

Project code: [MIT](LICENSE). Third-party components retain their own terms:
[notices](THIRD_PARTY_NOTICES.md). AI prompts and hashes are in the
[demo pack](docs/vorntekDemo/README.md). This is not trademark clearance or a claim
of exclusive rights over AI-generated outputs.
