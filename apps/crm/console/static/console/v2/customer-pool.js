(() => {
  const advancedFilters = document.querySelector('[data-pool-advanced-filters]');
  if (advancedFilters && window.matchMedia('(max-width: 720px)').matches) {
    advancedFilters.removeAttribute('open');
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

  const selectedCompanies = () => {
    const ids = new Set();
    rows.filter((row) => row.checked).forEach((row) => {
      if (row.dataset.companyId) ids.add(row.dataset.companyId);
    });
    return [...ids];
  };

  const bulkReviewModal = document.querySelector('#pool-bulk-review-modal');
  const bulkReviewForm = document.querySelector('#pool-bulk-review-form');
  const bulkReviewSummary = document.querySelector('[data-pool-bulk-review-summary]');
  const bulkReviewOpeners = [...document.querySelectorAll('[data-pool-bulk-review-open]')];
  if (bulkReviewModal && bulkReviewForm && bulkReviewSummary) {
    let bulkReviewReturnFocus = null;
    bulkReviewOpeners.forEach((opener) => opener.addEventListener('click', () => {
      bulkReviewReturnFocus = opener;
      const companyIds = selectedCompanies();
      bulkReviewForm
        .querySelectorAll('[data-pool-bulk-review-selection]')
        .forEach((input) => input.remove());
      companyIds.forEach((companyId) => {
        const input = document.createElement('input');
        input.type = 'hidden';
        input.name = 'company_ids';
        input.value = companyId;
        input.dataset.poolBulkReviewSelection = '1';
        bulkReviewForm.append(input);
      });
      bulkReviewSummary.textContent = `将核验 ${companyIds.length} 家企业：每家独立执行同一门禁，通过后立即进入所选团队公海。`;
    }));
    bulkReviewModal.addEventListener('shown.bs.modal', () => {
      bulkReviewModal.querySelector('select, button[type="submit"]')?.focus();
    });
    bulkReviewModal.addEventListener('hidden.bs.modal', () => bulkReviewReturnFocus?.focus());
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
      const count = rows.filter((row) => row.checked).length;
      exportSummary.textContent = mode === 'selected'
        ? `将导出明确勾选的 ${count} 家客户。`
        : exportSummary.dataset.currentSummary;
    }));
    exportModal.addEventListener('shown.bs.modal', () => {
      exportModal.querySelector('input:not([type="hidden"]):not([disabled]), select, button')?.focus();
    });
    exportModal.addEventListener('hidden.bs.modal', () => exportReturnFocus?.focus());
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
})();
