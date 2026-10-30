// Lista de alimente: one draft, three meals and a set of existing BC links.
import { numberValue } from './receipt-number.js';
import { confirmBox, reasonBox } from '../utils/confirm-box.js';
import { format, formatMoney, parse } from './fields.js';
import { el, scope, field, column, grid, paintTree, groupedNodes, docNode, modal, rowMenu, openMenu, showMessage, statusBar } from './ui.js';
import { documentReport } from './document-report.js';
import { showDocumentHistory } from './document-history.js';
import { ActionAddButton } from '../components/action-add-button/action-add-button.js';
import { receiptTotal } from './receipt-reconciliation.js';

const MEALS = [['md', 'Mic dejun'], ['d', 'Dejun'], ['c', 'Cină / Gustare']];
const MONTHS = ['Ianuarie', 'Februarie', 'Martie', 'Aprilie', 'Mai', 'Iunie', 'Iulie', 'August', 'Septembrie', 'Octombrie', 'Noiembrie', 'Decembrie'];
const TOTALS = [['prior', 'Sold anterior'], ['Valoare', 'Valoare listă'], ['bons_total', 'Total bon\nconsum'], ['diff', 'Diferență'], ['closing', 'Sold']];
const blankDish = () => ({ key: key(), IdFel: null, Tip: '', Explicatie: '' });
const isBlankDish = (row) => !row.IdFel && !String(row.Tip || '').trim() && !String(row.Explicatie || '').trim();
const dishKey = (row) => row.IdFel != null ? 'id:' + row.IdFel : 'text:' + String(row.Tip || '').trim().toLocaleLowerCase('ro');
const key = () => crypto.randomUUID();
const number = (value) => Number(String(value ?? '').replace(/\s/g, '').replace(',', '.')) || 0;
const dateText = (value) => format(value, { type: 'date' });
const amount = (value) => format(value, { type: 'decimal' });
export function mountLista(host, opts) {
  const state = { docs: [], lookups: { dishes: [] }, doc: null, draft: null, mode: 'view', dirty: false, canWrite: false, closed: false, zec: 4, request: null };
  const headUi = scope(), mealUi = scope(), bonsUi = scope(), treeUi = scope(), dialogs = scope();
  const inputs = new Map(); let disposed = false, generation = 0, balanceSequence = 0, openingBalance = null, bonsGrid;
  const status = el('span', { className: 'vc-save-status', role: 'status' });
  const title = el('h3'), pill = el('span', { className: 'vc-pill' });
  const head = el('div', { className: 'vc-grid vc-doc__header' }), meals = el('div', { className: 'vc-lista__meals' });
  const bons = el('div', { className: 'vc-lista__bons' }), bar = el('div', { className: 'vc-actions vc-doc__bar' }), treeHost = el('aside', { className: 'vc-doc__tree' });
  const totalsWrap = el('div', { className: 'vc-lista__totals-wrap' });
  const overview = el('div', { className: 'vc-lista__overview' }, head, bons);
  const root = el('section', { className: 'vc-doc vc-doc--lista' }, el('div', { className: 'vc-doc__main' }, el('div', { className: 'vc-doc__titlebar' }, title, pill), overview, meals, bar), treeHost);
  host.replaceChildren(root);
  const say = (text = '', bad = false) => {
    if (disposed) return;
    status.textContent = text; paintBar();
    showMessage(status, text, bad ? 'error' : 'info');
  };
  const report = (error) => { if (!disposed && error.name !== 'AbortError') say(error.message || 'Operația a eșuat.', true); };
  const query = () => new URLSearchParams(Object.entries(opts.sel()).filter(([, v]) => v != null && v !== ''));
  const url = (path, extra = '') => '/api/vercon/doc/lista' + path + '?' + query() + extra;
  const write = (method, path, body) => opts.api('/api/vercon/doc/lista' + path, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...opts.sel(), ...body }) });
  const editing = () => state.mode !== 'view';
  const fullEdit = () => editing() && state.mode !== 'portions';
  const canModify = doc => !!doc && !doc.annulled && (!doc.order_locked || doc.portions_editable || doc.date_editable);
  const editLabel = doc => doc?.order_locked ? doc.portions_editable ? 'Modifică data / numărul copiilor' : 'Modifică data' : 'Modifică';
  const dirty = () => state.dirty || headUi.dirty() || mealUi.dirty();
  const blankMeal = () => ({ P: '', VMD: '', D: '', items: [] });
  const fromDoc = (doc) => ({ header: { Numar: doc.number, Data: doc.header.Data, Comentarii: doc.header.Comentarii || '' },
    meals: Object.fromEntries(MEALS.map(([id]) => [id, { ...doc.meals[id], items: doc.meals[id].items.map((row) => ({ ...row, key: key() })) }])), bons: doc.bons.map((row) => ({ ...row })) });
  const value = () => editing() ? state.draft : state.doc ? fromDoc(state.doc) : null;
  async function commit() { return await headUi.commit() && await mealUi.commit(); }
  async function allowDiscard() { return !dirty() || await confirmBox('Renunțați la modificările nesalvate?', { title: 'Modificări nesalvate', yes: 'Renunț', no: 'Rămân aici', warning: true }); }
  async function refresh() { const data = await opts.api(url('/list')); if (disposed) return; state.docs = data.documents; state.canWrite = data.can_write && !data.closed; state.closed = data.closed; state.zec = data.zec; }
  async function openDoc(id) {
    if (!await allowDiscard()) return; const token = ++generation;
    const doc = await opts.api(url('/' + id)); if (disposed || token !== generation) return;
    state.doc = doc; rememberExplanations(doc); state.mode = 'view'; state.draft = null; state.dirty = false; state.request = null;
    say(); paint();
  }
  async function startAdd() {
    if (!await allowDiscard()) return; ++generation; state.mode = 'add'; state.dirty = false; state.request = null;
    state.draft = { header: { Numar: '', Data: state.lookups.today, Comentarii: '' }, meals: { md: blankMeal(), d: blankMeal(), c: blankMeal() }, bons: [] }; say(); paint();
  }
  function startEdit() {
    if (!state.doc || !state.canWrite || state.doc.annulled) return;
    if (!canModify(state.doc)) { say(state.doc.portions_message || state.doc.order_message, true); return; }
    ++generation; state.mode = state.doc.order_locked ? 'portions' : 'edit';
    state.draft = fromDoc(state.doc); state.dirty = false; state.request = null;
    say(state.mode === 'portions' ? 'Puteți modifica data între listele vecine, păstrând ordinea listelor.' + (state.doc.portions_editable ? ' Puteți modifica și numărul copiilor / porțiilor.' : '') : ''); paint();
  }
  async function discard() { if (!await allowDiscard()) return; ++generation; state.mode = 'view'; state.draft = null; state.dirty = false; state.request = null; say(); paint(); }
  function paint() {
    const v = value(); title.textContent = editing() ? 'Listă de alimente • ' + (state.mode === 'add' ? 'listă nouă' : state.mode === 'portions' ? 'modificare dată / număr copii' : 'modificare') : state.doc ? 'Listă de alimente • nr. ' + state.doc.number : 'Liste de alimente';
    pill.textContent = editing() ? 'Ciornă' : state.doc ? state.doc.annulled ? 'Anulată' : 'Salvată' : ''; pill.hidden = !pill.textContent;
    pill.dataset.state = editing() ? 'draft' : state.doc?.annulled ? 'annulled' : 'saved';
    headUi.clear(); inputs.clear(); head.replaceChildren(); totalsWrap.replaceChildren(); totalsWrap.hidden = !v; openingBalance = null;
    if (v) for (const spec of [{ key: 'Numar', label: 'Număr listă', type: 'int', min: 1 }, { key: 'Data', label: 'Data', type: 'date', required: true }, { key: 'Comentarii', label: 'Comentarii', type: 'text' }]) {
      const f = field(spec, v.header[spec.key], fullEdit() || state.mode === 'portions' && spec.key === 'Data' && state.doc?.date_editable, (raw) => {
        state.draft.header[spec.key] = raw; state.dirty = true;
        if (spec.key === 'Data') void refreshPriorTotals();
      }, headUi);
      if (spec.key === 'Numar') { f.input.placeholder = 'Automat'; f.input.title = 'Lăsați gol pentru numerotare automată.'; }
      if (spec.key === 'Data' && state.mode === 'portions') {
        f.input._picker.field.title = 'Data permisă: ' + (state.doc.date_minimum ? dateText(state.doc.date_minimum) : 'începutul anului')
          + ' – ' + (state.doc.date_maximum ? dateText(state.doc.date_maximum) : 'sfârșitul anului') + ', păstrând ordinea listelor.';
      }
      inputs.set(spec.key, f); head.append(f.node);
    } else head.append(el('p', { className: 'vc-nom__empty', textContent: 'Alegeți o listă salvată sau adăugați una nouă.' }));
    if (v) {
      const totalsHost = el('div', { className: 'vc-lista__totals' });
      for (const [name, label] of TOTALS) {
        const f = field({ key: name, label, type: 'text', textAlign: 'center' }, '', false, null, headUi);
        inputs.set(name, f); totalsHost.append(f.node);
      }
      totalsWrap.append(totalsHost);
      head.append(totalsWrap);
    }
    paintMeals(); paintBons(); paintBar(); paintSaved(); freezePending();
    void refreshPriorTotals();
  }
  function paintBalance(name, raw, previous = false) {
    const f = inputs.get(name); if (!f) return;
    let label = previous ? 'Sold anterior' : 'Sold';
    if (raw != null) label = (number(raw) < 0 ? 'Economii' : 'Depășiri') + (previous ? '\nAnterioare' : '');
    f.node.firstElementChild.textContent = label;
    f.input.setAttribute('aria-label', label.replace('\n', ' '));
    f.input._box.setValue(raw == null ? '' : formatMoney(String(raw).replace(/^-/, '')));
  }
  function paintPriorTotals(values, hint) {
    openingBalance = null;
    if (values?.economy !== '' && values?.economy != null) openingBalance = '-' + values.economy;
    else if (values?.overrun !== '' && values?.overrun != null) openingBalance = values.overrun;
    paintBalance('prior', openingBalance, true);
    const f = inputs.get('prior');
    if (f) f.input.title = f.node.title = hint;
    totals();
  }
  async function refreshPriorTotals() {
    const sequence = ++balanceSequence, token = generation, v = value();
    if (!v) return;
    const parsed = parse(v.header.Data, { type: 'date', required: true });
    if (!parsed.ok) { paintPriorTotals(null, 'Alegeți data listei pentru calcul.'); return; }
    const date = parsed.value;
    const isCurrent = () => !disposed && sequence === balanceSequence && token === generation && value()?.header.Data === date;
    paintPriorTotals(null, 'Se calculează soldul anterior datei listei…');
    const exclude = state.mode !== 'add' && state.doc ? '&exclude=' + state.doc.id : '';
    try {
      const values = await opts.api(url('/balance', '&date=' + encodeURIComponent(date) + exclude));
      if (isCurrent()) paintPriorTotals(values, 'Cumulat înainte de ' + dateText(date) + ', conform raportului.');
    } catch (error) {
      if (!isCurrent()) return;
      paintPriorTotals(null, 'Soldul anterior nu a putut fi calculat.'); report(error);
    }
  }
  function mealValue(meal) { return (number(meal.P) * number(meal.VMD)).toFixed(state.zec); }
  function rememberExplanations(doc) {
    const suggestions = state.lookups.explanations ||= [];
    for (const [id] of MEALS) for (const row of doc.meals[id].items) {
      if (row.Explicatie && !suggestions.some(item => dishKey(item) === dishKey(row) && item.Explicatie === row.Explicatie)) {
        suggestions.push({ IdFel: row.IdFel, Tip: row.Tip, Explicatie: row.Explicatie });
      }
    }
  }
  function explanationOptions(row) {
    const current = value();
    const items = [...(state.lookups.explanations || []), ...MEALS.flatMap(([id]) => current?.meals[id].items || [])];
    const texts = items.filter(item => dishKey(item) === dishKey(row)).map(item => String(item.Explicatie || '').trim()).filter(Boolean);
    return [...new Set(texts)].sort((a, b) => a.localeCompare(b, 'ro')).map(text => ({ value: text, label: text }));
  }
  function paintMeals() {
    mealUi.clear(); meals.replaceChildren(); const v = value(); if (!v) return;
    for (const [id, label] of MEALS) {
      const meal = v.meals[id], panel = el('section', { className: 'vc-lista__meal' }), summaryHost = el('div', { className: 'vc-lista__summary' }), dishesHost = el('div', { className: 'vc-lista__dishes' });
      panel.append(el('h4', { textContent: label }), summaryHost, dishesHost); meals.append(panel);
      const summaryRow = { key: id, P: meal.P, VMD: meal.VMD, V: editing() ? mealValue(meal) : meal.V };
      let dishGrid;
      const summary = grid(summaryHost, { layoutId: 'vercon.lista.' + id + '.summary.v2', proportionalWidths: true, rowKey: 'key', rows: [summaryRow], editable: editing(), footer: false,
        columns: [
          column({ key: 'P', label: 'Porții', type: 'int', min: 0 }, { width: 85, editable: state.mode === 'add' || !!state.doc?.portions_editable, filter: false, sortable: false }),
          column({ key: 'VMD', label: 'Preț', type: 'decimal', min: 0 }, { width: 100, editable: fullEdit(), filter: false, sortable: false }),
          column({ key: 'V', label: 'Valoare', type: 'decimal' }, { width: 115, editable: false, filter: false, sortable: false })],
        onCellSave: ({ key: cell, value: raw }) => { meal[cell] = raw ?? ''; state.dirty = true; return { [cell]: raw, V: mealValue(meal) }; },
        onCellSaved: () => totals(),
      }, mealUi);
      const dropDish = (row) => {
        meal.items = meal.items.filter(item => item.key !== row.key);
        dishGrid.setRows(meal.items, { keepWidths: true });
      };
      dishGrid = grid(dishesHost, { layoutId: 'vercon.lista.' + id + '.dishes.v2', layout: { fill: 'Explicatie' }, rowKey: 'key', rows: meal.items, editable: fullEdit(), singleClickEdit: true,
        newRowAlwaysVisible: fullEdit(), isBlankRow: isBlankDish, onNewRow: () => { const row = blankDish(); meal.items.push(row); return row; }, onCancelNewRow: dropDish,
        columns: [
          column({ key: 'Tip', label: 'FELUL', type: 'text', max: 50 }, { width: 57, minWidth: 57, fixedWidth: true, editor: 'list', dropDownMinWidth: 120,
            dropDownOnEnter: row => !row.Tip && row.IdFel == null,
            options: row => state.lookups.dishes.filter(dish => !meal.items.some(item => item.key !== row.key && (
              item.IdFel != null && String(item.IdFel) === String(dish.id)
              || String(item.Tip || '').trim().toLocaleLowerCase('ro') === dish.label.trim().toLocaleLowerCase('ro')
            ))).map(dish => ({ value: dish.label, label: dish.label })), filter: false, sortable: false }),
          column({ key: 'Explicatie', label: 'Explicații', type: 'text', max: 255 }, { width: 100, editor: 'list', searchable: true, allowCustom: true, showArrow: true, dropDownMinWidth: 300, options: explanationOptions, filter: false, sortable: false }),
          { key: 'actions', title: '⋮', width: 30, minWidth: 30, editable: false, filter: false, sortable: false, display: 'button', actionText: '⋮' }],
        footer: false,
        onCellSave: ({ row, key: cell, value: raw }) => {
          const item = meal.items.find((it) => it.key === row.key); const patch = { [cell]: raw };
          if (cell === 'Tip') {
            patch.IdFel = state.lookups.dishes.find((dish) => dish.label === raw)?.id ?? null;
            if (raw && meal.items.some(other => other.key !== row.key && dishKey(other) === dishKey({ ...patch, Tip: raw }))) {
              throw new Error('Felul ales este deja folosit la această masă.');
            }
          }
          Object.assign(item, patch); dishGrid.ensureNewRow(); state.dirty = true; return patch;
        },
        onEditError: ({ error }) => report(error),
        onCellAction: ({ row }) => rowMenu(dishGrid, row, [{ label: 'Elimină rândul', disabled: !fullEdit(), run: async () => {
          if (!await commit()) return; dropDish(row); state.dirty = true;
        } }]),
      }, mealUi);
      void summary;
    }
  }
  function paintBons() {
    bonsUi.clear(); bons.replaceChildren(); bonsGrid = null; const v = value(); if (!v) return;
    const gridHost = el('div');
    bons.append(el('h4', { textContent: 'Bonuri de consum asociate' }), gridHost);
    bonsGrid = grid(gridHost, { layoutId: 'vercon.lista.bons.compact-v1', proportionalWidths: true, rowKey: 'id', rows: v.bons.map((row, index) => ({ ...row, ordinal: index + 1 })), editable: false,
      columns: [
        { key: 'ordinal', title: '#', width: 40 },
        column({ key: 'number', label: 'Număr BC', type: 'int' }),
        column({ key: 'date', label: 'Data', type: 'date' }),
        column({ key: 'receiver', label: 'Primitor' }, { width: 240 }),
        column({ key: 'total', label: 'Valoare BC', type: 'decimal' }),
        { key: 'actions', title: '⋮', width: 36, display: 'button', actionText: '⋮' }],
      footer: true, footerCaption: '', footerAction: { label: '+ Adaugă rând', span: 2, disabled: !fullEdit() }, onFooterAction: () => chooseBon().catch(report),
      onCellAction: ({ row }) => rowMenu(bonsGrid, row, [{ label: 'Scoate BC din listă', disabled: !fullEdit(), run: async () => {
        if (!await commit()) return; state.draft.bons = state.draft.bons.filter((item) => item.id !== row.id); state.dirty = true; paintBons();
      } }]),
    }, bonsUi);
    totals();
  }
  function totals() {
    const v = value(); if (!v || !bonsGrid) return;
    const listTotal = editing() ? MEALS.reduce((sum, [id]) => sum + number(mealValue(v.meals[id])), 0).toFixed(state.zec) : state.doc.header.Valoare;
    const bcTotal = editing() ? v.bons.filter((row) => !row.annulled).reduce((sum, row) => sum + number(row.total), 0).toFixed(state.zec) : state.doc.header.bons_total;
    const diff = editing() ? (number(listTotal) - number(bcTotal)).toFixed(state.zec) : state.doc.header.diff;
    bonsGrid.setFooter({ values: { receiver: 'TOTAL BC', total: amount(bcTotal) } });
    for (const [name, raw] of [['Valoare', listTotal], ['bons_total', bcTotal], ['diff', diff]]) inputs.get(name)?.input._box.setValue(formatMoney(raw));
    // Report sign: closing balance = previous balance - (list value - BC total).
    const opposite = diff == null ? null : String(diff).startsWith('-') ? String(diff).slice(1) : '-' + diff;
    const closing = openingBalance == null || opposite == null ? null : receiptTotal([{ balance: openingBalance }, { balance: opposite }], 'balance', 4);
    paintBalance('closing', closing);
  }
  async function chooseBon() {
    if (!fullEdit() || !await commit()) return; const token = generation, draft = state.draft;
    const data = await opts.api(url('/bons', state.mode === 'edit' ? '&exclude=' + state.doc.id : '')); if (disposed || token !== generation || draft !== state.draft) return;
    const available = data.bons.filter((row) => !draft.bons.some((item) => String(item.id) === String(row.id)));
    const popup = modal('Alege B.C.', dialogs), tree = el('div', { className: 'vc-bon-picker' }), detail = el('p', { className: 'vc-hint' });
    const add = el('button', { type: 'button', textContent: 'Adaugă BC', disabled: true, className: 'is-primary' }), cancel = el('button', { type: 'button', textContent: 'Renunță' });
    let selected; popup.body.append(tree, detail); popup.footer.append(cancel, add); cancel.addEventListener('click', popup.close);
    const nodes = groupedNodes(available, [(row) => row.receiver, (row) => dateText(row.date)], (row) => row.number + ' • ' + amount(row.total) + ' lei',
      [null, (row) => ({ sortType: 'date', sortValue: row.date, sortDirection: 'desc' }),
        (row) => ({ sortType: 'date', sortValue: row.date, sortDirection: 'desc', sortThen: [{ type: 'number', value: numberValue(row.number) }, { type: 'number', value: row.id }] })]);
    const stripActions = (rows) => rows.forEach((node) => { delete node.rightIcon; if (node.children) stripActions(node.children); }); stripActions(nodes);
    paintTree(tree, nodes, { title: 'Bonuri disponibile', onSelect: (id) => { selected = available.find((row) => String(row.id) === id); add.disabled = !selected; detail.textContent = selected ? selected.number + ' • ' + selected.receiver + ' • ' + amount(selected.total) + ' lei' : ''; } }, popup.scope);
    if (!available.length) detail.textContent = 'Nu există bonuri de consum disponibile.';
    add.addEventListener('click', () => {
      if (!selected || token !== generation || state.draft !== draft || draft.bons.some((row) => String(row.id) === String(selected.id))) return;
      draft.bons.push({ ...selected, annulled: false }); state.dirty = true; popup.close(); paintBons();
    });
  }
  function paintSaved() {
    treeUi.clear(); treeHost.hidden = editing(); if (editing()) { treeHost.replaceChildren(); return; }
    const months = new Map();
    for (const row of state.docs) {
      const month = row.date?.slice(0, 7) || '';
      if (!months.has(month)) months.set(month, []);
      months.get(month).push(row);
    }
    const nodes = [...months].sort(([a], [b]) => b.localeCompare(a)).map(([month, rows]) => ({
      id: 'month:' + month,
      label: month ? MONTHS[Number(month.slice(5)) - 1] + '/' + month.slice(0, 4) : 'Fără dată',
      sortType: 'date', sortValue: month ? `${month}-01` : null, sortDirection: 'desc',
      children: rows.map(row => ({ ...docNode(row, 'Lista ' + row.number + ' • ' + dateText(row.date) + (row.annulled ? ' • anulată' : ''), formatMoney(row.total)),
        sortType: 'date', sortValue: row.date, sortDirection: 'desc', sortThen: [{ type: 'number', value: row.number }] })),
    }));
    paintTree(treeHost, nodes, { title: 'Liste salvate', showSearchBox: false, collapsed: true, selected: state.doc?.id, onSelect: (id) => openDoc(id).catch(report), onAction: (id, anchor) => openMenu(anchor, [
      { label: 'Deschide', run: () => openDoc(id) },
      { label: editLabel(state.docs.find(row => String(row.id) === id)), disabled: !state.canWrite || !canModify(state.docs.find(row => String(row.id) === id)), run: async () => { await openDoc(id); if (!disposed && state.doc && String(state.doc.id) === id) startEdit(); } },
      { label: 'Vizualizare', run: async () => { await openDoc(id); if (!disposed && state.doc && String(state.doc.id) === id) await showPreview(); } },
      { label: 'Istoric', run: async () => { await openDoc(id); if (!disposed && state.doc && String(state.doc.id) === id) await history(); } },
      { label: 'Anulează lista', disabled: !state.canWrite || state.docs.some((row) => String(row.id) === id && (row.annulled || row.order_locked)), run: async () => { await openDoc(id); if (!disposed && state.doc && String(state.doc.id) === id) await cancelList(); } },
    ]) }, treeUi);
    treeUi.own(new ActionAddButton(treeHost.querySelector(':scope > h4'), {
      className: 'vc-lista__tree-add', title: 'Adaugă listă de alimente', disabled: !state.canWrite,
      onClick: () => startAdd().catch(report),
    }));
  }
  async function showPreview() {
    if (!await commit()) return; const v = value(); if (!v) return;
    const sections = [{ title: 'Lista nr. ' + (v.header.Numar || 'automat') + ' • ' + dateText(v.header.Data), columns: [column({ key: 'Comentarii', label: 'Comentarii' }, { width: 600 })], rows: [v.header] }];
    for (const [id, label] of MEALS) {
      const m = v.meals[id]; sections.push({ title: label, columns: [column({ key: 'P', label: 'Porții', type: 'int' }), column({ key: 'VMD', label: 'Preț', type: 'decimal' }), column({ key: 'V', label: 'Valoare', type: 'decimal' })], rows: [{ ...m, V: editing() ? mealValue(m) : m.V }] },
        { title: 'Feluri • ' + label, columns: [column({ key: 'Tip', label: 'FELUL' }), column({ key: 'Explicatie', label: 'Explicații' }, { width: 480 })], rows: m.items.filter(row => !isBlankDish(row)) });
    }
    sections.push({ title: 'Bonuri asociate', columns: [column({ key: 'number', label: 'BC', type: 'int' }), column({ key: 'date', label: 'Data', type: 'date' }), column({ key: 'receiver', label: 'Primitor' }, { width: 260 }), column({ key: 'total', label: 'Valoare', type: 'decimal' })], rows: v.bons });
    const token = generation, draft = state.draft, doc = editing() ? null : state.doc;
    await documentReport({ api: opts.api, selection: opts.sel(), family: 'lista', document: doc,
      title: editing() ? 'Listă de alimente • Ciornă nesalvată' + (v.header.Numar ? ' nr. ' + v.header.Numar : '') : 'Listă de alimente nr. ' + state.doc.number,
      date: v.header.Data,
      sections, owner: dialogs, isCurrent: () => !disposed && token === generation && draft === state.draft });
  }
  function paintBar() {
    const button = (label, run, disabled = false, className = '') => { const b = el('button', { type: 'button', textContent: label, disabled, className }); b.addEventListener('click', () => Promise.resolve(run()).catch(report)); return b; };
    bar.classList.toggle('vc-lista__bar--editing', editing());
    const note = state.closed ? el('span', { className: 'vc-hint', textContent: 'Perioada este închisă.', 'data-kbot-tooltip-overflow': '', tabIndex: 0 }) : null;
    if (editing()) {
      statusBar(bar, { start: [button('Renunță', discard, false, 'vc-lista__cancel')], status, note,
        end: [button('Salvează lista', save, false, 'is-primary vc-lista__save')] });
    } else {
      statusBar(bar, { start: state.doc ? [button('Vizualizare', showPreview)] : [], status, note,
        end: state.doc ? [button('Modifică', startEdit, !state.canWrite || !canModify(state.doc))] : [] });
    }
  }
  function collect() {
    const errors = [], header = {}, draft = state.draft, outputMeals = {};
    for (const spec of [{ key: 'Numar', type: 'int', min: 1 }, { key: 'Data', type: 'date', required: true }]) {
      const result = parse(draft.header[spec.key], spec); inputs.get(spec.key).error.textContent = result.ok ? '' : result.message;
      if (!result.ok) errors.push(result.message); else header[spec.key] = result.value;
    }
    header.Comentarii = (draft.header.Comentarii || '').trim() || null;
    for (const [id, label] of MEALS) {
      const m = draft.meals[id], out = { items: m.items.filter(row => !isBlankDish(row)).map((row) => ({ IdFel: row.IdFel, Tip: (row.Tip || '').trim() || null, Explicatie: (row.Explicatie || '').trim() })) };
      for (const name of ['P', 'VMD', 'D']) { const result = parse(m[name], { type: name === 'VMD' ? 'decimal' : 'int', min: 0 }); if (!result.ok) errors.push(label + ': ' + result.message); out[name] = result.value ?? 0; } outputMeals[id] = out;
    }
    return { errors, body: { header, meals: outputMeals, bons: draft.bons.map((row) => row.id) } };
  }
  async function history() {
    const doc = state.doc;
    if (doc) await showDocumentHistory({ api: opts.api, url: url('/' + doc.id + '/history'),
      title: 'Istoric • Lista de alimente nr. ' + doc.number, owner: dialogs, isCurrent: () => !disposed && state.doc === doc });
  }
  function freezePending() { head.inert = meals.inert = bons.inert = !!state.request; }
  function collectPortions() {
    const portions = {}, errors = [];
    const parsedDate = parse(state.draft.header.Data, { type: 'date', required: true });
    inputs.get('Data').error.textContent = parsedDate.ok ? '' : parsedDate.message;
    if (!parsedDate.ok) errors.push(parsedDate.message);
    if (!state.doc.portions_editable) return { errors, body: { date: parsedDate.value } };
    for (const [id, label] of MEALS) {
      const raw = state.draft.meals[id].P;
      const result = parse(raw == null || raw === '' ? 0 : raw, { type: 'int', min: 0, required: true });
      if (!result.ok) errors.push(label + ': ' + result.message);
      else portions[id] = result.value;
    }
    const changed = MEALS.some(([id]) => number(portions[id]) !== number(state.doc.meals[id].P));
    return { errors, body: { date: parsedDate.value, ...(changed ? { portions } : {}) } };
  }
  async function save() {
    if (!state.canWrite || !editing() || !await commit()) return;
    const portionsOnly = state.mode === 'portions', collected = portionsOnly ? collectPortions() : collect();
    if (collected.errors.length) { say(collected.errors.join(' '), true); return; }
    if (!portionsOnly && !collected.body.bons.length && !await confirmBox('Salvați lista fără bonuri de consum?', { title: 'Listă fără bonuri', yes: 'Salvează', no: 'Revin' })) return;
    // Retain the exact body after an uncertain POST response; retries must carry the same request.
    const adding = state.mode === 'add'; const request = state.request || { body: collected.body, key: key() };
    if (adding) state.request = request; root.inert = true; say('Se salvează…');
    try {
      const result = adding ? await write('POST', '', { ...request.body, request_key: request.key })
        : await write('PUT', '/' + state.doc.id + (portionsOnly ? '/portions' : ''), { ...collected.body, version: state.doc.version });
      if (disposed) return; ++generation; state.doc = result.document; rememberExplanations(state.doc); state.draft = null; state.mode = 'view'; state.dirty = false; state.request = null;
      paint(); say('Salvat.'); try { await refresh(); if (!disposed) paintSaved(); } catch (error) { report(error); }
    } catch (error) {
      if (error.status && error.status < 500) state.request = null;
      report(error); if (state.request) say('Răspunsul salvării nu este confirmat. Salvează retrimite aceeași listă. Păstrați ciorna până la confirmare.', true);
    } finally { if (!disposed) { root.inert = false; freezePending(); } }
  }
  async function cancelList() {
    const doc = state.doc; if (!doc || doc.annulled || !state.canWrite) return;
    if (doc.order_locked) { say(doc.order_message, true); return; }
    const reason = await reasonBox('Anulați lista nr. ' + doc.number + '? Bonurile pot fi asociate altei liste.', { title: 'Anulare listă', yes: 'Anulează lista' }); if (!reason || disposed) return;
    root.inert = true; try { const result = await write('POST', '/' + doc.id + '/cancel', { reason, version: doc.version }); if (disposed) return; state.doc = result.document; paint(); say('Anulată.'); await refresh(); if (!disposed) paintSaved(); } finally { if (!disposed) root.inert = false; }
  }
  const ready = Promise.all([refresh(), opts.api(url('/lookups'))]).then(([, lookups]) => { if (!disposed) { state.lookups = lookups; paint(); } }).catch(report); paint();
  return { ready, isDirty: dirty,
    destroy: () => { disposed = true; ++generation; for (const owner of [headUi, mealUi, bonsUi, treeUi, dialogs]) owner.clear(); host.replaceChildren(); } };
}
