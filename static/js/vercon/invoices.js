import { el, scope, field, column, grid, modal, rowMenu, showMessage } from './ui.js';
import { confirmBox } from '../utils/confirm-box.js';
import { decimalText } from './decimal-text.js';

const vatPercent = raw => {
  if (raw == null) return '';
  const match = /^(-?)(\d+)(?:\.(\d*))?$/.exec(String(raw));
  if (!match) return String(raw);
  const decimals = (match[3] || '').padEnd(2, '0');
  const whole = BigInt(match[2] + decimals.slice(0, 2)).toString(), tail = decimals.slice(2).replace(/0+$/, '');
  return match[1] + whole + (tail ? '.' + tail : '');
};

// The invoice is DC-wide; the receipt selected by its caller stays in its own stock context.
export function invoiceManager(opts) {
  const view = modal('Factură de cumpărare · recepții repartizate', opts.owner);
  view.dialog.classList.add('vc-invoices');
  const headers = scope(), linesOwner = scope(), listOwner = scope(), receiptsOwner = scope();
  view.scope.own(() => [headers, linesOwner, listOwner, receiptsOwner].forEach(owner => owner.clear()));
  let model = opts.current ? structuredClone(opts.current) : blank(), rows = [], dirty = false, busy = false, pending = null, lineGrid;
  const inputs = new Map();
  const status = el('p', { role: 'status' }), controls = el('div', { className: 'vc-actions' });
  const list = el('div', { className: 'vc-invoices__list' }), header = el('div', { className: 'vc-grid vc-invoices__header' });
  const lines = el('div', { className: 'vc-invoices__lines' }), receipts = el('div', { className: 'vc-invoices__receipts' });
  const summary = el('p', { className: 'vc-hint' });
  const editor = el('section', {}, header, summary, el('h4', { textContent: 'Linii factură · valorile țintă de repartizat' }), lines,
    el('h4', { textContent: 'NIR-uri asociate · toate subunitățile și gestiunile' }), receipts);
  view.body.append(controls, el('div', { className: 'vc-invoices__layout' }, list, editor), status);
  const say = (message, bad = false) => showMessage(status, message, bad ? 'error' : 'info');
  const query = () => new URLSearchParams(Object.entries(opts.sel()).filter(([, value]) => value != null));
  const read = id => opts.api(`/api/vercon/invoices/${id}?${query()}`);
  function blank() { return { partner: opts.header?.IdPartener ?? null, number: opts.header?.NrFact || '', date: opts.header?.DataFc || '', currency: 'RON', total: '', lines: [], receipts: [] }; }
  const button = (text, run, disabled = false) => {
    const node = el('button', { type: 'button', textContent: text, disabled });
    node.addEventListener('click', () => Promise.resolve().then(run).catch(error => say(error.message, true))); return node;
  };
  const mark = () => { dirty = true; };
  async function discard() { return !pending && (!dirty || await confirmBox('Renunțați la modificările facturii?', { yes: 'Renunț', no: 'Rămân aici' })); }
  async function close() { if (busy || !await discard()) return; view.close(); }
  view.dialog.addEventListener('cancel', event => { event.preventDefault(); event.stopImmediatePropagation(); void close(); }, true);
  const retry = button('Reia aceeași operație', () => send(pending.action, pending.body)); retry.hidden = true;
  view.footer.append(button('Închide', close), retry,
    button('Folosește factura pentru acest NIR', async () => {
      if (!model.id || busy || pending) return;
      if (dirty) { say('Salvați mai întâi factura modificată.', true); return; }
      await opts.onChoose?.(structuredClone(model)); view.close();
    }, !opts.onChoose));
  async function send(action, body) {
    if (busy) return;
    if (!pending) pending = { action, body: { ...opts.sel(), ...body, request_key: crypto.randomUUID() } };
    busy = true; editor.inert = list.inert = controls.inert = true; say('Se salvează…');
    try {
      const result = await opts.api(`/api/vercon/invoices/${pending.action}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(pending.body) });
      pending = null; dirty = false;
      if (action === 'backfill') { say(`${result.attached} recepții istorice asociate provizoriu.`); }
      else { model = result; paint(); say('Salvat.'); }
      await refresh();
    } catch (error) {
      if (error.status && error.status < 500) pending = null;
      say(pending ? 'Răspuns neconfirmat. Folosiți „Reia aceeași operație” înainte de alte modificări.' : error.message, true);
    } finally {
      busy = false; editor.inert = list.inert = controls.inert = !!pending; retry.hidden = !pending;
    }
  }
  async function save() {
    if (!await headers.commit() || !await linesOwner.commit()) return;
    if (!headers.validate()) return;
    await send('save', { ...model, lines: model.lines.map(row => ({ ...row, vat_percent: row.vat_percent ?? vatPercent(row.rate) })) });
  }
  async function refresh() {
    const params = query();
    if (model.partner) params.set('partner', model.partner);
    const answer = await opts.api(`/api/vercon/invoices?${params}`);
    if (view.closed) return;
    rows = answer.invoices; listOwner.clear(); list.replaceChildren();
    const listGrid = grid(list, { rows, rowKey: 'id', editable: false, columns: [
      { key: 'number', title: 'Factură', width: 145 }, { key: 'date', title: 'Data', width: 105 },
      { key: 'state', title: 'Stare', width: 110 }, { key: 'total', title: 'ValoareFC confirmată', width: 130 },
      { key: '_action', title: '⋮', width: 42, display: 'button', actionText: '⋮' }],
      onCellAction: ({ row }) => rowMenu(listGrid, row, [
        { label: 'Deschide', run: async () => { if (!await discard()) return; model = await read(row.id); dirty = false; paint(); } },
        { label: 'Reunește cu factura deschisă', disabled: !opts.canWrite || !model.id || model.id === row.id,
          run: async () => {
            if (dirty) { say('Salvați sau abandonați modificările înainte de reunire.', true); return; }
            if (!await confirmBox(`Reuniți fragmentul ${row.number} cu factura deschisă? Totalul global va trebui confirmat separat.`, { yes: 'Reunește', no: 'Renunță' })) return;
            await send('merge', { target: model.id, version: model.version, sources: [{ id: row.id, version: row.version }] });
          } },
      ]),
    }, listOwner);
  }
  function paint() {
    headers.clear(); header.replaceChildren(); inputs.clear();
    const specs = [
      { key: 'partner', label: 'Furnizor', type: 'ref', required: true, options: opts.partners.map(row => ({ value: row.id, label: row.name })) },
      { key: 'number', label: 'Număr factură', required: true, type: 'text' },
      { key: 'date', label: 'Data facturii', required: true, type: 'date' },
      { key: 'currency', label: 'Monedă', required: true, type: 'text' },
      { key: 'total', label: 'ValoareFC · total factură', required: true, type: 'decimal', min: 0 },
    ];
    for (const spec of specs) {
      const locked = model.receipts?.length && spec.key !== 'total';
      const item = field(spec, model[spec.key], opts.canWrite && !locked, value => { model[spec.key] = spec.key === 'partner' ? Number(value) : value; mark(); }, headers);
      header.append(item.node); inputs.set(spec.key, item.input);
    }
    summary.textContent = `Stare: ${model.state || 'în lucru'} · Recepționat: ${decimalText(model.received || '0', 4)} lei` +
      (model.migrated && !model.confirmed ? ' · Antetele sursă sunt fragmente; totalul facturii nu este încă confirmat.' : '');
    linesOwner.clear(); lines.replaceChildren();
    model.lines = (model.lines || []).map(row => ({ ...row, key: row.key || crypto.randomUUID(), vat_percent: row.vat_percent ?? vatPercent(row.rate) }));
    const specsLines = [{ key: 'name', label: 'Produs pe factură', type: 'text', required: true }, { key: 'um', label: 'UM', type: 'text' },
      { key: 'quantity', label: 'Cantitate facturată', type: 'decimal', required: true, min: 0 },
      { key: 'vat_percent', label: 'TVA %', type: 'decimal', required: true, min: 0 },
      { key: 'total', label: 'Valoare cu TVA țintă', type: 'decimal', required: true, min: 0 }];
    lineGrid = grid(lines, { rows: model.lines, rowKey: 'key', editable: opts.canWrite,
      columns: [...specsLines.map(spec => column(spec, { editable: row => opts.canWrite && !row.reconstructed })), { key: 'received_quantity', title: 'Recepționat', editable: false },
        { key: '_action', title: '⋮', width: 42, display: 'button', actionText: '⋮', editable: false }],
      footer: true, footerAction: { label: 'Adaugă linie', span: 2, disabled: !opts.canWrite },
      onFooterAction: async () => { if (!await linesOwner.commit()) return; model.lines.push({ key: crypto.randomUUID(), name: '', um: '', quantity: '', vat_percent: '', total: '' }); mark(); paint(); },
      onCellSave: ({ row, key, value }) => { const source = model.lines.find(item => item.key === row.key); source[key] = value; mark(); return { [key]: value }; },
      onCellAction: ({ row }) => rowMenu(lineGrid, row, [{ label: 'Scoate linia', disabled: !opts.canWrite || row.reconstructed || !!Number(row.received_quantity), run: () => { model.lines = model.lines.filter(item => item.key !== row.key); mark(); paint(); } }]),
    }, linesOwner);
    receiptsOwner.clear(); receipts.replaceChildren();
    grid(receipts, { rows: model.receipts || [], rowKey: 'link', editable: false, columns: [
      { key: 'subunit_name', title: 'Subunitate' }, { key: 'gestiune_name', title: 'Gestiune' }, { key: 'account', title: 'Cont' },
      { key: 'number', title: 'NIR' }, { key: 'day', title: 'Data' }, { key: 'total', title: 'Valoare locală' }, { key: 'source_total', title: 'Antet sursă' },
    ] }, receiptsOwner);
  }
  controls.append(button('Factură nouă', async () => { if (!await discard()) return; model = blank(); paint(); }),
    button('Reîmprospătează lista', refresh), button('Salvează factura', save, !opts.canWrite),
    button('Finalizează reconcilierea', async () => { if (dirty || !model.id) { say('Salvați mai întâi factura.', true); return; } await send('finalize', { id: model.id, version: model.version }); }, !opts.canWrite),
    button('Asociază istoricul gestiunii', async () => {
      if (!await confirmBox('Creați asocieri provizorii pentru NIR-urile istorice neasociate? Valorile și stocul rămân identice.', { yes: 'Asociază', no: 'Renunță' })) return;
      await send('backfill', {});
    }, !opts.canWrite));
  paint(); void refresh().catch(error => say(error.message, true));
  return view;
}
