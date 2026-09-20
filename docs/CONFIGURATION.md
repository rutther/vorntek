# Configuration / 配置

This page describes the source candidate's supported configuration boundaries.
It does not contain production values. Start from `.env.example`; never copy an
old host's `.env`, Compose project name, secret directory or database volume.

本页说明源码候选支持的配置边界，不包含生产值。新安装从 `.env.example` 开始，
不得复制旧主机的 `.env`、Compose 项目名、秘密目录或数据库卷。

## Generate one installation / 生成一套安装配置

```sh
python3 scripts/configure.py
docker compose config --quiet
```

The script exclusively creates `.env` plus four random files under `.secrets/`
and refuses to overwrite either location. The files provide the PostgreSQL admin
password, application database password, Django signing key and encrypted-secret
vault key. Keep the complete set private and recoverable; do not print, commit,
email or place it in a public backup.

脚本以排他方式创建 `.env` 和 `.secrets/` 下四份随机秘密；任一目标已存在就拒绝
覆盖。四份文件分别用于 PostgreSQL 管理密码、应用数据库密码、Django 签名密钥和
加密秘密保险库密钥。整套材料必须私密、可恢复，不得打印、提交、邮件发送或放入公开备份。

## Public non-secret settings / 公开非秘密设置

| Setting | Purpose | Fresh default |
| --- | --- | --- |
| `COMPOSE_PROJECT_NAME` | Owns container/network/volume names; changing it selects another installation | `vorntek` |
| `NEWCROWN_HTTP_PORT` | Loopback front-door port | `8088` |
| `NEWCROWN_SITE_URL` | Canonical browser origin used by CSRF and public-form checks | `http://localhost:8088` |
| `NEWCROWN_ALLOWED_HOSTS` | Comma-separated Django host allowlist | `localhost,127.0.0.1,crm` |
| `NEWCROWN_ARTICLE_PUBLIC_ORIGIN` | HTTPS origin used while rendering private article candidates | `https://vorntek.example` (non-live placeholder) |
| `NEWCROWN_*_IMAGE` | Exact local/registry image references for CRM, website and maintenance | development tags |

`NEWCROWN_*` and the `newcrown` database/role names are compatibility identifiers,
not public branding. Do not rename an existing project to improve appearance: it
can silently attach a different set of volumes.

`NEWCROWN_*` 以及数据库/角色名 `newcrown` 是兼容标识，不是品牌。不要为了名称
好看而修改已安装项目名，否则可能悄悄切换到另一组卷。

## Secrets and optional channels / 秘密与可选通道

Core secrets are mounted from `/run/secrets`; the application refuses simultaneous
direct and `_FILE` values. SMTP also accepts `SITEOS_EMAIL_HOST_PASSWORD_FILE` in
a private Compose override. Do not put SMTP, Meta, Google or WhatsApp credentials
in `.env.example`, Git, images or screenshots. Platform secrets configured through
the CRM are encrypted by the vault key.

核心秘密从 `/run/secrets` 挂载；同一秘密同时提供直接值和 `_FILE` 会被拒绝。
私有 Compose override 可用 `SITEOS_EMAIL_HOST_PASSWORD_FILE` 提供 SMTP 密码。
SMTP、Meta、Google、WhatsApp 凭据不得写入示例环境、Git、镜像或截图；通过 CRM
配置的平台秘密依赖保险库密钥加密。

Base Compose deliberately fixes external I/O, scheduled work and WhatsApp live
sending to disabled values and uses an internal application network. Enabling a
real channel requires a separately reviewed override and platform acceptance; an
`.env` edit alone is not supported. WhatsApp remains out of current scope.

基础 Compose 固定关闭外发、定时任务和 WhatsApp 实发，并使用内部应用网络。真实
通道必须通过单独审查的 override 和平台验收启用，不能只改 `.env`；WhatsApp 当前仍暂停。

## HTTPS and public operation / HTTPS 与公网运行

The default is loopback HTTP for isolated acceptance. A public installation needs
an independently tested HTTPS entrypoint plus correct allowed hosts, trusted
origins, secure session/CSRF cookies, HSTS policy and proxy-protocol trust. Do not
publish port 8088 directly or copy another domain's settings. Run the read-only
preflight after every configuration change:

默认配置只用于回环 HTTP 隔离验收。公网安装必须另行验证 HTTPS 入口，并准确配置
Host、可信来源、安全 Cookie、HSTS 和代理协议；不得直接暴露 8088，也不得复制别的
域名配置。每次配置变更后运行只读检查：

```sh
docker compose run --rm --no-deps crm python manage.py release_preflight --strict
```

`--strict` requires PostgreSQL, a valid migration ledger, usable storage and all
external writers paused. It does not apply migrations or create missing paths.
