# Source-only acceptance — 2026-09-12

This checks source independence, not a clean operating-system installation or
approval to deploy/publish.

## Frozen input

The candidate had no commits or remote. Its staged source was exported with
`git checkout-index --all --prefix=<new-empty-directory>/` into an independent
temporary directory outside the workspace. A new local Git index there produced
the identical tree `cb24ae69984909d5e612654fb162be097dda0eb7`, verifying exported
paths, bytes and modes against the candidate index. No commit or push occurred.

Only staged files were exported: no private runtime archive, application `.env`,
virtual environment, customer database, private backups or excluded QA images.
The [retired old-host tools](LEGACY_TOOLS.md) were not present. This report and
subsequent status-only documentation are not part of that frozen input tree.

## Checks

- 34 distribution, archive, CI and public-input tests passed in 0.880s.
- 14 Node browser-script contract tests passed with no failures or skips.
- Complete synthetic SQLite application regression: 643 tests in 447.355s;
  636 passed, seven skipped, zero failures. Django checks found no issues and
  the synthetic test database was destroyed by the runner. The preceding suite
  had 656 tests; the 13-test difference is exactly the retired old-host tool
  contracts, not removed business tests.

Tests execute from the exported directory. They reuse the declared dependencies
installed in the local Python 3.12.14 interpreter, Node and the local PyYAML test
dependency; this does not demonstrate a fresh dependency installation. The
application runner refuses an application `.env`, uses synthetic SQLite data and
blocks non-loopback Python socket calls. Its guard is not an OS network sandbox.

## Remaining gates

PostgreSQL18/Linux/Compose image builds, runtime volume permissions, real reverse
proxy/browser flows, server restart, authorized private-data recovery, media
rights, overall license and public GitHub release are separate pending checks.
The previously observed PostgreSQL17 lifecycle/HTTP results remain separate
evidence; they were not rerun by this source-only test.
