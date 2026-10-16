# Install the industrial company example / 工业企业示例

Start with the root README and a new isolated installation. The bundled public
site already contains six equipment concepts, three applications and engineering
guides. Its media, video and GLB files are tracked source resources; no production
database or private operator script is needed.

Create your own administrator. Keep external sending, scheduled delivery and
live WhatsApp disabled. First view the industrial import plan, then explicitly
import with your existing administrator:

```sh
docker compose exec crm python manage.py seed_industrial_examples
docker compose exec crm python manage.py seed_industrial_examples --apply --synthetic-only --owner YOUR_ADMIN
```

The `industrial-v1` pack installs fifteen actual media assets, six disabled GLB
profiles and one engineering article in English, Chinese and Arabic. A missing
Arabic locale is added with RTL direction; existing locale/site settings and
passwords are preserved. Article source is the same tracked
`docs/vorntekDemo/articles.json` copied into the application image at build time.
Profiles remain disabled and no public placement is created automatically.

The article key `industrialInspectionGuide` and slug
`inspection-station-planning` deliberately coexist with the earlier optional
general/content exercise. The existing published demonstration uses its retained
historical bookmark; new installations need not adopt that old identity.
The command refuses unknown conflicting identities or an already owned asset,
and repeating the same pack retains later edits without duplicating records.
A different digest refuses rather than silently upgrading installed examples.
No purge/reset command is provided for the industrial pack.

## Review and publish / 审阅与发布

1. Open `/admin/`, then the Articles workspace. Filter the title “Planning an
   inspection station around the component” or “围绕工件，规划一台检测工作站”.
2. Inspect the draft, locale, cover and engineering text. Save as published using
   the normal article editor when ready. This changes content status, not the
   served website. Publish the translations you intend to include.
3. Open the article release/private preview workflow. Build the reviewed whole
   article preview, then its exact whole-site candidate. If content is incomplete,
   fix the named language/asset issue rather than bypassing validation.
4. In the release workspace, inspect that candidate, select it with the required
   reason/current-version check, then deploy the selected version. Selection alone
   does not change the public site. Retain the deployment receipt.
5. Check `/articles/inspection-station-planning/`,
   `/zh/articles/inspection-station-planning/` and
   `/ar/articles/inspection-station-planning/` after deployment; Arabic uses RTL.
   An unpublished translation should not be represented as a public article.

Use the 3D configuration workspace to inspect the six disabled profiles. Product
pages work from their bundled models. Public model replacement requires an
explicit slot binding and the ordinary website publication steps; importing
profiles does not activate or overwrite a slot.

For inquiry/task/workflow sample records, optionally run the independent
`seed_example_bundle` command documented in EXAMPLE_DATA.md. That pack supplies
cross-module business exercises and multiple workflow instances; it does not
replace the industrial company package or create real provider receipts.

中文：工业官网直接随源码提供；后台工业文章与素材通过上述命令显式导入。默认只输出计划，
不要求装入真实数据库或导出线上数据。导入不改账号、密码、域名、平台凭据和发布指针。
新示例使用独立文章标识，旧练习无需删除。发布仍需正常编辑、预览、选版和部署步骤。
