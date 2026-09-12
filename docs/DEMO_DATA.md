# Fictional customer data / 虚构客户数据

Optional, **isolated demo only**. Install normally first. This command does not
reset schemas, migrate, create users/passwords, change integrations, submit forms,
create marketing receipts or send messages. It preserves existing inquiries.

```sh
# Preview only, no database reads or writes:
docker compose exec crm python manage.py seed_vorntek_demo
# Explicitly acknowledge the isolated fictional-data target and apply:
docker compose exec crm python manage.py seed_vorntek_demo --apply --synthetic-only
```

The write requires PostgreSQL, exactly one initialized site named `Vorntek` with
code `siteos_demo`, a default sales team, no existing companies anywhere in this
database, external I/O/live WhatsApp off, and no enabled integrations. A short
transaction locks the company table, with a five-second lock timeout. Any failure
rolls back all inserted rows. As usual, PostgreSQL sequence values can advance
after a rollback; IDs need not start at one or be contiguous. Repeating the command
refuses a nonempty customer database; it never replaces, duplicates or clears it.

The result is 200 clearly named synthetic companies with 200 pool states, 200
reserved `example.invalid` email addresses and 200 source labels. There are seven
industrial categories and five source types (manual, research, website form,
Meta native form, other), 40 examples per source; 150 available, 40 pending review
and 10 archived. No account owns these rows initially; use normal role/account and
customer assignment workflows to explore the CRM.

All contacts are `do_not_contact/restricted`. Source metadata explicitly says
synthetic and declared, with no real platform IDs, attribution claims or consent.
Automatic-source labels illustrate UI filtering, **not** actual Meta/webhook
receipts. Use the separate HTTP tests for real application ingestion verification.

## 中文

这是可选的独立演示初始化，不是生产导入或恢复工具。先按 README 正常安装，
第一条命令只预览；确认目标仅用于虚构数据后，第二条命令才写入。

仅支持 PostgreSQL；必须是唯一 Vorntek 站点、已建立默认团队、客户表为空、
外发及接入全部关闭。重复执行会拒绝，不清库、不覆盖已有客户，不创建固定账号密码，
不改动已有询盘。写入有事务和表锁保护；失败后数据回滚，序列号可能留有空缺。

200 家企业包含七类工业业务和五种数据来源，其中公海可领取 150 家、待核验 40 家、
归档 10 家。联系人全部使用保留的无效域名，并标记禁止联系。网站/Meta 来源只是明确
标注的合成展示数据，不代表实际接收平台线索、客户同意或营销回传成功。

旧 `prepare_candidate_demo.py` 的清库/旧行业/消息初始化代码已经退役。
它只保留少数测试使用的纯函数，直接执行会拒绝。原件保存在维护者私有归档及原项目中，
不是本开源项目的运行依赖。
