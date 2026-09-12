# Continuous integration / 持续集成

2026-09-13 — the first [public workflow run](https://github.com/rutther/vorntek/actions/runs/34711508885)
tested commit `a13278a9b55189735415bd60e2ab726b6b864015`:

- Application job **passed**: locked dependencies and `pip check`, 42 root tests,
  browser-script contracts, 669 application tests in 351.453s (667 passed, two
  skipped), Django system checks clean.
- PostgreSQL/Compose job **failed** at standalone PostgreSQL startup; no subsequent
  Compose build/start checks ran in that attempt. APT supplied PG18.6; deployed
  Compose still uses PG18.3. The original harness did not expose the server log.
- A restricted Linux reproduction of the same startup arguments failed trying to
  create the default socket lock under distro-owned `/var/run/postgresql`;
  explicitly placing the socket in the unique test directory started successfully,
  then the test cluster stopped. The runner now supplies that private POSIX socket
  directory and includes redacted startup diagnostics. Windows TCP behavior is
  unchanged; two new contract tests pass (44 root tests total). The exact first CI
  error cannot be recovered from its disposed server log; the socket issue is the
  reproduced portability defect, and the next CI run must validate the correction.

These findings do not claim an all-green workflow or published binary image.
Older local-only counts below are historical.

`.github/workflows/ci.yml` defines two independent ephemeral Ubuntu24.04 jobs:

| Job | Checks | Limitations |
| --- | --- | --- |
| Application | Pinned Python dependencies, `pip check`, recovery/configuration unit tests, Node script contracts, complete SQLite regression | Synthetic transports; not real Meta or full PostgreSQL business acceptance |
| PostgreSQL and Compose | Official PG18 package binaries, isolated install/upgrade/restore/HTTP harness, all image builds, actual container entrypoint smoke and maintenance CLI help | Must actually run before claiming success; not full private-data recovery, all browser flows or publishing |

## Current local evidence

- `run_application_tests.py`: 656 tests in 449.361s, 649 passed, seven skipped,
  zero failures. Django system checks reported no issues. Python3.12.14/Windows
  was used locally, not the CI job's declared Python3.12.12/Linux environment.
- Unit/distribution/CI configuration checks: 29 passed. Node VM tests: 14 passed.
- PG17.11 `test_postgres_install.py --http`: 23 aggregate checks passed, including
  the actual smoke script. The HTTP server and unique cluster stopped. Private
  synthetic evidence is retained outside Git in the operator's private test directory.
- `pip check` in the existing local interpreter found no broken requirements;
  this is not a fresh dependency-install proof. YAML contract tests are not the
  GitHub Actions validator or an online execution result.

The Ubuntu runner inventory currently lists PostgreSQL16, so the second job uses
the official signed PostgreSQL package repository explicitly for PG18. It creates
a separate random-port synthetic cluster, not the runner's default cluster. APT
selects the available PG18 patch version; the Compose image separately declares
18.3. These are not bit-for-bit identical or immutable APT dependencies.

## Permission and data boundaries

- Only `contents: read`; checkout does not persist credentials. Actions use full
  commit hashes resolved from their official repositories on 2026-09-12.
- No `pull_request_target`, privileged deployment credentials, registry login,
  repository push, artifact upload or persistent self-hosted runner.
- Recovery evidence remains on the ephemeral runner until disposal. Never upload
  backups, file manifests, `.env`, `.secrets` or runtime exports as build artifacts.
- Base Compose disables measurement/marketing delivery and scheduled work. The
  full compatibility runner enables mocked I/O branches but blocks non-loopback
  Python socket connect/connect_ex/sendto. This is not a general OS sandbox and
  does not govern arbitrary C extensions, DNS lookups or child processes.
- Container startup and package installation happen only on the disposable CI
  host. This workflow does not authorize or access a project server. Cleanup uses
  the job's Compose project without `down -v`; the runner eventually disposes its
  own volumes. Never copy that cleanup assumption to a shared production machine.

## Local reproduction

Use the commands in [CONTRIBUTING.md](../CONTRIBUTING.md). In addition, if local
PostgreSQL binaries are available:

```sh
python scripts/test_postgres_install.py --pg-bin /absolute/path/to/pgsql/bin --http
```

For an already authorized isolated Compose stack, the smoke checker performs GETs
only and refuses non-loopback targets or external redirects:

```sh
python scripts/check_local_stack.py --url http://127.0.0.1:8088/ --wait-seconds 180
```

It checks website/contact/login/health/static responses and disabled measurement.
It does not submit a form, start a worker, validate queue outcomes or certify UI
quality. The separate HTTP lifecycle test does submit synthetic local inquiries.

本地 YAML/合同测试通过不等于 GitHub CI 成功。首次发布前必须实际执行流水线，
核对 PostgreSQL18 和容器构建/启动结果，并完成秘密、素材许可及真实恢复验收。
当前支持版本、库名和授权边界以项目 README、状态记录及运维文档为准。

## Upstream references

The permission/pinning design follows GitHub's
[secure-use reference](https://docs.github.com/en/actions/reference/security/secure-use).
Actions are from [checkout](https://github.com/actions/checkout),
[setup-python](https://github.com/actions/setup-python) and
[setup-node](https://github.com/actions/setup-node). Runner assumptions come from
the official [Ubuntu24.04 inventory](https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2404-Readme.md).
Package setup follows [PostgreSQL's Ubuntu instructions](https://www.postgresql.org/download/linux/ubuntu/).
