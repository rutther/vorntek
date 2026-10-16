# Optional synthetic example bundle

The integration candidate includes `console/data/general_examples.json`, separate
from application logic. Empty installation does not import it automatically.
All contacts and website references use the reserved `example.invalid` domain.
The importer rejects real contact routes and credential fields.

Plan only (no database access):

```sh
python manage.py seed_example_bundle
```

For a separately configured synthetic target, using an existing administrator:

```sh
python manage.py seed_example_bundle --apply --synthetic-only --site siteos_demo --owner YOUR_EXISTING_ADMIN
```

All external delivery and scheduled jobs must already be disabled. The command
does not change those switches, create an account, change credentials, activate
an integration, apply a runtime binding, or pretend a provider received events.
Existing conflicting form, team, company or content identities are not replaced.

The data covers configurable personal/organization/support forms, fictional
organizations and contacts, inquiry stages and a recycle-bin example, individual
and corporate opportunities, open/in-progress/completed/canceled tasks, three
same-template loop instances, and English/Chinese draft articles and pages.
Business rows, workflows and persistence are real; the business stories are not.
Example inquiries use the existing `disabled` notification policy, not a new
delivery status. The recycle-bin scenario calls the real, audited trash command.
Managers can select up to 100 currently visible leads, move unconverted records
to the recoverable trash, then restore them. Trashed leads leave ordinary queues;
their activities, pending tasks and delivery facts remain stored. Restore retains
the original owner, stage and task state; normal delivery policy still applies,
and previously successful deliveries are not replayed.
This base pack does not include file examples; the separate optional extension
below adds them without changing an already installed general-v1 digest.

## Repeat import and bounded reset

An append-only SQL migration adds a per-site ownership ledger. It records exact
row identities, source digest and row fingerprints, not passwords or secrets.
Repeating the same import does not add duplicates or overwrite later edits.
A changed installed bundle is refused instead of silently migrating user data.

```sh
python manage.py seed_example_bundle --apply --synthetic-only --site siteos_demo --owner YOUR_EXISTING_ADMIN --reset
```

Reset currently means **refresh pristine scenario dates in place**, not uninstall
or purge: IDs, configured values and immutable sales/audit histories are retained.
It renews relative inquiry/open-task dates and synchronizes next-step summaries.
Any later edit, missing row or non-example reference causes the whole reset to
be refused before updates; accounts, credentials, original site settings and
unrelated business rows are never reset. FK checks include physical consumers
not represented by the current Django models. No history trigger is disabled.

This describes integration-candidate behavior. Full PostgreSQL, deployment,
recycle-bin controls and complete example coverage require their own observed
acceptance; the importer itself does not prove those results.

## Optional content and file extension

`console/data/content_examples.json` is a second independently versioned pack.
It requires the general pack above and the same existing administrator. It does
not update the general pack, its records, the account, site configuration or
credentials. It is not automatically loaded by a blank installation.

```sh
python manage.py seed_content_examples
python manage.py seed_content_examples --apply --synthetic-only --site siteos_demo --owner YOUR_EXISTING_ADMIN
```

The default command is a database/storage-free plan. Writing requires external
delivery, schedulers, live WhatsApp sending and site integrations to remain off.
The command refuses conflicting identities and pre-existing matching asset or
private-attachment content instead of adopting another operator's files.

Included examples are newly authored, industry-neutral and explicitly synthetic:

- A valid 480×240 PNG collaboration-card image and a valid glTF 2 binary triangle
  (a geometric exercise, not a company/product model).
- Two UTF-8 private notes attached to an inquiry and an opportunity tracked by
  the general pack, using the normal private-attachment service.
- English/Chinese draft versions of one article, saved through the real editor,
  with asset-library cover bindings. Preview/publish are still explicit operator
  actions; importing examples does not deploy or publish a website.
- A disabled 3D view profile and disabled homepage placement, created through the
  existing configuration forms. They do not replace an existing slot or activate
  public content. The historical slot identifier is technical compatibility, not
  a required industry classification.

The extension ledger tracks exact row fingerprints and file paths/hashes/sizes.
Repeated import retains later edits and adds no rows/files. Its `--reset` option
means **validate pristine ownership and retain all rows, identifiers and bytes**;
it does not undo edits, erase history, uninstall files or publish anything. An
edited/missing row or file, later non-example FK reference, changed source pack
or incomplete ownership prevents reset before any update. The general pack may
also refuse its own reset after extension attachments reference it; this is
intentional cross-pack protection, not permission to detach later work.

All writes are one database transaction using existing upload services. Files
cannot be rolled back by SQL: an interrupted/failed import leaves durable upload
recovery markers. Use the existing bounded asset/private-upload recovery commands
against the same explicitly configured storage roots and site, with PostgreSQL
available. Never remove unknown files or delete an entire storage directory.
The journals distinguish committed references from safe owned rollback orphans.
Missing DB outcome is not authorization to clear a file.

This extension provides genuine editable content/files, not external platform
acceptance, a complete example of every module, or final deployment/restore proof.
