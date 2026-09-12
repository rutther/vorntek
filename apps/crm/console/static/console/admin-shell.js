(function () {
    const payloadNode = document.getElementById('page-payload');
    const root = document.getElementById('workspace-root');
    const app = document.getElementById('app');
    const navToggle = document.getElementById('toggleNav');
    const mobileNavOpen = document.getElementById('openMobileNav');
    const mobileNavMask = document.getElementById('mobileNavMask');
    const mobileNavMedia = window.matchMedia('(max-width: 920px)');

    window.lucide?.createIcons({attrs: {'stroke-width': 1.8}});

    function closeMobileNav() {
        app?.classList.remove('is-mobile-nav-open');
        mobileNavOpen?.setAttribute('aria-expanded', 'false');
        if (mobileNavMask) {
            mobileNavMask.hidden = true;
        }
    }

    function updateNavToggle() {
        if (!navToggle || !app) {
            return;
        }
        if (mobileNavMedia.matches) {
            navToggle.setAttribute('aria-pressed', 'false');
            navToggle.setAttribute('aria-label', '关闭主导航');
            navToggle.setAttribute('title', '关闭主导航');
            return;
        }
        const collapsed = app.classList.contains('is-collapsed');
        const label = collapsed ? '展开主导航' : '收起主导航';
        navToggle.setAttribute('aria-pressed', String(collapsed));
        navToggle.setAttribute('aria-label', label);
        navToggle.setAttribute('title', label);
    }

    navToggle?.addEventListener('click', () => {
        if (mobileNavMedia.matches) {
            closeMobileNav();
            return;
        }
        app?.classList.toggle('is-collapsed');
        updateNavToggle();
    });
    mobileNavMedia.addEventListener?.('change', updateNavToggle);
    mobileNavOpen?.addEventListener('click', () => {
        app?.classList.add('is-mobile-nav-open');
        mobileNavOpen.setAttribute('aria-expanded', 'true');
        if (mobileNavMask) {
            mobileNavMask.hidden = false;
        }
    });
    mobileNavMask?.addEventListener('click', closeMobileNav);
    document.querySelectorAll('.sidebar .nav-item').forEach((link) => {
        link.addEventListener('click', closeMobileNav);
    });
    updateNavToggle();

    if (!payloadNode || !root || !app) {
        return;
    }

    const payload = JSON.parse(payloadNode.textContent || '{}');
    const csrfToken = document.querySelector('#csrf-template input[name="csrfmiddlewaretoken"]')?.value || '';
    const CHECKBOX_COLUMN_WIDTH = 42;
    const DEFAULT_ACTION_COLUMN_WIDTH = 156;
    const EMPTY_TEXT = '\u2014';
    const TYPE_COLUMN_WIDTHS = {
        title: 264,
        path: 248,
        badge: 96,
        text: 148,
    };
    const SECTION_COLUMN_WIDTHS = {
        articles: {
            title: 264,
            path: 236,
            category: 136,
            status: 92,
            seo: 88,
            updatedAt: 144,
            slug: 196,
            parent: 160,
        },
        assets: {
            title: 248,
            publicPath: 312,
            assetType: 104,
            status: 92,
            size: 104,
            updatedAt: 144,
        },
        marketing: {
            title: 240,
            provider: 132,
            integrationType: 104,
            publicId: 188,
            goal: 124,
            targetKind: 132,
            target: 248,
            integration: 156,
            event: 116,
            priority: 92,
            status: 92,
            updatedAt: 144,
        },
        releases: {
            title: 240,
            buildType: 112,
            status: 92,
            operator: 120,
            updatedAt: 144,
        },
    };

    const requestedTabKey = window.location.hash.replace(/^#/, '');
    const initialTabKey = payload.tabs?.some((tab) => tab.key === requestedTabKey)
        ? requestedTabKey
        : payload.tabs?.[0]?.key || 'all';

    const state = {
        tabKey: initialTabKey,
        query: '',
        selectedId: null,
        checked: new Set(),
        filters: {},
        collapsed: new Set(),
    };
    let detailReturnFocus = null;

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

    function joinClasses() {
        return Array.from(arguments).filter(Boolean).join(' ');
    }

    function safeText(value) {
        if (value === undefined || value === null || value === '') {
            return EMPTY_TEXT;
        }
        return String(value);
    }

    function iconMarkup(sectionKey) {
        const icons = {
            articles:
                '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 4h9l3 3v13H6z"></path><path d="M15 4v3h3"></path></svg>',
            assets:
                '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 6h16v12H4z"></path><path d="m8 14 2.5-3 2 2 2.5-3 3 4"></path></svg>',
            marketing:
                '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 12h4l3-7 4 14 3-7h2"></path></svg>',
            releases:
                '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3v12"></path><path d="m7 10 5 5 5-5"></path><path d="M5 20h14"></path></svg>',
        };
        return icons[sectionKey] || icons.articles;
    }

    function activeView() {
        return payload.views?.[state.tabKey] || payload;
    }

    function activeTable() {
        return activeView().table || {};
    }

    function tableBulkActions() {
        const actions = activeTable().bulkActions;
        return Array.isArray(actions) ? actions : [];
    }

    function tableIsSelectable() {
        return activeTable().selectable === true && tableBulkActions().length > 0;
    }

    function tableHasRowActions() {
        return (activeTable().rows || []).some((row) => (row.actions || []).length > 0);
    }

    function visibleTableColumns() {
        return (activeTable().columns || []).filter((column) => {
            const breakpoint = Number(column.hideBelow || 0);
            return !breakpoint || window.innerWidth >= breakpoint;
        });
    }

    function activeFilterGroups() {
        const view = activeView();
        if (Array.isArray(view.filterGroups) && view.filterGroups.length) {
            return view.filterGroups;
        }
        if (Array.isArray(view.filters) && view.filters.length) {
            return [{ key: 'default', label: '筛选', options: view.filters }];
        }
        return [];
    }

    function ensureFilterState() {
        const groupKeys = new Set();
        activeFilterGroups().forEach((group) => {
            groupKeys.add(group.key);
            if (!state.filters[group.key]) {
                state.filters[group.key] = group.options?.[0]?.key || 'all';
            }
        });
        Object.keys(state.filters).forEach((key) => {
            if (!groupKeys.has(key)) {
                delete state.filters[key];
            }
        });
    }

    function pageActions() {
        return activeView().actions || payload.actions || [];
    }

    function scopeActions() {
        return activeView().scopeActions || payload.scopeActions || [];
    }

    function actionColumnWidth() {
        return activeView().table?.actionWidth || payload.table?.actionWidth || DEFAULT_ACTION_COLUMN_WIDTH;
    }

    function columnWidth(column) {
        const customWidths = activeView().table?.columnWidths || payload.table?.columnWidths || {};
        const sectionWidths = SECTION_COLUMN_WIDTHS[payload.sectionKey] || {};
        return customWidths[column.key] || sectionWidths[column.key] || TYPE_COLUMN_WIDTHS[column.type] || 148;
    }

    function tableMinWidth(columns, {selectable, showActions}) {
        return (
            (selectable ? CHECKBOX_COLUMN_WIDTH : 0) +
            (showActions ? actionColumnWidth() : 0) +
            columns.reduce((sum, column) => sum + columnWidth(column), 0)
        );
    }

    function createAction(action, className) {
        if (action.kind === 'post') {
            const form = document.createElement('form');
            form.method = 'post';
            form.action = action.href;
            form.className = 'inline-form';

            const token = document.createElement('input');
            token.type = 'hidden';
            token.name = 'csrfmiddlewaretoken';
            token.value = csrfToken;

            const button = el('button', className || 'button neutral', action.label);
            button.type = 'submit';
            form.append(token, button);
            return form;
        }

        const link = el('a', className || 'button neutral', action.label);
        link.href = action.href;
        if (action.external) {
            link.target = '_blank';
            link.rel = 'noreferrer';
        }
        return link;
    }

    function createBulkAction(action) {
        const node = createAction(action, `button${action.tone === 'primary' ? ' primary' : ' neutral'}`);
        if (node.tagName === 'FORM') {
            Array.from(state.checked).forEach((rowId) => {
                const input = document.createElement('input');
                input.type = 'hidden';
                input.name = 'record_ids';
                input.value = rowId;
                node.append(input);
            });
        }
        return node;
    }

    function rowMatchesFilters(row) {
        return activeFilterGroups().every((group) => {
            const activeKey = state.filters[group.key] || group.options?.[0]?.key || 'all';
            return activeKey === 'all' || (row.filterKeys || []).includes(activeKey);
        });
    }

    function rowMatchesSearch(row) {
        if (!state.query) {
            return true;
        }
        const haystack = `${row.search || ''} ${row.title || ''} ${row.subtitle || ''}`.toLowerCase();
        return haystack.includes(state.query.toLowerCase());
    }

    function rowMap(rows) {
        return new Map(rows.map((row) => [row.id, row]));
    }

    function isHiddenByCollapsedAncestor(row, map) {
        if (state.query) {
            return false;
        }
        let parentId = row.parentId;
        while (parentId) {
            if (state.collapsed.has(parentId)) {
                return true;
            }
            parentId = map.get(parentId)?.parentId || null;
        }
        return false;
    }

    function visibleRows() {
        ensureFilterState();
        const rows = activeView().table?.rows || [];
        const map = rowMap(rows);
        return rows.filter((row) => rowMatchesFilters(row) && rowMatchesSearch(row) && !isHiddenByCollapsedAncestor(row, map));
    }

    function firstRowId() {
        return visibleRows()[0]?.id || null;
    }

    function selectedRow(rows) {
        return rows.find((row) => row.id === state.selectedId) || null;
    }

    function statuslineText() {
        const view = activeView();
        if (view.summary?.length) {
            return view.summary.map((item) => `${item.label} ${item.value}`).join(' / ');
        }
        return view.workspaceMeta || payload.workspaceMeta || '';
    }

    function setToolrowVisibility(node, shouldShow) {
        if (!node) {
            return;
        }
        node.classList.toggle('is-hidden', !shouldShow);
    }

    function renderTabs() {
        const container = document.getElementById('tabs');
        if (!container) {
            return;
        }
        clear(container);

        const tabs = payload.tabs || [];
        if (tabs.length <= 1) {
            return;
        }

        tabs.forEach((tab) => {
            const button = el('button', `button${state.tabKey === tab.key ? ' is-active' : ' neutral'}`, tab.label);
            button.type = 'button';
            button.dataset.tabKey = tab.key;
            button.addEventListener('click', () => {
                state.tabKey = tab.key;
                window.history.replaceState(null, '', `#${tab.key}`);
                state.selectedId = null;
                state.checked.clear();
                state.filters = {};
                ensureFilterState();
                state.selectedId = firstRowId();
                render();
            });
            container.append(button);
        });
    }

    function renderScopebar() {
        const container = document.getElementById('scopebar');
        if (!container) {
            return;
        }
        clear(container);
        scopeActions().forEach((action) => {
            container.append(createAction(action, `button${action.tone === 'primary' ? ' is-active' : ' neutral'}`));
        });
    }

    function renderPageActions() {
        const container = document.getElementById('pageActions');
        if (!container) {
            return;
        }
        clear(container);
        pageActions().forEach((action) => {
            container.append(createAction(action, `button${action.tone === 'primary' ? ' primary' : ' neutral'}`));
        });
    }

    function renderFilters() {
        const container = document.getElementById('filters');
        if (!container) {
            return;
        }
        clear(container);

        activeFilterGroups().forEach((group) => {
            const shell = el('div', 'filter-group');
            shell.append(el('span', 'filter-group-label', group.label));
            (group.options || []).forEach((option) => {
                const isActive = (state.filters[group.key] || 'all') === option.key;
                const button = el('button', `button${isActive ? ' is-active' : ' subtle'}`, option.label);
                button.type = 'button';
                button.setAttribute('aria-pressed', String(isActive));
                button.addEventListener('click', () => {
                    state.filters[group.key] = option.key;
                    state.selectedId = firstRowId();
                    renderWorkspace();
                    renderFilters();
                    renderStatusline();
                });
                shell.append(button);
            });
            container.append(shell);
        });
    }

    function renderSelectionBar() {
        const container = document.getElementById('selectionBar');
        if (!container) {
            return;
        }
        clear(container);
        if (!tableIsSelectable() || state.checked.size === 0) {
            return;
        }

        const selected = el('button', 'button is-active', `${state.checked.size} 项已选`);
        selected.type = 'button';
        selected.disabled = true;

        const clearButton = el('button', 'button neutral', '取消选择');
        clearButton.type = 'button';
        clearButton.addEventListener('click', () => {
            state.checked.clear();
            renderSelectionBar();
            renderStatusline();
            renderWorkspace();
        });

        container.append(selected);
        tableBulkActions().forEach((action) => container.append(createBulkAction(action)));
        container.append(clearButton);
    }

    function renderStatusline() {
        const node = document.getElementById('statusline');
        if (node) {
            clear(node);
            const summary = activeView().summary || [];
            node.classList.toggle('statusline-metrics', Boolean(summary.length));
            if (summary.length) {
                summary.forEach((item) => {
                    const metric = el('span', 'statusline-metric');
                    metric.append(
                        el('span', 'statusline-metric-label', item.label),
                        el('strong', '', item.value),
                    );
                    node.append(metric);
                });
            } else {
                node.textContent = statuslineText();
            }
        }

        const input = document.getElementById('searchInput');
        if (input) {
            input.placeholder = activeView().searchPlaceholder || payload.searchPlaceholder || '';
        }

        setToolrowVisibility(document.querySelector('.toolrow-main'), Boolean(scopeActions().length || pageActions().length || statuslineText()));
        setToolrowVisibility(
            document.querySelector('.toolrow-sub'),
            Boolean(
                (payload.tabs || []).length > 1 ||
                activeFilterGroups().length ||
                (tableIsSelectable() && state.checked.size)
            ),
        );
    }

    function renderCell(column, row) {
        if (column.type === 'title') {
            const wrapper = el('div', 'title-cell');
            const tree = el('div', 'title-tree');
            tree.style.setProperty('--tree-depth', String(row.depth || 0));

            if (row.hasChildren) {
                const toggle = el('button', `tree-toggle${state.collapsed.has(row.id) ? ' is-collapsed' : ''}`);
                toggle.type = 'button';
                toggle.setAttribute('aria-label', state.collapsed.has(row.id) ? '展开子分类' : '收起子分类');
                toggle.innerHTML =
                    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m6 9 6 6 6-6"></path></svg>';
                toggle.addEventListener('click', (event) => {
                    event.stopPropagation();
                    if (state.collapsed.has(row.id)) {
                        state.collapsed.delete(row.id);
                    } else {
                        state.collapsed.add(row.id);
                    }
                    renderWorkspace();
                });
                tree.append(toggle);
            } else {
                tree.append(el('span', 'tree-spacer'));
            }

            const icon = el('span', 'record-icon');
            icon.innerHTML = iconMarkup(payload.sectionKey);

            const copy = el('div', 'title-copy');
            copy.append(el('strong', '', safeText(row.title)));
            if (row.subtitle) {
                copy.append(el('span', '', row.subtitle));
            }

            tree.append(icon);
            wrapper.append(tree, copy);
            return wrapper;
        }

        const value = row.cells?.[column.key];
        if (column.type === 'badge' && value) {
            return el('span', `badge ${value.tone || 'slate'}`, safeText(value.label));
        }

        if (column.type === 'path') {
            return el('span', 'cell-text mono', safeText(value));
        }

        return el('span', 'cell-text', safeText(value));
    }

    function renderRowActions(row) {
        const wrap = el('div', 'table-actions');
        (row.actions || []).forEach((action) => {
            const node = createAction(action, 'table-button');
            node.addEventListener?.('click', (event) => event.stopPropagation());
            wrap.append(node);
        });
        return wrap;
    }

    function renderTable(rows) {
        const shell = el('div', 'table-shell');
        if (!rows.length) {
            shell.append(el('div', 'empty-state', activeView().table?.emptyMessage || '当前没有记录。'));
            return shell;
        }

        const columns = visibleTableColumns();
        const selectable = tableIsSelectable();
        const showActions = tableHasRowActions();
        const scroll = el('div', 'table-scroll');
        const table = document.createElement('table');
        const colgroup = document.createElement('colgroup');
        const thead = document.createElement('thead');
        const tbody = document.createElement('tbody');
        const headRow = document.createElement('tr');

        table.style.setProperty('--action-column-width', `${actionColumnWidth()}px`);
        table.style.width = `max(100%, ${tableMinWidth(columns, {selectable, showActions})}px)`;

        if (selectable) {
            const checkCol = document.createElement('col');
            checkCol.style.width = `${CHECKBOX_COLUMN_WIDTH}px`;
            colgroup.append(checkCol);
        }

        columns.forEach((column) => {
            const col = document.createElement('col');
            col.style.width = `${columnWidth(column)}px`;
            col.dataset.key = column.key;
            colgroup.append(col);
        });

        if (showActions) {
            const actionsCol = document.createElement('col');
            actionsCol.style.width = `${actionColumnWidth()}px`;
            colgroup.append(actionsCol);
        }

        if (selectable) {
            const selectHead = el('th', 'check-cell sticky-check');
            const selectAll = document.createElement('input');
            selectAll.type = 'checkbox';
            selectAll.id = 'checkAll';
            selectAll.setAttribute('aria-label', '选择当前结果中的全部记录');
            selectAll.checked = rows.length > 0 && rows.every((row) => state.checked.has(row.id));
            selectAll.addEventListener('change', () => {
                if (selectAll.checked) {
                    rows.forEach((row) => state.checked.add(row.id));
                } else {
                    rows.forEach((row) => state.checked.delete(row.id));
                }
                renderSelectionBar();
                renderStatusline();
                renderWorkspace();
            });
            selectHead.append(selectAll);
            headRow.append(selectHead);
        }

        columns.forEach((column, index) => {
            const th = el(
                'th',
                joinClasses(
                    index === 0 && 'sticky-title',
                    index === 0 && !selectable && 'sticky-title--edge',
                    `col-${column.key}`,
                ),
                column.label,
            );
            headRow.append(th);
        });

        if (showActions) {
            headRow.append(el('th', 'actions-col', '操作'));
        }
        thead.append(headRow);

        rows.forEach((row) => {
            const tr = document.createElement('tr');
            if (row.id === state.selectedId) {
                tr.className = 'is-selected';
            }
            tr.tabIndex = 0;
            tr.dataset.rowId = String(row.id);
            tr.setAttribute('aria-label', `查看 ${safeText(row.title)} 详情`);

            const open = () => {
                state.selectedId = row.id;
                table.querySelectorAll('tbody tr[data-row-id]').forEach((item) => {
                    item.classList.toggle('is-selected', item === tr);
                });
                openDetail(row, tr);
            };
            tr.addEventListener('click', open);
            tr.addEventListener('keydown', (event) => {
                if (event.target !== tr) {
                    return;
                }
                if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    open();
                }
            });

            if (selectable) {
                const checkCell = el('td', 'check-cell sticky-check');
                const check = document.createElement('input');
                check.type = 'checkbox';
                check.className = 'row-check';
                check.checked = state.checked.has(row.id);
                check.setAttribute('aria-label', `选择 ${row.title}`);
                check.addEventListener('click', (event) => event.stopPropagation());
                check.addEventListener('keydown', (event) => event.stopPropagation());
                check.addEventListener('change', () => {
                    if (check.checked) {
                        state.checked.add(row.id);
                    } else {
                        state.checked.delete(row.id);
                    }
                    renderSelectionBar();
                    renderStatusline();
                });
                checkCell.append(check);
                tr.append(checkCell);
            }

            columns.forEach((column, index) => {
                const td = el(
                    'td',
                    joinClasses(
                        index === 0 && 'sticky-title',
                        index === 0 && !selectable && 'sticky-title--edge',
                        `col-${column.key}`,
                    ),
                );
                td.append(renderCell(column, row));
                tr.append(td);
            });

            if (showActions) {
                const actionsCell = el('td', 'actions-col');
                actionsCell.append(renderRowActions(row));
                tr.append(actionsCell);
            }
            tbody.append(tr);
        });

        table.append(colgroup, thead, tbody);
        scroll.append(table);
        shell.append(scroll);
        return shell;
    }

    function renderCards(rows) {
        const list = el('div', 'mobile-card-list');
        if (!rows.length) {
            list.append(el('div', 'empty-state', activeView().table?.emptyMessage || '当前没有记录。'));
            return list;
        }

        const columns = visibleTableColumns();
        const selectable = tableIsSelectable();
        rows.forEach((row) => {
            const card = el('article', joinClasses('mobile-record-card', row.id === state.selectedId && 'is-selected'));

            const head = el('div', 'mobile-record-head');
            head.classList.toggle('is-unselectable', !selectable);
            const titleColumn = columns.find((column) => column.type === 'title') || columns[0];
            if (selectable) {
                const check = document.createElement('input');
                check.type = 'checkbox';
                check.className = 'row-check';
                check.checked = state.checked.has(row.id);
                check.setAttribute('aria-label', `选择 ${row.title}`);
                check.addEventListener('click', (event) => event.stopPropagation());
                check.addEventListener('change', () => {
                    if (check.checked) {
                        state.checked.add(row.id);
                    } else {
                        state.checked.delete(row.id);
                    }
                    renderSelectionBar();
                    renderStatusline();
                });
                head.append(check);
            }
            head.append(renderCell(titleColumn, row));

            const facts = el('dl', 'mobile-record-facts');
            columns.filter((column) => column !== titleColumn).forEach((column) => {
                const fact = el('div', 'mobile-record-fact');
                fact.append(el('dt', '', column.label));
                const dd = document.createElement('dd');
                dd.append(renderCell(column, row));
                fact.append(dd);
                facts.append(fact);
            });

            const open = (trigger) => {
                state.selectedId = row.id;
                list.querySelectorAll('.mobile-record-card').forEach((item) => {
                    item.classList.toggle('is-selected', item === card);
                });
                openDetail(row, trigger);
            };
            const actions = renderRowActions(row);
            actions.classList.add('mobile-record-actions');
            const detailButton = el('button', 'table-button', '查看详情');
            detailButton.type = 'button';
            detailButton.setAttribute('aria-label', `查看 ${safeText(row.title)} 详情`);
            detailButton.addEventListener('click', (event) => {
                event.stopPropagation();
                open(detailButton);
            });
            actions.prepend(detailButton);
            card.append(head, facts, actions);
            list.append(card);
        });
        return list;
    }

    function renderSummary() {
        const shell = el('div', 'summary-shell');
        const view = activeView();
        if (view.workspaceMeta || payload.workspaceMeta) {
            shell.append(el('section', 'summary-meta', view.workspaceMeta || payload.workspaceMeta));
        }
        if (view.notes?.length) {
            const notes = el('section', 'summary-notes');
            view.notes.forEach((note) => {
                const item = el('article', 'summary-note');
                item.append(el('h3', '', note.title));
                item.append(el('p', '', note.body));
                notes.append(item);
            });
            shell.append(notes);
        }
        return shell;
    }

    function renderWorkspace() {
        clear(root);
        const rows = visibleRows();
        if (payload.pageType === 'list') {
            root.append(
                window.matchMedia('(max-width: 720px)').matches
                    ? renderCards(rows)
                    : renderTable(rows)
            );
            return;
        }
        root.append(renderSummary());
    }

    function openDetail(row, trigger) {
        const mask = document.getElementById('detailMask');
        const drawer = document.getElementById('detailDrawer');
        const title = document.getElementById('detailTitle');
        const subtitle = document.getElementById('detailSubtitle');
        const list = document.getElementById('detailList');
        const actions = document.getElementById('detailActions');

        if (!row || !mask || !drawer || !title || !subtitle || !list || !actions) {
            return;
        }

        title.textContent = safeText(row.title);
        subtitle.textContent = row.subtitle || '查看当前记录的主要字段。';
        clear(list);
        clear(actions);

        (row.details || []).forEach((item) => {
            const detail = el('div', 'detail-item');
            detail.append(el('span', '', safeText(item.label)));
            detail.append(el('strong', '', safeText(item.value)));
            list.append(detail);
        });

        (row.actions || []).forEach((action) => {
            actions.append(createAction(action, `button${action.tone === 'primary' ? ' primary' : ' neutral'}`));
        });

        detailReturnFocus = trigger || document.activeElement;
        app.setAttribute('inert', '');
        mask.classList.add('show');
        drawer.classList.add('show');
        drawer.setAttribute('aria-hidden', 'false');
        window.requestAnimationFrame(() => {
            document.getElementById('closeDetail')?.focus({preventScroll: true});
        });
    }

    function closeDetail() {
        const mask = document.getElementById('detailMask');
        const drawer = document.getElementById('detailDrawer');
        mask?.classList.remove('show');
        drawer?.classList.remove('show');
        drawer?.setAttribute('aria-hidden', 'true');
        app.removeAttribute('inert');
        const returnTarget = detailReturnFocus;
        detailReturnFocus = null;
        if (returnTarget?.isConnected) {
            window.requestAnimationFrame(() => returnTarget.focus({preventScroll: true}));
        }
    }

    function toast(message) {
        const node = document.getElementById('toast');
        if (!node) {
            return;
        }
        node.textContent = message;
        node.classList.add('show');
        clearTimeout(window.__siteosToastTimer);
        window.__siteosToastTimer = setTimeout(() => node.classList.remove('show'), 1800);
    }

    function render() {
        ensureFilterState();
        renderTabs();
        renderScopebar();
        renderPageActions();
        renderFilters();
        renderSelectionBar();
        renderStatusline();
        renderWorkspace();
    }

    document.getElementById('closeDetail')?.addEventListener('click', closeDetail);
    document.getElementById('detailMask')?.addEventListener('click', closeDetail);

    document.getElementById('searchInput')?.addEventListener('input', (event) => {
        state.query = event.target.value || '';
        state.selectedId = firstRowId();
        renderWorkspace();
    });

    document.getElementById('previewRow')?.addEventListener('click', () => {
        const row = selectedRow(visibleRows());
        const previewAction = row?.actions?.find((action) => action.label.includes('预览'));
        if (previewAction) {
            window.open(previewAction.href, previewAction.external ? '_blank' : '_self', 'noreferrer');
            return;
        }
        toast('当前记录没有预览入口。');
    });

    document.getElementById('editRow')?.addEventListener('click', () => {
        const row = selectedRow(visibleRows());
        const editAction = row?.actions?.find((action) => action.label.includes('编辑'));
        if (editAction) {
            window.location.href = editAction.href;
            return;
        }
        toast('当前记录没有编辑入口。');
    });

    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            closeDetail();
            closeMobileNav();
        }
    });

    const mobileListMedia = window.matchMedia('(max-width: 720px)');
    mobileListMedia.addEventListener?.('change', () => renderWorkspace());
    let tableResizeFrame = 0;
    window.addEventListener('resize', () => {
        if (payload.pageType !== 'list') {
            return;
        }
        window.cancelAnimationFrame(tableResizeFrame);
        tableResizeFrame = window.requestAnimationFrame(() => renderWorkspace());
    });

    state.selectedId = firstRowId();
    render();
})();
