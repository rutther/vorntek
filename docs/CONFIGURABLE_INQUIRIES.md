# Configurable website inquiries / 可配置网站询盘

This describes the current integration candidate, not an accepted deployment.

The public website uses the saved form definition for enabled fields, required
flags, labels, placeholders, input types and select choices. It does not require
company, country, an industry selector or a project parameter unless configured.
At least one enabled contact route (email or phone) must be filled, and contact
and privacy acknowledgement remain required.

在后台「营销 → 获客表单」修改启用、必填和显示名称，保存后刷新官网即可生效。
已被提交的表单代码是公开接口身份，保存名称或字段不应改变原地址。新建表单时
应使用独立代码；不能以改名代替迁移现有页面引用。

## CRM continuation / 后续处理

线索列表、详情、旧版编辑入口和通知采用保存的字段标签与选项文字，
未启用的行业字段不作为默认需求。原始提交保持保留，不因关闭字段而删数据。

资格判断仍要求人工记录依据；已合格咨询可以按同一表单的必填规则转商机。
企业和姓名未设为必填时，不强制补填，也不编造企业或人名；联系人可显示已知
邮箱或电话。有企业信息则照常关联企业，旧版未配置表单保留原身份校验。
个人商机的关联仍受站点、团队、负责人及主要联系人边界约束。

转换会保留来源及字段快照。已安排的同负责人、同团队、同时间且尚无CRM关联的
开放任务会接续关联，不再重复创建下一步；已完成或其他对象的任务不被改写。
商机默认字段为需求范围、项目参数和补充要求；从可配置表单取得的字段标签继续
沿用，其他自定义需求在来源资料中可见。外部平台是否收到事件仍须另行核验。

## Public document contract

Generated pages declare `data-configured-form` and `data-form-code` on the form,
with a `data-inquiry-fields` container and dedicated status, submit and load-retry
controls. `inquiry-form.js` obtains the real definition from
`/admin/api/leads/forms/<code>/definition/` and submits to the corresponding
`submit/` endpoint. Those requests use the real backend, validation and storage.
There are no hard-coded success responses or browser-only saved records.

The existing `form_schema.fields` contract controls custom `select`, `number`,
`date` and text fields as well as standard contact fields. Disabled fields are
not rendered or submitted. Standard fields are sent at the payload root; custom
fields are sent under `extra_fields`. Definitions which cannot be loaded or are
invalid do not unlock a guessed fallback form. Retry loading is explicit.

默认页面不再使用七类工业业务作为强制字段。旧的、没有配置标记的已生成文档仍有
兼容控制器，不会在升级时被误绑定两次；这不代表整站旧工业内容已经清洗完毕。

## Submission, consent and recovery

- Submitting locks only that form. Different forms on the same page remain
  independent. Failed capture retains values and the same operation identity;
  changed field values produce a new identity.
- Required rules and at least one contact route are checked in both browser and
  backend. The backend remains authoritative for validation and persistence.
- Marketing attribution is included only with explicit marketing consent. The
  signed measurement context and authoritative server receipt are passed through;
  a browser success message is not evidence of external Meta acceptance/dedup.
- A duplicate capture is shown as already received without creating another
  record. A retry receipt may still authorize the canonical browser event; the
  measurement loader validates that receipt before any delivery.
- The real source website release includes this controller as an immutable,
  verified file. Packaging tests read the staged controller bytes, not just an
  incremented asset count.

## Observed candidate evidence

Synthetic loopback UI operation used the actual admin editor and public endpoints
for (1) ordinary consultation without company/country or industry requirements,
then (2) email plus a required operator-labelled reference field with name/company/
country/phone disabled. Saved configuration survived a local service restart;
both captures persisted and the duplicate first request remained one row.
No external email or platform events were sent. This does not replace final
PostgreSQL, domain/HTTPS or deployment acceptance.
