(function () {
    'use strict';

    const q = (selector, root = document) => root.querySelector(selector);
    const qa = (selector, root = document) => Array.from(root.querySelectorAll(selector));

    function renderIcons(root) {
        if (window.lucide && typeof window.lucide.createIcons === 'function') {
            window.lucide.createIcons({ attrs: { 'stroke-width': 1.8 }, root: root || document });
        }
    }

    function announce(message, tone) {
        const region = q('[data-nc-live]');
        if (!region) return;
        region.textContent = message;
        region.classList.toggle('is-error', tone === 'error');
        region.classList.toggle('is-warning', tone === 'warning');
        region.setAttribute('role', tone === 'error' ? 'alert' : 'status');
        region.setAttribute('aria-live', tone === 'error' ? 'assertive' : 'polite');
        region.classList.add('is-visible');
        window.clearTimeout(announce.timer);
        announce.timer = window.setTimeout(() => region.classList.remove('is-visible'), 5500);
    }

    function uuid() {
        if (window.crypto && typeof window.crypto.randomUUID === 'function') {
            return window.crypto.randomUUID();
        }
        return 'nc-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 14);
    }

    function csrfToken(root) {
        const input = q('input[name="csrfmiddlewaretoken"]', root || document);
        if (input && input.value) return input.value;
        const pair = document.cookie.split('; ').find((item) => item.startsWith('csrftoken='));
        return pair ? decodeURIComponent(pair.slice('csrftoken='.length)) : '';
    }

    function setAsyncControlsBusy(controls, busy, busyControl, busyLabel) {
        Array.from(new Set((controls || []).filter(Boolean))).forEach((control) => {
            if (busy) {
                if (control.dataset.ncAsyncBusy === 'true') return;
                control.dataset.ncAsyncBusy = 'true';
                control.dataset.ncAsyncWasDisabled = String(control.disabled);
                control.disabled = true;
            } else if (control.dataset.ncAsyncBusy === 'true') {
                control.disabled = control.dataset.ncAsyncWasDisabled === 'true';
                delete control.dataset.ncAsyncBusy;
                delete control.dataset.ncAsyncWasDisabled;
            }
        });
        if (!busyControl) return;
        if (busy) {
            busyControl.dataset.ncAsyncIdleLabel = busyControl.textContent;
            if (busyLabel) busyControl.textContent = busyLabel;
        } else if (busyControl.dataset.ncAsyncIdleLabel !== undefined) {
            busyControl.textContent = busyControl.dataset.ncAsyncIdleLabel;
            delete busyControl.dataset.ncAsyncIdleLabel;
        }
        busyControl.setAttribute('aria-busy', String(busy));
    }

    function currentFieldValue(name) {
        const search = q('.nc-search');
        const field = search && search.elements ? search.elements.namedItem(name) : null;
        if (field) return String(field.value || '').trim();
        return String(new URLSearchParams(window.location.search).get(name) || '').trim();
    }

    function currentViewState(allowedFields) {
        const fallback = ['q', 'stage', 'language', 'source', 'owner', 'sla', 'page_size'];
        const fields = allowedFields.length ? allowedFields : fallback;
        const filters = {};
        fields.forEach((name) => {
            if (name === 'sort' || name === 'page') return;
            const value = currentFieldValue(name);
            if (value && value !== 'all') filters[name] = value;
        });
        const sort = currentFieldValue('sort') || 'sla';
        return {
            filters: filters,
            sort: [sort],
            columns: [
                'title', 'company', 'country', 'product', 'capacity',
                'contact', 'source', 'submitted_at', 'assignee', 'sla'
            ]
        };
    }

    function canonicalQueueKey() {
        const state = currentViewState([]);
        const page = String(new URLSearchParams(window.location.search).get('page') || '1');
        const normalized = Object.keys(state.filters).sort().reduce((result, key) => {
            result[key] = state.filters[key];
            return result;
        }, { page: page, sort: state.sort[0] });
        return JSON.stringify(normalized);
    }

    function sessionIdentity() {
        const workspace = q('[data-nc-lead-workspace]');
        const userId = String(workspace ? workspace.dataset.ncUserId || '' : '');
        const siteId = String(workspace ? workspace.dataset.ncSiteId || '' : '');
        if (!/^\d+$/.test(userId) || !/^\d+$/.test(siteId)) return null;
        return { userId: userId, siteId: siteId };
    }

    function sessionPrefix(kind) {
        const identity = sessionIdentity();
        return identity
            ? 'nc:leads:v2:' + kind + ':' + identity.userId + ':' + identity.siteId + ':'
            : '';
    }

    function storageKey(kind) {
        const prefix = sessionPrefix(kind);
        return prefix ? prefix + canonicalQueueKey() : '';
    }

    function readSessionJson(key, fallback) {
        if (!key) return fallback;
        try {
            const value = window.sessionStorage.getItem(key);
            return value ? JSON.parse(value) : fallback;
        } catch (_) {
            return fallback;
        }
    }

    function writeSessionJson(key, value) {
        if (!key) return;
        try { window.sessionStorage.setItem(key, JSON.stringify(value)); } catch (_) {}
    }

    function sessionStorageKeys() {
        const keys = [];
        try {
            for (let index = 0; index < window.sessionStorage.length; index += 1) {
                const key = window.sessionStorage.key(index);
                if (key) keys.push(key);
            }
        } catch (_) {}
        return keys;
    }

    function purgeLegacyLeadSessionState() {
        const legacyDraft = /^nc:leads:v2:draft:\d+$/;
        const legacyQueue = /^nc:leads:v2:(selection|scroll):\{/;
        sessionStorageKeys().forEach((key) => {
            if (!legacyDraft.test(key) && !legacyQueue.test(key)) return;
            try { window.sessionStorage.removeItem(key); } catch (_) {}
        });
    }

    function initLogoutSessionCleanup() {
        const form = q('[data-nc-logout-form]');
        const identity = sessionIdentity();
        if (!form || !identity) return;
        form.addEventListener('submit', (event) => {
            const disposition = q('[data-nc-disposition-form]');
            const dirty = disposition && disposition.dataset.ncDirty === 'true';
            const unresolved = disposition && disposition.dataset.ncUnresolvedDraft === 'true';
            if ((dirty || unresolved) && !window.confirm('退出登录将舍弃这条线索尚未保存或未处理的草稿。确定退出吗？')) {
                event.preventDefault();
                return;
            }
            document.dispatchEvent(new CustomEvent('nc:logout-start'));
            const prefixes = ['draft', 'selection', 'scroll', 'assignment-retry'].map((kind) => (
                'nc:leads:v2:' + kind + ':' + identity.userId + ':' + identity.siteId + ':'
            ));
            sessionStorageKeys().forEach((key) => {
                if (!prefixes.some((prefix) => key.startsWith(prefix))) return;
                try { window.sessionStorage.removeItem(key); } catch (_) {}
            });
        });
    }

    function initQueueRestoration() {
        const list = q('.nc-lead-list');
        if (!list) return;
        const scrollKey = storageKey('scroll');
        const saved = Number(readSessionJson(scrollKey, 0));
        let allowPersist = true;
        window.requestAnimationFrame(() => {
            if (Number.isFinite(saved) && saved >= 0) list.scrollTop = saved;
        });
        const persist = () => {
            if (allowPersist) writeSessionJson(scrollKey, list.scrollTop);
        };
        list.addEventListener('scroll', persist, { passive: true });
        qa('.nc-lead-row', list).forEach((link) => link.addEventListener('click', persist));
        window.addEventListener('pagehide', persist);
        document.addEventListener('nc:logout-start', () => {
            allowPersist = false;
            try { window.sessionStorage.removeItem(scrollKey); } catch (_) {}
        });
    }

    function initSidebar() {
        const shell = q('.nc-shell');
        const button = q('[data-nc-sidebar-collapse]');
        if (!shell || !button) return;
        let collapsed = false;
        try { collapsed = window.localStorage.getItem('nc-sidebar-collapsed') === '1'; } catch (_) {}
        const updateButton = () => {
            const label = collapsed ? '展开主导航' : '收起主导航';
            button.setAttribute('aria-pressed', String(collapsed));
            button.setAttribute('aria-label', label);
            button.setAttribute('title', label);
            const copy = q('span', button);
            if (copy) copy.textContent = collapsed ? '展开菜单' : '收起菜单';
        };
        shell.classList.toggle('is-sidebar-collapsed', collapsed);
        updateButton();
        button.addEventListener('click', () => {
            collapsed = !shell.classList.contains('is-sidebar-collapsed');
            shell.classList.toggle('is-sidebar-collapsed', collapsed);
            updateButton();
            try { window.localStorage.setItem('nc-sidebar-collapsed', collapsed ? '1' : '0'); } catch (_) {}
        });
    }

    function activateTab(name, focus) {
        const tabs = qa('[data-nc-tab]');
        const panels = qa('[data-nc-panel]');
        tabs.forEach((tab) => {
            const active = tab.dataset.ncTab === name;
            tab.classList.toggle('is-active', active);
            tab.setAttribute('aria-selected', String(active));
            tab.tabIndex = active ? 0 : -1;
            if (active && focus) tab.focus();
        });
        panels.forEach((panel) => {
            const active = panel.dataset.ncPanel === name;
            panel.classList.toggle('is-active', active);
            panel.hidden = !active;
        });
    }

    function initTabs() {
        const tabs = qa('[data-nc-tab]');
        if (!tabs.length) return;
        const initial = tabs.find((tab) => tab.getAttribute('aria-selected') === 'true') || tabs[0];
        activateTab(initial.dataset.ncTab, false);
        tabs.forEach((tab, index) => {
            tab.addEventListener('click', () => activateTab(tab.dataset.ncTab));
            tab.addEventListener('keydown', (event) => {
                if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
                event.preventDefault();
                let next = index;
                if (event.key === 'ArrowLeft') next = (index - 1 + tabs.length) % tabs.length;
                if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
                if (event.key === 'Home') next = 0;
                if (event.key === 'End') next = tabs.length - 1;
                activateTab(tabs[next].dataset.ncTab, true);
            });
        });
    }

    function updateQualification() {
        const card = q('.nc-qualification-card');
        if (!card) return;
        const boxes = qa('.nc-qualification-list input[type="checkbox"]', card);
        const score = boxes.filter((box) => box.checked).length * 20;
        qa('[data-nc-qualification-score]').forEach((label) => {
            label.textContent = score + '%';
        });
        qa('[data-nc-qualification-progress]').forEach((progress) => {
            progress.style.width = score + '%';
        });
    }

    function initQualification() {
        qa('.nc-qualification-list input[type="checkbox"]').forEach((box) => {
            box.addEventListener('change', updateQualification);
        });
        updateQualification();
    }

    function localDatetimeTomorrow() {
        const date = new Date(Date.now() + 24 * 60 * 60 * 1000);
        date.setMinutes(Math.ceil(date.getMinutes() / 15) * 15, 0, 0);
        const offset = date.getTimezoneOffset();
        return new Date(date.getTime() - offset * 60000).toISOString().slice(0, 16);
    }

    function initTaskModal() {
        const element = q('[data-nc-task-modal]');
        if (!element) return;
        const modal = window.bootstrap && window.bootstrap.Modal
            ? window.bootstrap.Modal.getOrCreateInstance(element)
            : null;
        const create = q('[data-nc-create-task]', element);
        const due = q('[name="task_due_at"]', element);
        const fields = qa('[data-nc-task-field]', element);
        let confirmed = create.checked;
        if (due && !due.value) due.value = localDatetimeTomorrow();
        fields.forEach((field) => { field.disabled = false; });
        qa('[data-nc-open-task]').forEach((button) => {
            button.addEventListener('click', () => {
                if (modal) modal.show();
            });
        });
        element.addEventListener('shown.bs.modal', () => {
            const title = q('[name="task_title"]', element);
            if (title) title.focus();
        });
        element.addEventListener('hidden.bs.modal', () => {
            if (!confirmed && create.checked) {
                create.checked = false;
                create.dispatchEvent(new Event('change', { bubbles: true }));
            }
        });
        document.addEventListener('nc:draft-restored', () => {
            confirmed = Boolean(create.checked);
        });
        const confirm = q('[data-nc-confirm-task]', element);
        if (confirm) {
            confirm.addEventListener('click', () => {
                const title = q('[name="task_title"]', element);
                if (!title.value.trim()) {
                    title.focus();
                    announce('请填写下一步动作。', 'error');
                    return;
                }
                if (!due.value) {
                    due.focus();
                    announce('请选择计划时间。', 'error');
                    return;
                }
                confirmed = true;
                create.checked = true;
                create.dispatchEvent(new Event('change', { bubbles: true }));
                if (modal) modal.hide();
                else q('.btn-close', element).click();
                announce('下一任务已加入本次处置，点击保存后一起提交。');
            });
        }
    }

    function conversionFailureMessage(payload, status) {
        const errors = payload && payload.errors && typeof payload.errors === 'object'
            ? payload.errors
            : {};
        const messages = [];
        Object.keys(errors).forEach((key) => {
            const value = errors[key];
            if (Array.isArray(value)) {
                value.forEach((item) => {
                    if (typeof item === 'string' && item.trim()) messages.push(item.trim());
                });
            } else if (typeof value === 'string' && value.trim()) {
                messages.push(value.trim());
            }
        });
        if (messages.length) return messages.join(' ');
        if (payload && typeof payload.message === 'string' && payload.message.trim()) {
            return payload.message.trim();
        }
        if (status === 409) return '线索状态已发生变化，请刷新页面核对后再尝试转换。';
        if (status === 403 || status === 404) return '线索范围或权限已发生变化，请刷新页面后重试。';
        if (status >= 400 && status < 500) return '当前线索不满足转换条件，请核对负责人、企业和资格阶段。';
        return '转换暂时失败，请稍后重新尝试。';
    }

    function initLeadConversion() {
        const modalElement = q('[data-nc-conversion-modal]');
        const form = q('[data-nc-conversion-form]', modalElement || document);
        if (!modalElement || !form) return;
        const submit = q('[data-nc-conversion-submit]', form);
        const submitLabel = q('[data-nc-conversion-submit-label]', form);
        const feedback = q('[data-nc-conversion-feedback]', form);
        const name = q('[name="opportunity_name"]', form);
        const dismissControls = qa('[data-nc-conversion-dismiss]', form);
        let completed = false;

        const showFeedback = (message, tone) => {
            feedback.textContent = message;
            feedback.hidden = false;
            feedback.classList.toggle('alert-success', tone === 'success');
            feedback.classList.toggle('alert-warning', tone === 'warning');
            feedback.classList.toggle('alert-danger', tone === 'error');
            feedback.setAttribute('role', tone === 'success' ? 'status' : 'alert');
            feedback.setAttribute('aria-live', tone === 'success' ? 'polite' : 'assertive');
        };
        const clearFeedback = () => {
            feedback.hidden = true;
            feedback.textContent = '';
            feedback.classList.remove('alert-success', 'alert-warning', 'alert-danger');
            feedback.setAttribute('role', 'status');
            feedback.setAttribute('aria-live', 'polite');
        };
        const setBusy = (busy) => {
            form.setAttribute('aria-busy', String(busy));
            submit.disabled = busy;
            dismissControls.forEach((control) => { control.disabled = busy; });
            if (submitLabel) submitLabel.textContent = busy ? '正在转换…' : '确认转换';
        };

        modalElement.addEventListener('shown.bs.modal', () => {
            if (name) {
                name.focus();
                name.select();
            }
        });
        modalElement.addEventListener('hide.bs.modal', (event) => {
            if (form.getAttribute('aria-busy') === 'true') event.preventDefault();
        });
        modalElement.addEventListener('hidden.bs.modal', () => {
            if (!completed) {
                clearFeedback();
                setBusy(false);
            }
        });

        form.addEventListener('submit', async (event) => {
            event.preventDefault();
            if (form.getAttribute('aria-busy') === 'true') return;
            const requestBody = new FormData(form);
            clearFeedback();
            setBusy(true);
            try {
                const response = await window.fetch(form.action, {
                    method: 'POST',
                    body: requestBody,
                    credentials: 'same-origin',
                    headers: {
                        Accept: 'application/json',
                        'X-Requested-With': 'XMLHttpRequest',
                        'X-CSRFToken': csrfToken(form)
                    }
                });
                let payload = {};
                let payloadParsed = false;
                try {
                    payload = await response.json();
                    payloadParsed = true;
                } catch (_) {}
                if (response.ok && !payloadParsed) {
                    throw new TypeError('The conversion response could not be verified.');
                }
                if (!response.ok || !payload.ok) {
                    const tone = response.status === 409 ? 'warning' : 'error';
                    const message = conversionFailureMessage(payload, response.status);
                    showFeedback(message, tone);
                    if (submitLabel) submitLabel.textContent = '重新尝试';
                    return;
                }

                completed = true;
                const successMessage = payload.created
                    ? '转换成功，正在刷新企业、联系人和商机关系。'
                    : '这条线索已经完成转换，正在刷新关系。';
                showFeedback(successMessage, 'success');
                window.setTimeout(() => { window.location.reload(); }, 650);
            } catch (_) {
                const message = '网络异常，转换结果尚未确认。请刷新核对关系；仍未关联时可再次尝试。';
                showFeedback(message, 'warning');
                if (submitLabel) submitLabel.textContent = '重新尝试';
            } finally {
                if (!completed) {
                    form.setAttribute('aria-busy', 'false');
                    submit.disabled = false;
                    dismissControls.forEach((control) => { control.disabled = false; });
                }
            }
        });
    }

    function initActivityDefaults() {
        const radios = qa('input[name="activity_type"]');
        const direction = q('[name="activity_direction"]');
        radios.forEach((radio) => radio.addEventListener('change', () => {
            if (radio.checked && radio.value === 'note' && direction) direction.value = 'internal';
            if (radio.checked && radio.value !== 'note' && direction && direction.value === 'internal') direction.value = 'outbound';
        }));
    }

    async function postAssignment(recordIds, assigneeId) {
        const workspace = q('[data-nc-lead-workspace]');
        const url = workspace ? workspace.dataset.ncBulkUrl : '';
        if (!url) throw new Error('负责人分配接口尚未配置。');
        const body = new FormData();
        recordIds.forEach((id) => body.append('record_ids', String(id)));
        body.append('assignee_id', String(assigneeId));
        const response = await window.fetch(url, {
            method: 'POST',
            body: body,
            credentials: 'same-origin',
            headers: {
                Accept: 'application/json',
                'X-Requested-With': 'XMLHttpRequest',
                'X-CSRFToken': csrfToken()
            }
        });
        let payload = {};
        try { payload = await response.json(); } catch (_) {}
        const partial = response.status === 207;
        if ((!response.ok && !partial) || (!payload.ok && !partial)) {
            const error = new Error(payload.message || '负责人更新失败，请检查后重试。');
            error.payload = payload;
            error.status = response.status;
            throw error;
        }
        return { payload: payload, partial: partial };
    }

    function assignmentChangesCurrentQueue() {
        const ownerFilter = currentFieldValue('owner') || 'all';
        return ownerFilter !== 'all';
    }

    function reloadCanonicalLeadQueue(workspace) {
        const raw = workspace ? String(workspace.dataset.ncListUrl || '') : '';
        let destination = null;
        try { destination = new URL(raw, window.location.href); } catch (_) {}
        document.dispatchEvent(new CustomEvent('nc:queue-reload'));
        if (destination && destination.origin === window.location.origin) {
            window.location.assign(destination.href);
        } else {
            window.location.reload();
        }
    }

    function updateAssignedRecord(record) {
        if (!record || record.id === undefined || record.id === null) return;
        const row = q('[data-nc-record-row][data-nc-record-id="' + String(record.id) + '"]');
        const rowOwner = row && q('[data-nc-row-owner]', row);
        const ownerLabel = record.assignee && record.assignee.label
            ? record.assignee.label
            : '未分配';
        if (rowOwner) rowOwner.textContent = ownerLabel;

        const detail = q('[data-nc-owner-assignment][data-nc-record-id="' + String(record.id) + '"]');
        if (!detail) return;
        const select = q('[data-nc-assignee]', detail);
        const button = q('[data-nc-update-owner]', detail);
        const resolvedId = record.assignee && record.assignee.id !== null
            ? String(record.assignee.id)
            : '';
        if (select) {
            if (resolvedId && !qa('option', select).some((option) => option.value === resolvedId)) {
                const option = document.createElement('option');
                option.value = resolvedId;
                option.textContent = ownerLabel;
                select.append(option);
            }
            select.value = resolvedId;
            select.dataset.ncOriginalAssignee = resolvedId;
        }
        if (button) button.disabled = true;
        const version = q('input[name="expected_updated_at"]');
        if (version && record.current_updated_at) {
            version.value = record.current_updated_at;
            document.dispatchEvent(new CustomEvent('nc:server-version-updated', {
                detail: { recordId: String(record.id), updatedAt: record.current_updated_at }
            }));
        }
    }

    function assignmentFailureMessage(payload, fallback) {
        if (payload && payload.message) return payload.message;
        const failures = payload && Array.isArray(payload.failures) ? payload.failures : [];
        if (failures.length && failures[0].message) return failures[0].message;
        return fallback;
    }

    function initBulkSelection() {
        const controls = qa('[data-nc-record-select]');
        const pageToggle = q('[data-nc-select-page]');
        const bar = q('[data-nc-bulk-bar]');
        if (!controls.length || !pageToggle || !bar) return;
        const list = q('.nc-lead-list');
        const count = q('[data-nc-selected-count]', bar);
        const owner = q('[data-nc-bulk-owner]', bar);
        const assign = q('[data-nc-bulk-assign]', bar);
        const clear = q('[data-nc-bulk-clear]', bar);
        const failurePanel = q('[data-nc-bulk-failures]', bar);
        const failureDetails = q('[data-nc-bulk-failure-details]', bar);
        const failureCount = q('[data-nc-bulk-failure-count]', bar);
        const failureList = q('[data-nc-bulk-failure-list]', bar);
        const workspace = q('[data-nc-lead-workspace]');
        const maxRecords = Number(workspace && workspace.dataset.ncBulkMax) || controls.length;
        const selectionKey = storageKey('selection');
        const retryKey = storageKey('assignment-retry');
        let allowSelectionPersist = true;
        const currentIds = new Set(controls.map((control) => String(control.value)));
        const storedSelection = readSessionJson(selectionKey, []);
        const savedIds = (Array.isArray(storedSelection) ? storedSelection : [])
            .map((id) => String(id))
            .filter((id) => /^\d+$/.test(id) && currentIds.has(id));
        controls.forEach((control) => { control.checked = savedIds.includes(String(control.value)); });
        const storedRetry = readSessionJson(retryKey, null);
        if (isPlainObject(storedRetry) && /^\d+$/.test(String(storedRetry.ownerId || ''))) {
            const savedOwner = String(storedRetry.ownerId);
            if (qa('option', owner).some((option) => option.value === savedOwner)) owner.value = savedOwner;
        }

        const selectedIds = () => controls.filter((control) => control.checked).map((control) => String(control.value));
        const showFailures = (failures) => {
            const items = Array.isArray(failures) ? failures : [];
            if (!failurePanel || !failureList || !items.length) {
                if (failurePanel) failurePanel.hidden = true;
                if (failureList) failureList.replaceChildren();
                return;
            }
            failureList.replaceChildren();
            items.forEach((failure) => {
                const item = document.createElement('li');
                const lead = document.createElement('strong');
                const reason = document.createElement('span');
                const id = failure && failure.id !== undefined && failure.id !== null
                    ? String(failure.id)
                    : '未知';
                lead.textContent = '线索 #' + id;
                reason.textContent = failure && failure.message
                    ? String(failure.message)
                    : '未提供失败原因。';
                item.append(lead, reason);
                failureList.append(item);
            });
            if (failureCount) failureCount.textContent = String(items.length);
            if (failureDetails) failureDetails.open = true;
            failurePanel.hidden = false;
        };
        if (isPlainObject(storedRetry) && Array.isArray(storedRetry.failures)) {
            showFailures(storedRetry.failures);
        }
        const update = () => {
            const ids = selectedIds();
            const visible = ids.length > 0;
            if (count) count.textContent = String(ids.length);
            pageToggle.checked = ids.length === controls.length;
            pageToggle.indeterminate = ids.length > 0 && ids.length < controls.length;
            bar.classList.toggle('is-visible', visible);
            bar.setAttribute('aria-hidden', String(!visible));
            if (list) list.classList.toggle('has-bulk-selection', visible);
            if (assign) assign.disabled = !visible || !owner.value;
            if (allowSelectionPersist) writeSessionJson(selectionKey, ids);
        };
        document.addEventListener('nc:logout-start', () => {
            allowSelectionPersist = false;
            try { window.sessionStorage.removeItem(selectionKey); } catch (_) {}
            try { window.sessionStorage.removeItem(retryKey); } catch (_) {}
        });

        controls.forEach((control) => control.addEventListener('change', () => {
            const ids = selectedIds();
            if (ids.length > maxRecords) {
                control.checked = false;
                announce('单次最多选择 ' + maxRecords + ' 条线索。', 'warning');
            }
            update();
        }));
        pageToggle.addEventListener('change', () => {
            if (pageToggle.checked && controls.length > maxRecords) {
                pageToggle.checked = false;
                announce('当前页超过单次上限 ' + maxRecords + ' 条，请分批勾选。', 'warning');
                update();
                return;
            }
            controls.forEach((control) => { control.checked = pageToggle.checked; });
            update();
        });
        owner.addEventListener('change', update);
        clear.addEventListener('click', () => {
            controls.forEach((control) => { control.checked = false; });
            pageToggle.checked = false;
            owner.value = '';
            showFailures([]);
            try { window.sessionStorage.removeItem(retryKey); } catch (_) {}
            update();
            pageToggle.focus();
        });
        assign.addEventListener('click', async () => {
            const ids = selectedIds();
            if (!ids.length) return;
            if (!owner.value) {
                owner.focus();
                announce('请先选择负责人。', 'error');
                return;
            }
            const assigneeId = String(owner.value);
            const capturedIds = new Set(ids);
            const busyControls = controls.concat([pageToggle, owner, clear, assign]);
            let keepBusyForNavigation = false;
            showFailures([]);
            setAsyncControlsBusy(busyControls, true, assign, '分配中…');
            try {
                const result = await postAssignment(ids, assigneeId);
                const records = (Array.isArray(result.payload.records) ? result.payload.records : [])
                    .filter((item) => capturedIds.has(String(item.id)));
                const failures = (Array.isArray(result.payload.failures) ? result.payload.failures : [])
                    .filter((item) => capturedIds.has(String(item.id)));
                records.forEach(updateAssignedRecord);
                const failureIds = new Set(failures.map((item) => String(item.id)));
                controls.forEach((control) => {
                    const id = String(control.value);
                    if (capturedIds.has(id) && !failureIds.has(id)) control.checked = false;
                });
                update();
                if (failures.length) {
                    writeSessionJson(retryKey, { ownerId: assigneeId, failures: failures });
                } else {
                    try { window.sessionStorage.removeItem(retryKey); } catch (_) {}
                }
                if (records.length && assignmentChangesCurrentQueue()) {
                    writeSessionJson(selectionKey, failures.map((item) => String(item.id)));
                    keepBusyForNavigation = true;
                    reloadCanonicalLeadQueue(workspace);
                    return;
                }
                if (failures.length) {
                    showFailures(failures);
                    announce(
                        assignmentFailureMessage(result.payload, records.length + ' 条已更新，' + failures.length + ' 条未更新；失败项仍保留勾选。'),
                        'warning'
                    );
                } else {
                    showFailures([]);
                    announce(result.payload.message || records.length + ' 条线索已更新负责人。');
                }
            } catch (error) {
                const failures = error.payload && Array.isArray(error.payload.failures)
                    ? error.payload.failures
                    : [];
                showFailures(failures);
                const fallback = error instanceof TypeError
                    ? '网络连接异常，当前勾选和负责人输入已保留。'
                    : (error.message || '批量分配失败，当前勾选和负责人输入已保留。');
                announce(assignmentFailureMessage(error.payload, fallback), 'error');
            } finally {
                if (!keepBusyForNavigation) {
                    setAsyncControlsBusy(busyControls, false, assign);
                    update();
                }
            }
        });
        update();
    }

    function initOwnerAssignment() {
        const wrap = q('[data-nc-owner-assignment]');
        if (!wrap) return;
        const select = q('[data-nc-assignee]', wrap);
        const button = q('[data-nc-update-owner]', wrap);
        if (!select || !button) return;
        const updateButton = () => {
            button.disabled = !select.value || select.value === String(select.dataset.ncOriginalAssignee || '');
        };
        select.addEventListener('change', updateButton);
        button.addEventListener('click', async () => {
            const recordId = String(wrap.dataset.ncRecordId || '');
            if (!recordId) {
                announce('当前线索标识缺失，无法更新负责人。', 'error');
                return;
            }
            const assigneeId = String(select.value || '');
            const busyControls = [select, button];
            let keepBusyForNavigation = false;
            setAsyncControlsBusy(busyControls, true, button);
            try {
                const result = await postAssignment([recordId], assigneeId);
                const record = Array.isArray(result.payload.records)
                    ? result.payload.records.find((item) => String(item.id) === recordId)
                    : null;
                if (!record) {
                    throw new Error(assignmentFailureMessage(result.payload, '负责人未更新，请稍后重试。'));
                }
                updateAssignedRecord(record);
                if (!record.current_updated_at) {
                    qa('button[type="submit"]', q('[data-nc-disposition-form]') || document).forEach((item) => { item.disabled = true; });
                    announce('负责人已更新，但页面版本信息缺失；请重新载入后再保存处置。', 'warning');
                    return;
                }
                if (assignmentChangesCurrentQueue()) {
                    keepBusyForNavigation = true;
                    reloadCanonicalLeadQueue(q('[data-nc-lead-workspace]'));
                    return;
                }
                announce(result.payload.message || '负责人已更新，当前处置内容仍保留。');
            } catch (error) {
                const fallback = error instanceof TypeError
                    ? '网络连接异常，当前负责人选择已保留。'
                    : (error.message || '负责人更新失败，当前选择已保留。');
                announce(assignmentFailureMessage(error.payload, fallback), 'error');
            } finally {
                if (!keepBusyForNavigation) {
                    setAsyncControlsBusy(busyControls, false, button);
                    updateButton();
                }
            }
        });
        updateButton();
    }

    function clearFormErrors(form) {
        qa('[aria-invalid="true"]', form).forEach((field) => field.removeAttribute('aria-invalid'));
        const error = q('[data-nc-save-view-error]', form);
        if (error) {
            error.hidden = true;
            error.textContent = '';
        }
    }

    function firstError(payload, fallback) {
        if (payload && payload.message) return payload.message;
        const errors = payload && payload.errors ? payload.errors : {};
        for (const value of Object.values(errors)) {
            if (Array.isArray(value) && value.length) return String(value[0]);
            if (value) return String(value);
        }
        return fallback;
    }

    function appendSavedView(view) {
        if (!view || !view.name || !view.apply_url) return false;
        qa('[data-nc-saved-view-list]').forEach((list) => {
            qa('[data-nc-saved-view-empty]', list).forEach((empty) => empty.remove());
            qa('[data-nc-saved-view-id]', list).forEach((item) => {
                const sameView = String(item.dataset.ncSavedViewId) === String(view.id);
                if (sameView) {
                    item.remove();
                    return;
                }
                if (view.is_default && item.dataset.ncSavedViewDefault === 'true') {
                    item.dataset.ncSavedViewDefault = 'false';
                    const meta = q('em', item);
                    if (meta && meta.textContent === '默认') {
                        if (item.dataset.ncSavedViewShared === 'true') {
                            meta.textContent = item.dataset.ncSavedViewTeam || '团队';
                        } else {
                            meta.remove();
                        }
                    }
                }
            });
            const link = document.createElement('a');
            link.href = view.apply_url;
            link.title = view.name;
            link.dataset.ncSavedViewId = String(view.id);
            link.dataset.ncSavedViewDefault = view.is_default ? 'true' : 'false';
            link.dataset.ncSavedViewShared = view.is_shared ? 'true' : 'false';
            link.dataset.ncSavedViewTeam = (view.team && view.team.label) || '团队';
            const name = document.createElement('span');
            name.textContent = view.name;
            link.append(name);
            if (view.is_default || view.is_shared) {
                const meta = document.createElement('em');
                meta.textContent = view.is_default
                    ? '默认'
                    : ((view.team && view.team.label) || '团队');
                link.append(meta);
            }
            list.append(link);
        });
        return true;
    }

    function initSavedViews() {
        const modalElement = q('[data-nc-save-view-modal]');
        const form = q('[data-nc-save-view-form]');
        if (!modalElement || !form) return;
        const name = q('[name="name"]', form);
        const visibility = q('[data-nc-view-visibility]', form);
        const teamWrap = q('[data-nc-view-team-wrap]', form);
        const team = q('[data-nc-view-team]', form);
        const submit = q('[data-nc-save-view-submit]', form);
        const error = q('[data-nc-save-view-error]', form);
        const allowed = qa('[data-nc-allowed-filter-field]', form).map((field) => field.value);

        const syncVisibility = () => {
            const shared = visibility && visibility.value === 'team';
            if (teamWrap) teamWrap.hidden = !shared;
            if (team) {
                team.disabled = !shared;
                team.required = shared;
            }
        };
        if (visibility) visibility.addEventListener('change', syncVisibility);
        syncVisibility();

        modalElement.addEventListener('show.bs.modal', () => {
            clearFormErrors(form);
            const state = currentViewState(allowed);
            q('[data-nc-view-filters]', form).value = JSON.stringify(state.filters);
            q('[data-nc-view-columns]', form).value = JSON.stringify(state.columns);
            q('[data-nc-view-sort]', form).value = JSON.stringify(state.sort);
        });
        modalElement.addEventListener('shown.bs.modal', () => {
            if (name) name.focus();
        });
        modalElement.addEventListener('hide.bs.modal', (event) => {
            if (form.getAttribute('aria-busy') !== 'true') return;
            event.preventDefault();
            announce('正在保存视图，请等待本次请求完成。', 'warning');
        });

        form.addEventListener('submit', async (event) => {
            event.preventDefault();
            clearFormErrors(form);
            if (!form.action) {
                if (error) {
                    error.textContent = '保存视图接口尚未配置。';
                    error.hidden = false;
                }
                return;
            }
            if (!name.value.trim()) {
                name.setAttribute('aria-invalid', 'true');
                if (error) {
                    error.textContent = '请填写视图名称。';
                    error.hidden = false;
                }
                name.focus();
                return;
            }
            if (visibility && visibility.value === 'team' && (!team || !team.value)) {
                if (team) {
                    team.setAttribute('aria-invalid', 'true');
                    team.focus();
                }
                if (error) {
                    error.textContent = '请选择要共享的销售团队。';
                    error.hidden = false;
                }
                return;
            }
            const body = new FormData(form);
            if (visibility && visibility.value === 'team') body.set('is_shared', '1');
            else {
                body.delete('is_shared');
                body.delete('team');
            }
            const busyControls = qa('input, select, textarea, button', form);
            setAsyncControlsBusy(busyControls, true, submit, '保存中…');
            form.setAttribute('aria-busy', 'true');
            let saved = false;
            try {
                const response = await window.fetch(form.action, {
                    method: 'POST',
                    body: body,
                    credentials: 'same-origin',
                    headers: {
                        Accept: 'application/json',
                        'X-Requested-With': 'XMLHttpRequest',
                        'X-CSRFToken': csrfToken(form)
                    }
                });
                let payload = {};
                try { payload = await response.json(); } catch (_) {}
                if (!response.ok || !payload.ok) {
                    const errors = payload.errors || {};
                    Object.keys(errors).forEach((fieldName) => {
                        const field = form.elements.namedItem(fieldName);
                        if (field && field.setAttribute) field.setAttribute('aria-invalid', 'true');
                    });
                    const message = firstError(payload, '保存视图失败，请检查输入后重试。');
                    if (error) {
                        error.textContent = message;
                        error.hidden = false;
                    }
                    announce(message, 'error');
                    return;
                }
                if (!appendSavedView(payload.saved_view)) {
                    throw new Error('视图已保存，但返回数据不完整；重新载入后即可查看。');
                }
                announce(payload.message || '当前视图已保存。');
                saved = true;
                form.reset();
                syncVisibility();
            } catch (fetchError) {
                const message = fetchError instanceof TypeError
                    ? '网络连接异常，视图名称与设置已保留。'
                    : (fetchError.message || '保存视图失败，当前输入已保留。');
                if (error) {
                    error.textContent = message;
                    error.hidden = false;
                }
                announce(message, 'error');
            } finally {
                form.setAttribute('aria-busy', 'false');
                setAsyncControlsBusy(busyControls, false, submit);
                syncVisibility();
                if (saved) {
                    const close = q('[data-bs-dismiss="modal"]', modalElement);
                    if (close) close.click();
                }
            }
        });
    }

    const DISPOSITION_CONTROL_FIELDS = new Set([
        'csrfmiddlewaretoken',
        'expected_updated_at',
        'idempotency_token',
        'continue_action',
        'current_url',
        'next_url',
        'q',
        'stage_filter',
        'language',
        'source',
        'owner',
        'sla',
        'sort',
        'page',
        'page_size',
        'locale'
    ]);

    function isPlainObject(value) {
        return Boolean(value) && Object.prototype.toString.call(value) === '[object Object]';
    }

    function editableDispositionControls(form) {
        return qa('input[name], select[name], textarea[name]', form).filter((field) => {
            const type = String(field.type || '').toLowerCase();
            const busyLocked = field.dataset.ncBusyControl === 'true'
                && field.dataset.ncBusyWasDisabled !== 'true';
            return (!field.disabled || busyLocked)
                && !DISPOSITION_CONTROL_FIELDS.has(field.name)
                && !['hidden', 'button', 'submit', 'reset', 'file'].includes(type);
        });
    }

    function editableDispositionGroups(form) {
        const groups = new Map();
        editableDispositionControls(form).forEach((field) => {
            if (!groups.has(field.name)) groups.set(field.name, []);
            groups.get(field.name).push(field);
        });
        return groups;
    }

    function serializeEditableDisposition(form) {
        const fields = {};
        editableDispositionGroups(form).forEach((controls, name) => {
            const first = controls[0];
            const type = String(first.type || '').toLowerCase();
            if (type === 'radio') {
                const selected = controls.find((control) => control.checked);
                fields[name] = { kind: 'radio', value: selected ? String(selected.value) : null };
                return;
            }
            if (type === 'checkbox') {
                fields[name] = controls.length === 1
                    ? { kind: 'checkbox', checked: Boolean(first.checked) }
                    : {
                        kind: 'checkboxes',
                        values: controls.filter((control) => control.checked).map((control) => String(control.value))
                    };
                return;
            }
            if (first.tagName === 'SELECT' && first.multiple) {
                fields[name] = {
                    kind: 'multiple',
                    values: Array.from(first.selectedOptions).map((option) => String(option.value))
                };
                return;
            }
            fields[name] = controls.length === 1
                ? { kind: 'value', value: String(first.value || '') }
                : { kind: 'values', values: controls.map((control) => String(control.value || '')) };
        });
        return fields;
    }

    function restoreEditableDisposition(form, savedFields) {
        if (!isPlainObject(savedFields)) return false;
        let restored = false;
        editableDispositionGroups(form).forEach((controls, name) => {
            const saved = savedFields[name];
            if (!isPlainObject(saved)) return;
            const first = controls[0];
            if (saved.kind === 'radio' && (saved.value === null || typeof saved.value === 'string')) {
                const allowed = new Set(controls.map((control) => String(control.value)));
                if (saved.value !== null && !allowed.has(saved.value)) return;
                controls.forEach((control) => { control.checked = saved.value !== null && String(control.value) === saved.value; });
                restored = true;
                return;
            }
            if (saved.kind === 'checkbox' && typeof saved.checked === 'boolean' && controls.length === 1) {
                first.checked = saved.checked;
                restored = true;
                return;
            }
            if (saved.kind === 'checkboxes' && Array.isArray(saved.values)) {
                const allowed = new Set(controls.map((control) => String(control.value)));
                const values = saved.values.map(String).filter((value) => allowed.has(value));
                controls.forEach((control) => { control.checked = values.includes(String(control.value)); });
                restored = true;
                return;
            }
            if (saved.kind === 'multiple' && Array.isArray(saved.values) && first.tagName === 'SELECT' && first.multiple) {
                const allowed = new Set(qa('option', first).map((option) => String(option.value)));
                const values = saved.values.map(String).filter((value) => allowed.has(value));
                qa('option', first).forEach((option) => { option.selected = values.includes(String(option.value)); });
                restored = true;
                return;
            }
            if (saved.kind === 'value' && typeof saved.value === 'string' && controls.length === 1) {
                if (first.tagName === 'SELECT') {
                    const allowed = new Set(qa('option', first).map((option) => String(option.value)));
                    if (!allowed.has(saved.value)) return;
                }
                first.value = saved.value;
                restored = true;
                return;
            }
            if (saved.kind === 'values' && Array.isArray(saved.values) && saved.values.length === controls.length) {
                saved.values.forEach((value, index) => {
                    if (typeof value === 'string') controls[index].value = value;
                });
                restored = true;
            }
        });
        return restored;
    }

    function initDispositionDrafts() {
        const form = q('[data-nc-disposition-form]');
        const workspace = q('[data-nc-lead-workspace]');
        const recordId = String(workspace ? workspace.dataset.ncSelectedRecord || '' : '');
        if (!form || !/^\d+$/.test(recordId)) return null;

        const draftPrefix = sessionPrefix('draft');
        const draftKey = draftPrefix ? draftPrefix + recordId : '';
        const draftAlert = q('[data-nc-draft-alert]');
        const draftTime = q('[data-nc-draft-time]', draftAlert || document);
        const versionWarning = q('[data-nc-draft-version-warning]', draftAlert || document);
        const conflictPanel = q('[data-nc-conflict-panel]');
        const conflictMessage = q('[data-nc-conflict-message]', conflictPanel || document);
        const expectedVersion = q('input[name="expected_updated_at"]', form);
        const idempotencyToken = q('[data-nc-idempotency-token]', form);
        if (idempotencyToken && !idempotencyToken.value) idempotencyToken.value = uuid();
        let serverVersion = expectedVersion ? String(expectedVersion.value || '') : '';
        let baseline = JSON.stringify(serializeEditableDisposition(form));
        let dirty = false;
        let allowNavigation = false;
        let conflictActive = false;
        let submissionInFlight = false;
        let inFlightFields = null;
        let ambiguousRecovery = false;
        let ambiguousRecoveryFields = null;
        let draftTimer = null;
        let storageErrorAnnounced = false;

        const removeStoredDraft = () => {
            if (!draftKey) return;
            try { window.sessionStorage.removeItem(draftKey); } catch (_) {}
        };
        const readStoredDraft = () => {
            if (!draftKey) return null;
            let raw = '';
            try { raw = window.sessionStorage.getItem(draftKey) || ''; } catch (_) { return null; }
            if (!raw) return null;
            if (raw.length > 500000) {
                removeStoredDraft();
                return null;
            }
            try {
                const value = JSON.parse(raw);
                const supportedVersion = value.version === 1 || value.version === 2;
                const validToken = value.version === 1 || (
                    typeof value.idempotencyToken === 'string'
                    && /^[A-Za-z0-9._:-]{8,128}$/.test(value.idempotencyToken)
                );
                const valid = isPlainObject(value)
                    && supportedVersion
                    && validToken
                    && String(value.recordId) === recordId
                    && Number.isFinite(value.savedAt)
                    && isPlainObject(value.fields)
                    && Object.keys(value.fields).length <= 100;
                if (!valid) {
                    removeStoredDraft();
                    return null;
                }
                return value;
            } catch (_) {
                removeStoredDraft();
                return null;
            }
        };
        let unresolvedDraft = readStoredDraft();
        const setUnresolvedDraft = (draft) => {
            unresolvedDraft = draft || null;
            form.dataset.ncUnresolvedDraft = unresolvedDraft ? 'true' : 'false';
        };
        setUnresolvedDraft(unresolvedDraft);

        const updateDirty = () => {
            dirty = JSON.stringify(serializeEditableDisposition(form)) !== baseline;
            form.dataset.ncDirty = dirty ? 'true' : 'false';
            return dirty;
        };
        const draftPayload = (options) => ({
            version: 2,
            recordId: recordId,
            savedAt: Date.now(),
            serverUpdatedAt: serverVersion,
            idempotencyToken: idempotencyToken ? String(idempotencyToken.value || '') : '',
            submissionState: options && options.submissionState === 'ambiguous' ? 'ambiguous' : 'draft',
            fields: options && isPlainObject(options.fields)
                ? options.fields
                : serializeEditableDisposition(form)
        });
        const writeDraft = (payload) => {
            if (!draftKey) {
                if (!storageErrorAnnounced) {
                    announce('草稿隔离信息缺失。当前输入仍在页面中，离开前请先完成保存。', 'error');
                    storageErrorAnnounced = true;
                }
                return false;
            }
            try {
                window.sessionStorage.setItem(draftKey, JSON.stringify(payload));
                storageErrorAnnounced = false;
                return true;
            } catch (_) {
                if (!storageErrorAnnounced) {
                    announce('本机草稿保存失败。当前输入仍在页面中，离开前请先完成保存。', 'error');
                    storageErrorAnnounced = true;
                }
                return false;
            }
        };
        const persistNow = (force) => {
            window.clearTimeout(draftTimer);
            draftTimer = null;
            updateDirty();
            if (submissionInFlight || unresolvedDraft) return true;
            if (!dirty) {
                removeStoredDraft();
                return true;
            }
            return writeDraft(draftPayload());
        };
        const scheduleDraft = () => {
            window.clearTimeout(draftTimer);
            draftTimer = window.setTimeout(() => persistNow(false), 350);
        };
        const focusCurrentTab = () => {
            const activeTab = q('[data-nc-tab][aria-selected="true"]');
            if (activeTab) activeTab.focus();
        };
        const showStoredDraft = (draft) => {
            if (!draftAlert || !draft) return;
            const date = new Date(draft.savedAt);
            if (draftTime && !Number.isNaN(date.getTime())) {
                draftTime.dateTime = date.toISOString();
                draftTime.textContent = new Intl.DateTimeFormat('zh-CN', {
                    month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit'
                }).format(date);
            }
            if (versionWarning) {
                versionWarning.hidden = !draft.serverUpdatedAt || draft.serverUpdatedAt === serverVersion;
            }
            draftAlert.hidden = false;
        };

        if (unresolvedDraft) showStoredDraft(unresolvedDraft);
        updateDirty();

        form.addEventListener('input', () => {
            if (ambiguousRecovery && ambiguousRecoveryFields) {
                if (JSON.stringify(serializeEditableDisposition(form)) !== JSON.stringify(ambiguousRecoveryFields)) {
                    restoreEditableDisposition(form, ambiguousRecoveryFields);
                    updateQualification();
                    announce('网络结果尚未确认；已恢复原请求，请先原样重试或舍弃草稿。', 'warning');
                }
            }
            if (updateDirty() && !unresolvedDraft) scheduleDraft();
            else if (!dirty && !unresolvedDraft) removeStoredDraft();
        });
        form.addEventListener('change', () => {
            if (ambiguousRecovery && ambiguousRecoveryFields) {
                if (JSON.stringify(serializeEditableDisposition(form)) !== JSON.stringify(ambiguousRecoveryFields)) {
                    restoreEditableDisposition(form, ambiguousRecoveryFields);
                    updateQualification();
                    announce('网络结果尚未确认；已恢复原请求，请先原样重试或舍弃草稿。', 'warning');
                }
            }
            if (updateDirty() && !unresolvedDraft) scheduleDraft();
            else if (!dirty && !unresolvedDraft) removeStoredDraft();
        });

        const restore = q('[data-nc-draft-restore]');
        if (restore) restore.addEventListener('click', () => {
            if (!unresolvedDraft) return;
            const draft = unresolvedDraft;
            const restored = restoreEditableDisposition(form, draft.fields);
            if (draft.version === 2 && idempotencyToken) {
                idempotencyToken.value = draft.idempotencyToken;
            }
            ambiguousRecovery = draft.version === 2 && draft.submissionState === 'ambiguous';
            ambiguousRecoveryFields = ambiguousRecovery ? draft.fields : null;
            setUnresolvedDraft(null);
            if (draftAlert) draftAlert.hidden = true;
            document.dispatchEvent(new CustomEvent('nc:draft-restored', { detail: { recordId: recordId } }));
            updateQualification();
            updateDirty();
            if (ambiguousRecovery) writeDraft(draft);
            else persistNow(true);
            focusCurrentTab();
            const restoredMessage = ambiguousRecovery
                ? '已恢复上次结果未明的原请求；请原样重试，系统会复用同一幂等令牌避免重复写入。'
                : '草稿已恢复，请核对最新服务器内容后再保存。';
            announce(restored ? restoredMessage : '草稿没有可恢复的有效字段。', restored ? 'warning' : 'error');
        });

        const discard = q('[data-nc-draft-discard]');
        if (discard) discard.addEventListener('click', () => {
            const discardAmbiguous = Boolean(
                unresolvedDraft
                && unresolvedDraft.version === 2
                && unresolvedDraft.submissionState === 'ambiguous'
            );
            setUnresolvedDraft(null);
            ambiguousRecovery = false;
            ambiguousRecoveryFields = null;
            removeStoredDraft();
            if (draftAlert) draftAlert.hidden = true;
            if (discardAmbiguous) {
                allowNavigation = true;
                window.location.reload();
                return;
            }
            updateDirty();
            if (dirty) scheduleDraft();
            focusCurrentTab();
            announce(dirty ? '旧草稿已舍弃；当前页面的新输入会继续自动保存。' : '未保存草稿已舍弃。');
        });

        const reloadWithDraft = q('[data-nc-conflict-reload]');
        if (reloadWithDraft) reloadWithDraft.addEventListener('click', () => {
            if (!unresolvedDraft) persistNow(true);
            allowNavigation = true;
            window.location.reload();
        });
        const discardAndReload = q('[data-nc-conflict-discard]');
        if (discardAndReload) discardAndReload.addEventListener('click', () => {
            removeStoredDraft();
            setUnresolvedDraft(null);
            ambiguousRecovery = false;
            ambiguousRecoveryFields = null;
            allowNavigation = true;
            window.location.reload();
        });

        window.addEventListener('beforeunload', (event) => {
            if ((!dirty && !unresolvedDraft && !submissionInFlight) || allowNavigation) return;
            persistNow(false);
            event.preventDefault();
            event.returnValue = '';
        });
        window.addEventListener('pagehide', () => {
            if ((dirty || unresolvedDraft || submissionInFlight) && !allowNavigation) persistNow(false);
        });
        document.addEventListener('nc:server-version-updated', (event) => {
            if (!event.detail || String(event.detail.recordId) !== recordId || !event.detail.updatedAt) return;
            serverVersion = String(event.detail.updatedAt);
            if (unresolvedDraft && versionWarning) {
                versionWarning.hidden = !unresolvedDraft.serverUpdatedAt || unresolvedDraft.serverUpdatedAt === serverVersion;
            } else if (dirty) {
                persistNow(true);
            }
        });
        document.addEventListener('nc:logout-start', () => {
            window.clearTimeout(draftTimer);
            draftTimer = null;
            allowNavigation = true;
            submissionInFlight = false;
            ambiguousRecovery = false;
            ambiguousRecoveryFields = null;
            dirty = false;
            form.dataset.ncDirty = 'false';
            setUnresolvedDraft(null);
            removeStoredDraft();
        });
        document.addEventListener('nc:queue-reload', () => {
            if (!unresolvedDraft) persistNow(true);
            allowNavigation = true;
        });

        const enterConflictState = (message, announcement) => {
            submissionInFlight = false;
            inFlightFields = null;
            ambiguousRecovery = false;
            ambiguousRecoveryFields = null;
            updateDirty();
            const conflictDraft = draftPayload();
            const stored = writeDraft(conflictDraft);
            if (stored) setUnresolvedDraft(conflictDraft);
            conflictActive = true;
            if (draftAlert) draftAlert.hidden = true;
            if (conflictMessage) {
                conflictMessage.textContent = stored
                    ? (message + ' 你的输入已保存为本机草稿；请先载入最新版本，再选择恢复。')
                    : (message + ' 本机草稿写入失败，请不要关闭此页面。');
            }
            if (conflictPanel) {
                conflictPanel.hidden = false;
                conflictPanel.focus();
            }
            qa('button[type="submit"]', form).forEach((button) => { button.disabled = true; });
            announce(stored ? announcement : '服务器内容已更新，但草稿写入失败。', 'error');
        };

        const setServerVersion = (updatedAt) => {
            if (!updatedAt) return;
            serverVersion = String(updatedAt);
            if (expectedVersion) expectedVersion.value = serverVersion;
        };

        const lateChangedFields = (requestFields, currentFields) => {
            const changed = {};
            Object.keys(currentFields || {}).forEach((name) => {
                if (JSON.stringify(currentFields[name]) !== JSON.stringify((requestFields || {})[name])) {
                    changed[name] = currentFields[name];
                }
            });
            return changed;
        };

        return {
            beforeSubmit: () => {
                if (unresolvedDraft) {
                    showStoredDraft(unresolvedDraft);
                    if (draftAlert) draftAlert.focus();
                    announce('请先恢复或舍弃现有草稿，再提交当前处置。', 'warning');
                    return false;
                }
                if (
                    ambiguousRecovery
                    && ambiguousRecoveryFields
                    && JSON.stringify(serializeEditableDisposition(form)) !== JSON.stringify(ambiguousRecoveryFields)
                ) {
                    restoreEditableDisposition(form, ambiguousRecoveryFields);
                    updateQualification();
                    updateDirty();
                    announce('请先原样重试结果未明的请求，或舍弃草稿后重新处置。', 'warning');
                    return false;
                }
                return persistNow(true);
            },
            persistNow: () => persistNow(true),
            beginSubmission: (requestFields) => {
                submissionInFlight = true;
                inFlightFields = JSON.parse(JSON.stringify(requestFields || {}));
                return writeDraft(draftPayload({
                    submissionState: 'ambiguous',
                    fields: inFlightFields,
                }));
            },
            handleKnownFailure: () => {
                submissionInFlight = false;
                inFlightFields = null;
                ambiguousRecovery = false;
                ambiguousRecoveryFields = null;
                writeDraft(draftPayload());
            },
            handleAmbiguousFailure: () => {
                submissionInFlight = false;
                ambiguousRecovery = Boolean(inFlightFields);
                ambiguousRecoveryFields = inFlightFields;
            },
            handleConflict: (message) => {
                enterConflictState(message, '检测到并发更新，当前输入已保存为草稿。');
            },
            handlePostSubmitChanges: (message, requestFields, currentFields, updatedAt) => {
                submissionInFlight = false;
                inFlightFields = null;
                setServerVersion(updatedAt);
                if (idempotencyToken) idempotencyToken.value = uuid();
                const changedFields = lateChangedFields(requestFields, currentFields);
                const lateDraft = draftPayload({ fields: changedFields });
                const stored = writeDraft(lateDraft);
                if (stored) setUnresolvedDraft(lateDraft);
                conflictActive = true;
                baseline = JSON.stringify(requestFields || {});
                updateDirty();
                if (draftAlert) draftAlert.hidden = true;
                if (conflictMessage) {
                    conflictMessage.textContent = stored
                        ? (message + ' 只有请求期间发生变化的字段被保留；未变的沟通和任务不会重放。')
                        : (message + ' 本机草稿写入失败，请不要关闭此页面。');
                }
                if (conflictPanel) {
                    conflictPanel.hidden = false;
                    conflictPanel.focus();
                }
                qa('button[type="submit"]', form).forEach((button) => { button.disabled = true; });
                announce(stored ? '上一版本已保存；请求期间的新更改已单独保留。' : '处置已保存，但后续更改草稿写入失败。', stored ? 'warning' : 'error');
            },
            markSaved: (updatedAt, navigationAllowed) => {
                window.clearTimeout(draftTimer);
                submissionInFlight = false;
                inFlightFields = null;
                removeStoredDraft();
                setUnresolvedDraft(null);
                ambiguousRecovery = false;
                ambiguousRecoveryFields = null;
                setServerVersion(updatedAt);
                if (idempotencyToken) idempotencyToken.value = uuid();
                baseline = JSON.stringify(serializeEditableDisposition(form));
                dirty = false;
                form.dataset.ncDirty = 'false';
                allowNavigation = Boolean(navigationAllowed);
            },
            isConflict: () => conflictActive
        };
    }

    function setBusy(form, busy) {
        form.setAttribute('aria-busy', String(busy));
        document.documentElement.dataset.ncDispositionBusy = busy ? 'true' : 'false';
        if (busy) {
            const formControls = qa('input:not([type="hidden"]), select, textarea, button', form);
            const assignmentControls = qa('[data-nc-select-page], [data-nc-record-select], [data-nc-bulk-bar] select, [data-nc-bulk-bar] button');
            Array.from(new Set(formControls.concat(assignmentControls))).forEach((control) => {
                control.dataset.ncBusyControl = 'true';
                control.dataset.ncBusyWasDisabled = String(control.disabled);
                control.disabled = true;
            });
            qa('a[href]', form).forEach((link) => {
                if (link.getAttribute('aria-disabled') === 'true') return;
                link.dataset.ncBusyLink = 'true';
                link.dataset.ncBusyHadTabindex = String(link.hasAttribute('tabindex'));
                link.dataset.ncBusyTabindex = link.getAttribute('tabindex') || '';
                link.setAttribute('aria-disabled', 'true');
                link.setAttribute('tabindex', '-1');
            });
            return;
        }
        qa('[data-nc-busy-control="true"]').forEach((control) => {
            control.disabled = control.dataset.ncBusyWasDisabled === 'true';
            delete control.dataset.ncBusyControl;
            delete control.dataset.ncBusyWasDisabled;
        });
        qa('[data-nc-busy-link="true"]', form).forEach((link) => {
            link.removeAttribute('aria-disabled');
            if (link.dataset.ncBusyHadTabindex === 'true') {
                link.setAttribute('tabindex', link.dataset.ncBusyTabindex);
            } else {
                link.removeAttribute('tabindex');
            }
            delete link.dataset.ncBusyLink;
            delete link.dataset.ncBusyHadTabindex;
            delete link.dataset.ncBusyTabindex;
        });
    }

    function initDispositionNavigationGuard(form) {
        document.addEventListener('click', (event) => {
            if (document.documentElement.dataset.ncDispositionBusy !== 'true') return;
            const link = event.target && event.target.closest ? event.target.closest('a[href]') : null;
            if (!link || String(link.target || '').toLowerCase() === '_blank') return;
            event.preventDefault();
            event.stopImmediatePropagation();
            announce('请等待当前处置保存完成后再离开此页。', 'warning');
        }, true);
        document.addEventListener('submit', (event) => {
            if (document.documentElement.dataset.ncDispositionBusy !== 'true') return;
            if (event.target === form && form.getAttribute('aria-busy') !== 'true') return;
            event.preventDefault();
            event.stopImmediatePropagation();
            announce('请等待当前处置保存完成后再提交其他操作。', 'warning');
        }, true);
    }

    function validateDisposition(form) {
        const body = q('[data-nc-activity-body]', form);
        if (body && !body.value.trim()) {
            activateTab('activity');
            body.focus();
            announce('请先记录本次沟通结果，再保存处置。', 'error');
            return false;
        }
        const createTask = q('[data-nc-create-task]', form);
        if (createTask && createTask.checked) {
            const due = q('[name="task_due_at"]', form);
            if (!due || !due.value) {
                announce('已选择创建任务，请补充计划时间。', 'error');
                return false;
            }
        }
        return true;
    }

    function initDisposition(draftManager) {
        const form = q('[data-nc-disposition-form]');
        if (!form) return;
        const token = q('[data-nc-idempotency-token]', form);
        const action = q('[data-nc-continue-action]', form);
        if (token && !token.value) token.value = uuid();
        initDispositionNavigationGuard(form);

        qa('[data-nc-submit]', form).forEach((button) => {
            button.addEventListener('click', () => { action.value = button.dataset.ncSubmit; });
        });
        form.addEventListener('click', (event) => {
            const lockedAction = event.target && event.target.closest
                ? event.target.closest('[data-nc-busy-control="true"], [data-nc-busy-link="true"]')
                : null;
            if (!lockedAction) return;
            event.preventDefault();
            event.stopImmediatePropagation();
        }, true);

        form.addEventListener('submit', async (event) => {
            event.preventDefault();
            const assignmentBusy = q('[data-nc-bulk-assign][aria-busy="true"]');
            if (form.getAttribute('aria-busy') === 'true' || q('[aria-busy="true"]', form) || assignmentBusy) {
                announce('请等待当前操作完成后再保存处置。', 'warning');
                return;
            }
            if (event.submitter && event.submitter.dataset.ncSubmit) {
                action.value = event.submitter.dataset.ncSubmit;
            }
            if (draftManager && !draftManager.beforeSubmit()) return;
            if (!validateDisposition(form)) return;
            const requestBody = new FormData(form);
            const requestFields = serializeEditableDisposition(form);
            const requestSnapshot = JSON.stringify(requestFields);
            if (draftManager && !draftManager.beginSubmission(requestFields)) {
                announce('无法安全保存本次请求的重试令牌，已取消提交。', 'error');
                return;
            }
            setBusy(form, true);
            let keepLockedForNavigation = false;
            let disableAfterKnownSuccess = false;
            try {
                const response = await window.fetch(form.action, {
                    method: 'POST',
                    body: requestBody,
                    credentials: 'same-origin',
                    headers: {
                        Accept: 'application/json',
                        'X-Requested-With': 'XMLHttpRequest'
                    }
                });
                let payload = {};
                let payloadParsed = false;
                try {
                    payload = await response.json();
                    payloadParsed = true;
                } catch (_) {}
                if (response.ok && !payloadParsed) {
                    throw new TypeError('The server response could not be verified.');
                }
                if (!response.ok || !payload.ok) {
                    const message = payload.message || '保存失败，请检查输入后重试。';
                    if (response.status === 409 && draftManager) draftManager.handleConflict(message);
                    else {
                        if (draftManager) draftManager.handleKnownFailure();
                        announce(message, 'error');
                    }
                    return;
                }
                const currentFields = serializeEditableDisposition(form);
                const currentSnapshot = JSON.stringify(currentFields);
                if (currentSnapshot !== requestSnapshot) {
                    const message = '上一版本已保存，但检测到请求期间还有新的更改。';
                    if (draftManager) draftManager.handlePostSubmitChanges(
                        message,
                        requestFields,
                        currentFields,
                        payload.current_updated_at
                    );
                    else announce(message + ' 当前内容仍保留在页面中，请重新载入后核对。', 'warning');
                    return;
                }
                let destination = null;
                const redirectUrl = typeof payload.redirect_url === 'string' ? payload.redirect_url.trim() : '';
                if (redirectUrl) {
                    try { destination = new URL(redirectUrl, window.location.href); } catch (_) {}
                }
                if (!destination || destination.origin !== window.location.origin) {
                    const message = '线索处置已保存，但服务器返回的下一地址无效。';
                    if (draftManager) draftManager.markSaved(payload.current_updated_at, false);
                    disableAfterKnownSuccess = true;
                    announce(message + ' 草稿已清理；请重新载入后继续。', 'warning');
                    return;
                }
                if (draftManager) draftManager.markSaved(payload.current_updated_at, true);
                keepLockedForNavigation = true;
                announce(payload.message || '线索处置已保存。');
                window.setTimeout(() => { window.location.assign(destination.href); }, 280);
            } catch (_) {
                if (draftManager) draftManager.handleAmbiguousFailure();
                announce('网络连接异常，请勿修改当前内容；重试将复用同一幂等令牌，避免重复写入。', 'error');
            } finally {
                if (!keepLockedForNavigation) {
                    setBusy(form, false);
                    if (disableAfterKnownSuccess) {
                        qa('button[type="submit"]', form).forEach((button) => { button.disabled = true; });
                    }
                    if (draftManager && draftManager.isConflict()) {
                        qa('button[type="submit"]', form).forEach((button) => { button.disabled = true; });
                    }
                }
            }
        });

        document.addEventListener('keydown', (event) => {
            if (!(event.ctrlKey || event.metaKey)) return;
            if (event.target && event.target.closest && event.target.closest('.modal')) return;
            if (form.getAttribute('aria-busy') === 'true' && (event.key.toLowerCase() === 's' || event.key === 'Enter')) {
                event.preventDefault();
                return;
            }
            if (event.key.toLowerCase() === 's') {
                event.preventDefault();
                action.value = 'stay';
                form.requestSubmit(q('[data-nc-submit="stay"]', form));
            }
            if (event.key === 'Enter') {
                event.preventDefault();
                action.value = 'next';
                form.requestSubmit(q('[data-nc-submit="next"]', form));
            }
        });
    }

    function initSimpleForms() {
        qa('[data-nc-submit-on-change]').forEach((control) => {
            control.addEventListener('change', () => {
                if (!control.form) return;
                if (document.documentElement.dataset.ncDispositionBusy === 'true') {
                    announce('请等待当前处置保存完成后再更改队列筛选。', 'warning');
                    return;
                }
                control.form.requestSubmit();
            });
        });
    }

    document.addEventListener('DOMContentLoaded', () => {
        purgeLegacyLeadSessionState();
        initLogoutSessionCleanup();
        renderIcons();
        initSidebar();
        initQueueRestoration();
        initTabs();
        initQualification();
        initTaskModal();
        initLeadConversion();
        initActivityDefaults();
        initBulkSelection();
        initOwnerAssignment();
        initSavedViews();
        const draftManager = initDispositionDrafts();
        initDisposition(draftManager);
        initSimpleForms();
    });
})();
