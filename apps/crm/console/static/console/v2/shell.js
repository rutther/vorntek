(function () {
    'use strict';

    const query = (selector, root = document) => root.querySelector(selector);

    function renderIcons() {
        if (window.lucide && typeof window.lucide.createIcons === 'function') {
            window.lucide.createIcons({
                attrs: { 'stroke-width': 1.8 },
                root: document,
            });
        }
    }

    function initSidebar() {
        // The first vertical slice still owns this behavior inside leads.js.
        // Avoid double-binding while the shared shell is introduced around it.
        if (query('[data-nc-lead-workspace]')) return;

        const shell = query('.nc-shell');
        const button = query('[data-nc-sidebar-collapse]');
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
            const copy = query('span', button);
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
    }

    document.addEventListener('DOMContentLoaded', () => {
        renderIcons();
        initSidebar();
    });
})();
