# Implementation status / 实现状态

## v0.2.0-rc.5 release candidate — 2026-10-20

This candidate synchronizes reusable acquisition, CRM operations and guided-loop
improvements into the industry-neutral product. Hong Kong was a read-only evidence
source. No customer record, credential, production configuration, backup or private
history was copied into this repository.

Scope: [RELEASE_SCOPE.md](RELEASE_SCOPE.md). Install via the bilingual root README.
Industrial content: [INDUSTRIAL_EXAMPLE.md](INDUSTRIAL_EXAMPLE.md). The source
version is `VERSION` and is reported by `/healthz/`.

## Observed candidate and deployment checks

- The frozen source candidate passed 118 root distribution tests, 1379 application
  tests with 140 documented SQLite conditional skips, 209 guided-loop tests and the
  production UI build. The changed navigation/customer-pool regression set also
  passed independently.
- Isolated PostgreSQL 17.11 passed fresh 0→47 and trusted 43→47 installation and
  upgrade, repeat apply, read-only plan, schema parity, rollback, original-column
  data preservation and the six release acceptance groups.
- The exact source candidate `3e5bad27be2332f1a2005d046049565585aef31b` was
  promoted to the authorized Vorntek installation. Final read-only checks observed
  five healthy containers, 47 migrations, unchanged business row counts and all
  external-I/O, scheduler and WhatsApp live-send flags at `0`.
- Real-browser production review covered the English public site and contact form
  without submission, CRM workbench, lead selection/batch controls, customer pool,
  three independent loop instances, seven-node flow view, task guide, all sixteen
  operation figures and the verification page. The disposable review operator was
  deleted; the original eight-user count was restored.
- GitHub pull-request CI, immutable tag/release assets and anonymous public-download
  installation are still pending for this candidate and must not inherit rc.4's
  green checks.

## Candidate identities and boundaries

- Product candidate: `3e5bad27be2332f1a2005d046049565585aef31b` plus the
  release-metadata commit prepared on the same branch.
- Running Vorntek CRM/worker image:
  `sha256:db2ef62270fa602c24f397b5bdf093b963546b637ce04b021a34357270bb6607`.
- Running Vorntek website image:
  `sha256:656c96060f5d0da5e52d665e5282421393178dc03f2131961453d998daba955a`.
- Target public version: `v0.2.0-rc.5`; PR, CI, tag, release and anonymous-download
  identities will be recorded only after they exist.

The authorized Vorntek deployment did not modify the Hong Kong installation, any
external Meta account or any real delivery channel. Publishing source is not a
provider test, and browser acceptance is not evidence of Meta reception or
deduplication.

本页是当前发行状态入口。旧公网演示交付与本轮 GitHub 发行分别记账；旧“未上线”或
“待恢复”仅属于历史时点，不据此重跑服务器维护。本地通过不等于远端 CI 或公开发行；
原检查点保留在[历史状态](IMPLEMENTATION_STATUS_HISTORY.md)。
