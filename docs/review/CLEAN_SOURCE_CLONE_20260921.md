# Clean source-clone acceptance — 2026-09-21 / 纯净源码克隆验收

This record proves that the candidate source at commit
`ac83ca404812ee059e952826ed56b0ef3d303878` can be reconstructed and tested from
a separate Git clone without untracked workspace inputs. It is **source-only
Windows evidence**, not anonymous GitHub, Linux, PostgreSQL, Compose, Nginx or
production acceptance.

本记录证明候选源码提交 `ac83ca404812ee059e952826ed56b0ef3d303878` 可从独立 Git
克隆重新安装并测试，不依赖原工作区的未跟踪文件。它仅属于 **Windows 源码证据**，不是
匿名 GitHub、Linux、PostgreSQL、Compose、Nginx 或生产验收。

## Isolation and environment / 隔离与环境

- The repository was cloned with `git clone --no-local --no-hardlinks` into a
  newly generated directory under the Windows user temporary directory, then
  checked out detached at the exact commit above.
- A new `.venv` was created inside the clone. Dependencies were installed only
  from the tracked `apps/crm/requirements.lock` and `requirements-dev.txt`.
- Environment: Windows 11 AMD64, Python 3.13.15, pip 26.2.1 and Node 24.19.0.
- pip was allowed to use its local download cache. This proves a fresh virtual
  environment install, but not a cache-free or offline installation.
- No production host, database, Meta asset, email recipient or WhatsApp
  destination was contacted or changed.

## Observed results / 实测结果

1. `python -m pip check` reported no broken requirements.
2. `python scripts/release_manifest.py --require-clean` passed at the exact
   commit. The manifest reported 517 tracked files and source-tree SHA-256
   `6ec0ea0a2a31d10ca5a8b8efbe6d971d544fdc17d9270e519c2ef78c48417930`.
3. Repository unit/distribution/CI/documentation tests: **63 passed**.
4. Node browser-script contracts: **23 passed**.
5. `scripts/run_application_tests.py`: **806 tests in 520.232 seconds**, eight
   explicit skips and zero failures. Django system checks reported no issues.
6. After all checks, `git status --short --branch` showed only detached HEAD and
   no changed or untracked files. The tests did not contaminate the source clone.

## What this does not prove / 本记录不证明的内容

- The commit was available only in the local repository, so the clone was not an
  anonymous network clone of GitHub. Repeat that check against the exact pushed
  commit before release.
- This host has no usable Linux container/PostgreSQL runtime. PostgreSQL 18 empty
  install, `0.1.0-rc.1` upgrade, independent restore, Compose build/start/health,
  Nginx serving, restart persistence and queued XLSX browser download remain open.
- Candidate CI declares Ubuntu 24.04 and Python 3.12.12; this Windows/Python 3.13
  run does not replace that remote job.
- No production deployment or real-data testing was authorized or performed.

后续必须在推送后的精确远程提交上重复匿名克隆，并在一次性 Linux 环境完成 PostgreSQL
18、Compose、Nginx、升级、恢复和队列导出验收；本记录不能替代这些门槛。
