# Vorntek isolated deployment / 隔离部署验收

Observed 2026-09-13. The Vorntek revision now runs on the user-authorized isolated
Linux test instance; this is not a public GitHub release or a production migration.
The actual test address/private recovery locations remain in the operator handoff.

## Latest follow-up: sales flow, image review and repository creation

- [Deployed role/sales HTTP acceptance](review/DEPLOYED_ROLES_20260913.md) passed.
  Now 201 companies/eight inquiries/outbox zero; five test accounts disabled and
  admin credentials unchanged. Older 200-company counts below are prior snapshots.
- [Clean maintenance-layer and restore evidence](review/IMAGE_SECURITY_20260913.md)
  confirms removal of the inherited default test key. Real 70-table checkpoint
  restore and subsequent backup passed. Live website/CRM remain f7; the configured
  maintenance tag has not changed. Latest root regression: 42 passed.
- Public `rutther/vorntek` exists with only an initial README. Source upload,
  release/CI, browser disk delivery and clean-clone installation remain pending.

## Follow-up deployed revision — 2026-09-13

Active isolated source tree: `f7b78ef98b7c4bd1436b8ddecb5455260a62c138`.
The 32-file delta was applied to the verified source-only archive in a new release
directory. All 440 paths and Git blob hashes matched before private configuration
was copied separately. Delta: 66,507 bytes; SHA-256
`bb4a70b2a63ccea0e0e5f061c7f1a8aeca330aecbf0e76a361230f6b1fe38268`.

| Image | Local immutable image ID |
| --- | --- |
| `vorntek-crm:f7b78ef98b7c` | `sha256:b41928a8e37a7c9610e8cf88b265f9d57587841e95af1aeaec5d6f5e0b6bdf20` |
| `vorntek-website:f7b78ef98b7c` | `sha256:6e3e1e7ade098047d84ff6cfbe855bb02cc7d38046450ca0fdbba91d693ef729` |
| `vorntek-maintenance:f7b78ef98b7c` | `sha256:20ad54f37a711d3ff37df0b198648c5cf9063892cd9e09742b01ca047616bfa7` |

- Real credential-free PID1 tests: idle SIGTERM exit zero in 0.45s; active mocked
  job finished and exited zero in 3.28s, without a second job. Deployed real export
  service stopped with exit zero in 0.71s and restarted successfully.
- Stopped only isolated writers and made a fresh database/files/runtime/config
  checkpoint. Independent recovery matched all 70 table contents, 853 live
  columns, 1,156 constraints, 249 indexes, 17 triggers and 67 sequence positions.
  Initial comparison flagged physical ordinal gaps in `django_content_type`;
  pg_restore compacts a formerly dropped column slot. Comparing live-column order
  and every other column property resolved it; names/types/defaults were not ignored.
- A clearly synthetic vault value encrypted before backup decrypted in the
  restored database. Recovery into two new volumes verified zero asset files and
  four runtime files, including the XLSX. No restored services started. This is
  not real customer attachment or production-data recovery evidence.
- Existing Compose project, four live volumes and credentials preserved.
  Initializer exited zero; PostgreSQL/CRM/scheduler healthy; website/exports up.
  Unrelated shared-host containers remained up four weeks.
- All seven business areas match database rows and authenticated CRM detail pages.
  Two remaining areas passed real HTTP validation, persistence and replay checks.
  The harness initially compared mixed-case email literally; expected canonical
  lowercase was corrected and the existing row replayed, not created again.
  Final counts: 200 companies, eight inquiries (one legacy plus seven areas),
  zero outbox/enabled integrations. External sending remains off.
- Authenticated template and existing export downloads passed after upgrade:
  Vorntek filenames, unchanged XLSX hash matching its job. Public homepage, login,
  analytics, script and `/healthz/` returned 200. An initial `/admin/healthz/` probe
  was 404 because that is not the configured health route; it is not counted passed.
- Edge switched the deployed chart from 24 to 168 points; the real CSV link has
  169 rows including header and filename `vorntekSyntheticData168h.csv`. Clicking
  did not locate a file in standard Downloads: browser disk delivery remains unproven.
- Fresh distribution tests: 40 passed; JS: 23 passed. The 669-test full application
  result below applies to this code. Later report edits are not in frozen images.

Earlier sections below are historical. Still required: deployed role/sales/UI
acceptance, browser disk delivery, final source/history/image/license review,
GitHub release/CI and clean-clone installation. Real-data restoration remains
unauthorized; this fictional demonstration does not silently satisfy that gate.

## Initial Vorntek version (superseded)

- Frozen source tree: `11b1d7ebb591227e9bcc2411f525543d258738e6`.
- Source-only archive: 28,180,941 bytes, SHA-256
  `6f60c0d3ce7c69153c1d2c15c690d4a293e1e80a57ae9ee0a2e2ee2f777b83a0`.
- CRM image: `vorntek-crm:11b1d7ebb591`, ID
  `sha256:7fb843450e9b01b93d334b11eeff1889394f02526beb6c99866e5692bb4c5720`.
