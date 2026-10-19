import { el, paintTree, CheckBox } from './ui.js';

const FILTERS = [['receiver', 'Primitori', 'receivers'], ['document', 'Documente', 'documents']];

export function selectionHeader(owner, onChange) {
  const node = el('header', { className: 'vc-reports__selection-header' }, el('h4', { textContent: 'Selectare date' }));
  const check = owner.own(new CheckBox(node, { label: 'Selectează TOATE', disabled: true,
    className: 'vc-reports__select-all', onChange }));
  return { node, check };
}

export function selectionNodes(response) {
  const nodes = [];
  if (response.account_filter && response.spec.filters.includes('category')) {
    nodes.push({ id: 'selection:use-accounts', label: 'Conturi în folosință', children: response.categories.map(item => ({ id: `category-filter:${item.id}`, label: item.label })) });
  }
  if (response.spec.filters.includes('variant')) {
    const categories = new Map();
    for (const item of response.variants) {
      const category = String(item.category ?? 'none');
      if (!categories.has(category)) categories.set(category, { id: `category:${category}`, label: item.account || 'Fără încadrare', children: [], products: new Map() });
      const bucket = categories.get(category), product = String(item.product ?? item.id);
      if (!bucket.products.has(product)) {
        const node = { id: `product:${category}:${product}`, label: `${item.name || item.label}${item.um ? ' / ' + item.um : ''}`, children: [] };
        bucket.products.set(product, node); bucket.children.push(node);
      }
      bucket.products.get(product).children.push({ id: `variant:${item.id}`, label: item.variant_label || item.label });
    }
    const children = [...categories.values()].map(({ products, ...node }) => node);
    nodes.push(...children);
  }
  for (const [key, label, collection] of FILTERS) {
    if (!response.spec.filters.includes(key) || !response[collection]?.length) continue;
    const title = key !== 'receiver' ? label : response.spec.family === 'bs' ? 'Predători' : response.spec.ledger === 'custody' ? 'Deținători' : label;
    nodes.push({ id: `selection:${key}`, label: title, children: response[collection].map(item => ({ id: `${key}:${item.id}`, label: item.label })) });
  }
  return nodes;
}

