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

Brand name, locale labels and logo URL come from the site record/configuration.
The public implementation deliberately does not carry the Hong Kong company's
name, fixed language set, host paths, Meta Pixel injection or production-only
asset assumptions. Generated pages contain no executable marketing script.
Administrators can still configure HTTPS image URLs; rendering records those
URLs but does not fetch them.

## Lifecycle boundary

The current accepted slice stops at an in-memory release and rendered document
map. It does **not** yet provide:

- immutable on-disk release storage and retention;
- private preview serving and traversal-safe asset reading;
- an operator approval/activation transaction;
- integration with the static website build and rollback pointer;
- background scheduling or external publication;
- a claim that CMS edits are live merely because rendering succeeded.

Those concerns must be added as independently tested layers. Activation must
verify the same release digest, write to a versioned staging area, switch a
single pointer only after complete verification and retain the previous release
for rollback. No production path or domain may be used as a default.

## Clean-install contract

The feature uses `markdown-it-py==4.2.0` plus its locked `mdurl==0.1.2`
dependency. Current-host license evidence is bound to the exact lock-file hash in
`docs/review/python-dependencies.json`. The earlier Linux image inventory is
historical evidence for the preceding 24-package lock and must be regenerated
before a new binary image is published.

Tests cover deterministic snapshots, unpublished-row exclusion, duplicate and
path rejection, explicit translation links, safe Markdown, one-H1 output,
preview/live robots behavior, absence of tracking scripts, JSON script-breakout
defense and post-freeze tamper rejection. Database-backed snapshot, immutable
store, private preview and end-to-end activation tests remain later gates.

中文：当前完成的是可审计的文章快照与安全渲染核心，不是“一键发布”。后续必须把
版本化存储、私有预览、静态站构建、原子切换和回滚分别实现并验收；在此之前不得将
渲染成功描述为生产网站已经更新。
