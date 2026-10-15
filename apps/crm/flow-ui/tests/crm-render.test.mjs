import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { readFileSync } from "node:fs";
import { JSDOM } from "jsdom";

import { build } from "esbuild";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

function assertReadableGuideSvg(bytes) {
  const document = new JSDOM(bytes.toString('utf8'), {contentType: 'image/svg+xml'}).window.document;
  assert.equal(document.documentElement.localName, 'svg');
  assert.ok(document.querySelector('title')?.textContent.length > 4);
  assert.match(document.querySelector('desc')?.textContent || '', /示例/);
  assert.ok(document.querySelectorAll('rect,path,text').length >= 10);
  const [x,y,width,height] = document.documentElement.getAttribute('viewBox').split(' ').map(Number);
  assert.deepEqual([x,y], [0,0]);
  assert.ok(width >= 800 && height >= 600);
}

const compiled = await build({ entryPoints: [fileURLToPath(new URL("../src/App.jsx", import.meta.url))], bundle: true, write: false,
  platform: "node", format: "cjs", external: ["react", "react-dom", "react-dom/server"], loader: { ".css": "empty", ".png": "dataurl", ".svg": "dataurl" }, logLevel: "silent" });
const module = { exports: {} };
new Function("require", "module", "exports", compiled.outputFiles[0].text)(createRequire(import.meta.url), module, module.exports);
const { App } = module.exports;
globalThis.localStorage = { getItem() { throw Error("CRM must not read localStorage"); }, setItem() { throw Error("CRM must not write localStorage"); } };

const loop = { id: "crm-test", name: "香港演练流程", code: "NC-TEST", environment: "test", provider: "meta", preset: "website", configs: {}, drafts: {},
  filterEnabled: false, positions: {}, revision: 1, version: 1, check: null, history: [], archived: false, updatedAt: "2026-09-26T00:00:00Z", sources: [], taskKeys: ["dataset", "website", "pixel", "form", "crm", "server", "ad"] };
const render = (hash, loops = [], legacyLoops = []) => {
  globalThis.location = { hash };
  return renderToStaticMarkup(React.createElement(App, { crm: true, initialData: { loops, legacyLoops }, api: {} }));
};

test("empty CRM renders no prototype seeds or duplicate account/navigation shell", () => {
  const html = render("#/loops");
  assert.match(html, /创建第一条流程/);
  assert.doesNotMatch(html, /global-sidebar|small-avatar|非洲灌装线询盘|交互原型/);
  const doc = new JSDOM(html).window.document;
  assert.equal(doc.querySelector('.global-header'), null, 'the host already supplies the global breadcrumb');
  assert.equal(doc.querySelector('.guide-page-heading h1').textContent, '引导式闭环');
});

test('guide collection and creation use one page heading while keeping return and other surfaces intact',()=>{
  const create=new JSDOM(render('#/new',[loop])).window.document;
  assert.equal(create.querySelector('.global-header'),null);
  assert.equal(create.querySelector('.guide-page-heading h1').textContent,'新建闭环');
  assert.ok([...create.querySelectorAll('button')].some(button=>button.textContent==='返回闭环列表'));
  const closed=new JSDOM(render('#/flow/crm-test',[{...loop,guideClosed:true}])).window.document;
  assert.ok(closed.querySelector('.global-header'),'closed guide must retain its list navigation');
  globalThis.location={hash:'#/loops'};
  const plans=new JSDOM(renderToStaticMarkup(React.createElement(App,{crm:true,surface:'plans',initialData:{loops:[loop],legacyLoops:[]},api:{}}))).window.document;
  assert.ok(plans.querySelector('.global-header'),'direct plan surface is outside this visual change');
});

