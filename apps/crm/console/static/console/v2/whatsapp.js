(() => {
    'use strict';

    const workspace = document.querySelector('[data-nc-wa-workspace]');
    if (!workspace) return;

    const globalFeedback = workspace.querySelector('[data-wa-global-feedback]');
    const csrfToken = () => workspace.querySelector('input[name="csrfmiddlewaretoken"]')?.value || '';

    const announceError = (message) => {
        if (!globalFeedback) return;
        globalFeedback.textContent = message || '操作失败，请稍后重试。';
        globalFeedback.hidden = false;
        window.setTimeout(() => {
            globalFeedback.hidden = true;
        }, 7000);
    };

    const parseResponse = async (response) => {
        let payload = {};
        try {
            payload = await response.json();
        } catch (_error) {
            payload = {};
        }
        if (!response.ok || payload.ok === false) {
            const error = new Error(payload.error || payload.message || `请求失败（HTTP ${response.status}）`);
            error.status = response.status;
            throw error;
        }
        return payload;
    };

    const post = async (url, formData) => {
        if (!navigator.onLine) throw new Error('网络已断开，操作尚未提交。');
        if (!formData.has('csrfmiddlewaretoken')) {
            formData.append('csrfmiddlewaretoken', csrfToken());
        }
        const response = await fetch(url, {
            method: 'POST',
            body: formData,
            credentials: 'same-origin',
            headers: {
                Accept: 'application/json',
                'X-Requested-With': 'XMLHttpRequest',
            },
        });
        return parseResponse(response);
    };

    const setBusy = (control, busy, busyLabel = '处理中…') => {
        if (!control) return;
        if (busy) {
            control.dataset.previousLabel = control.textContent;
            control.disabled = true;
            control.setAttribute('aria-busy', 'true');
            if (control.querySelector('span')) control.querySelector('span').textContent = busyLabel;
        } else {
            control.disabled = false;
            control.removeAttribute('aria-busy');
            if (control.dataset.previousLabel && control.querySelector('span')) {
                control.querySelector('span').textContent = control.dataset.previousLabel.trim();
            }
        }
    };

    const reloadAfterSuccess = () => window.location.reload();

    const timeline = workspace.querySelector('[data-wa-timeline]');
    if (timeline) {
        window.requestAnimationFrame(() => {
            timeline.scrollTop = timeline.scrollHeight;
        });
    }

    const idempotencyInput = workspace.querySelector('[data-wa-idempotency]');
    const resetIdempotency = () => {
        if (!idempotencyInput) return;
        idempotencyInput.value = window.crypto?.randomUUID?.()
            || `browser-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    };
    resetIdempotency();

    const sendForm = workspace.querySelector('[data-wa-send-form]');
    const noteForm = workspace.querySelector('[data-wa-note-form]');
    const sendFeedback = workspace.querySelector('[data-wa-feedback]');
    const noteFeedback = workspace.querySelector('[data-wa-note-feedback]');
    const composeTabs = [...workspace.querySelectorAll('[data-wa-compose-tab]')];
    const modeSelect = workspace.querySelector('[data-wa-mode]');
    const templateWrap = workspace.querySelector('[data-wa-template-wrap]');
    const templateSelect = workspace.querySelector('[data-wa-template]');
    const messageBody = workspace.querySelector('[data-wa-body]');
    const fileInput = workspace.querySelector('[data-wa-file]');
    const fileLabel = workspace.querySelector('[data-wa-file-label]');

    const syncMode = () => {
        if (!modeSelect) return;
        const selectedOption = modeSelect.options[modeSelect.selectedIndex];
        if (selectedOption?.disabled) {
            const firstEnabled = [...modeSelect.options].find((option) => !option.disabled);
            if (firstEnabled) modeSelect.value = firstEnabled.value;
        }
        const templateMode = modeSelect.value === 'template';
        if (templateWrap) templateWrap.hidden = !templateMode;
        if (templateSelect) templateSelect.required = templateMode;
        if (messageBody) {
            messageBody.placeholder = templateMode
                ? '可选：添加仅供内部识别的模板发送备注…'
                : '输入给客户的消息…';
        }
        if (fileInput) fileInput.disabled = templateMode || sendForm?.dataset.waBlocked === 'true';
    };
    modeSelect?.addEventListener('change', syncMode);
    syncMode();

    composeTabs.forEach((tab) => {
        tab.addEventListener('click', () => {
            const target = tab.dataset.waComposeTab;
            composeTabs.forEach((item) => {
                const active = item === tab;
                item.classList.toggle('is-active', active);
                item.setAttribute('aria-selected', String(active));
            });
            if (sendForm) sendForm.hidden = target !== 'message';
            if (noteForm) noteForm.hidden = target !== 'note';
            (target === 'note' ? noteForm : sendForm)?.querySelector('textarea')?.focus();
        });
    });

    fileInput?.addEventListener('change', () => {
        if (!fileLabel) return;
        const file = fileInput.files?.[0];
        fileLabel.textContent = file ? file.name : '添加附件';
    });

    const replyInput = workspace.querySelector('[data-wa-reply-id]');
    const replying = workspace.querySelector('[data-wa-replying]');
    const replyingLabel = workspace.querySelector('[data-wa-replying-label]');
    workspace.querySelectorAll('[data-wa-reply]').forEach((button) => {
        button.addEventListener('click', () => {
            if (replyInput) replyInput.value = button.dataset.waReply || '';
            if (replyingLabel) replyingLabel.textContent = button.dataset.waReplyLabel || '客户消息';
            if (replying) replying.hidden = false;
            messageBody?.focus();
        });
    });
    workspace.querySelector('[data-wa-reply-clear]')?.addEventListener('click', () => {
        if (replyInput) replyInput.value = '';
        if (replying) replying.hidden = true;
    });

    sendForm?.addEventListener('submit', async (event) => {
        event.preventDefault();
        const submit = sendForm.querySelector('[data-wa-send]');
        const body = messageBody?.value.trim() || '';
        const hasFile = Boolean(fileInput?.files?.length);
        if (modeSelect?.value === 'template' && !templateSelect?.value) {
            sendFeedback.textContent = '请选择已批准模板。';
            sendFeedback.classList.add('is-error');
            templateSelect?.focus();
            return;
        }
        if (modeSelect?.value !== 'template' && !body && !hasFile) {
            sendFeedback.textContent = '请输入消息或添加附件。';
            sendFeedback.classList.add('is-error');
            messageBody?.focus();
            return;
        }
        sendFeedback.textContent = '正在安全提交…';
        sendFeedback.classList.remove('is-error');
        setBusy(submit, true, '提交中…');
        try {
            await post(sendForm.action, new FormData(sendForm));
            sendFeedback.textContent = '已提交到发送队列。';
            resetIdempotency();
            reloadAfterSuccess();
        } catch (error) {
            sendFeedback.textContent = error.message;
            sendFeedback.classList.add('is-error');
            setBusy(submit, false);
        }
    });

    noteForm?.addEventListener('submit', async (event) => {
        event.preventDefault();
        const submit = noteForm.querySelector('button[type="submit"]');
        const textarea = noteForm.querySelector('textarea');
        if (!textarea?.value.trim()) {
            noteFeedback.textContent = '请输入内部备注。';
            noteFeedback.classList.add('is-error');
            textarea?.focus();
            return;
        }
        noteFeedback.textContent = '正在保存…';
        noteFeedback.classList.remove('is-error');
        setBusy(submit, true);
        try {
            await post(noteForm.action, new FormData(noteForm));
            reloadAfterSuccess();
        } catch (error) {
            noteFeedback.textContent = error.message;
            noteFeedback.classList.add('is-error');
            setBusy(submit, false);
        }
    });

    const dispositionForm = workspace.querySelector('[data-wa-disposition-form]');
    const dispositionAction = dispositionForm?.querySelector('[data-wa-disposition-action]');
    const requirementFields = dispositionForm?.querySelector('[data-wa-requirement-fields]');
    const nextStepFields = dispositionForm?.querySelector('[data-wa-next-step-fields]');
    const valueFields = dispositionForm?.querySelector('[data-wa-value-fields]');
    const taskDue = dispositionForm?.querySelector('[data-wa-task-due]');
    const buyerValue = dispositionForm?.querySelector('[data-wa-buyer-value]');
    const dispositionFeedback = dispositionForm?.querySelector('[data-wa-disposition-feedback]');
    const dispositionIdempotency = dispositionForm?.querySelector('[data-wa-disposition-idempotency]');

    const resetDispositionIdempotency = () => {
        if (!dispositionIdempotency) return;
        dispositionIdempotency.value = window.crypto?.randomUUID?.()
            || `disposition-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    };

    const syncDisposition = () => {
        if (!dispositionAction) return;
        const action = dispositionAction.value;
        const needsRequirements = ['qualified', 'quotation', 'won'].includes(action);
        const needsNextStep = ['contacted', 'qualified', 'quotation'].includes(action);
        const needsValue = action === 'won';
        if (requirementFields) requirementFields.hidden = !needsRequirements;
        if (nextStepFields) nextStepFields.hidden = !needsNextStep;
        if (valueFields) valueFields.hidden = !needsValue;
        requirementFields?.querySelectorAll('[data-wa-required-field]').forEach((input) => {
            input.required = needsRequirements;
        });
        if (taskDue) taskDue.required = needsNextStep;
        if (buyerValue) buyerValue.required = needsValue;
    };

    resetDispositionIdempotency();
    dispositionAction?.addEventListener('change', syncDisposition);
    syncDisposition();

    dispositionForm?.addEventListener('submit', async (event) => {
        event.preventDefault();
        const submit = dispositionForm.querySelector('[data-wa-disposition-submit]');
        dispositionFeedback.textContent = '正在一次性保存…';
        dispositionFeedback.classList.remove('is-error');
        setBusy(submit, true);
        try {
            await post(dispositionForm.action, new FormData(dispositionForm));
            dispositionFeedback.textContent = '已保存，正在刷新 CRM 状态。';
            resetDispositionIdempotency();
            reloadAfterSuccess();
        } catch (error) {
            dispositionFeedback.textContent = error.message;
            dispositionFeedback.classList.add('is-error');
            setBusy(submit, false);
        }
    });

    workspace.querySelector('[data-wa-assign-form]')?.addEventListener('submit', async (event) => {
        event.preventDefault();
        const form = event.currentTarget;
        const submit = form.querySelector('button[type="submit"]');
        setBusy(submit, true);
        try {
            await post(form.action, new FormData(form));
            reloadAfterSuccess();
        } catch (error) {
            announceError(error.message);
            setBusy(submit, false);
        }
    });

    workspace.querySelectorAll('[data-wa-action]').forEach((button) => {
        button.addEventListener('click', async () => {
            const action = button.dataset.waAction;
            const formData = new FormData();
            if (action === 'assign') formData.append('owner_user_id', button.dataset.ownerId || '');
            if (action === 'state') formData.append('action', button.dataset.stateAction || '');
            setBusy(button, true);
            try {
                await post(button.dataset.url, formData);
                if (action === 'read') {
                    button.remove();
                    workspace.querySelectorAll('.nc-wa-conversation.is-selected b').forEach((badge) => badge.remove());
                    return;
                }
                reloadAfterSuccess();
            } catch (error) {
                announceError(error.message);
                setBusy(button, false);
            }
        });
    });

    const syncNetworkState = () => {
        workspace.classList.toggle('is-offline', !navigator.onLine);
        workspace.querySelectorAll('button[type="submit"], [data-wa-action]').forEach((control) => {
            if (!navigator.onLine) {
                control.dataset.networkDisabled = String(control.disabled);
                control.disabled = true;
            } else if (control.dataset.networkDisabled === 'false') {
                control.disabled = false;
                delete control.dataset.networkDisabled;
            }
        });
    };
    window.addEventListener('online', syncNetworkState);
    window.addEventListener('offline', syncNetworkState);
    syncNetworkState();
})();