export function mountSelection(host, response, checked, onChange, owner, { loadBranch, onDocuments, onBusy, onError } = {}) {
  const nodes = response.nodes;
  const documentDetails = new Map((response.documents || []).map(item => [String(item.id), item]));
  const chosen = new Set(checked.filter(id => !id.startsWith('supplier:'))), pending = new Map(), checking = new Set();
  const controller = new AbortController();
  let disposed = false, suppress = true, searchVersion = 0, selectingAll = false;
  const leaf = id => /^(variant|category-filter|receiver|document|receipt-product|receipt-document-product|outgoing-item):/.test(id);
  const prefixOf = node => node.id.startsWith('out-account:') ? `outgoing-item:account:${node.id.split(':')[1]}:`
    : node.id.startsWith('out-receiver:') ? `outgoing-item:receiver:${node.id.split(':')[1]}:`
    : node.id.startsWith('out-document:') ? node.id.replace('out-document:', 'outgoing-item:') + ':'
    : node.id.startsWith('outgoing-item:') ? node.id
    : node.id.startsWith('receipt-supplier:') ? `receipt-product:${node.id.split(':')[1]}:`
    : node.id.startsWith('nir-supplier:') ? `receipt-document-product:${node.id.split(':')[1]}:`
    : node.id.startsWith('document-supplier:') ? 'document:'
    : node.id.startsWith('category:') || node.id === 'selection:products' ? 'variant:'
    : node.id === 'selection:use-accounts' ? 'category-filter:' : node.id.slice('selection:'.length) + ':';
  const leaves = (items, result = new Set()) => {
    for (const node of items) {
      if (leaf(node.id)) result.add(node.id);
      if (node.children) leaves(node.children, result);
    }
    return result;
  };
  const snapshot = items => items.map(node => ({ ...node, children: node.lazy
    ? [{ id: `waiting:${node.id}`, label: 'Deschideți ramura pentru încărcare.', selectable: false }]
    : node.children ? snapshot(node.children) : undefined }));
  const find = (id, items = nodes) => {
    for (const node of items) {
      if (node.id === id) return node;
      const child = node.children && find(id, node.children);
      if (child) return child;
    }
    return null;
  };
  const hasLazy = node => node.lazy || node.children?.some(hasLazy);
  host._vcExpanded = new Set();
  const tree = paintTree(host, snapshot(nodes), { title: 'Selectare date', collapsed: true,
    checkable: true, branchChecks: true,
    onToggle: (id, open) => { if (open) void load(id).catch(failed); },
    onCheckChange: ids => {
      if (suppress || disposed) return;
      // Selections in branches not loaded yet remain intact.
      for (const id of chosen) if (tree._checkIndex.has(id)) chosen.delete(id);
      ids.filter(leaf).forEach(id => chosen.add(id));
      updateAll();
      onChange([...chosen]);
    },
  }, owner);
  const header = selectionHeader(owner, checked => { void selectAll(checked); });
  host.firstElementChild.replaceWith(header.node);
  const hint = el('p', { className: 'vc-hint', textContent: 'Deschideți ramurile pentru încărcare și bifați datele dorite. Fără bife într-o categorie de filtre se folosesc toate datele acesteia.' });
  host.append(hint);
  const failed = problem => {
    if (disposed || problem.name === 'AbortError') return;
    onError?.(new Error(`${problem.message} Închideți și redeschideți ramura pentru a reîncerca.`));
  };
  const hasCheckbox = tree.hasCheckbox.bind(tree);
  tree.hasCheckbox = node => {
    const full = tree._checkIndex.get(String(node.id)) || node;
    return !String(full.id).startsWith('waiting:')
      && (/^(category|document-supplier|receipt-supplier|nir-supplier|out-account|out-receiver|out-document):/.test(String(full.id)) && (full.lazy || full.children?.length)
        || (full.children?.length ? hasCheckbox(full) : leaf(full.id)));
  };

  function updateAll() {
    const all = leaves(nodes), complete = !nodes.some(hasLazy);
    const count = [...all].filter(id => chosen.has(id)).length;
    header.check.checked = complete && all.size > 0 && count === all.size;
    header.check.input.indeterminate = chosen.size > 0 && !header.check.checked;
    header.check.setEnabled(nodes.length > 0 && !pending.size && !checking.size && !selectingAll);
  }

  function busy() {
    if (disposed) return;
    const loading = pending.size > 0 || checking.size > 0 || selectingAll;
    tree.treeElement.setAttribute('aria-busy', String(loading));
    updateAll(); onBusy?.(loading);
  }

  async function selectAll(checked) {
    if (disposed || selectingAll || pending.size || checking.size) return;
    selectingAll = true; busy();
    const activeNodes = nodes;
    try {
      if (checked) await Promise.all(nodes.map(node => load(node.id, true)));
      if (disposed || nodes !== activeNodes) return;
      chosen.clear();
      if (checked) for (const id of leaves(nodes)) chosen.add(id);
      redraw(); onChange([...chosen]);
    } catch (problem) { failed(problem); }
    finally { selectingAll = false; busy(); }
  }

  function redraw() {
    const expanded = new Set(tree.expandedNodes), top = tree.treeContainer.scrollTop;
    const query = tree.searchInput?.value.trim().toLowerCase() || '';
    suppress = true;
    try {
      tree.setData(snapshot(nodes), { preserveExpanded: true });
      tree.setCheckedLeaves([...chosen]);
      tree.expandedNodes = expanded;
      tree.isTreeRendered = false;
      if (query.length >= 3) tree.renderTreeWithData(query);
      else tree.renderTree('');
      if (tree.searchInput) tree.searchInput.value = query;
      if (tree.searchClear) tree.searchClear.hidden = !query;
      tree.treeContainer.scrollTop = top;
    } finally { suppress = false; updateAll(); }
  }

  function load(id, recursive = false) {
    const node = find(id);
    if (disposed || !node || !(recursive ? hasLazy(node) : node.lazy)) return Promise.resolve();
    if (pending.has(id)) return pending.get(id).then(() => load(id, recursive));
    const task = (async () => {
      tree.treeElement.setAttribute('aria-busy', 'true');
      const result = await loadBranch(id, recursive, controller.signal);
      if (disposed) return;
      node.children = result.nodes; delete node.lazy;
      if (find(id) !== node) return;
      if (result.documents) {
        result.documents.forEach(item => documentDetails.set(String(item.id), item));
        onDocuments?.([...documentDetails.values()]);
      }
      redraw();
    })().finally(() => {
      pending.delete(id);
      busy();
    });
    pending.set(id, task);
    busy();
    return task;
  }

  const toggleCheck = tree.toggleCheckNode.bind(tree);
  tree.toggleCheckNode = id => {
    if (checking.has(id) || disposed || selectingAll) return;
    const node = find(id);
    if (!node || !hasLazy(node)) { toggleCheck(id); return; }
    checking.add(id);
    busy();
    void load(id, true).then(() => { if (!disposed && find(id) === node) toggleCheck(id); })
      .catch(failed).finally(() => {
        checking.delete(id);
        busy();
      });
  };
  // Searching explicitly loads the remaining branches, then uses the common tree filter.
  tree.handleLocalSearch = event => {
    clearTimeout(tree.localSearchTimeout);
    const query = event.target.value.trim().toLowerCase(), version = ++searchVersion;
    tree.localSearchTimeout = setTimeout(async () => {
      if (disposed || version !== searchVersion) return;
      try {
        if (query.length >= 3) await Promise.all(nodes.map(node => load(node.id, true)));
        if (disposed || version !== searchVersion) return;
        tree.expandedNodes.clear(); tree.treeContainer.replaceChildren(); tree.isTreeRendered = false;
        if (query.length >= 3) tree.renderTreeWithData(query);
        else { tree.clearLocalSearch(false); tree.renderTree(''); }
        if (tree.searchClear) tree.searchClear.hidden = !query;
      } catch (problem) { failed(problem); }
    }, 300);
  };
  const cancelSearch = () => { ++searchVersion; clearTimeout(tree.localSearchTimeout); };
  tree.searchClear?.addEventListener('click', cancelSearch, { capture: true, signal: controller.signal });
  tree.searchInput?.addEventListener('keydown', event => {
    if (event.key === 'Escape') cancelSearch();
  }, { capture: true, signal: controller.signal });
  redraw();
  owner.own(() => { disposed = true; ++searchVersion; clearTimeout(tree.localSearchTimeout); controller.abort(); });
  const selectedRoots = nodes.filter(node => [...chosen].some(id => id.startsWith(prefixOf(node))));
  const ready = Promise.all(selectedRoots.map(node => load(node.id, true))).then(() => {
    if (disposed) return;
    // Accounts share the variant filter: reconcile against their combined leaves.
    const valid = leaves(nodes);
    let changed = false;
    for (const id of chosen) if (!valid.has(id)) { chosen.delete(id); changed = true; }
    if (changed) { redraw(); onChange([...chosen]); }
  });
  return { getCheckedLeaves: () => [...chosen],
    getFilters: (spec = response.spec) => selectionFilters([...chosen].filter(id => {
      if (/^receipt-(product|document-product):/.test(id)) return spec.incoming_selection && !header.check.checked;
      if (id.startsWith('outgoing-item:')) return (spec.outgoing_selection || spec.outgoing_filters) && !header.check.checked;
      const key = id.startsWith('category-filter:') ? 'category' : id.split(':')[0];
      return spec.filters.includes(key) && (!header.check.checked || spec[key + '_required']);
    })), ready };
}

