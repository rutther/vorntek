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

    document.addEventListener('DOMContentLoaded', () => {
        renderIcons();
    });
})();
