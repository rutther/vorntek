# Vorntek

面向工业 B2B 团队、可自行部署的网站与业务管理系统，包含 PostgreSQL、Docker Compose、
可配置询盘、CRM、营销闭环、内容发布、账号权限及备份恢复工具。

[English](README.md)

可替换的工业企业示例包含六种设备、应用方案、工程指南、三维模型与询盘表单。
底层业务字段和分类可以配置，不把工业示例变成固定行业规则。
参见[发行范围](docs/RELEASE_SCOPE.md)和[当前验证状态](docs/IMPLEMENTATION_STATUS.md)。

已公开的 [v0.2.0-rc.3](https://github.com/rutther/vorntek/releases/tag/v0.2.0-rc.3)、
[对应 CI](https://github.com/rutther/vorntek/actions/runs/38982603782)及
[历史交付证据](docs/PUBLIC_DELIVERY.md)描述的是上一版本，不代表当前源码或任何服务器状态。
当前源码版本以 [`VERSION`](VERSION) 为准，并由 `/healthz/` 返回。不可变下载包、校验和及
运行证据发布在同名 GitHub Release；[验证状态](docs/IMPLEMENTATION_STATUS.md)分别记录本地、
CI 与公开下载后的证据。

## 包含什么

- 中英文官网，示例内容和目录条目可配置，不固定行业或强制七类业务。
- 共用询盘处理，可配置字段、必填规则和后端校验；个人、企业、支持等场景真实保存到 CRM。
- 员工账号与角色、线索、客户公海、分配、任务、商机、导入模板、严格的 21 列
  客户池 CSV 往返和后台 XLSX 导出。
- 唯一 SQL 安装链、数据库/文件持久化、健康检查、受保护的备份恢复和默认暂停的集成任务。
- 平台接入、获客方案、多闭环实例、多 Pixel、流程图、步骤图示、配置与独立运行证据。
- 文章私有预览、固化候选、显式部署和网站服务指针恢复，不把草稿编辑与线上发布混为一谈。
- 保留可配置的追踪合同；Meta、Google 广告测量、邮件及 WhatsApp 默认关闭。
  本发行版尚未验证真实 Meta 去重，不将模拟测试冒充外部验收。

Vorntek 网站内容和可选业务样例均为**虚构合成数据**，不代表真实企业经历、客户案例、
产品认证或固定行业模型。安装不要求导入示例业务数据；示例是可编辑记录，不是假成功状态，
也没有连接真实设备。

## 隔离安装

前提：Python 3、Docker Engine、支持 Linux 容器的 Docker Compose。
使用干净目录及独立环境，不要连接生产数据库。

```sh
python3 scripts/build_vorntek_site.py
python3 scripts/configure.py
docker compose build
docker compose up -d
docker compose ps -a
docker compose exec crm python manage.py createsuperuser
```

打开 `http://localhost:8088/` 和 `http://localhost:8088/admin/`。
**没有默认管理员密码**。初始化会检查并执行 SQL 链，建立最小站点/角色/表单配置，
收集后台静态资源。默认仅监听回环地址，应用网络限制对外连接。

仅填写虚构资料。公网 HTTP 无法保护密码和表单数据；真实使用前应配置 HTTPS、
安全 Cookie、准确来源、访问权限、保留政策及备份，不能直接把示例暴露到互联网。

如果 Docker 构建网络无法解析依赖下载主机，请使用已记录的、仅针对构建阶段的
[DNS 排障流程](docs/BUILD_NETWORK.md)；不要放宽应用网络或改动主机上的无关服务。

## 配置与升级

工业后台示例独立可选，先查看计划，再使用刚创建的管理员显式导入
（将 `YOUR_ADMIN` 替换为管理员名）：

```sh
docker compose exec crm python manage.py seed_industrial_examples
docker compose exec crm python manage.py seed_industrial_examples --apply --synthetic-only --owner YOUR_ADMIN
```

它添加真实媒体、六个停用的3D展示配置和中英阿工程文章草稿；不自动发布、不启用接入、
不替换已有业务。[工业示例指南](docs/INDUSTRIAL_EXAMPLE.md)说明正常预览、选版和发布路径。
跨模块业务示例另行可选，与企业内容分开。

可选的[合成示例数据包](docs/EXAMPLE_DATA.md)独立版本化，覆盖关联的询盘、企业、联系人、
商机、任务、闭环实例、内容和文件。默认只生成计划，不写数据：
`docker compose exec crm python manage.py seed_example_bundle` 及
`docker compose exec crm python manage.py seed_content_examples`。
显式导入需要合成目标及既有管理员；重复导入保留后续修改，限定重置不是清库。
旧 [200 家客户演示](docs/DEMO_DATA.md)仅为历史可选方案，不是产品默认。不要使用旧清库脚本。

`scripts/configure.py` 创建 `.env` 和 `.secrets/` 中四份独立秘密，拒绝覆盖已有配置。
不要将秘密放入 Git、截图或公开产物。Windows 下应将目录权限限制为本人及容器运行时。
保险库密钥需要与受控恢复材料配套保管，单独数据库备份无法完整恢复。

新安装的 Compose 项目/镜像使用 Vorntek 名称。暂保留 `NEWCROWN_*` 设置、
`newcrown` 数据库/角色标识及 `siteos_demo` 站点代码作为**技术兼容标识**，
不是对外品牌。不要为了换品牌而修改已有 Compose 项目名或清空卷，否则可能选中另一套库。

新安装使用 `general` 通用询盘类别；旧 `project-inquiry` 表单代码是技术兼容标识，
不是行业要求。初始化只补充缺少的配置，不会悄悄转换已有表单；已有字段的必填要求由表单编辑器显式配置。
见[替换检查表](docs/VORNTEK_ROLLOUT.md)。不改写已执行的 SQL；不完整历史迁移账本
会被拒绝，不能删除账本记录或运行旧演示重置工具来绕过检查。

更新已有安装前请遵循中英双语[更新操作手册](docs/UPGRADE.md)。迁移前必须在外部写入
已暂停的情况下通过只读 `python manage.py release_preflight --strict` 检查。

文章私有预览要求将 `NEWCROWN_ARTICLE_PUBLIC_ORIGIN` 配置为本安装真实的 HTTPS
公开来源；示例值有意使用不可上线域名。私有预览需要登录且不会激活官网，其资源与版本
边界详见[文章交付架构](docs/ARTICLE_DELIVERY.md)。

网站容器前使用 HTTPS 代理时，按[可信代理部署说明](docs/HTTPS_PROXY.md)核实精确对端。
默认 HTTP 配置不信任传入转发头；只打开安全 Cookie 不等于完成两层代理部署。

## 开发与验证

```sh
python3 -m pip install -r apps/crm/requirements.lock -r requirements-dev.txt
python3 -m unittest discover -s tests -v
npm --prefix apps/crm/flow-ui ci
node --test scripts/test_measurement.mjs scripts/test_form_status.mjs scripts/test_credential_receipt.mjs scripts/test_vorntek_form.mjs
npm --prefix apps/crm/flow-ui test
npm --prefix apps/crm/flow-ui run build
python3 scripts/run_application_tests.py
```

Node 依赖隔离安装在 `apps/crm/flow-ui`；根目录 JS 检查会复用其中声明的 `jsdom`
版本，因此必须先执行上述安装命令。应用运行器使用合成 SQLite 并阻断非回环 Python 网络，
不能代替 PostgreSQL/浏览器验收。另行安装 PostgreSQL 后可执行
`python3 scripts/test_postgres_install.py --pg-bin /path/to/bin --http`。
公开下载的源码压缩包有意不包含 `.git`；在该环境中，根测试套件只明确跳过依赖
Git 历史/跟踪清单的完整性检查。仓库检出仍会执行这些检查，其他压缩包可运行测试照常执行。

网站内容由[目录配置](docs/vorntekDemo/catalog.json)和
`scripts/build_vorntek_site.py` 生成。修改脚本/样式后应重新生成 `apps/website/`，
更新基于内容哈希的缓存版本。
当前可选目录为行业中立示例，不与旧后端业务枚举绑定；条目数量及标识可变，
也支持空目录。`--catalog PATH --output DIRECTORY` 可另建完整静态产物；生成器
不删除目标中的其他文件。参见[当前示例内容说明](docs/vorntekDemo/README.md)，
不要把旧工业演示的历史验收当作当前产品要求。

## 运维与许可

- [配置参考](docs/CONFIGURATION.md)：来源地址、公开设置、文件秘密、HTTPS 与默认关闭的外部通道。
- [日常管理](docs/ADMINISTRATION.md)：启动、账号角色、业务操作、worker 与安全停止。
- [开发规范](docs/DEVELOPMENT.md)及[故障排查](docs/TROUBLESHOOTING.md)：可复现检查和失败关闭诊断。
- [备份恢复](docs/BACKUP_RESTORE.md)：数据库、文件卷与秘密配套恢复；恢复时停止写入方并暂停外部队列。
- [后台任务](docs/BACKGROUND_TASKS.md)：区分导出 worker 与外部集成调度。
- [账号安全](docs/ACCOUNT_SECURITY.md)、[安全政策](SECURITY.md)、
  [贡献说明](CONTRIBUTING.md)、[CI](docs/CI.md)。

`docker compose down -v` 会销毁持久化卷，**不是升级命令**。
已发布源码及其初始历史已经检查；容器二进制再分发及最终发布验收仍未完成。
公开项目不包含真实客户、凭据、原企业照片或私有备份。

项目代码采用 [MIT](LICENSE)，第三方组件保留自己的许可，见[第三方声明](THIRD_PARTY_NOTICES.md)。
AI 提示词与哈希见[演示素材包](docs/vorntekDemo/README.md)，不作为商标已核准或
AI 产物具有排他权利的证明。
