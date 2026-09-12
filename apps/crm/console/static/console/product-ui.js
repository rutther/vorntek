(function () {
    const body = document.body;
    const app = document.getElementById('app');
    const mobileButton = document.getElementById('mobileNavButton');
    const sidebarMask = document.getElementById('sidebarMask');
    const toggleNav = document.getElementById('toggleNav');
    const legacyPage = Boolean(document.getElementById('page-payload'));

    function closeMobileNav() {
        body.classList.remove('nav-open');
        mobileButton?.setAttribute('aria-expanded', 'false');
    }

    mobileButton?.addEventListener('click', function () {
        const willOpen = !body.classList.contains('nav-open');
        body.classList.toggle('nav-open', willOpen);
        mobileButton.setAttribute('aria-expanded', String(willOpen));
    });

    sidebarMask?.addEventListener('click', closeMobileNav);

    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') closeMobileNav();
    });

    window.addEventListener('resize', function () {
        if (window.innerWidth > 1000) closeMobileNav();
    });

    document.querySelectorAll('.sidebar .nav-item').forEach(function (link) {
        link.addEventListener('click', closeMobileNav);
    });

    if (!legacyPage) {
        toggleNav?.addEventListener('click', function () {
            app?.classList.toggle('is-collapsed');
        });
    }
}());
