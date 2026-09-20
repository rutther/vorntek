# Article delivery architecture / 文章交付架构

Updated 2026-09-21. This document describes the accepted product-neutral core,
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
activates anything itself. An explicit-version reader supports composition from a
verified live-mode artifact without changing the article pointer.

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

`console/website_release_store.py` and `console/website_candidate.py` freeze the
complete static website source together with a live-mode article artifact. The
composer gives the CMS complete ownership of article namespaces, so withdrawal
cannot retain old source pages; it rejects links/reparse points, unsafe paths,
hard links, case collisions, oversized inputs, missing internal references,
direct HTML/CSS external subresources and changed or extra files. CSS, JavaScript
and images are hashed into the same content-addressed
bundle. A candidate may be built only from a same-site review record whose CMS
source digest is still current and whose translations are complete. The database
records the exact website, article and base-source versions. Candidate building
does not select either internal pointer and does not change a web-server root.

The release center exposes that operation only as a POST action on an explicit,
same-site reviewed article-preview row. It requires `content.read`,
`releases.read` and the independently granted high-risk
`releases.candidate_build` capability. Migration 0030 adds that capability by
replacing the database CHECK constraint in an append-only migration; it does not
rewrite the original access-control migration. Failures expose a generic operator
message while the immutable release audit records retain only a stable error code.

`console/website_selection.py` adds the next lifecycle fact without deploying it:
an immutable, site-scoped candidate-selection event. Migration 0031 keeps
`releases.candidate_select` separate from candidate construction and creates a
database ledger protected by a same-site composite foreign key and an
update/delete rejection trigger. Selection verifies the complete stored candidate,
uses the previously selected version as a compare-and-swap precondition, and uses
a UUID request receipt for replay safety. Updates and rollbacks append new events;
they do not rewrite history, set a release to `live`, change `active.json`, copy
files or touch a web-server root. The authenticated, site-scoped confirmation
page requires the same content/release reads plus `releases.candidate_select`,
re-verifies the artifact before display and again on submission, shows the exact
website/article/base versions and file count, requires a reason, and rejects a
stale expected version without exposing the internal error code. The release list
marks the current choice as `已选择（未部署）` and hides a redundant select action.

`console/website_serving_store.py` now provides the filesystem boundary needed
by the eventual deployment service. Compose gives CRM read/write access to a
dedicated `website_runtime` volume and mounts that same volume read-only in the
Nginx container. The checked-in website is copied into an installation-only
`releases/bundled` baseline when the volume is first created. Verified candidates
are copied byte-for-byte into immutable digest-named directories; a second source
verification closes the copy/switch race, and Nginx reads only the controlled
relative `current` symlink. Switching and rollback replace that symlink with one
same-filesystem rename. Unknown owners, stale expected versions, changed/extra
files, unsafe pointers, links and source drift fail closed. The deployment
manifest is dot-prefixed and the existing Nginx hidden-path rule denies it.

This adapter cannot authorize itself merely because a candidate directory exists;
only the database-gated deployment service may call it.

`console/website_deployment.py` and migration 0032 add the database gate above
that primitive. Deployment has a separate high-risk `releases.deploy` capability
contract and accepts only the latest immutable candidate-selection row. A durable
`prepared` operation binds the request UUID, site, selection, release, target,
previous deployed version, actor and reason before any serving write. While it is
prepared, both a different deployment and a new candidate selection are refused.
After the filesystem CAS succeeds, a second transaction verifies the served bytes,
appends an immutable receipt and performs the sole allowed operation transition to
`activated`. A crash after the pointer switch leaves `prepared`; replaying the same
UUID recognizes the target, re-verifies it and finishes the receipt. A failure
known to occur before switching becomes terminal `failed` and permits a new request.
Database triggers reject operation rewrites/deletes, invalid transitions, receipt
fields that do not match the prepared operation, and receipt updates/deletes.

`console/website_deployment_forms.py` and the authenticated deployment route add
the operator boundary. The route requires content read, release read and the
independently granted `releases.deploy` capability together. It re-verifies the
candidate before rendering and again through the service on submission, requires
an 8–500 character reason and a v4 UUID, and posts the exact selection/deployed
preconditions. Its page states that confirmation changes this installation's
public website; it does not imply or authorize a Hong Kong production deployment.
Release rows distinguish selected, recovery-pending and deployed states. A
prepared operation reopens with its original UUID and read-only reason and only
the original operator can resume it. Stale and internal failure codes are replaced
with safe operator messages. No worker or scheduler invokes deployment.

Linux PostgreSQL/Compose must still prove the SQL triggers, volume copy-up and real
Nginx behavior before this lifecycle is accepted for a release.

