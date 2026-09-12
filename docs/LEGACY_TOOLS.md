# Retired old-host deployment tools

The independent distribution uses root `compose.yaml`, the guarded database
initializer, and the recovery procedures in [BACKUP_RESTORE.md](BACKUP_RESTORE.md).
It does not use the previous host's fixed directories, container names or service
installation scripts.

The following files were retired from this candidate on 2026-09-12:

- `apps/crm/scripts/build-production-release.ps1`
- `apps/crm/scripts/install-production-release.sh`
- `apps/crm/scripts/install-customer-export-worker.sh`
- `apps/crm/deploy/siteos-customer-export-worker`
- `apps/crm/deploy/siteos-customer-export-worker.service`
- `apps/crm/leads/test_production_release_scripts.py`

The last file contained 13 contracts for those old-host tools, not application
business tests. These contracts do not establish that the replacement container
deployment works. See [CI.md](CI.md) for replacement checks and their pending
runtime acceptance. No existing SQL migration or business test was removed.

All six local files were moved, with matching before/after SHA-256 hashes, into
the ignored `.runtime/retiredLegacy20260912/` private archive with their relative
paths preserved. They are also unchanged in the original project. The archive
is not required to build or test this distribution and is not published.

`prepare_candidate_demo.py` now contains only existing pure synthetic-test helpers
and a fail-closed CLI. Its schema-reset, old-company seed and messaging fixtures
were retired on 2026-09-13 after an identical private backup was verified. The
original project is unchanged. The safe opt-in replacement is documented in
[DEMO_DATA.md](DEMO_DATA.md); it never resets or overwrites customer data.

## Historical content rewrite/seeding scripts

Five additional scripts were removed from the public index and moved to the
ignored `.runtime/retiredContent20260912/`, preserving their relative paths and
matching SHA-256 bytes. The original repository remains unchanged:

| Script under `apps/crm/scripts/` | Observed behavior, not executed |
| --- | --- |
| `enrich_product_page_sources.py` | Rewrites old product JSON with embedded demo copy. |
| `refresh_benchmark_free_pages.py` | Overwrites selected old page JSON with embedded copy. |
| `refresh_products_from_newamstar.py` | Fetches third-party product pages, extracts text/images, rewrites old product JSON and clears its source label. |
| `seed_newamstar_articles.py` | Initializes Django at import and overwrites matching published articles with embedded English/Arabic copy and third-party image links. |
| `upgrade_demo_content_v2.py` | Rewrites old home/category/product JSON, including third-party image URLs. |

Their `parents[3]` root resolves to `apps/` in this distribution. The expected
`apps/content/pages` and `apps/apps/admin` directories do not exist. Before moving
the files, literal source/reference searches found no callers elsewhere in the
candidate; root Compose initializes with `bootstrap_site`, not these scripts.
None was imported or executed during the audit. No website page, customer data,
database migration, content-management API or business test was removed.

The release-input test now checks their absence, ignore rules, Git exclusion and
absence of named Python callers. These static checks are not proof against every
possible dynamically constructed reference. Targeted application checks provide
additional evidence; see implementation status for their actual results.

A tracked website/CRM text search for `newamstar` returned no remaining matches
after retirement. This narrow negative result does not prove that existing text
or images are original or licensed. The 123 raster assets and all content rights
still require verification before public release. Moving these tools does not
remove or authorize previously generated content.
