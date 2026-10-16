# Content editing, import and private preview

The Article editor keeps a stable multilingual `contentKey` independent of title
and public slug. Use the same key for translations; existing keys remain immutable
in normal editing. New keys use lower-case-leading camelCase (up to80 characters).
Drafts may omit a key; publication requires one. Enabled languages come from site
configuration, not a fixed industry or three-language assumption.

Saving a row locks and compares its current route/article versions. Stale edits
cannot overwrite a newer draft or undo another operator's publication. HTTP writes
also recheck current account, login session and exact locale/sitewide grants at
the commit boundary. ZIP imports repeat that authorization for every committed
article; revocation stops remaining entries without undoing earlier commits.

ZIP import opens with **preview only** selected. Review the planned results before
turning off preview. Existing articles remain untouched unless the separate
overwrite permission is selected; published content additionally requires publish
capability. Duplicate paths/identities, mismatched declared locale and conflicting
identities do not authorize replacing existing content. Save/import failures keep
entered text and provide corrective feedback.

The editor's private preview link opens a read-only workspace. Generating a saved
single-article preview, including a draft, requires POST and CSRF. It does not
publish the row or activate an artifact. History and verified artifact links remain
available after refresh/restart. Preview rendering stays network-silent and uses
the existing private artifact reader and site boundary.

Whole-site published previews retain their previous build API and redirect.
Single-article preview records have a distinct scope/key and are refused by the
whole-site candidate builder. The existing reviewed whole-site candidate,
selection and deployment checks remain mandatory; editing/published database
status or generating a preview alone never proves the public website changed.

Configure `SITEOS_ARTICLE_PREVIEW_ROOT` and `SITEOS_ARTICLE_PUBLIC_ORIGIN` for the
independent installation. Do not borrow another site's files or credentials.
