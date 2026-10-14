// Lista de alimente: one draft, three meals and a set of existing BC links.
import { confirmBox, reasonBox } from '../utils/confirm-box.js';
import { format, parse } from './fields.js';
import { el, scope, field, column, grid, paintTree, groupedNodes, modal, rowMenu, openMenu, showMessage } from './ui.js';
import { documentReport } from './document-report.js';

const MEALS = [['md', 'Mic dejun'], ['d', 'Dejun'], ['c', 'Cină / Gustare']];
const key = () => crypto.randomUUID();
const number = (value) => Number(String(value ?? '').replace(/\s/g, '').replace(',', '.')) || 0;
const dateText = (value) => format(value, { type: 'date' });
const amount = (value) => format(value, { type: 'decimal' });
export function mountLista(host, opts) {
  const state = { docs: [], lookups: { dishes: [] }, doc: null, draft: null, mode: 'view', dirty: false, canWrite: false, closed: false, zec: 4, request: null };
  const headUi = scope(), mealUi = scope(), bonsUi = scope(), treeUi = scope(), dialogs = scope();
  const inputs = new Map(); let disposed = false, generation = 0, bonsGrid;
  const status = el('span', { className: 'vc-save-status', role: 'status' });
  const title = el('h3'), pill = el('span', { className: 'vc-pill' });
  const head = el('div', { className: 'vc-grid vc-doc__header' }), meals = el('div', { className: 'vc-lista__meals' });
  const bons = el('div', { className: 'vc-lista__bons' }), bar = el('div', { className: 'vc-actions vc-doc__bar' }), treeHost = el('aside', { className: 'vc-doc__tree' });
  const overview = el('div', { className: 'vc-lista__overview' }, head, bons);
  const root = el('section', { className: 'vc-doc vc-doc--lista' }, el('div', { className: 'vc-doc__main' }, el('div', { className: 'vc-doc__titlebar' }, title, pill), overview, meals, bar), treeHost);
  host.replaceChildren(root);
  const say = (text = '', bad = false) => { if (!disposed) showMessage(status, text, bad ? 'error' : 'info'); };
  const report = (error) => { if (!disposed && error.name !== 'AbortError') say(error.message || 'Operația a eșuat.', true); };
  const query = () => new URLSearchParams(Object.entries(opts.sel()).filter(([, v]) => v != null && v !== ''));
  const url = (path, extra = '') => '/api/vercon/doc/lista' + path + '?' + query() + extra;
  const write = (method, path, body) => opts.api('/api/vercon/doc/lista' + path, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...opts.sel(), ...body }) });
  const editing = () => state.mode !== 'view';
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
    state.doc = doc; state.mode = 'view'; state.draft = null; state.dirty = false; state.request = null; say(); paint();
  }
  async function startAdd() {
    if (!await allowDiscard()) return; ++generation; state.mode = 'add'; state.dirty = false; state.request = null;
    state.draft = { header: { Numar: '', Data: state.lookups.today, Comentarii: '' }, meals: { md: blankMeal(), d: blankMeal(), c: blankMeal() }, bons: [] }; say(); paint();
  }
  function startEdit() { ++generation; state.mode = 'edit'; state.draft = fromDoc(state.doc); state.dirty = false; state.request = null; say(); paint(); }
  async function discard() { if (!await allowDiscard()) return; ++generation; state.mode = 'view'; state.draft = null; state.dirty = false; state.request = null; say(); paint(); }
  function paint() {
    const v = value(); title.textContent = editing() ? 'Listă de alimente • ' + (state.mode === 'add' ? 'listă nouă' : 'modificare') : state.doc ? 'Listă de alimente • nr. ' + state.doc.number : 'Liste de alimente';
    pill.textContent = editing() ? 'Ciornă' : state.doc ? state.doc.annulled ? 'Anulată' : 'Salvată' : ''; pill.hidden = !pill.textContent;
    pill.dataset.state = editing() ? 'draft' : state.doc?.annulled ? 'annulled' : 'saved';
    headUi.clear(); inputs.clear(); head.replaceChildren();
    if (v) for (const spec of [{ key: 'Numar', label: 'Număr listă (gol = automat)', type: 'int', min: 1 }, { key: 'Data', label: 'Data', type: 'date', required: true }, { key: 'Comentarii', label: 'Comentarii', type: 'text' }]) {
      const f = field(spec, v.header[spec.key], editing(), (raw) => { state.draft.header[spec.key] = raw; state.dirty = true; }, headUi);
      inputs.set(spec.key, f); head.append(f.node);
    } else head.append(el('p', { className: 'vc-nom__empty', textContent: 'Alegeți o listă salvată sau adăugați una nouă.' }));
    paintMeals(); paintBons(); paintBar(); paintSaved(); freezePending();
  }
  function mealValue(meal) { return (number(meal.P) * number(meal.VMD)).toFixed(state.zec); }
  function paintMeals() {
    mealUi.clear(); meals.replaceChildren(); const v = value(); if (!v) return;
    for (const [id, label] of MEALS) {
      const meal = v.meals[id], panel = el('section', { className: 'vc-lista__meal' }), summaryHost = el('div', { className: 'vc-lista__summary' }), dishesHost = el('div', { className: 'vc-lista__dishes' });
      panel.append(el('h4', { textContent: label }), summaryHost, dishesHost); meals.append(panel);
      const summaryRow = { key: id, P: meal.P, VMD: meal.VMD, V: editing() ? mealValue(meal) : meal.V, D: meal.D };
      let dishGrid;
      const summary = grid(summaryHost, { layoutId: 'vercon.lista.' + id + '.summary.compact-v1', proportionalWidths: true, rowKey: 'key', rows: [summaryRow], editable: editing(), footer: false,
        columns: [
          column({ key: 'P', label: 'P · Porții', type: 'int', min: 0 }, { width: 85, filter: false, sortable: false }),
          column({ key: 'VMD', label: 'VMD · Lei/porție', type: 'decimal', min: 0 }, { width: 125, filter: false, sortable: false }),
          column({ key: 'V', label: 'V · Lei/masă', type: 'decimal' }, { width: 125, editable: false, filter: false, sortable: false }),
          column({ key: 'D', label: 'D · Distribuit', type: 'int', min: 0 }, { width: 105, filter: false, sortable: false })],
        onCellSave: ({ key: cell, value: raw }) => { meal[cell] = raw ?? ''; state.dirty = true; return { [cell]: raw, V: mealValue(meal) }; },
        onCellSaved: () => { if (!dishGrid.hasEdit) dishGrid.setRows(meal.items.map((row, index) => ({ ...row, ordinal: index + 1, portions: meal.P }))); totals(); },
      }, mealUi);
      dishGrid = grid(dishesHost, { layoutId: 'vercon.lista.' + id + '.dishes.compact-v1', proportionalWidths: true, rowKey: 'key', rows: meal.items.map((row, index) => ({ ...row, ordinal: index + 1, portions: meal.P })), editable: editing(),
        columns: [
          { key: 'ordinal', title: '#', width: 36, editable: false, filter: false, sortable: false },
          column({ key: 'portions', label: 'Porții', type: 'int' }, { width: 60, editable: false, filter: false, sortable: false }),
          column({ key: 'Tip', label: 'Fel de mâncare', type: 'text' }, { width: 130, filter: false, sortable: false }),
          column({ key: 'Explicatie', label: 'Explicații', type: 'text' }, { width: 200, filter: false, sortable: false }),
          { key: 'actions', title: '⋮', width: 36, editable: false, filter: false, sortable: false, display: 'button', actionText: '⋮' }],
        footer: true, footerCaption: '', footerAction: { label: '+ Adaugă rând', span: 2, disabled: !editing() }, onFooterAction: async () => {
          if (!await commit()) return; const row = { key: key(), IdFel: null, Tip: '', Explicatie: '' }; meal.items.push(row); state.dirty = true; paintMeals();
        },
        onCellSave: ({ row, key: cell, value: raw }) => {
          const item = meal.items.find((it) => it.key === row.key); const patch = { [cell]: cell === 'IdFel' && raw ? Number(raw) : raw };
          Object.assign(item, patch); state.dirty = true; return patch;
        },
        onCellAction: ({ row }) => rowMenu(dishGrid, row, [{ label: 'Elimină rândul', disabled: !editing(), run: async () => {
          if (!await commit()) return; meal.items = meal.items.filter((it) => it.key !== row.key); state.dirty = true; paintMeals();
        } }]),
      }, mealUi);
      void summary;
    }
  }
  function paintBons() {
    bonsUi.clear(); bons.replaceChildren(); bonsGrid = null; const v = value(); if (!v) return;
    const gridHost = el('div'), sumsHost = el('div', { className: 'vc-lista__summary' });
    bons.append(el('h4', { textContent: 'Bonuri de consum asociate' }), gridHost, sumsHost);
    bonsGrid = grid(gridHost, { layoutId: 'vercon.lista.bons.compact-v1', proportionalWidths: true, rowKey: 'id', rows: v.bons.map((row, index) => ({ ...row, ordinal: index + 1 })), editable: false,
      columns: [
        { key: 'ordinal', title: '#', width: 40 },
        column({ key: 'number', label: 'Număr BC', type: 'int' }),
        column({ key: 'date', label: 'Data', type: 'date' }),
        column({ key: 'receiver', label: 'Primitor' }, { width: 240 }),
        column({ key: 'total', label: 'Valoare BC', type: 'decimal' }),
        { key: 'actions', title: '⋮', width: 36, display: 'button', actionText: '⋮' }],
      footer: true, footerCaption: '', footerAction: { label: '+ Adaugă rând', span: 2, disabled: !editing() }, onFooterAction: () => chooseBon().catch(report),
      onCellAction: ({ row }) => rowMenu(bonsGrid, row, [{ label: 'Scoate BC din listă', disabled: !editing(), run: async () => {
        if (!await commit()) return; state.draft.bons = state.draft.bons.filter((item) => item.id !== row.id); state.dirty = true; paintBons();
      } }]),
    }, bonsUi);
    const sums = grid(sumsHost, { rows: [], proportionalWidths: true, rowKey: 'key', editable: false, footer: false, columns: [
      column({ key: 'list', label: 'Valoare listă', type: 'decimal' }, { width: 180 }),
      column({ key: 'bons', label: 'Total BC', type: 'decimal' }, { width: 180 }),
      column({ key: 'diff', label: 'Diferență', type: 'decimal' }, { width: 180 })] }, bonsUi);
    bonsGrid._sums = sums; totals();
  }
  function totals() {
    const v = value(); if (!v || !bonsGrid) return;
    const listTotal = editing() ? MEALS.reduce((sum, [id]) => sum + number(mealValue(v.meals[id])), 0).toFixed(state.zec) : state.doc.header.Valoare;
    const bcTotal = editing() ? v.bons.filter((row) => !row.annulled).reduce((sum, row) => sum + number(row.total), 0).toFixed(state.zec) : state.doc.header.bons_total;
    const diff = editing() ? (number(listTotal) - number(bcTotal)).toFixed(state.zec) : state.doc.header.diff;
    bonsGrid.setFooter({ values: { receiver: 'TOTAL BC', total: amount(bcTotal) } }); bonsGrid._sums.setRows([{ key: 'total', list: listTotal, bons: bcTotal, diff }]);
  }
  async function chooseBon() {
    if (!editing() || !await commit()) return; const token = generation, draft = state.draft;
    const data = await opts.api(url('/bons', state.mode === 'edit' ? '&exclude=' + state.doc.id : '')); if (disposed || token !== generation || draft !== state.draft) return;
    const available = data.bons.filter((row) => !draft.bons.some((item) => String(item.id) === String(row.id)));
    const popup = modal('Alege B.C.', dialogs), tree = el('div', { className: 'vc-bon-picker' }), detail = el('p', { className: 'vc-hint' });
    const add = el('button', { type: 'button', textContent: 'Adaugă BC', disabled: true, className: 'is-primary' }), cancel = el('button', { type: 'button', textContent: 'Renunță' });
    let selected; popup.body.append(tree, detail); popup.footer.append(cancel, add); cancel.addEventListener('click', popup.close);
    const nodes = groupedNodes(available, [(row) => row.receiver, (row) => dateText(row.date)], (row) => 'BC ' + row.number + ' • ' + amount(row.total) + ' lei');
    const stripActions = (rows) => rows.forEach((node) => { delete node.rightIcon; if (node.children) stripActions(node.children); }); stripActions(nodes);
    paintTree(tree, nodes, { title: 'Bonuri disponibile', onSelect: (id) => { selected = available.find((row) => String(row.id) === id); add.disabled = !selected; detail.textContent = selected ? 'BC ' + selected.number + ' • ' + selected.receiver + ' • ' + amount(selected.total) + ' lei' : ''; } }, popup.scope);
    if (!available.length) detail.textContent = 'Nu există bonuri de consum disponibile.';
    add.addEventListener('click', () => {
      if (!selected || token !== generation || state.draft !== draft || draft.bons.some((row) => String(row.id) === String(selected.id))) return;
      draft.bons.push({ ...selected, annulled: false }); state.dirty = true; popup.close(); paintBons();
    });
  }
  function paintSaved() {
    treeUi.clear(); treeHost.hidden = editing(); if (editing()) { treeHost.replaceChildren(); return; }
    const nodes = groupedNodes(state.docs, [(row) => row.date?.slice(0, 4), (row) => row.date?.slice(0, 7), (row) => dateText(row.date)], (row) => 'Lista ' + row.number + ' • ' + amount(row.total) + ' lei' + (row.annulled ? ' • anulată' : ''));
    paintTree(treeHost, nodes, { title: 'Liste salvate', showSearchBox: false, collapsed: true, selected: state.doc?.id, onSelect: (id) => openDoc(id).catch(report), onAction: (id, anchor) => openMenu(anchor, [
      { label: 'Deschide', run: () => openDoc(id) },
      { label: 'Modifică', disabled: !state.canWrite || state.docs.find((row) => String(row.id) === id)?.annulled, run: async () => { await openDoc(id); if (!disposed && state.doc && String(state.doc.id) === id) startEdit(); } },
      { label: 'Previzualizare', run: async () => { await openDoc(id); if (!disposed && state.doc && String(state.doc.id) === id) await showPreview(); } },
      { label: 'Anulează lista', disabled: !state.canWrite || state.docs.find((row) => String(row.id) === id)?.annulled, run: async () => { await openDoc(id); if (!disposed && state.doc && String(state.doc.id) === id) await cancelList(); } },
    ]) }, treeUi);
  }
  async function showPreview() {
    if (!await commit()) return; const v = value(); if (!v) return;
    const sections = [{ title: 'Lista nr. ' + (v.header.Numar || 'automat') + ' • ' + dateText(v.header.Data), columns: [column({ key: 'Comentarii', label: 'Comentarii' }, { width: 600 })], rows: [v.header] }];
    for (const [id, label] of MEALS) {
      const m = v.meals[id]; sections.push({ title: label, columns: ['P', 'VMD', 'V', 'D'].map((name) => column({ key: name, label: name, type: 'decimal' })), rows: [{ ...m, V: editing() ? mealValue(m) : m.V }] },
        { title: 'Feluri • ' + label, columns: [column({ key: 'Tip', label: 'Fel de mâncare' }), column({ key: 'Explicatie', label: 'Explicații' }, { width: 480 })], rows: m.items });
    }
    sections.push({ title: 'Bonuri asociate', columns: [column({ key: 'number', label: 'BC', type: 'int' }), column({ key: 'date', label: 'Data', type: 'date' }), column({ key: 'receiver', label: 'Primitor' }, { width: 260 }), column({ key: 'total', label: 'Valoare', type: 'decimal' })], rows: v.bons });
    const token = generation, draft = state.draft, doc = editing() ? null : state.doc;
    await documentReport({ api: opts.api, selection: opts.sel(), family: 'lista', document: doc,
      title: editing() ? 'Listă de alimente • Ciornă nesalvată' : 'Listă de alimente nr. ' + state.doc.number,
      sections, owner: dialogs, isCurrent: () => !disposed && token === generation && draft === state.draft });
  }
  function paintBar() {
    bar.replaceChildren(); const button = (label, run, disabled = false) => { const b = el('button', { type: 'button', textContent: label, disabled }); b.addEventListener('click', () => Promise.resolve(run()).catch(report)); return b; };
    if (editing()) bar.append(button('Renunță', discard), button('Previzualizare', showPreview), button('Salvează lista', save));
    else { if (state.canWrite) bar.append(button('+ Adaugă', startAdd)); if (state.doc) bar.append(button('Modifică', startEdit, !state.canWrite || state.doc.annulled), button('Previzualizare', showPreview)); }
    bar.append(status); if (state.closed) bar.append(el('span', { className: 'vc-hint', textContent: 'Perioada este închisă.' }));
  }
  function collect() {
    const errors = [], header = {}, draft = state.draft, outputMeals = {};
    for (const spec of [{ key: 'Numar', type: 'int', min: 1 }, { key: 'Data', type: 'date', required: true }]) {
      const result = parse(draft.header[spec.key], spec); inputs.get(spec.key).error.textContent = result.ok ? '' : result.message;
      if (!result.ok) errors.push(result.message); else header[spec.key] = result.value;
    }
    header.Comentarii = (draft.header.Comentarii || '').trim() || null;
    for (const [id, label] of MEALS) {
      const m = draft.meals[id], out = { items: m.items.map((row) => ({ IdFel: row.IdFel, Tip: (row.Tip || '').trim() || null, Explicatie: (row.Explicatie || '').trim() })) };
      for (const name of ['P', 'VMD', 'D']) { const result = parse(m[name], { type: name === 'VMD' ? 'decimal' : 'int', min: 0 }); if (!result.ok) errors.push(label + ': ' + result.message); out[name] = result.value ?? 0; } outputMeals[id] = out;
    }
    return { errors, body: { header, meals: outputMeals, bons: draft.bons.map((row) => row.id) } };
  }
  function freezePending() { head.inert = meals.inert = bons.inert = !!state.request; }
  async function save() {
    if (!state.canWrite || !editing() || !await commit()) return; const collected = collect(); if (collected.errors.length) { say(collected.errors.join(' '), true); return; }
    if (!collected.body.bons.length && !await confirmBox('Salvați lista fără bonuri de consum?', { title: 'Listă fără bonuri', yes: 'Salvează', no: 'Revin' })) return;
    // Retain the exact body after an uncertain POST response; retries must carry the same request.
    const adding = state.mode === 'add'; const request = state.request || { body: collected.body, key: key() };
    if (adding) state.request = request; root.inert = true; say('Se salvează…');
    try {
      const result = adding ? await write('POST', '', { ...request.body, request_key: request.key }) : await write('PUT', '/' + state.doc.id, { ...collected.body, version: state.doc.version });
      if (disposed) return; ++generation; state.doc = result.document; state.draft = null; state.mode = 'view'; state.dirty = false; state.request = null;
      paint(); say('Salvat.'); try { await refresh(); if (!disposed) paintSaved(); } catch (error) { report(error); }
    } catch (error) {
      if (error.status && error.status < 500) state.request = null;
      report(error); if (state.request) say('Răspunsul salvării nu este confirmat. Salvează retrimite aceeași listă. Păstrați ciorna până la confirmare.', true);
    } finally { if (!disposed) { root.inert = false; freezePending(); } }
  }
  async function cancelList() {
    const doc = state.doc; if (!doc || doc.annulled || !state.canWrite) return;
    const reason = await reasonBox('Anulați lista nr. ' + doc.number + '? Bonurile pot fi asociate altei liste.', { title: 'Anulare listă', yes: 'Anulează lista' }); if (!reason || disposed) return;
    root.inert = true; try { const result = await write('POST', '/' + doc.id + '/cancel', { reason, version: doc.version }); if (disposed) return; state.doc = result.document; paint(); say('Anulată.'); await refresh(); if (!disposed) paintSaved(); } finally { if (!disposed) root.inert = false; }
  }
  const ready = Promise.all([refresh(), opts.api(url('/lookups'))]).then(([, lookups]) => { if (!disposed) { state.lookups = lookups; paint(); } }).catch(report); paint();
  return { ready, isDirty: dirty,
    destroy: () => { disposed = true; ++generation; for (const owner of [headUi, mealUi, bonsUi, treeUi, dialogs]) owner.clear(); host.replaceChildren(); } };
}
