# Deployed role and sales-flow acceptance — 2026-09-13

Tested actual deployed tree `f7b78ef98b7c4bd1436b8ddecb5455260a62c138`, PostgreSQL18,
and normal session/CSRF-authenticated HTTP endpoints on the private container
network. These are HTTP/database checks, not browser screenshots or live Meta tests.

| Observable check | Result |
| --- | --- |
| Five temporary accounts authenticate: two sales, manager, marketing, content | Passed |
| Anonymous protected-page access redirects; all five cannot manage system users | Passed |
| Marketing/content cannot access sales leads | Passed |
| Assigned sales owner and manager can read; other salesperson cannot read/convert | Passed, no rejected-write effects |
| Qualification creates one task; retry is idempotent | Passed |
| Stale version submission is refused without duplicate task | Passed |
| Convert inquiry to owned customer, contact, opportunity and website source | Passed, exactly once |
| Repeated conversion does not create duplicates | Passed |
| Complete task, then manager hands customer to existing administrator | Passed |
| Active customer/lead/opportunity ownership moves; old owner loses scope | Passed |
| Completed task keeps its historical actor | Passed |

Only clearly synthetic inquiry 8 was converted, producing company 201, task 1
and opportunity 1. Final state: **201 companies, eight inquiries, one conversion,
one completed task, one opportunity, zero outbox events**. The original 200 demo
customers were not rewritten by this test. All five temporary accounts were
disabled and assigned unusable passwords; the existing admin credential was unchanged.

The restricted runtime proof is `role-business-f7-proof.json`; no passwords,
session cookies or customer data are copied into this report. No ads, Meta,
WhatsApp, email or original-production actions were performed.
