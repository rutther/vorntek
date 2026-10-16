# Continuous integration / 持续集成

`.github/workflows/ci.yml` validates PRs, main pushes, `v0.2.*` tags and manual
dispatches. Two ephemeral Ubuntu24.04 jobs use pinned Actions, read-only
repository permission and no retained checkout credentials.

| Job | Checks |
| --- | --- |
| application | Python lock/install/audit, repository tests, history guard with exact asset approvals, release identity, npm lock/audit, selected browser contracts, full flow-ui tests/build and complete synthetic SQLite suite |
| postgres-and-compose | PostgreSQL18, actual old public-main upgrade, prior RC schema route, fresh install/failed migration/backup restore/HTTP, Compose builds/startup/preflight, Nginx publication/recovery/rollback, restart and worker stop |

The public upgrade baseline is `6d3eefe7e5e124c31d175a1bfc971b58dd197082`
with32 SQL migrations. The harness initializes that actual code, creates synthetic
business rows and a vault value, upgrades with the candidate, compares every old
table's original columns and checks password/vault/task access. The older26-SQL
route and fail-closed installer checks remain.

```sh
git worktree add --detach /YOUR_TEMP/vorntek-public-main 6d3eefe7e5e124c31d175a1bfc971b58dd197082
python scripts/test_release_upgrade.py --baseline-checkout /YOUR_TEMP/vorntek-public-main --candidate-checkout . --pg-bin /YOUR_POSTGRES/bin
```

Use your own temporary path and installed PostgreSQL binaries. Only new local
clusters are used; this is not a running-database upgrade command. Local Windows
PG17 results supplement Linux PG18/Compose rather than replacing them.

No job deploys a website, publishes images, reads production secrets, uses
`pull_request_target`, uploads backups or contacts an advertising provider.
Cleanup stops only the disposable runner's project. Do not copy its cleanup
assumptions onto a shared production server.

Current candidate GitHub CI is pending. Historical runs are preserved in
[CI_HISTORY.md](CI_HISTORY.md); their success belongs only to the named commits.
Current preparation results: [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md).

中文：最终交付须链接准确commit的实际远端结果，不能继承旧绿灯。新库、升级与恢复均
使用合成环境，默认外发关闭。流水线不携带客户站部署凭据。
