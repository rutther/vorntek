# Authenticated 3D draft preview / 后台 3D 编辑预览

## Public candidate integration / 公开候选整合

New source now freezes enabled `homepage_hero` and `homepage_equipment_preview`
placements into whole-site candidates, including only their active, site-owned
models, saved camera/settings and self-hosted renderer dependencies. A disabled
placement/profile publishes no model. Saving a profile is not deployment;
candidate selection and deployment remain the existing explicit operations.
Changing a view produces a different immutable candidate, not a modification of
an old candidate. Corrupt/missing/unscoped files block the new candidate instead
of leaving an empty successful public slot. Original files remain unchanged.

GLB uses the same pinned model-viewer as authenticated editing. PLY/SOG use the
pinned SuperSplat 1.37.0 bundle with its embedded PlayCanvas 2.23.0 engine; only
the selected file and local dependencies are accessible under the generated
document CSP. SOG publication rejects archive traversal, duplicate entries,
unbounded metadata, encryption and texture references outside the archive.
Model files are bounded to 128MiB, with the existing total candidate size cap.

The first public-candidate source (`5d5ed56`) passed isolated Linux image, fresh
install, old-version upgrade and six PostgreSQL test groups, with cleanup.
It is **not deployed**. Local browser acceptance has rendered synthetic GLB,
binary PLY and a real bundled SOG. Further editing/control fixes in the current
source need their own native gate and final-host publication/restart acceptance.

## Authenticated editing

The 3D profile and slot editors can preview self-contained GLB (glTF 2), PLY and bundled SOG files through
their existing site-scoped asset permission. A draft model need not be copied to
the public website first. The viewer uses the authenticated asset read endpoint,
not its not-yet-published `public_path`. Preview is read-only and no-store;
profile/slot activation and website deployment remain separate actions.

In the profile editor, drag the model, choose **读取当前视角**, then **保存配置**.
The captured world position/target/FOV is written to the ordinary form. Refresh
and reopen to verify the persisted view. Background/rotation/interaction settings
are used by the preview; controls reflect unsaved query parameters without saving.
For splats, a minimal explicit local embedding extension exposes the current
world position, actual focus target and FOV, including after pan/zoom. The original
pinned upstream bundle is retained unchanged; `siteos-viewer.js` is reproducibly
derived by `scripts/vendor_splat_viewer.py`, with both hashes and the exact purpose
recorded in provenance. The extension adds no network/global-state access.
Only the same-origin embedding editor may exchange camera messages. Other pages
retain their normal frame policy; only the authenticated preview permits its
same-origin editor to frame it.

The pinned self-hosted renderer is model-viewer 4.4.0. Its own embedded WebAssembly
requires `wasm-unsafe-eval` **only in this preview document's CSP**; arbitrary JS
`unsafe-eval`, external scripts, remote models and cloud decoders remain disallowed.
Browser modules use ordinary `.js` URLs with `type="module"`, retaining JavaScript
MIME compatibility with the existing Nginx image; no global MIME change is needed.
The file is inspected before displaying a viewer: scoped storage path, size/SHA,
glTF2 format and embedded-resource requirements. Optional external textures/buffers,
Draco/Basis/meshopt dependencies and unsupported/changed/missing files produce a
bounded explanation, not a fake success or remote decoder download. Original
files, downloads, metadata and profile editing remain intact. Current preview
bounds are 128MiB per model and 2MiB for its JSON metadata.

The editor now uses the same authenticated route for GLB, PLY and SOG, not the
legacy unpublished public path. Exported SOG/PLY `viewerUrl` points to the generated
profile document, which exists only after an enabled slot is published. Saving a
profile alone never activates a public slot. Media-audit work previously deferred
by the user is not reopened by this fix.

Visual query overrides can enable and disable unsaved switches in both directions;
they cannot change the authenticated asset URL. For splats, input locking gates
both engine keyboard/gamepad input and canvas pointer gestures. Automatic motion
respects the visitor's reduced-motion preference and explains a paused rotation.
Actual engine asset errors are handled (the pinned viewer has no `error:changed`
state); a slow load gives a recoverable warning after 30 seconds, not false success.

The ordinary asset upload/database contract accepts `.glb`, not standalone
`.gltf`. A compatibility parser branch does not establish `.gltf` upload support;
no new asset extension or database constraint has been introduced here.

Reproduce the frozen browser artifacts with `python scripts/vendor_model_viewer.py`;
the checked-in bytes suffice for ordinary offline image/runtime builds. Run
`node --test scripts/test_three_d_camera.mjs scripts/test_three_d_load_state.mjs scripts/test_three_d_preview_config.mjs` and application cases
`console.test_three_d_preview` / `console.test_content_route_permissions`.
Actual browser render/drag/capture/save/reopen is separate acceptance, not inferred
from these tests. See current implementation status before claiming deployment.
