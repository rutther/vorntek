# Hong Kong production parity ledger

Updated: 2026-09-20. This ledger classifies every file changed between the
production-corresponding CRM baseline `d44dfdb` and observed Hong Kong source
commit `7ed9c56` (106 paths, +12,450/-225). It is a source-disposition record,
not a claim that the public repository already matches every production screen.
The ordered UTF-8 path list (one path per line, final newline) has SHA-256
`07cf028699e3cc05220496b670881687669493e0003bd915b69eb2a612fe86a4`.

The Hong Kong `filline.com` stack is the business reference. The former Hubei
host is not a source or deployment target. No production data, credentials,
host paths or regional research records may enter this repository.

## Disposition rules

- **PORT / PARTIAL** — generic product behavior. Port in reviewed slices and
  keep public hardening already present; “partial” means another slice remains.
- **ARTICLE REVIEW** — potentially reusable CMS-to-static delivery capability.
  Keep pending until its threat model, lifecycle and clean-install contract have
  been reviewed independently.
- **PRIVATE EXCLUDE** — company research corpus, source-specific identity rules,
  retained historical evidence or its presentation. Do not publish; a future
  extension point must use user-supplied data and neutral schemas.
- **CORRECT FIRST** — behavior is useful in principle but current production
  semantics overstate readiness or infer completion from history. Do not port as
  implemented.
- **REPLACE** — Hong Kong installer or one-off migration control. The public
  equivalent is the canonical migration chain, Compose lifecycle and documented
  preflight/upgrade flow, not the host-specific script.

## PORT / PARTIAL — customer-pool product capability (29)

- `console/access.py` — partial: bulk-review route is ported; re-check remaining mixed access additions.
- `console/customer_pool_exports.py` — partial: standard-21 export is ported; dense/filter export parity remains.
- `console/customer_pool_queries.py` — port next as the neutral read/query boundary after independent query review.
- `console/customer_pool_views.py` — partial: standard import/export and bulk review are ported; filters/dense view remain.
- `console/forms.py` — review and port only neutral article/customer validation changes.
- `console/navigation.py` — partial shared file; exclude guided-loop navigation until semantics are corrected.
- `console/static/console/v2/customer-pool-dense.css` — port with the dense-view slice.
- `console/static/console/v2/customer-pool.css` — partial shared presentation; port only with tested UI behavior.
- `console/static/console/v2/customer-pool.js` — partial: safe selection/bulk review is ported; dense controls remain.
- `console/static/console/v2/new-crown-theme.css` — review visual changes separately; no production-brand coupling.
- `console/templates/console/v2/base.html` — mixed shell change; do not expose blocked guided-loop navigation.
- `console/templates/console/v2/pages/_contact_evidence.html` — port only as neutral contact provenance display.
- `console/templates/console/v2/pages/customer_pool.html` — partial: standard export and bulk review are ported.
- `console/templates/console/v2/pages/customer_pool_detail.html` — port neutral 21-column route display.
- `console/templates/console/v2/pages/customer_pool_import.html` — partial: bounded standard CSV import is ported.
- `console/test_customer_pool_views.py` — partial: port tests alongside each accepted view slice.
- `console/test_navigation.py` — mixed; keep blocked destinations out of public expectations.
- `console/urls.py` — partial shared router; add only accepted public endpoints.
- `db/migrations/0028_customer_pool_standard21.sql` — ported byte-for-byte; append-only 0029 adds reference integrity.
- `db/migrations/SHA256SUMS` — ported with canonical checksums for 0028–0029.
- `leads/customer_import_services.py` — partial standard-21 preview/commit integration is ported without private adapters.
- `leads/customer_pool_publish.py` — ported with deterministic route choice and per-company results.
- `leads/customer_standard21_import.py` — ported with bounded, strict parsing and value reconciliation.
- `leads/customer_value_engine.py` — ported generic deterministic scoring.
- `leads/models.py` — partial model fields and pool rows are ported with corrected nullable-reference deletion behavior.
- `leads/test_customer_pool_publish.py` — ported generic success, restriction, replay/conflict and validation tests.
- `leads/test_customer_pool_services.py` — mixed regression additions; compare and port only generic missing cases.
- `leads/test_customer_standard21_import.py` — ported and expanded with byte-for-byte round-trip coverage.
- `leads/test_customer_value_engine.py` — ported generic scoring contracts.

## ARTICLE REVIEW — reusable CMS delivery candidate (25)

