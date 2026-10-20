# Implementation status / 实现状态

## v0.2.0-rc.5 release candidate — 2026-10-20

This candidate synchronizes reusable acquisition, CRM operations and guided-loop
improvements into the industry-neutral product. Hong Kong was a read-only evidence
source. No customer record, credential, production configuration, backup or private
history was copied into this repository.

Scope: [RELEASE_SCOPE.md](RELEASE_SCOPE.md). Install via the bilingual root README.
Industrial content: [INDUSTRIAL_EXAMPLE.md](INDUSTRIAL_EXAMPLE.md). The source
version is `VERSION` and is reported by `/healthz/`.

## Accepted source, release and deployment evidence

- Pull request [#7](https://github.com/rutther/vorntek/pull/7) was squash-merged as
  `b5a17a50ab88313c13f32112261f9c9d38e5c6a1`. Pull-request, main and immutable-tag
  CI each passed the application and PostgreSQL 18/Compose jobs.
- Public prerelease [`v0.2.0-rc.5`](https://github.com/rutther/vorntek/releases/tag/v0.2.0-rc.5)
  contains deterministic source archives, a release manifest and checksums. A new
  anonymous installation verified all checksums before extraction and completed
  110 root tests with eight Git-only checks skipped, 1379 application tests, 51
  root JavaScript tests, 209 guided-loop tests, both dependency audits and the
  production UI build.
- The exact merge commit was promoted to the authorized Vorntek installation after
  a fresh paired backup, isolated restore and an isolated PostgreSQL gate. Final
  read-only checks observed five healthy containers, 47 migrations, preserved
  business counts and every external-I/O, scheduler and WhatsApp live-send flag at
  `0`.
- Real-browser production review covered the English public site and contact form
  without submission, CRM workbench, lead selection/batch controls, customer pool,
  three independent loop instances, flow/task views and operation figures. The
  append-only audit log retained the review actor as an inactive, permission-free,
  unusable-password tombstone; no review credential remains.
- The complete identities, asset hashes, test counts, production observations and
  non-claims are in the [rc.5 acceptance record](releases/0.2.0-rc.5-acceptance.md).

## Candidate identities and boundaries

- Product commit and immutable tag target:
  `b5a17a50ab88313c13f32112261f9c9d38e5c6a1`.
- Running Vorntek CRM/worker image:
  `sha256:4a1878514ae988d69b1ecfab0298e379d4868f0bed7962760578ac471eb70b0c`.
- Running Vorntek website image:
  `sha256:bfbff73ec124487db352a160cd2e87fa6e83120f24b914ec096a25a60ab28105`.
- Public version: `v0.2.0-rc.5`; its tag, release assets and public-download evidence
  are immutable identities recorded in the acceptance record.

The authorized Vorntek deployment did not modify the Hong Kong installation, any
external Meta account or any real delivery channel. Publishing source is not a
provider test, and browser acceptance is not evidence of Meta reception or
deduplication.

本页是当前发行状态入口。旧公网演示交付与本轮 GitHub 发行分别记账；旧“未上线”或
“待恢复”仅属于历史时点，不据此重跑服务器维护。本地通过不等于远端 CI 或公开发行；
原检查点保留在[历史状态](IMPLEMENTATION_STATUS_HISTORY.md)。
