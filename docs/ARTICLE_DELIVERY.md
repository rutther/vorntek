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

`console/article_preview.py` and `console/article_preview_reader.py` add an
authenticated, site-scoped private preview of the complete published-article
snapshot. Generation and reading require content read, release read and preview
build capabilities together, and every readable version needs a version-bound
review record. The reader verifies the immutable artifact before
and after reads, rewrites only manifest-known article links, serves only resources
actually referenced by the frozen HTML, rejects traversal and unsafe CSS, and
strips scripts, forms, frames, external navigation and external images. Preview
responses are private/no-store and carry a deny-by-default CSP; no active release
pointer is changed.

Brand name, locale labels and logo URL come from the site record/configuration.
The public implementation deliberately does not carry the Hong Kong company's
name, fixed language set, host paths, Meta Pixel injection or production-only
asset assumptions. Generated pages contain no executable marketing script.
Administrators can still configure HTTPS image URLs; rendering records those
URLs but does not fetch them. The private preview strips those external image
loads so opening it does not contact their hosts.

## Lifecycle boundary

The current accepted slice stops at authenticated, network-silent private preview
and read-only build input. It does **not** yet provide:

- an operator approval/activation transaction;
- whole-site composition with frozen CSS/images and a rollback pointer;
- background scheduling or external publication;
- a claim that CMS edits are live merely because rendering succeeded.

Those concerns must be added as independently tested layers. The storage pointer
is an internal artifact-selection primitive, not authorization to publish a
website. The future composer must consume the complete pinned file set, stage a
whole website, switch its own pointer only after verification and retain the
previous website release for rollback. No production path or domain may be used
as a default.

## Preview configuration and persistence

Compose stores preview artifacts under `crm_runtime` and includes that volume in
the existing file-backup workflow. The CRM image copies only Vorntek
`styles.css` and bitmap assets into a read-only preview asset root; it does not
copy website JavaScript or measurement code. A real installation must set
`NEWCROWN_ARTICLE_PUBLIC_ORIGIN` to its actual HTTPS public origin. The checked-in
`https://vorntek.example` value is deliberately non-live. Source development
defaults the asset root to `apps/website`; deployments may override both roots
only with absolute, privately owned paths.

There is no preview-retention policy yet. Preview HTML is immutable and
content-addressed, but CSS/images are read from the current configured asset root
and are not yet frozen into the article manifest. Backup inclusion therefore
preserves existing artifact directories but does not make this slice a complete,
reproducible website release. The whole-site composer must bind and verify those
resources before activation can be considered.

## Clean-install contract

The feature uses `markdown-it-py==4.2.0` plus its locked `mdurl==0.1.2`
dependency. Current-host license evidence is bound to the exact lock-file hash in
`docs/review/python-dependencies.json`. The earlier Linux image inventory is
historical evidence for the preceding 24-package lock and must be regenerated
before a new binary image is published.

Forty-one focused article tests cover deterministic snapshots, unpublished-row
exclusion, duplicate and path rejection, explicit translation links, safe
Markdown, one-H1 output, preview/live robots behavior, absence of tracking
scripts, JSON script-breakout defense, store ownership, generic locale paths,
CAS activation, update/withdrawal/rollback, changed/extra file rejection, stale
build inputs, preview-to-release refusal, deterministic database-backed preview
records, traversal rejection, network-silent rewriting and tamper detection.
Route-policy tests separately cover the exact three-capability gate, HTTP methods
and private security headers. PostgreSQL/Compose and end-to-end whole-site
activation tests remain later gates.

中文：当前完成的是可审计快照、安全渲染、私有不可变存储、固定版本构建输入，以及
经过权限控制、阻断外部网络请求的后台私有预览，不是“一键发布”。后续仍须把整站资源
固化、发布授权、原子切换和整站回滚分别实现并验收；在此之前不得将文章 artifact 或
私有预览描述为生产网站已经更新。
