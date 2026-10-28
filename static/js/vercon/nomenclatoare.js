// Grid-only nomenclatures; settings are one modal window: a grid of properties and a grid with the next numbers.
export { mountNomenclator } from './nomenclator-grid.js';
import { parse } from './fields.js';
import { confirmBox } from '../utils/confirm-box.js';
import { ModalFrame } from '../components/modal-frame/modal-frame.js';
import { el, scope, column, grid, showMessage, statusBar } from './ui.js';

// 420 px + 20% + 20% = 605 px. The first column of both grids is the fill column: it takes all the width the second one leaves
// (the vertical scrollbar included), so a scrollbar never brings a horizontal one; 200 is only its starting width.
const SETTINGS_WIDTH = 605, SETTINGS_HEIGHT = 700, TEXT_COLUMN = 200, VALUE_COLUMN = 118, FILL = { fill: 'label' };
const DESCRIPTIONS = {
  zec: 'Zecimale afișate și de calcul (0–4)',
  bc_per_account: 'Numerotare bonuri de consum pe cont',
  SI_LA: 'Sold inițial Alimente',
};
// A stored value without the zeros of its fixed storage scale: 4.0000 -> 4.
const plain = (text) => String(text).includes('.') ? String(text).replace(/\.?0+$/, '') : String(text);

