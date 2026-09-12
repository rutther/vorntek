(function () {
    'use strict';

    const root = document.querySelector('[data-nc-opportunity-page]');
    if (!root) return;

    document.querySelectorAll('[data-nc-submit-on-change]').forEach((control) => {
        control.addEventListener('change', () => {
            if (control.form) control.form.requestSubmit();
        });
    });

    const liveRegion = document.querySelector('[data-nc-live]');
    const announce = (message) => {
        if (liveRegion) liveRegion.textContent = message;
    };

    const setStatus = (form, message, tone) => {
        const status = form.querySelector('[data-nc-form-status]');
        if (!status) return;
        status.hidden = false;
        status.classList.remove('alert-info', 'alert-success', 'alert-danger', 'alert-warning');
        status.classList.add(`alert-${tone}`);
        status.textContent = message;
    };

    const clearStatus = (form) => {
        const status = form.querySelector('[data-nc-form-status]');
        if (!status) return;
        status.hidden = true;
        status.textContent = '';
        status.classList.remove('alert-info', 'alert-success', 'alert-danger', 'alert-warning');
    };

    const clearErrors = (form) => {
        form.querySelectorAll('.is-invalid').forEach((field) => {
            field.classList.remove('is-invalid');
            field.removeAttribute('aria-invalid');
        });
        form.querySelectorAll('[data-error-for]').forEach((feedback) => {
            feedback.textContent = '';
            feedback.style.display = '';
        });
        clearStatus(form);
    };

    const fieldFeedback = (form, fieldName) => {
        return Array.from(form.querySelectorAll('[data-error-for]'))
            .find((node) => node.dataset.errorFor === fieldName) || null;
    };

    const showErrors = (form, errors) => {
        let firstInvalid = null;
        const general = [];
        Object.entries(errors || {}).forEach(([fieldName, values]) => {
            const messages = Array.isArray(values) ? values.map(String) : [String(values)];
            const field = form.elements.namedItem(fieldName);
            const feedback = fieldFeedback(form, fieldName);
            if (field && field instanceof HTMLElement) {
                field.classList.add('is-invalid');
                field.setAttribute('aria-invalid', 'true');
                if (!firstInvalid) firstInvalid = field;
            }
            if (feedback) {
                feedback.textContent = messages.join(' ');
                feedback.style.display = 'block';
            } else {
                general.push(...messages);
            }
        });
        if (general.length) setStatus(form, general.join(' '), 'danger');
        else setStatus(form, '请检查标出的字段后重试。', 'danger');
        if (firstInvalid) firstInvalid.focus();
    };

    const parsePayload = async (response) => {
        try {
            return await response.json();
        } catch (_error) {
            return {ok: false, errors: {__all__: ['服务器返回了无法识别的结果。']}};
        }
    };

    const setBusy = (form, busy) => {
        form.setAttribute('aria-busy', busy ? 'true' : 'false');
        form.querySelectorAll('button, input, select, textarea').forEach((control) => {
            if (control.type === 'hidden') return;
            control.disabled = busy;
        });
        const submit = form.querySelector('[data-nc-submit]');
        if (submit) {
            const label = submit.querySelector('span');
            if (label) {
                if (!label.dataset.idleLabel) label.dataset.idleLabel = label.textContent;
                label.textContent = busy ? '正在提交…' : label.dataset.idleLabel;
            }
        }
    };

    document.querySelectorAll('[data-nc-opportunity-form]').forEach((form) => {
        form.addEventListener('submit', async (event) => {
            event.preventDefault();
            clearErrors(form);
            if (!form.checkValidity()) {
                form.reportValidity();
                setStatus(form, '请先填写必填字段。', 'danger');
                return;
            }
            // FormData ignores disabled controls. Capture the complete user
            // input before setBusy disables fields to prevent duplicate edits.
            const data = new FormData(form);
            const csrf = data.get('csrfmiddlewaretoken') || '';
            setBusy(form, true);
            setStatus(form, '正在保存，请勿重复提交。', 'info');
            try {
                const response = await fetch(form.action, {
                    method: 'POST',
                    body: data,
                    credentials: 'same-origin',
                    headers: {
                        'Accept': 'application/json',
                        'X-Requested-With': 'XMLHttpRequest',
                        'X-CSRFToken': String(csrf),
                    },
                });
                const payload = await parsePayload(response);
                if (response.status === 409) {
                    setStatus(form, '服务器上的销售机会已经变化。请保留当前输入，关闭窗口并刷新后核对最新内容。', 'warning');
                    announce('保存冲突：服务器上的销售机会已经变化。');
                    return;
                }
                if (!response.ok || payload.ok === false) {
                    showErrors(form, payload.errors || {__all__: ['保存失败，请检查输入后重试。']});
                    announce('保存失败，请检查表单错误。');
                    return;
                }
                setStatus(form, '保存成功，正在刷新最新记录。', 'success');
                announce('销售机会已保存。');
                const reloadUrl = root.dataset.ncReloadUrl || window.location.href;
                window.setTimeout(() => {
                    if (reloadUrl.startsWith('/')) window.location.assign(reloadUrl);
                    else window.location.reload();
                }, 500);
            } catch (_error) {
                setStatus(form, '网络结果未知，输入仍保留在当前窗口。请检查连接后安全重试。', 'danger');
                announce('网络结果未知，表单输入已保留。');
            } finally {
                setBusy(form, false);
            }
        });
    });

    const stageTarget = document.querySelector('[data-nc-stage-target]');
    if (stageTarget) {
        const stageForm = stageTarget.form;
        const reason = stageForm ? stageForm.elements.namedItem('reason') : null;
        const state = stageForm ? stageForm.querySelector('[data-nc-reason-state]') : null;
        const updateReasonState = () => {
            const selected = stageTarget.selectedOptions[0];
            const required = selected && selected.dataset.requiresReason === 'true';
            if (reason && reason instanceof HTMLTextAreaElement) reason.required = Boolean(required);
            if (state) state.textContent = required ? '必填' : '可选';
        };
        stageTarget.addEventListener('change', updateReasonState);
        updateReasonState();
    }

    document.querySelectorAll('[data-nc-opportunity-modal]').forEach((modal) => {
        let trigger = null;
        modal.addEventListener('show.bs.modal', (event) => {
            trigger = event.relatedTarget instanceof HTMLElement ? event.relatedTarget : document.activeElement;
        });
        modal.addEventListener('shown.bs.modal', () => {
            const first = modal.querySelector('input:not([type="hidden"]), select, textarea, button:not(.btn-close)');
            if (first instanceof HTMLElement) first.focus();
        });
        modal.addEventListener('hidden.bs.modal', () => {
            const form = modal.querySelector('form');
            if (form) {
                clearErrors(form);
                setBusy(form, false);
            }
            // Bootstrap finishes tearing down the focus trap after the hidden
            // event. Restore focus on the next frame so keyboard users return
            // to the exact action that opened the dialog instead of <body>.
            if (trigger instanceof HTMLElement) {
                window.requestAnimationFrame(() => trigger.focus({preventScroll: true}));
            }
        });
    });
}());
