# Engineering plan and acceptance ledger

Updated: 2026-09-20. This is the active implementation plan for turning the
current Vorntek source into a maintainable, independently deployable release.
`IMPLEMENTATION_STATUS.md` records observed evidence; this file records the work
sequence and acceptance gates.

## Evidence baseline

- Business reference: the Hong Kong `filline.com` stack. The former Hubei host is
  not part of the current architecture and is not a deployment target.
- Production-corresponding CRM source: local commit `7ed9c56`. A read-only
  comparison found its 426 tracked files semantically aligned with the Hong Kong
  application source; fourteen text files differed only by line endings.
- Public software repository: `rutther/vorntek`, local `main` at `b5fb537` before
  this work. It already contained the Vorntek example, Compose isolation, 27 SQL
  migrations, CI, guarded initialization, backup/restore and public-input checks.
- Website reference: the checked Hong Kong and local website trees had 302/302
  matching files for the observed `20260916live04` build. Public Vorntek content
  remains a deliberately fictional and redistributable example, not a copy of
  private company content.
- Pre-change local baseline: 44 repository checks, 23 browser-script contracts
  and 669 Django tests passed; seven Django tests were explicitly skipped. Docker
  is unavailable on the current Windows host, so PostgreSQL 18 and Compose proof
  must run in CI or another disposable Docker-capable environment.

No production write, deployment, customer-data transfer, domain change, Meta
operation or WhatsApp enablement is authorized by this plan.

## Production delta classification

| Class | Treatment | Current examples |
| --- | --- | --- |
| Generic product capability | Port in reviewed, tested slices | 21-column customer pool, value scoring, filters, export, safe batch publishing |
| Public software hardening | Keep or improve public implementation | Vorntek demo, default-disabled external I/O, migration ledger, backup/restore, source and license guards |
| Private/company-specific input | Exclude or replace with a documented extension interface | Regional research bundles, locked private source manifests, real customer data, production credentials and host scripts |
| Known-defective or overstated behavior | Do not port until corrected and separately accepted | Guided-loop completion inferred from historical/test rows, weak credential readiness and overstated optimization claims |
| Production operations | Prepare instructions only until separately authorized | Hong Kong deployment, real-data restore, domain cutover and service interruption |

## Work stages

### 1. Baseline and source map

- [x] Read workspace handoff, repository rules and closed-loop evidence.
- [x] Identify authoritative production, website and public-source locations.
- [x] Recheck local Git state, public remote and Hong Kong runtime read-only state.
- [x] Establish repeatable Python and JavaScript test baselines.
- [ ] Record the remaining production-to-public file/function matrix with an
  explicit disposition for every post-baseline production module.

Exit condition: every candidate input has a named owner, provenance and one of
the classifications above; unknown files are not silently shipped.

### 2. Generic business parity

- [x] Add migration 0028 and append-only integrity migration 0029 for the standard
  21-column data model.
- [x] Add strict CSV parsing, deterministic value reconciliation, bounded upload,
  idempotent preview/commit and persisted source rows.
- [x] Add authorized deterministic CSV export and round-trip tests.
- [x] Add safe bulk review/publish with deterministic route selection,
  per-company transactions, itemized failures and role-denial coverage.
- [ ] Complete customer-pool filtering, dense view and controlled column
  selection without importing private research adapters.
- [ ] Compare remaining generic article delivery and CRM improvements against the
  public abstractions; port only independently useful behavior.
- [ ] Correct the guided-loop semantics before considering any public inclusion.

Exit condition: generic Hong Kong capabilities selected for the public product
are present with success, denial, replay and failure-path tests; excluded private
inputs have documented extension boundaries.

### 3. Versioned installation and upgrade

- [x] Establish a semantic source version and expose it from CRM health output.
- [x] Add a preflight command that reports application version, database ledger,
  pending migrations, storage access and unsafe external-I/O settings without
  mutating state.
- [ ] Prove empty PostgreSQL 18 installation, 0.1.0-rc.1-to-current upgrade,
  repeat execution and rejected downgrade/unknown-ledger behavior.
- [x] Add a release manifest tying source version, migration tip and website asset
  build together.
- [x] Document stop-writers, backup, upgrade, health verification and
  rollback/restore as one operator runbook.

Exit condition: version mismatch is diagnosable before writes; an upgrade either
completes consistently or stops without concealing partial state.

### 4. Recovery, operations and security

- [ ] Repeat database and file-volume restore into an independent environment and
  verify record counts, sequences, file hashes and vault decryption.
- [ ] Verify restart persistence, worker shutdown/restart and scheduler-disabled
  defaults with actual containers.
- [ ] Review permissions, input limits, audit/log redaction, secret mounts and
  dependency advisories for the release commit.
- [ ] Add static checks that reject private research manifests, real identifiers,
  secrets, backups and retired-host scripts from public release inputs.

Exit condition: recovery is demonstrated, not inferred from backup creation; no
external sending is enabled by default.

### 5. Documentation and clean-clone acceptance

- [ ] Align English/Chinese install, configuration, administration, upgrade,
  backup/restore, development and troubleshooting documentation with commands.
- [ ] Validate desktop/mobile browser paths including inquiry, login, customer
  import/export and an actual browser file save.
- [ ] Run all local suites, PostgreSQL lifecycle/HTTP acceptance and Compose
  build/start/health/stop in disposable environments.
- [ ] Clone the candidate commit anonymously into a clean directory and repeat
  documented installation without untracked local inputs.

Exit condition: a third party can install and operate the source using only the
repository and documented prerequisites.

### 6. Release and GitHub delivery

- [ ] Review `git diff`, provenance, licenses, secrets/privacy and generated files.
- [ ] Create small, auditable commits and select a release-candidate version.
- [ ] Push the reviewed source to the existing GitHub repository without force.
- [ ] Confirm the remote commit and required CI jobs; repeat clean-clone checks
  against that exact remote commit.
- [ ] Publish a final acceptance report distinguishing automated, runtime,
  browser and external-platform evidence plus all remaining limits.

Exit condition: GitHub, the acceptance commit and the reported version are the
same immutable source state. Source push does not imply a Hong Kong deployment.

## Failure handling

- Preserve user changes and stop on overlapping uncommitted work.
- Never edit an applied migration; append a new numbered migration and update its
  checksum manifest.
- Keep migrations transactional and initialization ledger-guarded. Do not invent
  ledger entries or reset volumes to force success.
- Use synthetic data and isolated databases for tests. A failed test remains
  evidence to fix, not a reason to weaken the assertion.
- If a production-only action becomes necessary, finish all independent work,
  then present the exact target, expected interruption, backup evidence and
  rollback command for separate authorization.
