# Public release review

Observed 2026-09-21. **RC1 source is public; the 0.2 candidate is unpushed and its
runtime release gates remain open. Binary-image review also remains open.** This
records evidence and publication boundaries, not a legal certification.

## Current unpushed candidate

- Candidate source commit `e971f9c226a966eb564b473e9f4c4dedaa5d7418`
  passed a clean history audit from immutable tag `v0.1.0-rc.1`: 32 commits,
  609 introduced objects, 359 blobs/path bindings, largest 142,197 bytes and
  zero findings. The scanner includes blobs later deleted, binds reused content
  to every candidate-changed path and never prints matched material.
  See the [exact source-history review](review/SOURCE_HISTORY_REVIEW_20260921.md).
- Current-tree guards separately reject private/backup/key inputs, token shapes,
  credential URLs, known production identifiers and all 45 excluded private
  production paths. Root checks are 66/66; locked Python dependency audit reports
  no known vulnerability at the observed time.
- Actual loopback browser evidence now includes a standard-21 CSV download saved
  and byte-inspected on disk. The earlier browser-save gap is closed for that
  immediate CSV path; queued XLSX and equivalent packaged Linux browser paths
  remain open. See the [browser record](review/BROWSER_ACCEPTANCE_20260921.md).
- This candidate has not passed its remote Ubuntu/PostgreSQL 18/Compose/Nginx
  workflow and has not been pushed or tagged. Do not describe it as released.
- The public repository's read-only API reported private vulnerability reporting
  as disabled on 2026-09-21. Enable it with repository-owner authority or publish
  another real private contact before selecting a supported candidate release;
  the current policy deliberately does not point reporters to a disabled form.

## Source that was published

[Repository](https://github.com/rutther/vorntek) and
[v0.1.0-rc.1](https://github.com/rutther/vorntek/releases/tag/v0.1.0-rc.1) are public.
The tag points to `fe353544d6d37d5cbf01ccecbb157cf30edba6fe`, tree
`459827218ec0b80d287bfaf8aacc864189841bb3`, 444 tracked files.
It has [successful CI](https://github.com/rutther/vorntek/actions/runs/34712023363).
See [installation and version mapping](PUBLIC_DELIVERY.md).

- Publication used newly created, inspected source history; the original private
  repository's history was not copied.
- The complete three-commit RC1 history scan returned nine reviewed non-secret
  storage/test identifiers, not zero alerts. The eight-file fix diff returned
  zero alerts. Later documentation-only changes were reviewed separately; do
  not apply these scan counts automatically to an arbitrary future commit.
- Initial source upload was checked against its frozen Git tree and anonymously
  cloned. Fresh installation needed no copied production config, users, files
  or database. The documented host-only build-network fallback was tested.
- Project code is MIT. Dependencies keep their own terms; all code and bundled
  content are not automatically MIT.

## Content and dependency disposition

| Input | Disposition / evidence |
| --- | --- |
| Former company photographs and logo | Privately retained, excluded from public source and build context; replaced with eight AI originals |
| Inherited QA screenshots/comparison | Kept privately, excluded from source/build; not assumed to depict synthetic people |
| Historical host/content/reset helpers | Retired or made refusing; original sources preserved; [exact disposition](LEGACY_TOOLS.md) |
| Browser libraries | Lucide ISC/Feather MIT, Bootstrap/Popper MIT notices checked against exact packages; Tabler's existing MIT notice retained with the upstream-fetch limitation recorded |
| Python dependencies | Current 26-package lock and host license-file evidence are hash-bound; the previous 24-package Linux image inventory remains version-bound historical evidence and must be regenerated |
| Images and fonts | [AI prompts/hashes](vorntekDemo/README.md); no claim of trademark clearance, exclusive AI rights, engineering certification or exhaustive dynamic font inspection |

The old 123/125-raster counts describe superseded private inputs; they are not the
current published asset count. See [third-party notices](../THIRD_PARTY_NOTICES.md)
and [vendor evidence](review/vendor-packages.json).

## Images are a separate deliverable

The old maintenance image contained an inherited default test private key.
The clean final-stage image omits it from its layers and passed actual independent
backup/restore. It now supplies the demonstration's configured maintenance tools;
existing application containers were not restarted by that configuration change.
[Exact image and scan evidence](review/IMAGE_SECURITY_20260913.md).

No image binary, donor stage, build cache or private recovery archive was uploaded.
The [binary redistribution review](review/BINARY_REDISTRIBUTION.md) now includes
Debian/Alpine package and native-wheel/SBOM observations. Presence of copyright
files or a software bill of materials does not by itself close component-license
and corresponding-source requirements.

## Remaining release gates

- Do not copy real customer data into the fictional public demo. Original private
  restoration remains unperformed pending explicit scope and safe target.
- Repeat the browser flows in the packaged Linux stack and verify an actual queued
  XLSX worker download; the immediate standard-21 CSV save is already evidenced.
- Before any binary publication, resolve notices/source materials, freeze and
  scan exact final images, confirm registry permissions, then independently pull
  and test the published digests. Do not promise future source-delivery services
  on the user's behalf.
- Preserve default-disabled external integrations. Do not describe simulated
  contracts as verified real Meta browser/server deduplication.
- Configure and verify a real private vulnerability-reporting channel and define
  the supported-version policy; GitHub private reporting was disabled when checked.
- Real use needs HTTPS/security/retention controls; public HTTP is synthetic-only.

中文：已公开的是 RC1；当前 0.2 候选及其历史审查尚未推送。标准 21 列即时 CSV
已经有真实浏览器落盘证据，仍未完成的是打包 Linux 环境复验、排队 XLSX 落盘、
原真实数据恢复和预构建镜像交付。未验证的外部能力继续明确标注。

Earlier reviews, failures and remediations remain available in
[the immutable historical report](https://github.com/rutther/vorntek/blob/772cdd40c18d8a096488e760354150be3c6540ad/docs/PUBLIC_RELEASE_REVIEW.md)
and the linked stage evidence; they no longer appear as conflicting current status.