test('guide instance header groups identity and actions without duplicating the workspace context',()=>{
  const doc=new JSDOM(render('#/flow/crm-test?view=tasks',[{...loop,note:'本实例的独立备注'}])).window.document;
  const header=doc.querySelector('header[aria-label="当前闭环实例"]');
  assert.ok(header);
  assert.equal(header.querySelector('select[aria-label="切换流程"]').value,'crm-test');
  assert.match(header.textContent,/香港演练流程|演练测试/);
  assert.ok(header.querySelector('[aria-label="重命名当前流程"]'));
  assert.equal(header.querySelector('details p').textContent,'本实例的独立备注');
  assert.ok(header.querySelector('.guide-config-badge'));
  assert.equal(doc.querySelector('.workflow-tabs nav small'),null,'saved count belongs to instance status, not a second tab badge');
  assert.equal(doc.querySelector('.plan-guide-context'),null,'maintenance actions must live with the instance, not a duplicate bar');
});

test('record-only updates do not advance the configuration revision in the instance heading',()=>{
  for(const version of [8,9]){
    const doc=new JSDOM(render('#/flow/crm-test?view=verification',[{...loop,version,revision:7}])).window.document;
    const header=doc.querySelector('header[aria-label="当前闭环实例"]');
    assert.match(header.textContent,/配置版本 7/);
    assert.doesNotMatch(header.textContent,new RegExp(`配置版本 ${version}`));
  }
});

test("CRM records have internal checks and manual observation, no simulation controls", () => {
  const html = render("#/flow/crm-test?view=verification", [loop]);
  assert.match(html, /检查 CRM 配置/);
  assert.match(html, /保存本次实操记录/);
  assert.match(html, /人工观察/);
  assert.doesNotMatch(html, /运行模拟验证|所有检查通过|服务器回传失败/);
});

test('archived closed guides expose their own persisted history without enabling writes or reopening',()=>{
  const archived={...loop,archived:true,guideClosed:true,history:[{kind:'evidence',nodeKey:'website',result:'pending',note:'ARCHIVED-HISTORY-ONLY: continue after review',at:'2026-10-10T00:00:00Z',revision:1}]};
  const doc=new JSDOM(render('#/flow/crm-test?view=verification',[archived])).window.document;
  const records=doc.querySelector('.crm-records');
  assert.ok(records,'view records must not be intercepted by the closed-guide screen');
  assert.equal(doc.querySelector('.global-header'),null,'the instance workspace already supplies navigation');
  assert.equal(doc.querySelectorAll('[aria-label="当前闭环实例"]').length,1);
  assert.equal([...doc.querySelectorAll('button')].some(button=>button.textContent==='关闭引导'),false,'closed guides must not offer close again');
  assert.match(doc.querySelector('.guide-instance-maintenance').textContent,/引导已关闭/);
  assert.match(records.querySelector('.verification-list').textContent,/ARCHIVED-HISTORY-ONLY/);
  for(const field of records.querySelectorAll('input,textarea,select')) assert.equal(field.disabled,true);
  for(const label of ['保存本次实操记录','检查 CRM 配置']){
    const button=[...records.querySelectorAll('button')].find(item=>item.textContent===label);
    assert.ok(button);
    assert.equal(button.disabled,true);
  }
  assert.equal(archived.guideClosed,true);
  assert.equal(archived.archived,true);
  assert.equal(new JSDOM(render('#/flow/crm-test?view=tasks',[archived])).window.document.querySelector('.crm-records'),null,'other closed-guide routes retain their guard');
});

