# Candidate source-history review — 2026-09-21 / 候选源码历史审查

This review covers the unpushed candidate history from trusted public tag
`v0.1.0-rc.1` (`fe353544d6d37d5cbf01ccecbb157cf30edba6fe`) through source
commit `6faa1367bf40831676d1a965e3c7af052b14e69b`. It is a secret/privacy
and distribution-input guard, not a legal certification or runtime acceptance.

本审查覆盖已公开可信标签 `v0.1.0-rc.1` 至尚未推送的源码提交 `6faa136`。它检查
秘密、隐私和公开分发输入边界，不是法律认证，也不等于运行环境验收。

## Exact result / 精确结果

The clean-tree command

```sh
python3 scripts/audit_release_history.py --base v0.1.0-rc.1 --require-clean
```

reported:

- 35 candidate commits;
- 634 unique introduced Git objects;
- 369 unique blobs and 369 changed blob/path bindings, all treated as text by
  the scanner;
- largest blob: `apps/crm/console/views.py`, 142,197 bytes;
- one-megabyte maximum newly introduced blob policy;
- **zero findings**.

The scanner inspects objects introduced anywhere in the range, including a blob
that was committed and later deleted. It binds every candidate blob version to
every path changed to that version, so reusing test content under a runtime path
cannot hide a path-specific rule. It refuses private-key markers, common AWS,
GitHub and Meta live-token shapes, credential-bearing database/cache URLs outside
test fixtures, tracked environment/private/runtime/backup/key/archive paths,
known production identifiers inside deployable inputs and oversized blobs. A
finding reports only its category, object ID and path; matched material is not
echoed.

审计器检查整个范围中曾出现的对象，所以“先提交、后删除”的 blob 也不会逃过检查。
发现项只报告类别、对象 ID 和路径，不回显匹配到的秘密内容。

## Independent contracts / 独立合同

- Three synthetic repository tests prove a clean range passes, a deleted secret
  blob still fails, and blocked paths/runtime production identifiers/large blobs
  fail without secret echo.
- The existing current-tree distribution suite separately binds the 45-path
  private-exclusion ledger, blocked file classes, token shapes, credential URLs,
  production identifiers, dependency/license evidence and generated website.
- Candidate CI now performs a full-history checkout and repeats this audit from
  the immutable RC1 tag before application regression.

## Limits and next evidence / 限制与后续证据

- Pattern scanning cannot prove the absence of every possible secret or personal
  datum. Human diff/provenance/license review and current-tree tests remain
  required.
- This is not malware analysis, dependency vulnerability scanning, container-layer
  inspection or binary-redistribution approval.
- The report document is necessarily committed after the exact audited source
  commit. The pushed candidate CI must scan the final commit, including this
  report, and its remote result must be recorded before release.
- PostgreSQL 18, Compose, Nginx, upgrade and independent restore acceptance remain
  separate open runtime gates.
