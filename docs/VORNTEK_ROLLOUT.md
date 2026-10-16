# Vorntek rollout / 替换部署检查表

This is the current integration rollout checklist, not execution evidence.
[Earlier isolated deployment evidence](VORNTEK_DEPLOYMENT.md) describes an older
baseline and must not be used to certify this candidate or its public HTTPS route.

## Intended result

Preserve the full working website, CRM, marketing workflows and effective visual
language while removing customer identity, fixed industry fields and deployment
coupling. Vorntek examples are explicitly fictional and optional, not seven
mandatory industrial business lines. Source production stays untouched; target
accounts, secrets, configuration and later business data remain protected.

This stage delivers the authorized Vorntek installation. GitHub push, tags and
formal distribution are a later stage, not an implicit deployment side effect.

## Upgrade the authorized isolated instance

1. Verify actual host, release, Compose project, image IDs, volumes and origin.
   Stop if they do not identify the specifically authorized instance.
2. Back up database, file volumes and private configuration; verify recovery in
   separate storage. Record site/form metadata for rollback.
3. Build reviewed source. Keep existing project/volume names and secrets. Do not
   reinitialize, truncate, rename or drop the database to change branding.
4. Preserve existing site/form metadata during append-only schema upgrade.
   Explicitly configure the target's intended neutral form fields through the
   existing editor; verify public display, backend requirements and saved detail
   agree. For the bundled shared-base website, review `articleDelivery` settings:
   legacy sites may need explicit `baseRouteMode=shared`, preserving other settings.
   Do not silently rewrite every existing site or user-defined form.
5. Deploy website/backend together using the verified [HTTPS proxy contract](HTTPS_PROXY.md).
   Preserve verified original switches; new synthetic integrations remain silent.
   Restore temporarily paused services to the intended recorded state. Do not
   change unrelated shared-host applications or borrow source production secrets.
6. Verify every configured public route and image, language/navigation and normal
   public mobile access. Admin verification is PC-only. Exercise two configurable
   neutral inquiry scenarios through HTTP -> database -> CRM, assignment/tasks,
   bulk trash/restore, roles, multiple same-kind loops, diagrams/step figures,
   content preview/publication/rollback, errors and persistence after restart.
7. Record source tree, images, address, evidence and limitations. On failure,
   restore the recorded code/metadata only; do not discard new submissions or files.

Fresh installations receive a `general` form with configurable field requirements.
Existing forms are not silently converted by bootstrap. Optional
[example packs](EXAMPLE_DATA.md) require explicit guarded imports; repeated imports
must not overwrite later work or multiply rows. Legacy environment, SQL and site
identifiers are technical compatibility contracts, not public-facing branding.

## Later public release gates

- Original-company content is excluded from the reviewed public tree; AI hashes
  and third-party notices match shipped bytes.
- Staged source and complete intended public history contain no real customers,
  credentials, private configuration, backups, runtime receipts or old screenshots.
- Current application, JS, packaging, PostgreSQL, browser, container and recovery
  checks pass. Old New Crown reports are not evidence for the Vorntek revision.
- Both README flows work from clean source, then from the actual public clone.
- Public repository/tag/image and deployed version correspond; record actual CI.
- Do not declare completion while any required gate remains unverified.

For the current server-delivery stage, record the actual entry point, source/image
identity, switches, backup/recovery procedure and observed limitations. Software
verification and external-provider observations are separate; do not invent real
Meta acceptance or block unrelated delivery solely for missing provider accounts.
