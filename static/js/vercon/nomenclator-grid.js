import { confirmBox, pickBox } from '../utils/confirm-box.js';
import { el, scope, column, grid, rowMenu, openMenu, showMessage } from './ui.js';
import { decimalText } from './decimal-text.js';
import { mountProducts } from './product-nomenclator.js';
import { ActionAddButton } from '../components/action-add-button/action-add-button.js';

const titles = { categorii: 'Categorii / Conturi', produse: 'Produse', variante: 'Variante de preț',
  clienti: 'Clienți / Gestionari', tva: 'Cote TVA', comisie_nir: 'Comisie Recepție NIR',
  comisie_inventar: 'Comisie Inventariere', feluri: 'Feluri de mâncare' };

function mountCrud(host, opts) {
  const ui = scope();
  const state = { rows: [], columns: opts.initialColumns || [], lookups: {}, labels: {}, selected: null, selectedId: null, canWrite: false,
    filter: '', visibility: opts.visibility || 'active', adding: null };
  const unsaved = new Set(), requests = new Map(), finishing = new Map();
  let disposed = false, generation = 0, statementGeneration = 0, dataGrid, busy = false, loadedParent = null;
  const list = el('div', { className: 'vc-nom__list' });
  const status = el('span', { className: 'vc-save-status', role: 'status' });
  const search = el('input', { type: 'search', placeholder: 'Caută…', 'aria-label': 'Caută în ' + opts.title });
  const visibilityLabel = () => ({ active: 'active', inactive: 'inactive', all: 'toate', used: 'folosite în ' + opts.sel().year })[state.visibility];
  const filterButton = el('button', { type: 'button', textContent: 'Afișare: ' + visibilityLabel() });
  const addControl = opts.explicitAdd ? new ActionAddButton(null, { className: 'vc-nom__add',
    title: 'Adauga produs nou', disabled: true, onClick: () => { void startAdd().catch(report); } }) : null;
  const addButton = addControl?.element || null;
  const statement = el('div', { className: 'vc-nom__statement', 'aria-live': 'polite' });
  const root = el('section', { className: 'vc-nom vc-nom--grid' },
    el('div', { className: 'vc-nom__bar' }, addButton, el('h3', { textContent: opts.title }), opts.hideSearch ? null : search, filterButton), list);
  if (opts.kind === 'categorii') root.append(statement);
  root.append(status); host.replaceChildren(root);
  const endpoint = '/api/vercon/nom/' + encodeURIComponent(opts.kind);
  const query = () => new URLSearchParams(Object.entries(opts.sel()).filter(([, value]) => value != null && value !== ''));
  const parent = () => opts.parent?.() ?? null;
  const selected = () => state.rows.find((item) => item.__key === state.selected && !item.__new) || null;
  const say = (message = '', bad = false) => { if (!disposed) showMessage(status, message, bad ? 'error' : 'info'); };
  const report = (error) => { if (!disposed && error.name !== 'AbortError') say(error.message || 'Operația a eșuat.', true); };
  const blank = (item) => !!item.__new && state.columns.every((spec) => item[spec.key] == null || String(item[spec.key]).trim() === '');
  const dirty = () => busy || unsaved.size > 0 || !!dataGrid?.hasEdit;
  const text = (value, spec) => spec.type === 'ref'
    ? (state.labels[spec.key] || state.lookups[spec.key] || []).find((choice) => String(choice.id) === String(value))?.label || ''
    : spec.type === 'decimal' ? decimalText(value, 4) : String(value ?? '');
  const visible = () => state.rows.filter((item) => state.adding ? item.__key === state.adding : item.__new || unsaved.has(item.__key)
    || ((state.visibility === 'all' || (state.visibility === 'used' ? item.active && item.used_in_year : !!item.active === (state.visibility === 'active')))
      && (!state.filter || state.columns.some((spec) => (!opts.searchKeys || opts.searchKeys.includes(spec.key))
        && text(item[spec.key], spec).toLocaleLowerCase('ro').includes(state.filter)))));
  const write = (method, path, body) => {
    const sel = opts.sel();
    return opts.api(endpoint + path, { method, headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ subunit: sel.subunit, gestiune: sel.gestiune, ...body }) });
  };
  async function allowDiscard() {
    if (busy) { say('Așteptați confirmarea salvării.'); return false; }
    return !dirty() || await confirmBox('Renunțați la modificările nesalvate din ' + opts.title + '?',
      { title: 'Modificări nesalvate', yes: 'Renunț', no: 'Rămân aici', warning: true });
  }
  async function paintStatement(item) {
    if (opts.kind !== 'categorii') return;
    const token = ++statementGeneration;
    statement.replaceChildren(el('p', { textContent: item ? 'Se încarcă fișa contului…' : 'Selectați o categorie pentru fișa contului.' }));
    if (!item) return;
    try {
      const data = await opts.api(endpoint + '/' + item.id + '/statement?' + query());
      if (disposed || token !== statementGeneration) return;
      const totals = el('dl', { className: 'vc-nom__totals' });
      for (const [key, label] of [['opening', 'SI'], ['incoming', 'Intrări'], ['outgoing', 'Ieșiri'], ['closing', 'SF']]) {
        totals.append(el('div', {}, el('dt', { textContent: label }), el('dd', { textContent: decimalText(data[key], 2) })));
      }
      statement.replaceChildren(el('h4', { textContent: 'Fișă cont · ' + item.Masina + ' · ' + data.year + ' · lei' }), totals);
    } catch (error) {
      if (!disposed && token === statementGeneration && error.name !== 'AbortError') {
        statement.replaceChildren(el('p', { textContent: error.message || 'Fișa contului nu a putut fi încărcată.' }));
      }
    }
  }
  async function choose(item) {
    const id = item?.__new ? null : item?.id ?? null;
    if (state.selected === item?.__key && state.selectedId === id) return;
    if (!await (opts.beforeSelect?.() ?? true) || disposed) {
      dataGrid?.cancelEdit(true);
      if (state.selected != null) dataGrid?.selectRow(state.selected);
      return;
    }
    state.selected = item?.__key ?? null;
    state.selectedId = id;
    void paintStatement(selected());
    await opts.onSelect?.(selected());
  }
  function newRow() {
    const item = { __key: crypto.randomUUID(), __new: true, active: true,
      ...Object.fromEntries(state.columns.map((spec) => [spec.key, null])) };
    state.rows.push(item); return item;
  }
  function removeNew(item) {
    if (!item.__new || requests.has(item.__key)) return;
    state.rows = state.rows.filter((row) => row.__key !== item.__key); unsaved.delete(item.__key);
    if (state.adding === item.__key) state.adding = null;
    dataGrid?.setRows(visible(), { keepWidths: true });
  }
  async function persist(item) {
    if (!unsaved.has(item.__key)) return item;
    const missing = state.columns.find((spec) => spec.required && (item[spec.key] == null || String(item[spec.key]).trim() === ''));
    if (missing) {
      const message = 'Produsul nu poate fi salvat. Completați ' + missing.label + ' în rând.';
      if (opts.rowSave) throw new Error(message);
      say('Completați ' + missing.label + ' în rând.'); return item;
    }
    let request = requests.get(item.__key);
    if (opts.kind === 'produse' && !request && !String(item.CPV || '').trim()) {
      if (!await confirmBox('Produsul nu are cod CPV. Îl salvați fără CPV?',
        { title: 'Avertizare CPV', yes: 'Salvează fără CPV', no: 'Continuă editarea', warning: true }) || disposed) return item;
    }
    if (!request) {
      request = { adding: !!item.__new, values: Object.fromEntries(state.columns.map((spec) => [spec.key, item[spec.key]])),
        parent: parent(), key: crypto.randomUUID(), version: item.version };
      requests.set(item.__key, request);
    }
    busy = true; search.disabled = filterButton.disabled = true; say('Se salvează…');
    if (addButton) addButton.disabled = true;
    try {
      const result = request.adding
        ? await write('POST', '', { values: request.values, parent: request.parent, request_key: request.key })
        : await write('PUT', '/' + item.id, { values: request.values, version: request.version });
      if (disposed) return item;
      Object.assign(item, result.row, { __new: false });
      requests.delete(item.__key); unsaved.delete(item.__key); say('Salvat.');
      Promise.resolve(opts.onSaved?.(item)).catch(report);
      return item;
    } catch (error) {
      if (error.status && error.status < 500) requests.delete(item.__key);
      report(error); throw error;
    } finally { busy = false; if (!disposed) { search.disabled = filterButton.disabled = false; if (addButton) addButton.disabled = !state.canWrite; } }
  }
  function rowIssue(item) {
    if (!unsaved.has(item.__key) || blank(item)) return '';
    const missing = state.columns.filter((spec) => spec.required && (item[spec.key] == null || String(item[spec.key]).trim() === ''));
    return missing.length ? 'Produsul nu poate fi salvat. Completați: ' + missing.map((spec) => spec.label).join(', ') + '.' : '';
  }
  async function finishRow(item) {
    if (blank(item)) { unsaved.delete(item.__key); return true; }
    if (!unsaved.has(item.__key)) return true;
    if (finishing.has(item.__key)) return finishing.get(item.__key);
    const operation = (async () => {
      const adding = !!item.__new;
      try {
        if (rowIssue(item)) { say(rowIssue(item), true); dataGrid.validate(); return false; }
        await persist(item);
        if (disposed || unsaved.has(item.__key)) return false;
        if (adding && opts.explicitAdd) {
          state.adding = null; state.visibility = 'all'; filterButton.textContent = 'Afișare: ' + visibilityLabel();
          state.selected = String(item.id);
          await load({ keepGrid: true });
          if (disposed) return false;
          dataGrid.selectRow(String(item.id), { reveal: true });
          await choose(state.rows.find((row) => row.id === item.id));
          say('Salvat. Produsul nou este selectat.');
        }
        return true;
      } catch (error) { report(error); return false; }
    })();
    finishing.set(item.__key, operation);
    try { return await operation; } finally { finishing.delete(item.__key); }
  }
  async function commit() {
    if (!await ui.commit() || disposed) return false;
    if (opts.rowSave) for (const item of [...state.rows]) if (!await finishRow(item)) return false;
    return true;
  }
  async function startAdd() {
    if (!state.canWrite || busy || !await commit() || disposed) return;
    const existing = state.rows.find((item) => blank(item));
    const item = existing || newRow();
    state.adding = item.__key; state.selected = null; state.selectedId = null;
    dataGrid.setRows(visible(), { keepWidths: true });
    await opts.onSelect?.(null);
    dataGrid.selectRow(item.__key);
    await dataGrid.beginEditNewRow('DenumireP');
    say('Completați produsul. Enter în ultima celulă editabilă sau „Salvează rândul” finalizează adăugarea.');
  }
  async function saveCell({ row, key, value }) {
    const item = state.rows.find((candidate) => candidate.__key === row.__key);
    if (busy || requests.has(item.__key)) throw new Error('Reîncercați salvarea rândului din meniul ⋮ înainte de alte modificări.');
    Object.assign(item, { [key]: value }); unsaved.add(item.__key);
    if (opts.rowSave) return { ...item };
    return { ...await persist(item) };
  }
  async function toggle(item) {
    if (!state.canWrite || item.__new || busy || unsaved.has(item.__key)) return;
    if (!await ui.commit() || !await (opts.beforeSelect?.() ?? true)) return;
    const active = !item.active;
    if (!active && item.deactivate_reason) { say(item.deactivate_reason, true); return; }
    if (!await confirmBox((active ? 'Activați ' : 'Dezactivați ') + '«' + text(item[state.columns[0]?.key], state.columns[0]) + '»?',
      { title: active ? 'Activare' : 'Dezactivare', yes: active ? 'Activează' : 'Dezactivează', no: 'Renunță' }) || disposed) return;
    busy = true; root.inert = true;
    try {
      const result = await write('POST', '/' + item.id + '/active', { active, version: item.version });
      if (disposed) return;
      Object.assign(item, result.row);
      const rows = visible();
      if (!rows.some((row) => row.__key === state.selected)) { state.selected = null; state.selectedId = null; }
      dataGrid.setRows(rows, { keepWidths: true });
      if (state.selected != null) dataGrid.selectRow(state.selected);
      void paintStatement(selected()); await opts.onSelect?.(selected());
      say(active ? 'Activat.' : 'Dezactivat.');
    }
    catch (error) { report(error); } finally { busy = false; if (!disposed) root.inert = false; }
  }
  function actions(item) {
    rowMenu(dataGrid, item, [
      { label: 'Salvează rândul', disabled: !state.canWrite || !unsaved.has(item.__key) || busy, run: async () => {
        try {
          if (requests.has(item.__key)) dataGrid.cancelEdit(true);
          if (dataGrid.hasEdit && !await dataGrid.commitEdit()) return;
          const adding = !!item.__new;
          if (opts.rowSave) { if (!await finishRow(item) || adding) return; }
          else await persist(item);
          dataGrid.setRows(visible(), { keepWidths: true }); await choose(item);
        }
        catch (error) { report(error); }
      } },
      { label: item.active ? 'Dezactivează' : 'Activează', disabled: !state.canWrite || item.__new || unsaved.has(item.__key)
        || busy || (item.active && !!item.deactivate_reason), run: () => toggle(item).catch(report) },
      { label: 'Reîncarcă lista', disabled: busy, run: async () => { if (await allowDiscard()) { discard(); await load(); } } },
    ]);
  }
  function paint() {
    ui.clear(); list.replaceChildren();
    const body = el('div'); list.append(body);
    const editable = state.canWrite && (!opts.parent || parent() != null);
    if (addButton) addButton.disabled = !editable || busy;
    const displayColumns = opts.columnOrder
      ? [...state.columns].sort((a, b) => {
        const rank = (spec) => opts.columnOrder.includes(spec.key) ? opts.columnOrder.indexOf(spec.key) : opts.columnOrder.length;
        return rank(a) - rank(b);
      }) : state.columns;
    dataGrid = grid(body, { layoutId: 'vercon.nom.' + opts.kind + '.inline-v2', layout: {}, proportionalWidths: true, ...opts.gridOptions,
      rowKey: '__key', rows: visible(), editable, singleClickEdit: true, newRowAlwaysVisible: editable,
      singleNewRow: !!opts.explicitAdd,
      onNewRow: newRow, isBlankRow: blank, onCancelNewRow: removeNew,
      rowIssue: opts.rowSave ? rowIssue : undefined,
      confirmRowIssue: opts.rowSave ? async ({ row, message }) => {
        const keep = await confirmBox(message + ' Continuați editarea?',
          { title: 'Produs incomplet', yes: 'Continuă editarea', no: 'Anulează rândul', warning: true });
        if (!keep) unsaved.delete(row.__key);
        return keep ? 'continue' : 'cancel';
      } : undefined,
      onRowLeave: opts.rowSave ? async ({ row }) => {
        const completingAdd = !!row.__new && unsaved.has(row.__key) && !blank(row);
        return await finishRow(row) && !completingAdd;
      } : undefined,
      editGuard: (item) => busy || requests.has(item.__key) ? 'Așteptați confirmarea sau reîncercați salvarea din ⋮.' : '',
      columns: [...displayColumns.map((spec) => column(opts.rowSave ? { ...spec, required: false } : spec, { width: 1, minWidth: 0, editable, required: spec.required,
        options: (state.lookups[spec.key] || []).map((choice) => ({ value: choice.id, label: choice.label })),
        formatter: (value) => text(value, spec),
        filterValue: opts.filterByLabel && spec.type === 'ref' ? (value) => text(value, spec) : undefined,
        cellAction: spec.key === opts.pickerColumn ? { text: '...', label: 'Alege codul CPV', selectedOnly: true, disabled: !state.canWrite } : undefined,
        ...opts.columnOptions?.(spec) })),
      ...(opts.hideState ? [] : [{ key: 'active', title: 'Stare', width: 70, minWidth: 0, fixedWidth: true, editable: false, formatter: (value) => value ? 'Activ' : 'Inactiv' }]),
      { key: 'actions', title: '⋮', width: 36, minWidth: 36, fixedWidth: true, editable: false, filter: false, sortable: false, display: 'button', actionText: '⋮' }],
      onCellSave: saveCell, onCellSaved: ({ row }) => {
        if (!row) return;
        void choose(row).catch(report);
        if (opts.kind === 'categorii' && state.selected === row.__key) void paintStatement(selected());
      },
      onEditError: ({ error }) => report(error), onEditBlocked: ({ message }) => say(message, true),
      onSelect: (item) => { void choose(item).catch(report); }, onCellAction: ({ row, key }) => {
        if (key === 'actions') actions(row);
        else Promise.resolve(opts.onCellAction?.({ row, key })).catch(report);
      },
      footer: true, footerCaption: '', emptyText: opts.parent && parent() == null ? 'Selectați mai întâi un produs.' : 'Nu există înregistrări.',
    }, ui);
    if (state.selected != null) dataGrid.selectRow(state.selected);
  }
  async function load({ keepGrid = false } = {}) {
    const token = ++generation, parentId = parent();
    if (opts.parent && (parentId == null || parentId !== loadedParent)) {
      loadedParent = parentId; state.rows = []; state.selected = null; state.selectedId = null; paint();
      if (parentId == null) { list.inert = false; return; }
    }
    list.inert = true;
    try {
      const data = await opts.api(endpoint + '?' + query() + (parentId == null ? '' : '&parent=' + parentId));
      if (disposed || token !== generation || parentId !== parent()) return;
      state.rows = data.rows.map((item) => ({ ...item, __key: String(item.id), __new: false }));
      state.columns = data.columns; state.lookups = data.lookups; state.labels = data.labels || data.lookups; state.canWrite = data.can_write;
      unsaved.clear(); requests.clear();
      if (!selected()) state.selected = null;
      const item = selected(), changed = state.selectedId !== (item?.id ?? null);
      state.selectedId = item?.id ?? null;
      if (keepGrid && dataGrid) {
        dataGrid.setRows(visible(), { keepWidths: true });
        if (state.selected != null) dataGrid.selectRow(state.selected);
      } else paint();
      void paintStatement(item);
      if (changed) await opts.onSelect?.(item);
    } finally { if (!disposed && token === generation) list.inert = false; }
  }
  function discard() { dataGrid?.cancelEdit(true); state.adding = null; unsaved.clear(); requests.clear(); }
  async function refreshVisible() {
    if (!await commit() || disposed) return;
    const rows = visible();
    dataGrid?.setRows(rows, { keepWidths: true });
    if (state.selected != null && !rows.some((row) => row.__key === state.selected)) await choose(null);
    else if (state.selected != null) dataGrid?.selectRow(state.selected);
  }
  search.addEventListener('input', () => { state.filter = search.value.trim().toLocaleLowerCase('ro'); void refreshVisible().catch(report); });
  filterButton.addEventListener('click', async () => {
    if (!await commit()) return;
    if (opts.productVisibility) {
      openMenu(filterButton, [
        { label: 'Afișează TOATE produsele', on: state.visibility === 'all', run: () => setVisibility(state.visibility === 'all' ? 'used' : 'all').catch(report) },
        { label: 'Afișează produse INACTIVE', on: state.visibility === 'inactive', run: () => setVisibility(state.visibility === 'inactive' ? 'used' : 'inactive').catch(report) },
      ], { label: 'Afișare produse', checkable: true, align: 'right' });
      return;
    }
    const choice = await pickBox('Afișează înregistrările', [{ value: 'active', label: 'Active' }, { value: 'inactive', label: 'Inactive' }, { value: 'all', label: 'Toate' }]);
    if (choice == null || disposed) return;
    await setVisibility(choice);
  });
  async function setVisibility(choice) {
    state.visibility = choice; filterButton.textContent = 'Afișare: ' + visibilityLabel();
    await refreshVisible();
  }
  async function setValue(rowKey, key, value) {
    if (!state.canWrite || busy || !await ui.commit()) throw new Error('Confirmați editarea curentă înainte de alegerea CPV.');
    const item = state.rows.find((row) => row.__key === rowKey);
    if (!item) throw new Error('Rândul produsului nu mai este disponibil.');
    const pending = requests.get(rowKey);
    if (pending && pending.values[key] !== value) throw new Error('Reîncercați salvarea neconfirmată a rândului înainte de alte modificări.');
    const result = pending ? await persist(item) : await saveCell({ row: item, key, value });
    if (!disposed) {
      if (opts.rowSave) dataGrid.refreshRows();
      else dataGrid.setRows(visible(), { keepWidths: true });
      if (state.selected != null) dataGrid.selectRow(state.selected);
      await choose(result);
    }
    return result;
  }
  return { load, selected, setValue, commit, commitCell: () => ui.commit(), isDirty: dirty, allowDiscard, discard,
    search: (value) => { search.value = value; state.filter = value.trim().toLocaleLowerCase('ro'); void refreshVisible().catch(report); },
    destroy: () => { disposed = true; ++generation; ++statementGeneration; addControl?.destroy(); ui.clear(); host.replaceChildren(); } };
}

