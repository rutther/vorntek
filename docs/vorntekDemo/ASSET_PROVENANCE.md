# Corporate-site asset provenance — industrial B2B

No customer photography, company testimonials, certificates or competitor files
are part of this package. Vorntek logo is the existing project brand asset.

## Original equipment concepts

`scripts/build_industrial_models.py` constructs six metre-scale equipment models
from explicitly authored manufactured parts. `catalog.json` supplies the product
descriptions and concept dimensions. These are illustrative engineering concepts,
not measured equipment performance or certified product drawings.

The catalogue PNGs (`inspect`, `measure`, `assemble`, `convey`, `mark`, `connect`,
their `-side` views and `inspect-detail`) are actual browser renders of those
same GLBs using the project's pinned model-viewer 4.4.0. Capture setup:
1422×800 viewport, neutral environment, exposure 1.1, shadow intensity 1;
front orbit 35deg/76deg/125%, side -50deg/78deg/125%, detail20deg/78deg/85%,
30deg field of view. They may be regenerated from that deterministic geometry.

## Generated application photography

Created with the built-in image generation tool on 2026-10-16:

- `industrial-hero.png`: a bright integration workshop, graphite-framed guarded
  vision cell, cobalt lower cabinet, silver roller transfer and assembly bench;
  eye-level view, left-side space for HTML typography, no people/logos/labels.
- `inspection-application.png`: component-inspection setting with reference
  measuring bench, camera enclosure, representative trays and diffuse daylight;
  no people, company branding, labels or borrowed product identifiers.
- `assembly-application.png`: modular steel assembly benches, blue tool panels,
  fixtures, material bins and connecting roller conveyor in a light factory;
  no people, wood, logos, certifications or customer-identifiable material.

Application photography illustrates the working context. Exact form and
dimensions of the six product concepts come from the GLBs and specification
tables, rather than from the generated contextual photographs.

The earlier office-furniture hero is a discarded concept held outside this
package; it is not a publication input.

## Structure videos and specification files

`videos/inspect-overview.webm` and `videos/assemble-overview.webm` are approximately
eight-second, 1280×720 turntables recorded from the same original GLBs through
model-viewer's rendered canvas. They show equipment structure, not a claimed
production cycle. The development receiver is loopback-only and accepts two
bounded authored asset names; it is not a public upload endpoint.

The six UTF-8 specification downloads are generated from the same catalogue
entries used to render the tables. No CRM records or customer data are exported.

## Third-party font and viewer

Manrope Latin variable WOFF2 is self-hosted from the official Google Fonts
distribution: `https://fonts.gstatic.com/s/manrope/v20/xn7gYHE41ni1AdIRggexSg.woff2`.
Its SIL Open Font License is retained at `assets/fonts/OFL.txt`, retrieved from
the official `google/fonts` repository `ofl/manrope/OFL.txt`. Chinese falls back
to locally available CJK fonts; no remote browser font request is used.

model-viewer remains the already pinned 4.4.0 project dependency. Its exact
upstream bytes, provenance and all bundled dependency notices are preserved.
Public notice filenames gain `.txt` to comply with the existing publication
allowlist. No license text has been changed.
