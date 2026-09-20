# Changelog

All notable changes to Vorntek are recorded here. The project follows
[Semantic Versioning](https://semver.org/); development versions are not release
certifications.

## [Unreleased]

### Added

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

### Changed

- Import preview creation now serializes per site and safely reuses an existing
  batch when the same file is previewed again.
- The customer import page accepts the standard CSV format with bounded reads.
- The review queue can publish explicit selections without bypassing the existing
  single-record review gate; restricted routes stay in review for manual handling.
- Customer-pool exports now preserve the active neutral filters and offer an
  explicit standard-21 column picker while keeping phone and email as required keys.
- Customer detail pages now show value, all standard-21 routes in deterministic
  order, hard-restriction warnings and neutral contact provenance labels.
- Python dependency evidence is bound to the exact lock-file hash; the earlier
  Linux image inventory is retained as historical evidence instead of being
  relabeled after Markdown dependencies were added.
- The packaging regression now verifies the complete 32-migration chain.
- Django is updated within the 5.2 LTS line from 5.2.13 to the 5.2.17 security
  patch. Provider errors and default access logs are consistently redacted, and
  gateway/parser upload limits now match each accepted application route.

## [0.1.0-rc.1] - 2026-09-13

- First public source release with the bilingual Vorntek website, CRM, guarded
  SQL installation, isolated Compose stack, CI, backup/restore and synthetic demo.

[Unreleased]: https://github.com/rutther/vorntek/compare/v0.1.0-rc.1...HEAD
[0.1.0-rc.1]: https://github.com/rutther/vorntek/releases/tag/v0.1.0-rc.1
