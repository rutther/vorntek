# Vorntek

可自行部署的工业企业官网与 CRM，包含 PostgreSQL、Docker Compose、询盘留资、
客户管理、账号权限、导入导出和备份恢复工具。

[English](README.md)

**源码与候选版本已公开，不等于生产安全上线。** Vorntek 已运行在授权的独立测试实例，含 200 家
初始化虚构客户和销售验收转换的 1 家合成客户；完整发布验收仍在继续。
请查看[部署证据](docs/VORNTEK_DEPLOYMENT.md)及[当前状态](docs/IMPLEMENTATION_STATUS.md)。

[GitHub 仓库](https://github.com/rutther/vorntek)和
[v0.1.0-rc.1](https://github.com/rutther/vorntek/releases/tag/v0.1.0-rc.1)已公开；
[该版本的两项 CI](https://github.com/rutther/vorntek/actions/runs/34712023363)均通过，
独立克隆安装、询盘/登录和重启检查通过。详见[交付证据与剩余限制](docs/PUBLIC_DELIVERY.md)。

## 包含什么

- 中英文官网：15 个页面、七类工业业务、原创 AI Logo 及 AI 产品/车间概念图。
- 首页和联系页共用询盘逻辑，将业务方向、项目说明和应用背景保存到 CRM。
- 员工账号与角色、线索、客户公海、分配、任务、商机、导入模板和后台 XLSX 导出。
- 唯一 SQL 安装链、数据库/文件持久化、健康检查、受保护的备份恢复和默认暂停的集成任务。
- 保留可配置的追踪合同；Meta、Google 广告测量、邮件及 WhatsApp 默认关闭。
  本发行版尚未验证真实 Meta 去重，不将模拟测试冒充外部验收。

Vorntek 是一家**虚构中国企业**，涵盖循环水控制、液体控制、水质传感器、气体传感器、
丙烷微型暖气机、小型涡轮喷气式发动机和工业数据分析。图表/CSV 使用确定性合成数据，
未连接真实设备。AI 图片是外观概念，不是工程图纸、认证产品、经测试参数或安全承诺。

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

## 配置与升级

可选的 200 家虚构客户数据，可先运行
`docker compose exec crm python manage.py seed_vorntek_demo` 预览。
显式写入命令与安全边界见[演示数据](docs/DEMO_DATA.md)，不要使用旧清库脚本。

`scripts/configure.py` 创建 `.env` 和 `.secrets/` 中四份独立秘密，拒绝覆盖已有配置。
不要将秘密放入 Git、截图或公开产物。Windows 下应将目录权限限制为本人及容器运行时。
保险库密钥需要与受控恢复材料配套保管，单独数据库备份无法完整恢复。

新安装的 Compose 项目/镜像使用 Vorntek 名称。暂保留 `NEWCROWN_*` 设置、
`newcrown` 数据库/角色标识及 `siteos_demo` 站点代码作为**技术兼容标识**，
不是对外品牌。不要为了换品牌而修改已有 Compose 项目名或清空卷，否则可能选中另一套库。

工业表单类别为 `industrial`。初始化只补充缺少的配置，不会悄悄转换已有表单。
见[替换检查表](docs/VORNTEK_ROLLOUT.md)。不改写已执行的 SQL；不完整历史迁移账本
会被拒绝，不能删除账本记录或运行旧演示重置工具来绕过检查。

## 开发与验证

```sh
python3 -m pip install -r apps/crm/requirements.lock -r requirements-dev.txt
python3 -m unittest discover -s tests -v
node --test scripts/test_measurement.mjs scripts/test_form_status.mjs scripts/test_credential_receipt.mjs scripts/test_vorntek_form.mjs
python3 scripts/run_application_tests.py
```

Node 仅用于 JS 测试。应用运行器使用合成 SQLite 并阻断非回环 Python 网络，
不能代替 PostgreSQL/浏览器验收。另行安装 PostgreSQL 后可执行
`python3 scripts/test_postgres_install.py --pg-bin /path/to/bin --http`。

网站内容由[目录配置](docs/vorntekDemo/catalog.json)和
`scripts/build_vorntek_site.py` 生成。修改脚本/样式后应重新生成 `apps/website/`，
更新基于内容哈希的缓存版本。

## 运维与许可

- [备份恢复](docs/BACKUP_RESTORE.md)：数据库、文件卷与秘密配套恢复；恢复时停止写入方并暂停外部队列。
- [后台任务](docs/BACKGROUND_TASKS.md)：区分导出 worker 与外部集成调度。
- [账号安全](docs/ACCOUNT_SECURITY.md)、[安全政策](SECURITY.md)、
  [贡献说明](CONTRIBUTING.md)、[CI](docs/CI.md)。

`docker compose down -v` 会销毁持久化卷，**不是升级命令**。
发布前仍须完成源码、历史和镜像检查。公开项目不包含真实客户、凭据、原企业照片或私有备份。

项目代码采用 [MIT](LICENSE)，第三方组件保留自己的许可，见[第三方声明](THIRD_PARTY_NOTICES.md)。
AI 提示词与哈希见[演示素材包](docs/vorntekDemo/README.md)，不作为商标已核准或
AI 产物具有排他权利的证明。
