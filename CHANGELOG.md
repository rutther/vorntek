# Changelog

## [0.2.0-rc.4] — fresh-install browser fixture alignment

- The isolated browser-acceptance fixture now creates the same `general` inquiry
  contract as a fresh product installation instead of selecting the legacy
  `industrial` compatibility category.
- A regression test keeps the synthetic fixture aligned with the documented
  fresh-install behavior while leaving historical industrial forms compatible.
- Supersedes `0.2.0-rc.3`; its application and clean-download verification remain
  valid, but browser review exposed stale legacy fields in the synthetic fixture.

## [0.2.0-rc.3] — clean Node verification setup

- Bilingual README and development commands now install the locked `flow-ui`
  Node dependencies before running root JavaScript checks.
- The same documented sequence runs the complete guided UI suite and production
  build, so an anonymous source download no longer depends on inherited modules.
- Supersedes `0.2.0-rc.2`; its Python archive verification is correct, but its
  clean-download Node command omitted the required dependency-install step.

## [0.2.0-rc.2] — source archive verification fix

- Git-history and tracked-input tests now report explicit skips when run from a
  downloaded source archive, which intentionally has no `.git` metadata.
- Repository checkouts still run every history-dependent release-integrity test;
  non-Git application and distribution tests continue to run inside archives.
- Supersedes `0.2.0-rc.1`, whose application and CI checks passed but whose
  documented archive test command ended with eight Git-metadata errors.

## [0.2.0-rc.1] — source release candidate

- Complete desktop CRM and guided marketing workspace, illustrated guidance,
  flow diagrams, independent workflow instances and scoped Pixel runtime.
- Configurable inquiry fields through public validation, CRM, qualification,
  notification and opportunity handling; consent and immutable delivery routing.
- Recoverable lead trash with multi-selection and original task/state restoration,
  fresh role/session checks and durable private-file recovery.
- Replaceable industrial company website: six equipment models, bilingual
  catalogue/applications/guides, media, downloads and optional three-language drafts.
- Authenticated article/GLB/splat preview and immutable candidate/selection/
  deployment/recovery, retaining explicit operator actions.
- Optional `seed_industrial_examples`, preserving old examples, passwords and
  later edits; additive SQL through0043 and actual public-main upgrade checks.
- Reviewed exact resource identities and reproducible source packaging.
  External delivery is disabled by default; no prebuilt image or cluster SLA.

Publication and final download acceptance are tracked separately. Earlier
development details below are historical notes, not current pending-work claims.

## Integration completeness follow-up

- Authenticated GLB draft preview reads the scoped stored asset instead of
  a not-yet-published public URL. The self-hosted renderer supports actual
  drag/camera capture and ordinary save/reopen without enabling a public slot.
  Missing/changed, external-resource and optional-decoder models show explicit
  failure states, with no remote decoder or cloud runtime requests.

- Article preview now hands off its exact current/reused record to candidate
  construction instead of making operators guess the newest history row.
  Draft-only, missing-translation and preview-only permission boundaries remain.

- Editable select choices now persist from the ordinary form editor; enabled
  selects without usable choices are rejected before saving/public exposure.
- Public forms distinguish actionable validation errors from uncertain transport
  failures, display bounded literal messages and retain inputs/retry identity.

- Restore the existing Pixel scope save sub-operation and its original version,
  placement, consent and transaction guards; saving does not activate delivery.
- Restore the current CRM source-analysis page and matching-field completeness
  view, with aggregate-only queries, explicit failure feedback and preserved
  legacy advanced-tracking links. No advertising-attribution result is implied.