test('server inspector reports this instance saved event and dedup agreement, never unsaved defaults or receipt',()=>{
  const cases=[
    {configs:{server:{event:'Lead',dedup:true}},drafts:{server:{event:'qualified_lead',dedup:false}},event:'Lead',agreement:'两路使用同一事件编号'},
    {configs:{server:{event:'qualified_lead',dedup:false}},drafts:{},event:'qualified_lead',agreement:'尚未约定两路同一事件编号'},
    {configs:{},drafts:{server:{event:'Lead',dedup:true}},event:'尚未保存',agreement:'尚未保存'},
  ];
  for(const scenario of cases){
    const doc=new JSDOM(render('#/flow/crm-test?view=design&node=server',[{...loop,configs:scenario.configs,drafts:scenario.drafts}])).window.document;
    const inspector=doc.querySelector('.node-inspector');
    const rows=[...inspector.querySelectorAll('dl > div')];
    const values=Object.fromEntries(rows.map(row=>[row.querySelector('dt').textContent,row.querySelector('dd').textContent]));
    assert.equal(values['事件'],scenario.event);
    assert.equal(values['去重约定'],scenario.agreement);
    assert.doesNotMatch(inspector.querySelector('.node-state').textContent,/去重通过|已收到事件|运行中/);
    assert.match(inspector.querySelector('.inspector-protocol>p').textContent,/不代表 Meta 接收或去重通过/);
  }
  const other=new JSDOM(render('#/flow/crm-test?view=design&node=website',[{...loop,configs:{server:{event:'qualified_lead',dedup:true}}}])).window.document;
  assert.doesNotMatch(other.querySelector('.node-inspector').textContent,/qualified_lead|去重约定/,'a different selected node must not inherit the server summary');
});

test('guide canvas has a keyboard reachable scroll region and optional node picker, not a replacement diagram',()=>{
  const doc=new JSDOM(render('#/flow/crm-test?view=design&node=server',[loop])).window.document;
  const canvas=doc.querySelector('.flow-canvas');
  assert.equal(canvas.getAttribute('role'),'region');
  assert.equal(canvas.getAttribute('tabindex'),'0');
  assert.ok(canvas.getAttribute('aria-label').includes('连接图'));
  assert.ok(canvas.querySelector('.react-flow'),'the scroll region must contain the real interactive diagram');
  const picker=doc.querySelector('details.guide-node-picker');
  assert.ok(picker);
  assert.equal(picker.open,false);
  assert.equal(picker.querySelectorAll('.mobile-node-list button').length,7);
  assert.ok([...picker.querySelectorAll('button')].some(button=>button.textContent.includes('服务器回传')));
});

test("legacy config links open the inline guide without losing bound-asset protection", () => {
  const html = render("#/flow/crm-test/config/dataset", [{ ...loop, configs: { dataset: { source: "created", externalId: "12345678", alias: "已登记资产" } } }]);
  assert.match(html, /典型操作指引/);
  assert.match(html, /流程全览/);
  assert.doesNotMatch(html, /class="config-navigation"|返回流程画布/);
  assert.equal((html.match(/class="config-form(?: pixel-step-form)?"/g) || []).length, 7);
  assert.match(html, /aria-controls="guide-panel-dataset" aria-expanded="true"/);
  assert.match(html, /readOnly=""/);
  assert.match(html, /已关联数据集的来源和 ID 只读/);
  assert.doesNotMatch(html, /本原型只提供模拟反馈/);
  const breadcrumb = html.match(/<div class="breadcrumb">([\s\S]*?)<\/div>/)[1];
  assert.match(breadcrumb, /香港演练流程/);
  assert.match(breadcrumb, /重命名当前流程/);
});

test("task view contains every step's real form and independent expansion controls", () => {
  const html = render("#/flow/crm-test?view=tasks&node=website", [loop]);
  assert.equal((html.match(/class="config-form(?: pixel-step-form)?"/g) || []).length, 7);
  assert.match(html, /全部展开/);
  assert.match(html, /全部收起/);
  assert.match(html, /aria-controls="guide-panel-website" aria-expanded="true"/);
  assert.match(html, /aria-controls="guide-panel-dataset" aria-expanded="false"/);
  assert.match(html, /保存本步/);
  assert.doesNotMatch(html, /打开配置页|保存并返回画布/);
});

