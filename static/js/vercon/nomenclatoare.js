// Server-described nomenclatures. Draft values are independent of list rendering.
import { confirmBox, pickBox } from '../utils/confirm-box.js';
import { format, parse } from './fields.js';
import { el, scope, field, column, grid, paintTree, groupedNodes, openMenu, rowMenu, showMessage } from './ui.js';
const titles = { categorii: 'Categorii / Conturi', produse: 'Produse', variante: 'Variante de preț', clienti: 'Clienți / Gestionari', tva: 'Cote TVA', comisie_nir: 'Comisie de recepție', comisie_inventar: 'Comisie de inventariere', feluri: 'Feluri de mâncare' };
function mountCrud(host, opts) {
  const state = { rows: [], columns: [], lookups: {}, selected: null, mode: 'view', draft: null, dirty: false, filter: '', visibility: 'active', request: null, canWrite: false, loaded: false };
  const listUi = scope(), formUi = scope(), inputs = new Map();
  let disposed = false, sequence = 0, dataGrid;
  const status = el('span', { className: 'vc-save-status', role: 'status' });
  const list = el('div', { className: 'vc-nom__list' }), form = el('form', { className: 'vc-nom__form', noValidate: true });
  const fields = el('div', { className: 'vc-nom__fields vc-grid' }), bar = el('div', { className: 'vc-actions' });
  const search = el('input', { type: 'search', placeholder: 'Caută…', 'aria-label': 'Caută în ' + opts.title });
  const filterButton = el('button', { type: 'button', textContent: 'Afișare: active' });
  form.append(fields, bar);
  const root = el('section', { className: 'vc-nom' }, el('div', { className: 'vc-nom__bar' }, el('h3', { textContent: opts.title }), search, filterButton),
    el('div', { className: 'vc-nom__body' }, el('div', { className: 'vc-nom__left' }, list), el('div', { className: 'vc-nom__right' }, form)));
  host.replaceChildren(root);
  const parent = () => opts.parent?.() ?? null;
  const row = () => state.rows.find((item) => String(item.id) === String(state.selected)) || null;
  const dirty = () => state.dirty || formUi.dirty();
  const say = (text = '', bad = false) => { if (!disposed) showMessage(status, text, bad ? 'error' : 'info'); };
  const report = (error) => {
    if (disposed || error.name === 'AbortError') return;
    for (const [key, text] of Object.entries(error.fields || {})) if (inputs.has(key)) inputs.get(key).error.textContent = text;
    say(error.message || 'Operația a eșuat. Datele au rămas în formular.', true);
  };
  const query = () => new URLSearchParams(Object.entries(opts.sel()).filter(([, value]) => value != null && value !== ''));
  const endpoint = '/api/vercon/nom/' + encodeURIComponent(opts.kind);
  const write = (method, path, body) => { const sel = opts.sel(); return opts.api(endpoint + path, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ subunit: sel.subunit, gestiune: sel.gestiune, ...body }) }); };
  const caption = (item) => String(item?.[state.columns[0]?.key] ?? '');
  const cellText = (value, spec) => spec.type === 'ref' ? (state.lookups[spec.key] || []).find((choice) => String(choice.id) === String(value))?.label || '' : format(value, spec);
  const visible = () => state.rows.filter((item) => (state.visibility === 'all' || !!item.active === (state.visibility === 'active')) && (!state.filter || state.columns.some((spec) => cellText(item[spec.key], spec).toLocaleLowerCase('ro').includes(state.filter))));
  async function allowDiscard() {
    return !dirty() || await confirmBox('Renunțați la modificările nesalvate din ' + opts.title + '?', { title: 'Modificări nesalvate', yes: 'Renunț', no: 'Rămân aici', warning: true });
  }
  async function choose(id) {
    if (!await allowDiscard() || !await (opts.beforeSelect?.() ?? true) || disposed) return false;
    state.selected = state.rows.find((item) => String(item.id) === String(id))?.id ?? null; state.mode = 'view'; state.draft = null; state.dirty = false; state.request = null; say(); paint(); await opts.onSelect?.(row()); return true;
  }
  function options(anchor, item) {
    openMenu(anchor, [
      { label: 'Deschide', run: () => choose(item.id) },
      { label: 'Modifică', disabled: !state.canWrite, run: async () => { if (await choose(item.id)) edit(); } },
      { label: item.active ? 'Dezactivează' : 'Activează', disabled: !state.canWrite, run: async () => { if (await choose(item.id)) await toggle(); } },
    ]);
  }
  function paintList() {
    listUi.clear(); list.replaceChildren(); dataGrid = null; const items = visible();
    if (opts.kind === 'produse') {
      const nodes = groupedNodes(items, [(item) => cellText(item.IdMasina, { key: 'IdMasina', type: 'ref' }) || 'Fără categorie'], (item) => caption(item) + (!item.active ? ' • inactiv' : ''));
      paintTree(list, nodes, { title: 'Produse pe categorii', selected: state.selected, onSelect: (id) => choose(id).catch(report), onAction: (id, anchor) => { const item = state.rows.find((it) => String(it.id) === id); if (item) options(anchor, item); } }, listUi);
    } else {
      const body = el('div'); list.append(body);
      dataGrid = grid(body, { layoutId: 'vercon.nom.' + opts.kind, rowKey: 'id', rows: items, editable: false,
        columns: [...state.columns.map((spec) => column(spec, { editable: false, formatter: (value) => cellText(value, spec), width: spec.type === 'text' ? 190 : 120 })), { key: 'activeText', title: 'Stare', width: 85, formatter: (value) => value }, { key: 'actions', title: '⋮', width: 36, display: 'button', actionText: '⋮' }],
        onSelect: (item) => choose(item.id).catch(report), onCellAction: ({ row: item }) => rowMenu(dataGrid, item, [
          { label: 'Deschide', run: () => choose(item.id) },
          { label: 'Modifică', disabled: !state.canWrite, run: async () => { if (await choose(item.id)) edit(); } },
          { label: item.active ? 'Dezactivează' : 'Activează', disabled: !state.canWrite, run: async () => { if (await choose(item.id)) await toggle(); } },
        ]), footer: true, footerCaption: '{0} înregistrări',
      }, listUi);
      dataGrid.setRows(items.map((item) => ({ ...item, activeText: item.active ? 'Activ' : 'Inactiv' }))); if (state.selected != null) dataGrid.selectRow(state.selected);
    }
    if (!state.loaded) list.append(el('p', { textContent: 'Se încarcă…' }));
  }
  function paintForm() {
    formUi.clear(); inputs.clear(); fields.replaceChildren(); bar.replaceChildren(); const editing = state.mode !== 'view', blocked = opts.parent && parent() == null;
    if (blocked) fields.append(el('p', { className: 'vc-nom__empty', textContent: 'Alegeți mai întâi un produs.' }));
    else if (!editing && !row()) fields.append(el('p', { className: 'vc-nom__empty', textContent: 'Alegeți o înregistrare sau adăugați una nouă.' }));
    else for (const spec of state.columns) {
      const f = field({ ...spec, options: state.lookups[spec.key] || [] }, editing ? state.draft[spec.key] : row()[spec.key], editing,
        (raw) => { state.draft[spec.key] = raw; state.dirty = true; }, formUi); inputs.set(spec.key, f); fields.append(f.node);
    }
    const button = (label, run, type = 'button') => { const b = el('button', { type, textContent: label }); if (type !== 'submit') b.addEventListener('click', () => Promise.resolve(run()).catch(report)); return b; };
    if (editing) bar.append(button('Renunță', async () => { if (!await allowDiscard()) return; state.mode = 'view'; state.draft = null; state.dirty = false; state.request = null; say(); paintForm(); }), button('Salvează', null, 'submit'));
    else if (state.canWrite && !blocked) {
      bar.append(button('+ Adaugă', async () => {
        if (!await (opts.beforeSelect?.() ?? true) || disposed) return;
        state.mode = 'add'; state.selected = null; state.draft = Object.fromEntries(state.columns.map((spec) => [spec.key, ''])); state.dirty = false; state.request = null; say(); paintForm(); await opts.onSelect?.(null);
      }));
      if (row()) bar.append(button('Modifică', edit), button(row().active ? 'Dezactivează' : 'Activează', toggle));
    }
    bar.append(status); fields.inert = list.inert = !!state.request;
  }
  function paint() { paintList(); paintForm(); }
  function edit() { if (!row() || !state.canWrite) return; state.mode = 'edit'; state.draft = { ...row() }; state.dirty = false; state.request = null; say(); paintForm(); }
  async function load(keep = true) {
    const token = ++sequence, selected = keep ? state.selected : null, parentId = parent();
    if (opts.parent && parentId == null) { state.rows = []; state.selected = null; state.mode = 'view'; state.draft = null; state.loaded = true; paint(); return; }
    const data = await opts.api(endpoint + '?' + query() + (parentId != null ? '&parent=' + parentId : '')); if (disposed || token !== sequence || parentId !== parent()) return;
    state.rows = data.rows; state.columns = data.columns; state.lookups = data.lookups; state.canWrite = data.can_write; state.loaded = true;
    state.selected = state.rows.some((item) => String(item.id) === String(selected)) ? state.rows.find((item) => String(item.id) === String(selected)).id : null; paint();
  }
  async function save() {
    if (!state.canWrite || state.mode === 'view' || !await formUi.commit() || !await (opts.beforeSelect?.() ?? true)) return; const values = {}; let valid = true;
    for (const spec of state.columns) {
      const raw = inputs.get(spec.key).input.value;
      const result = spec.type === 'ref' ? { ok: !!raw || !spec.required, value: raw ? Number(raw) : null, message: 'Alegeți o valoare.' } : parse(raw, spec);
      inputs.get(spec.key).error.textContent = result.ok ? '' : result.message; if (!result.ok) valid = false; values[spec.key] = result.value;
    }
    if (!valid) { say('Corectați câmpurile marcate.', true); return; }
    const adding = state.mode === 'add', request = state.request || { values, parent: parent(), key: crypto.randomUUID() };
    if (adding) state.request = request; root.inert = true; say('Se salvează…');
    try {
      const result = adding ? await write('POST', '', { values: request.values, parent: request.parent, request_key: request.key }) : await write('PUT', '/' + row().id, { values, version: row().version });
      if (disposed) return; state.mode = 'view'; state.draft = null; state.dirty = false; state.request = null; state.selected = result.row.id;
      // Keep the acknowledged row even when refreshing the list fails.
      state.rows = [...state.rows.filter((item) => item.id !== result.row.id), result.row]; paint(); say('Salvat.');
      await load(); if (!disposed) await opts.onSelect?.(row());
    } catch (error) { if (error.status && error.status < 500) state.request = null; report(error); if (state.request) say('Răspuns neconfirmat. Salvează retrimite aceeași înregistrare.', true); } finally { if (!disposed) { root.inert = false; fields.inert = list.inert = !!state.request; } }
  }
  async function toggle() {
    const item = row(); if (!item || !state.canWrite || !await (opts.beforeSelect?.() ?? true)) return;
    const active = !item.active;
    if (!await confirmBox((active ? 'Activați ' : 'Dezactivați ') + '«' + caption(item) + '»?', { title: active ? 'Activare' : 'Dezactivare', yes: active ? 'Activează' : 'Dezactivează', no: 'Renunță' }) || disposed) return;
    root.inert = true;
    try { await write('POST', '/' + item.id + '/active', { active, version: item.version }); if (disposed) return; await load(); say(active ? 'Activat.' : 'Dezactivat.'); await opts.onSelect?.(row()); } catch (error) { report(error); } finally { if (!disposed) root.inert = false; }
  }
  const setSearch = (text) => { state.filter = text.trim().toLocaleLowerCase('ro'); search.value = text; paintList(); };
  search.addEventListener('input', () => setSearch(search.value));
  filterButton.addEventListener('click', async () => {
    const choice = await pickBox('Afișează înregistrările', [{ value: 'active', label: 'Active' }, { value: 'inactive', label: 'Inactive' }, { value: 'all', label: 'Toate' }]); if (choice == null || disposed) return;
    state.visibility = choice; filterButton.textContent = 'Afișare: ' + ({ active: 'active', inactive: 'inactive', all: 'toate' })[choice]; paintList();
  });
  form.addEventListener('submit', (event) => { event.preventDefault(); save().catch(report); });
  return { load, isDirty: dirty, selected: row, search: setSearch, allowDiscard, discard: () => { state.mode = 'view'; state.draft = null; state.dirty = false; state.request = null; },
    destroy: () => { disposed = true; ++sequence; listUi.clear(); formUi.clear(); host.replaceChildren(); } };
}
export function mountNomenclator(host, opts) {
  const main = el('div'), sub = el('div', { className: 'vc-nom__sub' }); host.replaceChildren(main, sub); sub.hidden = !!opts.kinds[1];
  let variants;
  const crud = mountCrud(main, { ...opts, kind: opts.kinds[0], title: titles[opts.kinds[0]] || opts.kinds[0],
    beforeSelect: async () => { if (!variants || !await variants.allowDiscard()) return !variants; variants.discard(); return true; },
    onSelect: async (row) => { sub.hidden = !row; if (variants) await variants.load(false); },
  });
  if (opts.kinds[1]) variants = mountCrud(sub, { ...opts, kind: opts.kinds[1], title: titles[opts.kinds[1]] || opts.kinds[1], parent: () => crud.selected()?.id ?? null });
  const all = [crud, variants].filter(Boolean);
  return { ready: Promise.all(all.map((item) => item.load(false))), isDirty: () => all.some((item) => item.isDirty()), search: (text) => all.forEach((item) => item.search(text)),
    destroy: () => { all.forEach((item) => item.destroy()); host.replaceChildren(); } };
}

