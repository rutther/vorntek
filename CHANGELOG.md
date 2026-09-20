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
- The standard 21-column customer-pool CSV workflow: strict parsing, value
  reconciliation, preview/commit, row-level persistence and authorized CSV export.
- Controlled bulk review/publish for customer-pool records, with deterministic
  route selection, per-company transactions, permission checks and itemized results.
- Migration `0028_customer_pool_standard21.sql` without rewriting the existing
  27-migration history.
- Append-only migration 0029, which requires pool-row batch/row references to be
  paired and site-consistent.

### Changed

- Import preview creation now serializes per site and safely reuses an existing
  batch when the same file is previewed again.
- The customer import page accepts the standard CSV format with bounded reads.
- The review queue can publish explicit selections without bypassing the existing
  single-record review gate; restricted routes stay in review for manual handling.

## [0.1.0-rc.1] - 2026-09-13

- First public source release with the bilingual Vorntek website, CRM, guarded
  SQL installation, isolated Compose stack, CI, backup/restore and synthetic demo.

[Unreleased]: https://github.com/rutther/vorntek/compare/v0.1.0-rc.1...HEAD
[0.1.0-rc.1]: https://github.com/rutther/vorntek/releases/tag/v0.1.0-rc.1
