(function () {
    'use strict';

    const shell = document.querySelector('.nc-shell');
    const button = document.querySelector('[data-nc-sidebar-collapse]');
    if (!shell || !button) return;

    let collapsed = false;
    try {
        collapsed = window.localStorage.getItem('nc-sidebar-collapsed') === '1';
    } catch (_) {
        collapsed = false;
    }

    const update = () => {
        const label = collapsed ? '展开主导航' : '收起主导航';
        shell.classList.toggle('is-sidebar-collapsed', collapsed);
        button.setAttribute('aria-pressed', String(collapsed));
        button.setAttribute('aria-label', label);
        button.setAttribute('title', label);
        const copy = button.querySelector('span');
        if (copy) copy.textContent = collapsed ? '展开菜单' : '收起菜单';
    };

    update();
    button.addEventListener('click', () => {
        collapsed = !shell.classList.contains('is-sidebar-collapsed');
        try {
            window.localStorage.setItem('nc-sidebar-collapsed', collapsed ? '1' : '0');
        } catch (_) {
            // A disabled storage backend must not break navigation.
        }
        update();
    });
})();