Brand name, locale labels and logo URL come from the site record/configuration.
The public implementation deliberately does not carry the Hong Kong company's
name, fixed language set, host paths, Meta Pixel injection or production-only
asset assumptions. Generated pages contain no executable marketing script.
Administrators can still configure HTTPS image URLs; rendering records those
URLs but does not fetch them. The private preview strips those external image
loads so opening it does not contact their hosts.

## Lifecycle boundary

The current accepted slice includes an audited immutable whole-site candidate,
a non-deploying selection ledger, a verified serving-store primitive, its
crash-resumable database deployment service and an exact-permission confirmation
route.
It does **not** yet provide:

- background scheduling or external publication;
- a claim that CMS edits are live merely because rendering succeeded.

Those concerns must be added as independently tested layers. Both private storage
pointers are internal artifact-selection primitives, not authorization to publish
a website. The future serving adapter must switch only to an operator-selected,
verified whole-site version and retain the previous website release for rollback.
No production path or domain may be used as a default.

## Preview configuration and persistence

Compose stores previews, live-mode article artifacts and whole-site candidates
under `crm_runtime`, which is included in the existing file-backup workflow. The
CRM image copies the complete checked-in Vorntek website into a read-only source
root for deterministic composition. The separate `website_runtime` volume is a
derived serving cache: Nginx mounts it read-only, and it must be reconstructed
from the restored database ledger plus the backed-up verified candidate store
rather than treated as original business data. The private preview reader still exposes
only referenced CSS/bitmaps and strips executable or external behavior; candidate
composition, by contrast, hashes the exact JS/CSS/image source intended for a
future serving adapter. A real installation must set
`NEWCROWN_ARTICLE_PUBLIC_ORIGIN` to its actual HTTPS public origin. The checked-in
`https://vorntek.example` value is deliberately non-live. Source development
defaults the asset root to `apps/website`; deployments may override both roots
only with absolute, privately owned paths.

There is no artifact-retention policy yet. Preview HTML remains independent from
its current asset root, while each whole-site candidate freezes and verifies all
of those resources. Backup inclusion preserves the private stores, but a restored
candidate is not automatically selected or served. Vorntek clean installs declare
shared base-page routes (for example `/company/`) while translated articles keep
their own language paths; other products default to localized base-page routes
unless explicitly configured.

## Clean-install contract

The feature uses `markdown-it-py==4.2.0` plus its locked `mdurl==0.1.2`
dependency. Current-host license evidence is bound to the exact lock-file hash in
`docs/review/python-dependencies.json`. The earlier Linux image inventory is
historical evidence for the preceding 24-package lock and must be regenerated
before a new binary image is published.

Fifty-five focused article/website-delivery tests cover deterministic snapshots, unpublished-row
exclusion, duplicate and path rejection, explicit translation links, safe
Markdown, one-H1 output, preview/live robots behavior, absence of tracking
scripts, JSON script-breakout defense, store ownership, generic locale paths,
CAS activation, update/withdrawal/rollback, changed/extra file rejection, stale
build inputs, preview-to-release refusal, deterministic database-backed preview
records, traversal rejection, network-silent rewriting, shared/localized base
routes, full Vorntek composition, asset freezing, withdrawal, missing-link
rejection, candidate source-drift refusal, site scoping and tamper detection.
Route-policy tests separately cover the exact three-capability gate, HTTP methods
for both private preview and candidate build, plus private security headers;
selection service tests cover CAS, replay, rollback, site isolation, immutable
history and artifact/record mismatch refusal. Four cross-platform serving-store
tests cover materialization, stale CAS, tamper/extra-file refusal and rollback;
the actual POSIX relative-symlink case is explicitly left for Linux CI/container
acceptance because this Windows host cannot create it.
Eight deployment-service tests cover current-selection binding, stale conditions,
idempotent replay, crash-after-switch resumption, terminal pre-switch failure,
selection/deployment interlock, update/rollback chains and immutable model behavior.
Route and template tests additionally cover the exact deployment capability,
reason/UUID form, current-selection binding, original-request recovery and safe
error wording.
PostgreSQL/Compose triggers and end-to-end whole-site activation remain later gates.

中文：当前完成的是可审计快照、安全渲染、私有不可变存储、固定版本构建输入、经过权限
控制且阻断外部网络请求的后台私有预览，以及把官网 HTML/CSS/JS/图片与正式模式文章
一起固化的整站候选、不产生部署副作用的候选选择审计链和操作员确认页面，以及尚未接入
路由的只读挂载/不可变复制/原子指针 serving-store 基础；候选构建与选择是两项独立高风险
权限。数据库门禁、可恢复操作状态、独立部署收据和精确权限确认页已经接通，但没有后台自动
部署，且尚未完成 Linux PostgreSQL/Nginx 容器端到端验收；在该验收前仍不能称为发布完成。
后续须验证首次卷初始化、真实原子切换、中断续办、回滚和重启持久化；
在此之前不得将 artifact、私有预览、候选构建或底层原子指针描述为生产网站
已经更新。
