(() => {
    'use strict';

    const page = document.querySelector('[data-nc-task-page]');
    if (!page && !document.querySelector('[data-nc-task-form]')) return;

    const uuid = () => {
        if (window.crypto && typeof window.crypto.randomUUID === 'function') {
            return window.crypto.randomUUID();
        }
        const bytes = new Uint8Array(16);
        window.crypto.getRandomValues(bytes);
        return Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('');
    };

    const csrf = (form) => {
        const field = form.querySelector('input[name="csrfmiddlewaretoken"]');
        return field ? field.value : '';
    };

    const setStatus = (form, message, tone = 'danger') => {
        const status = form.querySelector('[data-nc-form-status]');
        if (!status) return;
        status.hidden = !message;
        status.className = `alert alert-${tone} mt-3 mb-0`;
        status.textContent = message || '';
    };

    const resetErrors = (form) => {
        form.querySelectorAll('.is-invalid').forEach((field) => field.classList.remove('is-invalid'));
        form.querySelectorAll('[data-error-for]').forEach((node) => { node.textContent = ''; });
        const summary = form.querySelector('[data-nc-form-error]');
        if (summary) {
            summary.hidden = true;
            summary.textContent = '';
        }
        setStatus(form, '');
    };

    const showErrors = (form, payload) => {
        const errors = payload && payload.errors ? payload.errors : {};
        let firstField = null;
        const general = [];
        Object.entries(errors).forEach(([name, messages]) => {
            const text = Array.isArray(messages) ? messages.join(' ') : String(messages || '');
            if (name === '__all__') {
                general.push(text);
                return;
            }
            const field = form.querySelector(`[name="${CSS.escape(name)}"]`);
            const target = form.querySelector(`[data-error-for="${CSS.escape(name)}"]`);
            if (field) {
                field.classList.add('is-invalid');
                if (!firstField && !field.disabled && field.type !== 'hidden') firstField = field;
            }
            if (target) target.textContent = text;
            if (!field && !target) general.push(text);
        });
        if (!Object.keys(errors).length && payload && payload.message) general.push(payload.message);
        const summary = form.querySelector('[data-nc-form-error]');
        if (summary && general.length) {
            summary.textContent = general.join(' ');
            summary.hidden = false;
            summary.tabIndex = -1;
            summary.focus();
        } else if (firstField) {
            firstField.focus();
        } else if (payload && payload.message) {
            setStatus(form, payload.message, 'danger');
        }
    };

    const setBusy = (form, busy) => {
        form.setAttribute('aria-busy', busy ? 'true' : 'false');
        form.querySelectorAll('[data-nc-submit]').forEach((button) => {
            button.disabled = busy;
            const label = button.querySelector('span');
            if (!label) return;
            if (!label.dataset.originalLabel) label.dataset.originalLabel = label.textContent;
            label.textContent = busy ? '处理中…' : label.dataset.originalLabel;
        });
    };

    document.querySelectorAll('[data-nc-task-form]').forEach((form) => {
        const token = form.querySelector('input[name="idempotency_token"]');
        if (token && !token.value) token.value = uuid();
        form.addEventListener('submit', async (event) => {
            event.preventDefault();
            resetErrors(form);
            if (!form.reportValidity()) return;
            setBusy(form, true);
            try {
                const response = await window.fetch(form.action, {
                    method: 'POST',
                    body: new FormData(form),
                    credentials: 'same-origin',
                    headers: {'Accept': 'application/json', 'X-CSRFToken': csrf(form)},
                });
                const payload = await response.json().catch(() => ({ok: false, message: '服务器返回了无法识别的响应。'}));
                if (!response.ok || !payload.ok) {
                    showErrors(form, payload);
                    return;
                }
                setStatus(form, payload.replayed ? '请求已安全处理，正在打开原记录。' : '已保存，正在刷新。', 'success');
                window.location.assign(payload.detail_url || page?.dataset.ncReloadUrl || window.location.href);
            } catch (_error) {
                setStatus(form, '网络状态未知。你的输入已保留，可使用同一请求安全重试。', 'danger');
            } finally {
                setBusy(form, false);
            }
        });
    });

    const bulkForm = document.querySelector('[data-nc-task-bulk-form]');
    if (bulkForm) {
        const rows = Array.from(document.querySelectorAll('[data-nc-task-select]'));
        const selectAll = document.querySelector('[data-nc-task-select-all]');
        const count = bulkForm.querySelector('[data-nc-task-selected-count]');
        const itemsField = bulkForm.querySelector('[name="items_json"]');
        const selectedRows = () => rows.filter((item) => item.checked);
        const syncSelection = () => {
            const selected = selectedRows();
            bulkForm.hidden = selected.length === 0;
            count.textContent = String(selected.length);
            itemsField.value = JSON.stringify(selected.map((item) => ({
                id: Number(item.dataset.taskId),
                version: item.dataset.taskVersion,
            })));
            if (selectAll) {
                selectAll.checked = selected.length === rows.length && rows.length > 0;
                selectAll.indeterminate = selected.length > 0 && selected.length < rows.length;
            }
        };
        rows.forEach((item) => item.addEventListener('change', syncSelection));
        selectAll?.addEventListener('change', () => {
            rows.forEach((item) => { item.checked = selectAll.checked; });
            syncSelection();
        });
        bulkForm.querySelector('[data-nc-task-clear-selection]')?.addEventListener('click', () => {
            rows.forEach((item) => { item.checked = false; });
            syncSelection();
        });
        bulkForm.addEventListener('submit', async (event) => {
            event.preventDefault();
            resetErrors(bulkForm);
            syncSelection();
            if (!bulkForm.reportValidity()) return;
            setBusy(bulkForm, true);
            try {
                const response = await window.fetch(bulkForm.action, {
                    method: 'POST', body: new FormData(bulkForm), credentials: 'same-origin',
                    headers: {'Accept': 'application/json', 'X-CSRFToken': csrf(bulkForm)},
                });
                const payload = await response.json().catch(() => ({ok: false, message: '服务器返回了无法识别的响应。'}));
                if (!response.ok || !payload.ok) {
                    showErrors(bulkForm, payload);
                    return;
                }
                const byId = new Map(rows.map((item) => [Number(item.dataset.taskId), item]));
                payload.results.forEach((result) => {
                    if (result.status === 'succeeded' && byId.has(result.id)) byId.get(result.id).checked = false;
                });
                syncSelection();
                if (payload.partial) {
                    const failed = payload.total - payload.succeeded;
                    setStatus(bulkForm, `已分配 ${payload.succeeded} 条；${failed} 条因版本、状态或团队不匹配而保留，请刷新后核对。`, 'warning');
                } else {
                    setStatus(bulkForm, `已分配 ${payload.succeeded} 条，正在刷新。`, 'success');
                    window.location.assign(page?.dataset.ncReloadUrl || window.location.href);
                }
            } catch (_error) {
                setStatus(bulkForm, '网络状态未知。成功与否未被假定，请刷新任务状态后再处理。', 'danger');
            } finally {
                setBusy(bulkForm, false);
            }
        });
        syncSelection();
    }

    const toggle = document.querySelector('[data-nc-next-task-toggle]');
    const nextPanel = document.querySelector('[data-nc-next-task-panel]');
    if (toggle && nextPanel) {
        const sync = () => {
            const enabled = toggle.checked;
            nextPanel.hidden = !enabled;
            nextPanel.disabled = !enabled;
            nextPanel.querySelectorAll('input, select, textarea').forEach((field) => {
                field.disabled = !enabled;
                if (['next_title', 'next_due_at', 'next_owner_user'].includes(field.name)) field.required = enabled;
            });
            if (enabled) nextPanel.querySelector('input:not([type="hidden"])')?.focus();
        };
        toggle.addEventListener('change', sync);
        sync();
    }

    const createForm = document.querySelector('[data-nc-task-form="create"]');
    if (createForm && page) {
        const search = createForm.querySelector('[data-nc-target-search]');
        const results = createForm.querySelector('[data-nc-target-results]');
        const targetType = createForm.querySelector('[name="target_type"]');
        const targetId = createForm.querySelector('[name="target_id"]');
        const owner = createForm.querySelector('[name="owner_user"]');
        let timer = null;
        let requestNumber = 0;

        const message = (text) => {
            results.replaceChildren();
            const node = document.createElement('p');
            node.className = 'text-secondary mb-0';
            node.textContent = text;
            results.appendChild(node);
        };

        const resetTarget = () => {
            targetId.value = '';
            owner.replaceChildren(new Option('先选择关联对象', ''));
            owner.disabled = true;
        };

        const selectTarget = (item, button) => {
            results.querySelectorAll('[role="option"]').forEach((node) => node.setAttribute('aria-selected', 'false'));
            button.setAttribute('aria-selected', 'true');
            targetId.value = String(item.id);
            owner.replaceChildren();
            item.owners.forEach((entry) => owner.add(new Option(entry.label, String(entry.id))));
            owner.disabled = item.owners.length === 0;
            if (!item.owners.length) {
                owner.add(new Option('当前团队没有可分配成员', ''));
                setStatus(createForm, '该对象所在团队没有可用负责人，暂时不能创建任务。', 'warning');
            } else {
                setStatus(createForm, `已选择 ${item.label} · ${item.team}`, 'info');
            }
        };

        const render = (payload) => {
            results.replaceChildren();
            if (!payload.items || !payload.items.length) {
                message(payload.message || '没有匹配的可访问对象。');
                return;
            }
            payload.items.forEach((item) => {
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'nc-task-target-option';
                button.setAttribute('role', 'option');
                button.setAttribute('aria-selected', 'false');
                const main = document.createElement('span');
                const title = document.createElement('strong');
                const meta = document.createElement('span');
                const team = document.createElement('small');
                title.textContent = item.label;
                meta.textContent = item.meta;
                team.textContent = item.team;
                main.append(title, meta);
                button.append(main, team);
                button.addEventListener('click', () => selectTarget(item, button));
                results.appendChild(button);
            });
            if (payload.message) {
                const hint = document.createElement('p');
                hint.className = 'text-secondary small px-3 py-2 mb-0 border-top';
                hint.textContent = payload.message;
                results.appendChild(hint);
            }
        };

        const searchTargets = async () => {
            const query = search.value.trim().replace(/\s+/g, ' ');
            resetTarget();
            if (query.length < 2) {
                message('请输入至少 2 个字符。');
                return;
            }
            const current = ++requestNumber;
            message('正在搜索…');
            const url = new URL(page.dataset.targetSearchUrl, window.location.origin);
            url.searchParams.set('type', targetType.value);
            url.searchParams.set('q', query);
            try {
                const response = await window.fetch(url, {headers: {'Accept': 'application/json'}, credentials: 'same-origin'});
                const payload = await response.json();
                if (current !== requestNumber) return;
                if (!response.ok || !payload.ok) {
                    message('搜索暂时不可用，请稍后重试。');
                    return;
                }
                render(payload);
            } catch (_error) {
                if (current === requestNumber) message('搜索暂时不可用，请稍后重试。');
            }
        };

        search.addEventListener('input', () => {
            window.clearTimeout(timer);
            timer = window.setTimeout(searchTargets, 280);
        });
        targetType.addEventListener('change', () => {
            search.value = '';
            resetTarget();
            message('输入关键词后选择一个关联对象。');
            search.focus();
        });
    }

    document.querySelectorAll('.modal').forEach((modal) => {
        modal.addEventListener('shown.bs.modal', () => {
            const occurred = modal.querySelector('[data-nc-activity-occurred]');
            if (occurred && !occurred.value) {
                const now = new Date();
                const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000);
                occurred.value = local.toISOString().slice(0, 16);
            }
            const preferred = modal.querySelector('textarea[required], input:not([type="hidden"]):not([disabled]), select:not([disabled])');
            preferred?.focus();
        });
    });

    const autoCreate = document.querySelector('[data-nc-auto-open-create]');
    if (autoCreate) {
        let attempts = 0;
        const openWhenReady = () => {
            attempts += 1;
            const Modal = window.tabler?.Modal || window.tabler?.bootstrap?.Modal || window.bootstrap?.Modal;
            if (Modal) {
                Modal.getOrCreateInstance(autoCreate).show();
                return;
            }
            if (attempts < 20) window.setTimeout(openWhenReady, 50);
        };
        openWhenReady();
    }
})();
