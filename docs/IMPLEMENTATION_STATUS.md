# Implementation status / 实现状态

## v0.2.0-rc.4 published prerelease — 2026-10-17

This publication contains the website/CRM integration and replaceable industrial
company package. It is separate from every running deployment; this release task
did not update either production site.

Scope: [RELEASE_SCOPE.md](RELEASE_SCOPE.md). Install via the bilingual root README.
Industrial content: [INDUSTRIAL_EXAMPLE.md](INDUSTRIAL_EXAMPLE.md). The source
version is `VERSION` and is reported by `/healthz/`.

## Observed release checks

- Industrial/content examples: 30 tests, 1 conditional skip. Actual writers created
  media, drafts and disabled profiles; repeat import retained later edits, conflicts
  were refused and rollback retained durable file recovery records.
- Rc.4 passed 116 root tests, 1361 application tests with 140 conditional SQLite
  skips, 208 guided-loop tests and 51 root JavaScript checks; the Vite build passed.
  An anonymous download repeated the same 116/1361/208/51 results without Git
  metadata. `pip check` passed; after updating the fresh virtual environment's
  installer from pip 25.0.1 to 26.2.1, `pip-audit` reported no known
  vulnerabilities. `npm audit` reported zero findings.
- Exact-resource guard: 8 tests passed; release history accepted 10 reviewed paths
  with zero findings across 125 commits and 2594 objects. Gitleaks 8.30.1 scanned
  124 reachable commits and reported no leaks after exact-fingerprint review of
  synthetic test values and public browser storage keys.
- Isolated PostgreSQL 17 lifecycle passed from actual public main through the
  current 43-entry chain. It preserved old rows, password, vault, tasks and
  publication data; paired database/file backup, independent restore, rollback,
  re-entry, local HTTP inquiry/qualification flow and industrial example lifecycle
  passed. Linux PostgreSQL 18 and Compose passed in GitHub tag CI.
- Anonymous rc.4 browser acceptance submitted the fresh-install `general` inquiry,
  observed the persisted lead, selected and moved it to recoverable trash, restored
  it to the active queue, and opened tasks and event operations. It created two
  independent Meta website loop instances, switched between them, and observed the
  ten-step instructions, step illustration and cross-system flow diagram. Content,
  assets and the industrial public product were opened; the GLB model rendered in
  the interactive 3D viewer. External delivery and scheduled tasks stayed disabled.
- The optional industrial pack's read-only plan reported three articles, six models
  and fifteen files. Its PostgreSQL write lifecycle passed the tag's GitHub CI.
  A local SQLite apply was not accepted as installation evidence because canonical
  business tables are installed by the PostgreSQL SQL chain. Docker was unavailable
  on the anonymous Windows host, so Compose evidence remains the successful tag CI
  job rather than a claimed local rerun.
- GitHub private vulnerability reporting was enabled and read back as enabled.

Rc.1 passed its own CI and anonymous hashes, but archive testing exposed Git-only
root checks. Rc.2 fixed that path; its clean Node check then exposed missing module
installation. Rc.3 fixed the documented Node setup; anonymous browser review then
found its fixture selected a legacy industrial form category. Rc.4 aligns that
fixture with the documented fresh-install `general` contract without converting
existing legacy forms.

## Published identities and boundaries

- Pull request: [#5](https://github.com/rutther/vorntek/pull/5).
- Merge commit and immutable tag: `20946994d1592a2a09e204b886c13e45560e304f`,
  `v0.2.0-rc.4`.
- Main CI: [38985116604](https://github.com/rutther/vorntek/actions/runs/38985116604).
- Tag CI: [38985631768](https://github.com/rutther/vorntek/actions/runs/38985631768).
- Release: [v0.2.0-rc.4](https://github.com/rutther/vorntek/releases/tag/v0.2.0-rc.4).
- Exact public download hashes and anonymous acceptance are recorded in
  [the rc.4 acceptance record](releases/0.2.0-rc.4-acceptance.md).

This source publication did not update `vorntek.com`, the Hong Kong installation,
any external Meta account or any real delivery channel. Publishing source is not
deployment, and synthetic browser acceptance is not real-provider verification.

本页是当前发行状态入口。旧公网演示交付与本轮 GitHub 发行分别记账；旧“未上线”或
“待恢复”仅属于历史时点，不据此重跑服务器维护。本地通过不等于远端 CI 或公开发行；
原检查点保留在[历史状态](IMPLEMENTATION_STATUS_HISTORY.md)。
