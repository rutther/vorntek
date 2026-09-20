# Browser acceptance — 2026-09-21 / 浏览器验收

This record covers actual browser interaction against an ephemeral loopback-only
fixture at source commit `613294f` plus the browser-fixture changes under review.
It is **UI evidence**, not PostgreSQL, Compose, Nginx or production acceptance.

本记录是针对回环地址临时夹具的真实浏览器交互证据，基线为源码提交 `613294f`
及本阶段待审浏览器夹具改动。它属于 **UI 证据**，不是 PostgreSQL、Compose、Nginx
或生产验收。

## Isolation / 隔离边界

- Windows host; Microsoft Edge for desktop/mobile and inquiry checks, Codex
  in-app Chromium for the file-upload/download flow.
- Throwaway Django test database in SQLite shared-memory mode. The fixture binds
  only `127.0.0.1`, clears application/database environment variables, disables
  external I/O, scheduling and WhatsApp live sending, and uses only `.invalid`
  contacts and synthetic companies.
- The fixture creates a random temporary administrator password under ignored
  `.runtime/browser-acceptance/credentials.json` and deletes it at shutdown.
  There is no product default administrator password.
- No Hong Kong service, production database, Meta asset, email recipient or
  WhatsApp destination was contacted or changed.

## Observed browser evidence / 浏览器实测证据

1. **Desktop website:** Edge loaded the generated homepage at a 1391-pixel inner
   width. Document client/scroll widths were both 1376 pixels, so no horizontal
   document overflow was present. Browser warning/error logs were empty.
2. **Mobile website:** at an explicit 375×812 viewport, inner width was 375,
   document/body client and scroll widths were 360, form and input widths were
   317, and the mobile menu control was visible. The viewport override was reset.
3. **Inquiry:** the browser submitted one fictional industrial inquiry and showed
   `Inquiry received in the demo CRM.` The form reset. Authenticated CRM then
   displayed the same fictional name/company, `waterQualitySensors`, application
   context, consent decisions and no Meta outbox event.
4. **Login:** the generated fixture account authenticated through the real login,
   CSRF and session flow. The CRM identified it as system administrator.
5. **Import:** the browser selected and uploaded the actual generated 21-column
   CSV. The first stale fixture value (`31.0`) was correctly rejected because the
   current recomputed value was `36.0`; after fixing the fixture, a new hash/batch
   preview showed one added row and zero rejects. Explicit confirmation completed
   the batch and the 21-column customer appeared in the dense pool view.
6. **Export and disk save:** the current-filter 21-column export required its
   confirmation dialog, emitted an actual browser download event and saved
   `vorntek-customer-pool-standard21.csv` in the browser Downloads directory.
   Independent filesystem inspection found 411 bytes, UTF-8 BOM, one data row,
   exactly 21 columns (`phone` through `source_channel`) and SHA-256
   `d20588c5b657eb813ae9281a85119e3c5552d3c4ff3c455cebfb119515b0d124`.
7. **Shutdown evidence:** the fixture reported one lead submission, three
   companies, two import batches (one intentionally rejected preview and one
   completed), one imported pool row and zero queued XLSX jobs. The 21-column CSV
   is immediate and therefore does not create a background export job.

The Edge extension itself lacked local-file permission and blocked its template
response with `ERR_BLOCKED_BY_CLIENT`; that is a browser-extension environment
limit, not an application pass. The independent in-app browser completed both the
upload and actual disk download, so the application path has direct browser
evidence without changing the user's Edge extension permissions.

Edge 扩展自身没有本地文件权限，并以 `ERR_BLOCKED_BY_CLIENT` 阻断模板响应；这属于
浏览器扩展环境限制，不能记成应用通过。随后使用独立内置浏览器完成真实上传及落盘下载，
没有为验收擅自修改用户的 Edge 扩展权限。

## Reproduce / 复现

```sh
python3 scripts/run_browser_acceptance_fixture.py --port 8765 --review-seconds 900
```

Use the printed loopback URL, generated upload path and temporary credential-file
path. Stop the process normally; it writes a non-secret count report to
`.runtime/browser-acceptance/evidence.json` and removes the credential file.

使用命令输出的回环 URL、上传文件路径和临时凭据文件路径。正常停止后，脚本把不含秘密的
计数报告写入 `.runtime/browser-acceptance/evidence.json`，并删除凭据文件。

## Remaining gates / 仍待验收

- Repeat equivalent inquiry/login/import/export/download paths in the exact Linux
  PostgreSQL 18/Compose/Nginx candidate after those runtimes become available.
- Verify queued XLSX export worker, restart persistence and permission-denial UI
  in that packaged environment.
- This run does not authorize production deployment or real-data testing.
