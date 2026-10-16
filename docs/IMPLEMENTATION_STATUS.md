# Implementation status / 实现状态

## v0.2.0-rc.1 source candidate — 2026-10-17

This branch contains the complete website/CRM integration and replaceable
industrial company package. Its earlier demonstration deployment is separate
from this GitHub source release. This task does not update either running site.

Scope: [RELEASE_SCOPE.md](RELEASE_SCOPE.md). Install via the bilingual root README.
Industrial content: [INDUSTRIAL_EXAMPLE.md](INDUSTRIAL_EXAMPLE.md). The source
version is VERSION and is reported by `/healthz/`.

## Observed source-candidate checks

- Industrial/content examples:30 tests,1 conditional skip. Actual writers created
  media, drafts and disabled profiles; repeat import retained later edits, conflicts
  were refused and rollback retained durable file recovery records.
- Final clean source tree `72b7647`: complete application1361 tests with140
  conditional SQLite skips; root distribution112 tests. Guided-loop suite208 passed
  and Vite build passed. Selected website script
  contracts:51 passed. Python runtime audit reported no known vulnerabilities;
  npm audit reported zero findings for the current lock at observation time.
- Exact-resource guard:8 tests passed; release history accepted10 reviewed paths
  with zero findings across116 candidate commits. Gitleaks8.30.1 scanned119
  reachable commits and reported no leaks after exact-fingerprint review of
  synthetic test values and public browser storage keys.
- Isolated PostgreSQL17 lifecycle passed from actual public main (32 SQL entries)
  to the current43-entry chain. It preserved old rows, password, vault, tasks and
  publication data; paired database/file backup, independent restore, rollback,
  re-entry, local HTTP inquiry/qualification flow and industrial example lifecycle
  passed. Linux PostgreSQL18 and Compose remain GitHub CI responsibilities.
- Real local browser acceptance passed the industrial public site and language
  switch, authenticated workbench, customer-pool selection, two independent guided
  loops with diagrams/ten-step tasks, content/assets navigation and the 3D workspace.
  External delivery and scheduled tasks stayed disabled.
- GitHub private vulnerability reporting was enabled and read back as enabled.

## Remaining gates

GitHub PR checks, Linux PostgreSQL18/Compose CI, merge, immutable Tag/Release and
anonymous release download installation are recorded only after their actual
remote runs. This file never treats a local candidate as a published release or
as an update to either running website.

本页是当前发行状态入口。旧公网演示交付与本轮GitHub发行分别记账；旧“未上线”或
“待恢复”仅属于历史时点，不据此重跑服务器维护。本地通过不等于远端CI或公开发行；
原检查点保留在[历史状态](IMPLEMENTATION_STATUS_HISTORY.md)。
