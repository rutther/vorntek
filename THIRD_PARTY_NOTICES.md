# Third-party notices / 第三方组件声明

2026-09-13 — reviewed source pre-release published; binary redistribution review remains open.
Project code is MIT. Source/history evidence is version-bound in the release review;
it does not approve arbitrary future images or certify all license obligations.
Third-party components keep their own notices and terms; they do not grant rights
over website photographs, trademarks, customer material or unrelated content.

## Vendored browser components

| Component | Observed version | License evidence retained |
| --- | --- | --- |
| Lucide | 1.8.0 | [Full upstream ISC notice plus Feather-derived-icon MIT notice](apps/crm/console/static/console/lucide.LICENSE.txt) |
| Tabler core | 1.4.0 | Package declares MIT; [existing notice](apps/crm/console/static/console/vendor/tabler/1.4.0/LICENSE) retained |
| Bootstrap, bundled by Tabler | 5.3.7 | [MIT notice from the exact package](apps/crm/console/static/console/vendor/tabler/1.4.0/BOOTSTRAP_LICENSE) |
| Popper core, bundled by Tabler | 2.11.8 | [MIT notice from the exact package](apps/crm/console/static/console/vendor/tabler/1.4.0/POPPER_LICENSE) |

Downloaded package archives were checked against their registry SHA-512 integrity
metadata **without executing package scripts**. Lucide JS and its full license,
Tabler JS/CSS, and the added Bootstrap/Popper notices matched exact package bytes.
Tabler's upstream JS source map identifies Popper2.11.8; its JS banner identifies
Bootstrap5.3.7. Not every library mentioned in Tabler's development dependency list
is shipped in this project; that list alone is not a bundled-component inventory.

Evidence is in [vendor package records](docs/review/vendor-packages.json).
Lucide's [official versioned license](https://github.com/lucide-icons/lucide/blob/1.8.0/LICENSE)
also matched the package license. Tabler's attempted version-tag LICENSE URLs
were unavailable and its npm archive omitted its own root LICENSE; therefore the
existing notice is described as retained, not falsely certified as a fetched
tag-file match. Package metadata and vendored headers both declare MIT.

Upstream projects: [Lucide](https://github.com/lucide-icons/lucide),
[Tabler](https://github.com/tabler/tabler), [Bootstrap](https://github.com/twbs/bootstrap),
[Popper](https://github.com/popperjs/popper-core).

## Python runtime dependencies

The current source lock contains 26 distributions. The current-host inventory is
bound to that lock's SHA-256 and includes the new MIT-declared MarkdownIt-py 4.2.0
and mdurl 0.1.2 license-file hashes. It is recorded in
[python-dependencies.json](docs/review/python-dependencies.json) and reproducible
with `python scripts/inventory_dependencies.py` in that interpreter.

The [actual Linux image inventory](docs/review/python-dependencies-linux.json)
records all 24 distributions from the preceding lock inside the f7 CRM image,
identified by its immutable image ID and preceding lock hash. It is retained as
historical image evidence, not relabeled to cover the two packages added later.
A new Linux image must be rebuilt and inventoried before binary publication. No
Python package is relicensed to MIT by this project; image publication remains
gated on the applicable terms and native/OS component review.

The inventory includes BSD/MIT/Apache/PSF declarations and dependencies declaring
MPL or LGPL; **do not label every dependency MIT**. Some packages also carry
additional notices or bundled-library obligations. Preserve distribution license
files in built images and verify the actual Linux wheels, OS packages and image
layers before publication. This current-host interpreter inventory is not proof
of those future image contents or a license-compatibility opinion.

## Fonts, photographs, brand and historical evidence

- No standalone `.woff`, `.woff2`, `.ttf` or `.otf` files were found in the staged
  tree. Narrow CSS/HTML searches did not identify Google Fonts/rsms imports. This
  is a source observation, not a complete runtime network/resource inventory.
- The old website image tree and CRM brand mark were moved into a private,
  ignored local archive before Vorntek replacement. They must not enter the final
  staged tree or container context. This operation did not alter the original site.
- Current Vorntek website images comprise eight original AI-generated PNGs;
  the CRM uses a copy of that logo. Prompts, hashes and the fictional-company
  disclosure are in [the demo pack](docs/vorntekDemo/README.md). The project
  offers its generated demo assets for reuse to the extent it can grant rights;
  this is not trademark clearance, third-party likeness clearance or a guarantee
  of copyright protection or exclusive ownership in generated images.
- The two QA images and their comparison HTML remain locally but are excluded
  from Git and the image build context. No assertion was made that labels inside
  those screenshots represented synthetic rather than real people/customers.
- Historical production and third-party-content seeding helpers remain under
  review. They are not part of Compose installation or permission to copy content.

项目代码已按用户委托选择 MIT，源码候选版及对应历史已有检查记录。未来修改仍需
复核；实际预构建镜像的原生库、许可证及源码材料仍未全部闭合，见
[二进制分发审查](docs/review/BINARY_REDISTRIBUTION.md)。不要上传客户数据或旧截图。

See [public-release gate](docs/PUBLIC_RELEASE_REVIEW.md) before publication.
