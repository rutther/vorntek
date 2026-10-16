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
This does not yet prove complete module/attachment/published-site coverage.

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