// Storage is always DECIMAL(20,4) and is not a setting; ZEC controls calculation/display precision.
export function mountSettings(host, opts) {
  const ui = scope(); let disposed = false, data, numbering = [], dirty = false, frame = null, rows = [], numbersGrid = null, refreshing = false;
  const status = el('span', { className: 'vc-save-status', role: 'status' });
  const saveButton = el('button', { type: 'button', textContent: 'Salvează', className: 'is-primary' });
  const closeButton = el('button', { type: 'button', textContent: 'Închide' });
  const gridHost = el('div', { className: 'vc-settings-grid' }), numbersHost = el('div', { className: 'vc-settings-numbers' });
  const root = el('section', { className: 'vc-nom vc-settings', 'aria-label': 'Setări gestiune' }, gridHost, numbersHost,
    statusBar(el('div', { className: 'vc-actions' }), { start: [closeButton], status, end: [saveButton] })); host.replaceChildren(root);
  const say = (text = '', bad = false) => { if (!disposed) showMessage(status, text, bad ? 'error' : 'info'); };
  const rowsOf = (answer) => {
    const list = [{ key: 'zec', label: DESCRIPTIONS.zec, kind: 'int', value: answer.zec }];
    if (answer.type === 'MATERIALE') list.push({ key: 'bc_per_account', label: DESCRIPTIONS.bc_per_account, kind: 'bool', value: !!answer.bc_per_account });
    for (const item of answer.settings) {
      if (item.key === 'NUM_BC_PE_CONT') continue;   // it is the check box above
      list.push({ key: item.key, label: DESCRIPTIONS[item.key] || item.key, kind: 'text', value: plain(item.value) });
    }
    return list.map((row) => ({ ...row, editable: !!answer.can_write && row.kind !== 'text' }));
  };
  const zecValue = (raw) => {
    const result = parse(raw, { key: 'zec', type: 'int', required: true, min: 0 });
    if (!result.ok) throw new Error(result.message);
    if (result.value > 4) throw new Error('Valoarea maximă este 4.');
    return result.value;
  };
  function paint() {
    ui.clear(); rows = rowsOf(data); saveButton.hidden = !data.can_write;
    grid(gridHost, { layoutId: 'vercon.settings.properties', rowKey: 'key', rows, editable: !!data.can_write, footer: false, defaultSort: false, layout: FILL, columns: [
      column({ key: 'label', label: 'Descriere' }, { width: TEXT_COLUMN, editable: false }),
      column({ key: 'value', label: 'Valoare' }, { width: VALUE_COLUMN, align: 'center', filter: false, editable: (row) => row.editable,
        cellFor: (row) => row.kind === 'bool' ? { display: 'checkbox', editor: 'checkbox' } : row.kind === 'int' ? { editor: 'number', parse: zecValue } : {} }),
    ], onCellSave: () => { dirty = true; say(''); } }, ui);
    // The current number (the last one given) of every numbered document of this gestiune and exercise (read only);
    // the button in the header of the number column reads them again.
    numbersGrid = grid(numbersHost, { layoutId: 'vercon.settings.numbering', rowKey: 'key', rows: numbering, editable: false, footer: false, defaultSort: false, layout: FILL,
      onHeaderAction: () => { refreshNumbers(); }, columns: [
      column({ key: 'label', label: 'Document' }, { width: TEXT_COLUMN, editable: false }),
      column({ key: 'current', label: 'Număr' }, { width: VALUE_COLUMN, align: 'center', filter: false, editable: false, headerButton: { text: '🔄', title: 'Recitește numerele curente' } }),
    ] }, ui);
    dirty = false;
    if (!frame) { frame = new ModalFrame(root, { width: SETTINGS_WIDTH, height: SETTINGS_HEIGHT, title: 'Setări gestiune', showClose: false, onRequestClose: requestClose }); frame.open(); }
  }
  const queryOf = () => new URLSearchParams(Object.entries(opts.sel()).filter(([, v]) => v != null && v !== ''));
  async function refreshNumbers() {
    if (disposed || refreshing || !numbersGrid) return;
    refreshing = true; say('Se recitesc numerele…');
    try {
      const answer = await opts.api('/api/vercon/numbering?' + queryOf()); if (disposed) return;
      numbering = answer.rows; numbersGrid.setRows(numbering, { keepWidths: true }); say('Numerele au fost recitite.');
    } catch (error) { if (!disposed && error.name !== 'AbortError') say(error.message || 'Numerele nu au putut fi citite.', true); }
    finally { refreshing = false; }
  }
  async function load() {
    const query = queryOf(); let numberError = '';
    const [response, numbers] = await Promise.all([opts.api('/api/vercon/settings?' + query),
      opts.api('/api/vercon/numbering?' + query).catch((error) => { if (error.name === 'AbortError') throw error; numberError = error.message || 'Numerotarea nu a putut fi citită.'; return { rows: [] }; })]);
    if (disposed) return; data = response; numbering = numbers.rows; paint(); if (numberError) say(numberError, true);
  }
  async function requestClose() {
    if ((dirty || ui.dirty()) && !await confirmBox('Renunțați la modificările nesalvate?', { title: 'Modificări nesalvate', yes: 'Renunț', no: 'Rămân aici', warning: true })) return;
    dirty = false; opts.onClose?.();
  }
  closeButton.addEventListener('click', () => { requestClose().catch((error) => say(error.message || 'Fereastra nu a putut fi închisă.', true)); });
  saveButton.addEventListener('click', async () => {
    if (!data?.can_write || !await ui.commit()) return;
    const values = { scara: 4 }, changedZec = rows.find((row) => row.key === 'zec').value !== data.zec;
    for (const row of rows) { if (row.key === 'zec') values.zec = row.value; if (row.key === 'bc_per_account') values.bc_per_account = !!row.value; }
    root.inert = true; say('Se salvează…');
    try { const sel = opts.sel(); await opts.api('/api/vercon/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ subunit: sel.subunit, gestiune: sel.gestiune, ...values }) }); if (disposed) return; await load(); say(changedZec ? 'Salvat. Reîncărcați pagina pentru aplicarea zecimalelor.' : 'Salvat.'); }
    catch (error) { if (!disposed && error.name !== 'AbortError') say(Object.values(error.fields || {})[0] || error.message || 'Salvarea a eșuat.', true); }
    finally { if (!disposed) root.inert = false; }
  });
  return { ready: load(), isDirty: () => dirty || ui.dirty(), destroy: () => { disposed = true; ui.clear(); frame?.destroy(); frame = null; host.replaceChildren(); } };
}
