(function () {
    const configNode = document.getElementById('sales-config');
    const workspace = document.getElementById('sales-workspace');
    const root = document.getElementById('sales-root');
    const statusNode = document.getElementById('sales-status');
    if (!configNode || !workspace || !root || !statusNode) return;

    const config = JSON.parse(configNode.textContent || '{}');
    const csrfToken = document.querySelector('#csrf-template input[name="csrfmiddlewaretoken"]')?.value || '';
    const drawer = document.getElementById('detailDrawer');
    const drawerMask = document.getElementById('detailMask');
    const drawerTitle = document.getElementById('detailTitle');
    const drawerSubtitle = document.getElementById('detailSubtitle');
    const drawerBody = document.getElementById('detailList');
    const drawerActions = document.getElementById('detailActions');
    const toastNode = document.getElementById('toast');
    const dialog = document.getElementById('sales-dialog');
    const dialogTitle = document.getElementById('sales-dialog-title');
    const dialogEyebrow = document.getElementById('sales-dialog-eyebrow');
    const dialogBody = document.getElementById('sales-dialog-body');
    const dialogSubmit = document.getElementById('sales-dialog-submit');
    const state = {
        view: config.activeView || 'workbench',
        collectionPage: 1,
        latestDetail: null,
        pipelineMode: new URLSearchParams(window.location.search).get('mode') === 'list' ? 'list' : 'board',
        pipelineFilters: {},
        workbenchFilter: 'all',
        workbenchQuery: '',
    };
    const initialParams = new URLSearchParams(window.location.search);
    const todayLabel = document.getElementById('sales-today-label');
    if (todayLabel) {
        todayLabel.textContent = new Intl.DateTimeFormat('zh-CN', {
            year: 'numeric', month: 'long', day: 'numeric', weekday: 'short'
        }).format(new Date());
    }

    const LABELS = {
        full_name: '姓名', name: '名称', company: '企业', country: '国家/地区', city: '城市',
        website: '网站', industry: '行业', email: '邮箱', phone: '电话', whatsapp_phone: 'WhatsApp',
        job_title: '职位', preferred_language: '首选语言', owner: '负责人', status: '状态', stage: '销售阶段',
        source_channel: '来源渠道', source_detail: '来源详情', message: '原始需求', notes: '备注',
        contact_count: '联系人', opportunity_count: '项目数', contact: '联系人', product_scope: '产品范围',
        capacity_target: '产能目标', value_amount: '预计金额', currency: '币种', probability: '成交概率',
        next_step: '下一步', next_follow_up_at: '下次跟进', expected_close_date: '预计成交日期',
        submitted_at: '提交时间', updated_at: '更新时间', task_type: '任务类型', priority: '优先级',
        due_at: '到期时间', reminder_at: '提醒时间', completed_at: '完成时间', outcome: '结果',
        description: '说明', won_reason: '成交原因', lost_reason: '丢单原因'
    };

    const STAGE_LABELS = {
        qualification: '资格确认', discovery: '需求澄清', solution: '方案设计', quotation: '报价',
        negotiation: '商务谈判', on_hold: '暂缓', won: '已成交', lost: '已丢单',
        new: '新提交', contacted: '已联系', qualified: '高质量线索', spam: '垃圾线索'
    };

    const STATUS_LABELS = {
        prospect: '潜在客户', customer: '客户', inactive: '停用', active: '有效',
        open: '待处理', in_progress: '进行中', completed: '已完成', canceled: '已取消', cancelled: '已取消'
    };

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = String(text);
        return node;
    }

    function icon(name) {
        const node = el('i');
        node.dataset.lucide = name;
        node.setAttribute('aria-hidden', 'true');
        return node;
    }

    function refreshIcons() {
        window.lucide?.createIcons({attrs: {'stroke-width': 1.8}});
    }

    function clear(node) {
        while (node?.firstChild) node.removeChild(node.firstChild);
    }

    function value(value) {
        if (value === null || value === undefined || value === '') return '未填写';
        if (typeof value === 'object') return value.name || JSON.stringify(value);
        return String(value);
    }

    function formatDate(input) {
        if (!input) return '未安排';
        const date = new Date(input);
        if (Number.isNaN(date.getTime())) return value(input);
        return new Intl.DateTimeFormat('zh-CN', {
            year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false
        }).format(date);
    }

    function formatMoney(amount, currency) {
        if (amount === null || amount === undefined || amount === '') return '未填写';
        const number = Number(amount);
        if (Number.isNaN(number)) return `${amount} ${currency || ''}`.trim();
        return `${currency || 'USD'} ${new Intl.NumberFormat('en-US', {maximumFractionDigits: 0}).format(number)}`;
    }

    function labelFor(key, raw) {
        if (key === 'stage') return STAGE_LABELS[raw] || value(raw);
        if (key === 'status') return STATUS_LABELS[raw] || value(raw);
        if (key === 'expected_close_date') {
            return raw ? String(raw).slice(0, 10) : '未安排';
        }
        if (['due_at', 'reminder_at', 'completed_at', 'next_follow_up_at', 'submitted_at', 'updated_at'].includes(key)) {
            return formatDate(raw);
        }
        if (key === 'probability' && raw !== null && raw !== undefined) return `${raw}%`;
        return value(raw);
    }

    function badge(text, tone) {
        return el('span', `sales-badge${tone ? ` ${tone}` : ''}`, text);
    }

    function urgencyTone(urgency) {
        if (urgency === '已逾期') return 'is-danger';
        if (urgency === '今天到期') return 'is-warning';
        if (urgency === '未安排') return 'is-neutral';
        return '';
    }

    function showStatus(message, tone) {
        statusNode.className = `sales-status${tone === 'error' ? ' is-error' : ''}`;
        clear(statusNode);
        if (tone !== 'error') statusNode.append(el('span', 'sales-spinner'));
        statusNode.append(el('span', '', message));
        workspace.setAttribute('aria-busy', tone === 'error' ? 'false' : 'true');
    }

    function hideStatus() {
        statusNode.className = 'sales-status is-ready';
        workspace.setAttribute('aria-busy', 'false');
        refreshIcons();
    }

    function toast(message) {
        if (!toastNode) return;
        toastNode.textContent = message;
        toastNode.classList.add('show');
        clearTimeout(window.__salesToastTimer);
        window.__salesToastTimer = setTimeout(() => toastNode.classList.remove('show'), 2200);
    }

    async function requestJson(url, options) {
        const response = await fetch(url, {
            credentials: 'same-origin',
            headers: {
                Accept: 'application/json',
                ...(options?.body instanceof FormData ? {} : {'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8'}),
                ...(options?.headers || {}),
            },
            ...options,
        });
        const payload = await response.json().catch(() => ({ok: false, errors: {__all__: ['服务器返回了无法识别的响应。']}}));
        if (!response.ok || payload.ok === false) {
            const error = new Error(flattenErrors(payload.errors) || `请求失败 (${response.status})`);
            error.payload = payload;
            throw error;
        }
        return payload;
    }

    function flattenErrors(errors) {
        if (!errors) return '';
        return Object.values(errors).flat().join(' ');
    }

    function endpoint(template, replacements) {
        let url = template;
        Object.entries(replacements || {}).forEach(([key, replacement]) => {
            url = url.replace(key, String(replacement));
        });
        return url;
    }

    function panel(title, description) {
        const shell = el('section', 'sales-panel');
        const head = el('header', 'sales-panel-head');
        const copy = el('div');
        copy.append(el('h2', '', title));
        if (description) copy.append(el('p', '', description));
        const actions = el('div', 'sales-panel-actions');
        head.append(copy, actions);
        shell.append(head);
        return {shell, head, actions};
    }

    async function loadView() {
        clear(root);
        showStatus('正在读取当前工作区...');
        try {
            if (state.view === 'workbench') await loadWorkbench();
            else if (state.view === 'pipeline') {
                await loadPipeline({
                    q: initialParams.get('q') || '',
                    stage: initialParams.get('stage') || '',
                    owner: initialParams.get('owner') || '',
                    include_closed: ['1', 'true'].includes(initialParams.get('include_closed')),
                });
            } else {
                const requestedPage = Number(initialParams.get('page') || 1);
                await loadCollection(state.view, {
                    q: initialParams.get('q') || '',
                    status: initialParams.get('status') || '',
                    page: Number.isFinite(requestedPage) && requestedPage > 0 ? requestedPage : 1,
                });
            }
            hideStatus();
        } catch (error) {
            showStatus(error.message || '读取失败，请稍后重试。', 'error');
        }
    }

    async function loadWorkbench() {
        const data = await requestJson(`${config.urls.workbench}?locale=${encodeURIComponent(config.locale)}`);
        const summary = el('section', 'sales-summary-strip');
        const metrics = [
            ['all', '全部待办', data.action_queue.length, 'rows-3'],
            ['new', '新线索', data.counts.new_leads || 0, 'user-plus'],
            ['overdue', '已逾期', data.counts.overdue || 0, 'clock-alert'],
            ['today', '今天到期', data.counts.today_tasks || 0, 'calendar-check-2'],
            ['week', '七天内', data.counts.due_soon || 0, 'calendar-days'],
            ['unassigned', '未分配', data.counts.unassigned || 0, 'user-round-x'],
        ];
        metrics.forEach(([key, label, count, iconName]) => {
            const button = el('button', `sales-summary-item${state.workbenchFilter === key ? ' is-active' : ''}`);
            button.type = 'button';
            button.append(icon(iconName), el('span', '', label), el('strong', '', count));
            button.addEventListener('click', () => {
                state.workbenchFilter = key;
                renderWorkbenchContent(data);
            });
            summary.append(button);
        });

        clear(root);
        root.append(summary);
        root.append(renderWorkbenchContent(data, true));
        refreshIcons();
    }

    function filteredWorkItems(items) {
        const query = state.workbenchQuery.trim().toLowerCase();
        return items.filter((item) => {
            const matchesQuery = !query || [item.title, item.company, item.country, item.project, item.next_action, item.owner]
                .filter(Boolean).join(' ').toLowerCase().includes(query);
            if (!matchesQuery) return false;
            if (state.workbenchFilter === 'overdue') return item.urgency === '已逾期';
            if (state.workbenchFilter === 'today') return item.urgency === '今天到期';
            if (state.workbenchFilter === 'week') return ['三天内', '即将到期'].includes(item.urgency);
            if (state.workbenchFilter === 'new') return item.kind === 'lead';
            if (state.workbenchFilter === 'unassigned') return item.owner === '未分配';
            return true;
        });
    }

    function renderWorkbenchContent(data, initial) {
        const existing = root.querySelector('.sales-workbench-grid');
        const layout = el('div', 'sales-workbench-grid');
        const queuePanel = panel('我的待办', '先处理逾期和新线索，再推进今天到期的项目动作。');
        queuePanel.shell.classList.add('sales-queue-panel');

        const queueTools = el('div', 'sales-queue-tools');
        const search = document.createElement('input');
        search.type = 'search';
        search.value = state.workbenchQuery;
        search.placeholder = '搜索联系人、公司或项目';
        search.setAttribute('aria-label', '搜索当前待办');
        search.addEventListener('input', () => {
            state.workbenchQuery = search.value;
            renderWorkbenchContent(data);
        });
        const clearButton = el('button', 'icon-button');
        clearButton.type = 'button';
        clearButton.title = '清空筛选';
        clearButton.setAttribute('aria-label', '清空筛选');
        clearButton.append(icon('rotate-ccw'));
        clearButton.addEventListener('click', () => {
            state.workbenchFilter = 'all';
            state.workbenchQuery = '';
            renderWorkbenchContent(data);
        });
        queueTools.append(search, clearButton);
        queuePanel.actions.append(queueTools);

        const queue = el('div', 'sales-queue');
        const visibleItems = filteredWorkItems(data.action_queue);
        if (!visibleItems.length) {
            queue.append(el('div', 'sales-empty', '当前筛选下没有待处理事项。'));
        } else {
            const groups = [
                ['已逾期', visibleItems.filter((item) => item.urgency === '已逾期')],
                ['今天到期', visibleItems.filter((item) => item.urgency === '今天到期')],
                ['接下来七天', visibleItems.filter((item) => !['已逾期', '今天到期'].includes(item.urgency))],
            ];
            groups.filter(([, items]) => items.length).forEach(([label, items]) => {
                const group = el('section', 'sales-queue-group');
                const heading = el('header', 'sales-queue-group-head');
                heading.append(el('strong', '', label), el('span', '', `${items.length} 项`));
                group.append(heading);
                items.forEach((item) => group.append(renderQueueItem(item)));
                queue.append(group);
            });
        }
        queuePanel.shell.append(queue);

        const side = el('aside', 'sales-side-panel');
        const sideHead = el('header', 'sales-side-head');
        sideHead.append(icon('sparkles'), el('div', '', null));
        sideHead.lastChild.append(el('h2', '', '工作提示'), el('p', '', '系统根据响应时限和跟进日期自动排序。'));
        side.append(sideHead);

        const focusList = el('div', 'sales-focus-list');
        [
            ['clock-alert', '逾期待处理', `${data.counts.overdue || 0} 项`, 'is-danger'],
            ['user-round-x', '等待分配', `${data.counts.unassigned || 0} 条线索`, ''],
            ['calendar-days', '七天内动作', `${data.counts.due_soon || 0} 项`, ''],
        ].forEach(([iconName, label, valueText, tone]) => {
            const row = el('div', `sales-focus-row ${tone}`.trim());
            row.append(icon(iconName), el('span', '', label), el('strong', '', valueText));
            focusList.append(row);
        });
        side.append(focusList);

        const exception = el('section', 'sales-health-section');
        exception.append(el('h3', '', '数据与回传'));
        if (!data.show_system_exceptions || !data.system_exceptions.length) {
            const healthy = el('div', 'sales-health-ok');
            healthy.append(icon('circle-check'), el('span', '', '没有发现阻断销售跟进的问题'));
            exception.append(healthy);
        } else {
            data.system_exceptions.forEach((item) => {
                const row = el('div', 'sales-exception-item');
                row.append(el('span', '', item.label), badge(item.count, 'is-danger'));
                exception.append(row);
            });
        }
        const links = el('div', 'sales-side-links');
        const leadsLink = el('a', '', '查看线索收件箱');
        leadsLink.href = config.urls.leads;
        const marketingLink = el('a', '', '检查营销回传');
        marketingLink.href = config.urls.marketing;
        links.append(leadsLink, marketingLink);
        exception.append(links);
        side.append(exception);

        layout.append(queuePanel.shell, side);
        if (existing && !initial) existing.replaceWith(layout);
        refreshIcons();
        return layout;
    }

    function renderQueueItem(item) {
        const row = el('article', 'sales-queue-item');
        row.tabIndex = 0;
        const typeLabels = {lead: '新线索', opportunity: '销售机会', task: '任务'};
        const typeIcons = {lead: 'user-plus', opportunity: 'badge-dollar-sign', task: 'check-square-2'};

        const identity = el('div', 'sales-queue-person');
        const titleLine = el('div', 'sales-queue-title-line');
        titleLine.append(icon(typeIcons[item.kind] || 'circle'), el('strong', '', item.title));
        identity.append(titleLine, el('span', '', [item.company, item.country].filter(Boolean).join(' · ') || '未关联企业'));

        const need = el('div', 'sales-queue-need');
        need.append(el('strong', '', item.project || '未填写项目范围'), el('span', '', item.next_action || '更新跟进记录'));

        const owner = el('div', 'sales-queue-owner');
        owner.append(el('span', 'sales-owner-avatar', (item.owner || '?').slice(0, 1).toUpperCase()), el('span', '', item.owner || '未分配'));

        const due = el('div', 'sales-queue-due');
        due.append(badge(item.urgency, urgencyTone(item.urgency)), el('span', '', formatDate(item.due_at)));

        const action = el('button', 'icon-button sales-row-open');
        action.type = 'button';
        action.title = item.kind === 'task' ? '查看任务' : `打开${typeLabels[item.kind] || '记录'}`;
        action.setAttribute('aria-label', action.title);
        action.append(icon('arrow-up-right'));
        const open = () => {
            if (item.kind === 'task') {
                window.location.href = '?view=tasks';
                return;
            }
            openRecord(item.kind, item.entity_id);
        };
        action.addEventListener('click', (event) => { event.stopPropagation(); open(); });
        row.addEventListener('click', open);
        row.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') open(); });
        row.append(identity, need, owner, due, action);
        return row;
    }

    async function loadPipeline(filters) {
        filters = {
            q: filters?.q || '',
            stage: filters?.stage || '',
            owner: filters?.owner || '',
            include_closed: Boolean(filters?.include_closed),
        };
        state.pipelineFilters = filters;
        const params = new URLSearchParams({locale: config.locale});
        Object.entries(filters || {}).forEach(([key, item]) => {
            if (item !== '' && item !== false) params.set(key, item === true ? '1' : item);
        });
        const [data, savedViewData] = await Promise.all([
            requestJson(`${config.urls.pipeline}?${params}`),
            requestJson(`${config.urls.savedView}?locale=${encodeURIComponent(config.locale)}&scope=opportunities`)
                .catch(() => ({saved_views: []})),
        ]);
        updatePipelineUrl(filters);
        const pipelinePanel = panel('销售机会', `${data.count} 个项目，金额按当前阶段概率计算。`);
        pipelinePanel.actions.append(renderPipelineModeControl());
        const toolbar = renderPipelineToolbar(filters, savedViewData.saved_views || [], () => loadPipelineFromToolbar());
        pipelinePanel.shell.append(renderPipelineSummary(data.rows), toolbar);
        if (state.pipelineMode === 'list') {
            if (config.canBulkAssign) pipelinePanel.shell.append(renderPipelineBulkBar());
            pipelinePanel.shell.append(renderPipelineList(data.rows));
        } else {
            const scroll = el('div', 'sales-pipeline-scroll');
            const board = el('div', 'sales-pipeline-board');
            data.stages.forEach((stage) => board.append(renderPipelineColumn(stage)));
            scroll.append(board);
            pipelinePanel.shell.append(scroll);
        }
        clear(root);
        root.append(pipelinePanel.shell);
        hideStatus();
    }

    function renderPipelineSummary(rows) {
        const activeRows = rows.filter((item) => !['won', 'lost'].includes(item.stage));
        const valueTotals = new Map();
        const weightedTotals = new Map();
        activeRows.forEach((item) => {
            const currency = item.currency || 'USD';
            const amount = Number(item.value_amount || 0);
            valueTotals.set(currency, (valueTotals.get(currency) || 0) + amount);
            weightedTotals.set(currency, (weightedTotals.get(currency) || 0) + amount * Number(item.probability || 0) / 100);
        });
        const moneySummary = (totals) => [...totals.entries()]
            .map(([currency, amount]) => formatMoney(amount, currency))
            .join(' · ') || 'USD 0';
        const averageProbability = activeRows.length
            ? Math.round(activeRows.reduce((sum, item) => sum + Number(item.probability || 0), 0) / activeRows.length)
            : 0;
        const now = Date.now();
        const overdue = activeRows.filter((item) => item.next_follow_up_at && new Date(item.next_follow_up_at).getTime() < now).length;
        const unassigned = activeRows.filter((item) => !item.owner || item.owner === '未分配').length;

        const summary = el('section', 'sales-pipeline-summary');
        [
            ['briefcase-business', '进行中', activeRows.length, ''],
            ['circle-dollar-sign', '机会总额', moneySummary(valueTotals), ''],
            ['chart-no-axes-combined', '加权金额', moneySummary(weightedTotals), ''],
            ['percent', '平均概率', `${averageProbability}%`, ''],
            ['clock-alert', '跟进逾期', overdue, overdue ? 'is-danger' : ''],
            ['user-round-x', '未分配', unassigned, unassigned ? 'is-warning' : ''],
        ].forEach(([iconName, label, metric, tone]) => {
            const item = el('div', `sales-pipeline-summary-item ${tone}`.trim());
            item.append(icon(iconName));
            const copy = el('span');
            copy.append(el('small', '', label), el('strong', '', metric));
            item.append(copy);
            summary.append(item);
        });
        return summary;
    }

    function updatePipelineUrl(filters) {
        const params = new URLSearchParams({view: 'pipeline'});
        if (state.pipelineMode === 'list') params.set('mode', 'list');
        Object.entries(filters).forEach(([key, item]) => {
            if (item !== '' && item !== false) params.set(key, item === true ? '1' : item);
        });
        history.replaceState(null, '', `${location.pathname}?${params}`);
    }

    function renderPipelineModeControl() {
        const control = el('div', 'sales-segmented');
        [['board', '看板'], ['list', '表格']].forEach(([mode, label]) => {
            const button = el('button', mode === state.pipelineMode ? 'is-active' : '', label);
            button.type = 'button';
            button.setAttribute('aria-pressed', String(mode === state.pipelineMode));
            button.addEventListener('click', () => {
                if (state.pipelineMode === mode) return;
                state.pipelineMode = mode;
                loadPipeline(state.pipelineFilters);
            });
            control.append(button);
        });
        return control;
    }

    function renderPipelineToolbar(filters, savedViews, submit) {
        const toolbar = el('form', 'sales-toolbar');
        const search = el('label');
        search.append(el('span', '', '搜索项目、企业、联系人或产品范围'));
        const input = document.createElement('input');
        input.type = 'search'; input.name = 'q'; input.value = filters.q || ''; input.placeholder = '例如 can line、Saudi Arabia、36,000 BPH';
        search.append(input);
        const closed = el('label');
        closed.append(el('span', '', '项目范围'));
        const select = document.createElement('select');
        select.name = 'include_closed';
        [['0', '进行中的项目'], ['1', '包含成交与丢单']].forEach(([raw, text]) => {
            const option = el('option', '', text); option.value = raw; option.selected = Boolean(filters.include_closed) === (raw === '1'); select.append(option);
        });
        closed.append(select);
        const stage = el('label'); stage.append(el('span', '', '销售阶段'));
        const stageSelect = document.createElement('select'); stageSelect.name = 'stage';
        [['', '全部阶段'], ...Object.entries(STAGE_LABELS).filter(([key]) => ['qualification','discovery','solution','quotation','negotiation','on_hold','won','lost'].includes(key))]
            .forEach(([raw, text]) => { const option = el('option', '', text); option.value = raw; option.selected = raw === filters.stage; stageSelect.append(option); });
        stage.append(stageSelect);
        const owner = el('label'); owner.append(el('span', '', '负责人'));
        const ownerSelect = document.createElement('select'); ownerSelect.name = 'owner';
        [['', '全部负责人'], ...(config.assignmentUsers || []).map((item) => [String(item.id), item.label])]
            .forEach(([raw, text]) => { const option = el('option', '', text); option.value = raw; option.selected = raw === String(filters.owner || ''); ownerSelect.append(option); });
        owner.append(ownerSelect);
        const saved = el('label'); saved.append(el('span', '', '保存的视图'));
        const savedSelect = document.createElement('select'); savedSelect.name = 'saved_view';
        const emptySaved = el('option', '', savedViews.length ? '选择一个已保存视图' : '暂无保存视图'); emptySaved.value = ''; savedSelect.append(emptySaved);
        savedViews.forEach((item) => { const option = el('option', '', `${item.name}${item.is_shared ? ' · 团队' : ''}`); option.value = String(item.id); option.dataset.filters = JSON.stringify(item.filters || {}); savedSelect.append(option); });
        savedSelect.addEventListener('change', () => {
            const option = savedSelect.selectedOptions[0];
            if (!option?.dataset.filters) return;
            loadPipeline(JSON.parse(option.dataset.filters));
        });
        saved.append(savedSelect);
        const actions = el('div', 'sales-toolbar-actions');
        const apply = el('button', 'button primary', '应用'); apply.type = 'submit';
        const reset = el('button', 'button neutral', '清空'); reset.type = 'button';
        reset.addEventListener('click', () => loadPipeline({}));
        const save = el('button', 'button neutral', '保存筛选'); save.type = 'button';
        save.addEventListener('click', () => openSavePipelineViewDialog(filters));
        actions.append(apply, reset, save);
        toolbar.append(search, closed, stage, owner, saved, actions);
        toolbar.addEventListener('submit', (event) => { event.preventDefault(); submit(toolbar); });
        return toolbar;
    }

    function loadPipelineFromToolbar() {
        const form = root.querySelector('.sales-toolbar');
        loadPipeline({
            q: form?.q?.value || '',
            stage: form?.stage?.value || '',
            owner: form?.owner?.value || '',
            include_closed: form?.include_closed?.value === '1',
        });
    }

    function openSavePipelineViewDialog(filters) {
        openDialogForm('保存筛选', '保存当前销售管道视图', [
            inputField('name', '视图名称', 'text', {required: true, wide: true}),
            inputField('scope', '', 'hidden', {value: 'opportunities'}),
            inputField('filters_json', '', 'hidden', {value: JSON.stringify(filters)}),
            inputField('columns_json', '', 'hidden', {value: '[]'}),
            inputField('sort_json', '', 'hidden', {value: '[]'}),
        ], async (data) => {
            data.append('locale', config.locale);
            await requestJson(config.urls.savedView, {method: 'POST', body: data, headers: {'X-CSRFToken': csrfToken}});
            await loadPipeline(filters);
        });
    }

    function renderPipelineBulkBar() {
        const bar = el('div', 'sales-bulkbar');
        bar.append(el('span', '', '勾选项目后，可统一分配给团队成员。'));
        const select = document.createElement('select'); select.id = 'pipeline-bulk-owner';
        const placeholder = el('option', '', '选择负责人'); placeholder.value = ''; select.append(placeholder);
        (config.assignmentUsers || []).forEach((item) => { const option = el('option', '', item.label); option.value = item.id; select.append(option); });
        const button = el('button', 'button primary', '批量分配'); button.type = 'button';
        button.addEventListener('click', bulkAssignOpportunities);
        bar.append(select, button);
        return bar;
    }

    function renderPipelineList(items) {
        const wrap = el('div', 'sales-pipeline-list');
        if (!items.length) { wrap.append(el('div', 'sales-empty', '当前筛选条件下没有项目。')); return wrap; }
        const table = el('table', 'sales-record-table sales-opportunity-table');
        const thead = document.createElement('thead'); const head = document.createElement('tr');
        if (config.canBulkAssign) {
            const check = document.createElement('input'); check.type = 'checkbox'; check.setAttribute('aria-label', '选择全部项目');
            check.addEventListener('change', () => document.querySelectorAll('.sales-opportunity-check').forEach((item) => { item.checked = check.checked; }));
            const cell = document.createElement('th'); cell.append(check); head.append(cell);
        }
        ['项目', '企业', '阶段', '预计金额', '概率', '负责人', '下次跟进', '操作'].forEach((label) => head.append(el('th', '', label)));
        thead.append(head);
        const tbody = document.createElement('tbody');
        items.forEach((item) => {
            const row = document.createElement('tr');
            if (config.canBulkAssign) { const cell = document.createElement('td'); const check = document.createElement('input'); check.type = 'checkbox'; check.value = item.id; check.className = 'sales-opportunity-check'; check.setAttribute('aria-label', `选择项目 ${item.name}`); cell.append(check); row.append(cell); }
            const title = document.createElement('td'); title.append(el('strong', '', item.name)); row.append(title);
            [item.company || '未关联', STAGE_LABELS[item.stage] || item.stage, formatMoney(item.value_amount, item.currency), `${item.probability || 0}%`, item.owner || '未分配', formatDate(item.next_follow_up_at)]
                .forEach((content) => row.append(el('td', '', content)));
            const actionCell = document.createElement('td'); const action = el('button', 'button neutral', '查看'); action.type = 'button'; action.addEventListener('click', () => openRecord('opportunity', item.id)); actionCell.append(action); row.append(actionCell);
            tbody.append(row);
        });
        table.append(thead, tbody);
        const mobile = el('div', 'sales-record-mobile');
        items.forEach((item) => mobile.append(renderOpportunityMobileCard(item)));
        wrap.append(table, mobile);
        return wrap;
    }

    function renderOpportunityMobileCard(item) {
        const card = el('article', 'sales-card sales-opportunity-card');
        const head = el('div', 'sales-opportunity-card-head');
        if (config.canBulkAssign) { const check = document.createElement('input'); check.type = 'checkbox'; check.value = item.id; check.className = 'sales-opportunity-check'; check.setAttribute('aria-label', `选择项目 ${item.name}`); head.append(check); }
        const copy = el('div', 'sales-record-title'); copy.append(el('strong', '', item.name), el('span', '', [item.company, item.country].filter(Boolean).join(' · '))); head.append(copy, badge(STAGE_LABELS[item.stage] || item.stage, 'is-neutral'));
        const facts = document.createElement('dl'); facts.className = 'sales-card-facts';
        [['预计金额', formatMoney(item.value_amount, item.currency)], ['概率', `${item.probability || 0}%`], ['负责人', item.owner || '未分配'], ['下次跟进', formatDate(item.next_follow_up_at)]].forEach(([term, content]) => { const fact = el('div'); fact.append(el('dt', '', term), el('dd', '', content)); facts.append(fact); });
        const actions = el('div', 'sales-card-actions'); const action = el('button', 'button neutral', '查看项目'); action.type = 'button'; action.addEventListener('click', () => openRecord('opportunity', item.id)); actions.append(action);
        card.append(head, facts, actions); return card;
    }

    async function bulkAssignOpportunities() {
        const owner = document.getElementById('pipeline-bulk-owner')?.value || '';
        const ids = [...new Set([...document.querySelectorAll('.sales-opportunity-check:checked')].map((item) => item.value))];
        if (!ids.length || !owner) { toast('请先选择项目和负责人'); return; }
        const data = new FormData(); data.append('record_type', 'opportunities'); data.append('assignee_id', owner); data.append('locale', config.locale); ids.forEach((id) => data.append('record_ids', id));
        try {
            const result = await requestJson(config.urls.bulkAssign, {method: 'POST', body: data, headers: {'X-CSRFToken': csrfToken}});
            toast(`已分配 ${result.updated || ids.length} 个项目`);
            await loadPipeline(state.pipelineFilters);
        } catch (error) { toast(error.message); }
    }

    function renderPipelineColumn(stage) {
        const column = el('details', 'sales-pipeline-column');
        column.dataset.stage = stage.key;
        column.open = stage.count > 0 || window.matchMedia('(min-width: 721px)').matches;
        const summary = document.createElement('summary');
        const totals = (stage.value_by_currency || [])
            .map((item) => formatMoney(item.amount, item.currency))
            .join(' · ');
        const summaryCopy = el('span', 'sales-pipeline-column-title');
        summaryCopy.append(el('strong', '', stage.label), badge(stage.count, 'is-neutral'));
        summary.append(summaryCopy, el('span', 'sales-pipeline-column-total', totals || '暂无预计金额'));
        const items = el('div', 'sales-pipeline-items');
        if (!stage.items.length) items.append(el('div', 'sales-empty', '暂无项目'));
        stage.items.forEach((item) => {
            const card = el('button', 'sales-pipeline-card'); card.type = 'button';
            const cardHead = el('span', 'sales-pipeline-card-head');
            cardHead.append(el('strong', '', item.name), el('b', '', `${item.probability || 0}%`));
            card.append(cardHead);
            card.append(el('span', 'sales-pipeline-company', [item.company, item.country].filter(Boolean).join(' · ') || '未关联企业'));
            card.append(el('span', 'sales-pipeline-scope', item.product_scope || '未填写产品范围'));
            if (item.capacity_target) card.append(el('span', 'sales-pipeline-capacity', item.capacity_target));
            const valueLine = el('span', 'sales-pipeline-value');
            valueLine.append(el('small', '', '预计金额'), el('strong', '', formatMoney(item.value_amount, item.currency)));
            card.append(valueLine);
            const meta = el('span', 'sales-pipeline-card-meta');
            const owner = el('span', 'sales-pipeline-card-owner');
            owner.append(el('i', 'sales-owner-avatar', (item.owner || '?').slice(0, 1).toUpperCase()), el('span', '', item.owner || '未分配'));
            const followUp = el('span', 'sales-pipeline-card-followup');
            followUp.append(icon('calendar-clock'), el('span', '', formatDate(item.next_follow_up_at)));
            meta.append(owner, followUp);
            card.append(meta);
            card.addEventListener('click', () => openRecord('opportunity', item.id));
            items.append(card);
        });
        column.append(summary, items);
        return column;
    }

    async function loadCollection(collection, filters) {
        state.collectionPage = Number(filters?.page || state.collectionPage || 1);
        const params = new URLSearchParams({locale: config.locale, page: String(state.collectionPage), page_size: '50'});
        if (filters?.q) params.set('q', filters.q);
        if (filters?.status) params.set('status', filters.status);
        const data = await requestJson(`${endpoint(config.urls.collection, {'__collection__': collection})}?${params}`);
        const titles = {companies: '企业台账', contacts: '联系人台账', tasks: '任务清单'};
        const descriptions = {
            companies: '沉淀客户组织、国家、行业和关联项目。',
            contacts: '联系人与企业、沟通方式和负责人的统一记录。',
            tasks: '按到期时间集中查看和完成销售任务。'
        };
        const collectionPanel = panel(titles[collection], `${descriptions[collection]} 当前 ${data.count} 条。`);
        const toolbar = renderCollectionToolbar(collection, filters || {}, () => loadCollectionFromToolbar(collection));
        collectionPanel.shell.append(toolbar, renderCollectionRecords(collection, data.items));
        collectionPanel.shell.append(renderPagination(data, () => loadCollection(collection, filters)));
        clear(root);
        root.append(collectionPanel.shell);
        hideStatus();
    }

    function renderCollectionToolbar(collection, filters, submit) {
        const toolbar = el('form', 'sales-toolbar');
        const search = el('label'); search.append(el('span', '', '搜索'));
        const input = document.createElement('input'); input.type = 'search'; input.name = 'q'; input.value = filters.q || '';
        input.placeholder = collection === 'tasks' ? '搜索任务或说明' : '搜索名称、企业、国家或联系方式'; search.append(input);
        const status = el('label'); status.append(el('span', '', '状态'));
        const select = document.createElement('select'); select.name = 'status';
        const options = collection === 'companies'
            ? [['', '全部'], ['prospect', '潜在客户'], ['customer', '客户'], ['inactive', '停用']]
            : collection === 'contacts'
                ? [['', '全部'], ['active', '有效'], ['inactive', '停用']]
                : [['', '全部'], ['open', '待处理'], ['in_progress', '进行中'], ['completed', '已完成'], ['canceled', '已取消']];
        options.forEach(([raw, text]) => { const option = el('option', '', text); option.value = raw; option.selected = raw === (filters.status || ''); select.append(option); });
        status.append(select);
        const actions = el('div', 'sales-toolbar-actions');
        const apply = el('button', 'button primary', '应用'); apply.type = 'submit';
        const clearButton = el('button', 'button neutral', '清空'); clearButton.type = 'button';
        clearButton.addEventListener('click', () => { state.collectionPage = 1; loadCollection(collection, {}); });
        actions.append(apply, clearButton);
        toolbar.append(search, status, actions);
        toolbar.addEventListener('submit', (event) => { event.preventDefault(); state.collectionPage = 1; submit(); });
        return toolbar;
    }

    function loadCollectionFromToolbar(collection) {
        const form = root.querySelector('.sales-toolbar');
        loadCollection(collection, {
            q: form?.q?.value || '',
            status: form?.status?.value || '',
            page: state.collectionPage,
        });
    }

    function renderCollectionRecords(collection, items) {
        const wrap = el('div');
        if (!items.length) { wrap.append(el('div', 'sales-empty', '当前筛选条件下没有记录。')); return wrap; }
        const columns = collectionColumns(collection);
        const table = el('table', 'sales-record-table');
        const thead = document.createElement('thead'); const head = document.createElement('tr');
        columns.forEach((column) => head.append(el('th', '', column.label))); head.append(el('th', '', '操作')); thead.append(head);
        const tbody = document.createElement('tbody');
        items.forEach((item) => tbody.append(renderRecordRow(collection, columns, item)));
        table.append(thead, tbody);
        const mobile = el('div', 'sales-record-mobile');
        items.forEach((item) => mobile.append(renderRecordCard(collection, columns, item)));
        wrap.append(table, mobile);
        return wrap;
    }

    function collectionColumns(collection) {
        if (collection === 'companies') return [
            {key: 'name', label: '企业'}, {key: 'country', label: '国家/地区'}, {key: 'industry', label: '行业'},
            {key: 'owner', label: '负责人'}, {key: 'status', label: '状态'}, {key: 'opportunity_count', label: '项目'}
        ];
        if (collection === 'contacts') return [
            {key: 'full_name', label: '联系人'}, {key: 'company', label: '企业'}, {key: 'email', label: '邮箱'},
            {key: 'phone', label: '电话'}, {key: 'owner', label: '负责人'}, {key: 'status', label: '状态'}
        ];
        return [
            {key: 'title', label: '任务'}, {key: 'due_at', label: '到期'}, {key: 'owner', label: '负责人'},
            {key: 'priority', label: '优先级'}, {key: 'status', label: '状态'}
        ];
    }

    function recordField(item, key) {
        if (key === 'company') return item.company?.name || '未关联';
        if (key === 'due_at') return formatDate(item.due_at);
        if (key === 'status') return STATUS_LABELS[item.status] || STAGE_LABELS[item.status] || value(item.status);
        if (key === 'priority') return {urgent: '紧急', high: '高', normal: '普通', low: '低'}[item.priority] || value(item.priority);
        return value(item[key]);
    }

    function renderRecordRow(collection, columns, item) {
        const row = el('tr', 'sales-record-row');
        columns.forEach((column, index) => {
            const cell = document.createElement('td');
            if (index === 0) {
                const title = el('div', 'sales-record-title');
                title.append(el('strong', '', recordField(item, column.key)));
                if (item.description) title.append(el('span', '', item.description));
                cell.append(title);
            } else if (column.key === 'status') cell.append(badge(recordField(item, column.key), item.status === 'completed' || item.status === 'customer' ? 'is-success' : 'is-neutral'));
            else cell.textContent = recordField(item, column.key);
            row.append(cell);
        });
        const actionCell = document.createElement('td'); actionCell.append(recordAction(collection, item)); row.append(actionCell);
        if (collection !== 'tasks') row.addEventListener('click', () => openRecord(collection === 'companies' ? 'company' : 'contact', item.id));
        return row;
    }

    function renderRecordCard(collection, columns, item) {
        const card = el('article', 'sales-card');
        const title = el('div', 'sales-record-title'); title.append(el('strong', '', recordField(item, columns[0].key)));
        if (item.description) title.append(el('span', '', item.description)); card.append(title);
        const facts = document.createElement('dl'); facts.className = 'sales-card-facts';
        columns.slice(1).forEach((column) => { const fact = el('div'); fact.append(el('dt', '', column.label), el('dd', '', recordField(item, column.key))); facts.append(fact); });
        const actions = el('div', 'sales-card-actions'); actions.append(recordAction(collection, item));
        card.append(facts, actions); return card;
    }

    function recordAction(collection, item) {
        const action = el('button', 'button neutral', collection === 'tasks' ? (item.status === 'completed' ? '已完成' : '标记完成') : '查看详情');
        action.type = 'button';
        if (collection === 'tasks' && item.status === 'completed') action.disabled = true;
        action.addEventListener('click', (event) => {
            event.stopPropagation();
            if (collection === 'tasks') completeTask(item.id);
            else openRecord(collection === 'companies' ? 'company' : 'contact', item.id);
        });
        return action;
    }

    function renderPagination(data) {
        const footer = el('footer', 'sales-pagination');
        footer.append(el('span', '', `第 ${data.page} / ${data.pages} 页，共 ${data.count} 条`));
        const actions = el('div', 'sales-card-actions');
        const previous = el('button', 'button neutral', '上一页'); previous.type = 'button'; previous.disabled = data.page <= 1;
        const next = el('button', 'button neutral', '下一页'); next.type = 'button'; next.disabled = data.page >= data.pages;
        previous.addEventListener('click', () => { state.collectionPage = Math.max(1, data.page - 1); loadCollectionFromToolbar(state.view); });
        next.addEventListener('click', () => { state.collectionPage = Math.min(data.pages, data.page + 1); loadCollectionFromToolbar(state.view); });
        actions.append(previous, next); footer.append(actions); return footer;
    }

    async function openRecord(type, id) {
        try {
            const url = endpoint(config.urls.record, {'__type__': type, '/0/': `/${id}/`});
            const data = await requestJson(`${url}?locale=${encodeURIComponent(config.locale)}`);
            state.latestDetail = {type, id};
            renderDrawer(data);
        } catch (error) {
            toast(error.message);
        }
    }

    function renderDrawer(data) {
        if (!drawer || !drawerBody || !drawerActions) return;
        const record = data.record;
        drawerTitle.textContent = record.name || record.full_name || `记录 #${record.id}`;
        drawerSubtitle.textContent = [record.company?.name || record.company, record.country, record.owner].filter(Boolean).join(' · ') || '销售记录详情';
        clear(drawerBody); clear(drawerActions);
        drawerBody.append(renderRecordDetails(record));
        drawerBody.append(renderTimelineSection('最近活动', data.activities, (item) => [item.subject, `${formatDate(item.occurred_at)} · ${item.actor}`, item.body]));
        drawerBody.append(renderTimelineSection('关联任务', data.tasks, (item) => [item.title, `${formatDate(item.due_at)} · ${item.owner}`, STATUS_LABELS[item.status] || item.status], true));
        drawerBody.append(renderAttachmentSection(data.attachments));
        if (record.type === 'opportunity') drawerBody.append(renderStageHistory(data.stage_history));

        const activity = el('button', 'button neutral', '记录沟通'); activity.type = 'button'; activity.addEventListener('click', () => openActivityDialog(record));
        const task = el('button', 'button neutral', '新建任务'); task.type = 'button'; task.addEventListener('click', () => openTaskDialog(record));
        const file = el('button', 'button neutral', '上传附件'); file.type = 'button'; file.addEventListener('click', () => openAttachmentDialog(record));
        drawerActions.append(activity, task, file);
        if (record.type === 'opportunity') {
            const stage = el('button', 'button primary', '推进阶段'); stage.type = 'button'; stage.addEventListener('click', () => openStageDialog(record)); drawerActions.append(stage);
        }
        drawerMask?.classList.add('show'); drawer.classList.add('show'); drawer.setAttribute('aria-hidden', 'false');
        refreshIcons();
    }

    function renderRecordDetails(record) {
        const section = el('section', 'sales-detail-section'); section.append(el('h3', '', '业务资料'));
        const grid = el('div', 'sales-detail-grid');
        Object.entries(record).forEach(([key, raw]) => {
            if (['id', 'type', 'version', 'conversion', 'source_submission_id'].includes(key) || raw === null || raw === '') return;
            const displayValue = key === 'value_amount' ? formatMoney(raw, record.currency) : labelFor(key, raw);
            const item = el('div'); item.append(el('span', '', LABELS[key] || key), el('strong', '', displayValue)); grid.append(item);
        });
        section.append(grid); return section;
    }

    function renderTimelineSection(title, items, formatter, withTaskAction) {
        const section = el('section', 'sales-detail-section'); section.append(el('h3', '', title));
        const timeline = el('div', 'sales-timeline');
        if (!items?.length) timeline.append(el('div', 'sales-empty', '暂无记录'));
        (items || []).forEach((item) => {
            const row = el('article'); const parts = formatter(item).filter(Boolean); row.append(el('strong', '', parts[0])); parts.slice(1).forEach((part) => row.append(el('span', '', part)));
            if (withTaskAction && item.status !== 'completed') {
                const complete = el('button', 'button neutral', '完成任务'); complete.type = 'button'; complete.addEventListener('click', () => completeTask(item.id, true)); row.append(complete);
            }
            timeline.append(row);
        });
        section.append(timeline); return section;
    }

    function renderAttachmentSection(items) {
        const section = el('section', 'sales-detail-section'); section.append(el('h3', '', '附件'));
        const timeline = el('div', 'sales-timeline');
        if (!items?.length) timeline.append(el('div', 'sales-empty', '暂无附件'));
        (items || []).forEach((item) => {
            const row = el('article'); const link = el('a', '', item.title || item.original_name); link.href = item.download_url; row.append(link, el('span', '', `${item.uploaded_by} · ${formatDate(item.created_at)}`)); timeline.append(row);
        });
        section.append(timeline); return section;
    }

    function renderStageHistory(items) {
        return renderTimelineSection('阶段历史', items || [], (item) => [
            `${STAGE_LABELS[item.from_stage] || item.from_stage || '建立'} → ${STAGE_LABELS[item.to_stage] || item.to_stage}`,
            `${formatDate(item.changed_at)} · ${item.changed_by_user__username || '系统'}`,
            item.reason
        ]);
    }

    function closeDrawer() {
        drawerMask?.classList.remove('show'); drawer?.classList.remove('show'); drawer?.setAttribute('aria-hidden', 'true');
    }

    function inputField(name, label, type, options) {
        if (type === 'hidden') {
            const hidden = document.createElement('input');
            hidden.type = 'hidden';
            hidden.name = name;
            hidden.value = options?.value || '';
            return hidden;
        }
        const wrap = el('label', options?.wide ? 'is-wide' : ''); wrap.append(el('span', '', label));
        let input;
        if (type === 'textarea') { input = document.createElement('textarea'); input.rows = options?.rows || 4; }
        else if (type === 'select') {
            input = document.createElement('select');
            (options?.choices || []).forEach(([raw, text]) => { const option = el('option', '', text); option.value = raw; if (raw === options?.value) option.selected = true; input.append(option); });
        } else { input = document.createElement('input'); input.type = type || 'text'; if (options?.value) input.value = options.value; }
        input.name = name; if (options?.required) input.required = true; wrap.append(input); return wrap;
    }

    function openDialogForm(eyebrow, title, fields, submit) {
        clear(dialogBody); dialogEyebrow.textContent = eyebrow; dialogTitle.textContent = title;
        const form = el('div', 'sales-form'); fields.forEach((field) => form.append(field)); dialogBody.append(form);
        dialogSubmit.onclick = async () => {
            const data = new FormData(); form.querySelectorAll('input, select, textarea').forEach((input) => {
                if (input.type === 'file') { if (input.files?.[0]) data.append(input.name, input.files[0]); }
                else data.append(input.name, input.value);
            });
            form.querySelector('.sales-form-error')?.remove();
            try { dialogSubmit.disabled = true; await submit(data); dialog.close(); toast('已保存'); if (state.latestDetail) await openRecord(state.latestDetail.type, state.latestDetail.id); }
            catch (error) { const message = el('div', 'sales-form-error', error.message); form.prepend(message); }
            finally { dialogSubmit.disabled = false; }
        };
        dialog.showModal();
    }

    function targetFields(record, data) {
        data.append('target_type', record.type); data.append('target_id', record.id); data.append('locale', config.locale); return data;
    }

    function localDateValue(date) {
        const pad = (item) => String(item).padStart(2, '0');
        return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
    }

    function openActivityDialog(record) {
        openDialogForm('跟进活动', '记录一次真实沟通', [
            inputField('activity_type', '活动类型', 'select', {choices: [['note','跟进记录'],['call','电话'],['email','邮件'],['whatsapp','WhatsApp'],['meeting','会议'],['site_visit','现场拜访']]}),
            inputField('direction', '沟通方向', 'select', {choices: [['outbound','我方发起'],['inbound','客户发起'],['internal','内部记录']]}),
            inputField('subject', '主题', 'text', {required: true, wide: true}),
            inputField('body', '沟通记录', 'textarea', {required: true, wide: true, rows: 5}),
            inputField('occurred_at', '发生时间', 'datetime-local', {required: true, value: localDateValue(new Date()), wide: true})
        ], async (data) => {
            targetFields(record, data); await requestJson(config.urls.activityCreate, {method: 'POST', body: data, headers: {'X-CSRFToken': csrfToken}});
        });
    }

    function openTaskDialog(record) {
        const tomorrow = new Date(Date.now() + 24 * 60 * 60 * 1000); tomorrow.setMinutes(0, 0, 0);
        openDialogForm('销售任务', '安排下一步行动', [
            inputField('title', '任务', 'text', {required: true, wide: true}),
            inputField('task_type', '类型', 'select', {choices: [['follow_up','跟进'],['call','电话'],['email','邮件'],['whatsapp','WhatsApp'],['meeting','会议'],['quote','报价'],['review','内部评审'],['other','其他']]}),
            inputField('priority', '优先级', 'select', {choices: [['normal','普通'],['high','高'],['urgent','紧急'],['low','低']]}),
            inputField('owner_user', '负责人', 'hidden', {value: String(config.currentUserId)}),
            inputField('due_at', '到期时间', 'datetime-local', {required: true, value: localDateValue(tomorrow)}),
            inputField('reminder_at', '提醒时间', 'datetime-local'),
            inputField('description', '说明', 'textarea', {wide: true, rows: 4})
        ], async (data) => {
            targetFields(record, data); await requestJson(config.urls.taskCreate, {method: 'POST', body: data, headers: {'X-CSRFToken': csrfToken}});
        });
    }

    function openStageDialog(record) {
        openDialogForm('销售管道', '推进项目阶段', [
            inputField('stage', '新阶段', 'select', {wide: true, value: record.stage, choices: Object.entries(STAGE_LABELS).filter(([key]) => ['qualification','discovery','solution','quotation','negotiation','on_hold','won','lost'].includes(key))}),
            inputField('reason', '变更说明', 'textarea', {wide: true, rows: 4})
        ], async (data) => {
            data.append('locale', config.locale);
            data.append('version', record.version || '');
            const url = endpoint(config.urls.opportunityStage, {'/0/': `/${record.id}/`});
            await requestJson(url, {method: 'POST', body: data, headers: {'X-CSRFToken': csrfToken}});
            if (state.view === 'pipeline') await loadPipeline();
        });
    }

    function openAttachmentDialog(record) {
        openDialogForm('项目文件', '上传关联附件', [
            inputField('title', '附件标题', 'text', {wide: true}),
            inputField('file', '选择文件', 'file', {required: true, wide: true})
        ], async (data) => {
            targetFields(record, data); await requestJson(config.urls.attachments, {method: 'POST', body: data, headers: {'X-CSRFToken': csrfToken}});
        });
    }

    async function completeTask(taskId, refreshDrawer) {
        try {
            const data = new FormData(); data.append('outcome', '已在销售工作台完成'); data.append('locale', config.locale);
            const url = endpoint(config.urls.taskComplete, {'/0/': `/${taskId}/`});
            await requestJson(url, {method: 'POST', body: data, headers: {'X-CSRFToken': csrfToken}});
            toast('任务已完成');
            if (refreshDrawer && state.latestDetail) await openRecord(state.latestDetail.type, state.latestDetail.id);
            else await loadCollection('tasks', {});
        } catch (error) { toast(error.message); }
    }

    document.getElementById('closeDetail')?.addEventListener('click', closeDrawer);
    drawerMask?.addEventListener('click', closeDrawer);
    document.addEventListener('keydown', (event) => { if (event.key === 'Escape') closeDrawer(); });
    loadView();
}());
