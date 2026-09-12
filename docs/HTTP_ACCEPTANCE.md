# Local website-to-CRM HTTP acceptance

2026-09-12. Candidate source, Windows, PostgreSQL17.11, unique temporary database,
synthetic contacts and accounts only. This is **not** Docker/Nginx/PG18, new-server
or real Meta acceptance.

## Reproduce the automatic checks

With application dependencies installed and a suitable PostgreSQL binary tree:

```sh
python scripts/test_postgres_install.py --pg-bin /absolute/path/to/pgsql/bin --http
node --test scripts/test_form_status.mjs scripts/test_measurement.mjs
```

The harness runs the 14 database lifecycle/scheduler checks, then creates a
separate `newcrown_http_test` database using the actual installer and bootstrap.
It creates two random-password test accounts with administrator/sales permissions
and team membership. There is no shared default password or schema-reset command.

The test-only `serve_local_test.py` exposes the selected website tree and Django
application on one loopback origin. It refuses non-test/non-loopback databases,
application `.env` files and enabled external-I/O settings. An additional Python
TCP guard rejects non-loopback connections in that process. This is a WSGI test
server, not the container deployment server; browser networking is separate.

The existing HTTP verifier now accepts its DSN through `SITEOS_TEST_DATABASE_URL`
so the harness does not put the test password into the command line. It submits
to `/admin/api/leads/forms/project-inquiry/submit/`, matching the actual homepage
and contact JavaScript, rather than relying only on the public API alias.

## Automatic evidence

The final automatic run passed 16 aggregate checks (14 database/scheduler + 2 HTTP):

- Homepage, contact page, Pixel configuration script and CRM login served successfully.
- Public measurement configuration returned browser Meta and Google measurement disabled.
- Invalid submission returned 400 without a row; an accepted synthetic inquiry
  produced one row and replay of that same request returned the existing ID.
- Real HTTP login/CSRF/session flow worked for administrator and sales roles.
- Administrator assigned the lead; sales qualified it with a follow-up task.
- Conversion created one owned company, contact, opportunity, customer source and
  email/phone contact points; repeating conversion created no duplicates.
- Website intake remained `website_form` / `automatic_receive`, while synthetic
  campaign attribution remained separate. The sales user could see the company.
- External integrations stayed disabled and no marketing outbox row was added.

## Actual browser evidence and discovered fix

The harness can keep its isolated server available for bounded browser review:

```sh
python scripts/test_postgres_install.py --pg-bin /absolute/path/to/pgsql/bin --http --review-seconds 900
```

It prints a loopback URL and a private completion-marker path, not passwords.
Closing review or timeout does not by itself prove browser success: browser
observations and database counts must be recorded separately. The server and
cluster stop after the bounded review. Do not use this mode with real contacts.

Observed in the actual browser:

1. Rejected optional marketing consent. Empty homepage submission showed field
   errors rather than successful submission.
2. Filled and submitted a synthetic homepage inquiry. The success message appeared,
   focus moved to it and the inputs reset. No error/warning logs were reported.
3. Found a defect: a later empty submission showed validation errors **and the
   preceding success text**. Both homepage and contact handlers retained that state.
4. Fixed both handlers to clear the preceding result before validating a new
   submission attempt. Active-request duplicate clicks still preserve the pending
   request state. Eight HTML script references now use cache version `20260912a`.
5. Reloaded the fixed homepage, submitted a second distinct synthetic inquiry,
   then empty-submitted again: validation appeared and success-text count was zero.
6. Submitted the full contact-page form with its extra required inquiry/timeline
   fields. Success appeared; another empty submission no longer retained success.
7. At a requested 375×812 mobile viewport, document client/scroll widths both
   measured 360px (scrollbar excluded), form width 328px. Error links and fields
   remained visible without horizontal document overflow. Viewport was reset.
8. With marketing consent rejected, the DOM contained no injected Meta/Google
   measurement scripts. This observation does not establish external event delivery
   or platform deduplication. Browser error/warning logs were empty in these checks.

Browser snapshots/screenshots were captured in the task trace; no private browser
profile or cookies are added to this repository. Four guard-level JavaScript tests
cover clearing stale status and preserving in-flight status for the two handlers;
these and five measurement VM tests passed (9 total). The guard tests are not a
substitute for the above browser interactions.

After stopping the test servers, the same test database was independently
restarted for a read-only persistence check: each of the three distinct browser
submissions had exactly one row, total submissions were four including the
automatic HTTP inquiry, and marketing outbox count was zero. It was stopped again.
The first direct inspection attempt raced with teardown and failed to connect;
the later controlled restart produced the actual count evidence, not that failed attempt.

## Remaining acceptance

Packaged Linux containers/proxy, full visual CRM/role flows, export download,
negative permission paths, authorized real-data restore, live scheduling and
public-source installation still require acceptance. Canonical metadata and
brand/content redistribution configuration also require release review. Nothing
in these local results authorizes deployment, production submissions or publishing.
