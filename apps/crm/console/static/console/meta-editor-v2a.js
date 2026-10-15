(() => {
  const root = document.querySelector('.mi-editor');
  if (!root) return;
  const forms = [...root.querySelectorAll('[data-mi-edit]')];
  const baseline = new Map(forms.map(form => [form, new URLSearchParams(new FormData(form)).toString()]));
  const dirty = form => new URLSearchParams(new FormData(form)).toString() !== baseline.get(form);
  const saved = root.dataset.credentialSaved === 'true';
  const updates = [];
  let leaving = false;
  const pending = (form, event) => {
    if (form.getAttribute('aria-busy') === 'true') {event.preventDefault(); return;}
    leaving = true;
    form.setAttribute('aria-busy', 'true');
    form.querySelectorAll('button[type=submit], button:not([type])').forEach(button => {
      button.dataset.miLabel = button.textContent;
      button.textContent = form.hasAttribute('data-mi-diagnose') ? '正在检查…' : '正在保存…';
      button.disabled = true;
    });
  };
  forms.forEach(form => {
    const reset = document.createElement('button');
    const credential = form.hasAttribute('data-mi-credentials');
    reset.type = 'reset'; reset.className = 'mi-button'; reset.textContent = credential && saved ? '取消更换' : '撤销修改'; reset.hidden = true;
    const actions = form.querySelector('.mi-actions');
    actions?.insertBefore(reset, actions.querySelector('button'));
    const status = form.querySelector('[data-mi-status]');
    const failed = Boolean(root.dataset.errorSection) && root.dataset.errorSection === form.querySelector('[name=_section]')?.value;
    const originalStatus = failed ? '未保存 · 请修正后重试' : status?.textContent || '无修改';
    const submit = form.querySelector('button[type=submit], button:not([type])');
    const originallyDisabled = Boolean(submit?.disabled);
    const disclosure = form.closest('.mi-setting-row');
    const badge = disclosure?.querySelector(':scope > summary .mi-status');
    const badgeText = failed ? '未保存' : badge?.textContent;
    const badgeClass = failed ? 'mi-status mi-warning' : badge?.className;
    const token = form.querySelector('#id_primary_secret');
    const ref = form.querySelector('#id_secret_ref');
    const originalRef = ref?.value || '';
    const tokenField = form.querySelector('[data-mi-token-field]');
    const tokenLabel = form.querySelector('[data-mi-token-label]');
    const requiredLabel = form.querySelector('label[for=id_primary_secret] small');
    const savedPanel = credential ? root.querySelector('[data-mi-saved]') : null;
    const savedTitle = savedPanel?.querySelector('strong');
    const savedCopy = savedPanel?.querySelector('p');
    const originalSavedCopy = savedCopy?.textContent;
    const hint = credential ? root.querySelector('.mi-next > p') : null;
    const originalHint = hint?.textContent;
    const update = () => {
      const changed = dirty(form);
      reset.hidden = !changed && !(credential && saved);
      if (status) {status.setAttribute('aria-live','polite');status.textContent=changed?'有修改 · 待保存':originalStatus;status.classList.toggle('mi-pending',changed);}
      if (badge) {badge.textContent=changed?'待保存':badgeText;badge.className=changed?'mi-status mi-warning':badgeClass;}
      if (submit) submit.disabled = originallyDisabled || !changed;
      if (credential && token) {
        const hasToken = Boolean(token.value.trim());
        const hasReference = Boolean(ref?.value.trim()) && ref.value !== originalRef;
        token.required = !hasReference;
        token.setAttribute('aria-required', String(!hasReference));
        if (requiredLabel) {requiredLabel.textContent=hasReference?'已用已有密钥':saved?'更换时必填':'必填';requiredLabel.classList.toggle('mi-required',!hasReference);}
        if (savedTitle) savedTitle.textContent=changed?'原凭据仍保留':'凭据已保存';
        if (savedCopy) savedCopy.textContent=changed?'新凭据待保存；取消不会影响原凭据。':originalSavedCopy;
        if (hint) hint.textContent=changed?'有未保存的凭据修改。先保存或取消，再检查连接。':originalHint;
        if (submit) submit.disabled = originallyDisabled || !(hasToken || hasReference);
        if (tokenField && tokenLabel) {
          const serverError = Boolean(form.querySelector('#mi-token-error')) && !changed;
          tokenField.dataset.state = hasToken || hasReference ? 'pending' : serverError ? 'error' : 'empty';
          tokenLabel.textContent=hasToken?'已填写 · 待保存':hasReference?'使用已有密钥 · 待保存':serverError?'未保存，请重新填写':'待填写';
        }
        if (changed) token.removeAttribute('aria-invalid');
      }
    };
    updates.push(update);
    update();
    form.addEventListener('input', update);
    form.addEventListener('change', update);
    // Native reset restores controls after its event. A microtask can run too
    // early in a real browser, leaving feedback stuck on "待保存".
    form.addEventListener('reset', () => setTimeout(() => {
      update();
      if (credential && saved) {
        const details=form.closest('details');
        if(details){details.open=false;details.querySelector('summary')?.focus();}
      }
    }, 0));
    token?.addEventListener('invalid', () => {
      token.setAttribute('aria-invalid','true');
      if(tokenField) tokenField.dataset.state='error';
      if(tokenLabel) tokenLabel.textContent='请填写访问令牌，或使用下方已有密钥';
    });
    form.addEventListener('submit', event => {
      if (forms.some(other => other !== form && dirty(other))) {
        event.preventDefault();
        const status = form.querySelector('[data-mi-status]');
        if (status) {status.textContent = '其他区域也有修改。请先用“撤销修改”保留单一区域，再保存。'; status.setAttribute('role','alert');}
        return;
      }
      pending(form, event);
    });
  });
  root.querySelector('[data-mi-diagnose]')?.addEventListener('submit', event => {
    if (forms.some(dirty)) {
      event.preventDefault();
      const note = root.querySelector('#mi-diagnostic-note');
      note.textContent = '有未保存修改，请先保存或撤销，再检查连接。';
      note.setAttribute('role', 'alert');
      forms.find(dirty)?.querySelector('input:not([type=hidden]), textarea')?.focus();
    } else {pending(event.currentTarget, event);}
  });
  window.addEventListener('pageshow', () => {
    leaving = false;
    root.querySelectorAll('form[aria-busy]').forEach(form => form.removeAttribute('aria-busy'));
    root.querySelectorAll('button[data-mi-label]').forEach(button => {
      button.textContent = button.dataset.miLabel; button.disabled = false; delete button.dataset.miLabel;
    });
    updates.forEach(update=>update());
  });
  window.addEventListener('beforeunload', event => {
    if (!leaving && forms.some(dirty)) {event.preventDefault(); event.returnValue = '';}
  });
  const errors = root.querySelector('.mi-errors');
  errors?.focus();
  errors?.querySelectorAll('a').forEach(link => link.addEventListener('click', () => {
    const field = document.getElementById(link.hash.slice(1));
    for (let parent = field?.parentElement; parent; parent = parent.parentElement) {
      if (parent.tagName === 'DETAILS') parent.open = true;
    }
    field?.focus();
  }));
})();
