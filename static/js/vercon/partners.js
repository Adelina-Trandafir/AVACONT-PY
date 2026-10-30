// SLICE-VC06-22: the existing nomenclature workspace and shared CUSTOM controls.
import { confirmBox } from '../utils/confirm-box.js';
import { el, scope, column, grid, TextBox, statusBar, showMessage, rowMenu, closeMenu } from './ui.js';

const endpoint = '/api/vercon/partners';
const compare = new Intl.Collator('ro', { sensitivity: 'base', numeric: true });

export function mountPartners(host, opts) {
  const ui = scope();
  let disposed = false, busy = false, choosing = false, canUpdate = false, rows = [], selected = null, dataGrid, missingCode = null;
  const status = el('span', { className: 'vc-save-status', role: 'status' });
  const refresh = el('button', { type: 'button', textContent: 'Reîmprospătează' });
  const manual = el('button', { type: 'button', textContent: 'Salvează numai CodulFiscal', hidden: true });
  const searchHost = el('div', { className: 'vc-partners__search' });
  const list = el('div', { className: 'vc-nom__list vc-partners__list' });
  const details = el('div', { className: 'vc-partners__details vc-nom__fields' });
  const title = el('h4', { textContent: 'Alegeți un furnizor din listă.' });
  const root = el('section', { className: 'vc-nom-page vc-partners', 'aria-label': 'Furnizori (Parteneri)' },
    el('div', { className: 'vc-nom__bar' }, el('h3', { textContent: 'Furnizori (Parteneri)' }), searchHost),
    el('div', { className: 'vc-partners__body' }, list, el('section', { className: 'vc-partners__card' }, title, details)),
    statusBar(el('div', { className: 'vc-actions' }), { start: [refresh], status, end: [manual] }));
  host.replaceChildren(root);
  const say = (text = '', bad = false) => { if (!disposed) showMessage(status, text, bad ? 'error' : 'info'); };
  const report = (error) => { if (!disposed && error.name !== 'AbortError') say(error.message || 'Operația a eșuat.', true); };
  const controls = new Map();
  for (const [key, label] of [['CodPartener', 'Cod partener'], ['CodFiscal', 'Cod fiscal'], ['DenumirePartener', 'Denumire'],
    ['ContIBAN', 'Cont IBAN'], ['Banca', 'Bancă'], ['Adresa', 'Adresă']]) {
    const node = el('label', { className: 'vc-field', 'data-field': key }, el('span', { textContent: label }));
    controls.set(key, ui.own(new TextBox(node, { type: 'text', maxLength: key === 'CodFiscal' ? 50 : 255,
      readOnly: true, ariaLabel: label, multiline: key === 'Adresa', rows: 3,
      onInput: () => { if (key === 'CodFiscal') { missingCode = null; sync(); } } })));
    details.append(node);
  }
  const fiscal = controls.get('CodFiscal');
  const search = ui.own(new TextBox(searchHost, { type: 'search', placeholder: 'Caută furnizor / cod fiscal…',
    ariaLabel: 'Caută furnizor', onInput: () => paintRows() }));
  const dirty = () => !!selected && fiscal.text !== String(selected.CodFiscal || '');
  const query = () => new URLSearchParams(Object.entries(opts.sel()).filter(([, value]) => value != null && value !== ''));
  const visible = () => {
    const text = search.text.trim().toLocaleLowerCase('ro');
    return rows.filter((row) => !text || ['DenumirePartener', 'CodFiscal', 'CodPartener'].some((key) =>
      String(row[key] || '').toLocaleLowerCase('ro').includes(text)))
      .sort((a, b) => compare.compare(a.DenumirePartener, b.DenumirePartener)
        || compare.compare(a.CodPartener, b.CodPartener) || compare.compare(a.CodFiscal, b.CodFiscal));
  };
  function sync() {
    manual.hidden = !canUpdate || missingCode == null; manual.disabled = busy || !selected;
    refresh.disabled = busy;
    fiscal.setReadOnly(!canUpdate || !selected);
  }
  function show(row) {
    selected = row; missingCode = null;
    title.textContent = row?.DenumirePartener || (row ? 'Furnizor fără denumire' : 'Alegeți un furnizor din listă.');
    for (const [key, box] of controls) { box.setValue(row?.[key] || ''); box.clearError(); }
    sync();
  }
  function paintRows() {
    if (!dataGrid) return;
    const listRows = visible();
    dataGrid.setRows(listRows, { keepWidths: true });
    if (selected && listRows.some((row) => row.id === selected.id)) dataGrid.selectRow(selected.id);
  }
  async function discard() {
    return !dirty() || await confirmBox('Renunțați la codul fiscal nesalvat?',
      { title: 'Modificări nesalvate', yes: 'Renunț', no: 'Rămân aici', warning: true });
  }
  async function choose(row) {
    if (selected?.id === row.id) return true;
    if (busy || choosing) { if (selected) dataGrid.selectRow(selected.id); return false; }
    choosing = true;
    try {
      if (!await discard() || disposed) { if (!disposed && selected) dataGrid.selectRow(selected.id); return false; }
      show(row); say('');
      dataGrid.selectRow(row.id);
      return true;
    } finally { choosing = false; }
  }
  function actions(row) {
    rowMenu(dataGrid, row, [{ label: 'Sincronizează cu ANAF', disabled: busy || choosing,
      run: async () => {
        try { if (await choose(row) && !disposed) await run('anaf'); } catch (error) { report(error); }
      } }]);
  }
  async function load() {
    const answer = await opts.api(endpoint + '?' + query());
    if (disposed) return;
    const previous = selected?.id;
    rows = answer.rows;
    canUpdate = answer.can_update;
    if (!dataGrid) dataGrid = grid(list, { layoutId: 'vercon.partners', rowKey: 'id', editable: false,
      footerCaption: '{0} furnizori', emptyText: 'Nu există furnizori în filtrul ales.', layout: {}, proportionalWidths: true,
      defaultSort: { key: 'DenumirePartener', dir: 'asc' }, columns: [
        column({ key: 'DenumirePartener', label: 'Denumire' }, { editable: false, width: 280, minWidth: 0 }),
        column({ key: 'CodFiscal', label: 'Cod fiscal' }, { editable: false, width: 135, minWidth: 0 }),
        column({ key: 'CodPartener', label: 'Cod partener' }, { editable: false, width: 110, minWidth: 0 }),
        { key: 'actions', title: '⋮', width: 36, minWidth: 36, fixedWidth: true, editable: false,
          filter: false, sortable: false, display: 'button', actionText: '⋮' },
      ], onSelect: (row) => { void choose(row).catch(report); },
      onCellAction: ({ row, key }) => { if (key === 'actions') actions(row); } }, ui);
    show(rows.find((row) => row.id === previous) || null); paintRows();
  }
  async function run(mode) {
    if (busy || choosing || !canUpdate || !selected) return;
    const code = fiscal.text.trim();
    if (!code) { fiscal.setError('Completați codul fiscal.'); fiscal.input.focus(); return; }
    if (mode === 'fiscal' && !await confirmBox('ANAF nu a găsit codul fiscal ' + missingCode
      + '. Salvați numai CodulFiscal pentru «' + selected.DenumirePartener + '»?',
      { title: 'Cod fiscal negăsit în ANAF', yes: 'Salvează CodulFiscal', no: 'Renunță' })) return;
    if (disposed) return;
    busy = true; root.inert = true; sync(); say('Se interoghează ANAF…');
    try {
      await opts.ensureWrite();
      if (disposed) return;
      const selection = opts.sel();
      const result = await opts.api(endpoint + '/' + selected.id + '/anaf', { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ subunit: selection.subunit,
          gestiune: selection.gestiune, version: selected.version, cod_fiscal: code, mode }) });
      if (disposed) return;
      if (!result.row) {
        missingCode = result.cod_fiscal;
        say('Codul nu a fost găsit în ANAF. Puteți salva numai CodulFiscal.');
        return;
      }
      rows = rows.map((row) => row.id === result.row.id ? result.row : row);
      show(result.row); paintRows();
      say(mode === 'fiscal' ? 'CodulFiscal a fost salvat.' : 'Furnizorul a fost actualizat din ANAF.');
    } catch (error) { missingCode = null; report(error); }
    finally { busy = false; if (!disposed) { root.inert = false; sync(); } }
  }
  manual.addEventListener('click', () => { void run('fiscal').catch(report); });
  refresh.addEventListener('click', async () => {
    if (busy || choosing || !await discard() || disposed) return;
    busy = true; root.inert = true; sync();
    try { await load(); say('Lista a fost reîmprospătată.'); } catch (error) { report(error); }
    finally { busy = false; if (!disposed) { root.inert = false; sync(); } }
  });
  return { ready: load(), isDirty: () => busy || dirty(), canChangeContext: () => !busy && !choosing,
    destroy: () => { disposed = true; closeMenu(); ui.clear(); host.replaceChildren(); } };
}