// Numeric policy is editable only before the first data, as enforced by the server.
export function mountSettings(host, opts) {
  const ui = scope(), inputs = new Map(); let disposed = false, data, dirty = false;
  const status = el('span', { className: 'vc-save-status', role: 'status' }), fields = el('div', { className: 'vc-grid' }), note = el('p', { className: 'vc-hint' }), counters = el('div');
  const saveButton = el('button', { type: 'submit', textContent: 'Salvează', className: 'is-primary' });
  const form = el('form', { noValidate: true }, fields, note, el('div', { className: 'vc-actions' }, saveButton, status), counters);
  const root = el('section', { className: 'vc-nom' }, el('h3', { textContent: 'Setări gestiune' }), form); host.replaceChildren(root);
  const say = (text = '', bad = false) => { if (!disposed) showMessage(status, text, bad ? 'error' : 'info'); };
  const specs = [{ key: 'zec', label: 'Zecimale afișate (0–4)', type: 'int', required: true, min: 0 }, { key: 'scara', label: 'Scara de stocare (0–4)', type: 'int', required: true, min: 0 }];
  function paint() {
    ui.clear(); inputs.clear(); fields.replaceChildren(); counters.replaceChildren(); const editable = data.can_write && !data.populated; saveButton.hidden = !editable;
    for (const spec of specs) { const control = field(spec, data[spec.key], editable, () => { dirty = true; }, ui); inputs.set(spec.key, control); fields.append(control.node); }
    note.textContent = data.populated ? 'Gestiunea conține date: zecimalele și scara nu se mai pot schimba.' : 'Valorile se pot schimba cât timp gestiunea nu conține date.';
    grid(counters, { layoutId: 'vercon.settings.counters', rowKey: 'key', rows: data.settings, editable: false, footer: false, columns: [
      column({ key: 'key', label: 'Setare' }, { width: 320, editable: false }), column({ key: 'value', label: 'Valoare', type: 'int' }, { editable: false }),
    ] }, ui); dirty = false;
  }
  async function load() { const query = new URLSearchParams(Object.entries(opts.sel()).filter(([, v]) => v != null && v !== '')); const response = await opts.api('/api/vercon/settings?' + query); if (disposed) return; data = response; paint(); }
  form.addEventListener('submit', async (event) => {
    event.preventDefault(); if (!data?.can_write || data.populated || !await ui.commit()) return; const values = {}; let valid = true;
    for (const spec of specs) {
      const result = parse(inputs.get(spec.key).input.value, spec); const message = !result.ok ? result.message : result.value > 4 ? 'Valoarea maximă este 4.' : '';
      inputs.get(spec.key).error.textContent = message; if (message) valid = false; values[spec.key] = result.value;
    }
    if (valid && values.scara < values.zec) { inputs.get('scara').error.textContent = 'Scara trebuie să fie cel puțin egală cu zecimalele afișate.'; valid = false; }
    if (!valid) { say('Corectați câmpurile marcate.', true); return; } root.inert = true; say('Se salvează…');
    try { const sel = opts.sel(); await opts.api('/api/vercon/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ subunit: sel.subunit, gestiune: sel.gestiune, ...values }) }); if (disposed) return; dirty = false; await load(); say('Salvat. Reîncărcați pagina pentru aplicarea noii scări.'); }
    catch (error) { if (!disposed && error.name !== 'AbortError') { for (const [key, text] of Object.entries(error.fields || {})) if (inputs.has(key)) inputs.get(key).error.textContent = text; say(error.message || 'Salvarea a eșuat.', true); } }
    finally { if (!disposed) root.inert = false; }
  });
  return { ready: load(), isDirty: () => dirty || ui.dirty(), destroy: () => { disposed = true; ui.clear(); host.replaceChildren(); } };
}
