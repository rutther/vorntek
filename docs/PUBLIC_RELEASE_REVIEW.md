# Public release review — incomplete

2026-09-12. This is a release gate, not an approval or a license grant.

## Follow-up source/image review — 2026-09-13

The authorized public repository `rutther/vorntek` now exists with only an initial
README (`04d9e730c1c54c403b311cfe1b286ada1516973d`). Its complete diff was inspected:
only the project name and fictional-demo summary. No application code, release
or registry image has been uploaded. Earlier no-repository statements are history.

[Layer findings and remediation](review/IMAGE_SECURITY_20260913.md) distinguish
the nine source false positives from an inherited default private key in the old
maintenance image. The clean final image's only alert is a Perl documentation
symbol; real backup/restore works. Do not publish the old image or donor cache.
The current Linux Python dependency evidence is in
[python-dependencies-linux.json](review/python-dependencies-linux.json).
Final Git history and binary redistribution gates remain open; an empty repository
or source upload alone is not completed release acceptance.

## Latest Vorntek source review — 2026-09-13

- Eight original AI images now replace the old website/brand media. Their bytes
  are copied into the website and CRM and checked against the generation manifest.
  The 123-image counts below are historical, not the current public input.
- The legacy reset seeder is privately archived; its public CLI now refuses and
  only pure test helpers remain. Optional 200-customer initialization is additive,
  explicit, isolated and synthetic; see [demo data](DEMO_DATA.md).
- GitHub sign-in was observed as `rutther`; intended repository is `rutther/vorntek`
  under MIT. The repository has not yet been created or published.
- A source-pattern screen found old dataset identifiers in mock test fixtures
  and developer-machine paths in two reports. These were removed from the
  public input; no production integration is changed. A pattern screen alone is
  not a comprehensive secret audit or proof of publishability.
- Final staged history, Linux image layers, actual dependency license inventory,
  deployed revision and clean public-clone checks remain required. Older local
  acceptance records below are not evidence for this new revision.

## Follow-up: old-host tools and source independence

Subsequent local review retired five unused historical content-rewrite/seeding
scripts, preserving byte-identical private copies and original sources. One
fetches third-party product pages and another overwrites published articles;
neither is part of root Compose initialization. See [legacy tools](LEGACY_TOOLS.md)
for exact scope and evidence. The 35 release/distribution/recovery checks passed.
No remaining tracked website/CRM literal `newamstar` matches were found, but
content/media provenance is not established by that search. The 123 raster assets
and overall publication permission remain unresolved.

Six old-host deployment/service/test files were moved into a private ignored
archive, with original sources retained. Their 13 associated tool contracts were
retired, not business tests. The independent staged-source copy passed 34 unit
checks, 14 Node tests and the 643-test application suite (636 passed, seven
skipped). See [source-only evidence](SOURCE_ONLY_ACCEPTANCE.md) and
[legacy tools](LEGACY_TOOLS.md). Media/content rights remain unresolved;
this does not approve publication or container deployment.

## Latest review outcome — 2026-09-12

- Excluded the two inherited QA screenshots and their comparison HTML from the
  public Git index and added a matching ignore rule. All three remain on local
  disk, and the original repository was untouched. Reference search found only
  the comparison page's own image references; business pages were not changed.
- Added exact upstream Lucide ISC/Feather MIT, Bootstrap MIT and Popper MIT license
  texts. Verified corresponding package SHA-512 integrity and selected vendored
  byte hashes without executing packages. Tabler's own existing notice was retained;
  the unavailable versioned license source is explicitly recorded as a limitation.
- Inventoried 24 locked Python distributions and their actual installed license
  files. Versions match the lockfile; this Windows evidence is not a Linux-image
  license audit. See [third-party notices](../THIRD_PARTY_NOTICES.md) and `review/`.
- Current staged raster count is **123** (21 JPEG, 37 PNG, 65 WebP), comprising
  122 website images and one CRM brand mark. Redistribution rights remain unknown.
  No standalone font files were staged; narrow source font-import searches found
  no matches. This is not exhaustive dynamic resource inspection.
- 33 unit/distribution/release-input tests passed. Final source/history, image-layer,
  legacy production/content-helper and media-rights reviews are not complete.

The following initial findings are retained as historical observations; their
125-image/QA-still-staged counts are superseded by the current outcome above.

## Initial file review

- Selected staged text files were searched for private-key headers, GitHub token
  prefixes, AWS access-key identifiers and long Meta-token patterns. No matches
  were returned. This narrow pattern scan cannot prove absence of secrets.
- No staged `.env` other than `.env.example`, private-key containers, database
  dumps, spreadsheets or customer CSV files were listed by the initial filename
  screen. Actual values, history, images and build outputs require further review.
- The staged candidate contains **125 raster image files**: 21 JPEG, 39 PNG and
  65 WebP. Of these, 122 belong to the website, one is the CRM brand mark and two
  are inherited CRM QA screenshots. Their presence on a public website does not
  establish redistribution rights.
- A QA screenshot was visually inspected and contains names/company-like labels.
  These must be traced to verified synthetic fixtures or excluded before release;
  appearances alone do not prove that they are safe. QA screenshots are already
  excluded from the image build context, but are still in the source candidate.
- Inherited `apps/crm/scripts` still contains old production installation helpers,
  a demo-reset/preparation helper and content-seeding scripts naming external
  sources. They are not part of the root Compose installation. Review, retire or
  clearly isolate them before release; do not execute them against any existing
  system or infer content redistribution rights from their inclusion.

## Required before publication

1. Confirm the publishing account, repository, license and authority over the code.
2. Review third-party dependencies, scripts, brand assets, photographs, fonts and
   any remotely loaded resources. Record evidence or substitute authorized assets.
3. Trace screenshots and fixtures; remove private material from the public
   candidate without deleting the user's original source or evidence.
4. Scan the final source tree and all history to be published, including staged
   changes, generated artifacts, image layers and CI logs. Inspect findings without
   copying secret values into reports.
5. Verify no production configuration, fixed administrator credentials, customer
   data, private backup or developer-machine paths are necessary for installation.
6. Build and install from the exact reviewed release and document the result.

No GitHub repository has been created or pushed as part of this review. Do not
run a public push merely because this checklist exists.
