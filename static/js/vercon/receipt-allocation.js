import { ComboTree } from '../components/combotree/combotree.js';
import { el } from './ui.js';

// The UI calls stock destinations "subunități" (Alimente, Materiale, OI, MF).
// Each root carries the complete existing SubunitId/GestiuneId context; accounts never create a new root.
export function receiptAllocation({ api, selection, draft, mode }) {
  let catalog = { accounts: [], destinations: [], products: [], vat: [] };
  let reopen = null;
  const current = () => `${selection().subunit}:${selection().gestiune}`;
  const product = id => catalog.products.find(item => String(item.id) === String(id));
  const destination = id => catalog.destinations.find(item => item.id === id);
  const accounts = () => mode() === 'edit' ? catalog.accounts.filter(item => item.destination === current()) : catalog.accounts;
  function ensure() {
    const data = draft();
    if (!data || data.accounts) return;
    const fromRows = [...new Set(data.lines.map(row => row.category ?? product(row.product)?.category).filter(Boolean))];
    const local = accounts().filter(item => item.destination === current());
    data.accounts = mode() === 'edit' ? fromRows.length ? fromRows : [data.header.IdMasina].filter(Boolean)
      : catalog.accounts.length === 1 ? [catalog.accounts[0].id] : local.length === 1 ? [local[0].id] : [];
  }
  const selectedAccounts = () => { ensure(); return accounts().filter(item => draft()?.accounts?.some(id => String(id) === String(item.id))); };
  const roots = () => [...new Set(selectedAccounts().map(item => item.destination))];
  function nodes() {
    return selectedAccounts().map(account => ({ id: `account:${account.id}`,
      label: `${account.label} · ${destination(account.destination)?.name || ''}`,
      children: catalog.products.filter(item => item.category === account.id).map(item => ({ id: String(item.id), label: item.name })) }))
      .filter(node => node.children.length);
  }
  function accountField(owner, editable, onChange, onTab) {
    ensure();
    const node = el('label', { className: 'vc-field vc-receipt-accounts', 'data-field': 'Accounts' }, el('span', { textContent: 'Categorie / cont' + (editable ? ' *' : '') }));
    const host = el('div'); node.append(host);
    const choices = accounts();
    const treeNodes = catalog.destinations.map(root => ({ id: `destination:${root.id}`, label: root.name,
      children: choices.filter(a => a.destination === root.id).map(a => ({ id: String(a.id), label: a.label })) }))
      .filter(root => root.children.length || root.id === `destination:${current()}`);
    let loading = true;
    const combo = owner.own(new ComboTree(host, { nodes: treeNodes, checkable: true, branchChecks: false, onTab,
      expanded: [`destination:${current()}`], placeholder: 'Bifați conturile…',
      formatCheckSummary: count => count ? `${count} conturi selectate` : 'Bifați conturile…',
      onCheckChange: ids => {
        if (loading) return;
        const before = [...draft().accounts];
        reopen = combo.isVisible ? [...combo.expandedNodes] : null;
        const operation = Promise.resolve(onChange(ids.map(Number))).catch(error => {
          reopen = null;
          loading = true; combo.setCheckedLeaves(before.map(String)); loading = false;
          node.title = error.message;
          throw error;
        });
        combo._accountChange = operation;
        operation.catch(() => {}).finally(() => { if (combo._accountChange === operation) combo._accountChange = null; });
      } }));
    combo.setCheckedLeaves((draft()?.accounts || []).map(String)); loading = false;
    combo.disabled = !editable || catalog.accounts.length === 1;
    combo.input.disabled = combo.disabled;
    combo.arrow.setAttribute('aria-disabled', String(combo.disabled));
    Object.defineProperty(combo, 'hasEdit', { get: () => !!combo._accountChange });
    combo.commitEdit = async () => { try { await combo._accountChange; return true; } catch { return false; } };
    combo.validate = () => {
      const ok = combo.getCheckedLeaves().length > 0;
      combo.input.classList.toggle('is-invalid', !ok);
      if (!ok) node.title = 'Bifați cel puțin un cont.';
      return { ok };
    };
    if (reopen) {
      const expanded = reopen; reopen = null;
      queueMicrotask(() => {
        if (!host.isConnected || combo.disabled) return;
        combo.show(); combo.expandedNodes = new Set(expanded);
        combo.isTreeRendered = false; combo.renderTree(''); combo.positionDropdown();
      });
    }
    return { node, input: combo.input, combo };
  }
  return {
    async load() {
      reopen = null;
      catalog = await api('/api/vercon/doc/nir/destinations?' + new URLSearchParams(selection()));
      return catalog;
    },
    ensure, nodes, product, destination, selectedAccounts, roots, accountField,
    precision: row => destination(product(row?.product)?.destination)?.zec,
    account: id => catalog.accounts.find(item => String(item.id) === String(id)),
    selectionForProduct: id => {
      const root = destination(product(id)?.destination);
      return root ? { subunit: root.subunit, gestiune: root.gestiune, year: ['MF', 'OI'].includes(root.type) ? '' : selection().year } : selection();
    },
    multi: () => roots().length > 1,
    taxOptions: row => catalog.vat.filter(item => item.destination === (product(row?.product)?.destination || current())),
    defaultTax: (id, percent) => catalog.vat.find(item => item.destination === product(id)?.destination && Number(item.percent) === Number(percent))?.id ?? null,
    tax: id => catalog.vat.find(item => String(item.id) === String(id)),
    isMF: row => (product(row?.product)?.type || (mode() === 'edit' ? selection().type : null)) === 'MF',
    hasMF: () => selectedAccounts().some(item => destination(item.destination)?.type === 'MF'),
    numberSelection: () => {
      const root = destination(roots()[0]);
      return root ? { subunit: root.subunit, gestiune: root.gestiune, year: ['MF', 'OI'].includes(root.type) ? '' : selection().year } : selection();
    },
  };
}
