# Article delivery architecture / 文章交付架构

Updated 2026-09-20. This document describes the accepted product-neutral core,
not a production publishing authorization.

## Accepted core

`console/article_delivery.py` converts explicitly published CMS rows into a
deterministic, digest-bound release contract. It requires exactly one default
locale, explicit content identities, unique public paths, an HTTPS public origin
and safe asset URLs. Missing translations remain missing; content is never
silently copied from another language.

`console/article_rendering.py` verifies that digest before rendering article and
language-index documents. Markdown raw HTML is disabled, executable link schemes
are rejected, JSON-LD is escaped against script breakout, an article page has one
top-level heading, and preview output is `noindex,nofollow`. The renderer performs
no file, network or database writes.

`console/article_release_store.py` stages those documents in a private,
owner-marked, content-addressed store. It rejects filesystem links/reparse points,
unknown or locale-hard-coded artifact paths, unexpected files and changed bytes.
Activation uses an expected-current compare-and-swap pointer, so a stale operator
tab cannot replace a newer activation. Withdrawal is represented by a new empty
release; reads never search an older version for missing content. Preview and live
stores have distinct ownership markers, and live staging refuses incomplete
language sets.

`console/article_build_input.py` returns one pinned, verified active version for a
later whole-site composer. It checks the pointer before and after reading every
hash-verified UTF-8 artifact, rejects preview artifacts in release mode and never
activates anything itself.

Brand name, locale labels and logo URL come from the site record/configuration.
The public implementation deliberately does not carry the Hong Kong company's
name, fixed language set, host paths, Meta Pixel injection or production-only
asset assumptions. Generated pages contain no executable marketing script.
Administrators can still configure HTTPS image URLs; rendering records those
URLs but does not fetch them.

## Lifecycle boundary

The current accepted slice stops at a private immutable store and read-only build
input. It does **not** yet provide:

- private preview serving and traversal-safe asset reading;
- an operator approval/activation transaction;
- integration with the static website build and rollback pointer;
- background scheduling or external publication;
- a claim that CMS edits are live merely because rendering succeeded.

Those concerns must be added as independently tested layers. The storage pointer
is an internal artifact-selection primitive, not authorization to publish a
website. The future composer must consume the complete pinned file set, stage a
whole website, switch its own pointer only after verification and retain the
previous website release for rollback. No production path or domain may be used
as a default.

## Clean-install contract

The feature uses `markdown-it-py==4.2.0` plus its locked `mdurl==0.1.2`
dependency. Current-host license evidence is bound to the exact lock-file hash in
`docs/review/python-dependencies.json`. The earlier Linux image inventory is
historical evidence for the preceding 24-package lock and must be regenerated
before a new binary image is published.

Thirty-one focused tests cover deterministic snapshots, unpublished-row
exclusion, duplicate and path rejection, explicit translation links, safe
Markdown, one-H1 output, preview/live robots behavior, absence of tracking
scripts, JSON script-breakout defense, store ownership, generic locale paths,
CAS activation, update/withdrawal/rollback, changed/extra file rejection, stale
build inputs and preview-to-release refusal. Database-backed snapshot, private
preview and end-to-end whole-site activation tests remain later gates.

中文：当前完成的是可审计快照、安全渲染、私有不可变存储和固定版本构建输入，不是
“一键发布”。后续必须把私有预览、整站静态构建、发布授权、原子切换和整站回滚分别
实现并验收；在此之前不得将文章 artifact 激活描述为生产网站已经更新。
