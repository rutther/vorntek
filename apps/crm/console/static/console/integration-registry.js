(() => {
  const form = document.querySelector('[data-ir-create]');
  if (!form) return;
  let submitting = false;
  const initial = new URLSearchParams(new FormData(form)).toString();
  const dirty = () => new URLSearchParams(new FormData(form)).toString() !== initial;
  const button = form.querySelector('button[type=submit]');
  const status = form.querySelector('[data-ir-status]');
  form.addEventListener('input', () => { status.textContent = dirty() ? '待创建 · 不会开启发送' : '不会开启发送'; });
  form.addEventListener('submit', event => {
    if (submitting) { event.preventDefault(); return; }
    submitting = true;
    form.setAttribute('aria-busy', 'true');
    button.disabled = true;
    button.textContent = '正在创建…';
  });
  window.addEventListener('beforeunload', event => {
    if (!submitting && dirty()) { event.preventDefault(); event.returnValue = ''; }
  });
  window.addEventListener('pageshow', () => {
    submitting = false;
    form.removeAttribute('aria-busy');
    button.disabled = false;
    button.textContent = '创建并配置';
  });
  form.querySelector('.ir-errors')?.focus();
})();
