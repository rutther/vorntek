# Authenticated 3D draft preview / 后台 3D 编辑预览

The 3D profile and slot editors can preview self-contained GLB (glTF 2) files through
their existing site-scoped asset permission. A draft model need not be copied to
the public website first. The viewer uses the authenticated asset read endpoint,
not its not-yet-published `public_path`. Preview is read-only and no-store;
profile/slot activation and website deployment remain separate actions.

In the profile editor, drag the model, choose **读取当前视角**, then **保存配置**.
The captured world position/target/FOV is written to the ordinary form. Refresh
and reopen to verify the persisted view. Background/rotation/interaction settings
are used by the preview; controls reflect unsaved query parameters without saving.
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

This fixes the authenticated GLB editing path. It does **not** claim that the
legacy splat/PLY renderer or every public website slot is implemented or tested.
The existing SOG/PLY routing remains separate; don't activate a public slot based
only on successful private preview. Media-audit work previously deferred by the
user is not reopened by this fix.

The ordinary asset upload/database contract accepts `.glb`, not standalone
`.gltf`. A compatibility parser branch does not establish `.gltf` upload support;
no new asset extension or database constraint has been introduced here.

Reproduce the frozen browser artifacts with `python scripts/vendor_model_viewer.py`;
the checked-in bytes suffice for ordinary offline image/runtime builds. Run
`node --test scripts/test_three_d_camera.mjs` and application cases
`console.test_three_d_preview` / `console.test_content_route_permissions`.
Actual browser render/drag/capture/save/reopen is separate acceptance, not inferred
from these tests. See current implementation status before claiming deployment.
