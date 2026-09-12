(function () {
    'use strict';

    function firstMessage(errors, fallback) {
        if (!errors || typeof errors !== 'object') return fallback;
        const values = Object.values(errors);
        for (const value of values) {
            if (Array.isArray(value) && value.length) return String(value[0]);
            if (value) return String(value);
        }
        return fallback;
    }

    function setFormStatus(form, tone, message) {
        const status = form.querySelector('[data-nc-form-status]');
        if (!status) return;
        status.className = `alert alert-${tone} mt-3 mb-0`;
        status.textContent = message;
        status.hidden = false;
    }

    function clearFormErrors(form) {
        form.querySelectorAll('.is-invalid').forEach((field) => field.classList.remove('is-invalid'));
        form.querySelectorAll('[data-error-for]').forEach((node) => {
            node.textContent = '';
        });
        const status = form.querySelector('[data-nc-form-status]');
        if (status) {
            status.hidden = true;
            status.textContent = '';
        }
    }

    function showFormErrors(form, errors) {
        Object.entries(errors || {}).forEach(([name, messages]) => {
            const field = form.elements.namedItem(name);
            if (field && field.classList) field.classList.add('is-invalid');
            const feedback = form.querySelector(`[data-error-for="${CSS.escape(name)}"]`);
            if (feedback) feedback.textContent = Array.isArray(messages) ? messages.join(' ') : String(messages);
        });
    }

    const filterForm = document.querySelector('[data-nc-account-filters]');
    if (filterForm) {
        filterForm.addEventListener('submit', () => {
            const workspace = filterForm.closest('[data-nc-account-workspace]');
            const button = filterForm.querySelector('[data-nc-filter-submit]');
            if (workspace) workspace.setAttribute('aria-busy', 'true');
            if (button) {
                button.disabled = true;
                const label = button.querySelector('span');
                if (label) label.textContent = '正在应用…';
            }
        });
    }

    document.querySelectorAll('[data-nc-copy]').forEach((button) => {
        button.addEventListener('click', async () => {
            const value = button.getAttribute('data-nc-copy') || '';
            const status = document.querySelector('[data-nc-copy-status]');
            try {
                await navigator.clipboard.writeText(value);
                if (status) status.textContent = '已复制到剪贴板。';
                button.classList.add('text-success');
                window.setTimeout(() => button.classList.remove('text-success'), 900);
            } catch (_error) {
                if (status) status.textContent = '浏览器阻止了复制，请手动选择文本。';
            }
        });
    });

    const lookup = document.querySelector('[data-nc-company-lookup]');
    if (lookup) {
        const input = lookup.querySelector('[data-nc-company-search]');
        const hidden = lookup.querySelector('[data-nc-company-value]');
        const results = lookup.querySelector('[data-nc-company-results]');
        const hint = lookup.querySelector('[data-nc-company-hint]');
        const clear = lookup.querySelector('[data-nc-company-clear]');
        const searchUrl = lookup.getAttribute('data-search-url');
        let timer = 0;
        let controller = null;

        function closeResults() {
            results.replaceChildren();
            results.hidden = true;
        }

        function chooseCompany(item) {
            hidden.value = String(item.id);
            input.value = item.name;
            hint.textContent = `已选择：${item.name}${item.location ? ` · ${item.location}` : ''}`;
            closeResults();
            input.focus();
        }

        async function searchCompanies() {
            const query = input.value.trim();
            if (query.length < 2) {
                hint.textContent = '请输入至少 2 个字符搜索同团队企业。';
                closeResults();
                return;
            }
            if (controller) controller.abort();
            controller = new AbortController();
            hint.textContent = '正在搜索同团队企业…';
            try {
                const url = new URL(searchUrl, window.location.origin);
                url.searchParams.set('q', query);
                const response = await fetch(url, {
                    headers: { Accept: 'application/json' },
                    credentials: 'same-origin',
                    signal: controller.signal,
                });
                const payload = await response.json();
                if (!response.ok || !payload.ok) throw new Error('lookup_failed');
                results.replaceChildren();
                payload.items.forEach((item) => {
                    const option = document.createElement('button');
                    option.type = 'button';
                    option.className = 'list-group-item list-group-item-action';
                    const title = document.createElement('strong');
                    title.textContent = item.name;
                    const meta = document.createElement('small');
                    meta.textContent = item.location || '未填写地区';
                    option.append(title, meta);
                    option.addEventListener('click', () => chooseCompany(item));
                    results.append(option);
                });
                if (!payload.items.length) {
                    const empty = document.createElement('div');
                    empty.className = 'list-group-item text-secondary';
                    empty.textContent = '没有匹配的同团队企业。';
                    results.append(empty);
                }
                results.hidden = false;
                hint.textContent = payload.message || `找到 ${payload.items.length} 条企业。请选择一条。`;
            } catch (error) {
                if (error.name === 'AbortError') return;
                closeResults();
                hint.textContent = '企业搜索暂时失败，当前关联尚未改变。';
            }
        }

        input.addEventListener('input', () => {
            window.clearTimeout(timer);
            timer = window.setTimeout(searchCompanies, 260);
        });
        input.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') closeResults();
        });
        clear.addEventListener('click', () => {
            hidden.value = '';
            input.value = '';
            hint.textContent = '已选择移除企业关联；保存后生效。';
            closeResults();
            input.focus();
        });
    }

    const editForm = document.querySelector('[data-nc-account-form]');
    if (editForm) {
        editForm.addEventListener('submit', async (event) => {
            event.preventDefault();
            clearFormErrors(editForm);
            const submit = editForm.querySelector('[data-nc-submit]');
            const label = submit && submit.querySelector('span');
            if (submit) submit.disabled = true;
            if (label) label.textContent = '正在保存…';
            editForm.setAttribute('aria-busy', 'true');
            try {
                const response = await fetch(editForm.action, {
                    method: 'POST',
                    body: new FormData(editForm),
                    headers: { Accept: 'application/json' },
                    credentials: 'same-origin',
                });
                let payload = null;
                try {
                    payload = await response.json();
                } catch (_error) {
                    payload = { ok: false, errors: { __all__: ['服务器返回了无法识别的响应。'] } };
                }
                if (!response.ok || !payload.ok) {
                    showFormErrors(editForm, payload.errors);
                    const conflict = response.status === 409;
                    setFormStatus(
                        editForm,
                        conflict ? 'warning' : 'danger',
                        firstMessage(
                            payload.errors,
                            conflict ? '资料已更新，请刷新后核对。' : '保存失败，请检查表单。'
                        )
                    );
                    return;
                }
                setFormStatus(editForm, 'success', '资料已保存，正在刷新关系摘要…');
                window.setTimeout(() => window.location.reload(), 450);
            } catch (_error) {
                setFormStatus(editForm, 'danger', '网络或服务器暂时不可用；没有确认保存成功。请重试。');
            } finally {
                editForm.removeAttribute('aria-busy');
                if (submit) submit.disabled = false;
                if (label) label.textContent = '保存更改';
            }
        });
    }

    const modal = document.querySelector('[data-nc-account-modal]');
    if (modal) {
        modal.addEventListener('shown.bs.modal', () => {
            const first = modal.querySelector('input:not([type="hidden"]), select, textarea');
            if (first) first.focus();
        });
    }
}());
