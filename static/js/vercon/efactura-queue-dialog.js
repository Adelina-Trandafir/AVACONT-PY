import { el, grid, column, scope, ValueType, statusBar } from './ui.js';
import { pickBox, reasonBox } from '../utils/confirm-box.js';
import { compareTyped } from '../utils/typed-order.js';

// Invoice flags apply to the entire unit; they never return the invoice to the accountant.
export async function pickEfactura({ read, post }) {
  const owner = scope(), dialog = el('dialog', { className: 'vc-efi', 'aria-label': 'Facturi din eFactura' });
  const status = el('p', { className: 'vc-efi__status vc-save-status', role: 'status' });
  const filter = el('button', { type: 'button', textContent: 'Afișare: Active' });
  const host = el('div', { className: 'vc-efi__queue-grid' });
  const useButton = el('button', { type: 'button', className: 'is-primary', textContent: 'Folosește factura', disabled: true });
  const returnButton = el('button', { type: 'button', textContent: 'Returnează…', disabled: true });
  const closeButton = el('button', { type: 'button', textContent: 'Închide' });
  dialog.append(el('header', {}, el('h2', { textContent: 'Facturi trimise de contabil (eFactura)' })),
    el('div', { className: 'vc-efi__body' }, el('div', { className: 'vc-efi__actions' }, filter), host),
    statusBar(el('footer'), { start: [closeButton], status, end: [returnButton, useButton] }));
  document.body.append(dialog); dialog.showModal();
  let items = [], chosen = null, busy = false, closed = false, visibility = 'active', visibilityAvailable = false, invoices = null;
  let resolve; const done = new Promise(answer => { resolve = answer; });
  const say = (text, bad = false) => { status.textContent = text; status.dataset.state = bad ? 'error' : 'info'; };
  const controls = () => {
    filter.disabled = closeButton.disabled = busy;
    useButton.disabled = busy || !chosen || chosen.inactive || chosen.manual_links || chosen.state === 'PRELUATA';
    returnButton.disabled = busy || !chosen || chosen.state !== 'TRIMISA';
    invoices?.setBusy(busy);
  };
  const choose = item => {
    chosen = item; controls();
    if (!busy && item) say([
      item.state === 'PRELUATA' ? 'Factura este preluată integral.'
        : item.manual_links ? 'Factura are asocieri pe poziții. Pentru rest folosiți «Asociază eFactura…» din NIR-ul salvat.' : '',
      `${item.lines} poziții.`, item.sent_by ? 'Trimisă de: ' + item.sent_by + '.' : '',
      item.note ? 'Notă contabil: ' + item.note : '',
    ].filter(Boolean).join(' '));
  };
  invoices = grid(host, { rowKey: 'id', rows: [], editable: true, footer: false, proportionalWidths: true,
    emptyText: 'Nu există facturi în filtrul ales.',
    columns: [column({ key: 'supplier', label: 'Furnizor' }, { width: 3, minWidth: 150, editable: false }),
      column({ key: 'number', label: 'Factura' }, { width: 2, minWidth: 100, editable: false }),
      column({ key: 'date', label: 'Data', type: 'date' }, { width: 105, minWidth: 105, fixedWidth: true, editable: false }),
      column({ key: 'total', label: 'Total', type: 'money' }, { width: 115, minWidth: 100, fixedWidth: true, editable: false }),
      column({ key: 'state_label', label: 'Asociere' }, { width: 2, minWidth: 120, editable: false }),
      { key: 'inactive', title: 'Inactiv', width: 80, minWidth: 80, fixedWidth: true, valueType: ValueType.Boolean,
        display: 'checkbox', editor: 'checkbox', editable: () => visibilityAvailable && !busy }],
    onSelect: choose,
    onCellSave: async ({ row, value }) => {
      busy = true; controls(); say('Se salvează marcajul Inactiv…');
      try {
        const result = await post(`/${row.id}/visibility`, { inactive: value === true });
        return { inactive: result.inactive };
      } finally { busy = false; controls(); }
    },
    onCellSaved: () => { void load(); },
    onEditError: ({ error }) => say(error.message || 'Marcajul nu a putut fi salvat. Reluați bifarea.', true),
  }, owner);
  invoices.sortBy('date', 'desc');

  async function load() {
    if (closed || busy) return;
    busy = true; controls(); say('Se încarcă facturile…');
    try {
      const answer = await read('', { visibility });
      visibilityAvailable = !!answer.visibility_available;
      items = (answer.items || []).map(item => ({ ...item,
        state_label: item.state === 'PARTIALA' ? 'Asociată parțial' : item.state === 'PRELUATA' ? 'Asociată integral' : 'Neasociată' }))
        .sort((a, b) => compareTyped(b.date, a.date, 'date') || compareTyped(a.supplier, b.supplier, 'text') || compareTyped(a.number, b.number, 'text'));
      invoices.setRows(items); choose(items.find(item => item.id === chosen?.id) || null);
      if (chosen) invoices.selectRow(chosen.id);
      say(!answer.available ? 'Facturile din eFactura nu sunt disponibile în această bază de date.'
        : `${items.length} facturi afișate.${visibilityAvailable ? '' : ' Marcajul Inactiv necesită instalarea manuală a structurii EV_03.'}`);
    } catch (error) { say(error.message || 'Lista nu a putut fi încărcată.', true); }
    finally { busy = false; controls(); }
  }
  const close = value => { if (busy || closed) return; closed = true; owner.clear(); dialog.close(); dialog.remove(); resolve(value); };
  async function use() {
    if (!chosen || busy || chosen.inactive || chosen.manual_links || chosen.state === 'PRELUATA') return;
    busy = true; controls(); say('Se citește factura…');
    try { const detail = await read(`/${chosen.id}`); busy = false; close(detail); }
    catch (error) { busy = false; controls(); say(error.message || 'Factura nu a putut fi citită.', true); }
  }
  async function giveBack() {
    if (!chosen || busy || chosen.state !== 'TRIMISA') return;
    const item = chosen;
    const reason = await reasonBox(`Returnați contabilului factura ${item.number} (${item.supplier || item.cui || ''})?`,
      { title: 'Returnare factură', label: 'Motiv', yes: 'Returnează', no: 'Renunță', maxLength: 500 });
    if (!reason || closed || busy) return;
    busy = true; controls(); say('Se returnează…');
    try { await post(`/${item.id}/return`, { reason }); chosen = null; busy = false; await load(); }
    catch (error) { busy = false; controls(); say(error.message || 'Returnarea nu a putut fi făcută.', true); }
  }
  filter.addEventListener('click', async () => {
    if (busy || !await invoices.commitEdit()) return;
    const selected = await pickBox('Afișează facturile', [
      { value: 'active', label: 'Active' }, { value: 'inactive', label: 'Inactive' }, { value: 'all', label: 'Toate' }]);
    if (!selected || closed || busy) return;
    visibility = selected; filter.textContent = 'Afișare: ' + { active: 'Active', inactive: 'Inactive', all: 'Toate' }[visibility];
    await load();
  });
  useButton.addEventListener('click', () => void use());
  returnButton.addEventListener('click', () => void giveBack());
  closeButton.addEventListener('click', () => close(null));
  dialog.addEventListener('cancel', event => { event.preventDefault(); close(null); });
  void load();
  return done;
}
