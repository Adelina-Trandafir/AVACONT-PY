import { el, paintTree } from './ui.js';

const FILTERS = [['supplier', 'Furnizori', 'suppliers'], ['receiver', 'Primitori', 'receivers'], ['document', 'Documente', 'documents']];

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
    if (children.length) nodes.push({ id: 'selection:products', label: 'Produse', children });
  }
  for (const [key, label, collection] of FILTERS) {
    if (!response.spec.filters.includes(key) || !response[collection]?.length) continue;
    const title = key !== 'receiver' ? label : response.spec.family === 'bs' ? 'Predători' : response.spec.ledger === 'custody' ? 'Deținători' : label;
    nodes.push({ id: `selection:${key}`, label: title, children: response[collection].map(item => ({ id: `${key}:${item.id}`, label: item.label })) });
  }
  return nodes;
}

export function mountSelection(host, response, checked, onChange, owner) {
  const nodes = selectionNodes(response);
  let initializing = true;
  const tree = paintTree(host, nodes, { title: 'Selectare date', checkable: true, branchChecks: true,
    onCheckChange: ids => { if (!initializing) onChange(ids); } }, owner);
  tree.setCheckedLeaves(checked);
  initializing = false;
  const hint = el('p', { className: 'vc-hint', textContent: 'Bifați datele dorite. Fără bife într-o categorie de filtre se folosesc toate datele acesteia.' });
  host.append(hint);
  return tree;
}

export function selectionFilters(ids) {
  const collected = {};
  for (const id of ids) {
    const [rawKey, value] = id.split(':');
    const key = rawKey === 'category-filter' ? 'category' : rawKey;
    if (!['variant', 'category', 'supplier', 'receiver', 'document'].includes(key)) continue;
    (collected[key] ||= []).push(value);
  }
  const filters = {};
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
