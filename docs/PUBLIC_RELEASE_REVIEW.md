# Public release review

The v0.2.0-rc.4 source release was published on 2026-10-17 from merge commit
`20946994d1592a2a09e204b886c13e45560e304f`. Current facts are in
IMPLEMENTATION_STATUS.md and the immutable
[acceptance record](releases/0.2.0-rc.4-acceptance.md); earlier observations are
preserved in PUBLIC_RELEASE_REVIEW_HISTORY.md and are not evidence for rc.4.

Before first public push, review all pending ancestor commits/messages,
blob/path bindings and media, not just HEAD. Run private-input tests, the full
history guard and a pinned supplemental secret scanner. Follow
review/RELEASE_ASSET_POLICY.md; exact large-resource approvals never waive secret
checks. Inspect all newly distributed vendor/media notices. Public accessibility
does not itself establish redistribution permission.

Build and exercise a clean checkout without inherited runtime, credentials or
developer packages. Record actual installation, upgrade, recovery and browser
checks with platform/conditional-skip limits. Public evidence must not include
private deployment logs, customer screenshots, environment values or backups.

Merged-source CI, tag CI, version/commit identity and download hashes were checked
as separate states. The public assets were downloaded without repository
credentials into a new directory, verified by SHA-256 and exercised without Git
metadata or inherited packages. The acceptance record preserves the commands,
results and limits.

This release is source plus Compose build definitions. Prebuilt-image publishing
is outside scope; historical binary review is not approval to upload old native
layers. Third-party terms are retained, not replaced with the project's MIT.

Private vulnerability reporting was enabled and read back on 2026-10-17. The public
repository also has secret scanning/push protection; local pre-push review still
runs before exposing a new branch. Branch rules and final release identities are
recorded after their GitHub operations actually complete. Rc.1, rc.2 and rc.3
release pages now identify rc.4 as their replacement while retaining historical
assets and notes.
