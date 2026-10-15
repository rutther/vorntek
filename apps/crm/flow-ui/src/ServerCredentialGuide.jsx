import React from 'react';
import { GuideIllustration } from './GuideIllustration.jsx';
import './server-credential-guide.css';

// Public, user-approved instruction pictures. Never include a generated token.
const pictures = '/static/console/meta-help/';

export function ServerCredentialGuide({ datasetId, active }) {
  return <details className="external-help server-credential-guide" aria-label="服务器回传凭据获取图解">
    <summary>Token 从哪里来？看图操作</summary>
    <p>Token（访问令牌）是允许网站服务器发送事件的钥匙，不是数据集 ID。</p>
    <ol>
      <li><div className="guide-location-heading"><span>进入 Meta 事件管理工具。</span>
        <GuideIllustration src={`${pictures}meta-open-events-manager-20261007.svg`} title="进入事件管理工具" active={active}
          alt="广告追踪区域的 Pixel 提示中，前往事件管理工具按钮的位置。"
          caption="如果广告页出现这条提示，可点击“前往事件管理工具”；没有提示也可直接打开事件管理工具，不需要制造报错。" />
      </div></li>
      <li>选择本流程数据集：<strong>{datasetId || '先在第 01 步登记数字 ID'}</strong>。确认编号相同，再打开「设置」。</li>
      <li><div className="guide-location-heading"><span>找到「设置直接集成」，生成访问口令。</span>
        <GuideIllustration src={`${pictures}meta-generate-access-token-20261007.svg`} title="生成访问口令" active={active}
          alt="设置页中，设置直接集成、选中不使用 Dataset Quality API 进行设置、生成访问口令三个位置用红框标明。"
          caption="本次直接回传方案：选择“不使用 Dataset Quality API 进行设置”，再点“生成访问口令”。正确入口是“设置”，不是“历史记录”。" />
      </div></li>
      <li>复制口令，在下方「打开本数据集接入管理」进入的页面填写 Access Token，保存后检查连接。已有凭据时不必重复生成。</li>
    </ol>
    <p>口令只填在接入管理页，不要贴到聊天、截图或本步记录中。保存口令不等于启用发送或测试通过。</p>
  </details>;
}
