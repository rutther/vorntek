(() => {
    'use strict';

    document.querySelectorAll('[data-password-toggle]').forEach((button) => {
        button.addEventListener('click', () => {
            const input = button.closest('.password-control')?.querySelector('input');
            if (!input) return;
            const showing = input.type === 'text';
            input.type = showing ? 'password' : 'text';
            button.textContent = showing ? '显示' : '隐藏';
            button.setAttribute('aria-label', `${showing ? '显示' : '隐藏'}${input.name === 'confirm_password' ? '确认密码' : '新密码'}`);
        });
    });

    const receipt = document.querySelector('[data-credential-receipt]');
    if (!receipt) return;

    const username = receipt.querySelector('[data-credential-username]')?.textContent || '';
    const passwordNode = receipt.querySelector('[data-credential-password]');
    let password = passwordNode?.textContent || '';
    let cleared = false;
    const status = receipt.querySelector('[data-copy-status]');

    const clearCredentials = () => {
        cleared = true;
        password = '';
        if (passwordNode) passwordNode.textContent = '';
        receipt.remove();
    };

    receipt.querySelector('[data-credential-dismiss]')?.addEventListener('click', clearCredentials);
    window.addEventListener('pagehide', clearCredentials, { once: true });

    receipt.querySelectorAll('[data-copy-credential]').forEach((button) => {
        button.addEventListener('click', async () => {
            if (cleared) return;
            const target = button.dataset.copyCredential;
            const value = target === 'username'
                ? username
                : target === 'password'
                    ? password
                    : `登录名：${username}\n临时密码：${password}`;
            try {
                await navigator.clipboard.writeText(value);
                if (cleared) return;
                if (status) status.textContent = '已复制。请通过安全渠道交付，不要发送到公开群聊。';
            } catch (_error) {
                if (cleared) return;
                if (status) status.textContent = '浏览器未允许自动复制，请手动选中文本。';
            }
        });
    });
})();
