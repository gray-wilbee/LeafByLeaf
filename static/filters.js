const SavedFilters = {
  async load(scope, selectId) {
    const select = document.getElementById(selectId);
    if (!select) return;
    const r = await fetch(`/api/filters/${scope}`);
    const data = await r.json();
    const filters = data.filters || [];
    select.innerHTML = '<option value="">Saved filters...</option>' + filters.map(f =>
      `<option value="${encodeURIComponent(JSON.stringify(f.params || {}))}">${f.name}</option>`
    ).join('');
  },

  apply(selectId, basePath) {
    const select = document.getElementById(selectId);
    if (!select || !select.value) return;
    const saved = JSON.parse(decodeURIComponent(select.value));
    const params = new URLSearchParams();
    Object.entries(saved || {}).forEach(([key, value]) => {
      if (Array.isArray(value)) value.forEach(v => params.append(key, v));
      else if (value !== undefined && value !== null && value !== '') params.set(key, value);
    });
    window.location.href = basePath + (params.toString() ? `?${params}` : '');
  },

  async save(scope, params, selectId) {
    const name = prompt('Save filter as:');
    if (!name || !name.trim()) return;
    await fetch(`/api/filters/${scope}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name.trim(), params }),
    });
    await SavedFilters.load(scope, selectId);
  },
};

const TagFilterTree = {
  init(rootId) {
    const root = document.getElementById(rootId);
    if (!root) return;
    root.querySelectorAll('.filter-tag-item[data-parent-id]').forEach(row => {
      if (row.dataset.parentId) {
        row.classList.add('filter-tag-item--collapsed');
        row.style.display = 'none';
      }
    });
    root.querySelectorAll('.filter-tag-expand').forEach(btn => {
      if (btn.dataset.bound === '1') return;
      btn.dataset.bound = '1';
      btn.addEventListener('click', e => {
        e.preventDefault();
        e.stopPropagation();
        const id = btn.dataset.tagId;
        const expanded = btn.getAttribute('aria-expanded') === 'true';
        btn.setAttribute('aria-expanded', expanded ? 'false' : 'true');
        btn.textContent = expanded ? '▸' : '▾';
        TagFilterTree.setChildrenVisible(root, id, !expanded);
      });
    });
  },

  setChildrenVisible(root, parentId, visible) {
    root.querySelectorAll(`.filter-tag-item[data-parent-id="${parentId}"]`).forEach(row => {
      row.classList.toggle('filter-tag-item--collapsed', !visible);
      row.style.display = visible ? '' : 'none';
      if (!visible) {
        const btn = row.querySelector('.filter-tag-expand');
        const childId = btn?.dataset.tagId;
        if (btn) {
          btn.setAttribute('aria-expanded', 'false');
          btn.textContent = '▸';
        }
        if (childId) TagFilterTree.setChildrenVisible(root, childId, false);
      }
    });
  },

  search(rootId, query) {
    const root = document.getElementById(rootId);
    if (!root) return;
    const q = query.trim().toLowerCase();
    root.querySelectorAll('.filter-tag-item').forEach(item => {
      const collapsed = item.classList.contains('filter-tag-item--collapsed');
      const matches = item.textContent.toLowerCase().includes(q);
      item.style.display = q ? (matches ? '' : 'none') : (collapsed ? 'none' : '');
    });
  },

  checkedParams(rootId) {
    const params = [];
    document.querySelectorAll(`#${rootId} input[type=checkbox]:checked`).forEach(cb => params.push(cb.value));
    return params;
  },
};

window.SavedFilters = SavedFilters;
window.TagFilterTree = TagFilterTree;
