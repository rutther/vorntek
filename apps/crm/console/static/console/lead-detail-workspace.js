(() => {
    const root = document.getElementById('lead-engagement');
    if (!root) return;

    const statusNode = document.getElementById('lead-engagement-status');
    const activityList = document.getElementById('lead-activities-list');
    const taskList = document.getElementById('lead-tasks-list');
    const fileList = document.getElementById('lead-files-list');
    const stageList = document.getElementById('lead-stage-evidence-list');
    const csrfToken = document.querySelector('input[name="csrfmiddlewaretoken"]')?.value || '';
    const leadId = root.dataset.leadId;

    const activityLabels = {
        note: '跟进记录', call: '电话', email: '邮件', whatsapp: 'WhatsApp',
        meeting: '会议', site_visit: '现场拜访', task: '任务', file: '文件', system: '系统记录',
        conversion: '线索转换', stage_change: '阶段变化',
    };
    const taskStatusLabels = {
        open: '待处理',
        in_progress: '进行中',
        completed: '已完成',
        canceled: '已取消',
        // Read-only compatibility for any pre-constraint legacy fixture.
        cancelled: '已取消',
    };
    const stageLabels = {
        qualification: '资格确认', discovery: '需求澄清', solution: '方案设计',
        quotation: '报价', negotiation: '商务谈判', on_hold: '暂缓', won: '已成交', lost: '已丢单',
    };

    function el(tag, className = '', text = '') {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== '') node.textContent = String(text);
        return node;
    }

    function formatDate(value) {
        if (!value) return '未设置';
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return String(value);
        return new Intl.DateTimeFormat('zh-CN', {
            year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
        }).format(date);
    }

    function localDateTime(date) {
        const offset = date.getTimezoneOffset() * 60000;
        return new Date(date.getTime() - offset).toISOString().slice(0, 16);
    }

    function flattenErrors(payload) {
        const errors = payload?.errors || {};
        const messages = Object.values(errors).flat().filter(Boolean);
        return messages.join(' ') || '操作失败，请检查填写内容。';
    }

    async function requestJson(url, options = {}) {
        const response = await fetch(url, {
            credentials: 'same-origin',
            ...options,
            headers: {'Accept': 'application/json', ...(options.headers || {})},
        });
        let payload = {};
        try {
            payload = await response.json();
        } catch (_error) {
            payload = {};
        }
        if (!response.ok || payload.ok === false) throw new Error(flattenErrors(payload));
        return payload;
    }

    function emptyState(text) {
        return el('p', 'lead-engagement-empty', text);
    }

    function renderActivities(items) {
        activityList.replaceChildren();
        if (!items.length) {
            activityList.append(emptyState('尚无沟通记录。'));
            return;
        }
        items.slice(0, 10).forEach((item) => {
            const card = el('article', 'lead-engagement-item');
            const head = el('div', 'lead-engagement-item-head');
            head.append(
                el('strong', '', item.subject || activityLabels[item.activity_type] || '跟进记录'),
                el('span', 'lead-engagement-chip', activityLabels[item.activity_type] || item.activity_type),
            );
            if (item.body) card.append(head, el('p', '', item.body));
            else card.append(head);
            card.append(el('small', '', `${item.actor || '系统'} · ${formatDate(item.occurred_at)}`));
            activityList.append(card);
        });
    }

    function renderTasks(items) {
        taskList.replaceChildren();
        if (!items.length) {
            taskList.append(emptyState('尚无跟进任务。'));
            return;
        }
        items.slice(0, 10).forEach((item) => {
            const card = el('article', 'lead-engagement-item');
            const head = el('div', 'lead-engagement-item-head');
            const label = taskStatusLabels[item.status] || item.status;
            head.append(el('strong', '', item.title), el('span', `lead-engagement-chip is-${item.status}`, label));
            card.append(head);
            if (item.description) card.append(el('p', '', item.description));
            card.append(el('small', '', `${item.owner || '未分配'} · 到期 ${formatDate(item.due_at)}`));
            const link = el('a', 'button neutral compact', '查看并处理');
            link.href = item.detail_url;
            card.append(link);
            taskList.append(card);
        });
    }

    function readableSize(bytes) {
        const value = Number(bytes || 0);
        if (value < 1024) return `${value} B`;
        if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
        return `${(value / 1024 / 1024).toFixed(1)} MB`;
    }

    function renderFiles(items) {
        fileList.replaceChildren();
        if (!items.length) {
            fileList.append(emptyState('尚无项目文件。'));
            return;
        }
        items.slice(0, 10).forEach((item) => {
            const card = el('article', 'lead-engagement-item');
            const link = el('a', 'lead-engagement-file', item.title || item.original_name || '下载附件');
            link.href = item.download_url;
            card.append(link, el('small', '', `${readableSize(item.file_size_bytes)} · ${item.uploaded_by || '系统'} · ${formatDate(item.created_at)}`));
            fileList.append(card);
        });
    }

    function renderStageEvidence(payload) {
        if (!stageList || !payload) return;
        stageList.replaceChildren();
        const record = payload.record || {};
        const summary = el('div', 'lead-stage-summary');
        summary.append(el('span', '', '当前阶段'), el('strong', '', stageLabels[record.stage] || record.stage || '未设置'));
        if (record.won_reason) summary.append(el('span', '', '成交原因'), el('strong', '', record.won_reason));
        if (record.lost_reason) summary.append(el('span', '', '丢单原因'), el('strong', '', record.lost_reason));
        stageList.append(summary);
        const history = payload.stage_history || [];
        if (!history.length) {
            stageList.append(emptyState('尚无阶段变化记录。'));
            return;
        }
        const historyList = el('div', 'lead-stage-history');
        history.slice(0, 10).forEach((item) => {
            const from = item.from_stage ? (stageLabels[item.from_stage] || item.from_stage) : '建立项目';
            const to = stageLabels[item.to_stage] || item.to_stage;
            const row = el('article', 'lead-stage-history-item');
            row.append(
                el('strong', '', `${from} → ${to}`),
                el('p', '', item.reason || '未填写阶段说明'),
                el('small', '', `${item.changed_by_user__username || '系统'} · ${formatDate(item.changed_at)}`),
            );
            historyList.append(row);
        });
        stageList.append(historyList);
    }

    function setStatus(message, isError = false) {
        statusNode.textContent = message;
        statusNode.classList.toggle('is-error', isError);
    }

    async function load() {
        setStatus('正在读取跟进记录...');
        try {
            const [leadPayload, opportunityPayload] = await Promise.all([
                requestJson(root.dataset.detailUrl),
                root.dataset.opportunityDetailUrl ? requestJson(root.dataset.opportunityDetailUrl) : Promise.resolve(null),
            ]);
            renderActivities(leadPayload.activities || []);
            renderTasks(leadPayload.tasks || []);
            renderFiles(leadPayload.attachments || []);
            renderStageEvidence(opportunityPayload);
            setStatus(`已读取 ${leadPayload.activities?.length || 0} 条沟通、${leadPayload.tasks?.length || 0} 项任务和 ${leadPayload.attachments?.length || 0} 个文件。`);
        } catch (error) {
            setStatus(error.message, true);
            activityList.replaceChildren(emptyState('跟进记录读取失败。'));
            taskList.replaceChildren(emptyState('任务读取失败。'));
            fileList.replaceChildren(emptyState('文件读取失败。'));
        }
    }

    const dialogs = {
        activity: document.getElementById('lead-activity-dialog'),
        task: document.getElementById('lead-task-dialog'),
        attachment: document.getElementById('lead-attachment-dialog'),
    };

    document.querySelectorAll('[data-lead-dialog]').forEach((button) => {
        button.addEventListener('click', () => {
            const dialog = dialogs[button.dataset.leadDialog];
            if (!dialog) return;
            if (button.dataset.leadDialog === 'activity') {
                dialog.querySelector('[name="occurred_at"]').value = localDateTime(new Date());
            }
            if (button.dataset.leadDialog === 'task') {
                const due = new Date(Date.now() + 24 * 60 * 60 * 1000);
                dialog.querySelector('[name="due_at"]').value = localDateTime(due);
            }
            dialog.showModal();
        });
    });

    document.querySelectorAll('[data-dialog-close]').forEach((button) => {
        button.addEventListener('click', () => button.closest('dialog')?.close());
    });

    function copyOwnerOptions() {
        const source = document.getElementById('id_assignee');
        const target = document.getElementById('lead-task-owner');
        if (!source || !target) return;
        target.replaceChildren();
        [...source.options].forEach((option) => {
            if (!option.value) return;
            const copy = new Option(option.textContent, option.value, false, option.selected);
            target.add(copy);
        });
        if (!target.value && target.options.length) target.options[0].selected = true;
    }

    async function submitDialog(form, url, {attachment = false} = {}) {
        const errorNode = form.querySelector('[data-dialog-error]');
        const submit = form.querySelector('[type="submit"]');
        errorNode.hidden = true;
        errorNode.textContent = '';
        submit.disabled = true;
        const token = form.querySelector('input[name="idempotency_token"]');
        if (token && !token.value) token.value = window.crypto.randomUUID();
        const body = new FormData(form);
        body.append('target_type', 'lead');
        body.append('target_id', leadId);
        try {
            await requestJson(url, {method: 'POST', headers: {'X-CSRFToken': csrfToken}, body});
            form.closest('dialog').close();
            form.reset();
            if (token) token.value = window.crypto.randomUUID();
            if (!attachment) copyOwnerOptions();
            await load();
        } catch (error) {
            errorNode.textContent = error.message;
            errorNode.hidden = false;
        } finally {
            submit.disabled = false;
        }
    }

    document.getElementById('lead-activity-form')?.addEventListener('submit', (event) => {
        event.preventDefault();
        submitDialog(event.currentTarget, root.dataset.activityUrl);
    });
    document.getElementById('lead-task-form')?.addEventListener('submit', (event) => {
        event.preventDefault();
        submitDialog(event.currentTarget, root.dataset.taskUrl);
    });
    document.getElementById('lead-attachment-form')?.addEventListener('submit', (event) => {
        event.preventDefault();
        submitDialog(event.currentTarget, root.dataset.attachmentUrl, {attachment: true});
    });

    copyOwnerOptions();
    load();
})();
