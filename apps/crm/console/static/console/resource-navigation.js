(() => {
  const shell = document.querySelector('.nc-shell');
  const open = document.querySelector('[data-resource-nav-open]');
  const close = document.querySelector('[data-resource-nav-close]');
  const backdrop = document.querySelector('[data-resource-nav-backdrop]');
  const sidebar = document.querySelector('.nc-sidebar');
  const app = document.querySelector('.nc-app');
  if (!open || !close || !sidebar || !app || !backdrop) return;
  const mobile = window.matchMedia('(max-width:1120px)');
  let expanded = false;
  const update = (value, restoreFocus = true) => {
    expanded = Boolean(value && mobile.matches);
    shell.classList.toggle('is-resource-nav-open', expanded);
    open.setAttribute('aria-expanded', String(expanded));
    backdrop.hidden = !expanded;
    app.toggleAttribute('inert', expanded);
    sidebar.toggleAttribute('inert', mobile.matches && !expanded);
    if (expanded) {
      sidebar.setAttribute('role', 'dialog');
      sidebar.setAttribute('aria-modal', 'true');
      close.focus();
    } else {
      sidebar.removeAttribute('role');
      sidebar.removeAttribute('aria-modal');
      if (restoreFocus && mobile.matches) open.focus();
    }
  };
  open.addEventListener('click', () => update(true));
  close.addEventListener('click', () => update(false));
  backdrop.addEventListener('click', () => update(false));
  document.addEventListener('keydown', event => {
    if (!expanded) return;
    if (event.key === 'Escape') { event.preventDefault(); update(false); }
    if (event.key === 'Tab') {
      const controls = [...sidebar.querySelectorAll('a[href],button:not([disabled]),[tabindex="0"]')]
        .filter(el => el.getClientRects().length);
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }
  });
  mobile.addEventListener('change', () => update(false, false));
  window.addEventListener('pageshow', () => update(false, false));
  update(false, false);
})();
