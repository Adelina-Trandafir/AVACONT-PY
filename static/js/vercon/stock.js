// VC08-01: balances and warehouse statements from the same server stock engine.
import { el, scope, field, grid, column, paintTree, preview, showMessage, CheckBox } from './ui.js';
import { decimalText as numberText } from './decimal-text.js';
import { receiptTotal } from './receipt-reconciliation.js';

const months = ['Ianuarie', 'Februarie', 'Martie', 'Aprilie', 'Mai', 'Iunie', 'Iulie', 'August', 'Septembrie', 'Octombrie', 'Noiembrie', 'Decembrie'];
const titles = { nir: 'Intrare', bc: 'Consum / Predare', br: 'Retur', storno: 'Storno', bp: 'Predare', bt_in: 'Transfer primit', bt_out: 'Transfer predat', bs: 'Casare' };

export function mountStock(host, { api, sel, signal, openForm, canOpen }) {
  const owner = scope(), trees = scope(); owner.own(() => trees.clear());
  const lifetime = new AbortController(); owner.own(() => lifetime.abort());
  const stop = () => lifetime.abort(); signal?.addEventListener('abort', stop, { once: true });
  owner.own(() => signal?.removeEventListener('abort', stop));
  const now = new Date(), year = Number(sel().year || now.getFullYear()), month = year === now.getFullYear() ? now.getMonth() : 0;
  const state = { start: `${year}-${String(month + 1).padStart(2, '0')}-01`, end: endOfMonth(year, month), category: null,
    ledger: 'warehouse', data: null, selected: null, search: '', seq: 0, destroyed: false };
  const root = el('section', { className: 'vc-stock' });
  const left = el('aside', { className: 'vc-stock__left' }), center = el('div', { className: 'vc-stock__center' }), right = el('aside', { className: 'vc-stock__right' });
  const period = el('section', { className: 'vc-stock__panel vc-stock__tree' });
  const categories = el('section', { className: 'vc-stock__panel vc-stock__tree vc-stock__categories' });
  left.append(period, categories);
  const panel = el('section', { className: 'vc-stock__panel' }), title = el('h3', { textContent: 'Stocuri' });
  const filters = el('div', { className: 'vc-stock__filters' });
  const begin = field({ key: 'start', label: 'De la', type: 'date', required: true }, state.start, true, (value) => { state.start = value; invalidate(); }, owner);
  const end = field({ key: 'end', label: 'Până la', type: 'date', required: true }, state.end, true, (value) => { state.end = value; invalidate(); }, owner);
  const refresh = el('button', { type: 'button', textContent: 'Calculează' });
  refresh.addEventListener('click', () => void load());
  filters.append(begin.node, end.node, refresh);
  const only = owner.own(new CheckBox(filters, { label: 'Doar produse cu stoc', checked: true, className: 'vc-stock__only', onChange: () => paint() })).input;
  if (['OI', 'MF'].includes(sel().type)) {
    const ledger = field({ key: 'ledger', label: 'Evidență', type: 'select', options: [
      { value: 'warehouse', label: 'Magazie' }, { value: 'custody', label: 'Deținători' },
    ] }, state.ledger, true, (value) => { state.ledger = value; state.category = null; void load(); }, owner);
    filters.append(ledger.node);
  }
  const status = el('p', { className: 'vc-stock__status', role: 'status' });
  const tableHost = el('div', { className: 'vc-stock__grid' }), total = el('output', { className: 'vc-stock__total' });
  const numericColumn = (key, label, width = 115) => column({ key, label, type: 'decimal' }, { editable: false, width,
    formatter: (value) => numberText(value, state.data?.zec ?? 4) });
  const columns = [column({ key: 'name', label: 'Produs' }, { editable: false, width: 230 }),
    column({ key: 'category_name', label: 'Cont' }, { editable: false, width: 160 }),
    column({ key: 'um', label: 'UM' }, { editable: false, width: 65 }),
    column({ key: 'holder_name', label: 'Deținător' }, { editable: false, width: 180, visible: ['OI', 'MF'].includes(sel().type) }),
    numericColumn('opening', 'Stoc inițial'), numericColumn('in', 'Intrări + retur'), numericColumn('out', 'Ieșiri + storno'),
    numericColumn('closing', 'Stoc final'), numericColumn('price', 'Preț cu TVA'), numericColumn('value', 'Valoare stoc', 145)];
  const stockGrid = grid(tableHost, { columns, rows: [], rowKey: 'key', layoutId: 'vercon.stock.balance',
    onSelect: (row) => { state.selected = row; paintDetails(); } }, owner);
  panel.append(title, filters, status, tableHost, total);
  const movementPanel = el('section', { className: 'vc-stock__panel' }, el('h4', { textContent: 'Mișcări • produsul selectat' }));
  const movementHost = el('div', { className: 'vc-stock__movements' }); movementPanel.append(movementHost);
  const movementColumns = [column({ key: 'date', label: 'Data', type: 'date' }, { editable: false }),
    column({ key: 'number', label: 'Document' }, { editable: false }), column({ key: 'kind', label: 'Mișcare' }, { editable: false, formatter: (value) => titles[value] || value }),
    numericColumn('quantity', 'Cantitate'), numericColumn('balance', 'Sold'), numericColumn('balance_value', 'Valoare sold', 145)];
  const movementGrid = grid(movementHost, { columns: movementColumns, rows: [], rowKey: 'key', layoutId: 'vercon.stock.statement' }, owner);
  center.append(panel, movementPanel);
  const detail = el('section', { className: 'vc-stock__panel vc-stock__details' });
  const actions = el('section', { className: 'vc-stock__panel' }, el('h4', { textContent: 'Consultare' }));
  const commands = el('div', { className: 'vc-actions' });
  const sheetButton = el('button', { type: 'button', textContent: 'Fișa de magazie', disabled: true });
  sheetButton.addEventListener('click', () => {
    if (!state.selected || !state.data) return;
    preview(`Fișă • ${state.selected.name}`, [{ title: `${state.data.start} — ${state.data.end}`, columns: movementColumns, rows: selectedMovements() }], owner, { period: state.data });
  });
  const balanceButton = el('button', { type: 'button', textContent: 'Previzualizare balanță', disabled: true });
  balanceButton.addEventListener('click', () => {
    if (!state.data) return;
    preview('Balanță produse', [{ title: `${state.data.start} — ${state.data.end}`, columns, rows: dataRows() }], owner, { period: state.data });
  });
  commands.append(sheetButton, balanceButton);
  for (const [key, label] of [['nir', '+ NIR nou'], ['bc', '+ Bon de consum']]) {
    if (!canOpen(key)) continue;
    const button = el('button', { type: 'button', textContent: label });
    button.addEventListener('click', () => openForm(key)); commands.append(button);
  }
  actions.append(commands); right.append(detail, actions); root.append(left, center, right); host.replaceChildren(root);

  function invalidate() {
    ++state.seq; state.data = null; state.selected = null; paint(); status.textContent = 'Perioada a fost schimbată. Apăsați Calculează.';
  }
  function dataRows() {
    return (state.data?.rows || []).map((row) => ({ ...row, key: `${row.holder ?? 'warehouse'}:${row.category ?? ''}:${row.variant}`,
      opening: row.quantities.opening, in: add(row.quantities.entries, row.quantities.returns, row.quantities.transfers_in),
      out: add(row.quantities.issues, row.quantities.storno, row.quantities.transfers_out, row.quantities.scrapping),
      closing: row.quantities.closing, value: row.values.closing }));
  }
  function add(...values) {
    return receiptTotal(values.map(value => ({ quantity: value ?? '0' })), 'quantity', state.data?.scale ?? 4);
  }
  function selectedMovements() {
    return (state.data?.statement || []).filter((row) => row.variant === state.selected?.variant && row.holder === state.selected?.holder && row.category === state.selected?.category)
      .map((row) => ({ ...row, key: `${row.kind}:${row.document_id}:${row.line_id}:${row.holder ?? ''}` }));
  }
  function paint() {
    const rows = dataRows().filter((row) => (!only.checked || !/^[-+]?0(?:\.0*)?$/.test(row.closing))
      && `${row.name} ${row.code}`.toLocaleLowerCase('ro').includes(state.search));
    stockGrid.setRows(rows);
    state.selected = rows.find((row) => row.key === state.selected?.key) || rows[0] || null;
    if (state.selected) stockGrid.selectRow(state.selected.key);
    total.textContent = state.data ? `Valoare totală • ${numberText(state.data.value_totals.closing, state.data.zec)} lei` : '';
    title.textContent = state.data ? `Stocuri • ${state.data.start} — ${state.data.end}` : 'Stocuri';
    balanceButton.disabled = !state.data; paintDetails();
  }
  function paintDetails() {
    detail.replaceChildren(el('h4', { textContent: state.selected?.name || 'Detalii produs' }));
    sheetButton.disabled = !state.selected;
    movementGrid.setRows(selectedMovements());
    if (!state.selected) { detail.append(el('p', { textContent: 'Selectați un produs din grilă.' })); return; }
    const row = state.selected, dl = el('dl');
    for (const [label, value] of [['Categorie', row.category_name], ['UM', row.um], ['Preț cu TVA', numberText(row.price, state.data.zec)],
      ['Stoc final', numberText(row.closing, state.data.zec)], ['Valoare stoc', numberText(row.value, state.data.zec)]]) {
      dl.append(el('dt', { textContent: label }), el('dd', { textContent: value || '—' }));
    }
    detail.append(dl);
  }
  function paintTrees() {
    trees.clear();
    paintTree(period, [{ id: `year:${year}`, label: `Anul ${year}`, children: months.map((label, index) => ({ id: `doc:${index}`, label })) }],
      { title: 'Perioada', selected: Number(state.start.slice(5, 7)) - 1, onSelect: (value) => {
        const index = Number(value); state.start = `${year}-${String(index + 1).padStart(2, '0')}-01`; state.end = endOfMonth(year, index);
        begin.input._picker.setValue(new Date(year, index, 1), false); end.input._picker.setValue(new Date(year, index + 1, 0), false); void load();
      } }, trees);
    paintTree(categories, [{ id: 'doc:all', label: 'Toate categoriile' }, ...(state.data?.categories || []).map((item) => ({ id: `doc:${item.id ?? 'none'}`, label: item.label }))],
      { title: 'Categorii / Conturi', selected: state.category ?? 'all', onSelect: (id) => { state.category = id === 'all' ? null : id; void load(); } }, trees);
  }
  async function load() {
    if (!await owner.commit() || lifetime.signal.aborted) return;
    const sequence = ++state.seq;
    state.data = null; state.selected = null; paint();
    const params = new URLSearchParams({ ...sel(), start: state.start, end: state.end, ledger: state.ledger });
    if (state.category != null) params.set('category', state.category);
    status.textContent = 'Se calculează stocurile…'; status.setAttribute('role', 'status');
    try {
      const data = await api(`/api/vercon/stock/balance?${params}`, { signal: lifetime.signal });
      if (state.destroyed || sequence !== state.seq) return;
      state.data = data; status.textContent = ''; paintTrees(); paint();
    } catch (error) {
      if (state.destroyed || sequence !== state.seq || error.name === 'AbortError') return;
      showMessage(status, error.message || 'Stocul nu a putut fi calculat.', 'error');
    }
  }
  paintTrees(); paint(); void load();
  return { isDirty: () => false, search(value) { state.search = String(value || '').toLocaleLowerCase('ro'); paint(); },
    destroy() { state.destroyed = true; ++state.seq; owner.clear(); } };
}

function endOfMonth(year, month) {
  const day = new Date(year, month + 1, 0).getDate();
  return `${year}-${String(month + 1).padStart(2, '0')}-${day}`;
}
