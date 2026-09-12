# Source and image security evidence — 2026-09-13

Scope: the source tree `f7b78ef98b7c4bd1436b8ddecb5455260a62c138` and its
three locally built images. This is not approval of a later commit, public
registry artifact, dependency vulnerability audit or production-data migration.

## Method and findings

- Official Gitleaks 8.30.1 Linux x64 archive SHA-256:
  `551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb`.
  Reports used full redaction and remain private. Source input was a fresh,
  manifest-verified 440-file archive without private runtime/configuration.
- Source scan returned **nine alerts**, not zero. Manual review identified two
  browser local-storage key names, three synthetic idempotency labels and four
  mocked submission UUIDs. These are not authentication secrets. No broad
  rule/file exclusions were added.
- An archive-only scan was insufficient. The three image archives were unpacked
  into **35 unique layers / 23,784 regular files**, retaining older layers rather
  than scanning only the merged filesystem; extraction did not follow links.
- That layer/config review found the same nine source alerts, a Perl API
  documentation symbol (`COPHH_KEY_UTF8`), two references to the official Python
  image's **public GPG verification-key fingerprint**, and one genuine private-key
  file: the PostgreSQL donor image's generated `ssl-cert-snakeoil.key`.
  The GPG fingerprint alerts belong to the CRM Python base-image configuration,
  not to the maintenance-image environment. None is a user's SSH/admin credential.
- The default test private key is not acceptable in the project's published
  maintenance artifact. Old maintenance images and intermediate donor/build-cache
  artifacts must **not** be published. A later-layer deletion alone is insufficient.

Exact source alert locations at this tree:

| File (under `apps/`) | Lines | Classification |
| --- | --- | --- |
| `website/assets/meta-pixel.js` | 9, 12 | Browser storage names, not secret values |
| `crm/console/test_whatsapp_workspace_v2.py` | 219, 306 | Synthetic idempotency labels |
| `crm/leads/test_customer_pool_services.py` | 609 | Synthetic competing-claim label |
| `crm/leads/tests.py` | 225, 569, 762, 824 | Mock submission UUID fixtures |

## Clean maintenance candidate

`deploy/Dockerfile.maintenance` now copies the sanitized filesystem into a new
`FROM scratch` final image, preserving tools/libraries/license files without the
donor's earlier layers. It removes only the unused generated test key/certificate.
The donor stage and build cache are not release outputs.

Actual test image: `vorntek-maintenance:cleanKey01`, local image ID
`sha256:06af461567d7a7575180a51d295dc6291f1cf680375e6e9a81eac4fc7b415e6d`.
Built from verified f7 source plus the revised maintenance Dockerfile; it is not
claimed to represent the entire later documentation/test tree.

- Actual final archive: **one layer / 9,488 regular files**. Neither removed path
  exists in that layer. Gitleaks returned one alert: the non-secret Perl API
  documentation symbol above. This is reviewed triage, not a zero-alert scan.
- Restricted, read-only, network-disabled smoke checks passed: UID10001, psycopg
  import, key absence, PG18.3 dump/restore binaries and both archive CLI entrypoints.
- Actual private-network PG18 restore from the immutable synthetic checkpoint
  passed into a new empty database. All 70 table contents, 853 live columns,
  1,156 constraints, 249 indexes, 17 triggers and 67 sequence positions matched
  the checkpoint. No restored application/worker services started.
- The same image created and verified a new logical backup of that isolated
  restored database. Current live records and original production were not reset.
- A static regression test protects the donor/final-stage separation; 41 root
  tests passed. The runtime layer/restore checks above are the stronger evidence.

Follow-up: the operator's maintenance/filebackup/filerestore configuration now
selects this exact clean image. Expanded Compose differed only in those three
image fields; existing containers were not restarted. The release-local backup
directory was explicitly created after a refused missing-bind-source attempt and
exact configuration rollback. Both archive CLI help checks and configured
maintenance CLI startup passed; these do not constitute a new database restore.

The image has not been published. Source/history and public CI/clone evidence
subsequently completed for the source pre-release; see [current delivery](../PUBLIC_DELIVERY.md).
[Binary redistribution](BINARY_REDISTRIBUTION.md) remains a separate gate.

References: [official scanner release](https://github.com/gitleaks/gitleaks/releases/tag/v8.30.1),
[scanner documentation](https://github.com/gitleaks/gitleaks).
