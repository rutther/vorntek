# Implementation status / 实现状态

## v0.2.0-rc.1 source candidate — 2026-10-17

This branch contains the complete website/CRM integration and replaceable
industrial company package. Its earlier demonstration deployment is separate
from this GitHub source release. This task does not update either running site.

Scope: [RELEASE_SCOPE.md](RELEASE_SCOPE.md). Install via the bilingual root README.
Industrial content: [INDUSTRIAL_EXAMPLE.md](INDUSTRIAL_EXAMPLE.md). The source
version is VERSION and is reported by `/healthz/`.

## Observed preparation checks

- Industrial/content examples:30 tests,1 conditional skip. Actual writers created
  media, drafts and disabled profiles; repeat import retained later edits, conflicts
  were refused and rollback retained durable file recovery records.
- Complete application after that change:1361 tests,140 conditional SQLite skips.
  Root distribution checks:101 tests. These local results do not imply all cases
  executed under PostgreSQL.
- Guided-loop suite:208 passed; Vite build passed. Selected website script
  contracts:51 passed. Python runtime audit reported no known vulnerabilities;
  npm audit reported zero findings for the current lock at observation time.
- Exact-resource guard:8 tests passed; the earlier integration history accepted
  10 reviewed paths with no findings. Gitleaks8.30.1 found only a reviewed literal
  UUID test fixture; exact-fingerprint exclusion and rescan passed. Final source
  history checks still run before the first public push.
- GitHub private vulnerability reporting was enabled and read back as enabled.

## Remaining gates

Actual public-main upgrade, final PostgreSQL/Compose lifecycle, clean clone/build,
current browser acceptance, final source/privacy checks, whole-branch review,
GitHub PR/CI/merge/Tag/Release and anonymous download installation remain in
progress. Each gate must bind to the final source revision before completion.

本页是当前发行状态入口。旧公网演示交付与本轮GitHub发行分别记账；旧“未上线”或
“待恢复”仅属于历史时点，不据此重跑服务器维护。上述发行门槛尚未全部完成，不把
本地通过写成正式发行。原检查点保留在[历史状态](IMPLEMENTATION_STATUS_HISTORY.md)。