export function mountNomenclator(host, opts) {
  if (opts.kinds[0] === 'produse' && opts.kinds[1] === 'variante') return mountProducts(host, opts, mountCrud);
  const combined = opts.kinds.includes('categorii') && opts.kinds.includes('tva');
  const committees = opts.kinds.includes('comisie_nir') && opts.kinds.includes('comisie_inventar');
  const root = el('div', { className: 'vc-nom-page' + (combined ? ' vc-nom-page--database' : committees ? ' vc-nom-page--committees' : '') });
  host.replaceChildren(root);
  const all = [];
  if (combined || committees) {
    for (const kind of opts.kinds) {
      const panel = el('div', { className: 'vc-nom-panel vc-nom-panel--' + kind }); root.append(panel);
      all.push(mountCrud(panel, { ...opts, kind, title: titles[kind] }));
    }
  } else {
    const main = el('div', { className: 'vc-nom-panel' }), sub = el('div', { className: 'vc-nom-panel', hidden: true }); root.append(main);
    let variants;
    const crud = mountCrud(main, { ...opts, kind: opts.kinds[0], title: titles[opts.kinds[0]],
      beforeSelect: async () => !variants || await variants.allowDiscard(),
      onSelect: async (row) => { if (!variants) return; variants.discard(); sub.hidden = !row; await variants.load(); } });
    all.push(crud);
    if (opts.kinds[1]) {
      root.classList.add('vc-nom-page--products'); root.append(sub);
      variants = mountCrud(sub, { ...opts, kind: opts.kinds[1], title: titles[opts.kinds[1]], parent: () => crud.selected()?.id ?? null });
      all.push(variants);
    }
  }
  return { ready: Promise.all(all.map((item) => item.load())), isDirty: () => all.some((item) => item.isDirty()),
    search: (value) => all.forEach((item) => item.search(value)), destroy: () => { all.forEach((item) => item.destroy()); host.replaceChildren(); } };
}
