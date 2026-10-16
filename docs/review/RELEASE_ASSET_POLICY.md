# Reviewed large source resources

The default history-audit limit remains 1,000,000 bytes. The asset manifest
permits only individually reviewed path/SHA256/size triples above that limit.
It never exempts a file from secret, private-path or production-marker checks.
Identical bytes introduced under another path require a separate review.
Unknown and unused approvals fail the audit; historical approved bytes remain
auditable even after deletion. A PR's new approval is a review decision, not
proof that the resource has a license.

On 2026-10-17 the existing reproducer scripts fetched the pinned official npm
archives for model-viewer4.4.0 and SuperSplat1.37.0/PlayCanvas2.23.0. Archive
integrities, artifact bytes and license notices matched the committed inputs.
The authored SuperSplat camera extension was reproduced from its exact upstream
bytes. No package lifecycle scripts ran. See adjacent vendor provenance files
and THIRD_PARTY_NOTICES.md for individual obligations.

The three generated industrial context images have original prompts and intended
uses recorded in docs/vorntekDemo/ASSET_PROVENANCE.md. Source and published copies
are independently listed. They do not contain customer photography or performance
evidence. These six distinct large contents occupy ten reviewed paths.

Run before publication:

```sh
python scripts/audit_release_history.py --base v0.1.0-rc.1 --require-clean --asset-allowlist docs/review/release-asset-allowlist.json
```

Changing resource content requires a new provenance review and exact manifest
entry. Do not increase the global limit, delete historical scan coverage or
remove another valid rule to accommodate a resource.

## Supplemental secret scan

Gitleaks8.30.1 checks the pending Git history with redacted output. The sole
reviewed false positive is the literal UUID `12345678-abcd-4000-9000-123456789012`
in the historical React runtime-label test. `.gitleaksignore` identifies its
exact commit/path/rule/line; it does not ignore the file or a class of credentials.
Keep raw scan output outside release artifacts and record the scanned commit.
