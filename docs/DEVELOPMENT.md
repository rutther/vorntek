# Development / 开发

The repository keeps application source, generated website output, canonical SQL
migrations, tests and deployment definitions together. Use synthetic data and an
isolated environment; never point development commands at a customer database.

仓库统一保存应用源码、生成后的官网、唯一 SQL 迁移链、测试和部署定义。开发只使用
合成数据和隔离环境，任何开发命令不得指向客户数据库。

## Toolchain and setup / 工具链与安装

Candidate CI uses Python 3.12.12, Node 24.20.0, Ubuntu 24.04, PostgreSQL 18 and
Docker Compose. The source/SQLite suites also run on the current Windows host.

```sh
python3 -m venv .venv
python3 -m pip install -r apps/crm/requirements.lock -r requirements-dev.txt
python3 -m pip check
```

`requirements.txt` declares direct application dependencies; `requirements.lock`
selects the complete application environment. Review dependency changes and
regenerate the recorded host/Linux evidence rather than silently refreshing locks.

## Required local checks / 必跑本地检查

```sh
python3 -m unittest discover -s tests -v
node --test scripts/test_measurement.mjs scripts/test_form_status.mjs scripts/test_credential_receipt.mjs scripts/test_vorntek_form.mjs
python3 scripts/run_application_tests.py
python3 -m pip_audit -r apps/crm/requirements.lock --progress-spinner off
python3 scripts/audit_release_history.py --base v0.1.0-rc.1 --require-clean
```

The history audit requires the immutable RC1 tag and full Git history. It scans
candidate-only objects, including deleted blobs, without printing matched secret
material. A passing pattern scan complements human review; it cannot certify that
every possible secret or personal datum is absent.

The Django runner creates a synthetic SQLite database and blocks non-loopback
Python sockets. It is compatibility coverage, not PostgreSQL, browser, container
or external-platform acceptance. With isolated PostgreSQL binaries, run:

```sh
python3 scripts/test_postgres_install.py --pg-bin /path/to/postgresql/18/bin --http
```

For actual browser layout and synthetic inquiry/import/export interaction when
PostgreSQL is unavailable, run the loopback-only ephemeral fixture:

```sh
python3 scripts/run_browser_acceptance_fixture.py --port 8765 --review-seconds 900
```

It prints paths for a generated upload and temporary credentials, then removes
the credential file at shutdown. This SQLite fixture is UI evidence only and
never replaces the PostgreSQL/Compose/Nginx release gates. See the latest
[browser acceptance record](review/BROWSER_ACCEPTANCE_20260921.md).

在缺少 PostgreSQL 时，可用上述回环临时夹具执行真实浏览器布局、合成询盘和导入导出；
它只提供 UI 证据，不能替代 PostgreSQL、Compose 或 Nginx 发布门槛。

## Source and migrations / 源码与迁移

- Change source generators/catalogue first, then run
  `python3 scripts/build_vorntek_site.py`; generated `apps/website/` must match
  the clean generator output.
- Never edit an applied SQL migration. Add the next numbered file, update
  `apps/crm/db/migrations/SHA256SUMS`, and test empty install, contiguous upgrade,
  replay and rejection of gaps/unknown history.
- Preserve role, site and locale scope at the query/service boundary. Add success,
  denial, stale, replay and failure-path tests; do not weaken an assertion to make
  CI pass.
- Keep private research adapters, real identifiers, secrets, backups and retired
  host scripts outside public inputs. The root distribution suite enforces the
  current exclusion ledger.

## Commit and candidate evidence / 提交与候选证据

```sh
git diff --check
git status --short
python3 scripts/release_manifest.py --require-clean
```

Use small reviewable commits. Update `CHANGELOG.md`, implementation status and the
acceptance ledger with tests actually run and unverified environments. A manifest
binds source state but does not prove runtime acceptance or authorize deployment.

The latest [clean source-clone record](review/CLEAN_SOURCE_CLONE_20260921.md)
shows the source-only reconstruction procedure and its limits. Before release,
repeat it as an anonymous clone of the exact pushed commit and run the separate
Linux/PostgreSQL/Compose gates; a local clean clone does not satisfy those checks.

使用小而可审查的提交。同步更新变更日志、实施状态和验收台账，只记录实际执行的测试，
明确未验证环境。发布清单只绑定源码状态，不等于运行验收，也不授权部署。
