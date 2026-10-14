// The asset identity and cost remain immutable in this register.
import { confirmBox } from '../utils/confirm-box.js';
import { parse } from './fields.js';
import { el, scope, grid, column, rowMenu, preview, showMessage } from './ui.js';
import { openDepreciation } from './depreciation.js';
export function mountMf(host, opts) {
  const ui = scope(), dialogs = scope(), state = { assets: [], canWrite: false, canReport: false, draft: null, dirty: false, filter: '' };
  let disposed = false, dataGrid;
  const status = el('span', { className: 'vc-save-status', role: 'status' }), body = el('div', { className: 'vc-doc__lines' }), bar = el('div', { className: 'vc-actions' });
  const root = el('section', { className: 'vc-nom' }, el('h3', { textContent: 'Registru mijloace fixe' }), body, bar); host.replaceChildren(root);
  const say = (text = '', bad = false) => { if (!disposed) showMessage(status, text, bad ? 'error' : 'info'); };
  const report = (error) => { if (!disposed && error.name !== 'AbortError') say([error.message || 'Operația a eșuat.', ...Object.values(error.fields || {})].join(' '), true); };
  const dirty = () => state.dirty || ui.dirty();
  const query = () => new URLSearchParams(Object.entries(opts.sel()).filter(([, v]) => v != null && v !== ''));
  const specs = [
    { key: 'name', label: 'Mijloc fix', width: 240 }, { key: 'code', label: 'Nr. inventar', width: 140 },
    { key: 'entry_date', label: 'Data intrării', type: 'date', required: true, mutable: true },
    { key: 'value', label: 'Valoare', type: 'decimal' },
    { key: 'DNF', label: 'DNF · luni', type: 'int', required: true, min: 1, mutable: true },
    { key: 'DF', label: 'DF · luni', type: 'int', min: 0, mutable: true },
    { key: 'monthly', label: 'Valoare lunară', type: 'decimal' },
    { key: 'holdersText', label: 'Deținător', width: 260 }, { key: 'written_off', label: 'Casat', type: 'decimal' },
  ];
  const columns = () => specs.map((spec) => column(spec, { width: spec.width || 120, editable: spec.mutable ? (row) => row.id === state.draft?.id : false }));
  function rows() { return state.assets.filter((asset) => !state.filter || (asset.name + ' ' + asset.code).toLocaleLowerCase('ro').includes(state.filter)).map((asset) => ({
    ...(state.draft?.id === asset.id ? state.draft : asset), holdersText: (asset.holders || []).map((holder) => holder.holder + ' (' + holder.qty + ')').join(', '),
  })); }
  function paint() {
    ui.clear(); body.replaceChildren();
    dataGrid = grid(body, { layoutId: 'vercon.mf.register', rowKey: 'id', rows: rows(), editable: !!state.draft, columns: [...columns(), { key: 'actions', title: '⋮', width: 36, display: 'button', actionText: '⋮', editable: false }],
      footer: true, footerCaption: '{0} mijloace fixe',
      onCellSave: ({ key, value }) => { state.draft[key] = value; state.dirty = true; return { [key]: value }; },
      onCellAction: ({ row }) => rowMenu(dataGrid, row, [
        { label: 'Amortizare la dată', disabled: !state.canReport || !!state.draft || !row.id,
          run: () => openDepreciation(row, opts, dialogs) },
        { label: 'Modifică datele de amortizare', disabled: !state.canWrite || !!state.draft, run: () => { state.draft = { ...state.assets.find((asset) => asset.id === row.id) }; state.dirty = false; say(); paint(); dataGrid.beginEdit(dataGrid._rows.find((asset) => asset.id === row.id), 'entry_date'); } },
        { label: 'Previzualizare', run: () => preview('Mijloc fix • ' + row.code, [{ title: row.name, columns: columns().map((col) => ({ ...col, editable: false })), rows: [row] }], dialogs) },
      ]),
    }, ui);
    bar.replaceChildren(); const button = (label, run) => { const b = el('button', { type: 'button', textContent: label }); b.addEventListener('click', () => Promise.resolve(run()).catch(report)); return b; };
    if (state.draft) bar.append(button('Renunță', async () => {
      if (dirty() && !await confirmBox('Renunțați la datele nesalvate ale mijlocului fix?', { title: 'Renunțare', yes: 'Renunț', no: 'Rămân aici' })) return;
      state.draft = null; state.dirty = false; say(); paint();
    }), button('Salvează', save));
    bar.append(status);
  }
  async function load() { const data = await opts.api('/api/vercon/mf/assets?' + query()); if (disposed) return; state.assets = data.assets; state.canWrite = data.can_write; state.canReport = !!data.can_report; paint(); }
  async function save() {
    if (!state.canWrite || !state.draft || !await ui.commit()) return; const draft = state.draft, values = {}, errors = [];
    for (const spec of specs.filter((item) => item.mutable)) { const result = parse(draft[spec.key], spec); if (!result.ok) errors.push(spec.label + ': ' + result.message); else values[spec.key] = result.value; }
    if (errors.length) { say(errors.join(' '), true); return; } root.inert = true; say('Se salvează…');
    try {
      const sel = opts.sel(); await opts.api('/api/vercon/mf/assets/' + draft.id, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ subunit: sel.subunit, gestiune: sel.gestiune, year: sel.year, version: draft.version, DataIntrare: values.entry_date, DNF: values.DNF, DF: values.DF ?? 0 }) });
      if (disposed) return; state.draft = null; state.dirty = false; paint(); say('Salvat.'); await load();
    } catch (error) { report(error); } finally { if (!disposed) root.inert = false; }
  }
  const ready = load().catch(report);
  return { ready, isDirty: dirty, search: async (text) => {
    if (!await ui.commit() || disposed) return; state.filter = text.trim().toLocaleLowerCase('ro');
    // Keep the edited asset visible even if it no longer matches the search.
    if (state.draft && !rows().some((row) => row.id === state.draft.id)) state.filter = ''; dataGrid?.setRows(rows());
  }, destroy: () => { disposed = true; ui.clear(); dialogs.clear(); host.replaceChildren(); } };
}
