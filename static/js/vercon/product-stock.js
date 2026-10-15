import { el, scope, paintTree, markSelected } from './ui.js';
import { format } from './fields.js';
import { decimalText } from './decimal-text.js';

const kinds = { nir: 'NIR', storno: 'Storno', bc: 'BC', br: 'Retur', bp: 'Predare',
  bt_in: 'Transfer primit', bt_out: 'Transfer predat', bs: 'Casare' };
const amount = (value) => value == null ? '—' : decimalText(value, 2);
const price = (value) => value == null ? 'Fără preț' : decimalText(value, 4) + ' lei';

export function mountProductStock(host, opts) {
  const ui = scope();
  const title = el('h3', { textContent: 'Fișe de magazie pe prețuri' });
  const identity = el('p', { className: 'vc-products-stock__identity' });
  const treeHost = el('div', { className: 'vc-products-stock__tree' });
  const card = el('section', { className: 'vc-products-stock__card', 'aria-live': 'polite' });
  const root = el('section', { className: 'vc-products-stock' }, title, identity,
    el('div', { className: 'vc-products-stock__content' }, treeHost, card));
  host.replaceChildren(root);
  let disposed = false, generation = 0, product = null, data = null, selected = null, tree = null, nodes = [];
  function message(text, bad = false) {
    ui.clear(); tree = null; treeHost.replaceChildren();
    card.replaceChildren(el('p', { className: 'vc-products-stock__message', textContent: text, role: bad ? 'alert' : 'status' }));
    root.classList.add('is-empty');
  }
  function renderCard(variant) {
    const head = el('tr', {}, ...['Data', 'Document', 'Cant.', 'Sold'].map((label, index) =>
      el('th', { textContent: label, scope: 'col', className: index > 1 ? 'is-number' : '' })));
    const body = el('tbody', {}, ...variant.rows.map((row) => el('tr', {},
      el('td', { textContent: format(row.date, { type: 'date' }) }),
      el('td', { textContent: `${kinds[row.kind] || row.kind} ${row.number ?? ''}` }),
      el('td', { className: 'is-number', textContent: amount(row.quantity) }),
      el('td', { className: 'is-number', textContent: amount(row.balance) }))));
    card.replaceChildren(el('h4', { textContent: 'Preț cu TVA: ' + price(variant.price) + (variant.code ? ' · ' + variant.code : '') }),
      el('div', { className: 'vc-products-stock__movements' }, variant.rows.length
        ? el('table', {}, el('thead', {}, head), body)
        : el('p', { className: 'vc-products-stock__message', textContent: 'Fără mișcări în anul selectat.' })),
      el('footer', { className: 'vc-products-stock__total', textContent: `Stoc: ${amount(variant.closing)} ${data.um}` }));
  }
  function select(id) {
    selected = id;
    const variant = data?.variants.find((item) => String(item.id) === String(id));
    if (!variant) return;
    if (tree) markSelected(tree, variant.id);
    renderCard(variant);
  }
  function render() {
    ui.clear(); tree = null;
    identity.textContent = data.name + ' · ' + data.year;
    if (!data.variants.length) { message('Produsul nu are prețuri salvate.'); return; }
    root.classList.remove('is-empty');
    nodes = data.variants.map((variant) => ({ id: 'doc:' + variant.id,
      label: price(variant.price) + ' · ' + (variant.first_date ? format(variant.first_date, { type: 'date' }) : 'fără dată'),
      tooltip: 'Varianta ' + variant.id,
    }));
    tree = paintTree(treeHost, nodes, { title: 'Prețuri', showSearchBox: false,
      onSelect: select,
    }, ui);
    select(data.variants.some((item) => String(item.id) === String(selected)) ? selected : data.variants[0].id);
  }
  async function load(item, preferred = null) {
    const token = ++generation;
    const changed = product?.id !== item?.id;
    product = item; data = null;
    if (changed) selected = null;
    if (preferred != null) selected = preferred;
    identity.textContent = item?.DenumireP || '';
    message(item ? 'Se încarcă fișele de magazie…' : 'Selectați un produs pentru fișa de magazie.');
    if (!item) return;
    try {
      const query = new URLSearchParams(Object.entries(opts.sel()).filter(([, value]) => value != null && value !== ''));
      const result = await opts.api('/api/vercon/nom/produse/' + item.id + '/statement?' + query);
      if (disposed || token !== generation) return;
      data = result; render();
    } catch (error) {
      if (!disposed && token === generation && error.name !== 'AbortError') message(error.message || 'Fișele nu au putut fi încărcate.', true);
    }
  }
  message('Selectați un produs pentru fișa de magazie.');
  return { load, select, destroy: () => { disposed = true; ++generation; ui.clear(); host.replaceChildren(); } };
}
