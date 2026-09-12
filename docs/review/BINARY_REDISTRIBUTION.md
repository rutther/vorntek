# Binary redistribution review / 容器二进制分发审查

Observed 2026-09-13. **No prebuilt images have been published.** Source is public
and builds successfully; this is a separate review, not a vulnerability audit or
legal certification. It does not change the website/CRM feature set.

## Exact image observations

Read-only, network-disabled inspection containers had no business/configuration
mounts. The existing application services were not modified.

| Image | Immutable local image ID | Observed OS packages |
| --- | --- | --- |
| CRM cloneA132 | `sha256:3b16fb33176639a1188cd48fff0f59d0c788b1993646e31172be43e521b9e949` | Debian 105 packages; 105 matching copyright files |
| Website cloneA132 | `sha256:f62e25a3162cad227f8301097d3614868f134b8a7aa41ae0dd9e51c0ebba6f79` | Alpine 68 packages; six files in license/documentation directories |
| Maintenance cloneA132 | `sha256:deb6e3791e97bd98d78e48d787ad0a6176d0b325f77ecfaa27aa921d2d8c3fd2` | Debian 161 packages; 161 matching copyright files |

Matching file counts do not establish complete notice text or corresponding-source
delivery. These IDs are not registry digests and do not describe arbitrary rebuilds.

Alpine metadata contains GPL/LGPL, permissive, dual and compound license expressions.
For one exact sample, installed apk-tools 2.14.6-r3 maps to
[this pinned APKBUILD](https://github.com/alpinelinux/aports/blob/41847d6ccff08940b5bf1ba0d6005e95897039f9/main/apk-tools/APKBUILD).
The complete file was read, versions matched, and its source list includes an
upstream archive, two patches and a supporting file with four SHA-512 declarations.
The recipe is not the source archive itself: those inputs have not yet been
downloaded and checksum-verified for this release. No recipe was executed.

## Native wheels and their upstream SBOMs

Actual CRM-image files were hashed, not inferred only from requirements.lock:

| Installed distribution | Native files | License files | Upstream SBOM observations |
| --- | --- | --- | --- |
| psycopg-binary 3.3.3 | 17 (15 bundled libraries, two extensions) | One LICENSE.txt | CycloneDX: ten component entries, duplicates and missing license fields |
| cryptography 50.0.1 | One Rust extension | Three | CycloneDX: 39 Rust entries and one separately declared OpenSSL component |
| cffi 2.1.1 | One extension | One | No SBOM-named file in its installed distribution listing |

Runtime observation: Psycopg's binary implementation reports libpq loaded/build
version 180000; cryptography reports OpenSSL 4.0.2. This is not a claim that those
versions are vulnerability-free. The outer Python package license does not
automatically describe every bundled native library. The upstream
[Psycopg installation guide](https://www.psycopg.org/psycopg3/docs/basic/install.html)
explains the self-contained binary installation versus system-linked alternatives.

The psycopg SBOM does not by itself establish complete mapping for all 15 libraries.
Its bundled LICENSE.txt is LGPLv3 text and does not name the examined native-library
families; this is an observation about that file, not proof that every other image
location lacks a notice. Rust SBOM entries may include build-time dependencies;
counting all entries as linked runtime code would be incorrect. OpenSSL's separate
SBOM version matches runtime, but its license field is empty and its source URL
has not yet been validated as a delivered source archive.

## Requirements before image publication

1. Freeze exact final layers/configuration; repeat security checks for that input.
2. Preserve applicable copyright, license and NOTICE materials for actual bundled
   components, including license exceptions and compound expressions.
3. Complete version-linked source/patch/build provenance and the source delivery
   required by the applicable licenses; a source URL or SBOM alone is insufficient.
   Do not make a continuing source-supply promise on the operator's behalf.
4. Confirm the registry and scoped publishing permissions. Publish only reviewed
   final images, never the old maintenance image, donor stages or private caches.
5. Pull the published digests into an independent installation and verify them.

No registry credentials or new publishing permissions were created during this
review. Private evidence remains outside Git and contains no production-data
restoration. The operator is deciding whether prebuilt image distribution remains
required in addition to the already working source-build delivery; until then,
this item is open, not silently removed from the plan.

中文：源码可构建不等于容器二进制已发布。系统包、wheel内原生库和上游SBOM已经实际
检查，但组件声明、版本对应源码和最终镜像发布/拉取仍有缺口。没有将所有依赖改标MIT，
没有发布镜像，也没有因这份审查改动现用网站或数据库。
