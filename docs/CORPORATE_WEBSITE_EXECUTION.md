# Corporate website execution — 2026-10-16

Base: 1c4dcf7840a410d4f04016c689431da282d01e7c. Existing clean feature checkout `codex/hk-to-vorntek` is retained. No main-branch work, GitHub push or customer-source copy.

Binding brief: the user goal and `docs/product/VORNTEK_CORPORATE_WEBSITE_RESEARCH_PLAN_20261016.md` in the parent workspace. This record concerns the new corporate-site work, not the completed earlier migration.

## Tasks

1. Inventory and content: reusable components, original owned content, stable identities, model dimensions, complete six-product/three-scenario industrial B2B content package.
2. Implement shared templates, language metadata, navigation, product family interaction, gallery, specifications, business 3D, resources and contact flow.
3. Integrate article presentation, configured inquiry context and immutable publication. Preserve records and old routes.
4. Run isolated business tests and actual browser journeys, repair observed defects; whole-change fresh review.
5. Back up current Vorntek, deploy exact candidate through existing release mechanism, restore normal services and verify public routes and media.
6. Record completion evidence and recovery instructions against every goal requirement.

## Interfaces and decisions

- Content -> templates: stable product and family IDs; bilingual strings; metric dimensions in millimetres; no backend category enum.
- Templates -> publication: keep existing home-slot anchors; enabled database placements must be explicitly reviewed before switching the website, so earlier test assets cannot replace the new hero unnoticed.
- Forms -> CRM: keep definition-driven controller and validation; product/solution context is descriptive input, never authority over server configuration or consent.
- Articles -> site shell: keep locale routes and published records; integrate existing renderer rather than overwrite article history with static files.
- Ruling: use the existing feature checkout and versioned local content package; a separate worktree adds no needed isolation to this clean dedicated product checkout. All earlier commits remain recoverable.
- User correction: industrial B2B is the primary customer audience. This supersedes the initial workspace-furniture content. Use an industrial inspection and modular automation supplier: vision inspection, dimensional measurement, assembly, conveying, identification and data acquisition. Keep the generic software boundary.
- Ruling: retained Vorntek brand and the new industrial brief are visual authority. No new questionnaire. Code-led samples use industrial product photography, navy typography, light factory surfaces, graphite frames and purposeful blue equipment panels. Seed f6ca3bf2 is exploration only; the pinned brief takes precedence over unrelated suggestions.
- Ruling: earlier tests asserting exactly 15 pages, exactly three SVGs or repeated fictional disclaimers encode the rejected content. Replace those assertions with route/resource integrity, generic catalogue escaping, deterministic clean builds and correct concentrated disclosure; preserve business protections.
- Verification scope: meaningful generation/security/contract tests plus rendered desktop/mobile and interaction checks; editorial wording and spacing are evaluated directly rather than mirrored in unit assertions.

## Direction contract (development only)

THESIS: help an industrial buyer understand the equipment, examine relevant parameters and integration constraints, then discuss a real production requirement.

OWN-WORLD: Vorntek navy/blue wordmark; light industrial environments, graphite metal frames, blue equipment panels, readable sans-serif headlines, precise rules, equipment photography and compact specification tables. Existing brand assets retained.

STORY: understand the offer, browse three families, examine one of six equipment types, review an application and technical documentation, then submit a contextual engineering inquiry.

FIRST VIEWPORT: restrained white navigation, a large navy headline beside an industrial inspection and assembly installation, two clear actions. No developer disclaimers in the headline, no invented performance statistics.

FORM: industrial equipment catalogue with application and engineering resources; code-led under the pinned brief. Deliberate interaction is family switching and equipment inspection.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance.

## Progress

- Planning baseline verified: source clean at 1c4dcf7, original backend/website publication mechanisms inspected. No production mutation.
- Task 1 in progress: content and model-generation package; new tests precede generator changes.
- Direction correction received during Task 1; initial furniture assets are not accepted for publication. Completed office-image generation is a discarded concept, not a delivered asset. Existing handcrafted GLB primitive builder can be reused to model actual industrial equipment.
- Task 1: industrial content implemented (six equipment types, three production applications, three planning guides); own model bounds checked against declared dimensions. Actual generated factory visuals and model-derived catalogue images inspected. Product media remain local pending final review.
- Task 2: 38 corporate HTML routes generated; shared templates, family tabs, gallery, bilingual text/metadata, contextual inquiry, six text data sheets and two original structural videos implemented. Initial Python generation 12 checks and browser contract 40 checks passed. This is not final deployment acceptance.
- Task 3 in progress: page-slot registry and immutable replacement implemented; focused publication/article/3D group 35 checks passed. Full application run at intermediate source passed 1348 with140 conditional skips; repo run from correct product cwd passed93. Initial repo invocation from parent cwd failed two imports; preserved as an invocation failure, not silently counted as green.
- Ruling: product-page 3D positions come from bounded source registry, not six hardcoded database enums. Existing home positions remain valid. Exact shared pinned viewer bytes are deduplicated; differing vendor bytes or private-video uploads still fail closed.
- Ruling: the obsolete legacy-industry DOM tests now execute the actual saved-definition inquiry controller. New tests verify preserved inputs, unchanged retry identity, double-submit suppression, server validation and denied-consent behavior. No business protection was removed to satisfy old presentation assertions.
- Actual local processes: static asset renderer port49711/session95747; corporate static preview port49712/session22143; bounded video receiver port49713/session84774. These are temporary local helpers, not Vorntek deployment. Close them after final capture/review.
- Industrial direction confirmed again in the current user turn. Actual Chinese homepage reloaded and viewed; desktop capture is in the private operator checkpoint folder. Existing footer privacy action was duplicated by a document-order fallback in the unchanged measurement controller. Reproduced with the real controller and DOM (2 actions instead of1), corrected selector fallback, seven corporate interaction checks passed and actual browser now exposes one working action. Generated38 routes rebuilt. This does not close the remaining full-site/business/publication checks.
- Third-party notices updated for the added self-hosted Manrope font and current original equipment assets; previous no-font/eight-images statements were historical, not current-candidate evidence.
- Current read-only production baseline attempt did not connect: the invoked default Python lacks paramiko. No remote command ran and no production state was changed. Keep the empty02 report/error log as failed invocation evidence; use the established operator interpreter before retrying, not an unguarded deployment.
