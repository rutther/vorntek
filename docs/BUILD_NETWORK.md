# Build-network troubleshooting / 构建网络排障

Normal installation uses `docker compose build`. Package downloads must succeed;
`Temporary failure in name resolution` does not mean the pinned version is absent.

On the authorized Linux test host, a fresh public clone failed while pip resolved
the package index through Docker's default build network. Host networking had
worked in earlier isolated builds. If host DNS works, the operator can explicitly
choose this **build-only** fallback for a trusted checkout:

```sh
docker build --network=host -f deploy/Dockerfile.crm -t vorntek-crm:dev .
docker build --network=host -f deploy/Dockerfile.website -t vorntek-website:dev .
docker build --network=host -f deploy/Dockerfile.maintenance -t vorntek-maintenance:dev .
docker compose up -d
```

Use the exact image tags from your private `.env` if they differ from these fresh
defaults. Review Dockerfiles before granting build steps access to the host network;
this is a Linux-specific fallback, not a default for untrusted code. Do not mount
host credentials or the Docker socket into build steps. Never publish the intermediate
maintenance donor stage or its build cache.

This does not change Docker daemon DNS, the firewall, the runtime `private` network,
marketing settings, database ports or unrelated services. Do not add `network_mode:
host` to the running CRM/database as a package-download workaround. A warm-cache
build is not proof that an uncached dependency download works.

默认使用 Compose 构建。若是构建容器 DNS 故障，可对可信源码显式使用以上仅构建阶段的
Linux host 网络回退，并核对镜像标签；不要因此放开运行时数据库/CRM 网络、修改全机
防火墙或删除依赖锁定。构建失败日志和回退实测结果必须分别记录，不把失败写成通过。