- Website image: `vorntek-website:11b1d7ebb591`, ID
  `sha256:b30f02c1cc589d82a52fadf2efd3266340709c0a1cc4bb76bbc547b7314a0a22`.
- Maintenance image: `vorntek-maintenance:11b1d7ebb591`, ID
  `sha256:e3a6ab1d1e4729516cfc8530baaacb791750686f06384ba20d1cf1f91118523e`.

Images were built from the checksum-verified Git-index archive, without private
configuration or runtime files in the build context. This does not replace the
remaining final image-layer/security/license audit. Later acceptance/status edits
are documentation only; they are not claimed to be in this frozen source archive.

## Upgrade and recovery evidence

- Verified original target, Compose project and persistent volume names. Kept
  project/volumes, database, secrets and accounts; copied private configuration
  separately and changed only the three application image tags.
- Stopped this instance's writers. Created a new PostgreSQL18 backup and restored
  into a separate empty verification database. All 70 table row counts and sorted
  row-content hashes matched. Schema comparison matched 853 columns, 1,156
  constraints, 249 indexes, 17 triggers and 67 sequences. PG18's constraint count
  is not the earlier PG17 report's count; comparison was between the two PG18 DBs.
- Snapshotted the file volume (zero files) and runtime volume (two private files),
  plus separate secret/configuration recovery material. Snapshot creation is not
  complete file-restoration or vault-decryption proof for this new checkpoint.
- Applied the unchanged canonical chain: 27 recorded, zero pending; 58 models/
  799 fields matched. Bootstrap added missing configuration without overwriting.
- Row-locked, guarded metadata update changed only the expected demo site name
  and form name/category to Vorntek/industrial. Preserved the original synthetic
  inquiry and accounts; no real records were copied.
- Ran the guarded 200-customer initializer. Rebuilt only generated CRM static
  assets with `collectstatic --clear`; old generated assets are recoverable from
  the previous application image, not user-owned uploads.
- Recreated services with the same volumes. PostgreSQL/CRM/scheduler health checks
  passed, exports/website remained running, initializer exited zero. Existing
  unrelated shared-host containers remained running without restart.

## Actual application verification

- External HTTP fetched all 15 page routes plus 12 other website assets. All 27
  responses were 200 and matched source bytes (normalized Git text line endings).
- The public Chinese homepage was visually inspected with actual AI imagery.
- Actual browser industrial-analytics inquiry displayed success. Exactly one
  matching row existed, and an authenticated CRM HTTP detail page contained its
  synthetic address and application context. Credentials were used only over
  the private container network and were not printed or sent via public HTTP.
- Existing admin login and customer-pool HTML passed. Template endpoint returned
  a 7,518-byte XLSX-format response; this does not prove browser file delivery.
- Final counts: 200 companies, 200 pool states, 200 contact points, 200 sources,
  two inquiries (one preserved, one browser test), zero outbox/enabled integrations.
  Global external I/O and live WhatsApp remain off.

## Still required

Follow-up before the next deployment: queued export job 1 was processed by the
running worker, downloaded over authenticated private-network HTTP and parsed.
Its XLSX contains 200 matching company rows, 200 matching source rows, and excludes
all 200 restricted/do-not-contact addresses. File size 28,370 bytes; SHA-256
`edd3c8bd9f2ef7f04ab1c51586fc2fc85713a8438ab5607585dba81b4671abd4` matches the job.
This is real file-content evidence, not browser disk-delivery evidence.

The graceful-shutdown fix is implemented locally: finish the current job, stop
before another claim, interrupt idle waiting, restore prior signal handlers and
allow a 60-second Compose grace period. Seven new tests plus export regressions
passed (36 tests); complete application rerun 669 tests/481.471s: 662 passed,
seven skipped, no failures. Template follow-up 27 passed; distribution 40 passed.
The analytics CSV now uses a real download link for the selected dataset instead
of a detached timed-Blob click; all 23 JS contracts passed, including exact CSV/
chart equivalence for 24 and 168 samples. Actual container stop and new version
deployment are the next checks, not established by these local results.

- Complete this deployed revision's seven-area HTTP/business/role/export/browser
  and restart/recovery checks; local PG17/full SQLite tests are separate evidence.
- During the old export worker stop, Docker needed its timeout and reported exit
  137. Source inspection found no SIGTERM handler for its watch loop. No export
  job was active in this demo, but graceful shutdown needs correction and testing.
- Real browser CSV/XLSX delivery, fresh checkpoint file restore/vault verification,
  final public tree/history/image review, GitHub repository/CI/release/clean clone.
- Public HTTP remains synthetic-only; HTTPS/real-data recovery/production switch
  and real Meta deduplication are not established by this deployment.

Rollback must preserve inquiries created after the checkpoint. Do not restore the
pre-upgrade database over the running one. Pause only this instance if necessary;
retain current data and assess old-code/industrial-form compatibility before any
code/metadata rollback. The pre-upgrade recovery proof is not a proven no-data-loss
rollback of later submissions. The overall goal remains unfinished.
