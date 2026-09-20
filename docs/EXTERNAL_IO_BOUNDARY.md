# Isolated-environment external I/O review

2026-09-12. Local source and mocked-call evidence, not deployed network acceptance.

`NEWCROWN_ALLOW_EXTERNAL_IO=0` is the candidate's default. It must remain off
during synthetic deployment and authorized private restoration. Integration rows
copied from another environment must not override this policy.

| Path reviewed | Disabled behavior |
| --- | --- |
| Browser Meta Pixel / Google tag configuration | Returns disabled flags and blank IDs; browser script stays silent. |
| Meta CAPI/outbox and lead retrieval | Dispatch stops before processing records; direct lead retrieval refuses before reading credentials. |
| Google credentials, HTTP dispatch and status polling | Refuses credential loading/HTTP; dispatch and polling return without processing records. |
| Email inquiry/reminder and SMTP test actions | Return without constructing/sending messages. |
| Platform diagnostics | Refuses external requests. |
| WhatsApp live Graph/YCloud clients, media and templates | Constructors refuse before credential access; direct HTTP boundaries recheck policy, including previously created clients. Mock fixtures remain network-free. |
| Private article preview | Authenticated reader strips scripts, forms, frames, external navigation/images and rejects CSS network loads; no-store CSP also denies script, connect, frame, object and form targets. |

The final row closes an identified gap: the older send-only WhatsApp switch did
not cover template reads/media downloads or direct transport clients. The global
guard is additional to existing live-send/DEBUG checks, not permission to enable
WhatsApp. No external account or configuration was changed, and no real network
calls were used for verification.

The local search covered ordinary CRM Python HTTP/mail calls outside tests and
standalone helper scripts. It also identified hostname resolution in settings and
the existing preview-build subprocess. This is not an operating-system firewall,
DNS isolation proof, or a guarantee covering arbitrary subprocess/plugin code.
The container's internal network is a separate defense that still requires
Linux/Docker runtime verification. Browser embedded videos or external navigation
are not isolated by a server environment variable.

This switch does not freeze every database writer: inbound webhook storage, mock
processing and exports can still modify local data. Stop web writers and **all**
workers before restoration, then make explicit queue-release decisions. See
[background tasks](BACKGROUND_TASKS.md) and [backup/restore](BACKUP_RESTORE.md).

Tests: `leads.test_external_io_boundary` verifies refusal without secret reads or
HTTP calls, cached-client policy rechecks, factories with a live-send flag present,
both providers, direct template helpers and functioning network-free mocks. The
existing WhatsApp contract suite verifies unchanged enabled/mock behavior with
transports mocked. Current run results are recorded in implementation status;
neither these tests nor source search establish real Meta/WhatsApp acceptance.