test("the current walkthrough prepends static step zero without changing saved configuration counts or identities", () => {
  const id = "05ae53ef-90fc-4687-a5fe-3ebcac03974b";
  const html = render(`#/flow/${id}?view=tasks&node=dataset`, [{ ...loop, id, configs: { website: { url: "https://example.com" } } }]);
  const start = html.indexOf('id="guide-toggle-meta-start"');
  assert.ok(start >= 0 && start < html.indexOf('id="guide-toggle-dataset"'), "step zero must precede the existing dataset step");
  assert.match(html, /aria-controls="guide-panel-meta-start" aria-expanded="true"/);
  const intro = html.split('id="guide-panel-meta-start"')[1].split('id="guide-toggle-dataset"')[0];
  assert.doesNotMatch(intro, /<form|<input|<select|保存本步|保存并继续|暂存草稿/);
  assert.match(html, /配置已保存 1 \/ 7 项/);
  assert.equal((html.match(/class="config-form(?: pixel-step-form)?"/g) || []).length, 7);
  const datasetHeader = html.split('id="guide-toggle-dataset"')[1].split('</button>')[0];
  assert.match(datasetHeader, /task-number[^>]*>01</);
});

test("website preset shares step zero across instances but not other presets or the canvas", () => {
  const html = render("#/flow/crm-test?view=tasks", [loop]);
  assert.match(html, /id="guide-toggle-meta-start"/);
  assert.doesNotMatch(render("#/flow/crm-test?view=tasks", [{ ...loop, preset: "instant_form" }]), /id="guide-toggle-meta-start"/);
  const id = "05ae53ef-90fc-4687-a5fe-3ebcac03974b";
  const canvas = render(`#/flow/${id}?view=design`, [{ ...loop, id }]);
  assert.doesNotMatch(canvas, /id="guide-toggle-meta-start"/);
});

