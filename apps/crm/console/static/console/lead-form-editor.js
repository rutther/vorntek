(() => {
  const root = document.querySelector('[data-lead-custom-fields]');
  const hidden = document.getElementById('id_custom_fields_json');
  if (!root || !hidden) return;

  const list = root.querySelector('[data-custom-field-list]');
  const empty = root.querySelector('[data-custom-field-empty]');
  const add = root.querySelector('[data-add-custom-field]');
  const count = document.querySelector('[data-lead-field-count]');
  const baseCount = Number(count?.dataset.baseCount || 0);
  const maxFields = 20;
  const typeLabels = [
    ['text', '单行文本'],
    ['textarea', '多行文本'],
    ['email', '邮箱'],
    ['tel', '电话'],
    ['number', '数字'],
    ['date', '日期'],
    ['select', '单选下拉'],
  ];

  const readInitial = () => {
    try {
      const value = JSON.parse(hidden.value || '[]');
      return Array.isArray(value) ? value.slice(0, maxFields) : [];
    } catch (_error) {
      return [];
    }
  };

  const valueOf = (row, selector) => row.querySelector(selector)?.value?.trim() || '';
  const optionSpecs = (raw) => String(raw || '')
    .split(/\n+/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const separator = line.indexOf('|');
      const value = (separator >= 0 ? line.slice(0, separator) : line).trim();
      const label = (separator >= 0 ? line.slice(separator + 1) : line).trim();
      return value && label ? {value, label} : null;
    })
    .filter(Boolean);

  const serialize = () => {
    const fields = [...list.querySelectorAll('[data-custom-field-row]')].map((row) => ({
      name: valueOf(row, '[data-custom-name]').toLowerCase(),
      type: valueOf(row, '[data-custom-type]') || 'text',
      label: valueOf(row, '[data-custom-label]'),
      placeholder: valueOf(row, '[data-custom-placeholder]'),
      enabled: Boolean(row.querySelector('[data-custom-enabled]')?.checked),
      required: Boolean(row.querySelector('[data-custom-required]')?.checked),
      options: optionSpecs(valueOf(row, '[data-custom-options]')),
    }));
    hidden.value = JSON.stringify(fields);
    empty.hidden = fields.length > 0;
    add.disabled = fields.length >= maxFields;
    if (count) count.textContent = `${baseCount + fields.length} 个字段`;
  };

  const labeledInput = (captionText, className, value, placeholder) => {
    const label = document.createElement('label');
    label.className = 'lead-custom-control';
    const caption = document.createElement('span');
    caption.textContent = captionText;
    const input = document.createElement('input');
    input.type = 'text';
    input.className = className;
    input.value = value || '';
    input.placeholder = placeholder || '';
    input.maxLength = className.includes('name') ? 64 : 200;
    label.append(caption, input);
    return label;
  };

  const labeledTextarea = (captionText, className, value, placeholder) => {
    const label = document.createElement('label');
    label.className = 'lead-custom-control';
    const caption = document.createElement('span');
    caption.textContent = captionText;
    const textarea = document.createElement('textarea');
    textarea.className = className;
    textarea.value = value || '';
    textarea.placeholder = placeholder || '';
    textarea.rows = 2;
    textarea.maxLength = 24000;
    label.append(caption, textarea);
    return label;
  };

  const makeRow = (field = {}) => {
    const row = document.createElement('div');
    row.className = 'lead-custom-field-row';
    row.dataset.customFieldRow = '';

    const identity = document.createElement('div');
    identity.className = 'lead-custom-field-identity';
    identity.append(
      labeledInput('字段代码', 'lead-custom-name', field.name, '例如：project_deadline'),
      labeledInput('显示名称', 'lead-custom-label', field.label, '例如：计划时间'),
    );
    const nameInput = identity.querySelector('.lead-custom-name');
    nameInput.dataset.customName = '';
    nameInput.pattern = '[a-z][a-z0-9_]{1,63}';
    nameInput.required = true;
    const labelInput = identity.querySelector('.lead-custom-label');
    labelInput.dataset.customLabel = '';
    labelInput.required = true;

    const settings = document.createElement('div');
    settings.className = 'lead-custom-field-settings';
    const typeLabel = document.createElement('label');
    typeLabel.className = 'lead-custom-control';
    const typeCaption = document.createElement('span');
    typeCaption.textContent = '字段类型';
    const type = document.createElement('select');
    type.dataset.customType = '';
    for (const [value, label] of typeLabels) {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = label;
      option.selected = value === (field.type || 'text');
      type.append(option);
    }
    typeLabel.append(typeCaption, type);

    const enabledLabel = document.createElement('label');
    enabledLabel.className = 'lead-custom-required';
    const enabled = document.createElement('input');
    enabled.type = 'checkbox';
    enabled.checked = field.enabled !== false;
    enabled.dataset.customEnabled = '';
    enabledLabel.append(enabled, document.createTextNode(' 启用'));

    const requiredLabel = document.createElement('label');
    requiredLabel.className = 'lead-custom-required';
    const required = document.createElement('input');
    required.type = 'checkbox';
    required.checked = Boolean(field.required);
    required.dataset.customRequired = '';
    requiredLabel.append(required, document.createTextNode(' 必填'));
    settings.append(typeLabel, enabledLabel, requiredLabel);

    const copy = document.createElement('div');
    copy.className = 'lead-custom-field-copy';
    const placeholder = labeledInput('输入提示', 'lead-custom-placeholder', field.placeholder, '告诉客户如何填写');
    placeholder.querySelector('input').dataset.customPlaceholder = '';
    const options = labeledTextarea(
      '下拉选项（每行：稳定值 | 显示名称）',
      'lead-custom-options',
      (field.options || []).map((option) => {
        const value = typeof option === 'object' ? option.value || option.label : option;
        const label = typeof option === 'object' ? option.label || option.value : option;
        return `${value} | ${label}`;
      }).join('\n'),
      'q2 | 第二季度',
    );
    const optionsInput = options.querySelector('textarea');
    optionsInput.dataset.customOptions = '';
    copy.append(placeholder, options);

    const actions = document.createElement('div');
    actions.className = 'lead-custom-actions';
    const moveButton = (direction, title, icon) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'icon-button';
      button.title = title;
      button.setAttribute('aria-label', title);
      const buttonIcon = document.createElement('i');
      buttonIcon.dataset.lucide = icon;
      button.append(buttonIcon);
      button.addEventListener('click', () => {
        const sibling = direction < 0 ? row.previousElementSibling : row.nextElementSibling;
        if (!sibling) return;
        if (direction < 0) list.insertBefore(row, sibling);
        else list.insertBefore(sibling, row);
        serialize();
      });
      return button;
    };
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'icon-button lead-custom-remove';
    remove.title = '删除这个自定义字段';
    remove.setAttribute('aria-label', '删除这个自定义字段');
    const removeIcon = document.createElement('i');
    removeIcon.dataset.lucide = 'trash-2';
    remove.append(removeIcon);
    remove.addEventListener('click', () => {
      row.remove();
      serialize();
      window.lucide?.createIcons?.();
    });
    actions.append(
      moveButton(-1, '上移这个字段', 'arrow-up'),
      moveButton(1, '下移这个字段', 'arrow-down'),
      remove,
    );

    const updateOptions = () => {
      options.hidden = type.value !== 'select';
      if (options.hidden) optionsInput.value = '';
      serialize();
    };
    type.addEventListener('change', updateOptions);
    row.addEventListener('input', serialize);
    row.addEventListener('change', serialize);
    row.append(identity, settings, copy, actions);
    list.append(row);
    options.hidden = type.value !== 'select';
  };

  readInitial().forEach(makeRow);
  add.addEventListener('click', () => {
    const used = new Set(
      [...list.querySelectorAll('[data-custom-name]')].map((input) => input.value),
    );
    let index = list.children.length + 1;
    while (used.has(`custom_field_${index}`)) index += 1;
    makeRow({name: `custom_field_${index}`, type: 'text', label: ''});
    serialize();
    list.lastElementChild?.querySelector('[data-custom-label]')?.focus();
    window.lucide?.createIcons?.();
  });
  serialize();
  window.lucide?.createIcons?.();
})();