All notable changes to Vorntek are recorded here. The project follows
[Semantic Versioning](https://semver.org/); development versions are not release
certifications.

## [Unreleased]

### Fixed

- Same-origin website navigation is no longer classified as an external referral;
  consent filtering and campaign precedence remain unchanged.
- Remaining conversation requirement examples use neutral categories and project
  parameters instead of one customer's equipment or production units.

- Candidate build failures explain missing translations or public resources
  without exposing internal paths; deployment guidance no longer names a client environment.
- Shared-base article navigation keeps the current article language.
- The keyboard skip link no longer creates horizontal page overflow in RTL.
- Article category pickers now show only choices accepted by the editor, while
  ordinary edits can retain an existing unpublished category in the same scope.
- Standalone console website links inherit the configured site origin instead
  of an unrelated developer port; an explicit separate preview origin still wins.
- Lead workspace timestamps use the active display timezone, matching task pages
  for the same stored instant without rewriting database timestamps.
- Integration help no longer embeds a former customer's phone or account suffix.

### Added

- A plan-first, stale-state-guarded `configure_site_origin` maintenance command
  for existing installations, with one-field updates and immutable audit records.

- An opt-in, exact-peer HTTPS edge proxy renderer with standalone real Nginx
  spoofing/client-identity checks; no automatic host configuration changes.
- Updated bilingual neutral-product installation and rollout guidance, separating
  optional example packs and historical release evidence from current delivery.

- A canonical `VERSION` file and runtime version reporting in `/healthz/`.
- A read-only `release_preflight` command for version, migration ledger, storage
  and outbound-writer readiness, enforced by container CI.
- A deterministic, non-secret source release manifest tying the Git tree, version,
  website tree, migration tip, Compose file and dependency lock together.
- A complete 106-path Hong Kong production-delta ledger that separates reusable
  product work from private research inputs, defective semantics and host scripts.
- The standard 21-column customer-pool CSV workflow: strict parsing, value
  reconciliation, preview/commit, row-level persistence and authorized CSV export.
- Controlled bulk review/publish for customer-pool records, with deterministic
  route selection, per-company transactions, permission checks and itemized results.
- A role-bounded customer-pool query layer plus dense standard-21 and company
  views, multi-country/value/state filters, sortable columns and numeric phone
  ordering.
- A product-neutral, digest-bound article release and safe Markdown rendering
  core with explicit locale relationships and no marketing-script injection.
- A private owner-marked article artifact store with content-addressed versions,
  compare-and-swap activation, withdrawal/rollback and pinned read-only build input.
- Authenticated, site-scoped article private preview with version-bound review records,
  traversal-safe referenced assets and network-silent no-store/CSP responses.
- An immutable whole-site candidate store that freezes the checked-in website and
  live-mode article artifact, verifies internal links and records exact source
  versions without selecting or deploying the candidate.
- An authenticated, site-scoped release-center action that requires the separate
  `releases.candidate_build` capability and builds only from an explicit reviewed
  article preview; it cannot select or deploy the result.
- An append-only whole-site candidate selection ledger with an independent
  `releases.candidate_select` capability, expected-version CAS, idempotent request
  receipts, verified update/rollback chains and no artifact activation or deploy.
- A site-scoped operator confirmation page that re-verifies candidate evidence,
  requires an audit reason, rejects stale forms and labels selection as not deployed.
- A dedicated website serving volume and fail-closed serving store that copies
  verified candidates into immutable releases and atomically switches a relative
  Nginx pointer while the web server remains read-only.
- A crash-resumable, database-gated website deployment core with an independent
  `releases.deploy` capability, prepared-operation interlock and immutable receipt.
- An authenticated deployment confirmation page that re-verifies the current
  selection, makes the serving impact explicit, rejects stale forms and resumes
  a prepared operation only with its original UUID, reason and operator.
- Migration `0028_customer_pool_standard21.sql` without rewriting the existing
  27-migration history.
- Append-only migration 0029, which requires pool-row batch/row references to be
  paired and site-consistent.
- Append-only migration 0030, which extends the content-grant database constraint
  with the high-risk whole-site candidate-build capability.
- Append-only migration 0031, which adds the separate candidate-selection
  capability and immutable site/candidate selection ledger.
- Append-only migration 0032, which adds deployment permission, recoverable
  operation transitions, selection/deployment binding and immutable receipts.
- A pinned Python dependency vulnerability audit in candidate CI.
- Bilingual configuration, administration, development and troubleshooting
  guides with repository checks for operator entry points, commands and links.
- A loopback-only ephemeral browser-acceptance fixture for real desktop/mobile,
  inquiry, login, standard-21 import and disk-download verification without
  external delivery or persistent credentials.
- A clean source-clone acceptance record proving a fresh locked install and the
  complete source, JavaScript and Django suites without untracked workspace inputs.
- A candidate Git-history audit that checks all post-RC1 objects, including blobs
  later deleted, for blocked secret/privacy inputs and oversized files without
  echoing matched material; full-history CI repeats the gate.

### Changed

- Standalone PostgreSQL acceptance follows current model/schema and scheduler
  contracts, admits only the proven lossless literal-array restore reparse, and
  verifies configurable neutral/personal inquiry flows rather than retired
  fixed-industry defaults. Windows inventory uses an isolated real serving tree.

- Import preview creation now serializes per site and safely reuses an existing
  batch when the same file is previewed again.
- The customer import page accepts the standard CSV format with bounded reads.
- The review queue can publish explicit selections without bypassing the existing
  single-record review gate; standard-21 rows are authoritative and restricted
  routes cannot fall back to older unmapped contact evidence.
- Customer-pool exports now preserve the active neutral filters and offer an
  explicit standard-21 column picker while keeping phone and email as required
  keys. CSV output neutralizes spreadsheet-formula prefixes, including after
  leading whitespace, without rewriting syntactically valid international phone
  text.
- Customer detail pages now show value, all standard-21 routes in deterministic
  order, hard-restriction warnings and neutral contact provenance labels.
- Python dependency evidence is bound to the exact lock-file hash; the earlier
  Linux image inventory is retained as historical evidence instead of being
  relabeled after Markdown dependencies were added.
- The packaging regression now verifies the complete 32-migration chain.
- Django is updated within the 5.2 LTS line from 5.2.13 to the 5.2.17 security
  patch. Provider errors and default access logs are consistently redacted, and
  gateway/parser upload limits now match each accepted application route.
- Article, whole-site candidate and serving stores now share a POSIX advisory
  lock that the kernel releases after process termination, so a stale lock file
  cannot permanently block crash recovery. Article staging also removes failed
  temporary trees, failed pointer-swap files and hardlinked artifact files.

## [0.1.0-rc.1] - 2026-09-13

- First public source release with the bilingual Vorntek website, CRM, guarded
  SQL installation, isolated Compose stack, CI, backup/restore and synthetic demo.

[Unreleased]: https://github.com/rutther/vorntek/compare/v0.1.0-rc.1...HEAD
[0.1.0-rc.1]: https://github.com/rutther/vorntek/releases/tag/v0.1.0-rc.1