test("step zero separates Meta fields, actions, selected values and stop instructions without fake controls", () => {
  const id = "05ae53ef-90fc-4687-a5fe-3ebcac03974b";
  const html = render(`#/flow/${id}?view=tasks`, [{ ...loop, id }]);
  const intro = new JSDOM(html).window.document.getElementById('guide-panel-meta-start').innerHTML;
  assert.equal((intro.match(/class="meta-start-setting"/g) || []).length, 7);
  assert.match(intro, /<ol class="meta-start-settings" aria-label="广告系列操作顺序"/);
  assert.match(intro, /<ol class="meta-start-settings" aria-label="广告组操作顺序"/);
  assert.match(intro, /meta-start-field">购买类型<\/div>[\s\S]*?<strong class="meta-start-choice">竞拍/);
  assert.doesNotMatch(intro, /meta-start-action|保持/);
  assert.doesNotMatch(intro, /meta-start-columns|<dl|<dt|<dd/);
  assert.equal((intro.match(/class="meta-start-choice"/g) || []).length, 4);
  for (const value of ["竞拍", "潜在客户", "网站", "潜在客户人数最大化"]) {
    assert.ok(intro.includes(`class="meta-start-choice">${value}</strong>`));
  }
  assert.match(intro, /<strong>不要点击「立即应用」<\/strong>/);
  assert.match(intro, /<b>现在不要点击「发布」<\/b>/);
  assert.doesNotMatch(intro, /<input|<select|已完成/);
  assert.match(intro, /aria-label="广告系列设置图示"/);
  assert.match(intro, /popover="manual" role="dialog"/);
});


test("legacy records retain links to their actual CRM detail pages", () => {
  const html = render("#/loops", [], [{ id: "old", name: "此前已登记流程", url: "/admin/marketing/guided-loop/meta/website-form/old/" }]);
  assert.match(html, /href="\/admin\/marketing\/guided-loop\/meta\/website-form\/old\/"/);
  assert.match(html, /此前已登记流程/);
});

test("ad-set guide follows dataset, retains seven real forms and shows recommendations not completion", () => {
  const id = "05ae53ef-90fc-4687-a5fe-3ebcac03974b";
  const html = render(`#/flow/${id}?view=tasks`, [{ ...loop, id, configs: { dataset: { externalId: "12345678", source: "created" } } }]);
  assert.ok(html.indexOf('id="guide-toggle-dataset"') < html.indexOf('id="guide-toggle-meta-adset"'));
  assert.ok(html.indexOf('id="guide-toggle-meta-adset"') < html.indexOf('id="guide-toggle-website"'));
  assert.match(html, /aria-controls="guide-panel-meta-adset" aria-expanded="true"/);
  const panel = html.split('id="guide-panel-meta-adset"')[1].split('id="guide-toggle-website"')[0];
  for (const text of ["成为潜在客户（Lead）", "标准", "无 · 暂不填写", "暂不应用", "不启用", "点击 7 天 · 互动 1 天 · 浏览 1 天", "12345678", "本页没有读取 Meta 的实时事件接收状态"]) assert.ok(panel.includes(text), text);
  assert.equal((panel.match(/class="meta-start-setting"/g) || []).length, 6);
  assert.doesNotMatch(panel, /<form|<input|<select|已验证通过|保存本步|保存并继续/);
  assert.match(html, /配置已保存 1 \/ 7 项/);
  assert.equal((html.match(/class="config-form(?: pixel-step-form)?"/g) || []).length, 7);
  assert.match(html.split('id="guide-toggle-website"')[1].split('</button>')[0], /task-number[^>]*>03</);
});

test("ad-set guide never takes a draft ID as a saved dataset or leaks a different instance's ID", () => {
  const id = "05ae53ef-90fc-4687-a5fe-3ebcac03974b";
  const html = render(`#/flow/${id}?view=tasks&node=meta-adset`, [{ ...loop, id, drafts: { dataset: { externalId: "55555555" } } }]);
  const panel = html.split('id="guide-panel-meta-adset"')[1].split('id="guide-toggle-website"')[0];
  assert.match(panel, /尚未登记/);
  assert.doesNotMatch(panel, /55555555/);
  assert.match(html, /aria-controls="guide-panel-meta-adset" aria-expanded="true"/);
  assert.doesNotMatch(render("#/flow/crm-test?view=tasks", [{ ...loop, provider: "other" }]), /id="guide-toggle-meta-adset"/);
  const other = render("#/flow/crm-test?view=tasks", [loop]);
  assert.match(other, /id="guide-toggle-meta-adset"/);
  assert.doesNotMatch(other, /900000000000008|55555555/);
});

test("recommendations place accessible help beside values and contain no account-specific instructions", () => {
  const html = render("#/flow/crm-test?view=tasks&node=meta-adset", [loop]);
  const panel = html.split('id="guide-panel-meta-adset"')[1].split('id="guide-toggle-website"')[0];
  assert.doesNotMatch(new JSDOM(html).window.document.getElementById('guide-panel-meta-adset').textContent, /meta-start-action|保持|FBreels|本轮|本次|你当前页面/);
  assert.match(panel, /meta-start-answer"><strong class="meta-start-choice">无 · 暂不填写<\/strong><button[^>]*aria-expanded="false"[^>]*aria-controls="[^"]+"[^>]*>[\s\S]*?这和广告预算有什么区别？<\/button><\/div>/);
  assert.equal((panel.match(/class="meta-setting-help-toggle"/g) || []).length, 5);
  assert.equal((panel.match(/class="meta-setting-help-content" hidden=""/g) || []).length, 5);
});

test("website step uses Meta destination labels and a contextual screenshot without adding config fields", () => {
  const html = render("#/flow/crm-test?view=tasks&node=website", [loop]);
  const panel = html.split('id="guide-panel-website"')[1].split('id="guide-toggle-pixel"')[0];
  assert.ok(html.includes("设置目标位置"));
  for (const text of ["Meta 广告管理工具 → 广告层级 → 目标位置", "显示链接", "浏览器附加组件", "网址", "对照 Meta 的目标位置"]) assert.ok(panel.includes(text), text);
  assert.ok(!panel.includes("实际落地页网址"));
  assert.ok(!panel.includes("① 广告系列名称"));
  assert.equal((panel.match(/<input/g) || []).length, 1);
  assert.ok(panel.includes('class="guide-reference-figure"'));
  assert.ok(panel.includes('aria-label="查看原图：对照 Meta 的目标位置"'));
  assert.ok(panel.includes('alt="Meta 目标位置示意图：网站、示例网址与浏览器附加组件选项。"'));
  assert.equal((html.match(/class="config-form(?: pixel-step-form)?"/g) || []).length, 7);
});

test("unconfigured conversion warning links setup to browser and server steps, not a second installation", () => {
  const html = render("#/flow/crm-test?view=tasks&node=meta-adset", [loop]);
  const panel = html.split('id="guide-panel-meta-adset"')[1].split('id="guide-toggle-website"')[0];
  assert.match(panel, /aria-label="尚未设置转化事件图示"/);
  assert.match(panel, /aria-label="选择追踪方式图示"/);
  assert.match(panel, /以标准事件追踪转化/);
  assert.match(panel, /查看 04 浏览器事件接入/);
  assert.match(panel, /查看 07 服务器回传/);
  assert.doesNotMatch(panel, /<form|<input|<select|已验证通过/);
  assert.equal((html.match(/class="config-form(?: pixel-step-form)?"/g) || []).length, 7);
});

test("dataset create/reuse walkthrough is scoped to Meta website and leaves bound identity and form count intact", () => {
  const configured = { ...loop, configs: { dataset: { source: "created", externalId: "12345678" } } };
  const html = render("#/flow/crm-test?view=tasks&node=dataset", [configured]);
  const panel = html.split('id="guide-panel-dataset"')[1].split('id="guide-toggle-meta-adset"')[0];
  assert.match(panel, /aria-label="数据集创建与复用指引"/);
  assert.match(panel, /aria-label="创建 Pixel 像素代码图示"/);
  assert.match(panel, /aria-label="完成 Pixel 像素代码设置图示"/);
  assert.match(panel, /readOnly=""[^>]*value="12345678"/);
  assert.equal((html.match(/class="config-form(?: pixel-step-form)?"/g) || []).length, 7);
  for (const other of [{ ...configured, provider: "other" }, { ...configured, preset: "instant_form" }]) {
    assert.doesNotMatch(render("#/flow/crm-test?view=tasks&node=dataset", [other]), /aria-label="数据集创建与复用指引"/);
  }
});

test("first conversion setup is discoverable before troubleshooting and carries pictures without extra forms", () => {
  const html = render("#/flow/crm-test?view=tasks&node=meta-adset", [{ ...loop, filterEnabled: true, taskKeys: ["dataset", "website", "pixel", "form", "crm", "filter", "server", "ad"] }]);
  const panel = html.split('id="guide-panel-meta-adset"')[1].split('id="guide-toggle-website"')[0];
  assert.match(panel, /aria-label="首次设置转化事件操作顺序"/);
  assert.match(panel, /aria-label="Lead 安装说明图示"/);
  assert.match(panel, /查看 08 服务器回传/);
  assert.match(panel, /查看 10 闭环测试/);
  assert.doesNotMatch(panel, /<form|<input|<select/);
});

test("pixel setup places managed configuration and a single right footer within step04", () => {
  const panelFor = current => render("#/flow/crm-test?view=tasks&node=pixel", [current]).split('id="guide-panel-pixel"')[1].split('id="guide-toggle-form"')[0];
  const panel = panelFor(loop);
  assert.match(panel, /aria-label="配置网站 Pixel 接入"/);
  assert.match(panel, /当前数据集/);
  assert.equal((panel.match(/<footer/g)||[]).length,1);
  assert.match(panel,/保存并继续/);
  assert.doesNotMatch(panelFor({ ...loop, provider: "other" }), /添加事件代码图示|查看更多事件/);
  assert.doesNotMatch(panel,/保存接入范围/);
});

test("closed-loop evidence leads back to the ad-set check without saving or publishing", () => {
  const html = render("#/flow/crm-test?view=tasks&node=closed-loop-test", [loop]);
  const panel = html.split('id="guide-panel-closed-loop-test"')[1];
  assert.match(panel, /回到 02 复核转化事件/);
  assert.doesNotMatch(panel, /<form|<input|<select/);
});

test("closed-loop preparation exposes its own readable entry picture without a submit action", () => {
  const html = render("#/flow/crm-test?view=tasks&node=closed-loop-test", [loop]);
  const dom = new JSDOM(html);
  const panel = dom.window.document.getElementById("guide-panel-closed-loop-test");
  const row = [...panel.querySelectorAll(".meta-start-setting")].find(item =>
    item.querySelector(".meta-start-field")?.textContent === "准备观察");
  const trigger = row.querySelector(".guide-illustration-trigger");
  assert.ok(trigger, "test preparation must offer the entry picture where the user needs it");
  assert.equal(trigger.type, "button");
  const picture = dom.window.document.getElementById(trigger.getAttribute("aria-controls"));
  assert.equal(picture.getAttribute("role"), "dialog");
  const image = picture.querySelector("img");
  assert.ok(image?.alt.includes("前往事件管理工具"));
  const src = image.getAttribute("src");
  assert.match(src, /^\/static\/console\/meta-help\/[a-z0-9-]+\.svg$/);
  assert.equal(picture.querySelector("a").getAttribute("href"), src);
  const bytes = readFileSync(new URL("../../console/static/" + src.slice("/static/".length), import.meta.url));
  assertReadableGuideSvg(bytes);
  assert.equal(picture.querySelector("form,input,textarea,select"), null);
  dom.window.close();
});

test("browser test pictures explain starting and observing before submitting an inquiry", () => {
  const dom = new JSDOM(render("#/flow/crm-test?view=tasks&node=closed-loop-test", [loop]));
  const panel = dom.window.document.getElementById("guide-panel-closed-loop-test");
  const rows = [...panel.querySelectorAll(".meta-start-setting")];
  const labels = rows.map(row => row.querySelector(".meta-start-field")?.textContent);
  for (const [label, file, title] of [
    ["打开网站测试", "meta-test-website-events-20261009.svg", "打开网站测试图示"],
    ["先看浏览事件", "meta-test-pageview-received-20261009.svg", "查看浏览事件结果图示"],
  ]) {
    const row = rows[labels.indexOf(label)];
    assert.ok(row, `${label} must be a visible instruction row`);
    assert.ok(labels.indexOf(label) < labels.indexOf("提交测试询盘"));
    const trigger = row.querySelector(".guide-illustration-trigger");
    assert.equal(trigger?.getAttribute("aria-label"), title);
    const picture = dom.window.document.getElementById(trigger.getAttribute("aria-controls"));
    const src = `/static/console/meta-help/${file}`;
    assert.equal(picture.querySelector("img").getAttribute("src"), src);
    assert.equal(picture.querySelector("a").getAttribute("href"), src);
    assert.ok(picture.querySelector("img").alt.length > 20);
    const bytes = readFileSync(new URL("../../console/static/" + src.slice("/static/".length), import.meta.url));
    assertReadableGuideSvg(bytes);
  }
  assert.match(panel.textContent, /PageView/);
  assert.match(panel.textContent, /不代表.*Lead.*服务器回传.*去重/);
  assert.match(panel.textContent, /ERR_BLOCKED_BY_CLIENT/);
  assert.match(panel.textContent, /不必关闭全部扩展/);
  assert.equal(panel.querySelector("form,input,textarea,select"), null);
  dom.window.close();
});

test("destination input stays neutral when empty and preserves a saved or draft URL", () => {
  for (const [configs, drafts, expected] of [
    [{}, {}, ""],
    [{ website: { url: "https://vorntek.com/contact" } }, {}, "https://vorntek.com/contact"],
    [{ website: { url: "https://vorntek.com/contact" } }, { website: { url: "https://vorntek.com/contact?lang=en" } }, "https://vorntek.com/contact?lang=en"],
  ]) {
    const html = render("#/flow/crm-test?view=tasks&node=website", [{ ...loop, configs, drafts }]);
    const panel = html.split('id="guide-panel-website"')[1].split('id="guide-toggle-pixel"')[0];
    const input = panel.match(/<input\b[^>]*>/)[0];
    assert.ok(input.includes(`value="${expected}"`));
    const placeholder = input.match(/placeholder="([^"]*)"/)?.[1] || "";
    assert.doesNotMatch(placeholder, /https?:\/\/|\b[a-z0-9-]+\.(com|org|net)\b/);
  }
});

test("closed-loop test follows ad config, deep-links open it and never pretends to run a test", () => {
  const html = render("#/flow/crm-test?view=tasks&node=closed-loop-test", [loop]);
  assert.ok(html.indexOf('id="guide-toggle-closed-loop-test"') > html.indexOf('id="guide-toggle-ad"'));
  assert.match(html, /aria-controls="guide-panel-closed-loop-test" aria-expanded="true"/);
  const panel = html.split('id="guide-panel-closed-loop-test"')[1];
  for (const text of ["闭环测试", "尚未登记", "TEST / DO NOT CONTACT", "事件编号", "重复", "记录 CRM 接收结果", "记录 Meta 接收与去重结果", "不会自动提交询盘"]) assert.ok(html.includes(text), text);
  assert.doesNotMatch(panel, /<input|<select|开始自动测试|测试已通过/);
  assert.equal((html.match(/class="config-form(?: pixel-step-form)?"/g) || []).length, 7);
  assert.match(html, /配置已保存/);
  assert.doesNotMatch(render("#/flow/crm-test?view=tasks", [{ ...loop, preset: "other" }]), /id="guide-toggle-closed-loop-test"/);
});

test("CRM production entry fails closed when bootstrap is absent or malformed", async () => {
  const built = await build({ entryPoints: [fileURLToPath(new URL("../src/main.jsx", import.meta.url))], bundle: true, write: false,
    platform: "node", format: "cjs", external: ["react", "react-dom", "react-dom/client"], loader: { ".css": "empty", ".png": "dataurl", ".svg": "dataurl" },
    define: { __FLOW_CRM_BUILD__: "true" }, logLevel: "silent" });
  const originalDocument = globalThis.document;
  const require = createRequire(import.meta.url);
  try {
    for (const bootstrap of [null, { textContent: "{invalid" }]) {
      let tree;
      globalThis.document = { getElementById: (id) => id === "flow-bootstrap" ? bootstrap : {} };
      const module = { exports: {} };
      new Function("require", "module", "exports", built.outputFiles[0].text)(
        (name) => name === "react-dom/client" ? { createRoot: () => ({ render: (element) => { tree = element; } }) } : require(name), module, module.exports);
      const html = renderToStaticMarkup(tree);
      assert.match(html, /无法读取 CRM 工作区设置/);
      assert.doesNotMatch(html, /非洲灌装线询盘|交互原型|创建第一条流程/);
    }
  } finally { globalThis.document = originalDocument; }
});

test("server credential picture notes are scoped to the Meta website step and never accept secrets", () => {
  const saved = {...loop, configs: {dataset: {externalId: '12345678'}}};
  const html = render('#/flow/crm-test?view=tasks&node=server', [saved]);
  const panel = html.split('id="guide-panel-server"')[1].split('id="guide-toggle-ad"')[0];
  assert.match(panel, /aria-label="服务器回传凭据获取图解"/);
  assert.match(panel, /aria-label="进入事件管理工具图示"/);
  assert.match(panel, /aria-label="生成访问口令图示"/);
  const notes = panel.split('aria-label="服务器回传凭据获取图解"')[1].split('</details>')[0];
  assert.match(notes, /12345678/);
  assert.match(notes, /meta-open-events-manager-20261007\.svg/);
  assert.match(notes, /meta-generate-access-token-20261007\.svg/);
  assert.doesNotMatch(notes, /<input|<select|<textarea/);
  assert.equal((html.match(/class="config-form(?: pixel-step-form)?"/g) || []).length, 7);
  for (const other of [{...saved, provider:'other'}, {...saved, preset:'instant_form'}]) {
    assert.doesNotMatch(render('#/flow/crm-test?view=tasks&node=server', [other]), /aria-label="服务器回传凭据获取图解"/);
  }
});
