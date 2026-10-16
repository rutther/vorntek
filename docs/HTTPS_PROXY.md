# HTTPS edge and website proxy / HTTPS 入口与网站代理

The base Compose file is a private HTTP installation. A public TLS edge needs an
explicit trust contract, not a global proxy range or blindly forwarded headers.
This procedure creates configuration only; it never changes DNS, firewall rules,
the installed host Nginx, secrets, containers or another installation.

默认 Compose 是私有 HTTP 安装。公网 HTTPS 入口需要明确的代理信任合同，不能信任全网、
猜测 Docker 网段或直接转发用户提供的头。下列工具只生成配置，不修改线上服务。

## 1. Verify both peers / 核实两跳对端

Record the **actual socket peer** seen by the website container when the host
edge connects to its loopback-published port. It may be a Docker bridge gateway,
not `127.0.0.1`. Also record the website container IP seen by CRM. Never infer
these from an example subnet. Recreating containers/networks can change them;
pin owned addresses in the deployment configuration or revalidate before traffic
is enabled. Do not trust an entire shared host/container network as a shortcut.

分别核实主机入口→网站容器、网站容器→CRM 的实际对端。端口映射后的对端可能是网桥网关，
不是回环地址。更换网络或容器后重新核实；未确认信任地址时不要切换公网流量。

## 2. Generate an opt-in website config / 生成显式启用配置

Replace `VERIFIED_EDGE_PEER_IP` with the verified **single IP**, not a hostname or
CIDR. The output path must be new; the renderer refuses to overwrite a file.

```sh
python3 scripts/render_https_proxy.py --edge-proxy-ip VERIFIED_EDGE_PEER_IP --output website-https.conf
```

Omit `--output` to print the plan without writing a file. The generated config
keeps the existing upload limits, static routes and serving-store contract. Mount
the reviewed file read-only at `/etc/nginx/conf.d/default.conf` in the website
service of this installation only. Run Nginx syntax validation before replacement.
The packaged HTTP default is deliberately unchanged.

工具只接受明确的单个 IP。生成文件保留上传限制、静态路径和网站服务目录合同；只在目标安装的
网站容器中以只读方式挂载，替换前做语法检查。默认 HTTP 安装不自动启用信任。

## 3. Edge and application settings / 入口与应用配置

The TLS edge must replace incoming `X-Forwarded-For` with `$remote_addr`, and
`X-Forwarded-Proto` with `$scheme`. Do not use `$proxy_add_x_forwarded_for` at this
first trusted boundary. Preserve the validated host. Keep the container's host
port bound to loopback, with no direct public CRM/database port.

Configure the application consistently for the real HTTPS origin:

- `NEWCROWN_SITE_URL` and `NEWCROWN_ARTICLE_PUBLIC_ORIGIN`: exact HTTPS origin.
- `NEWCROWN_ALLOWED_HOSTS`: this installation hostname and required internal health host.
- `NEWCROWN_TRUSTED_PROXY_CIDRS`: the website peer's exact `/32` or `/128` only.
- In the CRM/initializer/workers environment, set
  `SITEOS_ADMIN_SESSION_COOKIE_SECURE=1`, `SITEOS_ADMIN_CSRF_COOKIE_SECURE=1`,
  and `SITEOS_ADMIN_TRUST_X_FORWARDED_PROTO=1`. The base Compose hard-codes HTTP
  cookie settings, so a reviewed Compose override is required.
- Keep `SITEOS_ADMIN_SECURE_SSL_REDIRECT=0` for the existing direct HTTP health
  check. Redirect public HTTP at the edge while retaining the HTTP-01 challenge
  path. Do not enable an application redirect that breaks internal health checks.
- Preserve the installation's existing delivery switches, secrets and queues.
  TLS setup never authorizes outgoing customer/platform traffic.

入口必须覆盖转发头；应用只信任网站容器的精确地址。安全 Cookie、来源、主机及预览来源须一致。
内部 HTTP 健康检查继续可用；公网跳转交由入口处理。证书续期的 HTTP-01 路径必须保留。

## 4. Verify before switching / 切换前验证

On Linux with Nginx available, run the real isolated HTTP fixture:

```sh
python3 scripts/test_https_proxy_runtime.py
```

It starts its own loopback-only Nginx and echo backend, verifies trusted and
untrusted peers, rejects spoofed protocol/client headers, and checks two clients
remain distinguishable across the edge. It does not install/reload host config.
`--baseline` deliberately reproduces the old header-loss contract and must fail;
this is diagnostic, not an alternate production configuration.

Then verify the **actual final TLS route**: certificate/hostname, HTTP redirect,
login and secure cookies, CSRF-protected save, public inquiry, distinct client
resolution, application health and service restart. Standalone HTTP fixtures do
not prove the real certificate, Docker peer identities or final deployment.

最后必须核对实际 HTTPS 网址、证书、登录、安全 Cookie、CSRF 保存、询盘、客户端区分和重启。
独立 HTTP 夹具通过不能冒充真实 TLS 或正式部署验收。

The trust semantics follow the official Nginx
[real IP module](https://nginx.org/en/docs/http/ngx_http_realip_module.html) and
[geo module](https://nginx.org/en/docs/http/ngx_http_geo_module.html): protocol
trust uses the original socket peer, not the rewritten client IP.
