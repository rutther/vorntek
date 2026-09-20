(() => {
  const advancedFilters = document.querySelector('[data-pool-advanced-filters]');
  if (advancedFilters && window.matchMedia('(max-width: 720px)').matches) {
    advancedFilters.removeAttribute('open');
  }

  const pageForm = document.querySelector('[data-pool-page-form]');
  const pageSizeSelect = document.querySelector('[data-pool-page-size]');
  if (pageForm && pageSizeSelect) {
    pageSizeSelect.addEventListener('change', () => {
      if (typeof pageForm.requestSubmit === 'function') pageForm.requestSubmit();
      else pageForm.submit();
    });
  }

  const countryFilter = document.querySelector('[data-pool-country]');
  if (countryFilter) {
    const summary = countryFilter.querySelector('[data-pool-country-summary]');
    const boxes = [...countryFilter.querySelectorAll('input[name="country"]')];
    const emptyLabel = summary?.dataset.poolCountryEmpty || summary?.textContent.trim() || '';
    const refresh = () => {
      const picked = boxes.filter((box) => box.checked).map((box) => box.value);
      countryFilter.classList.toggle('is-active', picked.length > 0);
      if (!summary) return;
      if (!picked.length) {
        summary.textContent = emptyLabel;
      } else if (picked.length <= 4) {
        summary.textContent = `已选 ${picked.length} 国：${picked.join('、')}`;
      } else {
        summary.textContent = `已选 ${picked.length} 国：${picked.slice(0, 4).join('、')} 等`;
      }
    };
    boxes.forEach((box) => box.addEventListener('change', refresh));
    document.addEventListener('click', (event) => {
      if (countryFilter.open && !countryFilter.contains(event.target)) countryFilter.open = false;
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && countryFilter.open) countryFilter.open = false;
    });
    refresh();
  }

  const activeExports = document.querySelector('[data-pool-export-active]');
  if (activeExports) {
    const initialSnapshot = activeExports.dataset.statusSnapshot || '';
    const poll = async () => {
      try {
        const response = await window.fetch(activeExports.dataset.statusUrl, {
          credentials: 'same-origin',
          headers: { Accept: 'application/json' },
        });
        if (response.redirected || response.status === 401 || response.status === 403) {
          window.location.reload();
          return;
        }
        if (response.ok) {
          const payload = await response.json();
          const snapshot = (payload.jobs || [])
            .map((job) => `${job.id}:${job.status}:${job.updated_at}`)
            .join('|');
          if (snapshot !== initialSnapshot) {
            window.location.reload();
            return;
          }
        }
      } catch (_error) {
        // A temporary polling failure must not interrupt the current page task.
      }
      window.setTimeout(poll, 2000);
    };
    window.setTimeout(poll, 2000);
  }

  const selectAll = document.querySelector('[data-pool-select-all]');
  const rows = [...document.querySelectorAll('[data-pool-select-row]')];
  const selectionActions = [...document.querySelectorAll('[data-pool-selection-action]')];
  const selectedCounts = [...document.querySelectorAll('[data-pool-selected-count]')];
  const refreshSelection = () => {
    const selected = rows.filter((row) => row.checked);
    selectedCounts.forEach((counter) => { counter.textContent = String(selected.length); });
    selectionActions.forEach((action) => { action.disabled = selected.length === 0; });
    if (selectAll) {
      selectAll.checked = rows.every((item) => item.checked);
      selectAll.indeterminate = !selectAll.checked && rows.some((item) => item.checked);
    }
  };
  if (selectAll && rows.length) {
    selectAll.addEventListener('change', () => {
      rows.forEach((row) => { row.checked = selectAll.checked; });
      refreshSelection();
    });
    rows.forEach((row) => row.addEventListener('change', refreshSelection));
    refreshSelection();
  }

  const selectedEntries = () => {
    const unique = new Map();
    rows.filter((row) => row.checked).forEach((row) => {
      const companyId = row.dataset.companyId;
      if (companyId && !unique.has(companyId)) {
        unique.set(companyId, row.dataset.companyVersion || '');
      }
    });
    return [...unique.entries()];
  };
  const injectSelectedInputs = (form, entries, name, valueFor) => {
    form.querySelectorAll('[data-pool-selected-input]').forEach((input) => input.remove());
    entries.forEach((entry) => {
      const input = document.createElement('input');
      input.type = 'hidden';
      input.name = name;
      input.value = valueFor(entry);
      input.dataset.poolSelectedInput = '1';
      form.append(input);
    });
  };

  const claimForm = document.querySelector('#pool-bulk-claim');
  if (claimForm) {
    claimForm.addEventListener('submit', (event) => {
      event.preventDefault();
      injectSelectedInputs(claimForm, selectedEntries(), 'items', (entry) => `${entry[0]}:${entry[1] || 0}`);
      claimForm.submit();
    });
  }

  const exportModal = document.querySelector('#pool-export-modal');
  const exportMode = document.querySelector('#pool-export-mode');
  const exportSummary = document.querySelector('[data-pool-export-summary]');
  const exportOpeners = [...document.querySelectorAll('[data-pool-export-open]')];
  if (exportModal && exportMode && exportSummary) {
    let exportReturnFocus = null;
    exportOpeners.forEach((opener) => opener.addEventListener('click', () => {
      exportReturnFocus = opener;
      const mode = opener.dataset.poolExportOpen || 'current';
      exportMode.value = mode;
      const entries = selectedEntries();
      const checked = rows.filter((row) => row.checked).length;
      const exportForm = document.querySelector('#pool-export-form');
      if (exportForm) {
        injectSelectedInputs(
          exportForm,
          mode === 'selected' ? entries : [],
          'company_ids',
          (entry) => entry[0],
        );
      }
      exportSummary.textContent = mode === 'selected'
        ? `将导出明确勾选的 ${entries.length} 家企业（共 ${checked} 条勾选记录）。`
        : exportSummary.dataset.currentSummary;
    }));
    exportModal.addEventListener('shown.bs.modal', () => {
      exportModal.querySelector('input:not([type="hidden"]):not([disabled]), select, button')?.focus();
    });
    exportModal.addEventListener('hidden.bs.modal', () => exportReturnFocus?.focus());
  }

  const standard21Modal = document.querySelector('#pool-standard21-modal');
  const standard21Form = document.querySelector('#pool-standard21-form');
  const standard21Mode = document.querySelector('#pool-standard21-mode');
  const standard21Summary = document.querySelector('[data-pool-standard21-summary]');
  const standard21Openers = [...document.querySelectorAll('[data-pool-standard21-open]')];
  if (standard21Modal && standard21Form && standard21Mode && standard21Summary) {
    let standard21ReturnFocus = null;
    standard21Openers.forEach((opener) => opener.addEventListener('click', () => {
      standard21ReturnFocus = opener;
      const mode = opener.dataset.poolStandard21Open || 'current';
      const entries = selectedEntries();
      const checked = rows.filter((row) => row.checked).length;
      standard21Mode.value = mode;
      injectSelectedInputs(
        standard21Form,
        mode === 'selected' ? entries : [],
        'company_ids',
        (entry) => entry[0],
      );
      if (mode === 'selected') {
        standard21Summary.textContent = `将导出明确勾选的 ${entries.length} 家企业（共 ${checked} 条勾选记录）的全部 21 列客资行。`;
      } else {
        standard21Summary.textContent = standard21Summary.dataset.currentSummary;
      }
    }));
    standard21Modal.addEventListener('shown.bs.modal', () => {
      standard21Modal.querySelector('button[type="submit"]')?.focus();
    });
    standard21Modal.addEventListener('hidden.bs.modal', () => standard21ReturnFocus?.focus());
  }

  const bulkReviewModal = document.querySelector('#pool-bulk-review-modal');
  const bulkReviewForm = document.querySelector('#pool-bulk-review-form');
  const bulkReviewSummary = document.querySelector('[data-pool-bulk-review-summary]');
  const bulkReviewOpeners = [...document.querySelectorAll('[data-pool-bulk-review-open]')];
  if (bulkReviewModal && bulkReviewForm && bulkReviewSummary) {
    let bulkReviewReturnFocus = null;
    bulkReviewOpeners.forEach((opener) => opener.addEventListener('click', () => {
      bulkReviewReturnFocus = opener;
      const entries = selectedEntries();
      const checked = rows.filter((row) => row.checked).length;
      injectSelectedInputs(bulkReviewForm, entries, 'company_ids', (entry) => entry[0]);
      bulkReviewSummary.textContent = `将核验 ${entries.length} 家企业（来自 ${checked} 条勾选记录）：每家独立跑同一门禁，通过的立即进入所选团队公海。`;
    }));
    bulkReviewModal.addEventListener('shown.bs.modal', () => {
      bulkReviewModal.querySelector('select, button[type="submit"]')?.focus();
    });
    bulkReviewModal.addEventListener('hidden.bs.modal', () => bulkReviewReturnFocus?.focus());
  }

  const contactRoutes = [...document.querySelectorAll('[data-pool-contact-route]')];
  if (contactRoutes.length) {
    const validateRoutes = () => {
      const hasValue = contactRoutes.some((input) => input.value.trim());
      contactRoutes.forEach((input, index) => {
        input.setCustomValidity(!hasValue && index === 0 ? '请至少填写邮箱或电话中的一种。' : '');
      });
    };
    contactRoutes.forEach((input) => input.addEventListener('input', validateRoutes));
    contactRoutes[0].form?.addEventListener('submit', validateRoutes);
  }

  const createModal = document.querySelector('#pool-create-modal');
  if (createModal) {
    let returnFocus = null;
    createModal.addEventListener('show.bs.modal', (event) => {
      returnFocus = event.relatedTarget || document.activeElement;
    });
    createModal.addEventListener('shown.bs.modal', () => {
      const firstField = createModal.querySelector('input:not([type="hidden"]), select, textarea');
      window.setTimeout(() => firstField?.focus(), 50);
    });
    createModal.addEventListener('hidden.bs.modal', () => {
      if (returnFocus instanceof HTMLElement) returnFocus.focus();
    });
    if (createModal.hasAttribute('data-pool-reopen')) {
      document.querySelector('[data-bs-target="#pool-create-modal"]')?.click();
    }
  }
  const denseTable = document.querySelector('.nc-pool-table-dense');
  if (denseTable) {
    const storageKey = 'ncPoolDenseColumns:v1';
    const minWidth = 48;
    const cols = [...denseTable.querySelectorAll('col[data-pool-col]')];
    const defaultWidths = {};
    cols.forEach((col) => { defaultWidths[col.dataset.poolCol] = parseFloat(col.style.width) || 0; });
    const applyWidths = (widths) => {
      let total = 0;
      cols.forEach((col) => {
        const width = widths[col.dataset.poolCol];
        if (Number.isFinite(width)) {
          col.style.width = `${Math.max(minWidth, Math.round(width))}px`;
        }
        total += parseFloat(col.style.width) || 0;
      });
      if (total > 0) denseTable.style.width = `${total}px`;
    };
    const readWidths = () => {
      try {
        const parsed = JSON.parse(window.localStorage.getItem(storageKey) || 'null');
        return parsed && typeof parsed === 'object' ? parsed : {};
      } catch (_error) {
        return {};
      }
    };
    let widths = { ...defaultWidths, ...readWidths() };
    applyWidths(widths);
    const persist = () => {
      try {
        window.localStorage.setItem(storageKey, JSON.stringify(widths));
      } catch (_error) {
        // 存储不可用时列宽只在当前页面生效。
      }
    };
    denseTable.querySelectorAll('[data-pool-resize]').forEach((handle) => {
      handle.addEventListener('pointerdown', (event) => {
        const key = handle.dataset.poolResize;
        const col = cols.find((item) => item.dataset.poolCol === key);
        if (!col) return;
        event.preventDefault();
        const startX = event.clientX;
        const startWidth = col.getBoundingClientRect().width;
        handle.setPointerCapture(event.pointerId);
        document.body.classList.add('nc-pool-resizing');
        const onMove = (moveEvent) => {
          widths = { ...widths, [key]: startWidth + (moveEvent.clientX - startX) };
          applyWidths(widths);
        };
        const onEnd = () => {
          handle.removeEventListener('pointermove', onMove);
          handle.removeEventListener('pointerup', onEnd);
          handle.removeEventListener('pointercancel', onEnd);
          document.body.classList.remove('nc-pool-resizing');
          persist();
        };
        handle.addEventListener('pointermove', onMove);
        handle.addEventListener('pointerup', onEnd);
        handle.addEventListener('pointercancel', onEnd);
      });
    });
    const resetColumns = document.querySelector('[data-pool-reset-columns]');
    if (resetColumns) {
      resetColumns.addEventListener('click', () => {
        widths = { ...defaultWidths };
        try {
          window.localStorage.removeItem(storageKey);
        } catch (_error) {
          // 存储不可用时无需清理。
        }
        applyWidths(widths);
      });
    }
    denseTable.querySelectorAll('tbody td').forEach((cell) => {
      if (cell.querySelector('a, button, form, input')) return;
      const text = cell.textContent.trim();
      if (text && text !== '—') cell.title = text;
    });
  }
  if (standard21Form) {
    const columnBoxes = [...standard21Form.querySelectorAll('.nc-pool-column-grid input[type="checkbox"][name="columns"]')];
    const warning = standard21Form.querySelector('[data-pool-column-warning]');
    const defaultSummary = standard21Form.querySelector('[data-pool-standard21-summary]');
    const update = () => {
      const kept = columnBoxes.filter((box) => box.checked).length + 2;
      if (warning) {
        const dropped = columnBoxes.filter((box) => !box.checked).length;
        warning.hidden = dropped === 0;
        warning.textContent = dropped
          ? `已裁掉 ${dropped} 列：该文件不再符合《21 列标准格式》，不能直接回写研究表。`
          : '';
      }
      if (defaultSummary && defaultSummary.dataset.currentSummary) {
        defaultSummary.textContent = columnBoxes.length
          ? `${defaultSummary.dataset.currentSummary}本次导出 ${kept} / 21 列。`
          : defaultSummary.dataset.currentSummary;
      }
    };
    standard21Form.querySelectorAll('[data-pool-columns-select]').forEach((button) => {
      button.addEventListener('click', () => {
        const keepAll = button.dataset.poolColumnsSelect === 'all';
        columnBoxes.forEach((box) => { box.checked = keepAll; });
        update();
      });
    });
    columnBoxes.forEach((box) => box.addEventListener('change', update));
    update();
    const columnModal = document.querySelector('#pool-standard21-modal');
    if (columnModal) columnModal.addEventListener('shown.bs.modal', update);
  }
})();
