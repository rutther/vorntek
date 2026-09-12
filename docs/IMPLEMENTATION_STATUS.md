# Implementation status / 实现状态

2026-09-13 — development candidate, not a public release.

## Latest: complete source published; first CI finding under correction

- Public commit `a13278a9b55189735415bd60e2ab726b6b864015`, tree
  `ed7c23eee2e872e20c121a6c0824f9c4f639b7a8`, contains all 443 reviewed files.
  A fresh anonymous server clone matched both commit and tree; full two-commit
  history scan returned only the same nine manually reviewed non-secret alerts.
- GitHub application CI passed: 669 tests, two skips, plus 42 root tests and JS.
  PostgreSQL CI failed before startup. A reproduced POSIX socket-directory issue
  is corrected locally; 44 root tests pass. See [CI](CI.md) for exact limits.
- Fresh-clone generation/configuration leaves tracked sources unchanged. Default
  Docker build failed on this host's build-network DNS; build-only host-network
  fallback is being verified. This is not yet successful clean-install acceptance.
- No release version or registry images published. Live f7 instance and original
  production remain unchanged. Earlier source-upload-pending notes are history.

## Latest: deployed sales acceptance and maintenance-layer remediation

- Public repository [rutther/vorntek](https://github.com/rutther/vorntek) now exists
  with only the initial README, commit `04d9e730c1c54c403b311cfe1b286ada1516973d`.
  Verified via GitHub and `git ls-remote`; application sources are not uploaded.
- Fresh root regression including Linux inventory coverage: 42 tests passed.

- [Real deployed role/sales HTTP checks](review/DEPLOYED_ROLES_20260913.md) passed.
  There are now 201 synthetic companies/eight inquiries/outbox zero. Five test
  accounts are disabled; admin credentials and the original 200 demo rows remain.
- [Image-layer review and clean maintenance recovery](review/IMAGE_SECURITY_20260913.md)
  found and removed an inherited default test private key from the final candidate
  image, including its layer history. Actual new-database restore and backup passed.
  Running website/CRM are still f7; the clean maintenance tag is not yet configured.
- Actual Linux license evidence for the 24 locked Python distributions is now
  retained. This does not approve redistribution of all OS/binary dependencies.
- Final source/history review, browser disk delivery, public CI/release and
  clean-clone installation remain incomplete. These new results supersede the
  pending role/sales and 200-company counts in older sections below.

## Latest: export fix deployed and fresh checkpoint restored

- Active tree `f7b78ef98b7c4bd1436b8ddecb5455260a62c138`: 440 source files verified,
  three images built, existing project/volumes/credentials preserved. Real export
  service stops with exit zero and restarts.
- Fresh recovery matched 70 table contents/schema/sequence positions; synthetic
  vault decrypts; zero asset files and four runtime files restored independently.
- Seven business areas match CRM details. Existing XLSX survives upgrade/hash
  check; template/export filenames use Vorntek. 200 companies, eight inquiries,
  zero outbox; external channels off. Public routes and Edge chart/CSV content pass.
- Distribution 40 and JS 23 passed. Browser disk delivery, remaining deployed
  role/sales/UI and final release gates are incomplete. GitHub is unpublished.
  See [exact versions and evidence](VORNTEK_DEPLOYMENT.md). Earlier entries are history.

## Latest: Vorntek deployed to the authorized isolated instance

- Frozen code tree `11b1d7ebb591227e9bcc2411f525543d258738e6`, three images built,
  private configuration and existing volumes/accounts preserved. Guarded brand/
  industrial-form metadata update and 200-customer initialization completed.
- Pre-upgrade PG18 checkpoint restored independently; all 70 table fingerprints,
  schema and sequence state matched. File/runtime/config snapshots retained.
- Public 27 website files/routes byte-matched. Actual browser inquiry matched the
  database and authenticated CRM page. Admin login/pool/template endpoint passed.
  Final company/source/contact/pool counts 200 each, inquiries two, outbox zero.
- [Deployment evidence and limits](VORNTEK_DEPLOYMENT.md): full deployed business/
  export/recovery/role/browser acceptance, export-worker graceful stop, final
  source/image review and public GitHub/CI/clean-clone release remain incomplete.
  Prior no-server-write/local-only entries below describe earlier phase boundaries.

## Latest: safe fictional customer initialization and release-input cleanup

- Replaced the legacy schema-reset seeder with a refusing CLI/pure-test-helper
  shim after a byte-identical private backup. Added optional plan-first,
  transactional 200-customer initialization: seven business areas, five source
  labels, reserved invalid contacts, no accounts or external messages.
- Local PostgreSQL17.11: 27 aggregate lifecycle/HTTP/seed checks passed; final-row
  seed failure rolled back all rows; repeat refused without duplication. HTTP and
  cluster stopped. Targeted tests: 26 and 108 passed; distribution checks: 40 passed.
  Full application rerun: 662 tests/454.639s, 655 passed, seven environment skips,
  zero failures; test database destroyed normally. Node: 22 passed.
- Replaced inherited company email/domain and dataset identifiers in mock tests;
  removed private developer-machine paths from public reports. Source provenance
  and public-release review now distinguish current Vorntek from old snapshots.
- Read-only new-server check still shows the earlier healthy isolated release,
  zero companies, one inquiry, zero outbox/enabled integrations; no server writes.
  See [demo commands](DEMO_DATA.md) and [acceptance](VORNTEK_ACCEPTANCE.md).

## Latest: Vorntek implementation and local business acceptance

- New bilingual 15-page website, seven industrial inquiry categories, eight AI
  assets, CRM brand/context display and synthetic analytics are implemented.
  Old website/mark are privately archived, not deleted from original sources.
- Current full application suite: 656 tests in 459.444s, 649 passed, 7 skipped,
  zero failures. Node: 22 passed. Distribution/archive/website: 40 passed.
- Fresh local PostgreSQL17.11: 23 lifecycle/HTTP checks passed, including seven
  product categories, database/CRM matching, assignment/qualification/conversion
  and recovery safeguards. Actual browser inquiry matched once; final synthetic
  tally nine inquiries, seven business areas, zero outbox. Cluster/server stopped.
- Both README files and MIT are prepared. Edge confirmed GitHub account `rutther`;
  intended `rutther/vorntek` is not yet a created/published repository.
- [Detailed local evidence](VORNTEK_ACCEPTANCE.md) records scope and remaining
  gaps: CSV download delivery, legacy-public-content review, new-server rebrand,
  PG18/container/recovery, public GitHub/CI/clean-clone install. No deployment,
  real-data copy, Meta/advertising/WhatsApp action occurred in this stage.
- Entries below are historical and do not override this current state.

## Latest: Vorntek fictional-company creative pack — 2026-09-12

- User selected Vorntek and seven lines spanning fluid control, water/gas sensors,
  compact propane heating, small turbojet exterior concepts and industrial analytics.
  Added [first-phase brand/content pack](vorntekDemo/README.md), one AI logo and seven
  newly generated product/scene images, structured catalogue and generation manifests.
- This is a local creative/content stage, not a deployed rebrand or an implemented
  analytics product. No application/database/server change occurred. Future work
  separates company content from software, replaces legacy industry-specific forms
  and verifies the complete website/CRM before an explicitly scoped deployment.

## Latest: explicit IPv4 test-access request — 2026-09-12

- The user requested direct IPv4 access without a domain. A private server-only
  override now publishes the isolated website port; exact host/origin and site
  URL settings were updated without changing source defaults or rebuilding images.
- External read-only checks passed homepage/contact/admin-login/health routes;
  environment files and private backup paths are not served. Database stays private,
  synthetic data is preserved, marketing/email/WhatsApp sending remains disabled.
- This is temporary unencrypted HTTP test access, not production security or
  customer-data restoration acceptance. The user was warned not to use real data
  or reused passwords. Prior loopback-only deployment notes below are historical;
  the distributed Compose default still stays loopback-only. No public GitHub release.

## Latest: authorized isolated Linux deployment passed — 2026-09-12

- Website, CRM and independent PostgreSQL18 now run in an isolated Linux Compose
  project through a loopback-only SSH-tunnel entrance. Existing server workloads
  remain untouched. No production data, domain change or external sending.
- Actual fresh install uncovered and fixed application-password newline handling
  in the role initializer. Fresh independent PG18 login proved the corrected SQL;
  all 27 canonical business migrations are unchanged. Build-only host networking
  worked around this host's bridge DNS issue without global Docker/firewall changes.
- Actual PG18 schema (70 public tables, 58 models/799 fields), synthetic inquiry
  rejection/acceptance/deduplication, admin session login and service restart passed.
  One synthetic inquiry remains, no outbound event rows. Scheduler is healthy but
  scheduled external operations remain paused.
- Synthetic database restore matched source row counts/content hashes in all 70
  tables; runtime snapshot restored two files into a separate empty volume with
  verification. Not a production/complete attachment/vault recovery certificate.
- Full Linux source-only SQLite suite: 650 tests in 1142.033s, 648 passed, two
  skipped, zero failures. Current local packaging checks: 36 passed; Node: 14 passed.
  The deployed application image uses the frozen source tree; the host SQL fix and
  new inquiry verifier are explicitly recorded overlays, not an unrecorded release.
- [Container evidence](CONTAINER_ACCEPTANCE.md) supersedes earlier statements that
  no server deployment or PG18/Compose acceptance had occurred. Prior entries below
  are historical. Full server business/UI acceptance, real-data restore, production
  HTTPS, content rights/license, public GitHub/CI and independent install still
  remain; the overall goal is not complete.

## Latest: live transport isolation gap closed — 2026-09-12

- Source review found that paused WhatsApp send configuration did not protect
  direct live clients, template reads or media downloads with copied live rows.
  Added global external-I/O refusal before credential loading and at transport
  boundaries for Graph/YCloud. Existing send/DEBUG policy and mock behavior remain.
  This is isolation hardening, not activation of WhatsApp.
- Added seven boundary tests for constructors, cached clients, live factories,
  template helpers, mocks, Google OAuth/HTTP/polling and Meta retrieval/diagnostics.
  Final targeted run: 74 tests passed in 6.785s, zero skips/failures; database
  destroyed normally, Django checks clean. No real platform calls were made.
- See [external-I/O scope](EXTERNAL_IO_BOUNDARY.md). DNS/subprocess/browser resource
  isolation and actual container networking are not proven by these tests. All
  writers/workers still must stop for private restoration. Full application
  regression predates this patch; final release regression remains required.
- No production source, server, Meta asset, advertising or external account was
  changed. New-server deployment, private restore and public release remain gated.

## Latest: historical content tooling retired — 2026-09-12

- Reviewed five historical content rewrite/seeding helpers without executing or
  importing them. They target absent legacy directories, have no named callers
  in the candidate search, and are not part of Compose initialization. Two embed
  third-party fetch/article replacement behavior. All five were moved to a private
  ignored archive with matching hashes and excluded from Git/container context.
  Original repository files and existing website content were not changed.
- 35 unit/distribution/release-input checks passed in 0.861s. Targeted packaging,
  content-role/access and release-security regression: 47 tests in 17.390s,
  44 passed, three skipped, zero failures. The previous full 643-test run predates
  this retirement and was not rerun. No business test was retired in this step.
- See [legacy disposition](LEGACY_TOOLS.md). A narrow tracked-source search found
  no remaining literal third-party source name; it does not establish content
  originality or license. 123 image assets, final source/artifact review and
  publishing rights remain open. No server/GitHub write or private-data transfer.

## Latest: independent source-only regression — 2026-09-12

- Retired six old-host deployment/service/test files to a private ignored archive;
  byte hashes matched and original repository files remain untouched. Their 13
  tool-specific tests retired with them; no business tests or SQL were removed.
- Exported only staged source to a separate temporary directory and verified an
  identical Git tree before testing. No private runtime or hidden application
  configuration was copied. See [source-only evidence](SOURCE_ONLY_ACCEPTANCE.md).
- From that copy: 34 unit/distribution/recovery checks and 14 Node tests passed.
  Full synthetic application regression: 643 tests in 447.355s, 636 passed,
  seven skipped, zero failures; Django checks clean. Dependencies were reused
  from the local interpreter, so this is not fresh Linux/Compose acceptance.
- Updated the DB guide to the supported 27-migration installer and documented
  [legacy tool disposition](LEGACY_TOOLS.md). Server deployment, private-data
  restoration, final content rights and public release remain pending. No server
  or GitHub write occurred.

## Latest: partial public-content and license review — 2026-09-12

- Removed two unverified QA images and their comparison page from the public
  index, not from disk; original sources remain intact. Added ignore coverage.
- Added three missing upstream license notices and verified relevant package
  integrity/file hashes. Recorded 24 Python dependencies and installed license
  hashes. See [third-party notices](../THIRD_PARTY_NOTICES.md).
- 33 unit/distribution/release-input tests passed. Business UI/code was unchanged;
  no need to reinterpret the preceding 656-test regression as a new run.
- 123 remaining raster assets, project license, legacy helpers, final source and
  image/history review still gate public release. No server/GitHub mutation or
  real-data transfer occurred. Downloaded audit packages stay in ignored runtime.

## Latest: complete regression and CI source — 2026-09-12

- Added a synthetic SQLite suite runner that refuses an application `.env`,
  resets application/database connection variables and blocks non-loopback Python
  sockets. Full current suite: 656 tests in 449.361s, 649 passed, seven skipped,
  zero failures; Django checks found no issues. This supersedes the earlier full
  regression for application changes through portable exports.
- PostgreSQL17 lifecycle with HTTP reran: 23 aggregate checks passed, including
  the new read-only smoke CLI against the real local test server. Its cluster and
  HTTP process stopped. No PostgreSQL18/container or real-data claim follows.
- Prepared pinned, read-only GitHub CI for application regression and PG18/
  Compose build/smoke, plus SECURITY.md and CONTRIBUTING.md. No remote CI run,
  repository creation, artifact/image upload or server change occurred.
- 29 recovery/configuration/CI unit tests and 14 Node tests passed. `pip check`
  found no conflicts in the existing local test interpreter; a fresh Linux
  dependency install is still to be verified. See [CI evidence and limits](CI.md).

## Latest: file snapshots and portable exports — 2026-09-12

- Added private content-addressed file snapshots and guarded recovery with full
  byte/hash verification, empty-directory preservation, explicit volume identity,
  no overwrite, unsafe/link/colliding-path refusal and incomplete-restore marker.
  File backup/restore profiles have no network, credentials or automatic startup.
- New customer-export records now use private-root-relative paths. Existing
  absolute references remain valid only inside the current root; no guessed
  old-server remapping or production data update was performed.
- 25 unit/distribution/filesystem tests passed, including actual snapshot CLI
  execution; 29 customer-pool/path tests passed in 41.319s, including an actual
  generated XLSX download after moving its root without changing the DB record.
- See [file recovery evidence](FILE_RECOVERY_ACCEPTANCE.md). Real Docker mounts,
  combined production snapshot consistency, old absolute-path mapping and full
  private-data recovery remain pending. No server or GitHub changes occurred.

## Latest: operational database archives — 2026-09-12

- Added plan-by-default backup/verify/restore CLI: explicit database identity,
  non-superuser role, private no-overwrite archive and integrity manifest, empty
  target/session/version/lock guards and single-transaction restore. No automatic
  schema adoption, service start, queue release or file/secret copying.
- Added an opt-in maintenance image/profile with only the app DB credential,
  private network and explicitly provisioned backup directory. Actual image build
  and Compose execution remain pending; source configuration tests alone do not
  prove the container works.
- Local PostgreSQL17 lifecycle now passed 20 checks, including real utility
  archive/restore of 70 tables and synthetic vault decryption, corrupt/nonempty/
  active-session rejection, and an actual pg_restore failure rolling back all
  created objects and rows. The final temporary cluster was stopped.
- 13 unit/distribution tests passed. See [backup/restore](BACKUP_RESTORE.md).
  Full file-volume recovery, legacy-ledger adoption, PostgreSQL18/container,
  new-server deployment and private-data transfer are still unaccepted.

## Latest: account receipt lifecycle — 2026-09-12

- Verified existing account hashing, one-time create/reset receipt, no-store
  response, audit redaction, reset/session invalidation and administrative guards.
  Fixed the receipt script retaining copyable password state after page exit;
  dismissal/pagehide now remove the receipt and disable retained copy handlers.
- 22 account-governance tests passed in 26.934s; five new receipt VM tests and
  nine existing form/measurement tests passed. No real-browser BFCache claim.
  See [account security](ACCOUNT_SECURITY.md). Production accounts were not changed.

## Initial packaging evidence (later sections supersede pending items)

- Source baseline: CRM commit `d44dfdb83da45eed69703ef8eba3fee07d877be4`; 180 live website files captured and hash-verified before edits. No production database or credentials included.
- Existing UI and business code retained. Website measurement is being converted from an embedded production Pixel ID to explicit same-origin configuration.
- Added: verified SQL installer with partial-ledger refusal, minimum site bootstrap, file-backed secrets, Compose candidate, persistent stores and English/Chinese README drafts.
- Nine packaging unit tests, six distribution/configuration tests and five JavaScript VM contract tests passed. The VM uses synthetic configuration and makes no real platform requests. A new isolated PostgreSQL 17.11 lifecycle run passed 12 checks, including installation, 0026-to-0027 upgrade, rollback, repeat initialization and backup/restore. See [PostgreSQL acceptance](POSTGRES_ACCEPTANCE.md). PostgreSQL 18/container, PostgreSQL business regression and actual browser acceptance remain pending.
- Fresh complete SQLite compatibility regression passed: 643 tests in 461.829 seconds, 636 passed and seven skipped, zero failures. Django system checks reported no issues. The run uses mocked external transports with the test setting enabled, not real external acceptance. This supersedes the initial 641-test run with one outdated exact-response assertion; that assertion was fixed before this fresh run. Production/default external sending remains disabled.
- The inherited website release-manifest was removed from the distribution because it no longer describes the edited candidate; its original is retained in the private workspace source snapshot. New release hashes must be generated from the final build.
- Public release remains blocked by incomplete acceptance, asset/license review and unconfirmed publishing account/repository/license. This is not an assertion that every staged asset is redistributable.
- Initial narrow credential-pattern and private-file-extension scans returned no findings; 125 raster images still require rights/privacy review. See [public release review](PUBLIC_RELEASE_REVIEW.md). This is not a complete secret, history or image-layer audit.
- The Compose candidate uses an isolated application network. Task-level guards now prevent outbox dispatch, automated inbound retry and notification delivery before state changes when external I/O is disabled. Full external-I/O regression and actual container-network verification are still pending. No production data may be restored into this candidate yet.
- The existing full application includes CMS/preview and external-channel capabilities with additional configuration requirements. Their presence in source is not proof that they have been enabled or accepted externally.

## Scheduler and direct transport hardening — 2026-09-12

- Added a serial, PostgreSQL-locked scheduler for Meta-only inbound retry (5m),
  marketing outbox (5m) and email retry/reminders (15m). Both scheduler and external
  I/O switches must be enabled; base Compose keeps them disabled. WhatsApp jobs
  are not scheduled. See [background tasks](BACKGROUND_TASKS.md).
- Provider filtering also covers stale inbound claims. Direct Google dispatch,
  polling/OAuth, Meta lead fetching, platform connection diagnostics and reminder
  entry points now honor the environment-level external-I/O switch.
- PostgreSQL17 lifecycle rerun passed 14 checks including duplicate scheduler
  refusal and no row changes in paused mode; temporary cluster stopped. The
  preceding 643-test full regression predates these additions; the final targeted
  regression passed 121 tests in 25.349 seconds (scheduler, packaging, measurement,
  lead services, Meta webhook and marketing event workspace), zero failures.
- The 6 distribution tests passed with scheduler included in the network,
  readiness and default-off assertions. Actual container/timed-task acceptance,
  all-channel egress audit and controlled release of restored export queues are
  still pending. No production/private database restore is approved by these tests.

## Real local HTTP and browser flow — 2026-09-12

- Added loopback-only website/CRM test server and optional `--http` lifecycle
  verification using a fresh PostgreSQL17 test database and random-password
  synthetic administrator/sales accounts. The final 16 aggregate checks passed;
  HTTP session/CSRF, invalid/duplicate submission, assignment, qualification and
  conversion through the actual website endpoint were verified.
- Real browser testing found and fixed stale success feedback on a new invalid
  attempt in homepage and contact forms. Both paths were reloaded and visually
  retested; styles were preserved and eight script cache references updated.
  Nine JavaScript guard/measurement tests passed.
- Restarted the isolated test database and verified three distinct browser
  submissions each persisted once, four total inquiries including automatic HTTP
  verification, and zero marketing outbox rows. All test services were stopped.
- See [HTTP/browser acceptance](HTTP_ACCEPTANCE.md). These results do not replace
  Docker/Nginx/PostgreSQL18, full CRM UI/export, private recovery or external
  platform acceptance. No production changes or GitHub publication occurred.
