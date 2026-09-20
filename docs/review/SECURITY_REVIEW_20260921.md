# Release-candidate security review — 2026-09-21

Scope: the unpushed `0.2.0-dev.1` source candidate after the Hong Kong parity
work. This is a source/configuration review plus local synthetic tests. It is not
a penetration test, a review of private production data, or proof that the
current images and Compose stack have run successfully on Linux.

## Findings resolved in this review

1. **Python security patch lag.** `pip-audit 2.10.1` reported 23 advisory
   records (including duplicate aliases/records) against one locked package,
   Django 5.2.13. The official supported-version page identifies 5.2.17 as the
   current 5.2 LTS patch. Django's 5.2.17 notes record one high-, two moderate-
   and one low-severity fix in 5.2.16. The direct requirement and complete lock
   now use 5.2.17; the host dependency/license inventory is rebound to the new
   lock hash. A second audit resolved 25 distributions and returned **no known
   vulnerabilities**. CI now installs pinned `pip-audit==2.10.1` and audits the
   application lock on every candidate run.
2. **Inconsistent provider-error redaction.** The dedicated event console
   already removed credentials, bearer values, payload fields, email addresses
   and phone numbers, but legacy lead list/detail and system-admin workbench
   paths still exposed raw `last_error`. All user-facing outbox/inbound paths and
   new audit snapshots now use the same bounded redactor. Tests cover email,
   phone and token-shaped values. Existing private database rows are not
   rewritten; retention rules remain responsible for their lifecycle.
3. **Access-log query leakage.** Default Nginx/Gunicorn combined request logs can
   include query strings, referrers, client addresses and user agents. The
   candidate now emits a privacy-minimal access record containing only timestamp,
   method, normalized path without query arguments, protocol, status and byte
   count. Database audit rows retain authenticated business-action evidence and
   record method/path only, never request bodies or query strings.
4. **Request-limit mismatch.** Nginx previously allowed 30 MiB everywhere while
   application contracts ranged from 2 MB webhooks to 128 MiB video/3D assets.
   This both overexposed ordinary endpoints and made supported large assets
   unreachable. The default gateway limit is now 2 MiB. Only exact authenticated
   upload routes receive bounded multipart headroom: 129 MiB assets, 51 MiB CRM
   attachments, 26 MiB paused WhatsApp attachments, 17 MiB article ZIPs, 11 MiB
   customer imports and 9 MiB article-cover uploads. Application content,
   extension, archive-entry and uncompressed-size checks remain authoritative.
   Django also explicitly caps parsed non-file data at 2 MiB, in-memory files at
   1 MiB, fields at 1,000 and uploaded files at 20.
5. **Optional SMTP secret transport.** `SITEOS_EMAIL_HOST_PASSWORD` now uses the
   same ambiguity-refusing secret loader as database, Django and vault keys, so
   operators may supply `SITEOS_EMAIL_HOST_PASSWORD_FILE` through a private
   Compose override instead of exposing the value in the environment.

## Reviewed control map

| Boundary | Current source evidence | Remaining runtime evidence |
| --- | --- | --- |
| Roles and high-risk actions | Site/locale-scoped role queries; separate candidate-build, selection and deployment capabilities; stale and replay guards | Re-run real PostgreSQL trigger/permission paths in the current Linux candidate |
| Container privilege | CRM processes run as UID/GID 10001, read-only root, `/tmp` tmpfs, all capabilities dropped and `no-new-privileges`; maintenance is non-root; the only published port is loopback | Confirm effective users, mounts, capabilities and Nginx startup from expanded Compose |
| Network and external I/O | Application services use an internal network; external I/O, scheduled tasks and WhatsApp live sending are disabled by default; file archive jobs have no network or secrets | Confirm runtime network membership and disabled-writer behavior after restart |
| Secrets | Four generated values are unique, not printed, ignored by Git/build context and mounted as Compose secrets; app services never receive the database-admin secret; direct/file ambiguity is refused | Verify effective secret mounts/permissions without displaying values; define a private SMTP secret override only if SMTP is enabled |
| Inputs and storage | Per-route gateway caps, Django parser caps, endpoint size/type/archive checks, storage allowlists, traversal/link/hardlink/case-collision refusal | Run `nginx -t`, container upload boundary probes and persistent-volume checks on Linux |
| Logs and audit | Privacy-minimal access log; stable generic storage errors; bounded event-error redaction; audit request metadata excludes body/query | Inspect current container log samples using synthetic markers only |
| Dependencies | Exact Python lock, host license evidence, `pip check`, local `pip-audit` zero-known-vulnerability result, and CI audit step | Rebuild/reinventory Linux distributions and scan final OS/image layers for the exact candidate |

## Verification performed

- `python -m pip check`: no broken requirements after upgrading Django.
- `python -m pip_audit -r apps/crm/requirements.lock --progress-spinner off`:
  no known vulnerabilities after the upgrade.
- 55 root distribution/CI/recovery checks passed.
- 811 Django synthetic SQLite tests passed with eight explicit environment
  skips; Django system checks reported no issues.
- 57 focused workbench/lead/marketing/packaging tests passed after the redactor
  was isolated into its own boundary module.

The scanner result is time-bound and covers Python package advisories only. It
does not clear base-image OS packages, browser vendor files, unpublished image
layers or future advisories. The Nginx route configuration and Compose privilege
state remain runtime-unaccepted until the current commit runs in the disposable
Linux job.

References:

- [Official Django supported versions](https://www.djangoproject.com/download/)
- [Django 5.2.17 release notes](https://docs.djangoproject.com/en/5.2/releases/5.2.17/)
- [pip-audit](https://github.com/pypa/pip-audit)
