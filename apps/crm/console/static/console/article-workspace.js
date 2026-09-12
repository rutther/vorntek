(function () {
    const root = document.getElementById('article-workspace-root');
    const dataNode = document.getElementById('article-workspace-data');
    if (!root || !dataNode) {
        return;
    }

    const data = JSON.parse(dataNode.textContent || '{}');
    const categories = data.categories || [];
    const categoryMap = new Map(categories.map((item) => [String(item.id), item]));
    const childrenMap = new Map();
    const csrfToken = document.querySelector('#csrf-template input[name=csrfmiddlewaretoken]')?.value || '';

    const state = {
        search: '',
        status: 'all',
        seo: 'all',
        scope: data.selection?.scope === 'uncategorized' ? 'uncategorized' : 'all',
        selectedTaxa: new Set((data.selection?.selectedTaxa || []).map(String)),
        expanded: new Set(),
        panel: data.initialPanel || '',
        page: 1,
        pageSize: data.pagination?.pageSize || 50,
    };

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) {
            node.className = className;
        }
        if (text !== undefined && text !== null) {
            node.textContent = text;
        }
        return node;
    }

    function clear(node) {
        while (node.firstChild) {
            node.removeChild(node.firstChild);
        }
    }

    function childItems(parentId) {
        const key = parentId == null ? 'root' : String(parentId);
        if (!childrenMap.has(key)) {
            const items = categories.filter((item) => {
                const itemParent = item.parentId == null ? '' : String(item.parentId);
                const parentKey = parentId == null ? '' : String(parentId);
                return itemParent === parentKey;
            });
            childrenMap.set(key, items);
        }
        return childrenMap.get(key) || [];
    }

    function ensureExpanded() {
        if (state.expanded.size) {
            return;
        }
        childItems(null).forEach((node) => state.expanded.add(String(node.id)));
    }

    function anyCategorySelected() {
        return state.selectedTaxa.size > 0;
    }

    function resetListState() {
        state.status = 'all';
        state.seo = 'all';
        state.search = '';
        state.page = 1;
    }

    function clearCategorySelection() {
        state.scope = 'all';
        state.selectedTaxa.clear();
        state.page = 1;
    }

    function setUncategorizedScope() {
        state.scope = 'uncategorized';
        state.selectedTaxa.clear();
        state.page = 1;
    }

    function toggleTaxon(id) {
        const key = String(id);
        state.scope = 'all';
        if (state.selectedTaxa.has(key)) {
            state.selectedTaxa.delete(key);
        } else {
            state.selectedTaxa.add(key);
        }
        state.page = 1;
    }

    function selectedCategories() {
        return Array.from(state.selectedTaxa)
            .map((id) => categoryMap.get(id))
            .filter(Boolean);
    }

    function selectedCategoryLabel() {
        const selected = selectedCategories();
        if (state.scope === 'uncategorized') {
            return '未分类文章';
        }
        if (!selected.length) {
            return '全部文章';
        }
        if (selected.length === 1) {
            return selected[0].trailLabel || selected[0].name;
        }
        return `已选 ${selected.length} 个分类`;
    }

    function matchesTaxon(article) {
        if (state.scope === 'uncategorized') {
            return !article.categoryId;
        }
        if (!anyCategorySelected()) {
            return true;
        }
        const trailIds = (article.categoryTrailIds || []).map(String);
        return Array.from(state.selectedTaxa).some((id) => trailIds.includes(id));
    }

    function matchesSeo(article) {
        if (state.seo === 'all') {
            return true;
        }
        if (state.seo === 'incomplete') {
            return article.seoKey === 'warning' || article.seoKey === 'critical';
        }
        return article.seoKey === state.seo;
    }

    function matchesSearch(article) {
        if (!state.search) {
            return true;
        }
        const haystack = [
            article.title,
            article.excerpt,
            article.path,
            article.categoryPath,
        ].join(' ').toLowerCase();
        return haystack.includes(state.search.toLowerCase());
    }

    function filteredArticles() {
        return (data.articles || []).filter((article) => {
            if (state.status !== 'all' && article.statusKey !== state.status) {
                return false;
            }
            if (!matchesSeo(article)) {
                return false;
            }
            if (!matchesTaxon(article)) {
                return false;
            }
            return matchesSearch(article);
        });
    }

    function totalPages() {
        return Math.max(1, Math.ceil(filteredArticles().length / state.pageSize));
    }

    function normalizePage() {
        const maxPage = totalPages();
        if (state.page > maxPage) {
            state.page = maxPage;
        }
        if (state.page < 1) {
            state.page = 1;
        }
    }

    function pagedArticles() {
        const rows = filteredArticles();
        const start = (state.page - 1) * state.pageSize;
        return rows.slice(start, start + state.pageSize);
    }

    function badgeClass(tone) {
        if (tone === 'green') return 'article-badge article-badge--green';
        if (tone === 'amber') return 'article-badge article-badge--amber';
        if (tone === 'red') return 'article-badge article-badge--red';
        return 'article-badge article-badge--slate';
    }

    function postForm(action, label, className) {
        const form = el('form', 'article-inlineform');
        form.method = 'post';
        form.action = action;

        const csrf = document.createElement('input');
        csrf.type = 'hidden';
        csrf.name = 'csrfmiddlewaretoken';
        csrf.value = csrfToken;

        const next = document.createElement('input');
        next.type = 'hidden';
        next.name = 'next';
        next.value = window.location.pathname + window.location.search;

        const button = el('button', className || 'article-ghost', label);
        button.type = 'submit';

        form.append(csrf, next, button);
        return form;
    }

    function renderLocaleSwitch() {
        const shell = el('div', 'article-toolbar__locales');
        (data.localeActions || []).forEach((action) => {
            const link = el('a', `article-chip${action.active ? ' is-active' : ''}`, action.label);
            link.href = action.href;
            if (action.active) {
                link.setAttribute('aria-current', 'page');
            }
            shell.append(link);
        });

        if (data.permissions?.canManageLocales) {
            const manage = el('button', 'article-ghost', '语言管理');
            manage.type = 'button';
            manage.addEventListener('click', () => {
                state.panel = 'locale-manager';
                render();
            });
            shell.append(manage);
        }
        return shell;
    }

    function renderTopActions() {
        const shell = el('div', 'article-toolbar__actions');
        if (data.toolbarActions?.primary) {
            const primary = el('a', 'article-primary', data.toolbarActions.primary.label);
            primary.href = data.toolbarActions.primary.href;
            shell.append(primary);
        }
        (data.toolbarActions?.secondary || []).forEach((action) => {
            const link = el('a', 'article-ghost', action.label);
            link.href = action.href;
            shell.append(link);
        });
        return shell;
    }

    function renderFilterGroup(label, options, activeKey, onSelect) {
        const group = el('div', 'article-filtergroup');
        group.setAttribute('role', 'group');
        group.setAttribute('aria-label', `${label}筛选`);
        group.append(el('span', 'article-filtergroup__label', label));
        const chips = el('div', 'article-filtergroup__chips');

        options.forEach((option) => {
            const button = el('button', `article-filterchip${activeKey === option.key ? ' is-active' : ''}`);
            button.type = 'button';
            button.setAttribute('aria-pressed', String(activeKey === option.key));
            button.append(el('span', 'article-filterchip__label', option.label));
            button.append(el('span', 'article-filterchip__count', String(option.count)));
            button.addEventListener('click', () => onSelect(option.key));
            chips.append(button);
        });

        group.append(chips);
        return group;
    }

    function renderFilters() {
        const shell = el('section', 'article-filters');
        shell.append(
            renderFilterGroup('状态', data.filters?.status || [], state.status, (key) => {
                state.status = key;
                state.page = 1;
                render();
            }),
        );

        shell.append(
            renderFilterGroup('SEO', data.filters?.seo || [], state.seo, (key) => {
                state.seo = key;
                state.page = 1;
                render();
            }),
        );

        const utilities = el('div', 'article-filterrow__utilities');
        const search = el('label', 'article-search');
        search.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"></circle><path d="m20 20-3.5-3.5"></path></svg>';
        const input = document.createElement('input');
        input.type = 'search';
        input.placeholder = '搜索标题、摘要、路径或分类';
        input.value = state.search;
        input.addEventListener('input', () => {
            state.search = input.value.trim();
            state.page = 1;
            const caret = input.selectionStart;
            render();
            const replacement = root.querySelector('.article-search input');
            if (replacement) {
                replacement.focus({ preventScroll: true });
                if (caret !== null) {
                    replacement.setSelectionRange(caret, caret);
                }
            }
        });
        search.append(input);
        utilities.append(search);
        if (state.status !== 'all' || state.seo !== 'all' || state.search || state.scope === 'uncategorized' || anyCategorySelected()) {
            const reset = el('button', 'article-ghost', '清空筛选');
            reset.type = 'button';
            reset.addEventListener('click', () => {
                resetListState();
                clearCategorySelection();
                render();
            });
            utilities.append(reset);
        }
        shell.append(utilities);
        return shell;
    }

    function renderScopeButton(label, active, count, onClick) {
        const button = el('button', `taxonomy-scope${active ? ' is-active' : ''}`);
        button.type = 'button';
        button.setAttribute('aria-pressed', String(active));
        button.addEventListener('click', onClick);
        button.append(el('span', 'taxonomy-scope__label', label), el('span', 'taxonomy-scope__count', String(count)));
        return button;
    }

    function renderTreeNode(node) {
        const wrap = el('div', 'taxonomy-node');
        const row = el('div', 'taxonomy-node__row');
        row.style.setProperty('--depth', String(node.depth || 0));

        if (node.childCount > 0) {
            const toggle = el('button', `taxonomy-node__toggle${state.expanded.has(String(node.id)) ? '' : ' is-collapsed'}`);
            toggle.type = 'button';
            toggle.setAttribute('aria-label', state.expanded.has(String(node.id)) ? '收起子分类' : '展开子分类');
            toggle.setAttribute('aria-expanded', String(state.expanded.has(String(node.id))));
            toggle.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m6 9 6 6 6-6"></path></svg>';
            toggle.addEventListener('click', (event) => {
                event.stopPropagation();
                const key = String(node.id);
                if (state.expanded.has(key)) {
                    state.expanded.delete(key);
                } else {
                    state.expanded.add(key);
                }
                render();
            });
            row.append(toggle);
        } else {
            row.append(el('span', 'taxonomy-node__spacer'));
        }

        const checkboxWrap = el('label', 'taxonomy-node__check');
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.checked = state.selectedTaxa.has(String(node.id));
        checkbox.setAttribute('aria-label', `选择分类 ${node.trailLabel || node.name}`);
        checkbox.addEventListener('change', () => {
            toggleTaxon(node.id);
            render();
        });
        const indicator = el('span', 'taxonomy-node__checkmark');
        checkboxWrap.append(checkbox, indicator);
        row.append(checkboxWrap);

        const button = el('button', `taxonomy-node__button${state.selectedTaxa.has(String(node.id)) ? ' is-active' : ''}`);
        button.type = 'button';
        button.setAttribute('aria-pressed', String(state.selectedTaxa.has(String(node.id))));
        button.addEventListener('click', () => {
            toggleTaxon(node.id);
            render();
        });

        const copy = el('span', 'taxonomy-node__copy');
        copy.append(el('strong', '', node.name));
        copy.append(el('span', '', `${node.level} 级 · ${node.branchCount} 篇`));
        button.append(copy);

        row.append(button, el('span', 'taxonomy-node__count', String(node.branchCount)));
        wrap.append(row);

        if (node.childCount > 0 && state.expanded.has(String(node.id))) {
            const children = el('div', 'taxonomy-node__children');
            childItems(node.id).forEach((child) => children.append(renderTreeNode(child)));
            wrap.append(children);
        }

        return wrap;
    }

    function renderTaxonomyRail() {
        ensureExpanded();
        const aside = el('aside', 'taxonomy-rail');
        aside.setAttribute('aria-label', '文章分类筛选');

        const head = el('div', 'taxonomy-rail__head');
        const title = el('div', 'taxonomy-rail__title');
        title.append(el('strong', '', '分类筛选'));
        title.append(el('span', '', anyCategorySelected() ? `已选 ${state.selectedTaxa.size} 个分类` : '按分类范围缩小文章结果'));

        const actions = el('div', 'taxonomy-rail__actions');
        if (data.permissions?.canWrite) {
            const manage = el('button', 'article-ghost', '分类管理');
            manage.type = 'button';
            manage.addEventListener('click', () => {
                state.panel = 'taxonomy-manager';
                render();
            });
            actions.append(manage);
        }

        if (state.scope === 'uncategorized' || anyCategorySelected()) {
            const clearButton = el('button', 'article-ghost', '清空');
            clearButton.type = 'button';
            clearButton.addEventListener('click', () => {
                clearCategorySelection();
                render();
            });
            actions.append(clearButton);
        }

        head.append(title, actions);

        const scopes = el('div', 'taxonomy-rail__scopes');
        scopes.append(
            renderScopeButton('全部文章', state.scope === 'all' && !anyCategorySelected(), data.stats?.total || 0, () => {
                clearCategorySelection();
                render();
            }),
            renderScopeButton('未分类', state.scope === 'uncategorized', data.stats?.uncategorized || 0, () => {
                setUncategorizedScope();
                render();
            }),
        );

        const tree = el('div', 'taxonomy-rail__tree');
        childItems(null).forEach((node) => tree.append(renderTreeNode(node)));

        aside.append(head, scopes, tree);
        return aside;
    }

    function renderTable(rows) {
        if (!rows.length) {
            const empty = el('div', 'article-empty');
            empty.append(el('strong', '', '当前筛选结果为空'));
            empty.append(el('span', '', '请调整分类、状态、SEO 或搜索条件。'));
            return empty;
        }

        const shell = el('div', 'article-table');
        const table = document.createElement('table');

        const colgroup = document.createElement('colgroup');
        ['', 'article-col--category', 'article-col--status', 'article-col--seo', 'article-col--updated', 'article-col--actions']
            .forEach((className) => {
                const col = document.createElement('col');
                if (className) {
                    col.className = className;
                }
                colgroup.append(col);
            });
        table.innerHTML = `
            <thead>
                <tr>
                    <th>文章</th>
                    <th class="article-col--category">分类</th>
                    <th class="article-col--status">状态</th>
                    <th class="article-col--seo">SEO</th>
                    <th class="article-col--updated">更新</th>
                    <th class="article-col--actions">操作</th>
                </tr>
            </thead>
        `;
        table.prepend(colgroup);

        const body = document.createElement('tbody');

        rows.forEach((row) => {
            const tr = document.createElement('tr');

            const articleCell = document.createElement('td');
            const articleWrap = el('div', 'article-row__main');
            const articleTitle = el(row.editHref ? 'a' : 'strong', 'article-row__title', row.title);
            if (row.editHref) {
                articleTitle.href = row.editHref;
            }
            articleWrap.append(articleTitle);
            articleWrap.append(el('span', 'article-row__path', row.path));
            if (row.excerpt) {
                articleCell.title = row.excerpt;
            }
            articleCell.append(articleWrap);

            const categoryCell = document.createElement('td');
            categoryCell.className = 'article-col--category';
            const category = el('span', 'article-row__category', row.categoryPath);
            category.title = row.categoryPath;
            categoryCell.append(category);

            const statusCell = document.createElement('td');
            statusCell.className = 'article-col--status';
            statusCell.append(el('span', badgeClass(row.statusTone), row.statusLabel));

            const seoCell = document.createElement('td');
            seoCell.className = 'article-col--seo';
            const seoBadge = el('span', badgeClass(row.seoTone), row.seoLabel);
            if ((row.seoMissing || []).length) {
                seoBadge.title = `缺少：${row.seoMissing.join('、')}`;
            }
            seoCell.append(seoBadge);

            const updatedCell = document.createElement('td');
            updatedCell.className = 'article-col--updated';
            updatedCell.append(el('span', 'article-row__updated', row.updatedAt));

            const actionCell = document.createElement('td');
            actionCell.className = 'article-col--actions';
            const actions = el('div', 'article-row__actions');
            const preview = el('a', 'article-row__action', '预览');
            preview.href = row.previewHref;
            preview.target = '_blank';
            preview.rel = 'noreferrer';
            if (row.editHref) {
                const edit = el('a', 'article-row__action', '编辑');
                edit.href = row.editHref;
                actions.append(edit);
            }
            actions.append(preview);
            actionCell.append(actions);

            tr.append(articleCell, categoryCell, statusCell, seoCell, updatedCell, actionCell);
            body.append(tr);
        });

        table.append(body);
        shell.append(table);
        return shell;
    }

    function renderPagination() {
        normalizePage();
        const total = filteredArticles().length;
        const pageCount = totalPages();
        const start = total ? ((state.page - 1) * state.pageSize) + 1 : 0;
        const end = Math.min(state.page * state.pageSize, total);
        const footer = el('div', 'article-content__footer');
        footer.append(el('span', 'article-pagination__meta', `${start}-${end} / ${total} 篇`));

        const actions = el('div', 'article-pagination__actions');

        const pageSizeLabel = el('label', 'article-pagesize');
        pageSizeLabel.append(el('span', '', '每页'));
        const pageSizeSelect = document.createElement('select');
        [20, 50, 100].forEach((size) => {
            const option = document.createElement('option');
            option.value = String(size);
            option.textContent = String(size);
            option.selected = state.pageSize === size;
            pageSizeSelect.append(option);
        });
        pageSizeSelect.addEventListener('change', () => {
            state.pageSize = Number(pageSizeSelect.value) || 50;
            state.page = 1;
            render();
        });
        pageSizeLabel.append(pageSizeSelect);
        actions.append(pageSizeLabel);

        const prev = el('button', 'article-ghost', '上一页');
        prev.type = 'button';
        prev.disabled = state.page <= 1;
        prev.addEventListener('click', () => {
            if (state.page <= 1) {
                return;
            }
            state.page -= 1;
            render();
        });

        const pages = el('div', 'article-pagination__pages');
        let firstPage = Math.max(1, state.page - 2);
        let lastPage = Math.min(pageCount, firstPage + 4);
        firstPage = Math.max(1, lastPage - 4);
        for (let page = firstPage; page <= lastPage; page += 1) {
            const button = el('button', `article-pagebutton${page === state.page ? ' is-active' : ''}`, String(page));
            button.type = 'button';
            button.setAttribute('aria-label', `第 ${page} 页`);
            button.addEventListener('click', () => {
                state.page = page;
                render();
            });
            pages.append(button);
        }

        const next = el('button', 'article-ghost', '下一页');
        next.type = 'button';
        next.disabled = state.page >= pageCount;
        next.addEventListener('click', () => {
            if (state.page >= pageCount) {
                return;
            }
            state.page += 1;
            render();
        });

        actions.append(prev, pages, next);
        footer.append(actions);
        return footer;
    }

    function renderContent() {
        normalizePage();
        const section = el('section', 'article-content');

        const head = el('div', 'article-content__head');
        const summary = el('div', 'article-content__summary');
        summary.append(el('h2', '', selectedCategoryLabel()));
        summary.append(el('p', '', `当前结果 ${filteredArticles().length} 篇`));
        head.append(summary);

        section.append(head, renderTable(pagedArticles()), renderPagination());
        return section;
    }

    function renderTaxonomyManagerPanel() {
        if (!data.permissions?.canWrite) {
            return null;
        }
        const panel = el('aside', 'article-panel is-open');

        const head = el('div', 'article-panel__head');
        const title = el('div', 'article-panel__title');
        title.append(el('strong', '', '分类管理'));
        title.append(el('span', '', '左侧树负责筛选；这里负责新建、编辑和维护分类结构。'));
        const close = el('button', 'article-ghost', '关闭');
        close.type = 'button';
        close.addEventListener('click', () => {
            state.panel = '';
            render();
        });
        head.append(title, close);

        const actions = el('div', 'article-panel__actions');
        if (data.taxonomyActions?.rootCreateHref) {
            const createRoot = el('a', 'article-primary', '新建一级分类');
            createRoot.href = data.taxonomyActions.rootCreateHref;
            actions.append(createRoot);
        }

        const list = el('div', 'article-panel__list');
        categories.forEach((row) => {
            const item = el('div', 'article-panel__item');
            item.style.setProperty('--depth', String(row.depth || 0));

            const copy = el('div', 'article-panel__item-copy');
            copy.append(el('strong', '', row.name));
            copy.append(el('span', '', `${row.level} 级 · 直接 ${row.directCount} 篇 · 含子级 ${row.branchCount} 篇`));

            const itemActions = el('div', 'article-panel__item-actions');
            if (row.editHref) {
                const edit = el('a', 'article-ghost', '编辑');
                edit.href = row.editHref;
                itemActions.append(edit);
            }

            if (row.level < 3 && row.newChildHref) {
                const createChild = el('a', 'article-ghost', '新建子级');
                createChild.href = row.newChildHref;
                itemActions.append(createChild);
            }

            item.append(copy, itemActions);
            list.append(item);
        });

        panel.append(head, actions, list);
        return panel;
    }

    function renderLocaleManagerPanel() {
        if (!data.permissions?.canManageLocales) {
            return null;
        }
        const panel = el('aside', 'article-panel is-open');

        const head = el('div', 'article-panel__head');
        const title = el('div', 'article-panel__title');
        title.append(el('strong', '', data.localeManager?.title || '语言管理'));
        title.append(el('span', '', data.localeManager?.summary || '在这里新增语言、切换默认语言、启用或停用语言。'));
        const close = el('button', 'article-ghost', '关闭');
        close.type = 'button';
        close.addEventListener('click', () => {
            state.panel = '';
            render();
        });
        head.append(title, close);

        const actions = el('div', 'article-panel__actions');
        if (data.localeManager?.createHref) {
            const create = el('a', 'article-primary', '新增语言');
            create.href = data.localeManager.createHref;
            actions.append(create);
        }

        const list = el('div', 'article-panel__list');
        (data.localeManager?.items || []).forEach((item) => {
            const row = el('div', 'article-panel__item');
            const copy = el('div', 'article-panel__item-copy');
            const heading = `${item.label} · ${String(item.code).toUpperCase()}`;
            const meta = `${item.direction === 'rtl' ? 'RTL' : 'LTR'} · ${item.enabled ? '已启用' : '已停用'}${item.isDefault ? ' · 默认语言' : ''}`;
            copy.append(el('strong', '', heading));
            copy.append(el('span', '', meta));

            const actionsWrap = el('div', 'article-panel__item-actions');
            const jump = item.switchHref
                ? el('a', 'article-ghost', item.code === data.locale?.code ? '当前语言' : '切换')
                : el('span', 'article-ghost is-disabled', item.enabled ? '无查看权限' : '已停用');
            if (item.switchHref) {
                jump.href = item.switchHref;
            }
            actionsWrap.append(jump);

            if (item.makeDefaultHref) {
                actionsWrap.append(postForm(item.makeDefaultHref, '设为默认', 'article-ghost'));
            }

            if (item.disableHref) {
                actionsWrap.append(postForm(item.disableHref, '停用', 'article-ghost'));
            }
            if (item.enableHref) {
                actionsWrap.append(postForm(item.enableHref, '启用', 'article-ghost'));
            }

            row.append(copy, actionsWrap);
            list.append(row);
        });

        panel.append(head, actions, list);
        return panel;
    }

    function renderBackdrop() {
        if (!state.panel) {
            return null;
        }
        const backdrop = el('div', 'article-backdrop is-open');
        backdrop.addEventListener('click', () => {
            state.panel = '';
            render();
        });
        return backdrop;
    }

    function renderPanel() {
        if (state.panel === 'taxonomy-manager') {
            return renderTaxonomyManagerPanel();
        }
        if (state.panel === 'locale-manager') {
            return renderLocaleManagerPanel();
        }
        return null;
    }

    function render() {
        normalizePage();
        clear(root);

        const workspace = el('div', 'article-workspace');

        const toolbar = el('section', 'article-toolbar');
        toolbar.append(renderLocaleSwitch(), renderTopActions());

        const layout = el('section', 'article-layout');
        layout.append(renderTaxonomyRail(), renderContent());

        workspace.append(toolbar, renderFilters(), layout);
        root.append(workspace);

        const backdrop = renderBackdrop();
        const panel = renderPanel();
        if (backdrop) {
            root.append(backdrop);
        }
        if (panel) {
            root.append(panel);
        }
    }

    render();
})();
