# Public release review

The v0.2.0-rc.2 source release is in preparation. Current facts are in
IMPLEMENTATION_STATUS.md; earlier observations are preserved in
PUBLIC_RELEASE_REVIEW_HISTORY.md and are not evidence for this new revision.

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

Before Tag/Release, verify actual merged-source CI, version/commit identity and
download hashes. After release, independently download and install the public
inputs. Push, merge, Tag and download acceptance are separate observable states.

This release is source plus Compose build definitions. Prebuilt-image publishing
is outside scope; historical binary review is not approval to upload old native
layers. Third-party terms are retained, not replaced with the project's MIT.

Private vulnerability reporting was enabled and read back on2026-10-17. The public
repository also has secret scanning/push protection; local pre-push review still
runs before exposing a new branch. Branch rules and final release identities are
recorded after their GitHub operations actually complete.
