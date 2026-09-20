# Contributing / 开发协作

This source repository is public under the project [MIT license](LICENSE). That
license covers project code only: third-party components and media retain their
own terms in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Do not add private
company data, production configuration, or material without redistribution
evidence.

## Local development

Read `AGENTS.md`, the bilingual README and `docs/IMPLEMENTATION_STATUS.md` first.
Keep the existing product/UI style, business contracts, authorization and audit
semantics. Work in this independent project, not the old production checkout.
Use synthetic data in isolated environments and preserve other contributors'
changes. Do not use a shared or real customer database for tests.

Install the application lockfile plus `requirements-dev.txt` in a private virtual
environment. `requirements.txt` declares direct dependencies; `requirements.lock`
selects versions. Review updates rather than silently refreshing all packages.

```sh
python -m pip install -r apps/crm/requirements.lock -r requirements-dev.txt
python -m pip check
python -m unittest discover -s tests -v
node --test scripts/test_measurement.mjs scripts/test_form_status.mjs scripts/test_credential_receipt.mjs scripts/test_vorntek_form.mjs
python scripts/run_application_tests.py
```

The application runner refuses an application `.env`, resets application/database
environment variables and creates an isolated SQLite test DB. It enables mocked
transport branches while blocking non-loopback Python socket connections; this is
not an OS network sandbox or real platform acceptance. Use a credential-free test
process; actual PostgreSQL/Compose checks are described in [CI](docs/CI.md).

开发与测试只使用合成数据。未经单独授权，不部署服务器、不提交真实表单、不迁移
客户文件、不操作 Meta/广告、不恢复 WhatsApp、不上传预构建镜像。向现有 GitHub
仓库提交源码必须先通过本文件规定的审查与测试；生产部署仍需单独授权。

## Changes and review

- Keep applied SQL immutable. Add a numbered migration and update the verified
  manifest through review; prove install, upgrade and failure recovery. Never
  invent legacy migration records to make a check pass.
- Test both success and permission/failure paths. UI changes need actual browser
  evidence at desktop/mobile sizes, not only a screenshot or VM unit test.
- Describe scope, source baseline, tests actually run, skipped/unverified areas,
  compatibility impact and rollback. Preserve provenance and update both READMEs
  when commands or operator-visible behavior change.
- Exclude private data, secrets, backups, logs and runtime files. Review media and
  dependency redistribution rights; public availability is not a license grant.
- CI builds and tests only. Release/deployment authorization, repository settings,
  real-data recovery, image publication and domain switching are separate gates.

Report vulnerabilities privately according to [SECURITY.md](SECURITY.md), not in
a public pull request with customer data or live exploit credentials.
