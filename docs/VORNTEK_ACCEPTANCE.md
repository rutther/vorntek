# Vorntek local acceptance / 本地验收

Observed 2026-09-13 (Asia/Shanghai). Local candidate only, not a deployment or
public release. Exact source is retained in the candidate Git index after this stage.

## Changes verified

- Replaced the old public website with 15 bilingual Vorntek pages and eight
  original AI images; original files are preserved in a private ignored archive.
- Shared industrial inquiry form with seven camelCase business identifiers,
  required project message and optional application context. No beverage-capacity
  requirement for industrial forms; legacy form contracts remain compatible.
- CRM login/sidebar branding, customer import-template title, exports and
  notification text use Vorntek. Industrial lead detail displays application
  context instead of fictitious beverage capacity/container requirements.
- Server canonicalizes industrial product labels and rejects missing, unknown or
  non-scalar business identifiers as validation errors.
- Browser FormStart/Lead contract retained, Lead eventID matches submission ID;
  failed/duplicate submissions do not falsely emit a new successful Lead.
- No advertising consent prompt when measurement is disabled/unavailable.
  EN/ZH persists through navigation; generated assets use content-hash cache keys.

## Results

### Safe 200-customer initializer follow-up

The retired legacy CLI now refuses execution; pure helper imports remain compatible.
`seed_vorntek_demo` defaults to plan-only and requires an explicit isolated-target
acknowledgement for writes. New local PostgreSQL17.11 run: **27 aggregate checks
passed**, HTTP process and cluster stopped normally. Added coverage proves plan
and missing-ack no-write behavior, wrong-site refusal, final-row exception/full
transaction rollback, exactly 200 companies/pool states/contacts/sources, seven
industries/five source types, zero unrelated-table changes and nonempty repeat
refusal. No accounts/messages/marketing events were created by the seeder.

Targeted helper/seed regression: 26 passed (2.321s). After replacing inherited
real-company test addresses/dataset identifiers with reserved/mock values, the
affected 108 tests passed (13.168s). Distribution checks: 40 passed (1.116s),
Node: 22 passed. The complete application rerun passed **662 tests in 454.639s:
655 passed, seven environment skips, zero failures**; test database destroyed
normally. The fixture-address changes were additionally checked by the 108-test
run above. These are local checks, not deployed 200-customer evidence.

### Earlier full website/CRM run

| Scope | Current evidence | Result |
| --- | --- | --- |
| Full SQLite application regression | 656 tests, 459.444 seconds | 649 passed, 7 environment skips, zero failures |
| Targeted changed CRM UI/input | 20 tests, 5.423 seconds | Passed before full rerun |
| Node controller/measurement/receipt | 22 tests | Passed, synthetic transports only |
| Distribution, archives and generated website | 40 tests | Passed; includes 15-page links/headings, AI copy hashes, catalogue IDs and clean deterministic build |
| Local PostgreSQL lifecycle | 23 aggregate checks, PostgreSQL 17.11 | Passed; HTTP process and cluster stopped normally |
| HTTP business lifecycle | Session/CSRF login, submit/retry, assign, qualify with task, convert and repeat conversion | One owned company/contact/opportunity/source, no duplicate conversion or outbox |
| Seven industrial business areas | Real HTTP, database and authenticated CRM HTML | Each persisted without capacity; application context visible; exact replay deduplicated |
| Actual browser inquiry | Mobile Chinese gas-sensor form | Success, database match exactly one; subsequent empty attempt shows error rather than stale success |
| Final synthetic database tally | Read-only SQL before stopping | 9 inquiries, 7 distinct business areas, one browser inquiry, zero outbox |
| Responsive/navigation | In-app browser at 390x844; content width 375 including scrollbar allowance | Menu works, width=scrollWidth=375; product CTA preselects business and preserves Chinese |
| Analytics interaction | 24-hour to 7-day selection | Chart changes to 168 points, metric/sample count follows, zero real devices claimed |

PostgreSQL recovery matched 70 tables, 441 constraints, 249 indexes, 17 triggers
and 67 sequences, including synthetic vault decryption. It also exercised failed
restore rollback, corrupt archives, nonempty targets, concurrent installer/scheduler,
paused tasks and applied-migration checksum refusal. No production restore occurred.

The first full regression had three outdated branded-asset assertions; corrected
expected names and the additional logo-only CSS, then reran the full suite above.
The first seven-area HTTP batch hit the default per-IP rate limit (HTTP 429), not
a capture failure. The disposable local HTTP process was explicitly configured at
20 accepted submissions/10 minutes for the batch and browser checks. Deployment
defaults remain 5/10 minutes. This run does not prove every deployed rate-limit path.

## Incomplete evidence / release gates

- CSV chart export click was exercised, but download observation timed out in the
  browser controller. No matching file was found in the assumed Downloads folder;
  that does not prove a download failure because the actual download destination
  was not established. Browser security blocked the privileged download-history
  page, so it was not bypassed. Actual file delivery/content still needs validation.
- New-server Vorntek build, deployment, PostgreSQL18 acceptance, same-version
  backups/recovery and full CRM visual/business verification are outstanding.
- Original-company content in remaining legacy helpers, public historical docs,
  final staged tree/history and actual image layers still need release review.
- GitHub target account is observed as `rutther`; `rutther/vorntek` has not yet
  been created/pushed/verified. MIT and both README files are prepared locally.
- Technical NEWCROWN/SITEOS configuration/database identifiers intentionally
  remain for compatibility; they must not be confused with unreplaced visible branding.
- Real Meta browser/server deduplication, real customer recovery, HTTPS and
  production-domain switching are not established by this local synthetic run.

See [rollout gates](VORNTEK_ROLLOUT.md). The overall goal remains unfinished.
