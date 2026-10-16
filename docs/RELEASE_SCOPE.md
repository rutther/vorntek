# Vorntek source pre-release scope

Vorntek is a self-hosted website and business workspace for industrial B2B teams.
Its industrial company example is replaceable content. Forms, product counts,
business categories and supported site languages are configuration, not fixed
industrial schema enums. Project code remains MIT; third-party code and media
retain their respective terms.

| Area | Included behavior |
| --- | --- |
| Public company website | Bilingual industrial pages, catalogue/families, galleries, specifications, video, six GLB concepts, contact and privacy choices |
| Inquiry and CRM | Configured field validation, consent, persistence, assignment, qualification, leads, opportunities, tasks and recoverable lead trash |
| Marketing workspace | Platform configuration, acquisition plans, multiple workflows including same-template instances, diagrams, guide figures and separately recorded runtime observations |
| Content and 3D | Article editing/import/translations, authenticated previews, media library, GLB and splat configuration, immutable website candidates, selection, deployment and recovery |
| Operations | Roles, audit history, append-only SQL chain, durable private files, health/version checks, background jobs, guarded backup and restore |
| Optional examples | Industry-neutral business exercises plus independent industrial articles/assets; no user passwords or external credentials |

The source installation serves the bundled industrial static site immediately;
the business database starts without customer data. Explicit example commands add
editable records. Article drafts still require the normal publishing workflow.
Six product pages retain their bundled model fallback even without DB placements.

The supported deployment target for this candidate is a single Linux Docker
Compose installation with PostgreSQL18. The declared Python/Node/build inputs
are in Dockerfiles and lockfiles. Windows source tests supplement Linux acceptance;
they do not mean native Windows production deployment or Docker Desktop support
was certified. The administration workspace targets PC browsers; public website
mobile layout and Arabic article direction remain supported features.

External Meta/Google/email/WhatsApp delivery is off by default. Mocked transport
tests verify software behavior, not provider delivery, advertising attribution or
Meta deduplication. Operators configure their own authorized credentials and
production origins separately. No default administrator password is shipped.

This release packages source and the Compose build definitions, not prebuilt
container images. Multi-node failover, a guaranteed availability SLA, SaaS billing,
Kubernetes, MFA implementation and new WhatsApp integration are outside this
release. Existing customer-pool and export code remains available; no new module
redesign is part of release preparation.

Legacy `NEWCROWN_*`, database/role names and `siteos_demo` are compatibility
identifiers. Existing installations must retain project, volume and secret
identities. They are not permission to reuse another installation's database.

Current tests and open release gates are recorded in IMPLEMENTATION_STATUS.md;
an old green CI run or deployed example does not certify a new source version.