- `console/article_build_input.py`
- `console/article_delivery.py`
- `console/article_exports.py`
- `console/article_imports.py`
- `console/article_isolated_publish.py`
- `console/article_preview.py`
- `console/article_preview_reader.py`
- `console/article_release_store.py`
- `console/article_rendering.py`
- `console/article_workspace.py`
- `console/static/console/article-workspace.js`
- `console/templates/console/_article_editor_workspace.html`
- `console/templates/console/_article_import_workspace.html`
- `console/templates/console/_article_release_preview.html`
- `console/templates/console/_article_workspace.html`
- `console/templates/console/article_delivery_page.html`
- `console/test_article_build_input.py`
- `console/test_article_delivery.py`
- `console/test_article_delivery_cms.py`
- `console/test_article_release_store.py`
- `console/test_article_rendering.py`
- `console/test_content_route_permissions.py`
- `console/views.py`
- `requirements.lock`
- `requirements.txt`

These paths are reviewed as one candidate slice. The Markdown dependency must
be accepted only with the renderer and
its security tests, never as an unexplained lock-file drift.

## PRIVATE EXCLUDE — source-specific research and retained evidence (45)

- `console/customer_reference_views.py`
- `console/customer_research_evidence.py`
- `console/templates/console/v2/pages/_research_evidence.html`
- `console/templates/console/v2/pages/_source_import_evidence.html`
- `console/templates/console/v2/pages/customer_references.html`
- `console/test_emea_history_presentation.py`
- `console/test_legacy_checklist_presentation.py`
- `leads/central_asia_bundle.py`
- `leads/central_asia_payloads.py`
- `leads/customer_reference_review.py`
- `leads/emea_auxiliary_evidence.py`
- `leads/emea_call_checklist.py`
- `leads/emea_history_bundle.py`
- `leads/emea_history_evidence.py`
- `leads/emea_history_import.py`
- `leads/emea_history_manifest.py`
- `leads/emea_residual_archive.py`
- `leads/legacy_checklist_evidence.py`
- `leads/legacy_country_import.py`
- `leads/legacy_country_manifest.py`
- `leads/legacy_country_reader.py`
- `leads/legacy_country_references.py`
- `leads/legacy_research_pool.py`
- `leads/legacy_source_observations.py`
- `leads/north_africa_payloads.py`
- `leads/research_evidence_tables.py`
- `leads/research_identity.py`
- `leads/research_source_inventory.py`
- `leads/reviewed_research_identity.py`
- `leads/test_central_asia_bundle.py`
- `leads/test_central_asia_payloads.py`
- `leads/test_emea_auxiliary_evidence.py`
- `leads/test_emea_call_checklist.py`
- `leads/test_emea_history_bundle.py`
- `leads/test_emea_history_evidence.py`
- `leads/test_emea_history_import_contract.py`
- `leads/test_legacy_checklist_evidence.py`
- `leads/test_legacy_research_pool.py`
- `leads/test_legacy_source_observations.py`
- `leads/test_north_africa_payloads.py`
- `leads/test_research_evidence_tables.py`
- `leads/test_research_identity.py`
- `leads/test_research_snapshot_formula.py`
- `leads/test_research_source_inventory.py`
- `leads/test_reviewed_research_identity.py`

The concrete country/account patterns, reviewed hashes and history manifests are
business evidence, not reusable software defaults. Their tests are excluded for
the same reason; omitting tests while publishing the hard-coded rules would not
make those rules safe.

## CORRECT FIRST — guided-loop semantics (4)

- `console/marketing_guided_loop_queries.py`
- `console/marketing_guided_loop_views.py`
- `console/templates/console/v2/pages/marketing_guided_loop.html`
- `console/test_marketing_guided_loop_v2.py`

The current implementation mixes configuration, historical rows and test-mode
signals into completion/readiness summaries. A public version must define each
state from current authoritative evidence, distinguish configured/test/live and
never infer platform acceptance from local history.

## REPLACE — Hong Kong production procedure (3)

- `leads/management/commands/apply_customer_pool_standard21.py`
- `scripts/build-production-release.ps1`
- `scripts/install-production-release.sh`

These implement an authorized one-host upgrade gate and contain production
layout assumptions. Public releases use the immutable migration ledger,
`release_preflight --strict`, Compose initialization and `docs/UPGRADE.md`.

## Completeness and next gates

The lists above are mechanically compared with `git diff --name-only
d44dfdb..7ed9c56`; no changed path may be unclassified or appear twice. The
next implementation slices are:

1. neutral customer-pool query/filter/dense-view behavior;
2. independent security and lifecycle review of the article-delivery candidate;
3. a new guided-loop state model, if retained, rather than copying current
   production semantics;
4. a documented extension boundary for user-supplied research data, without any
   of the excluded identifiers, hashes or manifests.