export function selectionFilters(ids) {
  const collected = {};
  const receipts = [];
  const outgoing = [];
  for (const id of ids) {
    if (/^receipt-(product|document-product):/.test(id)) { receipts.push(id); continue; }
    if (id.startsWith('outgoing-item:')) { outgoing.push(id); continue; }
    const [rawKey, value] = id.split(':');
    const key = rawKey === 'category-filter' ? 'category' : rawKey;
    if (!['variant', 'category', 'receiver', 'document'].includes(key)) continue;
    (collected[key] ||= []).push(value);
  }
  const filters = {};
  if (receipts.length) filters.receipt_selection = receipts.join(',');
  if (outgoing.length) filters.outgoing_selection = outgoing.join(',');
  for (const [key, values] of Object.entries(collected)) {
    if (values.length === 1) filters[key] = values[0]; else filters[`${key}_ids`] = values.join(',');
  }
  return filters;
}

export function selectionError(spec, ids) {
  if (!spec) return 'Alegeți raportul.';
  for (const [key, label] of [['variant', 'produs / variantă'], ['supplier', 'furnizor'], ['document', 'document']]) {
    if (spec[`${key}_required`] && ids.filter(id => id.startsWith(key + ':')).length !== 1) return `Alegeți un singur ${label} pentru acest raport.`;
  }
  return '';
}
