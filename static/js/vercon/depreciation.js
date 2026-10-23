// VC08-02: date projection from the server; no local accounting calculations.
import { el, field, grid, column, modal, preview, showMessage, statusBar } from './ui.js';
import { decimalText } from './decimal-text.js';

const statusText = { invalid: 'Date de corectat', not_entered: 'Neintrată la data aleasă', not_in_use: 'Nepredat',
  active: 'În amortizare', fully_depreciated: 'Amortizat integral', written_off: 'Casat' };

export function openDepreciation(asset, opts, owner) {
  const view = modal(`Amortizare • ${asset.code || asset.name}`, owner);
  view.body.classList.add('vc-mf-amortization');
  const state = { sequence: 0, data: null, date: defaultDate(opts.sel().year) };
  view.scope.own(() => { ++state.sequence; });
  const invalidate = () => {
    ++state.sequence; state.data = null; dataGrid.setRows([]); totals.replaceChildren(); warnings.replaceChildren(); pdf.disabled = true;
  };
  const dateField = field({ key: 'date', label: 'La data', type: 'date', required: true }, state.date, true,
    (value) => { if (state.date !== value) { state.date = value; invalidate(); } }, view.scope);
  const calculate = el('button', { type: 'button', textContent: 'Calculează' });
  calculate.addEventListener('click', () => void load());
  const toolbar = el('div', { className: 'vc-mf-amortization__toolbar vc-actions' }, dateField.node, calculate);
  const status = el('p', { className: 'vc-save-status', role: 'status' }), warnings = el('div', { className: 'vc-mf-amortization__issues', role: 'status' });
  const host = el('div', { className: 'vc-mf-amortization__grid' }), totals = el('dl', { className: 'vc-mf-amortization__totals' });
  const amount = (key, label) => column({ key, label, type: 'decimal' }, { editable: false, width: 145,
    formatter: (value) => value == null ? '—' : decimalText(value, state.data?.zec ?? 4) });
  const columns = [column({ key: 'name', label: 'Mijloc fix' }, { editable: false, width: 220 }),
    column({ key: 'code', label: 'Nr. inventar' }, { editable: false, width: 140 }),
    column({ key: 'start_date', label: 'Prima predare', type: 'date' }, { editable: false }),
    column({ key: 'used_months', label: 'Luni folosite', type: 'int' }, { editable: false }),
    column({ key: 'remaining_months', label: 'Luni rămase', type: 'int' }, { editable: false }),
    amount('value', 'Valoare variantă'), amount('monthly', 'Valoare lunară'), amount('month_depreciation', 'Amortizare lună'),
    amount('accumulated', 'Amortizare cumulată'), amount('remaining', 'Valoare rămasă'),
    column({ key: 'status', label: 'Stare' }, { editable: false, width: 150, formatter: (value) => statusText[value] || value })];
  const dataGrid = grid(host, { columns, rows: [], rowKey: 'row_id', footer: false, layoutId: 'vercon.mf.depreciation' }, view.scope);
  const explanation = el('p', { className: 'vc-mf-amortization__note', textContent:
    'Calcul la data aleasă, cu parametrii curenți din registru și valoarea curentă a variantei. Durata deja folosită (DF) se adaugă la lunile calendaristice de la prima predare; transferul păstrează începutul. Valorile sursă rămân păstrate.' });
  view.body.append(toolbar, explanation, host, totals, warnings);
  const close = el('button', { type: 'button', textContent: 'Închide' }); close.addEventListener('click', view.close);
  const pdf = el('button', { type: 'button', textContent: 'Previzualizare', disabled: true });
  pdf.addEventListener('click', () => {
    if (!state.data) return;
    const diagnostics = diagnosticRows();
    preview('Amortizare', [{ title: 'Calcul pe activ • ' + asset.code, columns, rows: state.data.assets },
      { title: 'Parametri și observații', columns: [{ key: 'label', title: 'Parametru', width: 230 }, { key: 'value', title: 'Valoare / observație', width: 650 }], rows: diagnostics }], view.scope,
    { documentLabel: asset.code || asset.name, date: state.data.date });
  });
  statusBar(view.footer, { start: [close], status, end: [pdf] });

  function diagnosticRows() {
    const row = state.data?.assets[0]; if (!row) return [];
    const zec = state.data.zec;
    return [
      { label: 'Data calculului', value: state.data.date },
      { label: 'Bază de evaluare', value: 'Prețul curent al variantei' },
      { label: 'Data intrării', value: row.entry_date || 'Lipsă' },
      { label: 'Prima recepție', value: row.acquisition_date || 'Lipsă' },
      { label: 'Durate', value: `Normală (DNF): ${row.DNF ?? 'lipsă'} luni; deja folosită (DF): ${row.DF ?? 'lipsă'} luni` },
      { label: 'Valoare lunară din registru', value: row.source_monthly.ValoareLuna == null ? 'Lipsă' : decimalText(row.source_monthly.ValoareLuna, zec) },
      { label: 'Valoare lunară istorică', value: row.source_monthly.ValoareLunara == null ? 'Lipsă' : decimalText(row.source_monthly.ValoareLunara, zec) },
      { label: 'Sursa valorii lunare', value: { ValoareLuna: 'Registru', ValoareLunara: 'Valoarea istorică', value_divided_by_DNF: 'Calculată din valoare și durata normală' }[row.monthly_source] || 'Necalculată' },
      { label: 'Documente la dată', value: `${row.receipt_lines} linii recepție; ${row.handover_lines} predare; ${row.transfer_lines} transfer` },
      ...(row.stop_date ? [{ label: 'Oprire prin casare', value: row.stop_date }] : []),
      ...(row.issues || []).map((issue) => ({ label: 'Observație', value: issue.message })),
    ];
  }
  async function load() {
    if (!await view.scope.commit() || view.closed) return;
    invalidate(); const sequence = ++state.sequence;
    status.textContent = 'Se calculează amortizarea…'; status.setAttribute('role', 'status');
    const params = new URLSearchParams(Object.entries({ ...opts.sel(), date: state.date, variant: asset.id }).filter(([, value]) => value != null && value !== ''));
    try {
      const data = await opts.api('/api/vercon/mf/depreciation?' + params);
      if (view.closed || sequence !== state.sequence) return;
      state.data = data; status.textContent = ''; dataGrid.setRows(data.assets);
      const row = data.assets[0];
      if (!row) { showMessage(status, 'Activul nu mai există în registru.', 'error'); return; }
      for (const [label, value] of [['Luna următoare', row.next_month_depreciation], ['Rest la casare', row.written_off_remaining]]) {
        if (value != null) totals.append(el('dt', { textContent: label }), el('dd', { textContent: decimalText(value, data.zec) }));
      }
      for (const item of diagnosticRows()) warnings.append(el('p', {}, el('b', { textContent: item.label + ': ' }), item.value));
      if (row.status === 'invalid') showMessage(status, 'Amortizarea nu poate fi calculată pentru acest activ. Consultați datele de corectat.', 'error');
      else if (row.status === 'not_entered' || row.status === 'not_in_use') showMessage(status, statusText[row.status] + '. Amortizarea la această dată nu este inclusă în totaluri.', 'info');
      pdf.disabled = false;
    } catch (error) {
      if (view.closed || sequence !== state.sequence || error.name === 'AbortError') return;
      showMessage(status, error.message || 'Amortizarea nu a putut fi calculată.', 'error');
    }
  }
  if (asset.id == null) showMessage(status, 'Rândul din registru nu are un activ asociat. Corectați datele înainte de calcul.', 'error');
  else void load();
  return view;
}

function defaultDate(selectedYear) {
  const today = new Date(), year = Number(selectedYear || today.getFullYear());
  return year === today.getFullYear() ? `${year}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}` : `${year}-12-31`;
}
