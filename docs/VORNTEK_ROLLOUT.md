# Vorntek rollout / 替换部署检查表

The first isolated Vorntek deployment is now verified; see
[actual deployment evidence](VORNTEK_DEPLOYMENT.md). Full release acceptance and
GitHub publication are incomplete. This checklist is not execution evidence.

## Intended result

The user explicitly replaced the original-company demonstration with fictional
Vorntek, across website, CRM branding, sample content and GitHub distribution.
Use seven industrial business lines and newly generated AI imagery. Preserve the
working CRM capabilities and its visual language. No real customers or original
company media belong in this public demo. Original production stays untouched.

The GitHub account was observed in the user's signed-in Edge session as `rutther`.
Intended repository: `rutther/vorntek`; project license: MIT. Creation, upload,
source/history review and public access remain separate unverified steps.

## Upgrade the authorized isolated instance

1. Verify actual host, release, Compose project, image IDs, volumes and origin.
   Stop if they do not identify the specifically authorized instance.
2. Back up database, file volumes and private configuration; verify recovery in
   separate storage. Record site/form metadata for rollback.
3. Build reviewed source. Keep existing project/volume names and secrets. Do not
   reinitialize, truncate, rename or drop the database to change branding.
4. In a transaction with row locks, verify expected `siteos_demo` and
   `project-inquiry` metadata. Change only the intended site display name, form
   display name/category (`industrial`) and explicitly reviewed locale settings.
   Preserve business records, ownership, roles and integration configuration.
5. Deploy website/backend together; keep all external I/O, browser measurement,
   email and WhatsApp off. Do not change domains or unrelated shared-host services.
6. Verify all 15 public routes, images, EN/ZH and mobile layouts, login, seven
   business areas through HTTP -> database -> CRM display, failures, deduplication,
   role permissions and persistence after restart.
7. Record source tree, images, address, evidence and limitations. On failure,
   restore the recorded code/metadata only; do not discard new submissions or files.

Fresh installations receive `industrial` automatically. Existing forms are not
silently converted by bootstrap. Legacy environment, SQL and site identifiers are
technical compatibility contracts, not public-facing company branding.

## Public release gates

- Original-company content is excluded from the reviewed public tree; AI hashes
  and third-party notices match shipped bytes.
- Staged source and complete intended public history contain no real customers,
  credentials, private configuration, backups, runtime receipts or old screenshots.
- Current application, JS, packaging, PostgreSQL, browser, container and recovery
  checks pass. Old New Crown reports are not evidence for the Vorntek revision.
- Both README flows work from clean source, then from the actual public clone.
- Public repository/tag/image and deployed version correspond; record actual CI.
- Do not declare completion while any required gate remains unverified.
