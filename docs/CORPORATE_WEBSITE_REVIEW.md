# Whole-change review and disposition

Range reviewed:1c4dcf7..1dae860 by a fresh independent reviewer. Read-only review;
no reviewer edits, external writes or delegated reviews. Verdict: needs changes,
two Important and one initially Minor; no verified Critical finding.

## Findings and one fix pass

1. **3D retry did not retry.** Reassigning an unchanged source did not reload
   model-viewer. The old test's manually dispatched success was insufficient.
   `review-retry-red-01` reproduces the unchanged instance; the fix creates a fresh
   instance, ignores detached events, and uses a same-origin retry URL. The pinned
   [upstream loader](https://github.com/google/model-viewer/blob/v4.4.0/packages/model-viewer/src/three-components/CachingGLTFLoader.ts)
   caches even its failure projection by URL, so merely replacing an element is
   not enough. Actual loopback browser probe: first GLB request503, retry200,
   loaded equipment and drag rotation. Network counts and screenshots retained.
2. **Default article language could lose to saved preference.** Shared English
   article links omitted `lang=en`. Explicit language now applies to every shared
   website link, including default English, preserving other query/fragment data.
   `review-english-red-01` -> `review-english-green-01`; actual round trip is part
   of the browser check, not inferred from markup alone.
3. **Accepted catalogue IDs could fail the publication contract.** Regraded from
   Minor to Important: a normal content-package replacement could build but then
   block the whole candidate, contradicting the required replaceable-content use.
   Canonical existing IDs keep their codes; other allowed IDs receive stable
   bounded SHA-256-based codes in a reserved namespace. Labels are normalized and
   bounded; template anchors and registry use the same mapping. The failing test
   covers hyphen, case, reserved prefix and maximum-length IDs.

Each finding has observed RED and GREEN evidence, not a second reviewer claiming
it fixed. Full-source regression follows the one fix pass. No deferred minor
remains from this review.

## Final native-image build finding

The complete208-test flow build exposed jsdom's cascade difference for the
generic hidden rule preceding `.button` display. The real local browser had
hidden the loaded retry control, but the frozen-image gate correctly refused
the failing contract. Added a final component-specific hidden-state rule and
checked both failure-visible and recovered-hidden states. RED11/12 -> GREEN208/208;
no skipped test or weakened consent/retry assertion. The first native attempt
failed build-network DNS; the second reached this assertion. Both retained their
terminal failed status, cleaned their temporary resources and left live services
and database untouched. A new exact source candidate is required.

## Reviewer declined-to-judge: parent rulings

- Visual pages, videos, real WebGL recovery and mobile were not independently
  watched by the reviewer. **Ruling:** parent actual browser evidence is required;
  these remain in the goal, not silently excluded. Cost if wrong: visible defects.
- Full application suite was not independently rerun. **Ruling:** bind parent
  final full-suite evidence to the final source. Cost if wrong: regression risk.
- Production content updates, old synthetic home-slot changes and actual release
  were pending. **Ruling:** keep goal active through backup, normal publication
  and public verification. Cost if wrong: unchanged/broken live site.
- Prior migration, HK production and real external sending were not re-audited.
  **Ruling:** retain earlier evidence and explicit no-HK/no-send boundaries; no
  new success claim for those systems. Cost if wrong: no proof beyond that scope.
